"""Orpheus MVP Test — Prove the HuggingFace Space returns high-quality MIDI.

This script does EXACTLY what the HF Gradio GUI does:
1. Create a fresh Gradio client (fresh session = no accumulated state)
2. Upload a minimal seed MIDI
3. Pick 2 instruments (Piano + Bass, like the user's test)
4. Call /generate_music_and_state (generates 10 batches on GPU)
5. Call /add_batch for ONE batch (fresh session = only this batch's content)
6. Analyze the raw MIDI output with mido

If this produces high-quality output, the issue is in our pipeline.
If this produces poor output, the issue is in how we call Orpheus.
"""
from __future__ import annotations

import os
import sys
import time
import struct
import tempfile
import logging
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orpheus_mvp")


def create_minimal_seed(tempo: int = 120) -> str:
    """Create the absolute minimum valid MIDI file — just a tempo marker."""
    fd, path = tempfile.mkstemp(suffix=".mid")

    # Build a minimal Type 0 MIDI file with just a tempo track
    us_per_beat = int(60_000_000 / tempo)

    # Tempo meta event: FF 51 03 <3 bytes of microseconds per beat>
    tempo_event = bytes([
        0x00,        # delta time
        0xFF, 0x51, 0x03,
        (us_per_beat >> 16) & 0xFF,
        (us_per_beat >> 8) & 0xFF,
        us_per_beat & 0xFF,
    ])

    # End of track: FF 2F 00
    end_event = bytes([0x00, 0xFF, 0x2F, 0x00])

    track_data = tempo_event + end_event
    track_chunk = b"MTrk" + struct.pack(">I", len(track_data)) + track_data
    header = b"MThd" + struct.pack(">IHH", 6, 0, 1) + struct.pack(">H", 480)

    with os.fdopen(fd, "wb") as f:
        f.write(header + track_chunk)

    return path


def analyze_midi(path: str) -> dict[str, Any]:
    """Analyze a MIDI file using mido and print summary."""
    import mido

    mid = mido.MidiFile(path)
    logger.info(f"  MIDI type: {mid.type}, ticks_per_beat: {mid.ticks_per_beat}, "
                f"tracks: {len(mid.tracks)}")

    total_notes = 0
    channels_used = set()
    programs_used = {}
    note_on_velocities = []
    pitches = []

    for i, track in enumerate(mid.tracks):
        track_notes = 0
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0:
                track_notes += 1
                channels_used.add(msg.channel)
                note_on_velocities.append(msg.velocity)
                pitches.append(msg.note)
            elif msg.type == "program_change":
                programs_used[msg.channel] = msg.program

        total_notes += track_notes
        if track_notes > 0:
            logger.info(f"  Track {i}: {track_notes} notes, {len(track)} events")

    logger.info(f"  Total notes: {total_notes}")
    logger.info(f"  Channels used: {sorted(channels_used)}")
    logger.info(f"  Programs: {programs_used}")

    if note_on_velocities:
        avg_vel = sum(note_on_velocities) / len(note_on_velocities)
        min_vel = min(note_on_velocities)
        max_vel = max(note_on_velocities)
        unique_vel = len(set(note_on_velocities))
        logger.info(f"  Velocity: avg={avg_vel:.0f}, min={min_vel}, max={max_vel}, "
                     f"unique={unique_vel}")

    if pitches:
        pitch_range = max(pitches) - min(pitches)
        unique_pitches = len(set(pitches))
        logger.info(f"  Pitch: range={pitch_range} semitones, "
                     f"unique={unique_pitches}, low={min(pitches)}, high={max(pitches)}")

    duration_s = mid.length
    logger.info(f"  Duration: {duration_s:.1f}s")

    return {
        "total_notes": total_notes,
        "channels": sorted(channels_used),
        "programs": programs_used,
        "duration_s": duration_s,
        "unique_velocities": len(set(note_on_velocities)) if note_on_velocities else 0,
        "unique_pitches": len(set(pitches)) if pitches else 0,
    }


