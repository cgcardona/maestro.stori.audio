"""Tests for Muse Hub RSS/Atom feed endpoints.

Covers every acceptance criterion from issue #430:
- GET /musehub/repos/{repo_id}/feed.rss     — RSS 2.0 commit feed
- GET /musehub/repos/{repo_id}/releases.rss — RSS 2.0 releases feed
- GET /musehub/repos/{repo_id}/issues.rss   — RSS 2.0 open-issues feed
- GET /musehub/repos/{repo_id}/feed.atom    — Atom 1.0 commit feed
- Public repos return 200 with correct Content-Type
- Private repos return 403 (feed readers cannot supply credentials)
- Non-existent repos return 404
- Feed XML includes valid structure (channel/item for RSS, feed/entry for Atom)

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.api.routes.musehub.feeds import (
    _atom_date,
    _build_atom_envelope,
    _build_rss_envelope,
    _commit_atom_entry,
    _commit_rss_item,
    _issue_rss_item,
    _release_rss_item,
    _rss_pub_date,
)
from maestro.db.musehub_models import MusehubCommit
from maestro.models.musehub import (
    CommitResponse,
    IssueResponse,
    ReleaseResponse,
)
from maestro.services.musehub_release_packager import build_empty_download_urls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_public_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    name: str = "feed-public-repo",
) -> str:
    """Create a public repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser", "visibility": "public"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _create_private_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    name: str = "feed-private-repo",
) -> str:
    """Create a private repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _insert_commit(
    db_session: AsyncSession,
    repo_id: str,
    commit_id: str,
    message: str,
) -> None:
    """Insert a commit directly into the DB (no push API exists)."""
    db_session.add(
        MusehubCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message=message,
            author="testuser",
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    await db_session.commit()


async def _create_release(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    tag: str = "v1.0",
    title: str = "Initial Release",
) -> None:
    """Create a release via the API."""
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        json={"tag": tag, "title": title, "body": "## Notes\n\nFirst release."},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


async def _create_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    title: str = "Verse feels unresolved",
) -> None:
    """Create an open issue via the API."""
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        json={"title": title, "body": "Needs work.", "labels": []},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# GET /repos/{repo_id}/feed.rss — commit RSS feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_rss_feed_public_repo_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Public repo commit RSS feed returns 200 with application/rss+xml."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-commit-public-1")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.rss")
    assert response.status_code == 200
    assert "application/rss+xml" in response.headers["content-type"]


@pytest.mark.anyio
async def test_commit_rss_feed_contains_rss_structure(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Commit RSS feed body is valid RSS 2.0 with <rss> and <channel> tags."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-commit-structure")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.rss")
    assert response.status_code == 200
    body = response.text
    assert '<rss version="2.0">' in body
    assert "<channel>" in body
    assert "</channel>" in body
    assert "</rss>" in body


@pytest.mark.anyio
async def test_commit_rss_feed_includes_commit_items(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Commit RSS feed contains <item> elements for each commit."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-commit-items")
    await _insert_commit(db_session, repo_id, "cmt001", "Add melodic intro")
    await _insert_commit(db_session, repo_id, "cmt002", "Rework verse harmony")

    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.rss")
    assert response.status_code == 200
    body = response.text
    assert "<item>" in body
    assert "Add melodic intro" in body
    assert "Rework verse harmony" in body


@pytest.mark.anyio
async def test_commit_rss_feed_private_repo_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Private repo commit RSS feed returns 403 Forbidden."""
    repo_id = await _create_private_repo(client, auth_headers, "rss-commit-private")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.rss")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_commit_rss_feed_nonexistent_repo_returns_404(client: AsyncClient) -> None:
    """Commit RSS feed returns 404 for a non-existent repo."""
    response = await client.get("/api/v1/musehub/repos/ghost-repo-id/feed.rss")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_commit_rss_feed_empty_repo_returns_valid_xml(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Commit RSS feed is valid XML even when the repo has no commits."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-commit-empty")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.rss")
    assert response.status_code == 200
    body = response.text
    assert '<rss version="2.0">' in body
    assert "<item>" not in body


# ---------------------------------------------------------------------------
# GET /repos/{repo_id}/releases.rss — releases RSS feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_releases_rss_feed_public_repo_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Public repo releases RSS feed returns 200 with application/rss+xml."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-releases-public-1")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/releases.rss")
    assert response.status_code == 200
    assert "application/rss+xml" in response.headers["content-type"]


@pytest.mark.anyio
async def test_releases_rss_feed_includes_release_items(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Releases RSS feed includes <item> elements for each release."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-releases-items")
    await _create_release(client, auth_headers, repo_id, tag="v1.0", title="First Cut")
    await _create_release(client, auth_headers, repo_id, tag="v2.0", title="Second Cut")

    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/releases.rss")
    assert response.status_code == 200
    body = response.text
    assert "<item>" in body
    assert "Release v1.0: First Cut" in body
    assert "Release v2.0: Second Cut" in body


