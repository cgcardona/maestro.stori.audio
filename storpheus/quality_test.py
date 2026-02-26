"""Direct Orpheus quality test — iterative continuation on paid A100 Space.

Each iteration:
1. Fresh client (avoids stale gr.State issues)
2. Feed previous output MIDI as seed (grows the composition)
3. Generate → add batch 0 → save MIDI + audio
4. Use that output MIDI as seed for next iteration

This mirrors the Auto-Continuations notebook workflow.
"""
from __future__ import annotations

import os
import shutil
import time

from gradio_client import Client, handle_file

OUT = "/data/cache/quality_test"
os.makedirs(OUT, exist_ok=True)

SPACE = os.environ.get("STORI_STORPHEUS_SPACE", "cgcardona/Orpheus-Music-Transformer")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

SEED = "/app/seed_library/seeds/neo_soul/neo_soul_00.mid"
NUM_ITERATIONS = 5
GEN_TOKENS = 512
TEMPERATURE = 0.9
TOP_P = 0.96

print(f"Space: {SPACE}")
print(f"HF_TOKEN: {HF_TOKEN[:10]}..." if HF_TOKEN else "HF_TOKEN: (not set)")
print(f"Seed: {SEED} ({os.path.getsize(SEED)} bytes)")
print(f"Iterations: {NUM_ITERATIONS}, gen_tokens: {GEN_TOKENS}")
print(f"Temperature: {TEMPERATURE}, top_p: {TOP_P}")
print()

current_seed = SEED
total_start = time.time()

for i in range(1, NUM_ITERATIONS + 1):
    print(f"[Iter {i}/{NUM_ITERATIONS}] Seed: {os.path.basename(current_seed)} "
          f"({os.path.getsize(current_seed)} bytes)")

    client = Client(SPACE, hf_token=HF_TOKEN)

    t0 = time.time()
    result = client.predict(
        input_midi=handle_file(current_seed),
        apply_sustains=True,
        remove_duplicate_pitches=True,
        remove_overlapping_durations=True,
        prime_instruments=[],
        num_prime_tokens=6656,
        num_gen_tokens=GEN_TOKENS,
        model_temperature=TEMPERATURE,
        model_top_p=TOP_P,
        add_drums=False,
        add_outro=False,
        api_name="/generate_music_and_state",
    )
    gen_time = time.time() - t0
    print(f"  Generated in {gen_time:.1f}s")

    batch = client.predict(batch_number=0, api_name="/add_batch")
    midi_path = batch[2]
    audio_path = batch[0]

    midi_size = os.path.getsize(str(midi_path)) if midi_path else 0
    print(f"  Batch 0 added: MIDI={midi_size} bytes")

    iter_mid = os.path.join(OUT, f"iter_{i}.mid")
    if midi_path:
        shutil.copy2(str(midi_path), iter_mid)
        current_seed = iter_mid
    if isinstance(audio_path, str) and os.path.exists(audio_path):
        shutil.copy2(audio_path, os.path.join(OUT, f"iter_{i}.mp3"))

    print()

total_time = time.time() - total_start

# Save final iteration as the composition
final_mid = os.path.join(OUT, f"iter_{NUM_ITERATIONS}.mid")
final_mp3 = os.path.join(OUT, f"iter_{NUM_ITERATIONS}.mp3")
if os.path.exists(final_mid):
    shutil.copy2(final_mid, os.path.join(OUT, "composition.mid"))
if os.path.exists(final_mp3):
    shutil.copy2(final_mp3, os.path.join(OUT, "composition.mp3"))

print(f"=== DONE in {total_time:.0f}s ===")
for f in sorted(os.listdir(OUT)):
    size = os.path.getsize(os.path.join(OUT, f))
    print(f"  {f}: {size // 1024}KB")
