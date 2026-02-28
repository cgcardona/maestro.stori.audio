"""Muse Hub repo, branch, commit, and agent context route handlers.

Endpoint summary:
  POST /musehub/repos                              — create a new remote repo
  GET  /musehub/repos/{repo_id}                   — get repo metadata
  GET  /musehub/repos/{repo_id}/branches          — list all branches
  GET  /musehub/repos/{repo_id}/commits           — list commits (newest first)
  GET  /musehub/repos/{repo_id}/context           — agent context briefing

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository and maestro.services.musehub_context.
"""
from __future__ import annotations

import logging

import yaml  # type: ignore[import-untyped]  # PyYAML ships no py.typed marker
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    BranchListResponse,
    CommitListResponse,
    CreateRepoRequest,
    RepoResponse,
)
from maestro.models.musehub_context import (
    AgentContextResponse,
    ContextDepth,
    ContextFormat,
)
from maestro.services import musehub_context, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos",
    response_model=RepoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a remote Muse repo",
)
async def create_repo(
    body: CreateRepoRequest,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoResponse:
    """Create a new remote Muse Hub repository owned by the authenticated user."""
    owner_user_id: str = claims.get("sub") or ""
    repo = await musehub_repository.create_repo(
        db,
        name=body.name,
        visibility=body.visibility,
        owner_user_id=owner_user_id,
    )
    await db.commit()
    return repo


@router.get(
    "/repos/{repo_id}",
    response_model=RepoResponse,
    summary="Get remote repo metadata",
)
async def get_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> RepoResponse:
    """Return metadata for the given repo. Returns 404 if not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    return repo


@router.get(
    "/repos/{repo_id}/branches",
    response_model=BranchListResponse,
    summary="List all branches in a remote repo",
)
async def list_branches(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> BranchListResponse:
    """Return all branch pointers for a repo, ordered by name."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    branches = await musehub_repository.list_branches(db, repo_id)
    return BranchListResponse(branches=branches)


@router.get(
    "/repos/{repo_id}/commits",
    response_model=CommitListResponse,
    summary="List commits in a remote repo (newest first)",
)
async def list_commits(
    repo_id: str,
    branch: str | None = Query(None, description="Filter by branch name"),
    limit: int = Query(50, ge=1, le=200, description="Max commits to return"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> CommitListResponse:
    """Return commits for a repo, newest first. Optionally filter by branch."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    commits, total = await musehub_repository.list_commits(
        db, repo_id, branch=branch, limit=limit
    )
    return CommitListResponse(commits=commits, total=total)


@router.get(
    "/repos/{repo_id}/context",
    summary="Get complete agent context for a repo ref",
    responses={
        200: {"description": "Agent context document (JSON or YAML)"},
        404: {"description": "Repo not found or ref has no commits"},
    },
)
async def get_agent_context(
    repo_id: str,
    ref: str = Query(
        "HEAD",
        description="Branch name or commit ID to build context for. 'HEAD' resolves to the latest commit.",
    ),
    depth: ContextDepth = Query(
        ContextDepth.standard,
        description="Depth level: 'brief' (~2K tokens), 'standard' (~8K tokens), 'verbose' (uncapped)",
    ),
    format: ContextFormat = Query(
        ContextFormat.json,
        description="Response format: 'json' or 'yaml'",
    ),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> Response:
    """Return a complete musical context briefing for AI agent consumption.

    This endpoint is the canonical entry point for agents starting a composition
    session.  It aggregates musical state, commit history, per-dimension analysis,
    open PRs, open issues, and actionable suggestions into a single document.

    Use ``?depth=brief`` to fit the response in a small context window (~2 K tokens).
    Use ``?depth=verbose`` for full bodies and extended history.
    Use ``?format=yaml`` for human-readable output (e.g. in agent logs).
    """
    context = await musehub_context.build_agent_context(
        db,
        repo_id=repo_id,
        ref=ref,
        depth=depth,
    )
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found or ref has no commits",
        )

    if format == ContextFormat.yaml:
        payload = context.model_dump(by_alias=True)
        yaml_text: str = yaml.dump(payload, allow_unicode=True, sort_keys=False)
        return Response(content=yaml_text, media_type="application/x-yaml")

    return Response(
        content=context.model_dump_json(by_alias=True),
        media_type="application/json",
    )
