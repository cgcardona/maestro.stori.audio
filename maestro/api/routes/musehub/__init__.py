"""Muse Hub route package.

Composes sub-routers for repos/branches/commits, issue tracking, pull
requests, releases, and the push/pull sync protocol under the shared
``/musehub`` prefix. Registered in ``maestro.main`` as:

    app.include_router(musehub.router, prefix="/api/v1", tags=["musehub"])

Every route under this router requires a valid JWT Bearer token — the
``require_valid_token`` dependency is wired at the router level so that
no endpoint can be added without authentication. Individual endpoints
that also declare ``Depends(require_valid_token)`` to obtain the token
claims are not double-charged; FastAPI deduplicates identical dependencies
within a single request.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from maestro.api.routes.musehub import analysis, issues, objects, pull_requests, releases, repos, search, sync, webhooks
from maestro.auth.dependencies import require_valid_token

router = APIRouter(
    prefix="/musehub",
    tags=["musehub"],
    dependencies=[Depends(require_valid_token)],
)

# All fixed-path subrouters are included BEFORE repos.router so they are matched
# first and are not shadowed by the /{owner}/{repo_slug} wildcard route declared
# last in repos.py.
router.include_router(issues.router)
router.include_router(pull_requests.router)
router.include_router(releases.router)
router.include_router(sync.router)
router.include_router(objects.router)
router.include_router(search.router)
router.include_router(analysis.router)
router.include_router(webhooks.router)
# repos.router last — contains the /{owner}/{repo_slug} wildcard route.
router.include_router(repos.router)

__all__ = ["router"]
