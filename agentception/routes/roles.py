"""Role file reader/writer API for the Role Studio editor (AC-301/303).

Exposes all managed ``.cursor/roles/*.md`` and ``.cursor/PARALLEL_*.md`` files
through a REST API so the Role Studio UI (AC-302/303) can list, read, update,
diff, commit, and inspect git history for each file without direct filesystem access.

Managed files are defined in ``_MANAGED_FILES`` — a hardcoded allowlist that
prevents arbitrary writes to the repository. Slugs are the dict keys; paths
are relative to ``settings.repo_dir``.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agentception.config import settings
from agentception.intelligence.role_versions import (
    read_role_versions,
    record_version_bump,
)
from agentception.models import (
    RoleCommitRequest,
    RoleCommitResponse,
    RoleContent,
    RoleDiffRequest,
    RoleDiffResponse,
    RoleMeta,
    RoleUpdateRequest,
    RoleUpdateResponse,
    RoleVersionEntry,
    RoleVersionInfo,
    RoleVersionsResponse,
)

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
async def get_role(slug: str) -> RoleContent:
    """Return the full file content and metadata for a managed slug.

    Raises HTTP 404 when the slug is not in the managed allowlist or the file
    does not exist on disk.
    """
    rel_path = _resolve_slug(slug)
    meta = await _build_meta(slug, rel_path)
    abs_path = settings.repo_dir / rel_path
    content = abs_path.read_text(encoding="utf-8")
    return RoleContent(slug=slug, content=content, meta=meta)


@router.put("/{slug}", summary="Write new content to a managed role file")
async def update_role(slug: str, body: RoleUpdateRequest) -> RoleUpdateResponse:
    """Overwrite a managed file with new content and return a diff vs HEAD.

    Does NOT auto-commit — the caller is responsible for committing the change
    (or discarding it with ``git restore``). The returned ``diff`` is the output
    of ``git diff HEAD -- <path>`` immediately after writing; an empty string
    means the content was identical to the committed version.

    Raises HTTP 404 when the slug is not in the managed allowlist.
    """
    rel_path = _resolve_slug(slug)
    abs_path = settings.repo_dir / rel_path
    abs_path.write_text(body.content, encoding="utf-8")
    logger.info("✅ Wrote %d bytes to %s", len(body.content), rel_path)

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(settings.repo_dir),
        "diff", "HEAD", "--", rel_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    diff = stdout.decode()

    meta = await _build_meta(slug, rel_path)
    return RoleUpdateResponse(slug=slug, diff=diff, meta=meta)


@router.get("/{slug}/history", summary="Return git commit history for a managed role file")
async def role_history(slug: str) -> list[dict[str, str]]:
    """Return the last 20 git commits that touched the managed file.

    Each entry has ``sha``, ``date`` (ISO-8601), and ``subject``.
    Returns an empty list when the file has no commits (e.g. brand-new file).
    Raises HTTP 404 when the slug is not in the managed allowlist.
    """
    rel_path = _resolve_slug(slug)
    return await _git_log_recent(settings.repo_dir, rel_path)


@router.post("/{slug}/diff", summary="Preview a unified diff of proposed content vs HEAD")
async def role_diff(slug: str, body: RoleDiffRequest) -> RoleDiffResponse:
    """Return a unified diff comparing ``body.content`` against HEAD without writing the file.

    Accepts a POST body so that large managed files (e.g. PARALLEL_PR_REVIEW.md)
    do not exceed Nginx's URI length limit.  Writes ``body.content`` to a temp
    file, then runs ``git diff --no-index`` between the committed file and the
    temp file so the user can review changes before saving.  An empty ``diff``
    string means the proposed content is identical to the committed version.
    Raises HTTP 404 for unknown slugs.
    """
    rel_path = _resolve_slug(slug)
    abs_path = settings.repo_dir / rel_path

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"Managed file not found on disk: {rel_path}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(body.content)
        tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(settings.repo_dir),
            "diff", "--no-index", "--unified=3",
            str(abs_path), tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        diff = stdout.decode()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return RoleDiffResponse(slug=slug, diff=diff)


@router.post("/{slug}/commit", summary="Write content and create a git commit for a managed role file")
async def commit_role(slug: str, body: RoleCommitRequest) -> RoleCommitResponse:
    """Write ``body.content`` to the managed file, stage it, and create a git commit.

    The commit message is ``role(agentception): update {slug}``.  Returns the
    resulting commit SHA so the UI can confirm the commit was created.
    Raises HTTP 404 for unknown slugs or when the file does not exist on disk.
    Raises HTTP 500 when ``git commit`` fails (e.g. nothing to commit because
    the content is identical to HEAD).
    """
    rel_path = _resolve_slug(slug)
    abs_path = settings.repo_dir / rel_path

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"Managed file not found on disk: {rel_path}")

    abs_path.write_text(body.content, encoding="utf-8")
    logger.info("✅ Wrote %d bytes to %s for commit", len(body.content), rel_path)

    add_proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(settings.repo_dir),
        "add", rel_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, add_err = await add_proc.communicate()
    if add_proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"git add failed: {add_err.decode().strip()}",
        )

    commit_message = f"role(agentception): update {slug}"
    commit_proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(settings.repo_dir),
        "commit", "-m", commit_message,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    commit_out, commit_err = await commit_proc.communicate()
    if commit_proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"git commit failed: {commit_err.decode().strip() or commit_out.decode().strip()}",
        )

    sha_proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(settings.repo_dir),
        "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    sha_out, _ = await sha_proc.communicate()
    commit_sha = sha_out.decode().strip()

    logger.info("✅ Committed %s → %s", rel_path, commit_sha[:8])

    # Record the new commit SHA in role-versions.json so callers can correlate
    # which role version governed agents in any given batch (AC-503).
    await record_version_bump(slug, commit_sha)

    return RoleCommitResponse(slug=slug, commit_sha=commit_sha, message=commit_message)


@router.get("/{slug}/versions", summary="Return role version history for a managed slug (AC-503)")
async def role_versions_api(slug: str) -> RoleVersionsResponse:
    """Return structured version history for ``slug`` from role-versions.json.

    The history is chronologically ordered (oldest first).  Each entry records
    the git SHA, version label (v1, v2, …), and UNIX timestamp of the commit.
    Returns an empty history list when no commits have been recorded yet —
    this is not an error; it simply means the role has not been committed via
    the Role Studio commit endpoint.

    Raises HTTP 404 when ``slug`` is not in the managed allowlist.
    """
    _resolve_slug(slug)  # raises 404 for unknown slugs

    data = await read_role_versions()
    versions_map: dict[str, object] = data.get("versions", {})  # type: ignore[assignment]
    if not isinstance(versions_map, dict):
        versions_map = {}

    raw_entry = versions_map.get(slug)
    if isinstance(raw_entry, dict):
        current = str(raw_entry.get("current", "v1"))
        raw_history: list[dict[str, object]] = raw_entry.get("history", [])  # type: ignore[assignment]
        if not isinstance(raw_history, list):
            raw_history = []
        history = []
        for h in raw_history:
            if not isinstance(h, dict):
                continue
            ts_raw = h.get("timestamp")
            ts = int(ts_raw) if isinstance(ts_raw, (int, float)) else 0
            history.append(
                RoleVersionEntry(
                    sha=str(h.get("sha", "")),
                    label=str(h.get("label", "")),
                    timestamp=ts,
                )
            )
    else:
        current = "v1"
        history = []

    return RoleVersionsResponse(
        slug=slug,
        versions=RoleVersionInfo(current=current, history=history),
    )
