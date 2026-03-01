"""Muse Hub fork network UI route — interactive DAG of a repo's fork tree.

Serves the fork network page for any public Muse Hub repo.  The page renders
an interactive SVG directed acyclic graph (DAG) where each node is a fork and
each edge is coloured by the divergence (commits ahead) between the fork and
its parent.

Endpoint:
  GET /musehub/ui/{owner}/{repo_slug}/forks — fork network page

Content negotiation (one URL, two audiences):
  HTML (default) — interactive SVG DAG rendered via Jinja2.
  JSON (``Accept: application/json`` or ``?format=json``) — returns
  ``ForkNetworkResponse`` with the full tree, suitable for programmatic
  traversal by agents.

Auth:
  No JWT required — public repos are visible to everyone.  The client-side
  JavaScript reads a token from ``localStorage`` only for write actions
  (e.g. "Contribute upstream").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import musehub_models as db
from maestro.db import get_db
from maestro.models.musehub import ForkNetworkNode, ForkNetworkResponse
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_repo(owner: str, repo_slug: str, db_session: AsyncSession) -> tuple[str, str]:
    """Resolve owner+slug to (repo_id, base_url); raise 404 when not found.

    Returns both the internal repo_id UUID and the canonical UI base URL so
    route handlers can unpack both in one call without repeating the lookup.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db_session, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), f"/musehub/ui/{owner}/{repo_slug}"


async def _count_commits(db_session: AsyncSession, repo_id: str) -> int:
    """Return the total number of commits in a repo.

    Used as a fast proxy for divergence — a fork's commit count relative to
    the source indicates how far it has diverged.
    """
    result = await db_session.execute(
        select(func.count()).where(db.MusehubCommit.repo_id == repo_id)
    )
    return result.scalar_one()


async def _build_fork_network(
    db_session: AsyncSession,
    source_repo_id: str,
    source_owner: str,
    source_slug: str,
    source_commit_count: int,
) -> ForkNetworkResponse:
    """Build the full fork network tree rooted at *source_repo_id*.

    Queries ``musehub_forks`` for all direct forks of the source repo, then
    for each fork repo fetches its metadata and commit count.  Divergence is
    approximated as the number of commits in the fork that exceed the source's
    count (commits ahead).  This is a set-cardinality proxy — sufficient for
    display without requiring a full commit-graph traversal.

    Returns a ``ForkNetworkResponse`` with a recursive ``ForkNetworkNode`` tree.
    The root node always has ``divergence_commits=0``; fork nodes carry the
    ahead count vs. the source.
    """
    _utc_now = datetime.now(tz=timezone.utc)

    fork_rows = (
        await db_session.execute(
            select(db.MusehubFork).where(db.MusehubFork.source_repo_id == source_repo_id)
        )
    ).scalars().all()

    children: list[ForkNetworkNode] = []
    for fork in fork_rows:
        fork_repo_row = await db_session.get(db.MusehubRepo, fork.fork_repo_id)
        if fork_repo_row is None or fork_repo_row.deleted_at is not None:
            continue

        fork_commit_count = await _count_commits(db_session, fork.fork_repo_id)
        ahead = max(0, fork_commit_count - source_commit_count)

        children.append(
            ForkNetworkNode(
                owner=fork_repo_row.owner,
                repo_slug=fork_repo_row.slug,
                repo_id=str(fork_repo_row.repo_id),
                divergence_commits=ahead,
                forked_by=fork.forked_by,
                forked_at=fork.created_at,
                children=[],
            )
        )

    root = ForkNetworkNode(
        owner=source_owner,
        repo_slug=source_slug,
        repo_id=source_repo_id,
        divergence_commits=0,
        forked_by="",
        forked_at=None,
        children=children,
    )

    logger.info(
        "✅ Fork network: source=%s/%s children=%d",
        source_owner,
        source_slug,
        len(children),
    )
    return ForkNetworkResponse(root=root, total_forks=len(children))


# ---------------------------------------------------------------------------
# Fork network page
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}/forks",
    summary="Muse Hub fork network — interactive SVG DAG of repo forks",
)
async def forks_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db_session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the fork network page or return structured fork data as JSON.

    HTML (default): renders an SSR fork table and an interactive SVG DAG where
    each node represents a fork of the source repo.  The table is rendered
    server-side by Jinja2; the DAG is driven by JavaScript using fork data
    injected as ``window.__forkNetwork`` (no async fetch needed).

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``ForkNetworkResponse`` with a recursive ``ForkNetworkNode`` tree — the
    canonical contract for agents that need to reason about the fork graph
    without parsing HTML.

    No JWT required — public repo fork graphs are visible to everyone.  The
    client-side token is only used for write actions embedded in the page.

    Returns 404 when the owner/slug combination is not found.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db_session)

    source_commit_count = await _count_commits(db_session, repo_id)
    fork_network = await _build_fork_network(
        db_session,
        source_repo_id=repo_id,
        source_owner=owner,
        source_slug=repo_slug,
        source_commit_count=source_commit_count,
    )

    # Pre-serialize the full network for the SVG DAG JavaScript renderer.
    # Jinja2's tojson filter cannot serialize Pydantic models directly, so we
    # convert to a plain dict (camelCase, JSON-safe) before passing to the template.
    fork_network_json: dict[str, object] = fork_network.model_dump(by_alias=True, mode="json")

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/forks.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "forks",
            "total_forks": fork_network.total_forks,
            "forks": fork_network.root.children,
            "fork_network_json": fork_network_json,
            "breadcrumb_data": [
                {"label": owner, "url": f"/musehub/ui/{owner}"},
                {"label": repo_slug, "url": base_url},
                {"label": "forks", "url": ""},
            ],
        },
        templates=templates,
        json_data=fork_network,
        format_param=format,
    )
