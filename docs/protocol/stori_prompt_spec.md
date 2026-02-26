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
- **MIDI Expressiveness** — sustain, expression, mod wheel, pitch bend,
  aftertouch, filter, portamento, and every CC 0-127

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
Energy:       # optional — very low | low | medium | high | very high
Role:         # optional — list of musical roles
Constraints:  # optional — key-value boundary hints
Vibe:         # optional — producer idiom lexicon (weighted)
Request:      # required for edit/ask; optional for compose — natural language description of intent
```

**Mode** is always required. **Request** is required for `edit` and `ask` modes;
for `compose` mode it is optional — when omitted the server synthesizes a
request from the other dimensions (Style, Role, etc.).

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

### Energy

Coarse energy level. Maps directly to the EmotionVector's energy and
motion axes (1.5× weight). See the EmotionVector section for the full
vocabulary and numeric mapping.

```yaml
Energy: very low    # stillness, ambient
Energy: low         # relaxed, subdued
Energy: medium      # balanced, standard
Energy: high        # energetic, driving
Energy: very high   # explosive, full-force
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
Dynamics maps to MIDI note velocity (0-127) and CC 11 (Expression).

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
  expression_cc:                  # CC 11 — fine dynamic shading
    curve: follow velocity arc
    range: [40, 120]
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

### Humanization

Micro-timing and velocity humanization controlling the "feel" of the
performance. These parameters feed directly into the expressiveness
post-processor that runs on every generated track.

The post-processor is **always active** — these fields override its
defaults. Without explicit `Humanization:`, the system uses genre-
appropriate defaults from the Style (e.g. jazz = heavy timing jitter +
laid-back feel; techno = tight grid + subtle velocity variation).

```yaml
Humanization:
  timing:
    jitter: 0.06             # ± beats of random offset (0 = quantized)
    late_bias: 0.015          # positive = laid-back, negative = pushed
    grid: 16th               # grid that jitter is relative to
  velocity:
    arc: phrase               # phrase | bar | crescendo | none
    stdev: 18                 # target standard deviation across notes
    accents:
      beats: [1, 3]           # strong beats (0-indexed within bar)
      strength: 12            # velocity boost on accents
    ghost_notes:
      probability: 0.15       # chance of inserting ghost note
      velocity: [25, 45]      # velocity range for ghost notes
  feel: behind the beat       # or: on the grid, pushed, drunken
```

**Timing presets by genre** (defaults when `Humanization:` is omitted):

| Genre | Jitter (beats) | Late bias | Feel |
|-------|---------------|-----------|------|
| Classical | 0.06 | 0.00 | Rubato-influenced |
| Jazz | 0.05 | 0.015 | Laid back |
| Lo-fi | 0.05 | 0.02 | Lazy, behind |
| Boom Bap | 0.03 | 0.01 | Slight swing |
| Trap | 0.015 | 0.00 | Tight, punchy |
| House | 0.01 | 0.00 | Metronomic groove |
| Techno | 0.008 | 0.00 | Precise, mechanical |
| Ambient | 0.07 | 0.00 | Floating, free |
| Funk | 0.02 | 0.005 | Tight pocket |

------------------------------------------------------------------------

### Orchestration

Instrument assignment, technique, articulation, doublings, counterpoint.
Articulation directives map to MIDI performance data: legato (CC 68),
portamento (CC 5/65), sustain pedal (CC 64), soft pedal (CC 67).

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
    portamento: glide on tied notes  # CC 5 + CC 65
  piano:
    voicing: rootless left hand (7th and 3rd only)
    pedaling: half pedal throughout   # CC 64 continuous values
    right_hand: single-note melody with occasional 3rd doubling
  strings:
    doublings: [violin I, viola]
    articulation: col legno on accented beats, arco elsewhere
    register: mid (G3–E5)
    vibrato: delayed onset, moderate depth  # CC 76-78
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

**This block produces mandatory tool calls.** Every entry under `Effects` is translated into `stori_add_insert_effect` calls after tracks are created. The translation table:

| Effects entry | Tool call |
|---|---|
| `drums.compression` / `drums.compressor` | `stori_add_insert_effect(type="compressor")` |
| `drums.room` / `drums.reverb` | `stori_add_insert_effect(type="reverb")` or reverb bus send |
| `drums.saturation` / `drums.tape` | `stori_add_insert_effect(type="overdrive")` |
| `bass.eq` | `stori_add_insert_effect(type="eq")` |
| `bass.saturation` / `bass.tube` | `stori_add_insert_effect(type="overdrive")` |
| `bass.distortion` / `bass.fuzz` | `stori_add_insert_effect(type="distortion")` |
| `*.reverb` (2+ tracks) | `stori_ensure_bus(name="Reverb")` → `stori_add_send` per track |
| `*.chorus` | `stori_add_insert_effect(type="chorus")` |
| `*.tremolo` | `stori_add_insert_effect(type="tremolo")` |
| `*.delay` | `stori_add_insert_effect(type="delay")` |
| `*.filter` | `stori_add_insert_effect(type="filter")` |
| `*.phaser` | `stori_add_insert_effect(type="phaser")` |
| `lead.overdrive` / `lead.cranked` | `stori_add_insert_effect(type="overdrive")` |
| `lead.distortion` | `stori_add_insert_effect(type="distortion")` |

