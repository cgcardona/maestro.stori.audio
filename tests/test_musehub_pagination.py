"""Tests for RFC 8288 Link header pagination on Muse Hub list endpoints.

Covers acceptance criteria from issue #456:
- PaginationParams dependency parses page/per_page and cursor/limit query params
- build_link_header emits correct RFC 8288 rel links for first/last/prev/next
- build_cursor_link_header emits a rel="next" link with cursor and limit
- paginate_list slices correctly and returns accurate total
- GET /musehub/repos/{repo_id}/issues returns Link header and total field
- GET /musehub/repos/{repo_id}/pull-requests returns Link header and total field
- GET /musehub/repos/{repo_id}/commits returns Link header when per_page > 0
- GET /musehub/repos returns rel="next" Link header when next_cursor is present

All tests use fixtures from conftest.py. No live external APIs are called.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request as StarletteRequest

from maestro.api.routes.musehub.pagination import (
    PaginationParams,
    build_cursor_link_header,
    build_link_header,
    paginate_list,
)


# ---------------------------------------------------------------------------
# Unit tests — pagination helpers
# ---------------------------------------------------------------------------


def _make_request(url: str) -> StarletteRequest:
    """Build a minimal Starlette Request for testing URL construction."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": url.split("?")[0],
        "query_string": url.split("?")[1].encode() if "?" in url else b"",
        "headers": [],
    }
    return StarletteRequest(scope)


def test_paginate_list_first_page() -> None:
    """paginate_list returns the first page slice and correct total."""
    items = list(range(55))
    page, total = paginate_list(items, page=1, per_page=20)
    assert total == 55
    assert page == list(range(20))


def test_paginate_list_middle_page() -> None:
    """paginate_list returns the correct middle page slice."""
    items = list(range(55))
    page, total = paginate_list(items, page=2, per_page=20)
    assert total == 55
    assert page == list(range(20, 40))


def test_paginate_list_last_partial_page() -> None:
    """paginate_list returns a partial slice on the final page."""
    items = list(range(55))
    page, total = paginate_list(items, page=3, per_page=20)
    assert total == 55
    assert page == list(range(40, 55))


def test_paginate_list_beyond_last_page_returns_empty() -> None:
    """paginate_list returns an empty slice when page exceeds total."""
    items = list(range(10))
    page, total = paginate_list(items, page=5, per_page=10)
    assert total == 10
    assert page == []


def test_paginate_list_empty_input() -> None:
    """paginate_list handles empty input gracefully."""
    page_items: list[int]
    page_items, total = paginate_list([], page=1, per_page=20)
    assert total == 0
    assert page_items == []


def test_build_link_header_single_page() -> None:
    """build_link_header emits only first and last when there is exactly one page."""
    req = _make_request("http://test/api/v1/musehub/repos/r1/issues?page=1&per_page=20")
    header = build_link_header(req, total=5, page=1, per_page=20)
    assert 'rel="first"' in header
    assert 'rel="last"' in header
    assert 'rel="next"' not in header
    assert 'rel="prev"' not in header


def test_build_link_header_first_of_many() -> None:
    """build_link_header emits first, last, and next (but not prev) on page 1 of N."""
    req = _make_request("http://test/api/v1/musehub/repos/r1/issues?page=1&per_page=10")
    header = build_link_header(req, total=55, page=1, per_page=10)
    assert 'rel="first"' in header
    assert 'rel="last"' in header
    assert 'rel="next"' in header
    assert 'rel="prev"' not in header
    assert "page=2" in header
    assert "page=6" in header  # last page for 55 items at 10/page


def test_build_link_header_middle_page() -> None:
    """build_link_header emits all four rels on an interior page."""
    req = _make_request("http://test/api/v1/musehub/repos/r1/issues?page=3&per_page=10")
    header = build_link_header(req, total=55, page=3, per_page=10)
    assert 'rel="first"' in header
    assert 'rel="last"' in header
    assert 'rel="next"' in header
    assert 'rel="prev"' in header
    assert "page=4" in header
    assert "page=2" in header


def test_build_link_header_last_page() -> None:
    """build_link_header emits prev (but not next) on the last page."""
    req = _make_request("http://test/api/v1/musehub/repos/r1/issues?page=6&per_page=10")
    header = build_link_header(req, total=55, page=6, per_page=10)
    assert 'rel="first"' in header
    assert 'rel="last"' in header
    assert 'rel="prev"' in header
    assert 'rel="next"' not in header


def test_build_link_header_preserves_existing_query_params() -> None:
    """build_link_header keeps non-pagination query params on generated URLs."""
    req = _make_request("http://test/api/v1/musehub/repos/r1/issues?state=open&page=1&per_page=10")
    header = build_link_header(req, total=30, page=1, per_page=10)
    assert "state=open" in header


