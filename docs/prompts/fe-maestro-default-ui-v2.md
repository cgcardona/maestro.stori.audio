# Maestro Default UI v2 — Frontend Integration Prompt

**Target Agent:** Swift Frontend Engineer (macOS app — SwiftUI)
**Priority:** CRITICAL — Backend is live and verified; frontend should replace hardcoded fallback data with live API responses.

---

## Context

The backend has shipped 4 new endpoints that serve the creative launchpad data. They are live, tested (45 tests, all passing), and verified via curl. The frontend currently uses hardcoded fallback data — these endpoints enable server-driven content.

**Base URL:** `{baseURL}/api/v1`
**Auth:** Endpoints 1–3 are public (no auth). Endpoint 4 requires `Authorization: Bearer <token>`.

The frontend should gracefully fall back to its existing local data if any endpoint returns an error or is unreachable.

---

## Endpoints & Live Response Examples

### 1. GET /api/v1/maestro/ui/placeholders

Rotating strings for the hero prompt input field. No auth required.

```bash
curl -s http://localhost:10001/api/v1/maestro/ui/placeholders
```

**Live response:**

```json
{
    "placeholders": [
        "Describe a groove…",
        "Build a cinematic swell…",
        "Make something nobody has heard before…",
        "A lo-fi beat for a rainy afternoon…",
        "Jazz trio warming up in a dim club…",
        "Epic orchestral buildup to a drop…",
        "Funky bassline with a pocket feel…",
        "Ambient textures for a midnight drive…"
    ]
}
```

**Swift integration:** Replace the hardcoded `placeholders` array with a fetch from this endpoint. Continue cycling every 4 seconds. Fall back to local strings on error.

---

### 2. GET /api/v1/maestro/prompts

Returns 4 randomly sampled STORI PROMPT inspiration cards from a curated pool of 22. Every call returns a different random set. No auth required.

```bash
curl -s http://localhost:10001/api/v1/maestro/prompts
```

**Live response (example — results change on every call):**

```json
{
    "prompts": [
        {
            "id": "lofi_boom_bap",
            "title": "Lo-fi boom bap · Cm · 75 BPM",
            "preview": "Mode: compose · Section: verse\nStyle: lofi hip hop · Key: Cm · 75 BPM\nRole: drums, bass, piano, melody\nVibe: dusty x3, warm x2, melancholic",
            "fullPrompt": "STORI PROMPT\nMode: compose\nSection: verse\nStyle: lofi hip hop\nKey: Cm\nTempo: 75\nRole: [drums, bass, piano, melody]\n..."
        },
        {
            "id": "melodic_techno_drop",
            "title": "Melodic techno drop · Am · 128 BPM",
            "preview": "Mode: compose · Section: drop\nStyle: melodic techno · Key: Am · 128 BPM\nRole: kick, bass, lead, pads, perc\nVibe: hypnotic x3, driving x2, euphoric",
            "fullPrompt": "STORI PROMPT\nMode: compose\nSection: drop\nStyle: melodic techno\n..."
        },
        {
            "id": "jazz_reharmonization",
            "title": "Jazz reharmonization · Bb · 120 BPM",
            "preview": "Mode: compose · Section: bridge\nStyle: bebop jazz · Key: Bb · 120 BPM\nRole: piano, bass, drums\nVibe: jazzy x2, mysterious x2, bittersweet, flowing",
            "fullPrompt": "STORI PROMPT\nMode: compose\nSection: bridge\n..."
        },
        {
            "id": "ambient_drone",
            "title": "Ambient drone · D · 58 BPM",
            "preview": "Mode: compose · Section: intro\nStyle: ambient / drone · Key: D · 58 BPM\nRole: pads, arp, sub drone, texture\nVibe: dreamy x3, atmospheric x2, minimal, peaceful",
            "fullPrompt": "STORI PROMPT\nMode: compose\nSection: intro\n..."
        }
    ]
}
```

**Key design notes:**
- Returns exactly 4 items per call, randomly sampled from 22 curated prompts
- Each `fullPrompt` is a complete STORI PROMPT YAML — inject it verbatim into the compose input on tap
- `preview` shows the first 3–4 YAML lines for the card UI — contains Style, Key, BPM, Role, and Vibe at a glance
- The pool spans: lo-fi boom bap, melodic techno, cinematic orchestral, Afrobeats, ambient drone, jazz reharmonization, dark trap, bossa nova, funk, neo-soul, drum & bass, minimal house, synthwave, post-rock, reggaeton, classical strings, psytrance, indie folk, New Orleans brass, Nordic ambient, flamenco fusion, UK garage

