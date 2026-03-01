"""Muse Hub label management UI route handlers.

Serves the labels management page at ``/{owner}/{repo}/labels`` and
provides POST handlers for label mutations (create, edit, delete, reset).

Endpoint summary:
  GET  /musehub/ui/{owner}/{repo_slug}/labels                          — label list page (public)
  POST /musehub/ui/{owner}/{repo_slug}/labels                          — create label (auth required)
  POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/edit          — update label (auth required)
  POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/delete        — delete label (auth required)
  POST /musehub/ui/{owner}/{repo_slug}/labels/reset                    — reset to 10 defaults (auth required)

Auth policy:
  GET: no authentication required — public repos are readable without a token.
  POST mutations: require a valid Bearer JWT (``require_valid_token``).
  The JS on the labels page reads a token from ``localStorage`` and sends it as
  ``Authorization: Bearer <token>`` on every mutation call.

Design rationale:
  POST routes return JSON so the in-page JS can handle success/error inline
  without a full page reload.  This keeps the UX snappy while keeping the
  server-side contract simple and testable.

  The ``reset`` route is UI-only (not in the JSON API): it wipes all repo
  labels and re-seeds the canonical 10 defaults.  It is useful after
  accidental bulk deletions or when onboarding a repo from an external source.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.labels import DEFAULT_LABELS
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class _LabelRow(BaseModel):
    """Internal representation of a label row with issue count."""

    label_id: str
    repo_id: str
    name: str
    color: str
    description: str | None = None
    issue_count: int = 0

    model_config = {"from_attributes": True}


class _LabelListPayload(BaseModel):
    """JSON payload for the labels list — consumed by the template JS and agents."""

    labels: list[_LabelRow]
    total: int


class _LabelCreateBody(BaseModel):
    """Request body for creating a label."""

    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field(..., pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=200)


class _LabelEditBody(BaseModel):
    """Request body for editing a label (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=50)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=200)


class _LabelActionResponse(BaseModel):
    """Returned by create / edit / delete / reset — confirms the result."""

    ok: bool
    message: str
    label_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


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
    return str(row.repo_id), _base_url(owner, repo_slug)


async def _fetch_labels(db: AsyncSession, repo_id: str) -> list[_LabelRow]:
    """Return all labels for *repo_id* with their open-issue counts.

    Uses a LEFT JOIN on ``musehub_issue_labels`` / ``musehub_issues`` so that
    labels with zero issues still appear.  Sorted alphabetically by name.
    """
    result = await db.execute(
        text(
            """
            SELECT
              ml.id          AS label_id,
              ml.repo_id     AS repo_id,
              ml.name        AS name,
              ml.color       AS color,
              ml.description AS description,
              COUNT(mil.label_id) AS issue_count
            FROM musehub_labels ml
            LEFT JOIN musehub_issue_labels mil ON mil.label_id = ml.id
            WHERE ml.repo_id = :repo_id
            GROUP BY ml.id, ml.repo_id, ml.name, ml.color, ml.description
            ORDER BY ml.name ASC
            """
        ),
        {"repo_id": repo_id},
    )
    rows = result.mappings().all()
    return [_LabelRow(**dict(r)) for r in rows]


