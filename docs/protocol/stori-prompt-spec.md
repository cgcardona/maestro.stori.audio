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

## 🎵 Example 1 --- Compose (Advanced)

    STORI PROMPT
    Mode: compose
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

**Tests:** `tests/test_prompt_parser.py` (56), `tests/test_intent_structured.py` (26),
`tests/test_structured_prompt_integration.py` (16), `tests/test_tool_validation.py` (6 new).
All existing tests pass unchanged (zero regression).
