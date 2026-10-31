"""Motor 1: la función "Change gender" de Praat (vía praat-parselmouth).

Praat analiza el pitch, desplaza los formantes remuestreando y recoloca el
tono con PSOLA. Mantiene la duración exacta (duration factor = 1).
"""
import numpy as np
import parselmouth
from parselmouth.praat import call

from .base import GENDER_PARAMS, VoiceEngine, match_length, resample

# Rango de búsqueda de pitch de la voz ORIGINAL (masculina, incluso gritando).
PITCH_FLOOR = 75.0
PITCH_CEILING = 600.0


class PraatGenderEngine(VoiceEngine):
    id = "praat"
    name = "Praat - Change gender"
    description = "PSOLA + desplazamiento de formantes. Natural, conserva bien la respiración."
    params = GENDER_PARAMS

    def process(self, audio, sr, params):
        snd = parselmouth.Sound(np.asarray(audio, dtype=np.float64), sampling_frequency=sr)
        out = call(snd, "Change gender",
                   PITCH_FLOOR, PITCH_CEILING,
                   float(params["formant_ratio"]),
                   float(params["pitch_median"]),
                   float(params["pitch_range"]),
                   1.0)
        y = resample(out.values[0], out.sampling_frequency, sr)
        return match_length(y, len(audio))


def pitch_median(audio, sr):
    """Pitch mediano (Hz) de las partes con voz, o 0 si no detecta voz."""
    snd = parselmouth.Sound(np.asarray(audio, dtype=np.float64), sampling_frequency=sr)
    f0 = snd.to_pitch(time_step=0.01, pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING)
    f0 = f0.selected_array["frequency"]
    f0 = f0[f0 > 0]
    return float(np.median(f0)) if len(f0) else 0.0
