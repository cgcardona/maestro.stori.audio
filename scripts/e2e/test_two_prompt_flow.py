#!/usr/bin/env python3
"""
End-to-end two-prompt STORI PROMPT flow test.

Prompt 1 → empty project → EDITING → tool calls applied → project built
Prompt 2 → project with content → COMPOSING → variation returned

Usage:
    python scripts/e2e/test_two_prompt_flow.py <jwt-token>
    python scripts/e2e/test_two_prompt_flow.py <jwt-token> --api https://stage.stori.audio/api/v1
"""

import sys
import json
import uuid
import argparse
import urllib.request
import urllib.error

# ─── Prompts ──────────────────────────────────────────────────────────────────

PROMPT_1 = """\
STORI PROMPT
Mode: compose
Section: intro
Position: at 0
Style: lo-fi jazz
Key: Cm
Tempo: 72
Role: [pads, piano]
Constraints:
  bars: 4
  density: very sparse
Vibe: [dreamy x3, suspended, melancholic x2]
Request: |
  Floating intro — just pads and a single piano voice. One note at a time.
  Like walking into an empty room that used to mean something.

Harmony:
  progression: [Cm(maj7), Abmaj9, Ebmaj7, Bb7sus4]
  voicing: open, widely spaced — avoid anything below C3
  color: |
    Cm(maj7) is the key chord — the major 7th (B natural) creates
    beautiful dissonance against the minor tonality. Let it breathe.
  rhythm: one chord per bar, played on beat 1 and held

Melody:
  scale: C natural minor with chromatic passing tones
  register: high (G5–C6)
  contour: |
    Three descending phrases, each starting a step lower than the last.
    Leave at least 2 bars of silence in bar 4 — just the pad sustaining.
  density: one note every 2–3 beats
  touch: very soft, as if remembering something

Orchestration:
  piano:
    technique: single notes only — no chords in the right hand
    pedaling: full pedal throughout, let harmonics blur
    velocity: pp (30–45)
  pads:
    texture: slow attack (3–4 seconds), very long sustain
    register: wide — low strings and high shimmer simultaneously
    movement: subtle filter motion, like breathing

Effects:
  piano:
    reverb:
      type: large hall
      decay: 4.5s
      predelay: 20ms
      wet: 45%
    tape: slight flutter, subtle warble
  pads:
    reverb:
      type: infinite shimmer
      wet: 60%

Expression:
  arc: suspension → longing
  narrative: |
    3am. Rain on the window. You're in the kitchen with the light off
    because you don't want to fully be awake yet. The city hums.
  spatial_image: |
    Piano is slightly left of center, intimate. Pads are everywhere —
    wide, diffuse, as if the sound is the room itself.

Texture:
  density: extremely sparse — single-note melody, sustained pads only
  principle: what you don't play is as important as what you do
"""

PROMPT_2 = """\
STORI PROMPT
Mode: compose
Section: verse
Position: after intro
Style: lo-fi hip hop
Key: Cm
Tempo: 72
Role: [drums, bass, piano, melody]
Constraints:
  bars: 16
  density: medium-sparse
Vibe: [dusty x3, warm x2, laid back, melancholic]
Request: |
  The full groove drops in — but gently. Lazy boom bap, deep bass,
  rootless piano chord stabs, and the melody continues from the intro.
  Nothing is rushed. Everything is slightly behind the beat.

Harmony:
  progression: [Cm7, Abmaj7, Ebmaj7, Bb7]
  bars_per_chord: 2
  voicing: |
    Piano plays rootless close-position voicings:
    - Cm7 → Eb G Bb D (3rd, 5th, 7th, 9th — no root)
    - Abmaj7 → C Eb G (3rd, 5th, 7th — no root)
    - Ebmaj7 → G Bb D (3rd, 5th, 7th — no root)
    - Bb7 → D F Ab (3rd, 5th, 7th — no root)
  rhythm: half-note stabs — beat 1 and beat 3 of each bar
  color: |
    The 9th on Cm7 (D) is the emotional center. Feature it in the melody.

Melody:
  scale: C dorian (raised 6th adds brightness against the minor)
  register: mid (Bb4–G5)
  contour: |
    2-bar phrases: ascending to a peak on bar 2, then descending.
    Bar 4 of every 4-bar group: silence, let the drums breathe.
  ornamentation:
    - grace notes before beat 1 of each phrase
    - occasional blue note (Eb in a dorian context — the flatted 3rd as passing tone)
  voice_leading: stepwise motion preferred, no leaps larger than a 5th

Rhythm:
  feel: behind the beat — 30ms average delay on snare and bass
  swing: 54%
  subdivision: 16th-note feel
  kick:
    pattern: beat 1 (strong) + beat 2.5 (ghost) + beat 3.5 (pushes bar 4)
    velocity_variation: ±15 between hits
  snare:
    pattern: beat 2 and beat 4
    ghost_notes:
      density: every 3rd 16th
"""

