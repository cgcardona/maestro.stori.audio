#!/usr/bin/env python3
"""
Download high-quality reference MIDI files for phrasing + expressiveness analysis.

Sources (all freely licensed for research):
  - MAESTRO v3 (Google Magenta): 1,276 solo classical piano performances.
    CC-BY-NC-SA 4.0.  ~90 MB zip.
  - SymphonyNet: 46,359 multi-track symphonic MIDI files (strings, woodwinds,
    brass, percussion, keys).  MIT.  ~2 GB zip via Google Drive.
  - MusicNet: 330 chamber music reference MIDI (violin, cello, clarinet,
    flute, horn, oboe, piano).  CC-BY-SA 4.0.  Small tar.gz.
  - Lakh MIDI Dataset (LMD-full): 176,581 multi-genre, multi-instrument MIDI.
    Variable quality — filtered during analysis.  ~1.65 GB tar.gz.

Usage:
    python scripts/download_reference_midi.py --source maestro
    python scripts/download_reference_midi.py --source symphonynet
    python scripts/download_reference_midi.py --source all
    python scripts/download_reference_midi.py --source all --limit 500
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import random
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

BASE_DEST = "reference_midi"

MAESTRO_URL = (
    "https://storage.googleapis.com/magentadata/datasets/maestro/v3.0.0/"
    "maestro-v3.0.0-midi.zip"
)

MUSICNET_URL = "https://zenodo.org/records/5120004/files/musicnet_midis.tar.gz"

LMD_URL = "http://hog.ee.columbia.edu/craffel/lmd/lmd_full.tar.gz"

# SymphonyNet: hosted on Google Drive (large file, needs confirmation bypass)
SYMPHONYNET_GDRIVE_ID = "1j9Pvtzaq8k_QIPs8e2ikvCR-BusPluTb"


def _download_url(url: str, desc: str, timeout: int = 600) -> bytes:
    """Download a URL with progress logging, return raw bytes."""
    logger.info(f"Downloading {desc}...")
    req = Request(url, headers={"User-Agent": "StoriMaestro/1.0"})
    response = urlopen(req, timeout=timeout)
    data = response.read()
    mb = len(data) / 1024 / 1024
    logger.info(f"Downloaded {mb:.1f} MB")
    return data


def _download_gdrive(file_id: str, dest_dir: str, desc: str) -> str:
    """Download a large file from Google Drive using gdown.

    Returns the path to the downloaded file on disk (temp location).
    """
    import gdown

    logger.info(f"Downloading {desc} from Google Drive (via gdown)...")
    url = f"https://drive.google.com/uc?id={file_id}"
    tmp_path = os.path.join(dest_dir, "_download_tmp.zip")
    gdown.download(url, tmp_path, quiet=False, fuzzy=True)

    size = os.path.getsize(tmp_path)
    logger.info(f"Downloaded {size / 1024 / 1024:.1f} MB")
    return tmp_path


def _extract_midi_from_zip(data: bytes, dest: Path, limit: int | None = None) -> list[str]:
    """Extract .mid/.midi files from a zip archive."""
    saved: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        midi_names = [n for n in zf.namelist()
                      if n.lower().endswith((".mid", ".midi")) and not n.startswith("__MACOSX")]
        logger.info(f"Archive contains {len(midi_names)} MIDI files")

        if limit and len(midi_names) > limit:
            random.seed(42)
            midi_names = sorted(random.sample(midi_names, limit))
            logger.info(f"Sampling {limit} files (seed=42)")

        for i, name in enumerate(midi_names, 1):
            basename = Path(name).name
            if not basename:
                continue
            out_path = dest / basename
            if out_path.exists():
                saved.append(str(out_path))
                continue
            out_path.write_bytes(zf.read(name))
            saved.append(str(out_path))
            if i % 5000 == 0:
                logger.info(f"  ... extracted {i}/{len(midi_names)} files")

    return saved


def _extract_midi_from_tar(data: bytes, dest: Path, limit: int | None = None) -> list[str]:
    """Extract .mid/.midi files from a tar/tar.gz archive."""
    saved: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        midi_members = [m for m in tf.getmembers()
                        if m.isfile() and m.name.lower().endswith((".mid", ".midi"))]
        logger.info(f"Archive contains {len(midi_members)} MIDI files")

        if limit and len(midi_members) > limit:
            random.seed(42)
            midi_members = sorted(random.sample(midi_members, limit), key=lambda m: m.name)
            logger.info(f"Sampling {limit} files (seed=42)")

        for i, member in enumerate(midi_members, 1):
            basename = Path(member.name).name
            if not basename:
                continue
            out_path = dest / basename
            if out_path.exists():
                saved.append(str(out_path))
                continue
            f = tf.extractfile(member)
            if f:
                out_path.write_bytes(f.read())
                saved.append(str(out_path))
            if i % 5000 == 0:
                logger.info(f"  ... extracted {i}/{len(midi_members)} files")

    return saved


def _extract_midi_from_tar_file(tar_path: str, dest: Path,
                                limit: int | None = None) -> list[str]:
    """Extract .mid/.midi files from a tar/tar.gz file on disk."""
    saved: list[str] = []
    with tarfile.open(tar_path, mode="r:*") as tf:
        midi_members = [m for m in tf.getmembers()
                        if m.isfile() and m.name.lower().endswith((".mid", ".midi"))]
        logger.info(f"Archive contains {len(midi_members)} MIDI files")

        if limit and len(midi_members) > limit:
            random.seed(42)
            midi_members = sorted(random.sample(midi_members, limit), key=lambda m: m.name)
            logger.info(f"Sampling {limit} files (seed=42)")

        for i, member in enumerate(midi_members, 1):
            basename = Path(member.name).name
            if not basename:
                continue
            out_path = dest / basename
            if out_path.exists():
                saved.append(str(out_path))
                continue
            f = tf.extractfile(member)
            if f:
                out_path.write_bytes(f.read())
                saved.append(str(out_path))
            if i % 5000 == 0:
                logger.info(f"  ... extracted {i}/{len(midi_members)} files")

    return saved


def _stream_download_and_extract_tar(url: str, dest: Path, desc: str,
                                     limit: int | None = None,
                                     timeout: int = 1800) -> list[str]:
    """Stream-download a large tar.gz and extract MIDI files without buffering
    the entire archive in memory. Used for multi-GB downloads (Lakh)."""
    dest.mkdir(parents=True, exist_ok=True)
    logger.info(f"Stream-downloading {desc}...")

    req = Request(url, headers={"User-Agent": "StoriMaestro/1.0"})
    response = urlopen(req, timeout=timeout)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    try:
        downloaded = 0
        while True:
            chunk = response.read(8 * 1024 * 1024)  # 8 MB chunks
            if not chunk:
                break
            tmp.write(chunk)
            downloaded += len(chunk)
            mb = downloaded / 1024 / 1024
            if downloaded % (100 * 1024 * 1024) < 8 * 1024 * 1024:
                logger.info(f"  ... downloaded {mb:.0f} MB")
        tmp.close()
        logger.info(f"Download complete: {downloaded / 1024 / 1024:.0f} MB")

        saved: list[str] = []
        with tarfile.open(tmp.name, mode="r:gz") as tf:
            midi_members = [m for m in tf.getmembers()
                            if m.isfile() and m.name.lower().endswith((".mid", ".midi"))]
            logger.info(f"Archive contains {len(midi_members)} MIDI files")

            if limit and len(midi_members) > limit:
                random.seed(42)
                midi_members = sorted(random.sample(midi_members, limit), key=lambda m: m.name)
                logger.info(f"Sampling {limit} files (seed=42)")

            for i, member in enumerate(midi_members, 1):
                basename = Path(member.name).name
                if not basename:
                    continue
                out_path = dest / basename
                if out_path.exists():
                    saved.append(str(out_path))
                    continue
                f = tf.extractfile(member)
                if f:
                    out_path.write_bytes(f.read())
                    saved.append(str(out_path))
                if i % 10000 == 0:
                    logger.info(f"  ... extracted {i}/{len(midi_members)} files")

        return saved
    finally:
        os.unlink(tmp.name)


# ─── Source-specific downloaders ───────────────────────────────────────────

def download_maestro(dest: str, limit: int | None = None) -> list[str]:
    """MAESTRO v3 — 1,276 classical piano performances."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    existing = list(dest_path.glob("*.mid")) + list(dest_path.glob("*.midi"))
    if existing:
        logger.info(f"[maestro] Found {len(existing)} existing files in {dest}")
        if limit and len(existing) >= limit:
            return [str(p) for p in sorted(existing)[:limit]]

    data = _download_url(MAESTRO_URL, "MAESTRO v3 (~90 MB)")
    saved = _extract_midi_from_zip(data, dest_path, limit)
    logger.info(f"[maestro] {len(saved)} MIDI files saved to {dest}")
    return saved


