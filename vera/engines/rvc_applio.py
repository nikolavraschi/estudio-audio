"""Motor 3 (futuro): RVC a través de Applio por línea de comandos.

NO está implementado todavía. Este archivo deja el lugar listo; ver RVC.md
para el plan de conexión.
"""
from .base import Param, VoiceEngine


class RvcApplioEngine(VoiceEngine):
    id = "rvc"
    name = "RVC (Applio) - próximamente"
    description = "Conversión con modelo de voz entrenado. Pendiente: ver RVC.md."
    params = (
        Param("pitch_shift", "Transposición", -24, 24, 12, 1, "semitonos"),
        Param("index_rate", "Parecido al modelo (index rate)", 0.0, 1.0, 0.75, 0.05),
        Param("protect", "Proteger consonantes / respiración", 0.0, 0.5, 0.33, 0.01),
    )

    def is_available(self):
        return False, "El motor RVC todavía no está conectado (ver RVC.md)."

    def process(self, audio, sr, params):
        # Plan: guardar `audio` en un WAV temporal, llamar a Applio:
        #   python core.py infer --input_path in.wav --output_path out.wav
        #       --pth_path VERA.pth --index_path VERA.index --pitch <pitch_shift> ...
        # leer out.wav, remuestrear a `sr` y devolver con match_length(y, len(audio)).
        raise NotImplementedError("Motor RVC no implementado todavía.")
