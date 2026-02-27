"""MVP Happy Path: Maestro → Orpheus end-to-end.

Runs inside the maestro container. Sends a composition prompt, captures
SSE events, downloads artifacts from Orpheus, and produces clean output:

  /tmp/mvp_output/
    intro.mp3          ← section audio
    groove.mp3
    verse.mp3
    build.mp3
    song.mp3           ← all sections concatenated
    intro.mid / .webp  ← MIDI + piano-roll plot per section

Usage:
    docker compose exec -e JWT="..." maestro python scripts/e2e/mvp_happy_path.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

JWT = os.environ.get("JWT", "")
MAESTRO_URL = "http://localhost:10001/api/v1/maestro/stream"
ORPHEUS_URL = "http://storpheus:10002"
OUTPUT_DIR = Path("/data/mvp_output")

PROMPT = """\
STORI PROMPT
Mode: compose
Section: intro
Style: neo-soul
Key: Fm
Tempo: 92
Energy: medium
Role: [drums, bass, keys]
Constraints:
  bars: 8
  density: medium
Vibe: [warm x2, groovy x2, intimate, soulful, late-night]

Request: |
  A compact neo-soul pocket piece in two sections. 4-bar intro — keys
  play a Rhodes chord pad on Fm9-Bbm7, bass enters on beat 3 of bar 2
  with a warm walking line, drums enter bar 3 with brushed hi-hats and
  a lazy kick. 4-bar groove — full pocket: drums lay down a tight boom-
  bap-meets-neo-soul pattern, bass locks with the kick on a Fm9-Db9-
  Bbm7-C7alt loop, keys add a simple right-hand motif above the chords.
  Like D'Angelo's Voodoo — unhurried, deep pocket, every note in its
  place.

Harmony:
  progression: [Fm9, Db9, Bbm7, C7alt]
  voicing: rootless — 3rd and 7th in left hand, 9th and extensions in right
  rhythm: whole notes in intro, quarter-note pushes in groove
  extensions: 9ths on all chords, altered extensions on C7
  color: dark warmth — minor 9ths and altered dominants
  reharmonize: |
    Bar 8 substitutes C7alt with C7#9#5 for maximum tension into repeat

Melody:
  scale: F dorian
  register: mid (C4-Ab5)
  contour: |
    Intro: no melody — chords and bass only.
    Groove: short 2-note motif (F5-Eb5) on the "and" of beat 2, call-response.
  phrases:
    structure: 1-bar call, 1-bar response
    breath: 2 beats of silence between phrases
  density: very sparse — 2 notes per bar average
  ornamentation:
    - grace note on the minor 3rd (Ab)
    - blue note (Cb) approach in bar 7

Rhythm:
  feel: behind the beat — lazy neo-soul pocket
  subdivision: 16th-note feel
  swing: 58%
  accent:
    pattern: ghost snare on e-and-a of beats 2 and 4
    weight: subtle — velocity ±8
  ghost_notes:
    instrument: snare
    velocity: 28-42
    frequency: every other 16th on beats 2 and 4
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early

Dynamics:
  overall: mp to mf
  arc:
    - bars: 1-2
      level: mp
      shape: flat — keys alone, intimate
    - bars: 3-4
      level: mp to mf
      shape: linear crescendo as instruments enter
    - bars: 5-8
      level: mf
      shape: steady groove with subtle accents
  accent_velocity: 98
  ghost_velocity: 32

Orchestration:
  drums:
    kit: neo-soul (vinyl kick, brushed snare, slightly open hi-hat)
    hi_hat: 8th notes, slightly open on upbeats
    kick: quarter notes with ghost on the e of 2
    snare: 2 and 4 with ghost rolls
  bass:
    technique: finger style, round tone
    register: low (F1-C3)
    articulation: legato with occasional staccato on syncopations
  keys:
    voicing: Rhodes electric piano — rootless left hand, melody right hand
    pedaling: no sustain pedal — Rhodes damper only
    right_hand: sparse motif above chord pads
