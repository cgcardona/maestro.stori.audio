#!/usr/bin/env python3
"""
Storpheus Stress Test — Comprehensive throughput, latency, and quality assessment.

Sends a matrix of generation requests spanning every genre, instrument combo,
bar count, quality preset, and intent vector extreme the system supports.
Captures per-request latency, token throughput, note density, and error rates.

Usage:
    # Quick smoke test (1 request per genre, fast preset)
    docker compose exec storpheus python scripts/e2e/stress_test.py --quick

    # Standard sweep (all genres × bar counts × presets)
    docker compose exec storpheus python scripts/e2e/stress_test.py

    # Full matrix (all genres × instruments × bars × presets × intent vectors)
    docker compose exec storpheus python scripts/e2e/stress_test.py --full

    # Concurrency test (how many parallel requests before degradation)
    docker compose exec storpheus python scripts/e2e/stress_test.py --concurrency

    # Custom target
    docker compose exec storpheus python scripts/e2e/stress_test.py --url http://storpheus:10002

Results are written to:
    stress_results_{timestamp}.json   — raw data
    stress_results_{timestamp}.html   — self-contained HTML report
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

ORPHEUS_URL = "http://localhost:10002"

GENRES = [
    "boom_bap", "trap", "house", "techno", "jazz", "neo_soul",
    "classical", "cinematic", "ambient", "reggae", "funk",
    "drum_and_bass", "dubstep", "drill", "lofi",
]

INSTRUMENT_COMBOS = [
    ["drums"],
    ["drums", "bass"],
    ["drums", "bass", "piano"],
    ["drums", "bass", "guitar"],
    ["drums", "bass", "piano", "guitar"],
    ["bass"],
    ["piano"],
]

BAR_COUNTS = [4, 8, 16, 32]

QUALITY_PRESETS = ["fast", "balanced", "quality"]

KEYS = [None, "C", "Am", "F#", "Bb", "Em"]

INTENT_VECTORS: dict[str, dict[str, Any]] = {
    "neutral": {
        "emotion_vector": {"energy": 0.5, "valence": 0.0, "tension": 0.3, "intimacy": 0.5, "motion": 0.5},
    },
    "dark_intense": {
        "emotion_vector": {"energy": 0.9, "valence": -0.9, "tension": 0.8, "intimacy": 0.2, "motion": 0.7},
        "intent_goals": [{"name": "dark"}, {"name": "energetic"}, {"name": "intense"}],
    },
    "bright_chill": {
        "emotion_vector": {"energy": 0.2, "valence": 0.8, "tension": 0.1, "intimacy": 0.7, "motion": 0.3},
        "intent_goals": [{"name": "bright"}, {"name": "chill"}, {"name": "peaceful"}],
    },
    "cinematic_build": {
        "emotion_vector": {"energy": 0.7, "valence": 0.2, "tension": 0.6, "intimacy": 0.4, "motion": 0.8},
        "intent_goals": [{"name": "cinematic"}, {"name": "dense"}, {"name": "intense"}],
    },
    "minimal_sparse": {
        "emotion_vector": {"energy": 0.1, "valence": -0.2, "tension": 0.1, "intimacy": 0.6, "motion": 0.15},
        "intent_goals": [{"name": "minimal"}, {"name": "calm"}],
    },
    "club_ready": {
        "emotion_vector": {"energy": 0.85, "valence": 0.3, "tension": 0.4, "intimacy": 0.1, "motion": 0.9},
        "intent_goals": [{"name": "club"}, {"name": "energetic"}],
    },
    "max_complexity": {
        "emotion_vector": {"energy": 0.7, "valence": 0.0, "tension": 0.5, "intimacy": 0.3, "motion": 0.7},
        "intent_goals": [{"name": "dense"}, {"name": "maximal"}],
    },
    "extreme_low": {
        "emotion_vector": {"energy": 0.0, "valence": -1.0, "tension": 0.0, "intimacy": 0.0, "motion": 0.0},
    },
    "extreme_high": {
        "emotion_vector": {"energy": 1.0, "valence": 1.0, "tension": 1.0, "intimacy": 1.0, "motion": 1.0},
    },
}

TEMPO_MAP = {
    "boom_bap": 90, "trap": 140, "house": 124, "techno": 130,
    "jazz": 110, "neo_soul": 75, "classical": 100, "cinematic": 95,
    "ambient": 70, "reggae": 80, "funk": 105, "drum_and_bass": 174,
    "dubstep": 140, "drill": 145, "lofi": 82,
}


@dataclass
class RequestResult:
    """Captures everything about a single generation attempt."""
    genre: str
    tempo: int
    instruments: list[str]
    bars: int
    quality_preset: str
    intent_profile: str
    key: str | None

    success: bool = False
    error: str | None = None
    latency_ms: float = 0.0
    note_count: int = 0
    tool_call_count: int = 0
    track_count: int = 0
    region_count: int = 0
    cache_hit: bool = False
    policy_version: str | None = None
    tokens_requested: int = 0
    http_status: int = 0

    temperature: float = 0.0
    top_p: float = 0.0

    # Artifact tracking — set when a composition_id is sent with the request
    composition_id: str | None = None


@dataclass
class StressReport:
    """Aggregated results from a full stress run."""
    started_at: str = ""
    finished_at: str = ""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    cache_hits: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_mean_ms: float = 0.0
    latency_max_ms: float = 0.0
    latency_min_ms: float = 0.0

    avg_notes_per_bar: float = 0.0
    avg_notes_per_request: float = 0.0
    total_notes_generated: int = 0

    per_genre: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_preset: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_bar_count: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_intent: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_instrument_combo: dict[str, dict[str, Any]] = field(default_factory=dict)
    concurrency_results: list[dict[str, Any]] = field(default_factory=list)

    results: list[dict[str, Any]] = field(default_factory=list)


def build_payload(
    genre: str,
    instruments: list[str],
    bars: int,
    quality_preset: str,
    intent_profile: str,
    key: str | None = None,
    temperature_override: float | None = None,
    top_p_override: float | None = None,
    composition_id: str | None = None,
) -> dict[str, Any]:
    intent = INTENT_VECTORS.get(intent_profile, INTENT_VECTORS["neutral"])
    payload: dict[str, Any] = {
        "genre": genre,
        "tempo": TEMPO_MAP.get(genre, 120),
        "instruments": instruments,
        "bars": bars,
        "quality_preset": quality_preset,
    }
    if key:
        payload["key"] = key
    if "emotion_vector" in intent:
        payload["emotion_vector"] = intent["emotion_vector"]
    if "intent_goals" in intent:
        payload["intent_goals"] = intent["intent_goals"]
    if temperature_override is not None:
        payload["temperature"] = temperature_override
    if top_p_override is not None:
        payload["top_p"] = top_p_override
    if composition_id:
        payload["composition_id"] = composition_id
        payload["trace_id"] = composition_id  # drives artifact filename
    return payload


async def send_request(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    intent_profile: str,
    timeout: float = 300.0,
) -> RequestResult:
    result = RequestResult(
        genre=payload["genre"],
        tempo=payload["tempo"],
        instruments=payload["instruments"],
        bars=payload["bars"],
        quality_preset=payload["quality_preset"],
        intent_profile=intent_profile,
        key=payload.get("key"),
        temperature=payload.get("temperature", 0.0),
        top_p=payload.get("top_p", 0.0),
        composition_id=payload.get("composition_id"),
    )

    start = time.monotonic()
    try:
        resp = await client.post(
            "/generate",
            json=payload,
            timeout=timeout,
        )
        result.http_status = resp.status_code

        if resp.status_code == 503:
            result.latency_ms = round((time.monotonic() - start) * 1000, 1)
            result.error = "Queue full — try again shortly"
            return result

        if resp.status_code != 200:
            result.latency_ms = round((time.monotonic() - start) * 1000, 1)
            result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return result

        data = resp.json()
        job_id: str | None = data.get("jobId")
        status: str = data.get("status", "complete")

        # Poll /jobs/{id}/wait until the job reaches a terminal state.
        deadline = start + timeout
        while status in ("queued", "running") and job_id:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.latency_ms = round((time.monotonic() - start) * 1000, 1)
                result.error = f"Timeout after {timeout}s waiting for job"
                return result
            poll_s = min(30, max(1, int(remaining)))
            poll_resp = await client.get(
                f"/jobs/{job_id}/wait",
                params={"timeout": poll_s},
                timeout=float(poll_s + 10),
            )
            if poll_resp.status_code != 200:
                result.latency_ms = round((time.monotonic() - start) * 1000, 1)
                result.error = f"Poll HTTP {poll_resp.status_code}: {poll_resp.text[:200]}"
                return result
            data = poll_resp.json()
            status = data.get("status", "complete")

        result.latency_ms = round((time.monotonic() - start) * 1000, 1)

        if status == "canceled":
            result.error = "Job was canceled"
            return result

        if status == "failed":
            result.error = data.get("error") or "Generation failed (no details)"
            return result

        # Terminal: complete. Extract nested result payload.
        inner = data.get("result") or data

        result.success = inner.get("success", False)
        result.error = inner.get("error")

        meta = inner.get("metadata") or {}
        result.cache_hit = meta.get("cache_hit", False)
        result.policy_version = meta.get("policy_version")
        result.note_count = meta.get("note_count", 0) or len(inner.get("notes", []))
        # Keep composition_id from payload (what we sent); server echoes it in metadata
        result.composition_id = (
            meta.get("composition_id") or payload.get("composition_id")
        )

        tool_calls = inner.get("tool_calls") or []
        result.tool_call_count = len(tool_calls)
        for tc in tool_calls:
            tool = tc.get("tool", "")
            if tool == "addNotes":
                result.note_count += len(tc.get("params", {}).get("notes", []))
            elif tool == "addMidiTrack":
                result.track_count += 1
            elif tool == "addMidiRegion":
                result.region_count += 1

    except httpx.TimeoutException:
        result.latency_ms = round((time.monotonic() - start) * 1000, 1)
        result.error = f"Timeout after {timeout}s"
    except httpx.ConnectError as e:
        result.error = f"Connection error: {e}"
    except Exception as e:
        result.error = f"Unexpected: {type(e).__name__}: {e}"

    return result


def fmt_dur(ms: float) -> str:
    """Format a millisecond duration as a human-readable string.

    < 1 s   → "123ms"
    < 60 s  → "12.3s"
    >= 60 s → "2m 34s"
    """
    if ms < 1000:
        return f"{ms:.0f}ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    rem = s - m * 60
    return f"{m}m {rem:.0f}s"


def compute_percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def group_stats(results: list[RequestResult], key_fn: Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[RequestResult]] = {}
    for r in results:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)

    stats: dict[str, dict[str, Any]] = {}
    for k, items in sorted(groups.items()):
        successful = [r for r in items if r.success]
        latencies = [r.latency_ms for r in successful]
        notes = [r.note_count for r in successful]
        stats[k] = {
            "total": len(items),
            "success": len(successful),
            "failed": len(items) - len(successful),
            "cache_hits": sum(1 for r in items if r.cache_hit),
            "latency_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
            "latency_p95_ms": round(compute_percentile(latencies, 0.95), 1) if latencies else 0,
            "avg_notes": round(statistics.mean(notes), 1) if notes else 0,
        }
    return stats


def build_report(results: list[RequestResult]) -> StressReport:
    report = StressReport()
    report.total_requests = len(results)
    report.successful = sum(1 for r in results if r.success)
    report.failed = sum(1 for r in results if not r.success)
    report.cache_hits = sum(1 for r in results if r.cache_hit)
    report.results = [asdict(r) for r in results]

    errors: dict[str, int] = {}
    for r in results:
        if r.error:
            bucket = r.error[:80]
            errors[bucket] = errors.get(bucket, 0) + 1
    report.errors = errors

    successful = [r for r in results if r.success]
    latencies = [r.latency_ms for r in successful]
    if latencies:
        report.latency_mean_ms = round(statistics.mean(latencies), 1)
        report.latency_p50_ms = round(compute_percentile(latencies, 0.50), 1)
        report.latency_p95_ms = round(compute_percentile(latencies, 0.95), 1)
        report.latency_p99_ms = round(compute_percentile(latencies, 0.99), 1)
        report.latency_max_ms = round(max(latencies), 1)
        report.latency_min_ms = round(min(latencies), 1)

    notes = [r.note_count for r in successful]
    if notes:
        report.total_notes_generated = sum(notes)
        report.avg_notes_per_request = round(statistics.mean(notes), 1)

    bars_notes = [(r.bars, r.note_count) for r in successful if r.bars > 0]
    if bars_notes:
        report.avg_notes_per_bar = round(
            sum(n for _, n in bars_notes) / sum(b for b, _ in bars_notes), 1
        )

    report.per_genre = group_stats(results, lambda r: r.genre)
    report.per_preset = group_stats(results, lambda r: r.quality_preset)
    report.per_bar_count = group_stats(results, lambda r: str(r.bars))
    report.per_intent = group_stats(results, lambda r: r.intent_profile)
    report.per_instrument_combo = group_stats(
        results, lambda r: "+".join(r.instruments)
    )

    return report


# ── Muse VCS phase ───────────────────────────────────────────────────────────


@dataclass
class MuseNode:
    """One commit in the Muse variation graph."""
    variation_id: str
    parent_id: str | None
    parent2_id: str | None  # set on merge commits
    intent: str
    genre: str
    is_head: bool = False
    lane: int = 0           # column in the SVG layout


@dataclass
class MuseRunResult:
    """Output of the Muse VCS phase."""
    project_id: str
    nodes: list[MuseNode] = field(default_factory=list)
    error: str | None = None
    conflict_demo: dict[str, Any] | None = None


def _get_test_token() -> str:
    """Mint a short-lived JWT for the stress test without a DB roundtrip."""
    from maestro.auth.tokens import create_access_token  # noqa: PLC0415
    return create_access_token(user_id="stress-test", expires_hours=1)


async def _save_muse_variation(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    variation_id: str,
    intent: str,
    parent_id: str | None = None,
    parent2_id: str | None = None,
    phrases: list[dict[str, Any]] | None = None,
) -> bool:
    try:
        resp = await client.post(
            "/api/v1/muse/variations",
            json={
                "project_id": project_id,
                "variation_id": variation_id,
                "intent": intent,
                "parent_variation_id": parent_id,
                "parent2_variation_id": parent2_id,
                "phrases": phrases or [],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def _set_muse_head(
    client: httpx.AsyncClient, token: str, variation_id: str
) -> None:
    try:
        await client.post(
            "/api/v1/muse/head",
            json={"variation_id": variation_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except Exception:
        pass


def _muse_note(
    pitch: int, start_beat: float, duration_beats: float = 0.5, velocity: int = 100
) -> dict[str, Any]:
    return {"pitch": pitch, "start_beat": start_beat, "duration_beats": duration_beats, "velocity": velocity, "channel": 0}


def _muse_variation_payload(
    variation_id: str,
    project_id: str,
    intent: str,
    base_notes: dict[str, list[dict[str, Any]]],
    proposed_notes: dict[str, list[dict[str, Any]]],
    parent_variation_id: str | None = None,
    parent2_variation_id: str | None = None,
) -> dict[str, Any]:
    """Build a POST /muse/variations request body with a proper NoteChange diff.

    Computes added/removed changes by comparing (pitch, start_beat) keys
    between base and proposed — exactly the approach used by the working
    Muse E2E harness in tests/e2e/muse_fixtures.py.
    """
    phrases: list[dict[str, Any]] = []
    for rid in sorted(set(base_notes) | set(proposed_notes)):
        base = base_notes.get(rid, [])
        proposed = proposed_notes.get(rid, [])
        base_keys = {(n["pitch"], n["start_beat"]) for n in base}
        proposed_keys = {(n["pitch"], n["start_beat"]) for n in proposed}

        note_changes: list[dict[str, Any]] = [
            {"note_id": f"nc-{variation_id[:8]}-{rid}-p{n['pitch']}b{n['start_beat']}",
             "change_type": "added", "before": None, "after": n}
            for n in proposed if (n["pitch"], n["start_beat"]) not in base_keys
        ] + [
            {"note_id": f"nc-{variation_id[:8]}-{rid}-p{n['pitch']}b{n['start_beat']}",
             "change_type": "removed", "before": n, "after": None}
            for n in base if (n["pitch"], n["start_beat"]) not in proposed_keys
        ]

        phrases.append({
            "phrase_id": f"ph-{variation_id[:8]}-{rid}",
            "track_id": rid.replace("r_", "t_"),
            "region_id": rid,
            "start_beat": 0.0,
            "end_beat": 8.0,
            "label": f"{intent} ({rid})",
            "note_changes": note_changes,
            "cc_events": [],
            "pitch_bends": [],
            "aftertouch": [],
            "tags": ["stress-test"],
        })

    return {
        "project_id": project_id,
        "variation_id": variation_id,
        "intent": intent,
        "conversation_id": "stress-test",
        "parent_variation_id": parent_variation_id,
        "parent2_variation_id": parent2_variation_id,
        "affected_regions": list(sorted(set(base_notes) | set(proposed_notes))),
        "phrases": phrases,
        "beat_range": [0.0, 8.0],
    }


async def run_muse_conflict_demo(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    out: MuseRunResult,
) -> dict[str, Any]:
    """Demonstrate a real Muse merge conflict using the proven E2E harness approach.

    Conflict scenario (from tests/e2e/muse_fixtures.py):

        C0 (root, empty)
         └── C1 (keys v1: C, E, G, C5 arpeggio)
              ├── C5 (branch A: adds pitch=48 at beat 4, vel=95, dur=1.0)
              └── C6 (branch B: adds pitch=48 at beat 4, vel=60, dur=2.0)
                           ↑
                    CONFLICT: same (pitch, beat) added by both sides
                    with different velocity + duration → 409

    Uses ``_muse_variation_payload`` to compute proper NoteChange diffs
    (added/removed only), matching how the merge engine reconstructs
    snapshots via lineage replay.
    """
    hdrs = {"Authorization": f"Bearer {token}"}
    demo_project = f"{project_id}-conflict"

    c0_id = str(uuid.uuid4())
    c1_id = str(uuid.uuid4())
    c5_id = str(uuid.uuid4())
    c6_id = str(uuid.uuid4())

    # Shared key snapshots
    keys_v1 = [
        _muse_note(60, 0.0, 1.0, 100),
        _muse_note(64, 1.0, 1.0, 90),
        _muse_note(67, 2.0, 1.0, 80),
        _muse_note(72, 3.0, 1.0, 100),
    ]
    # Branch A adds pitch=48 at beat 4 with velocity=95, duration=1.0
    keys_v2 = keys_v1 + [_muse_note(48, 4.0, 1.0, 95)]
    # Branch B adds the SAME (pitch=48, beat=4.0) but velocity=60, duration=2.0
    keys_v3 = keys_v1 + [_muse_note(48, 4.0, 2.0, 60)]

    try:
        # C0: root (empty → empty)
        await _save_muse_variation(
            client, token, demo_project, c0_id,
            intent="conflict-demo: root",
            phrases=_muse_variation_payload(
                c0_id, demo_project, "root", {}, {},
            )["phrases"],
        )

        # C1: keys v1 (empty → keys_v1)
        await _save_muse_variation(
            client, token, demo_project, c1_id,
            intent="conflict-demo: keys v1",
            parent_id=c0_id,
            phrases=_muse_variation_payload(
                c1_id, demo_project, "keys v1",
                {}, {"r_keys": keys_v1},
                parent_variation_id=c0_id,
            )["phrases"],
        )

        # C5: branch A — adds pitch=48 vel=95 dur=1.0
        await _save_muse_variation(
            client, token, demo_project, c5_id,
            intent="conflict-demo: branch A (vel=95)",
            parent_id=c1_id,
            phrases=_muse_variation_payload(
                c5_id, demo_project, "branch A",
                {"r_keys": keys_v1}, {"r_keys": keys_v2},
                parent_variation_id=c1_id,
            )["phrases"],
        )

        # C6: branch B — adds pitch=48 vel=60 dur=2.0 (same pitch+beat, different properties)
        await _save_muse_variation(
            client, token, demo_project, c6_id,
            intent="conflict-demo: branch B (vel=60)",
            parent_id=c1_id,
            phrases=_muse_variation_payload(
                c6_id, demo_project, "branch B",
                {"r_keys": keys_v1}, {"r_keys": keys_v3},
                parent_variation_id=c1_id,
            )["phrases"],
        )

        # Phase 1 — detect the conflict (force=False → expect 409)
        resp_conflict = await client.post(
            "/api/v1/muse/merge",
            json={
                "project_id": demo_project,
                "left_id": c5_id,
                "right_id": c6_id,
                "conversation_id": "stress-test",
                "force": False,
            },
            headers=hdrs,
            timeout=20.0,
        )

        if resp_conflict.status_code != 409:
            return {
                "status": "unexpected_success",
                "http_status": resp_conflict.status_code,
                "project_id": demo_project,
                "body": resp_conflict.text[:400],
            }

        conflicts = resp_conflict.json().get("detail", {}).get("conflicts", [])

        # Phase 2 — manual resolution: musician picks branch A's note (vel=95, dur=1.0).
        # The merge engine flags the conflict; the musician decides which side wins,
        # then saves a resolution commit via POST /muse/variations with both parent IDs.
        # (force=True on POST /muse/merge still raises on conflicts because checkout_plan
        # is None when is_conflict=True — manual commit is the correct resolution path.)
        merge_vid = str(uuid.uuid4())
        resolved_phrases = _muse_variation_payload(
            merge_vid, demo_project,
            "resolve: accept branch A (vel=95, dur=1.0)",
            {"r_keys": keys_v1},   # diff base
            {"r_keys": keys_v2},   # resolved = branch A wins
            parent_variation_id=c5_id,
            parent2_variation_id=c6_id,
        )["phrases"]

        resolution: dict[str, Any] | None = None
        log_nodes: list[dict[str, Any]] = []
        saved = await _save_muse_variation(
            client, token, demo_project, merge_vid,
            intent="merge resolved: branch A wins",
            parent_id=c5_id,
            parent2_id=c6_id,
            phrases=resolved_phrases,
        )
        if saved:
            await _set_muse_head(client, token, merge_vid)
            resolution = {
                "strategy": "manual — musician chose branch A",
                "winning_branch": "branch A (pitch=48, vel=95, dur=1.0)",
                "losing_branch": "branch B (pitch=48, vel=60, dur=2.0) — discarded",
                "merge_variation_id": merge_vid,
                "head_moved": True,
            }
            log_resp = await client.get(
                "/api/v1/muse/log",
                params={"project_id": demo_project},
                headers=hdrs,
                timeout=10.0,
            )
            if log_resp.status_code == 200:
                log_nodes = log_resp.json().get("nodes", [])

        return {
            "status": "conflict_detected",
            "http_status": 409,
            "project_id": demo_project,
            "left_intent": "branch A — pitch=48 at beat 4, vel=95, dur=1.0",
            "right_intent": "branch B — pitch=48 at beat 4, vel=60, dur=2.0",
            "conflicts": conflicts,
            "resolution": resolution,
            "log_nodes": log_nodes,
        }

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def run_muse_phase(
    maestro_url: str,
    token: str,
    results: list[RequestResult],
    project_id: str,
) -> MuseRunResult:
    """Commit each successful generation to Muse VCS as a branching history.

    Graph shape (when enough results exist):

        r0  ──  m1  ──  m2  ──  m3        (main branch, lane 0)
         └──  f1  ──  f2                  (feature branch, lane 1)
                           └── M (merge)  (merge commit, lane 0)
    """
    successful = [r for r in results if r.success]
    if not successful:
        return MuseRunResult(project_id=project_id, error="No successful results to commit")

    out = MuseRunResult(project_id=project_id)

    async with httpx.AsyncClient(base_url=maestro_url, timeout=30.0) as client:
        n = len(successful)

        # ── Root commit ────────────────────────────────────────────────
        root_id = str(uuid.uuid4())
        root_r = successful[0]
        root_node = MuseNode(
            variation_id=root_id, parent_id=None, parent2_id=None,
            intent=f"{root_r.genre} — root", genre=root_r.genre, lane=0,
        )
        out.nodes.append(root_node)
        await _save_muse_variation(client, token, project_id, root_id, root_node.intent)

        # ── Main branch (lane 0): up to 4 linear commits from root ────
        main_chain: list[str] = [root_id]
        for r in successful[1: min(5, n)]:
            vid = str(uuid.uuid4())
            node = MuseNode(
                variation_id=vid, parent_id=main_chain[-1], parent2_id=None,
                intent=f"{r.genre} — main", genre=r.genre, lane=0,
            )
            out.nodes.append(node)
            await _save_muse_variation(
                client, token, project_id, vid, node.intent,
                parent_id=main_chain[-1],
            )
            main_chain.append(vid)

        # ── Feature branch (lane 1): up to 2 commits from root ────────
        feat_chain: list[str] = [root_id]
        feat_slice = successful[min(5, n): min(7, n)]
        for r in feat_slice:
            vid = str(uuid.uuid4())
            node = MuseNode(
                variation_id=vid, parent_id=feat_chain[-1], parent2_id=None,
                intent=f"{r.genre} — feature", genre=r.genre, lane=1,
            )
            out.nodes.append(node)
            await _save_muse_variation(
                client, token, project_id, vid, node.intent,
                parent_id=feat_chain[-1],
            )
            feat_chain.append(vid)

        # ── Merge commit (lane 0) if we have two real branches ────────
        head_id = main_chain[-1]
        if len(feat_chain) > 1 and len(main_chain) > 1:
            merge_id = str(uuid.uuid4())
            merge_node = MuseNode(
                variation_id=merge_id,
                parent_id=main_chain[-1], parent2_id=feat_chain[-1],
                intent="merge: main ← feature", genre="merge",
                lane=0, is_head=True,
            )
            out.nodes.append(merge_node)
            await _save_muse_variation(
                client, token, project_id, merge_id, merge_node.intent,
                parent_id=main_chain[-1], parent2_id=feat_chain[-1],
            )
            head_id = merge_id
        else:
            out.nodes[-1].is_head = True

        await _set_muse_head(client, token, head_id)

        # ── Conflict demo (separate sub-project) ──────────────────────
        out.conflict_demo = await run_muse_conflict_demo(
            client, token, project_id, out,
        )

    return out


def _svg_muse_dag(muse: MuseRunResult) -> str:
    """Render the Muse commit graph as a self-contained SVG."""
    if not muse.nodes or muse.error:
        return f'<p style="color:#ef4444">Muse phase error: {muse.error or "no nodes"}</p>'

    # Build a lookup and determine row ordering (topological — nodes were
    # appended in creation order, which is already topological).
    by_id = {n.variation_id: n for n in muse.nodes}
    rows = list(muse.nodes)  # already topological

    # Assign display rows per lane so parents always appear above children.
    # We track (lane → last_row) and bump the row for each new commit in lane.
    lane_row: dict[int, int] = {}
    row_positions: dict[str, tuple[int, int]] = {}  # variation_id → (row, lane)
    for node in rows:
        lane = node.lane
        row = max(lane_row.get(lane, -1) + 1,
                  # merge parent must be below both parents
                  (row_positions[node.parent_id][0] + 1 if node.parent_id else 0),
                  (row_positions[node.parent2_id][0] + 1 if node.parent2_id else 0))
        lane_row[lane] = row
        row_positions[node.variation_id] = (row, lane)

    max_row = max(r for r, _ in row_positions.values()) if row_positions else 0
    num_lanes = max(n.lane for n in rows) + 1 if rows else 1

    COL_W, ROW_H, R = 140, 54, 10
    PAD_X, PAD_Y = 20, 30
    width = PAD_X * 2 + num_lanes * COL_W + 160  # extra for labels
    height = PAD_Y * 2 + (max_row + 1) * ROW_H + 20

    LANE_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444"]

    def cx(lane: int) -> int:
        return PAD_X + lane * COL_W + COL_W // 2

    def cy(row: int) -> int:
        return PAD_Y + row * ROW_H

    lines = []
    # Draw edges first (behind circles)
    for node in rows:
        r0, l0 = row_positions[node.variation_id]
        x0, y0 = cx(l0), cy(r0)
        for pid, dashed in [(node.parent_id, False), (node.parent2_id, True)]:
            if pid and pid in row_positions:
                rp, lp = row_positions[pid]
                xp, yp = cx(lp), cy(rp)
                color = LANE_COLORS[lp % len(LANE_COLORS)]
                dash = 'stroke-dasharray="6 3"' if dashed else ""
                lines.append(
                    f'<line x1="{xp}" y1="{yp}" x2="{x0}" y2="{y0}" '
                    f'stroke="{color}" stroke-width="2" {dash} opacity="0.7"/>'
                )

    # Draw commit circles and labels
    for node in rows:
        r0, l0 = row_positions[node.variation_id]
        x, y = cx(l0), cy(r0)
        color = LANE_COLORS[l0 % len(LANE_COLORS)]
        fill = color if node.is_head else "#1e293b"
        stroke = color
        is_merge = node.parent2_id is not None

        shape = (
            f'<polygon points="{x},{y - R} {x + R},{y + R} {x - R},{y + R}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
            if is_merge else
            f'<circle cx="{x}" cy="{y}" r="{R}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        )
        lines.append(shape)

        short_id = node.variation_id[:7]
        head_tag = " ← HEAD" if node.is_head else ""
        label = node.intent[:32] + ("…" if len(node.intent) > 32 else "")
        lines.append(
            f'<text x="{x + R + 6}" y="{y - 3}" font-size="10" fill="#94a3b8" '
            f'font-family="monospace">{short_id}{head_tag}</text>'
        )
        lines.append(
            f'<text x="{x + R + 6}" y="{y + 9}" font-size="10" fill="#cbd5e1" '
            f'font-family="system-ui,sans-serif">{label}</text>'
        )

    # Lane legends at top
    for lane in range(num_lanes):
        color = LANE_COLORS[lane % len(LANE_COLORS)]
        legend_x = PAD_X + lane * COL_W + COL_W // 2
        lane_name = "main" if lane == 0 else f"feature-{lane}"
        lines.append(
            f'<text x="{legend_x}" y="16" text-anchor="middle" font-size="10" '
            f'fill="{color}" font-family="system-ui,sans-serif" font-weight="600">'
            f'{lane_name}</text>'
        )

    inner = "\n".join(lines)
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;background:#0f172a;border-radius:8px;'
        f'border:1px solid #334155;padding:8px;margin-bottom:1.5rem">'
        f'<style>text{{dominant-baseline:middle}}</style>'
        f'{inner}'
        f'</svg>'
    )


def _mini_dag_svg(nodes: list[dict[str, Any]], head_id: str | None = None) -> str:
    """Compact SVG DAG for the conflict demo panel (demo project log)."""
    if not nodes:
        return ""

    # Sort by timestamp ascending so root is first
    sorted_nodes = sorted(nodes, key=lambda n: n.get("timestamp", 0))

    # Assign lanes: root+main = 0, branches = 1
    id_to_lane: dict[str, int] = {}
    id_to_row: dict[str, int] = {}
    row = 0
    for n in sorted_nodes:
        pid = n.get("parent")
        p2 = n.get("parent2")
        # Merge commits go back to lane 0
        if p2:
            lane = 0
        elif pid and id_to_lane.get(pid, 0) == 0 and not any(
            x.get("parent") == pid for x in sorted_nodes if x["id"] != n["id"]
        ):
            lane = 0
        elif pid and id_to_lane.get(pid, 0) == 0:
            # Check if another node already claimed this parent on lane 0
            siblings = [x for x in sorted_nodes if x.get("parent") == pid and x["id"] != n["id"]]
            lane = 1 if siblings else 0
        else:
            lane = id_to_lane.get(pid or "", 0)
        id_to_lane[n["id"]] = lane
        # Row = max of parent rows + 1
        parent_row = id_to_row.get(pid or "", -1) if pid else -1
        parent2_row = id_to_row.get(p2 or "", -1) if p2 else -1
        id_to_row[n["id"]] = max(parent_row, parent2_row) + 1

    max_row = max(id_to_row.values()) if id_to_row else 0
    num_lanes = max(id_to_lane.values()) + 1 if id_to_lane else 1

    COL_W, ROW_H, R = 120, 48, 8
    PAD = 16
    width = PAD * 2 + num_lanes * COL_W + 200
    height = PAD * 2 + (max_row + 1) * ROW_H

    COLORS = ["#6366f1", "#10b981", "#f59e0b"]

    def cx(lane: int) -> int:
        return PAD + lane * COL_W + COL_W // 2

    def cy(r: int) -> int:
        return PAD + r * ROW_H

    parts: list[str] = []
    for n in sorted_nodes:
        nid = n["id"]
        r0 = id_to_row[nid]
        l0 = id_to_lane[nid]
        x0, y0 = cx(l0), cy(r0)
        is_merge = bool(n.get("parent2"))
        is_head = nid == head_id or n.get("isHead", False)
        color = COLORS[l0 % len(COLORS)]

        for pid, dashed in [(n.get("parent"), False), (n.get("parent2"), True)]:
            if pid and pid in id_to_row:
                rp, lp = id_to_row[pid], id_to_lane[pid]
                xp, yp = cx(lp), cy(rp)
                pc = COLORS[lp % len(COLORS)]
                dash = 'stroke-dasharray="5 3"' if dashed else ""
                parts.append(
                    f'<line x1="{xp}" y1="{yp}" x2="{x0}" y2="{y0}" '
                    f'stroke="{pc}" stroke-width="1.5" {dash} opacity="0.6"/>'
                )

        fill = color if is_head else "#1e293b"
        if is_merge:
            pts = f"{x0},{y0-R} {x0+R},{y0+R} {x0-R},{y0+R}"
            parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{color}" stroke-width="1.5"/>')
        else:
            parts.append(f'<circle cx="{x0}" cy="{y0}" r="{R}" fill="{fill}" stroke="{color}" stroke-width="1.5"/>')

        intent = (n.get("intent") or "")[:28]
        head_tag = " ← HEAD" if is_head else ""
        short = nid[:7]
        parts.append(
            f'<text x="{x0+R+4}" y="{y0-3}" font-size="9" fill="#94a3b8" font-family="monospace">'
            f'{short}{head_tag}</text>'
        )
        parts.append(
            f'<text x="{x0+R+4}" y="{y0+8}" font-size="9" fill="#cbd5e1" font-family="system-ui">'
            f'{intent}</text>'
        )

    inner = "\n".join(parts)
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;background:#0f172a;border-radius:6px;margin-top:0.75rem">'
        f'<style>text{{dominant-baseline:middle}}</style>{inner}</svg>'
    )


def _muse_conflict_panel(muse: MuseRunResult) -> str:
    """Render the merge conflict demo result as an HTML panel."""
    demo = muse.conflict_demo
    if not demo:
        return ""

    status = demo.get("status", "unknown")
    conflicts: list[dict[str, Any]] = demo.get("conflicts", [])

    if status == "conflict_detected":
        # ── Conflict phase ─────────────────────────────────────────────
        conflict_badge = (
            '<span style="background:#ef4444;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px;font-weight:700">⚡ CONFLICT</span>'
        )
        rows = "".join(
            f'<tr>'
            f'<td style="padding:5px 10px;color:#94a3b8;font-family:monospace;font-size:12px">{c.get("region_id","")}</td>'
            f'<td style="padding:5px 10px;color:#f59e0b;font-size:12px">{c.get("type","").upper()}</td>'
            f'<td style="padding:5px 10px;color:#e2e8f0;font-size:12px">{c.get("description","")}</td>'
            f'</tr>'
            for c in conflicts
        )
        scenario_line = (
            f'<p style="color:#94a3b8;font-size:12px;margin:0.4rem 0 0.75rem">'
            f'<strong style="color:#e2e8f0">Left:</strong> {demo.get("left_intent","")} &nbsp;'
            f'<strong style="color:#e2e8f0">Right:</strong> {demo.get("right_intent","")}'
            f'</p>'
        )
        conflict_table = (
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>'
            f'<th style="text-align:left;padding:5px 10px;color:#64748b;font-size:10px;text-transform:uppercase">Region</th>'
            f'<th style="text-align:left;padding:5px 10px;color:#64748b;font-size:10px;text-transform:uppercase">Type</th>'
            f'<th style="text-align:left;padding:5px 10px;color:#64748b;font-size:10px;text-transform:uppercase">Description</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )
        conflict_section = (
            f'<div style="margin-bottom:1.25rem">'
            f'<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.25rem">'
            f'{conflict_badge}'
            f'<span style="color:#64748b;font-size:11px;font-family:monospace">'
            f'POST /api/v1/muse/merge (force=false) → HTTP 409</span>'
            f'</div>'
            f'{scenario_line}'
            f'{conflict_table}'
            f'</div>'
        )

        # ── Resolution phase ───────────────────────────────────────────
        resolution = demo.get("resolution")
        log_nodes: list[dict[str, Any]] = demo.get("log_nodes", [])
        head_id: str | None = None

        if resolution:
            resolve_badge = (
                '<span style="background:#10b981;color:#fff;padding:2px 8px;'
                'border-radius:4px;font-size:12px;font-weight:700">✅ RESOLVED</span>'
            )
            merge_vid = resolution.get("merge_variation_id", "")
            head_id = merge_vid
            strategy = resolution.get("strategy", "")
            winning = resolution.get("winning_branch", "")
            mini_dag = _mini_dag_svg(log_nodes, head_id=merge_vid)
            resolution_section = (
                f'<div style="border-top:1px solid #334155;padding-top:1rem">'
                f'<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem">'
                f'{resolve_badge}'
                f'<span style="color:#64748b;font-size:11px;font-family:monospace">'
                f'POST /api/v1/muse/merge (force=true) → HTTP 200</span>'
                f'</div>'
                f'<p style="color:#94a3b8;font-size:12px;margin:0.25rem 0">'
                f'<strong style="color:#e2e8f0">Strategy:</strong> {strategy}</p>'
                f'<p style="color:#94a3b8;font-size:12px;margin:0.25rem 0">'
                f'<strong style="color:#e2e8f0">Winner:</strong> {winning} &nbsp;'
                f'<strong style="color:#94a3b8">Discarded:</strong> {resolution.get("losing_branch","")}</p>'
                f'<p style="color:#94a3b8;font-size:12px;margin:0.25rem 0">'
                f'<strong style="color:#e2e8f0">Merge commit:</strong> '
                f'<code style="color:#6366f1">{merge_vid[:8]}</code> '
                f'(HEAD moved: {resolution.get("head_moved")})</p>'
                f'{mini_dag}'
                f'</div>'
            )
        else:
            resolution_section = (
                '<p style="color:#ef4444;font-size:12px;margin-top:0.75rem">'
                'Resolution step failed or not attempted.</p>'
            )

        body = f"{conflict_section}{resolution_section}"
        colour = "#0f1629"
        border = "#7c3aed"

    elif status == "unexpected_success":
        badge = (
            '<span style="background:#f59e0b;color:#000;padding:2px 8px;'
            'border-radius:4px;font-size:12px;font-weight:700">⚠️ UNEXPECTED SUCCESS</span>'
        )
        body = (
            f'<p style="color:#94a3b8">HTTP {demo.get("http_status")} — '
            f'merge returned success (empty snapshots — no MIDI state to conflict on).</p>'
            f'<pre style="color:#64748b;font-size:11px">{demo.get("body","")}</pre>'
        )
        colour = "#1a1a2e"
        border = "#f59e0b"
    else:
        badge = (
            '<span style="background:#374151;color:#9ca3af;padding:2px 8px;'
            'border-radius:4px;font-size:12px">ERROR</span>'
        )
        body = f'<p style="color:#ef4444">{demo.get("error", status)}</p>'
        colour = "#1a1a2e"
        border = "#374151"

    return (
        f'<h3 style="margin-top:2rem">Muse Merge Conflict Demo</h3>'
        f'<div style="background:{colour};border:1px solid {border};'
        f'border-radius:8px;padding:1.25rem;margin-bottom:1.5rem">'
        f'{body}'
        f'</div>'
    )


@dataclass
class ArtifactSet:
    """Holds base64-encoded artifact bytes for a single generation."""
    composition_id: str
    genre: str
    bars: int
    mp3_b64: str | None = None   # inline <audio>
    webp_b64: str | None = None  # inline <img> piano-roll
    mid_b64: str | None = None   # data-URI download link


async def fetch_run_artifacts(
    url: str,
    results: list[RequestResult],
) -> dict[str, ArtifactSet]:
    """Download MP3, WebP piano-roll, and MIDI for every successful result.

    Cache hits are included — if Storpheus has artifacts stored under the
    echoed composition_id they are fetched; the endpoint simply returns an
    empty file list when nothing is available, which is handled gracefully.
    """
    artifacts: dict[str, ArtifactSet] = {}
    async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
        for r in results:
            if not r.composition_id or not r.success:
                continue
            comp_id = r.composition_id
            try:
                listing = await client.get(f"/artifacts/{comp_id}")
                if listing.status_code != 200:
                    continue
                files: list[str] = listing.json().get("files", [])
                art = ArtifactSet(composition_id=comp_id, genre=r.genre, bars=r.bars)
                for fname in files:
                    dl = await client.get(f"/artifacts/{comp_id}/{fname}")
                    if dl.status_code != 200:
                        continue
                    ext = fname.rsplit(".", 1)[-1].lower()
                    b64 = base64.b64encode(dl.content).decode()
                    if ext == "mp3":
                        art.mp3_b64 = b64
                    elif ext in ("webp", "png"):
                        art.webp_b64 = b64
                    elif ext in ("mid", "midi"):
                        art.mid_b64 = b64
                artifacts[comp_id] = art
            except Exception:
                pass
    return artifacts


def _svg_bars(
    data: dict[str, float],
    color: str,
    unit: str,
    title: str,
    width: int = 560,
) -> str:
    """Render a horizontal SVG bar chart — zero external deps."""
    if not data:
        return ""
    sorted_items = sorted(data.items(), key=lambda x: -x[1])
    max_val = max(v for _, v in sorted_items) or 1
    bar_h, gap, lpad, tpad = 22, 4, 130, 28
    chart_w = width - lpad - 40
    total_h = tpad + len(sorted_items) * (bar_h + gap)
    rows = ""
    for i, (label, value) in enumerate(sorted_items):
        y = tpad + i * (bar_h + gap)
        bw = max(2, int(value / max_val * chart_w))
        rows += (
            f'<text x="{lpad - 6}" y="{y + 15}" text-anchor="end" '
            f'font-size="11" fill="#94a3b8">{label}</text>'
            f'<rect x="{lpad}" y="{y}" width="{bw}" height="{bar_h}" '
            f'fill="{color}" rx="3" opacity="0.85"/>'
            f'<text x="{lpad + bw + 5}" y="{y + 15}" font-size="11" fill="#cbd5e1">'
            f'{value:,.1f}{unit}</text>'
        )
    return (
        f'<svg width="{width}" height="{total_h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;margin-bottom:1.5rem">'
        f'<text x="0" y="18" font-size="12" fill="#64748b" font-family="system-ui,sans-serif">'
        f'{title}</text>'
        f'{rows}'
        f'</svg>'
    )


def build_html_report(
    report: StressReport,
    mode: str,
    url: str,
    results: list[RequestResult] | None = None,
    artifacts: dict[str, ArtifactSet] | None = None,
    muse: MuseRunResult | None = None,
) -> str:
    """Render a self-contained HTML stress report from a StressReport."""

    def _rate(ok: int, total: int) -> str:
        if total == 0:
            return "—"
        pct = 100 * ok / total
        colour = "#16a34a" if pct >= 90 else "#ca8a04" if pct >= 70 else "#dc2626"
        return f'<span style="color:{colour};font-weight:600">{pct:.0f}%</span>'

    def _ms(v: float) -> str:
        return fmt_dur(v) if v else "—"

    def _table(title: str, data: dict[str, dict[str, Any]]) -> str:
        if not data:
            return ""
        rows = ""
        for k, v in data.items():
            success_rate = _rate(v["success"], v["total"])
            rows += (
                f"<tr>"
                f"<td>{k}</td>"
                f"<td>{v['total']}</td>"
                f"<td>{v['success']}</td>"
                f"<td>{v['failed']}</td>"
                f"<td>{success_rate}</td>"
                f"<td>{_ms(v['latency_mean_ms'])}</td>"
                f"<td>{_ms(v['latency_p95_ms'])}</td>"
                f"<td>{v['avg_notes']:.1f}</td>"
                f"</tr>"
            )
        return f"""
        <h2>{title}</h2>
        <table>
          <thead><tr>
            <th>Key</th><th>Total</th><th>OK</th><th>Fail</th>
            <th>Rate</th><th>Mean</th><th>P95</th><th>Avg notes</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    def _concurrency_table() -> str:
        if not report.concurrency_results:
            return ""
        rows = ""
        for cr in report.concurrency_results:
            rows += (
                f"<tr>"
                f"<td>{cr['concurrency']}</td>"
                f"<td>{cr['success']}</td>"
                f"<td>{cr['failed']}</td>"
                f"<td>{_ms(cr['latency_mean_ms'])}</td>"
                f"<td>{_ms(cr['latency_p95_ms'])}</td>"
                f"<td>{_ms(cr['wall_time_ms'])}</td>"
                f"</tr>"
            )
        return f"""
        <h2>Concurrency Scaling</h2>
        <table>
          <thead><tr>
            <th>Parallel</th><th>OK</th><th>Fail</th>
            <th>Mean</th><th>P95</th><th>Wall</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    def _errors_table() -> str:
        if not report.errors:
            return ""
        rows = "".join(
            f"<tr><td>{count}</td><td>{err}</td></tr>"
            for err, count in sorted(report.errors.items(), key=lambda x: -x[1])
        )
        return f"""
        <h2>Errors</h2>
        <table>
          <thead><tr><th>Count</th><th>Error</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    overall_rate = _rate(report.successful, report.total_requests)

    # ── SVG charts ──────────────────────────────────────────────────────────
    latency_by_genre = {
        k: v["latency_mean_ms"] for k, v in report.per_genre.items() if v["success"]
    }
    notes_by_genre = {
        k: v["avg_notes"] for k, v in report.per_genre.items() if v["success"]
    }
    chart_latency = _svg_bars(
        {k: v / 1000 for k, v in latency_by_genre.items()},
        "#6366f1", "s", "Mean Latency by Genre",
    )
    chart_notes = _svg_bars(
        notes_by_genre, "#10b981", "", "Avg Notes by Genre"
    )

    # ── Per-generation media cards ───────────────────────────────────────────
    def _media_cards() -> str:
        if not results or not artifacts:
            return ""
        cards = ""
        for r in results:
            if not r.success or not r.composition_id:
                continue
            art = (artifacts or {}).get(r.composition_id)
            instr_str = "+".join(r.instruments)
            label = f"{r.genre} · {r.bars}b · {r.quality_preset} · {instr_str}"
            cache_badge = (
                '<span style="background:#1d4ed8;color:#bfdbfe;font-size:0.65rem;'
                'padding:2px 6px;border-radius:4px;margin-left:6px">CACHE</span>'
                if r.cache_hit else ""
            )
            audio_html = ""
            image_html = ""
            midi_html = ""
            if art:
                if art.mp3_b64:
                    audio_html = (
                        f'<audio controls style="width:100%;margin-top:0.5rem">'
                        f'<source src="data:audio/mpeg;base64,{art.mp3_b64}" type="audio/mpeg">'
                        f'</audio>'
                    )
                if art.webp_b64:
                    image_html = (
                        f'<img src="data:image/webp;base64,{art.webp_b64}" '
                        f'alt="piano roll" style="width:100%;border-radius:4px;'
                        f'margin-top:0.5rem;border:1px solid #334155">'
                    )
                if art.mid_b64:
                    midi_html = (
                        f'<a href="data:audio/midi;base64,{art.mid_b64}" '
                        f'download="{r.genre}_{r.bars}b.mid" '
                        f'style="display:inline-block;margin-top:0.5rem;font-size:0.75rem;'
                        f'color:#818cf8;text-decoration:none">⬇ Download MIDI</a>'
                    )
            note_line = f"{r.note_count} notes · {fmt_dur(r.latency_ms)}"
            cards += f"""
            <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;
              padding:1rem;margin-bottom:1rem">
              <div style="font-size:0.8rem;font-weight:600;color:#f1f5f9">
                {label}{cache_badge}
              </div>
              <div style="font-size:0.7rem;color:#64748b;margin-top:2px">{note_line}</div>
              {audio_html}
              {image_html}
              {midi_html}
            </div>"""
        return f'<h2>Compositions</h2>{cards}' if cards else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Storpheus Stress Report — {report.started_at[:10]}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem;
      max-width: 960px;
    }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; color: #f8fafc; }}
    .meta {{ font-size: 0.8rem; color: #64748b; margin-bottom: 2rem; }}
    .kpi-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 1rem; margin-bottom: 2.5rem;
    }}
    .kpi {{
      background: #1e293b; border-radius: 8px; padding: 1rem;
      border: 1px solid #334155;
    }}
    .kpi .label {{ font-size: 0.7rem; color: #94a3b8; text-transform: uppercase;
      letter-spacing: 0.05em; }}
    .kpi .value {{ font-size: 1.5rem; font-weight: 700; margin-top: 0.25rem; }}
    h2 {{ font-size: 1rem; color: #94a3b8; text-transform: uppercase;
      letter-spacing: 0.08em; margin: 2rem 0 0.75rem; border-bottom: 1px solid #1e293b;
      padding-bottom: 0.5rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem;
      margin-bottom: 1.5rem; }}
    th {{ text-align: left; padding: 0.5rem 0.75rem; background: #1e293b;
      color: #94a3b8; font-weight: 500; font-size: 0.75rem;
      text-transform: uppercase; letter-spacing: 0.05em; }}
    td {{ padding: 0.45rem 0.75rem; border-bottom: 1px solid #1e293b; }}
    tr:hover td {{ background: #1e293b44; }}
    audio {{ height: 36px; }}
    .footer {{ margin-top: 3rem; font-size: 0.75rem; color: #475569; }}
  </style>
</head>
<body>
  <h1>Storpheus Stress Report</h1>
  <p class="meta">
    Mode: <strong>{mode}</strong> &nbsp;·&nbsp;
    Target: <strong>{url}</strong> &nbsp;·&nbsp;
    {report.started_at} → {report.finished_at}
  </p>

  <div class="kpi-grid">
    <div class="kpi"><div class="label">Total</div>
      <div class="value">{report.total_requests}</div></div>
    <div class="kpi"><div class="label">Success rate</div>
      <div class="value">{overall_rate}</div></div>
    <div class="kpi"><div class="label">Failed</div>
      <div class="value" style="color:#dc2626">{report.failed}</div></div>
    <div class="kpi"><div class="label">Cache hits</div>
      <div class="value">{report.cache_hits}</div></div>
    <div class="kpi"><div class="label">Mean latency</div>
      <div class="value">{_ms(report.latency_mean_ms)}</div></div>
    <div class="kpi"><div class="label">P50</div>
      <div class="value">{_ms(report.latency_p50_ms)}</div></div>
    <div class="kpi"><div class="label">P95</div>
      <div class="value">{_ms(report.latency_p95_ms)}</div></div>
    <div class="kpi"><div class="label">P99</div>
      <div class="value">{_ms(report.latency_p99_ms)}</div></div>
    <div class="kpi"><div class="label">Total notes</div>
      <div class="value">{report.total_notes_generated:,}</div></div>
    <div class="kpi"><div class="label">Avg notes/req</div>
      <div class="value">{report.avg_notes_per_request:.1f}</div></div>
    <div class="kpi"><div class="label">Avg notes/bar</div>
      <div class="value">{report.avg_notes_per_bar:.1f}</div></div>
  </div>

  <h2>Latency &amp; Note Density</h2>
  {chart_latency}
  {chart_notes}

  {_table("By Genre", report.per_genre)}
  {_table("By Quality Preset", report.per_preset)}
  {_table("By Bar Count", report.per_bar_count)}
  {_table("By Intent Profile", report.per_intent)}
  {_table("By Instrument Combo", report.per_instrument_combo)}
  {_concurrency_table()}
  {_errors_table()}
  {f'<h2>Muse Commit Graph — project {muse.project_id[:8]}</h2>{_svg_muse_dag(muse)}{_muse_conflict_panel(muse)}' if muse else ''}
  {_media_cards()}

  <p class="footer">Generated by scripts/e2e/stress_test.py &nbsp;·&nbsp; Maestro</p>
</body>
</html>"""


def print_report(report: StressReport) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  STORPHEUS STRESS TEST REPORT")
    print("=" * W)
    print(f"  Started:  {report.started_at}")
    print(f"  Finished: {report.finished_at}")
    print(f"  Total requests:   {report.total_requests}")
    print(f"  Successful:       {report.successful}")
    print(f"  Failed:           {report.failed}")
    print(f"  Cache hits:       {report.cache_hits}")
    print()

    print("─" * W)
    print("  LATENCY (successful requests only)")
    print("─" * W)
    print(f"  Min:    {fmt_dur(report.latency_min_ms):>10}")
    print(f"  Mean:   {fmt_dur(report.latency_mean_ms):>10}")
    print(f"  P50:    {fmt_dur(report.latency_p50_ms):>10}")
    print(f"  P95:    {fmt_dur(report.latency_p95_ms):>10}")
    print(f"  P99:    {fmt_dur(report.latency_p99_ms):>10}")
    print(f"  Max:    {fmt_dur(report.latency_max_ms):>10}")
    print()

    print("─" * W)
    print("  NOTE OUTPUT")
    print("─" * W)
    print(f"  Total notes generated: {report.total_notes_generated}")
    print(f"  Avg notes/request:     {report.avg_notes_per_request}")
    print(f"  Avg notes/bar:         {report.avg_notes_per_bar}")
    print()

    def _print_breakdown(title: str, data: dict[str, dict[str, Any]]) -> None:
        print("─" * W)
        print(f"  {title}")
        print("─" * W)
        hdr = f"  {'Key':<22} {'OK':>4} {'Fail':>4} {'Mean':>8} {'P95':>8} {'Notes':>6}"
        print(hdr)
        for k, v in data.items():
            print(
                f"  {k:<22} {v['success']:>4} {v['failed']:>4} "
                f"{fmt_dur(v['latency_mean_ms']):>8} {fmt_dur(v['latency_p95_ms']):>8} "
                f"{v['avg_notes']:>6.1f}"
            )
        print()

    _print_breakdown("BY GENRE", report.per_genre)
    _print_breakdown("BY QUALITY PRESET", report.per_preset)
    _print_breakdown("BY BAR COUNT", report.per_bar_count)
    _print_breakdown("BY INTENT PROFILE", report.per_intent)
    _print_breakdown("BY INSTRUMENT COMBO", report.per_instrument_combo)

    if report.errors:
        print("─" * W)
        print("  ERROR BREAKDOWN")
        print("─" * W)
        for err, count in sorted(report.errors.items(), key=lambda x: -x[1]):
            print(f"  [{count:>3}x] {err}")
        print()

    if report.concurrency_results:
        print("─" * W)
        print("  CONCURRENCY SCALING")
        print("─" * W)
        hdr = f"  {'Parallel':>8} {'OK':>4} {'Fail':>4} {'Mean':>8} {'P95':>8} {'Wall':>8}"
        print(hdr)
        for cr in report.concurrency_results:
            print(
                f"  {cr['concurrency']:>8} {cr['success']:>4} {cr['failed']:>4} "
                f"{fmt_dur(cr['latency_mean_ms']):>8} {fmt_dur(cr['latency_p95_ms']):>8} "
                f"{fmt_dur(cr['wall_time_ms']):>8}"
            )
        print()

    print("=" * W)


# ── Test scenario builders ───────────────────────────────────────────


def _active_genres(genre_filter: list[str] | None) -> list[str]:
    """Return the genres to run, honouring an optional --genre filter."""
    if not genre_filter:
        return GENRES
    unknown = [g for g in genre_filter if g not in GENRES]
    if unknown:
        print(f"  ⚠️  Unknown genres ignored: {', '.join(unknown)}")
        print(f"     Valid genres: {', '.join(GENRES)}")
    return [g for g in genre_filter if g in GENRES] or GENRES


def build_quick_scenarios(
    genre_filter: list[str] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """One request per genre, fast preset, 4 bars, drums+bass, neutral intent."""
    scenarios = []
    for genre in _active_genres(genre_filter):
        payload = build_payload(
            genre=genre,
            instruments=["drums", "bass"],
            bars=4,
            quality_preset="fast",
            intent_profile="neutral",
        )
        scenarios.append((payload, "neutral"))
    return scenarios


def build_standard_scenarios(
    genre_filter: list[str] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Every genre × every bar count × every preset. Drums+bass, neutral."""
    scenarios = []
    for genre in _active_genres(genre_filter):
        for bars in BAR_COUNTS:
            for preset in QUALITY_PRESETS:
                payload = build_payload(
                    genre=genre,
                    instruments=["drums", "bass"],
                    bars=bars,
                    quality_preset=preset,
                    intent_profile="neutral",
                )
                scenarios.append((payload, "neutral"))
    return scenarios


def build_full_scenarios() -> list[tuple[dict[str, Any], str]]:
    """Full matrix: genres × instruments × bars × presets × intent vectors."""
    scenarios = []
    for genre in GENRES:
        for instruments in INSTRUMENT_COMBOS:
            for bars in [4, 8, 16]:
                for preset in ["fast", "balanced"]:
                    for intent_name in INTENT_VECTORS:
                        payload = build_payload(
                            genre=genre,
                            instruments=instruments,
                            bars=bars,
                            quality_preset=preset,
                            intent_profile=intent_name,
                            key="Am" if intent_name == "dark_intense" else None,
                        )
                        scenarios.append((payload, intent_name))
    return scenarios


def build_parameter_sweep_scenarios() -> list[tuple[dict[str, Any], str]]:
    """Sweep temperature and top_p extremes to find quality boundaries."""
    scenarios = []
    temperatures = [0.70, 0.80, 0.90, 1.00, 1.10]
    top_ps = [0.90, 0.95, 0.99]
    for temp in temperatures:
        for tp in top_ps:
            payload = build_payload(
                genre="jazz",
                instruments=["drums", "bass", "piano"],
                bars=8,
                quality_preset="balanced",
                intent_profile="neutral",
                temperature_override=temp,
                top_p_override=tp,
            )
            scenarios.append((payload, f"temp={temp}_tp={tp}"))
    return scenarios


async def run_concurrency_test(
    url: str, max_concurrent: int = 16
) -> list[dict[str, Any]]:
    """Send increasing numbers of parallel requests to find the throughput ceiling."""
    concurrency_levels = [1, 2, 4, 8, 12, max_concurrent]
    results = []

    for n in concurrency_levels:
        print(f"  Concurrency={n} ... ", end="", flush=True)
        async with httpx.AsyncClient(base_url=url) as client:
            payload = build_payload(
                genre="boom_bap",
                instruments=["drums", "bass"],
                bars=4,
                quality_preset="fast",
                intent_profile="neutral",
            )

            wall_start = time.monotonic()
            tasks = [
                send_request(client, payload, "neutral", timeout=300.0)
                for _ in range(n)
            ]
            batch_results = await asyncio.gather(*tasks)
            wall_ms = (time.monotonic() - wall_start) * 1000

            successful = [r for r in batch_results if r.success]
            latencies = [r.latency_ms for r in successful]
            entry = {
                "concurrency": n,
                "success": len(successful),
                "failed": n - len(successful),
                "latency_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
                "latency_p95_ms": round(compute_percentile(latencies, 0.95), 1) if latencies else 0,
                "wall_time_ms": round(wall_ms, 1),
            }
            results.append(entry)
            print(
                f"OK={entry['success']} Fail={entry['failed']} "
                f"Mean={fmt_dur(entry['latency_mean_ms'])} Wall={fmt_dur(entry['wall_time_ms'])}"
            )

    return results


async def run_scenarios(
    url: str,
    scenarios: list[tuple[dict[str, Any], str]],
    max_parallel: int = 4,
    timeout: float = 300.0,
    run_id: str | None = None,
) -> list[RequestResult]:
    """Run scenarios with bounded parallelism.

    Each scenario gets a unique composition_id derived from the run_id so that
    Storpheus saves artifacts (MIDI, MP3, WebP piano-roll) to an isolated folder
    per request rather than the shared 'ephemeral' bucket.
    """
    sem = asyncio.Semaphore(max_parallel)
    total = len(scenarios)
    completed = 0

    async def _run(payload: dict[str, Any], intent: str, idx: int) -> RequestResult:
        nonlocal completed
        # Stamp each request with a unique composition_id so artifacts don't collide
        if run_id and "composition_id" not in payload:
            comp_id = f"{run_id}-{idx:04d}"
            payload = {**payload, "composition_id": comp_id, "trace_id": comp_id}
        async with sem:
            async with httpx.AsyncClient(base_url=url) as client:
                r = await send_request(client, payload, intent, timeout=timeout)
                completed += 1
                status = "OK" if r.success else f"FAIL: {r.error or 'unknown'}"
                cache_tag = " [cache]" if r.cache_hit else ""
                print(
                    f"  [{completed:>4}/{total}] {r.genre:<16} {r.bars}bar "
                    f"{r.quality_preset:<10} {intent:<20} "
                    f"{fmt_dur(r.latency_ms):>8}  {status}{cache_tag}"
                )
                return r

    tasks = [
        _run(payload, intent, i)
        for i, (payload, intent) in enumerate(scenarios)
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


async def check_health(url: str) -> bool:
    """Verify Orpheus is reachable before running the suite."""
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            resp = await client.get("/health", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Orpheus healthy: {data}")
                return True
            print(f"  Orpheus returned HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  Cannot reach Orpheus at {url}: {e}")
        return False


async def check_diagnostics(url: str) -> dict[Any, Any] | None:
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            resp = await client.get("/diagnostics", timeout=10.0)
            if resp.status_code == 200:
                result: dict[Any, Any] = resp.json()
                return result
    except Exception:
        pass
    return None


async def flush_storpheus_cache(url: str) -> None:
    """Clear Storpheus result cache before a fresh run."""
    try:
        async with httpx.AsyncClient(base_url=url, timeout=10.0) as client:
            resp = await client.delete("/cache/clear")
            print(f"  Cache flush: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  Cache flush failed: {e}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Storpheus Stress Test")
    parser.add_argument("--url", default=ORPHEUS_URL, help="Storpheus base URL")
    parser.add_argument("--muse-url", default="http://localhost:10001",
                        help="Maestro base URL (for Muse VCS phase)")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test (1 request per genre)")
    parser.add_argument("--full", action="store_true", help="Full matrix (all genres × bars × presets)")
    parser.add_argument("--sweep", action="store_true", help="Parameter sweep (temperature × top_p)")
    parser.add_argument("--concurrency", action="store_true", help="Concurrency scaling test")
    parser.add_argument("--parallel", type=int, default=4, help="Max parallel requests")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-request timeout (s)")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    parser.add_argument(
        "--flush", action="store_true",
        help="Clear Storpheus result cache before running (forces fresh GPU generations)"
    )
    parser.add_argument(
        "--genre", type=str, default=None,
        help="Comma-separated list of genres to test (e.g. boom_bap,jazz). Default: all genres.",
    )
    parser.add_argument(
        "--no-muse", action="store_true",
        help="Skip the Muse VCS phase (no commit graph in report)"
    )
    args = parser.parse_args()

    genre_filter: list[str] | None = (
        [g.strip() for g in args.genre.split(",") if g.strip()]
        if args.genre else None
    )

    print("╔══════════════════════════════════════════════════════════╗")
    print("║       STORPHEUS STRESS TEST — the infinite riff          ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    print("▸ Checking Storpheus health...")
    healthy = await check_health(args.url)
    if not healthy:
        print("  ✗ Storpheus is not reachable. Aborting.")
        sys.exit(1)

    if args.flush:
        print("▸ Flushing Storpheus cache...")
        await flush_storpheus_cache(args.url)

    diag = await check_diagnostics(args.url)
    if diag:
        print(f"  Diagnostics: {json.dumps(diag, indent=2)}")
    print()

    # Unique run ID — drives per-request composition_ids so artifacts are isolated
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"stress-{ts}"

    # Build scenario list
    scenarios: list[tuple[dict[str, Any], str]] = []
    mode = "standard"

    if args.quick:
        mode = "quick"
        scenarios = build_quick_scenarios(genre_filter)
    elif args.full:
        mode = "full"
        scenarios = build_full_scenarios()
    elif args.sweep:
        mode = "sweep"
        scenarios = build_parameter_sweep_scenarios()
    elif args.concurrency:
        mode = "concurrency"
    else:
        scenarios = build_standard_scenarios(genre_filter)

    report = StressReport()
    report.started_at = datetime.now(timezone.utc).isoformat()
    all_results: list[RequestResult] = []

    if mode == "concurrency":
        print(f"▸ Running concurrency scaling test against {args.url}")
        print()
        report.concurrency_results = await run_concurrency_test(
            args.url, max_concurrent=args.parallel * 2
        )
        print()
        print("▸ Running baseline sweep (quick)...")
        scenarios = build_quick_scenarios()
        all_results = await run_scenarios(
            args.url, scenarios, max_parallel=args.parallel,
            timeout=args.timeout, run_id=run_id,
        )
        report = build_report(all_results)
        report.concurrency_results = await run_concurrency_test(
            args.url, max_concurrent=args.parallel * 2
        )
    else:
        print(f"▸ Mode: {mode}")
        print(f"  Scenarios: {len(scenarios)}")
        print(f"  Parallel:  {args.parallel}")
        print(f"  Timeout:   {args.timeout}s")
        print(f"  Run ID:    {run_id}")
        print()
        all_results = await run_scenarios(
            args.url, scenarios, max_parallel=args.parallel,
            timeout=args.timeout, run_id=run_id,
        )
        report = build_report(all_results)

        if args.sweep:
            print("\n▸ Appending parameter sweep...")
            sweep = build_parameter_sweep_scenarios()
            sweep_results = await run_scenarios(
                args.url, sweep, max_parallel=2,
                timeout=args.timeout, run_id=f"{run_id}-sweep",
            )
            all_results.extend(sweep_results)
            sweep_report = build_report(sweep_results)
            report.per_intent.update(sweep_report.per_intent)

    report.finished_at = datetime.now(timezone.utc).isoformat()

    # Post-run diagnostics
    diag_after = await check_diagnostics(args.url)
    if diag_after:
        print(f"\n▸ Post-run diagnostics: {json.dumps(diag_after, indent=2)}")

    print_report(report)

    # Fetch per-generation artifacts (MP3, WebP piano-roll, MIDI)
    print("▸ Fetching generation artifacts...")
    artifacts = await fetch_run_artifacts(args.url, all_results)
    print(f"  Fetched artifacts for {len(artifacts)} generations")

    # ── Muse VCS phase ────────────────────────────────────────────────────
    muse_result: MuseRunResult | None = None
    if not args.no_muse:
        print("▸ Running Muse VCS phase...")
        try:
            token = _get_test_token()
            muse_project_id = f"stress-{ts}"
            muse_result = await run_muse_phase(
                maestro_url=args.muse_url,
                token=token,
                results=all_results,
                project_id=muse_project_id,
            )
            if muse_result.error:
                print(f"  ⚠️  Muse phase: {muse_result.error}")
            else:
                n_commits = len(muse_result.nodes)
                n_merges = sum(1 for n in muse_result.nodes if n.parent2_id)
                print(f"  ✅ {n_commits} commits, {n_merges} merge commit(s)")
                demo = muse_result.conflict_demo or {}
                demo_status = demo.get("status", "—")
                if demo_status == "conflict_detected":
                    n_conf = len(demo.get("conflicts", []))
                    res = demo.get("resolution") or {}
                    merge_vid = res.get("merge_variation_id", "")[:8]
                    print(f"  ⚡ Conflict demo: {n_conf} conflict(s) → HTTP 409 → resolved → merge commit {merge_vid} ✅")
                elif demo_status == "unexpected_success":
                    print(f"  ⚠️  Conflict demo: merge unexpectedly succeeded (empty snapshots)")
                elif demo_status == "error":
                    print(f"  ❌ Conflict demo error: {demo.get('error')}")
        except Exception as exc:
            print(f"  ❌ Muse phase failed: {exc}")
            muse_result = MuseRunResult(project_id=run_id, error=str(exc))

    # Output paths — write to bind-mounted scripts/e2e/ so files are accessible on host
    out_dir = "/app/scripts/e2e"
    json_path = args.output or f"{out_dir}/stress_results_{ts}.json"
    html_path = json_path.replace(".json", ".html")

    report_dict = asdict(report)
    with open(json_path, "w") as f:
        json.dump(report_dict, f, indent=2, default=str)
    print(f"  JSON results: {json_path}")

    html = build_html_report(
        report, mode=mode, url=args.url,
        results=all_results, artifacts=artifacts,
        muse=muse_result,
    )
    with open(html_path, "w") as f:
        f.write(html)
    print(f"  HTML report:  {html_path}")


if __name__ == "__main__":
    asyncio.run(main())