These tool calls appear in the plan as explicit steps (`"Add effects to Drums"`, `"Add effects to Bass"`, etc.) so the frontend progress view reflects them.

**Style-based auto-inference:** Even without an explicit `Effects:` block, the planner always adds baseline effects from `Style` and `Role`: drums get a compressor, bass gets a compressor, pads and leads get a reverb bus send. Suppress with `Constraints: no_effects: true`.

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

**This block produces mandatory tool calls.** Each lane is translated into `stori_add_automation(target=TRACK_ID, points=[{beat, value, curve?}, …])`. The trackId comes from the `stori_add_midi_track` result for that track name. The plan tracker shows each as a per-track step with canonical label `"Write automation for <TrackName>"`.

Common `param` values: `volume`, `pan`, `reverb_wet`, `filter_cutoff`, `tremolo_rate`, `delay_feedback`, `overdrive_drive`. Curve values: `Linear`, `Smooth`, `Step`, `Exp`, `Log`.

### MIDI Expressiveness

Explicit control over MIDI performance data beyond notes. The Maestro pipeline
supports the **complete** set of musically relevant MIDI messages — every
property listed here flows all the way to the frontend DAW via the variation
pipeline or direct tool calls.

```yaml
MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches  # or: full sustain, no pedal, legato only
    changes_per_bar: 2–4
  expression:
    curve: crescendo bars 1–4, plateau bars 5–8
    range: [40, 120]           # CC 11 value range
  modulation:
    instrument: strings
    depth: subtle vibrato       # CC 1 values 0–30
    onset: delayed 1 beat      # vibrato starts after attack
  pitch_bend:
    range: ±2 semitones        # pitch bend range (set on synth)
    style: blues bends on 3rds and 7ths
    depth: quarter-tone to half-tone
  aftertouch:
    type: channel              # or: polyphonic
    response: velocity-mapped   # pressure follows velocity curve
    use: filter cutoff modulation
  breath_control:
    instrument: wind synth
    mapping: filter + volume    # CC 2 drives filter and amplitude
  filter:
    cutoff:
      sweep: low → high bars 5–8  # CC 74
      resonance: moderate          # CC 71
  cc_curves:
    - cc: 91                   # reverb send
      from: 20
      to: 100
      position: bars 1–8
    - cc: 93                   # chorus send
      value: 60
      position: bars 5–8
  articulation:
    legato: true               # CC 68
    portamento:
      time: 80                 # CC 5
      switch: on               # CC 65
    soft_pedal: bars 1–4       # CC 67
    sostenuto: beat 1 bar 5    # CC 66
```

**Supported MIDI messages** (complete coverage):

| Category | CC / Message | Description |
|----------|-------------|-------------|
| **Dynamics** | Note velocity | Per-note attack strength (0-127) |
| | CC 7 (Volume) | Channel volume |
| | CC 11 (Expression) | Fine dynamic control within volume |
| | CC 2 (Breath) | Wind instrument breath control |
| **Pedals** | CC 64 (Sustain) | Damper pedal (on/off or continuous) |
| | CC 66 (Sostenuto) | Sustain only held notes |
| | CC 67 (Soft Pedal) | Una corda / soft pedal |
| **Modulation** | CC 1 (Mod Wheel) | Vibrato, tremolo, filter sweep |
| | Pitch Bend | 14-bit pitch deviation (−8192 to 8191) |
| | Channel Aftertouch | Pressure applied after note-on (whole channel) |
| | Poly Key Pressure | Per-note aftertouch |
| **Timbre** | CC 74 (Filter Cutoff) | Brightness control |
| | CC 71 (Resonance) | Filter resonance / harmonic emphasis |
| | CC 73 (Attack) | Envelope attack time |
| | CC 72 (Release) | Envelope release time |
| | CC 76–78 (Vibrato) | Rate, depth, delay |
| **Spatial** | CC 10 (Pan) | Stereo position |
| | CC 91 (Reverb Send) | Reverb wet level |
| | CC 93 (Chorus Send) | Chorus wet level |
| **Technique** | CC 5 (Portamento Time) | Glide speed |
| | CC 65 (Portamento Switch) | Glide on/off |
| | CC 68 (Legato) | Legato mode on/off |
| | CC 84 (Portamento Control) | Source note for glide |
| **Program** | Program Change | Instrument selection (track-level) |

All 128 CC numbers are supported. The table above highlights the most
musically significant ones; any CC 0-127 can be specified via `cc_curves`.

**This block produces mandatory tool calls.** Each sub-property of `MidiExpressiveness` is translated into tool calls on the region after notes are added:

| MidiExpressiveness entry | Tool call |
|---|---|
| `cc_curves[cc: N, from: X, to: Y]` | `stori_add_midi_cc(regionId, cc=N, events=[{beat:0, value:X}, {beat:END, value:Y}])` |
| `sustain_pedal` with `changes_per_bar: N` | `stori_add_midi_cc(cc=64, events=[…])` — 127=pedal down, 0=up, N pairs per bar |
| `expression.range: [lo, hi]` | `stori_add_midi_cc(cc=11, events=[{beat:0, value:lo}, …])` |
| `modulation.depth` | `stori_add_midi_cc(cc=1, events=[…])` |
| `pitch_bend.style` | `stori_add_pitch_bend(regionId, events=[{beat, value}, …])` — 0=center, ±8192=±2 semitones |
| `filter.cutoff.sweep` | `stori_add_midi_cc(cc=74, events=[…])` |

