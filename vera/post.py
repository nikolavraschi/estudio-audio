"""Cadena de post-proceso suave. Cada etapa se puede apagar.

Orden: pasa altos -> EQ de presencia -> de-esser -> compresión -> loudness.
(El de-esser va después de la EQ porque la presencia realza las eses.)
"""
import numpy as np
import pyloudnorm as pyln
from scipy.signal import butter, resample_poly, sosfilt, sosfiltfilt

POST_DEFAULTS = {
    "hpf_on": True, "hpf_hz": 90.0,
    "eq_on": True, "eq_hz": 3000.0, "eq_db": 2.0,
    "deess_on": True, "deess_hz": 6000.0, "deess_max_db": 6.0,
    "comp_on": True, "comp_ratio": 2.0,
    "loud_on": True, "loud_lufs": -23.0,
}

# Techo de pico real tras normalizar: -1 dBTP deja margen para conversiones/codecs.
PEAK_CEILING_DB = -1.0


def _block_db(x, blk):
    nb = int(np.ceil(len(x) / blk))
    padded = np.pad(x, (0, nb * blk - len(x)))
    rms = np.sqrt(np.mean(padded.reshape(nb, blk) ** 2, axis=1))
    return 20 * np.log10(rms + 1e-12)


def _smooth_reduction(red, block_s, attack_s, release_s):
    a_att = np.exp(-block_s / attack_s)
    a_rel = np.exp(-block_s / release_s)
    out = np.empty_like(red)
    g = 0.0
    for i, r in enumerate(red):
        a = a_att if r > g else a_rel
        g = a * g + (1 - a) * r
        out[i] = g
    return out


def _apply_block_gain_db(x, blk, gain_db):
    centers = (np.arange(len(gain_db)) + 0.5) * blk
    g = np.interp(np.arange(len(x)), centers, 10 ** (gain_db / 20))
    return x * g


def highpass(x, sr, hz):
    # 2º orden aplicado ida y vuelta = 24 dB/oct efectivos, sin desfase ni retardo.
    sos = butter(2, hz, "highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, x)


def presence_eq(x, sr, hz, gain_db, q=0.9):
    """Campana (peaking) estilo RBJ: suma claridad/inteligibilidad en 2-4 kHz."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * hz / sr
    alpha = np.sin(w0) / (2 * q)
    cw = np.cos(w0)
    b = np.array([1 + alpha * A, -2 * cw, 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * cw, 1 - alpha / A])
    sos = np.concatenate([b / a[0], a / a[0]])[None, :]
    return sosfilt(sos, x)


def deess(x, sr, hz, max_red_db, threshold_rel_db=-14.0):
    """Baja la banda aguda solo cuando domina la señal (eses, 'sh', 'ch').

    Compara la energía de agudos con la total, así no depende del volumen de grabación.
    """
    sos = butter(4, hz, "highpass", fs=sr, output="sos")
    hi = sosfiltfilt(sos, x)
    lo = x - hi
    blk = max(1, int(sr * 0.003))
    full_db = _block_db(x, blk)
    rel = _block_db(hi, blk) - full_db
    red = np.clip(rel - threshold_rel_db, 0.0, max_red_db)
    red[full_db < -60] = 0.0
    red = _smooth_reduction(red, blk / sr, 0.001, 0.04)
    return lo + _apply_block_gain_db(hi, blk, -red)


def compress(x, sr, ratio, threshold_rel_db=-10.0, attack_s=0.01, release_s=0.15):
    """Compresión leve. El umbral se fija relativo a los picos de la frase."""
    blk = max(1, int(sr * 0.005))
    lvl = _block_db(x, blk)
    active = lvl[lvl > -60]
    if len(active) == 0:
        return x
    thr = np.percentile(active, 90) + threshold_rel_db
    red = np.maximum(0.0, lvl - thr) * (1 - 1 / ratio)
    red = _smooth_reduction(red, blk / sr, attack_s, release_s)
    return _apply_block_gain_db(x, blk, -red)


def true_peak_db(x):
    return 20 * np.log10(np.max(np.abs(resample_poly(x, 4, 1))) + 1e-12)


def loudness(x, sr):
    meter = pyln.Meter(sr)
    m = x if len(x) >= int(0.5 * sr) else np.pad(x, (0, int(0.5 * sr) - len(x)))
    return meter.integrated_loudness(m)


def run_chain(x, sr, p):
    """Aplica la cadena. Devuelve (audio, info) con datos para mostrar."""
    y = np.asarray(x, dtype=np.float64)
    info = {}
    if p.get("hpf_on"):
        y = highpass(y, sr, p["hpf_hz"])
    if p.get("eq_on") and p["eq_db"] != 0:
        y = presence_eq(y, sr, p["eq_hz"], p["eq_db"])
    if p.get("deess_on"):
        y = deess(y, sr, p["deess_hz"], p["deess_max_db"])
    if p.get("comp_on") and p["comp_ratio"] > 1:
        y = compress(y, sr, p["comp_ratio"])
    if p.get("loud_on"):
        lufs = loudness(y, sr)
        if np.isfinite(lufs):
            y = y * 10 ** ((p["loud_lufs"] - lufs) / 20)
    # Protección final contra saturación (siempre activa).
    tp = true_peak_db(y)
    if tp > PEAK_CEILING_DB:
        y = y * 10 ** ((PEAK_CEILING_DB - tp) / 20)
        info["limited_db"] = tp - PEAK_CEILING_DB
    info["lufs"] = loudness(y, sr)
    info["peak_db"] = true_peak_db(y)
    return y, info
