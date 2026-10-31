"""VERA - transformador de voz para SEÑAL.  Fase 1: probar un WAV ya grabado."""
import os
import queue
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import numpy as np

import post
from audio_io import SR, ABPlayer, list_devices, load_audio, save_wav
from engines import ENGINES
from engines.praat_gender import pitch_median

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "errores.log")


def log_error(exc_text):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + exc_text + "\n")


class App:
    def __init__(self, root):
        self.root = root
        root.title("VERA - transformador de voz (SEÑAL)")
        root.minsize(760, 640)

        self.player = ABPlayer()
        self.original = None      # audio original 48 kHz mono
        self.processed = None     # audio procesado
        self.source_path = None
        self.results = queue.Queue()
        self.busy = False
        self.pending = False      # se pidió reprocesar mientras se procesaba
        self.param_vars = {}
        self.post_vars = {}

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Probar WAV")

        self._build_file(tab)
        self._build_engine(tab)
        self._build_post(tab)
        self._build_playback(tab)

        self.status = tk.StringVar(value="Abrí un WAV con tu voz para empezar.")
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w",
                  padding=4).pack(fill="x", side="bottom")

        root.bind("<KeyPress-a>", lambda e: self._hotkey(e, lambda: self.switch("A")))
        root.bind("<KeyPress-b>", lambda e: self._hotkey(e, lambda: self.switch("B")))
        root.bind("<space>", lambda e: self._hotkey(e, self.toggle_play))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._poll)

    def _hotkey(self, event, action):
        if not isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            action()

    # ------------------------------------------------------------ interfaz
    def _build_file(self, parent):
        f = ttk.LabelFrame(parent, text="1. Archivo", padding=6)
        f.pack(fill="x")
        ttk.Button(f, text="Abrir WAV…", command=self.open_file).pack(side="left")
        self.file_info = tk.StringVar(value="(ningún archivo)")
        ttk.Label(f, textvariable=self.file_info).pack(side="left", padx=10)

    def _build_engine(self, parent):
        f = ttk.LabelFrame(parent, text="2. Motor y voz", padding=6)
        f.pack(fill="x", pady=6)
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
        ttk.Label(f, textvariable=self.engine_desc, foreground="#555").pack(anchor="w", pady=(4, 0))
        self.sliders_frame = ttk.Frame(f)
        self.sliders_frame.pack(fill="x")
        self.on_engine_change()

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

    def _build_playback(self, parent):
        f = ttk.LabelFrame(parent, text="4. Escuchar y comparar", padding=6)
        f.pack(fill="x", pady=6)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Salida:").pack(side="left")
        devs, default = list_devices("output")
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
        ttk.Button(row, text="Guardar procesado…", command=self.save_processed).pack(side="right")
        ttk.Label(f, text="Atajos: A / B cambian al instante sin cortar la frase · Espacio = reproducir/parar",
                  foreground="#555").pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------ motor
    def current_engine(self):
        return ENGINES[self.engine_names[self.engine_var.get()]]

    def on_engine_change(self):
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
        if ok:
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

    # ------------------------------------------------------------ acciones
    def open_file(self):
        path = filedialog.askopenfilename(title="Elegí una toma de voz",
                                          filetypes=[("Audio", "*.wav *.flac *.aiff *.aif"), ("Todos", "*.*")])
        if not path:
            return
        try:
            audio, sr_orig = load_audio(path)
        except Exception as e:
            messagebox.showerror("No pude abrir el archivo", str(e))
            return
        self.player.stop()
        self.original, self.processed, self.source_path = audio, None, path
        med = pitch_median(audio, SR)
        extra = "" if sr_orig == SR else f" (convertido de {sr_orig} Hz a 48 kHz)"
        self.file_info.set(f"{os.path.basename(path)} · {len(audio) / SR:.1f} s · "
                           f"tu tono mediano: {med:.0f} Hz{extra}")
        self.process()

    def auto_process(self):
        if self.auto_var.get():
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
            self.results.put(("ok", y, info))
        except Exception:
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
        except Exception as e:
            messagebox.showerror("Error de audio", f"No pude reproducir en ese dispositivo:\n{e}")

    def toggle_play(self):
        if self.player.playing:
            self.player.stop()
        else:
            self.switch(self.player.which)

    def save_processed(self):
        if self.processed is None:
            return
        base = os.path.splitext(os.path.basename(self.source_path))[0]
        folder = os.path.join(APP_DIR, "pruebas")
        os.makedirs(folder, exist_ok=True)
        engine = self.current_engine().id
        path = filedialog.asksaveasfilename(initialdir=folder, initialfile=f"{base}_VERA_{engine}.wav",
                                            defaultextension=".wav", filetypes=[("WAV 24 bit", "*.wav")])
        if not path:
            return
        if self.source_path and os.path.abspath(path) == os.path.abspath(self.source_path):
            messagebox.showerror("Cuidado", "Ese es el archivo original. Elegí otro nombre: el crudo no se pisa.")
            return
        save_wav(path, self.processed)
        self.status.set(f"Guardado: {path}")

    def on_close(self):
        self.player.stop()
        self.root.destroy()


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
