"""Role file reader/writer API for the Role Studio editor (AC-301).

Exposes all managed ``.cursor/roles/*.md`` and ``.cursor/PARALLEL_*.md`` files
through a REST API so the Role Studio UI (AC-302/303) can list, read, update,
and inspect git history for each file without direct filesystem access.

Managed files are defined in ``_MANAGED_FILES`` — a hardcoded allowlist that
prevents arbitrary writes to the repository. Slugs are the dict keys; paths
are relative to ``settings.repo_dir``.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agentception.config import settings
from agentception.models import RoleMeta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/roles", tags=["roles"])

# Allowlist of managed files. Slug → relative path from repo root.
# Only files in this dict can be read or written through the API.
_MANAGED_FILES: dict[str, str] = {
    "cto": ".cursor/roles/cto.md",
    "engineering-manager": ".cursor/roles/engineering-manager.md",
    "qa-manager": ".cursor/roles/qa-manager.md",
    "python-developer": ".cursor/roles/python-developer.md",
    "database-architect": ".cursor/roles/database-architect.md",
    "pr-reviewer": ".cursor/roles/pr-reviewer.md",
    "PARALLEL_ISSUE_TO_PR": ".cursor/PARALLEL_ISSUE_TO_PR.md",
    "PARALLEL_PR_REVIEW": ".cursor/PARALLEL_PR_REVIEW.md",
    "AGENT_COMMAND_POLICY": ".cursor/AGENT_COMMAND_POLICY.md",
}


async def _git_log_one(repo_dir: Path, rel_path: str) -> tuple[str, str]:
    """Return (sha, subject) of the most recent commit touching ``rel_path``.

    Returns empty strings when the file has never been committed or the git
    command fails — callers must tolerate missing history gracefully.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_dir),
        "log", "-1", "--format=%H\t%s", "--", rel_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    line = stdout.decode().strip()
    if not line or "\t" not in line:
        return "", ""
    sha, _, subject = line.partition("\t")
    return sha.strip(), subject.strip()


async def _git_log_recent(repo_dir: Path, rel_path: str, n: int = 20) -> list[dict[str, str]]:
    """Return the last ``n`` commits touching ``rel_path`` as a list of dicts.

    Each dict has ``sha``, ``subject``, and ``date`` (ISO-8601 format).
    Returns an empty list when there are no commits for the file.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_dir),
        "log", f"-{n}", "--format=%H\t%ai\t%s", "--", rel_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    entries: list[dict[str, str]] = []
    for line in stdout.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        entries.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return entries


async def _build_meta(slug: str, rel_path: str) -> RoleMeta:
    """Build a ``RoleMeta`` for the given slug and relative path.

    Reads the file from ``settings.repo_dir`` and runs a scoped ``git log``
    to populate the last-commit fields. Raises ``HTTPException(404)`` when
    the file does not exist on disk.
    """
    abs_path: Path = settings.repo_dir / rel_path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"Managed file not found on disk: {rel_path}")

    content = abs_path.read_text(encoding="utf-8")
    line_count = len(content.splitlines())
    mtime = abs_path.stat().st_mtime

    sha, message = await _git_log_one(settings.repo_dir, rel_path)

    return RoleMeta(
        slug=slug,
        path=rel_path,
        line_count=line_count,
        mtime=mtime,
        last_commit_sha=sha,
        last_commit_message=message,
    )


def _resolve_slug(slug: str) -> str:
    """Return the relative path for a slug, or raise HTTP 404.

    Centralises the allowlist lookup so callers don't repeat the guard.
    """
    rel_path = _MANAGED_FILES.get(slug)
    if rel_path is None:
        raise HTTPException(status_code=404, detail=f"Unknown role slug: {slug!r}")
    return rel_path


@router.get("", summary="List all managed role and cursor files")
async def list_roles() -> list[RoleMeta]:
    """Return metadata for every file in the managed allowlist.

    Files that exist in the allowlist but are missing from disk are silently
    omitted so a missing optional file does not break the entire listing.
    Returns slugs in the order they appear in ``_MANAGED_FILES``.
    """
    results: list[RoleMeta] = []
    for slug, rel_path in _MANAGED_FILES.items():
        abs_path = settings.repo_dir / rel_path
        if not abs_path.exists():
            logger.warning("⚠️  Managed file missing from disk: %s", rel_path)
            continue
        try:
            meta = await _build_meta(slug, rel_path)
            results.append(meta)
        except HTTPException:
            pass
    return results


@router.get("/{slug}", summary="Get content and metadata for a single role file")
async def get_role(slug: str) -> dict[str, object]:
    """Return the full file content and metadata for a managed slug.

    Response shape::

        {
            "slug": "python-developer",
            "content": "...",
            "meta": { ...RoleMeta fields... }
        }

    Raises HTTP 404 when the slug is not in the managed allowlist or the file
    does not exist on disk.
    """
    rel_path = _resolve_slug(slug)
    meta = await _build_meta(slug, rel_path)
    abs_path = settings.repo_dir / rel_path
    content = abs_path.read_text(encoding="utf-8")
    return {"slug": slug, "content": content, "meta": meta.model_dump()}


@router.put("/{slug}", summary="Write new content to a managed role file")
async def update_role(slug: str, body: dict[str, str]) -> dict[str, object]:
    """Overwrite a managed file with new content and return a diff vs HEAD.

    Does NOT auto-commit — the caller is responsible for committing the change
    (or discarding it with ``git restore``). The returned ``diff`` is the output
    of ``git diff HEAD -- <path>`` immediately after writing; an empty string
    means the content was identical to the committed version.

    Request body::

        { "content": "<new file content>" }

    Raises HTTP 400 when ``content`` is missing from the request body.
    Raises HTTP 404 when the slug is not in the managed allowlist.
    """
    rel_path = _resolve_slug(slug)
    new_content = body.get("content")
    if new_content is None:
        raise HTTPException(status_code=400, detail="Request body must include 'content'")

    abs_path = settings.repo_dir / rel_path
    abs_path.write_text(new_content, encoding="utf-8")
    logger.info("✅ Wrote %d bytes to %s", len(new_content), rel_path)

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(settings.repo_dir),
        "diff", "HEAD", "--", rel_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    diff = stdout.decode()

    meta = await _build_meta(slug, rel_path)
    return {"slug": slug, "diff": diff, "meta": meta.model_dump()}


@router.get("/{slug}/history", summary="Return git commit history for a managed role file")
async def role_history(slug: str) -> list[dict[str, str]]:
    """Return the last 20 git commits that touched the managed file.

    Each entry has ``sha``, ``date`` (ISO-8601), and ``subject``.
    Returns an empty list when the file has no commits (e.g. brand-new file).
    Raises HTTP 404 when the slug is not in the managed allowlist.
    """
    rel_path = _resolve_slug(slug)
    return await _git_log_recent(settings.repo_dir, rel_path)
