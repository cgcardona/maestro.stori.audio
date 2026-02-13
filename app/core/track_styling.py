"""
Track styling utilities for automatic color and icon assignment.

Provides intelligent defaults for track colors and icons based on
track names and instrument types.
"""

import random
from typing import Optional


# Professional track colors (hex) - matches frontend Tailwind palette
# These exact colors were previously working on the frontend
TRACK_COLORS = [
    # Core frontend colors (previously working)
    "#3B82F6",  # Blue
    "#EF4444",  # Red
    "#10B981",  # Green
    "#F59E0B",  # Yellow
    "#8B5CF6",  # Purple
    "#EC4899",  # Pink
    "#F97316",  # Orange
    "#14B8A6",  # Teal
    "#6366F1",  # Indigo
    "#6B7280",  # Gray
    
    # Additional colors for variety
    "#06B6D4",  # Cyan
    "#84CC16",  # Lime
    "#F43F5E",  # Rose
    "#A855F7",  # Violet
    "#0EA5E9",  # Sky
    "#22C55E",  # Emerald
    "#EAB308",  # Amber
    "#F97316",  # Orange (darker)
    "#EC4899",  # Fuchsia
    "#6366F1",  # Blue (darker)
]


# Icon mapping based on keywords in track name
ICON_KEYWORDS = {
    # Drums
    "drum": "waveform.path",
    "kick": "waveform.path",
    "snare": "waveform.path",
    "hat": "waveform.path",
    "cymbal": "waveform.path",
    "percussion": "waveform.path",
    
    # Bass
    "bass": "speaker.wave.3",
    
    # Piano/Keys
    "piano": "pianokeys",
    "key": "pianokeys",
    "keys": "pianokeys",
    "organ": "pianokeys.inverse",
    "synth": "waveform",
    "pad": "waveform.circle",
    
    # Guitars
    "guitar": "guitars.fill",
    "acoustic": "guitars",
    
    # Vocals
    "vocal": "music.mic.circle.fill",
    "voice": "music.mic.circle.fill",
    "mic": "music.mic.circle.fill",
    "lead": "music.mic",
    
    # Strings/Orchestra
    "string": "music.quarternote.3",
    "violin": "music.quarternote.3",
    "cello": "music.quarternote.3",
    "orchestra": "music.quarternote.3",
    
    # Brass
    "brass": "speaker.wave.2",
    "trumpet": "speaker.wave.2",
    "sax": "speaker.wave.2",
    "horn": "speaker.wave.2",
    
    # Effects
    "fx": "sparkles",
    "effect": "wand.and.rays",
    "atmosphere": "wand.and.stars",
    "ambient": "wand.and.stars.inverse",
    
    # Chords/Harmony
    "chord": "music.note.list",
    "harmony": "music.note.list",
    "arp": "bolt.circle",
    
    # Melody
    "melody": "music.note",
    "solo": "star.fill",
}


# Default icon if no keywords match
DEFAULT_ICON = "waveform"


def get_random_track_color() -> str:
    """Get a random color name for a new track."""
    return random.choice(TRACK_COLORS)


def infer_track_icon(track_name: str) -> str:
    """
    Infer an appropriate SF Symbol icon based on track name.
    
    Args:
        track_name: The name of the track (e.g., "Jam Drums", "Funky Bass")
        
    Returns:
        SF Symbol icon name (e.g., "waveform.path", "pianokeys")
    """
    if not track_name:
        return DEFAULT_ICON
    
    track_lower = track_name.lower()
    
    # Check for keyword matches
    for keyword, icon in ICON_KEYWORDS.items():
        if keyword in track_lower:
            return icon
    
    return DEFAULT_ICON


def get_track_styling(track_name: str) -> dict[str, str]:
    """
    Get both color and icon for a track.
    
    Args:
        track_name: The name of the track
        
    Returns:
        Dict with 'color' and 'icon' keys
    """
    return {
        "color": get_random_track_color(),
        "icon": infer_track_icon(track_name),
    }