The plan tracker surfaces these as per-track steps with canonical labels: `"Add MIDI CC to <TrackName>"`, `"Add pitch bend to <TrackName>"`. These labels follow the same canonical pattern convention used by the frontend's `ExecutionTimelineView` for per-instrument grouping.

------------------------------------------------------------------------

## EmotionVector — 5-Axis Conditioning System

Every STORI PROMPT is automatically translated into a 5-axis **EmotionVector**
that flows all the way into Orpheus as a numeric conditioning signal. The richer
your `Vibe`, `Section`, `Style`, and `Energy` fields, the more precisely Orpheus
generates music that matches your creative intent — not just stylistically, but
emotionally.

### The Five Axes

| Axis | Range | Low end | High end |
|------|-------|---------|----------|
| `energy` | 0.0 → 1.0 | stillness, ambient drift | explosive, full-force |
| `valence` | −1.0 → +1.0 | dark, sad, heavy | bright, joyful, uplifting |
| `tension` | 0.0 → 1.0 | resolved, settled | unresolved, anxious, suspended |
| `intimacy` | 0.0 → 1.0 | distant, epic, wide | close, personal, whispered |
| `motion` | 0.0 → 1.0 | static, sustained, floating | driving, rhythmic, propulsive |

### How Fields Blend Into the Vector

Fields are blended in priority order (higher priority overrides lower):

| Field | Weight | Effect |
|-------|--------|--------|
| `Section:` | 1.5× | Section presets (verse/chorus/bridge/drop) establish the coarse baseline |
| `Style:` | 0.5× | Genre defaults add light color (jazz, edm, ambient, etc.) |
| `Vibe:` | 1.0× each keyword | Explicit emotional keywords fine-tune each axis |
| `Energy:` | 1.5× | Direct energy + motion override |

### Section Presets

| Section | energy | valence | tension | intimacy | motion |
|---------|--------|---------|---------|----------|--------|
| `intro` | 0.30 | +0.10 | 0.20 | 0.60 | 0.30 |
| `verse` | 0.40 | +0.00 | 0.30 | 0.70 | 0.40 |
| `chorus` | 0.80 | +0.30 | 0.40 | 0.50 | 0.70 |
| `bridge` | 0.50 | −0.10 | 0.60 | 0.60 | 0.50 |
| `breakdown` | 0.20 | +0.00 | 0.50 | 0.80 | 0.20 |
| `buildup` | 0.60 | +0.20 | 0.70 | 0.40 | 0.60 |
| `drop` | 1.00 | +0.50 | 0.30 | 0.20 | 1.00 |
| `outro` | 0.25 | +0.20 | 0.10 | 0.70 | 0.20 |

### Energy Level Vocabulary

The `Energy:` field maps directly to energy + motion axes (1.5× weight):

```yaml
Energy: very low    # energy=0.10, motion=0.10
Energy: low         # energy=0.20, motion=0.20
Energy: medium      # energy=0.50, motion=0.50
Energy: high        # energy=0.80, motion=0.70
Energy: very high   # energy=0.95, motion=0.90
```

### Vibe Keyword Vocabulary

All recognized keywords and their primary axis effects. Multiple keywords
blend by averaging. Unrecognized words are silently ignored (use `Request:`
for concepts not in the list).

**Valence (dark ↔ bright):**

| Keyword | Primary effect |
|---------|---------------|
| `dark` | valence −0.45, tension +0.50 |
| `moody` | valence −0.30, tension +0.45 |
| `brooding` | valence −0.35, tension +0.50, energy +0.30 |
| `melancholic` | valence −0.50, energy +0.25, intimacy +0.70 |
| `sad` | valence −0.60, energy +0.30 |
| `nostalgic` | valence −0.20, intimacy +0.60 |
| `bittersweet` | valence −0.10, intimacy +0.60, tension +0.35 |
| `haunting` | valence −0.40, tension +0.65, energy +0.25 |
| `eerie` | valence −0.45, tension +0.70 |
| `mysterious` | valence −0.20, tension +0.60, energy +0.30 |
| `warm` | valence +0.30, intimacy +0.65 |
| `bright` | valence +0.50, energy +0.50 |
| `happy` | valence +0.60, energy +0.60 |
| `joyful` | valence +0.70, energy +0.65 |
| `uplifting` | valence +0.60, energy +0.65 |
| `triumphant` | valence +0.70, energy +0.85, tension +0.40 |
| `euphoric` | valence +0.90, energy +0.90, motion +0.90 |

**Energy / Intensity:**

| Keyword | Primary effect |
|---------|---------------|
| `calm` | energy +0.20, tension +0.10, motion +0.20 |
| `peaceful` | energy +0.20, tension +0.10 |
| `relaxed` | energy +0.25, tension +0.10, motion +0.20 |
| `mellow` | energy +0.30, tension +0.15, motion +0.30 |
| `laid-back` | energy +0.30, tension +0.10, motion +0.35 |
| `energetic` | energy +0.80, motion +0.70 |
| `intense` | energy +0.85, tension +0.70 |
| `aggressive` | energy +0.90, tension +0.80, motion +0.80 |
| `explosive` | energy +1.00, tension +0.80, motion +0.90 |