"""


async def stream_maestro() -> list[dict]:
    """POST to Maestro SSE endpoint and capture all events."""
    payload = {
        "prompt": PROMPT,
        "mode": "compose",
        "qualityPreset": "quality",
    }
    headers = {
        "Authorization": f"Bearer {JWT}",
        "Content-Type": "application/json",
    }

    events: list[dict] = []
    print(f"\n{'='*60}")
    print("MAESTRO → ORPHEUS MVP")
    print(f"{'='*60}")
    print(f"Style: neo-soul | Key: Fm | Tempo: 92 | Roles: drums, bass, keys")
    print(f"{'='*60}\n")

    timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        t0 = time.monotonic()
        async with client.stream(
            "POST", MAESTRO_URL, json=payload, headers=headers
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"ERROR: HTTP {resp.status_code}: {body.decode()[:500]}")
                return events

            print(f"✅ SSE stream started")

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        events.append(evt)
                        _print_event(evt, time.monotonic() - t0)
                    except json.JSONDecodeError:
                        pass

        total = time.monotonic() - t0
        print(f"\n✅ Done in {total:.0f}s — {len(events)} events")

    return events


def _print_event(evt: dict, elapsed: float) -> None:
    t = evt.get("type", "?")
    tag = f"[{elapsed:5.0f}s]"

    if t == "state":
        print(f"  {tag} → {evt.get('state')}")
    elif t == "generatorStart":
        print(f"  {tag} 🎵 generating {evt.get('role')}...")
    elif t == "generatorComplete":
        ms = evt.get("durationMs", 0)
        print(f"  {tag} ✅ {evt.get('role')}: {evt.get('noteCount', 0)} notes ({ms/1000:.1f}s)")
    elif t == "toolCall" and evt.get("name") == "stori_add_midi_track":
        print(f"  {tag} 🎸 track: {evt.get('params', {}).get('name', '?')}")
    elif t == "toolCall" and evt.get("name") == "stori_add_midi_region":
        print(f"  {tag} 📎 region: {evt.get('params', {}).get('name', '?')}")
    elif t == "complete":
        print(f"  {tag} 🏁 complete")
    elif t in ("error", "toolError"):
        print(f"  {tag} ❌ {evt.get('message') or evt.get('error', '')[:80]}")
    elif t in ("status",):
        pass  # suppress noise


def extract_section_order(events: list[dict]) -> list[str]:
    """Extract unique section names in order from region creation events."""
    seen: set[str] = set()
    sections: list[str] = []
    for evt in events:
        if evt.get("type") == "toolCall" and evt.get("name") == "stori_add_midi_region":
            name = evt.get("params", {}).get("name", "").lower().replace(" ", "_")
            if name and name not in seen:
                seen.add(name)
                sections.append(name)
    return sections


def extract_composition_id(events: list[dict]) -> str | None:
    """Extract the traceId used as composition_id for Orpheus artifacts."""
    for evt in events:
        if evt.get("type") == "state" and evt.get("traceId"):
            return evt["traceId"]
    for evt in events:
        if evt.get("traceId"):
            return evt["traceId"]
    return None


async def download_artifacts(
    comp_id: str, sections: list[str], output_dir: Path
) -> list[Path]:
    """Download artifacts from Orpheus and rename with section names."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(f"{ORPHEUS_URL}/artifacts/{comp_id}")
        if resp.status_code != 200:
            print(f"  ⚠️  No artifacts found for {comp_id}")
            return []
        data = resp.json()
        files = data.get("files", [])
        if not files:
            print(f"  ⚠️  Artifact directory empty for {comp_id}")
            return []

        print(f"\n  📦 Found {len(files)} artifact files")

        mp3s = sorted(f for f in files if f.endswith(".mp3"))
        mids = sorted(f for f in files if f.endswith(".mid"))
        plots = sorted(f for f in files if f.endswith(".webp") or f.endswith(".png"))

        mp3_paths: list[Path] = []
        for group, ext_list in [("audio", mp3s), ("midi", mids), ("plot", plots)]:
            for i, fname in enumerate(ext_list):
                section_name = sections[i] if i < len(sections) else f"section_{i}"
                ext = Path(fname).suffix
                dest_name = f"{section_name}{ext}"
                dest = output_dir / dest_name

                dl = await client.get(
                    f"{ORPHEUS_URL}/artifacts/{comp_id}/{fname}"
                )
                if dl.status_code == 200:
                    dest.write_bytes(dl.content)
                    size_kb = len(dl.content) / 1024
                    if ext == ".mp3":
                        mp3_paths.append(dest)
                        print(f"  🔊 {dest_name} ({size_kb:.0f}KB)")
                    elif ext == ".mid":
                        print(f"  🎵 {dest_name} ({size_kb:.0f}KB)")
                    else:
                        print(f"  📊 {dest_name} ({size_kb:.0f}KB)")

        return mp3_paths


