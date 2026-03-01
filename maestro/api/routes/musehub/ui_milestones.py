"""Muse Hub milestones UI route handlers.

Serves browser-readable HTML pages for the milestones section of a Muse Hub
repo — analogous to GitHub's Milestones tab but for music projects.

All pages are rendered via Jinja2 templates.  Route handlers resolve
server-side data (repo_id, owner, slug, milestone data) and pass a minimal
context dict to the template engine; all HTML, CSS, and JavaScript lives in
the template files, not here.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/milestones        — milestones list with progress bars
  GET /musehub/ui/{owner}/{repo_slug}/milestones/{number} — milestone detail with linked issues

Content negotiation applies to both endpoints:
  - Default (HTML): Jinja2 template with progress bars and issue counts.
  - ``?format=json`` or ``Accept: application/json``: returns the raw Pydantic
    response model with camelCase keys — the same contract used by the
    ``/api/v1/musehub/...`` endpoints.

These routes require NO JWT auth — they return HTML shells whose embedded
JavaScript fetches data from the authed JSON API (``/api/v1/musehub/...``)
using a token stored in ``localStorage``.
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
from maestro.models.musehub import (
    IssueListResponse,
    MilestoneListResponse,
    MilestoneResponse,
)
from maestro.services import musehub_issues, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


def _breadcrumbs(*segments: tuple[str, str]) -> list[dict[str, str]]:
    """Build breadcrumb_data list from (label, url) pairs."""
    return [{"label": label, "url": url} for label, url in segments]


async def _resolve_repo(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[str, str]:
    """Resolve owner+slug to repo_id; raise 404 if not found.

    Returns (repo_id, base_url) so callers can unpack both in one line.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), _base_url(owner, repo_slug)


# ---------------------------------------------------------------------------
# Milestones list page
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}/milestones",
    summary="Muse Hub milestones list page with progress bars",
)
async def milestones_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    state: str = Query(
        "open",
        pattern="^(open|closed|all)$",
        description="Filter milestones by state: 'open', 'closed', or 'all'",
    ),
    sort: str = Query(
        "due_on",
        pattern="^(due_on|title|completeness)$",
        description="Sort field: 'due_on', 'title', or 'completeness'",
    ),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the milestones list page or return structured milestone data as JSON.

    HTML (default): renders a filterable list of milestones with progress bars
    showing percentage of closed issues, state badges, due-date indicators, and
    links to each milestone's detail page.

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``MilestoneListResponse`` with all milestones for the given state.

    Why this route exists: milestone overview is the primary entry point for
    tracking compositional goals (album completion, mix revision milestones).
    Progress bars make the completion status immediately scannable.

    No JWT required — HTML shell; JS fetches authed data via localStorage token.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    milestone_data: MilestoneListResponse = await musehub_issues.list_milestones(
        db, repo_id, state=state, sort=sort
    )
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/milestones_list.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "milestones",
            "state": state,
            "sort": sort,
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("milestones", ""),
            ),
        },
        templates=templates,
        json_data=milestone_data,
        format_param=format,
    )


# ---------------------------------------------------------------------------
# Milestone detail page
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}/milestones/{number}",
    summary="Muse Hub milestone detail page with linked issues",
)
async def milestone_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    number: int,
    issue_state: str = Query(
        "open",
        alias="state",
        pattern="^(open|closed|all)$",
        description="Filter linked issues by state: 'open', 'closed', or 'all'",
    ),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the milestone detail page or return structured data as JSON.

    HTML (default): renders milestone metadata (title, description, due date,
    state badge, progress bar) followed by a filterable list of all issues
    assigned to this milestone — showing open and closed issues with state
    badges, labels, and links.

    JSON (``Accept: application/json`` or ``?format=json``): returns a dict
    containing both the ``MilestoneResponse`` and ``IssueListResponse`` for
    issues linked to this milestone.

    Why this route exists: the detail page is where composers track which
    specific tasks (issues) belong to a compositional milestone — e.g.
    "Mix Revision 2" might contain issues for each instrument's mix adjustments.
    The linked issues list lets them triage remaining work without switching
    to the issues tab.

    No JWT required — HTML shell; JS fetches authed data via localStorage token.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    milestone: MilestoneResponse | None = await musehub_issues.get_milestone(
        db, repo_id, number
    )
    if milestone is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Milestone #{number} not found in '{owner}/{repo_slug}'",
        )

    linked_issues = await musehub_issues.list_issues(
        db, repo_id, state=issue_state, milestone_id=str(milestone.milestone_id)
    )
    issue_list = IssueListResponse(issues=linked_issues, total=len(linked_issues))

    class _MilestoneDetailResponse(MilestoneResponse):
        """Composite response: milestone + linked issues for JSON consumers."""

        linked_issues: IssueListResponse

    json_data = _MilestoneDetailResponse(
        **milestone.model_dump(),
        linked_issues=issue_list,
    )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/milestone_detail.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "milestone_id": str(milestone.milestone_id),
            "milestone_number": number,
            "base_url": base_url,
            "current_page": "milestones",
            "issue_state": issue_state,
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("milestones", f"{base_url}/milestones"),
                (f"#{number}", ""),
            ),
        },
        templates=templates,
        json_data=json_data,
        format_param=format,
    )
