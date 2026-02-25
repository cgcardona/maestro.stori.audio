# Orpheus Contract Suite

> Exhaustive boundary audit between Maestro and Orpheus.
> Generated: 2026-02-24 | Last updated: 2026-02-25

---

## A. Executive Summary

### What This Contract Is

This document defines every input/output boundary between the **Maestro** orchestration service and the **Orpheus** music generation service. It specifies canonical schemas for all payloads, proves coverage of musical vectors across the boundary, and provides contract tests for automated validation.

### Architecture At A Glance

```
┌─────────────────────────┐         HTTP/JSON         ┌──────────────────────────┐
│       MAESTRO           │ ───────────────────────►   │        ORPHEUS           │
│  (orchestration, DAW    │   POST /generate           │  (MIDI generation,       │
│   state, intent engine, │   GET /jobs/{id}/wait      │   Gradio inference,      │
│   emotion vectors,      │   GET /health              │   GM resolution,         │
│   role profiles,        │ ◄───────────────────────   │   caching, quality)      │
│   expressiveness)       │   JSON responses           │                          │
└─────────────────────────┘                            └──────────────────────────┘
```

### Data Flow Summary

| Flow | Description |
|------|-------------|
| **Maestro → Orpheus** | Intent-conditioned generation request (genre, tempo, instruments, bars, emotion_vector, role_profile_summary, generation_constraints, intent_goals, quality_preset, seed, trace_id, intent_hash) |
| **Orpheus → Maestro** | Generated MIDI data (notes, CC events, pitch bends, aftertouch) + metadata |
| **Maestro post-processing** | Expressiveness enrichment (velocity curves, CC automation, pitch bends, timing humanization) applied Maestro-side after Orpheus response |

### Key Risks

| # | Risk | Severity | Status |
|---|------|----------|--------|
| R1 | Maestro intent lost at boundary (flat floats + goal strings only) | **CRITICAL** | **RESOLVED** — canonical intent blocks transmitted |
| R2 | EmotionVector tension axis dropped at boundary | **HIGH** | **RESOLVED** — full 5-axis emotion_vector sent |
| R3 | RoleProfile reduced to single complexity float | **HIGH** | **RESOLVED** — 12-field role_profile_summary transmitted |
| R4 | No deterministic seed propagation | **MEDIUM** | **RESOLVED** — seed parameter added |
| R5 | GenerationConstraints never sent to Orpheus | **HIGH** | **RESOLVED** — full generation_constraints block forwarded |
| R6 | Orpheus tool_calls coupled via string matching | **MEDIUM** | MITIGATED (adapter boundary exists) |
| R7 | No trace_id / request_id propagation | **MEDIUM** | **RESOLVED** — trace_id and intent_hash added |
| R8 | 63-feature heuristic time-series does not exist | **INFO** | DESIGN GAP |
| R9 | Emotional arcs (time-varying emotion) not implemented | **INFO** | DESIGN GAP |

### Current State

All P0/P1 remediation items have been implemented:
- Full canonical intent pipeline (emotion_vector, role_profile_summary, generation_constraints, intent_goals)
- seed, trace_id, intent_hash for determinism and observability
- fulfillment_report in response metadata
- Curated seed library replaces procedural seed generation (no create_seed_midi, no continuation seeds)
- generation_policy.py consumes canonical blocks directly (no parallel derivation)

---

## B. Boundary Map

### B.1 Directional Inventory

#### Maestro → Orpheus (3 active boundaries)

| # | Boundary | Direction | Method | Path | Sync/Async | Transport |
|---|----------|-----------|--------|------|------------|-----------|
| B1 | HealthCheck | M→O | GET | `/health` | Sync (3s timeout) | HTTP/JSON |
| B2 | SubmitGeneration | M→O | POST | `/generate` | Async (returns jobId) | HTTP/JSON |
| B3 | PollJob | M→O | GET | `/jobs/{jobId}/wait?timeout=30` | Long-poll | HTTP/JSON |

#### Orpheus → Maestro (0 active callbacks)

Orpheus has **no callback mechanism** to Maestro. All communication is Maestro-initiated poll-based. Orpheus only responds to requests.

#### Orpheus Endpoints NOT Used by Maestro

| Endpoint | Purpose | Used? |
|----------|---------|-------|
| `GET /diagnostics` | Service diagnostics | No |
| `GET /queue/status` | Queue depth/status | No |
| `GET /cache/stats` | Cache statistics | No |
| `POST /cache/warm` | Pre-generate common combos | No |
| `DELETE /cache/clear` | Clear caches | No |
| `POST /quality/evaluate` | Quality metrics | No |
| `POST /quality/ab-test` | A/B comparison | No |
| `POST /jobs/{id}/cancel` | Cancel job | No |

### B.2 Boundary Detail: SubmitGeneration (B2)

| Property | Value |
|----------|-------|
| **Name** | SubmitGeneration |
| **Direction** | Maestro → Orpheus |
| **Transport** | HTTP POST, JSON body |
| **Idempotency** | NOT idempotent. Each call may produce different results (stochastic generation). Orpheus deduplicates by `dedupe_key` within the job queue. |
| **Retry semantics** | 4 retries with delays [2s, 5s, 10s, 20s]. Retried on: `ReadTimeout`, `HTTPStatusError`, `503` (queue full). NOT retried on: `ConnectError`. |
| **Timeout budget** | Submit: connect 5s, read 10s, write 10s. Poll: connect 5s, read 35s, write 5s. Total budget: ~5 min (10 polls × 30s). |
| **Correlation** | `composition_id` (optional, free-form string). No `trace_id` or `request_id`. |
| **AuthN/AuthZ** | HuggingFace Bearer token in `Authorization` header (for Gradio Space). No Maestro-Orpheus auth. |
| **Circuit breaker** | Opens after 3 consecutive failures, 120s cooldown, half-open probe. |
| **Concurrency** | Semaphore-limited to `orpheus_max_concurrent` (default 2). |

### B.3 Boundary Detail: PollJob (B3)

| Property | Value |
|----------|-------|
| **Name** | PollJob |
| **Direction** | Maestro → Orpheus |
| **Transport** | HTTP GET, query param `timeout` |
| **Idempotency** | Idempotent (read-only). |
| **Retry semantics** | Up to `orpheus_poll_max_attempts` (default 10). ReadTimeout is NOT an error — job continues. ConnectError aborts. |
| **Timeout budget** | `orpheus_poll_timeout` seconds per poll (default 30) + 5s buffer. |

### B.4 Boundary JSON

```json
{
  "boundary_map": {
    "version": "1.0.0",
    "boundaries": [
      {
        "id": "B1",
        "name": "HealthCheck",
        "direction": "maestro_to_orpheus",
        "method": "GET",
        "path": "/health",
        "sync": true,
        "transport": "http_json",
        "idempotent": true,
        "timeout_ms": 3000,
        "retry": { "enabled": false },
        "auth": "none",
        "correlation_id": null
      },
      {
        "id": "B2",
        "name": "SubmitGeneration",
        "direction": "maestro_to_orpheus",
        "method": "POST",
        "path": "/generate",
        "sync": false,
        "transport": "http_json",
        "idempotent": false,
        "timeout_ms": { "connect": 5000, "read": 10000, "write": 10000 },
        "retry": {
          "enabled": true,
          "max_attempts": 4,
          "delays_ms": [2000, 5000, 10000, 20000],
          "retryable_errors": ["ReadTimeout", "HTTPStatusError", "503"]
        },
        "auth": "bearer_hf_token",
        "correlation_id": "composition_id",
        "circuit_breaker": {
          "threshold": 3,
          "cooldown_s": 120,
          "half_open": true
        },
        "concurrency_limit": 2
      },
      {
        "id": "B3",
        "name": "PollJob",
        "direction": "maestro_to_orpheus",
        "method": "GET",
        "path": "/jobs/{jobId}/wait",
        "sync": true,
        "transport": "http_json",
        "idempotent": true,
        "timeout_ms": { "connect": 5000, "read": 35000, "write": 5000 },
        "retry": {
          "enabled": true,
          "max_attempts": 10,
          "delays_ms": [0],
          "retryable_errors": ["ReadTimeout"]
        },
        "auth": "bearer_hf_token",
        "correlation_id": "jobId"
      }
    ],
    "unused_orpheus_endpoints": [
      "/diagnostics",
      "/queue/status",
      "/cache/stats",
      "/cache/warm",
      "/cache/clear",
      "/quality/evaluate",
      "/quality/ab-test",
      "/jobs/{id}/cancel"
    ]
  }
}
```

