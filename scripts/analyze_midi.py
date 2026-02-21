#!/usr/bin/env python3
"""
Analyze MIDI files for expressiveness metrics.

Usage:
    python scripts/analyze_midi.py path/to/file.mid
    python scripts/analyze_midi.py path/to/file.mid --json
    python scripts/analyze_midi.py path/to/directory/   # batch mode
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import mido


def analyze_midi(path: str) -> dict[str, Any]:
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat

    # Detect tempo from MIDI meta events (first set_tempo, or default 120 BPM)
    tempo_us = 500_000  # 120 BPM default
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo_us = msg.tempo
                break

    bpm = round(60_000_000 / tempo_us, 1)

    # Collect events across all tracks
    notes: list[dict] = []
    cc_events: list[dict] = []
    pitch_bends: list[dict] = []
    aftertouch: list[dict] = []
    pending: dict[tuple[int, int], dict] = {}  # (ch, pitch) → note dict

    total_tracks = len(mid.tracks)

    for track in mid.tracks:
        time = 0
        for msg in track:
            time += msg.time
            beat = round(time / tpb, 4)

            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                pending[key] = {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start_beat": beat,
                    "velocity": msg.velocity,
                }
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.channel, msg.note)
                if key in pending:
                    n = pending.pop(key)
                    n["duration_beats"] = round(beat - n["start_beat"], 4)
                    notes.append(n)
            elif msg.type == "control_change":
                cc_events.append({
                    "channel": msg.channel,
                    "cc": msg.control,
                    "beat": beat,
                    "value": msg.value,
                })
            elif msg.type == "pitchwheel":
                pitch_bends.append({
                    "channel": msg.channel,
                    "beat": beat,
                    "value": msg.pitch,
                })
            elif msg.type == "aftertouch":
                aftertouch.append({
                    "channel": msg.channel,
                    "beat": beat,
                    "value": msg.value,
                })
            elif msg.type == "polytouch":
                aftertouch.append({
                    "channel": msg.channel,
                    "beat": beat,
                    "value": msg.value,
                    "pitch": msg.note,
                })

    # Compute total duration in beats/bars
    max_beat = 0.0
    for n in notes:
        max_beat = max(max_beat, n["start_beat"] + n.get("duration_beats", 0))
    for ev in cc_events + pitch_bends + aftertouch:
        max_beat = max(max_beat, ev["beat"])
    total_bars = max(max_beat / 4, 1)

    # ── Event Counts ──
    report: dict[str, Any] = {
        "file": str(path),
        "bpm": bpm,
        "total_tracks": total_tracks,
        "total_bars": round(total_bars, 1),
        "total_beats": round(max_beat, 1),
        "events": {
            "notes": len(notes),
            "cc": len(cc_events),
            "pitch_bend": len(pitch_bends),
            "aftertouch": len(aftertouch),
            "total": len(notes) + len(cc_events) + len(pitch_bends) + len(aftertouch),
        },
        "density": {
            "notes_per_bar": round(len(notes) / total_bars, 1),
            "cc_per_bar": round(len(cc_events) / total_bars, 1),
            "pitch_bend_per_bar": round(len(pitch_bends) / total_bars, 1),
            "aftertouch_per_bar": round(len(aftertouch) / total_bars, 1),
        },
    }

    # ── Velocity Distribution ──
    velocities = [n["velocity"] for n in notes]
    if velocities:
        report["velocity"] = {
            "min": min(velocities),
            "max": max(velocities),
            "mean": round(statistics.mean(velocities), 1),
            "stdev": round(statistics.stdev(velocities), 1) if len(velocities) > 1 else 0,
            "range": max(velocities) - min(velocities),
        }

    # ── CC Breakdown ──
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

    # ── Pitch Bend Stats ──
    if pitch_bends:
        pb_vals = [ev["value"] for ev in pitch_bends]
        report["pitch_bend_stats"] = {
            "min": min(pb_vals),
            "max": max(pb_vals),
            "mean": round(statistics.mean(pb_vals), 1),
            "non_zero_count": sum(1 for v in pb_vals if v != 0),
        }

    # ── Note Onset Grid Deviation (expressiveness metric) ──
    if notes:
        onsets = [n["start_beat"] for n in notes]
        deviations_16th = [abs(o - round(o * 4) / 4) for o in onsets]
        deviations_8th = [abs(o - round(o * 2) / 2) for o in onsets]
        report["onset_grid_deviation"] = {
            "from_16th_grid_mean": round(statistics.mean(deviations_16th), 4),
            "from_8th_grid_mean": round(statistics.mean(deviations_8th), 4),
            "percent_off_16th": round(
                sum(1 for d in deviations_16th if d > 0.01) / len(deviations_16th) * 100, 1
            ),
        }

    # ── Duration Distribution ──
    durations = [n.get("duration_beats", 0) for n in notes if n.get("duration_beats", 0) > 0]
    if durations:
        report["duration"] = {
            "min": round(min(durations), 4),
            "max": round(max(durations), 4),
            "mean": round(statistics.mean(durations), 4),
            "stdev": round(statistics.stdev(durations), 4) if len(durations) > 1 else 0,
        }

    # ── Per-Channel Summary ──
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

    # ── Pitch Range ──
    if notes:
        pitches = [n["pitch"] for n in notes]
        report["pitch_range"] = {
            "min": min(pitches),
            "max": max(pitches),
            "range_semitones": max(pitches) - min(pitches),
            "unique_pitches": len(set(pitches)),
        }

    # ── Dynamics Arc (velocity per bar) ──
    if notes and total_bars >= 2:
        bar_vels: dict[int, list[int]] = defaultdict(list)
        for n in notes:
            bar_idx = int(n["start_beat"] // 4)
            bar_vels[bar_idx].append(n["velocity"])
        arc = {}
        for bar_idx in sorted(bar_vels):
            vels = bar_vels[bar_idx]
            arc[f"bar_{bar_idx + 1}"] = round(statistics.mean(vels), 1)
        report["dynamics_arc"] = arc

    return report


def print_report(report: dict[str, Any]) -> None:
    print(f"\n{'=' * 70}")
    print(f"  MIDI Analysis: {report['file']}")
    print(f"{'=' * 70}")
    print(f"  BPM: {report['bpm']}  |  Tracks: {report['total_tracks']}  |"
          f"  Bars: {report['total_bars']}  |  Beats: {report['total_beats']}")

    ev = report["events"]
    print(f"\n  EVENT COUNTS")
    print(f"    Notes:       {ev['notes']:>7}")
    print(f"    CC:          {ev['cc']:>7}")
    print(f"    Pitch Bend:  {ev['pitch_bend']:>7}")
    print(f"    Aftertouch:  {ev['aftertouch']:>7}")
    print(f"    TOTAL:       {ev['total']:>7}")

    d = report["density"]
    print(f"\n  DENSITY (per bar)")
    print(f"    Notes/bar:       {d['notes_per_bar']:>7}")
    print(f"    CC/bar:          {d['cc_per_bar']:>7}")
    print(f"    Pitch bend/bar:  {d['pitch_bend_per_bar']:>7}")
    print(f"    Aftertouch/bar:  {d['aftertouch_per_bar']:>7}")

    if "velocity" in report:
        v = report["velocity"]
        print(f"\n  VELOCITY")
        print(f"    Range: {v['min']}–{v['max']} (spread {v['range']})")
        print(f"    Mean: {v['mean']}  |  Stdev: {v['stdev']}")

    if "cc_breakdown" in report:
        print(f"\n  CC BREAKDOWN (top controllers)")
        for label, count in report["cc_breakdown"].items():
            print(f"    {label}: {count}")

    if "pitch_bend_stats" in report:
        pb = report["pitch_bend_stats"]
        print(f"\n  PITCH BEND")
        print(f"    Range: {pb['min']} to {pb['max']}")
        print(f"    Non-zero: {pb['non_zero_count']}")

    if "onset_grid_deviation" in report:
        og = report["onset_grid_deviation"]
        print(f"\n  ONSET GRID DEVIATION (expressiveness)")
        print(f"    Mean deviation from 16th grid: {og['from_16th_grid_mean']} beats")
        print(f"    % notes off 16th grid:         {og['percent_off_16th']}%")

    if "duration" in report:
        dur = report["duration"]
        print(f"\n  DURATION")
        print(f"    Range: {dur['min']}–{dur['max']} beats")
        print(f"    Mean: {dur['mean']}  |  Stdev: {dur['stdev']}")

    if "pitch_range" in report:
        pr = report["pitch_range"]
        print(f"\n  PITCH RANGE")
        print(f"    MIDI {pr['min']}–{pr['max']} ({pr['range_semitones']} semitones)")
        print(f"    Unique pitches: {pr['unique_pitches']}")

    if "channels" in report:
        print(f"\n  PER-CHANNEL")
        for ch, info in report["channels"].items():
            label = "DRUMS" if ch == 9 else f"Ch {ch}"
            print(f"    {label:>8}: {info['notes']:>5} notes, {info['cc']:>5} CC")

    if "dynamics_arc" in report:
        print(f"\n  DYNAMICS ARC (avg velocity per bar)")
        arc = report["dynamics_arc"]
        bars_to_show = list(arc.items())
        if len(bars_to_show) > 20:
            bars_to_show = bars_to_show[:10] + [("...", "...")] + bars_to_show[-5:]
        for label, val in bars_to_show:
            print(f"    {label}: {val}")

    print(f"\n{'=' * 70}\n")


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Build cross-file aggregate heuristics from a batch of analyses."""
    n = len(reports)
    if n == 0:
        return {}

    def safe_mean(vals: list[float]) -> float:
        return round(statistics.mean(vals), 2) if vals else 0

    def safe_median(vals: list[float]) -> float:
        return round(statistics.median(vals), 2) if vals else 0

    def safe_stdev(vals: list[float]) -> float:
        return round(statistics.stdev(vals), 2) if len(vals) > 1 else 0

    def pct(vals: list[float], p: float) -> float:
        s = sorted(vals)
        idx = int(len(s) * p)
        return round(s[min(idx, len(s) - 1)], 2)

    notes_per_bar = [r["density"]["notes_per_bar"] for r in reports]
    cc_per_bar = [r["density"]["cc_per_bar"] for r in reports]
    pb_per_bar = [r["density"]["pitch_bend_per_bar"] for r in reports]
    at_per_bar = [r["density"]["aftertouch_per_bar"] for r in reports]

    vel_means = [r["velocity"]["mean"] for r in reports if "velocity" in r]
    vel_stdevs = [r["velocity"]["stdev"] for r in reports if "velocity" in r]
    vel_ranges = [r["velocity"]["range"] for r in reports if "velocity" in r]

    grid_devs = [r["onset_grid_deviation"]["from_16th_grid_mean"] for r in reports if "onset_grid_deviation" in r]
    off_grid_pcts = [r["onset_grid_deviation"]["percent_off_16th"] for r in reports if "onset_grid_deviation" in r]

    dur_means = [r["duration"]["mean"] for r in reports if "duration" in r]
    dur_stdevs = [r["duration"]["stdev"] for r in reports if "duration" in r]

    # Aggregate CC breakdown across all files
    cc_totals: Counter = Counter()
    for r in reports:
        if "cc_breakdown" in r:
            for label, count in r["cc_breakdown"].items():
                cc_totals[label] += count

    agg: dict[str, Any] = {
        "file_count": n,
        "density": {
            "notes_per_bar":  {"mean": safe_mean(notes_per_bar), "median": safe_median(notes_per_bar), "p10": pct(notes_per_bar, 0.1), "p90": pct(notes_per_bar, 0.9)},
            "cc_per_bar":     {"mean": safe_mean(cc_per_bar), "median": safe_median(cc_per_bar), "p10": pct(cc_per_bar, 0.1), "p90": pct(cc_per_bar, 0.9)},
            "pb_per_bar":     {"mean": safe_mean(pb_per_bar), "median": safe_median(pb_per_bar)},
            "at_per_bar":     {"mean": safe_mean(at_per_bar), "median": safe_median(at_per_bar)},
        },
        "velocity": {
            "mean_of_means": safe_mean(vel_means),
            "mean_stdev": safe_mean(vel_stdevs),
            "mean_range": safe_mean(vel_ranges),
            "stdev_p10": pct(vel_stdevs, 0.1) if vel_stdevs else 0,
            "stdev_p90": pct(vel_stdevs, 0.9) if vel_stdevs else 0,
        },
        "timing_humanization": {
            "mean_16th_deviation": safe_mean(grid_devs),
            "median_pct_off_grid": safe_median(off_grid_pcts),
        },
        "duration": {
            "mean_of_means": safe_mean(dur_means),
            "mean_stdev": safe_mean(dur_stdevs),
        },
        "cc_breakdown_total": dict(cc_totals.most_common(20)),
    }
    return agg