**Intimacy / Space:**

| Keyword | Primary effect |
|---------|---------------|
| `intimate` | intimacy +0.80, energy +0.30 |
| `personal` | intimacy +0.75 |
| `cozy` | intimacy +0.70, energy +0.20, valence +0.20 |
| `atmospheric` | intimacy +0.50, tension +0.30, energy +0.20 |
| `cinematic` | intimacy +0.30, tension +0.50, energy +0.60 |
| `epic` | intimacy +0.10, energy +0.85 |
| `distant` | intimacy +0.15 |
| `dreamy` | tension +0.20, intimacy +0.70, energy +0.20 |

**Motion / Groove:**

| Keyword | Primary effect |
|---------|---------------|
| `sparse` | motion +0.20, energy +0.20 |
| `minimal` | motion +0.15, energy +0.15 |
| `flowing` | motion +0.60, tension +0.20 |
| `bouncy` | motion +0.70, energy +0.60, valence +0.30 |
| `groovy` | motion +0.75, energy +0.60 |
| `driving` | motion +0.80, energy +0.70 |
| `dense` | motion +0.70, energy +0.70 |

**Tension / Harmony:**

| Keyword | Primary effect |
|---------|---------------|
| `resolved` | tension +0.10, valence +0.20 |
| `anxious` | tension +0.80, energy +0.60 |
| `tense` | tension +0.75 |

### Genre Presets (from Style: field)

| Genre keyword | Preset vector |
|---------------|--------------|
| `lofi`, `lo-fi` | energy 0.30, valence −0.10, tension 0.20, intimacy 0.75, motion 0.35 |
| `hip-hop`, `hip hop`, `trap` | energy 0.70, valence +0.10, tension 0.40, intimacy 0.40, motion 0.75 |
| `jazz` | energy 0.50, valence +0.20, tension 0.50, intimacy 0.60, motion 0.50 |
| `ambient` | energy 0.15, valence +0.20, tension 0.20, intimacy 0.70, motion 0.10 |
| `edm`, `electronic` | energy 0.85, valence +0.40, tension 0.40, intimacy 0.20, motion 0.90 |
| `metal`, `rock` | energy 0.95, valence −0.30, tension 0.80, intimacy 0.20, motion 0.85 |
| `folk`, `indie` | energy 0.40, valence +0.10, tension 0.30, intimacy 0.80, motion 0.40 |
| `classical` | energy 0.50, valence +0.30, tension 0.40, intimacy 0.50, motion 0.40 |

### Storpheus Conditioning Chain

The final EmotionVector maps to three Orpheus intent fields:

```
valence [-1, +1]  →  tone_brightness [-1.0, +1.0]
energy [0, 1]     →  energy_intensity = (energy × 2.0) − 1.0  → [-1.0, +1.0]
thresholds        →  musical_goals (string list):
                      energy > 0.7  → "energetic"
                      energy < 0.3  → "sparse"
                      valence < -0.3 → "dark"
                      valence > 0.3  → "bright"
                      tension > 0.6  → "tense"
                      intimacy > 0.7 → "intimate"
                      motion > 0.7   → "driving"
                      motion < 0.25  → "sustained"
```

This chain means: a prompt with `Vibe: [euphoric x2, driving]` generates
measurably different Orpheus output than `Vibe: [melancholic, sparse]` —
the entire vibe vocabulary flows all the way to the neural generator.

### Refinement Language

After generation, these natural-language commands adjust the EmotionVector
for re-generation:

| Command | Axis delta |
|---------|-----------|
| `sadder` | valence −0.30 |
| `happier` / `brighter` | valence +0.25–0.30 |
| `darker` | valence −0.25 |
| `more intense` | energy +0.20, tension +0.15 |
| `calmer` | energy −0.25, tension −0.20, motion −0.15 |
| `more energetic` | energy +0.30, motion +0.15 |
| `more driving` | motion +0.30, energy +0.10 |
| `more sustained` | motion −0.30 |
| `more intimate` | intimacy +0.30, energy −0.10 |
| `more epic` | intimacy −0.30, energy +0.15 |
| `build up` | tension +0.25, energy +0.15 |
| `resolve it` | tension −0.40, valence +0.10 |
| `busier` | motion +0.25, energy +0.10 |
| `sparser` | motion −0.20, energy −0.10 |

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

MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches
    changes_per_bar: 2
  expression:
    curve: flat mp, slight swell bars 5–6
    range: [50, 95]
  pitch_bend:
    style: subtle blues bends on minor 3rds
    depth: quarter-tone
  aftertouch:
    type: channel
    response: gentle, velocity-mapped
  filter:
    cutoff:
      sweep: slow open bars 1–4
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

## Prompt → Expressive MIDI: End-to-End Flow

