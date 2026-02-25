#!/usr/bin/env python3
"""
Offline script: download Orpheus 230K Loops dataset, classify, score, and
export the best seeds per genre as MIDI files + metadata index.

Usage:
    python build_seed_library.py [--output-dir seed_library] [--top-n 10]

Requires ~2-4 GB RAM to load the pickle.  Writes MIDI files and a
metadata.json index to the output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import struct
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Genre classification heuristics
# ---------------------------------------------------------------------------

GENRE_KEYWORDS: dict[str, list[str]] = {
    # ── Electronic ──
    "drum_and_bass": ["drum and bass", "dnb", "d&b", "jungle", "liquid"],
    "house": ["house", "deep house", "tech house", "progressive house", "garage", "2-step"],
    "techno": ["techno", "industrial", "minimal techno", "melodic techno"],
    "dubstep": ["dubstep", "riddim", "brostep"],
    "trance": ["trance", "psytrance", "psy trance", "goa"],
    "synthwave": ["synthwave", "retrowave", "vaporwave", "outrun"],
    "ambient": ["ambient", "drone", "atmospheric", "meditation", "zen"],
    "lofi": ["lofi", "lo-fi", "chillhop"],
    "minimalist": ["minimalist", "phasing", "process music", "steve reich"],
    # ── Hip-hop / Trap ──
    "trap": ["trap", "808", "dark trap"],
    "hip_hop": ["hip hop", "hip-hop", "boom bap", "rap"],
    "drill": ["drill", "uk drill"],
    # ── Jazz / Soul / R&B ──
    "jazz": ["jazz", "bebop", "swing", "fusion"],
    "bossa_nova": ["bossa nova", "bossa", "brazilian jazz"],
    "neo_soul": ["neo soul", "neo-soul", "r&b", "rnb", "soul", "gospel"],
    "funk": ["funk", "disco", "motown"],
    "blues": ["blues", "delta blues"],
    # ── Classical / Orchestral ──
    "classical": ["classical", "piano sonata", "concerto", "symphony", "baroque",
                   "romantic", "chamber", "string quartet"],
    "cinematic": ["cinematic", "film", "soundtrack", "orchestral", "epic",
                   "through-composed", "score"],
    # ── Rock / Post-rock ──
    "rock": ["rock", "metal", "punk", "grunge", "indie rock", "post-rock",
             "progressive rock", "prog rock", "prog"],
    "pop": ["pop", "synth pop", "electropop", "indie pop"],
    # ── Latin / Caribbean ──
    "reggaeton": ["reggaeton", "dembow", "perreo"],
    "bossa_cumbia": ["cumbia", "colombian"],
    "tango": ["tango", "tango nuevo", "milonga"],
    "soca": ["soca", "calypso", "carnival"],
    "afro_cuban": ["afro-cuban", "rumba", "son cubano", "montuno", "salsa"],
    "reggae": ["reggae", "ska", "dub", "dancehall"],
    "andean": ["andean", "huayno", "charango"],
    # ── African ──
    "afrobeats": ["afrobeats", "afropop", "afro pop", "highlife"],
    "west_african": ["west african", "polyrhythm", "djembe", "dundun", "griot"],
    "ethio_jazz": ["ethio-jazz", "ethio jazz", "ethiopian", "éthiopiques"],
    "gnawa": ["gnawa", "gnaoua", "guembri"],
    # ── Middle Eastern / South Asian / Sufi ──
    "arabic_maqam": ["arabic", "maqam", "hijaz", "oud", "ney", "qanun"],
    "hindustani": ["hindustani", "raga", "sitar", "tabla", "tanpura", "carnatic"],
    "qawwali": ["qawwali", "sufi", "nusrat", "devotional"],
    "flamenco": ["flamenco", "bulería", "seguiriya", "spanish guitar"],
    "klezmer": ["klezmer", "yiddish", "freylekhs", "doina"],
    # ── East Asian / Southeast Asian / Oceanian ──
    "gamelan": ["gamelan", "balinese", "javanese", "pelog", "slendro", "kotekan"],
    "japanese": ["japanese", "zen", "shakuhachi", "koto", "taiko", "gagaku"],
    "korean": ["korean", "sanjo", "pansori", "gayageum", "janggu"],
    "polynesian": ["polynesian", "taiko fusion", "haka", "pacific", "oceanic"],
    # ── European folk / world ──
    "nordic": ["nordic", "scandinavian", "nyckelharpa", "hardingfele"],
    "balkan": ["balkan", "cocek", "čoček", "romani", "brass band"],
    "anatolian": ["anatolian", "turkish", "bağlama", "psych rock"],
    "gregorian": ["gregorian", "chant", "plainchant", "monastic", "organum"],
    "celtic": ["celtic", "irish", "scottish", "fiddle tune", "jig", "reel"],
    # ── Folk / Country / Americana ──
    "country": ["country", "bluegrass", "folk", "americana", "banjo"],
    "new_orleans": ["new orleans", "second line", "brass band", "dixieland"],
    "indie_folk": ["indie folk", "singer-songwriter", "acoustic"],
}

# Fallback genre for unclassified loops
FALLBACK_GENRE = "general"


def classify_genre(title: str, artist: str) -> str:
    """Best-effort genre from title/artist strings."""
    text = f"{title} {artist}".lower()
    for genre, keywords in GENRE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return genre
    return FALLBACK_GENRE


# ---------------------------------------------------------------------------
# Token-sequence analysis (lightweight quality proxy)
# ---------------------------------------------------------------------------

def score_token_sequence(tokens: list[int]) -> float:
    """Score a token sequence for seed quality (0-1).

    Higher is better.  We reward:
    - Length (more context for the model)
    - Diversity of token values (harmonic richness)
    - Moderate repetition (musical structure, not noise)
    """
    n = len(tokens)
    if n < 30:
        return 0.0

    length_score = min(n / 2000.0, 1.0)

    unique = len(set(tokens))
    diversity = min(unique / 200.0, 1.0)

    # Repetition ratio: count how many 3-gram patterns repeat
    trigrams: dict[tuple[int, ...], int] = defaultdict(int)
    for i in range(len(tokens) - 2):
        trigrams[(tokens[i], tokens[i + 1], tokens[i + 2])] += 1
    repeated = sum(1 for c in trigrams.values() if c > 1)
    total_trigrams = max(len(trigrams), 1)
    repetition = repeated / total_trigrams
    structure_score = min(repetition * 2.0, 1.0)

    return 0.4 * length_score + 0.35 * diversity + 0.25 * structure_score


# ---------------------------------------------------------------------------
# MIDI export from TMIDIX token sequences
# ---------------------------------------------------------------------------

def tokens_to_midi_bytes(tokens: list[int], tempo: int = 120) -> bytes:
    """Convert an Orpheus TMIDIX token sequence to a Standard MIDI file.

    Uses the exact same decoding as the HF Space's ``save_midi()`` in app.py:

    - Tokens   0-255:   time delta (value * 16 ms)
    - Tokens 256-16767: patch/pitch — patch=(tok-256)//128, pitch=(tok-256)%128
    - Tokens 16768-18815: dur/vel — dur=((tok-16768)//8)*16, vel=(((tok-16768)%8)+1)*15
    - Token  18816: SOS (ignored)
    - Token  18817: Outro (ignored)
    - Token  18818: EOS (ignored)
    """
    ticks_per_beat = 480
    microseconds_per_beat = int(60_000_000 / tempo)
    ms_to_ticks = ticks_per_beat / (microseconds_per_beat / 1000)

    time_ms = 0
    dur_ms = 16
    vel = 90
    pitch = 60
    channel = 0
    patch = 0

    patches: list[int] = [-1] * 16
    channels: list[int] = [0] * 16
    channels[9] = 1  # channel 9 reserved for drums

    events: list[tuple[int, int, int, int, int, int]] = []  # (tick, ch, note, vel, dur_ticks, patch)
    program_changes: list[tuple[int, int, int]] = []  # (tick, ch, program)

    for tok in tokens:
        if tok == 18816 or tok == 18817 or tok == 18818:
            continue

        if 0 <= tok < 256:
            time_ms += tok * 16

        elif 256 <= tok < 16768:
            patch = (tok - 256) // 128
            pitch = (tok - 256) % 128

            if patch < 128:
                if patch not in patches:
                    if 0 in channels:
                        cha = channels.index(0)
                        channels[cha] = 1
                    else:
                        cha = 15
                    patches[cha] = patch
                    program_changes.append(
                        (int(time_ms * ms_to_ticks), cha, patch)
                    )
                channel = patches.index(patch)
            elif patch == 128:
                channel = 9

        elif 16768 <= tok < 18816:
            dur_ms = ((tok - 16768) // 8) * 16
            vel = (((tok - 16768) % 8) + 1) * 15

            tick = int(time_ms * ms_to_ticks)
            dur_ticks = max(1, int(dur_ms * ms_to_ticks))
            events.append((tick, channel, pitch, vel, dur_ticks, patch))

    if not events:
        events = [(0, 0, 60, 80, 240, 0)]

    def var_len(val: int) -> bytes:
        result = []
        result.append(val & 0x7F)
        val >>= 7
        while val:
            result.append((val & 0x7F) | 0x80)
            val >>= 7
        return bytes(reversed(result))

    track_data = bytearray()
    # Tempo meta event
    track_data += b"\x00\xFF\x51\x03"
    track_data += struct.pack(">I", microseconds_per_beat)[1:]

    # Build all MIDI events (program changes + note on/off)
    midi_events: list[tuple[int, bytes]] = []

    for tick, ch, prog in program_changes:
        midi_events.append((tick, bytes([0xC0 | ch, prog])))

    for tick, ch, note, velocity, dur_ticks, _patch in events:
        note = max(0, min(127, note))
        velocity = max(1, min(127, velocity))
        midi_events.append((tick, bytes([0x90 | ch, note, velocity])))
        midi_events.append((tick + dur_ticks, bytes([0x80 | ch, note, 0])))

    midi_events.sort(key=lambda e: e[0])

    prev_tick = 0
    for abs_tick, data in midi_events:
        delta = abs_tick - prev_tick
        track_data += var_len(delta)
        track_data += data
        prev_tick = abs_tick

    # End of track
    track_data += b"\x00\xFF\x2F\x00"

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ticks_per_beat)
    track_chunk = b"MTrk" + struct.pack(">I", len(track_data)) + bytes(track_data)

    return header + track_chunk


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build Orpheus seed library from 230K loops dataset")
    parser.add_argument("--output-dir", default="seed_library", help="Output directory for seeds")
    parser.add_argument("--top-n", type=int, default=10, help="Number of top seeds per genre")
    parser.add_argument("--pickle-path", default=None,
                        help="Path to pre-downloaded pickle (skips HF download)")
    args = parser.parse_args()

    out = Path(args.output_dir)
    seeds_dir = out / "seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)

    # ── Download or locate the pickle ──
    if args.pickle_path:
        pkl_path = Path(args.pickle_path)
    else:
        logger.info("📥 Downloading Orpheus Loops Dataset from HuggingFace Hub...")
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            logger.error("Install huggingface_hub: pip install huggingface_hub")
            sys.exit(1)

        pkl_path = Path(hf_hub_download(
            repo_id="asigalov61/Orpheus-Music-Transformer",
            filename="orpheus_data/230414_Select_Orpheus_MIDI_Loops_Dataset_CC_BY_NC_SA.pickle",
            repo_type="model",
        ))
    logger.info(f"📂 Loading pickle from {pkl_path} ...")

    with open(pkl_path, "rb") as f:
        dataset = pickle.load(f)
    logger.info(f"✅ Loaded {len(dataset)} loops")

    # ── Classify and score ──
    genre_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, entry in enumerate(dataset):
        if len(entry) < 3:
            continue
        title, artist, tokens = entry[0], entry[1], entry[2]
        if not isinstance(tokens, (list, tuple)) or len(tokens) < 30:
            continue

        genre = classify_genre(str(title), str(artist))
        score = score_token_sequence(list(tokens))

        genre_buckets[genre].append({
            "index": idx,
            "title": str(title),
            "artist": str(artist),
            "token_count": len(tokens),
            "score": round(score, 4),
            "tokens": list(tokens),
        })

        if (idx + 1) % 50000 == 0:
            logger.info(f"   Processed {idx + 1}/{len(dataset)}...")

    logger.info(f"📊 Classified into {len(genre_buckets)} genres")
    for g, items in sorted(genre_buckets.items(), key=lambda x: -len(x[1])):
        logger.info(f"   {g}: {len(items)} loops")

    # ── Select top-N per genre and export MIDI ──
    metadata: dict[str, list[dict[str, Any]]] = {}

    for genre, items in genre_buckets.items():
        items.sort(key=lambda x: x["score"], reverse=True)
        top = items[:args.top_n]

        genre_dir = seeds_dir / genre
        genre_dir.mkdir(parents=True, exist_ok=True)

        genre_meta: list[dict[str, Any]] = []
        for rank, item in enumerate(top):
            midi_bytes = tokens_to_midi_bytes(item["tokens"])
            slug = f"{genre}_{rank:02d}"
            midi_path = genre_dir / f"{slug}.mid"
            midi_path.write_bytes(midi_bytes)

            entry_meta = {
                "file": str(midi_path.relative_to(out)),
                "genre": genre,
                "rank": rank,
                "title": item["title"],
                "artist": item["artist"],
                "token_count": item["token_count"],
                "score": item["score"],
                "sha256": hashlib.sha256(midi_bytes).hexdigest()[:16],
            }
            genre_meta.append(entry_meta)
            logger.info(f"   ✅ {slug}: score={item['score']:.4f}, {item['token_count']} tokens — {item['title']}")

        metadata[genre] = genre_meta

    # ── Write metadata index ──
    index_path = out / "metadata.json"
    with open(index_path, "w") as f:
        json.dump({
            "version": "1.0",
            "source": "asigalov61/Orpheus-Music-Transformer (230K Loops Dataset)",
            "total_genres": len(metadata),
            "seeds_per_genre": args.top_n,
            "genres": metadata,
        }, f, indent=2)

    logger.info(f"\n🎉 Seed library built: {index_path}")
    logger.info(f"   {sum(len(v) for v in metadata.values())} seed files across {len(metadata)} genres")


if __name__ == "__main__":
    main()
