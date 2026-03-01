"""Muse Hub route package.

Composes sub-routers for repos/branches/commits, issue tracking, pull
requests, releases, and the push/pull sync protocol under the shared
``/musehub`` prefix. Registered in ``maestro.main`` as:

    app.include_router(musehub.router, prefix="/api/v1", tags=["musehub"])

Auth policy:
- Public repo GET endpoints use ``optional_token`` — unauthenticated
  access is allowed for public visibility repos; private repos return 401.
- Write endpoints (POST/PUT/DELETE) and sync endpoints always use
  ``require_valid_token`` declared on the individual route handler.
"""
from __future__ import annotations

from fastapi import APIRouter

from maestro.api.routes.musehub import (
    analysis,
    blame,
    issues,
    objects,
    pull_requests,
    releases,
    repos,
    search,
    social,
    sync,
    webhooks,
)

router = APIRouter(
    prefix="/musehub",
)

# All fixed-path subrouters are included BEFORE repos.router so they are matched
# first and are not shadowed by the /{owner}/{repo_slug} wildcard route declared
# last in repos.py.
router.include_router(issues.router, tags=["Issues"])
router.include_router(pull_requests.router, tags=["Pull Requests"])
router.include_router(releases.router, tags=["Releases"])
router.include_router(sync.router, tags=["Sync"])
router.include_router(objects.router, tags=["Objects"])
router.include_router(blame.router, tags=["Blame"])
router.include_router(search.router, tags=["Search"])
router.include_router(analysis.router, tags=["Analysis"])
router.include_router(webhooks.router, tags=["Webhooks"])
router.include_router(social.router, tags=["Social"])
# repos.router last — contains the /{owner}/{repo_slug} wildcard route.
router.include_router(repos.router, tags=["Repos"])

__all__ = ["router"]
