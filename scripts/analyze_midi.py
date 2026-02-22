#!/usr/bin/env python3
"""
Comprehensive MIDI analysis — expressiveness, phrasing, rhythm, dynamics,
pitch, harmony, articulation, and performance characteristics.

Schema v2: every extractable musical signal from MIDI data.  Results are
versioned so the batch runner can invalidate stale caches.

Usage:
    python scripts/analyze_midi.py path/to/file.mid
    python scripts/analyze_midi.py path/to/file.mid --json
    python scripts/analyze_midi.py path/to/directory/   # batch mode
    python scripts/analyze_midi.py reference_midi/ --json --summary-only -o heuristics.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import mido

SCHEMA_VERSION = 2

# ── GM Program → Instrument Role mapping ──────────────────────────────────

_ROLE_RANGES: dict[str, list[range]] = {
    "lead": [
        range(40, 44),   # Violin, Viola, Cello (solo string leads)
        range(56, 64),   # Brass (trumpet, trombone, tuba, french horn)
        range(64, 72),   # Reed (sax, oboe, english horn, bassoon, clarinet)
        range(72, 80),   # Pipe (piccolo, flute, recorder, pan flute)
        range(80, 88),   # Synth Lead
        range(104, 112), # Ethnic (sitar, banjo, shamisen, koto)
    ],
    "bass": [
        range(32, 40),   # Bass (acoustic, electric, fretless, slap, synth)
        range(43, 44),   # Contrabass
        range(58, 59),   # Tuba
    ],
    "chords": [
        range(0, 8),     # Piano
        range(8, 16),    # Chromatic Percussion
        range(16, 24),   # Organ
        range(24, 32),   # Guitar
    ],
    "pads": [
        range(44, 56),   # String ensemble, Synth strings, Choir, Orchestra Hit
        range(88, 104),  # Synth Pad, Synth Effects
    ],
    "drums": [],
}

_GM_NAMES: dict[int, str] = {
    0: "acoustic_grand_piano", 4: "electric_piano", 6: "harpsichord",
    12: "marimba", 13: "xylophone", 16: "drawbar_organ", 19: "church_organ",
    24: "acoustic_guitar_nylon", 25: "acoustic_guitar_steel",
    26: "electric_guitar_jazz", 27: "electric_guitar_clean",
    29: "electric_guitar_muted", 30: "overdriven_guitar", 32: "acoustic_bass",
    33: "electric_bass_finger", 34: "electric_bass_pick", 35: "fretless_bass",
    38: "synth_bass_1", 40: "violin", 41: "viola", 42: "cello",
    43: "contrabass", 44: "tremolo_strings", 45: "pizzicato_strings",
    46: "orchestral_harp", 47: "timpani", 48: "string_ensemble_1",
    49: "string_ensemble_2", 50: "synth_strings_1", 52: "choir_aahs",
    53: "voice_oohs", 56: "trumpet", 57: "trombone", 58: "tuba",
    59: "muted_trumpet", 60: "french_horn", 61: "brass_section",
    64: "soprano_sax", 65: "alto_sax", 66: "tenor_sax", 67: "baritone_sax",
    68: "oboe", 69: "english_horn", 70: "bassoon", 71: "clarinet",
    72: "piccolo", 73: "flute", 74: "recorder", 75: "pan_flute",
    80: "synth_lead_square", 81: "synth_lead_sawtooth",
}


def _role_from_program(program: int, channel: int) -> str:
    if channel == 9:
        return "drums"
    for role, ranges in _ROLE_RANGES.items():
        for r in ranges:
            if program in r:
                return role
    return "other"


# ── Core MIDI parsing ─────────────────────────────────────────────────────

def _parse_midi(path: str) -> dict[str, Any]:
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat

    tempo_us = 500_000
    time_sig_num, time_sig_den = 4, 4
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo_us = msg.tempo
                break
            if msg.type == "time_signature":
                time_sig_num = msg.numerator
                time_sig_den = msg.denominator

    bpm = round(60_000_000 / tempo_us, 1)

    track_programs: dict[int, int] = {}
    track_channels: dict[int, int] = {}
    all_notes: list[dict] = []
    all_cc: list[dict] = []
    all_pb: list[dict] = []
    all_at: list[dict] = []
    per_track_notes: dict[int, list[dict]] = defaultdict(list)

    for track_idx, track in enumerate(mid.tracks):
        time = 0
        pending: dict[tuple[int, int], dict] = {}
        for msg in track:
            time += msg.time
            beat = round(time / tpb, 4)

            if msg.type == "program_change":
                track_programs[track_idx] = msg.program
                track_channels[track_idx] = msg.channel

            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                pending[key] = {
                    "channel": msg.channel, "pitch": msg.note,
                    "start_beat": beat, "velocity": msg.velocity,
                    "track_idx": track_idx,
                }
                if track_idx not in track_channels:
                    track_channels[track_idx] = msg.channel
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.channel, msg.note)
                if key in pending:
                    n = pending.pop(key)
                    n["duration_beats"] = round(beat - n["start_beat"], 4)
                    all_notes.append(n)
                    per_track_notes[track_idx].append(n)
            elif msg.type == "control_change":
                all_cc.append({"channel": msg.channel, "cc": msg.control,
                               "beat": beat, "value": msg.value})
            elif msg.type == "pitchwheel":
                all_pb.append({"channel": msg.channel, "beat": beat, "value": msg.pitch})
            elif msg.type in ("aftertouch", "polytouch"):
                all_at.append({"channel": msg.channel, "beat": beat, "value": msg.value})

    return {
        "path": path, "bpm": bpm, "tpb": tpb,
        "time_sig": f"{time_sig_num}/{time_sig_den}",
        "total_tracks": len(mid.tracks),
        "notes": all_notes, "cc_events": all_cc,
        "pitch_bends": all_pb, "aftertouch": all_at,
        "per_track_notes": dict(per_track_notes),
        "track_programs": track_programs,
        "track_channels": track_channels,
    }


# ── Comprehensive per-track musical analysis ──────────────────────────────

def _compute_track_metrics(notes: list[dict], total_beats: float) -> dict[str, Any]:
    """Extract every musical signal from a single track/voice."""
    if len(notes) < 4:
        return {}

    sn = sorted(notes, key=lambda n: n["start_beat"])
    m: dict[str, Any] = {}
    n_notes = len(sn)

    pitches = [n["pitch"] for n in sn]
    velocities = [n["velocity"] for n in sn]
    onsets = [n["start_beat"] for n in sn]
    durations = [n.get("duration_beats", 0.25) for n in sn]

    # ══════════════════════════════════════════════════════════════════════
    # 1. SILENCE & DENSITY
    # ══════════════════════════════════════════════════════════════════════

    # Rest ratio — fraction of beats with no notes
    resolution = 0.25
    occupied = set()
    for n in sn:
        s = n["start_beat"]
        d = n.get("duration_beats", 0.25)
        t = s
        while t < s + d:
            occupied.add(round(t / resolution))
            t += resolution
    total_slots = max(int(total_beats / resolution), 1)
    m["rest_ratio"] = round(1.0 - len(occupied) / total_slots, 4)

    # Note density per bar (4 beats)
    total_bars = max(total_beats / 4, 1)
    m["notes_per_bar"] = round(n_notes / total_bars, 2)

    # Rhythmic density curve — density in each quarter of the piece
    quarter_len = max(total_beats / 4, 1)
    density_curve = []
    for q in range(4):
        q_start = q * quarter_len
        q_end = q_start + quarter_len
        count = sum(1 for n in sn if q_start <= n["start_beat"] < q_end)
        density_curve.append(round(count / (quarter_len / 4), 2))  # per bar
    m["density_curve_q1_q4"] = density_curve

    # ══════════════════════════════════════════════════════════════════════
    # 2. PHRASING
    # ══════════════════════════════════════════════════════════════════════

    gap_threshold = 0.5
    phrases: list[list[dict]] = []
    current: list[dict] = [sn[0]]
    for i in range(1, n_notes):
        prev_end = sn[i - 1]["start_beat"] + sn[i - 1].get("duration_beats", 0.25)
        gap = sn[i]["start_beat"] - prev_end
        if gap >= gap_threshold:
            phrases.append(current)
            current = [sn[i]]
        else:
            current.append(sn[i])
    phrases.append(current)

    phrase_lengths = [
        p[-1]["start_beat"] + p[-1].get("duration_beats", 0.25) - p[0]["start_beat"]
        for p in phrases if len(p) >= 2
    ]
    if phrase_lengths:
        m["phrase_count"] = len(phrases)
        m["phrase_length"] = {
            "mean": round(statistics.mean(phrase_lengths), 2),
            "median": round(statistics.median(phrase_lengths), 2),
            "stdev": round(statistics.stdev(phrase_lengths), 2) if len(phrase_lengths) > 1 else 0,
        }
        npp = [len(p) for p in phrases]
        m["notes_per_phrase"] = {
            "mean": round(statistics.mean(npp), 1),
            "median": round(statistics.median(npp), 1),
        }
        # Phrase regularity — how consistent phrase lengths are (lower = more regular)
        if len(phrase_lengths) > 1:
            pl_mean = statistics.mean(phrase_lengths)
            m["phrase_regularity_cv"] = round(
                statistics.stdev(phrase_lengths) / pl_mean if pl_mean > 0 else 0, 3
            )

    # ══════════════════════════════════════════════════════════════════════
    # 3. RHYTHM & TIME
    # ══════════════════════════════════════════════════════════════════════

    # Note-length entropy
    quantized_dur = [round(d * 8) / 8 for d in durations]
    dur_counts = Counter(quantized_dur)
    entropy = -sum((c / n_notes) * math.log2(c / n_notes) for c in dur_counts.values() if c > 0)
    m["note_length_entropy"] = round(entropy, 3)

    # Duration distribution
    m["duration_stats"] = {
        "mean": round(statistics.mean(durations), 4),
        "median": round(statistics.median(durations), 4),
        "stdev": round(statistics.stdev(durations), 4) if n_notes > 1 else 0,
        "min": round(min(durations), 4),
        "max": round(max(durations), 4),
    }

    # IOI variability
    iois = [onsets[i + 1] - onsets[i] for i in range(n_notes - 1) if onsets[i + 1] > onsets[i]]
    if len(iois) > 2:
        ioi_mean = statistics.mean(iois)
        ioi_stdev = statistics.stdev(iois)
        m["ioi"] = {
            "mean": round(ioi_mean, 4),
            "stdev": round(ioi_stdev, 4),
            "cv": round(ioi_stdev / ioi_mean if ioi_mean > 0 else 0, 3),
        }

    # Syncopation — notes on weak beats vs strong beats
    strong_beats = {0.0, 1.0, 2.0, 3.0}
    weak_positions = 0
    for onset in onsets:
        beat_in_bar = round(onset % 4, 2)
        if beat_in_bar not in strong_beats:
            weak_positions += 1
    m["syncopation_ratio"] = round(weak_positions / n_notes, 3)

    # Onset beat-position histogram (12 slots per bar: each 8th-note triplet position)
    beat_histogram = [0] * 8  # 8 eighth-note positions in a bar
    for onset in onsets:
        slot = int(round(onset % 4 * 2)) % 8
        beat_histogram[slot] += 1
    total_h = sum(beat_histogram) or 1
    m["onset_beat_histogram"] = [round(c / total_h, 3) for c in beat_histogram]

    # Onset grid deviation (expressiveness/humanization)
    dev_16 = [abs(o - round(o * 4) / 4) for o in onsets]
    m["grid_deviation_16th"] = round(statistics.mean(dev_16), 4)
    m["pct_off_grid_16th"] = round(sum(1 for d in dev_16 if d > 0.01) / n_notes * 100, 1)

    # Swing ratio — ratio of long to short in consecutive pairs of 8th notes
    if len(iois) >= 4:
        pairs = [(iois[i], iois[i + 1]) for i in range(0, len(iois) - 1, 2)
                 if 0.1 < iois[i] < 1.0 and 0.1 < iois[i + 1] < 1.0]
        if len(pairs) >= 3:
            ratios = [a / b for a, b in pairs if b > 0]
            m["swing_ratio"] = round(statistics.mean(ratios), 3)

    # Grace note detection — very short notes (< 0.1 beats)
    grace_count = sum(1 for d in durations if d < 0.1)
    m["grace_note_ratio"] = round(grace_count / n_notes, 3)

    # Staccato / legato / sustained breakdown
    staccato = 0
    legato = 0
    sustained = 0
    artic_ratios: list[float] = []
    for i in range(n_notes - 1):
        d = durations[i]
        ioi_val = sn[i + 1]["start_beat"] - sn[i]["start_beat"]
        if ioi_val > 0:
            ratio = d / ioi_val
            artic_ratios.append(ratio)
            if ratio < 0.5:
                staccato += 1
            elif ratio > 1.0:
                sustained += 1
            else:
                legato += 1
    denom = max(staccato + legato + sustained, 1)
    m["staccato_ratio"] = round(staccato / denom, 3)
    m["legato_ratio"] = round(legato / denom, 3)
    m["sustained_ratio"] = round(sustained / denom, 3)
    if artic_ratios:
        m["articulation_ratio"] = {
            "mean": round(statistics.mean(artic_ratios), 3),
            "median": round(statistics.median(artic_ratios), 3),
        }

    # Trill detection — rapid alternation between two adjacent pitches
    trill_notes = 0
    if n_notes >= 6:
        for i in range(n_notes - 3):
            if (durations[i] < 0.3 and durations[i + 1] < 0.3 and durations[i + 2] < 0.3
                    and abs(pitches[i] - pitches[i + 1]) <= 2
                    and pitches[i] == pitches[i + 2]):
                trill_notes += 3
    m["trill_ratio"] = round(trill_notes / n_notes, 3)

    # Tremolo detection — rapid repetition of same pitch
    tremolo_notes = 0
    if n_notes >= 4:
        for i in range(n_notes - 2):
            if (durations[i] < 0.3 and durations[i + 1] < 0.3
                    and pitches[i] == pitches[i + 1] == pitches[i + 2]):
                tremolo_notes += 3
    m["tremolo_ratio"] = round(tremolo_notes / n_notes, 3)

    # ══════════════════════════════════════════════════════════════════════
    # 4. PITCH & MELODY
    # ══════════════════════════════════════════════════════════════════════

    m["pitch_range"] = {
        "min": min(pitches), "max": max(pitches),
        "range_semitones": max(pitches) - min(pitches),
        "unique_pitches": len(set(pitches)),
    }

    # Register distribution (low < 48 / mid 48-72 / high > 72)
    low = sum(1 for p in pitches if p < 48)
    mid = sum(1 for p in pitches if 48 <= p <= 72)
    high = sum(1 for p in pitches if p > 72)
    m["register"] = {
        "low_ratio": round(low / n_notes, 3),
        "mid_ratio": round(mid / n_notes, 3),
        "high_ratio": round(high / n_notes, 3),
        "mean_pitch": round(statistics.mean(pitches), 1),
    }

    # Pitch class histogram (which of the 12 semitones are used most)
    pc_counts = Counter(p % 12 for p in pitches)
    pc_names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
    pc_hist = {pc_names[i]: round(pc_counts.get(i, 0) / n_notes, 3) for i in range(12)}
    m["pitch_class_histogram"] = pc_hist
    # Pitch class entropy (tonal variety)
    pc_entropy = -sum(
        (c / n_notes) * math.log2(c / n_notes) for c in pc_counts.values() if c > 0
    )
    m["pitch_class_entropy"] = round(pc_entropy, 3)

    # Interval analysis
    intervals = [pitches[i + 1] - pitches[i] for i in range(n_notes - 1)]
    if intervals:
        abs_iv = [abs(iv) for iv in intervals]
        n_iv = len(abs_iv)
        m["intervals"] = {
            "repeat_ratio": round(sum(1 for v in abs_iv if v == 0) / n_iv, 3),
            "step_ratio": round(sum(1 for v in abs_iv if 1 <= v <= 2) / n_iv, 3),
            "leap_ratio": round(sum(1 for v in abs_iv if 3 <= v <= 7) / n_iv, 3),
            "large_leap_ratio": round(sum(1 for v in abs_iv if v >= 8) / n_iv, 3),
            "mean_abs": round(statistics.mean(abs_iv), 2),
            "ascending_ratio": round(sum(1 for iv in intervals if iv > 0) / n_iv, 3),
        }
        # Interval entropy
        iv_counts = Counter(intervals)
        iv_entropy = -sum(
            (c / n_iv) * math.log2(c / n_iv) for c in iv_counts.values() if c > 0
        )
        m["interval_entropy"] = round(iv_entropy, 3)

    # Contour complexity (direction changes)
    if n_notes >= 3:
        changes = 0
        for i in range(1, n_notes - 1):
            d1 = pitches[i] - pitches[i - 1]
            d2 = pitches[i + 1] - pitches[i]
            if (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
                changes += 1
        m["contour_complexity"] = round(changes / (n_notes - 2), 3)

    # Climax position — where the highest note appears (0.0 = start, 1.0 = end)
    max_pitch = max(pitches)
    climax_idx = pitches.index(max_pitch)
    m["climax_position"] = round(climax_idx / max(n_notes - 1, 1), 3)

    # Pitch gravity — tendency to return to most common pitch
    most_common_pitch = pc_counts.most_common(1)[0][0]
    returns_to_home = sum(1 for p in pitches if p % 12 == most_common_pitch)
    m["pitch_gravity"] = round(returns_to_home / n_notes, 3)

    # Pitch range per phrase
    if phrases and len(phrases) >= 2:
        phrase_ranges = [max(n["pitch"] for n in p) - min(n["pitch"] for n in p)
                         for p in phrases if len(p) >= 2]
        if phrase_ranges:
            m["phrase_pitch_range"] = {
                "mean": round(statistics.mean(phrase_ranges), 1),
                "median": round(statistics.median(phrase_ranges), 1),
            }

    # ══════════════════════════════════════════════════════════════════════
    # 5. MOTIF & THEMATIC STRUCTURE
    # ══════════════════════════════════════════════════════════════════════

    if len(intervals) >= 6:
        directions = [1 if iv > 0 else (-1 if iv < 0 else 0) for iv in intervals]
        bigrams = Counter(tuple(directions[i:i + 2]) for i in range(len(directions) - 1))
        trigrams = Counter(tuple(directions[i:i + 3]) for i in range(len(directions) - 2))
        # Also do pitch-interval n-grams (more specific than direction)
        clamped_iv = [max(-12, min(12, iv)) for iv in intervals]
        pitch_bigrams = Counter(tuple(clamped_iv[i:i + 2]) for i in range(len(clamped_iv) - 1))
        pitch_trigrams = Counter(tuple(clamped_iv[i:i + 3]) for i in range(len(clamped_iv) - 2))

        m["motif"] = {
            "direction_bigram_repeat": round(
                sum(c for c in bigrams.values() if c >= 2) / max(len(directions) - 1, 1), 3),
            "direction_trigram_repeat": round(
                sum(c for c in trigrams.values() if c >= 2) / max(len(directions) - 2, 1), 3),
            "pitch_bigram_repeat": round(
                sum(c for c in pitch_bigrams.values() if c >= 2) / max(len(clamped_iv) - 1, 1), 3),
            "pitch_trigram_repeat": round(
                sum(c for c in pitch_trigrams.values() if c >= 2) / max(len(clamped_iv) - 2, 1), 3),
            "unique_pitch_bigrams": len(pitch_bigrams),
            "unique_pitch_trigrams": len(pitch_trigrams),
        }

    # Rhythmic pattern repetition (quantized IOI patterns)
    if len(iois) >= 6:
        q_ioi = [round(ioi * 4) / 4 for ioi in iois]  # quantize to 16th
        rhythm_bigrams = Counter(tuple(q_ioi[i:i + 2]) for i in range(len(q_ioi) - 1))
        rhythm_trigrams = Counter(tuple(q_ioi[i:i + 3]) for i in range(len(q_ioi) - 2))
        m["rhythm_pattern"] = {
            "bigram_repeat": round(
                sum(c for c in rhythm_bigrams.values() if c >= 2) / max(len(q_ioi) - 1, 1), 3),
            "trigram_repeat": round(
                sum(c for c in rhythm_trigrams.values() if c >= 2) / max(len(q_ioi) - 2, 1), 3),
            "unique_rhythm_bigrams": len(rhythm_bigrams),
        }

    # ══════════════════════════════════════════════════════════════════════
    # 6. DYNAMICS & VELOCITY
    # ══════════════════════════════════════════════════════════════════════

    m["velocity"] = {
        "mean": round(statistics.mean(velocities), 1),
        "stdev": round(statistics.stdev(velocities), 1) if n_notes > 1 else 0,
        "min": min(velocities), "max": max(velocities),
        "range": max(velocities) - min(velocities),
    }

    # Velocity entropy
    vel_counts = Counter(velocities)
    vel_entropy = -sum(
        (c / n_notes) * math.log2(c / n_notes) for c in vel_counts.values() if c > 0
    )
    m["velocity_entropy"] = round(vel_entropy, 3)

    # Velocity–pitch correlation
    if n_notes >= 4:
        pitch_mean = statistics.mean(pitches)
        vel_mean = statistics.mean(velocities)
        pitch_std = statistics.stdev(pitches) if n_notes > 1 else 1
        vel_std = statistics.stdev(velocities) if n_notes > 1 else 1
        if pitch_std > 0 and vel_std > 0:
            covariance = sum(
                (p - pitch_mean) * (v - vel_mean) for p, v in zip(pitches, velocities)
            ) / n_notes
            m["velocity_pitch_correlation"] = round(covariance / (pitch_std * vel_std), 3)

    # Accent pattern — average velocity by beat position (8 eighth-note slots)
    accent_slots: dict[int, list[int]] = defaultdict(list)
    for n in sn:
        slot = int(round(n["start_beat"] % 4 * 2)) % 8
        accent_slots[slot].append(n["velocity"])
    accent_pattern = [
        round(statistics.mean(accent_slots[s]), 1) if s in accent_slots else 0.0
        for s in range(8)
    ]
    m["accent_pattern"] = accent_pattern

    # Velocity contour within phrases (crescendo/diminuendo tendency)
    if phrases and len(phrases) >= 2:
        phrase_vel_slopes: list[float] = []
        phrase_dyn_ranges: list[int] = []
        for p in phrases:
            if len(p) < 3:
                continue
            pvels = [n["velocity"] for n in p]
            phrase_dyn_ranges.append(max(pvels) - min(pvels))
            # Linear slope: positive = crescendo, negative = diminuendo
            x_mean = (len(pvels) - 1) / 2
            v_mean = statistics.mean(pvels)
            num = sum((i - x_mean) * (v - v_mean) for i, v in enumerate(pvels))
            den = sum((i - x_mean) ** 2 for i in range(len(pvels)))
            if den > 0:
                phrase_vel_slopes.append(num / den)

        if phrase_vel_slopes:
            m["phrase_velocity_slope"] = {
                "mean": round(statistics.mean(phrase_vel_slopes), 3),
                "stdev": round(statistics.stdev(phrase_vel_slopes), 3) if len(phrase_vel_slopes) > 1 else 0,
            }
        if phrase_dyn_ranges:
            m["phrase_dynamic_range"] = {
                "mean": round(statistics.mean(phrase_dyn_ranges), 1),
                "median": round(statistics.median(phrase_dyn_ranges), 1),
            }

    # Global dynamics arc (velocity trend across 4 quarters)
    vel_curve = []
    for q in range(4):
        q_start = q * (total_beats / 4)
        q_end = q_start + total_beats / 4
        q_vels = [n["velocity"] for n in sn if q_start <= n["start_beat"] < q_end]
        vel_curve.append(round(statistics.mean(q_vels), 1) if q_vels else 0)
    m["dynamics_curve_q1_q4"] = vel_curve

    # ══════════════════════════════════════════════════════════════════════
    # 7. POLYPHONY & TEXTURE  (sweep-line, O(n log n))
    # ══════════════════════════════════════════════════════════════════════

    events: list[tuple[float, int]] = []
    for n in sn:
        s = n["start_beat"]
        e = s + n.get("duration_beats", 0.25)
        events.append((s, 1))
        events.append((e, -1))
    events.sort()

    current_poly = 0
    max_poly = 0
    mono_time = 0.0
    poly_time = 0.0
    silent_time = 0.0
    weighted_sum = 0.0
    prev_t = events[0][0] if events else 0.0

    for t, delta in events:
        if t > prev_t:
            dt = t - prev_t
            if current_poly == 0:
                silent_time += dt
            elif current_poly == 1:
                mono_time += dt
            else:
                poly_time += dt
            weighted_sum += current_poly * dt
        current_poly += delta
        max_poly = max(max_poly, current_poly)
        prev_t = t

    total_active = mono_time + poly_time
    if total_active > 0:
        m["polyphony"] = {
            "mean": round(weighted_sum / total_active, 2),
            "max": max_poly,
            "pct_monophonic": round(mono_time / total_active, 3),
            "pct_polyphonic": round(poly_time / total_active, 3),
        }

    # Note overlap
    overlap_count = 0
    for i in range(n_notes - 1):
        end_i = sn[i]["start_beat"] + sn[i].get("duration_beats", 0.25)
        if end_i > sn[i + 1]["start_beat"]:
            overlap_count += 1
    m["note_overlap_ratio"] = round(overlap_count / max(n_notes - 1, 1), 3)

    # ══════════════════════════════════════════════════════════════════════
    # 8. PERFORMANCE IDIOM DETECTION
    # ══════════════════════════════════════════════════════════════════════

    # Arpeggio detection — rapid ascending/descending chord tones
    arpeggio_notes = 0
    if n_notes >= 3 and len(iois) >= 2 and len(intervals) >= 2:
        i = 0
        while i < n_notes - 2:
            run = 1
            while i + run < n_notes and run < 8:
                idx = i + run - 1
                if idx >= len(iois) or idx >= len(intervals):
                    break
                if iois[idx] > 0.2 or not (2 <= abs(intervals[idx]) <= 5):
                    break
                seg = intervals[i:i + run]
                if not (all(v > 0 for v in seg) or all(v < 0 for v in seg)):
                    break
                run += 1
            if run >= 3:
                arpeggio_notes += run
            i += max(run, 1)
    m["arpeggio_ratio"] = round(arpeggio_notes / n_notes, 3)

    # IOI trend within phrases (accelerando/ritardando)
    if phrases and len(phrases) >= 2:
        accel_count = 0
        rit_count = 0
        neutral_count = 0
        for p in phrases:
            if len(p) < 4:
                continue
            p_onsets = [n["start_beat"] for n in p]
            p_iois = [p_onsets[j + 1] - p_onsets[j] for j in range(len(p_onsets) - 1)
                       if p_onsets[j + 1] > p_onsets[j]]
            if len(p_iois) < 3:
                continue
            first_half = statistics.mean(p_iois[:len(p_iois) // 2])
            second_half = statistics.mean(p_iois[len(p_iois) // 2:])
            if first_half > 0:
                ratio = second_half / first_half
                if ratio < 0.85:
                    accel_count += 1
                elif ratio > 1.15:
                    rit_count += 1
                else:
                    neutral_count += 1
        total_p = accel_count + rit_count + neutral_count
        if total_p > 0:
            m["tempo_tendency"] = {
                "accelerando_ratio": round(accel_count / total_p, 3),
                "ritardando_ratio": round(rit_count / total_p, 3),
                "steady_ratio": round(neutral_count / total_p, 3),
            }

    return m


# ── File-level expressiveness metrics (CC, pitch bend, aftertouch) ────────

def _compute_expressiveness(
    notes: list[dict], cc_events: list[dict],
    pitch_bends: list[dict], aftertouch: list[dict],
    total_bars: float,
) -> dict[str, Any]:
    report: dict[str, Any] = {}

    report["events"] = {
        "notes": len(notes), "cc": len(cc_events),
        "pitch_bend": len(pitch_bends), "aftertouch": len(aftertouch),
        "total": len(notes) + len(cc_events) + len(pitch_bends) + len(aftertouch),
    }
    report["density"] = {
        "notes_per_bar": round(len(notes) / total_bars, 1),
        "cc_per_bar": round(len(cc_events) / total_bars, 1),
        "pitch_bend_per_bar": round(len(pitch_bends) / total_bars, 1),
        "aftertouch_per_bar": round(len(aftertouch) / total_bars, 1),
    }

    velocities = [n["velocity"] for n in notes]
    if velocities:
        report["velocity"] = {
            "min": min(velocities), "max": max(velocities),
            "mean": round(statistics.mean(velocities), 1),
            "stdev": round(statistics.stdev(velocities), 1) if len(velocities) > 1 else 0,
            "range": max(velocities) - min(velocities),
        }

    cc_counter: Counter = Counter()
    for ev in cc_events:
        cc_counter[ev["cc"]] += 1
    if cc_counter:
        cc_names = {
            1: "Mod Wheel", 2: "Breath", 5: "Portamento Time",
            7: "Volume", 10: "Pan", 11: "Expression",
            64: "Sustain Pedal", 65: "Portamento", 66: "Sostenuto",
            67: "Soft Pedal", 68: "Legato", 71: "Resonance",
            74: "Cutoff/Brightness", 91: "Reverb", 93: "Chorus",
        }
        report["cc_breakdown"] = {
            f"CC {cc} ({cc_names.get(cc, '?')})": count
            for cc, count in cc_counter.most_common(20)
        }

    if pitch_bends:
        pb_vals = [ev["value"] for ev in pitch_bends]
        report["pitch_bend_stats"] = {
            "min": min(pb_vals), "max": max(pb_vals),
            "mean": round(statistics.mean(pb_vals), 1),
            "non_zero_count": sum(1 for v in pb_vals if v != 0),
        }

    if notes:
        onsets = [n["start_beat"] for n in notes]
        dev_16 = [abs(o - round(o * 4) / 4) for o in onsets]
        report["onset_grid_deviation"] = {
            "from_16th_grid_mean": round(statistics.mean(dev_16), 4),
            "percent_off_16th": round(
                sum(1 for d in dev_16 if d > 0.01) / len(dev_16) * 100, 1
            ),
        }

    durations = [n.get("duration_beats", 0) for n in notes if n.get("duration_beats", 0) > 0]
    if durations:
        report["duration"] = {
            "min": round(min(durations), 4), "max": round(max(durations), 4),
            "mean": round(statistics.mean(durations), 4),
            "stdev": round(statistics.stdev(durations), 4) if len(durations) > 1 else 0,
        }

    ch_notes: dict[int, int] = defaultdict(int)
    ch_cc: dict[int, int] = defaultdict(int)
    for n in notes:
        ch_notes[n["channel"]] += 1
    for ev in cc_events:
        ch_cc[ev["channel"]] += 1
    all_chs = sorted(set(ch_notes) | set(ch_cc))
    report["channels"] = {
        ch: {"notes": ch_notes.get(ch, 0), "cc": ch_cc.get(ch, 0)}
        for ch in all_chs
    }

    if notes:
        pitches = [n["pitch"] for n in notes]
        report["pitch_range"] = {
            "min": min(pitches), "max": max(pitches),
            "range_semitones": max(pitches) - min(pitches),
            "unique_pitches": len(set(pitches)),
        }

    return report


# ── Full file analysis ────────────────────────────────────────────────────

def analyze_midi(path: str) -> dict[str, Any]:
    parsed = _parse_midi(path)
    notes = parsed["notes"]
    cc = parsed["cc_events"]
    pb = parsed["pitch_bends"]
    at = parsed["aftertouch"]

    max_beat = 0.0
    for n in notes:
        max_beat = max(max_beat, n["start_beat"] + n.get("duration_beats", 0))
    for ev in cc + pb + at:
        max_beat = max(max_beat, ev["beat"])
    total_bars = max(max_beat / 4, 1)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "file": str(path),
        "bpm": parsed["bpm"],
        "time_sig": parsed["time_sig"],
        "total_tracks": parsed["total_tracks"],
        "total_bars": round(total_bars, 1),
        "total_beats": round(max_beat, 1),
    }

    report.update(_compute_expressiveness(notes, cc, pb, at, total_bars))

    if notes:
        report["phrasing"] = _compute_track_metrics(notes, max_beat)

    # Per-track analysis with instrument role
    per_track = parsed["per_track_notes"]
    track_programs = parsed["track_programs"]
    track_channels = parsed["track_channels"]

    track_analysis: dict[str, dict[str, Any]] = {}
    for track_idx, track_notes in per_track.items():
        if len(track_notes) < 4:
            continue
        program = track_programs.get(track_idx, 0)
        channel = track_channels.get(track_idx, 0)
        role = _role_from_program(program, channel)

        track_max = max(
            n["start_beat"] + n.get("duration_beats", 0.25) for n in track_notes
        )
        tm = _compute_track_metrics(track_notes, track_max)
        if tm:
            tm["role"] = role
            tm["program"] = program
            tm["gm_name"] = _GM_NAMES.get(program, f"program_{program}")
            tm["channel"] = channel
            tm["note_count"] = len(track_notes)
            track_analysis[f"track_{track_idx}"] = tm

    if track_analysis:
        report["per_track"] = track_analysis

    return report


# ── Aggregation ───────────────────────────────────────────────────────────

def _safe_mean(vals: list[float]) -> float:
    return round(statistics.mean(vals), 3) if vals else 0

def _safe_median(vals: list[float]) -> float:
    return round(statistics.median(vals), 3) if vals else 0

def _safe_stdev(vals: list[float]) -> float:
    return round(statistics.stdev(vals), 3) if len(vals) > 1 else 0

def _pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return round(s[min(int(len(s) * p), len(s) - 1)], 3)

def _percentile_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {}
    return {
        "mean": _safe_mean(vals), "median": _safe_median(vals),
        "stdev": _safe_stdev(vals),
        "p10": _pct(vals, 0.10), "p25": _pct(vals, 0.25),
        "p75": _pct(vals, 0.75), "p90": _pct(vals, 0.90),
        "count": len(vals),
    }

# All scalar metrics we want to aggregate per role
_TRACK_SCALAR_METRICS = [
    "rest_ratio", "notes_per_bar", "note_length_entropy",
    "syncopation_ratio", "grid_deviation_16th", "pct_off_grid_16th",
    "grace_note_ratio", "staccato_ratio", "legato_ratio", "sustained_ratio",
    "trill_ratio", "tremolo_ratio", "arpeggio_ratio",
    "contour_complexity", "climax_position", "pitch_gravity",
    "pitch_class_entropy", "interval_entropy",
    "velocity_entropy", "note_overlap_ratio",
]
_TRACK_NESTED_METRICS = {
    "ioi": ["mean", "cv"],
    "phrase_length": ["mean", "median"],
    "notes_per_phrase": ["mean"],
    "intervals": ["repeat_ratio", "step_ratio", "leap_ratio", "large_leap_ratio",
                   "mean_abs", "ascending_ratio"],
    "velocity": ["mean", "stdev", "range"],
    "articulation_ratio": ["mean"],
    "pitch_range": ["range_semitones", "unique_pitches"],
    "register": ["low_ratio", "mid_ratio", "high_ratio", "mean_pitch"],
    "polyphony": ["mean", "max", "pct_monophonic", "pct_polyphonic"],
    "motif": ["direction_bigram_repeat", "direction_trigram_repeat",
              "pitch_bigram_repeat", "pitch_trigram_repeat"],
    "rhythm_pattern": ["bigram_repeat", "trigram_repeat"],
    "duration_stats": ["mean", "median", "stdev"],
    "phrase_velocity_slope": ["mean"],
    "phrase_dynamic_range": ["mean"],
    "phrase_pitch_range": ["mean"],
    "tempo_tendency": ["accelerando_ratio", "ritardando_ratio", "steady_ratio"],
}


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(reports)
    if n == 0:
        return {}

    # File-level expressiveness aggregate (preserved from v1)
    notes_per_bar = [r["density"]["notes_per_bar"] for r in reports]
    cc_per_bar = [r["density"]["cc_per_bar"] for r in reports]
    vel_means = [r["velocity"]["mean"] for r in reports if "velocity" in r]
    vel_stdevs = [r["velocity"]["stdev"] for r in reports if "velocity" in r]
    grid_devs = [r["onset_grid_deviation"]["from_16th_grid_mean"] for r in reports if "onset_grid_deviation" in r]
    off_grid = [r["onset_grid_deviation"]["percent_off_16th"] for r in reports if "onset_grid_deviation" in r]

    cc_totals: Counter = Counter()
    for r in reports:
        for label, count in r.get("cc_breakdown", {}).items():
            cc_totals[label] += count

    agg: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "file_count": n,
        "expressiveness": {
            "density": {
                "notes_per_bar": _percentile_stats(notes_per_bar),
                "cc_per_bar": _percentile_stats(cc_per_bar),
            },
            "velocity": {
                "mean_of_means": _safe_mean(vel_means),
                "mean_stdev": _safe_mean(vel_stdevs),
            },
            "timing": {
                "mean_16th_deviation": _safe_mean(grid_devs),
                "median_pct_off_grid": _safe_median(off_grid),
            },
            "cc_breakdown_total": dict(cc_totals.most_common(20)),
        },
    }

    # Per-role comprehensive aggregate
    role_data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for r in reports:
        tracks = r.get("per_track", {})
        for _tk, tm in tracks.items():
            role = tm.get("role", "other")

            for metric in _TRACK_SCALAR_METRICS:
                val = tm.get(metric)
                if val is not None:
                    role_data[role][metric].append(val)

            for parent, children in _TRACK_NESTED_METRICS.items():
                parent_data = tm.get(parent, {})
                if isinstance(parent_data, dict):
                    for child in children:
                        val = parent_data.get(child)
                        if val is not None:
                            role_data[role][f"{parent}.{child}"].append(val)

            # Special: phrase_regularity_cv
            val = tm.get("phrase_regularity_cv")
            if val is not None:
                role_data[role]["phrase_regularity_cv"].append(val)

            # Special: swing_ratio
            val = tm.get("swing_ratio")
            if val is not None:
                role_data[role]["swing_ratio"].append(val)

            # Velocity-pitch correlation
            val = tm.get("velocity_pitch_correlation")
            if val is not None:
                role_data[role]["velocity_pitch_correlation"].append(val)

    by_role: dict[str, dict[str, Any]] = {}
    for role, metrics in sorted(role_data.items()):
        role_agg: dict[str, Any] = {"track_count": max(
            len(v) for v in metrics.values()
        ) if metrics else 0}
        for metric_name, values in sorted(metrics.items()):
            role_agg[metric_name] = _percentile_stats(values)
        by_role[role] = role_agg

    agg["by_role"] = by_role
    return agg


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comprehensive MIDI analysis (schema v2)"
    )
    parser.add_argument("path", help="MIDI file or directory to analyze")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("-o", "--output", type=str, default=None)
    args = parser.parse_args()

    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.rglob("*.mid")) + sorted(target.rglob("*.midi"))
    elif target.is_file():
        files = [target]
    else:
        print(f"Error: {args.path} not found", file=sys.stderr)
        sys.exit(1)

    if not files:
        print(f"No MIDI files found in {args.path}", file=sys.stderr)
        sys.exit(1)

    reports: list[dict[str, Any]] = []
    errors = 0
    for i, f in enumerate(files, 1):
        try:
            reports.append(analyze_midi(str(f)))
            if len(files) > 10 and (i % 500 == 0 or i == len(files)):
                print(f"  ... analyzed {i}/{len(files)} files", file=sys.stderr)
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"Error analyzing {f}: {e}", file=sys.stderr)

    if args.json or args.output:
        output: dict[str, Any] = {}
        if len(reports) > 1:
            output["aggregate"] = aggregate_reports(reports)
        if not args.summary_only:
            output["files"] = reports
        elif len(reports) == 1:
            output = reports[0]
        else:
            output = output.get("aggregate", {})  # type: ignore[assignment]
        json_str = json.dumps(output, indent=2)
        if args.output:
            Path(args.output).write_text(json_str)
            print(f"Wrote results to {args.output}", file=sys.stderr)
        else:
            print(json_str)
    else:
        for r in reports:
            print(json.dumps(r, indent=2))
        if len(reports) > 1:
            print("\n=== AGGREGATE ===")
            print(json.dumps(aggregate_reports(reports), indent=2))
        if errors:
            print(f"  ({errors} files failed to parse)", file=sys.stderr)


if __name__ == "__main__":
    main()
