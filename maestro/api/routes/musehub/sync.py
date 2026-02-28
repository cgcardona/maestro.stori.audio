"""Muse Hub sync protocol route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/push — batch-commit and object upload
  POST /musehub/repos/{repo_id}/pull — fetch missing commits and objects

Both endpoints require a valid JWT Bearer token.  No business logic lives
here — all persistence is delegated to maestro.services.musehub_sync.

After a successful push, embeddings are computed for the new commits and
upserted to Qdrant as a BackgroundTask — the response is returned
immediately without waiting for embedding completion.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    CommitInput,
    PullRequest,
    PullResponse,
    PushRequest,
    PushResponse,
)
from maestro.services import musehub_repository, musehub_sync
from maestro.services.musehub_sync import embed_push_commits

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/push",
    response_model=PushResponse,
    summary="Push commits and objects to a remote Muse repo",
)
async def push(
    repo_id: str,
    body: PushRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> PushResponse:
    """Batch-upload commits and binary objects to the Hub.

    Enforces fast-forward semantics: a push that would move the branch head
    backwards (non-fast-forward) is rejected with ``409 non_fast_forward``
    unless ``force: true`` is set in the request body.

    Objects are base64-encoded in ``content_b64``.  For MVP, objects up to
    ~1 MB are fine; larger files will require pre-signed URL upload in a
    future release.

    After the DB commit, musical feature vectors are computed for the pushed
    commits and upserted to Qdrant as a background task so the push response
    is not delayed by embedding computation.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    author: str = claims.get("sub") or "unknown"
    is_public = (repo.visibility == "public")

    try:
        result = await musehub_sync.ingest_push(
            db,
            repo_id=repo_id,
            branch=body.branch,
            head_commit_id=body.head_commit_id,
            commits=body.commits,
            objects=body.objects,
            force=body.force,
            author=author,
        )
    except ValueError as exc:
        if str(exc) == "non_fast_forward":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "non_fast_forward"},
            )
        raise

    await db.commit()

    # Schedule embedding as background task — does not block the push response.
    background_tasks.add_task(
        embed_push_commits,
        commits=body.commits,
        repo_id=repo_id,
        branch=body.branch,
        author=author,
        is_public=is_public,
    )

    return result


@router.post(
    "/repos/{repo_id}/pull",
    response_model=PullResponse,
    summary="Fetch missing commits and objects from a remote Muse repo",
)
async def pull(
    repo_id: str,
    body: PullRequest,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PullResponse:
    """Return commits and objects the caller does not yet have.

    The client sends ``have_commits`` and ``have_objects`` as exclusion lists;
    the Hub returns everything it has that is NOT in those lists.

    MVP simplification: no ancestry traversal is performed — the client
    receives all stored commits/objects for the branch minus the ones it
    already has.  Streaming / pre-signed URL optimization is tracked in a
    follow-up issue.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    return await musehub_sync.compute_pull_delta(
        db,
        repo_id=repo_id,
        branch=body.branch,
        have_commits=body.have_commits,
        have_objects=body.have_objects,
    )
