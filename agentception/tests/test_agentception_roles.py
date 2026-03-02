"""Tests for the Role file reader/writer API (AC-301).

Covers:
- list_roles returns all managed files that exist on disk
- get_role returns content and meta for a known slug
- get_role returns 404 for an unknown slug
- update_role writes new content to disk
- role_history returns a list of commit dicts

Run targeted:
    pytest agentception/tests/test_agentception_roles.py -v
"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agentception.app import app
from agentception.models import RoleMeta


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Synchronous test client that handles lifespan correctly."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """Create a temp directory that mimics .cursor/roles/ structure with two role files."""
    roles_dir = tmp_path / ".cursor" / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "cto.md").write_text("# CTO\nLeads engineering.", encoding="utf-8")
    (roles_dir / "python-developer.md").write_text(
        "# Python Developer\nWrites code.", encoding="utf-8"
    )
    cursor_dir = tmp_path / ".cursor"
    (cursor_dir / "PARALLEL_ISSUE_TO_PR.md").write_text("# Parallel Issue\n", encoding="utf-8")
    return tmp_path


# ── list_roles ────────────────────────────────────────────────────────────────


def test_list_roles_returns_all_managed_files(
    client: TestClient,
    tmp_repo: Path,
) -> None:
    """GET /api/roles must return one RoleMeta entry per file that exists on disk."""
    with patch("agentception.routes.roles.settings") as mock_settings:
        mock_settings.repo_dir = tmp_repo

        with patch(
            "agentception.routes.roles._git_log_one",
            new=AsyncMock(return_value=("abc123", "initial commit")),
        ):
            response = client.get("/api/roles")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()}
    # Only files that exist in tmp_repo are returned
    assert "cto" in slugs
    assert "python-developer" in slugs
    assert "PARALLEL_ISSUE_TO_PR" in slugs
    # Files not created in tmp_repo are absent
    assert "engineering-manager" not in slugs


# ── get_role ──────────────────────────────────────────────────────────────────


def test_get_role_returns_content_and_meta(
    client: TestClient,
    tmp_repo: Path,
) -> None:
    """GET /api/roles/{slug} must return slug, content, and meta for a known slug."""
    with patch("agentception.routes.roles.settings") as mock_settings:
        mock_settings.repo_dir = tmp_repo

        with patch(
            "agentception.routes.roles._git_log_one",
            new=AsyncMock(return_value=("deadbeef", "feat: add cto role")),
        ):
            response = client.get("/api/roles/cto")

    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "cto"
    assert "CTO" in data["content"]
    meta = data["meta"]
    assert meta["slug"] == "cto"
    assert meta["path"] == ".cursor/roles/cto.md"
    assert meta["line_count"] >= 1
    assert meta["last_commit_sha"] == "deadbeef"
    assert meta["last_commit_message"] == "feat: add cto role"


def test_get_role_unknown_slug_404(
    client: TestClient,
    tmp_repo: Path,
) -> None:
    """GET /api/roles/{slug} must return 404 for a slug not in the managed allowlist."""
    with patch("agentception.routes.roles.settings") as mock_settings:
        mock_settings.repo_dir = tmp_repo

        response = client.get("/api/roles/nonexistent-role")

    assert response.status_code == 404
    assert "nonexistent-role" in response.json()["detail"]


# ── update_role ───────────────────────────────────────────────────────────────


def test_update_role_writes_file(
    client: TestClient,
    tmp_repo: Path,
) -> None:
    """PUT /api/roles/{slug} must write new content to disk and return a diff."""
    new_content = "# CTO (Updated)\nNew responsibilities.\n"

    with patch("agentception.routes.roles.settings") as mock_settings:
        mock_settings.repo_dir = tmp_repo

        with patch(
            "agentception.routes.roles._git_log_one",
            new=AsyncMock(return_value=("", "")),
        ):
            # Patch git diff subprocess so it doesn't require a real git repo
            async def fake_diff(*args: object, **kwargs: object) -> object:
                class FakeProc:
                    returncode = 0

                    async def communicate(self) -> tuple[bytes, bytes]:
                        return b"--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n", b""

                return FakeProc()

            with patch("asyncio.create_subprocess_exec", side_effect=fake_diff):
                response = client.put("/api/roles/cto", json={"content": new_content})

    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "cto"
    assert "diff" in data
    # Verify file was actually written
    written = (tmp_repo / ".cursor" / "roles" / "cto.md").read_text(encoding="utf-8")
    assert written == new_content


# ── role_history ──────────────────────────────────────────────────────────────


def test_role_history_returns_commits(
    client: TestClient,
    tmp_repo: Path,
) -> None:
    """GET /api/roles/{slug}/history must return a list of commit dicts."""
    fake_commits = [
        {"sha": "aaa111", "date": "2026-03-01 12:00:00 +0000", "subject": "feat: role update"},
        {"sha": "bbb222", "date": "2026-02-28 10:00:00 +0000", "subject": "initial commit"},
    ]

    with patch("agentception.routes.roles.settings") as mock_settings:
        mock_settings.repo_dir = tmp_repo

        with patch(
            "agentception.routes.roles._git_log_recent",
            new=AsyncMock(return_value=fake_commits),
        ):
            response = client.get("/api/roles/cto/history")

    assert response.status_code == 200
    history = response.json()
    assert len(history) == 2
    assert history[0]["sha"] == "aaa111"
    assert history[0]["subject"] == "feat: role update"
    assert "date" in history[0]
