"""Qdrant client for MuseHub semantic search — vector upsert and similarity queries.

Manages a dedicated Qdrant collection (``musehub_compositions``) separate from
the RAG docs collection used by the reasoning pipeline. Each point in the
collection represents a single Muse Hub commit and carries:

  - A 128-dim musical feature vector (see musehub_embeddings.py)
  - Payload metadata: repo_id, commit_id, is_public, branch, author

Boundary rules (same as other musehub services):
  - Must NOT import state stores, SSE queues, or LLM clients.
  - Must NOT import maestro.core.* modules.
  - May import from maestro.services.musehub_embeddings.
  - Wraps qdrant_client at the boundary with typed results.

Public factory
--------------
``get_qdrant_client()`` is an ``@lru_cache``-backed factory that returns the
process-level singleton.  Inject it via ``Depends(get_qdrant_client)`` in
FastAPI route handlers so the dependency is explicit, overridable in tests via
``app.dependency_overrides``, and never leaks global mutable state.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    QueryResponse,
    VectorParams,
)

from maestro.config import settings
from maestro.services.musehub_embeddings import VECTOR_DIM

logger = logging.getLogger(__name__)

COLLECTION_NAME = "musehub_compositions"


@dataclass
class SimilarCommitResult:
    """A single result from a MuseHub similarity search.

    All similarity scores are cosine distances in [0.0, 1.0] where 1.0 is
    identical and 0.0 is maximally dissimilar. Results are pre-sorted
    descending by score before being returned to callers.

    Fields:
        commit_id: The Muse Hub commit SHA that matched.
        repo_id: UUID of the repository containing the commit.
        score: Cosine similarity score in [0.0, 1.0].
        branch: Branch the commit lives on (e.g. "main").
        author: Commit author identifier (usually JWT ``sub``).
    """

    commit_id: str
    repo_id: str
    score: float
    branch: str
    author: str


class MusehubQdrantClient:
    """Typed wrapper around QdrantClient for MuseHub composition search.

    Provides two operations:
      1. ``upsert_embedding`` — store or update a commit's embedding on push.
      2. ``search_similar`` — find the N most similar commits to a query vector.

    Visibility filtering (``public_only=True``) is applied server-side by
    Qdrant's payload filter so private repos never appear in results.

    Instantiation is lightweight (no network call). The underlying
    QdrantClient is synchronous; all methods should be called from an async
    context using ``asyncio.to_thread`` or from synchronous test code.

    Args:
        host: Qdrant host (defaults to "qdrant" — the Docker service name).
        port: Qdrant gRPC/HTTP port (defaults to 6333).
    """

    def __init__(self, host: str = "qdrant", port: int = 6333) -> None:
        self._client = QdrantClient(host=host, port=port)
        self._collection_ready = False

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    def ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not already exist.

        Idempotent — safe to call on every startup or before the first upsert.
        Uses cosine distance so vector similarity maps directly to musical
        relatedness without further transformation.
        """
        existing = {c.name for c in self._client.get_collections().collections}
        if COLLECTION_NAME not in existing:
            self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info("✅ Created Qdrant collection '%s' (dim=%d)", COLLECTION_NAME, VECTOR_DIM)
        else:
            logger.debug("Collection '%s' already exists — skipping create", COLLECTION_NAME)
        self._collection_ready = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert_embedding(
        self,
        *,
        commit_id: str,
        repo_id: str,
        is_public: bool,
        vector: list[float],
        branch: str = "main",
        author: str = "",
    ) -> None:
        """Store or update the embedding for a commit in Qdrant.

        Called immediately after a successful push ingestion so the collection
        stays in sync with the Postgres commit table. Qdrant upserts are
        idempotent — re-pushing the same commit overwrites the old vector.

        The point ID is a deterministic integer derived from the first 8 bytes
        of the commit_id hex string. This is stable across restarts and avoids
        storing a separate ID mapping table.

        Args:
            commit_id: The Muse Hub commit SHA (used as the stable point ID).
            repo_id: UUID of the owning repo (stored as payload for filtering).
            is_public: Whether the repo is public — private repos are excluded
                from similarity search results.
            vector: 128-dim float vector from musehub_embeddings.compute_embedding.
            branch: Branch name (stored in payload for display purposes).
            author: Commit author string (stored in payload for display).
        """
        if not self._collection_ready:
            self.ensure_collection()

        point_id = _commit_id_to_int(commit_id)
        payload: dict[str, str | bool] = {
            "commit_id": commit_id,
            "repo_id": repo_id,
            "is_public": is_public,
            "branch": branch,
            "author": author,
        }
        self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        logger.info(
            "✅ Upserted embedding for commit=%s repo=%s is_public=%s",
            commit_id,
            repo_id,
            is_public,
        )

    def search_similar(
        self,
        *,
        query_vector: list[float],
        limit: int = 10,
        public_only: bool = True,
        exclude_commit_id: str | None = None,
    ) -> list[SimilarCommitResult]:
        """Return the most similar commits to a query vector.

        Applies a visibility filter when ``public_only=True`` so private repos
        never appear in results. The query commit itself is excluded when
        ``exclude_commit_id`` is provided.

        Args:
            query_vector: 128-dim float vector to search against.
            limit: Maximum number of results to return (default 10).
            public_only: If True, only return commits from public repos.
            exclude_commit_id: Commit ID to exclude from results (the query
                commit itself — avoids trivial self-match).

        Returns:
            List of SimilarCommitResult sorted descending by score.
        """
        if not self._collection_ready:
            self.ensure_collection()

        qdrant_filter: Filter | None = None
        if public_only:
            qdrant_filter = Filter(
                must=FieldCondition(key="is_public", match=MatchValue(value=True))
            )

        response: QueryResponse = self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=limit + (1 if exclude_commit_id else 0),
            query_filter=qdrant_filter,
            with_payload=True,
        )

        results: list[SimilarCommitResult] = []
        for hit in response.points:
            payload = hit.payload or {}
            cid = str(payload.get("commit_id", ""))
            if exclude_commit_id and cid == exclude_commit_id:
                continue
            results.append(
                SimilarCommitResult(
                    commit_id=cid,
                    repo_id=str(payload.get("repo_id", "")),
                    score=float(hit.score),
                    branch=str(payload.get("branch", "main")),
                    author=str(payload.get("author", "")),
                )
            )
            if len(results) >= limit:
                break

        logger.info("✅ Semantic search returned %d results (public_only=%s)", len(results), public_only)
        return results


