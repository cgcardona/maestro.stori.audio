"""
Drum Spec IR → MIDI notes renderer.

Renders DrumSpec + GlobalSpec to a list of {pitch, start_beat, duration_beats, velocity, layer}
with groove templates, layers, salience cap, fill bars, and variation.
Includes layer labels in output for critic scoring.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TypedDict

from app.contracts.json_types import NoteDict
from app.core.music_spec_ir import (
    DrumSpec,
    DrumLayerSpec,
    DrumConstraints,
    GlobalSpec,
    default_drum_spec,
    GROOVE_TEMPLATE_VALUES,
)
from app.services.groove_engine import apply_groove_map, RhythmSpine

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Drum Render Result: notes + metadata (including layer info)
# -----------------------------------------------------------------------------

class DrumRenderMetadata(TypedDict, total=False):
    """Metadata emitted by the drum IR renderer alongside its notes."""

    style: str
    groove_template: str
    humanize_profile: str
    bars: int
    tempo: int


@dataclass
class DrumRenderResult:
    """
    Result of drum rendering with notes and metadata.

    Attributes:
        notes: list of {pitch, start_beat, duration_beats, velocity, layer}
        layer_map: dict mapping note index → layer name
        rhythm_spine: RhythmSpine for bass/melody coupling
        metadata: Drum render provenance metadata
    """
    notes: list[NoteDict] = field(default_factory=list)
    layer_map: dict[int, str] = field(default_factory=dict)
    rhythm_spine: RhythmSpine | None = None
    metadata: DrumRenderMetadata = field(default_factory=DrumRenderMetadata)


# -----------------------------------------------------------------------------
# Groove templates: kick placement, hat grid, syncopation
# -----------------------------------------------------------------------------

def _kick_placements(groove_template: str, bar_in_phrase: int) -> list[float]:
    """Expected kick beat positions within a 4-beat bar (0..4)."""
    if groove_template == "house_four_on_floor":
        return [0.0, 1.0, 2.0, 3.0]
    if groove_template in ("trap_triplet", "trap_straight"):
        # Trap: 1 and 3, or 1 and 3.5
        if groove_template == "trap_triplet":
            return [0.0, 2.0, 2.666]  # triplet feel
        return [0.0, 2.0, 3.5]
    if groove_template == "boom_bap_swing":
        return [0.0, 2.5]
    return [0.0, 2.0]


def _hat_subdivision(groove_template: str) -> int:
    """Number of hat hits per beat (2=8ths, 4=16ths)."""
    if groove_template == "trap_straight":
        return 4  # 16ths
    if groove_template == "trap_triplet":
        return 3  # triplets
    if groove_template == "boom_bap_swing":
        return 2  # 8ths swung
    if groove_template == "house_four_on_floor":
        return 4
    return 4


def _snare_placements(groove_template: str) -> list[float]:
    """Snare/clap backbeat positions (beats within bar)."""
    if groove_template == "house_four_on_floor":
        return [1.0, 3.0]
    return [1.0, 3.0]  # 2 and 4


def _syncopation_probability(groove_template: str) -> float:
    """Probability of offbeat ghost/hat."""
    if groove_template == "boom_bap_swing":
        return 0.4
    if groove_template in ("trap_straight", "trap_triplet"):
        return 0.35
    return 0.2


# -----------------------------------------------------------------------------
# Salience: compute and enforce max_salience_per_beat
# -----------------------------------------------------------------------------

def _salience_at_beat(pairs: list[tuple[NoteDict, str]], beat: float, salience_weight: dict[str, float]) -> float:
    """Sum salience of all notes overlapping beat (by layer)."""
    total = 0.0
    for n, layer in pairs:
        start = n["start_beat"]
        dur = n.get("duration_beats", 0.25)
        if start <= beat < start + dur:
            total += salience_weight.get(layer, 0.5)
    return total


def _apply_salience_cap(
    notes: list[NoteDict],
    layer_of: dict[int, str],
    salience_weight: dict[str, float],
    max_salience_per_beat: float,
    beat_resolution: float = 0.25,
) -> list[NoteDict]:
    """Drop lowest-salience hits when a beat exceeds max_salience_per_beat."""
    if not notes:
        return notes
    pairs: list[tuple[NoteDict, str]] = [(n, layer_of.get(id(n), "timekeepers")) for n in notes]
    beats_to_check = set()
    for n in notes:
        start = n["start_beat"]
        dur = n.get("duration_beats", 0.25)
        b = start
        while b < start + dur:
            beats_to_check.add(round(b / beat_resolution) * beat_resolution)
            b += beat_resolution
    while True:
        max_excess = 0.0
        worst_beat = None
        for beat in beats_to_check:
            s = _salience_at_beat(pairs, beat, salience_weight)
            if s > max_salience_per_beat and s - max_salience_per_beat > max_excess:
                max_excess = s - max_salience_per_beat
                worst_beat = beat
        if worst_beat is None:
            break
        candidates = [
            (i, n, layer)
            for i, (n, layer) in enumerate(pairs)
            if n["start_beat"] <= worst_beat < n["start_beat"] + n.get("duration_beats", 0.25)
        ]
        if not candidates:
            break
        candidates.sort(key=lambda x: salience_weight.get(x[2], 0.5))
        idx = candidates[0][0]
        pairs.pop(idx)
    return [n for n, _ in pairs]


# -----------------------------------------------------------------------------
# Core render: one layer
# -----------------------------------------------------------------------------

def _render_core(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
    bar_in_phrase: int,
) -> list[NoteDict]:
    """Kick + snare/clap from groove template."""
    notes: list[NoteDict] = []
    groove = drum_spec.groove_template
    core = drum_spec.layers.get("core")
    if not core:
        return notes
    kick_beats = _kick_placements(groove, bar_in_phrase)
    snare_beats = _snare_placements(groove)
    v_lo, v_hi = core.velocity_range
    for b in kick_beats:
        beat = bar_start + b
        notes.append({
            "pitch": 36,
            "start_beat": round(beat, 3),
            "duration_beats": 0.4,
            "velocity": random.randint(v_lo, v_hi),
        })
    for b in snare_beats:
        beat = bar_start + b
        notes.append({
            "pitch": 38 if random.random() > 0.3 else 39,
            "start_beat": round(beat, 3),
            "duration_beats": 0.35,
            "velocity": random.randint(v_lo, v_hi),
        })
    return notes


def _render_timekeepers(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
    bar_in_phrase: int,
) -> list[NoteDict]:
    """Hi-hats from groove subdivision."""
    notes: list[NoteDict] = []
    layer = drum_spec.layers.get("timekeepers")
    if not layer:
        return notes
    sub = _hat_subdivision(drum_spec.groove_template)
    v_lo, v_hi = layer.velocity_range
    for i in range(4 * sub):
        beat_in_bar = i / sub
        beat = bar_start + beat_in_bar
        vel = random.randint(v_lo, v_hi)
        if i % 2 == 1:
            vel = max(v_lo, vel - 15)
        # Open hat on last 16th of bar sometimes
        pitch = 42
        if sub == 4 and i == 4 * sub - 1 and random.random() < 0.25:
            pitch = 46
        notes.append({
            "pitch": pitch,
            "start_beat": round(beat, 3),
            "duration_beats": 0.5 / sub,
            "velocity": vel,
        })
    return notes


def _render_fills(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
) -> list[NoteDict]:
    """Toms/snare roll only in fill bars."""
    notes: list[NoteDict] = []
    if bar_index not in drum_spec.constraints.fill_bars:
        return notes
    layer = drum_spec.layers.get("fills")
    if not layer or not layer.instruments:
        return notes
    # Fill: spread hits across the bar
    n_hits = random.randint(4, min(8, layer.max_fill_density_per_bar or 8))
    for i in range(n_hits):
        beat_in_bar = (i + 0.5) * (4.0 / n_hits)
        beat = bar_start + beat_in_bar
        pitch = random.choice(layer.instruments)
        notes.append({
            "pitch": pitch,
            "start_beat": round(beat, 3),
            "duration_beats": 0.25,
            "velocity": random.randint(70, 100),
        })
    return notes


def _render_ghost(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
    bar_in_phrase: int,
) -> list[NoteDict]:
    """Sparse ghost/rim hits on offbeats."""
    notes: list[NoteDict] = []
    layer = drum_spec.layers.get("ghost_layer")
    if not layer:
        return notes
    prob = _syncopation_probability(drum_spec.groove_template)
    v_lo, v_hi = layer.velocity_range
    for i in range(8):
        beat_in_bar = 0.5 * i + 0.25  # offbeats
        if random.random() > prob:
            continue
        beat = bar_start + beat_in_bar
        pitch = random.choice(layer.instruments)
        notes.append({
            "pitch": pitch,
            "start_beat": round(beat, 3),
            "duration_beats": 0.2,
            "velocity": random.randint(v_lo, v_hi),
        })
    return notes


def _render_cymbal_punctuation(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
) -> list[NoteDict]:
    """Crash/ride on section starts and end of fill bars."""
    notes: list[NoteDict] = []
    if bar_index not in drum_spec.constraints.fill_bars and bar_index % 4 != 0:
        return notes
    layer = drum_spec.layers.get("cymbal_punctuation")
    if not layer and random.random() > 0.3:
        return notes
    if not layer:
        return notes
    beat = bar_start + 0.0
    pitch = 49 if bar_index % 4 == 0 else 51
    notes.append({
        "pitch": pitch,
        "start_beat": round(beat, 3),
        "duration_beats": 0.5,
        "velocity": random.randint(75, 95),
    })
    return notes


def _render_ear_candy(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    bar_start: float,
    bar_index: int,
) -> list[NoteDict]:
    """Very sparse perc."""
    notes: list[NoteDict] = []
    layer = drum_spec.layers.get("ear_candy")
    if not layer or random.random() > layer.probability:
        return notes
    beat_in_bar = random.choice([0.5, 1.5, 2.5, 3.5])
    notes.append({
        "pitch": random.choice(layer.instruments),
        "start_beat": round(bar_start + beat_in_bar, 3),
        "duration_beats": 0.25,
        "velocity": random.randint(50, 75),
    })
    return notes


# -----------------------------------------------------------------------------
# Main entry: DrumSpec + GlobalSpec → notes (with layer labels)
# -----------------------------------------------------------------------------

def render_drum_spec(
    drum_spec: DrumSpec,
    global_spec: GlobalSpec,
    *,
    apply_salience_cap: bool = True,
    apply_groove: bool = True,
    return_result: bool = False,
) -> list[NoteDict] | DrumRenderResult:
    """
    Render DrumSpec + GlobalSpec to MIDI note list.

    Returns list of {pitch, start_beat, duration_beats, velocity, layer} with
    groove template, layers, fill bars, variation, and optional salience cap.
    
    Uses Groove Engine for humanization (style-specific microtiming).
    Each note includes a "layer" field for critic scoring.
    
    Args:
        drum_spec: DrumSpec with layers, groove template, constraints
        global_spec: GlobalSpec with tempo, bars, humanize profile
        apply_salience_cap: Whether to enforce max_salience_per_beat
        apply_groove: Whether to apply Groove Engine humanization
        return_result: If True, return DrumRenderResult with metadata; else just notes
    
    Returns:
        list of notes, or DrumRenderResult if return_result=True
    """
    notes: list[NoteDict] = []
    layer_of: dict[int, str] = {}
    salience_weight = drum_spec.salience_weight
    bars = global_spec.bars

    for bar_index in range(bars):
        bar_start = bar_index * 4.0
        bar_in_phrase = bar_index % 4
        
        # Core layer (kick + snare)
        for n in _render_core(drum_spec, global_spec, bar_start, bar_index, bar_in_phrase):
            n["layer"] = "core"
            notes.append(n)
            layer_of[id(n)] = "core"
        
        # Timekeepers (hi-hats)
        for n in _render_timekeepers(drum_spec, global_spec, bar_start, bar_index, bar_in_phrase):
            n["layer"] = "timekeepers"
            notes.append(n)
            layer_of[id(n)] = "timekeepers"
        
        # Fills (toms, rolls)
        for n in _render_fills(drum_spec, global_spec, bar_start, bar_index):
            n["layer"] = "fills"
            notes.append(n)
            layer_of[id(n)] = "fills"
        
        # Ghost layer (ghost notes, rim shots)
        for n in _render_ghost(drum_spec, global_spec, bar_start, bar_index, bar_in_phrase):
            n["layer"] = "ghost_layer"
            notes.append(n)
            layer_of[id(n)] = "ghost_layer"
        
        # Cymbal punctuation (crash, ride)
        for n in _render_cymbal_punctuation(drum_spec, global_spec, bar_start, bar_index):
            n["layer"] = "cymbal_punctuation"
            notes.append(n)
            layer_of[id(n)] = "cymbal_punctuation"
        
        # Ear candy (percussion)
        for n in _render_ear_candy(drum_spec, global_spec, bar_start, bar_index):
            n["layer"] = "ear_candy"
            notes.append(n)
            layer_of[id(n)] = "ear_candy"

    if apply_salience_cap:
        notes = _apply_salience_cap(
            notes,
            layer_of,
            salience_weight,
            drum_spec.constraints.max_salience_per_beat,
        )
    
    # Apply Groove Engine (style-aware humanization)
    if apply_groove:
        # Build layer_map by index for groove engine
        layer_map = {i: n.get("layer", "timekeepers") for i, n in enumerate(notes)}
        notes = apply_groove_map(
            notes,
            tempo=global_spec.tempo,
            style=drum_spec.groove_template,
            humanize_profile=global_spec.humanize_profile,
            layer_map=layer_map,
        )

    # Sort by start_beat for stable output
    notes.sort(key=lambda n: (n["start_beat"], n["pitch"]))
    
    logger.info(f"Drum IR render: {len(notes)} notes, {len(set(n['pitch'] for n in notes))} distinct pitches")
    
    if return_result:
        # Build layer_map by final index
        final_layer_map = {i: n.get("layer", "timekeepers") for i, n in enumerate(notes)}
        
        # Create rhythm spine for bass/melody coupling
        rhythm_spine = RhythmSpine.from_drum_notes(
            notes,
            tempo=global_spec.tempo,
            bars=global_spec.bars,
            style=drum_spec.style,
        )
        
        return DrumRenderResult(
            notes=notes,
            layer_map=final_layer_map,
            rhythm_spine=rhythm_spine,
            metadata={
                "style": drum_spec.style,
                "groove_template": drum_spec.groove_template,
                "humanize_profile": global_spec.humanize_profile,
                "bars": global_spec.bars,
                "tempo": global_spec.tempo,
            },
        )
    
    return notes
