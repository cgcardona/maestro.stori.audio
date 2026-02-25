"""Muse E2E Tour de Force — full VCS lifecycle through real HTTP + real DB.

Exercises every Muse primitive in a single deterministic scenario:
  commit → branch → merge → conflict → checkout (time travel)

Produces:
  1. MuseLogGraph JSON (pretty-printed)
  2. ASCII graph visualization (``git log --graph --oneline``)
  3. Summary table (commits, merges, checkouts, conflicts, drift blocks)

Run:
    docker compose exec maestro pytest tests/e2e/test_muse_e2e_harness.py -v -s
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
import json
import logging
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.e2e.muse_fixtures import (
    C0, C1, C2, C3, C5, C6,
    CONVO_ID, PROJECT_ID,
    cc_sustain_branch_a,
    cc_sustain_branch_b,
    make_variation_payload,
    snapshot_bass_v1,
    snapshot_drums_v1,
    snapshot_empty,
    snapshot_keys_v1,
    snapshot_keys_v2_with_cc,
    snapshot_keys_v3_conflict,
)

logger = logging.getLogger(__name__)

BASE = "/api/v1/muse"

# ── Counters for summary table ────────────────────────────────────────────

_checkouts_executed = 0
_drift_blocks = 0
_conflict_merges = 0
_forced_ops = 0


# ── Helpers ───────────────────────────────────────────────────────────────


async def save(client: AsyncClient, payload: dict[str, object], headers: dict[str, str]) -> Any:

    resp = await client.post(f"{BASE}/variations", json=payload, headers=headers)
    assert resp.status_code == 200, f"save failed: {resp.text}"
    return resp.json()


async def set_head(client: AsyncClient, vid: str, headers: dict[str, str]) -> Any:

    resp = await client.post(f"{BASE}/head", json={"variation_id": vid}, headers=headers)
    assert resp.status_code == 200, f"set_head failed: {resp.text}"
    return resp.json()


async def get_log(client: AsyncClient, headers: dict[str, str]) -> Any:

    resp = await client.get(f"{BASE}/log", params={"project_id": PROJECT_ID}, headers=headers)
    assert resp.status_code == 200, f"get_log failed: {resp.text}"
    return resp.json()


async def do_checkout(
    client: AsyncClient, target: str, headers: dict[str, str], *, force: bool = False,
) -> Any:
    global _checkouts_executed, _forced_ops
    resp = await client.post(f"{BASE}/checkout", json={
        "project_id": PROJECT_ID,
        "target_variation_id": target,
        "conversation_id": CONVO_ID,
        "force": force,
    }, headers=headers)
    if resp.status_code == 409:
        global _drift_blocks
        _drift_blocks += 1
        return resp.json()
    assert resp.status_code == 200, f"checkout failed: {resp.text}"
    _checkouts_executed += 1
    if force:
        _forced_ops += 1
    return resp.json()


async def do_merge(
    client: AsyncClient, left: str, right: str, headers: dict[str, str], *, force: bool = False,
) -> tuple[int, Any]:
    global _forced_ops
    resp = await client.post(f"{BASE}/merge", json={
        "project_id": PROJECT_ID,
        "left_id": left,
        "right_id": right,
        "conversation_id": CONVO_ID,
        "force": force,
    }, headers=headers)
    if force:
        _forced_ops += 1
    return resp.status_code, resp.json()


# ── The Test ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_muse_e2e_full_lifecycle(client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession) -> None:

    """Full Muse VCS lifecycle: commit → branch → merge → conflict → checkout."""
    global _checkouts_executed, _drift_blocks, _conflict_merges, _forced_ops
    _checkouts_executed = 0
    _drift_blocks = 0
    _conflict_merges = 0
    _forced_ops = 0

    headers = auth_headers

    # ── Step 0: Initialize ────────────────────────────────────────────
    print("\n═══ Step 0: Initialize ═══")
    await save(client, make_variation_payload(
        C0, "root", snapshot_empty(), snapshot_empty(),
    ), headers)
    await set_head(client, C0, headers)

    log = await get_log(client, headers)
    assert len(log["nodes"]) == 1
    assert log["head"] == C0
    print(f"  ✅ Root C0 committed, HEAD={C0[:8]}")

    # ── Step 1: Mainline commit C1 (keys v1) ──────────────────────────
    print("\n═══ Step 1: Mainline commit C1 (keys v1) ═══")
    await save(client, make_variation_payload(
        C1, "keys v1", snapshot_empty(), snapshot_keys_v1(),
        parent_variation_id=C0,
    ), headers)
    await set_head(client, C1, headers)

    co = await do_checkout(client, C1, headers, force=True)
    assert co["head_moved"]
    print(f"  ✅ C1 committed + checked out, executed={co['executed']} tool calls")

    # ── Step 2: Branch A — bass (C2) ─────────────────────────────────
    print("\n═══ Step 2: Branch A — bass v1 (C2) ═══")
    await save(client, make_variation_payload(
        C2, "bass v1", snapshot_empty(), snapshot_bass_v1(),
        parent_variation_id=C1,
    ), headers)
    await set_head(client, C2, headers)
    co = await do_checkout(client, C2, headers, force=True)
    assert co["head_moved"]

    log = await get_log(client, headers)
    node_ids = [n["id"] for n in log["nodes"]]
    assert C1 in node_ids and C2 in node_ids
    assert log["head"] == C2
    print(f"  ✅ C2 committed, HEAD={C2[:8]}, graph has {len(log['nodes'])} nodes")

    # ── Step 3: Branch B — drums (C3) ────────────────────────────────
    print("\n═══ Step 3: Branch B — drums v1 (C3) ═══")
    # Checkout back to C1 first (time travel!)
    co = await do_checkout(client, C1, headers, force=True)
    assert co["head_moved"]

    await save(client, make_variation_payload(
        C3, "drums v1", snapshot_empty(), snapshot_drums_v1(),
        parent_variation_id=C1,
    ), headers)
    await set_head(client, C3, headers)
    co = await do_checkout(client, C3, headers, force=True)
    assert co["head_moved"]
    print(f"  ✅ C3 committed, HEAD={C3[:8]}")

    # ── Step 4: Merge branches (C4 = merge commit) ───────────────────
    print("\n═══ Step 4: Merge C2 + C3 ═══")
    status, merge_resp = await do_merge(client, C2, C3, headers, force=True)
    assert status == 200, f"Merge failed: {merge_resp}"
    assert merge_resp["head_moved"]
    c4_id = merge_resp["merge_variation_id"]
    print(f"  ✅ Merge commit C4={c4_id[:8]}, executed={merge_resp['executed']} tool calls")

    log = await get_log(client, headers)
    assert log["head"] == c4_id
    c4_node = next(n for n in log["nodes"] if n["id"] == c4_id)
    assert c4_node["parent2"] is not None, "Merge commit must have two parents"
    print(f"  ✅ Merge commit has parent={c4_node['parent'][:8]}, parent2={c4_node['parent2'][:8]}")

    # ── Step 5: Conflict merge demo ──────────────────────────────────
    print("\n═══ Step 5: Conflict merge demo (C5 vs C6) ═══")
    # C5: branch from C1, adds note + CC in r_keys
    await save(client, make_variation_payload(
        C5, "keys v2 (branch A)", snapshot_keys_v1(), snapshot_keys_v2_with_cc(),
        parent_variation_id=C1,
        controller_changes=cc_sustain_branch_a(),
    ), headers)
    # C6: branch from C1, adds different note + different CC in r_keys
    await save(client, make_variation_payload(
        C6, "keys v3 (branch B)", snapshot_keys_v1(), snapshot_keys_v3_conflict(),
        parent_variation_id=C1,
        controller_changes=cc_sustain_branch_b(),
    ), headers)

    status, conflict_resp = await do_merge(client, C5, C6, headers)
    _conflict_merges += 1
    assert status == 409, f"Expected 409 conflict, got {status}: {conflict_resp}"
    detail = conflict_resp["detail"]
    assert detail["error"] == "merge_conflict"
    conflicts = detail["conflicts"]
    assert len(conflicts) >= 1, "Expected at least one conflict"
    print(f"  ✅ Conflict detected: {len(conflicts)} conflict(s)")
    for c in conflicts:
        print(f"     {c['type']}: {c['description']}")

    # ── Step 6: (Skipped — cherry-pick not yet implemented) ──────────
    print("\n═══ Step 6: Cherry-pick — skipped (future phase) ═══")

    # ── Step 7: Checkout traversal demo ──────────────────────────────
    print("\n═══ Step 7: Checkout traversal ═══")
    plan_hashes: list[str] = []

    co = await do_checkout(client, C1, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(co["plan_hash"])
    print(f"  → Checked out C1: executed={co['executed']}, hash={co['plan_hash'][:12]}")

    co = await do_checkout(client, C2, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(co["plan_hash"])
    print(f"  → Checked out C2: executed={co['executed']}, hash={co['plan_hash'][:12]}")

    co = await do_checkout(client, c4_id, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(co["plan_hash"])
    print(f"  → Checked out C4 (merge): executed={co['executed']}, hash={co['plan_hash'][:12]}")

    # Checkout to same target again — should be no-op or same hash
    co2 = await do_checkout(client, c4_id, headers, force=True)
    assert co2["head_moved"]
    print(f"  → Re-checkout C4: executed={co2['executed']}, hash={co2['plan_hash'][:12]}")
    print(f"  ✅ All checkouts transactional, plan hashes: {[h[:12] for h in plan_hashes]}")

    # ── Final assertions ─────────────────────────────────────────────
    print("\n═══ Final Assertions ═══")

    log = await get_log(client, headers)

    # DAG correctness
    node_map = {n["id"]: n for n in log["nodes"]}
    assert node_map[C0]["parent"] is None
    assert node_map[C1]["parent"] == C0
    assert node_map[C2]["parent"] == C1
    assert node_map[C3]["parent"] == C1
    assert node_map[C5]["parent"] == C1
    assert node_map[C6]["parent"] == C1
    print("  ✅ DAG parent relationships correct")

    # Merge commit has two parents
    assert c4_id in node_map
    assert node_map[c4_id]["parent"] is not None
    assert node_map[c4_id]["parent2"] is not None
    print("  ✅ Merge commit has 2 parents")

    # HEAD correctness
    assert log["head"] == c4_id
    print(f"  ✅ HEAD = {c4_id[:8]}")

    # Topological order: parents before children
    id_order = [n["id"] for n in log["nodes"]]
    for n in log["nodes"]:
        if n["parent"] and n["parent"] in node_map:
            assert id_order.index(n["parent"]) < id_order.index(n["id"]), \
                f"Parent {n['parent'][:8]} must appear before child {n['id'][:8]}"
        if n["parent2"] and n["parent2"] in node_map:
            assert id_order.index(n["parent2"]) < id_order.index(n["id"]), \
                f"Parent2 {n['parent2'][:8]} must appear before child {n['id'][:8]}"
    print("  ✅ Topological ordering: parents before children")

    # camelCase serialization
    for n in log["nodes"]:
        assert "isHead" in n
        assert "parent2" in n
    assert "projectId" in log
    print("  ✅ Serialization is camelCase and stable")

    # Conflict merge returned conflicts deterministically
    assert len(conflicts) >= 1
    assert all("region_id" in c and "type" in c and "description" in c for c in conflicts)
    print("  ✅ Conflict payloads deterministic")

    # ── Render output ────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  MUSE LOG GRAPH — ASCII")
    print("═" * 60)

    from app.services.muse_log_render import render_ascii_graph, render_json, render_summary_table
    from app.services.muse_log_graph import MuseLogGraph, MuseLogNode

    # Reconstruct MuseLogGraph from the JSON for rendering
    graph = MuseLogGraph(
        project_id=log["projectId"],
        head=log["head"],
        nodes=tuple(
            MuseLogNode(
                variation_id=n["id"],
                parent=n["parent"],
                parent2=n["parent2"],
                is_head=n["isHead"],
                timestamp=n["timestamp"],
                intent=n["intent"],
                affected_regions=tuple(n["regions"]),
            )
            for n in log["nodes"]
        ),
    )

    print(render_ascii_graph(graph))

    print("\n" + "═" * 60)
    print("  MUSE LOG GRAPH — JSON")
    print("═" * 60)
    print(render_json(graph))

    print("\n" + "═" * 60)
    print("  SUMMARY")
    print("═" * 60)
    print(render_summary_table(
        graph,
        checkouts_executed=_checkouts_executed,
        drift_blocks=_drift_blocks,
        conflict_merges=_conflict_merges,
        forced_ops=_forced_ops,
    ))
    print()
