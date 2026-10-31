"""VERA - transformador de voz para SEÑAL.

Fase 1: probar un WAV ya grabado (A/B, post-proceso, guardar).
Fase 2: grabar con el micrófono y transformar al instante.
Fase 3: presets guardables (motor + sliders + post-proceso).
Fase 4: procesar en lote una carpeta entera.
Fase 5: motor RVC conectado a Applio.
Además: "Mandar a Audio Mixer" abre el resultado en el editor del estudio.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

import batch
import post
import preset_store
from audio_io import SR, ABPlayer, Recorder, list_devices, load_audio, save_wav
from engines import ENGINES
from engines import rvc_applio

try:
    from engines.praat_gender import pitch_median
except Exception:  # noqa: BLE001 - sin Praat la app abre igual (con WORLD/RVC)
    def pitch_median(audio, sr):
        return 0.0

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
MIXER_SCRIPT = os.path.join(ROOT_DIR, "mixer", "audio_mixer.py")
LOG_PATH = os.path.join(APP_DIR, "errores.log")
TESTS_DIR = os.path.join(APP_DIR, "pruebas")
TAKES_DIR = os.path.join(APP_DIR, "tomas")
OUT_DIR = os.path.join(APP_DIR, "salida")
CURRENT_SETTINGS = "(ajustes actuales de «Probar voz»)"
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def log_error(exc_text):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + exc_text + "\n")


def gui_python():
    """Python para abrir otra ventana: pythonw en Windows (sin consola negra)."""
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(w):
            return w
    return exe


def launch_mixer(paths=()):
    if not os.path.isfile(MIXER_SCRIPT):
        raise FileNotFoundError(f"No encuentro Audio Mixer en {MIXER_SCRIPT}")
    subprocess.Popen([gui_python(), MIXER_SCRIPT, *paths], cwd=os.path.dirname(MIXER_SCRIPT),
                     creationflags=NOWIN)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def unique_path(folder, base, ext=".wav"):
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{n}{ext}")
        n += 1
    return path


class App:
    def __init__(self, root):
        self.root = root
        root.title("VERA - transformador de voz (SEÑAL)")
        root.minsize(820, 600)

        self.player = ABPlayer()
        self.recorder = Recorder()
        self.original = None      # audio original 48 kHz mono
        self.processed = None     # audio procesado
        self.processed_engine = None
        self.source_path = None
        self.results = queue.Queue()
        self.busy = False
        self.pending = False      # se pidió reprocesar mientras se procesaba
        self.param_vars = {}
        self.post_vars = {}
        self.batch_q = queue.Queue()
        self.batch_cancel = None

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Probar voz")
        tab_batch = ttk.Frame(nb, padding=8)
        nb.add(tab_batch, text="Lote (carpeta entera)")

        self.status = tk.StringVar(value="Abrí un WAV o grabá con el micrófono para empezar.")
        self._build_file(tab)
        self._build_engine(tab)
        self._build_post(tab)
        self._build_playback(tab)
        self._build_batch(tab_batch)

        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w",
                  padding=4).pack(fill="x", side="bottom")

        root.bind("<KeyPress-a>", lambda e: self._hotkey(e, lambda: self.switch("A")))
        root.bind("<KeyPress-b>", lambda e: self._hotkey(e, lambda: self.switch("B")))
        root.bind("<KeyPress-r>", lambda e: self._hotkey(e, self.toggle_record))
        root.bind("<space>", lambda e: self._hotkey(e, self.toggle_play))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._poll)

    def _hotkey(self, event, action):
        if not isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
            action()

    # ------------------------------------------------------------ interfaz: probar
    def _build_file(self, parent):
        f = ttk.LabelFrame(parent, text="1. Archivo o micrófono", padding=6)
        f.pack(fill="x")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="Abrir WAV…", command=self.open_file).pack(side="left")
        self.file_info = tk.StringVar(value="(ningún archivo)")
        ttk.Label(row, textvariable=self.file_info).pack(side="left", padx=10)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Micrófono:").pack(side="left")
        try:
            devs, default = list_devices("input")
        except Exception:  # noqa: BLE001
            devs, default = [], None
        self.in_devices = {label: idx for idx, label in devs}
        default_label = next((l for i, l in devs if i == default), devs[0][1] if devs else "")
        self.in_var = tk.StringVar(value=default_label)
        ttk.Combobox(row, textvariable=self.in_var, state="readonly", width=48,
                     values=[l for _, l in devs]).pack(side="left", padx=6)
        self.rec_btn = ttk.Button(row, text="● Grabar", command=self.toggle_record, width=10)
        self.rec_btn.pack(side="left", padx=4)
        self.vu = ttk.Progressbar(row, length=120, maximum=60)
        self.vu.pack(side="left", padx=6)
        self.rec_info = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.rec_info, width=16).pack(side="left")

    def _build_engine(self, parent):
        f = ttk.LabelFrame(parent, text="2. Preset, motor y voz", padding=6)
        f.pack(fill="x", pady=6)

        pre = ttk.Frame(f)
        pre.pack(fill="x", pady=(0, 6))
        ttk.Label(pre, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_cb = ttk.Combobox(pre, textvariable=self.preset_var, state="readonly", width=28)
        self.preset_cb.pack(side="left", padx=6)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda e: self.apply_preset(self.preset_var.get()))
        ttk.Button(pre, text="Guardar como…", command=self.save_preset).pack(side="left", padx=2)
        ttk.Button(pre, text="Borrar", command=self.delete_preset).pack(side="left", padx=2)
        ttk.Button(pre, text="Configurar RVC…", command=self.configure_rvc).pack(side="right")

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Motor:").pack(side="left")
        self.engine_names = {e.name: e.id for e in ENGINES.values()}
        self.engine_var = tk.StringVar(value=ENGINES["praat"].name)
        cb = ttk.Combobox(top, textvariable=self.engine_var, state="readonly", width=34,
                          values=list(self.engine_names))
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_engine_change())
        ttk.Button(top, text="Valores iniciales", command=self.reset_params).pack(side="left", padx=6)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Reprocesar al soltar un slider", variable=self.auto_var).pack(side="left", padx=6)
        self.engine_desc = tk.StringVar()
        ttk.Label(f, textvariable=self.engine_desc, foreground="#555", wraplength=760).pack(anchor="w", pady=(4, 0))
        self.sliders_frame = ttk.Frame(f)
        self.sliders_frame.pack(fill="x")
        self.on_engine_change(process=False)

    def _build_post(self, parent):
        f = ttk.LabelFrame(parent, text="3. Post-proceso (todo desactivable)", padding=6)
        f.pack(fill="x")
        d = post.POST_DEFAULTS
        rows = [
            ("hpf_on", "Pasa altos (quita retumbe/golpes)", [("hpf_hz", "Hz")]),
            ("eq_on", "EQ de presencia (claridad)", [("eq_hz", "Hz"), ("eq_db", "dB")]),
            ("deess_on", "De-esser (suaviza las eses)", [("deess_hz", "Hz desde"), ("deess_max_db", "dB máx.")]),
            ("comp_on", "Compresión leve", [("comp_ratio", ": 1")]),
            ("loud_on", "Normalizar loudness", [("loud_lufs", "LUFS")]),
        ]
        for r, (key, label, fields) in enumerate(rows):
            v = tk.BooleanVar(value=d[key])
            self.post_vars[key] = v
            ttk.Checkbutton(f, text=label, variable=v, command=self.auto_process).grid(row=r, column=0, sticky="w")
            c = 1
            for fk, unit in fields:
                sv = tk.StringVar(value=f"{d[fk]:g}")
                self.post_vars[fk] = sv
                e = ttk.Entry(f, textvariable=sv, width=7, justify="right")
                e.grid(row=r, column=c, padx=(12, 2))
                e.bind("<Return>", lambda ev: self.auto_process())
                e.bind("<FocusOut>", lambda ev: self.auto_process())
                ttk.Label(f, text=unit).grid(row=r, column=c + 1, sticky="w")
                c += 2
        # Recién ahora existen todos los controles: cargar el preset de base.
        self.refresh_presets(select=preset_store.DEFAULT_PRESET, apply=True)

    def _build_playback(self, parent):
        f = ttk.LabelFrame(parent, text="4. Escuchar y comparar", padding=6)
        f.pack(fill="x", pady=6)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Salida:").pack(side="left")
        try:
            devs, default = list_devices("output")
        except Exception:  # noqa: BLE001
            devs, default = [], None
        self.out_devices = {label: idx for idx, label in devs}
        default_label = next((l for i, l in devs if i == default), devs[0][1] if devs else "")
        self.out_var = tk.StringVar(value=default_label)
        cb = ttk.Combobox(row, textvariable=self.out_var, state="readonly", width=60, values=[l for _, l in devs])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.player.set_device(self.out_devices[self.out_var.get()]))
        self.player.set_device(self.out_devices.get(default_label))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Procesar", command=self.process).pack(side="left")
        self.btn_a = ttk.Button(row, text="▶ A: original", command=lambda: self.switch("A"))
        self.btn_a.pack(side="left", padx=(16, 4))
        self.btn_b = ttk.Button(row, text="▶ B: VERA", command=lambda: self.switch("B"))
        self.btn_b.pack(side="left", padx=4)
        ttk.Button(row, text="■ Stop", command=self.player.stop).pack(side="left", padx=4)
        self.loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Repetir", variable=self.loop_var,
                        command=lambda: setattr(self.player, "loop", self.loop_var.get())).pack(side="left", padx=8)
        self.player.loop = True
        self.match_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Igualar volumen A/B", variable=self.match_var,
                        command=self.update_tracks).pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x")
        self.now_playing = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.now_playing, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(row, text="Mandar a Audio Mixer", command=self.send_to_mixer).pack(side="right", padx=(4, 0))
        ttk.Button(row, text="Guardar procesado…", command=self.save_processed).pack(side="right")
        ttk.Label(f, text="Atajos: A / B cambian al instante sin cortar la frase · Espacio = reproducir/parar"
                          " · R = grabar/parar", foreground="#555").pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------ interfaz: lote
    def _build_batch(self, parent):
        ttk.Label(parent, foreground="#555", wraplength=760, justify="left", text=(
            "Procesa todos los audios de una carpeta con un preset. Los originales no se tocan: "
            "el resultado va a la carpeta de salida (WAV 48 kHz / 24 bit, misma duración exacta) "
            "con un informe.csv que se abre con Excel.")).pack(anchor="w", pady=(0, 8))
        g = ttk.Frame(parent)
        g.pack(fill="x")
        self.batch_in = tk.StringVar()
        self.batch_out = tk.StringVar(value=OUT_DIR)
        for r, (label, var) in enumerate((("Carpeta con las tomas:", self.batch_in),
                                          ("Carpeta de salida:", self.batch_out))):
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(g, textvariable=var, width=70).grid(row=r, column=1, padx=6, sticky="we")
            ttk.Button(g, text="Elegir…", command=lambda v=var: self._pick_dir(v)).grid(row=r, column=2)
        ttk.Label(g, text="Preset:").grid(row=2, column=0, sticky="w", pady=2)
        self.batch_preset = tk.StringVar(value=CURRENT_SETTINGS)
        self.batch_preset_cb = ttk.Combobox(g, textvariable=self.batch_preset, state="readonly", width=40)
        self.batch_preset_cb.grid(row=2, column=1, padx=6, sticky="w")
        ttk.Label(g, text="Agregar al nombre:").grid(row=3, column=0, sticky="w", pady=2)
        self.batch_suffix = tk.StringVar(value="_VERA")
        ttk.Entry(g, textvariable=self.batch_suffix, width=14).grid(row=3, column=1, padx=6, sticky="w")
        g.columnconfigure(1, weight=1)

        opts = ttk.Frame(parent)
        opts.pack(fill="x", pady=6)
        self.batch_recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Incluir subcarpetas", variable=self.batch_recursive).pack(side="left")
        self.batch_skip = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Saltear los que ya están procesados", variable=self.batch_skip).pack(
            side="left", padx=12)

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        self.batch_btn = ttk.Button(row, text="Procesar lote", command=self.start_batch)
        self.batch_btn.pack(side="left")
        self.batch_stop_btn = ttk.Button(row, text="Cancelar", command=self.cancel_batch, state="disabled")
        self.batch_stop_btn.pack(side="left", padx=6)
        ttk.Button(row, text="Abrir carpeta de salida",
                   command=lambda: open_folder(self.batch_out.get() or OUT_DIR)).pack(side="left", padx=6)
        self.batch_bar = ttk.Progressbar(row, length=260)
        self.batch_bar.pack(side="left", padx=8)
        self.batch_info = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.batch_info).pack(side="left")

        cols = ("archivo", "duracion", "lufs", "pico", "estado")
        heads = ("Archivo", "Duración (s)", "LUFS", "Pico (dBTP)", "Estado")
        widths = (260, 90, 60, 80, 260)
        tv_frame = ttk.Frame(parent)
        tv_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.batch_tv = ttk.Treeview(tv_frame, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, heads, widths):
            self.batch_tv.heading(c, text=h)
            self.batch_tv.column(c, width=w, anchor="w" if c in ("archivo", "estado") else "e")
        sb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.batch_tv.yview)
        self.batch_tv.configure(yscrollcommand=sb.set)
        self.batch_tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.refresh_presets()

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or APP_DIR)
        if d:
            var.set(d)

    # ------------------------------------------------------------ motor
    def current_engine(self):
        return ENGINES[self.engine_names[self.engine_var.get()]]

    def on_engine_change(self, process=True):
        engine = self.current_engine()
        ok, why = engine.is_available()
        self.engine_desc.set(engine.description if ok else f"⚠ {why}")
        old = {k: v.get() for k, v in self.param_vars.items()}
        for w in self.sliders_frame.winfo_children():
            w.destroy()
        self.param_vars = {}
        for r, p in enumerate(engine.params):
            ttk.Label(self.sliders_frame, text=f"{p.label} ({p.unit})" if p.unit else p.label).grid(
                row=r, column=0, sticky="w", pady=2)
            v = tk.DoubleVar(value=old.get(p.key, p.default))
            s = tk.Scale(self.sliders_frame, variable=v, from_=p.minimum, to=p.maximum,
                         resolution=p.resolution, orient="horizontal", length=360)
            s.grid(row=r, column=1, padx=8)
            s.bind("<ButtonRelease-1>", lambda e: self.auto_process())
            ttk.Label(self.sliders_frame, text=p.help, foreground="#555", wraplength=230).grid(
                row=r, column=2, sticky="w")
            self.param_vars[p.key] = v
        if ok and process:
            self.auto_process()

    def reset_params(self):
        for p in self.current_engine().params:
            self.param_vars[p.key].set(p.default)
        self.auto_process()

    def read_post(self):
        p = {}
        for k, default in post.POST_DEFAULTS.items():
            v = self.post_vars[k].get()
            if isinstance(default, bool):
                p[k] = bool(v)
            else:
                try:
                    p[k] = float(str(v).replace(",", "."))
                except ValueError:
                    p[k] = default
                    self.post_vars[k].set(f"{default:g}")
        return p

    def current_settings(self):
        return preset_store.clean_preset({"engine": self.current_engine().id,
                                          "params": {k: v.get() for k, v in self.param_vars.items()},
                                          "post": self.read_post()})

    # ------------------------------------------------------------ presets (fase 3)
    def refresh_presets(self, select=None, apply=False):
        names = preset_store.list_presets()
        self.preset_cb.configure(values=names)
        if hasattr(self, "batch_preset_cb"):
            self.batch_preset_cb.configure(values=[CURRENT_SETTINGS, *names])
            if self.batch_preset.get() not in (CURRENT_SETTINGS, *names):
                self.batch_preset.set(CURRENT_SETTINGS)
        if select in names:
            self.preset_var.set(select)
            if apply:
                self.apply_preset(select, process=False)
        elif self.preset_var.get() not in names:
            self.preset_var.set("")

    def apply_preset(self, name, process=True):
        try:
            p = preset_store.load_preset(name)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Preset", f"No pude leer el preset «{name}»:\n{e}")
            return
        self.engine_var.set(ENGINES[p["engine"]].name)
        self.on_engine_change(process=False)
        for k, v in p["params"].items():
            if k in self.param_vars:
                self.param_vars[k].set(v)
        for k, v in p["post"].items():
            self.post_vars[k].set(v if isinstance(v, bool) else f"{v:g}")
        self.status.set(f"Preset «{name}» cargado.")
        if process:
            self.auto_process()

    def save_preset(self):
        name = simpledialog.askstring("Guardar preset", "Nombre del preset:",
                                      initialvalue=self.preset_var.get() or "Mi VERA", parent=self.root)
        if not name:
            return
        clean = preset_store.safe_name(name)
        if clean in preset_store.list_presets() and not messagebox.askyesno(
                "Guardar preset", f"Ya existe «{clean}». ¿Lo reemplazo?"):
            return
        try:
            s = self.current_settings()
            saved = preset_store.save_preset(name, s["engine"], s["params"], s["post"])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Guardar preset", str(e))
            return
        self.refresh_presets(select=saved)
        self.status.set(f"Preset «{saved}» guardado en presets/{saved}.json")

    def delete_preset(self):
        name = self.preset_var.get()
        if not name:
            return
        if messagebox.askyesno("Borrar preset", f"¿Borrar el preset «{name}»?"):
            preset_store.delete_preset(name)
            self.refresh_presets()
            self.status.set(f"Preset «{name}» borrado.")

    # ------------------------------------------------------------ RVC (fase 5)
    def configure_rvc(self):
        RvcConfigDialog(self.root, on_save=lambda: self.on_engine_change(process=False))

    # ------------------------------------------------------------ acciones
    def open_file(self):
        path = filedialog.askopenfilename(title="Elegí una toma de voz",
                                          filetypes=[("Audio", "*.wav *.flac *.aiff *.aif *.ogg"), ("Todos", "*.*")])
        if not path:
            return
        try:
            audio, sr_orig = load_audio(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("No pude abrir el archivo", str(e))
            return
        self.set_original(audio, path, sr_orig)

    def set_original(self, audio, path, sr_orig=SR):
        self.player.stop()
        self.original, self.processed, self.source_path = audio, None, path
        try:
            med = pitch_median(audio, SR)
        except Exception:  # noqa: BLE001
            med = 0.0
        extra = "" if sr_orig == SR else f" (convertido de {sr_orig} Hz a 48 kHz)"
        tone = f" · tu tono mediano: {med:.0f} Hz" if med else ""
        self.file_info.set(f"{os.path.basename(path)} · {len(audio) / SR:.1f} s{tone}{extra}")
        self.process()

    # ---- grabar (fase 2)
    def toggle_record(self):
        if self.recorder.recording:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        label = self.in_var.get()
        if label not in self.in_devices:
            messagebox.showerror("Micrófono", "No encontré ningún micrófono. Conectalo y volvé a abrir VERA.")
            return
        self.player.stop()  # que no se grabe lo que suena por los parlantes
        try:
            self.recorder.start(self.in_devices[label])
        except Exception as e:  # noqa: BLE001
            log_error(traceback.format_exc())
            messagebox.showerror("Micrófono", f"No pude grabar con ese micrófono:\n{e}")
            return
        self.rec_btn.configure(text="■ Parar")
        self.status.set("Grabando… (R o ■ Parar para terminar)")

    def stop_record(self):
        audio = self.recorder.stop()
        self.rec_btn.configure(text="● Grabar")
        self.vu["value"] = 0
        if audio is None or len(audio) < int(0.3 * SR):
            self.rec_info.set("")
            self.status.set("La toma fue demasiado corta; no la guardé.")
            return
        os.makedirs(TAKES_DIR, exist_ok=True)
        path = unique_path(TAKES_DIR, time.strftime("toma_%Y%m%d-%H%M%S"))
        save_wav(path, audio)  # el crudo queda guardado siempre
        clipped = self.recorder.peak_hold_db > -0.5
        self.rec_info.set(f"{len(audio) / SR:.1f} s grabados")
        self.set_original(audio, path)
        if clipped:
            messagebox.showwarning("Saturó", "La toma llegó al máximo y puede estar distorsionada.\n"
                                             "Bajá la ganancia del micrófono o alejate un poco.")

    # ---- procesar
    def auto_process(self):
        if self.auto_var.get() and not self.current_engine().slow:
            self.process()

    def process(self):
        if self.original is None:
            return
        engine = self.current_engine()
        ok, why = engine.is_available()
        if not ok:
            self.status.set(why)
            return
        if self.busy:
            self.pending = True
            return
        self.busy = True
        params = {k: v.get() for k, v in self.param_vars.items()}
        post_params = self.read_post()
        self.status.set(f"Procesando con {engine.name}…")
        threading.Thread(target=self._worker, args=(engine, self.original, params, post_params),
                         daemon=True).start()

    def _worker(self, engine, audio, params, post_params):
        try:
            t0 = time.perf_counter()
            y = engine.process(audio, SR, params)
            t1 = time.perf_counter()
            y, info = post.run_chain(y, SR, post_params)
            info["t_engine"], info["t_post"] = t1 - t0, time.perf_counter() - t1
            info["engine"] = engine.id
            self.results.put(("ok", y, info))
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            log_error(tb)
            self.results.put(("error", tb.strip().splitlines()[-1], None))

    def _poll(self):
        try:
            while True:
                kind, data, info = self.results.get_nowait()
                self.busy = False
                if kind == "ok":
                    self.processed = data
                    self.processed_engine = info["engine"]
                    self.update_tracks()
                    msg = (f"Listo. Motor {info['t_engine']:.1f} s + post {info['t_post']:.1f} s · "
                           f"{info['lufs']:.1f} LUFS · pico {info['peak_db']:.1f} dBTP")
                    if "limited_db" in info:
                        msg += f" · bajé {info['limited_db']:.1f} dB para no saturar"
                    self.status.set(msg)
                else:
                    self.status.set(f"Error al procesar: {data}  (detalle en errores.log)")
                if self.pending:
                    self.pending = False
                    self.process()
        except queue.Empty:
            pass
        self._poll_batch()
        if self.recorder.recording:
            self.vu["value"] = max(0.0, 60 + self.recorder.level_db)
            self.rec_info.set(f"● {self.recorder.elapsed_s():.1f} s")
        elif self.recorder.stream is not None:
            self.stop_record()  # llegó al tope de duración
        if self.player.playing:
            label = "A: ORIGINAL" if self.player.which == "A" else "B: VERA"
            self.now_playing.set(f"Sonando {label}   {self.player.position_s():.1f} / {self.player.length_s():.1f} s")
        else:
            self.now_playing.set("")
        self.root.after(50, self._poll)

    def update_tracks(self):
        if self.original is None or self.processed is None:
            return
        a = self.original
        if self.match_var.get():
            # Para comparar justo: el más fuerte siempre "parece mejor".
            la, lb = post.loudness(a, SR), post.loudness(self.processed, SR)
            if np.isfinite(la) and np.isfinite(lb):
                a = a * 10 ** ((lb - la) / 20)
                peak = np.max(np.abs(a))
                if peak > 0.99:
                    a = a * 0.99 / peak
        was_playing, pos = self.player.playing, self.player.pos
        self.player.set_tracks(a, self.processed)
        if was_playing:
            self.player.pos = pos

    def switch(self, which):
        if self.processed is None:
            return
        try:
            self.player.switch(which)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Error de audio", f"No pude reproducir en ese dispositivo:\n{e}")

    def toggle_play(self):
        if self.player.playing:
            self.player.stop()
        else:
            self.switch(self.player.which)

    def _default_name(self):
        base = os.path.splitext(os.path.basename(self.source_path))[0]
        return f"{base}_VERA_{self.processed_engine or self.current_engine().id}"

    def save_processed(self):
        if self.processed is None:
            return
        os.makedirs(TESTS_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(initialdir=TESTS_DIR, initialfile=self._default_name() + ".wav",
                                            defaultextension=".wav", filetypes=[("WAV 24 bit", "*.wav")])
        if not path:
            return
        if self.source_path and os.path.abspath(path) == os.path.abspath(self.source_path):
            messagebox.showerror("Cuidado", "Ese es el archivo original. Elegí otro nombre: el crudo no se pisa.")
            return
        save_wav(path, self.processed)
        self.status.set(f"Guardado: {path}")

    def send_to_mixer(self):
        """Guarda el resultado en pruebas/ y lo abre en Audio Mixer para editarlo/mezclarlo."""
        if self.processed is None:
            self.status.set("Primero abrí o grabá una toma y procesala.")
            return
        os.makedirs(TESTS_DIR, exist_ok=True)
        path = unique_path(TESTS_DIR, self._default_name())
        save_wav(path, self.processed)
        try:
            launch_mixer([path])
        except Exception as e:  # noqa: BLE001
            log_error(traceback.format_exc())
            messagebox.showerror("Audio Mixer", f"Guardé {path}\npero no pude abrir Audio Mixer:\n{e}")
            return
        self.status.set(f"Abriendo Audio Mixer con {os.path.basename(path)}…")

    # ------------------------------------------------------------ lote (fase 4)
    def start_batch(self):
        if self.batch_cancel is not None:
            return
        in_dir, out_dir = self.batch_in.get().strip(), self.batch_out.get().strip() or OUT_DIR
        if not os.path.isdir(in_dir):
            messagebox.showerror("Lote", "Elegí la carpeta con las tomas.")
            return
        if os.path.abspath(in_dir) == os.path.abspath(out_dir) and not self.batch_suffix.get().strip():
            messagebox.showerror("Lote", "La salida es la misma carpeta que la entrada y sin nada agregado "
                                         "al nombre: se pisarían los originales. Cambiá una de las dos cosas.")
            return
        name = self.batch_preset.get()
        try:
            preset = self.current_settings() if name == CURRENT_SETTINGS else preset_store.load_preset(name)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Lote", f"No pude leer el preset:\n{e}")
            return
        ok, why = ENGINES[preset["engine"]].is_available()
        if not ok:
            messagebox.showerror("Lote", why)
            return
        n = len(batch.find_files(in_dir, self.batch_recursive.get()))
        if n == 0:
            messagebox.showinfo("Lote", "No encontré audios (.wav, .flac, .aiff, .ogg) en esa carpeta.")
            return
        self.batch_tv.delete(*self.batch_tv.get_children())
        self.batch_bar.configure(maximum=n, value=0)
        self.batch_info.set(f"0 / {n}")
        self.batch_btn.configure(state="disabled")
        self.batch_stop_btn.configure(state="normal")
        self.batch_cancel = threading.Event()
        args = dict(in_dir=in_dir, out_dir=out_dir, preset=preset, suffix=self.batch_suffix.get().strip(),
                    recursive=self.batch_recursive.get(), skip_existing=self.batch_skip.get(),
                    progress=lambda i, total, row: self.batch_q.put(("row", i, total, row)),
                    cancel=self.batch_cancel)
        threading.Thread(target=self._batch_worker, args=(args,), daemon=True).start()
        self.status.set(f"Lote: procesando {n} archivo(s) con {ENGINES[preset['engine']].name}…")

    def _batch_worker(self, args):
        try:
            rows = batch.run(**args)
            self.batch_q.put(("done", rows, args["out_dir"], args["cancel"].is_set()))
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            log_error(tb)
            self.batch_q.put(("fail", tb.strip().splitlines()[-1], None, None))

    def cancel_batch(self):
        if self.batch_cancel is not None:
            self.batch_cancel.set()
            self.batch_info.set("Cancelando (termino el archivo actual)…")

    def _poll_batch(self):
        try:
            while True:
                msg = self.batch_q.get_nowait()
                if msg[0] == "row":
                    _, i, total, row = msg
                    if "detalle" in row:
                        log_error(f"Lote: {row['archivo']}\n{row['detalle']}")
                    self.batch_tv.insert("", "end", values=(row["archivo"], row["duracion_s"], row["lufs"],
                                                            row["pico_dbtp"], row["estado"]))
                    self.batch_tv.yview_moveto(1.0)
                    self.batch_bar["value"] = i
                    self.batch_info.set(f"{i} / {total}")
                else:
                    self.batch_cancel = None
                    self.batch_btn.configure(state="normal")
                    self.batch_stop_btn.configure(state="disabled")
                    if msg[0] == "fail":
                        self.status.set(f"Lote: error {msg[1]} (detalle en errores.log)")
                    else:
                        _, rows, out_dir, cancelled = msg
                        errors = sum(r["estado"].startswith("ERROR") for r in rows)
                        self.status.set(
                            f"Lote {'cancelado' if cancelled else 'terminado'}: {len(rows)} archivo(s), "
                            f"{errors} con error. Informe: {os.path.join(out_dir, 'informe.csv')}")
        except queue.Empty:
            pass

    def on_close(self):
        if self.batch_cancel is not None:
            if not messagebox.askyesno("Salir", "Hay un lote procesándose. ¿Salir igual?"):
                return
            self.batch_cancel.set()
        self.recorder.stop()
        self.player.stop()
        self.root.destroy()


class RvcConfigDialog(tk.Toplevel):
    """Rutas de Applio y del modelo para el motor RVC (se guardan en config_local.json)."""

    def __init__(self, master, on_save=None):
        super().__init__(master)
        self.title("Configurar RVC (Applio)")
        self.transient(master)
        self.on_save = on_save
        self.cfg = rvc_applio.load_config()
        self.vars = {k: tk.StringVar(value=v) for k, v in self.cfg.items()}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        rows = (
            ("applio_dir", "Carpeta de Applio (la que tiene core.py):", "dir"),
            ("pth_path", "Modelo de voz (.pth):", ("Modelo RVC", "*.pth")),
            ("index_path", "Índice (.index, opcional):", ("Índice RVC", "*.index")),
            ("python", "Python de Applio (vacío = buscar solo):", ("Python", "python*.exe python*")),
        )
        for r, (key, label, kind) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=self.vars[key], width=60).grid(row=r, column=1, padx=6)
            ttk.Button(f, text="Elegir…", command=lambda k=key, t=kind: self.pick(k, t)).grid(row=r, column=2)
        r = len(rows)
        ttk.Label(f, text="Detector de tono:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.vars["f0_method"], values=rvc_applio.F0_METHODS,
                     width=14).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Opciones extra (avanzado):").grid(row=r + 1, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.vars["extra_args"], width=60).grid(row=r + 1, column=1, padx=6)
        self.check = tk.StringVar()
        ttk.Label(f, textvariable=self.check, foreground="#555", wraplength=620).grid(
            row=r + 2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.grid(row=r + 3, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(b, text="Revisar", command=self.verify).pack(side="left", padx=4)
        ttk.Button(b, text="Guardar", command=self.save).pack(side="left", padx=4)
        ttk.Button(b, text="Cancelar", command=self.destroy).pack(side="left")
        self.verify()
        self.grab_set()

    def current(self):
        return {k: v.get().strip() for k, v in self.vars.items()}

    def pick(self, key, kind):
        if kind == "dir":
            p = filedialog.askdirectory(parent=self, initialdir=self.vars[key].get() or None)
        else:
            p = filedialog.askopenfilename(parent=self, filetypes=[kind, ("Todos", "*.*")])
        if p:
            self.vars[key].set(os.path.normpath(p))
            self.verify()

    def verify(self):
        ok, why = rvc_applio.check_config(self.current())
        py = rvc_applio.find_applio_python(self.current()) if self.vars["applio_dir"].get() else ""
        self.check.set(("✔ Todo listo. " + (f"Python de Applio: {py}" if py else "")) if ok else f"⚠ {why}")

    def save(self):
        rvc_applio.save_config(self.current())
        if self.on_save:
            self.on_save()
        self.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
