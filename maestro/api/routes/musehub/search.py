"""MuseHub semantic search route handler.

Endpoint:
  GET /musehub/search/similar?commit={sha}&limit=10
    — Returns the N most musically similar public commits to the given SHA.

The route resolves the query commit from Postgres, fetches its stored
embedding from Qdrant, and returns ranked results with similarity scores.
If the commit is not yet embedded (e.g. pushed before this feature shipped)
a 404 is returned with a clear message — no silent empty-result fallback.

No business logic lives here — all vector operations are delegated to
maestro.services.musehub_qdrant and maestro.services.musehub_embeddings.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import require_valid_token
from maestro.config import settings
from maestro.db import get_db
from maestro.db import musehub_models as db
from maestro.models.musehub import SimilarCommitResponse, SimilarSearchResponse
from maestro.services.musehub_embeddings import compute_embedding
from maestro.services.musehub_qdrant import MusehubQdrantClient

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level singleton — one client per process, collection ensured on first use.
_qdrant_client: MusehubQdrantClient | None = None


def _get_qdrant_client() -> MusehubQdrantClient:
    """Return the process-level Qdrant client, creating it on first call."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = MusehubQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        _qdrant_client.ensure_collection()
    return _qdrant_client


@router.get(
    "/search/similar",
    response_model=SimilarSearchResponse,
    summary="Find musically similar commits across public repos",
)
async def search_similar(
    commit: str = Query(..., description="Commit SHA to use as the similarity query"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results"),
    db_session: AsyncSession = Depends(get_db),
    _: object = Depends(require_valid_token),
) -> SimilarSearchResponse:
    """Return the N most musically similar public commits to the given commit SHA.

    Resolves the query commit from Postgres to obtain its message (which encodes
    musical metadata), computes its embedding, then queries Qdrant for the closest
    vectors. Only commits from public repos appear in results — visibility is
    enforced server-side by Qdrant payload filtering.

    Raises:
        404: If the commit SHA is not found in the Muse Hub.
        503: If Qdrant is unavailable.
    """
    # --- Resolve commit from Postgres ---
    stmt = select(db.MusehubCommit).where(db.MusehubCommit.commit_id == commit)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit '{commit}' not found in Muse Hub.",
        )

    # --- Compute query vector from commit message ---
    query_vector = compute_embedding(row.message)

    # --- Query Qdrant (sync client — run in thread pool) ---
    try:
        client = _get_qdrant_client()
        raw_results = await asyncio.to_thread(
            client.search_similar,
            query_vector=query_vector,
            limit=limit,
            public_only=True,
            exclude_commit_id=commit,
        )
    except Exception as exc:
        logger.error("❌ Qdrant search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Similarity search is temporarily unavailable.",
        ) from exc

    results = [
        SimilarCommitResponse(
            commit_id=r.commit_id,
            repo_id=r.repo_id,
            score=r.score,
            branch=r.branch,
            author=r.author,
        )
        for r in raw_results
    ]

    logger.info(
        "✅ Similarity search for commit=%s returned %d results",
        commit,
        len(results),
    )
    return SimilarSearchResponse(query_commit=commit, results=results)
