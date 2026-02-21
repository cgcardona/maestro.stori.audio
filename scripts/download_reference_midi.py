#!/usr/bin/env python3
"""
Download high-quality reference MIDI files for expressiveness analysis.

Downloads from curated public sources:
  - MAESTRO v3 (Google Magenta): 1,276 virtuosic classical piano performances
    with full CC (pedal, expression), extreme velocity dynamics, and humanized
    timing.  CC-BY-NC-SA 4.0.  ~90 MB zipped.

Usage (inside Docker):
    python scripts/download_reference_midi.py
    python scripts/download_reference_midi.py --limit 50   # subset
    python scripts/download_reference_midi.py --dest /data/reference_midi
"""
import argparse
import io
import logging
import os
import random
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MAESTRO_URL = (
    "https://storage.googleapis.com/magentadata/datasets/maestro/v3.0.0/"
    "maestro-v3.0.0-midi.zip"
)

DEFAULT_DEST = "reference_midi/maestro"


def download_maestro(dest: str, limit: int | None = None) -> list[str]:
    """Download MAESTRO v3 MIDI files, return list of saved paths."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    existing = list(dest_path.glob("*.mid")) + list(dest_path.glob("*.midi"))
    if existing:
        logger.info(f"Found {len(existing)} existing MIDI files in {dest}")
        if limit and len(existing) >= limit:
            logger.info(f"Already have >= {limit} files, skipping download")
            return [str(p) for p in sorted(existing)[:limit]]

    logger.info(f"Downloading MAESTRO v3 MIDI archive (~90 MB)...")
    req = Request(MAESTRO_URL, headers={"User-Agent": "StoriMaestro/1.0"})
    response = urlopen(req, timeout=300)
    data = response.read()
    logger.info(f"Downloaded {len(data) / 1024 / 1024:.1f} MB")

    saved: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        midi_names = [n for n in zf.namelist() if n.lower().endswith((".mid", ".midi"))]
        logger.info(f"Archive contains {len(midi_names)} MIDI files")

        if limit and len(midi_names) > limit:
            random.seed(42)
            midi_names = sorted(random.sample(midi_names, limit))
            logger.info(f"Sampling {limit} files (seed=42 for reproducibility)")

        for name in midi_names:
            basename = Path(name).name
            out_path = dest_path / basename
            if out_path.exists():
                saved.append(str(out_path))
                continue
            out_path.write_bytes(zf.read(name))
            saved.append(str(out_path))

    logger.info(f"Saved {len(saved)} MIDI files to {dest}")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download high-quality reference MIDI files"
    )
    parser.add_argument(
        "--dest", default=DEFAULT_DEST, help="Destination directory"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max files to download (random sample, deterministic seed)"
    )
    args = parser.parse_args()

    paths = download_maestro(args.dest, args.limit)
    logger.info(f"\nDone. {len(paths)} MIDI files ready for analysis.")
    logger.info(f"Run:  python scripts/analyze_midi.py {args.dest}")


if __name__ == "__main__":
    main()