**Swift model:**

```swift
struct PromptItem: Codable, Identifiable {
    let id: String
    let title: String     // "Lo-fi boom bap · Cm · 75 BPM"
    let preview: String   // First 3-4 YAML lines for card display
    let fullPrompt: String // Complete STORI PROMPT — inject on tap
}

struct PromptsResponse: Codable {
    let prompts: [PromptItem]
}
```

**Swift integration:**
- Fetch on app launch (or launchpad view appear)
- Display as horizontal card carousel
- On card tap → inject `fullPrompt` verbatim into the hero prompt input
- Pull-to-refresh or "shuffle" button → refetch for a new set of 4
- Fall back to local hardcoded prompts on error

---

### 3. GET /api/v1/maestro/prompts/{template_id}

Single template lookup by ID. No auth required. Used for named templates, not the random pool.

```bash
curl -s http://localhost:10001/api/v1/maestro/prompts/lofi_chill
```

**Live response:**

```json
{
    "id": "lofi_chill",
    "title": "Lo-fi Chill",
    "fullPrompt": "Lo-fi hip hop beat at 85 BPM with dusty samples, vinyl crackle, and a chill late-night groove",
    "sections": [
        {"heading": "Style", "content": "Lo-fi hip hop, 85 BPM, key of Dm"},
        {"heading": "Arrangement", "content": "4-bar loop, mellow intro"},
        {"heading": "Instruments", "content": "Dusty drums, muted Rhodes, vinyl texture, soft sub bass"},
        {"heading": "Production Notes", "content": "Tape saturation, gentle sidechain, lo-pass filter"},
        {"heading": "Creative Intent", "content": "Late-night study session vibe, nostalgic warmth"}
    ]
}
```

**404 case:**

```bash
curl -s http://localhost:10001/api/v1/maestro/prompts/nonexistent
# → {"detail": "Template not found"}
```

**Valid template IDs:** `lofi_chill`, `dark_trap`, `jazz_trio`, `synthwave`, `cinematic`, `funk_groove`, `ambient`, `deep_house`, `full_production`, `beat_lab`, `mood_piece`

---

### 4. GET /api/v1/maestro/budget/status

Focused budget status for the Creative Fuel UI. **Auth required.**

```bash
curl -s -H "Authorization: Bearer <token>" http://localhost:10001/api/v1/maestro/budget/status
```

**Live response:**

```json
{
    "remaining": 0.0,
    "total": 5.0,
    "state": "exhausted",
    "sessionsUsed": 86
}
```

**State enum (authoritative — server is source of truth):**

| Condition         | State        |
|-------------------|--------------|
| remaining ≤ 0     | `exhausted`  |
| remaining < 0.25  | `critical`   |
| remaining < 1.0   | `low`        |
| else              | `normal`     |

**Swift model:**

```swift
struct BudgetStatus: Codable {
    let remaining: Double
    let total: Double
    let state: String    // "normal" | "low" | "critical" | "exhausted"
    let sessionsUsed: Int
}
```

**Swift integration:** Fetch on Creative Fuel view load. Map `state` to your `WarningLevel` enum. Real-time updates during composition still come via SSE `budgetUpdate` events.

---

## What Was Removed (v1 → v2)

The following endpoints from the previous design have been **removed**:
- `GET /api/v1/maestro/prompts/chips` — replaced by the random prompts pool
- `GET /api/v1/maestro/prompts/cards` — replaced by the random prompts pool

The new `/api/v1/maestro/prompts` endpoint supersedes both. Each `fullPrompt` is now a complete, expert-level STORI PROMPT that exercises the full spec (Harmony, Melody, Rhythm, Dynamics, Orchestration, Effects, Expression, Texture, MidiExpressiveness) — significantly richer than the simple text strings from the old chips.

---

## Implementation Checklist

1. Add API client methods for all 4 endpoints
2. On launchpad view appear: fetch placeholders and prompts concurrently; fall back on error
3. Render prompts as horizontal card carousel; show `title` large, `preview` as monospace/code snippet below
4. On card tap: inject `fullPrompt` verbatim into the hero prompt input
5. Add "Shuffle" / pull-to-refresh → refetch `/maestro/prompts` for a new random 4
6. Budget status: fetch on Creative Fuel view appear; real-time updates still via SSE
7. All endpoints degrade gracefully — app must work offline

---

## Verification

After wiring up, verify:
- 4 cards render on launchpad with distinct styles
- Repeated refreshes return different card sets
- Tapping a card injects the full STORI PROMPT YAML into the compose input
- Budget fuel badge reflects the `state` field
- App works offline with hardcoded fallback data
