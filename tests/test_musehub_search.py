"""Tests for Muse Hub cross-repo global search.

Covers acceptance criteria from issue #236:
- test_global_search_page_renders          — GET /musehub/ui/search returns 200 HTML
- test_global_search_results_grouped       — JSON results are grouped by repo
- test_global_search_public_only           — private repos are excluded
- test_global_search_json                  — JSON content-type returned
- test_global_search_empty_query_handled   — graceful response for empty result set
- test_global_search_requires_auth         — 401 without JWT
- test_global_search_keyword_mode          — keyword mode matches across message terms
- test_global_search_pattern_mode          — pattern mode uses SQL LIKE
- test_global_search_pagination            — page/page_size params respected
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db_session: AsyncSession,
    *,
    name: str = "test-repo",
    visibility: str = "public",
    owner: str = "test-owner",
) -> str:
    """Seed a repo and return its repo_id."""
    repo = MusehubRepo(name=name, visibility=visibility, owner_user_id=owner)
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _make_commit(
    db_session: AsyncSession,
    repo_id: str,
    *,
    commit_id: str,
    message: str,
    author: str = "alice",
    branch: str = "main",
) -> None:
    """Seed a commit attached to the given repo."""
    from datetime import datetime, timezone

    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_ids=[],
        message=message,
        author=author,
        timestamp=datetime.now(tz=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()


# ---------------------------------------------------------------------------
# UI page test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search returns 200 HTML with a search form (no auth required)."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Global Search" in body
    assert "Muse Hub" in body
    assert "q-input" in body
    assert "mode-sel" in body


@pytest.mark.anyio
async def test_global_search_page_pre_fills_query(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search?q=jazz pre-fills the search form with 'jazz'."""
    response = await client.get("/musehub/ui/search?q=jazz&mode=keyword")
    assert response.status_code == 200
    body = response.text
    assert "jazz" in body


# ---------------------------------------------------------------------------
# JSON API tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/search returns 401 without a JWT."""
    response = await client.get("/api/v1/musehub/search?q=jazz")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_global_search_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/search returns JSON with correct content-type."""
    response = await client.get(
        "/api/v1/musehub/search?q=jazz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    assert "groups" in data
    assert "query" in data
    assert data["query"] == "jazz"


@pytest.mark.anyio
async def test_global_search_public_only(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Private repos must not appear in global search results."""
    public_id = await _make_repo(db_session, name="public-beats", visibility="public")
    private_id = await _make_repo(db_session, name="secret-beats", visibility="private")

    await _make_commit(
        db_session, public_id, commit_id="pub001abc", message="jazz groove session"
    )
    await _make_commit(
        db_session, private_id, commit_id="priv001abc", message="jazz private session"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=jazz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    repo_ids_in_results = {g["repoId"] for g in data["groups"]}
    assert public_id in repo_ids_in_results
    assert private_id not in repo_ids_in_results


@pytest.mark.anyio
async def test_global_search_results_grouped(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Results are grouped by repo — each group has repoId, repoName, matches list."""
    repo_a = await _make_repo(db_session, name="repo-alpha", visibility="public")
    repo_b = await _make_repo(db_session, name="repo-beta", visibility="public")

    await _make_commit(
        db_session, repo_a, commit_id="a001abc123", message="bossa nova rhythm"
    )
    await _make_commit(
        db_session, repo_a, commit_id="a002abc123", message="bossa nova variation"
    )
    await _make_commit(
        db_session, repo_b, commit_id="b001abc123", message="bossa nova groove"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=bossa+nova",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    groups = data["groups"]

    # Both repos should appear
    group_repo_ids = {g["repoId"] for g in groups}
    assert repo_a in group_repo_ids
    assert repo_b in group_repo_ids

    # Each group has the required fields
    for group in groups:
        assert "repoId" in group
        assert "repoName" in group
        assert "repoOwner" in group
        assert "repoVisibility" in group
        assert "matches" in group
        assert "totalMatches" in group
        assert isinstance(group["matches"], list)

    # repo_a has 2 matches
    group_a = next(g for g in groups if g["repoId"] == repo_a)
    assert group_a["totalMatches"] == 2
    assert len(group_a["matches"]) == 2


@pytest.mark.anyio
async def test_global_search_empty_query_handled(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """A query that matches nothing returns empty groups and valid pagination metadata."""
    await _make_repo(db_session, name="silent-repo", visibility="public")

    response = await client.get(
        "/api/v1/musehub/search?q=zyxqwvutsr_no_match",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["groups"] == []
    assert data["page"] == 1
    assert "totalReposSearched" in data


@pytest.mark.anyio
async def test_global_search_keyword_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Keyword mode matches any term in the query (OR logic, case-insensitive)."""
    repo_id = await _make_repo(db_session, name="jazz-lab", visibility="public")
    await _make_commit(
        db_session, repo_id, commit_id="kw001abcde", message="Blues Shuffle in E"
    )
    await _make_commit(
        db_session, repo_id, commit_id="kw002abcde", message="Jazz Waltz Trio"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=blues&mode=keyword",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    messages = [m["message"] for m in group["matches"]]
    assert any("Blues" in msg for msg in messages)


@pytest.mark.anyio
async def test_global_search_pattern_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode applies a raw SQL LIKE pattern to commit messages."""
    repo_id = await _make_repo(db_session, name="pattern-lab", visibility="public")
    await _make_commit(
        db_session, repo_id, commit_id="pt001abcde", message="minor pentatonic run"
    )
    await _make_commit(
        db_session, repo_id, commit_id="pt002abcde", message="major scale exercise"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=%25minor%25&mode=pattern",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    assert group["totalMatches"] == 1
    assert "minor" in group["matches"][0]["message"]


@pytest.mark.anyio
async def test_global_search_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """page and page_size parameters control repo-group pagination."""
    # Create 3 public repos each with a matching commit
    ids = []
    for i in range(3):
        rid = await _make_repo(
            db_session, name=f"paged-repo-{i}", visibility="public", owner=f"owner-{i}"
        )
        ids.append(rid)
        await _make_commit(
            db_session, rid, commit_id=f"pg{i:03d}abcde", message="paginate funk groove"
        )

    # page_size=2 → first page returns at most 2 groups
    response = await client.get(
        "/api/v1/musehub/search?q=paginate&page=1&page_size=2",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["groups"]) <= 2
    assert data["page"] == 1
    assert data["pageSize"] == 2

    # page=2 returns the remaining group(s)
    response2 = await client.get(
        "/api/v1/musehub/search?q=paginate&page=2&page_size=2",
        headers=auth_headers,
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["page"] == 2


@pytest.mark.anyio
async def test_global_search_match_contains_required_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Each match entry contains commitId, message, author, branch, timestamp, repoId."""
    repo_id = await _make_repo(db_session, name="fields-check", visibility="public")
    await _make_commit(
        db_session,
        repo_id,
        commit_id="fc001abcde",
        message="swing feel experiment",
        author="charlie",
        branch="main",
    )

    response = await client.get(
        "/api/v1/musehub/search?q=swing",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    match = group["matches"][0]
    assert match["commitId"] == "fc001abcde"
    assert match["message"] == "swing feel experiment"
    assert match["author"] == "charlie"
    assert match["branch"] == "main"
    assert "timestamp" in match
    assert match["repoId"] == repo_id
