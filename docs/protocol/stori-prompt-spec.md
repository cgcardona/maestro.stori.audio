# Stori Structured Prompt — Format Specification

> The Stori Structured Prompt is the interface between human musical intent
> and an AI Maestro that knows everything about music theory, composition,
> melody, rhythm, voicings, orchestration, and expression.
>
> Think of it as a **musical score notation for the age of AI** — as precise
> as you need, as expressive as a maestro's imagination.

------------------------------------------------------------------------

## Philosophy

Music is a multifaceted jewel. The Stori Structured Prompt is designed to
honour every facet:

- **Arrangement** — where in time
- **Harmony** — what chords and voicings
- **Melody** — contour, register, phrase structure
- **Rhythm** — feel, subdivision, groove
- **Dynamics** — velocity arcs, automation curves
- **Orchestration** — instrument assignment, articulation, technique
- **Effects** — reverb character, saturation, compression shape
- **Expression** — emotional arc, narrative, tension/release
- **Texture** — density, register spread, layering
- **Form** — large-scale structure and development

Natural language remains fully valid. The structured format exists to
remove ambiguity and unlock dimensions of control that free text cannot
reliably convey.

------------------------------------------------------------------------

## Format

The Stori Structured Prompt is a **YAML document with a sentinel header**.

```
STORI PROMPT
<YAML body>
```

The sentinel line `STORI PROMPT` (case-insensitive) is not YAML — it is
a recognisable trigger that no natural language prompt would begin with.
Everything after it is a standard YAML document.

**Why YAML?**

| Property | Benefit |
|---|---|
| Human-readable | Composers can write it in a chat field |
| Infinitely nestable | Supports `Harmony.voicing.extensions` |
| Native lists and dicts | No special syntax for arrays |
| Block scalars (`\|`, `>`) | Multi-line Request text preserved |
| Comments (`#`) | Composers can annotate their intent |
| Type coercion | `Tempo: 75` stays an integer, not a string |
| Universally understood | Any LLM already speaks YAML |

------------------------------------------------------------------------

## Routing Fields

These fields are parsed deterministically by the server. They drive
intent classification, arrangement positioning, and planner routing.
All other fields are **Maestro Dimensions** (see below).

```yaml
STORI PROMPT
Mode:         # required — compose | edit | ask
Section:      # optional — names this prompt's output section
Position:     # optional — arrangement placement (see below)
Target:       # optional — project | selection | track:<name> | region:<name>
Style:        # optional — e.g. "lofi hip hop", "melodic techno"
Key:          # optional — e.g. "Cm", "F#m", "D dorian"
Tempo:        # optional — integer BPM
Role:         # optional — list of musical roles
Constraints:  # optional — key-value boundary hints
Vibe:         # optional — producer idiom lexicon (weighted)
Request:      # required — natural language description of intent
```

Only **Mode** and **Request** are required. Every other field is optional.

------------------------------------------------------------------------

## Routing Field Reference

### Mode

Hard routing signal — overrides fuzzy intent classification.

```yaml
Mode: compose   # Creates new musical material
Mode: edit      # Modifies existing tracks, regions, or project state
Mode: ask       # Reasoning only — no mutations
```

------------------------------------------------------------------------

### Section

Names the output of this prompt for future `Position:` references.

```yaml
Section: intro
Section: verse
Section: chorus
Section: bridge
Section: breakdown
Section: outro
Section: pre-chorus fill
```

------------------------------------------------------------------------

### Position

Arrangement placement using a CSS-pseudo-selector-style vocabulary.
The server resolves Position to a concrete beat number from the project
state and injects it into the Maestro context — no manual beat math needed.

**Relationships:**

