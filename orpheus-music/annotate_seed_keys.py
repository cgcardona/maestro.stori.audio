#!/usr/bin/env python3
"""One-time script: detect key for every seed in the library and
annotate metadata.json with ``"key": "C major"`` fields.

Run inside the orpheus container:
    docker compose exec orpheus python annotate_seed_keys.py

Idempotent — safe to re-run.  Seeds that already have a ``key`` field
are skipped unless ``--force`` is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from key_detection import detect_key

_LIBRARY_DIR = Path(__file__).parent / "seed_library"
_METADATA_PATH = _LIBRARY_DIR / "metadata.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate seed library with detected keys")
    parser.add_argument("--force", action="store_true", help="Re-detect even if key already set")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing")
    args = parser.parse_args()

    if not _METADATA_PATH.exists():
        print(f"❌ Metadata file not found: {_METADATA_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(_METADATA_PATH) as f:
        metadata = json.load(f)

    genres = metadata.get("genres", {})
    total = 0
    detected = 0
    failed = 0
    skipped = 0

    for genre_key, seeds in genres.items():
        for entry in seeds:
            total += 1

            if not args.force and "key" in entry:
                skipped += 1
                continue

            seed_path = _LIBRARY_DIR / entry["file"]
            if not seed_path.exists():
                print(f"  ⚠️ Missing: {entry['file']}")
                failed += 1
                continue

            result = detect_key(seed_path)
            if result is None:
                print(f"  ⚠️ Could not detect key: {entry['file']}")
                entry["key"] = None
                entry["key_confidence"] = 0.0
                failed += 1
                continue

            tonic, mode, confidence = result
            key_str = f"{tonic} {mode}"
            entry["key"] = key_str
            entry["key_confidence"] = confidence
            detected += 1

            print(
                f"  🎵 {entry['file']:40s} → {key_str:10s} "
                f"(confidence={confidence:.3f})"
            )

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Summary:")
    print(f"  Total seeds:  {total}")
    print(f"  Detected:     {detected}")
    print(f"  Failed:       {failed}")
    print(f"  Skipped:      {skipped}")

    if not args.dry_run and detected > 0:
        tmp = _METADATA_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(metadata, f, indent=2)
        tmp.rename(_METADATA_PATH)
        print(f"  ✅ Updated {_METADATA_PATH}")


if __name__ == "__main__":
    main()