---

## C. Canonical Data Model: Musical DNA

### C.1 Current State: RoleProfile (40-field Static Aggregate)

The system does NOT currently have a per-16th-note time-series heuristic vector. Instead, it has **aggregate median statistics** from 222,497 MIDI files (1,844,218 tracks) stored as static `RoleProfile` instances per instrument role. These medians are computed offline and loaded at startup from `app/data/heuristics_v2.json`.

#### RoleProfile Fields (40 fields — NOT 63)

| # | Field | Type | Units | Range | Category |
|---|-------|------|-------|-------|----------|
| 1 | `rest_ratio` | float | ratio | [0, 1] | Silence & Density |
| 2 | `notes_per_bar` | float | count/bar | [0, ∞) | Silence & Density |
| 3 | `phrase_length_beats` | float | beats | [0, ∞) | Phrasing |
| 4 | `notes_per_phrase` | float | count | [0, ∞) | Phrasing |
| 5 | `phrase_regularity_cv` | float | CV | [0, ∞) | Phrasing |
| 6 | `note_length_entropy` | float | bits | [0, ∞) | Phrasing |
| 7 | `syncopation_ratio` | float | ratio | [0, 1] | Rhythm |
| 8 | `swing_ratio` | float | ratio | [0, 1] | Rhythm |
| 9 | `rhythm_trigram_repeat` | float | ratio | [0, 1] | Rhythm |
| 10 | `ioi_cv` | float | CV | [0, ∞) | Rhythm |
| 11 | `step_ratio` | float | ratio | [0, 1] | Melody & Pitch |
| 12 | `leap_ratio` | float | ratio | [0, 1] | Melody & Pitch |
| 13 | `repeat_ratio` | float | ratio | [0, 1] | Melody & Pitch |
| 14 | `pitch_class_entropy` | float | bits | [0, log2(12)] | Melody & Pitch |
| 15 | `contour_complexity` | float | norm | [0, 1] | Melody & Pitch |
| 16 | `interval_entropy` | float | bits | [0, ∞) | Melody & Pitch |
| 17 | `pitch_gravity` | float | norm | [0, 1] | Melody & Pitch |
| 18 | `climax_position` | float | ratio | [0, 1] | Melody & Pitch |
| 19 | `pitch_range_semitones` | float | semitones | [0, 127] | Melody & Pitch |
| 20 | `register_mean_pitch` | float | MIDI note | [0, 127] | Register |
| 21 | `register_low_ratio` | float | ratio | [0, 1] | Register |
| 22 | `register_mid_ratio` | float | ratio | [0, 1] | Register |
| 23 | `register_high_ratio` | float | ratio | [0, 1] | Register |
| 24 | `velocity_mean` | float | MIDI vel | [0, 127] | Dynamics |
| 25 | `velocity_range` | float | MIDI vel | [0, 127] | Dynamics |
| 26 | `velocity_stdev` | float | MIDI vel | [0, 127] | Dynamics |
| 27 | `velocity_entropy` | float | bits | [0, ∞) | Dynamics |
| 28 | `velocity_pitch_correlation` | float | r | [-1, 1] | Dynamics |
| 29 | `phrase_velocity_slope` | float | vel/beat | (-∞, ∞) | Dynamics |
| 30 | `accelerando_ratio` | float | ratio | [0, 1] | Tempo Tendency |
| 31 | `ritardando_ratio` | float | ratio | [0, 1] | Tempo Tendency |
| 32 | `staccato_ratio` | float | ratio | [0, 1] | Articulation |
| 33 | `legato_ratio` | float | ratio | [0, 1] | Articulation |
| 34 | `sustained_ratio` | float | ratio | [0, 1] | Articulation |
| 35 | `polyphony_mean` | float | voices | [1, ∞) | Polyphony |
| 36 | `pct_monophonic` | float | ratio | [0, 1] | Polyphony |
| 37 | `motif_pitch_trigram_repeat` | float | ratio | [0, 1] | Motif |
| 38 | `motif_direction_trigram_repeat` | float | ratio | [0, 1] | Motif |
| 39 | `orpheus_complexity` | float | norm | [0, 1] | Derived |
| 40 | `orpheus_density_hint` | str | enum | {"sparse", "moderate", "dense"} | Derived |

### C.2 What Crosses the Boundary

Of these 40 fields, only **3 pieces of information** actually reach Orpheus:

| RoleProfile Field(s) | Orpheus Parameter | Transformation | Information Loss |
|----------------------|-------------------|----------------|------------------|
| `contour_complexity` | `complexity` (float 0–1) | `min(1.0, contour_complexity)` | 39 other fields discarded |
| `rest_ratio > 0.4` | `musical_goals: ["breathing"]` | Boolean threshold | Continuous value lost |
| `pct_monophonic > 0.8` | `musical_goals: ["monophonic"]` | Boolean threshold | Continuous value lost |
| `motif_pitch_trigram_repeat > 0.85` | `musical_goals: ["repetitive"]` | Boolean threshold | Continuous value lost |
| `sustained_ratio > 0.03` | `musical_goals: ["sustained"]` | Boolean threshold | Continuous value lost |
| `syncopation_ratio > 0.5` | `musical_goals: ["syncopated"]` | Boolean threshold | Continuous value lost |

**FINDING: 87.5% information loss at the boundary** — 35 of 40 fields are never transmitted.

### C.3 SectionTelemetry (8-field Post-Generation Snapshot)

Maestro computes lightweight post-generation telemetry for cross-instrument awareness:

| Field | Type | Units | Range | Purpose |
|-------|------|-------|-------|---------|
| `energy_level` | float | norm | [0, 1] | normalized(velocity × density) |
| `density_score` | float | notes/beat | [0, ∞) | notes / beats |
| `groove_vector` | tuple[float, ...] | histogram | 16 bins, sum=1 | 16th-note onset distribution |
| `kick_pattern_hash` | str | MD5 | — | Drum pattern fingerprint |
| `rhythmic_complexity` | float | stdev | [0, ∞) | stddev of inter-onset intervals |
| `velocity_mean` | float | MIDI vel | [0, 127] | — |
| `velocity_variance` | float | MIDI vel² | [0, ∞) | — |

**FINDING:** SectionTelemetry does NOT cross the boundary — it's Maestro-internal for agent coordination.

### C.4 Proposed: MusicalDNASlice (Per-16th-Note Time-Series)

**STATUS: DOES NOT EXIST.** This is a design proposal for future implementation.

The aspirational 63-feature-per-16th-note vector would require:

1. **Time base computation:** At tempo T BPM, a 16th note = `60 / (T × 4)` seconds = `15 / T` seconds. Under tempo changes, use a `TempoMap` that maps absolute ticks to beat positions.

