"""AgentCeption service configuration.

All settings are prefixed with ``AC_`` so they never collide with Maestro's
``STORI_*`` namespace. Defaults work for local development without any env vars set.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentCeptionSettings(BaseSettings):
    """Runtime configuration for the AgentCeption dashboard service."""

    model_config = SettingsConfigDict(env_prefix="AC_")

    cursor_projects_dir: Path = Path.home() / ".cursor/projects"
    worktrees_dir: Path = Path.home() / ".cursor/worktrees/maestro"
    repo_dir: Path = Path("/Users/gabriel/dev/tellurstori/maestro")
    gh_repo: str = "cgcardona/maestro"
    poll_interval_seconds: int = 5
    github_cache_seconds: int = 10


settings = AgentCeptionSettings()
