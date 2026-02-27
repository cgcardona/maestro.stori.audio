"""
Critic: layer-aware scoring for Music Spec IR outputs.

Scores candidate outputs (drums, bass, melody, chords) with metrics:
- Drums: groove pocket, hat articulation, fill localization, ghost plausibility
- Bass: kick-bass alignment with anticipation awareness
- Melody: phrase structure, motif reuse
- Chords: voicing quality
"""
from __future__ import annotations

from maestro.services.critic.constants import (
    DRUM_WEIGHTS,
    ACCEPT_THRESHOLD_DRUM,
    ACCEPT_THRESHOLD_DRUM_QUALITY,
    ACCEPT_THRESHOLD_BASS,
    ACCEPT_THRESHOLD_BASS_QUALITY,
    ACCEPT_THRESHOLD_MELODY,
    ACCEPT_THRESHOLD_CHORDS,
)
from maestro.services.critic.helpers import (
    _distinct_pitches,
    _velocity_entropy_normalized,
    _offbeat_ratio,
    _get_notes_by_layer,
)
from maestro.services.critic.drums import (
    score_drum_notes,
    _score_groove_pocket,
    _score_hat_articulation,
    _score_fill_localization,
    _score_ghost_plausibility,
    _score_layer_balance,
    _score_repetition_structure,
    _score_velocity_dynamics,
)
from maestro.services.critic.bass import score_bass_notes
from maestro.services.critic.melody import score_melody_notes
from maestro.services.critic.chords import score_chord_notes
from maestro.services.critic.acceptance import accept_drum, accept_bass
from maestro.services.critic.sampling import RejectionSamplingResult, rejection_sample

__all__ = [
    "DRUM_WEIGHTS",
    "ACCEPT_THRESHOLD_DRUM",
    "ACCEPT_THRESHOLD_DRUM_QUALITY",
    "ACCEPT_THRESHOLD_BASS",
    "ACCEPT_THRESHOLD_BASS_QUALITY",
    "ACCEPT_THRESHOLD_MELODY",
    "ACCEPT_THRESHOLD_CHORDS",
    "_distinct_pitches",
    "_velocity_entropy_normalized",
    "_offbeat_ratio",
    "_get_notes_by_layer",
    "score_drum_notes",
    "_score_groove_pocket",
    "_score_hat_articulation",
    "_score_fill_localization",
    "_score_ghost_plausibility",
    "_score_layer_balance",
    "_score_repetition_structure",
    "_score_velocity_dynamics",
    "score_bass_notes",
    "score_melody_notes",
    "score_chord_notes",
    "accept_drum",
    "accept_bass",
    "RejectionSamplingResult",
    "rejection_sample",
]
