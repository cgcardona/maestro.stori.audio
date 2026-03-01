"""Muse Hub milestones route handlers.

Endpoint summary:
  GET    /musehub/repos/{repo_id}/milestones                     — list milestones (public)
  POST   /musehub/repos/{repo_id}/milestones                     — create milestone (auth required)
  GET    /musehub/repos/{repo_id}/milestones/{number}            — get single milestone (public)
  PATCH  /musehub/repos/{repo_id}/milestones/{number}            — update milestone (auth required)
  DELETE /musehub/repos/{repo_id}/milestones/{number}            — delete milestone (auth required)

Read endpoints use optional_token — unauthenticated access is allowed for public repos.
Write endpoints always require a valid JWT Bearer token.
Deleting a milestone sets milestone_id = NULL on associated issues (not cascade delete).
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db import musehub_models as db
from maestro.models.musehub import (
    MilestoneCreate,
    MilestoneListResponse,
    MilestoneResponse,
)
from maestro.models.base import CamelModel
from maestro.services import musehub_issues
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic request models ───────────────────────────────────────────────────


class MilestoneUpdate(CamelModel):
    """Body for PATCH /musehub/repos/{repo_id}/milestones/{number}.

    All fields are optional — send only the fields you want to change.
    ``state`` must be ``"open"`` or ``"closed"`` if provided.
    """

    title: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="New milestone title",
    )
    description: str | None = Field(None, description="New description (Markdown)")
    due_on: datetime | None = Field(None, description="New due date (ISO-8601 UTC); null to clear")
    state: str | None = Field(
        None,
        pattern="^(open|closed)$",
        description="Transition state: 'open' or 'closed'",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_milestone_response(
    row: db.MusehubMilestone, open_count: int = 0, closed_count: int = 0
) -> MilestoneResponse:
    """Convert an ORM row to the wire representation."""
    return MilestoneResponse(
        milestone_id=row.milestone_id,
        number=row.number,
        title=row.title,
        description=row.description,
        state=row.state,
        author=row.author,
        due_on=row.due_on,
        open_issues=open_count,
        closed_issues=closed_count,
        created_at=row.created_at,
    )


async def _get_issue_counts(
    db_session: AsyncSession, milestone_id: str
) -> tuple[int, int]:
    """Return (open_count, closed_count) of issues linked to a milestone."""
    open_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
        db.MusehubIssue.milestone_id == milestone_id,
        db.MusehubIssue.state == "open",
    )
    closed_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
        db.MusehubIssue.milestone_id == milestone_id,
        db.MusehubIssue.state == "closed",
    )
    open_count: int = (await db_session.execute(open_stmt)).scalar_one()
    closed_count: int = (await db_session.execute(closed_stmt)).scalar_one()
    return open_count, closed_count


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/milestones",
    response_model=MilestoneListResponse,
    operation_id="listMilestones",
    summary="List milestones for a Muse Hub repo",
)
async def list_milestones(
    repo_id: str,
    state: str = Query(
        "open",
        pattern="^(open|closed|all)$",
        description="Filter by state: 'open', 'closed', or 'all'",
    ),
    sort: str = Query(
        "due_on",
        pattern="^(due_on|title|completeness)$",
        description="Sort field: 'due_on', 'title', or 'completeness'",
    ),
    db_session: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> MilestoneListResponse:
    """Return milestones for a repo filtered by state.

    Supports filtering by ``state`` (open/closed/all) and sorting by
    ``due_on``, ``title``, or ``completeness`` (percentage of closed issues).
    Unauthenticated callers may only access public repos.
    """
    repo = await musehub_repository.get_repo(db_session, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await musehub_issues.list_milestones(db_session, repo_id, state=state, sort=sort)


@router.post(
    "/repos/{repo_id}/milestones",
    response_model=MilestoneResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMilestone",
    summary="Create a milestone for a Muse Hub repo",
)
async def create_milestone(
    repo_id: str,
    body: MilestoneCreate,
    db_session: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> MilestoneResponse:
    """Create a new milestone in ``open`` state.

    Caller must be the repo owner or a collaborator with write access.
    The milestone is assigned a sequential per-repo number starting at 1.
    """
    repo = await musehub_repository.get_repo(db_session, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    milestone = await musehub_issues.create_milestone(
        db_session,
        repo_id=repo_id,
        title=body.title,
        description=body.description,
        author=token.get("sub", ""),
        due_on=body.due_on,
    )
    await db_session.commit()
    logger.info("✅ Created milestone for repo %s: %s", repo_id, body.title)
    return milestone


@router.get(
    "/repos/{repo_id}/milestones/{number}",
    response_model=MilestoneResponse,
    operation_id="getMilestone",
    summary="Get a single milestone by its per-repo number",
)
async def get_milestone(
    repo_id: str,
    number: int,
    db_session: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> MilestoneResponse:
    """Return a single milestone with open and closed issue counts.

    The ``number`` is the per-repo sequential milestone number (1-based).
    Unauthenticated callers may only access public repos.
    """
    repo = await musehub_repository.get_repo(db_session, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    milestone = await musehub_issues.get_milestone(db_session, repo_id, number)
    if milestone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")
    return milestone


@router.patch(
    "/repos/{repo_id}/milestones/{number}",
    response_model=MilestoneResponse,
    operation_id="updateMilestone",
    summary="Update a milestone's title, description, due date, or state",
)
async def update_milestone(
    repo_id: str,
    number: int,
    body: MilestoneUpdate,
    db_session: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> MilestoneResponse:
    """Partially update a milestone.

    Only provided fields are updated; omitted fields retain their current values.
    Setting ``state="closed"`` closes the milestone (equivalent to marking it done).
    Caller must be the repo owner or a collaborator with write access.
    """
    repo = await musehub_repository.get_repo(db_session, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    stmt = select(db.MusehubMilestone).where(
        db.MusehubMilestone.repo_id == repo_id,
        db.MusehubMilestone.number == number,
    )
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")

    # Apply only provided fields
    if body.title is not None:
        row.title = body.title
    if body.description is not None:
        row.description = body.description
    if body.state is not None:
        row.state = body.state
    # due_on explicitly supports null (clear) vs. absent (keep current):
    # We only update if the field was included in the model payload.
    if "due_on" in body.model_fields_set:
        row.due_on = body.due_on

    await db_session.flush()
    await db_session.refresh(row)
    await db_session.commit()

    open_count, closed_count = await _get_issue_counts(db_session, row.milestone_id)
    logger.info("✅ Updated milestone #%d for repo %s", number, repo_id)
    return _to_milestone_response(row, open_count, closed_count)


@router.delete(
    "/repos/{repo_id}/milestones/{number}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMilestone",
    summary="Delete a milestone (issues become milestone-less, not deleted)",
)
async def delete_milestone(
    repo_id: str,
    number: int,
    db_session: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Delete a milestone.

    All issues currently assigned to this milestone have their ``milestone_id``
    set to NULL — the issues themselves are NOT deleted. This mirrors GitHub's
    behavior when a milestone is removed.
    Caller must be the repo owner or a collaborator with write access.
    """
    repo = await musehub_repository.get_repo(db_session, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    stmt = select(db.MusehubMilestone).where(
        db.MusehubMilestone.repo_id == repo_id,
        db.MusehubMilestone.number == number,
    )
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")

    milestone_id = row.milestone_id

    # Detach issues from this milestone before deleting (set to NULL, not cascade).
    unlink_stmt = (
        update(db.MusehubIssue)
        .where(db.MusehubIssue.milestone_id == milestone_id)
        .values(milestone_id=None)
    )
    await db_session.execute(unlink_stmt)
    await db_session.delete(row)
    await db_session.commit()
    logger.info("✅ Deleted milestone #%d (%s) from repo %s", number, milestone_id, repo_id)
