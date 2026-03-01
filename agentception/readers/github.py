"""GitHub data reader for AgentCeption.

All GitHub data flows through this module via ``gh`` CLI subprocess calls.
Results are cached for ``settings.github_cache_seconds`` (default 10 s) to
avoid hitting the GitHub API rate-limit and keep the dashboard UI snappy.

Write operations (``close_pr``, ``clear_wip_label``) always invalidate the
entire cache so subsequent reads reflect the new state without waiting for TTL
expiry.

Usage::

    from agentception.readers.github import get_open_issues, get_active_label

    issues = await get_open_issues(label="agentception/0-scaffold")
    label  = await get_active_label()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import cast

from agentception.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal TTL cache
# ---------------------------------------------------------------------------
# Format: {cache_key: (result, expires_at_unix)}
# ``object`` is intentional — callers are responsible for knowing the shape of
# what they stored (each public function wraps a specific type).
_cache: dict[str, tuple[object, float]] = {}


def _cache_get(key: str) -> object | None:
    """Return cached value if it exists and has not expired, else None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    result, expires_at = entry
    if time.monotonic() > expires_at:
        del _cache[key]
        return None
    return result


def _cache_set(key: str, value: object) -> None:
    """Store *value* in the cache with a TTL of ``github_cache_seconds``."""
    expires_at = time.monotonic() + settings.github_cache_seconds
    _cache[key] = (value, expires_at)


def _cache_invalidate() -> None:
    """Clear the entire cache.

    Called after any write operation so the next read reflects current state
    rather than serving a stale response that was cached before the mutation.
    """
    _cache.clear()
    logger.debug("⚠️  GitHub cache invalidated after write operation")


# ---------------------------------------------------------------------------
# Low-level subprocess helper
# ---------------------------------------------------------------------------

async def gh_json(args: list[str], jq: str, cache_key: str) -> object:
    """Run ``gh`` with ``--json`` + ``--jq`` and cache the result.

    Parameters
    ----------
    args:
        Additional ``gh`` sub-command arguments (e.g. ``["issue", "list",
        "--repo", "cgcardona/maestro"]``).  Do **not** include ``--json`` or
        ``--jq`` — those are appended automatically.
    jq:
        A ``jq`` filter string passed verbatim to ``--jq``.  The ``gh`` CLI
        uses its own bundled ``jq`` — no host installation required.
    cache_key:
        Opaque string that identifies this particular query.  Use a value that
        captures all arguments that affect the result so distinct queries never
        share a cache entry.

    Returns
    -------
    object
        Parsed JSON (list, dict, str, int, …) — shape depends on the ``jq``
        filter.  Callers must narrow the type themselves.

    Raises
    ------
    RuntimeError
        When ``gh`` exits with a non-zero status.
    """
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("✅ GitHub cache hit: %s", cache_key)
        return cached

    cmd = ["gh"] + args + ["--jq", jq]
    logger.debug("⏱️  gh subprocess: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"gh command failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()!r}  cmd={cmd}"
        )

    raw = stdout.decode().strip()
    if not raw:
        result: object = []
    else:
        result = json.loads(raw)

    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------

async def get_open_issues(label: str | None = None) -> list[dict[str, object]]:
    """List open issues, optionally filtered by a single label.

    Returns each issue as a dict with at minimum: ``number``, ``title``,
    ``labels`` (list of label objects), and ``body``.

    Parameters
    ----------
    label:
        When provided, only issues carrying this label are returned.
    """
    repo = settings.gh_repo
    args = [
        "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--json", "number,title,labels,body",
    ]
    if label:
        args += ["--label", label]

    cache_key = f"get_open_issues:label={label}"
    result = await gh_json(args, ".", cache_key)
    return cast(list[dict[str, object]], result)


async def get_open_prs() -> list[dict[str, object]]:
    """List open pull requests targeting the ``dev`` branch.

    Returns each PR as a dict with at minimum: ``number``, ``title``,
    ``headRefName``, and ``labels``.
    """
    repo = settings.gh_repo
    args = [
        "pr", "list",
        "--repo", repo,
        "--base", "dev",
        "--state", "open",
        "--json", "number,title,headRefName,labels",
    ]
    result = await gh_json(args, ".", "get_open_prs")
    return cast(list[dict[str, object]], result)


async def get_wip_issues() -> list[dict[str, object]]:
    """Return issues currently labelled ``agent:wip``.

    An ``agent:wip`` label signals that a pipeline agent has claimed the
    issue.  The dashboard uses this to detect in-flight work.
    """
    return await get_open_issues(label="agent:wip")


async def get_active_label() -> str | None:
    """Find the lowest-numbered ``agentception/*`` label that has open issues.

    Labels follow the pattern ``agentception/<N>-<slug>``.  The «active»
    label is the one with the smallest ``<N>`` — i.e. the phase currently
    being worked on.

    Returns ``None`` when no open issues carry an ``agentception/*`` label.
    """
    repo = settings.gh_repo
    args = [
        "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--json", "labels",
    ]
    result = await gh_json(args, "[.[].labels[].name]", "get_active_label")
    all_label_names = cast(list[str], result)

    agentception_labels: set[str] = {
        name for name in all_label_names if name.startswith("agentception/")
    }
    if not agentception_labels:
        return None

    def _sort_key(name: str) -> int:
        """Extract the numeric prefix after the slash for ordering."""
        suffix = name.split("/", 1)[-1]          # e.g. "0-scaffold"
        prefix = suffix.split("-", 1)[0]          # e.g. "0"
        try:
            return int(prefix)
        except ValueError:
            return 999

    return min(agentception_labels, key=_sort_key)


async def get_issue_body(number: int) -> str:
    """Fetch the markdown body of a single issue.

    Used by the ticket analyser and DAG builder to parse dependency
    declarations (``Depends on #N``) and extract structured metadata.

    Parameters
    ----------
    number:
        GitHub issue number.
    """
    repo = settings.gh_repo
    args = [
        "issue", "view", str(number),
        "--repo", repo,
        "--json", "body",
    ]
    result = await gh_json(args, ".body", f"get_issue_body:{number}")
    return cast(str, result)


# ---------------------------------------------------------------------------
# Write operations (always invalidate cache)
# ---------------------------------------------------------------------------

async def close_pr(number: int, comment: str) -> None:
    """Close a pull request and post a comment explaining the closure.

    Invalidates the cache so subsequent reads reflect the updated PR state.

    Parameters
    ----------
    number:
        GitHub PR number.
    comment:
        Comment body to post before closing (appears in the PR timeline).
    """
    repo = settings.gh_repo

    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "close", str(number),
        "--repo", repo,
        "--comment", comment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"gh pr close failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()!r}"
        )

    logger.info("✅ PR #%d closed with comment", number)
    _cache_invalidate()


async def clear_wip_label(issue_number: int) -> None:
    """Remove the ``agent:wip`` label from an issue.

    Called by the control plane after an agent completes its task so the
    issue no longer shows up in ``get_wip_issues()``.

    Invalidates the cache so subsequent reads see the updated label set.

    Parameters
    ----------
    issue_number:
        GitHub issue number to remove ``agent:wip`` from.
    """
    repo = settings.gh_repo

    proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "edit", str(issue_number),
        "--repo", repo,
        "--remove-label", "agent:wip",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"gh issue edit failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()!r}"
        )

    logger.info("✅ Removed agent:wip from issue #%d", issue_number)
    _cache_invalidate()