async def _assert_label_exists(
    db: AsyncSession, repo_id: str, label_id: str
) -> None:
    """Raise 404 if the label does not belong to this repo."""
    result = await db.execute(
        text(
            "SELECT 1 FROM musehub_labels "
            "WHERE id = :label_id AND repo_id = :repo_id"
        ),
        {"label_id": label_id, "repo_id": repo_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Label not found",
        )


# ---------------------------------------------------------------------------
# GET — label list page
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}/labels",
    summary="Muse Hub label list page — view and manage repo labels",
)
async def labels_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = None,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> StarletteResponse:
    """Render the labels management page or return structured JSON.

    HTML (default): renders ``musehub/pages/labels.html`` — an interactive
    page with:
      - Inline create form with a colour picker and description field
      - Label list: colour swatch, name, description, issue count badge
      - Edit and delete actions on each label (owner/write only, enforced JS-side)
      - «Reset to defaults» button (owner/write only)

    JSON (``Accept: application/json`` or ``?format=json``): returns
    :class:`_LabelListPayload` with all labels and their issue counts.

    Why this route exists: the labels page is the canonical place to define
    the taxonomy used across issues and PRs in a music project repo.
    Pre-seeded defaults (bug, enhancement, needs-arrangement, etc.) cover the
    most common categories; this page lets project owners customise them.

    No JWT required — the HTML shell loads public data. Mutations below
    require a valid token injected via the in-page JS from localStorage.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    labels = await _fetch_labels(db, repo_id)
    json_data = _LabelListPayload(labels=labels, total=len(labels))

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/labels.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "labels",
            "breadcrumb_data": [
                {"label": owner, "url": f"/musehub/ui/{owner}"},
                {"label": repo_slug, "url": base_url},
                {"label": "Labels", "url": ""},
            ],
        },
        templates=templates,
        json_data=json_data,
        format_param=format,
    )


# ---------------------------------------------------------------------------
# POST — create label
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/labels",
    summary="Create a new label in a Muse Hub repo",
    status_code=http_status.HTTP_201_CREATED,
)
async def create_label(
    owner: str,
    repo_slug: str,
    body: _LabelCreateBody,
    db: AsyncSession = Depends(get_db),
    _token: TokenClaims = Depends(require_valid_token),
) -> JSONResponse:
    """Create a label with the given name, hex colour, and optional description.

    Names must be unique within the repo.  Returns the new label's UUID and a
    confirmation message so the in-page JS can optimistically add the row.

    Called via ``fetch()`` from the in-page JavaScript with the Bearer token
    read from ``localStorage``.
    """
    repo_id, _ = await _resolve_repo(owner, repo_slug, db)

    existing = await db.execute(
        text(
            "SELECT 1 FROM musehub_labels "
            "WHERE repo_id = :repo_id AND name = :name"
        ),
        {"repo_id": repo_id, "name": body.name},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Label '{body.name}' already exists in this repo",
        )

    label_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO musehub_labels "
            "(id, repo_id, name, color, description, created_at) "
            "VALUES (:label_id, :repo_id, :name, :color, :description, CURRENT_TIMESTAMP)"
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

    response = _LabelActionResponse(
        ok=True,
        message=f"Label '{body.name}' created",
        label_id=label_id,
    )
    return JSONResponse(content=response.model_dump(), status_code=http_status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# POST — edit label
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/labels/{label_id}/edit",
    summary="Update a label's name, colour, or description",
)
async def edit_label(
    owner: str,
    repo_slug: str,
    label_id: str,
    body: _LabelEditBody,
    db: AsyncSession = Depends(get_db),
    _token: TokenClaims = Depends(require_valid_token),
) -> JSONResponse:
    """Partially update an existing label.

    Only fields present (non-null) in the request body are modified.
    Returns an ``ok`` confirmation so the JS can update the DOM in place.

    Path uses ``/edit`` suffix to distinguish from the delete action, matching
    the HTML form-action convention used across the Muse Hub UI.
    """
    repo_id, _ = await _resolve_repo(owner, repo_slug, db)
    await _assert_label_exists(db, repo_id, label_id)

    # Fetch current values so we can apply partial updates.
    current_result = await db.execute(
        text(
            "SELECT name, color, description "
            "FROM musehub_labels WHERE id = :label_id"
        ),
        {"label_id": label_id},
    )
    current = dict(current_result.mappings().one())

    new_name = body.name if body.name is not None else current["name"]
    new_color = body.color if body.color is not None else current["color"]
    new_description = body.description if body.description is not None else current["description"]

    if body.name is not None and body.name != current["name"]:
        collision = await db.execute(
            text(
                "SELECT 1 FROM musehub_labels "
                "WHERE repo_id = :repo_id AND name = :name AND id != :label_id"
            ),
            {"repo_id": repo_id, "name": body.name, "label_id": label_id},
        )
        if collision.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Label '{body.name}' already exists in this repo",
            )

    await db.execute(
        text(
            "UPDATE musehub_labels "
            "SET name = :name, color = :color, description = :description "
            "WHERE id = :label_id AND repo_id = :repo_id"
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

    response = _LabelActionResponse(
        ok=True,
        message=f"Label '{new_name}' updated",
        label_id=label_id,
    )
    return JSONResponse(content=response.model_dump())


# ---------------------------------------------------------------------------
# POST — delete label
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/labels/{label_id}/delete",
    summary="Delete a label from a Muse Hub repo",
)
async def delete_label(
    owner: str,
    repo_slug: str,
    label_id: str,
    db: AsyncSession = Depends(get_db),
    _token: TokenClaims = Depends(require_valid_token),
) -> JSONResponse:
    """Permanently delete a label and remove it from all issues and PRs.

    Uses ``/delete`` suffix (not ``DELETE`` method) so the action can be
    triggered from a standard HTML form via JavaScript fetch.

    Associations are cleaned up before the label row is removed to satisfy
    foreign-key constraints on ``musehub_issue_labels`` and ``musehub_pr_labels``.
    """
    repo_id, _ = await _resolve_repo(owner, repo_slug, db)
    await _assert_label_exists(db, repo_id, label_id)

    await db.execute(
        text("DELETE FROM musehub_issue_labels WHERE label_id = :label_id"),
        {"label_id": label_id},
    )
    await db.execute(
        text("DELETE FROM musehub_pr_labels WHERE label_id = :label_id"),
        {"label_id": label_id},
    )
    await db.execute(
        text(
            "DELETE FROM musehub_labels "
            "WHERE id = :label_id AND repo_id = :repo_id"
        ),
        {"label_id": label_id, "repo_id": repo_id},
    )
    await db.commit()
    logger.info("✅ Deleted label %s from repo %s", label_id, repo_id)

    response = _LabelActionResponse(
        ok=True,
        message="Label deleted",
        label_id=label_id,
    )
    return JSONResponse(content=response.model_dump())


# ---------------------------------------------------------------------------
# POST — reset to defaults
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/labels/reset",
    summary="Reset repo labels to the 10 canonical defaults",
)
async def reset_labels(
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
    _token: TokenClaims = Depends(require_valid_token),
) -> JSONResponse:
    """Delete all existing labels and re-seed the 10 canonical defaults.

    Why this route exists: useful after accidental bulk deletion or when
    onboarding a repo from an external source that uses non-standard labels.
    The reset is destructive — all custom labels and their issue/PR associations
    are removed before the defaults are inserted.

    This endpoint is UI-only (not exposed via the JSON API) because it is a
    high-impact destructive action best surfaced through the labelled
    «Reset to defaults» button with a confirmation dialog.
    """
    repo_id, _ = await _resolve_repo(owner, repo_slug, db)

    # Remove all associations first, then all labels for this repo.
    await db.execute(
        text(
            "DELETE FROM musehub_issue_labels "
            "WHERE label_id IN ("
            "  SELECT id FROM musehub_labels WHERE repo_id = :repo_id"
            ")"
        ),
        {"repo_id": repo_id},
    )
    await db.execute(
        text(
            "DELETE FROM musehub_pr_labels "
            "WHERE label_id IN ("
            "  SELECT id FROM musehub_labels WHERE repo_id = :repo_id"
            ")"
        ),
        {"repo_id": repo_id},
    )
    await db.execute(
        text("DELETE FROM musehub_labels WHERE repo_id = :repo_id"),
        {"repo_id": repo_id},
    )

    # Re-seed defaults.
    for label_def in DEFAULT_LABELS:
        await db.execute(
            text(
                "INSERT INTO musehub_labels "
                "(id, repo_id, name, color, description, created_at) "
                "VALUES (:label_id, :repo_id, :name, :color, :description, CURRENT_TIMESTAMP)"
            ),
            {
                "label_id": str(uuid.uuid4()),
                "repo_id": repo_id,
                "name": label_def["name"],
                "color": label_def["color"],
                "description": label_def.get("description"),
            },
        )

    await db.commit()
    logger.info(
        "✅ Reset labels to %d defaults for repo %s", len(DEFAULT_LABELS), repo_id
    )

    response = _LabelActionResponse(
        ok=True,
        message=f"Labels reset to {len(DEFAULT_LABELS)} defaults",
        label_id=None,
    )
    return JSONResponse(content=response.model_dump())
