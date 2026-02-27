"""Smoke test — step-by-step validation of each subsystem."""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx


def main() -> int:
    jwt = os.environ.get("JWT", "")
    if not jwt:
        print("JWT not set", file=sys.stderr)
        return 1

    base = "http://maestro:10001/api/v1"
    headers = {"Authorization": f"Bearer {jwt}"}

    # ── Step 1: Prompt fetch ─────────────────────────────────────────────
    print("=" * 60)
    print("STEP 1: Fetch prompts")
    print("=" * 60)
    r = httpx.get(f"{base}/maestro/prompts", headers=headers, timeout=10)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  FAIL: {r.text[:300]}")
        return 1
    prompts = r.json().get("prompts", [])
    print(f"  Got {len(prompts)} prompts")
    for p in prompts:
        pid = p.get("id", "?")
        has_full = bool(p.get("fullPrompt"))
        print(f"    {pid} (fullPrompt={has_full})")
    if not prompts:
        print("  FAIL: no prompts")
        return 1
    print("  PASS")

    # Pick the shortest prompt for speed
    selected = min(prompts, key=lambda p: len(p.get("fullPrompt", "")))
    prompt_text = selected.get("fullPrompt", "")
    print(f"\n  Selected: {selected['id']} ({len(prompt_text)} chars)")

    # ── Step 2: Maestro compose ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 2: Maestro compose (SSE stream)")
    print("=" * 60)
    conv_id = str(uuid.uuid4())
    payload = {
        "prompt": prompt_text,
        "mode": "generate",
        "conversationId": conv_id,
        "qualityPreset": "fast",
        "storePrompt": False,
    }
    print(f"  convId: {conv_id[:12]}...")
    print(f"  mode: generate")
    print(f"  prompt: {prompt_text[:80]}...")

    events: list[dict] = []
    tool_calls: list[dict] = []

    with httpx.Client(timeout=httpx.Timeout(connect=10, read=180, write=10, pool=10)) as c:
        with c.stream("POST", f"{base}/maestro/stream", json=payload, headers=headers) as resp:
            print(f"  HTTP {resp.status_code}")
            if resp.status_code != 200:
                body = resp.read().decode()
                print(f"  FAIL: {body[:500]}")
                return 1

            for line in resp.iter_lines():
                if not line.strip() or line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                etype = data.get("type", "?")
                seq = data.get("seq", -1)
                events.append(data)

                if etype == "state":
                    state_val = data.get("state", "?")
                    intent = data.get("intent", "?")
                    mode = data.get("executionMode", "?")
                    print(f"  [{seq:>3}] STATE: {state_val} intent={intent} mode={mode}")
                elif etype == "toolCall":
                    name = data.get("name", "?")
                    tool_calls.append(data)
                    print(f"  [{seq:>3}] TOOL: {name}")
                elif etype == "generatorStart":
                    role = data.get("role", "?")
                    bars = data.get("bars", "?")
                    print(f"  [{seq:>3}] GEN_START: role={role} bars={bars}")
                elif etype == "generatorComplete":
                    role = data.get("role", "?")
                    nc = data.get("noteCount", 0)
                    print(f"  [{seq:>3}] GEN_DONE: role={role} notes={nc}")
                elif etype == "complete":
                    success = data.get("success", False)
                    tokens = data.get("inputTokens", 0)
                    print(f"  [{seq:>3}] COMPLETE: success={success} tokens={tokens}")
                elif etype == "error":
                    msg = data.get("message", "?")
                    print(f"  [{seq:>3}] ERROR: {msg}")
                elif etype == "plan":
                    title = data.get("title", "?")[:50]
                    print(f"  [{seq:>3}] PLAN: {title}")
                else:
                    print(f"  [{seq:>3}] {etype}")

    print(f"\n  Total events: {len(events)}")
    print(f"  Tool calls: {len(tool_calls)}")

    # Check success
    complete_events = [e for e in events if e.get("type") == "complete"]
    if not complete_events:
        print("  FAIL: no complete event")
        return 1
    if not complete_events[-1].get("success"):
        print("  FAIL: complete.success=false")
        return 1
    print("  PASS")

    # ── Step 3: Check notes in tool calls ────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 3: Extract notes from tool calls")
    print("=" * 60)
    all_notes = []
    for tc in tool_calls:
        params = tc.get("params", {})
        notes = params.get("notes", [])
        if notes:
            all_notes.extend(notes)
    print(f"  Total notes across all tool calls: {len(all_notes)}")
    if all_notes:
        pitches = [n.get("pitch", 0) for n in all_notes]
        print(f"  Pitch range: {min(pitches)}-{max(pitches)}")
        print("  PASS")
    else:
        print("  WARN: no notes found (Storpheus may not have been invoked)")

    # ── Step 4: MUSE health ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 4: MUSE API health check")
    print("=" * 60)
    try:
        r = httpx.get(f"{base}/muse/log", params={"project_id": "smoke-test"}, headers=headers, timeout=10)
        print(f"  GET /muse/log: HTTP {r.status_code}")
        if r.status_code == 200:
            print("  PASS")
        else:
            print(f"  Response: {r.text[:200]}")
            print("  WARN: MUSE may not have data yet (expected for fresh DB)")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n" + "=" * 60)
    print("SMOKE TEST COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
