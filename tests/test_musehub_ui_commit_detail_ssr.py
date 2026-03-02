"""Tests for the SSR commit detail page — HTMX SSR + comment threading (issue #583).

Covers server-side rendering of commit metadata, comment thread, HTMX fragment
responses, audio shell, and 404 handling.

Test areas:
  Basic rendering
  - test_commit_detail_renders_message_server_side
  - test_commit_detail_unknown_sha_404

  SSR content
  - test_commit_detail_renders_diff_stats (author + branch metadata)
  - test_commit_detail_renders_comment_server_side
  - test_commit_detail_audio_shell_when_audio_url

  No-audio
  - test_commit_detail_no_audio_shell_when_no_url

  HTMX fragment
  - test_commit_detail_htmx_fragment_returns_comments
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubComment, MusehubCommit, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "beatmaker",
    slug: str = "jazz-project",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-beatmaker",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    commit_id: str | None = None,
    message: str = "Add jazz bridge section",
    author: str = "beatmaker",
    branch: str = "main",
    parent_ids: list[str] | None = None,
    snapshot_id: str | None = None,
) -> MusehubCommit:
    """Seed a commit and return it."""
    cid = commit_id or f"abc{uuid.uuid4().hex[:10]}"
    commit = MusehubCommit(
        commit_id=cid,
        repo_id=repo_id,
        branch=branch,
        parent_ids=parent_ids or [],
        message=message,
        author=author,
        timestamp=datetime.now(tz=timezone.utc),
        snapshot_id=snapshot_id,
    )
    db.add(commit)
    await db.commit()
    await db.refresh(commit)
    return commit


async def _make_commit_comment(
    db: AsyncSession,
    repo_id: str,
    commit_id: str,
    *,
    author: str = "producer",
    body: str = "Nice groove!",
    parent_id: str | None = None,
) -> MusehubComment:
    """Seed a commit comment and return it."""
    comment = MusehubComment(
        comment_id=str(uuid.uuid4()),
        repo_id=repo_id,
        target_type="commit",
        target_id=commit_id,
        author=author,
        body=body,
        parent_id=parent_id,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def _get_detail(
    client: AsyncClient,
    commit_id: str,
    owner: str = "beatmaker",
    slug: str = "jazz-project",
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Fetch the commit detail page; return (status_code, body_text)."""
    resp = await client.get(
        f"/musehub/ui/{owner}/{slug}/commits/{commit_id}",
        headers=headers or {},
    )
    return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_detail_renders_message_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit message appears in the HTML rendered on the server."""
    repo_id = await _make_repo(db_session)
    commit = await _make_commit(db_session, repo_id, message="Add jazz bridge section")

    status, body = await _get_detail(client, commit.commit_id)

    assert status == 200
    assert "Add jazz bridge section" in body


@pytest.mark.anyio
async def test_commit_detail_unknown_sha_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A non-existent commit SHA returns 404."""
    await _make_repo(db_session)

    resp = await client.get("/musehub/ui/beatmaker/jazz-project/commits/deadbeef00000000")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSR content
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_detail_renders_diff_stats(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit author and branch appear in the server-rendered metadata grid."""
    repo_id = await _make_repo(db_session)
    commit = await _make_commit(
        db_session, repo_id, author="jazzman", branch="feat/bridge"
    )

    status, body = await _get_detail(client, commit.commit_id)

    assert status == 200
    assert "jazzman" in body
    assert "feat/bridge" in body


@pytest.mark.anyio
async def test_commit_detail_renders_comment_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A seeded commit comment body appears in the rendered HTML."""
    repo_id = await _make_repo(db_session)
    commit = await _make_commit(db_session, repo_id)
    await _make_commit_comment(
        db_session, repo_id, commit.commit_id, body="Great chord progression!"
    )

    status, body = await _get_detail(client, commit.commit_id)

    assert status == 200
    assert "Great chord progression!" in body


@pytest.mark.anyio
async def test_commit_detail_audio_shell_when_audio_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When a commit has a snapshot_id, the waveform div is rendered with data-url."""
    repo_id = await _make_repo(db_session)
    snap_id = f"sha256:{uuid.uuid4().hex}"
    commit = await _make_commit(db_session, repo_id, snapshot_id=snap_id)

    status, body = await _get_detail(client, commit.commit_id)

    assert status == 200
    assert "commit-waveform" in body
    assert snap_id in body


# ---------------------------------------------------------------------------
# No-audio path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_detail_no_audio_shell_when_no_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When a commit has no snapshot_id, the waveform div is not rendered."""
    repo_id = await _make_repo(db_session)
    commit = await _make_commit(db_session, repo_id, snapshot_id=None)

    status, body = await _get_detail(client, commit.commit_id)

    assert status == 200
    assert "commit-waveform" not in body


# ---------------------------------------------------------------------------
# HTMX fragment
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_detail_htmx_fragment_returns_comments(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with HX-Request: true returns the comment fragment (no full page shell)."""
    repo_id = await _make_repo(db_session)
    commit = await _make_commit(db_session, repo_id)
    await _make_commit_comment(
        db_session, repo_id, commit.commit_id, body="Fragment visible here."
    )

    status, body = await _get_detail(
        client, commit.commit_id, headers={"HX-Request": "true"}
    )

    assert status == 200
    assert "Fragment visible here." in body
    # Fragment must not include the full page chrome
    assert "<html" not in body
    assert "<!DOCTYPE" not in body
