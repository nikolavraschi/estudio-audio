"""Registro de motores. Para sumar uno nuevo: crear la clase y agregarla acá."""
from .praat_gender import PraatGenderEngine
from .rvc_applio import RvcApplioEngine
from .world_vocoder import WorldEngine

ENGINES = {e.id: e for e in (PraatGenderEngine(), WorldEngine(), RvcApplioEngine())}
