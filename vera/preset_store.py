"""Fase 3: presets de VERA (motor + sliders + post-proceso) guardados en JSON.

Un preset es un archivo `presets/<nombre>.json` así:
    {"engine": "praat", "params": {...}, "post": {...}}
Lo usan la ventana de VERA, el procesamiento en lote y el puente con Audio Mixer.
"""
import json
import os
import re

import post
from engines import ENGINES

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PRESETS_DIR = os.path.join(APP_DIR, "presets")
DEFAULT_PRESET = "VERA base"


def _path(name):
    return os.path.join(PRESETS_DIR, f"{name}.json")


def safe_name(name):
    """Quita caracteres que Windows no acepta en nombres de archivo."""
    return re.sub(r'[\\/:*?"<>|]+', "", name).strip().rstrip(".")


def list_presets():
    if not os.path.isdir(PRESETS_DIR):
        return []
    names = [os.path.splitext(f)[0] for f in os.listdir(PRESETS_DIR) if f.lower().endswith(".json")]
    return sorted(names, key=lambda n: (n != DEFAULT_PRESET, n.lower()))


def clean_preset(data):
    """Completa con valores iniciales lo que falte y descarta lo desconocido.

    Así un preset viejo sigue funcionando aunque después se agreguen sliders.
    """
    engine_id = data.get("engine", "praat")
    if engine_id not in ENGINES:
        engine_id = "praat"
    engine = ENGINES[engine_id]
    params = engine.defaults()
    for k, v in (data.get("params") or {}).items():
        if k in params:
            params[k] = float(v)
    post_p = dict(post.POST_DEFAULTS)
    for k, v in (data.get("post") or {}).items():
        if k in post_p:
            post_p[k] = bool(v) if isinstance(post.POST_DEFAULTS[k], bool) else float(v)
    return {"engine": engine_id, "params": params, "post": post_p}


def load_preset(name):
    with open(_path(name), encoding="utf-8") as f:
        return clean_preset(json.load(f))


def save_preset(name, engine_id, params, post_params):
    name = safe_name(name)
    if not name:
        raise ValueError("El nombre del preset está vacío.")
    os.makedirs(PRESETS_DIR, exist_ok=True)
    data = clean_preset({"engine": engine_id, "params": params, "post": post_params})
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return name


def delete_preset(name):
    if os.path.exists(_path(name)):
        os.remove(_path(name))


def transform(audio, sr, preset):
    """Aplica motor + post-proceso de un preset. Devuelve (audio, info).

    `audio` es mono float a `sr` Hz (VERA trabaja a 48 kHz). La duración no cambia.
    """
    engine = ENGINES[preset["engine"]]
    ok, why = engine.is_available()
    if not ok:
        raise RuntimeError(why)
    y = engine.process(audio, sr, preset["params"])
    return post.run_chain(y, sr, preset["post"])
