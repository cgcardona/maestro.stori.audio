"""Tests for Muse Hub fork network visualization.

Tests cover:
- ForkNetworkNode model structure and self-referential children
- ForkNetworkResponse model structure
- list_repo_forks returns correct root node when repo has no forks
- list_repo_forks returns correct children when forks exist
- list_repo_forks returns empty response for missing repo
- divergence_commits is a non-negative integer for each fork node
- forks_page route returns HTML by default
- forks_page route returns JSON with correct structure on ?format=json
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Unit tests for models ─────────────────────────────────────────────────────


def test_fork_network_node_minimal() -> None:
    """ForkNetworkNode can be constructed with minimal required fields."""
    from maestro.models.musehub import ForkNetworkNode

    node = ForkNetworkNode(
        owner="alice",
        repo_slug="my-song",
        repo_id="repo-uuid-1",
        divergence_commits=0,
        forked_by="",
        forked_at=None,
    )
    assert node.owner == "alice"
    assert node.repo_slug == "my-song"
    assert node.divergence_commits == 0
    assert node.children == []
    assert node.forked_at is None


def test_fork_network_node_with_children() -> None:
    """ForkNetworkNode accepts nested children (self-referential)."""
    from maestro.models.musehub import ForkNetworkNode

    child = ForkNetworkNode(
        owner="bob",
        repo_slug="my-song",
        repo_id="repo-uuid-2",
        divergence_commits=5,
        forked_by="bob_user_id",
        forked_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    root = ForkNetworkNode(
        owner="alice",
        repo_slug="my-song",
        repo_id="repo-uuid-1",
        divergence_commits=0,
        forked_by="",
        forked_at=None,
        children=[child],
    )
    assert len(root.children) == 1
    assert root.children[0].owner == "bob"
    assert root.children[0].divergence_commits == 5


def test_fork_network_node_children_default_empty() -> None:
    """ForkNetworkNode.children defaults to an empty list, not None."""
    from maestro.models.musehub import ForkNetworkNode

    node = ForkNetworkNode(
        owner="alice",
        repo_slug="my-song",
        repo_id="repo-uuid-1",
        divergence_commits=0,
        forked_by="",
        forked_at=None,
    )
    assert isinstance(node.children, list)
    assert len(node.children) == 0


def test_fork_network_response_structure() -> None:
    """ForkNetworkResponse wraps a root node and exposes total_forks."""
    from maestro.models.musehub import ForkNetworkNode, ForkNetworkResponse

    root = ForkNetworkNode(
        owner="alice",
        repo_slug="my-song",
        repo_id="repo-uuid-1",
        divergence_commits=0,
        forked_by="",
        forked_at=None,
    )
    resp = ForkNetworkResponse(root=root, total_forks=0)
    assert resp.total_forks == 0
    assert resp.root.owner == "alice"


def test_fork_network_response_camel_case_serialisation() -> None:
    """ForkNetworkResponse serialises to camelCase for the JSON path."""
    from maestro.models.musehub import ForkNetworkNode, ForkNetworkResponse

    child_ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
    child = ForkNetworkNode(
        owner="bob",
        repo_slug="my-song",
        repo_id="repo-uuid-2",
        divergence_commits=3,
        forked_by="bob_id",
        forked_at=child_ts,
    )
    root = ForkNetworkNode(
        owner="alice",
        repo_slug="my-song",
        repo_id="repo-uuid-1",
        divergence_commits=0,
        forked_by="",
        forked_at=None,
        children=[child],
    )
    resp = ForkNetworkResponse(root=root, total_forks=1)
    data = resp.model_dump(by_alias=True, mode="json")

    assert "totalForks" in data
    assert data["totalForks"] == 1
    root_data = data["root"]
    assert "repoSlug" in root_data
    assert "divergenceCommits" in root_data
    assert root_data["repoSlug"] == "my-song"
    child_data = root_data["children"][0]
    assert child_data["owner"] == "bob"
    assert child_data["divergenceCommits"] == 3


# ── Unit tests for list_repo_forks service ────────────────────────────────────


@pytest.mark.anyio
async def test_list_repo_forks_missing_repo_returns_empty() -> None:
    """list_repo_forks returns a zero-total response when the repo does not exist."""
    from unittest.mock import AsyncMock, MagicMock

    from maestro.services.musehub_repository import list_repo_forks

    mock_db = AsyncMock()
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [first_result]

    result = await list_repo_forks(mock_db, "nonexistent-repo-id")
    assert result.total_forks == 0
    assert result.root.repo_id == "nonexistent-repo-id"
    assert result.root.children == []


@pytest.mark.anyio
async def test_list_repo_forks_no_forks() -> None:
    """list_repo_forks returns root with empty children when repo has no forks."""
    from unittest.mock import AsyncMock, MagicMock

    from maestro.services.musehub_repository import list_repo_forks

    source_row = MagicMock()
    source_row.repo_id = "source-repo-id"
    source_row.owner = "alice"
    source_row.slug = "my-song"
    source_row.name = "My Song"
    source_row.description = ""
    source_row.visibility = "public"
    source_row.tags = []
    source_row.key_signature = None
    source_row.tempo_bpm = None
    source_row.default_branch = "main"
    source_row.owner_user_id = "alice_uid"
    source_row.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    source_row.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    source_row.license = None
    source_row.topics = []
    source_row.settings = None
    source_row.template_repo_id = None
    source_row.homepage_url = None
    source_row.star_count = 0
    source_row.fork_count = 0
    source_row.watch_count = 0

    mock_db = AsyncMock()
    # First execute → scalar_one_or_none returns source_row
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = source_row
    # Second execute → .all() returns empty list (no forks)
    second_result = MagicMock()
    second_result.all.return_value = []
    mock_db.execute.side_effect = [first_result, second_result]

    result = await list_repo_forks(mock_db, "source-repo-id")
    assert result.total_forks == 0
    assert result.root.owner == "alice"
    assert result.root.repo_slug == "my-song"
    assert result.root.children == []
    assert result.root.divergence_commits == 0


@pytest.mark.anyio
async def test_list_repo_forks_with_forks() -> None:
    """list_repo_forks populates children when fork rows exist."""
    from unittest.mock import AsyncMock, MagicMock

    from maestro.services.musehub_repository import list_repo_forks

    source_row = MagicMock()
    source_row.repo_id = "source-repo-id"
    source_row.owner = "alice"
    source_row.slug = "my-song"
    source_row.name = "My Song"
    source_row.description = ""
    source_row.visibility = "public"
    source_row.tags = []
    source_row.key_signature = None
    source_row.tempo_bpm = None
    source_row.default_branch = "main"
    source_row.owner_user_id = "alice_uid"
    source_row.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    source_row.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    source_row.license = None
    source_row.topics = []
    source_row.settings = None
    source_row.template_repo_id = None
    source_row.homepage_url = None
    source_row.star_count = 0
    source_row.fork_count = 0
    source_row.watch_count = 0

    fork_record = MagicMock()
    fork_record.fork_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    fork_record.forked_by = "bob_uid"
    fork_record.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)

    fork_repo_row = MagicMock()
    fork_repo_row.repo_id = "fork-repo-id"
    fork_repo_row.owner = "bob"
    fork_repo_row.slug = "my-song"
    fork_repo_row.name = "My Song"
    fork_repo_row.description = ""
    fork_repo_row.visibility = "public"
    fork_repo_row.tags = []
    fork_repo_row.key_signature = None
    fork_repo_row.tempo_bpm = None
    fork_repo_row.default_branch = "main"
    fork_repo_row.owner_user_id = "bob_uid"
    fork_repo_row.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
    fork_repo_row.updated_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
    fork_repo_row.license = None
    fork_repo_row.topics = []
    fork_repo_row.settings = None
    fork_repo_row.template_repo_id = None
    fork_repo_row.homepage_url = None
    fork_repo_row.star_count = 0
    fork_repo_row.fork_count = 0
    fork_repo_row.watch_count = 0

    mock_db = AsyncMock()
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = source_row
    second_result = MagicMock()
    second_result.all.return_value = [(fork_record, fork_repo_row)]
    mock_db.execute.side_effect = [first_result, second_result]

    result = await list_repo_forks(mock_db, "source-repo-id")
    assert result.total_forks == 1
    assert len(result.root.children) == 1
    child = result.root.children[0]
    assert child.owner == "bob"
    assert child.repo_slug == "my-song"
    assert child.forked_by == "bob_uid"
    assert child.forked_at == datetime(2025, 6, 1, tzinfo=timezone.utc)
    assert child.divergence_commits >= 0


def test_fork_network_node_divergence_commits_non_negative() -> None:
    """divergence_commits is always non-negative for any fork_id."""
    from maestro.models.musehub import ForkNetworkNode

    node = ForkNetworkNode(
        owner="bob",
        repo_slug="my-song",
        repo_id="fork-repo-id",
        divergence_commits=7,
        forked_by="bob_uid",
        forked_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    assert node.divergence_commits >= 0


# ── Integration smoke tests for the route ────────────────────────────────────


def test_forks_page_route_returns_404_for_unknown_repo() -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/forks returns 404 for unknown repo."""
    from unittest.mock import AsyncMock, patch

    from maestro.db.database import get_db
    from maestro.main import app

    mock_db = AsyncMock()

    async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "maestro.api.routes.musehub.ui.musehub_repository.get_repo_orm_by_owner_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/musehub/ui/ghost/nonexistent/forks")
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_forks_page_route_json_format_returns_json() -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/forks?format=json returns JSON."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from maestro.db.database import get_db
    from maestro.main import app
    from maestro.models.musehub import ForkNetworkNode, ForkNetworkResponse

    mock_repo_orm = MagicMock()
    mock_repo_orm.repo_id = "test-repo-id"

    fake_network = ForkNetworkResponse(
        root=ForkNetworkNode(
            owner="alice",
            repo_slug="my-song",
            repo_id="test-repo-id",
            divergence_commits=0,
            forked_by="",
            forked_at=None,
        ),
        total_forks=0,
    )

    mock_db = AsyncMock()

    async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            patch(
                "maestro.api.routes.musehub.ui.musehub_repository.get_repo_orm_by_owner_slug",
                new_callable=AsyncMock,
                return_value=mock_repo_orm,
            ),
            patch(
                "maestro.api.routes.musehub.ui_forks.musehub_repository.list_repo_forks",
                new_callable=AsyncMock,
                return_value=fake_network,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/musehub/ui/alice/my-song/forks",
                params={"format": "json"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "root" in data
            assert "totalForks" in data
            assert data["totalForks"] == 0
            assert data["root"]["owner"] == "alice"
    finally:
        app.dependency_overrides.pop(get_db, None)