The following diagram shows how a structured prompt's expressive dimensions
become actual MIDI data in the DAW. Every link in this chain is implemented.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  STORI PROMPT                                                            │
│  ┌─────────────┐  ┌─────────────────┐  ┌────────────┐  ┌─────────────┐ │
│  │ Routing      │  │ Content Dims    │  │ Effects    │  │MidiExpress- │ │
│  │ Mode, Style, │  │ Harmony, Melody,│  │ drums:     │  │iveness /    │ │
│  │ Key, Tempo,  │  │ Rhythm,         │  │  compress- │  │ Automation  │ │
│  │ Role, Vibe   │  │ Orchestration,  │  │  ion, room │  │ cc_curves,  │ │
│  │              │  │ Expression,Form │  │ bass: eq   │  │ pitch_bend, │ │
│  │              │  │                 │  │ lead: od   │  │ sustain,    │ │
│  └──────┬───────┘  └────────┬────────┘  └─────┬──────┘  │ automation  │ │
└─────────┼───────────────────┼─────────────────┼─────────┴──────┬───────┘
          │                   │                 │                 │
          ▼                   │          PATH B │          PATH C │
   ┌──────────────┐           │   (mandatory    │   (mandatory    │
   │ Intent class │           │   tool calls)   │   tool calls)   │
   │ → EDITING /  │           │                 │                 │
   │   COMPOSING  │           │                 │                 │
   └──────┬───────┘           │                 ▼                 ▼
          │                   │   ┌────────────────────────────────────┐
          │                   │   │ Tool Call Translation              │
          │                   │   │ Effects → stori_add_insert_effect  │
          │                   │   │ cc_curves → stori_add_midi_cc      │
          │                   │   │ pitch_bend → stori_add_pitch_bend  │
          │                   │   │ sustain_pedal → stori_add_midi_cc  │
          │                   │   │ automation → stori_add_automation  │
          │                   │   └─────────────────┬──────────────────┘
          │                   ▼              PATH A  │
          │         ┌────────────────────┐           │
          │         │ Maestro LLM Prompt │           │
          │         │ Content dims +     │           │
          │         │ execution mandate  │           │
          │         │ for expressive     │           │
          │         │ blocks             │           │
          │         └────────┬───────────┘           │
          │                  │                        │
          ▼                  ▼                        ▼
   ┌──────────────┐  ┌──────────────────────────────────────┐
   │ EmotionVector│  │ Execution Plan (tool calls)           │
   │ (5-axis)     │  │ stori_generate, stori_add_notes       │
   │ → Orpheus    │  │ stori_add_insert_effect, stori_ensure_│
   │   condition  │  │ bus, stori_add_send,                  │
   └──────┬───────┘  │ stori_add_midi_cc, stori_add_pitch_   │
          │          │ bend, stori_add_automation            │
          │          └────────────────┬─────────────────────┘
          │                           │
          ▼                           ▼
   ┌─────────────────────────────────────────────┐
   │ Orpheus / Generator                          │
   │ Returns: notes[], cc_events[], pitch_bends[] │
   │          aftertouch[]                        │
   └──────────────────────┬──────────────────────┘
                          │
                          ▼
   ┌─────────────────────────────────────────────┐
   │ Executor → VariationContext (COMPOSING)      │
   │   or StateStore direct write (EDITING)       │
   │                                              │
   │ VariationService → Phrase.controller_changes │
   └──────────────────────┬──────────────────────┘
                          │
                          ▼
   ┌─────────────────────────────────────────────┐
   │ SSE Stream → Frontend                        │
   │   toolCall events (EDITING, applied directly)│
   │   phrase events with controller_changes      │
   │   (COMPOSING, accepted/discarded by user)    │
   │                                              │
   │ Commit → updated_regions                     │
   │   cc_events[], pitch_bends[], aftertouch[]   │
   └──────────────────────────────────────────────┘