# ---------------------------------------------------------------------------
# Public dependency factory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_qdrant_client() -> MusehubQdrantClient:
    """Return the process-level MusehubQdrantClient, creating it on first call.

    Decorated with ``@lru_cache(maxsize=1)`` so the client is initialised
    exactly once per process and reused on every subsequent call — the same
    semantics as the old module-level singleton, but explicit and injectable.

    Inject into FastAPI route handlers via ``Depends(get_qdrant_client)``.
    Override in tests via ``app.dependency_overrides[get_qdrant_client]``.

    Returns:
        A ready-to-use MusehubQdrantClient with the Qdrant collection ensured.
    """
    client = MusehubQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    client.ensure_collection()
    return client


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _commit_id_to_int(commit_id: str) -> int:
    """Convert a commit ID string to a stable 64-bit integer for Qdrant point IDs.

    Takes the first 16 hex characters (8 bytes) of the commit SHA and
    interprets them as an unsigned integer. For non-hex commit IDs the
    string is hashed with Python's built-in hash before masking to 63 bits
    (Qdrant requires positive integer IDs).

    Args:
        commit_id: Any non-empty commit identifier string.

    Returns:
        Positive integer in [1, 2^63 - 1].
    """
    try:
        # Take first 16 hex chars = 8 bytes = 64-bit int
        hex_prefix = commit_id.replace("-", "")[:16]
        value = int(hex_prefix, 16)
        # Qdrant requires positive IDs — ensure MSB is clear
        return value & 0x7FFFFFFFFFFFFFFF or 1
    except (ValueError, IndexError):
        # Fallback for non-hex IDs (e.g. test fixtures)
        return (hash(commit_id) & 0x7FFFFFFFFFFFFFFF) or 1
