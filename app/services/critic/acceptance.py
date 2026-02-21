"""Acceptance threshold checks for drum and bass scores."""

from app.services.critic.constants import (
    ACCEPT_THRESHOLD_DRUM,
    ACCEPT_THRESHOLD_DRUM_QUALITY,
    ACCEPT_THRESHOLD_BASS,
    ACCEPT_THRESHOLD_BASS_QUALITY,
)


def accept_drum(score: float, quality_preset: str = "balanced") -> bool:
    """Return True if the drum score meets the threshold for the given quality preset."""
    threshold = ACCEPT_THRESHOLD_DRUM_QUALITY if quality_preset == "quality" else ACCEPT_THRESHOLD_DRUM
    return score >= threshold


def accept_bass(score: float, quality_preset: str = "balanced") -> bool:
    """Return True if the bass score meets the threshold for the given quality preset."""
    threshold = ACCEPT_THRESHOLD_BASS_QUALITY if quality_preset == "quality" else ACCEPT_THRESHOLD_BASS
    return score >= threshold