@pytest.mark.anyio
async def test_releases_rss_feed_private_repo_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Private repo releases RSS feed returns 403 Forbidden."""
    repo_id = await _create_private_repo(client, auth_headers, "rss-releases-private")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/releases.rss")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_releases_rss_feed_nonexistent_repo_returns_404(client: AsyncClient) -> None:
    """Releases RSS feed returns 404 for a non-existent repo."""
    response = await client.get("/api/v1/musehub/repos/ghost-repo-id/releases.rss")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /repos/{repo_id}/issues.rss — open issues RSS feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issues_rss_feed_public_repo_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Public repo issues RSS feed returns 200 with application/rss+xml."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-issues-public-1")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/issues.rss")
    assert response.status_code == 200
    assert "application/rss+xml" in response.headers["content-type"]


@pytest.mark.anyio
async def test_issues_rss_feed_includes_open_issues(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Issues RSS feed includes <item> elements for open issues."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-issues-items")
    await _create_issue(client, auth_headers, repo_id, title="Bass muddy in chorus")
    await _create_issue(client, auth_headers, repo_id, title="Drums too loud")

    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/issues.rss")
    assert response.status_code == 200
    body = response.text
    assert "<item>" in body
    assert "Bass muddy in chorus" in body
    assert "Drums too loud" in body


@pytest.mark.anyio
async def test_issues_rss_feed_private_repo_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Private repo issues RSS feed returns 403 Forbidden."""
    repo_id = await _create_private_repo(client, auth_headers, "rss-issues-private")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/issues.rss")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_issues_rss_feed_nonexistent_repo_returns_404(client: AsyncClient) -> None:
    """Issues RSS feed returns 404 for a non-existent repo."""
    response = await client.get("/api/v1/musehub/repos/ghost-repo-id/issues.rss")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_issues_rss_feed_empty_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Issues RSS feed is valid XML with no <item> tags when repo has no issues."""
    repo_id = await _create_public_repo(client, auth_headers, "rss-issues-empty")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/issues.rss")
    assert response.status_code == 200
    assert "<item>" not in response.text


# ---------------------------------------------------------------------------
# GET /repos/{repo_id}/feed.atom — Atom 1.0 commit feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_atom_feed_public_repo_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Public repo commit Atom feed returns 200 with application/atom+xml."""
    repo_id = await _create_public_repo(client, auth_headers, "atom-commit-public-1")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.atom")
    assert response.status_code == 200
    assert "application/atom+xml" in response.headers["content-type"]


