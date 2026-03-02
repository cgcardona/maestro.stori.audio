"""AgentCeption service configuration.

All settings are prefixed with ``AC_`` so they never collide with Maestro's
``STORI_*`` namespace. Defaults work for local development without any env vars set.

When ``pipeline-config.json`` contains a ``projects`` list and an
``active_project`` name, the model validator applies the matching project's
``gh_repo``, ``repo_dir``, and ``worktrees_dir`` values over the env-var
defaults.  This is the primary mechanism for multi-repo generalisation (AC-601).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AgentCeptionSettings(BaseSettings):
    """Runtime configuration for the AgentCeption dashboard service.

    Path settings are resolved in order:
    1. Environment variables (``AC_REPO_DIR``, ``AC_WORKTREES_DIR``, etc.)
    2. Active project from ``pipeline-config.json`` (overrides env vars when present)
    """

    model_config = SettingsConfigDict(env_prefix="AC_")

    cursor_projects_dir: Path = Path.home() / ".cursor/projects"
    worktrees_dir: Path = Path.home() / ".cursor/worktrees/maestro"
    repo_dir: Path = Path.cwd()
    gh_repo: str = "cgcardona/maestro"
    poll_interval_seconds: int = 5
    github_cache_seconds: int = 10
    database_url: str | None = None
    """Async database URL for AgentCeption's own ac_* tables.

    Set via ``AC_DATABASE_URL`` env var (docker-compose injects this).
    Falls back to a local SQLite file when absent so the service starts
    without Postgres in pure-filesystem dev mode.
    """

    @model_validator(mode="after")
    def _apply_active_project(self) -> AgentCeptionSettings:
        """Override path settings from the active project in ``pipeline-config.json``.

        Reads the config file synchronously at initialisation so that all
        downstream code that imports ``settings`` sees the correct project
        paths immediately.  If the file is absent, malformed, or has no
        ``active_project`` key, the validator is a no-op.
        """
        config_path = self.repo_dir / ".cursor" / "pipeline-config.json"
        if not config_path.exists():
            return self
        try:
            raw: object = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover — filesystem error path
            logger.warning("⚠️ Could not read pipeline-config.json for project override: %s", exc)
            return self
        if not isinstance(raw, dict):
            return self
        active_name: object = raw.get("active_project")
        projects: object = raw.get("projects", [])
        if not isinstance(projects, list) or not active_name:
            return self
        for proj in projects:
            if not isinstance(proj, dict) or proj.get("name") != active_name:
                continue
            if "gh_repo" in proj and isinstance(proj["gh_repo"], str):
                self.gh_repo = proj["gh_repo"]
            if "repo_dir" in proj and isinstance(proj["repo_dir"], str):
                self.repo_dir = Path(proj["repo_dir"])
            if "worktrees_dir" in proj and isinstance(proj["worktrees_dir"], str):
                wd = proj["worktrees_dir"]
                if wd.startswith("~/"):
                    wd = str(Path.home()) + wd[1:]
                self.worktrees_dir = Path(wd)
            break
        return self


settings = AgentCeptionSettings()
