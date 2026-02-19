# Muse Protocol --- Language Agnostic Specification

Status: Canonical Source of Truth Version: v1.0 Audience: Any frontend,
backend, or tool implementing Muse

------------------------------------------------------------------------

# 0. PURPOSE

Muse defines a **platform-neutral protocol** for proposing, reviewing,
auditioning, approving, and committing musical changes.

Muse is NOT tied to: - Swift - Python - Stori - Maestro - Any DAW or UI
framework

Muse defines WHAT must happen --- not HOW it is implemented.

------------------------------------------------------------------------

# 1. DESIGN PRINCIPLES

1.  Non‑destructive by default
2.  Humans approve musical mutation
3.  Beat‑based time (never seconds)
4.  Deterministic streaming order
5.  Language and platform agnostic
6.  Canonical state MUST NOT change during review

------------------------------------------------------------------------

# 2. TERMINOLOGY (NORMATIVE)

  Term              Meaning
  ----------------- -----------------------------------------
  Muse              System proposing musical ideas
  Variation         Structured change proposal
  Phrase            Independently reviewable musical region
  NoteChange        Atomic note delta
  Canonical State   Actual project state
  Proposed State    Ephemeral variation preview

------------------------------------------------------------------------

# 3. EXECUTION MODES

Execution mode is decided by backend intent classification.

  Intent      Mode        Behavior
  ----------- ----------- -----------------
  COMPOSING   variation   Requires review
  EDITING     apply       Immediate
  REASONING   none        Chat only

Frontends MUST NOT override execution mode.

------------------------------------------------------------------------

# 4. LIFECYCLE

1.  Variation Proposed
2.  Meta Event Streamed
3.  Phrase Events Streamed
4.  Review Mode Active
5.  Accept OR Discard

Canonical mutation occurs ONLY after commit.

------------------------------------------------------------------------

# 5. IDENTIFIERS

All requests MUST include:

-   `projectId`
-   `variationId`
-   `baseStateId`
-   `requestId` (optional but recommended)

`baseStateId` enables optimistic concurrency.

------------------------------------------------------------------------

# 6. KEY NAMING CONTRACT

There is exactly one canonical key name per concept. No aliases, no fallbacks.

**Casing rule:** All JSON on the wire uses **camelCase**. Python internals use snake_case. Swift internals use camelCase. MCP tool names use snake_case (MCP convention).

## Project Context (frontend → backend)

Sent as the `project` field in compose requests. Entities use `"id"` as their self-identifier. All keys are **camelCase**.

``` json
{
  "id": "uuid",
  "name": "My Project",
  "tempo": 120,
  "key": "C",
  "timeSignature": "4/4",
  "tracks": [
    {
      "id": "uuid",
      "name": "Drums",
      "drumKitId": "acoustic",
      "gmProgram": null,
      "regions": [
        {
          "id": "uuid",
          "name": "Pattern 1",
          "startBeat": 0,
          "durationBeats": 16,
          "noteCount": 24
        }
      ]
    }
  ],
  "buses": [
    { "id": "uuid", "name": "Reverb" }
  ]
}
```

Rules:

-   Project's own ID: `"id"` (not `"projectId"` — that's for cross-references)
-   Entity self-IDs: always `"id"` (never `"trackId"`, `"regionId"`, or `"busId"`)
-   Regions: always `"regions"` (never `"midiRegions"`)
-   Key: always `"key"` (never `"keySignature"`)
-   Time signature: always `"timeSignature"` (never `"time_signature"`)
-   Track instrument: `"drumKitId"` and `"gmProgram"` (camelCase)
-   Region timing: `"startBeat"` and `"durationBeats"` (camelCase)
-   Notes may be omitted (send `"noteCount"` instead); backend preserves prior note data

## Tool Call Events (backend → frontend)

Tool names use **snake_case** (MCP convention). Parameters use **camelCase**.

``` json
{
  "type": "toolCall",
  "name": "stori_add_notes",
  "params": {
    "regionId": "uuid",
    "notes": [...]
  }
}
```

## SSE Events (backend → frontend)

All SSE event data uses **camelCase** for both type values and payload keys.

Event type values: `state`, `status`, `content`, `reasoning`, `planSummary`, `progress`, `meta`, `phrase`, `done`, `complete`, `toolCall`, `toolError`, `budgetUpdate`, `error`.

``` json
{
  "type": "meta",
  "variationId": "uuid",
  "baseStateId": "string",
  "aiExplanation": "string|null",
  "affectedTracks": ["uuid"],
  "affectedRegions": ["uuid"],
  "noteCounts": { "added": 0, "removed": 0, "modified": 0 }
}
```

------------------------------------------------------------------------

# 7. EVENT ENVELOPE (STREAMING CONTRACT)

All streaming messages MUST use camelCase keys:

``` json
{
  "type": "meta|phrase|done|error|heartbeat",
  "sequence": 1,
  "variationId": "uuid",
  "projectId": "uuid",
  "baseStateId": "uuid",
  "timestampMs": 0,
  "payload": {}
}
```

Rules:

-   sequence strictly increasing
-   meta MUST be first
-   done MUST be last

------------------------------------------------------------------------

# 8. DATA MODELS

All wire-format models use **camelCase** keys.

## Variation (meta event)

``` json
{
  "variationId": "uuid",
  "intent": "string",
  "aiExplanation": "string|null",
  "noteCounts": {
    "added": 0,
    "removed": 0,
    "modified": 0
  }
}
```

## Phrase

``` json
{
  "phraseId": "uuid",
  "trackId": "uuid",
  "regionId": "uuid",
  "startBeat": 0.0,
  "endBeat": 4.0,
  "label": "Bars 1-4",
  "noteChanges": [],
  "controllerChanges": []
}
```

## NoteChange

``` json
{
  "noteId": "uuid",
  "changeType": "added|removed|modified",
  "before": { "pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100, "channel": 0 },
  "after": { "pitch": 62, "startBeat": 0, "durationBeats": 1, "velocity": 100, "channel": 0 }
}
```

Rules:

added → before MUST be null\
removed → after MUST be null\
modified → both present

------------------------------------------------------------------------

# 9. BACKEND RESPONSIBILITIES

Backend MUST:

-   classify intent
-   construct proposed state
-   compute phrases
-   stream envelopes
-   enforce ordering
-   validate base_state_id on commit
-   apply accepted phrases atomically

Backend MUST NEVER mutate canonical state during proposal.

------------------------------------------------------------------------

# 10. FRONTEND RESPONSIBILITIES

Frontend MUST:

-   render proposed vs canonical state
-   provide audition modes:
    -   Original
    -   Variation
    -   Delta
-   allow partial phrase acceptance
-   send commit/discard requests

------------------------------------------------------------------------

# 11. AUDITION MODES (CONCEPTUAL)

Original → canonical only\
Variation → proposed state\
Delta → changed notes only

Implementation is language specific.

------------------------------------------------------------------------

# 12. FAILURE RULES

If stream fails: - keep received phrases - allow discard

If commit rejected: - frontend MUST regenerate variation

------------------------------------------------------------------------

# 13. SAFETY MODEL

-   review mode must be isolated
-   destructive edits should be blocked
-   commit is single undo boundary

------------------------------------------------------------------------

# 14. PHILOSOPHY

Muse is a protocol for **human‑guided AI creativity**.

AI proposes. Humans curate.
