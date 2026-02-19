# FE Agent Prompt — Project State Sync for Sequential Composition

## Context

Stori Maestro's backend resolves every compose request against a live snapshot
of the current DAW project. If that snapshot is absent or incomplete, several
critical features silently break:

| Feature | What breaks without state |
|---|---|
| `Position: after intro` | Can't find "intro" → defaults to beat 0, overwrites existing content |
| `Position: before chorus - 4` | Same — all named section references resolve to beat 0 |
| Entity-aware editing | LLM gets told "no tracks" even when the project has 12 tracks |
| Duplicate-ID prevention | Server can't check for collisions — may emit IDs the DAW already has |
| Conversation continuity | LLM context re-built from scratch each turn |

The backend is already designed to accept and use this state. The missing piece
is the client serializing and sending it on every request.

---

## Backend contract (already live)

### Request — `POST /api/v1/maestro/stream`

```json
{
  "prompt": "<raw prompt text — preserve verbatim, including STORI PROMPT header>",
  "conversation_id": "8fa3c1d2-...",
  "model": null,
  "store_prompt": true,
  "project": {
    "projectId": "7e4b0f91-...",
    "name": "Rain Check",
    "tempo": 92,
    "key": "Dm",
    "timeSignature": { "numerator": 4, "denominator": 4 },
    "tracks": [
      {
        "id": "abc-123-uuid",
        "name": "Drums",
        "drumKitId": "TR-909",
        "volume": 0.8,
        "pan": 0.5,
        "muted": false,
        "solo": false,
        "color": "blue",
        "regions": [
          {
            "id": "def-456-uuid",
            "name": "Intro",
            "startBeat": 0,
            "durationBeats": 16,
            "noteCount": 32
          }
        ]
      },
      {
        "id": "ghi-789-uuid",
        "name": "Bass",
        "gmProgram": 33,
        "volume": 0.75,
        "pan": 0.5,
        "muted": false,
        "solo": false,
        "color": "green",
        "regions": [
          {
            "id": "jkl-012-uuid",
            "name": "Intro",
            "startBeat": 0,
            "durationBeats": 16,
            "noteCount": 24
          }
        ]
      }
    ],
    "buses": [
      {
        "id": "mno-345-uuid",
        "name": "Reverb"
      }
    ]
  }
}
```

### `project` field — field-by-field spec

| Field | Type | Required | Notes |
|---|---|---|---|
| `projectId` | `String` (UUID) | ✓ | Stable ID for the project. Persists StateStore across requests. If absent the backend uses `conversation_id` as fallback — still send it. |
| `name` | `String` | ✓ | Project name shown in LLM context |
| `tempo` | `Double` | ✓ | Current BPM |
| `key` | `String` | ✓ | Key signature, e.g. `"Dm"`, `"Am"`, `"C"` |
| `timeSignature` | `Object` | ✓ | `{"numerator": 4, "denominator": 4}` — also accepts `"4/4"` string |
| `tracks` | `[Track]` | ✓ | All current tracks, even empty ones |
| `tracks[].id` | `String` (UUID) | ✓ | The server-assigned trackId (see SSE section below) |
| `tracks[].name` | `String` | ✓ | Track name — used for fuzzy entity resolution |
| `tracks[].gmProgram` | `Int?` | — | GM program number for melodic tracks |
| `tracks[].drumKitId` | `String?` | — | Drum kit ID for drum tracks |
| `tracks[].volume` | `Double` | — | 0.0–1.0 |
| `tracks[].pan` | `Double` | — | 0.0 (left) – 1.0 (right) |
| `tracks[].muted` | `Bool` | — | |
| `tracks[].solo` | `Bool` | — | |
| `tracks[].color` | `String?` | — | |
| `tracks[].regions` | `[Region]` | ✓ | All regions in the track |
| `tracks[].regions[].id` | `String` (UUID) | ✓ | Server-assigned regionId |
| `tracks[].regions[].name` | `String` | ✓ | Region name — **critical for `Position:` resolution** (e.g. `Position: after intro` scans for regions named "intro") |
| `tracks[].regions[].startBeat` | `Double` | ✓ | Beat offset from project start — used to calculate arrangement positions |
| `tracks[].regions[].durationBeats` | `Double` | ✓ | Length in beats — used to calculate end beat |
| `tracks[].regions[].noteCount` | `Int?` | — | Optional, improves LLM context |
| `buses` | `[Bus]` | — | Omit if empty |
| `buses[].id` | `String` (UUID) | ✓ | Server-assigned busId |
| `buses[].name` | `String` | ✓ | Bus name |

