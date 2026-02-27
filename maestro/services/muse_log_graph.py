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
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

from maestro.services.muse_repository import VariationSummary, get_variations_for_project

logger = logging.getLogger(__name__)


# ── Pydantic response models ──────────────────────────────────────────────────


class MuseLogNodeResponse(BaseModel):
    """Wire representation of a single node in the Muse commit DAG.

    Produced by ``MuseLogNode.to_response()`` and serialised as JSON by the
    ``GET /muse/log`` endpoint.  Field names are camelCase to match the Stori
    DAW Swift convention.

    Attributes:
        id: UUID of this variation (commit).
        parent: UUID of the first (or only) parent variation, or ``None`` for
            the root commit.
        parent2: UUID of the second parent for merge commits; ``None`` for
            ordinary (linear) commits.
        isHead: ``True`` if this node is the current HEAD pointer for the
            project (i.e. the variation the DAW currently has loaded).
        timestamp: POSIX timestamp (seconds since epoch, float) of when the
            variation was committed.  Used for topological sort tie-breaking
            and for display in the DAW's history panel.
        intent: Free-text intent string supplied when the variation was created
            (e.g. ``"add bass groove"``), or ``None`` if none was provided.
        regions: List of region IDs whose MIDI content was affected by this
            variation.  Empty for root / no-op commits.
    """

    id: str = Field(description="UUID of this variation (commit).")
    parent: str | None = Field(
        description="UUID of the first parent variation, or None for the root commit."
    )
    parent2: str | None = Field(
        description=(
            "UUID of the second parent for merge commits; "
            "None for ordinary (linear) commits."
        )
    )
    isHead: bool = Field(
        description=(
            "True if this node is the current HEAD pointer — "
            "the variation the DAW currently has loaded."
        )
    )
    timestamp: float = Field(
        description=(
            "POSIX timestamp (seconds since epoch) of when the variation was committed. "
            "Used for topological sort tie-breaking and history-panel display."
        )
    )
    intent: str | None = Field(
        description=(
            "Free-text intent string supplied at commit time "
            "(e.g. 'add bass groove'), or None if none was provided."
        )
    )
    regions: list[str] = Field(
        description=(
            "List of region IDs whose MIDI content was affected by this variation. "
            "Empty for root or no-op commits."
        )
    )


class MuseLogGraphResponse(BaseModel):
    """Wire representation of the full Muse commit DAG for a project.

    Produced by ``MuseLogGraph.to_response()`` and returned directly by
    ``GET /muse/log``.  The Swift frontend renders this as a visual commit
    timeline, highlighting the HEAD node and drawing parent edges.

    Attributes:
        projectId: UUID of the project this DAG belongs to.
        head: UUID of the current HEAD variation, or ``None`` if no HEAD has
            been set yet (brand-new project with no commits).
        nodes: Topologically sorted list of all variations (commits) for this
            project.  Parents always appear before their children; ties are
            broken by timestamp then variation UUID.
    """

    projectId: str = Field(
        description="UUID of the project this DAG belongs to."
    )
    head: str | None = Field(
        description=(
            "UUID of the current HEAD variation, or None if no HEAD has been set yet "
            "(brand-new project with no commits)."
        )
    )
    nodes: list[MuseLogNodeResponse] = Field(
        description=(
            "Topologically sorted list of all variations (commits) for this project. "
            "Parents always appear before their children; ties broken by timestamp then UUID."
        )
    )


# ── Internal domain dataclasses ───────────────────────────────────────────────


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

    def to_response(self) -> MuseLogNodeResponse:
        """Convert this internal node to its Pydantic wire representation.

        Translates snake_case internal field names to the camelCase names
        expected by the Stori DAW frontend, and converts the immutable
        ``affected_regions`` tuple to a plain list.

        Returns:
            ``MuseLogNodeResponse`` ready for JSON serialisation by FastAPI.
        """
        return MuseLogNodeResponse(
            id=self.variation_id,
            parent=self.parent,
            parent2=self.parent2,
            isHead=self.is_head,
            timestamp=self.timestamp,
            intent=self.intent,
            regions=list(self.affected_regions),
        )


@dataclass(frozen=True)
class MuseLogGraph:
    """The full commit DAG for a project, topologically ordered."""

    project_id: str
    head: str | None
    nodes: tuple[MuseLogNode, ...]

    def to_response(self) -> MuseLogGraphResponse:
        """Convert this internal graph to its Pydantic wire representation.

        Calls ``MuseLogNode.to_response()`` on every node in topological order
        and wraps them in a ``MuseLogGraphResponse``.

        Returns:
            ``MuseLogGraphResponse`` ready for JSON serialisation by FastAPI.
        """
        return MuseLogGraphResponse(
            projectId=self.project_id,
            head=self.head,
            nodes=[n.to_response() for n in self.nodes],
        )


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
