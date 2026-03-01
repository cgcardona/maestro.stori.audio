"""Muse Hub fork network visualization page.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/forks — fork network tree (HTML or JSON)

HTML response: an interactive tree diagram showing how the repo has been forked,
with per-fork divergence commit counts and links to each fork repo.

JSON response (``?format=json`` or ``Accept: application/json``): returns a
``ForkNetworkResponse`` with the recursive node graph:

    {
      "root": {
        "owner": "alice",
        "repoSlug": "my-song",
        "repoId": "...",
        "divergenceCommits": 0,
        "forkedBy": "",
        "forkedAt": null,
        "children": [
          {
            "owner": "bob",
            "repoSlug": "my-song",
            "repoId": "...",
            "divergenceCommits": 7,
            "forkedBy": "bob_user_id",
            "forkedAt": "2025-01-15T10:00:00Z",
            "children": []
          }
        ]
      },
      "totalForks": 1
    }

No JWT is required to render the HTML shell.  Auth for private-repo data
is handled client-side via localStorage JWT, consistent with all other
MuseHub UI pages.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.api.routes.musehub.ui import _breadcrumbs, _resolve_repo, templates
from maestro.db import get_db
from maestro.models.musehub import ForkNetworkResponse
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])


@router.get(
    "/{owner}/{repo_slug}/forks",
    summary="Muse Hub fork network visualization page",
)
async def forks_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the fork network tree page or return structured graph data as JSON.

    Why this route exists: musicians and AI agents need to see how a repo has
    been forked and how far each fork has diverged — analogous to GitHub's
    "Network" tab.  This lets an agent identify the most active fork or decide
    when a fork should be merged back upstream.

    HTML (default): renders an interactive tree diagram showing the root repo
    and all direct forks, with per-fork divergence commit counts and links to
    each fork's home page.

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``ForkNetworkResponse`` — a recursive node graph where each ``ForkNetworkNode``
    carries ``divergenceCommits`` (commits ahead of its parent), ``forkedBy``,
    ``forkedAt``, and its own ``children`` list.

    Auth: no JWT required to render the HTML shell.  The embedded JavaScript
    fetches authenticated data from ``GET /api/v1/musehub/repos/{repo_id}/forks``
    using the token stored in localStorage.

    Returns 404 when the repo owner/slug is not found.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    network: ForkNetworkResponse = await musehub_repository.list_repo_forks(db, repo_id)

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/forks.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "forks",
            "total_forks": network.total_forks,
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("forks", ""),
            ),
        },
        templates=templates,
        json_data=network,
        format_param=format,
    )
