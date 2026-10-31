"""Estudio de audio: ventana de inicio para abrir VERA o Audio Mixer.

Cada app se abre en su propio proceso (VERA usa Tkinter y Audio Mixer usa PySide6),
así si una se cuelga la otra sigue funcionando.
"""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APPS = {
    "vera": (os.path.join(ROOT_DIR, "vera", "app.py"),
             "VERA · transformar voz",
             "Convierte tus tomas en la voz de VERA. Probar con A/B, grabar con el micrófono, "
             "presets, lote de una carpeta entera y RVC."),
    "mixer": (os.path.join(ROOT_DIR, "mixer", "audio_mixer.py"),
              "Audio Mixer · editar y mezclar",
              "Cortá fragmentos de varios audios, ordenalos, aplicá efectos (o la voz de VERA) "
              "y exportá un solo audio."),
}
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def gui_python():
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(w):
            return w
    return exe


def launch(key):
    script = APPS[key][0]
    if not os.path.isfile(script):
        messagebox.showerror("Estudio de audio", f"No encuentro {script}")
        return
    subprocess.Popen([gui_python(), script], cwd=os.path.dirname(script), creationflags=NOWIN)


def main():
    root = tk.Tk()
    root.title("Estudio de audio")
    root.resizable(False, False)
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    f = ttk.Frame(root, padding=16)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="Estudio de audio", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(f, text="Elegí qué abrir. Podés tener las dos abiertas a la vez.",
              foreground="#555").pack(anchor="w", pady=(0, 12))
    for key, (_, title, desc) in APPS.items():
        box = ttk.Frame(f, padding=(0, 6))
        box.pack(fill="x")
        ttk.Button(box, text=title, width=32, command=lambda k=key: launch(k)).pack(side="left")
        ttk.Label(box, text=desc, wraplength=330, foreground="#444").pack(side="left", padx=12)
    ttk.Button(f, text="Cerrar", command=root.destroy).pack(anchor="e", pady=(12, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