| Form | Musical meaning |
|---|---|
| `last` | Append after all existing content |
| `after <section>` | Sequential — start after named section ends |
| `before <section>` | Insert before named section begins |
| `before <section> - N` | Anticipatory pickup N beats before section |
| `alongside <section>` | Parallel layer — same start beat as section |
| `alongside <section> + N` | Parallel layer, N beats late |
| `between <A> <B>` | Transition bridge — fill gap between A and B |
| `within <section> bar N` | N bars into named section |
| `at <beat>` | Absolute beat number |
| `at bar <N>` | Bar reference (bar 1 = beat 0, 4/4 assumed) |

**Offset operator** (`+` / `-` in beats):

```yaml
Position: before chorus - 4    # 4-beat pickup into the drop
Position: after intro + 2      # 2-beat breathing room
Position: alongside verse + 8  # enters 8 beats into the verse
```

**Frontend requirement:** Pass the current project state in `project`
when submitting the request. Empty `project: {}` resolves to beat 0.

**Backwards compatibility:** `After: intro` is equivalent to
`Position: after intro`. `After:` remains fully supported.

------------------------------------------------------------------------

### Target

Maps to MCP tool scope.

```yaml
Target: project
Target: selection
Target: track:Bass
Target: region:Verse Melody
```

------------------------------------------------------------------------

### Style, Key, Tempo

Prevent planner inference loops. Used directly in generation.

```yaml
Style: boom bap hip hop
Key: F#m
Key: D dorian
Tempo: 92
```

------------------------------------------------------------------------

### Role

Explicit musical responsibilities for planner routing. List or inline.

```yaml
Role: [drums, bass, melody]

Role:
  - kick
  - sub bass
  - arp
  - pad
```

------------------------------------------------------------------------

### Constraints

Planner and MCP boundary hints.

```yaml
Constraints:
  bars: 8
  density: sparse
  no reverb: true
  gm_program: 38
```

------------------------------------------------------------------------

### Vibe

Producer idiom lexicon. Weighted entries bias parameter selection.

```yaml
Vibe: [dusty x3, warm x2, laid back]

Vibe:
  - darker: 2
  - hypnotic: 3
  - wider: 1
```

------------------------------------------------------------------------

### Request

Natural language. Fully expressive. This is the Maestro's brief.
Use YAML block scalar for multi-line:

```yaml
Request: |
  Floating atmospheric intro — warm pad chords drifting through
  Cm-Ab-Eb-Bb and a wistful piano melody with plenty of space,
  like looking out a rainy window at night.
```

------------------------------------------------------------------------

## Maestro Dimensions

Everything beyond the routing fields. The server passes these verbatim
into the Maestro LLM system prompt as structured YAML. The Maestro
interprets and applies every dimension — no Python parsing required.

This means the vocabulary is **open and forwards-compatible**. New
dimensions you invent today work immediately.

------------------------------------------------------------------------

### Harmony

Chord progressions, voicings, extensions, reharmonization, tension.

```yaml
Harmony:
  progression: [Cm7, Abmaj7, Ebmaj7, Bb7]
  voicing: rootless, close position
  rhythm: half-note stabs on beats 1 and 3
  extensions: 9ths throughout, avoid 3rds in bass
  color: bittersweet — major 7ths over minor roots
  reharmonize: substitute V7 with bVII7 in bar 4
  tension:
    point: bar 7
    device: tritone substitution
    release: beat 1 bar 8
```

------------------------------------------------------------------------

### Melody

Scale/mode, contour, register, phrasing, ornamentation, voice leading.

```yaml
Melody:
  scale: C dorian
  register: upper mid (C5–C6)
  contour: arch — rises to peak at bar 5, resolves downward
  phrases:
    structure: 4 + 4 bars
    feel: call and response
    breath: leave 2 beats of space at end of each phrase
  density: sparse — notes on average every 1.5 beats
  ornamentation:
    - grace notes before strong beats
    - occasional blue note (bVII)
  voice_leading:
    - stepwise motion preferred
    - no parallel fifths
    - resolve leading tones
```

------------------------------------------------------------------------

### Rhythm

