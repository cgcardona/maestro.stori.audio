# Orpheus Music Transformer — Comprehensive Diagnostic Handoff

> **Purpose**: We are building a music composition system (Stori Maestro) that
> calls the Orpheus Music Transformer via its Gradio API to generate MIDI music.
> Our output sounds like "a random cat walking across a keyboard" while the
> exact same model produces beautiful multi-instrument compositions when used
> directly through its Hugging Face Gradio UI. We need outside eyes to find
> what we're doing wrong.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [The Orpheus Model — What It Is](#2-the-orpheus-model)
3. [Token Encoding Scheme (CRITICAL)](#3-token-encoding-scheme)
4. [The HF Space Code — What It Does](#4-the-hf-space-code)
5. [What Happens When You Click "Generate" on the UI](#5-ui-generate-flow)
6. [Our Code — What We Do Differently](#6-our-code)
7. [ROOT CAUSE FOUND: Corrupted Seed Library](#7-root-cause)
8. [Secondary Issues](#8-secondary-issues)
9. [Evidence & Logs](#9-evidence)
10. [Open Questions](#10-open-questions)
11. [What We Need Help With](#11-help-needed)

---

## 1. System Architecture

```
User (Stori DAW, macOS app)
  → Maestro (Python FastAPI, orchestrator, SSE streaming)
    → Our Orpheus Service (orpheus-music/music_service.py, FastAPI, port 10002)
      → gradio_client Python library
        → HF Space Gradio API (cgcardona/Orpheus-Music-Transformer, A100 GPU)
          → Orpheus Music Transformer model (479M params, inference)
```

We do NOT run the model locally. Every generation request proxies through the
Gradio API to a paid HF Space running on an Nvidia A100 (80GB VRAM).

The HF Space is a duplicate of `asigalov61/Orpheus-Music-Transformer` — the
original creator's Space. Same code, same model weights, same SoundFont.

---

## 2. The Orpheus Model

- **Architecture**: 479M-parameter autoregressive transformer with RoPE and
  Flash Attention
- **Sequence Length**: Up to 8192 tokens
- **Training Data**: 2.31M+ high-quality MIDIs from the Godzilla MIDI Dataset,
  trained for 4 full epochs
- **Checkpoint**: `Orpheus_Music_Transformer_Large_Trained_Model_29816_steps_0.7765_loss_0.7615_acc.pth`
- **Inference**: bfloat16 precision, Flash Attention, compiled with `torch.compile()`
- **Generation**: Top-p sampling with configurable temperature
- **Special Tokens**:
  - `18816` = SOS (Start of Sequence)
  - `18817` = Outro (helps generate natural endings)
  - `18818` = EOS (End of Sequence, used as `eos_token` in generation)
  - `18819` = PAD

---

## 3. Token Encoding Scheme (CRITICAL)

The model uses a compact 3-token-per-note encoding. Understanding this is
essential because **we got it wrong in our seed generation code**.

### Encoding (MIDI → Tokens) — from `load_midi()` in app.py

```python
melody_chords = [18816]  # Always starts with SOS

for chord in dcscore:
    delta_time = chord[0][0]
    melody_chords.append(delta_time)  # Token range: 0-255

    for note in chord:
        dur = max(1, min(255, note[1]))        # Duration: 1-255
        pat = max(0, min(128, note[5]))        # Patch: 0-128 (128=drums)
        ptc = max(1, min(127, note[3]))        # Pitch: 1-127
        vel = max(8, min(127, note[4]))        # Velocity → octo-velocity
        velocity = round(vel / 15) - 1         # 0-7

        pat_ptc = (128 * pat) + ptc             # Patch-pitch composite
        dur_vel = (8 * dur) + velocity          # Duration-velocity composite

        melody_chords.extend([
            pat_ptc + 256,      # Token range: 256-16767
            dur_vel + 16768     # Token range: 16768-18815
        ])
```

### Token Ranges Summary

| Range       | Meaning              | Count  |
|-------------|----------------------|--------|
| 0-255       | Time delta (×16ms)   | 256    |
| 256-16767   | Patch/Pitch note     | 16,512 |
| 16768-18815 | Duration/Velocity    | 2,048  |
| 18816       | SOS                  | 1      |
| 18817       | Outro                | 1      |
| 18818       | EOS                  | 1      |
| 18819       | PAD                  | 1      |

### Decoding (Tokens → MIDI) — from `save_midi()` in app.py

```python
for token in tokens:
    if 0 <= token < 256:
        time += token * 16                        # Time delta in ms

    if 256 <= token < 16768:
        patch = (token - 256) // 128              # GM program (0-127) or 128=drums
        pitch = (token - 256) % 128               # MIDI pitch (0-127)
        # Channel assignment: drums → ch9, others → first available channel

    if 16768 <= token < 18816:
        dur = ((token - 16768) // 8) * 16         # Duration in ms
        vel = (((token - 16768) % 8) + 1) * 15    # Velocity (15-120, 8 levels)
        # This token completes a note → write it
```

### Key Properties

- **3 tokens per note**: time_delta, patch_pitch, dur_vel
- **7 tokens per tri-chord**: 1 time_delta + 3×(patch_pitch + dur_vel)
- **Duration-and-velocity-last ordering**: the model sees what note/instrument
  first, then decides how long/loud
- **128 GM instruments + drums**: patch 0-127 = standard GM, patch 128 = drums
- **Octo-velocity**: 8 velocity levels (not full 127), quantized
- **Time in 16ms steps**: max delta = 255×16 = 4080ms

---

## 4. The HF Space Code — What It Does

### Full `generate_music_and_state()` function (simplified)

```python
def generate_music_and_state(
    input_midi,                    # Uploaded MIDI file (or None)
    apply_sustains,                # True
    remove_duplicate_pitches,      # True
    remove_overlapping_durations,  # True
    prime_instruments,             # List of GM instrument names (e.g. ["Shakuhachi"])
    num_prime_tokens,              # 6656 (max context window)
    num_gen_tokens,                # 512 (tokens to generate)
    model_temperature,             # 0.9
    model_top_p,                   # 0.96
    add_drums,                     # False
    add_outro,                     # False
    final_composition,             # gr.State([]) — persists across calls
    generated_batches,             # gr.State([]) — persists across calls
    block_lines,                   # gr.State([]) — persists across calls
):
    # CASE 1: First call with a MIDI file uploaded
    if not final_composition and input_midi is not None:
        final_composition = load_midi(input_midi, ...)
        # Truncate if needed
        if num_prime_tokens < 6656:
            final_composition = final_composition[:num_prime_tokens]

    # CASE 2: First call with NO MIDI (just click Generate)
    if not final_composition and input_midi is None:
        final_composition = [18816, 0]  # SOS + time=0
        # Add one note per selected instrument:
        for i, instr in enumerate(prime_instruments):
            instr_num = patch2number[instr]  # GM program number
            # Create a single note: pitch descends by octave per instrument
            patch_pitch_token = (128 * instr_num) + (72 - (i * 12)) + 256
            dur_vel_token = (8 * 16) + 5 + 16768  # dur=16(×16ms=256ms), vel=5(→90)
            final_composition.extend([patch_pitch_token, dur_vel_token])

    # CASE 3: Subsequent calls (final_composition already has data from
    #          previous generate + add_batch cycles)
    # → falls through, uses existing final_composition as context

    # Optionally add outro/drums tokens
    if add_outro:
        final_composition.append(18817)
    if add_drums:
        drum_pitches = random.sample([35, 36, 41, 43, 45], k=1)
        for dp in drum_pitches:
            final_composition.extend([(128 * 128) + dp + 256])

    # GENERATE: run model inference
    # Context window: if len > 6656, truncate to [SOS] + last 6656 tokens
    batched_gen_tokens = generate_music(
        final_composition, num_gen_tokens,
        NUM_OUT_BATCHES=10, model_temperature, model_top_p
    )
    # Returns: list of 10 token lists (one per batch)

    generated_batches = batched_gen_tokens  # Save for add_batch
    return [final_composition, generated_batches, block_lines] + audio_and_plots
```

### `generate_music()` — the actual model call

```python
@spaces.GPU
def generate_music(prime, num_gen_tokens, num_gen_batches, temperature, top_p):
    if len(prime) >= 6656:
        prime = [18816] + prime[-6656:]  # Keep SOS + last 6656 tokens

    inp = torch.LongTensor([prime] * num_gen_batches).cuda()  # 10 copies

    with torch.amp.autocast('cuda', dtype=torch.bfloat16):
        out = model.generate(
            inp,
            num_gen_tokens,
            filter_logits_fn=top_p,
            filter_kwargs={'thres': top_p},
            temperature=temperature,
            eos_token=18818,
            return_prime=False,  # Only return NEW tokens
        )
    return out.tolist()  # 10 lists of generated tokens
```

### `add_batch()` — append selected batch to composition

```python
def add_batch(batch_number, final_composition, generated_batches, block_lines):
    if generated_batches:
        # Simply append the chosen batch's tokens to the composition
        final_composition.extend(generated_batches[batch_number])

        # Render to MIDI, audio, and plot
        fname, midi_score = save_midi(final_composition)
        midi_audio = midi_to_colab_audio(fname + '.mid', soundfont_path=SOUNDFONT)
        midi_plot = TMIDIX.plot_ms_SONG(midi_score, ...)

        return (16000, midi_audio), midi_plot, fname + '.mid',
               final_composition, generated_batches, block_lines
```

### Key Insight: `prime_instruments` vs `input_midi` are MUTUALLY EXCLUSIVE

```python
# From generate_music_and_state():

# If MIDI is uploaded → load_midi() tokenizes it → prime_instruments IGNORED
if not final_composition and input_midi is not None:
    final_composition = load_midi(input_midi, ...)
    # prime_instruments code never runs because final_composition is now truthy

# If NO MIDI → prime_instruments used to create initial notes
if not final_composition and input_midi is None:
    final_composition = [18816, 0]
    for i, instr in enumerate(prime_instruments):
        ...
```

**This means: when you upload a seed MIDI, the selected instruments are
completely ignored. The model generates freely from whatever is in the MIDI.**

---

## 5. What Happens When You Click "Generate" on the UI

### Example: User selects "Shakuhachi", clicks Generate (from user's test)

1. `final_composition = []` (fresh session)
2. `input_midi = None` (no file uploaded)
3. Code enters CASE 2:
   ```python
   final_composition = [18816, 0]  # SOS + time=0
   # Shakuhachi = GM program 77
   # patch_pitch = (128 * 77) + (72 - 0*12) + 256 = 10184
   # dur_vel = (8 * 16) + 5 + 16768 = 16901
   final_composition = [18816, 0, 10184, 16901]  # 4 tokens
   ```
4. Log confirms: **"Composition has 4 tokens"**
5. Model generates 512 new tokens from this 4-token context, 10 times in parallel
6. All 10 batches rendered to audio with SoundFont
7. User picks best batch → "sounds really, really good"

### What the model receives as input

```
[18816, 0, 10184, 16901] → 10 copies on GPU → generate 512 tokens each
```

That's it. **4 tokens.** SOS, zero time, one Shakuhachi note at C5 with
medium duration/velocity. The model's 2.31M-MIDI training kicks in and
generates coherent multi-voice Shakuhachi music from this minimal seed.

---

## 6. Our Code — What We Do Differently

### Our generation pipeline (music_service.py, simplified)

```python
async def _generate_impl(request, worker_id):
    client = _client_pool.fresh(worker_id)  # Fresh Gradio client

    # Step 1: Resolve seed MIDI
    seed_path, _, _ = _resolve_seed(genre=request.genre)  # From our seed library

    # Step 2: Resolve instruments to TMIDIX names
    orpheus_instruments = []
    for inst in request.instruments:
        tmidix_name = resolve_tmidix_name(inst)  # e.g. "bass" → "Electric Bass(finger)"
        if tmidix_name:
            orpheus_instruments.append(tmidix_name)

    # Step 3: Call Gradio API
    _gen_result = client.predict(
        input_midi=handle_file(seed_path),  # ← OUR SEED MIDI
        apply_sustains=True,
        remove_duplicate_pitches=True,
        remove_overlapping_durations=True,
        prime_instruments=orpheus_instruments,  # ← IGNORED (because input_midi is set!)
        num_prime_tokens=6656,
        num_gen_tokens=num_gen_tokens,         # 512 (from allocate_token_budget)
        model_temperature=temperature,          # 0.75 (we lowered from 0.9!)
        model_top_p=top_p,                      # 0.96
        add_drums=add_drums,
        add_outro=request.add_outro,
        api_name="/generate_music_and_state",
    )

    # Step 4: Add batch 0
    midi_result = client.predict(batch_number=0, api_name="/add_batch")
    midi_path = midi_result[2]

    # Step 5: Parse MIDI, extract channels, build tool calls for DAW
    ...
```

### Key Differences From the UI

| Parameter            | HF Space UI                 | Our Code                         |
|----------------------|-----------------------------|----------------------------------|
| `input_midi`         | `None` (no upload)          | Our seed library MIDI (broken!)  |
| `prime_instruments`  | `["Shakuhachi"]` (works)    | Passed but IGNORED (input_midi set) |
| `temperature`        | `0.9` (default)             | `0.75` (we lowered it)           |
| `batch selection`    | User picks best of 10       | Always batch 0 (blind)           |
| `seed context`       | 4 tokens (SOS + 1 note)     | ~1600 tokens (broken MIDI)       |
| `iterations`         | User can iterate many times | Single generation                |

---

## 7. ROOT CAUSE FOUND: Corrupted Seed Library

### The Smoking Gun

Our seed library (371 MIDI files across 39 genres) was built by
`build_seed_library.py`. This script downloads Orpheus's training data tokens
from HuggingFace and converts them to MIDI files. **The conversion uses
completely wrong math.**

### The Wrong Code (build_seed_library.py, lines 149-178)

```python
def tokens_to_midi_bytes(tokens: list[int], tempo: int = 120) -> bytes:
    """Convert an Orpheus TMIDIX token sequence to a minimal Standard MIDI file.

    Orpheus uses roughly 3 tokens per note: [pitch_token, time_token, dur_token].
    The exact tokenisation is undocumented, so we write raw tokens as
    note-on/note-off pairs using a simple heuristic mapping that works well
    enough for seeding the model (the model re-tokenises the MIDI anyway).
    """
    i = 0
    while i + 2 < len(tokens):
        t0, t1, t2 = tokens[i], tokens[i + 1], tokens[i + 2]
        pitch = t0 % 128              # WRONG
        channel = (t0 // 128) % 16    # WRONG (no channel in encoding)
        time_delta = max(0, t1 % 480) # WRONG
        dur = max(1, t2 % 480)        # WRONG
        vel = 80 + (t2 % 40)          # WRONG
        i += 3
```

**The comment literally says "The exact tokenisation is undocumented" — but it
IS documented in `app.py`'s `save_midi()` function.** The heuristic math
(`% 128`, `% 480`, etc.) has no relation to the actual token encoding.

### What the Correct Code Should Be

From `save_midi()` in `app.py`:

```python
for token in tokens:
    if 0 <= token < 256:
        time += token * 16
    if 256 <= token < 16768:
        patch = (token - 256) // 128
        pitch = (token - 256) % 128
    if 16768 <= token < 18816:
        dur = ((token - 16768) // 8) * 16
        vel = (((token - 16768) % 8) + 1) * 15
```

### Evidence of Corruption

MIDI analysis of our `neo_soul_00.mid` seed:

```
Type: 0, Ticks/beat: 480
Track 0: 538 notes
  Program changes: []           ← ZERO program changes (no instruments assigned!)
  Channels used: [0,1,2,3,4,5,6,7,8,11,12,13]  ← 12 random channels
  Pitch range: 0-123 (mean=55)
  Notes below A0 (pitch 21): 108/538 (20%)  ← 20% SUB-AUDIBLE PITCHES
  First notes:
    ch=3  note=0   vel=97   ← Pitch 0 = C-1 (inaudible)
    ch=7  note=3   vel=86   ← Pitch 3 = Eb-1 (inaudible)
    ch=13 note=69  vel=117
    ch=11 note=12  vel=94   ← Pitch 12 = C0 (barely audible)
```

A real MIDI would have:
- Program change events assigning instruments to channels
- Notes on channel 0 (piano), channel 9 (drums), etc.
- Pitches primarily in the 36-96 range (musical range)
- No notes at pitch 0-20

### The Chain of Failure

```
1. Our build_seed_library.py takes valid Orpheus tokens
2. Converts them with WRONG heuristic math → garbage MIDI file
3. We upload this garbage MIDI to the HF Space
4. Space's load_midi() tokenizes the garbage MIDI back to tokens
5. But the tokens now represent musical nonsense (wrong pitches, wrong
   instruments, wrong timing)
6. Model generates 512 new tokens continuing from this nonsensical context
7. Output sounds like "random cat walking across a keyboard"
```

**The model is working perfectly. We're feeding it garbage context.**

---

## 8. Secondary Issues

### 8.1 Temperature Lowered from 0.9 to 0.75

We changed `DEFAULT_TEMPERATURE` from `0.9` to `0.75` in `generation_policy.py`,
reasoning it would produce "more coherent" output. But the Space's default is
0.9 for a reason — the model was trained and tuned for this temperature. Lower
temperature makes the output more repetitive and less musical.

### 8.2 Prime Instruments Silently Ignored

When we pass `input_midi` (our seed), the Space's `generate_music_and_state()`
ignores `prime_instruments` entirely (mutually exclusive code paths). So even
though we resolve instruments like `"Shakuhachi"` → `"Shakuhachi"` correctly,
the model never sees them.

### 8.3 Always Picking Batch 0

The model generates 10 parallel stochastic batches. We always pick batch 0.
The UI lets the user listen to all 10 and pick the best. Batch quality varies
significantly — the user's screenshot shows they picked batch 9 as the best.

### 8.4 Gradio State Not Persisting for Iterative Continuation

The HF Space maintains `final_composition` as `gr.State` across API calls. For
iterative continuation (generate → add batch → generate again), this state must
persist. When using `gradio_client`, state is session-scoped. Creating a fresh
`Client()` each time resets the state.

Our `_client_pool.fresh(worker_id)` creates a new client for each request,
meaning we never do iterative continuation — each generation starts from
scratch with our (broken) seed.

### 8.5 The orpheus_mvp_test.py Also Has Issues

This test creates a "minimal seed" MIDI with only a tempo event (no notes).
When the Space tokenizes this, it gets `[18816]` (just SOS), which makes
`final_composition` truthy, so the `prime_instruments` code path is skipped.
The instruments `["Acoustic Grand", "Electric Bass(finger)"]` are passed but
silently ignored.

---

## 9. Evidence & Logs

### Successful UI Generation (user's test)

```
Prime instruments: ['Shakuhachi']
Num prime tokens: 6656
Num gen tokens: 512
Model temp: 0.9
Model top p: 0.96
Add drums: False
Add outro: False
Composition has 4 tokens    ← SOS + time=0 + 1 Shakuhachi note
Generating...
Done!
```

Result: "multiple different flutes playing harmonies, playing different
melodies, cross melodies; it sounds really, really good"

### Our API Generation (quality_test.py)

```
Space: cgcardona/Orpheus-Music-Transformer
Seed: neo_soul_00.mid (4623 bytes)   ← BROKEN SEED
Iterations: 5, gen_tokens: 512
Temperature: 0.9, top_p: 0.96

[Iter 1/5] Generated in 35.1s → MIDI=5878 bytes
[Iter 2/5] Generated in 33.7s → MIDI=6528 bytes
[Iter 3/5] Generated in 28.3s → MIDI=6918 bytes
[Iter 4/5] Generated in 30.9s → MIDI=7338 bytes
[Iter 5/5] Generated in 29.4s → MIDI=7590 bytes
```

Result: "sounds like a random cat walking across a keyboard"

### Output MIDI Analysis

```
=== OUTPUT MIDI (composition.mid) ===
Type: 1, Ticks/beat: 1000
Track 1: 923 notes
  Programs: [0,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0]  ← All piano + drums
  Channels: [0, 9]
  Pitch range: 1-123    ← Note: pitch 1 is inaudible
  Velocity range: 75-120
  First notes:
    ch=0 note=1  vel=90   ← Pitch 1 = C#-1 (inaudible garbage from seed)
    ch=0 note=69 vel=120
    ch=0 note=3  vel=90   ← Pitch 3 = Eb-1 (inaudible garbage from seed)
```

The output MIDI starts with the same garbage pitches (1, 3) as the seed input,
confirming the model is faithfully continuing from the corrupted context.

---

## 10. Open Questions

1. **If we fix the seeds, is that sufficient?** Or do we also need to stop
   passing `input_midi` and use `prime_instruments` instead (like the UI does)?

2. **Should we use seeds at all?** The UI produces great music with just
   instrument priming (4 tokens). Seeds provide genre context but add
   complexity. What's the quality trade-off?

3. **How important is batch selection?** If we always pick batch 0, are we
   getting average quality or worst-case? Should we implement quality scoring
   to auto-select the best batch?

4. **Is temperature 0.75 worse than 0.9?** We assumed lower = more coherent,
   but the model may be calibrated for 0.9.

5. **Can we do iterative continuation via the API?** The UI supports
   generate → pick best → add → generate again. Can `gradio_client` maintain
   `gr.State` across multiple calls? Our tests suggest it can within the same
   session but not across fresh clients.

6. **Is there a way to validate seed MIDI quality before sending?** We need a
   "tokenize and check" step that verifies the MIDI will produce sensible
   tokens.

---

## 11. What We Need Help With

### Immediate Fix

1. **Rebuild the seed library** using the CORRECT token decoder from `save_midi()`
   in `app.py` — or simply use `save_midi()` directly since we have the raw tokens.

2. **Test with NO seed** (just `prime_instruments` like the UI) to confirm the
   model produces good output through our API pipeline.

3. **Restore temperature to 0.9** (Space default).

### Architecture Questions

4. **What's the optimal calling pattern?** Options:
   - A) No seed, just prime_instruments (like clicking Generate on UI)
   - B) Valid seed MIDI (properly encoded) for genre context
   - C) Iterative continuation (generate → add best → generate again)

5. **How should we handle batch selection?** Options:
   - A) Always batch 0 (current, probably fine for most cases)
   - B) Random batch (adds variety)
   - C) Score all 10 batches and pick the best (most complex)

6. **Should we run the model locally instead of proxying to HF?** We have the
   model weights (1.92GB), the SoundFont, and the inference code. This would
   give us full control and eliminate Gradio state management issues.

---

## Appendix A: File Locations

| File | Purpose |
|------|---------|
| `orpheus-music/music_service.py` | Our Orpheus service (calls Gradio API) |
| `orpheus-music/generation_policy.py` | Token budget allocation, temperature defaults |
| `orpheus-music/build_seed_library.py` | **BROKEN** seed MIDI generator |
| `orpheus-music/seed_library/` | 371 seed MIDIs (ALL corrupted) |
| `orpheus-music/orpheus_mvp_test.py` | MVP test script |
| `orpheus-music/quality_test.py` | Iterative continuation test |
| HF Space `app.py` | [Full source](https://huggingface.co/spaces/asigalov61/Orpheus-Music-Transformer/raw/main/app.py) |

## Appendix B: GM Instrument Priming

When the UI uses `prime_instruments` with no MIDI upload, each instrument gets
exactly one note:

```python
# For instrument i (0-indexed):
patch_pitch_token = (128 * gm_program_number) + (72 - (i * 12)) + 256
dur_vel_token = (8 * 16) + 5 + 16768

# This creates:
# - Pitch: C5 for first instrument, C4 for second, C3 for third, etc.
# - Duration: 16 × 16ms = 256ms
# - Velocity: (5+1) × 15 = 90
```

So selecting `["Acoustic Grand", "Electric Bass(finger)", "Drums"]` produces:

```
[18816,          # SOS
 0,              # time delta = 0
 256+72,         # Piano (prog 0) at pitch C5 = token 328
 16768+133,      # dur=16, vel=5 = token 16901
 256+128*33+60,  # Bass (prog 33) at pitch C4 = token 4540
 16901,          # same dur/vel
 256+128*128+72, # Drums (prog 128) at pitch C5 = token 16712
 16901]          # same dur/vel
```

Total: 8 tokens for 3 instruments. The model generates from this minimal context.

## Appendix C: The `patch2number` Mapping

The Space uses `TMIDIX.Number2patch` (inverted) to map instrument names to GM
program numbers. Key entries relevant to our usage:

| Name | GM Program |
|------|-----------|
| `Acoustic Grand` | 0 |
| `Electric Bass(finger)` | 33 |
| `Shakuhachi` | 77 |
| `Drums` | 128 (special) |
| `Strings` | 48 |
| `Flute` | 73 |
| `Acoustic Guitar(nylon)` | 24 |

The full list is the standard 128 GM instruments plus `Drums` as 128.
