"""Fase 4: procesar una carpeta entera con un preset.

- Nunca pisa los originales: la salida va a otra carpeta (por defecto `salida/`).
- Respeta las subcarpetas de la entrada.
- Verifica que cada archivo dure exactamente lo mismo que el original.
- Deja un `informe.csv` en la carpeta de salida (se abre con Excel).
"""
import csv
import os
import time
import traceback

import preset_store
from audio_io import SR, load_audio, save_wav

AUDIO_EXT = (".wav", ".flac", ".aiff", ".aif", ".ogg")
# Tolerancia al comparar duraciones: medio milisegundo (por redondeos al convertir de 44.1 a 48 kHz).
DURATION_TOL_S = 0.0005


def find_files(folder, recursive=True):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(AUDIO_EXT):
                out.append(os.path.join(root, f))
        if not recursive:
            break
    return out


def output_path(src, in_dir, out_dir, suffix):
    rel = os.path.relpath(src, in_dir)
    base = os.path.splitext(rel)[0]
    return os.path.join(out_dir, f"{base}{suffix}.wav")


def run(in_dir, out_dir, preset, suffix="_VERA", recursive=True, skip_existing=False,
        progress=None, cancel=None):
    """Procesa la carpeta. `progress(i, total, fila)` se llama después de cada archivo.

    `cancel` es un threading.Event opcional. Devuelve la lista de filas del informe.
    """
    in_dir, out_dir = os.path.abspath(in_dir), os.path.abspath(out_dir)
    files = find_files(in_dir, recursive)
    # Si la salida está dentro de la entrada, no volver a procesar lo ya procesado.
    files = [f for f in files if not os.path.abspath(f).startswith(out_dir + os.sep)]
    rows = []
    for i, src in enumerate(files, 1):
        if cancel is not None and cancel.is_set():
            break
        dst = output_path(src, in_dir, out_dir, suffix)
        row = {"archivo": os.path.relpath(src, in_dir), "salida": os.path.relpath(dst, out_dir),
               "duracion_s": "", "lufs": "", "pico_dbtp": "", "segundos_proceso": "", "estado": ""}
        t0 = time.perf_counter()
        try:
            if os.path.abspath(dst) == os.path.abspath(src):
                raise RuntimeError("la salida coincide con el original (no se pisa el crudo)")
            if skip_existing and os.path.exists(dst):
                row["estado"] = "salteado (ya existía)"
            else:
                audio, sr_orig = load_audio(src)
                y, info = preset_store.transform(audio, SR, preset)
                dur_in, dur_out = len(audio) / SR, len(y) / SR
                if abs(dur_in - dur_out) > DURATION_TOL_S:
                    raise RuntimeError(f"cambió la duración ({dur_in:.3f} s → {dur_out:.3f} s)")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                save_wav(dst, y)
                row.update(duracion_s=f"{dur_out:.3f}", lufs=f"{info['lufs']:.1f}",
                           pico_dbtp=f"{info['peak_db']:.1f}",
                           estado="ok" + (f" (bajé {info['limited_db']:.1f} dB para no saturar)"
                                          if "limited_db" in info else ""))
        except Exception as e:  # noqa: BLE001 - un archivo roto no frena el lote
            row["estado"] = f"ERROR: {e}"
            row["detalle"] = traceback.format_exc()
        row["segundos_proceso"] = f"{time.perf_counter() - t0:.1f}"
        rows.append(row)
        if progress:
            progress(i, len(files), row)
    if rows:
        write_report(out_dir, rows)
    return rows


def write_report(out_dir, rows):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "informe.csv")
    cols = ["archivo", "salida", "duracion_s", "lufs", "pico_dbtp", "segundos_proceso", "estado"]
    # utf-8-sig + ';' para que Excel en español lo abra bien con doble clic.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path