2. **Proposed schema (extending RoleProfile to time-series):**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "MusicalDNASlice",
  "description": "One 16th-note slice of musical feature vector. PROPOSED — NOT YET IMPLEMENTED.",
  "type": "object",
  "required": ["track_id", "time_index", "bar", "beat", "subdivision", "tempo", "time_signature", "features"],
  "properties": {
    "track_id": { "type": "string" },
    "segment_id": { "type": "string" },
    "time_index": { "type": "integer", "minimum": 0, "description": "0-based 16th-note index from track start" },
    "absolute_time_ms": { "type": "number", "minimum": 0 },
    "bar": { "type": "integer", "minimum": 0 },
    "beat": { "type": "integer", "minimum": 0, "maximum": 3, "description": "Beat within bar (0-indexed, 4/4 assumed)" },
    "subdivision": { "type": "integer", "minimum": 0, "maximum": 3, "description": "16th within beat (0-3)" },
    "tempo": { "type": "number", "minimum": 1, "description": "BPM at this slice" },
    "time_signature": { "type": "string", "default": "4/4" },
    "features": {
      "type": "array",
      "items": { "type": "number" },
      "minItems": 40,
      "maxItems": 63,
      "description": "Feature vector — indices correspond to RoleProfile field order"
    }
  }
}
```

**Introspection needed:** The 63 features mentioned in the original context likely refers to an offline analysis pipeline that is not yet integrated into the runtime system. To resolve:
- Check `scripts/analyze_midi.py` for the full feature extraction list
- Check `app/data/heuristics_v2.json` for the raw JSON structure
- Determine if features 41–63 exist in the analysis but were pruned from RoleProfile

---

## D. Canonical Data Model: Voice / Timbre Layer

### D.1 GM Program Resolution (Current Implementation)

Orpheus uses **0-indexed GM program numbers** (0–127). The mapping system has three layers:

| Layer | Function | Input | Output |
|-------|----------|-------|--------|
| Alias lookup | `resolve_gm_program(role)` | Natural language name | GM program 0–127, or None for drums |
| TMIDIX name | `resolve_tmidix_name(role)` | Natural language name | TMIDIX patch string |
| Channel index | `_resolve_melodic_index(role)` | Natural language name | Channel 0/1/2, or None for drums |

#### Channel Assignment Strategy

| Channel | Role | GM Program Range |
|---------|------|-----------------|
| 9 (MIDI ch10) | Drums/percussion | N/A (no program change) |
| 0 | Bass family | GM 32–39 |
| 1 | Piano/keys/organ | GM 0–7, 16–23 |
| 2+ | Everything else | GM 8–15, 24–31, 40–127 |

#### Alias Coverage

`_GM_ALIASES` contains **200+ mappings** including:
- Standard instruments (piano, guitar, bass, strings, brass, woodwinds)
- Electronic (synth lead, synth pad, synth bass)
- World instruments (sitar, koto, gayageum, kalimba, etc.)
- Percussion keywords in `_DRUM_KEYWORDS` (frozenset: drums, kick, snare, hi-hat, tabla, cajon, 808, etc.)

### D.2 Voice Object Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GMVoice",
  "type": "object",
  "required": ["gm_program_number", "name", "family"],
  "properties": {
    "gm_program_number": {
      "type": "integer",
      "minimum": 0,
      "maximum": 127,
      "description": "0-indexed General MIDI program number"
    },
    "name": {
      "type": "string",
      "description": "Canonical GM instrument name"
    },
    "family": {
      "type": "string",
      "enum": ["piano", "chromatic_percussion", "organ", "guitar", "bass", "strings", "ensemble", "brass", "reed", "pipe", "synth_lead", "synth_pad", "synth_effects", "ethnic", "percussive", "sound_effects"],
      "description": "GM instrument family (groups of 8)"
    },
    "channel_preference": {
      "type": ["integer", "null"],
      "description": "Preferred MIDI channel index (0=bass, 1=keys, 2=other, null=drums)"
    },
    "polyphony_constraint": {
      "type": "string",
      "enum": ["monophonic", "polyphonic", "unconstrained"],
      "description": "UNKNOWN — not currently modeled. Proposed."
    },
    "pitch_range": {
      "type": "object",
      "properties": {
        "low": { "type": "integer", "minimum": 0, "maximum": 127 },
        "high": { "type": "integer", "minimum": 0, "maximum": 127 }
      },
      "description": "UNKNOWN — not currently modeled. Proposed."
    }
  }
}
```

**FINDING:** Orpheus does NOT model polyphony constraints or pitch ranges per voice. It relies on the ML model to implicitly learn these. This is a gap for validation — Orpheus cannot verify that generated notes are within the playable range of the assigned instrument.

### D.3 OrchestrationDirective Schema

**STATUS: DOES NOT EXIST as a formal schema.** Orchestration is implicit:
- Maestro's LLM agent selects instruments and passes them as string names
- Orpheus resolves names to GM programs and channels
- No voicing rules, register constraints, or doubling directives cross the boundary

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "OrchestrationDirective",
  "description": "PROPOSED — not yet implemented",
  "type": "object",
  "properties": {
    "voices": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "role": { "type": "string" },
          "gm_program": { "type": "integer" },
          "register": {
            "type": "object",
            "properties": {
              "low": { "type": "integer" },
              "high": { "type": "integer" },
              "center": { "type": "integer" }
            }
          },
          "doubling": { "type": "string", "enum": ["none", "octave_up", "octave_down", "unison"] },
          "articulation_hints": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "voicing_rules": {
      "type": "object",
      "properties": {
        "max_spread_semitones": { "type": "integer" },
        "avoid_crossing": { "type": "boolean" },
        "root_position_bias": { "type": "number", "minimum": 0, "maximum": 1 }
      }
    }
  }
}
```

---

## E. Canonical Data Model: Emotion Vectors

### E.1 EmotionVector (Current Implementation)

| Axis | Range | Default | Semantics |
|------|-------|---------|-----------|
| `energy` | [0.0, 1.0] | 0.5 | stillness → explosive |
| `valence` | [-1.0, +1.0] | 0.0 | dark/sad → bright/joyful |
| `tension` | [0.0, 1.0] | 0.3 | resolved → unresolved/anxious |
| `intimacy` | [0.0, 1.0] | 0.5 | distant/epic → close/personal |
| `motion` | [0.0, 1.0] | 0.5 | static/sustained → driving/rhythmic |

### E.2 EmotionVector → Orpheus Mapping (Lossy)

| EmotionVector Axis | Orpheus Field | Transformation | Orpheus Range |
|-------------------|---------------|----------------|---------------|
| `valence` | `tone_brightness` | Direct: `valence` | [-1, +1] |
| `energy` | `energy_intensity` | Scale: `energy * 2.0 - 1.0` | [-1, +1] |
| `intimacy` | `tone_warmth` | Scale: `intimacy * 2.0 - 1.0` | [-1, +1] |
| `motion` | `energy_excitement` | Scale: `motion * 2.0 - 1.0` | [-1, +1] |
| **`tension`** | **DROPPED** | **Not mapped to any Orpheus field** | **N/A** |

**CRITICAL FINDING: The `tension` axis is lost at the boundary.** It only contributes indirectly through `musical_goals: ["tense"]` when `tension > 0.6`. Continuous tension information below that threshold is discarded.

### E.3 Musical Goals Derivation (Threshold-Based)

| Condition | Goal Added |
|-----------|-----------|
| `energy > 0.7` | `"energetic"` |
| `energy < 0.3` | `"sparse"` |
| `valence < -0.3` | `"dark"` |
| `valence > 0.3` | `"bright"` |
| `tension > 0.6` | `"tense"` |
| `intimacy > 0.7` | `"intimate"` |
| `motion > 0.7` | `"driving"` |
| `motion < 0.25` | `"sustained"` |
| `rest_ratio > 0.4` (role) | `"breathing"` |
| `pct_monophonic > 0.8` (role) | `"monophonic"` |
| `motif_pitch_trigram_repeat > 0.85` (role) | `"repetitive"` |
| `sustained_ratio > 0.03` (role) | `"sustained"` |
| `syncopation_ratio > 0.5` (role) | `"syncopated"` |

**FINDING:** Musical goals are an open vocabulary (any string is accepted by Orpheus). There is no validation, no canonical enumeration, and no guarantee that Orpheus's `generation_policy.py` handles all goals that Maestro can produce.

### E.4 EmotionVector JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "EmotionVector",
  "type": "object",
  "required": ["energy", "valence", "tension", "intimacy", "motion"],
  "properties": {
    "energy": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5 },
    "valence": { "type": "number", "minimum": -1.0, "maximum": 1.0, "default": 0.0 },
    "tension": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.3 },
    "intimacy": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5 },
    "motion": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5 }
  }
}
```

### E.5 EmotionArc (Temporal Emotion)

**STATUS: DOES NOT EXIST.** Emotion vectors are static per generation call. A generation call covers a single section (N bars). There is no mechanism to:
- Specify emotion curves within a section
- Ramp between emotions across bars
- Express conflicting or overlapping emotional arcs

