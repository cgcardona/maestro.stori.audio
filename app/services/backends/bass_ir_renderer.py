"""
Bass Spec IR → MIDI notes renderer.

Renders BassSpec + GlobalSpec + HarmonicSpec to bass notes with kick alignment,
chord follow (root/fifth), anticipation, octave jump.

Uses RhythmSpine for proper kick coupling from actual drum output.
Supports anticipation slots and response slots for groove feel.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from app.core.music_spec_ir import (
    BassSpec,
    GlobalSpec,
    HarmonicSpec,
    ChordScheduleEntry,
)
from app.core.chord_utils import chord_to_root_and_fifth_midi
from app.services.groove_engine import RhythmSpine, get_groove_profile

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Bass Render Result: notes + coupling metadata
# -----------------------------------------------------------------------------

@dataclass
class BassRenderResult:
    """Result of bass rendering with coupling metrics."""
    notes: list[dict[str, Any]] = field(default_factory=list)
    kick_alignment_ratio: float = 0.0
    anticipation_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def _chord_at_bar(schedule: list[ChordScheduleEntry], bar: int) -> str:
    """Return chord symbol for the given bar (use last schedule entry with bar <= bar)."""
    if not schedule:
        return "C"
    best = schedule[0]
    for e in schedule:
        if e.bar <= bar:
            best = e
        else:
            break
    return best.chord


def _kick_beats_for_bar(groove_template: str, bar_start_beat: float) -> list[float]:
    """Expected kick beat positions for one bar (global beat indices). Trap: 0, 2, 3.5 per bar."""
    if groove_template == "house_four_on_floor":
        return [bar_start_beat + b for b in [0.0, 1.0, 2.0, 3.0]]
    if groove_template in ("trap_triplet", "trap_straight"):
        if groove_template == "trap_triplet":
            return [bar_start_beat + b for b in [0.0, 2.0, 2.666]]
        return [bar_start_beat + b for b in [0.0, 2.0, 3.5]]
    if groove_template == "boom_bap_swing":
        return [bar_start_beat + b for b in [0.0, 2.5]]
    return [bar_start_beat + b for b in [0.0, 2.0]]


def _is_dense_region(bar_index: int, bars: int, kick_count_in_bar: int) -> bool:
    """Detect if this is a dense rhythmic region (more activity)."""
    # Fill bars and bars with many kicks are dense
    if bar_index % 4 == 3:  # Fill bar
        return True
    if kick_count_in_bar >= 3:
        return True
    return False


def render_bass_spec(
    bass_spec: BassSpec,
    global_spec: GlobalSpec,
    harmonic_spec: HarmonicSpec,
    drum_groove_template: str = "trap_straight",
    drum_kick_beats: list[float] | None = None,
    rhythm_spine: RhythmSpine | None = None,
    *,
    apply_humanize: bool = True,
    return_result: bool = False,
) -> list[dict[str, Any]] | BassRenderResult:
    """
    Render BassSpec + GlobalSpec + HarmonicSpec to MIDI note list.

    Uses chord_schedule for roots/fifths. Now supports proper kick coupling:
    1. If rhythm_spine provided: uses actual kick onsets from drum output
    2. Elif drum_kick_beats provided: uses explicit kick positions
    3. Else: derives kick positions from drum_groove_template
    
    New features:
    - Anticipation slots: bass can play slightly before strong kicks
    - Note length shaping: staccato in dense regions, longer in sparse
    - Style-appropriate timing via groove engine
    
    Args:
        bass_spec: BassSpec with rhythm lock, kick follow, etc.
        global_spec: GlobalSpec with tempo, bars
        harmonic_spec: HarmonicSpec with chord schedule
        drum_groove_template: Fallback groove template if no kick data
        drum_kick_beats: Optional explicit kick positions
        rhythm_spine: Optional RhythmSpine from drum render (preferred)
        apply_humanize: Whether to apply groove humanization
        return_result: If True, return BassRenderResult with metrics
    
    Returns:
        list of notes, or BassRenderResult if return_result=True
    """
    notes = []
    schedule = harmonic_spec.chord_schedule
    bars = global_spec.bars
    rng = random.Random()
    
    # Get kick onsets from rhythm spine or fallback
    if rhythm_spine is not None:
        all_kick_beats = rhythm_spine.kick_onsets
        anticipation_slots = rhythm_spine.get_anticipation_slots(beat_before=0.125)
        snare_onsets = rhythm_spine.snare_onsets
    elif drum_kick_beats is not None:
        all_kick_beats = drum_kick_beats
        anticipation_slots = [round(k - 0.125, 4) for k in drum_kick_beats if k >= 0.125]
        snare_onsets = []
    else:
        all_kick_beats = []
        anticipation_slots = []
        snare_onsets = []
    
    # Track coupling metrics
    kick_aligned_count = 0
    anticipation_count = 0
    total_notes = 0

    for bar_index in range(bars):
        bar_start = bar_index * 4.0
        bar_end = bar_start + 4.0
        chord = _chord_at_bar(schedule, bar_index)
        root_midi, fifth_midi = chord_to_root_and_fifth_midi(chord, bass_spec.root_octave)

        # Get kick beats for this bar
        if all_kick_beats:
            kick_beats = [b for b in all_kick_beats if bar_start <= b < bar_end]
        else:
            kick_beats = _kick_beats_for_bar(drum_groove_template, bar_start)
        
        # Get anticipation slots for this bar
        bar_anticipation_slots = [b for b in anticipation_slots if bar_start <= b < bar_end]
        
        # Detect dense vs sparse region for note length shaping
        is_dense = _is_dense_region(bar_index, bars, len(kick_beats))

        # Density: min_notes_per_bar .. max_notes_per_bar
        min_n = bass_spec.density_target.min_notes_per_bar
        max_n = bass_spec.density_target.max_notes_per_bar
        num_notes = rng.randint(min_n, max_n) if max_n > min_n else min_n

        # Build candidate slots: kick-aligned, anticipation, syncopation, grid
        used_beats: set[float] = set()
        candidates_8 = [bar_start + i * 0.5 for i in range(8)]
        candidates_16 = [bar_start + i * 0.25 for i in range(16)] if bass_spec.syncopation_allowed else candidates_8
        
        for note_idx in range(num_notes):
            b = None
            is_anticipation = False
            is_kick_aligned = False
            
            # Priority 1: Strong kick alignment
            if kick_beats and rng.random() < bass_spec.kick_follow_probability:
                pool = [k for k in kick_beats if k not in used_beats]
                if pool:
                    b = rng.choice(pool)
                    is_kick_aligned = True
            
            # Priority 2: Anticipation (slightly before kick on strong beats)
            if b is None and bass_spec.anticipation_allowed and bar_anticipation_slots:
                if rng.random() < 0.35:  # 35% chance to use anticipation
                    pool = [a for a in bar_anticipation_slots if a not in used_beats and a >= bar_start]
                    if pool:
                        b = rng.choice(pool)
                        is_anticipation = True
            
            # Priority 3: Syncopation / grid fill
            if b is None:
                pool = [c for c in candidates_16 if c not in used_beats]
                if pool:
                    b = rng.choice(pool)
                else:
                    b = bar_start + note_idx * 0.25
            
            used_beats.add(b)
            
            # Track metrics
            if is_kick_aligned:
                kick_aligned_count += 1
            if is_anticipation:
                anticipation_count += 1
            total_notes += 1

            # Pitch: root or fifth; octave jump sometimes
            if rng.random() < bass_spec.octave_jump_probability:
                pitch = root_midi + 12 if rng.random() < 0.5 else fifth_midi + 12
            elif rng.random() < 0.6:
                pitch = root_midi
            else:
                pitch = fifth_midi

            # Clamp to bass register
            if pitch < 24:
                pitch = 24
            if pitch > 55:
                pitch = pitch - 12

            # Note length shaping: staccato in dense regions, longer in sparse
            dur_lo = bass_spec.note_length.min_beats
            dur_hi = bass_spec.note_length.max_beats
            if is_dense:
                # Shorter notes in dense regions
                duration = dur_lo + rng.random() * (dur_hi - dur_lo) * 0.5
            else:
                # Longer notes in sparse regions
                duration = dur_lo + rng.random() * (dur_hi - dur_lo)
            duration = round(duration * 4) / 4  # quarter grid

            # Velocity: slightly louder on kick-aligned notes
            base_vel = rng.randint(80, 105)
            if is_kick_aligned:
                vel = min(127, base_vel + 8)
            elif is_anticipation:
                vel = max(70, base_vel - 5)  # Slightly softer on anticipation
            else:
                vel = base_vel
            
            notes.append({
                "pitch": int(pitch),
                "start_beat": round(b * 4) / 4,
                "duration_beats": duration,
                "velocity": vel,
            })

    # Apply groove humanization (bass-specific timing)
    if apply_humanize:
        profile = get_groove_profile(drum_groove_template, global_spec.humanize_profile)
        ms_per_beat = 60_000 / global_spec.tempo
        
        for n in notes:
            # Bass gets slight timing variance, but less than drums
            # Use the kick role offset as a guide (bass should be tight with kick)
            offset_range = profile.role_offset_ms.get("kick", (-5, 5))
            offset_ms = rng.randint(offset_range[0], offset_range[1])
            offset_beats = offset_ms / ms_per_beat
            n["start_beat"] = max(0.0, round((n["start_beat"] + offset_beats) * 8) / 8)

    notes.sort(key=lambda x: (x["start_beat"], x["pitch"]))
    
    # Calculate alignment ratio
    kick_alignment_ratio = kick_aligned_count / total_notes if total_notes > 0 else 0.0
    
    logger.info(f"Bass IR render: {len(notes)} notes, kick alignment {kick_alignment_ratio:.1%}")
    
    if return_result:
        return BassRenderResult(
            notes=notes,
            kick_alignment_ratio=kick_alignment_ratio,
            anticipation_count=anticipation_count,
            metadata={
                "total_notes": total_notes,
                "kick_aligned_count": kick_aligned_count,
                "anticipation_count": anticipation_count,
                "style": bass_spec.style,
                "used_rhythm_spine": rhythm_spine is not None,
            },
        )
    
    return notes
