"""Muse Inspect — serialize the full Muse commit graph to structured output.

This module is the read-only introspection engine for ``muse inspect``.  It
traverses the commit graph reachable from one or more branch heads and returns
a typed :class:`MuseInspectResult` that CLI formatters render as JSON, DOT, or
Mermaid.

Why this exists
---------------
Machine-readable graph export is a prerequisite for tooling (IDEs, CI, AI
agents) that need to reason about the shape of a project's musical history
without parsing human-readable ``muse log`` output.  The three format options
target different consumers:

- **json** — primary format for agents and programmatic clients.
- **dot** — Graphviz DOT graph for visualization pipelines.
- **mermaid** — Mermaid.js for inline documentation and GitHub markdown.

Result types
------------
:class:`MuseInspectCommit`  — one node in the graph.
:class:`MuseInspectResult`  — the full serialized graph.

Both are frozen dataclasses; callers treat them as immutable value objects.
"""
from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.models import MuseCliCommit, MuseCliTag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------


class InspectFormat(str, Enum):
    """Supported output formats for ``muse inspect``."""

    json = "json"
    dot = "dot"
    mermaid = "mermaid"


# ---------------------------------------------------------------------------
# Result types (registered in docs/reference/type_contracts.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MuseInspectCommit:
    """One commit node in the inspected graph.

    Fields mirror the :class:`~maestro.muse_cli.models.MuseCliCommit` ORM row
    but are typed for agent consumption:

    - ``commit_id`` / ``short_id``: full and 8-char abbreviated hash.
    - ``branch``: the branch this commit was recorded on.
    - ``parent_commit_id`` / ``parent2_commit_id``: DAG parent links (second
      parent is reserved for merge commits, issue #35).
    - ``message``: human-readable commit message.
    - ``author``: committer identity string.
    - ``committed_at``: ISO-8601 UTC timestamp string.
    - ``snapshot_id``: content-addressed snapshot hash.
    - ``metadata``: extensible annotation dict (tempo_bpm, etc.).
    - ``tags``: list of music-semantic tag strings attached to this commit.
    """

    commit_id: str
    short_id: str
    branch: str
    parent_commit_id: Optional[str]
    parent2_commit_id: Optional[str]
    message: str
    author: str
    committed_at: str
    snapshot_id: str
    metadata: dict[str, object]
    tags: list[str]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dict for this commit node."""
        return {
            "commit_id": self.commit_id,
            "short_id": self.short_id,
            "branch": self.branch,
            "parent_commit_id": self.parent_commit_id,
            "parent2_commit_id": self.parent2_commit_id,
            "message": self.message,
            "author": self.author,
            "committed_at": self.committed_at,
            "snapshot_id": self.snapshot_id,
            "metadata": self.metadata,
            "tags": self.tags,
        }


@dataclass(frozen=True)
class MuseInspectResult:
    """Full serialized commit graph for a Muse repository.

    Returned by :func:`build_inspect_result` and rendered by the three
    format functions.

    - ``repo_id``: UUID identifying the local repository.
    - ``current_branch``: the branch HEAD currently points to.
    - ``branches``: mapping of branch names to their HEAD commit ID.
    - ``commits``: all graph nodes reachable from the traversed heads,
      newest-first.
    """

    repo_id: str
    current_branch: str
    branches: dict[str, str]
    commits: list[MuseInspectCommit]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dict of the full graph."""
        return {
            "repo_id": self.repo_id,
            "current_branch": self.current_branch,
            "branches": self.branches,
            "commits": [c.to_dict() for c in self.commits],
        }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def _read_branches(muse_dir: pathlib.Path) -> dict[str, str]:
    """Scan ``.muse/refs/heads/`` and return a ``{branch: commit_id}`` dict.

    Branches with empty or missing ref files are excluded (no commits yet).
    """
    heads_dir = muse_dir / "refs" / "heads"
    branches: dict[str, str] = {}
    if not heads_dir.is_dir():
        return branches
    for ref_file in heads_dir.iterdir():
        commit_id = ref_file.read_text().strip()
        if commit_id:
            branches[ref_file.name] = commit_id
    return branches


async def _load_commit_tags(
    session: AsyncSession, commit_ids: list[str]
) -> dict[str, list[str]]:
    """Bulk-load tag strings for a set of commit IDs.

    Returns a ``{commit_id: [tag, ...]}`` mapping; commits without tags map
    to an empty list.  A single query is issued regardless of graph size.
    """
    if not commit_ids:
        return {}
    result = await session.execute(
        select(MuseCliTag).where(MuseCliTag.commit_id.in_(commit_ids))
    )
    tags_by_commit: dict[str, list[str]] = {cid: [] for cid in commit_ids}
    for tag_row in result.scalars().all():
        tags_by_commit.setdefault(tag_row.commit_id, []).append(tag_row.tag)
    return tags_by_commit


