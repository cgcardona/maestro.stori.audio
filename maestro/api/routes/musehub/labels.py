"""Muse Hub label management route handlers.

Endpoint summary:
  GET    /musehub/repos/{repo_id}/labels                                     — list labels (public)
  POST   /musehub/repos/{repo_id}/labels                                     — create label (auth required)
  PATCH  /musehub/repos/{repo_id}/labels/{label_id}                          — update label (auth required)
  DELETE /musehub/repos/{repo_id}/labels/{label_id}                          — delete label (auth required)
  POST   /musehub/repos/{repo_id}/issues/{number}/labels                     — assign labels to issue (auth required)
  DELETE /musehub/repos/{repo_id}/issues/{number}/labels/{label_id}          — remove label from issue (auth required)
  POST   /musehub/repos/{repo_id}/pull-requests/{pr_id}/labels               — assign labels to PR (auth required)
  DELETE /musehub/repos/{repo_id}/pull-requests/{pr_id}/labels/{label_id}    — remove label from PR (auth required)

Read endpoints use optional_token — unauthenticated access is allowed for public repos.
Write endpoints always require a valid JWT Bearer token.

ORM dependency: maestro.db.musehub_label_models (batch-01 / PR-464).
If that module is not yet merged, mypy will report a missing import — this is
expected and resolves once the batch-01 migration PR merges.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.services import musehub_repository

if TYPE_CHECKING:
    pass  # ORM models imported at runtime below via conditional import

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Default labels seeded on repo creation ────────────────────────────────────

DEFAULT_LABELS: list[dict[str, str]] = [
    {"name": "bug", "color": "#d73a4a", "description": "Something isn't working"},
    {"name": "enhancement", "color": "#a2eeef", "description": "New feature or request"},
    {"name": "question", "color": "#d876e3", "description": "Further information is requested"},
    {"name": "documentation", "color": "#0075ca", "description": "Improvements or additions to documentation"},
    {"name": "good first issue", "color": "#7057ff", "description": "Good for newcomers"},
    {"name": "help wanted", "color": "#008672", "description": "Extra attention is needed"},
    {"name": "needs-arrangement", "color": "#e4e669", "description": "Track needs musical arrangement work"},
    {"name": "musical-theory", "color": "#0e8a16", "description": "Related to music theory decisions"},
    {"name": "merge-conflict", "color": "#b60205", "description": "Has conflicting changes that must be resolved"},
    {"name": "analysis", "color": "#1d76db", "description": "Requires deeper analysis or review"},
]


# ── Pydantic request / response models ───────────────────────────────────────


class LabelCreate(BaseModel):
    """Payload for creating a new label."""

    name: str = Field(..., min_length=1, max_length=50, description="Label name (unique within repo)")
    color: str = Field(
        ...,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="Hex colour string, e.g. '#d73a4a'",
    )
    description: str | None = Field(None, max_length=200, description="Optional human-readable description")


class LabelUpdate(BaseModel):
    """Payload for updating an existing label (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=50)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=200)


class LabelResponse(BaseModel):
    """Public representation of a label."""

    label_id: str
    repo_id: str
    name: str
    color: str
    description: str | None = None

    model_config = {"from_attributes": True}


class LabelListResponse(BaseModel):
    """Paginated list of labels."""

    items: list[LabelResponse]
    total: int


class AssignLabelsRequest(BaseModel):
    """Body for bulk-assigning labels to an issue or PR."""

    label_ids: list[str] = Field(..., min_length=1, description="Array of label UUIDs to assign")


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_label_or_404(db: AsyncSession, repo_id: str, label_id: str) -> LabelResponse:
    """Fetch a single label by ID, raising 404 if not found.

    Uses a raw SQL query so this file compiles cleanly before the ORM model
    (batch-01 / PR-464) is merged into dev.
    """
    result = await db.execute(
        text(
            "SELECT label_id, repo_id, name, color, description "
            "FROM musehub_labels "
            "WHERE label_id = :label_id AND repo_id = :repo_id"
        ),
        {"label_id": label_id, "repo_id": repo_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Label not found")
    return LabelResponse(**dict(row))


# ── Label CRUD ────────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/labels",
    response_model=LabelListResponse,
    operation_id="listLabels",
    summary="List all labels for a Muse Hub repo",
)
async def list_labels(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> LabelListResponse:
    """Return every label defined in *repo_id*.

    This endpoint is publicly accessible — no authentication required.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    result = await db.execute(
        text(
            "SELECT label_id, repo_id, name, color, description "
            "FROM musehub_labels "
            "WHERE repo_id = :repo_id "
            "ORDER BY name ASC"
        ),
        {"repo_id": repo_id},
    )
    rows = result.mappings().all()
    items = [LabelResponse(**dict(r)) for r in rows]
    return LabelListResponse(items=items, total=len(items))


@router.post(
    "/repos/{repo_id}/labels",
    response_model=LabelResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createLabel",
    summary="Create a label in a Muse Hub repo",
)
async def create_label(
    repo_id: str,
    body: LabelCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> LabelResponse:
    """Create a new label with a name, hex colour, and optional description.

    The caller must be authenticated.  Names must be unique within the repo.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    # Enforce name uniqueness within the repo.
    existing = await db.execute(
        text(
            "SELECT 1 FROM musehub_labels "
            "WHERE repo_id = :repo_id AND name = :name"
        ),
        {"repo_id": repo_id, "name": body.name},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Label '{body.name}' already exists in this repo",
        )

    label_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO musehub_labels (label_id, repo_id, name, color, description) "
            "VALUES (:label_id, :repo_id, :name, :color, :description)"
        ),
        {
            "label_id": label_id,
            "repo_id": repo_id,
            "name": body.name,
            "color": body.color,
            "description": body.description,
        },
    )
    await db.commit()
    logger.info("✅ Created label '%s' (%s) in repo %s", body.name, label_id, repo_id)
    return LabelResponse(
        label_id=label_id,
        repo_id=repo_id,
        name=body.name,
        color=body.color,
        description=body.description,
    )


