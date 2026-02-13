"""
Chord name → pitch utilities for bass, harmonic, and melody renderers.
Supports common symbols: Cm, Eb, F#m, G7, etc.
"""
from typing import Tuple

# Root name -> pitch class (C=0, C#=1, ... B=11)
_ROOT_PC = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11,
}


def chord_root_pitch_class(chord_name: str) -> int:
    """Parse chord name to root pitch class (0-11). Cm -> 0, Eb -> 3."""
    s = (chord_name or "C").strip()
    if not s:
        return 0
    s_upper = s.upper()
    if len(s) >= 2 and s_upper[1] in "#B":
        root = s_upper[:2]
        if root == "DB":
            root = "C#"
        elif root == "EB":
            root = "D#"
        elif root == "GB":
            root = "F#"
        elif root == "AB":
            root = "G#"
        elif root == "BB":
            root = "A#"
    else:
        root = s_upper[0]
    return _ROOT_PC.get(root, 0)


def chord_to_root_and_fifth_midi(chord_name: str, root_octave: int) -> Tuple[int, int]:
    """Return (root_midi, fifth_midi) for bass. Fifth is perfect 5th (7 semitones)."""
    pc = chord_root_pitch_class(chord_name)
    root_midi = root_octave * 12 + pc
    fifth_midi = root_midi + 7
    return root_midi, fifth_midi


def chord_to_scale_degrees(chord_name: str, num_degrees: int = 3) -> list[int]:
    """Return scale degrees in semitones above root for voicing. 3 = root, third, fifth."""
    s = (chord_name or "C").strip()
    is_minor = "m" in s.lower() and s.lower().index("m") >= 1
    # root, third, fifth (and optionally seventh)
    if is_minor:
        degrees = [0, 3, 7]  # min third, fifth
    else:
        degrees = [0, 4, 7]  # maj third, fifth
    if num_degrees >= 4:
        degrees.append(10 if is_minor else 11)  # min7 or maj7
    return degrees[:num_degrees]


def chord_to_midi_voicing(chord_name: str, octave: int, num_voices: int = 4) -> list[int]:
    """Return list of MIDI pitches for chord voicing (root, third, fifth, seventh)."""
    pc = chord_root_pitch_class(chord_name)
    degrees = chord_to_scale_degrees(chord_name, num_degrees=num_voices)
    base = octave * 12 + pc
    return [base + d for d in degrees]