async def _walk_from(
    session: AsyncSession,
    start_commit_id: str,
    depth: Optional[int],
    visited: set[str],
) -> list[MuseCliCommit]:
    """Walk the parent chain from *start_commit_id*, newest-first.

    Stops when the chain is exhausted, *depth* is reached, or a node has
    already been visited (avoids re-traversing shared history when
    ``--branches`` combines multiple heads).

    Args:
        session:         Async SQLAlchemy session.
        start_commit_id: SHA of the first commit to visit.
        depth:           Maximum number of commits to return (``None`` = unlimited).
        visited:         Mutable set of already-visited commit IDs.  Updated
                         in-place so sibling traversals share state.

    Returns:
        Ordered list of :class:`MuseCliCommit` rows, newest-first.
    """
    rows: list[MuseCliCommit] = []
    current_id: Optional[str] = start_commit_id
    while current_id and current_id not in visited:
        if depth is not None and len(rows) >= depth:
            break
        row = await session.get(MuseCliCommit, current_id)
        if row is None:
            logger.warning("⚠️ muse inspect: commit %s not found — chain broken", current_id[:8])
            break
        visited.add(current_id)
        rows.append(row)
        current_id = row.parent_commit_id
    return rows


async def build_inspect_result(
    session: AsyncSession,
    root: pathlib.Path,
    *,
    ref: Optional[str] = None,
    depth: Optional[int] = None,
    include_branches: bool = False,
) -> MuseInspectResult:
    """Build the full :class:`MuseInspectResult` for a Muse repository.

    This is the primary entry point for ``muse inspect``.  It reads the
    repository state from the ``.muse/`` directory, resolves starting commit
    IDs, walks the graph, and returns a fully typed result.

    Args:
        session:          Async SQLAlchemy session (read-only operations only).
        root:             Repository root path (contains ``.muse/``).
        ref:              Optional starting commit reference.  Accepts a full
                          or abbreviated SHA, a branch name, or ``None``/
                          ``"HEAD"`` (default: HEAD of current branch).
        depth:            Maximum commits per branch traversal (``None`` =
                          unlimited).
        include_branches: When ``True``, traverse all branches and merge
                          their reachable commits into the output.  When
                          ``False``, only the current branch (or *ref*) is
                          traversed.

    Returns:
        :class:`MuseInspectResult` with all graph nodes and branch pointers.

    Raises:
        ValueError: When *ref* cannot be resolved to a commit.
        FileNotFoundError: When ``.muse/`` or ``repo.json`` are missing.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    current_branch = head_ref.rsplit("/", 1)[-1]          # "main"

    all_branches = _read_branches(muse_dir)

    # Determine the set of starting commit IDs to traverse.
    start_ids: list[str] = []

    if ref is not None and ref.upper() != "HEAD":
        # Resolve *ref*: branch name first, then exact SHA, then prefix.
        if ref in all_branches:
            start_ids.append(all_branches[ref])
        else:
            # Try exact or prefix match in DB.
            exact = await session.get(MuseCliCommit, ref)
            if exact is not None:
                start_ids.append(exact.commit_id)
            else:
                from sqlalchemy.future import select as sa_select
                result = await session.execute(
                    sa_select(MuseCliCommit).where(
                        MuseCliCommit.repo_id == repo_id,
                        MuseCliCommit.commit_id.startswith(ref),
                    )
                )
                first = result.scalars().first()
                if first is None:
                    raise ValueError(f"Cannot resolve ref {ref!r} to a commit.")
                start_ids.append(first.commit_id)
    else:
        # Default: HEAD of current branch.
        head_commit_id = all_branches.get(current_branch, "")
        if head_commit_id:
            start_ids.append(head_commit_id)

    if include_branches:
        for branch_commit_id in all_branches.values():
            if branch_commit_id not in start_ids:
                start_ids.append(branch_commit_id)

    # Walk the graph.
    visited: set[str] = set()
    all_rows: list[MuseCliCommit] = []
    for start_id in start_ids:
        rows = await _walk_from(session, start_id, depth, visited)
        all_rows.extend(rows)

    # Bulk-load tags.
    row_ids = [r.commit_id for r in all_rows]
    tags_by_commit = await _load_commit_tags(session, row_ids)

    # Build typed result nodes.
    commits = [
        MuseInspectCommit(
            commit_id=row.commit_id,
            short_id=row.commit_id[:8],
            branch=row.branch,
            parent_commit_id=row.parent_commit_id,
            parent2_commit_id=row.parent2_commit_id,
            message=row.message,
            author=row.author,
            committed_at=row.committed_at.isoformat(),
            snapshot_id=row.snapshot_id,
            metadata=dict(row.commit_metadata) if row.commit_metadata else {},
            tags=tags_by_commit.get(row.commit_id, []),
        )
        for row in all_rows
    ]

    logger.info(
        "✅ muse inspect: %d commit(s), %d branch(es) (repo=%s)",
        len(commits),
        len(all_branches),
        repo_id[:8],
    )
    return MuseInspectResult(
        repo_id=repo_id,
        current_branch=current_branch,
        branches=all_branches,
        commits=commits,
    )


# ---------------------------------------------------------------------------
# Format renderers
# ---------------------------------------------------------------------------


def render_json(result: MuseInspectResult, indent: int = 2) -> str:
    """Serialize *result* to a JSON string.

    The JSON shape matches the format specified in issue #98:
    ``repo_id``, ``current_branch``, ``branches``, and ``commits`` array.

    Args:
        result: The inspect result to serialize.
        indent: JSON indentation level (default 2).

    Returns:
        Formatted JSON string.
    """
    return json.dumps(result.to_dict(), indent=indent, default=str)


def render_dot(result: MuseInspectResult) -> str:
    """Serialize *result* to a Graphviz DOT directed graph.

    Each commit becomes a labelled node.  Parent edges point from child
    to parent (matching git's convention).  Branch refs appear as bold
    rectangular nodes pointing to their HEAD commit.

    Args:
        result: The inspect result to serialize.

    Returns:
        DOT source string, suitable for piping to ``dot -Tsvg``.
    """
    lines: list[str] = ["digraph muse_graph {", '  rankdir="LR";', '  node [shape=ellipse];', ""]

    for commit in result.commits:
        label = f"{commit.short_id}\\n{commit.message[:40]}"
        if commit.message and len(commit.message) > 40:
            label += "…"
        lines.append(f'  "{commit.commit_id}" [label="{label}"];')

    lines.append("")

    for commit in result.commits:
        if commit.parent_commit_id:
            lines.append(f'  "{commit.commit_id}" -> "{commit.parent_commit_id}";')
        if commit.parent2_commit_id:
            lines.append(
                f'  "{commit.commit_id}" -> "{commit.parent2_commit_id}" [style=dashed];'
            )

    lines.append("")
    lines.append("  // Branch pointers")
    lines.append('  node [shape=rectangle style=bold];')
    for branch, head_id in result.branches.items():
        safe_branch = branch.replace("/", "_").replace("-", "_")
        arrow = " -> " if head_id else ""
        if head_id:
            lines.append(f'  "branch_{safe_branch}" [label="{branch}"];')
            lines.append(f'  "branch_{safe_branch}" -> "{head_id}";')

    lines.append("}")
    return "\n".join(lines)


def render_mermaid(result: MuseInspectResult) -> str:
    """Serialize *result* to a Mermaid.js graph definition.

    Produces a left-to-right ``graph LR`` block.  Commit nodes are labelled
    with their short ID and truncated message.  Branch refs appear as
    rectangular nodes pointing to their HEAD commit.

    Args:
        result: The inspect result to serialize.

    Returns:
        Mermaid source string, suitable for embedding in GitHub markdown
        inside a ``mermaid`` fenced code block.
    """
    lines: list[str] = ["graph LR"]

    for commit in result.commits:
        msg = commit.message[:35]
        if len(commit.message) > 35:
            msg += "…"
        safe_msg = msg.replace('"', "'")
        lines.append(f'  {commit.commit_id[:8]}["{commit.short_id}: {safe_msg}"]')

    for commit in result.commits:
        if commit.parent_commit_id:
            lines.append(f"  {commit.commit_id[:8]} --> {commit.parent_commit_id[:8]}")
        if commit.parent2_commit_id:
            lines.append(
                f"  {commit.commit_id[:8]} -.-> {commit.parent2_commit_id[:8]}"
            )

    for branch, head_id in result.branches.items():
        if head_id:
            safe_branch = branch.replace("/", "_").replace("-", "_")
            lines.append(f'  {safe_branch}["{branch}"]')
            lines.append(f"  {safe_branch} --> {head_id[:8]}")

    return "\n".join(lines)
