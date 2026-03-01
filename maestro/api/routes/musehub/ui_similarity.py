"""Muse Hub musical similarity page.

Serves the ``/{owner}/{repo_slug}/similarity/{base}...{head}`` UI page that
visualises the 10-dimension musical similarity vector between two Muse refs.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/similarity/{refs}
    refs encodes ``base...head`` (same convention as the compare page).
    HTML (default) → interactive similarity report with radar chart.
    JSON  (``?format=json`` or ``Accept: application/json``)
         → raw :class:`~maestro.models.musehub_analysis.RefSimilarityResponse`.

Why a dedicated page instead of reusing compare:
  The compare page shows *divergence* (how much changed).  This page shows
  *similarity* (how musically alike two refs are) — an inverted framing that
  is more useful when evaluating whether a variation stays true to a reference.
  The 10-dimension spider chart with base=solid and head=dashed allows a
  producer to immediately see which musical axes pulled apart.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub._templates import templates
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.services import musehub_analysis, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])


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
    "/{owner}/{repo_slug}/similarity/{refs}",
    summary="Muse Hub musical similarity score page",
)
async def similarity_page(
    request: Request,
    owner: str,
    repo_slug: str,
    refs: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the musical similarity report between two Muse refs.

    ``refs`` encodes the two refs as ``base...head``, matching the URL
    convention used by the compare page.  The 10-dimension spider chart
    renders the base ref as a solid polygon and the head ref as a dashed
    overlay so producers can immediately see where the two refs diverge.

    Content negotiation:
    - HTML (default): interactive similarity report via Jinja2.
    - JSON (``Accept: application/json`` or ``?format=json``):
      returns the full :class:`~maestro.models.musehub_analysis.RefSimilarityResponse`.

    Returns 404 when:
    - The repo owner/slug is unknown.
    - The ``refs`` value does not contain the ``...`` separator.

    Agent use case: call with ``?format=json`` to obtain a machine-readable
    similarity vector before deciding whether to generate additional variation
    material.  An ``overall_similarity`` below 0.75 signals that the two refs
    have diverged significantly and a merge should be reviewed carefully.
    """
    if "..." not in refs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Invalid similarity spec '{refs}' — expected format: base...head",
        )
    base_ref, head_ref = refs.split("...", 1)
    if not base_ref or not head_ref:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Both base and head refs must be non-empty",
        )

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    similarity = musehub_analysis.compute_ref_similarity(
        repo_id=repo_id, base_ref=base_ref, compare_ref=head_ref
    )

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "refs": refs,
        "base_url": base_url,
        "current_page": "similarity",
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "similarity", "url": ""},
            {"label": f"{base_ref}...{head_ref}", "url": ""},
        ],
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/similarity.html",
        context=context,
        templates=templates,
        json_data=similarity,
        format_param=format,
    )