Groove feel, subdivision, swing ratio, polyrhythm, accent patterns.

```yaml
Rhythm:
  feel: behind the beat (laid back)
  subdivision: 16th-note feel
  swing: 55%  # 50% = straight, 67% = heavy swing
  accent:
    pattern: on the "e" of beats 2 and 4
    weight: subtle — velocity ±10
  polyrhythm:
    bass: quarter notes (4 against)
    melody: 8th-note triplets (3 against)
  ghost_notes:
    instrument: snare
    velocity: 30–45
    frequency: every 3rd 16th note
  pushed_hits:
    - beat: 3.5
      anticipation: 16th note early
```

------------------------------------------------------------------------

### Dynamics

Velocity arcs, automation, crescendo/decrescendo, accent shaping.

```yaml
Dynamics:
  overall: piano to mezzoforte (pp–mf)
  arc:
    - bars: 1–4
      level: pp
      shape: flat
    - bars: 5–6
      level: pp → mf
      shape: exponential crescendo
    - bars: 7–8
      level: mf
      shape: flat with accents
  accent_velocity: 95
  ghost_velocity: 35
  automation:
    - param: filter_cutoff
      from: 400hz
      to: 2.5khz
      position: bars 5–8
      curve: exponential
    - param: volume
      from: 0.4
      to: 0.85
      position: bars 1–4
      curve: linear
```

------------------------------------------------------------------------

### Orchestration

Instrument assignment, technique, articulation, doublings, counterpoint.

```yaml
Orchestration:
  drums:
    kit: boom bap (tr-808 kick, jazz snare)
    hi_hat: slightly open on the "and"s
    kick: slightly late (groovy, not mechanical)
    brush_snare: ghost roll in bar 4
  bass:
    technique: finger style, palm muted
    register: low (E2–D3)
    articulation: legato with occasional staccato on syncopations
  piano:
    voicing: rootless left hand (7th and 3rd only)
    pedaling: half pedal throughout
    right_hand: single-note melody with occasional 3rd doubling
  strings:
    doublings: [violin I, viola]
    articulation: col legno on accented beats, arco elsewhere
    register: mid (G3–E5)
  counterpoint:
    bass_vs_melody: contrary motion in bars 5–6
    inner_voices: hold while outer voices move
```

------------------------------------------------------------------------

### Effects

Reverb character, delay type, saturation, compression shape, EQ.

```yaml
Effects:
  drums:
    compression:
      type: FET (fast attack)
      attack: 2ms
      release: auto
      ratio: 4:1
      glue: subtle
    saturation:
      type: tape
      amount: subtle (harmonics only)
    room: small plate, 0.8s decay
  bass:
    eq:
      - band: low shelf
        freq: 80hz
        gain: +2db
      - band: notch
        freq: 200hz
        gain: -3db
    saturation:
      type: tube overdrive
      drive: low (add warmth, not distortion)
  master:
    reverb:
      type: hall (long, diffuse)
      size: large
      predelay: 25ms
      wet: 15%
    tape_warmth: subtle wow/flutter
```

------------------------------------------------------------------------

### Expression

Emotional arc, narrative, tension points, spatial image, character.

```yaml
Expression:
  arc: melancholic → hopeful → resolve
  narrative: |
    3am in an empty diner. Rain on the window. The last person in the
    city who remembers how things used to be. Not sad — just present.
  tension_points:
    - bar: 4
      device: unresolved suspension
    - bar: 7
      device: chromatic approach note, delayed resolution
  release: beat 1 bar 8
  spatial_image: |
    Wide stereo. Drums centered, slightly back. Bass center, upfront.
    Melody left of center. Pads wide, far back, almost like memory.
  character: |
    Like Ahmad Jamal playing for nobody. Unhurried. Every note chosen.
```

------------------------------------------------------------------------

### Texture

Density, register spread, layering strategy, space management.