@router.patch(
    "/repos/{repo_id}/labels/{label_id}",
    response_model=LabelResponse,
    operation_id="updateLabel",
    summary="Update a label's name, colour, or description",
)
async def update_label(
    repo_id: str,
    label_id: str,
    body: LabelUpdate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> LabelResponse:
    """Partially update an existing label.

    Only fields present in the request body are modified; omitted fields are
    left unchanged.  The caller must be authenticated.
    """
    label = await _get_label_or_404(db, repo_id, label_id)

    new_name = body.name if body.name is not None else label.name
    new_color = body.color if body.color is not None else label.color
    new_description = body.description if body.description is not None else label.description

    # If the name is changing, check uniqueness.
    if body.name is not None and body.name != label.name:
        existing = await db.execute(
            text(
                "SELECT 1 FROM musehub_labels "
                "WHERE repo_id = :repo_id AND name = :name AND label_id != :label_id"
            ),
            {"repo_id": repo_id, "name": body.name, "label_id": label_id},
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Label '{body.name}' already exists in this repo",
            )

    await db.execute(
        text(
            "UPDATE musehub_labels "
            "SET name = :name, color = :color, description = :description "
            "WHERE label_id = :label_id AND repo_id = :repo_id"
        ),
        {
            "name": new_name,
            "color": new_color,
            "description": new_description,
            "label_id": label_id,
            "repo_id": repo_id,
        },
    )
    await db.commit()
    logger.info("✅ Updated label %s in repo %s", label_id, repo_id)
    return LabelResponse(
        label_id=label_id,
        repo_id=repo_id,
        name=new_name,
        color=new_color,
        description=new_description,
    )


@router.delete(
    "/repos/{repo_id}/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteLabel",
    summary="Delete a label from a Muse Hub repo",
)
async def delete_label(
    repo_id: str,
    label_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Permanently delete a label and remove it from all associated issues and PRs.

    The caller must be authenticated.
    """
    await _get_label_or_404(db, repo_id, label_id)

    # Remove associations first to maintain referential integrity.
    await db.execute(
        text("DELETE FROM musehub_issue_labels WHERE label_id = :label_id"),
        {"label_id": label_id},
    )
    await db.execute(
        text("DELETE FROM musehub_pr_labels WHERE label_id = :label_id"),
        {"label_id": label_id},
    )
    await db.execute(
        text("DELETE FROM musehub_labels WHERE label_id = :label_id AND repo_id = :repo_id"),
        {"label_id": label_id, "repo_id": repo_id},
    )
    await db.commit()
    logger.info("✅ Deleted label %s from repo %s", label_id, repo_id)


# ── Issue label associations ──────────────────────────────────────────────────


@router.post(
    "/repos/{repo_id}/issues/{number}/labels",
    response_model=list[LabelResponse],
    status_code=status.HTTP_200_OK,
    operation_id="assignLabelsToIssue",
    summary="Assign one or more labels to an issue",
)
async def assign_labels_to_issue(
    repo_id: str,
    number: int,
    body: AssignLabelsRequest,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> list[LabelResponse]:
    """Assign a set of labels (by UUID) to an issue identified by its per-repo number.

    Labels already assigned are silently ignored (idempotent).
    The caller must be authenticated.
    """
    # Resolve issue_id from its per-repo number.
    issue_result = await db.execute(
        text(
            "SELECT issue_id FROM musehub_issues "
            "WHERE repo_id = :repo_id AND number = :number"
        ),
        {"repo_id": repo_id, "number": number},
    )
    issue_id: str | None = issue_result.scalar_one_or_none()
    if issue_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    assigned: list[LabelResponse] = []
    for label_id in body.label_ids:
        label = await _get_label_or_404(db, repo_id, label_id)
        # Upsert — ignore duplicate assignments.
        await db.execute(
            text(
                "INSERT INTO musehub_issue_labels (issue_id, label_id) "
                "VALUES (:issue_id, :label_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"issue_id": issue_id, "label_id": label_id},
        )
        assigned.append(label)

    await db.commit()
    logger.info("✅ Assigned %d label(s) to issue #%s in repo %s", len(assigned), number, repo_id)
    return assigned


@router.delete(
    "/repos/{repo_id}/issues/{number}/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeLabelFromIssue",
    summary="Remove a label from an issue",
)
async def remove_label_from_issue(
    repo_id: str,
    number: int,
    label_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Remove a single label association from an issue.

    Returns 204 whether or not the label was assigned to the issue, making
    this endpoint safely idempotent.  The caller must be authenticated.
    """
    issue_result = await db.execute(
        text(
            "SELECT issue_id FROM musehub_issues "
            "WHERE repo_id = :repo_id AND number = :number"
        ),
        {"repo_id": repo_id, "number": number},
    )
    issue_id: str | None = issue_result.scalar_one_or_none()
    if issue_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    await db.execute(
        text(
            "DELETE FROM musehub_issue_labels "
            "WHERE issue_id = :issue_id AND label_id = :label_id"
        ),
        {"issue_id": issue_id, "label_id": label_id},
    )
    await db.commit()
    logger.info("✅ Removed label %s from issue #%s in repo %s", label_id, number, repo_id)


# ── Pull-request label associations ──────────────────────────────────────────


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/labels",
    response_model=list[LabelResponse],
    status_code=status.HTTP_200_OK,
    operation_id="assignLabelsToPR",
    summary="Assign one or more labels to a pull request",
)
async def assign_labels_to_pr(
    repo_id: str,
    pr_id: str,
    body: AssignLabelsRequest,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> list[LabelResponse]:
    """Assign a set of labels (by UUID) to a pull request identified by *pr_id*.

    Labels already assigned are silently ignored (idempotent).
    The caller must be authenticated.
    """
    pr_result = await db.execute(
        text(
            "SELECT pr_id FROM musehub_pull_requests "
            "WHERE pr_id = :pr_id AND repo_id = :repo_id"
        ),
        {"pr_id": pr_id, "repo_id": repo_id},
    )
    existing_pr_id: str | None = pr_result.scalar_one_or_none()
    if existing_pr_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")

    assigned: list[LabelResponse] = []
    for label_id in body.label_ids:
        label = await _get_label_or_404(db, repo_id, label_id)
        await db.execute(
            text(
                "INSERT INTO musehub_pr_labels (pr_id, label_id) "
                "VALUES (:pr_id, :label_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"pr_id": pr_id, "label_id": label_id},
        )
        assigned.append(label)

    await db.commit()
    logger.info("✅ Assigned %d label(s) to PR %s in repo %s", len(assigned), pr_id, repo_id)
    return assigned


@router.delete(
    "/repos/{repo_id}/pull-requests/{pr_id}/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeLabelFromPR",
    summary="Remove a label from a pull request",
)
async def remove_label_from_pr(
    repo_id: str,
    pr_id: str,
    label_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Remove a single label association from a pull request.

    Returns 204 whether or not the label was previously assigned, making
    this endpoint safely idempotent.  The caller must be authenticated.
    """
    pr_result = await db.execute(
        text(
            "SELECT pr_id FROM musehub_pull_requests "
            "WHERE pr_id = :pr_id AND repo_id = :repo_id"
        ),
        {"pr_id": pr_id, "repo_id": repo_id},
    )
    existing_pr_id: str | None = pr_result.scalar_one_or_none()
    if existing_pr_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")

    await db.execute(
        text(
            "DELETE FROM musehub_pr_labels "
            "WHERE pr_id = :pr_id AND label_id = :label_id"
        ),
        {"pr_id": pr_id, "label_id": label_id},
    )
    await db.commit()
    logger.info("✅ Removed label %s from PR %s in repo %s", label_id, pr_id, repo_id)


# ── Utility: seed default labels for a new repo ───────────────────────────────


async def seed_default_labels(db: AsyncSession, repo_id: str) -> None:
    """Insert the standard set of default labels for a newly created repo.

    Called by the repo-creation service after the repo row is committed.
    Skips any label whose name already exists in the repo (safe to call
    multiple times).
    """
    for label_def in DEFAULT_LABELS:
        existing = await db.execute(
            text(
                "SELECT 1 FROM musehub_labels "
                "WHERE repo_id = :repo_id AND name = :name"
            ),
            {"repo_id": repo_id, "name": label_def["name"]},
        )
        if existing.scalar_one_or_none() is not None:
            continue  # Already seeded — skip.
        await db.execute(
            text(
                "INSERT INTO musehub_labels (label_id, repo_id, name, color, description) "
                "VALUES (:label_id, :repo_id, :name, :color, :description)"
            ),
            {
                "label_id": str(uuid.uuid4()),
                "repo_id": repo_id,
                "name": label_def["name"],
                "color": label_def["color"],
                "description": label_def.get("description"),
            },
        )
    logger.info("✅ Seeded default labels for repo %s", repo_id)