@pytest.mark.anyio
async def test_commit_atom_feed_contains_atom_structure(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Atom feed body contains Atom 1.0 namespace and required <feed> tags."""
    repo_id = await _create_public_repo(client, auth_headers, "atom-commit-structure")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.atom")
    assert response.status_code == 200
    body = response.text
    assert 'xmlns="http://www.w3.org/2005/Atom"' in body
    assert "<feed" in body
    assert "</feed>" in body
    assert "<title>" in body
    assert "<updated>" in body


@pytest.mark.anyio
async def test_commit_atom_feed_includes_entries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Atom feed contains <entry> elements for each commit."""
    repo_id = await _create_public_repo(client, auth_headers, "atom-commit-entries")
    await _insert_commit(db_session, repo_id, "cmt003", "Introduce syncopated kick pattern")

    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.atom")
    assert response.status_code == 200
    body = response.text
    assert "<entry>" in body
    assert "Introduce syncopated kick pattern" in body


@pytest.mark.anyio
async def test_commit_atom_feed_private_repo_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Private repo Atom feed returns 403 Forbidden."""
    repo_id = await _create_private_repo(client, auth_headers, "atom-commit-private")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.atom")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_commit_atom_feed_nonexistent_repo_returns_404(client: AsyncClient) -> None:
    """Atom commit feed returns 404 for a non-existent repo."""
    response = await client.get("/api/v1/musehub/repos/ghost-repo-id/feed.atom")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_commit_atom_feed_empty_repo_valid_xml(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Atom feed is valid XML with no <entry> tags when repo has no commits."""
    repo_id = await _create_public_repo(client, auth_headers, "atom-commit-empty")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/feed.atom")
    assert response.status_code == 200
    assert "<entry>" not in response.text


# ---------------------------------------------------------------------------
# XML builder unit tests (pure functions, no HTTP)
# ---------------------------------------------------------------------------


def _make_commit(commit_id: str = "abc123", message: str = "Add bass groove") -> CommitResponse:
    """Build a minimal CommitResponse for unit tests."""
    return CommitResponse(
        commit_id=commit_id,
        branch="main",
        parent_ids=[],
        message=message,
        author="testuser",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_release(
    release_id: str = "rel-001",
    tag: str = "v1.0",
    title: str = "Initial Release",
) -> ReleaseResponse:
    """Build a minimal ReleaseResponse for unit tests."""
    return ReleaseResponse(
        release_id=release_id,
        tag=tag,
        title=title,
        body="Release notes here.",
        commit_id=None,
        download_urls=build_empty_download_urls(),
        author="testuser",
        created_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_issue(
    issue_id: str = "iss-001",
    number: int = 1,
    title: str = "Verse feels unresolved",
) -> IssueResponse:
    """Build a minimal IssueResponse for unit tests."""
    return IssueResponse(
        issue_id=issue_id,
        number=number,
        title=title,
        body="Needs more resolution.",
        state="open",
        labels=[],
        author="testuser",
        assignee=None,
        milestone_id=None,
        milestone_title=None,
        updated_at=None,
        comment_count=0,
        created_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_rss_pub_date_utc_naive() -> None:
    """_rss_pub_date treats naive datetimes as UTC."""
    dt = datetime(2024, 6, 15, 12, 0, 0)
    result = _rss_pub_date(dt)
    assert "2024" in result
    assert "+0000" in result


def test_rss_pub_date_with_tz() -> None:
    """_rss_pub_date preserves timezone-aware datetimes."""
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = _rss_pub_date(dt)
    assert "Sat, 15 Jun 2024 12:00:00 +0000" == result


def test_atom_date_format() -> None:
    """_atom_date formats in RFC 3339 / ISO 8601."""
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert _atom_date(dt) == "2024-06-15T12:00:00Z"


def test_commit_rss_item_contains_required_fields() -> None:
    """_commit_rss_item includes title, link, guid, and pubDate."""
    commit = _make_commit(commit_id="abc123", message="Add bass groove")
    xml = _commit_rss_item(commit, owner="miles", slug="kind-of-blue")
    assert "<item>" in xml
    assert "<title>Add bass groove</title>" in xml
    assert "abc123" in xml
    assert "/musehub/ui/miles/kind-of-blue/commits/abc123" in xml
    assert "<pubDate>" in xml
    assert "<guid" in xml


def test_commit_rss_item_truncates_long_title() -> None:
    """_commit_rss_item truncates commit message to 80 chars in <title>."""
    long_msg = "A" * 120
    commit = _make_commit(message=long_msg)
    xml = _commit_rss_item(commit, owner="u", slug="r")
    # The <title> element should be truncated to 80 chars
    assert "<title>" + "A" * 80 + "</title>" in xml


def test_commit_rss_item_escapes_xml_chars() -> None:
    """_commit_rss_item escapes < > & in commit messages."""
    commit = _make_commit(message="Use <5 voices & keep it > quiet")
    xml = _commit_rss_item(commit, owner="u", slug="r")
    assert "&lt;" in xml
    assert "&gt;" in xml
    assert "&amp;" in xml


def test_release_rss_item_title_format() -> None:
    """_release_rss_item formats title as 'Release {tag}: {name}'."""
    release = _make_release(tag="v2.0", title="Big Refactor")
    xml = _release_rss_item(release, owner="miles", slug="kind-of-blue")
    assert "Release v2.0: Big Refactor" in xml


def test_release_rss_item_link_includes_tag() -> None:
    """_release_rss_item link path includes the release tag."""
    release = _make_release(tag="v1.0")
    xml = _release_rss_item(release, owner="u", slug="r")
    assert "/musehub/ui/u/r/releases/v1.0" in xml


def test_release_rss_item_no_enclosure_when_no_mp3() -> None:
    """_release_rss_item omits <enclosure> when no mp3 download URL is set."""
    release = _make_release()
    xml = _release_rss_item(release, owner="u", slug="r")
    assert "<enclosure" not in xml


def test_issue_rss_item_contains_required_fields() -> None:
    """_issue_rss_item includes title, link, guid, and pubDate."""
    issue = _make_issue(number=7, title="Chord clash on beat 3")
    xml = _issue_rss_item(issue, owner="miles", slug="kind-of-blue")
    assert "<item>" in xml
    assert "Chord clash on beat 3" in xml
    assert "/musehub/ui/miles/kind-of-blue/issues/7" in xml
    assert "<guid" in xml


def test_commit_atom_entry_contains_required_fields() -> None:
    """_commit_atom_entry includes title, link href, id, updated, and summary."""
    commit = _make_commit(commit_id="def456", message="Reharmonise verse")
    xml = _commit_atom_entry(commit, owner="miles", slug="kind-of-blue")
    assert "<entry>" in xml
    assert "Reharmonise verse" in xml
    assert 'href="/musehub/ui/miles/kind-of-blue/commits/def456"' in xml
    assert "<updated>" in xml
    assert "<id>" in xml


def test_build_rss_envelope_structure() -> None:
    """_build_rss_envelope produces a well-formed RSS 2.0 document."""
    xml = _build_rss_envelope(
        title="Test Feed",
        link="https://example.com",
        description="A test feed",
        items=["<item><title>Item 1</title></item>"],
    )
    assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
    assert '<rss version="2.0">' in xml
    assert "<title>Test Feed</title>" in xml
    assert "Item 1" in xml


def test_build_atom_envelope_structure() -> None:
    """_build_atom_envelope produces a well-formed Atom 1.0 document."""
    xml = _build_atom_envelope(
        title="Atom Feed",
        feed_id="tag:example:feed",
        updated="2024-06-15T12:00:00Z",
        entries=["<entry><title>E1</title></entry>"],
    )
    assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
    assert 'xmlns="http://www.w3.org/2005/Atom"' in xml
    assert "<title>Atom Feed</title>" in xml
    assert "E1" in xml