# ─── Helpers ──────────────────────────────────────────────────────────────────

def hdr(text: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {text}")
    print('─'*60)

def ok(text: str) -> None:
    print(f"  ✅ {text}")

def info(text: str) -> None:
    print(f"  ℹ️  {text}")

def warn(text: str) -> None:
    print(f"  ⚠️  {text}")

def fail(text: str) -> None:
    print(f"  ❌ {text}")


def stream_maestro(api: str, token: str, prompt: str, project: dict,
                   conversation_id: str, label: str) -> list[dict]:
    """POST to /maestro/stream and collect all SSE events. Returns list of parsed event dicts."""
    body = json.dumps({
        "prompt": prompt,
        "project": project,
        "conversationId": conversation_id,
        "storePrompt": False,
    }).encode()

    req = urllib.request.Request(
        f"{api}/maestro/stream",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    events: list[dict] = []
    print(f"\n  Streaming {label}...")

    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").rstrip("\n")
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            events.append(ev)
            t = ev.get("type", "?")
            # Print terse per-event output
            if t == "state":
                print(f"    📡 state={ev.get('state')}  intent={ev.get('intent')}  confidence={ev.get('confidence')}")
            elif t == "status":
                print(f"    📡 status: {ev.get('message')}")
            elif t == "planSummary":
                print(f"    📡 planSummary: {ev.get('totalSteps')} steps  "
                      f"(generations={ev.get('generations')} edits={ev.get('edits')})")
            elif t == "progress":
                cur, tot = ev.get("currentStep", "?"), ev.get("totalSteps", "?")
                msg = ev.get("message", "")
                print(f"    📡 progress {cur}/{tot}: {msg}")
            elif t == "toolStart":
                print(f"    📡 toolStart: {ev.get('label')}")
            elif t == "toolCall":
                name = ev.get("name", "?")
                params = ev.get("params", {})
                note_count = len(params.get("notes", [])) if "notes" in params else None
                extra = f"  [{note_count} notes]" if note_count is not None else ""
                print(f"    📡 toolCall: {name}{extra}")
            elif t == "toolError":
                print(f"    📡 toolError: {ev.get('name')} — {ev.get('error')}")
            elif t == "meta":
                print(f"    📡 meta: variationId={ev.get('variationId', '?')[:8]}...  "
                      f"baseStateId={ev.get('baseStateId')}  "
                      f"noteCounts={ev.get('noteCounts')}")
            elif t == "phrase":
                print(f"    📡 phrase: {ev.get('phraseId', '?')[:8]}...  "
                      f"beats {ev.get('startBeat')}–{ev.get('endBeat')}  "
                      f"changes={len(ev.get('noteChanges', []))}")
            elif t == "done":
                print(f"    📡 done: variationId={ev.get('variationId', '?')[:8]}...  "
                      f"phraseCount={ev.get('phraseCount')}")
            elif t == "complete":
                print(f"    📡 complete: success={ev.get('success')}  "
                      f"variationId={str(ev.get('variationId',''))[:8] or 'n/a'}  "
                      f"toolCalls={len(ev.get('toolCalls') or [])}  "
                      f"totalChanges={ev.get('totalChanges', 'n/a')}")
            elif t == "budgetUpdate":
                print(f"    📡 budgetUpdate: remaining=${ev.get('budgetRemaining'):.2f}  "
                      f"cost=${ev.get('cost', 0):.4f}")
            elif t == "content":
                snippet = ev.get("content", "")[:80].replace("\n", " ")
                print(f"    📡 content: \"{snippet}\"")
            elif t == "reasoning":
                snippet = ev.get("content", ev.get("delta", ""))[:60].replace("\n", " ")
                print(f"    📡 reasoning: \"{snippet}...\"")
            elif t == "error":
                print(f"    📡 ERROR: {ev.get('message')}")
            else:
                print(f"    📡 {t}: {str(ev)[:80]}")

    return events


def build_project_from_editing_events(events: list[dict], tempo: int, key: str) -> dict:
    """
    Reconstruct a project context from the toolCall SSE events emitted during
    an EDITING session. This is what the frontend does after prompt 1 completes —
    it builds a project context from the DAW state (which was updated by each toolCall).

    We parse stori_add_midi_track, stori_add_midi_region, and stori_add_notes
    to reconstruct tracks → regions → note counts.
    """
    tracks: dict[str, dict] = {}        # trackId → {id, name, gmProgram, drumKitId, regions: {}}
    regions: dict[str, dict] = {}       # regionId → {id, name, trackId, startBeat, durationBeats, noteCount}
    note_counts: dict[str, int] = {}    # regionId → count

    for ev in events:
        if ev.get("type") != "toolCall":
            continue
        name = ev.get("name", "")
        params = ev.get("params", {})

        if name in ("stori_add_midi_track", "stori_add_track"):
            tid = params.get("trackId")
            if tid:
                tracks[tid] = {
                    "id": tid,
                    "name": params.get("name", "Track"),
                    "gmProgram": params.get("gmProgram"),
                    "drumKitId": params.get("drumKitId"),
                    "volume": params.get("volume", 0.8),
                    "pan": params.get("pan", 0.5),
                    "regions": [],
                }

        elif name in ("stori_add_midi_region", "stori_add_region"):
            rid = params.get("regionId")
            tid = params.get("trackId")
            if rid and tid:
                regions[rid] = {
                    "id": rid,
                    "name": params.get("name", "Region"),
                    "startBeat": params.get("startBeat", 0),
                    "durationBeats": params.get("durationBeats", 16),
                    "noteCount": 0,
                }
                # Link to track
                if tid in tracks:
                    tracks[tid]["regions"].append(rid)

        elif name == "stori_add_notes":
            rid = params.get("regionId")
            notes = params.get("notes", [])
            if rid:
                note_counts[rid] = note_counts.get(rid, 0) + len(notes)

    # Apply note counts and assemble final project structure
    for rid, count in note_counts.items():
        if rid in regions:
            regions[rid]["noteCount"] = count

    # Build tracks list in insertion order, each with its regions
    tracks_list = []
    for tid, track in tracks.items():
        track_regions = [
            {k: v for k, v in regions[rid].items() if k != "trackId"}
            for rid in track["regions"]
            if rid in regions
        ]
        track_out = {
            "id": track["id"],
            "name": track["name"],
            "regions": track_regions,
        }
        if track.get("gmProgram") is not None:
            track_out["gmProgram"] = track["gmProgram"]
        if track.get("drumKitId"):
            track_out["drumKitId"] = track["drumKitId"]
        tracks_list.append(track_out)

    return {
        "id": str(uuid.uuid4()),
        "name": "Stori Session",
        "tempo": tempo,
        "key": key,
        "timeSignature": "4/4",
        "tracks": tracks_list,
        "buses": [],
    }


def summarise_prompt1(events: list[dict]) -> None:
    tool_calls = [e for e in events if e.get("type") == "toolCall"]
    tracks = [e for e in tool_calls if e.get("name") in ("stori_add_midi_track", "stori_add_track")]
    regions = [e for e in tool_calls if e.get("name") in ("stori_add_midi_region", "stori_add_region")]
    notes = [e for e in tool_calls if e.get("name") == "stori_add_notes"]
    total_notes = sum(len(e.get("params", {}).get("notes", [])) for e in notes)
    complete = next((e for e in events if e.get("type") == "complete"), None)
    errors = [e for e in events if e.get("type") == "toolError"]

    ok(f"{len(tracks)} tracks created")
    ok(f"{len(regions)} regions created")
    ok(f"{total_notes} total notes written across {len(notes)} stori_add_notes calls")
    if errors:
        warn(f"{len(errors)} tool validation errors (non-fatal)")
    if complete and complete.get("success"):
        ok(f"complete event received  stateVersion={complete.get('stateVersion', 'n/a')}")
    else:
        fail("no successful complete event")


def summarise_prompt2(events: list[dict]) -> None:
    meta = next((e for e in events if e.get("type") == "meta"), None)
    phrases = [e for e in events if e.get("type") == "phrase"]
    done = next((e for e in events if e.get("type") == "done"), None)
    complete = next((e for e in events if e.get("type") == "complete"), None)
    errors = [e for e in events if e.get("type") == "toolError"]

    if meta:
        ok(f"meta event received")
        ok(f"  variationId:  {meta.get('variationId')}")
        ok(f"  baseStateId:  {meta.get('baseStateId')}")
        ok(f"  noteCounts:   {meta.get('noteCounts')}")
        ok(f"  affectedTracks:  {len(meta.get('affectedTracks', []))} track(s)")
        ok(f"  affectedRegions: {len(meta.get('affectedRegions', []))} region(s)")
    else:
        fail("no meta event — variation was not generated")

    if phrases:
        ok(f"{len(phrases)} phrase events received")
        total_changes = sum(len(p.get("noteChanges", [])) for p in phrases)
        ok(f"{total_changes} total note changes across all phrases")
    else:
        fail("no phrase events")

    if done:
        ok(f"done event received  phraseCount={done.get('phraseCount')}")
    else:
        fail("no done event")

    if complete and complete.get("success"):
        ok(f"complete event received  totalChanges={complete.get('totalChanges', 'n/a')}")
    else:
        fail("no successful complete event")

    if errors:
        warn(f"{len(errors)} tool error event(s) during variation generation")

    # Print what the frontend needs for the commit flow
    if meta:
        print()
        info("FE commit payload (what to send to /variation/commit after user approves):")
        print(json.dumps({
            "variationId": meta.get("variationId"),
            "projectId": meta.get("projectId", "<from project context>"),
            "baseStateId": meta.get("baseStateId"),
            "acceptedPhraseIds": [p.get("phraseId") for p in phrases],
        }, indent=2))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("token")
    parser.add_argument("--api", default="http://localhost:10001/api/v1")
    args = parser.parse_args()

    token = args.token
    api = args.api.rstrip("/")
    conv_id = str(uuid.uuid4())  # shared conversation thread for both prompts

    print("=" * 60)
    print("  STORI Two-Prompt E2E Flow Test")
    print("=" * 60)
    print(f"  API:  {api}")
    print(f"  Conv: {conv_id[:8]}...")

    # ── PROMPT 1 ──────────────────────────────────────────────────────────────
    hdr("PROMPT 1: Floating intro (EDITING — new project)")
    empty_project: dict = {}   # empty → server routes to EDITING + composition mode

    try:
        p1_events = stream_maestro(api, token, PROMPT_1, empty_project, conv_id, "Prompt 1")
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code}: {e.read().decode()[:200]}")
        sys.exit(1)

    hdr("Prompt 1 — Results")
    summarise_prompt1(p1_events)

    # Check that we actually got tool calls before proceeding
    tool_calls_p1 = [e for e in p1_events if e.get("type") == "toolCall"]
    if not tool_calls_p1:
        fail("Prompt 1 produced no toolCall events — cannot build project context for Prompt 2")
        sys.exit(1)

    # ── Build project context from what was created ───────────────────────────
    hdr("Building project context from Prompt 1 output")
    project_ctx = build_project_from_editing_events(p1_events, tempo=72, key="Cm")

    info(f"Project has {len(project_ctx['tracks'])} track(s):")
    for track in project_ctx["tracks"]:
        region_summary = ", ".join(
            f"{r['name']} ({r.get('noteCount', 0)} notes)"
            for r in track["regions"]
        )
        info(f"  [{track['id'][:8]}] {track['name']}: {region_summary or '(no regions)'}")

    print()
    info("Full project context JSON (what FE sends as `project` in Prompt 2):")
    print(json.dumps(project_ctx, indent=2))

    # ── PROMPT 2 ──────────────────────────────────────────────────────────────
    hdr("PROMPT 2: Verse groove (COMPOSING — variation mode)")
    info("Sending project context built from Prompt 1...")

    try:
        p2_events = stream_maestro(api, token, PROMPT_2, project_ctx, conv_id, "Prompt 2")
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code}: {e.read().decode()[:200]}")
        sys.exit(1)

    hdr("Prompt 2 — Results")
    summarise_prompt2(p2_events)

    # ── Final verdict ─────────────────────────────────────────────────────────
    hdr("Final verdict")
    p2_meta = next((e for e in p2_events if e.get("type") == "meta"), None)
    p2_phrases = [e for e in p2_events if e.get("type") == "phrase"]
    if p2_meta and p2_phrases:
        ok("Backend is producing the correct variation output for a two-prompt flow.")
        ok("The FE needs to:")
        info("  1. After Prompt 1 complete: read toolCall events → update DAW → rebuild projectContext()")
        info("  2. Send rebuilt projectContext() as `project` in Prompt 2 request body")
        info("  3. On `state: composing` SSE: show progress UI (NOT Muse UX yet)")
        info("  4. On `progress` SSE: show step label from `message` field")
        info("  5. On `meta` SSE: NOW show Muse variation UX with variationId + baseStateId")
        info("  6. On `phrase` SSE: render each phrase in the variation viewer")
        info("  7. On `done` SSE: enable Accept/Discard buttons")
        info("  8. On Accept: POST /variation/commit with variationId + baseStateId + acceptedPhraseIds")
    else:
        fail("Two-prompt flow incomplete — see errors above.")


if __name__ == "__main__":
    main()
