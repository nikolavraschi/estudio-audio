"""Motor 2: vocoder WORLD (pyworld).

Descompone la voz en tres capas (tono, envolvente espectral = formantes,
aperiodicidad = aire/ruido), modifica tono y formantes por separado y
vuelve a sintetizar. Desplazamientos grandes suelen quedar más limpios que
en Praat, pero a veces suena un poco "zumbón".
"""
import numpy as np
import pyworld as pw

from .base import GENDER_PARAMS, VoiceEngine, match_length, resample

FRAME_PERIOD = 5.0  # ms
F0_FLOOR = 60.0
F0_CEIL = 600.0
ANALYSIS_SR = 16000  # el tono se detecta a 16 kHz: mismo resultado, mucho más rápido


def _warp_bins(env, ratio, log_domain):
    """Estira la envolvente en frecuencia: lo que estaba en f pasa a f*ratio."""
    n = env.shape[1]
    src = np.arange(n) / ratio
    i0 = np.floor(src).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    frac = src - i0
    data = np.log(np.maximum(env, 1e-16)) if log_domain else env
    out = data[:, i0] * (1.0 - frac) + data[:, i1] * frac
    return np.exp(out) if log_domain else out


class WorldEngine(VoiceEngine):
    id = "world"
    name = "WORLD vocoder"
    description = "Separa tono, formantes y aire. Muy controlable; a veces algo 'zumbón'."
    params = GENDER_PARAMS

    def process(self, audio, sr, params):
        x = np.ascontiguousarray(audio, dtype=np.float64)
        x16 = np.ascontiguousarray(resample(x, sr, ANALYSIS_SR))
        f0, t = pw.harvest(x16, ANALYSIS_SR, f0_floor=F0_FLOOR, f0_ceil=F0_CEIL,
                           frame_period=FRAME_PERIOD)
        voiced = f0 > 0
        if not voiced.any():
            return np.array(audio, copy=True)

        sp = pw.cheaptrick(x, f0, t, sr)
        ap = pw.d4c(x, f0, t, sr)

        # Tono: se lleva la mediana al objetivo y se escala el rango en escala logarítmica
        # (misma lógica que Praat: rango 1 = misma expresividad en semitonos).
        src_median = np.median(f0[voiced])
        new_f0 = np.zeros_like(f0)
        new_f0[voiced] = float(params["pitch_median"]) * np.exp(
            float(params["pitch_range"]) * np.log(f0[voiced] / src_median))

        ratio = float(params["formant_ratio"])
        new_sp = _warp_bins(sp, ratio, log_domain=True)
        new_ap = np.clip(_warp_bins(ap, ratio, log_domain=False), 0.0, 1.0)

        y = pw.synthesize(new_f0, np.ascontiguousarray(new_sp), np.ascontiguousarray(new_ap),
                          sr, FRAME_PERIOD)
        return match_length(y, len(audio))
