"""Drum scoring functions."""
from __future__ import annotations

import logging

from app.contracts.json_types import NoteDict
from app.services.critic.constants import DRUM_WEIGHTS
from app.services.critic.helpers import _offbeat_ratio, _get_notes_by_layer

logger = logging.getLogger(__name__)


def _score_groove_pocket(
    notes: list[NoteDict],
    layer_map: dict[int, str] | None = None,
    style: str = "trap",
) -> tuple[float, list[str]]:
    """Score timing consistency per instrument role (kick vs snare, hat regularity)."""
    repair: list[str] = []
    by_layer = _get_notes_by_layer(notes, layer_map)

    if not notes:
        return 0.0, ["empty_drums"]

    scores: list[float] = []

    core_notes = by_layer.get("core", [])
    if core_notes:
        kicks = [n for n in core_notes if n.get("pitch") in (35, 36)]
        snares = [n for n in core_notes if n.get("pitch") in (38, 39, 40)]
        if kicks and snares:
            kick_offsets = [(n["start_beat"] % 0.25) for n in kicks]
            snare_offsets = [(n["start_beat"] % 0.25) for n in snares]
            avg_kick = sum(kick_offsets) / len(kick_offsets) if kick_offsets else 0
            avg_snare = sum(snare_offsets) / len(snare_offsets) if snare_offsets else 0
            if avg_snare >= avg_kick:
                scores.append(1.0)
            else:
                scores.append(0.6)
                repair.append("pocket_inverted: snare should be later than kick")
        else:
            scores.append(0.7)

    hat_notes = by_layer.get("timekeepers", [])
    if hat_notes:
        offsets = [(n["start_beat"] % 0.5) for n in hat_notes]
        if offsets:
            avg = sum(offsets) / len(offsets)
            variance = sum((o - avg) ** 2 for o in offsets) / len(offsets)
            scores.append(max(0.5, 1.0 - variance * 10))

    ghost_notes = by_layer.get("ghost_layer", [])
    if ghost_notes:
        ghost_offsets = [(n["start_beat"] % 0.25) for n in ghost_notes]
        avg_ghost = sum(ghost_offsets) / len(ghost_offsets) if ghost_offsets else 0
        scores.append(1.0 if avg_ghost > 0.03 else 0.7)

    return sum(scores) / max(1, len(scores)) if scores else 0.7, repair


def _score_hat_articulation(
    notes: list[NoteDict],
    layer_map: dict[int, str] | None = None,
    bars: int = 16,
) -> tuple[float, list[str]]:
    """Score hi-hat articulation variety (closed/open mix, velocity arcs, bar-to-bar variation)."""
    repair: list[str] = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    hat_notes = by_layer.get("timekeepers", [])

    if not hat_notes:
        return 0.5, ["no_hats: add hi-hat layer"]

    scores: list[float] = []

    closed = sum(1 for n in hat_notes if n.get("pitch") == 42)
    open_hat = sum(1 for n in hat_notes if n.get("pitch") == 46)
    pedal = sum(1 for n in hat_notes if n.get("pitch") == 44)
    total_hats = len(hat_notes)
    open_ratio = (open_hat + pedal * 0.5) / total_hats if total_hats > 0 else 0

    if 0.03 <= open_ratio <= 0.25:
        scores.append(1.0)
    elif open_ratio < 0.03:
        scores.append(0.6)
        repair.append("hats_monotone: add occasional open hat on beat 4")
    else:
        scores.append(0.7)

    bar_vel_ranges: list[float] = []
    for b in range(bars):
        bar_start, bar_end = b * 4.0, b * 4.0 + 4.0
        bar_hats = [n for n in hat_notes if bar_start <= n.get("start_beat", 0) < bar_end]
        if len(bar_hats) >= 4:
            vels = [n.get("velocity", 80) for n in bar_hats]
            bar_vel_ranges.append(max(vels) - min(vels))
    if bar_vel_ranges:
        avg_range = sum(bar_vel_ranges) / len(bar_vel_ranges)
        if avg_range >= 12:
            scores.append(1.0)
        elif avg_range >= 6:
            scores.append(0.7)
        else:
            scores.append(0.4)
            repair.append("hats_flat_velocity: add velocity arc within bars")

    bar_patterns: list[tuple[float, ...]] = []
    for b in range(bars):
        bar_start = b * 4.0
        bar_hats = [n for n in hat_notes if bar_start <= n.get("start_beat", 0) < bar_start + 4.0]
        pattern = tuple(sorted(round((n["start_beat"] - bar_start) * 4) / 4 for n in bar_hats))
        bar_patterns.append(pattern)
    if len(bar_patterns) >= 2:
        variation_ratio = len(set(bar_patterns)) / len(bar_patterns)
        if 0.25 <= variation_ratio <= 0.8:
            scores.append(1.0)
        elif variation_ratio < 0.25:
            scores.append(0.5)
            repair.append("hats_repetitive: add variation every 2-4 bars")
        else:
            scores.append(0.7)

    return sum(scores) / max(1, len(scores)) if scores else 0.6, repair


