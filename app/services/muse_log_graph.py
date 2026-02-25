"""Muse Log Graph — serialize the commit DAG as Swift-ready JSON.

Pure read-only projection layer. Fetches all variations for a project
in a single query, builds the DAG in memory, and performs a stable
topological sort. O(N + E) time complexity.

Boundary rules:
  - Must NOT import StateStore, executor, handlers, LLM code,
    drift engine, merge engine, or checkout modules.
  - May import muse_repository (for bulk queries).
"""

from __future__ import annotations

import heapq
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.muse_repository import VariationSummary, get_variations_for_project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MuseLogNode:
    """A single node in the commit DAG."""

    variation_id: str
    parent: str | None
    parent2: str | None
    is_head: bool
    timestamp: float
    intent: str | None
    affected_regions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.variation_id,
            "parent": self.parent,
            "parent2": self.parent2,
            "isHead": self.is_head,
            "timestamp": self.timestamp,
            "intent": self.intent,
            "regions": list(self.affected_regions),
        }
        return d


@dataclass(frozen=True)
class MuseLogGraph:
    """The full commit DAG for a project, topologically ordered."""

    project_id: str
    head: str | None
    nodes: tuple[MuseLogNode, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "head": self.head,
            "nodes": [n.to_dict() for n in self.nodes],
        }


async def build_muse_log_graph(
    session: AsyncSession,
    project_id: str,
) -> MuseLogGraph:
    """Build the full commit DAG for a project.

    Performs a single bulk query, then computes the topological ordering
    in memory.  Parents always appear before children; ties are broken
    by timestamp (earliest first), then by variation_id for determinism.
    """
    summaries = await get_variations_for_project(session, project_id)

    if not summaries:
        return MuseLogGraph(project_id=project_id, head=None, nodes=())

    nodes = _build_nodes(summaries)
    sorted_nodes = _topological_sort(nodes)
    head_id = _find_head(summaries)

    logger.info(
        "✅ Log graph built: project=%s, %d nodes, head=%s",
        project_id[:8], len(sorted_nodes), (head_id or "none")[:8],
    )

    return MuseLogGraph(
        project_id=project_id,
        head=head_id,
        nodes=tuple(sorted_nodes),
    )


def _build_nodes(summaries: list[VariationSummary]) -> list[MuseLogNode]:
    """Convert VariationSummary rows into MuseLogNode instances."""
    return [
        MuseLogNode(
            variation_id=s.variation_id,
            parent=s.parent_variation_id,
            parent2=s.parent2_variation_id,
            is_head=s.is_head,
            timestamp=s.created_at.timestamp(),
            intent=s.intent if s.intent else None,
            affected_regions=s.affected_regions,
        )
        for s in summaries
    ]


def _find_head(summaries: list[VariationSummary]) -> str | None:
    """Return the HEAD variation_id, or None if no HEAD is set."""
    for s in summaries:
        if s.is_head:
            return s.variation_id
    return None


def _topological_sort(nodes: list[MuseLogNode]) -> list[MuseLogNode]:
    """Stable topological sort via Kahn's algorithm.

    Ordering guarantees:
    1. Parents always appear before children.
    2. Tie-break by timestamp (earliest first).
    3. Final tie-break by variation_id (lexicographic).
    """
    by_id: dict[str, MuseLogNode] = {n.variation_id: n for n in nodes}
    known_ids = set(by_id.keys())

    children: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n.variation_id: 0 for n in nodes}

    for node in nodes:
        if node.parent and node.parent in known_ids:
            children[node.parent].append(node.variation_id)
            in_degree[node.variation_id] += 1
        if node.parent2 and node.parent2 in known_ids:
            children[node.parent2].append(node.variation_id)
            in_degree[node.variation_id] += 1

    heap: list[tuple[float, str]] = []
    for vid, deg in in_degree.items():
        if deg == 0:
            n = by_id[vid]
            heapq.heappush(heap, (n.timestamp, vid))

    result: list[MuseLogNode] = []

    while heap:
        _, vid = heapq.heappop(heap)
        result.append(by_id[vid])

        for child_id in children[vid]:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                child = by_id[child_id]
                heapq.heappush(heap, (child.timestamp, child_id))

    return result
