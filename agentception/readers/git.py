"""Git repository data reader for AgentCeption.

Reads live git state (branches, worktrees, stash) from the mounted repo
at ``settings.repo_dir``.  All reads are subprocess calls; results are NOT
cached because git state changes frequently and the data is only fetched
on-demand (never every tick).

Public API:
    list_git_worktrees()  → all linked worktrees with branch + HEAD info
    list_git_branches()   → local branches with ahead/behind status
    list_git_stash()      → stash entries
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from agentception.config import settings

logger = logging.getLogger(__name__)

# Matches any branch created by AgentCeption: feat/issue-N or feat/brain-dump-*
_AGENT_BRANCH_RE = re.compile(r"^feat/(issue-\d+|brain-dump-.+)$")


async def _git(args: list[str]) -> str:
    """Run ``git -C <repo_dir> <args>`` and return stdout as a string.

    Returns an empty string on non-zero exit rather than raising, so callers
    can treat missing data as empty rather than an error.
    """
    repo = str(settings.repo_dir)
    cmd = ["git", "-C", repo] + args
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.debug("⚠️  git command failed: %s — %s", " ".join(cmd), stderr.decode().strip())
        return ""
    return stdout.decode().strip()


async def list_git_worktrees() -> list[dict[str, object]]:
    """Return all git worktrees (linked + main).

    Each dict has: ``path``, ``branch``, ``head_sha``, ``head_message``,
    ``is_main`` (bool), ``is_agent_branch`` (bool — matches feat/issue-N).
    """
    raw = await _git(["worktree", "list", "--porcelain"])
    worktrees: list[dict[str, object]] = []

    current: dict[str, object] = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):], "is_main": False, "is_agent_branch": False}
        elif line.startswith("HEAD "):
            current["head_sha"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            branch = line[len("branch "):]
            # Normalise refs/heads/feat/issue-731 → feat/issue-731
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/"):]
            current["branch"] = branch
            current["is_agent_branch"] = bool(_AGENT_BRANCH_RE.match(branch))
        elif line == "bare":
            current["bare"] = True

    if current:
        worktrees.append(current)

    # Mark the first entry as main worktree (git always lists main first).
    if worktrees:
        worktrees[0]["is_main"] = True

    # Fetch HEAD commit message for each worktree.
    for wt in worktrees:
        sha = str(wt.get("head_sha", ""))
        if sha:
            wt["head_message"] = await _git(["log", "-1", "--format=%s", sha])

    return worktrees


async def list_git_branches() -> list[dict[str, object]]:
    """Return local branches with ahead/behind counts relative to origin.

    Each dict has: ``name``, ``head_sha``, ``head_message``, ``ahead``,
    ``behind``, ``is_agent_branch`` (bool), ``is_current`` (bool).
    """
    # --format=%(refname:short) %(objectname:short) %(upstream:trackshort) %(HEAD)
    raw = await _git([
        "branch", "-v", "--format",
        "%(HEAD)|%(refname:short)|%(objectname:short)|%(subject)|%(upstream:trackshort)",
    ])

    branches: list[dict[str, object]] = []
    for line in raw.splitlines():
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        is_current_marker, name, sha, subject, track = parts
        is_current = is_current_marker.strip() == "*"

        # Parse ahead/behind from track shorthand like "[ahead 2]", "[behind 1]", "[gone]"
        ahead = 0
        behind = 0
        if "[ahead" in track:
            m = re.search(r"ahead\s+(\d+)", track)
            if m:
                ahead = int(m.group(1))
        if "behind" in track:
            m = re.search(r"behind\s+(\d+)", track)
            if m:
                behind = int(m.group(1))

        branches.append({
            "name": name.strip(),
            "head_sha": sha.strip(),
            "head_message": subject.strip(),
            "ahead": ahead,
            "behind": behind,
            "is_agent_branch": bool(_AGENT_BRANCH_RE.match(name.strip())),
            "is_current": is_current,
        })

    return branches


async def list_git_stash() -> list[dict[str, object]]:
    """Return stash entries.

    Each dict has: ``ref`` (stash@{N}), ``branch``, ``message``.
    """
    raw = await _git(["stash", "list", "--format=%gd|%gs"])
    entries: list[dict[str, object]] = []
    for line in raw.splitlines():
        parts = line.split("|", 1)
        ref = parts[0].strip() if parts else ""
        description = parts[1].strip() if len(parts) > 1 else ""

        # "WIP on <branch>: <hash> <msg>" or "On <branch>: <msg>"
        branch = ""
        m = re.match(r"(?:WIP on|On)\s+([^:]+):", description)
        if m:
            branch = m.group(1).strip()

        entries.append({"ref": ref, "branch": branch, "message": description})

    return entries
