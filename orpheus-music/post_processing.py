"""Post-processing pipeline for Orpheus-generated MIDI output.

Applied after candidate selection, before conversion to tool calls.
Each transform operates on the flat ``[{"pitch", "start_beat",
"duration_beats", "velocity"}, ...]`` note list.

Transforms are chainable and optional — the ``PostProcessor`` class
applies only those enabled by the generation constraints or control
vector.

Uses data-driven priors from the 222K-track Musical DNA heuristics
when ``RoleProfileSummary`` is available.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PostProcessorConfig:
    """Configuration for the post-processing pipeline.

    All fields are optional — ``None`` means "skip this transform".
    Values are typically sourced from ``GenerationConstraintsPayload``
    or the control vector.
    """
    # Velocity scaling
    velocity_floor: int | None = None
    velocity_ceiling: int | None = None

    # Register normalization
    register_center: int | None = None
    register_spread: int | None = None

    # Quantization
    subdivision: int | None = None  # 8 = 8th notes, 16 = 16th notes

    # Duration cleanup
    min_duration_beats: float = 0.0
    max_duration_beats: float = 0.0  # 0 = no cap

    # Humanization (from Musical DNA)
    swing_amount: float = 0.0  # 0.0 = straight, >0 = swing odd 16ths

    enabled: bool = True


class PostProcessor:
    """Chainable post-processing pipeline for generated notes."""

    def __init__(self, config: PostProcessorConfig) -> None:
        self.config = config
        self._transforms_applied: list[str] = []

    def process(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply all configured transforms to the note list in order.

        Returns the (possibly modified) note list. Non-destructive on
        empty inputs.
        """
        if not notes or not self.config.enabled:
            return notes

        self._transforms_applied = []

        notes = self._scale_velocity(notes)
        notes = self._normalize_register(notes)
        notes = self._quantize(notes)
        notes = self._cleanup_durations(notes)
        notes = self._apply_swing(notes)

        if self._transforms_applied:
            logger.info(
                "🎛️ Post-processing applied: %s (%d notes)",
                ", ".join(self._transforms_applied),
                len(notes),
            )

        return notes

    @property
    def transforms_applied(self) -> list[str]:
        return list(self._transforms_applied)

    # ── Individual transforms ──────────────────────────────────────

    def _scale_velocity(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Map velocity range to [floor, ceiling]."""
        floor_v = self.config.velocity_floor
        ceil_v = self.config.velocity_ceiling
        if floor_v is None and ceil_v is None:
            return notes

        floor_v = floor_v or 1
        ceil_v = ceil_v or 127
        if floor_v >= ceil_v:
            return notes

        velocities = [n.get("velocity", 80) for n in notes]
        if not velocities:
            return notes

        v_min = min(velocities)
        v_max = max(velocities)

        if v_min == v_max:
            target = (floor_v + ceil_v) // 2
            for n in notes:
                n["velocity"] = target
        else:
            src_range = v_max - v_min
            dst_range = ceil_v - floor_v
            for n in notes:
                v = n.get("velocity", 80)
                normalised = (v - v_min) / src_range
                n["velocity"] = max(1, min(127, round(floor_v + normalised * dst_range)))

        self._transforms_applied.append(f"velocity[{floor_v}-{ceil_v}]")
        return notes

    def _normalize_register(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Shift notes so the median pitch aligns with register_center."""
        center = self.config.register_center
        spread = self.config.register_spread
        if center is None:
            return notes

        pitches = sorted(n.get("pitch", 60) for n in notes)
        if not pitches:
            return notes

        median_pitch = pitches[len(pitches) // 2]
        shift = center - median_pitch

        if abs(shift) < 2:
            return notes

        # Quantize shift to whole octaves to preserve harmonic relationships
        octave_shift = round(shift / 12) * 12
        if octave_shift == 0:
            return notes

        for n in notes:
            new_p = n.get("pitch", 60) + octave_shift
            n["pitch"] = max(0, min(127, new_p))

        # If spread constraint exists, clamp outliers
        if spread is not None:
            low = center - spread
            high = center + spread
            for n in notes:
                n["pitch"] = max(low, min(high, n["pitch"]))

        self._transforms_applied.append(f"register[center={center},shift={octave_shift:+d}]")
        return notes

    def _quantize(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Snap note start times to the nearest subdivision grid."""
        sub = self.config.subdivision
        if sub is None or sub <= 0:
            return notes

        grid = 4.0 / sub  # beats per grid line (e.g., 16th → 0.25)

        for n in notes:
            start = n.get("start_beat", 0.0)
            quantized = round(start / grid) * grid
            n["start_beat"] = round(quantized, 4)

        self._transforms_applied.append(f"quantize[1/{sub}]")
        return notes

    def _cleanup_durations(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enforce minimum and maximum note durations."""
        min_d = self.config.min_duration_beats
        max_d = self.config.max_duration_beats

        if min_d <= 0 and max_d <= 0:
            return notes

        changed = False
        for n in notes:
            dur = n.get("duration_beats", 0.5)
            if min_d > 0 and dur < min_d:
                n["duration_beats"] = min_d
                changed = True
            if max_d > 0 and dur > max_d:
                n["duration_beats"] = max_d
                changed = True

        if changed:
            self._transforms_applied.append(
                f"duration[min={min_d:.2f},max={max_d:.2f}]"
            )
        return notes

    def _apply_swing(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply swing feel by delaying odd 16th-note positions.

        ``swing_amount`` in [0, 1]:  0 = straight, 0.33 = light swing,
        0.67 = heavy triplet swing.
        """
        amount = self.config.swing_amount
        if amount <= 0.01:
            return notes

        grid_16th = 0.25  # one 16th note in beats
        delay = grid_16th * amount * 0.5

        for n in notes:
            start = n.get("start_beat", 0.0)
            pos_in_beat = (start % 1.0)
            # Odd 16th positions: 0.25, 0.75
            if abs(pos_in_beat - 0.25) < 0.06 or abs(pos_in_beat - 0.75) < 0.06:
                n["start_beat"] = round(start + delay, 4)

        self._transforms_applied.append(f"swing[{amount:.2f}]")
        return notes


def build_post_processor(
    generation_constraints: dict[str, Any] | None = None,
    role_profile_summary: dict[str, Any] | None = None,
) -> PostProcessor:
    """Build a PostProcessor from Maestro's structured payloads.

    Uses generation_constraints for hard limits and role_profile_summary
    for data-driven defaults when constraints are absent.
    """
    gc = generation_constraints or {}
    rp = role_profile_summary or {}

    config = PostProcessorConfig()

    if "velocity_floor" in gc or "velocity_ceiling" in gc:
        config.velocity_floor = gc.get("velocity_floor")
        config.velocity_ceiling = gc.get("velocity_ceiling")

    if "register_center" in gc:
        config.register_center = gc.get("register_center")
        config.register_spread = gc.get("register_spread", 24)
    elif "register_mean_pitch" in rp:
        config.register_center = round(rp["register_mean_pitch"])

    if "subdivision" in gc:
        config.subdivision = gc.get("subdivision")

    if "swing_amount" in gc and gc["swing_amount"] > 0.01:
        config.swing_amount = gc["swing_amount"]
    elif "swing_ratio" in rp and rp["swing_ratio"] > 0.01:
        config.swing_amount = min(1.0, rp["swing_ratio"] * 2.0)

    return PostProcessor(config)