def download_symphonynet(dest: str, limit: int | None = None) -> list[str]:
    """SymphonyNet — 46,359 orchestral symphonic MIDI files."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    existing = list(dest_path.glob("*.mid")) + list(dest_path.glob("*.midi"))
    if existing:
        logger.info(f"[symphonynet] Found {len(existing)} existing files in {dest}")
        if limit and len(existing) >= limit:
            return [str(p) for p in sorted(existing)[:limit]]

    tmp_path = _download_gdrive(SYMPHONYNET_GDRIVE_ID, str(dest_path), "SymphonyNet (~436 MB)")
    try:
        # SymphonyNet is distributed as .tar.gz despite the Drive filename
        saved = _extract_midi_from_tar_file(tmp_path, dest_path, limit)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    logger.info(f"[symphonynet] {len(saved)} MIDI files saved to {dest}")
    return saved


def download_musicnet(dest: str, limit: int | None = None) -> list[str]:
    """MusicNet — 330 chamber music reference MIDI files."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    existing = list(dest_path.glob("*.mid")) + list(dest_path.glob("*.midi"))
    if existing:
        logger.info(f"[musicnet] Found {len(existing)} existing files in {dest}")
        if limit and len(existing) >= limit:
            return [str(p) for p in sorted(existing)[:limit]]

    data = _download_url(MUSICNET_URL, "MusicNet MIDI (~small)")
    saved = _extract_midi_from_tar(data, dest_path, limit)
    logger.info(f"[musicnet] {len(saved)} MIDI files saved to {dest}")
    return saved


