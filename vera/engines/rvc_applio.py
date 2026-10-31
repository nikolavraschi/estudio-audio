"""Motor 3: RVC a través de Applio por línea de comandos (Fase 5).

Applio se instala aparte, con su propio Python. Este motor:
  1. guarda el audio en un WAV temporal,
  2. ejecuta  <python de Applio> core.py infer --input_path ... --output_path ...
  3. lee el resultado, lo pasa a 48 kHz y lo deja con la duración EXACTA del original.

Las rutas (carpeta de Applio, modelo .pth y .index) se guardan en
`config_local.json` (no se sube a git: son rutas de tu PC). Se configuran desde
la ventana de VERA con el botón "Configurar RVC…". Ver RVC.md.
"""
import json
import os
import shlex
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from .base import Param, VoiceEngine, match_length, resample

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config_local.json")
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

CONFIG_DEFAULTS = {
    "applio_dir": "",      # carpeta donde está core.py de Applio
    "python": "",          # vacío = se busca solo (env\python.exe, .venv, ...)
    "pth_path": "",        # modelo de voz entrenado
    "index_path": "",      # opcional, mejora el parecido
    "f0_method": "rmvpe",  # detector de tono de Applio: rmvpe, crepe, fcpe...
    "extra_args": "",      # opciones extra para "core.py infer", por si tu versión las pide
}

F0_METHODS = ("rmvpe", "crepe", "crepe-tiny", "fcpe")


def load_config():
    cfg = dict(CONFIG_DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update({k: str(v) for k, v in json.load(f).items() if k in CONFIG_DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    data = {k: str(cfg.get(k, v)) for k, v in CONFIG_DEFAULTS.items()}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_applio_python(cfg):
    """Python con el que corre Applio (su entorno, no el de VERA)."""
    if cfg["python"]:
        return cfg["python"] if os.path.isfile(cfg["python"]) else ""
    d = cfg["applio_dir"]
    for rel in (("env", "python.exe"), (".venv", "Scripts", "python.exe"), ("venv", "Scripts", "python.exe"),
                (".venv", "bin", "python"), ("venv", "bin", "python"), ("env", "bin", "python")):
        p = os.path.join(d, *rel)
        if os.path.isfile(p):
            return p
    return ""


def check_config(cfg):
    """Devuelve (ok, motivo). No ejecuta nada, solo revisa que existan los archivos."""
    if not cfg["applio_dir"] or not os.path.isfile(os.path.join(cfg["applio_dir"], "core.py")):
        return False, "RVC: falta elegir la carpeta de Applio (la que tiene core.py). Botón 'Configurar RVC…'."
    if not find_applio_python(cfg):
        return False, "RVC: no encontré el Python de Applio (env\\python.exe). Indicalo en 'Configurar RVC…'."
    if not cfg["pth_path"] or not os.path.isfile(cfg["pth_path"]):
        return False, "RVC: falta el modelo de voz .pth. Elegilo en 'Configurar RVC…'."
    if cfg["index_path"] and not os.path.isfile(cfg["index_path"]):
        return False, "RVC: el archivo .index configurado no existe."
    return True, ""


def build_command(cfg, in_wav, out_wav, params):
    cmd = [find_applio_python(cfg), os.path.join(cfg["applio_dir"], "core.py"), "infer",
           "--input_path", in_wav, "--output_path", out_wav,
           "--pth_path", cfg["pth_path"],
           "--pitch", str(int(round(float(params["pitch_shift"])))),
           "--index_rate", f"{float(params['index_rate']):g}",
           "--protect", f"{float(params['protect']):g}",
           "--f0_method", cfg["f0_method"] or "rmvpe",
           "--export_format", "WAV"]
    if cfg["index_path"]:
        cmd += ["--index_path", cfg["index_path"]]
    if cfg["extra_args"].strip():
        cmd += shlex.split(cfg["extra_args"], posix=(os.name != "nt"))
    return cmd


class RvcApplioEngine(VoiceEngine):
    id = "rvc"
    name = "RVC (Applio)"
    description = ("Conversión con un modelo de voz entrenado (vía Applio). Es lento: "
                   "apretá Procesar cuando termines de mover los sliders.")
    slow = True
    params = (
        Param("pitch_shift", "Transposición", -24, 24, 12, 1, "semitonos",
              "Hombre a mujer suele ser +12. Si el modelo ya es agudo, probá +8 a +10."),
        Param("index_rate", "Parecido al modelo (index rate)", 0.0, 1.0, 0.75, 0.05, "",
              "Más = más parecido al timbre del modelo, pero puede sumar artefactos."),
        Param("protect", "Proteger consonantes / respiración", 0.0, 0.5, 0.33, 0.01, "",
              "Más bajo protege más las 's', 't' y la respiración. 0.5 = sin protección."),
    )

    def is_available(self):
        return check_config(load_config())

    def process(self, audio, sr, params):
        cfg = load_config()
        ok, why = check_config(cfg)
        if not ok:
            raise RuntimeError(why)
        with tempfile.TemporaryDirectory(prefix="vera_rvc_") as tmp:
            in_wav = os.path.join(tmp, "entrada.wav")
            out_wav = os.path.join(tmp, "salida.wav")
            sf.write(in_wav, np.clip(audio, -1, 1), sr, subtype="PCM_16")
            cmd = build_command(cfg, in_wav, out_wav, params)
            p = subprocess.run(cmd, cwd=cfg["applio_dir"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", creationflags=NOWIN)
            if not os.path.isfile(out_wav):
                # Algunas versiones cambian el nombre de salida: tomar el único WAV nuevo.
                outs = [f for f in os.listdir(tmp) if f.lower().endswith(".wav") and f != "entrada.wav"]
                if len(outs) == 1:
                    out_wav = os.path.join(tmp, outs[0])
            if p.returncode != 0 or not os.path.isfile(out_wav):
                tail = "\n".join((p.stderr or p.stdout or "").strip().splitlines()[-15:])
                raise RuntimeError(f"Applio falló (código {p.returncode}).\nComando: {' '.join(cmd)}\n{tail}")
            y, y_sr = sf.read(out_wav, always_2d=True, dtype="float64")
        y = resample(y.mean(axis=1), y_sr, sr)
        return match_length(y, len(audio))
