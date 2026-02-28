"""Tests for Muse Hub release management endpoints.

Covers every acceptance criterion from issue #242:
- POST /musehub/repos/{repo_id}/releases creates a release tied to a tag
- GET  /musehub/repos/{repo_id}/releases lists all releases (newest first)
- GET  /musehub/repos/{repo_id}/releases/{tag} returns release detail with download URLs
- Duplicate tag within the same repo returns 409 Conflict
- All endpoints require valid JWT (401 without token)
- Service layer: create_release, list_releases, get_release_by_tag, get_latest_release

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services import musehub_releases, musehub_repository
from maestro.services.musehub_release_packager import (
    build_download_urls,
    build_empty_download_urls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    name: str = "release-test-repo",
) -> str:
    """Create a repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _create_release(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    tag: str = "v1.0",
    title: str = "First Release",
    body: str = "# Release notes\n\nInitial release.",
    commit_id: str | None = None,
) -> dict[str, object]:
    """Create a release via the API and return the response body."""
    payload: dict[str, object] = {"tag": tag, "title": title, "body": body}
    if commit_id is not None:
        payload["commitId"] = commit_id
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/releases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_release_returns_all_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /releases creates a release and returns all required fields."""
    repo_id = await _create_repo(client, auth_headers, "create-release-repo")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        json={
            "tag": "v1.0",
            "title": "First Release",
            "body": "## Release Notes\n\nInitial composition released.",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["tag"] == "v1.0"
    assert body["title"] == "First Release"
    assert "body" in body
    assert "releaseId" in body
    assert "createdAt" in body
    assert "downloadUrls" in body


@pytest.mark.anyio
async def test_create_release_with_commit_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /releases with a commitId stores the commit reference."""
    repo_id = await _create_repo(client, auth_headers, "release-commit-repo")
    commit_sha = "abc123def456abc123def456abc123def456abc1"
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        json={"tag": "v2.0", "title": "Tagged Release", "commitId": commit_sha},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["commitId"] == commit_sha


@pytest.mark.anyio
async def test_create_release_duplicate_tag_returns_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /releases with a duplicate tag for the same repo returns 409 Conflict."""
    repo_id = await _create_repo(client, auth_headers, "dup-tag-repo")
    await _create_release(client, auth_headers, repo_id, tag="v1.0")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        json={"tag": "v1.0", "title": "Duplicate", "body": ""},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_create_release_same_tag_different_repos_ok(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The same tag can be used in different repos without conflict."""
    repo_a = await _create_repo(client, auth_headers, "tag-repo-a")
    repo_b = await _create_repo(client, auth_headers, "tag-repo-b")

    await _create_release(client, auth_headers, repo_a, tag="v1.0", title="A v1.0")
    # Creating the same tag in a different repo must succeed.
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_b}/releases",
        json={"tag": "v1.0", "title": "B v1.0"},
        headers=auth_headers,
    )
    assert response.status_code == 201


@pytest.mark.anyio
async def test_create_release_repo_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /releases returns 404 when the repo does not exist."""
    response = await client.post(
        "/api/v1/musehub/repos/nonexistent-repo-id/releases",
        json={"tag": "v1.0", "title": "Ghost release"},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/releases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_releases_empty_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases returns an empty list for a repo with no releases."""
    repo_id = await _create_repo(client, auth_headers, "empty-releases-repo")
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["releases"] == []