def _score_fill_localization(
    notes: list[NoteDict],
    layer_map: dict[int, str] | None = None,
    fill_bars: list[int] | None = None,
    bars: int = 16,
) -> tuple[float, list[str]]:
    """Score whether fills occur in the correct phrase-end bars."""
    repair: list[str] = []
    fill_bars = fill_bars or [b for b in range(3, bars, 4)]
    by_layer = _get_notes_by_layer(notes, layer_map)
    fill_notes = by_layer.get("fills", [])

    if not fill_notes:
        return 0.6, ["no_fills: consider adding fill in turnaround bars"]

    scores: list[float] = []

    in_fill_bars = sum(1 for n in fill_notes if int(n.get("start_beat", 0) // 4) in fill_bars)
    localization_ratio = in_fill_bars / len(fill_notes)

    if localization_ratio >= 0.7:
        scores.append(1.0)
    elif localization_ratio >= 0.4:
        scores.append(0.6)
        repair.append("fills_scattered: concentrate fills in phrase-end bars")
    else:
        scores.append(0.3)
        repair.append("fills_misplaced: fills should be in bars " + str(fill_bars))

    non_fill_bars = [b for b in range(bars) if b not in fill_bars]
    fill_bar_hits = sum(1 for n in notes if int(n.get("start_beat", 0) // 4) in fill_bars)
    non_fill_hits = sum(1 for n in notes if int(n.get("start_beat", 0) // 4) in non_fill_bars)
    if fill_bars and non_fill_bars:
        fill_density = fill_bar_hits / len(fill_bars)
        non_fill_density = non_fill_hits / len(non_fill_bars)
        scores.append(1.0 if fill_density > non_fill_density else 0.6)

    return sum(scores) / max(1, len(scores)) if scores else 0.6, repair


def _score_ghost_plausibility(
    notes: list[NoteDict],
    layer_map: dict[int, str] | None = None,
) -> tuple[float, list[str]]:
    """Score ghost note placement (near backbeats) and velocity (quiet)."""
    repair: list[str] = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    ghost_notes = by_layer.get("ghost_layer", [])

    if not ghost_notes:
        return 0.7, []

    scores: list[float] = []

    quiet_ratio = sum(1 for n in ghost_notes if n.get("velocity", 80) < 75) / len(ghost_notes)
    if quiet_ratio >= 0.7:
        scores.append(1.0)
    elif quiet_ratio >= 0.4:
        scores.append(0.7)
    else:
        scores.append(0.4)
        repair.append("ghosts_too_loud: ghost velocity should be < 70")

    near_backbeat = sum(
        1 for n in ghost_notes
        if abs((n.get("start_beat", 0) % 4) - 1.0) < 0.6
        or abs((n.get("start_beat", 0) % 4) - 3.0) < 0.6
        or (n.get("start_beat", 0) % 4) > 3.5
        or (n.get("start_beat", 0) % 4) < 0.5
    )
    backbeat_ratio = near_backbeat / len(ghost_notes)
    scores.append(1.0 if backbeat_ratio >= 0.5 else 0.7 if backbeat_ratio >= 0.3 else 0.5)

    return sum(scores) / max(1, len(scores)) if scores else 0.7, repair


def _score_layer_balance(
    notes: list[NoteDict],
    layer_map: dict[int, str] | None = None,
) -> tuple[float, list[str]]:
    """Score layer presence: core (kick/snare), timekeepers (hats), accent layers."""
    repair: list[str] = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    present_layers = set(by_layer.keys()) - {"unknown"}
    scores: list[float] = []

    if "core" in present_layers:
        scores.append(1.0)
    else:
        scores.append(0.0)
        repair.append("no_core: add kick and snare")

    if "timekeepers" in present_layers:
        scores.append(1.0)
    else:
        scores.append(0.3)
        repair.append("no_hats: add hi-hat layer")

    accent_layers = {"fills", "ghost_layer", "cymbal_punctuation", "ear_candy"}
    if present_layers & accent_layers:
        scores.append(1.0)
    else:
        scores.append(0.5)
        repair.append("no_accents: add fills or ghost notes")

    layer_count = len(present_layers)
    scores.append(1.0 if layer_count >= 4 else 0.8 if layer_count >= 3 else 0.6)

    return sum(scores) / max(1, len(scores)) if scores else 0.5, repair


def _score_repetition_structure(notes: list[NoteDict], bars: int = 16) -> tuple[float, list[str]]:
    """Score repetition structure: A/A' patterns OK, identical bars not OK."""
    repair: list[str] = []
    if not notes or bars < 2:
        return 0.5, []

    bar_rhythms: list[tuple[float, ...]] = []
    for b in range(bars):
        bar_start = b * 4.0
        onsets = tuple(sorted(
            round((n["start_beat"] - bar_start) * 4) / 4
            for n in notes
            if bar_start <= n.get("start_beat", 0) < bar_start + 4.0
        ))
        bar_rhythms.append(onsets)

    exact_repeats = sum(1 for i in range(1, len(bar_rhythms)) if bar_rhythms[i] == bar_rhythms[i - 1])
    exact_repeat_ratio = exact_repeats / max(1, len(bar_rhythms) - 1)

    similar_pairs = sum(
        1 for i in range(1, len(bar_rhythms))
        if len(set(bar_rhythms[i]) ^ set(bar_rhythms[i - 1])) <= 2
    )
    similar_ratio = similar_pairs / max(1, len(bar_rhythms) - 1)

    if exact_repeat_ratio < 0.3 and similar_ratio > 0.2:
        score = 1.0
    elif exact_repeat_ratio < 0.5:
        score = 0.8
    elif exact_repeat_ratio < 0.7:
        score = 0.5
        repair.append("too_repetitive: add bar-to-bar variation")
    else:
        score = 0.3
        repair.append("monotonous: patterns too identical, add fills/variations")

    return score, repair


def _score_velocity_dynamics(notes: list[NoteDict], bars: int = 16) -> tuple[float, list[str]]:
    """Score velocity dynamics: backbeat accents, overall dynamic range."""
    repair: list[str] = []
    if not notes:
        return 0.0, ["no_notes"]

    scores: list[float] = []

    beat_vels: dict[int, list[int]] = {0: [], 1: [], 2: [], 3: []}
    for n in notes:
        beat_vels[int(n.get("start_beat", 0) % 4)].append(n.get("velocity", 80))
    avg_vels = {b: sum(v) / len(v) if v else 80 for b, v in beat_vels.items()}

    if (avg_vels.get(1, 80) >= avg_vels.get(0, 80) * 0.95 and
            avg_vels.get(3, 80) >= avg_vels.get(2, 80) * 0.95):
        scores.append(1.0)
    else:
        scores.append(0.7)

    all_vels = [n.get("velocity", 80) for n in notes]
    if all_vels:
        vel_range = max(all_vels) - min(all_vels)
        if vel_range >= 30:
            scores.append(1.0)
        elif vel_range >= 15:
            scores.append(0.7)
        else:
            scores.append(0.4)
            repair.append("velocity_flat: increase dynamic range")

    return sum(scores) / max(1, len(scores)) if scores else 0.5, repair


def score_drum_notes(
    notes: list[NoteDict],
    *,
    layer_map: dict[int, str] | None = None,
    fill_bars: list[int] | None = None,
    bars: int = 16,
    style: str = "trap",
    max_salience_per_beat: float = 2.5,
    min_distinct: int = 8,
) -> tuple[float, list[str]]:
    """Score drum notes using groove-aware, layer-aware rubric. Returns (score 0–1, repairs)."""
    fill_bars = fill_bars or [b for b in range(3, bars, 4)]
    all_repair: list[str] = []
    scores: dict[str, float] = {}

    pocket_score, pocket_repair = _score_groove_pocket(notes, layer_map, style)
    scores["groove_pocket"] = pocket_score
    all_repair.extend(pocket_repair)

    hat_score, hat_repair = _score_hat_articulation(notes, layer_map, bars)
    scores["hat_articulation"] = hat_score
    all_repair.extend(hat_repair)

    fill_score, fill_repair = _score_fill_localization(notes, layer_map, fill_bars, bars)
    scores["fill_localization"] = fill_score
    all_repair.extend(fill_repair)

    ghost_score, ghost_repair = _score_ghost_plausibility(notes, layer_map)
    scores["ghost_plausibility"] = ghost_score
    all_repair.extend(ghost_repair)

    balance_score, balance_repair = _score_layer_balance(notes, layer_map)
    scores["layer_balance"] = balance_score
    all_repair.extend(balance_repair)

    rep_score, rep_repair = _score_repetition_structure(notes, bars)
    scores["repetition_structure"] = rep_score
    all_repair.extend(rep_repair)

    vel_score, vel_repair = _score_velocity_dynamics(notes, bars)
    scores["velocity_dynamics"] = vel_score
    all_repair.extend(vel_repair)

    scores["syncopation"] = min(1.0, _offbeat_ratio(notes) * 2.0)

    total = sum(DRUM_WEIGHTS.get(k, 0) * scores.get(k, 0.5) for k in DRUM_WEIGHTS)
    logger.debug(f"Critic scores: {scores}, total: {total:.3f}")
    return total, all_repair
