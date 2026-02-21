"""PLACEHOLDERS and PROMPT_POOL seed data for the Maestro UI.

The pool is assembled from regional modules so no single file becomes
a monolith.  Each module exports a list[PromptItem]; this aggregator
concatenates them and builds the ALL_PROMPT_IDS index.
"""

from app.data.maestro_ui.prompts_americas import PROMPTS_AMERICAS
from app.data.maestro_ui.prompts_europe import PROMPTS_EUROPE
from app.data.maestro_ui.prompts_global import PROMPTS_GLOBAL
from app.data.maestro_ui.prompts_cinematic import PROMPTS_CINEMATIC
from app.models.maestro_ui import PromptItem

PLACEHOLDERS: list[str] = [
    "Describe a groove\u2026",
    "Build a cinematic swell\u2026",
    "Make something nobody has heard before\u2026",
    "A lo-fi beat for a rainy afternoon\u2026",
    "Jazz trio warming up in a dim club\u2026",
    "Epic orchestral buildup to a drop\u2026",
    "Funky bassline with a pocket feel\u2026",
    "Ambient textures for a midnight drive\u2026",
    "West African polyrhythm in 12/8\u2026",
    "A raga for the evening sky\u2026",
    "Balkan brass in 7/8, pure fire\u2026",
    "Tango nuevo \u2014 bandoneon and strings\u2026",
]


# ---------------------------------------------------------------------------
# Assembled pool — randomly sampled 4-at-a-time by the API
# ---------------------------------------------------------------------------

PROMPT_POOL: list[PromptItem] = (
    PROMPTS_AMERICAS
    + PROMPTS_EUROPE
    + PROMPTS_GLOBAL
    + PROMPTS_CINEMATIC
)

ALL_PROMPT_IDS: set[str] = {p.id for p in PROMPT_POOL}