```yaml
Texture:
  density: sparse  # sparse | medium | dense | orchestral
  register_spread: low-mid  # avoid top 2 octaves
  layering:
    strategy: melody over chord pads, bass anchors bottom
    avoid: frequency masking between bass and left-hand piano
  space:
    principle: |
      Silence is as important as sound. Every instrument needs room.
      If it doesn't serve the moment, don't play it.
  stereo_field:
    drums: center ±20
    bass: center
    piano: left -15
    pads: wide ±60
    melody: right +20
```

------------------------------------------------------------------------

### Form

Large-scale structure, development, variation, narrative arc.

```yaml
Form:
  structure: AABA  # or: verse-chorus, through-composed, rondo
  development:
    - section: A (bars 1–8)
      intensity: low, establishing
    - section: A' (bars 9–16)
      variation: add brushed hi-hat, piano gets slightly louder
    - section: B (bars 17–24)
      contrast: modulate to Eb major, melody descends
    - section: A'' (bars 25–32)
      resolution: return home, sparser than before
  variation_strategy: |
    Each repetition of A reveals one more instrument. First time:
    piano alone. Second: bass enters. Third: full groove.
```

------------------------------------------------------------------------

### Automation

Explicit parameter automation curves for any track or effect parameter.

```yaml
Automation:
  - track: Pads
    param: reverb_wet
    events:
      - beat: 0
        value: 0.1
        curve: linear
      - beat: 16
        value: 0.6
        curve: exponential
      - beat: 24
        value: 0.1
        curve: linear
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -6db
      - beat: 8
        value: 0db
        curve: smooth
```

------------------------------------------------------------------------

## Full Maestro Example

A complete Stori Structured Prompt that leaves nothing to inference:

```yaml
STORI PROMPT
Mode: compose
Section: verse
Position: after intro + 2
Style: lofi hip hop
Key: Cm
Tempo: 75
Role: [drums, bass, piano, melody]
Constraints:
  bars: 8
  density: medium-sparse
Vibe: [dusty x3, warm x2, laid back, melancholic]

Request: |
  Verse groove — lazy boom bap with loose swing, deep bass anchoring
  Cm-Ab-Eb-Bb, lo-fi chord stabs, and a wistful piano melody with
  plenty of space. This is the emotional core of the track.

Harmony:
  progression: [Cm7, Abmaj7, Ebmaj7, Bb7sus4]
  voicing: rootless close position
  rhythm: half-note stabs on beats 1 and 3
  extensions: 9ths throughout
  color: bittersweet — Abmaj7 is the emotional peak each bar

Melody:
  scale: C dorian
  register: mid (Bb4–G5)
  contour: descending arch, resolves up on final bar
  phrases:
    structure: 2-bar call, 2-bar response, repeated
    breath: 1.5 beats of silence between phrases
  density: sparse — average 1 note per beat

Rhythm:
  feel: behind the beat
  swing: 55%
  ghost_notes:
    instrument: snare
    velocity: 30–40
  hi_hat: slightly open, loose

Dynamics:
  overall: mp throughout
  accent_velocity: 90
  ghost_velocity: 35

Orchestration:
  drums:
    kit: boom bap
    kick: slightly late
  bass:
    technique: finger style
    register: E2–G3
  piano:
    voicing: rootless
    pedaling: half pedal

Effects:
  drums:
    saturation: tape, subtle
    compression: slow attack
  bass:
    saturation: tube, warm
  piano:
    reverb: small room, 0.6s, 12ms predelay
  master:
    tape_warmth: true

Expression:
  arc: resignation → quiet acceptance
  narrative: |
    Late night, alone. Not lonely — at peace with it.
    Like Nujabes playing for the empty room.
  spatial_image: drums back-center, bass upfront-center, piano left, melody right

Texture:
  density: medium-sparse
  register_spread: E2–G5 (avoid top register)
  space: every instrument needs room to breathe
```