```

**Key insight:** A STORI PROMPT's expressive blocks flow through four parallel paths:

**Path A — LLM context (content dims):** `Harmony`, `Melody`, `Rhythm`, `Dynamics`, `Orchestration`, `Expression`, `Texture`, `Form` are injected verbatim into the Maestro system prompt. The LLM reads them and produces richer notes, voicings, rhythms, and dynamics.

**Path B — Mandatory tool call translation (effects + expressiveness):** `Effects`, `MidiExpressiveness`, and `Automation` blocks are translated by the system into explicit `stori_add_insert_effect`, `stori_add_midi_cc`, `stori_add_pitch_bend`, and `stori_add_automation` calls. The system prompt injects an execution mandate and the plan tracker surfaces each block as a visible frontend step. These are not suggestions.

**Path C — EmotionVector (Orpheus conditioning):** `Vibe`, `Section`, `Style`, and `Energy` are parsed into a 5-axis numeric vector forwarded directly to Orpheus as `tone_brightness`, `energy_intensity`, and `musical_goals`. A prompt with `Vibe: melancholic x3, sparse` generates measurably different notes than `Vibe: euphoric x3, driving` — the vibe vocabulary conditions the neural generator directly.

**Path D — Automatic expressiveness post-processing:** After Orpheus generates raw notes, the `ExpressivenessPostProcessor` automatically enriches them with performance-quality dynamics based on `Style`. This layer adds:
- **Velocity curves** — phrase-level arcs, accent patterns, ghost note insertion
- **CC automation** — CC 11 (expression) swells, CC 64 (sustain pedal) for keys, CC 1 (mod wheel) for vibrato
- **Pitch bends** — bass slides, approach notes, blues bends (style-dependent)
- **Timing humanization** — micro-timing jitter pushing ~92% of notes off the 16th grid (matching professional MIDI analysis of 200 MAESTRO performances)

Each genre has a tuned profile (classical, jazz, trap, house, ambient, etc.) calibrated against analysis of 200 professional classical piano performances and orchestral reference MIDIs. The post-processor runs on every non-drum instrument after Orpheus returns notes, before the critic scores the result.

Path D is **always active** — even a minimal prompt with just `Style: jazz` and no `MidiExpressiveness` block produces expressively humanized MIDI. Adding an explicit `MidiExpressiveness` block (Path B) layers on top of Path D for maximum control.

The result: a prompt with `Effects: drums: compression`, `MidiExpressiveness: cc_curves: [{cc: 91}]`, and `Vibe: groovy x3` produces four concrete actions — a compressor insert on the Drums track, a CC 91 automation curve on the drums region, an Orpheus call biased toward driving output, and automatic velocity curves + CC 11 expression swells + timing humanization on every melodic track.

------------------------------------------------------------------------

## Auto-Section Parsing (Single-Prompt Multi-Part Composition)

In addition to the sequential section workflow above, Maestro supports
**automatic section detection** from a single free-text prompt. When the
`Request:` field mentions multiple structural keywords (intro, verse,
chorus, bridge, breakdown, build, outro, drop), the coordinator:

1. **Detects sections** by scanning the prompt for structural keywords.
   Synonyms are normalized (e.g. "drop" → chorus, "breakdown" → bridge).
2. **Assigns beat ranges** proportionally based on default section weights,
   snapped to bar boundaries. Every section is at least 1 bar (4 beats).
   The last section is stretched to fill the exact total beat count.
3. **Generates per-section, per-instrument descriptions** from a template
   library tuned to each section's energy profile (e.g. chorus drums are
   "full energy, all elements active" while breakdown drums are "stripped
   to minimal or silence").
4. **Dispatches per-section regions** — each instrument agent receives the
   full section list and creates one `stori_add_midi_region` +
   `stori_generate_midi` pair per section, each with a section-specific
   `prompt` reflecting that section's energy, density, and musical role.

### Example

A prompt like:

```
Make a 32-bar reggaeton track with an intro, verse, and chorus in Bm at 96 BPM
```

Is automatically decomposed into:

```json
{
  "sections": [
    { "name": "intro",  "start_beat": 0,  "length_beats": 20 },
    { "name": "verse",  "start_beat": 20, "length_beats": 44 },
    { "name": "chorus", "start_beat": 64, "length_beats": 64 }
  ]
}
```

Each instrument agent then creates 3 regions (one per section) with
section-appropriate MIDI content, rather than one flat 128-beat region
with no structural variation.

### Inferred sections

Descriptive language also triggers section detection:

| Phrase | Inferred section |
|--------|-----------------|
| "builds up", "gradually rises" | `build` |
| "stripped back", "bare", "minimalist" | `breakdown` |
| "full drop", "big hit" | `chorus` |
| "opening" | `intro` |
| "closing", "ends" | `outro` |

### Fallback

If fewer than 2 section keywords are detected, the full arrangement is
treated as a single section (original behaviour preserved).

### Module

`app/core/maestro_agent_teams/sections.py` — `parse_sections(prompt, bars, roles)`.
Tests: `tests/test_sections.py`.

------------------------------------------------------------------------

## `stori_generate_midi` — Updated Tool Schema

The `stori_generate_midi` tool now requires explicit entity references
to prevent the ordering bugs where agents called the generator before
creating a region.

### Required parameters (all must be present)

| Parameter | Type | Description |
|-----------|------|-------------|
| `trackId` | string (UUID) | From `stori_add_midi_track` or existing track |
| `regionId` | string (UUID) | From `stori_add_midi_region` for this track. **Must** call `stori_add_midi_region` first. |
| `start_beat` | number | Beat position where this region starts (e.g. `0.0`) |
| `role` | string | Instrument role: `drums`, `bass`, `chords`, `melody`, `arp`, `pads`, `fx` |
| `style` | string | Style tag: `boom_bap`, `trap`, `house`, `lofi`, `jazz`, `reggaeton`, etc. |
| `tempo` | integer | Tempo in BPM |
| `bars` | integer | Number of bars to generate (1–64) |

### Optional parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | string | Key signature (e.g. `"Bm"`, `"F# minor"`) |
| `prompt` | string | **Instrument-specific musical description** (2–3 sentences). Include rhythmic role, note range, density, inter-track interaction, and genre idioms. Each section should have a different prompt. |
| `constraints` | object | Structured constraints (density, syncopation, swing, note_range) |

### Strict call ordering

For every track, the call sequence is:

```
stori_create_track     → returns trackId
stori_add_midi_region  → requires: trackId, startBeat, durationBeats
                         returns: regionId
stori_generate_midi    → requires: trackId, regionId, start_beat
                         (regionId from step 2, NOT the trackId)
