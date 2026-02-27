"""Music generation backends."""
from __future__ import annotations

from maestro.services.backends.drum_ir import DrumSpecBackend
from maestro.services.backends.bass_ir import BassSpecBackend
from maestro.services.backends.harmonic_ir import HarmonicSpecBackend
from maestro.services.backends.melody_ir import MelodySpecBackend
from maestro.services.backends.storpheus import StorpheusBackend

__all__ = [
    "DrumSpecBackend",
    "BassSpecBackend",
    "HarmonicSpecBackend",
    "MelodySpecBackend",
    "StorpheusBackend",
]
