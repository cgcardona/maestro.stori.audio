# Storpheus — Complete Reference

> **What Storpheus is**: our Docker service (port 10002) that acts as the
> intelligent proxy between Maestro and the Orpheus MIDI AI on HuggingFace/Gradio.
> Read this before changing anything in `storpheus/` or
> `app/services/storpheus.py`.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Maestro-Side Client — `app/services/storpheus.py`](#2-maestro-side-client)
   - [Types](#21-types)
   - [\_CircuitBreaker](#22-_circuitbreaker)
   - [StorpheusClient](#23-storpheusclient)
   - [Module-level functions](#24-module-level-functions)
3. [Maestro-Side Backend — `app/services/backends/storpheus.py`](#3-maestro-side-backend)
   - [StorpheusBackend](#31-storpheusbackend)
   - [Module-level helpers](#32-module-level-helpers)
4. [Configuration — `app/config.py`](#4-configuration)
5. [Storpheus HTTP API](#5-storpheus-http-api)
6. [Internal Types — `storpheus/storpheus_types.py`](#6-internal-types)
7. [HF Space Gradio API](#7-hf-space-gradio-api)
8. [Token Encoding Scheme](#8-token-encoding-scheme)
9. [Generation Parameters](#9-generation-parameters)
10. [Seed Library](#10-seed-library)
11. [Instrument Resolution (GM)](#11-instrument-resolution)
12. [Channel Mapping](#12-channel-mapping)
13. [MIDI Pipeline](#13-midi-pipeline)
14. [Expressiveness Layer](#14-expressiveness-layer)
15. [Quality Controls](#15-quality-controls)
16. [Session and State Management](#16-session-and-state-management)
17. [Caching](#17-caching)
18. [Constants Quick Reference](#18-constants-quick-reference)
19. [Lessons Learned](#19-lessons-learned)
20. [Stress Test → muse-work/ Output Contract](#20-stress-test--muse-work-output-contract)
21. [Troubleshooting](#21-troubleshooting)

---

## 1. Architecture

```
Stori (macOS DAW)
  │  REST / SSE
  ▼
Maestro (app/, port 10001)
  │  HTTP  POST /generate
  ▼
Storpheus (storpheus/, port 10002)   ← our Docker service
  │  gradio_client
  ▼
HuggingFace Space / Gradio
  │  model inference
  ▼
Orpheus Music Transformer            ← upstream HF MIDI AI (not ours)
```

**Key files:**

| Layer | File | Purpose |
|-------|------|---------|
| Maestro client | `app/services/storpheus.py` | HTTP client, circuit breaker, response normalizer |
| Maestro backend | `app/services/backends/storpheus.py` | `MusicGeneratorBackend` adapter, intent translation |
| Storpheus service | `storpheus/music_service.py` | FastAPI app, Gradio integration, MIDI pipeline |
| Generation policy | `storpheus/generation_policy.py` | Control vectors, token budgets, quality presets |
| Seed selection | `storpheus/seed_selector.py` | Genre + key-aware seed selection |
| Key detection | `storpheus/key_detection.py` | Krumhansl-Schmuckler key detection |
| MIDI transforms | `storpheus/midi_transforms.py` | Lossless MIDI transposition |
| Candidate scorer | `storpheus/candidate_scorer.py` | Multi-dimensional rejection sampling scorer |
| Post-processing | `storpheus/post_processing.py` | Velocity, register, quantization, swing |
| Internal types | `storpheus/storpheus_types.py` | TypedDicts used throughout the service |
| Quality metrics | `storpheus/quality_metrics.py` | Note analysis, rejection scoring |

---

## 2. Maestro-Side Client

**Module:** `app/services/storpheus.py`

This module owns everything Maestro needs to talk to Storpheus: the HTTP
client, the circuit breaker, the adapter that normalizes Storpheus tool calls
into Maestro-internal typed lists, and the process-wide singleton.

### 2.1 Types

#### `StorpheusRawResponse`

```python
class StorpheusRawResponse(TypedDict, total=False):
```

**Import:** `from maestro.services.storpheus import StorpheusRawResponse`

Raw HTTP response shape returned by `StorpheusClient.generate()`. All fields
are optional (`total=False`); presence depends on success vs. failure.

| Field | Type | Present when | Description |
|-------|------|-------------|-------------|
| `success` | `bool` | Always | `True` on success, `False` on any failure |
| `notes` | `list[NoteDict]` | Success (flat path) | Flat list of MIDI notes |
| `tool_calls` | `list[dict[str, object]]` | Success (tool-call path) | DAW-style tool calls from Storpheus |
| `metadata` | `dict[str, object]` | Success | Generation metadata (source, duration, etc.) |
| `channel_notes` | `dict[int, list[NoteDict]]` | Unified generation | Notes keyed by MIDI channel |
| `error` | `str` | Failure | Error description or code |
| `message` | `str` | Failure | Human-readable error message |
| `retry_count` | `int` | Always | Number of submit-phase retries performed |

Two paths exist in successful responses:
- **Flat path** (`notes` present): Storpheus resolved channels internally and
  returns a single flat list. No adapter needed.
- **Tool-call path** (`tool_calls` present): Storpheus returns DAW-format tool
  calls. Pass through `normalize_storpheus_tool_calls()` before use.

#### `StorpheusResultBucket`

```python
class StorpheusResultBucket(TypedDict):
```

**Import:** `from maestro.contracts.json_types import StorpheusResultBucket`

Output of `normalize_storpheus_tool_calls()`. All fields are always present
(empty lists when the corresponding tool calls are absent).

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `list[NoteDict]` | All notes from `addNotes` tool calls |
| `cc_events` | `list[CCEventDict]` | All MIDI CC events from `addMidiCC` calls |
| `pitch_bends` | `list[PitchBendDict]` | All pitch bend events from `addPitchBend` calls |
| `aftertouch` | `list[AftertouchDict]` | All aftertouch events from `addAftertouch` calls |

---

### 2.2 `_CircuitBreaker`

```python
class _CircuitBreaker:
```

Prevents cascading failures when Storpheus is unavailable. Not exported —
accessed only via `StorpheusClient._cb`.

**Constructor:**

```python
_CircuitBreaker(threshold: int = 3, cooldown: float = 60.0)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `threshold` | `int` | `3` | Consecutive failures before circuit opens |
| `cooldown` | `float` | `60.0` | Seconds to stay open before allowing a probe |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `threshold` | `int` | Failure count threshold (set at init) |
| `cooldown` | `float` | Cooldown duration in seconds (set at init) |
| `is_open` | `bool` | `True` when the circuit is tripped and the cooldown has not elapsed |

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `record_success() -> None` | `None` | Resets failure count and closes the circuit |
| `record_failure() -> None` | `None` | Increments failure count; opens circuit when threshold is reached; re-opens if a probe fails during cooldown |

**State machine:**

```
CLOSED → (threshold failures) → OPEN → (cooldown elapsed) → HALF-OPEN
HALF-OPEN → (success) → CLOSED
HALF-OPEN → (failure) → OPEN (cooldown reset)
```

---

### 2.3 `StorpheusClient`

```python
class StorpheusClient:
```

**Import:** `from maestro.services.storpheus import StorpheusClient`

Async HTTP client for the Storpheus music generation service. Uses a
long-lived `httpx.AsyncClient` with keepalive connection pooling so the
TCP/TLS handshake cost is paid once per worker process.

**Constructor:**

```python
StorpheusClient(
    base_url: str | None = None,
    timeout: int | None = None,
    hf_token: str | None = None,
    max_concurrent: int | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str \| None` | `settings.storpheus_base_url` | Storpheus service base URL. Trailing slash stripped. |
| `timeout` | `int \| None` | `settings.storpheus_timeout` | Read timeout in seconds for generation requests. |
| `hf_token` | `str \| None` | `settings.hf_api_key` | HuggingFace bearer token forwarded to Storpheus for GPU quota. |
| `max_concurrent` | `int \| None` | `settings.storpheus_max_concurrent` | Maximum parallel submit+poll cycles (serializes GPU access). |

**Instance attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `base_url` | `str` | Storpheus service base URL (trailing slash stripped) |
| `timeout` | `int` | Generation read timeout in seconds |
| `hf_token` | `str \| None` | HF API key for GPU quota |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `circuit_breaker_open` | `bool` | `True` when the circuit breaker is tripped; callers use this to skip generation without making an HTTP call |
| `client` | `httpx.AsyncClient` | Lazily created persistent HTTP client with keepalive pooling and auth headers |

**Async methods:**

---

#### `warmup() -> None`

Pre-establish the connection to Storpheus during application startup.

Performs a single lightweight health check to open the keepalive connection
so the first real generation request incurs no cold-start latency. Non-fatal:
if Storpheus is not yet ready, the error is logged as a warning and generation
will fail explicitly when needed.

**Returns:** `None`

---

#### `close() -> None`

Close the underlying `httpx.AsyncClient` and release the connection pool.

Call from `close_storpheus_client()` during FastAPI lifespan shutdown.

**Returns:** `None`

---

#### `health_check() -> bool`

Check whether the Storpheus service is reachable and healthy.

Uses a short 3-second probe timeout independent of the generation timeout so
health endpoints respond quickly even when the service is under load.

**Returns:** `bool` — `True` if `GET /health` responds with HTTP 200; `False` on any error or non-200 status.

---

#### `generate(...) -> StorpheusRawResponse`

Generate MIDI via Storpheus using the async submit + long-poll pattern:

1. `POST /generate` → returns immediately with `{jobId, status}` (cache hits arrive pre-completed).
2. `GET /jobs/{jobId}/wait?timeout=N` loops until `complete` or `failed`.

```python
async def generate(
    genre: str = "boom_bap",
    tempo: int = 120,
    instruments: list[str] | None = None,
    bars: int = 4,
    key: str | None = None,
    quality_preset: str = "balanced",
    composition_id: str | None = None,
    emotion_vector: dict[str, float] | None = None,
    role_profile_summary: dict[str, float] | None = None,
    generation_constraints: dict[str, object] | None = None,
    intent_goals: list[dict[str, object]] | None = None,
    seed: int | None = None,
    trace_id: str | None = None,
    intent_hash: str | None = None,
    add_outro: bool = False,
    unified_output: bool = False,
) -> StorpheusRawResponse
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `genre` | `str` | `"boom_bap"` | Musical style / genre string (e.g. `"jazz"`, `"minimal deep house"`) |
| `tempo` | `int` | `120` | BPM |
| `instruments` | `list[str] \| None` | `["drums", "bass"]` | List of instrument role names (e.g. `["drums", "bass", "piano"]`) |
| `bars` | `int` | `4` | Number of bars to generate |
| `key` | `str \| None` | `None` | Target musical key (e.g. `"Am"`, `"C"`) |
| `quality_preset` | `str` | `"balanced"` | `"fast"`, `"balanced"`, or `"quality"` — controls candidate count |
| `composition_id` | `str \| None` | `None` | UUID for correlation / caching (first 8 chars used in logs) |
| `emotion_vector` | `dict[str, float] \| None` | `None` | Serialised `EmotionVector` axes (`energy`, `valence`, `tension`, `intimacy`, `motion`) |
| `role_profile_summary` | `dict[str, float] \| None` | `None` | 12-field expressive summary from `RoleProfile.to_summary_dict()` |
| `generation_constraints` | `dict[str, object] \| None` | `None` | Serialised `GenerationConstraintsDict` (drum_density, subdivision, swing, etc.) |
| `intent_goals` | `list[dict[str, object]] \| None` | `None` | Weighted musical goals (e.g. `[{"name": "dark", "weight": 1.0, "constraint_type": "soft"}]`) |
| `seed` | `int \| None` | `None` | Random seed for deterministic generation |
| `trace_id` | `str \| None` | `None` | Trace UUID for distributed tracing |
| `intent_hash` | `str \| None` | `None` | 16-char SHA-256 of intent payload for idempotency |
| `add_outro` | `bool` | `False` | Append outro tokens to the Orpheus generation |
| `unified_output` | `bool` | `False` | When `True`, Storpheus returns `channel_notes` keyed by instrument |

**Returns:** `StorpheusRawResponse`

**Behaviour:**
- Returns immediately with `error="storpheus_circuit_open"` if the circuit breaker is open.
- Uses `_semaphore` to cap concurrent GPU calls at `max_concurrent`.
- Submit phase retries up to `_MAX_RETRIES = 4` times on 503 or timeout.
- Poll phase retries up to `settings.storpheus_poll_max_attempts` times with
  `settings.storpheus_poll_timeout` seconds per long-poll.
- `_CircuitBreaker.record_failure()` is called on any unrecoverable error.
- `_CircuitBreaker.record_success()` is called on the first successful poll completion.

**Static methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `_is_gpu_cold_start_error(text: str) -> bool` | `bool` | `True` if the error string contains a GPU quota / cold-start phrase |
| `_is_transient_error(text: str) -> bool` | `bool` | `True` if the error is transient (GPU, queue full, timeout) and worth retrying |

---

### 2.4 Module-level functions

#### `normalize_storpheus_tool_calls`

```python
def normalize_storpheus_tool_calls(
    tool_calls: list[dict[str, object]],
) -> StorpheusResultBucket
```

Translate Storpheus-format DAW tool calls into Maestro-internal flat typed
lists. This is the **quarantine boundary**: raw `dict[str, Any]` from Storpheus
enters, a fully-typed `StorpheusResultBucket` exits.

Tool names handled:

| Tool name | Output field | Data extracted |
|-----------|-------------|----------------|
| `addNotes` | `notes` | `params.notes` list |
| `addMidiCC` | `cc_events` | `params.cc` (number) + `params.events` [{beat, value}] |
| `addPitchBend` | `pitch_bends` | `params.events` [{beat, value}] |
| `addAftertouch` | `aftertouch` | `params.events` [{beat, value, pitch?}] |

Unknown tool names are silently ignored so new Storpheus tool calls don't
crash existing Maestro versions.

| Parameter | Type | Description |
|-----------|------|-------------|
| `tool_calls` | `list[dict[str, object]]` | Raw tool calls from `StorpheusRawResponse.tool_calls` |

**Returns:** `StorpheusResultBucket`

---

#### `get_storpheus_client`

```python
def get_storpheus_client() -> StorpheusClient
```

Return the process-wide `StorpheusClient` singleton. Creates one on first
call; returns the same instance on all subsequent calls. The singleton is
shared across all `StorpheusBackend` instances so the connection pool is
reused rather than recreated per request.

**Returns:** `StorpheusClient`

---

#### `close_storpheus_client`

```python
async def close_storpheus_client() -> None
```

Close the singleton `StorpheusClient` (drains the connection pool and closes
the underlying `httpx.AsyncClient`). Resets the singleton to `None` so the
next call to `get_storpheus_client()` creates a fresh instance.

Call this from FastAPI lifespan shutdown.

**Returns:** `None`

---

## 3. Maestro-Side Backend

**Module:** `app/services/backends/storpheus.py`

Adapts `StorpheusClient` to the `MusicGeneratorBackend` interface expected by
`MusicGenerator`. Translates Maestro's typed `GenerationContext` (emotion
vector, role profiles, constraints, goals) into the flat payload that
`StorpheusClient.generate()` accepts.

### 3.1 `StorpheusBackend`

```python
class StorpheusBackend(MusicGeneratorBackend):
```

**Import:** `from maestro.services.backends.storpheus import StorpheusBackend`

**Constructor:**

```python
StorpheusBackend()
```

No parameters. Retrieves the process-wide `StorpheusClient` singleton via
`get_storpheus_client()` and stores it as `self.client`.

**Instance attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `client` | `StorpheusClient` | Process-wide singleton HTTP client |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `backend_type` | `GeneratorBackend` | Always `GeneratorBackend.STORPHEUS` |

**Async methods:**

---

#### `is_available() -> bool`

Delegates to `self.client.health_check()`. Used by `MusicGenerator.get_available_backends()`.

**Returns:** `bool` — `True` if Storpheus is reachable and healthy.

---

#### `generate(...) -> GenerationResult`

Generate MIDI for a **single instrument**.

```python
async def generate(
    instrument: str,
    style: str,
    tempo: int,
    bars: int,
    key: str | None = None,
    chords: list[str] | None = None,
    context: GenerationContext | None = None,
) -> GenerationResult
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instrument` | `str` | *(required)* | Instrument role name (e.g. `"drums"`, `"bass"`, `"piano"`) |
| `style` | `str` | *(required)* | Musical style / genre string |
| `tempo` | `int` | *(required)* | BPM |
| `bars` | `int` | *(required)* | Number of bars |
| `key` | `str \| None` | `None` | Target key (e.g. `"Am"`) |
| `chords` | `list[str] \| None` | `None` | Chord progression (currently unused by Storpheus) |
| `context` | `GenerationContext \| None` | `None` | Typed dict with: `emotion_vector`, `quality_preset`, `composition_id`, `trace_id`, `seed`, `add_outro` |

**`context` keys:**

| Key | Type | Description |
|-----|------|-------------|
| `emotion_vector` | `EmotionVector \| None` | Drives emotional conditioning (energy, valence, tension, intimacy, motion) |
| `quality_preset` | `str` | `"fast"`, `"balanced"`, or `"quality"` (default: `"quality"`) |
| `composition_id` | `str \| None` | UUID for correlation and caching |
| `trace_id` | `str \| None` | Distributed trace UUID (auto-generated if absent) |
| `seed` | `int \| None` | Random seed for deterministic generation |
| `add_outro` | `bool` | Whether to append outro tokens |

**Returns:** `GenerationResult`

**Behaviour:**
- Derives `musical_goals` from `emotion_vector` thresholds and `role_profile` field values.
- Looks up `RoleProfile` for the instrument via `get_role_profile(instrument)` and includes its 12-field summary.
- Builds `GenerationConstraintsDict` from `emotion_to_constraints(emotion_vector)`.
- Computes `intent_hash` via `_build_intent_hash()` for idempotency tracking.
- On success, prefers the flat `notes` field; falls back to `normalize_storpheus_tool_calls(tool_calls)`.
- Applies `_rescale_beats()` if `ENABLE_BEAT_RESCALING=true` (disabled by default).

---

#### `generate_unified(...) -> GenerationResult`

Generate all instruments in a **single Storpheus call** for coherent
multi-instrument output.

```python
async def generate_unified(
    instruments: list[str],
    style: str,
    tempo: int,
    bars: int,
    key: str | None = None,
    context: GenerationContext | None = None,
) -> GenerationResult
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instruments` | `list[str]` | *(required)* | All instrument role names for this section |
| `style` | `str` | *(required)* | Musical style / genre |
| `tempo` | `int` | *(required)* | BPM |
| `bars` | `int` | *(required)* | Number of bars |
| `key` | `str \| None` | `None` | Target key |
| `context` | `GenerationContext \| None` | `None` | Same keys as `generate()` |

**Returns:** `GenerationResult`

The response `metadata` includes `unified_instruments: list[str]`.
When Storpheus returns `channel_notes`, the result contains
`channel_notes: dict[str, list[NoteDict]]` keyed by string channel index so
the caller can distribute notes to per-instrument tracks.

---

### 3.2 Module-level helpers

#### `_normalize_note_keys`

```python
def _normalize_note_keys(note: NoteDict) -> NoteDict
```

Converts snake_case note field names to camelCase wire format. Specifically
maps `start_beat → startBeat` and `duration_beats → durationBeats`. Fields
not in the mapping pass through unchanged.

| Parameter | Type | Description |
|-----------|------|-------------|
| `note` | `NoteDict` | Input note dict (may use either casing) |

**Returns:** `NoteDict` with camelCase keys.

---

#### `_rescale_beats`

```python
def _rescale_beats(
    notes: list[NoteDict],
    cc_events: list[CCEventDict],
    pitch_bends: list[PitchBendDict],
    aftertouch: list[AftertouchDict],
    target_beats: int,
    bars: int = 0,
) -> None
```

Rescales all beat-position fields **in-place** when Storpheus output is
temporally compressed (all notes fall in a window that is less than 50% of
the expected duration). Used to correct for model output that generates
events in a short time window even when many bars were requested.

Only triggers when:
- `notes` is non-empty
- `target_beats > 0`
- Note count ≥ `max(bars * 2, 8)` (avoids rescaling intentionally sparse content)
- Max note end position < `target_beats * 0.5`

Controlled by env var `ENABLE_BEAT_RESCALING` (default: `false`).

| Parameter | Type | Description |
|-----------|------|-------------|
| `notes` | `list[NoteDict]` | Notes to rescale in-place |
| `cc_events` | `list[CCEventDict]` | CC events to rescale in-place |
| `pitch_bends` | `list[PitchBendDict]` | Pitch bend events to rescale in-place |
| `aftertouch` | `list[AftertouchDict]` | Aftertouch events to rescale in-place |
| `target_beats` | `int` | Expected total duration in beats (`bars * 4`) |
| `bars` | `int` | Bar count (used to set minimum note threshold) |

**Returns:** `None` (mutates inputs in-place)

---

#### `_build_intent_hash`

```python
def _build_intent_hash(
    emotion_vector: dict[str, float] | None,
    role_profile_summary: dict[str, float] | None,
    generation_constraints: GenerationConstraintsDict | None,
    musical_goals: list[str],
) -> str
```

Compute a stable 16-character hex hash of the full intent payload for
idempotency tracking across Maestro and Storpheus.

Serialises all four inputs to a canonically sorted JSON blob and returns the
first 16 characters of the SHA-256 hex digest.

| Parameter | Type | Description |
|-----------|------|-------------|
| `emotion_vector` | `dict[str, float] \| None` | Serialised emotion vector axes |
| `role_profile_summary` | `dict[str, float] \| None` | 12-field role profile summary |
| `generation_constraints` | `GenerationConstraintsDict \| None` | Generation constraints |
| `musical_goals` | `list[str]` | Sorted list of goal strings |

**Returns:** `str` — 16-character hex string (first 16 chars of SHA-256)

---

## 4. Configuration

**Module:** `app/config.py` — `Settings` class (env prefix: none)

All settings default to safe development values. Override in `.env` or the
Docker Compose `environment` block.

| Setting | Env var | Type | Default | Description |
|---------|---------|------|---------|-------------|
| `storpheus_base_url` | `STORPHEUS_BASE_URL` | `str` | `"http://localhost:10002"` | Base URL of the Storpheus service. Inside Docker use `"http://storpheus:10002"`. |
| `storpheus_timeout` | `STORPHEUS_TIMEOUT` | `int` | `180` | Fallback max read timeout in seconds for generation HTTP calls. |
| `storpheus_max_concurrent` | `STORPHEUS_MAX_CONCURRENT` | `int` | `2` | Max parallel submit+poll cycles (semaphore). Serialises GPU access. |
| `storpheus_poll_timeout` | `STORPHEUS_POLL_TIMEOUT` | `int` | `30` | Seconds per `/jobs/{id}/wait` long-poll request. |
| `storpheus_poll_max_attempts` | `STORPHEUS_POLL_MAX_ATTEMPTS` | `int` | `10` | Maximum poll iterations before giving up (~5 min total at defaults). |
| `storpheus_cb_threshold` | `STORPHEUS_CB_THRESHOLD` | `int` | `3` | Consecutive failures before circuit breaker opens. |
| `storpheus_cb_cooldown` | `STORPHEUS_CB_COOLDOWN` | `int` | `120` | Seconds the circuit stays open before allowing a probe. |
| `storpheus_required` | `STORPHEUS_REQUIRED` | `bool` | `True` | Hard-gate: abort composition startup if Storpheus health check fails. |
| `storpheus_preserve_all_channels` | `STORPHEUS_PRESERVE_ALL_CHANNELS` | `bool` | `True` | Return all generated MIDI channels; DAW handles routing. |
| `storpheus_enable_beat_rescaling` | `STORPHEUS_ENABLE_BEAT_RESCALING` | `bool` | `False` | Enable `_rescale_beats()` post-processing (disabled while evaluating raw model timing). |
| `storpheus_max_session_tokens` | `STORPHEUS_MAX_SESSION_TOKENS` | `int` | `4096` | Token cap before Gradio session rotation. |
| `storpheus_loops_space` | `STORPHEUS_LOOPS_SPACE` | `str` | `""` | HF Space ID for the Orpheus Loops model (e.g. `"asigalov61/Orpheus-Music-Loops"`). Empty = disabled. |
| `storpheus_use_loops_model` | `STORPHEUS_USE_LOOPS_MODEL` | `bool` | `False` | Feature flag: route short requests (≤8 bars) to the Loops model. |

**Docker Compose:**

```yaml
# docker-compose.yml — storpheus service
environment:
  STORPHEUS_SPACE: ${STORPHEUS_SPACE:-cgcardona/Orpheus-Music-Transformer}
  STORPHEUS_CACHE_DIR: /data/cache
```

```yaml
# docker-compose.yml — maestro service
environment:
  STORPHEUS_BASE_URL: "http://storpheus:10002"
```

---

## 5. Storpheus HTTP API

The Storpheus FastAPI service (`storpheus/music_service.py`) exposes these
endpoints. All requests/responses are JSON.

### `GET /health`

Basic liveness check.

**Response:**
```json
{"status": "ok", "service": "storpheus"}
```

### `GET /health/full`

Extended status including Gradio Space connectivity.

**Response:**
```json
{
  "status": "ok",
  "service": "storpheus",
  "gradio_space": "cgcardona/Orpheus-Music-Transformer",
  "cache_size": 42
}
```

### `POST /generate`

Submit a generation job. Returns immediately.

**Request body** (all fields optional except `genre`/`tempo`/`bars`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `genre` | `str` | *(required)* | Musical style string |
| `tempo` | `int` | `120` | BPM |
| `instruments` | `list[str]` | `["drums", "bass"]` | Instrument role names |
| `bars` | `int` | `4` | Number of bars |
| `key` | `str \| null` | `null` | Target key (e.g. `"Am"`) |
| `quality_preset` | `str` | `"balanced"` | `"fast"`, `"balanced"`, `"quality"` |
| `composition_id` | `str \| null` | `null` | UUID for caching correlation |
| `emotion_vector` | `dict \| null` | `null` | Serialised EmotionVector |
| `role_profile_summary` | `dict \| null` | `null` | 12-field RoleProfile summary |
| `generation_constraints` | `dict \| null` | `null` | Serialised GenerationConstraints |
| `intent_goals` | `list[dict] \| null` | `null` | Weighted musical goals |
| `seed` | `int \| null` | `null` | Random seed |
| `trace_id` | `str \| null` | `null` | Trace UUID |
| `intent_hash` | `str \| null` | `null` | 16-char intent hash |
| `add_outro` | `bool` | `false` | Append outro tokens |
| `unified_output` | `bool` | `false` | Return `channel_notes` keyed by instrument |

**Response — cache hit (immediate):**
```json
{"status": "complete", "result": {<StorpheusRawResponse fields>}}
```

**Response — queued:**
```json
{"jobId": "<uuid>", "status": "queued", "position": 1}
```

### `GET /jobs/{job_id}/wait`

Long-poll for job completion.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | `int` | `30` | Max seconds to block before returning `"pending"` |

**Response — complete:**
```json
{"status": "complete", "result": {<StorpheusRawResponse fields>}}
```

**Response — still running:**
```json
{"status": "pending"}
```

**Response — failed:**
```json
{"status": "failed", "result": {"success": false, "error": "..."}}
```

### `DELETE /cache/clear`

Invalidate the in-memory generation cache.

**Response:** `{"cleared": true}`

### `GET /cache/stats`

Return cache hit/miss statistics.

**Response:**
```json
{"hits": 42, "misses": 18, "size": 12}
```

---

## 6. Internal Types

**Module:** `storpheus/storpheus_types.py`

TypedDicts used throughout the Storpheus service internally. These mirror the
corresponding types in `app/contracts/json_types.py` but are defined
independently to avoid cross-container imports.

### `StorpheusNoteDict`

```python
class StorpheusNoteDict(TypedDict, total=False):
```

A single MIDI note as parsed from a raw MIDI file. Uses snake_case
(internal convention inside Storpheus; converted to camelCase on the wire).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pitch` | `int` | **Yes** | MIDI note number (0-127) |
| `start_beat` | `float` | **Yes** | Start position in beats from bar start |
| `duration_beats` | `float` | **Yes** | Duration in beats |
| `velocity` | `int` | **Yes** | MIDI velocity (0-127) |

### `StorpheusCCEvent`

```python
class StorpheusCCEvent(TypedDict):
```

A MIDI Control Change event.

| Field | Type | Description |
|-------|------|-------------|
| `cc` | `int` | MIDI CC number (0-127; e.g. 64=sustain, 11=expression) |
| `beat` | `float` | Position in beats |
| `value` | `int` | CC value (0-127) |

### `StorpheusPitchBend`

```python
class StorpheusPitchBend(TypedDict):
```

A MIDI Pitch Bend event.

| Field | Type | Description |
|-------|------|-------------|
| `beat` | `float` | Position in beats |
| `value` | `int` | Pitch bend value (-8192 to +8191; 0 = centre) |

### `StorpheusAftertouch`

```python
class StorpheusAftertouch(TypedDict, total=False):
```

A MIDI Aftertouch event — channel or polyphonic.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `beat` | `float` | **Yes** | Position in beats |
| `value` | `int` | **Yes** | Aftertouch pressure value (0-127) |
| `pitch` | `int` | No | MIDI note number (present only for poly aftertouch) |

### `ParsedMidiResult`

```python
class ParsedMidiResult(TypedDict):
```

Return type of `parse_midi_to_notes()`. All fields always present.

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `dict[int, list[StorpheusNoteDict]]` | Notes keyed by MIDI channel number |
| `cc_events` | `dict[int, list[StorpheusCCEvent]]` | CC events keyed by channel |
| `pitch_bends` | `dict[int, list[StorpheusPitchBend]]` | Pitch bends keyed by channel |
| `aftertouch` | `dict[int, list[StorpheusAftertouch]]` | Aftertouch events keyed by channel |
| `program_changes` | `dict[int, int]` | Program number keyed by channel (GM instrument) |

### `CacheKeyData`

```python
class CacheKeyData(TypedDict):
```

Canonical request fields used for LRU cache key generation.

| Field | Type | Description |
|-------|------|-------------|
| `genre` | `str` | Musical style |
| `tempo` | `int` | BPM |
| `key` | `str` | Target key string |
| `instruments` | `list[str]` | Instrument role names |
| `bars` | `int` | Number of bars |
| `intent_goals` | `list[str]` | Goal name strings |
| `energy` | `float` | EmotionVector energy axis (0-1) |
| `valence` | `float` | EmotionVector valence axis (-1 to 1) |
| `tension` | `float` | EmotionVector tension axis (0-1) |
| `intimacy` | `float` | EmotionVector intimacy axis (0-1) |
| `motion` | `float` | EmotionVector motion axis (0-1) |
| `quality_preset` | `str` | Quality preset string |

### `FulfillmentReport`

```python
class FulfillmentReport(TypedDict):
```

Constraint-fulfillment report produced after candidate selection.

| Field | Type | Description |
|-------|------|-------------|
| `goal_scores` | `dict[str, float]` | Per-goal compliance scores (0.0-1.0) |
| `constraint_violations` | `list[str]` | Human-readable descriptions of violated constraints |
| `coverage_pct` | `float` | Fraction of bars that contain at least one note (0.0-1.0) |

### `GradioGenerationParams`

```python
class GradioGenerationParams(TypedDict):
```

Concrete Gradio API parameters derived from the generation control vector.

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | `float` | Sampling temperature (0.70-1.00; default 0.9) |
| `top_p` | `float` | Nucleus sampling threshold (0.90-0.98; default 0.96) |
| `num_prime_tokens` | `int` | Context tokens from seed (2048-6656) |
| `num_gen_tokens` | `int` | Tokens to generate (512-1024) |

### `WireNoteDict`

```python
class WireNoteDict(TypedDict):
```

A single MIDI note in camelCase wire format for the API boundary crossing
back to Maestro. `StorpheusNoteDict` (snake_case) is used internally.

| Field | Type | Description |
|-------|------|-------------|
| `pitch` | `int` | MIDI note number (0-127) |
| `startBeat` | `float` | Start position in beats |
| `durationBeats` | `float` | Duration in beats |
| `velocity` | `int` | MIDI velocity (0-127) |

### `GenerationComparison`

```python
class GenerationComparison(TypedDict):
```

Result of comparing two generation candidates.

| Field | Type | Description |
|-------|------|-------------|
| `generation_a` | `dict[str, float]` | Scores for candidate A |
| `generation_b` | `dict[str, float]` | Scores for candidate B |
| `winner` | `str` | `"a"`, `"b"`, or `"tie"` |
| `confidence` | `float` | Confidence in the winner decision (0.0-1.0) |

### `QualityEvalParams`

```python
class QualityEvalParams(TypedDict, total=False):
```

Parameters for a tool call inside a `/quality/evaluate` request.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `notes` | `list[StorpheusNoteDict]` | No | Notes to score (only `addNotes` calls are scored) |

### `QualityEvalToolCall`

```python
class QualityEvalToolCall(TypedDict):
```

A single tool call as submitted to `/quality/evaluate`.

| Field | Type | Description |
|-------|------|-------------|
| `tool` | `str` | Tool name (e.g. `"addNotes"`) |
| `params` | `QualityEvalParams` | Tool parameters |

### `ScoringParams` (dataclass)

```python
@dataclass
class ScoringParams:
```

All scoring parameters passed to `score_candidate()`. Extracted from the
generation request and policy controls before the candidate-selection loop
so each call is explicit and fully typed.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bars` | `int` | *(required)* | Number of bars (sets expected density) |
| `target_key` | `str \| None` | *(required)* | Target key string or `None` (skip key scoring) |
| `expected_channels` | `int` | *(required)* | Expected number of MIDI channels in output |
| `target_density` | `float \| None` | `None` | Notes per bar target (from constraints) |
| `register_center` | `int \| None` | `None` | Target median pitch (0-127) |
| `register_spread` | `int \| None` | `None` | Acceptable deviation from `register_center` in semitones |
| `velocity_floor` | `int \| None` | `None` | Minimum acceptable velocity |
| `velocity_ceiling` | `int \| None` | `None` | Maximum acceptable velocity |

### `BestCandidate` (dataclass)

```python
@dataclass
class BestCandidate:
```

The winning candidate retained after rejection-sampling evaluation. Wraps
everything needed for post-processing without carrying a loosely-typed dict.

| Field | Type | Description |
|-------|------|-------------|
| `midi_result` | `Sequence[object]` | Raw Gradio response tuple `[audio, plot, midi_path, …]` |
| `midi_path` | `str` | Path to the selected MIDI file on disk |
| `parsed` | `ParsedMidiResult` | Parsed notes, CC, PB, AT keyed by channel |
| `flat_notes` | `list[StorpheusNoteDict]` | All notes from all channels as a flat list |
| `batch_idx` | `int` | Which Gradio batch (0-9) this candidate came from |

---

## 7. HF Space Gradio API

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

**Critical rule:** `input_midi` and `prime_instruments` are mutually exclusive.
If you pass both, the Space uses `input_midi` and silently ignores
`prime_instruments`. Our code enforces: if a seed MIDI is available, always use
it as `input_midi` and set `prime_instruments=[]`.

### `/add_batch`

Appends a selected batch (0-9) to the running composition state.

| Parameter | Type | Notes |
|-----------|------|-------|
| `batch_number` | int | Which of the 10 batches to append (0-indexed). |

Returns `(audio_path, plot_path, midi_path)`.

**Batch accumulation:** We accumulate **1 batch** by default. Multiple batches
concatenated can sound disjointed. Prefer single batch per section with seed
continuity across sections.

---

## 8. Token Encoding Scheme

Orpheus uses a 3-token-per-note encoding. Getting this wrong corrupts all MIDI
output.

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

---

## 9. Generation Parameters

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

**Prime tokens:** Always maximized. More context = better output.
**Gen tokens:** Floor at 512. Fewer tokens produce sparse, low-quality output.

### Control Vector → Gradio params

| Control | Gradio param | Range |
|---------|-------------|-------|
| `creativity` (0-1) | `temperature` | 0.70 – 1.00 |
| `groove` (0-1) | `top_p` | 0.90 – 0.98 |
| bars × density | `num_gen_tokens` | 512 – 1024 |
| complexity | `num_prime_tokens` | 2048 – 6656 |

---

## 10. Seed Library

A curated collection of ~371 genre-specific MIDI files from Orpheus's training
data. Seeds provide rich musical context that dramatically improves generation
quality vs. using `prime_instruments` alone.

### Seed selection (`seed_selector.py`)

1. Load `seed_library/metadata.json` (includes pre-computed key per seed)
2. Match genre: exact → alias → substring → fallback to `"general"`
3. If `target_key` is set, prefer seeds with smallest transposition distance
4. Random selection within genre (deterministic with `seed`)
5. Returns `SeedSelection(path, transpose_semitones, detected_key, ...)`

### Critical: seed vs. prime_instruments

- **Seed available:** pass as `input_midi`, set `prime_instruments=[]`, `add_drums=False`
- **No seed:** set `input_midi=None`, pass instrument names as `prime_instruments`

---

## 11. Instrument Resolution

Three resolution layers convert Maestro's free-text instrument roles to
Orpheus-compatible identifiers:

### `resolve_gm_program(role: str) -> Optional[int]`

Maps role string → GM program number (0-127). Returns `None` for drums.

- Exact match in `_GM_ALIASES` dict (200+ entries)
- Substring match fallback

### `resolve_tmidix_name(role: str) -> Optional[str]`

Maps role → TMIDIX patch name (e.g. `"Acoustic Grand"`, `"Drums"`). Used in
`prime_instruments`.

### `_resolve_melodic_index(role: str) -> Optional[int]`

Maps role → preferred MIDI channel (0-based, excluding ch9):

| Index | GM range | Category |
|-------|----------|----------|
| 0 | 32-39 | Bass family |
| 1 | 0-7, 16-23 | Piano, keys, organ |
| 2 | Everything else | Melody, guitar, strings, brass |
| None | — | Drums (channel 9) |

### Adding new instruments

1. Add alias to `_GM_ALIASES` in `music_service.py`
2. Add parametrised test cases in `test_gm_resolution.py`
3. Use exact TMIDIX spelling from the Gradio dropdown

---

## 12. Channel Mapping

Orpheus generates multi-channel MIDI. Channel assignment:

- **Channel 9** = drums (GM standard)
- **Channel 0** = bass (GM 32-39)
- **Channel 1** = piano/keys/organ (GM 0-7, 16-23)
- **Channel 2+** = other melodic

`filter_channels_for_instruments()` keeps only channels matching requested
instruments. Falls back to nearest available melodic channel.

---

## 13. MIDI Pipeline

```
Gradio API → raw MIDI file
  │
  ├─ parse_midi_to_notes(midi_path, tempo) → ParsedMidiResult
  │
  └─ filter_channels_for_instruments(parsed, instruments)
       → filtered dict with only requested channels
       → flat note list returned to Maestro
```

Uses `mido` for MIDI parsing. Converts ticks to beats using `ticks_per_beat`.

---

## 14. Expressiveness Layer

### Key control via seed transposition

```
target_key → detect seed key (Krumhansl-Schmuckler)
  → compute shortest transposition → transpose_midi() → model
```

`transpose_distance()` picks the direction minimising total semitones. Drums
(channel 9/10) are always skipped.

### Rejection sampling (multi-batch scoring)

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
| Pattern diversity | Low | Entropy-based, penalises monotonous repetition |
| Silence coverage | Low | Are all bars populated? |

### Post-processing pipeline (`post_processing.py`)

| Transform | What it does |
|-----------|-------------|
| Velocity scaling | Maps velocity range to `[velocity_floor, velocity_ceiling]` |
| Register normalisation | Shifts octaves so median pitch aligns with `register_center` |
| Quantisation | Snaps `start_beat` to subdivision grid (8th, 16th notes) |
| Duration cleanup | Enforces `min_duration_beats` / `max_duration_beats` |
| Swing | Delays odd subdivisions by `swing_amount` (0.0-1.0) |

---

## 15. Quality Controls

`rejection_score(notes)` returns 0.0-1.0:

- Penalises single repeated notes
- Penalises sparse bars (low density)
- Rewards pitch diversity and velocity variation

| Preset | Effect |
|--------|--------|
| `fast` | 1 candidate, lowest latency |
| `balanced` | 3 candidates, default quality/speed tradeoff |
| `quality` | 10 candidates, best output |

---

## 16. Session and State Management

- One `gradio_client.Client` per generation session
- State accumulates within a session — create a **fresh client** for each
  iterative continuation
- `MAX_SESSION_TOKENS = 4096` — when cumulative tokens exceed this, session rotates
- Keepalive ping every 600 s prevents GPU timeout eviction

---

## 17. Caching

- LRU cache with 24-hour TTL
- Fuzzy matching (`_FUZZY_EPSILON = 0.35`) — similar requests can hit cache
- Disk persistence in `STORPHEUS_CACHE_DIR` (`/data/cache` in Docker)
- Cache key: hash of (instruments, genre, bars, quality_preset)
- `/cache/clear` endpoint to invalidate

---

## 18. Constants Quick Reference

| Constant | Value | File |
|----------|-------|------|
| `_DEFAULT_SPACE` | `cgcardona/Orpheus-Music-Transformer` | `music_service.py` |
| `DEFAULT_TEMPERATURE` | `0.9` | `generation_policy.py` |
| `DEFAULT_TOP_P` | `0.96` | `generation_policy.py` |
| `_MAX_PRIME_TOKENS` | `6656` | `generation_policy.py` |
| `_MAX_GEN_TOKENS` | `1024` | `generation_policy.py` |
| `_MIN_GEN_TOKENS` | `512` | `generation_policy.py` |
| `_TOKENS_PER_BAR` | `128` | `generation_policy.py` |
| `MAX_SESSION_TOKENS` | `4096` | `music_service.py` |
| `_MIN_SEED_NOTES` | `8` | `music_service.py` |
| `_MIN_SEED_BYTES` | `200` | `music_service.py` |
| `_KEEPALIVE_INTERVAL` | `600` s | `music_service.py` |
| `_FUZZY_EPSILON` | `0.35` | `music_service.py` |
| `STORPHEUS_ACCUMULATE_BATCHES` | `1` | env / `music_service.py` |
| `_MAX_RETRIES` | `4` | `app/services/storpheus.py` |
| `_RETRY_DELAYS` | `[2, 5, 10, 20]` s | `app/services/storpheus.py` |

---

## 19. Lessons Learned

### Lesson 1: Corrupted Seed Library

**Symptom:** All generated music sounded like random notes — no musical structure.

**Root cause:** `tokens_to_midi_bytes()` used incorrect heuristic math (`% 128`,
`% 480`) instead of the correct token ranges from the HF Space `save_midi()`.

**Fix:** Rewrote to exactly mirror the HF Space decoding. Rebuilt all 371 seeds.

**Prevention:** Token encoding is in [Section 8](#8-token-encoding-scheme).
Any change must be validated against the HF Space source.

---

### Lesson 2: Wrong HF Space

**Symptom:** `AppError: You have exceeded your GPU quota`

**Root cause:** Code pointed to the free-tier Space instead of our paid A100 Space.

**Fix:** `_DEFAULT_SPACE = "cgcardona/Orpheus-Music-Transformer"`.

**Prevention:** `STORPHEUS_SPACE` in `.env`; never hardcode the free Space.

---

### Lesson 3: `input_midi` vs `prime_instruments`

**Symptom:** Instrument selection ignored.

**Root cause:** Passing both — the Space silently ignores `prime_instruments`
when `input_midi` is set.

**Fix:** If seed exists, use `input_midi` only with `prime_instruments=[]`.

---

### Lesson 4: Temperature Must Stay at 0.9

**Symptom:** Lowering to 0.75 reduced variety without improving coherence.

**Root cause:** Model was tuned at 0.9. Lower = more repetitive, not better.

**Prevention:** Do not change without A/B testing against the HF Space UI.

---

### Lesson 5: Batch Accumulation

**Symptom:** Accumulating 5+ batches produced disjointed sections.

**Fix:** `STORPHEUS_ACCUMULATE_BATCHES=1`. Single batch per section with seed
continuity across sections.

---

### Lesson 6: Instrument Name Spelling

**Symptom:** `AppError: Value: 'Shakuhachi' is not in the list of choices`

**Root cause:** TMIDIX uses `Skakuhachi` (from GM patch name), not standard English.

**Fix:** Check Gradio dropdown for exact spelling; add to `_GM_ALIASES`.

---

### Lesson 7: Database Schema Drift

**Symptom:** `ProgrammingError: column "parent_variation_id" does not exist`

**Root cause:** `create_all()` doesn't add columns to existing tables.

**Fix:** Alembic migrations via `alembic upgrade head` in `entrypoint.sh`.

**Prevention:** All schema changes through Alembic. Never use `create_all()`.

---

### Lesson 8: Gen Tokens Floor

**Symptom:** Sparse output with few notes per bar.

**Root cause:** Too few generation tokens (<512) produces thin output.

**Fix:** `_MIN_GEN_TOKENS = 512`. Never lower this.

---

## 20. Stress Test → muse-work/ Output Contract

The stress test (`scripts/e2e/stress_test.py`) can write artifacts into a
deterministic `muse-work/` layout that `muse commit` can snapshot directly.

### Running the stress test with muse-work/ output

```bash
# Quick run — 1 request per genre, write muse-work/ layout + muse-batch.json
docker compose exec storpheus python scripts/e2e/stress_test.py \
    --quick --genre jazz,house --flush --output-dir ./muse-work

# Then commit using the batch manifest
muse commit --from-batch muse-batch.json
```

### Directory layout

```
muse-work/
  tracks/<instrument_combo>/<genre>_<bars>b_<composition_id>.mid
  renders/<genre>_<bars>b_<composition_id>.mp3
  previews/<genre>_<bars>b_<composition_id>.webp
  meta/<genre>_<bars>b_<composition_id>.json
muse-batch.json
```

### `muse-batch.json` schema

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | `string` | Unique run identifier, e.g. `"stress-20260227_172919"` |
| `generated_at` | `string` | ISO-8601 UTC timestamp |
| `commit_message_suggestion` | `string` | Suggested `muse commit` message |
| `files` | `array` | One entry per saved artifact (see below) |
| `provenance` | `object` | `prompt`, `model`, `seed`, `storpheus_version` |

Each `files[]` entry:

| Field | Type | Description |
|-------|------|-------------|
| `path` | `string` | Relative to repo root, starts with `muse-work/` |
| `role` | `string` | `"midi"` \| `"mp3"` \| `"webp"` \| `"meta"` |
| `genre` | `string` | Genre used for this generation |
| `bars` | `int` | Bar count |
| `cached` | `bool` | `true` if served from Storpheus cache |

**Invariants:**
- Failed generations are **excluded** from `files[]`
- Cache hits are **included** with `"cached": true`
- All paths are relative to the repo root (never absolute)

See `docs/architecture/muse_vcs.md` for the full generate → commit workflow.

---

## 21. Troubleshooting

### "Random keyboard strokes" output

1. Check seed library integrity — open a `.mid` file in a DAW or with `mido`.
2. Verify `_DEFAULT_SPACE` points to our paid Space (`cgcardona/...`).
3. Check temperature is 0.9 (not lower).
4. Confirm seed is passed as `input_midi` — check logs for `"Seed resolved"`.

### GPU quota exceeded

Verify `STORPHEUS_SPACE` and `HF_TOKEN` are set correctly in `.env`.
The paid Space requires a valid HuggingFace token.

### Circuit breaker tripped

Check `storpheus_circuit_open` in SSE error events. The breaker opens after
`storpheus_cb_threshold` consecutive failures and resets after
`storpheus_cb_cooldown` seconds. Check Storpheus container health:

```bash
docker compose exec storpheus curl -s http://localhost:10002/health
```

### Instrument not found

Check `_GM_ALIASES` for exact spelling. Use TMIDIX name, not common English.
Test with the Gradio UI dropdown to confirm.

### MIDI has wrong instruments

Enable debug logging to see channel selection in
`filter_channels_for_instruments()`. Storpheus may assign instruments to
different channels than expected; fallback logic will be logged.

### Slow generation (>60 s per call)

- Check HF Space status (may be cold-starting)
- Verify `num_gen_tokens` ≤ 1024
- Check `_KEEPALIVE_INTERVAL` is keeping the Space warm

### Running mypy on the Storpheus container

```bash
docker compose exec storpheus mypy .
```

### Running tests on the Storpheus container

```bash
docker compose exec storpheus pytest test_midi_pipeline.py test_quality_metrics.py test_expressiveness.py test_gm_resolution.py -v
```

---

## Progressive Generation (Issue #27)

### Overview

`POST /generate/progressive` implements dependency-ordered instrument generation.
Instruments are partitioned into four musical tiers and generated sequentially:

```
Drums → Bass → Harmony → Melody
```

Each tier's output MIDI is used as the seed for the next tier via the existing
`CompositionState.accumulated_midi_path` mechanism.  This means:

- **Bass** inherits the drum groove (correct rhythmic feel)
- **Harmony** inherits drums + bass (correct root motion)
- **Melody** inherits the full harmonic context (correct chord colours)

### Tier Classification

| Tier | GM Programs | Example Roles |
|------|-------------|---------------|
| `drums` | channel 10 (no GM program) | drums, kick, snare, hihat, 808 |
| `bass` | 32–39 | bass, electric bass, synth bass, fretless |
| `harmony` | 0–15, 16–23, 40–55, 88–95 | piano, organ, strings, pad, choir |
| `melody` | all others | lead, guitar, flute, trumpet, saxophone |

Roles that do not resolve to a GM program default to `melody` (richest context).

### API

**Request:** `POST /generate/progressive`

```json
{
  "genre": "boom_bap",
  "tempo": 90,
  "instruments": ["drums", "bass", "piano", "lead"],
  "bars": 4,
  "key": "C",
  "quality_preset": "balanced",
  "composition_id": "optional-uuid"
}
```

**Response:** `ProgressiveGenerationResult`

```json
{
  "success": true,
  "composition_id": "uuid",
  "tier_results": [
    {
      "tier": "drums",
      "instruments": ["drums"],
      "notes": [...],
      "channel_notes": {"drums": [...]},
      "metadata": {...},
      "elapsed_seconds": 24.1
    }
  ],
  "all_notes": [...],
  "total_elapsed_seconds": 87.3,
  "error": null
}
```

### Implementation

**Key functions** (all in `storpheus/music_service.py`):

| Function | Description |
|----------|-------------|
| `classify_instrument_tier(role)` | Returns `InstrumentTier` for a single role string |
| `group_instruments_by_tier(instruments)` | Partitions list into ordered tier dict |
| `_do_progressive_generate(request)` | Orchestrates sequential tier generation |

**Types** (in `storpheus/storpheus_types.py`):

| Type | Description |
|------|-------------|
| `InstrumentTier` | `str` enum: `drums \| bass \| harmony \| melody` |
| `ProgressiveTierResult` | Per-tier output: notes, channel_notes, metadata, timing |
| `ProgressiveGenerationResult` | Full run output: tier_results, all_notes, timing |

### Limitations and Next Steps

The current implementation runs all tiers synchronously within a single HTTP
request.  Full DAW streaming (per-tier SSE events as each layer completes)
depends on:

- **#18** — thin inference server with async job model
- **#19** — async API with SSE streaming support

Once those are in place, `_do_progressive_generate` can be adapted to emit
`storpheus_tier_complete` SSE events after each tier, letting the DAW
begin rendering drums while bass is still generating.

### Running progressive generation tests

```bash
docker compose exec storpheus pytest test_progressive_generation.py -v
```
