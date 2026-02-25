"""MidiAnalyzer — comprehensive MIDI quality metrics using mido.

Uses mido (lightweight, no numpy dependency) for MIDI parsing.
Computes structure, plausibility, harmonic, humanization, and garbage metrics.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from stori_tourdeforce.models import MidiMetrics

logger = logging.getLogger(__name__)

try:
    import mido
    HAS_MIDO = True
except ImportError:
    HAS_MIDO = False
    logger.warning("mido not installed — MIDI analysis will be limited to tool-call based metrics")


def analyze_midi_file(path: Path) -> MidiMetrics:
    """Analyze a MIDI file and return comprehensive metrics."""
    if not HAS_MIDO:
        return MidiMetrics()

    try:
        mid = mido.MidiFile(str(path))
    except Exception as e:
        logger.error("Failed to parse MIDI file %s: %s", path, e)
        return MidiMetrics()

    return _analyze_mido(mid)


def analyze_midi_bytes(data: bytes) -> MidiMetrics:
    """Analyze MIDI from raw bytes."""
    if not HAS_MIDO:
        return MidiMetrics()

    import io
    try:
        mid = mido.MidiFile(file=io.BytesIO(data))
    except Exception as e:
        logger.error("Failed to parse MIDI bytes: %s", e)
        return MidiMetrics()

    return _analyze_mido(mid)


def analyze_tool_call_notes(notes: list[dict]) -> MidiMetrics:
    """Analyze notes from Orpheus tool-call output (no MIDI file needed)."""
    if not notes:
        return MidiMetrics()

    pitches = [n.get("pitch", 60) for n in notes]
    velocities = [n.get("velocity", 80) for n in notes]
    durations = [n.get("durationBeats", n.get("duration_beats", 1.0)) for n in notes]
    starts = [n.get("startBeat", n.get("start_beat", 0.0)) for n in notes]

    metrics = MidiMetrics()
    metrics.note_count_total = len(notes)
    metrics.velocity_mean = sum(velocities) / len(velocities) if velocities else 0
    metrics.velocity_stdev = _stdev(velocities)
    metrics.velocity_range = (min(velocities), max(velocities)) if velocities else (0, 0)

    # Pitch analysis
    metrics.pitch_class_entropy = _pitch_class_entropy(pitches)
    metrics.pitch_range = {"all": (min(pitches), max(pitches)) if pitches else (0, 0)}

    # Duration/rhythm
    if starts:
        max_beat = max(s + d for s, d in zip(starts, durations))
        metrics.duration_sec = max_beat / 2.0  # rough at 120bpm

    # IOI distribution
    if len(starts) > 1:
        sorted_starts = sorted(starts)
        iois = [sorted_starts[i + 1] - sorted_starts[i] for i in range(len(sorted_starts) - 1)]
        iois = [x for x in iois if x > 0]
        if iois:
            metrics.ioi_distribution = _summarize_distribution(iois)

    # Note length distribution
    if durations:
        metrics.note_length_distribution = _summarize_distribution(durations)

    # Garbage checks
    metrics.zero_length_notes = sum(1 for d in durations if d <= 0)
    metrics.extreme_pitches = sum(1 for p in pitches if p < 12 or p > 108)
    metrics.impossible_velocities = sum(1 for v in velocities if v < 0 or v > 127)

    # Note spam: more than 32 notes starting within 0.25 beats
    _check_note_spam(starts, metrics)

    # Repetition score (pitch interval n-grams)
    metrics.repetition_score = _repetition_score(pitches)

    # Polyphony estimate
    metrics.polyphony_estimate = _polyphony_estimate(starts, durations)

    # Quality score
    metrics.quality_score = _compute_quality_score(metrics)

    return metrics


def _analyze_mido(mid: Any) -> MidiMetrics:
    """Core analysis using mido MidiFile object."""
    metrics = MidiMetrics()
    metrics.duration_sec = mid.length
    metrics.track_count = len(mid.tracks)

    # Extract tempo and time signature
    tempo = 500000  # default 120 BPM
    all_notes: list[dict] = []
    notes_by_track: dict[str, list[dict]] = defaultdict(list)
    instruments: set[int] = set()

    for track_idx, track in enumerate(mid.tracks):
        track_name = f"track_{track_idx}"
        abs_time = 0
        active_notes: dict[tuple[int, int], float] = {}

        for msg in track:
            abs_time += msg.time

            if msg.type == "set_tempo":
                tempo = msg.tempo
            elif msg.type == "time_signature":
                metrics.time_sig_changes += 1
            elif msg.type == "key_signature":
                metrics.key_signature = msg.key
            elif msg.type == "program_change":
                instruments.add(msg.program)
            elif msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                active_notes[key] = abs_time
            elif msg.type in ("note_off", "note_on") and (msg.type == "note_off" or msg.velocity == 0):
                key = (msg.channel, msg.note)
                start = active_notes.pop(key, abs_time)
                ticks_per_beat = mid.ticks_per_beat or 480
                start_beat = mido.tick2second(int(start), ticks_per_beat, tempo) * (tempo / 500000) * 2
                dur_beat = mido.tick2second(int(abs_time - start), ticks_per_beat, tempo) * (tempo / 500000) * 2

                note = {
                    "pitch": msg.note,
                    "velocity": msg.velocity if msg.type == "note_off" else 0,
                    "start_beat": start_beat,
                    "duration_beats": max(dur_beat, 0),
                    "channel": msg.channel,
                }
                all_notes.append(note)
                notes_by_track[track_name].append(note)

    metrics.tempo = mido.tempo2bpm(tempo)
    metrics.instrument_count = len(instruments)
    metrics.note_count_total = len(all_notes)

    for tname, tnotes in notes_by_track.items():
        metrics.notes_per_track[tname] = len(tnotes)

    metrics.empty_tracks = sum(1 for t in notes_by_track.values() if len(t) == 0)
    non_empty_tracks = [t for t in mid.tracks if any(m.type == "note_on" for m in t)]
    metrics.empty_tracks = metrics.track_count - len(non_empty_tracks)

    if not all_notes:
        return metrics

    pitches = [n["pitch"] for n in all_notes]
    velocities = [n["velocity"] for n in all_notes if n["velocity"] > 0]
    if not velocities:
        velocities = [80]
    durations = [n["duration_beats"] for n in all_notes]
    starts = [n["start_beat"] for n in all_notes]

    metrics.velocity_mean = sum(velocities) / len(velocities)
    metrics.velocity_stdev = _stdev(velocities)
    metrics.velocity_range = (min(velocities), max(velocities))

    metrics.pitch_class_entropy = _pitch_class_entropy(pitches)
    metrics.pitch_range = {"all": (min(pitches), max(pitches))}

    # Per-track pitch ranges
    for tname, tnotes in notes_by_track.items():
        tp = [n["pitch"] for n in tnotes]
        if tp:
            metrics.pitch_range[tname] = (min(tp), max(tp))

    metrics.zero_length_notes = sum(1 for d in durations if d <= 0)
    metrics.extreme_pitches = sum(1 for p in pitches if p < 12 or p > 108)
    metrics.impossible_velocities = sum(1 for v in velocities if v < 0 or v > 127)

    _check_note_spam(starts, metrics)

    if durations:
        metrics.note_length_distribution = _summarize_distribution(durations)

    if len(starts) > 1:
        sorted_starts = sorted(starts)
        iois = [sorted_starts[i + 1] - sorted_starts[i] for i in range(len(sorted_starts) - 1)]
        iois = [x for x in iois if x > 0]
        if iois:
            metrics.ioi_distribution = _summarize_distribution(iois)

    metrics.repetition_score = _repetition_score(pitches)
    metrics.polyphony_estimate = _polyphony_estimate(starts, durations)

    # Rhythmic density per bar (assuming 4 beats/bar)
    if starts:
        max_beat = max(starts) + 1
        bars = int(max_beat / 4) + 1
        density = [0.0] * bars
        for s in starts:
            bar = int(s / 4)
            if bar < bars:
                density[bar] += 1.0
        metrics.rhythmic_density_per_bar = density

    # Timing deviation from grid (quantize check)
    grid = 0.25  # 16th note
    deviations = [abs(s - round(s / grid) * grid) for s in starts]
    metrics.timing_deviation = sum(deviations) / len(deviations) if deviations else 0

    # Velocity variance pattern
    if len(velocities) > 1:
        diffs = [abs(velocities[i + 1] - velocities[i]) for i in range(len(velocities) - 1)]
        metrics.velocity_variance_pattern = sum(diffs) / len(diffs) if diffs else 0

    # Chord change rate (rough: pitch class set changes per bar)
    if starts and pitches:
        bars_dict: dict[int, set[int]] = defaultdict(set)
        for s, p in zip(starts, pitches):
            bars_dict[int(s / 4)].add(p % 12)
        if len(bars_dict) > 1:
            changes = 0
            prev_set: set[int] | None = None
            for bar_idx in sorted(bars_dict):
                if prev_set is not None and bars_dict[bar_idx] != prev_set:
                    changes += 1
                prev_set = bars_dict[bar_idx]
            metrics.chord_change_rate = changes / max(len(bars_dict) - 1, 1)

    metrics.quality_score = _compute_quality_score(metrics)
    return metrics


# ── Helpers ────────────────────────────────────────────────────────────────


def _stdev(values: list[float | int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _pitch_class_entropy(pitches: list[int]) -> float:
    """Shannon entropy of pitch class distribution (0-12 possible classes)."""
    if not pitches:
        return 0.0
    counts = Counter(p % 12 for p in pitches)
    total = sum(counts.values())
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _repetition_score(pitches: list[int]) -> float:
    """N-gram repetition score on pitch intervals. 0=no repetition, 1=fully repetitive."""
    if len(pitches) < 4:
        return 0.0
    intervals = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
    n = 3  # trigram
    ngrams = [tuple(intervals[i:i + n]) for i in range(len(intervals) - n + 1)]
    if not ngrams:
        return 0.0
    counts = Counter(ngrams)
    most_common_count = counts.most_common(1)[0][1]
    return most_common_count / len(ngrams)


def _polyphony_estimate(starts: list[float], durations: list[float]) -> float:
    """Average number of simultaneously sounding notes."""
    if not starts:
        return 0.0
    events: list[tuple[float, int]] = []
    for s, d in zip(starts, durations):
        events.append((s, 1))
        events.append((s + d, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    current = 0
    max_poly = 0
    total = 0.0
    count = 0
    for _, delta in events:
        current += delta
        max_poly = max(max_poly, current)
        total += current
        count += 1
    return total / count if count > 0 else 0.0


def _check_note_spam(starts: list[float], metrics: MidiMetrics) -> None:
    """Flag regions with excessive note density."""
    if len(starts) < 32:
        return
    sorted_starts = sorted(starts)
    window = 0.25
    spam_count = 0
    for i in range(len(sorted_starts) - 31):
        if sorted_starts[i + 31] - sorted_starts[i] <= window:
            spam_count += 1
    metrics.note_spam_regions = spam_count


def _summarize_distribution(values: list[float]) -> dict[str, float]:
    """Compute summary statistics for a distribution."""
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    return {
        "min": s[0],
        "max": s[-1],
        "mean": sum(s) / n,
        "median": s[n // 2],
        "stdev": _stdev(values),
    }


def _compute_quality_score(m: MidiMetrics) -> float:
    """Compute composite quality score (0-100).

    Weights:
    - Note count sanity: 20pts (penalize <4 or >2000)
    - Pitch entropy: 15pts (reward 2-4 bits)
    - Velocity variance: 15pts (reward humanlike)
    - No garbage: 30pts (heavy penalty for spam/extreme/zero-len)
    - Rhythmic diversity: 10pts
    - Polyphony: 10pts
    """
    score = 0.0

    # Note count (0-20)
    nc = m.note_count_total
    if 4 <= nc <= 2000:
        score += 20.0
    elif nc > 0:
        score += max(0, 20 - abs(nc - 100) * 0.1)

    # Pitch entropy (0-15) — ideal 2-4 bits
    if 2.0 <= m.pitch_class_entropy <= 4.0:
        score += 15.0
    elif m.pitch_class_entropy > 0:
        score += max(0, 15 - abs(m.pitch_class_entropy - 3.0) * 5)

    # Velocity variance (0-15)
    if 5 <= m.velocity_stdev <= 30:
        score += 15.0
    elif m.velocity_stdev > 0:
        score += max(0, 15 - abs(m.velocity_stdev - 15) * 0.5)

    # Garbage penalty (0-30)
    garbage = m.zero_length_notes + m.extreme_pitches + m.impossible_velocities + m.note_spam_regions * 10 + m.empty_tracks * 5
    score += max(0, 30 - garbage * 3)

    # Rhythmic diversity (0-10)
    if m.rhythmic_density_per_bar:
        density_stdev = _stdev(m.rhythmic_density_per_bar)
        if 1.0 <= density_stdev <= 10.0:
            score += 10.0
        elif density_stdev > 0:
            score += max(0, 10 - abs(density_stdev - 5) * 2)

    # Polyphony (0-10)
    if 1.0 <= m.polyphony_estimate <= 6.0:
        score += 10.0
    elif m.polyphony_estimate > 0:
        score += max(0, 10 - abs(m.polyphony_estimate - 3) * 2)

    return min(100.0, max(0.0, score))