def print_aggregate(agg: dict[str, Any]) -> None:
    n = agg["file_count"]
    print(f"\n{'=' * 70}")
    print(f"  AGGREGATE HEURISTICS ({n} files)")
    print(f"{'=' * 70}")

    d = agg["density"]
    print(f"\n  DENSITY PER BAR (mean / median / p10-p90)")
    for key in ("notes_per_bar", "cc_per_bar", "pb_per_bar", "at_per_bar"):
        info = d[key]
        label = key.replace("_", " ").title()
        if "p10" in info:
            print(f"    {label:20s}: {info['mean']:>7}  med {info['median']:>7}  p10-p90 {info['p10']}-{info['p90']}")
        else:
            print(f"    {label:20s}: {info['mean']:>7}  med {info['median']:>7}")

    v = agg["velocity"]
    print(f"\n  VELOCITY")
    print(f"    Avg mean velocity:   {v['mean_of_means']}")
    print(f"    Avg velocity stdev:  {v['mean_stdev']}  (p10={v['stdev_p10']}, p90={v['stdev_p90']})")
    print(f"    Avg velocity range:  {v['mean_range']}")

    t = agg["timing_humanization"]
    print(f"\n  TIMING HUMANIZATION")
    print(f"    Mean 16th-grid deviation: {t['mean_16th_deviation']} beats")
    print(f"    Median % notes off grid:  {t['median_pct_off_grid']}%")

    dur = agg["duration"]
    print(f"\n  DURATION")
    print(f"    Mean of means: {dur['mean_of_means']} beats")
    print(f"    Mean stdev:    {dur['mean_stdev']} beats")

    cc = agg.get("cc_breakdown_total", {})
    if cc:
        print(f"\n  CC USAGE (total across all files)")
        for label, count in list(cc.items())[:15]:
            print(f"    {label}: {count}")

    print(f"\n{'=' * 70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze MIDI files for expressiveness metrics")
    parser.add_argument("path", help="MIDI file or directory to analyze")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--summary-only", action="store_true",
                        help="For directories: only print aggregate summary, skip per-file reports")
    args = parser.parse_args()

    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("**/*.mid")) + sorted(target.glob("**/*.midi"))
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
            report = analyze_midi(str(f))
            reports.append(report)
            if not args.json and not args.summary_only and len(files) > 10:
                if i % 50 == 0 or i == len(files):
                    print(f"  ... analyzed {i}/{len(files)} files", file=sys.stderr)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"Error analyzing {f}: {e}", file=sys.stderr)

    if args.json:
        output: dict[str, Any] = {}
        if len(reports) > 1:
            output["aggregate"] = aggregate_reports(reports)
        if not args.summary_only:
            output["files"] = reports
        elif len(reports) == 1:
            output = reports[0]
        else:
            output = output.get("aggregate", {})  # type: ignore[assignment]
        print(json.dumps(output, indent=2))
    else:
        if not args.summary_only:
            for r in reports:
                print_report(r)

        if len(reports) > 1:
            agg = aggregate_reports(reports)
            print_aggregate(agg)

        if errors:
            print(f"  ({errors} files failed to parse)", file=sys.stderr)


if __name__ == "__main__":
    main()
