"""MuseClient — commit, branch, merge, graph via MUSE HTTP API."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

from stori_tourdeforce.config import TDFConfig
from stori_tourdeforce.models import (
    Component, EventType, Severity, TraceContext, sha256_payload,
)
from stori_tourdeforce.collectors.events import EventCollector
from stori_tourdeforce.collectors.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class MuseClient:
    """Talks to the MUSE VCS HTTP API for commits, merges, and graph exports."""

    def __init__(
        self,
        config: TDFConfig,
        event_collector: EventCollector,
        metrics: MetricsCollector,
        payload_dir: Path,
        muse_dir: Path,
    ) -> None:
        self._config = config
        self._events = event_collector
        self._metrics = metrics
        self._muse_dir = muse_dir
        self._muse_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            base_url=config.muse_base_url,
            timeout=30.0,
            headers=config.auth_headers,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def save_variation(
        self,
        run_id: str,
        trace: TraceContext,
        *,
        variation_id: str | None = None,
        intent: str = "compose",
        phrases: list[dict] | None = None,
        affected_tracks: list[str] | None = None,
        affected_regions: list[str] | None = None,
        parent_variation_id: str | None = None,
        parent2_variation_id: str | None = None,
        conversation_id: str = "default",
    ) -> str:
        """Persist a variation into MUSE history."""
        span = trace.new_span("muse_commit")
        vid = variation_id or str(uuid.uuid4())

        payload = {
            "project_id": self._config.muse_project_id,
            "variation_id": vid,
            "intent": intent,
            "conversation_id": conversation_id,
            "parent_variation_id": parent_variation_id,
            "parent2_variation_id": parent2_variation_id,
            "phrases": phrases or [],
            "affected_tracks": affected_tracks or [],
            "affected_regions": affected_regions or [],
        }

        await self._events.emit(
            run_id=run_id,
            scenario="muse_commit",
            component=Component.MUSE,
            event_type=EventType.MUSE_COMMIT,
            trace=trace,
            data={"variation_id": vid, "parent": parent_variation_id, "intent": intent},
        )

        async with self._metrics.timer("muse_save_variation", run_id):
            resp = await self._client.post("/variations", json=payload)

        if resp.status_code != 200:
            trace.end_span()
            raise MuseError(f"MUSE save_variation failed: {resp.status_code} — {resp.text[:500]}")

        trace.end_span()
        return vid

    async def set_head(
        self,
        run_id: str,
        trace: TraceContext,
        variation_id: str,
    ) -> None:
        """set HEAD pointer."""
        resp = await self._client.post("/head", json={"variation_id": variation_id})
        if resp.status_code != 200:
            raise MuseError(f"MUSE set_head failed: {resp.status_code} — {resp.text[:500]}")

    async def merge(
        self,
        run_id: str,
        trace: TraceContext,
        left_id: str,
        right_id: str,
        *,
        force: bool = True,
        conversation_id: str = "default",
    ) -> MergeResult:
        """Three-way merge of two variations."""
        span = trace.new_span("muse_merge")

        await self._events.emit(
            run_id=run_id,
            scenario="muse_merge",
            component=Component.MUSE,
            event_type=EventType.MUSE_MERGE,
            trace=trace,
            data={"left": left_id, "right": right_id},
        )

        payload = {
            "project_id": self._config.muse_project_id,
            "left_id": left_id,
            "right_id": right_id,
            "conversation_id": conversation_id,
            "force": force,
        }

        async with self._metrics.timer("muse_merge", run_id):
            resp = await self._client.post("/merge", json=payload)

        body = resp.json()

        if resp.status_code == 409:
            conflicts = body.get("detail", {}).get("conflicts", [])
            trace.end_span()
            return MergeResult(
                success=False,
                merge_variation_id="",
                conflicts=conflicts,
                status_code=409,
            )

        if resp.status_code != 200:
            trace.end_span()
            raise MuseError(f"MUSE merge failed: {resp.status_code} — {resp.text[:500]}")

        trace.end_span()
        return MergeResult(
            success=True,
            merge_variation_id=body.get("merge_variation_id", ""),
            executed=body.get("executed", 0),
            status_code=200,
        )

    async def get_log(
        self,
        run_id: str,
        trace: TraceContext,
    ) -> dict[str, Any]:
        """Fetch the commit DAG."""
        resp = await self._client.get("/log", params={"project_id": self._config.muse_project_id})
        if resp.status_code != 200:
            raise MuseError(f"MUSE get_log failed: {resp.status_code}")
        graph = resp.json()

        # Persist graph
        graph_file = self._muse_dir / "graph.json"
        graph_file.write_text(json.dumps(graph, indent=2))

        return graph

    async def checkout(
        self,
        run_id: str,
        trace: TraceContext,
        target_variation_id: str,
        *,
        force: bool = True,
        conversation_id: str = "default",
    ) -> CheckoutResult:
        """Checkout to a specific variation.

        Returns a CheckoutResult with success/blocked status and drift details.
        Non-force checkouts may return 409 if the working tree has drift.
        """
        span = trace.new_span("muse_checkout")

        payload = {
            "project_id": self._config.muse_project_id,
            "target_variation_id": target_variation_id,
            "conversation_id": conversation_id,
            "force": force,
        }

        await self._events.emit(
            run_id=run_id,
            scenario="muse_checkout",
            component=Component.MUSE,
            event_type=EventType.MUSE_COMMIT,
            trace=trace,
            tags={"operation": "checkout"},
            data={"target": target_variation_id, "force": force},
        )

        async with self._metrics.timer("muse_checkout", run_id, tags={"force": str(force)}):
            resp = await self._client.post("/checkout", json=payload)

        body = resp.json()

        if resp.status_code == 409:
            detail = body.get("detail", body)
            trace.end_span()
            return CheckoutResult(
                success=False,
                blocked=True,
                target=target_variation_id,
                drift_severity=detail.get("severity", "unknown"),
                drift_total_changes=detail.get("total_changes", 0),
                status_code=409,
            )

        if resp.status_code == 404:
            trace.end_span()
            raise MuseError(f"Variation {target_variation_id} not found (404)")

        if resp.status_code != 200:
            trace.end_span()
            raise MuseError(f"MUSE checkout failed: {resp.status_code} — {resp.text[:500]}")

        trace.end_span()
        return CheckoutResult(
            success=True,
            blocked=False,
            target=target_variation_id,
            head_moved=body.get("head_moved", False),
            executed=body.get("executed", 0),
            failed=body.get("failed", 0),
            plan_hash=body.get("plan_hash", ""),
            status_code=200,
        )

    async def save_conflict_branch(
        self,
        run_id: str,
        trace: TraceContext,
        *,
        variation_id: str,
        parent_variation_id: str,
        intent: str,
        target_region: str,
        target_track: str,
        notes: list[dict[str, Any]],
        conversation_id: str = "default",
    ) -> str:
        """Save a variation with explicit note changes — used to create deliberate conflicts.

        Both conflict branches must add notes at the same (pitch, start_beat) position
        but with different content to trigger MUSE's merge conflict detection.
        """
        note_changes = [
            {
                "note_id": f"nc-{variation_id[:8]}-{target_region}-p{n['pitch']}b{n['start_beat']}",
                "change_type": "added",
                "before": None,
                "after": n,
            }
            for n in notes
        ]

        phrases = [{
            "phrase_id": f"ph-{variation_id[:8]}-{target_region}",
            "track_id": target_track,
            "region_id": target_region,
            "start_beat": 0.0,
            "end_beat": 8.0,
            "label": f"{intent} ({target_region})",
            "note_changes": note_changes,
            "controller_changes": [],
        }]

        return await self.save_variation(
            run_id=run_id,
            trace=trace,
            variation_id=variation_id,
            intent=intent,
            phrases=phrases,
            affected_tracks=[target_track],
            affected_regions=[target_region],
            parent_variation_id=parent_variation_id,
            conversation_id=conversation_id,
        )


class CheckoutResult:
    """Structured checkout result."""

    def __init__(
        self,
        success: bool,
        blocked: bool = False,
        target: str = "",
        head_moved: bool = False,
        executed: int = 0,
        failed: int = 0,
        plan_hash: str = "",
        drift_severity: str = "",
        drift_total_changes: int = 0,
        status_code: int = 200,
    ) -> None:
        self.success = success
        self.blocked = blocked
        self.target = target
        self.head_moved = head_moved
        self.executed = executed
        self.failed = failed
        self.plan_hash = plan_hash
        self.drift_severity = drift_severity
        self.drift_total_changes = drift_total_changes
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "blocked": self.blocked,
            "target": self.target,
            "head_moved": self.head_moved,
            "executed": self.executed,
            "plan_hash": self.plan_hash[:12] if self.plan_hash else "",
            "drift_severity": self.drift_severity,
            "drift_total_changes": self.drift_total_changes,
        }


class MergeResult:
    """Structured merge result."""

    def __init__(
        self,
        success: bool,
        merge_variation_id: str = "",
        conflicts: list[dict] | None = None,
        executed: int = 0,
        status_code: int = 200,
    ) -> None:
        self.success = success
        self.merge_variation_id = merge_variation_id
        self.conflicts = conflicts or []
        self.executed = executed
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "merge_variation_id": self.merge_variation_id,
            "conflict_count": len(self.conflicts),
            "conflicts": self.conflicts,
            "executed": self.executed,
        }


class MuseError(Exception):
    pass
