#!/usr/bin/env python3
"""
Batch MIDI analysis — sequential, memory-safe, resumable.

Walks reference_midi/<source>/ directories one file at a time.  Each result
is saved as a tiny JSON file immediately, so the process can be killed and
restarted at any point without losing work.

Usage:
    python scripts/batch_analyze.py --midi-dir /data/reference_midi
    python scripts/batch_analyze.py --midi-dir /data/reference_midi --source symphonynet
    python scripts/batch_analyze.py --midi-dir /data/reference_midi --aggregate-only
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def _result_path(results_dir: Path, source: str, midi_stem: str) -> Path:
    return results_dir / source / f"{midi_stem}.json"


def _is_cache_current(result_path: Path) -> bool:
    """Check if a cached result uses the current schema version."""
    from scripts.analyze_midi import SCHEMA_VERSION
    if not result_path.exists():
        return False
    try:
        data = json.loads(result_path.read_text())
        return data.get("schema_version") == SCHEMA_VERSION
    except Exception:
        return False


def analyze_and_save(midi_path: Path, result_path: Path) -> bool:
    """Analyze one MIDI file and save the result. Returns True on success."""
    from scripts.analyze_midi import analyze_midi
    try:
        report = analyze_midi(str(midi_path))
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(report))
        return True
    except Exception as e:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"error": str(e), "file": str(midi_path)}))
        return False


def run_source(midi_dir: Path, results_dir: Path, source: str) -> tuple[int, int, int]:
    """Process all MIDI files for one source. Returns (total, done, errors)."""
    source_dir = midi_dir / source
    if not source_dir.is_dir():
        logger.warning(f"[{source}] Directory not found: {source_dir}")
        return 0, 0, 0

    logger.info(f"[{source}] Scanning {source_dir}...")

    done = 0
    skipped = 0
    errors = 0
    total = 0
    start = time.time()

    # Build file list first (lightweight — just names, no file reads)
    entries = [
        e for e in os.scandir(source_dir)
        if e.is_file() and e.name.lower().endswith((".mid", ".midi"))
    ]
    total = len(entries)
    entries.sort(key=lambda e: e.name)
    logger.info(f"  [{source}] {total} MIDI files found")

    for entry in entries:
        stem = Path(entry.name).stem
        rp = _result_path(results_dir, source, stem)

        if _is_cache_current(rp):
            skipped += 1
            if skipped == 1 or skipped % 10000 == 0:
                logger.info(f"  [{source}] Skipping cached ({skipped} so far)...")
            continue

        ok = analyze_and_save(Path(entry.path), rp)
        if ok:
            done += 1
        else:
            errors += 1

        processed = done + errors
        if processed % 500 == 0 or processed == 1:
            elapsed = time.time() - start
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = total - skipped - processed
            eta = remaining / rate if rate > 0 else 0
            logger.info(
                f"  [{source}] {processed}/{total - skipped} new ({errors} err), "
                f"{skipped} cached "
                f"[{rate:.1f}/sec, ETA {eta / 60:.0f}m]"
            )

    elapsed = time.time() - start
    logger.info(
        f"[{source}] Done: {total} total, {skipped} cached, "
        f"{done} new, {errors} errors in {elapsed:.0f}s"
    )
    return total, done + skipped, errors


def load_cached_reports(results_dir: Path, source: str | None = None) -> list[dict[str, Any]]:
    """Load all successful per-file results from disk."""
    reports: list[dict[str, Any]] = []
    if not results_dir.exists():
        return reports

    for subdir in sorted(results_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if source and subdir.name != source:
            continue
        count = 0
        for rp in subdir.iterdir():
            if not rp.name.endswith(".json"):
                continue
            try:
                data = json.loads(rp.read_text())
                if "error" not in data:
                    reports.append(data)
                    count += 1
            except Exception:
                pass
        logger.info(f"  Loaded {count} results from {subdir.name}")

    return reports


def build_aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts.analyze_midi import aggregate_reports
    return aggregate_reports(reports)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch MIDI analysis — sequential, resumable"
    )
    parser.add_argument(
        "--midi-dir", type=str, required=True,
        help="Base directory containing source subdirs (e.g. /data/reference_midi)",
    )
    parser.add_argument(
        "--source", type=str, default=None,
        help="Only process one source (maestro, symphonynet, musicnet, lakh)",
    )
    parser.add_argument(
        "--aggregate-only", action="store_true",
        help="Skip analysis, only aggregate existing results",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output path for heuristics JSON",
    )
    args = parser.parse_args()

    midi_dir = Path(args.midi_dir)
    results_dir = midi_dir / "_results"
    out_path = Path(args.output) if args.output else midi_dir / "phrasing_heuristics.json"

    if not args.aggregate_only:
        sources = [args.source] if args.source else [
            d.name for d in sorted(midi_dir.iterdir())
            if d.is_dir() and not d.name.startswith("_")
        ]
        for src in sources:
            run_source(midi_dir, results_dir, src)

    # Aggregate
    logger.info(f"\nLoading all cached results for aggregation...")
    reports = load_cached_reports(results_dir, args.source)

    if not reports:
        logger.error("No reports to aggregate.")
        sys.exit(1)

    logger.info(f"Aggregating {len(reports)} reports...")
    agg = build_aggregate(reports)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(agg, indent=2))
    logger.info(f"Heuristics saved to {out_path}")

    # Summary
    logger.info(f"\n{'=' * 60}")
    logger.info(f"  SUMMARY: {agg['file_count']} files")
    logger.info(f"{'=' * 60}")
    roles = agg.get("phrasing_by_role", {})
    for role, data in sorted(roles.items()):
        tc = data.get("track_count", 0)
        rr = data.get("rest_ratio", {}).get("mean", "?")
        ent = data.get("note_length_entropy", {}).get("mean", "?")
        ioi = data.get("ioi_cv", {}).get("mean", "?")
        cc = data.get("contour_complexity", {}).get("mean", "?")
        logger.info(
            f"  {role:10s}: {tc:>6} tracks  |  rest={rr}  entropy={ent}  "
            f"ioi_cv={ioi}  contour={cc}"
        )


if __name__ == "__main__":
    main()
