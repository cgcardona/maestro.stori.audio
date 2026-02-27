"""Tests for Muse Hub push/pull sync protocol.

Covers every acceptance criterion from issue #40:
- POST /push stores commits and objects (upsert)
- POST /push updates the branch head to head_commit_id
- POST /push rejects non-fast-forward updates with 409 (unless force=true)
- POST /pull returns commits/objects the caller does not have
- POST /push → POST /pull round-trip returns all committed data
- Both endpoints require valid JWT (401 without token)

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services import musehub_repository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_B64_MIDI = base64.b64encode(b"MIDI_CONTENT").decode()
_B64_MP3 = base64.b64encode(b"MP3_CONTENT").decode()


def _push_payload(
    *,
    branch: str = "main",
    head_commit_id: str = "c001",
    commits: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
    force: bool = False,
) -> dict[str, object]:
    return {
        "branch": branch,
        "headCommitId": head_commit_id,
        "commits": commits
        or [
            {
                "commitId": head_commit_id,
                "parentIds": [],
                "message": "init",
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ],
        "objects": objects or [],
        "force": force,
    }


def _pull_payload(
    *,
    branch: str = "main",
    have_commits: list[str] | None = None,
    have_objects: list[str] | None = None,
) -> dict[str, object]:
    return {
        "branch": branch,
        "haveCommits": have_commits or [],
        "haveObjects": have_objects or [],
    }


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "test-repo") -> str:
    r = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name},
        headers=auth_headers,
    )
    assert r.status_code == 201
    repo_id: str = r.json()["repoId"]
    return repo_id


# ---------------------------------------------------------------------------
# POST /push — stores commits and objects
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_stores_commits_and_objects(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Commits and objects are queryable after a successful push."""
    repo_id = await _create_repo(client, auth_headers, "push-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            payload = _push_payload(
                head_commit_id="c001",
                commits=[
                    {
                        "commitId": "c001",
                        "parentIds": [],
                        "message": "Add jazz track",
                        "timestamp": "2024-01-01T10:00:00Z",
                    }
                ],
                objects=[
                    {
                        "objectId": "sha256:aabbcc",
                        "path": "tracks/jazz.mid",
                        "contentB64": _B64_MIDI,
                    }
                ],
            )
            resp = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=payload,
                headers=auth_headers,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["remoteHead"] == "c001"

    # Commits visible via list endpoint
    commits_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits",
        headers=auth_headers,
    )
    assert commits_resp.status_code == 200
    commit_ids = [c["commitId"] for c in commits_resp.json()["commits"]]
    assert "c001" in commit_ids


# ---------------------------------------------------------------------------
# POST /push — updates branch head
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_updates_branch_head(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Branch head pointer is updated to head_commit_id after push."""
    repo_id = await _create_repo(client, auth_headers, "head-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(head_commit_id="c001"),
                headers=auth_headers,
            )

    branches_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/branches",
        headers=auth_headers,
    )
    assert branches_resp.status_code == 200
    branches = branches_resp.json()["branches"]
    main_branch = next((b for b in branches if b["name"] == "main"), None)
    assert main_branch is not None
    assert main_branch["headCommitId"] == "c001"


# ---------------------------------------------------------------------------
# POST /push — non-fast-forward rejected with 409
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_non_fast_forward_returns_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A push that would create a non-fast-forward update is rejected with 409."""
    repo_id = await _create_repo(client, auth_headers, "nff-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            # First push — sets remote head to c001
            r1 = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(head_commit_id="c001"),
                headers=auth_headers,
            )
            assert r1.status_code == 200

            # Second push — diverges: c002 does NOT descend from c001
            r2 = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(
                    head_commit_id="c002",
                    commits=[
                        {
                            "commitId": "c002",
                            "parentIds": [],  # no parent → diverged
                            "message": "diverged commit",
                            "timestamp": "2024-01-02T00:00:00Z",
                        }
                    ],
                ),
                headers=auth_headers,
            )

    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "non_fast_forward"


@pytest.mark.anyio
async def test_push_force_allows_non_fast_forward(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """force=true allows a non-fast-forward push."""
    repo_id = await _create_repo(client, auth_headers, "force-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(head_commit_id="c001"),
                headers=auth_headers,
            )

            r = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(
                    head_commit_id="c002",
                    commits=[
                        {
                            "commitId": "c002",
                            "parentIds": [],
                            "message": "force rewrite",
                            "timestamp": "2024-01-03T00:00:00Z",
                        }
                    ],
                    force=True,
                ),
                headers=auth_headers,
            )

    assert r.status_code == 200
    assert r.json()["remoteHead"] == "c002"


