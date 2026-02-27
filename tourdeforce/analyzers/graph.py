"""GraphAnalyzer — MUSE commit DAG metrics and ASCII rendering."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GraphAnalyzer:
    """Analyzes MUSE commit DAG structure."""

    def __init__(self, graph_data: dict[str, Any]) -> None:
        self._data = graph_data
        self._nodes = graph_data.get("nodes", [])

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def head(self) -> str | None:
        return self._data.get("head")

    @property
    def merge_count(self) -> int:
        return sum(1 for n in self._nodes if n.get("parent2"))

    @property
    def branch_head_count(self) -> int:
        child_parents: set[str] = set()
        for n in self._nodes:
            if n.get("parent"):
                child_parents.add(n["parent"])
            if n.get("parent2"):
                child_parents.add(n["parent2"])
        return sum(1 for n in self._nodes if n["id"] not in child_parents)

    @property
    def max_depth(self) -> int:
        """Longest path from any root to any leaf."""
        by_id = {n["id"]: n for n in self._nodes}
        depth_cache: dict[str, int] = {}

        def depth(nid: str) -> int:
            if nid in depth_cache:
                return depth_cache[nid]
            node = by_id.get(nid)
            if not node:
                return 0
            p1 = depth(node["parent"]) if node.get("parent") and node["parent"] in by_id else 0
            p2 = depth(node["parent2"]) if node.get("parent2") and node["parent2"] in by_id else 0
            d = max(p1, p2) + 1
            depth_cache[nid] = d
            return d

        return max((depth(n["id"]) for n in self._nodes), default=0)

    def render_ascii(self) -> str:
        """Simple ASCII graph rendering."""
        if not self._nodes:
            return "(empty graph)"

        lines: list[str] = []
        for node in self._nodes:
            short = node["id"][:8]
            head_marker = " (HEAD)" if node.get("isHead") else ""
            intent = node.get("intent", "")
            parent_info = ""
            if node.get("parent2"):
                parent_info = f" <- merge({node['parent'][:8]}, {node['parent2'][:8]})"
            elif node.get("parent"):
                parent_info = f" <- {node['parent'][:8]}"

            lines.append(f"  * {short} {intent}{head_marker}{parent_info}")

        return "\n".join(lines)

    def render_mermaid(self) -> str:
        """Render the commit DAG as a Mermaid graph definition."""
        if not self._nodes:
            return "graph TD\n  empty[No commits]"

        lines = ["graph TD"]
        for node in self._nodes:
            nid = f"n{node['id'][:8]}"
            intent = node.get("intent", "?").replace('"', "'")
            short = node["id"][:8]

            if node.get("parent2"):
                label = f"merge {short}"
            elif node.get("isHead"):
                label = f"{intent} {short} HEAD"
            else:
                label = f"{intent} {short}"

            lines.append(f'    {nid}["{label}"]')

        for node in self._nodes:
            nid = f"n{node['id'][:8]}"
            if node.get("parent"):
                pid = f"n{node['parent'][:8]}"
                lines.append(f"    {pid} --> {nid}")
            if node.get("parent2"):
                p2id = f"n{node['parent2'][:8]}"
                lines.append(f"    {p2id} --> {nid}")

        return "\n".join(lines)

    def to_metrics(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "head": self.head,
            "merge_count": self.merge_count,
            "branch_head_count": self.branch_head_count,
            "max_depth": self.max_depth,
        }

    def export_graph_json(self, path: Path) -> None:
        """Export graph as nodes/edges JSON for visualization."""
        nodes = [
            {"id": n["id"][:8], "label": n.get("intent", ""), "isHead": n.get("isHead", False)}
            for n in self._nodes
        ]
        edges = []
        for n in self._nodes:
            if n.get("parent"):
                edges.append({"source": n["parent"][:8], "target": n["id"][:8], "type": "parent"})
            if n.get("parent2"):
                edges.append({"source": n["parent2"][:8], "target": n["id"][:8], "type": "merge"})

        path.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=2))

    def export_ascii(self, path: Path) -> None:
        """Export ASCII graph to file."""
        path.write_text(self.render_ascii())