def main() -> None:
    from gradio_client import Client, handle_file

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
    space_id = os.environ.get("STORI_ORPHEUS_SPACE", "cgcardona/Orpheus-Music-Transformer")

    instruments = ["Acoustic Grand", "Electric Bass(finger)"]
    num_prime = 6656
    num_gen = 512
    temperature = 0.9
    top_p = 0.96

    logger.info("=" * 60)
    logger.info("ORPHEUS MVP TEST")
    logger.info("=" * 60)
    logger.info(f"Space: {space_id}")
    logger.info(f"Instruments: {instruments}")
    logger.info(f"Prime tokens: {num_prime}, Gen tokens: {num_gen}")
    logger.info(f"Temperature: {temperature}, Top-p: {top_p}")

    # Step 1: Create minimal seed
    seed_path = create_minimal_seed(tempo=120)
    logger.info(f"Seed MIDI: {seed_path} ({os.path.getsize(seed_path)} bytes)")

    # Step 2: Test 3 batches with FRESH clients each time
    results: list[dict[str, Any]] = []
    for batch_idx in [0, 4, 9]:
        logger.info("")
        logger.info(f"--- Batch {batch_idx} (fresh client) ---")

        t0 = time.time()
        client = Client(space_id, hf_token=hf_token)
        t_connect = time.time() - t0
        logger.info(f"  Client connected in {t_connect:.1f}s")

        # Step 3: Generate 10 batches
        t1 = time.time()
        gen_result = client.predict(
            input_midi=handle_file(seed_path),
            prime_instruments=instruments,
            num_prime_tokens=num_prime,
            num_gen_tokens=num_gen,
            model_temperature=temperature,
            model_top_p=top_p,
            add_drums=False,
            api_name="/generate_music_and_state",
        )
        t_gen = time.time() - t1
        logger.info(f"  /generate_music_and_state completed in {t_gen:.1f}s")

        # Step 4: Pick one batch
        t2 = time.time()
        batch_result = client.predict(
            batch_number=batch_idx,
            api_name="/add_batch",
        )
        t_batch = time.time() - t2
        logger.info(f"  /add_batch({batch_idx}) completed in {t_batch:.1f}s")

        # Step 5: Get the MIDI file path
        midi_path = batch_result[2]
        logger.info(f"  MIDI output: {midi_path}")

        if midi_path and os.path.exists(midi_path):
            file_size = os.path.getsize(midi_path)
            logger.info(f"  File size: {file_size} bytes")

            # Copy to a persistent location
            import shutil
            out_path = f"/tmp/orpheus_mvp_batch_{batch_idx}.mid"
            shutil.copy2(midi_path, out_path)
            logger.info(f"  Saved to: {out_path}")

            analysis = analyze_midi(midi_path)
            results.append({
                "batch": batch_idx,
                "analysis": analysis,
                "gen_time": t_gen,
                "total_time": t_connect + t_gen + t_batch,
            })
        else:
            logger.error(f"  No MIDI file returned!")
            results.append({"batch": batch_idx, "error": "No MIDI file"})

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    for r in results:
        if "error" in r:
            logger.info(f"  Batch {r['batch']}: ERROR - {r['error']}")
        else:
            a = r["analysis"]
            logger.info(
                f"  Batch {r['batch']}: {a['total_notes']} notes, "
                f"{a['unique_pitches']} unique pitches, "
                f"{a['unique_velocities']} unique velocities, "
                f"{a['duration_s']:.1f}s, "
                f"channels={a['channels']}, "
                f"gen_time={r['gen_time']:.1f}s"
            )

    # Verify diversity: are the batches different?
    if len(results) >= 2 and all("analysis" in r for r in results):
        note_counts = [r["analysis"]["total_notes"] for r in results]
        all_same = len(set(note_counts)) == 1
        if all_same:
            logger.warning("⚠️ All batches have identical note counts — possible diversity issue!")
        else:
            logger.info("✅ Batches have different note counts — stochastic diversity confirmed!")

    logger.info("")
    logger.info("Done! MIDI files saved to /tmp/orpheus_mvp_batch_*.mid")
    logger.info("Play them in a DAW to verify quality.")


if __name__ == "__main__":
    main()
