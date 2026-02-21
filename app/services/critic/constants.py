"""Thresholds and weights for the critic scoring system."""

# Drum rubric weights (sum to 1.0)
DRUM_WEIGHTS = {
    "groove_pocket": 0.20,
    "hat_articulation": 0.18,
    "fill_localization": 0.15,
    "ghost_plausibility": 0.12,
    "layer_balance": 0.12,
    "repetition_structure": 0.10,
    "velocity_dynamics": 0.08,
    "syncopation": 0.05,
}

ACCEPT_THRESHOLD_DRUM = 0.65
ACCEPT_THRESHOLD_DRUM_QUALITY = 0.75
ACCEPT_THRESHOLD_BASS = 0.55
ACCEPT_THRESHOLD_BASS_QUALITY = 0.70
ACCEPT_THRESHOLD_MELODY = 0.5
ACCEPT_THRESHOLD_CHORDS = 0.5
