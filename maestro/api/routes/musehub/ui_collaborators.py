"""Muse Hub collaborators/team management UI route.

Serves the admin-only team management page at:
  GET /musehub/ui/{owner}/{repo_slug}/settings/collaborators

The page lets repository admins and owners manage team access:
- User search + invite form with permission selector (read / write / admin)
- Collaborators table with colour-coded permission badges and remove buttons
- Pending invites section (invites not yet accepted)
- Owner crown badge to distinguish the repo owner from regular collaborators

Auth policy
-----------
The HTML shell requires no JWT — auth is enforced client-side:
  - The embedded JS reads a JWT from ``localStorage`` and sends it as a
    Bearer token to the collaborators JSON API.
  - If the caller lacks admin+ permission the API returns 403; the page
    renders a "Permission denied" error card rather than crashing.

JSON alternate
--------------
``?format=json`` or ``Accept: application/json`` returns
:class:`~maestro.api.routes.musehub.collaborators.CollaboratorListResponse`
populated from the database, suitable for agent consumption.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/settings/collaborators  — HTML (default) or JSON
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.collaborators import CollaboratorListResponse, _orm_to_response
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.db.musehub_collaborator_models import MusehubCollaborator
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"

# Instantiate locally rather than importing from ui.py to avoid a circular dep.
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


async def _resolve_repo_id(owner: str, repo_slug: str, db: AsyncSession) -> tuple[str, str]:
    """Resolve owner+slug to repo_id; raise 404 if not found.

    Returns (repo_id, base_url).
    """
    from fastapi import HTTPException
    from fastapi import status as http_status

    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), _base_url(owner, repo_slug)


@router.get(
    "/{owner}/{repo_slug}/settings/collaborators",
    summary="Muse Hub team management page — add/remove collaborators and set permissions",
)
async def collaborators_settings_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the admin-only collaborators/team management page.

    Why this route exists: repository admins need a GUI to manage who has
    access to a composition project, set granular permission levels (read /
    write / admin), search for MuseHub users to invite, and remove stale
    collaborators — all without issuing raw API calls.

    HTML (default): renders ``musehub/pages/collaborators_settings.html``
    with:
      - User search + invite form with permission selector
      - Collaborators table: avatar placeholder, username, permission badge
        (colour-coded: read=grey, write=blue, admin=orange, owner=gold crown)
      - Pending invites section
      - Remove button on each non-owner row (admin+ only; disabled for owner)

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``CollaboratorListResponse`` with all current collaborators.  Pending
    invites are not exposed via this shortcut — use the full collaborators
    API for filtering by invite status.

    Auth: the HTML shell carries no JWT server-side.  Client JS reads the
    token from ``localStorage`` and attaches it to every API call.  If the
    caller lacks admin+ permission the API returns 403; the page renders an
    inline error rather than crashing.
    """
    repo_id, base_url = await _resolve_repo_id(owner, repo_slug, db)

    # For JSON responses, eagerly fetch the collaborator list from DB.
    result = await db.execute(
        select(MusehubCollaborator).where(MusehubCollaborator.repo_id == repo_id)
    )
    rows = result.scalars().all()
    collaborators = [_orm_to_response(r) for r in rows]
    json_data = CollaboratorListResponse(collaborators=collaborators, total=len(collaborators))

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/collaborators_settings.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "settings",
            "settings_tab": "collaborators",
            "breadcrumb_data": [
                {"label": owner, "url": f"/musehub/ui/{owner}"},
                {"label": repo_slug, "url": base_url},
                {"label": "Settings", "url": f"{base_url}/settings"},
                {"label": "Collaborators", "url": ""},
            ],
        },
        templates=templates,
        json_data=json_data,
        format_param=format,
    )
