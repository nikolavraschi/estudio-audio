"""Registro de motores. Para sumar uno nuevo: crear la clase y agregarla acá.

Si la librería de un motor no se puede cargar (falta instalarla, versión rota…),
el motor aparece igual en el menú pero marcado como no disponible, y el resto
de la app sigue funcionando.
"""
import importlib
import traceback

from .base import VoiceEngine

# (id, nombre visible, módulo, clase)
_REGISTRY = (
    ("praat", "Praat - Change gender", "praat_gender", "PraatGenderEngine"),
    ("world", "WORLD vocoder", "world_vocoder", "WorldEngine"),
    ("rvc", "RVC (Applio)", "rvc_applio", "RvcApplioEngine"),
)


class BrokenEngine(VoiceEngine):
    """Lugar de un motor que no se pudo cargar."""

    def __init__(self, engine_id, name, error):
        self.id, self.name, self.error = engine_id, f"{name} (no disponible)", error
        self.description = ""

    def is_available(self):
        return False, f"No se pudo cargar este motor: {self.error}"


def _load():
    engines = {}
    for engine_id, name, module, cls in _REGISTRY:
        try:
            mod = importlib.import_module(f".{module}", __name__)
            engines[engine_id] = getattr(mod, cls)()
        except Exception as e:  # noqa: BLE001 - cualquier fallo de import deja el motor marcado
            engines[engine_id] = BrokenEngine(engine_id, name, f"{type(e).__name__}: {e}")
            engines[engine_id].traceback = traceback.format_exc()
    return engines


ENGINES = _load()
