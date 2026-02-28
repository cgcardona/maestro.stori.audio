"""Tests for Muse Hub issue tracking endpoints.

Covers every acceptance criterion from issue #42:
- POST /musehub/repos/{repo_id}/issues creates an issue in open state
- Issue numbers are sequential per repo starting at 1
- GET /musehub/repos/{repo_id}/issues returns open issues by default
- GET .../issues?label=<label> filters by label
- POST .../issues/{number}/close sets state to closed
- GET .../issues/{number} returns 404 for unknown issue numbers
- All endpoints require valid JWT

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services import musehub_repository, musehub_issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "test-repo") -> str:
    """Create a repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _create_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    title: str = "Kick clashes with bass in measure 4",
    body: str = "",
    labels: list[str] | None = None,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        json={"title": title, "body": body, "labels": labels or []},
        headers=auth_headers,
    )
    assert response.status_code == 201
    issue: dict[str, object] = response.json()
    return issue


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/issues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_issue_returns_open_state(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /issues creates an issue in 'open' state with all required fields."""
    repo_id = await _create_repo(client, auth_headers, "open-state-repo")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        json={"title": "Hi-hat / synth pad clash", "body": "Measure 8 has a frequency clash.", "labels": ["bug"]},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "open"
    assert body["title"] == "Hi-hat / synth pad clash"
    assert body["labels"] == ["bug"]
    assert "issueId" in body
    assert "number" in body
    assert "createdAt" in body


@pytest.mark.anyio
async def test_issue_numbers_sequential(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Issue numbers within a repo are sequential starting at 1."""
    repo_id = await _create_repo(client, auth_headers, "seq-repo")

    first = await _create_issue(client, auth_headers, repo_id, title="First issue")
    second = await _create_issue(client, auth_headers, repo_id, title="Second issue")
    third = await _create_issue(client, auth_headers, repo_id, title="Third issue")

    assert first["number"] == 1
    assert second["number"] == 2
    assert third["number"] == 3


@pytest.mark.anyio
async def test_issue_numbers_independent_per_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Issue numbers restart at 1 for each repo independently."""
    repo_a = await _create_repo(client, auth_headers, "repo-a")
    repo_b = await _create_repo(client, auth_headers, "repo-b")

    issue_a = await _create_issue(client, auth_headers, repo_a, title="Repo A issue")
    issue_b = await _create_issue(client, auth_headers, repo_b, title="Repo B issue")

    assert issue_a["number"] == 1
    assert issue_b["number"] == 1


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/issues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_issues_default_open_only(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues with no params returns only open issues."""
    repo_id = await _create_repo(client, auth_headers, "default-open-repo")
    await _create_issue(client, auth_headers, repo_id, title="Open issue")

    # Create a second issue and close it
    issue = await _create_issue(client, auth_headers, repo_id, title="Closed issue")
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue['number']}/close",
        headers=auth_headers,
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        headers=auth_headers,
    )
    assert response.status_code == 200
    issues = response.json()["issues"]
    assert len(issues) == 1
    assert issues[0]["state"] == "open"


@pytest.mark.anyio
async def test_list_issues_state_all_returns_all(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """?state=all returns both open and closed issues."""
    repo_id = await _create_repo(client, auth_headers, "state-all-repo")
    await _create_issue(client, auth_headers, repo_id, title="Open issue")
    issue = await _create_issue(client, auth_headers, repo_id, title="To close")
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue['number']}/close",
        headers=auth_headers,
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues?state=all",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert len(response.json()["issues"]) == 2


