"""Tests for Muse Hub in-repo search — issue #235.

Acceptance criteria verified:
- test_search_page_renders              — GET /musehub/ui/{repo_id}/search → 200 HTML
- test_search_keyword_mode              — keyword search returns matching commits
- test_search_keyword_empty_query       — empty keyword query returns empty matches
- test_search_musical_property          — musical property filter works
- test_search_natural_language          — ask mode returns matching commits
- test_search_pattern_message           — pattern matches commit message
- test_search_pattern_branch            — pattern matches branch name
- test_search_json_response             — JSON search endpoint returns SearchResponse shape
- test_search_date_range_since          — since filter excludes old commits
- test_search_date_range_until          — until filter excludes future commits
- test_search_invalid_mode              — invalid mode returns 422
- test_search_unknown_repo              — unknown repo_id returns 404
- test_search_requires_auth             — unauthenticated request returns 401
- test_search_limit_respected           — limit caps result count
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a minimal MuseHub repo; return repo_id."""
    repo = MusehubRepo(
        name="search-test-repo",
        visibility="private",
        owner_user_id="test-owner",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_snapshot(db: AsyncSession, snapshot_id: str) -> None:
    """Seed a minimal snapshot so FK constraint on commits is satisfied."""
    snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest={})
    db.add(snap)
    await db.flush()


async def _make_commit(
    db: AsyncSession,
    *,
    repo_id: str,
    message: str,
    branch: str = "main",
    author: str = "test-author",
    committed_at: datetime | None = None,
) -> MuseCliCommit:
    """Seed a single commit for search tests."""
    snap_id = "snap-" + str(uuid.uuid4()).replace("-", "")[:16]
    await _make_snapshot(db, snap_id)
    commit = MuseCliCommit(
        commit_id=str(uuid.uuid4()).replace("-", ""),
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snap_id,
        message=message,
        author=author,
        committed_at=committed_at or datetime.now(timezone.utc),
    )
    db.add(commit)
    await db.flush()
    return commit


# ---------------------------------------------------------------------------
# UI page test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/search returns 200 HTML with mode tabs."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Search Commits" in body
    # All four mode tab labels must be present
    assert "Keyword" in body
    assert "Natural Language" in body
    assert "Pattern" in body
    assert "Musical Properties" in body
    # Date range inputs
    assert "inp-since" in body
    assert "inp-until" in body


@pytest.mark.anyio
async def test_search_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Search UI page is accessible without a JWT (HTML shell, JS handles auth)."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/search")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# JSON search API — authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/search returns 401 without a token."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_search_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/search returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/search?mode=keyword&q=test",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_search_invalid_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET search with an unknown mode returns 422."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=badmode&q=x",
        headers=auth_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# JSON search API — keyword mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_keyword_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Keyword search returns commits whose messages overlap with the query."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="dark jazz bassline in Dm")
    await _make_commit(db_session, repo_id=repo_id, message="classical piano intro section")
    await _make_commit(db_session, repo_id=repo_id, message="hip hop drum fill pattern")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz+bassline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "keyword"
    assert data["query"] == "jazz bassline"
    # The jazz bassline commit must match; the others may or may not
    assert any("jazz" in m["message"].lower() for m in data["matches"])


@pytest.mark.anyio
async def test_search_keyword_empty_query(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Empty keyword query returns empty matches (no tokens → no overlap)."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()
    await _make_commit(db_session, repo_id=repo_id, message="some commit")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "keyword"
    assert data["matches"] == []


@pytest.mark.anyio
async def test_search_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Search response has the expected SearchResponse JSON shape."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()
    await _make_commit(db_session, repo_id=repo_id, message="piano chord progression F Bb Eb")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=piano",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    # Verify top-level envelope fields
    assert "mode" in data
    assert "query" in data
    assert "matches" in data
    assert "totalScanned" in data
    assert "limit" in data

    # Verify commit-match field shape
    if data["matches"]:
        m = data["matches"][0]
        assert "commitId" in m
        assert "branch" in m
        assert "message" in m
        assert "author" in m
        assert "timestamp" in m
        assert "score" in m
        assert "matchSource" in m


# ---------------------------------------------------------------------------
# JSON search API — musical property mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_musical_property(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Property mode filters commits containing the harmony string."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="add harmony=Eb bridge section")
    await _make_commit(db_session, repo_id=repo_id, message="drum groove tweak no harmony")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=property&harmony=Eb",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "property"
    assert len(data["matches"]) >= 1
    assert all("Eb" in m["message"] for m in data["matches"])


# ---------------------------------------------------------------------------
# JSON search API — natural language (ask) mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_natural_language(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Ask mode extracts keywords and returns relevant commits."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="switched tempo to 140bpm for drop")
    await _make_commit(db_session, repo_id=repo_id, message="piano melody in minor key")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=ask&q=what+tempo+changes+did+I+make",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ask"
    # The tempo commit should match; keywords extracted: "tempo", "changes"
    assert any("tempo" in m["message"].lower() for m in data["matches"])


# ---------------------------------------------------------------------------
# JSON search API — pattern mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_pattern_message(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode matches substring in commit message."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="add Cm7 chord voicing in bridge")
    await _make_commit(db_session, repo_id=repo_id, message="fix timing on verse drums")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=pattern&q=Cm7",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "pattern"
    assert len(data["matches"]) == 1
    assert "Cm7" in data["matches"][0]["message"]
    assert data["matches"][0]["matchSource"] == "message"


@pytest.mark.anyio
async def test_search_pattern_branch(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode matches substring in branch name when message doesn't match."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(
        db_session,
        repo_id=repo_id,
        message="rough cut",
        branch="feature/hip-hop-session",
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=pattern&q=hip-hop",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "pattern"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["matchSource"] == "branch"


# ---------------------------------------------------------------------------
# JSON search API — date range filters
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_date_range_since(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """since filter excludes commits committed before the given datetime."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_commit(db_session, repo_id=repo_id, message="old jazz commit", committed_at=old_ts)
    await _make_commit(db_session, repo_id=repo_id, message="new jazz commit", committed_at=new_ts)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz&since=2025-06-01T00:00:00Z",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert all(m["message"] != "old jazz commit" for m in data["matches"])
    assert any(m["message"] == "new jazz commit" for m in data["matches"])


@pytest.mark.anyio
async def test_search_date_range_until(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """until filter excludes commits committed after the given datetime."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_commit(db_session, repo_id=repo_id, message="old piano commit", committed_at=old_ts)
    await _make_commit(db_session, repo_id=repo_id, message="new piano commit", committed_at=new_ts)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=piano&until=2025-06-01T00:00:00Z",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert any(m["message"] == "old piano commit" for m in data["matches"])
    assert all(m["message"] != "new piano commit" for m in data["matches"])


# ---------------------------------------------------------------------------
# JSON search API — limit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_limit_respected(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """The limit parameter caps the number of results returned."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    for i in range(10):
        await _make_commit(db_session, repo_id=repo_id, message=f"bass groove iteration {i}")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=bass&limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["matches"]) <= 3
    assert data["limit"] == 3