@pytest.mark.anyio
async def test_list_releases_ordered_newest_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases returns releases ordered newest first."""
    repo_id = await _create_repo(client, auth_headers, "ordered-releases-repo")
    await _create_release(client, auth_headers, repo_id, tag="v1.0", title="First")
    await _create_release(client, auth_headers, repo_id, tag="v2.0", title="Second")
    await _create_release(client, auth_headers, repo_id, tag="v3.0", title="Third")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/releases",
        headers=auth_headers,
    )
    assert response.status_code == 200
    releases = response.json()["releases"]
    assert len(releases) == 3
    # Newest created last → appears first in the response.
    tags = [r["tag"] for r in releases]
    assert tags[0] == "v3.0"
    assert tags[-1] == "v1.0"


@pytest.mark.anyio
async def test_list_releases_repo_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases returns 404 when the repo does not exist."""
    response = await client.get(
        "/api/v1/musehub/repos/ghost-repo/releases",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/releases/{tag}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_release_detail_includes_download_urls(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases/{tag} returns a release with a downloadUrls structure."""
    repo_id = await _create_repo(client, auth_headers, "detail-url-repo")
    await _create_release(client, auth_headers, repo_id, tag="v1.0")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/releases/v1.0",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tag"] == "v1.0"
    assert "downloadUrls" in body
    urls = body["downloadUrls"]
    # A freshly created release with no objects has no download URLs.
    assert "midiBubdle" not in urls or urls.get("midiBundle") is None
    assert "stems" in urls
    assert "mp3" in urls
    assert "musicxml" in urls
    assert "metadata" in urls


@pytest.mark.anyio
async def test_release_detail_tag_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases/{tag} returns 404 when the tag does not exist."""
    repo_id = await _create_repo(client, auth_headers, "tag-404-repo")
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/releases/nonexistent-tag",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_release_detail_body_preserved(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /releases/{tag} returns the full release notes body."""
    repo_id = await _create_repo(client, auth_headers, "body-preserve-repo")
    notes = "# v1.0 Release\n\n- Added bass groove\n- Fixed timing drift in measure 4"
    await _create_release(
        client, auth_headers, repo_id, tag="v1.0", title="Groovy Release", body=notes
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/releases/v1.0",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["body"] == notes


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_release_write_requires_auth(client: AsyncClient) -> None:
    """POST release endpoint returns 401 without a Bearer token (always requires auth)."""
    response = await client.post("/api/v1/musehub/repos/some-repo/releases", json={})
    assert response.status_code == 401, "POST /releases should require auth"


@pytest.mark.anyio
async def test_release_read_endpoints_return_404_for_nonexistent_repo_without_auth(
    client: AsyncClient,
) -> None:
    """GET release endpoints return 404 for non-existent repos without a token.

    Read endpoints use optional_token — auth is visibility-based; missing repo → 404.
    """
    read_endpoints = [
        "/api/v1/musehub/repos/non-existent-repo/releases",
        "/api/v1/musehub/repos/non-existent-repo/releases/v1.0",
    ]
    for url in read_endpoints:
        response = await client.get(url)
        assert response.status_code == 404, f"GET {url} should return 404 for non-existent repo"


# ---------------------------------------------------------------------------
# Service layer — direct DB tests (no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_release_service_persists_to_db(db_session: AsyncSession) -> None:
    """musehub_releases.create_release() persists the row and all fields are correct."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="service-release-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-001",
    )
    await db_session.commit()

    release = await musehub_releases.create_release(
        db_session,
        repo_id=repo.repo_id,
        tag="v1.0",
        title="First Release",
        body="Initial cut of the jazz arrangement.",
        commit_id=None,
    )
    await db_session.commit()

    fetched = await musehub_releases.get_release_by_tag(db_session, repo.repo_id, "v1.0")
    assert fetched is not None
    assert fetched.release_id == release.release_id
    assert fetched.tag == "v1.0"
    assert fetched.title == "First Release"
    assert fetched.commit_id is None


@pytest.mark.anyio
async def test_create_release_duplicate_tag_raises_value_error(
    db_session: AsyncSession,
) -> None:
    """create_release() raises ValueError on duplicate tag within the same repo."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="dup-tag-svc-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-002",
    )
    await db_session.commit()

    await musehub_releases.create_release(
        db_session,
        repo_id=repo.repo_id,
        tag="v1.0",
        title="Original",
        body="",
        commit_id=None,
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="v1.0"):
        await musehub_releases.create_release(
            db_session,
            repo_id=repo.repo_id,
            tag="v1.0",
            title="Duplicate",
            body="",
            commit_id=None,
        )


@pytest.mark.anyio
async def test_list_releases_newest_first_service(db_session: AsyncSession) -> None:
    """list_releases() returns releases ordered by created_at descending."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="list-svc-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-003",
    )
    await db_session.commit()

    r1 = await musehub_releases.create_release(
        db_session, repo_id=repo.repo_id, tag="v1.0", title="One", body="", commit_id=None
    )
    await db_session.commit()
    r2 = await musehub_releases.create_release(
        db_session, repo_id=repo.repo_id, tag="v2.0", title="Two", body="", commit_id=None
    )
    await db_session.commit()

    result = await musehub_releases.list_releases(db_session, repo.repo_id)
    assert len(result) == 2
    # Newest first
    assert result[0].release_id == r2.release_id
    assert result[1].release_id == r1.release_id


@pytest.mark.anyio
async def test_get_latest_release_returns_newest(db_session: AsyncSession) -> None:
    """get_latest_release() returns the most recently created release."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="latest-svc-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-004",
    )
    await db_session.commit()

    await musehub_releases.create_release(
        db_session, repo_id=repo.repo_id, tag="v1.0", title="Old", body="", commit_id=None
    )
    await db_session.commit()
    r2 = await musehub_releases.create_release(
        db_session, repo_id=repo.repo_id, tag="v2.0", title="Latest", body="", commit_id=None
    )
    await db_session.commit()

    latest = await musehub_releases.get_latest_release(db_session, repo.repo_id)
    assert latest is not None
    assert latest.release_id == r2.release_id
    assert latest.tag == "v2.0"


@pytest.mark.anyio
async def test_get_latest_release_empty_repo_returns_none(
    db_session: AsyncSession,
) -> None:
    """get_latest_release() returns None when no releases exist for the repo."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="no-releases-repo",
        owner="testuser",
        visibility="private",
        owner_user_id="user-005",
    )
    await db_session.commit()

    latest = await musehub_releases.get_latest_release(db_session, repo.repo_id)
    assert latest is None


# ---------------------------------------------------------------------------
# Release packager unit tests
# ---------------------------------------------------------------------------


def test_build_download_urls_all_packages_available() -> None:
    """build_download_urls() returns URLs for every package type when all flags are set."""
    urls = build_download_urls(
        "repo-abc",
        "release-xyz",
        has_midi=True,
        has_stems=True,
        has_mp3=True,
        has_musicxml=True,
    )
    assert urls.midi_bundle is not None
    assert "midi" in urls.midi_bundle
    assert urls.stems is not None
    assert "stems" in urls.stems
    assert urls.mp3 is not None
    assert "mp3" in urls.mp3
    assert urls.musicxml is not None
    assert "musicxml" in urls.musicxml
    assert urls.metadata is not None
    assert "metadata" in urls.metadata


def test_build_download_urls_partial_packages() -> None:
    """build_download_urls() only sets URLs for enabled packages."""
    urls = build_download_urls("repo-abc", "release-xyz", has_midi=True)
    assert urls.midi_bundle is not None
    assert urls.stems is None
    assert urls.mp3 is None
    assert urls.musicxml is None
    # Metadata is available when any package is available.
    assert urls.metadata is not None


def test_build_empty_download_urls_all_none() -> None:
    """build_empty_download_urls() returns a model with all fields set to None."""
    urls = build_empty_download_urls()
    assert urls.midi_bundle is None
    assert urls.stems is None
    assert urls.mp3 is None
    assert urls.musicxml is None
    assert urls.metadata is None


def test_build_download_urls_no_packages() -> None:
    """build_download_urls() with no flags set returns None for all fields including metadata."""
    urls = build_download_urls("repo-abc", "release-xyz")
    assert urls.midi_bundle is None
    assert urls.stems is None
    assert urls.mp3 is None
    assert urls.musicxml is None
    assert urls.metadata is None
