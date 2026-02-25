"""
Registries and idiom mappings.

In the new architecture, these registries become primarily DATA.
Start here when you want 100x phrase coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Placeholder for future YAML-backed registries.
# For now, keep a minimal map that the intent router can grow into.

GOAL_SYNONYMS = {
    "darker": ["darker", "more dark", "less bright"],
    "brighter": ["brighter", "more bright", "more air", "shine"],
    "punchier": ["punchier", "more punch", "hit harder"],
    "wider": ["wider", "spread it out", "bigger stereo"],
    "more_energy": ["more energy", "more movement", "spice it up"],
}

# Macro mapping example: idiom -> macro id(s)
MACRO_REGISTRY = {
    "darker": ["mix.darker"],
}
