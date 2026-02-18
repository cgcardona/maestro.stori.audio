# 🎛️ Stori Structured Prompt Format

> Official structured prompt format for **Stori**. Designed to align
> with `intent_config.py`, `intent.py`, `planner.py`, `prompts.py`, and
> MCP tool schemas.
>
> This format provides deterministic routing for EDIT / COMPOSE / ASK
> workflows while preserving natural creative language.

------------------------------------------------------------------------

# 🧠 Philosophy

Stori supports full natural language --- but power users can provide
structured prompts to:

-   Remove ambiguity from intent classification
-   Improve planner richness
-   Reduce inference overhead
-   Increase MCP tool confidence
-   Align with producer idiom lexicon

Think of this as:

**Markdown for musical intent.**

------------------------------------------------------------------------

# 🚦 Core Modes

    Mode: compose | edit | ask

### compose

Creates new musical material via planner.

### edit

Modifies existing tracks, regions, or project state.

### ask

Reasoning only. No mutations.

Mode overrides fuzzy language detection from `intent.py`.

------------------------------------------------------------------------

# 🧱 Prompt Structure

    STORI PROMPT
    Mode:
    Section:
    Position:
    Target:
    Style:
    Key:
    Tempo:
    Role:
    Constraints:
    Vibe:
    Request:

All fields are optional except **Mode** and **Request**.

------------------------------------------------------------------------

# 🔹 Field Reference

## Mode

Hard routing signal.

Examples:

    Mode: compose
    Mode: edit
    Mode: ask

------------------------------------------------------------------------

## Section

Names the output of this prompt as a labeled section of the arrangement.
Used so subsequent prompts can reference it with `After:`.

Examples:

    Section: intro
    Section: verse
    Section: chorus
    Section: bridge
    Section: outro

The label is passed to the agent as context so it names tracks and regions
accordingly (e.g. "Intro Drums", "Verse Bass"). It also registers the section
in the arrangement timeline for `After:` resolution.

------------------------------------------------------------------------

## Position

The most expressive field in the spec. Declares **where** new content
sits in the arrangement timeline using a CSS-pseudo-selector-style
vocabulary: a relationship keyword, optional section references, and
an optional beat offset.

The server resolves `Position:` to a concrete beat number and injects
it into the agent context — no manual beat math required.

### Relationships

| Form | Musical meaning |
|------|----------------|
| `last` | Append after everything currently in the project |
| `after <section>` | Sequential — start after named section ends |
| `before <section>` | Insert before named section begins |
| `alongside <section>` | Parallel layer — start at the same beat as section |
| `between <A> <B>` | Transition bridge — fill the gap between A and B |
| `within <section> bar N` | Relative — N bars into named section |
| `at <beat>` / `<N>` | Absolute beat number |
| `at bar <N>` | Absolute bar reference (bar 1 = beat 0, bar 2 = beat 4, …) |

### Offset operator

Append `+ N` or `- N` (beats) to any relationship:

    Position: before chorus - 4       # 4-beat pickup into chorus
    Position: after intro + 2         # 2-beat breathing room before verse
    Position: alongside verse + 8     # enters 8 beats into the verse
    Position: between intro verse + 2 # 2 beats after the gap midpoint

Negative offsets on `before` express **anticipatory pickups** — a
fundamental musical gesture (lead-ins, drum fills, cinematic swells).

### Resolution rules (server-side)

- `last` — `max(startBeat + durationBeats)` across all regions.
- `after X` — max end beat of tracks/regions whose name contains X.
- `before X` — min start beat of X's tracks/regions.
- `alongside X` — min start beat of X's tracks/regions.
- `between X Y` — midpoint of the gap between X end and Y start.
- `within X bar N` — X's start beat + (N−1)×4 beats.
- `at N` / `beat N` — literal beat, no scanning.
- `at bar N` — `(N−1) × 4` beats (assumes 4/4).

If no matching section is found for a named reference, falls back to
`last` (max end beat).

**Frontend requirement:** Pass the current project state in the `project`
field of the stream request. An empty `project: {}` resolves everything
to beat 0.