def concatenate_mp3s(mp3_paths: list[Path], output: Path) -> bool:
    """Concatenate MP3 files by simple binary append (works for CBR MP3)."""
    if not mp3_paths:
        return False
    with open(output, "wb") as out:
        for p in mp3_paths:
            out.write(p.read_bytes())
    total_kb = output.stat().st_size / 1024
    print(f"  🎶 song.mp3 ({total_kb:.0f}KB) — {len(mp3_paths)} sections combined")
    return True


async def main():
    if not JWT:
        print("ERROR: set JWT environment variable")
        sys.exit(1)

    output = OUTPUT_DIR
    if output.exists():
        for f in output.iterdir():
            f.unlink()
    output.mkdir(parents=True, exist_ok=True)

    # 1. Clear Orpheus cache so we get fresh generation
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        try:
            await client.delete(f"{ORPHEUS_URL}/cache/clear")
            print("🗑️  Orpheus cache cleared")
        except Exception:
            pass

    # 2. Stream composition
    events = await stream_maestro()
    if not events:
        print("No events captured.")
        return

    # 3. Extract section names and composition ID
    sections = extract_section_order(events)
    comp_id = extract_composition_id(events)
    print(f"\n  Sections: {sections}")
    print(f"  Composition ID: {comp_id or '(not found)'}")

    if not comp_id:
        print("  ⚠️  No composition ID found — can't download artifacts.")
        print("     Saving SSE events only.")
        (output / "sse_events.json").write_text(json.dumps(events, indent=2))
        return

    # 4. Download and rename artifacts
    print(f"\n{'='*60}")
    print("DOWNLOADING ARTIFACTS")
    print(f"{'='*60}")
    mp3_paths = await download_artifacts(comp_id, sections, output)

    # 5. Concatenate into one song
    if mp3_paths:
        concatenate_mp3s(mp3_paths, output / "song.mp3")

    # 6. Summary
    all_files = sorted(output.iterdir())
    print(f"\n{'='*60}")
    print("OUTPUT")
    print(f"{'='*60}")
    for f in all_files:
        size = f.stat().st_size / 1024
        print(f"  {f.name:30s} {size:8.0f}KB")

    print(f"\n  Copying to host...")
    import subprocess
    subprocess.run(["rm", "-rf", "/tmp/song"], check=False)
    subprocess.run(["mkdir", "-p", "/tmp/song"], check=True)
    for f in all_files:
        subprocess.run(["cp", str(f), f"/tmp/song/{f.name}"], check=True)
    print(f"  ✅ Files at /tmp/song/ (inside Maestro container)")
    print(f"\n  Run on host:")
    print(f"    docker compose cp maestro:/data/mvp_output /tmp/song && open /tmp/song/")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