### SSE stream response — what to capture

The stream emits newline-delimited `data: {json}\n\n` SSE events. All event type names are **camelCase** (e.g. `toolCall`, `toolStart`, `planStepUpdate`). The frontend already processes most of them. Two event types carry the state you need to feed back:

#### `toolCall` — apply to DAW and **store the server-assigned ID**

```json
{
  "type": "toolCall",
  "id": "call_abc",
  "name": "stori_add_midi_track",
  "params": {
    "trackId": "abc-123-uuid",
    "name": "Drums",
    "drumKitId": "TR-909",
    "color": "blue",
    "volume": 0.8
  }
}
```

```json
{
  "type": "toolCall",
  "id": "call_def",
  "name": "stori_add_midi_region",
  "params": {
    "regionId": "def-456-uuid",
    "trackId": "abc-123-uuid",
    "name": "Intro",
    "startBeat": 0,
    "durationBeats": 16
  }
}
```

```json
{
  "type": "toolCall",
  "id": "call_ghi",
  "name": "stori_ensure_bus",
  "params": {
    "busId": "mno-345-uuid",
    "name": "Reverb"
  }
}
```

**For every `toolCall` event, extract and persist the server-assigned ID:**

| Tool name | ID field to capture | Store on |
|---|---|---|
| `stori_add_midi_track` | `params.trackId` | Track object |
| `stori_add_track` | `params.trackId` | Track object |
| `stori_add_midi_region` | `params.regionId` | Region object |
| `stori_add_region` | `params.regionId` | Region object |
| `stori_duplicate_region` | `params.newRegionId` | New region object |
| `stori_ensure_bus` | `params.busId` | Bus object |

All other params (`name`, `startBeat`, `durationBeats`, `drumKitId`,
`gmProgram`, etc.) are also authoritative — use them to populate the
corresponding DAW entity so the next `project` snapshot is accurate.

#### `plan` and `planStepUpdate` — structured progress checklist (EDITING)

In EDITING mode the backend also emits a structured plan before tool execution. These do not carry state that needs to be fed back in the next request — they are display-only:

- **`plan`**: `{ "type": "plan", "planId": "uuid", "title": "Creating lo-fi intro (Cm, 72 BPM)", "steps": [{ "stepId": "1", "label": "...", "status": "pending", "detail": "..." }] }` — render as a checklist card.
- **`planStepUpdate`**: `{ "type": "planStepUpdate", "stepId": "1", "status": "active" | "completed" | "failed" | "skipped", "result": "optional" }` — update the corresponding step's status icon.

See [api.md](../reference/api.md) for the full event reference.

#### `toolCall` for mutations — update existing entities

For tools that mutate existing entities (e.g. `stori_add_notes`,
`stori_set_tempo`, `stori_set_key`, `stori_move_region`,
`stori_set_track_volume`), apply the change to the DAW model and reflect it in
the next `project` snapshot. Key mutations to track:

| Tool | What to update in `project` |
|---|---|
| `stori_add_notes` | `regions[].noteCount` (increment by notes.length) |
| `stori_set_tempo` | `project.tempo` |
| `stori_set_key` / `stori_set_key_signature` | `project.key` |
| `stori_move_region` | `regions[].startBeat` |
| `stori_set_track_name` | `tracks[].name` |
| `stori_delete_region` | remove region from `tracks[].regions` |
| `stori_mute_track` | `tracks[].muted` |
| `stori_solo_track` | `tracks[].solo` |

---

## The critical round-trip

```
Request 1 (empty project, first prompt):
  project: { "projectId": "proj-uuid", "tracks": [], "buses": [], ... }

  → SSE emits:
      toolCall  stori_add_midi_track  { trackId: "abc-123", name: "Drums", ... }
      toolCall  stori_add_midi_region { regionId: "def-456", trackId: "abc-123",
                                         name: "Intro", startBeat: 0, durationBeats: 16 }
      toolCall  stori_add_notes       { regionId: "def-456", notes: [...] }

  FE stores: track abc-123, region def-456 (name="Intro", start=0, dur=16)

Request 2 (Verse prompt with `Position: after intro`):
  project: {
    "projectId": "proj-uuid",
    "tracks": [
      { "id": "abc-123", "name": "Drums", "regions": [
          { "id": "def-456", "name": "Intro", "startBeat": 0, "durationBeats": 16 }
      ]}
    ]
  }

  → Backend resolves "after intro" → beat 16 ✓
     New regions placed at startBeat >= 16, no overlap with Intro ✓
```

