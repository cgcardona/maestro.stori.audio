"""
Database module for Maestro.

Provides async SQLAlchemy support with PostgreSQL and SQLite.
"""
from __future__ import annotations

from maestro.db.database import (
    get_db,
    init_db,
    close_db,
    AsyncSessionLocal,
)
from maestro.db.models import User, UsageLog, AccessToken
from maestro.db import muse_models as muse_models  # noqa: F401 — register with Base
from maestro.db import musehub_models as musehub_models  # noqa: F401 — register with Base

__all__ = [
    "get_db",
    "init_db", 
    "close_db",
    "AsyncSessionLocal",
    "User",
    "UsageLog",
    "AccessToken",
]
