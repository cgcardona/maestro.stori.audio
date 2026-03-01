"""Muse Hub emotion-diff UI page.

Serves the ``/{owner}/{repo_slug}/emotion-diff/{base}...{head}`` page that
visualises the 8-axis emotional shift between two Muse refs.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/emotion-diff/{refs}
    refs encodes ``base...head`` (same convention as the compare page).
    HTML (default) → interactive emotion-diff report with side-by-side
                     radar charts, delta bar chart, and trajectory timeline.
    JSON  (``?format=json`` or ``Accept: application/json``)
         → raw :class:`~maestro.models.musehub_analysis.EmotionDiffResponse`.

Why a dedicated page instead of reusing the PR detail emotion widget:
  The PR detail page embeds the emotion radar as one panel among many.  This
  page gives the full-screen emotion-diff view with per-ref 8D radar charts,
  a delta bar chart, a prose interpretation, a "Listen to comparison" button,
  and an emotional trajectory timeline — features that do not fit in the PR
  detail sidebar.

Auto-discovered by the package ``__init__.py`` — do NOT edit that file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.services import musehub_analysis, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


async def _resolve_repo(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[str, str]:
    """Resolve owner+slug to (repo_id, base_url); raise 404 if not found."""
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), f"/musehub/ui/{owner}/{repo_slug}"


@router.get(
    "/{owner}/{repo_slug}/emotion-diff/{refs}",
    summary="Muse Hub emotion-diff page — 8-axis emotional shift between two refs",
)
async def emotion_diff_page(
    request: Request,
    owner: str,
    repo_slug: str,
    refs: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the 8-axis emotional diff page between two Muse refs.

    ``refs`` encodes the two refs as ``base...head``, matching the URL
    convention used by the compare and similarity pages.  The page renders:

    - Side-by-side 8-dimension radar charts (one per ref, same axis scale)
    - A delta bar chart per axis: green = increase, red = decrease
    - A prose interpretation of the dominant emotional shifts
    - A "Listen to comparison" button for both refs
    - An emotional trajectory timeline across commits between base and head

    Content negotiation:
    - HTML (default): interactive report via Jinja2.
    - JSON (``Accept: application/json`` or ``?format=json``):
      returns the raw :class:`~maestro.models.musehub_analysis.EmotionDiffResponse`.

    Returns 404 when:
    - The repo owner/slug is unknown.
    - The ``refs`` value does not contain the ``...`` separator.

    Agent use case: call with ``?format=json`` to obtain a machine-readable
    emotion-diff payload for programmatic analysis of emotional shifts between
    two commits — e.g. to decide whether a generative commit increased tension
    relative to the main branch without opening the browser.
    """
    if "..." not in refs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Invalid emotion-diff spec '{refs}' — expected format: base...head",
        )
    base_ref, head_ref = refs.split("...", 1)
    if not base_ref or not head_ref:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Both base and head refs must be non-empty",
        )

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    diff = musehub_analysis.compute_emotion_diff(
        repo_id=repo_id,
        head_ref=head_ref,
        base_ref=base_ref,
    )

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "refs": refs,
        "base_url": base_url,
        "current_page": "emotion-diff",
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "emotion-diff", "url": ""},
            {"label": f"{base_ref}...{head_ref}", "url": ""},
        ],
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/emotion_diff.html",
        context=context,
        templates=templates,
        json_data=diff,
        format_param=format,
    )
