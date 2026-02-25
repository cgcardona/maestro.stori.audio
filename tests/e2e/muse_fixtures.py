"""Deterministic fixtures for Muse E2E harness.

Provides fixed IDs, snapshot builders, and variation payload constructors
so the full VCS lifecycle can be exercised with stable, predictable data.
"""

from __future__ import annotations

from typing import Any

# ── Fixed IDs ─────────────────────────────────────────────────────────────

PROJECT_ID = "proj_muse_e2e"
CONVO_ID = "convo_muse_e2e"

R_KEYS = "r_keys"
R_BASS = "r_bass"
R_DRUMS = "r_drums"

T_KEYS = "t_keys"
T_BASS = "t_bass"
T_DRUMS = "t_drums"

C0 = "c0000000-0000-0000-0000-000000000000"
C1 = "c1000000-0000-0000-0000-000000000000"
C2 = "c2000000-0000-0000-0000-000000000000"
C3 = "c3000000-0000-0000-0000-000000000000"
# C4 = merge commit — ID assigned by merge_variations at runtime
C5 = "c5000000-0000-0000-0000-000000000000"
C6 = "c6000000-0000-0000-0000-000000000000"

_REGION_TRACK_MAP: dict[str, str] = {
    R_KEYS: T_KEYS,
    R_BASS: T_BASS,
    R_DRUMS: T_DRUMS,
}


def _track_for(region_id: str) -> str:
    return _REGION_TRACK_MAP.get(region_id, region_id.replace("r_", "t_"))


# ── Snapshot builders ─────────────────────────────────────────────────────


def snapshot_empty() -> dict[str, list[dict[str, Any]]]:
    return {}


def snapshot_keys_v1() -> dict[str, list[dict[str, Any]]]:
    """C major arpeggio — 4 notes in r_keys."""
    return {
        R_KEYS: [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
            {"pitch": 64, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 90},
            {"pitch": 67, "start_beat": 2.0, "duration_beats": 1.0, "velocity": 80},
            {"pitch": 72, "start_beat": 3.0, "duration_beats": 1.0, "velocity": 100},
        ],
    }


def snapshot_bass_v1() -> dict[str, list[dict[str, Any]]]:
    """Simple root-fifth bass line in r_bass."""
    return {
        R_BASS: [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 2.0, "velocity": 110},
            {"pitch": 43, "start_beat": 2.0, "duration_beats": 2.0, "velocity": 105},
        ],
    }


def snapshot_drums_v1() -> dict[str, list[dict[str, Any]]]:
    """Kick-snare-hat pattern in r_drums."""
    return {
        R_DRUMS: [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 120},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.5, "velocity": 100},
            {"pitch": 42, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 80},
            {"pitch": 42, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 80},
        ],
    }


def snapshot_keys_v2_with_cc() -> dict[str, list[dict[str, Any]]]:
    """Keys v1 with an extra note at pitch=48 beat=4 — conflict branch A."""
    notes = snapshot_keys_v1()[R_KEYS].copy()
    notes.append({"pitch": 48, "start_beat": 4.0, "duration_beats": 1.0, "velocity": 95})
    return {R_KEYS: notes}


def snapshot_keys_v3_conflict() -> dict[str, list[dict[str, Any]]]:
    """Keys v1 with same pitch=48 beat=4 but different velocity — conflict branch B.

    Overlaps with v2 at the same (pitch, start_beat) so the merge engine
    detects a conflicting addition.
    """
    notes = snapshot_keys_v1()[R_KEYS].copy()
    notes.append({"pitch": 48, "start_beat": 4.0, "duration_beats": 2.0, "velocity": 60})
    return {R_KEYS: notes}


def cc_sustain_branch_a() -> dict[str, list[dict[str, Any]]]:
    """CC64 sustain pattern for conflict branch A."""
    return {
        R_KEYS: [
            {"kind": "cc", "cc": 64, "beat": 0.0, "value": 127},
            {"kind": "cc", "cc": 64, "beat": 3.0, "value": 0},
        ],
    }


def cc_sustain_branch_b() -> dict[str, list[dict[str, Any]]]:
    """CC64 sustain pattern for conflict branch B (different values)."""
    return {
        R_KEYS: [
            {"kind": "cc", "cc": 64, "beat": 0.0, "value": 64},
            {"kind": "cc", "cc": 64, "beat": 2.0, "value": 0},
        ],
    }


# ── Variation payload builder ─────────────────────────────────────────────


def _note_key(n: dict[str, Any]) -> tuple[int, float]:
    return (n.get("pitch", 0), n.get("start_beat", 0.0))


def make_variation_payload(
    variation_id: str,
    intent: str,
    base_notes: dict[str, list[dict[str, Any]]],
    proposed_notes: dict[str, list[dict[str, Any]]],
    *,
    parent_variation_id: str | None = None,
    parent2_variation_id: str | None = None,
    controller_changes: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build a POST /muse/variations request body with proper NoteChange diffs."""
    phrases: list[dict[str, Any]] = []
    all_regions = sorted(set(base_notes) | set(proposed_notes))

    for rid in all_regions:
        base = base_notes.get(rid, [])
        proposed = proposed_notes.get(rid, [])

        base_keys = {_note_key(n) for n in base}
        proposed_keys = {_note_key(n) for n in proposed}

        note_changes: list[dict[str, Any]] = []
        for n in proposed:
            key = _note_key(n)
            if key not in base_keys:
                note_changes.append({
                    "note_id": f"nc-{variation_id[:8]}-{rid}-p{key[0]}b{key[1]}",
                    "change_type": "added",
                    "before": None,
                    "after": n,
                })
        for n in base:
            key = _note_key(n)
            if key not in proposed_keys:
                note_changes.append({
                    "note_id": f"nc-{variation_id[:8]}-{rid}-p{key[0]}b{key[1]}",
                    "change_type": "removed",
                    "before": n,
                    "after": None,
                })

        cc = (controller_changes or {}).get(rid, [])
        tid = _track_for(rid)

        phrases.append({
            "phrase_id": f"ph-{variation_id[:8]}-{rid}",
            "track_id": tid,
            "region_id": rid,
            "start_beat": 0.0,
            "end_beat": 8.0,
            "label": f"{intent} ({rid})",
            "note_changes": note_changes,
            "controller_changes": cc,
        })

    return {
        "project_id": PROJECT_ID,
        "variation_id": variation_id,
        "intent": intent,
        "conversation_id": CONVO_ID,
        "parent_variation_id": parent_variation_id,
        "parent2_variation_id": parent2_variation_id,
        "affected_tracks": [_track_for(r) for r in all_regions],
        "affected_regions": list(all_regions),
        "phrases": phrases,
        "beat_range": [0.0, 8.0],
    }