def download_lakh(dest: str, limit: int | None = None) -> list[str]:
    """Lakh MIDI Dataset — 176,581 multi-genre, multi-instrument MIDI files.

    Streams to a temp file to avoid holding 1.65 GB in memory.
    """
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    existing = list(dest_path.glob("*.mid")) + list(dest_path.glob("*.midi"))
    if existing:
        logger.info(f"[lakh] Found {len(existing)} existing files in {dest}")
        if limit and len(existing) >= limit:
            return [str(p) for p in sorted(existing)[:limit]]

    saved = _stream_download_and_extract_tar(
        LMD_URL, dest_path, "Lakh MIDI Dataset (~1.65 GB)", limit, timeout=1800
    )
    logger.info(f"[lakh] {len(saved)} MIDI files saved to {dest}")
    return saved


# ─── Registry ─────────────────────────────────────────────────────────────

SOURCES = {
    "maestro": {
        "fn": download_maestro,
        "dest": f"{BASE_DEST}/maestro",
        "desc": "MAESTRO v3 — 1,276 classical piano performances",
    },
    "symphonynet": {
        "fn": download_symphonynet,
        "dest": f"{BASE_DEST}/symphonynet",
        "desc": "SymphonyNet — 46,359 orchestral symphonic MIDI files",
    },
    "musicnet": {
        "fn": download_musicnet,
        "dest": f"{BASE_DEST}/musicnet",
        "desc": "MusicNet — 330 chamber music MIDI files",
    },
    "lakh": {
        "fn": download_lakh,
        "dest": f"{BASE_DEST}/lakh",
        "desc": "Lakh MIDI Dataset — 176,581 multi-genre MIDI files",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download reference MIDI datasets for phrasing analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {k:14s} {v['desc']}" for k, v in SOURCES.items()),
    )
    parser.add_argument(
        "--source", default="all",
        choices=list(SOURCES.keys()) + ["all"],
        help="Which dataset to download (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max files per source (random sample, deterministic seed)",
    )
    parser.add_argument(
        "--dest", default=None,
        help=f"Override base destination (default: {BASE_DEST}/<source>)",
    )
    args = parser.parse_args()

    targets = list(SOURCES.keys()) if args.source == "all" else [args.source]
    total = 0

    for name in targets:
        src = SOURCES[name]
        dest = args.dest or src["dest"]
        logger.info(f"\n{'=' * 60}")
        logger.info(f"  {src['desc']}")
        logger.info(f"{'=' * 60}")
        try:
            paths = src["fn"](dest, args.limit)
            total += len(paths)
        except Exception as e:
            logger.error(f"[{name}] Failed: {e}")
            logger.info("Continuing with next source...")

    logger.info(f"\nDone. {total} total MIDI files downloaded.")
    logger.info(f"Run analysis:  python scripts/analyze_midi.py {BASE_DEST} --json --summary-only")


if __name__ == "__main__":
    main()