@pytest.mark.anyio
async def test_list_issues_label_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues?label=bug returns only issues that have the 'bug' label."""
    repo_id = await _create_repo(client, auth_headers, "label-filter-repo")
    await _create_issue(client, auth_headers, repo_id, title="Bug issue", labels=["bug"])
    await _create_issue(client, auth_headers, repo_id, title="Feature issue", labels=["feature"])
    await _create_issue(client, auth_headers, repo_id, title="Multi-label", labels=["bug", "musical"])

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues?label=bug",
        headers=auth_headers,
    )
    assert response.status_code == 200
    issues = response.json()["issues"]
    assert len(issues) == 2
    for issue in issues:
        assert "bug" in issue["labels"]


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/issues/{issue_number}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_issue_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues/{number} returns 404 for a number that doesn't exist."""
    repo_id = await _create_repo(client, auth_headers, "not-found-repo")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues/999",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_issue_returns_full_object(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /issues/{number} returns the full issue object."""
    repo_id = await _create_repo(client, auth_headers, "get-issue-repo")
    created = await _create_issue(
        client, auth_headers, repo_id,
        title="Delay tail bleeds into next section",
        body="The reverb tail from the bridge extends 200ms into the verse.",
        labels=["musical", "mix"],
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/issues/{created['number']}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["issueId"] == created["issueId"]
    assert body["title"] == "Delay tail bleeds into next section"
    assert body["body"] == "The reverb tail from the bridge extends 200ms into the verse."
    assert body["labels"] == ["musical", "mix"]


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/issues/{issue_number}/close
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_issue_changes_state(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /issues/{number}/close sets the issue state to 'closed'."""
    repo_id = await _create_repo(client, auth_headers, "close-state-repo")
    issue = await _create_issue(client, auth_headers, repo_id, title="Clipping at measure 12")
    assert issue["state"] == "open"

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue['number']}/close",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["state"] == "closed"


@pytest.mark.anyio
async def test_close_nonexistent_issue_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /issues/999/close returns 404 for an unknown issue number."""
    repo_id = await _create_repo(client, auth_headers, "close-404-repo")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/999/close",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issues_require_auth(client: AsyncClient) -> None:
    """All issue endpoints return 401 without a Bearer token."""
    endpoints = [
        ("POST", "/api/v1/musehub/repos/some-repo/issues"),
        ("GET", "/api/v1/musehub/repos/some-repo/issues"),
        ("GET", "/api/v1/musehub/repos/some-repo/issues/1"),
        ("POST", "/api/v1/musehub/repos/some-repo/issues/1/close"),
    ]
    for method, url in endpoints:
        if method == "POST":
            response = await client.post(url, json={})
        else:
            response = await client.get(url)
        assert response.status_code == 401, f"{method} {url} should require auth"


# ---------------------------------------------------------------------------
# Service layer — direct DB tests (no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_issue_service_persists_to_db(db_session: AsyncSession) -> None:
    """musehub_issues.create_issue() persists the row and returns correct fields."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="service-issue-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-abc",
    )
    await db_session.commit()

    issue = await musehub_issues.create_issue(
        db_session,
        repo_id=repo.repo_id,
        title="Bass note timing drift",
        body="Measure 4, beat 3 — bass is 10ms late.",
        labels=["timing", "bass"],
    )
    await db_session.commit()

    fetched = await musehub_issues.get_issue(db_session, repo.repo_id, issue.number)
    assert fetched is not None
    assert fetched.title == "Bass note timing drift"
    assert fetched.state == "open"
    assert fetched.labels == ["timing", "bass"]
    assert fetched.number == 1


@pytest.mark.anyio
async def test_list_issues_closed_state_filter(db_session: AsyncSession) -> None:
    """list_issues() with state='closed' returns only closed issues."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="filter-state-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-xyz",
    )
    await db_session.commit()

    open_issue = await musehub_issues.create_issue(
        db_session, repo_id=repo.repo_id, title="Still open", body="", labels=[]
    )
    closed_issue = await musehub_issues.create_issue(
        db_session, repo_id=repo.repo_id, title="Already closed", body="", labels=[]
    )
    await musehub_issues.close_issue(db_session, repo.repo_id, closed_issue.number)
    await db_session.commit()

    open_list = await musehub_issues.list_issues(db_session, repo.repo_id, state="open")
    closed_list = await musehub_issues.list_issues(db_session, repo.repo_id, state="closed")
    all_list = await musehub_issues.list_issues(db_session, repo.repo_id, state="all")

    assert len(open_list) == 1
    assert open_list[0].issue_id == open_issue.issue_id
    assert len(closed_list) == 1
    assert closed_list[0].issue_id == closed_issue.issue_id
    assert len(all_list) == 2
