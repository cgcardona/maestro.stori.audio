"""Transcript reader for AgentCeption (AC-003).

Walks ``~/.cursor/projects/.../agent-transcripts/`` to build the parent/child
``AgentNode`` tree. Hierarchy is derived from the ``subagents/`` folder
structure that Cursor creates when an agent spawns sub-agents.

Public API:
    find_transcript_root()        → locate the transcripts directory
    build_agent_tree()            → parse one root UUID into an AgentNode tree
    read_transcript_messages()    → parse a JSONL file into [{role, text}]
    infer_role_from_messages()    → keyword heuristic → role string
    infer_status_from_messages()  → PR-URL heuristic → AgentStatus
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agentception.config import settings
from agentception.models import AgentNode, AgentStatus

logger = logging.getLogger(__name__)

# Ordered priority table: first matching keyword wins.
_ROLE_KEYWORDS: list[tuple[str, str]] = [
    ("CTO", "cto"),
    ("Engineering VP", "engineering-vp"),
    ("Engineering Manager", "engineering-manager"),
    ("QA VP", "qa-vp"),
    ("muse-specialist", "muse-specialist"),
    ("database-architect", "database-architect"),
    ("python-developer", "python-developer"),
    ("pr-reviewer", "pr-reviewer"),
]

_PR_URL_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/\d+")


async def find_transcript_root() -> Path | None:
    """Find the agent-transcripts/ directory for the current Cursor project.

    Strategy: derive the expected project-directory name from ``settings.repo_dir``
    by stripping the leading slash and replacing ``/`` with ``-``. If that exact
    directory exists under ``cursor_projects_dir``, return it. Otherwise fall back
    to the most-recently-modified ``agent-transcripts/`` found in any project
    directory — useful when the repo is opened under an unexpected path.

    Returns ``None`` if no transcript directory can be found at all.
    """
    base = settings.cursor_projects_dir
    if not base.exists():
        logger.warning("⚠️  cursor_projects_dir does not exist: %s", base)
        return None

    # Derive expected project dir name: /Users/gabriel/dev/…/maestro
    #   → Users-gabriel-dev-…-maestro
    repo_slug = str(settings.repo_dir).lstrip("/").replace("/", "-")
    exact = base / repo_slug / "agent-transcripts"
    if exact.exists():
        return exact

    # Fall back to the most recently modified agent-transcripts/ anywhere.
    best_mtime: float = -1.0
    best_path: Path | None = None
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts = project_dir / "agent-transcripts"
        if not transcripts.exists():
            continue
        mtime = transcripts.stat().st_mtime
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = transcripts

    if best_path is None:
        logger.warning("⚠️  No agent-transcripts directory found under %s", base)
    return best_path


async def read_transcript_messages(jsonl_path: Path) -> list[dict[str, str]]:
    """Parse a Cursor JSONL transcript file into a flat list of ``{role, text}`` dicts.

    Each line is a JSON object with the shape::

        {"role": "user"|"assistant",
         "message": {"content": [{"type": "text", "text": "..."}]}}

    Only ``type == "text"`` content blocks are included. Lines that fail JSON
    parsing are silently skipped to tolerate partial writes. Returns ``[]`` for
    an empty or missing file.
    """
    if not jsonl_path.exists():
        return []

    try:
        raw = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("⚠️  Cannot read transcript %s: %s", jsonl_path, exc)
        return []

    messages: list[dict[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        role: str = entry.get("role", "")
        content_parts: list[dict[str, str]] = (
            entry.get("message", {}).get("content", [])
        )
        for part in content_parts:
            if isinstance(part, dict) and part.get("type") == "text":
                messages.append({"role": role, "text": part.get("text", "")})

    return messages


def infer_role_from_messages(messages: list[dict[str, str]]) -> str:
    """Derive a role string from the first assistant message in the transcript.

    Scans ``_ROLE_KEYWORDS`` in order; returns the role for the first keyword
    found in the text. Returns ``"unknown"`` when no keyword matches.

    The first assistant message is the one most likely to contain a system
    prompt or role-declaration preamble, so only it is examined.
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        text = msg.get("text", "")
        for keyword, role in _ROLE_KEYWORDS:
            if keyword in text:
                return role
        # Only inspect the first assistant message.
        break
    return "unknown"


def infer_status_from_messages(messages: list[dict[str, str]]) -> AgentStatus:
    """Derive lifecycle status from the last assistant message.

    Heuristic: if the most-recent assistant message contains a GitHub pull-request
    URL (``github.com/<owner>/<repo>/pull/<n>``), the agent successfully opened
    a PR and is considered ``DONE``. Any other ending state maps to ``UNKNOWN``.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            if _PR_URL_RE.search(msg.get("text", "")):
                return AgentStatus.DONE
            break
    return AgentStatus.UNKNOWN


async def build_agent_tree(
    root_uuid: str,
    transcripts_dir: Path,
) -> AgentNode | None:
    """Recursively build an ``AgentNode`` tree rooted at ``root_uuid``.

    Cursor stores transcripts in one of two layouts:

    * **Leaf agent** (no sub-agents spawned)::

        <transcripts_dir>/<root_uuid>/<root_uuid>.jsonl

    * **Coordinator agent** (spawned sub-agents)::

        <transcripts_dir>/<root_uuid>/subagents/<child-uuid>.jsonl
        ...

      The coordinator may or may not have its own ``<root_uuid>.jsonl``;
      children are always leaf ``.jsonl`` files (the structure does not
      nest deeper in practice).

    Returns ``None`` if the root directory does not exist.
    """
    root_dir = transcripts_dir / root_uuid
    if not root_dir.exists():
        logger.warning("⚠️  Transcript directory not found: %s", root_dir)
        return None

    # Own transcript — may be absent for pure-coordinator agents.
    parent_jsonl = root_dir / f"{root_uuid}.jsonl"
    messages = await read_transcript_messages(parent_jsonl)

    # Use directory mtime as fallback when the JSONL file is absent.
    mtime = (
        parent_jsonl.stat().st_mtime
        if parent_jsonl.exists()
        else root_dir.stat().st_mtime
    )

    role = infer_role_from_messages(messages)
    status = infer_status_from_messages(messages)

    # Build children from subagents/ if present.
    children: list[AgentNode] = []
    subagents_dir = root_dir / "subagents"
    if subagents_dir.is_dir():
        for child_jsonl in sorted(subagents_dir.glob("*.jsonl")):
            child_uuid = child_jsonl.stem
            child_messages = await read_transcript_messages(child_jsonl)
            child_role = infer_role_from_messages(child_messages)
            child_status = infer_status_from_messages(child_messages)
            children.append(
                AgentNode(
                    id=child_uuid,
                    role=child_role,
                    status=child_status,
                    message_count=len(child_messages),
                    last_activity_mtime=child_jsonl.stat().st_mtime,
                    transcript_path=str(child_jsonl),
                )
            )

    return AgentNode(
        id=root_uuid,
        role=role,
        status=status,
        message_count=len(messages),
        last_activity_mtime=mtime,
        transcript_path=str(parent_jsonl) if parent_jsonl.exists() else None,
        children=children,
    )
