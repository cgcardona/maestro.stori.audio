"""
Track styling utilities for automatic color and icon assignment.

Provides intelligent defaults for track colors and icons based on
track names and instrument types.
"""

import re
from typing import Optional


# Named colors accepted by the macOS client (SwiftUI adaptive colors).
# Preferred over hex — they look correct in both light and dark mode.
NAMED_COLORS: list[str] = [
    "blue", "indigo", "purple", "pink", "red", "orange",
    "yellow", "green", "teal", "cyan", "mint", "gray",
]
NAMED_COLORS_SET: frozenset[str] = frozenset(NAMED_COLORS) | frozenset({"grey"})

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Palette rotation order — maximises visual contrast when assigning
# colors to multiple tracks in a single composition.
PALETTE_ROTATION: list[str] = list(NAMED_COLORS)

# Perceptually-spaced hex palette for multi-track compositions.
# Colors are ordered to maximise contrast between adjacent tracks;
# pick in index order and cycle only after all 8 are exhausted.
COMPOSITION_PALETTE: list[str] = [
    "#E87040",  # amber/orange  (warm)
    "#4A9EE8",  # sky blue      (cool)
    "#60C264",  # sage green    (natural)
    "#B06FD8",  # violet        (purple)
    "#E85D75",  # rose          (warm red)
    "#40C4C0",  # teal          (cyan)
    "#E8C040",  # gold          (yellow)
    "#8C8CE8",  # periwinkle    (blue-purple)
]

# Role/keyword → preferred named color (from FE contract).
_ROLE_COLOR_MAP: dict[str, str] = {
    "piano": "blue", "keys": "blue", "pads": "blue", "pad": "blue",
    "synth": "indigo", "electric piano": "indigo", "rhodes": "indigo",
    "strings": "purple", "orchestral": "purple", "violin": "purple",
    "cello": "purple", "viola": "purple",
    "vocal": "pink", "vocals": "pink", "choir": "pink", "voice": "pink",
    "drums": "red", "drum": "red", "kick": "red",
    "brass": "orange", "horns": "orange", "trumpet": "orange",
    "trombone": "orange", "horn": "orange",
    "guitar": "yellow", "plucked": "yellow",
    "bass": "green", "sub": "green",
    "woodwind": "teal", "flute": "teal", "clarinet": "teal",
    "saxophone": "teal", "sax": "teal",
    "fx": "cyan", "texture": "cyan", "ambient": "cyan", "atmosphere": "cyan",
    "perc": "mint", "percussion": "mint", "shaker": "mint", "auxiliary": "mint",
    "utility": "gray", "click": "gray",
}


# Icon mapping based on keywords in track name (FE contract role defaults).
# Longer/more-specific keywords must appear BEFORE shorter substrings
# (e.g. "acoustic guitar" before "guitar", "electric piano" before "piano").
_ICON_KEYWORD_LIST: list[tuple[str, str]] = [
    # Multi-word (must come first)
    ("acoustic guitar", "guitars"),
    ("electric piano", "pianokeys.inverse"),

    # Drums / Percussion
    ("drum", "instrument.drum"),
    ("percuss", "instrument.drum"),
    ("perc", "instrument.drum"),
    ("kit", "instrument.drum"),
    ("timpani", "instrument.drum"),
    ("kick", "instrument.drum"),
    ("snare", "instrument.drum"),
    ("hat", "instrument.drum"),
    ("cymbal", "instrument.drum"),

    # Bass (guitar family per FE contract)
    ("bass", "guitars.fill"),

    # Synth / Electric Piano (before piano/keys)
    ("synth", "pianokeys.inverse"),
    ("rhodes", "pianokeys.inverse"),

    # Organ (keyboard family)
    ("organ", "pianokeys"),

    # Guitar / Plucked
    ("guitar", "guitars.fill"),
    ("banjo", "guitars.fill"),
    ("mandolin", "guitars.fill"),

    # Piano / Keys
    ("piano", "pianokeys"),
    ("key", "pianokeys"),
    ("keys", "pianokeys"),
    ("chord", "pianokeys"),
    ("clavi", "pianokeys"),

    # Pad / Texture / Ambient
    ("pad", "waveform"),
    ("texture", "waveform"),
    ("ambient", "waveform"),

    # Harp
    ("harp", "instrument.harp"),

    # Strings
    ("string", "instrument.violin"),
    ("violin", "instrument.violin"),
    ("cello", "instrument.violin"),
    ("viola", "instrument.violin"),
    ("orchestra", "instrument.violin"),

    # Brass
    ("brass", "instrument.trumpet"),
    ("trumpet", "instrument.trumpet"),
    ("trombone", "instrument.trumpet"),
    ("horn", "instrument.trumpet"),
    ("tuba", "instrument.trumpet"),

    # Reed / Woodwind
    ("sax", "instrument.saxophone"),
    ("clarinet", "instrument.saxophone"),
    ("oboe", "instrument.saxophone"),
    ("reed", "instrument.saxophone"),

    # Pipe / Flute
    ("flute", "instrument.flute"),
    ("pipe", "instrument.flute"),
    ("recorder", "instrument.flute"),

    # Vocals
    ("vocal", "music.mic"),
    ("voice", "music.mic"),
    ("sing", "music.mic"),
    ("choir", "music.mic"),
    ("aah", "music.mic"),
    ("mic", "music.mic"),

    # Ensemble
    ("ensemble", "music.note.list"),
    ("harmony", "music.note.list"),

    # Chromatic Percussion / Mallet
    ("bell", "instrument.xylophone"),
    ("marimba", "instrument.xylophone"),
    ("xylophone", "instrument.xylophone"),
    ("vibraphone", "instrument.xylophone"),
    ("mallet", "instrument.xylophone"),
    ("chrom", "instrument.xylophone"),

    # Effects / Utility
    ("fx", "sparkles"),
    ("effect", "sparkles"),
    ("atmosphere", "sparkles"),

    # Melody (shorter keywords — must come AFTER longer ones like "synth lead")
    ("melody", "music.note"),
    ("lead", "music.note"),
    ("solo", "music.note"),
    ("arp", "music.quarternote.3"),
]