**Proposed schema:**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "EmotionArc",
  "description": "PROPOSED — time-varying emotion for multi-bar sections",
  "type": "object",
  "properties": {
    "segments": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "start_bar": { "type": "integer" },
          "end_bar": { "type": "integer" },
          "start_emotion": { "$ref": "#/definitions/EmotionVector" },
          "end_emotion": { "$ref": "#/definitions/EmotionVector" },
          "interpolation": { "type": "string", "enum": ["linear", "ease_in", "ease_out", "step"] }
        }
      }
    },
    "conflict_resolution": {
      "type": "string",
      "enum": ["blend_average", "latest_wins", "highest_energy"],
      "default": "blend_average"
    }
  }
}
```

---

## F. Canonical Data Model: Intent Engine Contract

### F.1 Current Intent Flow

```
User text → Intent classification (pattern/LLM)
         → Slots extraction (action, target, amount, etc.)
         → STORI PROMPT parsing → EmotionVector
         → OrpheusBackend.generate() → maps to Orpheus fields
```

The intent engine classifies **30+ intents** (PLAY, STOP, TRACK_ADD, GENERATE_MUSIC, etc.) but only `GENERATE_MUSIC` (and composing-mode intents) reach Orpheus. The engine does NOT produce a structured `IntentSpec` — instead, intent is decomposed into:

1. **Tool selection** (which tools the agent can call)
2. **EmotionVector** (derived from STORI PROMPT)
3. **Free-form LLM reasoning** (the agent's system prompt includes role profiles and decides parameters)

### F.2 GenerationConstraints (Computed but NOT Sent to Orpheus)

Maestro computes `GenerationConstraints` from the EmotionVector:

| Field | Type | Range | Derived From |
|-------|------|-------|-------------|
| `drum_density` | float | [0.2, 1.0] | `energy × motion` |
| `subdivision` | int | {8, 16} | `motion > 0.6 → 16` |
| `swing_amount` | float | [0.0, 0.25] | `1 - tension` |
| `register_center` | int | [48, 72] | `(valence+1)/2` lerp |
| `register_spread` | int | [6, 18] | `energy` lerp |
| `rest_density` | float | [0.1, 0.4] | `1 - motion` |
| `leap_probability` | float | [0.1, 0.4] | `tension` lerp |
| `chord_extensions` | bool | — | `tension > 0.5` |
| `borrowed_chord_probability` | float | [0.0, 0.3] | `tension` lerp |
| `harmonic_rhythm_bars` | float | [0.5, 2.0] | `1 - energy` |
| `velocity_floor` | int | [40, 80] | `energy` lerp |
| `velocity_ceiling` | int | [80, 120] | `energy` lerp |

**CRITICAL FINDING:** These 12 constraint fields are computed in `emotion_vector.py:emotion_to_constraints()` but **never transmitted to Orpheus**. Orpheus has its own `generation_policy.py:intent_to_controls()` that independently derives similar values. The two derivations may diverge.

### F.3 Orpheus GenerationControlVector (Orpheus-Side)

Orpheus independently computes:

| Field | Type | Range | Purpose |
|-------|------|-------|---------|
| `creativity` | float | [0, 1] | Model temperature scaling |
| `density` | float | [0, 1] | Note density target |
| `complexity` | float | [0, 1] | Harmonic/rhythmic complexity |
| `brightness` | float | [0, 1] | Timbre brightness |
| `tension` | float | [0, 1] | Harmonic tension |
| `groove` | float | [0, 1] | Rhythmic groove strength |
| `section_type` | str? | — | Section context |
| `loopable` | bool | — | Loop point constraints |
| `build_intensity` | bool | — | Crescendo mode |
| `quality_preset` | str | {"fast", "balanced", "quality"} | Candidate count |

**FINDING:** Orpheus re-derives `tension` from musical goals, despite Maestro having computed it precisely. This is a **parallel derivation anti-pattern** — two independent transformations of the same semantic concept.

### F.4 FulfillmentReport

**STATUS: DOES NOT EXIST.** Orpheus returns raw notes without any assessment of how well they fulfilled the intent. Post-generation quality scoring exists (`rejection_score`, `analyze_quality`) but:
- Scores are NOT returned to Maestro
- No comparison against original intent
- No constraint violation reporting
- No explanation or uncertainty measures

### F.5 IntentSpec JSON Schema (Proposed)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "IntentSpec",
  "description": "PROPOSED — structured intent specification for Orpheus",
  "type": "object",
  "properties": {
    "goals": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "weight": { "type": "number", "minimum": 0, "maximum": 1 },
          "constraint_type": { "type": "string", "enum": ["hard", "soft"] }
        }
      }
    },
    "emotion_vector": { "$ref": "#/definitions/EmotionVector" },
    "generation_constraints": { "$ref": "#/definitions/GenerationConstraints" },
    "role_profile_summary": {
      "type": "object",
      "description": "Subset of RoleProfile fields relevant to generation"
    },
    "hierarchy_level": {
      "type": "string",
      "enum": ["global", "section", "bar"],
      "description": "Granularity of this intent"
    }
  }
}
```

---

## G. API Surface: Request/Response Schemas

### G.1 GenerateRequest (Wire Format)

This is the **actual payload** sent from Maestro to Orpheus:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GenerateRequest",
  "type": "object",
  "required": ["genre", "tempo", "instruments", "bars"],
  "properties": {
    "genre": {
      "type": "string",
      "default": "boom_bap",
      "description": "Genre/style for seed selection and policy",
      "examples": ["boom_bap", "trap", "house", "techno", "jazz", "neo_soul", "classical", "cinematic", "ambient", "reggae", "funk", "dnb", "dubstep", "drill", "lofi"]
    },
    "tempo": {
      "type": "integer",
      "minimum": 20,
      "maximum": 300,
      "default": 90,
      "description": "BPM"
    },
    "instruments": {
      "type": "array",
      "items": { "type": "string" },
      "default": ["drums", "bass"],
      "description": "Instrument role names (resolved via _GM_ALIASES)"
    },
    "bars": {
      "type": "integer",
      "minimum": 1,
      "maximum": 64,
      "default": 4,
      "description": "Number of bars to generate"
    },
    "key": {
      "type": ["string", "null"],
      "description": "Musical key (e.g. 'C', 'F#', 'Bb'). Null = model chooses."
    "emotion_vector": {
      "type": ["object", "null"],
      "properties": {
        "energy": {"type": "number", "minimum": 0, "maximum": 1},
        "valence": {"type": "number", "minimum": -1, "maximum": 1},
        "tension": {"type": "number", "minimum": 0, "maximum": 1},
        "intimacy": {"type": "number", "minimum": 0, "maximum": 1},
        "motion": {"type": "number", "minimum": 0, "maximum": 1}
      },
      "description": "Full 5-axis emotion vector from Maestro"
    },
    "role_profile_summary": {
      "type": ["object", "null"],
      "description": "12-field subset of RoleProfile heuristics (rest_ratio, syncopation_ratio, swing_ratio, pitch_range_semitones, contour_complexity, velocity_entropy, staccato_ratio, legato_ratio, sustained_ratio, motif_pitch_trigram_repeat, polyphony_mean, register_mean_pitch)"
    },
    "generation_constraints": {
      "type": ["object", "null"],
      "description": "12 hard control fields (drum_density, subdivision, register_center, register_spread, velocity_floor, velocity_ceiling, swing_amount, note_density, rest_density, articulation_bias, dynamic_range, rhythmic_complexity)"
    },
    "intent_goals": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "weight": {"type": "number", "default": 1.0},
          "constraint_type": {"type": "string", "enum": ["soft", "hard"], "default": "soft"}
        }
      },
      "description": "Structured intent goals with weights"
    },
    "quality_preset": {
      "type": "string",
      "enum": ["fast", "balanced", "quality"],
      "default": "balanced",
      "description": "Candidate count and critic usage"
    },
    "composition_id": {
      "type": ["string", "null"],
      "description": "Session continuity identifier"
    },
    "seed": {
      "type": ["integer", "null"],
      "description": "Deterministic seed for reproducible generation"
    },
    "trace_id": {
      "type": ["string", "null"],
      "description": "End-to-end trace ID (UUID)"
    },
    "intent_hash": {
      "type": ["string", "null"],
      "description": "Stable hash of the intent payload"
    },
    },
    "temperature": {
      "type": ["number", "null"],
      "description": "Advanced override: model temperature (0.0–2.0)"
    },
    "top_p": {
      "type": ["number", "null"],
      "description": "Advanced override: nucleus sampling threshold"
    }
  }
}
```

#### Minimal Example

```json
{
  "genre": "boom_bap",
  "tempo": 90,
  "instruments": ["drums"],
  "bars": 4
}
```

#### Maximal Example

```json
{
  "genre": "jazz",
  "tempo": 120,
  "instruments": ["piano", "bass", "drums"],
  "bars": 8,
  "key": "Bb",
  "emotion_vector": {
    "energy": 0.4, "valence": 0.2, "tension": 0.3,
    "intimacy": 0.8, "motion": 0.5
  },
  "role_profile_summary": {
    "rest_ratio": 0.15, "syncopation_ratio": 0.3, "swing_ratio": 0.6,
    "pitch_range_semitones": 24, "contour_complexity": 0.72,
    "velocity_entropy": 0.8, "staccato_ratio": 0.1, "legato_ratio": 0.5,
    "sustained_ratio": 0.3, "motif_pitch_trigram_repeat": 0.2,
    "polyphony_mean": 2.5, "register_mean_pitch": 60
  },
  "generation_constraints": {
    "drum_density": 0.6, "subdivision": 8, "register_center": 60,
    "register_spread": 24, "velocity_floor": 40, "velocity_ceiling": 110,
    "swing_amount": 0.6
  },
  "intent_goals": [
    {"name": "intimate", "weight": 1.0},
    {"name": "syncopated", "weight": 0.8}
  ],
  "quality_preset": "quality",
  "composition_id": "comp_abc123",
  "seed": 42,
  "trace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "intent_hash": "sha256:abc123",
  "temperature": 0.85,
  "top_p": 0.94
}
```

### G.2 SubmitResponse (Immediate)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SubmitResponse",
  "type": "object",
  "required": ["status"],
  "properties": {
    "jobId": { "type": "string" },
    "status": { "type": "string", "enum": ["queued", "complete"] },
    "position": { "type": "integer" },
    "result": {
      "description": "Present only when status=complete (cache hit)",
      "$ref": "#/definitions/GenerateResponse"
    }
  }
}
```

