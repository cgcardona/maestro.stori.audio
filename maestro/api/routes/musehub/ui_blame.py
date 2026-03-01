"""Muse Hub blame view page — per-note commit attribution browser.

Serves the blame UI page for a given MIDI file path at a commit ref.  Each note
event is annotated with the commit that last introduced or modified it, giving
musicians and AI agents a per-measure provenance view of the composition.

Endpoint:
  GET /musehub/ui/{owner}/{repo_slug}/blame/{ref}/{path:path}

Content negotiation (one URL, two audiences):
  HTML (default) — interactive blame view rendered via Jinja2.
  JSON (``Accept: application/json`` or ``?format=json``) — returns
  ``BlameResponse`` with the full list of ``BlameEntry`` items in camelCase,
  mirroring the ``/api/v1/musehub/repos/{repo_id}/blame/{ref}`` API contract.

Auth:
  No JWT required to receive the HTML shell.  The client-side JavaScript reads
  a token from ``localStorage`` and passes it as a Bearer header when calling the
  JSON API, matching all other Muse Hub UI pages.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from maestro.api.routes.musehub.blame import _build_blame_entries
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.models.musehub import BlameResponse
from maestro.services import musehub_repository
from pathlib import Path
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


async def _resolve_repo(owner: str, repo_slug: str, db: AsyncSession) -> tuple[str, str]:
    """Resolve owner + slug to (repo_id, base_url); raise 404 when missing.

    Returns the repo_id string and the canonical UI base URL so callers can
    unpack both in one line without repeating the lookup boilerplate.
    """
    from fastapi import HTTPException
    from fastapi import status as http_status

    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), f"/musehub/ui/{owner}/{repo_slug}"


@router.get(
    "/{owner}/{repo_slug}/blame/{ref}/{path:path}",
    summary="Muse Hub blame view — per-note commit attribution",
)
async def blame_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    track: str | None = Query(
        None,
        description="Filter blame to a single instrument track (e.g. 'piano', 'bass')",
    ),
    beat_start: float | None = Query(
        None,
        alias="beatStart",
        description="Restrict to notes at or after this beat position",
    ),
    beat_end: float | None = Query(
        None,
        alias="beatEnd",
        description="Restrict to notes before this beat position",
    ),
    format: str | None = Query(
        None,
        description="Force response format: 'json' or omit for HTML",
    ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the blame view for a MIDI file at a given commit ref.

    Why this exists: musicians and AI agents need to know *which commit* last
    changed each note or measure in a composition — the musical equivalent of
    ``git blame``.  This page renders each note event coloured by its originating
    commit, with inline author and timestamp labels, so a reviewer can trace
    the evolution of any musical idea across the project's history.

    HTML (default): interactive piano-roll-style blame view via Jinja2.  The
    page shell fetches blame data from the JSON API client-side using the token
    stored in ``localStorage``.  The optional ``track``, ``beatStart``, and
    ``beatEnd`` query params are forwarded to the client for pre-filtering.

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``BlameResponse`` with camelCase keys.  Blame entries are built
    deterministically from the repo's commit history so agents can reason about
    provenance without navigating the HTML page.

    Returns 404 when the repo owner/slug combination is unknown.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    short_ref = ref[:8] if len(ref) >= 8 else ref
    short_path = path.split("/")[-1] if path else path

    # Eagerly compute blame data for JSON negotiation.
    # For HTML, this data is not rendered server-side — the template fetches it
    # client-side — but pre-computing here is cheap and enables the JSON path.
    commits_raw, _total = await musehub_repository.list_commits(db, repo_id, limit=50)
    commit_dicts: list[dict[str, object]] = [
        {
            "commit_id": c.commit_id,
            "message": c.message,
            "author": c.author,
            "timestamp": c.timestamp,
        }
        for c in commits_raw
    ]
    entries = _build_blame_entries(
        commits=commit_dicts,
        path=path,
        track_filter=track,
        beat_start_filter=beat_start,
        beat_end_filter=beat_end,
    )
    blame_data = BlameResponse(entries=entries, total_entries=len(entries))

    logger.info(
        "✅ Blame UI: repo=%s ref=%s path=%s entries=%d",
        repo_id,
        ref,
        path,
        len(entries),
    )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/blame.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "short_ref": short_ref,
            "path": path,
            "short_path": short_path,
            "base_url": base_url,
            "current_page": "blame",
            "track": track or "",
            "beat_start": beat_start,
            "beat_end": beat_end,
        },
        templates=templates,
        json_data=blame_data,
        format_param=format,
    )
