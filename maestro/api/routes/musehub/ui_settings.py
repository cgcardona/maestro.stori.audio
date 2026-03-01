"""Muse Hub repo settings page routes.

Serves the repository settings UI at ``/{owner}/{repo_slug}/settings``.
The page is split into four sidebar sections:

- **General** — name, description, homepage URL, topics, license, visibility.
- **Collaboration** — collaborator management panel.
- **Merge Settings** — merge/squash/rebase strategy toggles and auto-delete option.
- **Danger Zone** — archive, transfer ownership, and delete repo (with name confirmation).

Content negotiation:
- HTML (default): full interactive settings page via Jinja2 template.
- JSON (``Accept: application/json`` or ``?format=json``): returns
  ``RepoSettingsResponse`` — same model as the API endpoint
  ``GET /api/v1/musehub/repos/{repo_id}/settings``.

Auth contract:
- The HTML shell requires no JWT to render.  Client-side JavaScript reads the
  JWT from ``localStorage`` and fetches/patches settings via the API.
- Write operations (rename, visibility change, delete) call the authed API
  endpoints and are rejected with 401/403 by the API when unauthenticated.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.models.musehub import RepoSettingsResponse
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui-settings"])

from pathlib import Path
from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


@router.get(
    "/{owner}/{repo_slug}/settings",
    summary="Muse Hub repo settings page",
)
async def settings_page(
    request: Request,
    owner: str,
    repo_slug: str,
    section: str = Query(
        "general",
        pattern="^(general|collaboration|merge|danger)$",
        description="Settings sidebar section to show on load",
    ),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the repo settings page or return current settings as JSON.

    Why this exists: all mutable repo properties — visibility, name, merge
    strategy, collaborators, and destructive operations — live in one place,
    mirroring GitHub's ``/{owner}/{repo}/settings`` pattern so musicians
    and agents have a predictable entry point for administrative changes.

    HTML sections (sidebar navigation):
    - ``general``       — Basic identity: name, description, URL, topics, license, visibility.
    - ``collaboration`` — Invite/remove collaborators; set per-collaborator permissions.
    - ``merge``         — Enable/disable merge commit, squash, and rebase merge strategies;
                          toggle auto-delete of head branch after merge.
    - ``danger``        — Archive repo (read-only), transfer to another owner,
                          delete repo (requires typing the full repo name to confirm).

    Content negotiation:
    - HTML (default): interactive settings shell; JS fetches
      ``GET /api/v1/musehub/repos/{repo_id}/settings`` and patches via
      ``PATCH /api/v1/musehub/repos/{repo_id}/settings``.
    - JSON (``Accept: application/json`` or ``?format=json``): returns the
      current ``RepoSettingsResponse`` for programmatic inspection.

    The ``?section=`` param pre-scrolls the sidebar to the requested section
    on load, so deep-links like ``?section=danger`` work without JS.

    No JWT required to render the HTML shell.  All write operations require
    a valid owner/admin JWT, enforced by the API layer.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        from fastapi import HTTPException
        from fastapi import status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )

    repo_id = str(row.repo_id)
    base_url = _base_url(owner, repo_slug)

    settings: RepoSettingsResponse | None = await musehub_repository.get_repo_settings(
        db, repo_id
    )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/settings.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "active_section": section,
            "current_page": "settings",
        },
        templates=_templates,
        json_data=settings,
        format_param=format,
    )