### G.3 GenerateResponse (Final Result)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GenerateResponse",
  "type": "object",
  "required": ["success"],
  "properties": {
    "success": { "type": "boolean" },
    "tool_calls": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "tool": {
            "type": "string",
            "enum": ["createProject", "addMidiTrack", "addMidiRegion", "addNotes", "addMidiCC", "addPitchBend", "addAftertouch"]
          },
          "params": { "type": "object" }
        }
      },
      "description": "DAW-style tool calls (legacy path)"
    },
    "notes": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "required": ["pitch", "startBeat", "durationBeats", "velocity"],
        "properties": {
          "pitch": { "type": "integer", "minimum": 0, "maximum": 127 },
          "startBeat": { "type": "number", "minimum": 0 },
          "durationBeats": { "type": "number", "minimum": 0 },
          "velocity": { "type": "integer", "minimum": 0, "maximum": 127 }
        }
      },
      "description": "MVP path: pre-flattened note list"
    },
    "error": { "type": ["string", "null"] },
    "metadata": {
      "type": ["object", "null"],
      "properties": {
        "policy_version": { "type": "string" },
        "cache_hit": { "type": "boolean" },
        "approximate": { "type": "boolean" },
        "fuzzy_distance": { "type": "number" },
        "generation_time_ms": { "type": "number" },
        "bars_requested": { "type": "integer" },
        "genre": { "type": "string" }
      }
    }
  }
}
```

#### Minimal Response

```json
{
  "success": true,
  "tool_calls": [],
  "notes": [
    {"pitch": 60, "startBeat": 0.0, "durationBeats": 1.0, "velocity": 80}
  ],
  "metadata": {"policy_version": "v2.0", "cache_hit": false}
}
```

#### Maximal Response

```json
{
  "success": true,
  "tool_calls": [
    {
      "tool": "addNotes",
      "params": {
        "notes": [
          {"pitch": 36, "startBeat": 0.0, "durationBeats": 0.5, "velocity": 100},
          {"pitch": 38, "startBeat": 1.0, "durationBeats": 0.25, "velocity": 90}
        ]
      }
    },
    {
      "tool": "addMidiCC",
      "params": {
        "cc": 11,
        "events": [
          {"beat": 0.0, "value": 80},
          {"beat": 2.0, "value": 110}
        ]
      }
    },
    {
      "tool": "addPitchBend",
      "params": {
        "events": [
          {"beat": 3.85, "value": -4096},
          {"beat": 4.0, "value": 0}
        ]
      }
    },
    {
      "tool": "addAftertouch",
      "params": {
        "events": [
          {"beat": 2.0, "value": 60, "pitch": 62}
        ]
      }
    }
  ],
  "notes": [
    {"pitch": 36, "startBeat": 0.0, "durationBeats": 0.5, "velocity": 100},
    {"pitch": 38, "startBeat": 1.0, "durationBeats": 0.25, "velocity": 90}
  ],
  "metadata": {
    "policy_version": "v2.0",
    "cache_hit": false,
    "approximate": false,
    "generation_time_ms": 12340,
    "bars_requested": 4,
    "genre": "boom_bap",
    "controls": {
      "creativity": 0.65,
      "density": 0.5,
      "complexity": 0.72,
      "brightness": 0.6,
      "tension": 0.4,
      "groove": 0.55
    }
  }
}
```

### G.4 Error Response

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ErrorResponse",
  "type": "object",
  "properties": {
    "success": { "type": "boolean", "const": false },
    "error": { "type": "string" },
    "message": { "type": "string" },
    "retry_count": { "type": "integer" }
  }
}
```

Known error codes (string values, not HTTP status):
- `"orpheus_circuit_open"` — circuit breaker tripped
- `"Orpheus service not available"` — connection refused
- `"Orpheus queue full"` — 503 after max retries
- `"Generation did not complete within Ns"` — poll timeout
- `"gpu_unavailable"` — GPU cold start / quota

### G.5 Normalized Output (Maestro-Side Adapter)

After `normalize_orpheus_tool_calls()`, Maestro works with:

```json
{
  "notes": [
    {"pitch": 60, "startBeat": 0.0, "durationBeats": 1.0, "velocity": 80}
  ],
  "cc_events": [
    {"cc": 11, "beat": 0.0, "value": 80}
  ],
  "pitch_bends": [
    {"beat": 3.85, "value": -4096}
  ],
  "aftertouch": [
    {"beat": 2.0, "value": 60, "pitch": 62}
  ]
}
```

Note field name asymmetry:
- Orpheus (snake_case): `start_beat`, `duration_beats`
- MVP path (camelCase): `startBeat`, `durationBeats`
- Maestro normalizes via `_normalize_note_keys()` and `_SNAKE_TO_CAMEL` mapping

---

## H. Expressiveness & Coverage Audit

### H.1 Vector Coverage Matrix