stori_add_insert_effect → requires: trackId
```

Steps 2–3 repeat once per section in multi-section compositions.
Step 3 must not be called until step 2 for the **same section** has
returned successfully.

### Breaking change from previous schema

The old schema only required `[role, style, tempo, bars]`. Code that
calls `stori_generate_midi` without `trackId`, `regionId`, and
`start_beat` will now receive a validation error.

------------------------------------------------------------------------

## GPU Resilience — Retry and Warm-Up

### Problem

The Orpheus MIDI generation model runs on Gradio Spaces with GPU pods
that cold-start. A cold pod returns `"No GPU was available after 60s.
Retry later"`, which previously caused a permanent failure for that track.

### Retry logic

`StorpheusClient.generate()` now retries up to 3 times with exponential
backoff delays of **5s**, **15s**, **30s** when it detects a GPU
cold-start error in:

- The JSON response body (`success: false, error: "No GPU was available..."`)
- An HTTP error body (e.g. 503 with GPU text)

After all retries are exhausted, a structured error is returned:

```json
{
  "success": false,
  "error": "gpu_unavailable",
  "message": "MIDI generation failed after 3 attempts — GPU unavailable.",
  "retry_count": 3
}
```

Non-GPU errors (auth failures, network errors, etc.) are returned
immediately without retrying.

### GPU warm-up

Before spawning instrument agents, the coordinator fires a lightweight
`health_check()` probe to the Orpheus endpoint. This primes the GPU pod
so the first real generation call doesn't hit the 60-second cold-start.

### Frontend implications

- Tracks may take up to ~50 seconds longer when the GPU is cold (retry
  delays sum to 50s). The SSE stream remains open during this time.
- The `generatorComplete` event includes `durationMs` reflecting the
  full wall-clock time including retries.
- If all retries fail, the FE receives a `toolError` event with
  `"gpu_unavailable"` — display this clearly rather than a spinner.

### Module

`app/services/storpheus.py` — retry logic in `StorpheusClient.generate()`.
`app/core/maestro_agent_teams/coordinator.py` — warm-up probe before Phase 2.
Tests: `tests/test_storpheus_client.py` (`TestGpuColdStartRetry`).

------------------------------------------------------------------------

## Backend Implementation Status

| Item | Status | Module |
|---|---|--------|
| Prompt parser (YAML-first) | Done | `app/core/prompt_parser.py` |
| Intent routing gate | Done | `app/core/intent/` |
| Weighted vibes | Done | `app/core/intent_config.py` |
| Deterministic planner | Done | `app/core/planner/` |
| Structured prompt context + Maestro injection | Done | `app/core/prompts.py` |
| Pipeline threading | Done | `app/core/pipeline.py`, `app/core/maestro_handlers.py` |
| Target scope validation | Done | `app/core/tool_validation/` |
| Position: field (6 relationships + offset) | Done | `app/core/prompt_parser.py`, `app/core/prompts.py` |
| Section: field | Done | `app/core/prompt_parser.py` |
| Extensions pass-through (all Maestro dims) | Done | `app/core/prompt_parser.py`, `app/core/prompts.py` |
| Entity manifest in tool results | Done | `app/core/maestro_handlers.py` |
| `$N.field` variable references | Done | `app/core/maestro_handlers.py` |
| Vibe/Section/Style/Energy → EmotionVector → Orpheus | Done | `app/core/emotion_vector.py`, `app/core/executor/`, `app/services/backends/storpheus.py` |
| CC events extraction + pipeline (all 128 CCs) | Done | `app/services/backends/storpheus.py`, `app/core/executor/`, `app/services/variation/` |
| Pitch bend extraction + pipeline (14-bit) | Done | `app/services/backends/storpheus.py`, `app/core/executor/`, `app/services/variation/` |
| Aftertouch extraction + pipeline (channel + poly) | Done | `app/services/backends/storpheus.py`, `app/core/executor/`, `app/services/variation/` |
| Expressive data in `updated_regions` (commit response) | Done | `app/core/executor/`, `app/api/routes/variation/` |
| Routing-only context for planner (reduces verbosity) | Done | `app/core/prompts.py`, `app/core/planner/` |
| Planner reasoning fraction | Done | `app/core/planner/` |
| **Effects block → stori_add_insert_effect (mandatory translation)** | Done | `app/core/prompts.py`, `app/core/maestro_handlers.py` |
| **Style/Role → effects inference (deterministic, pre-LLM)** | Done | `app/core/planner._infer_mix_steps` |
| **MidiExpressiveness.cc_curves → stori_add_midi_cc** | Done | `app/core/prompts.py` (mandate), `app/core/maestro_handlers.py` (plan step) |
| **MidiExpressiveness.pitch_bend → stori_add_pitch_bend** | Done | `app/core/prompts.py` (mandate), `app/core/maestro_handlers.py` (plan step) |
| **MidiExpressiveness.sustain_pedal → stori_add_midi_cc (CC 64)** | Done | `app/core/prompts.py` (mandate), `app/core/maestro_handlers.py` (plan step) |
| **Automation block → stori_add_automation** | Done | `app/core/prompts.py` (mandate), `app/core/maestro_handlers.py` (plan step) |
| **Plan steps for expressive blocks (visible in frontend)** | Done | `app/core/maestro_handlers._PlanTracker.build_from_prompt` |
| **Track role inference from GM program / drum kit** | Done | `app/core/entity_context.infer_track_role` |
| **New-section track reuse (existing tracks matched by role)** | Done | `app/core/planner._match_roles_to_existing_tracks`, `app/core/entity_context` |
| **No-op tempo/key step elimination** | Done | `app/core/maestro_handlers._PlanTracker.build_from_prompt` |
| **stori_add_notes fake-param validation + circuit breaker** | Done | `app/core/tool_validation/`, `app/core/maestro_handlers._handle_editing` |
| **Bus-before-send ordering guaranteed** | Done | `app/core/planner._schema_to_tool_calls` |
| **Expressiveness post-processor (velocity, CC, PB, timing)** | Done | `app/services/expressiveness.py`, `app/services/music_generator.py` |
| **Genre expressiveness profiles (14 genres)** | Done | `app/services/expressiveness.py` (PROFILES) |
| **Storpheus MIDI parser: full CC/PB/AT extraction** | Done | `storpheus/music_service.py` (parse\_midi\_to\_notes) |
| **Storpheus token budget raised (1024 max, 24-64/bar)** | Done | `storpheus/music_service.py`, `storpheus/generation_policy.py` |
| **Curated seed library (genre-matched seeds from 230K Loops dataset)** | Done | `storpheus/seed_selector.py`, `storpheus/build_seed_library.py` |
| **Time signature-aware region calculation** | Done | `app/core/planner/conversion.py` (\_beats\_per\_bar) |
| **MIDI analysis tooling (reference corpus)** | Done | `scripts/analyze_midi.py`, `scripts/download_reference_midi.py` |
| **Auto-section parsing (single-prompt multi-part)** | Done | `app/core/maestro_agent_teams/sections.py` |
| **Multi-section agent pipeline (per-section region+generate)** | Done | `app/core/maestro_agent_teams/agent.py` |
| **stori_generate_midi: trackId/regionId/start_beat required** | Done | `app/core/tools/definitions.py`, `app/core/maestro_editing/tool_execution.py` |
| **Instrument-specific prompt field on stori_generate_midi** | Done | `app/core/tools/definitions.py`, `app/core/maestro_agent_teams/agent.py` |
| **GPU retry (3x backoff: 5s/15s/30s)** | Done | `app/services/storpheus.py` |
| **GPU warm-up probe before composition** | Done | `app/core/maestro_agent_teams/coordinator.py` |
| **Structured diagnostic logging for stori_generate_midi** | Done | `app/core/maestro_editing/tool_execution.py` |