------------------------------------------------------------------------

## Sequential Composition — Section Workflow

Build a full song section by section:

**Prompt 1 — Intro:**
```yaml
STORI PROMPT
Mode: compose
Section: intro
Style: lofi hip hop
Key: Cm
Tempo: 75
Role: [pads, melody]
Constraints:
  bars: 4
Vibe: [dreamy x3, melancholic x2]
Request: Floating atmospheric intro, warm and spacious.
```

**Prompt 2 — Verse (after intro):**
```yaml
STORI PROMPT
Mode: compose
Section: verse
Position: after intro
Style: lofi hip hop
Key: Cm
Tempo: 75
Role: [drums, bass, chords]
Constraints:
  bars: 8
Request: Verse groove — lazy boom bap, deep bass, chord stabs.
```

**Prompt 3 — Pre-chorus fill (pickup into chorus):**
```yaml
STORI PROMPT
Mode: compose
Section: pre-chorus fill
Position: before chorus - 4
Role: drums
Constraints:
  bars: 1
Request: 1-bar drum fill building into the chorus.
```

**Prompt 4 — Chorus (alongside verse, denser):**
```yaml
STORI PROMPT
Mode: compose
Section: chorus
Position: after verse
Role: [lead, chords, drums]
Constraints:
  bars: 8
  density: high
Vibe: [driving x3, euphoric x2]
Request: Full chorus — melodic lead, denser groove, brighter feel.
```

------------------------------------------------------------------------

## Agent Parsing Rules

### 1. Graceful degradation

Missing fields are inferred from Request. Mode still determines routing.

### 2. YAML first, legacy fallback

The parser tries `yaml.safe_load` on the body. If YAML parsing fails
(unusual characters, bad indentation in freehand text), it falls back to
the original line-by-line flat scanner. No prompt is silently dropped.

### 3. Extensions pass-through

Any top-level field not in the routing set (Mode, Section, Position,
Target, Style, Key, Tempo, Role, Constraints, Vibe, Request) is collected
in `extensions` and injected verbatim into the Maestro LLM system prompt
as structured YAML. **The vocabulary is open.** Invent new dimensions —
they work immediately.

### 4. Comments are preserved intent

```yaml
# The melody should feel like it remembers something
Melody:
  contour: descending  # like water finding its level
```

Comments are stripped by the YAML parser but their presence signals
that the user is thinking carefully. The Request should capture the
intent in prose if it matters.

### 5. Safety

Routing fields are parsed before freeform text. Mode is validated.
Structured fields reduce jailbreak surface area.

------------------------------------------------------------------------

## Backend Implementation Status

| Item | Status | Module |
|---|---|--------|
| Prompt parser (YAML-first) | Done | `app/core/prompt_parser.py` |
| Intent routing gate | Done | `app/core/intent.py` |
| Weighted vibes | Done | `app/core/intent_config.py` |
| Deterministic planner | Done | `app/core/planner.py` |
| Structured prompt context + Maestro injection | Done | `app/core/prompts.py` |
| Pipeline threading | Done | `app/core/pipeline.py`, `app/core/compose_handlers.py` |
| Target scope validation | Done | `app/core/tool_validation.py` |
| Position: field (6 relationships + offset) | Done | `app/core/prompt_parser.py`, `app/core/prompts.py` |
| Section: field | Done | `app/core/prompt_parser.py` |
| Extensions pass-through (all Maestro dims) | Done | `app/core/prompt_parser.py`, `app/core/prompts.py` |
| Entity manifest in tool results | Done | `app/core/compose_handlers.py` |
| `$N.field` variable references | Done | `app/core/compose_handlers.py` |

**Tests:** `tests/test_prompt_parser.py` (91+), `tests/test_intent_structured.py` (26),
`tests/test_structured_prompt_integration.py` (16), `tests/test_tool_validation.py` (6+).
All existing tests pass unchanged (zero regression).
