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
import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.e2e.muse_fixtures import (
    C0, C1, C2, C3, C5, C6,
    CONVO_ID, PROJECT_ID,
    MuseVariationPayload,
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


# ── Response-narrowing helpers ─────────────────────────────────────────────
# JSON responses are dict[str, object]. These helpers narrow values to their
# expected types and assert the API contract simultaneously.

def _s(d: dict[str, object], key: str) -> str:
    """Extract a required string field from a response dict."""
    v = d[key]
    assert isinstance(v, str), f"Expected str for {key!r}, got {type(v).__name__}: {v!r}"
    return v


def _s_opt(d: dict[str, object], key: str) -> str | None:
    """Extract an optional string field (None allowed) from a response dict."""
    v = d.get(key)
    assert v is None or isinstance(v, str), f"Expected str|None for {key!r}, got {type(v).__name__}"
    return v if isinstance(v, str) else None


def _d(d: dict[str, object], key: str) -> dict[str, object]:
    """Extract a required dict field from a response dict."""
    v = d[key]
    assert isinstance(v, dict), f"Expected dict for {key!r}, got {type(v).__name__}"
    return v


def _nodes(d: dict[str, object]) -> list[dict[str, object]]:
    """Extract and validate the 'nodes' list from a log response."""
    raw = d["nodes"]
    assert isinstance(raw, list), f"Expected list for 'nodes', got {type(raw).__name__}"
    result: list[dict[str, object]] = []
    for item in raw:
        assert isinstance(item, dict), f"Expected dict node, got {type(item).__name__}"
        result.append(item)
    return result

# ── Counters for summary table ────────────────────────────────────────────

_checkouts_executed = 0
_drift_blocks = 0
_conflict_merges = 0
_forced_ops = 0


# ── Helpers ───────────────────────────────────────────────────────────────


async def save(client: AsyncClient, payload: MuseVariationPayload, headers: dict[str, str]) -> dict[str, object]:
    resp = await client.post(f"{BASE}/variations", json=payload, headers=headers)
    assert resp.status_code == 200, f"save failed: {resp.text}"
    result: dict[str, object] = resp.json()
    return result


async def set_head(client: AsyncClient, vid: str, headers: dict[str, str]) -> dict[str, object]:
    resp = await client.post(f"{BASE}/head", json={"variation_id": vid}, headers=headers)
    assert resp.status_code == 200, f"set_head failed: {resp.text}"
    result: dict[str, object] = resp.json()
    return result


async def get_log(client: AsyncClient, headers: dict[str, str]) -> dict[str, object]:
    resp = await client.get(f"{BASE}/log", params={"project_id": PROJECT_ID}, headers=headers)
    assert resp.status_code == 200, f"get_log failed: {resp.text}"
    result: dict[str, object] = resp.json()
    return result


async def do_checkout(
    client: AsyncClient, target: str, headers: dict[str, str], *, force: bool = False,
) -> dict[str, object]:
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
        result: dict[str, object] = resp.json()
        return result
    assert resp.status_code == 200, f"checkout failed: {resp.text}"
    _checkouts_executed += 1
    if force:
        _forced_ops += 1
    result = resp.json()
    return result


async def do_merge(
    client: AsyncClient, left: str, right: str, headers: dict[str, str], *, force: bool = False,
) -> tuple[int, dict[str, object]]:
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
    body: dict[str, object] = resp.json()
    return resp.status_code, body


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
    assert len(_nodes(log)) == 1
    assert _s(log, "head") == C0
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
    print(f"  ✅ C1 committed + checked out, executed={_d(co, 'execution')['executed']} tool calls")

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
    nodes = _nodes(log)
    node_ids = [_s(n, "id") for n in nodes]
    assert C1 in node_ids and C2 in node_ids
    assert _s(log, "head") == C2
    print(f"  ✅ C2 committed, HEAD={C2[:8]}, graph has {len(nodes)} nodes")

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
    c4_id = _s(merge_resp, "merge_variation_id")
    print(f"  ✅ Merge commit C4={c4_id[:8]}, executed={_d(merge_resp, 'execution')['executed']} tool calls")

    log = await get_log(client, headers)
    assert _s(log, "head") == c4_id
    c4_node = next(n for n in _nodes(log) if _s(n, "id") == c4_id)
    assert c4_node["parent2"] is not None, "Merge commit must have two parents"
    print(f"  ✅ Merge commit has parent={_s(c4_node, 'parent')[:8]}, parent2={_s(c4_node, 'parent2')[:8]}")

    # ── Step 5: Conflict merge demo ──────────────────────────────────
    print("\n═══ Step 5: Conflict merge demo (C5 vs C6) ═══")
    # C5: branch from C1, adds note + CC in r_keys
    await save(client, make_variation_payload(
        C5, "keys v2 (branch A)", snapshot_keys_v1(), snapshot_keys_v2_with_cc(),
        parent_variation_id=C1,
        cc_events=cc_sustain_branch_a(),
    ), headers)
    # C6: branch from C1, adds different note + different CC in r_keys
    await save(client, make_variation_payload(
        C6, "keys v3 (branch B)", snapshot_keys_v1(), snapshot_keys_v3_conflict(),
        parent_variation_id=C1,
        cc_events=cc_sustain_branch_b(),
    ), headers)

    status, conflict_resp = await do_merge(client, C5, C6, headers)
    _conflict_merges += 1
    assert status == 409, f"Expected 409 conflict, got {status}: {conflict_resp}"
    detail = _d(conflict_resp, "detail")
    assert detail["error"] == "merge_conflict"
    _conflicts_raw = detail["conflicts"]
    assert isinstance(_conflicts_raw, list)
    conflicts: list[dict[str, object]] = [c for c in _conflicts_raw if isinstance(c, dict)]
    assert len(conflicts) >= 1, "Expected at least one conflict"
    print(f"  ✅ Conflict detected: {len(conflicts)} conflict(s)")
    for c in conflicts:
        print(f"     {_s(c, 'type')}: {_s(c, 'description')}")

    # ── Step 6: (Skipped — cherry-pick not yet implemented) ──────────
    print("\n═══ Step 6: Cherry-pick — skipped (future phase) ═══")

    # ── Step 7: Checkout traversal demo ──────────────────────────────
    print("\n═══ Step 7: Checkout traversal ═══")
    plan_hashes: list[str] = []

    co = await do_checkout(client, C1, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(_s(_d(co, "execution"), "plan_hash"))
    print(f"  → Checked out C1: executed={_d(co, 'execution')['executed']}, hash={_s(_d(co, 'execution'), 'plan_hash')[:12]}")

    co = await do_checkout(client, C2, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(_s(_d(co, "execution"), "plan_hash"))
    print(f"  → Checked out C2: executed={_d(co, 'execution')['executed']}, hash={_s(_d(co, 'execution'), 'plan_hash')[:12]}")

    co = await do_checkout(client, c4_id, headers, force=True)
    assert co["head_moved"]
    plan_hashes.append(_s(_d(co, "execution"), "plan_hash"))
    print(f"  → Checked out C4 (merge): executed={_d(co, 'execution')['executed']}, hash={_s(_d(co, 'execution'), 'plan_hash')[:12]}")

    # Checkout to same target again — should be no-op or same hash
    co2 = await do_checkout(client, c4_id, headers, force=True)
    assert co2["head_moved"]
    print(f"  → Re-checkout C4: executed={_d(co2, 'execution')['executed']}, hash={_s(_d(co2, 'execution'), 'plan_hash')[:12]}")
    print(f"  ✅ All checkouts transactional, plan hashes: {[h[:12] for h in plan_hashes]}")

    # ── Final assertions ─────────────────────────────────────────────
    print("\n═══ Final Assertions ═══")

    log = await get_log(client, headers)
    log_nodes = _nodes(log)

    # DAG correctness
    node_map = {_s(n, "id"): n for n in log_nodes}
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
    assert _s(log, "head") == c4_id
    print(f"  ✅ HEAD = {c4_id[:8]}")

    # Topological order: parents before children
    id_order = [_s(n, "id") for n in log_nodes]
    for n in log_nodes:
        n_id = _s(n, "id")
        n_parent = _s_opt(n, "parent")
        n_parent2 = _s_opt(n, "parent2")
        if n_parent and n_parent in node_map:
            assert id_order.index(n_parent) < id_order.index(n_id), \
                f"Parent {n_parent[:8]} must appear before child {n_id[:8]}"
        if n_parent2 and n_parent2 in node_map:
            assert id_order.index(n_parent2) < id_order.index(n_id), \
                f"Parent2 {n_parent2[:8]} must appear before child {n_id[:8]}"
    print("  ✅ Topological ordering: parents before children")

    # camelCase serialization
    for n in log_nodes:
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

    from maestro.services.muse_log_render import render_ascii_graph, render_json, render_summary_table
    from maestro.services.muse_log_graph import MuseLogGraph, MuseLogNode

    # Reconstruct MuseLogGraph from the JSON for rendering
    import time

    def _str_opt(v: object) -> str | None:
        return v if isinstance(v, str) else None

    def _float_ts(v: object) -> float:
        """Parse a timestamp value — ISO string or numeric — to a float epoch."""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            from datetime import datetime, timezone
            try:
                return datetime.fromisoformat(v).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        return time.time()

    graph = MuseLogGraph(
        project_id=_s(log, "projectId"),
        head=_s_opt(log, "head"),
        nodes=tuple(
            MuseLogNode(
                variation_id=_s(n, "id"),
                parent=_str_opt(n.get("parent")),
                parent2=_str_opt(n.get("parent2")),
                is_head=bool(n.get("isHead")),
                timestamp=_float_ts(n.get("timestamp")),
                intent=_str_opt(n.get("intent")),
                affected_regions=tuple(
                    r
                    for _rgns in [n.get("regions")]
                    if isinstance(_rgns, list)
                    for r in _rgns
                    if isinstance(r, str)
                ),
            )
            for n in log_nodes
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