| # | Maestro Vector/Parameter | Orpheus Slot | Mapping Quality |
|---|-------------------------|-------------|----------------|
| 1 | `EmotionVector.energy` | `energy_intensity` | ✅ Direct (scaled) |
| 2 | `EmotionVector.valence` | `tone_brightness` | ✅ Direct |
| 3 | `EmotionVector.tension` | — | ❌ **DROPPED** (threshold only → "tense" goal) |
| 4 | `EmotionVector.intimacy` | `tone_warmth` | ✅ Direct (scaled) |
| 5 | `EmotionVector.motion` | `energy_excitement` | ✅ Direct (scaled) |
| 6 | `RoleProfile.rest_ratio` | `musical_goals` | ⚠️ Lossy (threshold → "breathing") |
| 7 | `RoleProfile.notes_per_bar` | — | ❌ Not transmitted |
| 8 | `RoleProfile.phrase_length_beats` | — | ❌ Not transmitted |
| 9 | `RoleProfile.notes_per_phrase` | — | ❌ Not transmitted |
| 10 | `RoleProfile.phrase_regularity_cv` | — | ❌ Not transmitted |
| 11 | `RoleProfile.note_length_entropy` | — | ❌ Not transmitted |
| 12 | `RoleProfile.syncopation_ratio` | `musical_goals` | ⚠️ Lossy (threshold → "syncopated") |
| 13 | `RoleProfile.swing_ratio` | — | ❌ Not transmitted |
| 14 | `RoleProfile.rhythm_trigram_repeat` | — | ❌ Not transmitted |
| 15 | `RoleProfile.ioi_cv` | — | ❌ Not transmitted |
| 16 | `RoleProfile.step_ratio` | — | ❌ Not transmitted |
| 17 | `RoleProfile.leap_ratio` | — | ❌ Not transmitted |
| 18 | `RoleProfile.repeat_ratio` | — | ❌ Not transmitted |
| 19 | `RoleProfile.pitch_class_entropy` | — | ❌ Not transmitted |
| 20 | `RoleProfile.contour_complexity` | `complexity` | ✅ Direct |
| 21 | `RoleProfile.interval_entropy` | — | ❌ Not transmitted |
| 22 | `RoleProfile.pitch_gravity` | — | ❌ Not transmitted |
| 23 | `RoleProfile.climax_position` | — | ❌ Not transmitted |
| 24 | `RoleProfile.pitch_range_semitones` | — | ❌ Not transmitted |
| 25 | `RoleProfile.register_mean_pitch` | — | ❌ Not transmitted |
| 26 | `RoleProfile.register_*_ratio` (×3) | — | ❌ Not transmitted |
| 27 | `RoleProfile.velocity_mean` | — | ❌ Not transmitted |
| 28 | `RoleProfile.velocity_range` | — | ❌ Not transmitted |
| 29 | `RoleProfile.velocity_stdev` | — | ❌ Not transmitted |
| 30 | `RoleProfile.velocity_entropy` | — | ❌ Not transmitted |
| 31 | `RoleProfile.velocity_pitch_correlation` | — | ❌ Not transmitted |
| 32 | `RoleProfile.phrase_velocity_slope` | — | ❌ Not transmitted |
| 33 | `RoleProfile.accelerando_ratio` | — | ❌ Not transmitted |
| 34 | `RoleProfile.ritardando_ratio` | — | ❌ Not transmitted |
| 35 | `RoleProfile.staccato_ratio` | — | ❌ Not transmitted |
| 36 | `RoleProfile.legato_ratio` | — | ❌ Not transmitted |
| 37 | `RoleProfile.sustained_ratio` | `musical_goals` | ⚠️ Lossy (threshold → "sustained") |
| 38 | `RoleProfile.polyphony_mean` | — | ❌ Not transmitted |
| 39 | `RoleProfile.pct_monophonic` | `musical_goals` | ⚠️ Lossy (threshold → "monophonic") |
| 40 | `RoleProfile.motif_pitch_trigram_repeat` | `musical_goals` | ⚠️ Lossy (threshold → "repetitive") |
| 41 | `RoleProfile.motif_direction_trigram_repeat` | — | ❌ Not transmitted |
| 42 | `GenerationConstraints.drum_density` | — | ❌ Computed but not sent |
| 43 | `GenerationConstraints.subdivision` | — | ❌ Computed but not sent |
| 44 | `GenerationConstraints.swing_amount` | — | ❌ Computed but not sent |
| 45 | `GenerationConstraints.register_center` | — | ❌ Computed but not sent |
| 46 | `GenerationConstraints.register_spread` | — | ❌ Computed but not sent |
| 47 | `GenerationConstraints.rest_density` | — | ❌ Computed but not sent |
| 48 | `GenerationConstraints.leap_probability` | — | ❌ Computed but not sent |
| 49 | `GenerationConstraints.chord_extensions` | — | ❌ Computed but not sent |
| 50 | `GenerationConstraints.borrowed_chord_probability` | — | ❌ Computed but not sent |
| 51 | `GenerationConstraints.harmonic_rhythm_bars` | — | ❌ Computed but not sent |
| 52 | `GenerationConstraints.velocity_floor` | — | ❌ Computed but not sent |
| 53 | `GenerationConstraints.velocity_ceiling` | — | ❌ Computed but not sent |

### H.2 Coverage Summary

| Category | Direct | Lossy | Dropped | Total |
|----------|--------|-------|---------|-------|
| EmotionVector (5 axes) | 4 | 0 | **1** | 5 |
| RoleProfile (40 fields) | 1 | 5 | **34** | 40 |
| GenerationConstraints (12 fields) | 0 | 0 | **12** | 12 |
| **Total** | **5** | **5** | **47** | **57** |

**Coverage rate: 8.8% direct, 8.8% lossy, 82.4% dropped.**

### H.3 Collisions

| Maestro Concept A | Maestro Concept B | Orpheus Slot | Collision |
|-------------------|-------------------|-------------|-----------|
| `emotion.motion > 0.7` | `role.sustained_ratio > 0.03` | `musical_goals` | Both can produce `"sustained"` — but with opposite semantics. High motion → "driving" NOT "sustained", but role's `sustained_ratio > 0.03` adds "sustained" regardless. |
| `RoleProfile.orpheus_density_hint` | (never sent) | — | Computed but never transmitted. |

### H.4 Remediation Plan

| Priority | Action | Effort | Impact | Status |
|----------|--------|--------|--------|--------|
| P0 | Add `tension` float to GenerateRequest | S | Recovers dropped emotion axis | **DONE** — full `emotion_vector` |
| P1 | Add `role_profile` summary object to GenerateRequest | M | Recovers 25% of dropped information | **DONE** — 12-field `role_profile_summary` |
| P1 | Forward `GenerationConstraints` to Orpheus | M | Eliminates parallel derivation | **DONE** — `generation_constraints` block |
| P2 | Add `seed` parameter for deterministic generation | S | Enables reproducibility | **DONE** |
| P2 | Add `trace_id` to request headers | S | End-to-end observability | **DONE** — `trace_id` + `intent_hash` |
| P3 | Add FulfillmentReport to GenerateResponse | L | Intent verification | **DONE** — `fulfillment_report` in metadata |
| P3 | Design EmotionArc for temporal emotion | L | New capability | OPEN |

---

## I. Contract Test Suite

### I.1 Golden Tests

| Test ID | Name | Description |
|---------|------|-------------|
| G1 | `test_minimal_request_accepted` | Minimal payload (genre, tempo, instruments, bars) succeeds |
| G2 | `test_maximal_request_accepted` | All fields populated succeeds |
| G3 | `test_response_has_notes_or_tool_calls` | Successful response contains `notes` and/or `tool_calls` |
| G4 | `test_note_schema_valid` | Every note has pitch [0-127], startBeat ≥ 0, durationBeats > 0, velocity [0-127] |
| G5 | `test_notes_within_bar_range` | All note startBeats < bars × 4 |
| G6 | `test_normalize_tool_calls_roundtrip` | `addNotes` → `normalize_orpheus_tool_calls` → notes match |
| G7 | `test_emotion_to_orpheus_mapping` | EmotionVector axes correctly map to Orpheus fields |
| G8 | `test_tension_dropped_warning` | Verify tension > 0 does NOT appear in request (documents the gap) |

### I.2 Property-Based Tests

| Test ID | Name | Invariant |
|---------|------|-----------|
| P1 | `test_pitch_range_valid` | ∀ note: 0 ≤ pitch ≤ 127 |
| P2 | `test_velocity_range_valid` | ∀ note: 0 ≤ velocity ≤ 127 |
| P3 | `test_timing_non_negative` | ∀ note: startBeat ≥ 0 ∧ durationBeats > 0 |
| P4 | `test_cc_value_range` | ∀ cc_event: 0 ≤ value ≤ 127 |
| P5 | `test_pitch_bend_range` | ∀ pb: -8192 ≤ value ≤ 8191 |
| P6 | `test_emotion_vector_clamping` | EmotionVector clamps axes to valid ranges |
| P7 | `test_tone_brightness_equals_valence` | tone_brightness == emotion_vector.valence |
| P8 | `test_energy_intensity_scaling` | energy_intensity == energy * 2.0 - 1.0 |
| P9 | `test_musical_goals_deterministic` | Same EmotionVector + RoleProfile → same goals |
| P10 | `test_notes_sorted_by_start` | Output notes are sorted by startBeat (if contract requires) |

### I.3 Performance Tests

