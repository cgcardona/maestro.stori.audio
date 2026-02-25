# Orpheus Music Generation — Operational Reference

> **Purpose**: This document captures everything we know about the Orpheus Music
> Transformer integration — the API, the token encoding, the quality parameters,
> the seed library, and the hard-won lessons from debugging. Read this before
> changing anything in `orpheus-music/`.

---

## Table of Contents

1. [What Orpheus Is](#1-what-orpheus-is)
2. [Architecture](#2-architecture)
3. [HF Space Gradio API](#3-hf-space-gradio-api)
4. [Token Encoding Scheme](#4-token-encoding-scheme)
5. [Generation Parameters](#5-generation-parameters)
6. [Seed Library](#6-seed-library)
7. [Instrument Resolution (GM)](#7-instrument-resolution)
8. [Channel Mapping](#8-channel-mapping)
9. [MIDI Pipeline](#9-midi-pipeline)
10. [Expressiveness Layer](#10-expressiveness-layer)
11. [Quality Controls](#11-quality-controls)
12. [Session and State Management](#12-session-and-state-management)
13. [Caching](#13-caching)
14. [Lessons Learned (Regression Prevention)](#14-lessons-learned)
15. [Constants Quick Reference](#15-constants-quick-reference)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. What Orpheus Is

Orpheus Music Transformer is a neural MIDI generation model hosted as a
Gradio app on Hugging Face Spaces. It is a **continuation engine** — given a
seed (either MIDI tokens or instrument names), it generates new tokens that
extend the composition.

- **Model**: `asigalov61/Orpheus-Music-Transformer` (public/free tier)
- **Our Space**: `cgcardona/Orpheus-Music-Transformer` (paid A100 GPU)
- **Interface**: Gradio API via `gradio_client`
- **Output**: Raw token arrays, converted to MIDI by the Space

We do **not** run the model locally. All inference goes through the HF Space.

---

## 2. Architecture

```
Maestro (app/core/)
  │
  ├─ Intent: COMPOSING
  │    └─ Agent teams decide instruments, sections, bars
  │         └─ execute_unified_generation()
  │              └─ POST /generate → Orpheus REST API
  │
  └─ Orpheus REST API (orpheus-music/music_service.py, port 10002)
       │
       ├─ 1. Resolve seed (genre + key awareness)
       ├─ 2. Transpose seed to target key (if needed)
       ├─ 3. Apply control vector → Gradio params (temperature, top_p, tokens)
       ├─ 4. Call HF Space via gradio_client (N batches)
       ├─ 5. Score all candidates (key, density, register, velocity, diversity)
       ├─ 6. Post-process winner (velocity, register, quantize, swing)
       ├─ 7. Parse MIDI → notes, CC, pitch bends
       ├─ 8. Generate DAW tool calls (createProject, addTrack, addNotes, etc.)
       └─ 9. Return tool calls to Maestro for SSE streaming
```

**Key files:**

| File | Purpose |
|------|---------|
| `orpheus-music/music_service.py` | FastAPI app, Gradio integration, MIDI pipeline |
| `orpheus-music/generation_policy.py` | Control vectors, token budgets, quality presets |
| `orpheus-music/seed_selector.py` | Genre + key-aware seed selection |
| `orpheus-music/key_detection.py` | Krumhansl-Schmuckler key detection |
| `orpheus-music/midi_transforms.py` | Lossless MIDI transposition |
| `orpheus-music/candidate_scorer.py` | Multi-dimensional rejection sampling scorer |
| `orpheus-music/post_processing.py` | Velocity, register, quantization, swing transforms |
| `orpheus-music/build_seed_library.py` | Seed library builder (token → MIDI conversion) |
| `orpheus-music/quality_metrics.py` | Note analysis, rejection scoring |

---

## 3. HF Space Gradio API

Two API endpoints matter:

### `/generate_music_and_state`

The primary generation endpoint. Generates **10 parallel stochastic batches**
in a single call.

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `input_midi` | file/None | None | Seed MIDI file. **Mutually exclusive with `prime_instruments`**. |
| `apply_sustains` | bool | True | |
| `remove_duplicate_pitches` | bool | True | |
| `remove_overlapping_durations` | bool | True | |
| `prime_instruments` | list[str] | [] | TMIDIX instrument names. **Ignored if `input_midi` is set.** |
| `num_prime_tokens` | int | 6656 | Max context tokens from seed. |
| `num_gen_tokens` | int | 512 | Tokens to generate. |
| `model_temperature` | float | 0.9 | |
| `model_top_p` | float | 0.96 | |
| `add_drums` | bool | False | Append drum pattern. |
| `add_outro` | bool | False | Append ending tokens. |

**Critical rule**: `input_midi` and `prime_instruments` are mutually exclusive.
If you pass both, the Space uses `input_midi` and silently ignores
`prime_instruments`. Our code enforces: if a seed MIDI is available, always use
it as `input_midi` and pass `prime_instruments=[]`.

### `/add_batch`

Appends a selected batch (0-9) to the running composition state.

| Parameter | Type | Notes |
|-----------|------|-------|
| `batch_number` | int | Which of the 10 batches to append (0-indexed). |

Returns `(audio_path, plot_path, midi_path)`.

**Batch accumulation**: We accumulate **1 batch** by default
(`ORPHEUS_ACCUMULATE_BATCHES=1`). Multiple batches concatenated can sound
disjointed. Prefer single batch per section with seed continuity across sections.

---

## 4. Token Encoding Scheme

Orpheus uses a 3-token-per-note encoding. This is the exact scheme from the HF
Space's `app.py`. Getting this wrong corrupts all MIDI output.

```
Token range     | Meaning              | Decoding
----------------|----------------------|----------------------------------
0-255           | Time delta           | time_ms += token * 16
256-16767       | Patch + Pitch        | patch = (token - 256) // 128
                |                      | pitch = (token - 256) % 128
16768-18815     | Duration + Velocity  | dur_ms = ((token - 16768) // 8) * 16
                |                      | vel = (((token - 16768) % 8) + 1) * 15
18816           | SOS (start)          | Ignored
18817           | Outro marker         | Ignored
18818           | EOS (end)            | Ignored
```

**Patch numbers** map to General MIDI programs (0-127), plus 128 = drums.

The `tokens_to_midi_bytes()` function in `build_seed_library.py` must use
exactly this scheme. A previous version used heuristic math (`% 128`,
`% 480`) that produced corrupted MIDI — see [Lesson 1](#lesson-1).

---

## 5. Generation Parameters

### Defaults (match HF Space UI)

| Parameter | Value | Source |
|-----------|-------|--------|
| Temperature | **0.9** | `generation_policy.py:DEFAULT_TEMPERATURE` |
| Top-P | **0.96** | `generation_policy.py:DEFAULT_TOP_P` |
| Prime tokens | **6656** | `generation_policy.py:_MAX_PRIME_TOKENS` |
| Gen tokens | **512-1024** | `generation_policy.py:_MIN/_MAX_GEN_TOKENS` |

### Token budget allocation

```
gen_tokens = clamp(bars * 128, 512, 1024)
prime_tokens = 6656  (always max)
```

- **Prime tokens**: Always maximized. The model is a continuation engine; more
  context = better output.
- **Gen tokens**: Floor at 512. Fewer tokens produce sparse, low-quality output.
  Ceiling at 1024 to stay within the Space's proven range.
- **Tokens per bar**: ~128 (rough estimate: ~3 tokens per note).

### Temperature

**Do not lower below 0.9.** The model was tuned at 0.9. We tested 0.75 and it
reduced musical variety without improving quality. The HF Space defaults to 0.9.

---

## 6. Seed Library

### What it is

A curated collection of ~371 genre-specific MIDI files extracted from Orpheus's
training data. Seeds provide rich musical context that dramatically improves
generation quality vs. using `prime_instruments` alone.

### How seeds are built

`build_seed_library.py`:

1. Downloads token sequences from the Orpheus training dataset
2. Classifies by genre using keyword matching
3. Scores for quality (length, diversity, structure)
4. Converts tokens to MIDI using the **exact** encoding from the HF Space
5. Exports top seeds per genre as `.mid` files
6. Writes `seed_library/metadata.json` index

### How seeds are selected

`seed_selector.py`:

1. Load `metadata.json` (includes pre-computed key for each seed)
2. Match genre: exact → alias → substring → fallback to "general"
3. If `target_key` is set, prefer seeds closest to the target key (minimizes
   transposition distance, preserving model quality)
4. Random selection within genre (deterministic with seed)
5. Returns `SeedSelection(path, transpose_semitones, detected_key, ...)`
6. The caller transposes the seed MIDI if `transpose_semitones != 0`

### Seed quality thresholds

- `_MIN_SEED_NOTES = 8` — reject seeds with fewer than 8 notes
- `_MIN_SEED_BYTES = 200` — reject seeds smaller than 200 bytes

### Critical: Seed vs. prime_instruments

When a seed MIDI is available:
- Pass it as `input_midi` to the Gradio API
- Set `prime_instruments=[]`
- Set `add_drums=False` (the seed provides drum context)

When no seed is available (rare):
- Set `input_midi=None`
- Pass instrument names as `prime_instruments`
- `add_drums` may be set to `True`

---

## 7. Instrument Resolution

Three resolution layers convert Maestro's free-text instrument roles to
Orpheus-compatible identifiers:

### `resolve_gm_program(role) → Optional[int]`

Maps role string → GM program number (0-127). Returns `None` for drums.

- Exact match in `_GM_ALIASES` dict (200+ entries)
- Substring match as fallback
- `None` for unresolvable roles

### `resolve_tmidix_name(role) → Optional[str]`

Maps role → TMIDIX patch name string (e.g., `"Acoustic Grand"`, `"Drums"`).
This is what the Gradio API accepts in `prime_instruments`.

### `_resolve_melodic_index(role) → Optional[int]`

Maps role → preferred MIDI channel (0-based, excluding ch9):

| Index | GM range | Category |
|-------|----------|----------|
| 0 | 32-39 | Bass family |
| 1 | 0-7, 16-23 | Piano, keys, organ |
| 2 | Everything else | Melody, guitar, strings, brass, etc. |
| None | — | Drums (always channel 9) |

### Adding new instruments

1. Add the alias to `_GM_ALIASES` in `music_service.py`
2. Add parametrized test cases in `test_gm_resolution.py`
3. Use the exact spelling from the Gradio dropdown (e.g., `Skakuhachi` not `Shakuhachi`)

---

## 8. Channel Mapping

Orpheus generates multi-channel MIDI. Our pipeline maps channels to roles:

- **Channel 9** = drums (GM standard, no program change)
- **Channel 0** = bass (GM 32-39)
- **Channel 1** = piano/keys/organ (GM 0-7, 16-23)
- **Channel 2+** = other melodic (everything else)

`filter_channels_for_instruments()` keeps only channels matching the requested
instruments. If a preferred channel index is missing from the MIDI output, it
falls back to the nearest available melodic channel.

---

## 9. MIDI Pipeline

```
Gradio API → raw MIDI file
  │
  ├─ parse_midi_to_notes(midi_path, tempo)
  │    → {notes: {ch: [...]}, cc_events, pitch_bends, aftertouch, program_changes}
  │
  └─ filter_channels_for_instruments(parsed, instruments)
       → filtered dict with only requested channels
       → flat notes list returned to Maestro
```

### `parse_midi_to_notes()`

Parses a MIDI file into structured note and event data grouped by channel.
Uses the `mido` library. Converts ticks to beats using `ticks_per_beat`.

### `filter_channels_for_instruments()`

Keeps only channels corresponding to requested instruments. Uses
`_channels_to_keep()` which maps instruments to channel indices via
`_resolve_melodic_index()`.

### Output

Orpheus returns flat note lists directly to Maestro. Maestro handles DAW tool
call generation (createProject, addMidiTrack, addNotes, etc.) on its side.

---

## 10. Expressiveness Layer

The expressiveness layer transforms Orpheus from a raw continuation engine into
a musically intelligent generator. It sits between seed selection and tool call
generation, adding four capabilities:

### 10.1 Key Control via Seed Transposition

Orpheus has no key token — key is entirely implicit in the seed's pitch content.
We control key by transposing the seed MIDI before feeding it to the model.

**Pipeline:**

```
target_key (from request) → detect seed key (Krumhansl-Schmuckler)
  → compute shortest transposition distance → transpose_midi() → model
```

**Key detection** (`key_detection.py`): Correlates pitch-class histogram against
Krumhansl-Schmuckler major/minor profiles. Returns `(tonic, mode, confidence)`.
Pre-computed for all 371 seeds and stored in `metadata.json`.

**Transposition** (`midi_transforms.py`): Shifts all non-drum note pitches by N
semitones, clamped to 0-127. Writes to a temp file so the seed library is never
mutated. Drums (channel 10) are always skipped.

**Shortest path**: `transpose_distance()` picks the direction (up or down) that
minimizes total semitones moved. A tritone (6 semitones) can go either way.

### 10.2 Rejection Sampling (Multi-Batch Scoring)

The Gradio API generates 10 parallel stochastic batches per call. Previously we
used batch 0 and discarded the rest. Now we score N candidates and pick the best.

**Batch count** is controlled by quality preset:

| Preset | Batches scored |
|--------|---------------|
| `fast` | 1 |
| `balanced` | 3 |
| `quality` | 10 |

**Scoring dimensions** (`candidate_scorer.py`):

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Key compliance | High | Pitch-class distribution vs target key profile |
| Density match | Medium | Notes per bar vs requested density |
| Register compliance | Medium | Pitch centroid vs `register_center`/`register_spread` |
| Velocity compliance | Medium | Note velocity range vs `velocity_floor`/`velocity_ceiling` |
| Pattern diversity | Low | Entropy-based, penalizes monotonous repetition |
| Silence coverage | Low | Are all bars populated? |

Each dimension returns 0.0-1.0. The final score is a weighted sum. If a
candidate exceeds the acceptance threshold early, scoring stops (fast exit).

### 10.3 Control Vector Activation

`GenerationControlVector` translates emotional intent into concrete Gradio
sampling parameters. Previously computed but never used.

**Mapping** (`generation_policy.py`):

| Control | Gradio param | Range |
|---------|-------------|-------|
| `creativity` (0-1) | `temperature` | 0.70 – 1.00 |
| `groove` (0-1) | `top_p` | 0.90 – 0.98 |
| bars × density | `num_gen_tokens` | 512 – 1024 |
| complexity | `num_prime_tokens` | 2048 – 6656 |

Explicit request overrides (`request.temperature`, `request.top_p`) take
precedence over control-vector-derived values.

### 10.4 Post-Processing Pipeline

Applied after candidate selection, before tool call generation. Each transform
operates on the flat note list `[{pitch, start_beat, duration_beats, velocity}]`.

**Transforms** (`post_processing.py`):

| Transform | What it does |
|-----------|-------------|
| Velocity scaling | Maps velocity range to `[velocity_floor, velocity_ceiling]` |
| Register normalization | Shifts octaves so median pitch aligns with `register_center` |
| Quantization | Snaps `start_beat` to subdivision grid (8th, 16th notes) |
| Duration cleanup | Enforces `min_duration_beats` / `max_duration_beats` |
| Swing | Delays odd subdivisions by `swing_amount` (0.0-1.0) |

Transforms are configurable via `PostProcessorConfig`. The `build_post_processor()`
factory creates a configured instance from generation constraints and/or role
profile summaries (data-driven from the 222K-track Musical DNA heuristics).

The entire pipeline can be disabled with `enabled=False`.

### 10.5 Full Pipeline (End-to-End)

```
GenerateRequest
  │
  ├─ select_seed(genre, target_key)      → SeedSelection
  ├─ transpose_midi(seed, semitones)     → transposed seed (if needed)
  ├─ build_controls(emotion, constraints) → GenerationControlVector
  ├─ apply_controls_to_params(controls)  → {temperature, top_p, tokens}
  │
  ├─ Gradio /generate_music_and_state    → 10 batches
  │
  ├─ for batch in batches[:N]:           (N from quality preset)
  │    ├─ /add_batch(batch_idx)          → MIDI file
  │    ├─ parse_midi_to_notes()          → notes per channel
  │    ├─ score_candidate()              → CandidateScore
  │    └─ if score > threshold → accept early
  │
  ├─ best_candidate selected
  ├─ post_processor.process(notes)       → velocity, register, quantize, swing
  └─ filter_channels_for_instruments()   → flat notes returned to Maestro
```

### 10.6 Testing

All expressiveness modules are covered in `test_expressiveness.py` (47 tests):

- `TestKeyDetection` — major/minor scale detection, edge cases
- `TestKeyUtilities` — transposition distance, key parsing, Pearson correlation
- `TestTransposeNotes` — pitch shifting, clamping, drum skip
- `TestCandidateScoring` — multi-dimensional scoring, key compliance, selection
- `TestPostProcessing` — velocity, register, quantization, swing, config factory
- `TestControlVectorActivation` — temperature/top_p mapping, safe ranges, presets

---

## 11. Quality Controls

### Rejection scoring

`rejection_score(notes)` returns 0.0-1.0:
- Penalizes single repeated notes
- Penalizes sparse bars (low density)
- Rewards pitch diversity and velocity variation

### Seed quality analysis

`analyze_seed(midi_path)` checks note count, pitch range, polyphony, density,
and drum hits before using a seed.

### Quality presets

| Preset | Effect |
|--------|--------|
| `fast` | Fewer candidates, lower latency |
| `balanced` | Default — good quality/speed tradeoff |
| `quality` | More candidates, best output |

---

## 12. Session and State Management

### Gradio client lifecycle

- One `gradio_client.Client` per generation session
- The client maintains internal `gr.State` across `/generate_music_and_state`
  and `/add_batch` calls
- **Critical**: State accumulates within a session. When doing iterative
  continuation (generate → use output as seed → generate again), create a
  **fresh client** for each iteration and explicitly pass the previous output
  MIDI as `input_midi`. Do not rely on implicit state persistence.

### Session token cap

- `MAX_SESSION_TOKENS = 4096`
- When cumulative tokens exceed this cap, the session rotates (new client)
- Prevents context window overflow (model max: 8192 tokens)

### Keepalive

- Periodic ping every 600 seconds prevents GPU timeout eviction on the
  HF Space

---

## 13. Caching

- LRU cache with 24-hour TTL
- Fuzzy matching (`_FUZZY_EPSILON = 0.35`) — similar requests can hit cache
- Disk persistence in `ORPHEUS_CACHE_DIR` (`/data/cache` in Docker)
- Cache key: hash of (instruments, genre, bars, quality_preset)
- `/cache/clear` endpoint to invalidate

---

## 14. Lessons Learned (Regression Prevention)

### Lesson 1: Corrupted Seed Library {#lesson-1}

**Symptom**: All generated music sounded like "a random cat walking across a
keyboard" — just random notes with no musical structure.

**Root cause**: The `tokens_to_midi_bytes()` function in `build_seed_library.py`
used incorrect heuristic math to convert Orpheus tokens to MIDI bytes. The
encoding used `% 128` and `% 480` instead of the correct ranges documented in
the HF Space's `save_midi()` function.

**Fix**: Rewrote `tokens_to_midi_bytes()` to exactly mirror the HF Space's
`save_midi()` decoding. Rebuilt all 371 seed MIDIs.

**Prevention**: The token encoding is documented in [Section 4](#4-token-encoding-scheme).
Any change to token conversion must be validated against the HF Space source.

---

### Lesson 2: Wrong HF Space (GPU Quota)

**Symptom**: `AppError: You have exceeded your GPU quota (60s requested vs. 0s left)`

**Root cause**: Code was pointing to the public free-tier Space
(`asigalov61/Orpheus-Music-Transformer`) instead of our paid A100 Space.

**Fix**: Changed `_DEFAULT_SPACE` in `music_service.py` and `STORI_ORPHEUS_SPACE`
in `docker-compose.yml` to `cgcardona/Orpheus-Music-Transformer`.

**Prevention**: The `_DEFAULT_SPACE` constant must always point to our paid Space.
The free Space should never be hardcoded anywhere.

---

### Lesson 3: input_midi vs. prime_instruments

**Symptom**: Passing both `input_midi` and `prime_instruments` — the Space
silently ignores `prime_instruments` when `input_midi` is set.

**Root cause**: These parameters are mutually exclusive in the HF Space code,
but the API accepts both without error.

**Fix**: Our code now enforces: if a seed MIDI exists, use `input_midi` only
and set `prime_instruments=[]`.

**Prevention**: Never pass both. The rule is in `_do_generate()`.

---

### Lesson 4: Temperature Must Stay at 0.9

**Symptom**: Lowering temperature to 0.75 reduced musical variety without
improving coherence.

**Root cause**: The model was trained/tuned at temperature 0.9. The HF Space
defaults to 0.9. Lower temperatures make the model more repetitive without
improving structure.

**Fix**: Restored `DEFAULT_TEMPERATURE` to 0.9.

**Prevention**: Do not change temperature without A/B testing with the HF Space
UI as baseline.

---

### Lesson 5: Batch Accumulation

**Symptom**: Accumulating multiple batches (default 5 in the HF Space UI)
produced disjointed sections when called programmatically.

**Root cause**: The HF Space UI presents batches as cumulative — batch 9
contains the full composition. Accumulating batches via `/add_batch` appends
to `final_composition` state, but doing this without the UI's preview/selection
step adds unfiltered content.

**Fix**: Set `ORPHEUS_ACCUMULATE_BATCHES=1`. Prefer single batch per section
with seed continuity across sections.

**Prevention**: Keep batch count at 1 unless specifically testing accumulation.

---

### Lesson 6: Instrument Name Spelling

**Symptom**: `AppError: Value: 'Shakuhachi' is not in the list of choices`

**Root cause**: The Gradio API uses TMIDIX instrument names which don't always
match standard English spelling. The shakuhachi is spelled `Skakuhachi` in
TMIDIX (following the GM patch name).

**Fix**: Added `"skakuhachi": 77` to `_GM_ALIASES` and used the TMIDIX spelling.

**Prevention**: When adding instruments, always check the Gradio dropdown list
for exact spelling. The full list is in `_TMIDIX_PATCH_NAMES` in `music_service.py`.

---

### Lesson 7: Database Schema Drift

**Symptom**: `ProgrammingError: column "parent_variation_id" does not exist`
during MUSE variation save.

**Root cause**: The app used `Base.metadata.create_all()` for schema
initialization, which only creates new tables — it cannot add columns to
existing tables. When `parent_variation_id` was added to the model, the
database was never migrated.

**Fix**: Added Alembic migrations to the startup flow (`entrypoint.sh` runs
`alembic upgrade head` before uvicorn). Removed `create_all()` from
`init_db()`. Stamped the existing database at the initial migration revision.

**Prevention**: All schema changes must go through Alembic migrations. Never
rely on `create_all()` for production databases.

---

### Lesson 8: Gen Tokens Floor

**Symptom**: Sparse, low-quality output with few notes per bar.

**Root cause**: Requesting too few generation tokens (<512) produces thin output.

**Fix**: `_MIN_GEN_TOKENS = 512` — the floor matches the HF Space default.

**Prevention**: Do not lower `_MIN_GEN_TOKENS` below 512.

---

## 15. Constants Quick Reference

| Constant | Value | File | Line |
|----------|-------|------|------|
| `_DEFAULT_SPACE` | `cgcardona/Orpheus-Music-Transformer` | `music_service.py` | 75 |
| `DEFAULT_TEMPERATURE` | `0.9` | `generation_policy.py` | 480 |
| `DEFAULT_TOP_P` | `0.96` | `generation_policy.py` | 481 |
| `_MAX_PRIME_TOKENS` | `6656` | `generation_policy.py` | 485 |
| `_MAX_GEN_TOKENS` | `1024` | `generation_policy.py` | 486 |
| `_MIN_GEN_TOKENS` | `512` | `generation_policy.py` | 487 |
| `_TOKENS_PER_BAR` | `128` | `generation_policy.py` | 488 |
| `MAX_SESSION_TOKENS` | `4096` | `music_service.py` | 88 |
| `_MIN_SEED_NOTES` | `8` | `music_service.py` | 1105 |
| `_MIN_SEED_BYTES` | `200` | `music_service.py` | 1106 |
| `_KEEPALIVE_INTERVAL` | `600` s | `music_service.py` | 76 |
| `_FUZZY_EPSILON` | `0.35` | `music_service.py` | 91 |
| `ORPHEUS_ACCUMULATE_BATCHES` | `1` | env / `music_service.py` | — |

---

## 16. Troubleshooting

### "Random keyboard strokes" output

1. Check seed library integrity — are the `.mid` files valid MIDI? Open one in
   a DAW or with `mido`.
2. Verify `_DEFAULT_SPACE` points to our paid Space.
3. Check temperature is 0.9 (not lower).
4. Confirm seed is being passed as `input_midi` (check logs for
   `"Seed resolved"` messages).

### GPU quota exceeded

Verify `STORI_ORPHEUS_SPACE` and `HF_TOKEN` are set correctly in the
environment. The paid Space requires a valid HuggingFace token.

### Instrument not found

Check `_GM_ALIASES` for the exact spelling. Use the TMIDIX name, not the
common English name. Test with the Gradio UI dropdown to confirm.

### MIDI has wrong instruments

Check channel mapping in the logs. Orpheus may assign instruments to different
channels than expected. The `filter_channels_for_instruments()` function uses
fallback logic — enable debug logging to see channel selection.

### Slow generation (>60s per call)

- Check HF Space status (may be cold-starting)
- Verify `num_gen_tokens` is not exceeding 1024
- Check `_KEEPALIVE_INTERVAL` is keeping the Space warm