### How expressive blocks flow through the system

`Vibe`, `Section`, `Style`, and `Energy` fields are **parsed twice**:

1. **LLM context** — all fields injected into the Maestro system prompt for planning.
2. **Orpheus conditioning** — `emotion_vector_from_stori_prompt()` blends these into a 5-axis EmotionVector forwarded to Orpheus as `tone_brightness`, `energy_intensity`, and `musical_goals`. A prompt with `Vibe: melancholic, sparse` generates measurably different notes than `Vibe: euphoric, driving` — the vibe vocabulary flows all the way to the generator.

`Effects`, `MidiExpressiveness`, and `Automation` blocks flow through a **third path** — mandatory tool call translation:

3. **Tool call translation** — The structured prompt context injects an explicit execution mandate alongside the YAML block. The plan tracker surfaces each block as a per-track step with canonical labels (e.g. `"Add effects to Drums"`, `"Add MIDI CC to Bass"`, `"Write automation for Strings"`). The LLM translates every entry into `stori_add_insert_effect`, `stori_add_midi_cc`, `stori_add_pitch_bend`, or `stori_add_automation` calls. These are not suggestions — the system prompt treats them as a checklist.

All generated notes pass through a **fourth path** — automatic expressiveness:

4. **Expressiveness post-processing** — After Orpheus returns raw notes, `apply_expressiveness()` enriches every non-drum instrument with velocity curves, CC 11/64/1 automation, pitch bends, and timing humanization. Style-specific profiles (14 genres, calibrated against 200 MAESTRO performances) control the intensity. This ensures that even a minimal prompt produces professional-grade MIDI dynamics.

The richer text-only dimensions (`Expression`, `Harmony`, `Dynamics`, `Orchestration`, `Texture`, `Form`) reach the LLM context and shape note content but do not produce direct tool calls of their own.

**Tests:** `tests/test_prompt_parser.py` (91+), `tests/test_intent_structured.py` (26),
`tests/test_structured_prompt_integration.py` (16), `tests/test_tool_validation.py` (schema regression for stori_generate_midi),
`tests/test_neural_mvp.py` (EmotionVector parser),
`tests/test_context_injection.py` (routing-only context, extensions, translation mandate),
`tests/test_executor_deep.py` (CC, pitch bend, aftertouch pipeline, variation context),
`tests/test_maestro_handler_internals.py` (plan steps for expressive blocks),
`tests/test_planner_mocked.py` (effects inference, bus ordering, no-op suppression),
`tests/test_entity_context.py` (role inference, format_project_context, add_notes validation),
`tests/test_sections.py` (auto-section parsing, beat ranges, per-track descriptions),
`tests/test_storpheus_client.py` (GPU cold-start retry logic, backoff, structured error).