Without the state in Request 2, `resolve_position` finds no tracks,
defaults to beat 0, and the Verse overwrites the Intro.

---

## Task

### 1. Find the compose request builder

Locate wherever `POST /api/v1/maestro/stream` is constructed — likely a
`MaestroService`, `MaestroViewModel`, or `APIClient` method. It currently
passes `project: nil` or `project: [:]`.

### 2. Serialize the full project snapshot

Add a method (e.g. `func currentProjectSnapshot() -> [String: Any]`) that
walks the DAW model and returns a dictionary matching the spec above. Use the
DAW's native model objects — do not reconstruct from memory.

Key points:
- **Use the server-assigned IDs.** Track/region/bus IDs stored from toolCall
  events, not IDs generated by the DAW client. If the DAW assigns its own
  internal IDs, maintain a mapping (`dawLocalId → serverAssignedId`) and use
  the server ID in the snapshot.
- **Include every track**, even tracks with no regions (the backend needs them
  for entity resolution).
- **Include region name.** This is the token the `Position:` resolver matches
  against. If the DAW doesn't have a region name, use the track name as
  fallback (e.g. `"Drums Intro"`).
- **Include `startBeat` and `durationBeats`** for every region — they are
  required for beat arithmetic.
- **Do not include full `notes` arrays** unless the request specifically needs
  note-level editing context (it adds significant payload with no benefit for
  composition flows).

### 3. Capture server-assigned IDs from SSE events

Find wherever the app processes the SSE `toolCall` event. After forwarding the
call to the DAW engine, also persist the ID fields listed in the table above.

Pseudocode:

```swift
func handleToolCall(_ event: ToolCallEvent) {
    dawEngine.apply(event)          // existing logic

    // Capture server-assigned entity IDs
    switch event.name {
    case "stori_add_midi_track", "stori_add_track":
        if let trackId = event.params["trackId"] as? String,
           let name = event.params["name"] as? String {
            entityStore.registerTrack(serverTrackId: trackId, name: name, params: event.params)
        }
    case "stori_add_midi_region", "stori_add_region":
        if let regionId = event.params["regionId"] as? String,
           let trackId = event.params["trackId"] as? String,
           let name = event.params["name"] as? String {
            entityStore.registerRegion(
                serverRegionId: regionId,
                parentTrackId: trackId,
                name: name,
                startBeat: event.params["startBeat"] as? Double ?? 0,
                durationBeats: event.params["durationBeats"] as? Double ?? 4
            )
        }
    case "stori_duplicate_region":
        if let newRegionId = event.params["newRegionId"] as? String { ... }
    case "stori_ensure_bus":
        if let busId = event.params["busId"] as? String,
           let name = event.params["name"] as? String {
            entityStore.registerBus(serverBusId: busId, name: name)
        }
    default:
        break
    }
}
```

### 4. Persist `conversation_id`

If not already done:
- Generate a UUID on first compose request for each project session.
- Store it (e.g. in `UserDefaults`, in-memory per project, or in the project
  document).
- Send the same `conversation_id` on every subsequent request for that session.
- Reset (generate a new UUID) when the user starts a new project or explicitly
  clears history.

### 5. Preserve prompt text verbatim

When the user types or pastes a Stori Structured Prompt (starts with
`STORI PROMPT`), send the raw text to the backend without any preprocessing,
trimming of blank lines, or encoding. The YAML parser is whitespace-sensitive.

---

## Acceptance criteria

- [ ] `project.tracks` in every compose request contains all current tracks,
  each with a server-assigned `id` UUID (not a placeholder or DAW-local ID).
- [ ] `project.tracks[].regions` contains all current regions with server-
  assigned `id`, correct `name`, `startBeat`, and `durationBeats`.
- [ ] After "Prompt 1 — Intro" completes, submitting "Prompt 2" with
  `Position: after intro` places new regions at beat 16 (or wherever Intro
  ends), not beat 0.
- [ ] `conversation_id` is the same UUID for all prompts within a project
  session and is present on every request.
- [ ] `project.projectId` is present and stable for the lifetime of the project.
- [ ] Structured prompts that start with `STORI PROMPT` arrive at the backend
  with the header intact and all YAML indentation preserved.
- [ ] `stori_ensure_bus` tool calls result in the bus appearing in
  `project.buses` on the next request.
- [ ] No client-generated UUIDs appear as entity IDs in the project snapshot —
  only IDs echoed back from `toolCall` events.