### Examples

    Position: last                     # append to end
    Position: after intro              # sequential verse
    Position: after intro + 2          # verse with 2-beat gap
    Position: before chorus - 4        # 4-beat pickup into chorus
    Position: alongside verse          # new parallel layer
    Position: alongside verse + 8      # late-entry layer
    Position: between intro verse      # transition bridge
    Position: within verse bar 3       # starts at bar 3 of verse
    Position: at 0                     # absolute start
    Position: at bar 9                 # bar 9 (beat 32 in 4/4)
    Position: 64                       # absolute beat 64

### Backwards compatibility

`After: <value>` is a shorthand alias — it maps to `Position: after <value>`.
Both are supported. `Position:` wins if both are present.

    After: intro          # same as: Position: after intro
    After: last           # same as: Position: last
    After: 32             # same as: Position: 32

------------------------------------------------------------------------

## Target

Maps directly to MCP tool scope.

Supported forms:

    Target: project
    Target: selection
    Target: track:<name>
    Target: region:<name>

Examples:

    Target: track:Bass
    Target: region:Verse A

------------------------------------------------------------------------

## Style

Feeds directly into `planner.py` style analysis.

Examples:

    Style: melodic techno
    Style: boom bap hip hop
    Style: cinematic ambient

------------------------------------------------------------------------

## Key

Avoids planner inference loops.

    Key: F#m
    Key: Cmaj
    Key: D dorian

------------------------------------------------------------------------

## Tempo

    Tempo: 124 bpm
    Tempo: 92

------------------------------------------------------------------------

## Role

Explicit musical responsibility for planner routing.

Examples:

    Role: bassline
    Role: drums
    Role: counter melody
    Role: pads

Multiple roles allowed:

    Role:
    - kick
    - bass
    - arp

------------------------------------------------------------------------

## Constraints

Planner + MCP boundary hints.

Examples:

    Constraints:
    - bars: 8
    - density: sparse
    - instruments: kick, sub bass
    - no reverb
    - gm_program: 38

Common keys:

-   bars
-   density
-   instruments
-   effects
-   gm_program
-   arrangement_section

------------------------------------------------------------------------

## Vibe (Producer Idiom Lexicon)

These map to idioms defined in `intent_config.py`.

Examples:

    Vibe:
    - darker
    - punchier
    - wider
    - analog warmth
    - club energy

### Weighted Vibes

    Vibe:
    - darker:2
    - wider:1
    - aggressive:3

Weights influence parameter emphasis during composing or editing.

------------------------------------------------------------------------

## Request

Natural language description of intent.

This remains fully expressive.

Example:

    Request:
    Build an evolving groove that slowly opens into a main loop.

------------------------------------------------------------------------

# ⚙️ Agent Parsing Rules

## 1. Graceful Degradation

If fields are missing:

-   Planner infers from Request
-   Mode still determines routing

## 2. Natural Language Fallback

Users may mix structure and freeform language:

    Mode: compose
    Request:
    give me a darker techno intro at 126 bpm in F#m

## 3. Safety

Structured fields are parsed first. Freeform text is interpreted second.

This reduces jailbreak ambiguity.

------------------------------------------------------------------------

# 🧬 Planner Enhancements

When fields are present:

-   No tempo inference required
-   Key signature locked
-   Style vocabulary mapped immediately
-   Role targets specific arrangement logic
-   Vibe weights bias parameter selection
-   Constraints reduce hallucinated instruments

------------------------------------------------------------------------

# 🚀 FULL EXAMPLES

------------------------------------------------------------------------

## 🎵 Example 1 — Compose (Intro)

    STORI PROMPT
    Mode: compose
    Section: intro
    Target: project
    Style: melodic techno
    Key: F#m
    Tempo: 126

    Role:
    - kick
    - bass
    - arp
    - pad

    Constraints:
    - bars: 16
    - density: medium
    - instruments: analog kick, sub bass, pluck

    Vibe:
    - darker:2
    - hypnotic:3
    - wider:1

    Request:
    Build an intro groove that evolves every 4 bars and opens into a club-ready loop.

------------------------------------------------------------------------

## 🎵 Example 1b — Sequential Compose (Verse after Intro)