| Test ID | Name | SLA |
|---------|------|-----|
| PF1 | `test_generation_under_5min` | Total time (submit + poll) < 300s |
| PF2 | `test_cache_hit_under_1s` | Repeated identical request < 1000ms |
| PF3 | `test_64_bar_generation` | 64-bar request completes without OOM |
| PF4 | `test_normalize_tool_calls_performance` | 10,000 notes normalized in < 100ms |

### I.4 Compatibility Tests

| Test ID | Name | Description |
|---------|------|-------------|
| C1 | `test_unknown_fields_ignored` | Extra fields in request don't cause errors |
| C2 | `test_missing_optional_fields_ok` | Omitting optional fields uses defaults |
| C3 | `test_snake_case_and_camel_case_notes` | Both `start_beat` and `startBeat` accepted |
| C4 | `test_legacy_tool_calls_path` | Responses with only `tool_calls` (no `notes`) still work |
| C5 | `test_mvp_notes_path` | Responses with `notes` directly still work |

### I.5 Test Suite JSON

```json
{
  "test_suite": {
    "version": "1.0.0",
    "test_categories": {
      "golden": {
        "count": 8,
        "tests": [
          {
            "id": "G1",
            "name": "test_minimal_request_accepted",
            "input": {
              "genre": "boom_bap",
              "tempo": 90,
              "instruments": ["drums"],
              "bars": 4
            },
            "expected": {
              "success": true,
              "notes_not_empty": true
            }
          },
          {
            "id": "G4",
            "name": "test_note_schema_valid",
            "assertion": "for_all_notes",
            "invariants": [
              "0 <= pitch <= 127",
              "startBeat >= 0",
              "durationBeats > 0",
              "0 <= velocity <= 127"
            ]
          },
          {
            "id": "G7",
            "name": "test_emotion_to_orpheus_mapping",
            "input_emotion": {"energy": 0.8, "valence": -0.5, "tension": 0.9, "intimacy": 0.3, "motion": 0.7},
            "expected_orpheus_fields": {
              "tone_brightness": -0.5,
              "energy_intensity": 0.6,
              "tone_warmth": -0.4,
              "energy_excitement": 0.4
            },
            "expected_goals_include": ["energetic", "dark", "tense", "driving"],
            "KNOWN_GAP": "tension=0.9 only contributes 'tense' goal, continuous value lost"
          }
        ]
      },
      "property_based": {
        "count": 10,
        "framework": "hypothesis"
      },
      "performance": {
        "count": 4,
        "requires": "running_orpheus_service"
      },
      "compatibility": {
        "count": 5
      }
    }
  }
}
```

---

## J. Observability & Safety

### J.1 Required Metrics

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `orpheus_request_total` | counter | `status={success,failure,circuit_open}` | Request volume |
| `orpheus_request_duration_seconds` | histogram | `phase={submit,poll,total}` | Latency profile |
| `orpheus_circuit_breaker_state` | gauge | `state={closed,open,half_open}` | Availability |
| `orpheus_retry_total` | counter | `reason={timeout,503,error}` | Retry burden |
| `orpheus_notes_generated` | histogram | — | Output volume per request |
| `orpheus_cache_hit_total` | counter | `type={exact,fuzzy}` | Cache effectiveness |
| `orpheus_vector_coverage_pct` | gauge | — | % of Maestro vectors that reach Orpheus |
| `orpheus_emotion_tension_dropped` | counter | — | Tracks how often tension > 0 is lost |

### J.2 Required Structured Log Fields

| Field | Source | Present? |
|-------|--------|----------|
| `composition_id` | Maestro request | ✅ Yes (optional) |
| `job_id` | Orpheus submit response | ✅ Yes |
| `trace_id` | Maestro request | ✅ Yes |
| `intent_hash` | Maestro request | ✅ Yes |
| `seed` | Maestro request | ✅ Yes (optional) |
| `emotion_vector` | Maestro request | ✅ Yes |
| `quality_preset` | Request payload | ✅ Yes |
| `genre` | Request payload | ✅ Yes |
| `instruments` | Request payload | ✅ Yes |
| `policy_version` | Orpheus response metadata | ✅ Yes |
| `seed_provenance` | Orpheus response metadata | ✅ Yes (source, hash, notes, tokens) |

### J.3 Redaction Policy

| Data Type | Contains PII? | Redaction |
|-----------|---------------|-----------|
| `composition_id` | No (UUID) | None |
| `trace_id` | No (UUID) | None |
| `intent_hash` | No (hash) | None |
| `intent_goals` | No | None |
| `emotion_vector` | No | None |
| STORI PROMPT text | Potentially (user creative brief) | Do not log raw prompt text at INFO or lower. Redact in production telemetry. |
| HF Bearer token | Yes (API key) | Never log. Already handled by httpx. |

---

## K. Consolidated JSON Artifact

The complete machine-readable contract follows. All JSON is valid and parseable.

