"""Interfaz común de los "motores" de voz.

Todo motor (Praat, WORLD, y más adelante RVC) cumple el mismo contrato:
recibe audio mono float (-1..1) a `sr` Hz y devuelve audio mono de la
MISMA duración, para no romper la sincronía con el juego.
"""
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly


@dataclass(frozen=True)
class Param:
    """Un parámetro ajustable del motor. La interfaz arma un slider por cada uno."""
    key: str
    label: str
    minimum: float
    maximum: float
    default: float
    resolution: float = 0.01
    unit: str = ""
    help: str = ""


class VoiceEngine:
    id: str = ""
    name: str = ""
    description: str = ""
    params: tuple = ()

    def is_available(self):
        """Devuelve (disponible, motivo_si_no)."""
        return True, ""

    def defaults(self):
        return {p.key: p.default for p in self.params}

    def process(self, audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
        raise NotImplementedError


# Parámetros compartidos por los motores de "cambio de género".
GENDER_PARAMS = (
    Param("formant_ratio", "Formantes (timbre / tamaño de garganta)", 1.00, 1.40, 1.17, 0.01, "x",
          "Cuánto se 'achica' el tracto vocal. 1.15-1.20 es típico hombre a mujer."),
    Param("pitch_median", "Tono mediano", 120, 300, 210, 1, "Hz",
          "Altura promedio de la voz. Mujer adulta: 180-230 Hz."),
    Param("pitch_range", "Rango de entonación", 0.50, 2.00, 1.10, 0.05, "x",
          "1 = misma expresividad que tu voz; más = más melódica."),
)


def match_length(y: np.ndarray, n: int) -> np.ndarray:
    """Recorta o rellena con silencio para que dure exactamente n muestras."""
    if len(y) >= n:
        return y[:n]
    return np.pad(y, (0, n - len(y)))


def resample(y: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if int(sr_in) == int(sr_out):
        return y
    from math import gcd
    g = gcd(int(sr_in), int(sr_out))
    return resample_poly(y, int(sr_out) // g, int(sr_in) // g)