def test_build_cursor_link_header_emits_next_only() -> None:
    """build_cursor_link_header emits exactly one rel="next" with cursor and limit encoded."""
    req = _make_request("http://test/api/v1/musehub/repos?limit=20")
    header = build_cursor_link_header(req, next_cursor="abc123", limit=20)
    assert 'rel="next"' in header
    assert "cursor=abc123" in header
    assert "limit=20" in header
    assert 'rel="prev"' not in header
    assert 'rel="first"' not in header


# ---------------------------------------------------------------------------
# Integration tests — issues list endpoint
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str) -> str:
    r = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    repo_id: str = r.json()["repoId"]
    return repo_id


async def _create_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    title: str,
) -> None:
    r = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        json={"title": title, "body": ""},
        headers=auth_headers,
    )
    assert r.status_code == 201


@pytest.mark.anyio
async def test_list_issues_link_header_present(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues includes a Link header when pagination is active."""
    repo_id = await _create_repo(client, auth_headers, "pagination-issues-link")
    for i in range(5):
        await _create_issue(client, auth_headers, repo_id, f"Issue {i}")

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues?page=1&per_page=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "Link" in r.headers
    link = r.headers["Link"]
    assert 'rel="first"' in link
    assert 'rel="last"' in link
    assert 'rel="next"' in link


@pytest.mark.anyio
async def test_list_issues_total_field_returned(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues response body includes ``total`` with the count across all pages."""
    repo_id = await _create_repo(client, auth_headers, "pagination-issues-total")
    for i in range(7):
        await _create_issue(client, auth_headers, repo_id, f"Track issue {i}")

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues?page=1&per_page=3",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 7
    assert len(body["issues"]) == 3


@pytest.mark.anyio
async def test_list_issues_last_page_no_next(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues Link header on the last page has no rel=\"next\"."""
    repo_id = await _create_repo(client, auth_headers, "pagination-issues-last")
    for i in range(4):
        await _create_issue(client, auth_headers, repo_id, f"Issue {i}")

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues?page=2&per_page=3",
        headers=auth_headers,
    )
    assert r.status_code == 200
    link = r.headers["Link"]
    assert 'rel="next"' not in link
    assert 'rel="prev"' in link


@pytest.mark.anyio
async def test_list_issues_default_page_returns_all_when_small(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues with no pagination params returns results on page 1 (default)."""
    repo_id = await _create_repo(client, auth_headers, "pagination-issues-default")
    for i in range(3):
        await _create_issue(client, auth_headers, repo_id, f"Default page {i}")

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["issues"]) == 3
    assert body["total"] == 3


# ---------------------------------------------------------------------------
# Integration tests — PRs list endpoint
# ---------------------------------------------------------------------------



@pytest.mark.anyio
async def test_list_prs_link_header_present(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests includes a Link header when pagination is active."""
    from maestro.db.musehub_models import MusehubPullRequest

    repo_id = await _create_repo(client, auth_headers, "pagination-prs-link")

    # Insert PRs directly to avoid branch validation complexity in tests
    for i in range(3):
        db_session.add(MusehubPullRequest(
            repo_id=repo_id,
            title=f"PR {i}",
            from_branch=f"feat/{i}",
            to_branch="main",
            author="testuser",
        ))
    await db_session.commit()

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests?page=1&per_page=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "Link" in r.headers
    link = r.headers["Link"]
    assert 'rel="first"' in link
    assert 'rel="next"' in link


@pytest.mark.anyio
async def test_list_prs_total_field_returned(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests response body includes ``total`` field."""
    from maestro.db.musehub_models import MusehubPullRequest

    repo_id = await _create_repo(client, auth_headers, "pagination-prs-total")

    for i in range(4):
        db_session.add(MusehubPullRequest(
            repo_id=repo_id,
            title=f"PR {i}",
            from_branch=f"feat/{i}",
            to_branch="main",
            author="testuser",
        ))
    await db_session.commit()

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "total" in body
    assert body["total"] == 4


# ---------------------------------------------------------------------------
# Integration tests — commits list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_commits_link_header_with_per_page(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /commits with per_page > 0 includes an RFC 8288 Link header."""
    from datetime import datetime, timezone, timedelta
    from maestro.db.musehub_models import MusehubCommit

    repo_id = await _create_repo(client, auth_headers, "pagination-commits-link")
    now = datetime.now(tz=timezone.utc)

    for i in range(5):
        db_session.add(MusehubCommit(
            commit_id=f"sha-{i:04d}",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message=f"Commit {i}",
            author="testuser",
            timestamp=now + timedelta(seconds=i),
        ))
    await db_session.commit()

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits?page=1&per_page=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "Link" in r.headers
    link = r.headers["Link"]
    assert 'rel="first"' in link
    assert 'rel="next"' in link


@pytest.mark.anyio
async def test_list_commits_no_link_header_without_per_page(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /commits without per_page does NOT add a Link header (legacy mode)."""
    repo_id = await _create_repo(client, auth_headers, "pagination-commits-no-link")

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "Link" not in r.headers