```json
{
  "contract_version": "1.0.0",
  "generated_at": "2026-02-24",
  "boundary_map": {
    "boundaries": [
      {
        "id": "B1",
        "name": "HealthCheck",
        "direction": "maestro_to_orpheus",
        "method": "GET",
        "path": "/health",
        "sync": true,
        "transport": "http_json",
        "idempotent": true,
        "timeout_ms": 3000,
        "auth": "none"
      },
      {
        "id": "B2",
        "name": "SubmitGeneration",
        "direction": "maestro_to_orpheus",
        "method": "POST",
        "path": "/generate",
        "sync": false,
        "transport": "http_json",
        "idempotent": false,
        "timeout_ms": {"connect": 5000, "read": 10000, "write": 10000},
        "retry": {"max_attempts": 4, "delays_ms": [2000, 5000, 10000, 20000]},
        "circuit_breaker": {"threshold": 3, "cooldown_s": 120},
        "concurrency_limit": 2,
        "auth": "bearer_hf_token"
      },
      {
        "id": "B3",
        "name": "PollJob",
        "direction": "maestro_to_orpheus",
        "method": "GET",
        "path": "/jobs/{jobId}/wait",
        "sync": true,
        "transport": "http_json",
        "idempotent": true,
        "timeout_ms": {"connect": 5000, "read": 35000},
        "retry": {"max_attempts": 10},
        "auth": "bearer_hf_token"
      }
    ],
    "unused_endpoints": [
      "GET /diagnostics",
      "GET /queue/status",
      "GET /cache/stats",
      "POST /cache/warm",
      "DELETE /cache/clear",
      "POST /quality/evaluate",
      "POST /quality/ab-test",
      "POST /jobs/{id}/cancel"
    ],
    "callbacks_orpheus_to_maestro": []
  },
  "schemas": {
    "GenerateRequest": {
      "required": ["genre", "tempo", "instruments", "bars"],
      "fields": {
        "genre": {"type": "string", "default": "boom_bap"},
        "tempo": {"type": "integer", "range": [20, 300], "default": 90},
        "instruments": {"type": "array<string>", "default": ["drums", "bass"]},
        "bars": {"type": "integer", "range": [1, 64], "default": 4},
        "key": {"type": "string?", "default": null},
        "emotion_vector": {"type": "EmotionVectorPayload?", "default": null},
        "role_profile_summary": {"type": "RoleProfileSummary?", "default": null},
        "generation_constraints": {"type": "GenerationConstraintsPayload?", "default": null},
        "intent_goals": {"type": "array<IntentGoal>?", "default": null},
        "quality_preset": {"type": "enum", "values": ["fast", "balanced", "quality"], "default": "balanced"},
        "composition_id": {"type": "string?", "default": null},
        "seed": {"type": "integer?", "default": null},
        "trace_id": {"type": "string?", "default": null},
        "intent_hash": {"type": "string?", "default": null},
        "temperature": {"type": "float?", "default": null},
        "top_p": {"type": "float?", "default": null}
      }
    },
    "GenerateResponse": {
      "fields": {
        "success": {"type": "boolean", "required": true},
        "tool_calls": {"type": "array<ToolCall>", "default": []},
        "notes": {"type": "array<Note>?", "default": null},
        "error": {"type": "string?", "default": null},
        "metadata": {"type": "object?", "default": null}
      }
    },
    "Note": {
      "fields": {
        "pitch": {"type": "integer", "range": [0, 127]},
        "startBeat": {"type": "float", "minimum": 0},
        "durationBeats": {"type": "float", "minimum": 0},
        "velocity": {"type": "integer", "range": [0, 127]}
      },
      "note": "Also accepted as snake_case: start_beat, duration_beats"
    },
    "ToolCall": {
      "fields": {
        "tool": {"type": "string", "enum": ["addNotes", "addMidiCC", "addPitchBend", "addAftertouch"]},
        "params": {"type": "object"}
      }
    },
    "EmotionVector": {
      "fields": {
        "energy": {"type": "float", "range": [0, 1], "default": 0.5},
        "valence": {"type": "float", "range": [-1, 1], "default": 0.0},
        "tension": {"type": "float", "range": [0, 1], "default": 0.3},
        "intimacy": {"type": "float", "range": [0, 1], "default": 0.5},
        "motion": {"type": "float", "range": [0, 1], "default": 0.5}
      }
    },
    "EmotionToOrpheusMapping": {
      "mappings": [
        {"from": "valence", "to": "tone_brightness", "transform": "identity"},
        {"from": "energy", "to": "energy_intensity", "transform": "x * 2.0 - 1.0"},
        {"from": "intimacy", "to": "tone_warmth", "transform": "x * 2.0 - 1.0"},
        {"from": "motion", "to": "energy_excitement", "transform": "x * 2.0 - 1.0"},
        {"from": "tension", "to": null, "transform": "DROPPED"}
      ]
    },
    "RoleProfile": {
      "field_count": 40,
      "fields_crossing_boundary": 6,
      "fields_dropped": 34,
      "coverage_pct": 15.0
    },
    "GenerationConstraints": {
      "field_count": 12,
      "fields_crossing_boundary": 0,
      "status": "COMPUTED_BUT_NEVER_SENT"
    }
  },
  "api_spec": {
    "endpoints": [
      {
        "name": "SubmitGeneration",
        "method": "POST",
        "path": "/generate",
        "request_schema": "GenerateRequest",
        "response_schema": "SubmitResponse",
        "error_schema": "ErrorResponse",
        "versioning": "NONE",
        "streaming": false,
        "deterministic_mode": "NOT_SUPPORTED"
      },
      {
        "name": "PollJob",
        "method": "GET",
        "path": "/jobs/{jobId}/wait",
        "request_schema": {"params": {"timeout": "integer"}},
        "response_schema": "JobStatusResponse",
        "error_schema": "ErrorResponse"
      },
      {
        "name": "HealthCheck",
        "method": "GET",
        "path": "/health",
        "response_schema": {"status": "string", "service": "string"}
      }
    ],
    "proposed_endpoints": [
      "POST /generate/deterministic — with seed parameter",
      "GET /schema/version — schema version negotiation",
      "POST /analyze — ingest MIDI back into Musical DNA (round-trip)",
      "GET /diagnostics — already exists, not used by Maestro"
    ]
  },
  "coverage_audit": {
    "total_maestro_vectors": 57,
    "direct_mapping": 5,
    "lossy_mapping": 5,
    "not_supported": 47,
    "coverage_pct": 17.5,
    "critical_gaps": [
      {
        "field": "EmotionVector.tension",
        "status": "DROPPED",
        "impact": "Continuous tension information lost; only threshold at 0.6 preserved as goal",
        "remediation": "Add tension float to GenerateRequest"
      },
      {
        "field": "GenerationConstraints (all 12)",
        "status": "COMPUTED_NOT_SENT",
        "impact": "Parallel derivation in Orpheus may diverge from Maestro's computation",
        "remediation": "Forward constraints or unify derivation on one side"
      }
    ],
    "collisions": [
      {
        "fields": ["emotion.motion", "role.sustained_ratio"],
        "target": "musical_goals",
        "issue": "Both can produce 'sustained' with different semantics"
      }
    ]
  },
  "observability": {
    "metrics": [
      {"name": "orpheus_request_total", "type": "counter", "labels": ["status"]},
      {"name": "orpheus_request_duration_seconds", "type": "histogram", "labels": ["phase"]},
      {"name": "orpheus_circuit_breaker_state", "type": "gauge", "labels": ["state"]},
      {"name": "orpheus_notes_generated", "type": "histogram"},
      {"name": "orpheus_cache_hit_total", "type": "counter", "labels": ["type"]}
    ],
    "required_log_fields": [
      "composition_id", "job_id", "trace_id", "intent_hash",
      "emotion_vector", "quality_preset", "genre", "instruments", "policy_version",
      "seed_provenance"
    ],
    "missing_fields": []
  },
  "unknowns": [
    {
      "id": "U1",
      "description": "Are there 63 heuristic features or 40? The original spec mentions 63 but only 40 exist in RoleProfile.",
      "resolution": "Inspect scripts/analyze_midi.py and heuristics_v2.json for the full feature list. Check if 23 features were pruned or were never extracted.",
      "proposed_options": ["A: 40 is the true count", "B: 23 features exist in raw JSON but not in RoleProfile", "C: 63 was aspirational"]
    },
    {
      "id": "U2",
      "description": "Does Orpheus's generation_policy.py handle all musical_goals that Maestro can produce?",
      "resolution": "Enumerate all goals in generation_policy.py and cross-reference with OrpheusBackend + RoleProfile goal production. Check for unhandled goals.",
      "proposed_options": ["A: All goals handled", "B: Some goals silently ignored"]
    },
    {
      "id": "U3",
      "description": "Can Orpheus's fuzzy cache produce musically incoherent results when epsilon is too large?",
      "resolution": "Test with epsilon=0.35 and extreme emotional deltas. Listen to matched results.",
      "proposed_options": ["A: Epsilon 0.35 is safe", "B: Reduce to 0.2", "C: Add perceptual distance metric"]
    },
    {
      "id": "U4",
      "description": "What is the maximum practical bar count before Orpheus OOMs or produces degenerate output?",
      "resolution": "Stress test with bars=16, 32, 64, 128 and measure token budget, memory, and output quality.",
      "proposed_options": ["A: 64 bars is the hard limit (MAX_GEN_TOKENS)", "B: Quality degrades after 16 bars"]
    },
    {
      "id": "U5",
      "description": "Are GM voice polyphony constraints and pitch ranges needed for output validation?",
      "resolution": "Analyze generated output for out-of-range notes per instrument. If >5% are unrealistic, add constraints.",
      "proposed_options": ["A: Model handles implicitly (good enough)", "B: Add validation layer"]
    },
    {
      "id": "U6",
      "description": "Is the beat rescaling logic (ENABLE_BEAT_RESCALING) still needed or was it a workaround?",
      "resolution": "Check if any production generation requires rescaling. If disabled by default and no issues reported, consider removing.",
      "proposed_options": ["A: Remove dead code", "B: Keep as safety net"]
    },
    {
      "id": "U7",
      "description": "Do the 200+ _GM_ALIASES in Orpheus exactly match the instrument names that Maestro agents produce?",
      "resolution": "Log every instrument name Maestro sends over 1 week and check alias resolution success rate.",
      "proposed_options": ["A: Perfect coverage", "B: Some names fall through to default channel"]
    }
  ]
}
```

---

## Appendix: Introspection Plan

To resolve all UNKNOWNs, instrument the following:

| # | What to Instrument | Where | Output |
|---|-------------------|-------|--------|
| 1 | Log every `musical_goals` list at OrpheusBackend boundary | `app/services/backends/orpheus.py:generate()` | Canonical goal vocabulary |
| 2 | Log every instrument name pre-resolution | `orpheus-music/music_service.py:resolve_gm_program()` | Alias coverage report |
| 3 | Log `GenerationConstraints` vs Orpheus `GenerationControlVector` side-by-side | Both services | Divergence report |
| 4 | Add `X-Trace-ID` header to Orpheus requests | `app/services/orpheus.py:generate()` | End-to-end trace stitching |
| 5 | Log emotion vector at DEBUG before Orpheus mapping | Already present | Confirm no silent clipping |
| 6 | Count features in `heuristics_v2.json` per role | One-time script | Resolve 40 vs 63 question |
| 7 | Log `rejection_score` from Orpheus to Maestro via metadata | `orpheus-music/music_service.py` | Quality observability |