Send this as a second prompt after Example 1. The backend resolves
`Position: after intro` from the project state and starts all new
regions at the correct beat automatically.

    STORI PROMPT
    Mode: compose
    Section: verse
    Position: after intro
    Style: melodic techno
    Key: F#m
    Tempo: 126

    Role:
    - kick
    - bass
    - lead

    Constraints:
    - bars: 16
    - density: high

    Vibe:
    - hypnotic:3
    - driving:2

    Request:
    Full verse drop — harder kick, melodic lead riding over the bass.

------------------------------------------------------------------------

## 🎵 Example 1c — Anticipatory Pickup (Before Chorus)

The pickup starts 4 beats before the chorus — a 1-bar drum fill that
leads into the drop.

    STORI PROMPT
    Mode: compose
    Section: pre-chorus fill
    Position: before chorus - 4

    Role: drums

    Constraints:
    - bars: 1
    - density: high

    Vibe:
    - building:3
    - tension:2

    Request:
    1-bar drum fill building into the chorus drop.

------------------------------------------------------------------------

## 🎵 Example 1d — Parallel Layer (Alongside)

Adds a new synth pad layer that runs with the existing verse rather
than following it.

    STORI PROMPT
    Mode: compose
    Section: verse pad layer
    Position: alongside verse

    Role: pads

    Constraints:
    - bars: 16

    Vibe:
    - atmospheric:3
    - warm:2

    Request:
    Slow-moving pad chords underneath the verse — don't crowd the
    existing elements, just add depth.

------------------------------------------------------------------------

## 🎵 Example 1e — Transition Bridge (Between)

Fills the gap between intro and verse with a 4-bar transition.

    STORI PROMPT
    Mode: compose
    Section: transition
    Position: between intro verse

    Role:
    - drums
    - bass

    Constraints:
    - bars: 4

    Vibe:
    - building:2
    - momentum:3

    Request:
    4-bar energy bridge connecting intro to verse — rising energy.

------------------------------------------------------------------------

## 🎚️ Example 2 --- Edit Track

    STORI PROMPT
    Mode: edit
    Target: track:Bass

    Vibe:
    - punchier:3
    - tighter low end:2

    Constraints:
    - compressor: analog
    - eq_focus: 200hz cleanup

    Request:
    Tighten the bass and make it hit harder without increasing loudness.

------------------------------------------------------------------------

## ❓ Example 3 --- Ask / Reasoning

    STORI PROMPT
    Mode: ask
    Target: project

    Request:
    Why does my groove feel late when I add long reverb tails?

------------------------------------------------------------------------

# 🧩 Future Extensions

-   Inline directives: `@track:Drums`
-   Planner confidence scoring
-   MCP tool whitelisting
-   Swift Codable struct mapping
-   Muse variation hints

------------------------------------------------------------------------

# 🌀 Summary

The Stori Structured Prompt provides:

-   Deterministic routing
-   Producer‑native language
-   Planner‑optimized structure
-   MCP‑safe parsing
-   Fully optional fields

Natural language remains first‑class --- this format simply unlocks
**expert‑level control**.

------------------------------------------------------------------------

# Backend Implementation Status

All items are implemented.

| Item | Status | Module |
|------|--------|--------|
| Prompt parser | Done | `app/core/prompt_parser.py` |
| Intent routing gate | Done | `app/core/intent.py` |
| Weighted vibes | Done | `app/core/intent_config.py` |
| Deterministic planner | Done | `app/core/planner.py` |
| Structured prompt context | Done | `app/core/prompts.py` |
| Pipeline threading | Done | `app/core/pipeline.py`, `app/core/compose_handlers.py` |
| Target scope validation | Done | `app/core/tool_validation.py` |
| `Section` / `Position` fields (6 relationships) | Done | `app/core/prompt_parser.py`, `app/core/prompts.py` |
| Entity manifest in tool results | Done | `app/core/compose_handlers.py` |
| Variable reference resolution (`$N.field`) | Done | `app/core/compose_handlers.py` |

**Tests:** `tests/test_prompt_parser.py` (56+), `tests/test_intent_structured.py` (26),
`tests/test_structured_prompt_integration.py` (16), `tests/test_tool_validation.py` (6 new).
All existing tests pass unchanged (zero regression).
