"""Muse Hub route package.

Composes sub-routers for repos/branches/commits and issue tracking under
the shared ``/musehub`` prefix. Registered in ``maestro.main`` as:

    app.include_router(musehub.router, prefix="/api/v1", tags=["musehub"])
"""
from __future__ import annotations

from fastapi import APIRouter

from maestro.api.routes.musehub import issues, repos

router = APIRouter(prefix="/musehub", tags=["musehub"])

router.include_router(repos.router)
router.include_router(issues.router)

__all__ = ["router"]