# ---------------------------------------------------------------------------
# POST /pull — returns missing commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_returns_missing_commits(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Only commits not in have_commits are returned by pull."""
    repo_id = await _create_repo(client, auth_headers, "pull-commits-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            # Push two commits
            await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(
                    head_commit_id="c002",
                    commits=[
                        {
                            "commitId": "c001",
                            "parentIds": [],
                            "message": "first",
                            "timestamp": "2024-01-01T00:00:00Z",
                        },
                        {
                            "commitId": "c002",
                            "parentIds": ["c001"],
                            "message": "second",
                            "timestamp": "2024-01-02T00:00:00Z",
                        },
                    ],
                ),
                headers=auth_headers,
            )

            # Pull with c001 already known
            pull_resp = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/pull",
                json=_pull_payload(have_commits=["c001"]),
                headers=auth_headers,
            )

    assert pull_resp.status_code == 200
    body = pull_resp.json()
    commit_ids = [c["commitId"] for c in body["commits"]]
    assert "c002" in commit_ids
    assert "c001" not in commit_ids
    assert body["remoteHead"] == "c002"


# ---------------------------------------------------------------------------
# POST /pull — returns missing objects
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_returns_missing_objects(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Only objects not in have_objects are returned by pull."""
    repo_id = await _create_repo(client, auth_headers, "pull-objects-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            # Push two objects
            await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=_push_payload(
                    head_commit_id="c001",
                    objects=[
                        {
                            "objectId": "sha256:aaa",
                            "path": "tracks/a.mid",
                            "contentB64": _B64_MIDI,
                        },
                        {
                            "objectId": "sha256:bbb",
                            "path": "tracks/b.mp3",
                            "contentB64": _B64_MP3,
                        },
                    ],
                ),
                headers=auth_headers,
            )

            # Pull with sha256:aaa already known
            pull_resp = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/pull",
                json=_pull_payload(have_objects=["sha256:aaa"]),
                headers=auth_headers,
            )

    assert pull_resp.status_code == 200
    body = pull_resp.json()
    object_ids = [o["objectId"] for o in body["objects"]]
    assert "sha256:bbb" in object_ids
    assert "sha256:aaa" not in object_ids


# ---------------------------------------------------------------------------
# Push → pull round-trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_then_pull_roundtrip(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """After a push, a pull from a fresh client returns all commits and objects."""
    repo_id = await _create_repo(client, auth_headers, "roundtrip-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            push_payload = _push_payload(
                head_commit_id="c001",
                commits=[
                    {
                        "commitId": "c001",
                        "parentIds": [],
                        "message": "round-trip commit",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                objects=[
                    {
                        "objectId": "sha256:rt01",
                        "path": "tracks/rt.mid",
                        "contentB64": _B64_MIDI,
                    }
                ],
            )
            push_resp = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/push",
                json=push_payload,
                headers=auth_headers,
            )
            assert push_resp.status_code == 200

            pull_resp = await client.post(
                f"/api/v1/musehub/repos/{repo_id}/pull",
                json=_pull_payload(),
                headers=auth_headers,
            )

    assert pull_resp.status_code == 200
    body = pull_resp.json()
    commit_ids = [c["commitId"] for c in body["commits"]]
    object_ids = [o["objectId"] for o in body["objects"]]
    assert "c001" in commit_ids
    assert "sha256:rt01" in object_ids
    assert body["remoteHead"] == "c001"

    # Verify object content survived the round-trip
    obj = next(o for o in body["objects"] if o["objectId"] == "sha256:rt01")
    assert base64.b64decode(obj["contentB64"]) == b"MIDI_CONTENT"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_requires_auth(client: AsyncClient) -> None:
    """POST /push returns 401 without a Bearer token."""
    resp = await client.post(
        "/api/v1/musehub/repos/any-repo/push",
        json=_push_payload(),
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_pull_requires_auth(client: AsyncClient) -> None:
    """POST /pull returns 401 without a Bearer token."""
    resp = await client.post(
        "/api/v1/musehub/repos/any-repo/pull",
        json=_pull_payload(),
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 404 for unknown repo
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_unknown_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /push returns 404 when the repo does not exist."""
    resp = await client.post(
        "/api/v1/musehub/repos/ghost-repo/push",
        json=_push_payload(),
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_pull_unknown_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /pull returns 404 when the repo does not exist."""
    resp = await client.post(
        "/api/v1/musehub/repos/ghost-repo/pull",
        json=_pull_payload(),
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Idempotency — duplicate push does not create duplicate commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_idempotent_commits(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Pushing the same commit twice does not create duplicates."""
    repo_id = await _create_repo(client, auth_headers, "idem-test")

    with tempfile.TemporaryDirectory() as tmp:
        with patch("maestro.services.musehub_sync.settings") as mock_cfg:
            mock_cfg.musehub_objects_dir = tmp

            for _ in range(2):
                r = await client.post(
                    f"/api/v1/musehub/repos/{repo_id}/push",
                    json=_push_payload(head_commit_id="c001"),
                    headers=auth_headers,
                )
                assert r.status_code == 200

    commits_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits",
        headers=auth_headers,
    )
    commit_ids = [c["commitId"] for c in commits_resp.json()["commits"]]
    assert commit_ids.count("c001") == 1
