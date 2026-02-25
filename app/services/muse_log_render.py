"""Muse Log Renderer — ``git log --graph`` style ASCII visualization.

Takes a ``MuseLogGraph`` and produces:
1. ASCII graph with branch/merge lines
2. Pretty-printed JSON
3. Summary table

Pure rendering — no I/O, no DB, no mutations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.services.muse_log_graph import MuseLogGraph, MuseLogNode


def render_ascii_graph(graph: MuseLogGraph) -> str:
    """Render a ``git log --graph --oneline`` style ASCII visualization.

    Processes nodes newest-first. Each active "column" tracks a
    variation_id we expect to encounter next (following parent links).
    Merges create forks; convergence collapses columns.
    """
    if not graph.nodes:
        return "(empty graph)"

    nodes = list(reversed(graph.nodes))
    lines: list[str] = []
    columns: list[str | None] = []

    for node in nodes:
        vid = node.variation_id

        col = _index_of(columns, vid)
        if col is None:
            col = len(columns)
            columns.append(vid)

        n_cols = len(columns)
        short = vid[:8]
        head = " (HEAD)" if node.is_head else ""
        intent = node.intent or ""
        label = f"{short} {intent}{head}"

        is_merge = node.parent2 is not None

        # Draw the commit line
        parts = _col_chars(columns, n_cols, active=col)
        if is_merge:
            lines.append(" ".join(parts) + f"   {label}")
        else:
            lines.append(" ".join(parts) + f" {label}")

        # Handle parent wiring
        if is_merge:
            # Primary parent stays in this column
            columns[col] = node.parent

            p2 = node.parent2
            p2_col = _index_of(columns, p2)

            if p2_col is not None and p2_col != col:
                # Parent2 already tracked — draw convergence and collapse
                lo, hi = min(col, p2_col), max(col, p2_col)
                merge_parts: list[str] = []
                for i in range(n_cols):
                    if i == lo:
                        merge_parts.append("|")
                    elif i == hi:
                        merge_parts.append("/")
                    elif columns[i] is not None:
                        merge_parts.append("|")
                    else:
                        merge_parts.append(" ")
                lines.append(" ".join(merge_parts))
                columns[hi] = None
            else:
                # Parent2 not yet tracked — open a new column
                columns.append(p2)
                fork_parts: list[str] = []
                for i in range(len(columns)):
                    if i == col:
                        fork_parts.append("|")
                    elif i == len(columns) - 1:
                        fork_parts.append("\\")
                    elif columns[i] is not None:
                        fork_parts.append("|")
                    else:
                        fork_parts.append(" ")
                lines.append(" ".join(fork_parts))
        else:
            # Simple linear commit — track parent
            columns[col] = node.parent

        # Draw convergence lines when multiple columns point to the same parent,
        # then collapse the duplicate columns.
        _draw_and_collapse_duplicates(columns, lines)

        # Trim trailing dead columns
        while columns and columns[-1] is None:
            columns.pop()

    return "\n".join(lines)


def render_json(graph: MuseLogGraph) -> str:
    """Pretty-print the MuseLogGraph JSON."""
    return json.dumps(graph.to_dict(), indent=2, default=str)


def render_summary_table(
    graph: MuseLogGraph,
    *,
    checkouts_executed: int = 0,
    drift_blocks: int = 0,
    conflict_merges: int = 0,
    forced_ops: int = 0,
) -> str:
    """Render a summary statistics table."""
    total_commits = len(graph.nodes)
    merges = sum(1 for n in graph.nodes if n.parent2 is not None)

    child_set: set[str] = set()
    for n in graph.nodes:
        if n.parent:
            child_set.add(n.parent)
        if n.parent2:
            child_set.add(n.parent2)
    leaf_nodes = [n for n in graph.nodes if n.variation_id not in child_set]
    branch_heads = len(leaf_nodes)

    rows = [
        ("Commits", str(total_commits)),
        ("Merges", str(merges)),
        ("Branch heads", str(branch_heads)),
        ("Conflict merges attempted", str(conflict_merges)),
        ("Checkouts executed", str(checkouts_executed)),
        ("Drift blocks", str(drift_blocks)),
        ("Forced operations", str(forced_ops)),
    ]
    max_label = max(len(r[0]) for r in rows)
    lines = ["┌" + "─" * (max_label + 2) + "┬" + "──────┐"]
    for label, value in rows:
        lines.append(f"│ {label:<{max_label}} │ {value:>4} │")
    lines.append("└" + "─" * (max_label + 2) + "┴" + "──────┘")
    return "\n".join(lines)


# ── Private helpers ───────────────────────────────────────────────────────


def _index_of(columns: list[str | None], vid: str | None) -> int | None:
    if vid is None:
        return None
    for i, c in enumerate(columns):
        if c == vid:
            return i
    return None


def _col_chars(columns: list[str | None], n: int, active: int) -> list[str]:
    parts: list[str] = []
    for i in range(n):
        if i == active:
            parts.append("*")
        elif columns[i] is not None:
            parts.append("|")
        else:
            parts.append(" ")
    return parts


def _draw_and_collapse_duplicates(
    columns: list[str | None], lines: list[str],
) -> None:
    """When multiple columns track the same parent, draw ``/`` convergence
    lines and collapse the rightmost duplicates."""
    changed = True
    while changed:
        changed = False
        seen: dict[str, int] = {}
        for i, c in enumerate(columns):
            if c is None:
                continue
            if c in seen:
                keep, remove = seen[c], i
                lo, hi = min(keep, remove), max(keep, remove)
                n = len(columns)
                parts: list[str] = []
                for j in range(n):
                    if j == lo:
                        parts.append("|")
                    elif j == hi:
                        parts.append("/")
                    elif columns[j] is not None:
                        parts.append("|")
                    else:
                        parts.append(" ")
                lines.append(" ".join(parts))
                columns[hi] = None
                # Trim trailing Nones immediately so subsequent
                # iterations see a clean column list.
                while columns and columns[-1] is None:
                    columns.pop()
                changed = True
                break
            else:
                seen[c] = i