DEFAULT_ICON = "music.note"


def normalize_color(raw: Optional[str]) -> Optional[str]:
    """Validate and pass through a client-safe color value.

    Returns the color unchanged if it's a recognised named color or
    valid ``#RRGGBB`` hex string, otherwise ``None``.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if cleaned in NAMED_COLORS_SET:
        return cleaned if cleaned != "grey" else "gray"
    if _HEX_RE.match(raw.strip()):
        return raw.strip()
    return None


def color_for_role(track_name: str, rotation_index: int = 0) -> str:
    """Pick a named color based on the track name / role.

    Falls back to the palette rotation when no keyword matches.
    """
    lower = track_name.lower()
    for keyword, color in _ROLE_COLOR_MAP.items():
        if keyword in lower:
            return color
    return PALETTE_ROTATION[rotation_index % len(PALETTE_ROTATION)]


def get_random_track_color() -> str:
    """Get a named color for a new track (rotation-based, not random)."""
    return PALETTE_ROTATION[0]


def allocate_colors(instrument_names: list[str]) -> dict[str, str]:
    """Assign one hex color per instrument, guaranteed no repeats.

    Colors are drawn from ``COMPOSITION_PALETTE`` in index order so that
    adjacent tracks are always maximally distinct.  The palette cycles only
    after all 8 entries are exhausted (rare in practice).

    Args:
        instrument_names: Ordered list of instrument/role names.

    Returns:
        Mapping of ``{instrument_name: hex_color}``.
    """
    return {
        name: COMPOSITION_PALETTE[i % len(COMPOSITION_PALETTE)]
        for i, name in enumerate(instrument_names)
    }


def is_valid_icon(icon: Optional[str]) -> bool:
    """Return True if the icon is in the curated SF Symbol allowlist."""
    if not icon:
        return False
    from app.core.tool_validation.constants import VALID_SF_SYMBOL_ICONS
    return icon in VALID_SF_SYMBOL_ICONS


def infer_track_icon(track_name: str) -> str:
    """Infer an SF Symbol icon based on track name keywords.

    Multi-word keywords are checked first so "Acoustic Guitar" matches
    ``guitars`` before the shorter ``guitar`` (``guitars.fill``).
    """
    if not track_name:
        return DEFAULT_ICON

    track_lower = track_name.lower()

    for keyword, icon in _ICON_KEYWORD_LIST:
        if keyword in track_lower:
            return icon

    return DEFAULT_ICON


def get_track_styling(
    track_name: str,
    rotation_index: int = 0,
) -> dict[str, str]:
    """
    Get both color and icon for a track.
    
    Args:
        track_name: The name of the track
        rotation_index: Index into the palette rotation for fallback color.
        
    Returns:
        Dict with 'color' and 'icon' keys
    """
    return {
        "color": color_for_role(track_name, rotation_index),
        "icon": infer_track_icon(track_name),
    }
