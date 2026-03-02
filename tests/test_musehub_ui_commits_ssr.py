"""SSR + HTMX fragment tests for the Muse Hub commits list page — issue #570.

Validates that commit data is rendered server-side into HTML (no JS required)
and that HTMX fragment requests return bare HTML without the full page shell.

Covers GET /musehub/ui/{owner}/{repo_slug}/commits:

- test_commits_page_renders_commit_message_server_side
    Seed a commit; its message appears in the response HTML.

- test_commits_page_filter_form_has_hx_get
    The filter form has hx-get attribute pointing at the commits URL.

- test_commits_page_fragment_on_htmx_request
    GET with HX-Request: true returns a bare fragment (no <html>/<head> shell).

- test_commits_page_author_filter_narrows_results
    ?author=alice shows only Alice's commits; Bob's are absent.

- test_commits_page_pagination_renders_next
    More than per_page commits → "Older →" link present in the response.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubRepo

# ── Constants ──────────────────────────────────────────────────────────────────

_OWNER = "ssr570owner"
_SLUG = "ssr570-commits"
_SHA_ALICE = "aa" + "0" * 38
_SHA_BOB = "bb" + "0" * 38


# ── Seed helpers ───────────────────────────────────────────────────────────────


async def _seed_repo(db: AsyncSession) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        repo_id=str(uuid.uuid4()),
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id=str(uuid.uuid4()),
    )
    db.add(repo)
    await db.flush()
    return str(repo.repo_id)


async def _seed_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    commit_id: str | None = None,
    author: str = "alice",
    message: str = "Test commit message",
    branch: str = "main",
    timestamp: datetime | None = None,
) -> MusehubCommit:
    """Seed a commit row and return the ORM object."""
    cid = commit_id or (uuid.uuid4().hex + uuid.uuid4().hex)[:40]
    ts = timestamp or datetime.now(timezone.utc)
    commit = MusehubCommit(
        commit_id=cid,
        repo_id=repo_id,
        branch=branch,
        parent_ids=[],
        message=message,
        author=author,
        timestamp=ts,
        snapshot_id=None,
    )
    db.add(commit)
    await db.flush()
    return commit


async def _seed_branch(db: AsyncSession, repo_id: str, head_id: str, name: str = "main") -> None:
    """Seed a branch row."""
    db.add(MusehubBranch(repo_id=repo_id, name=name, head_commit_id=head_id))
    await db.flush()


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_page_renders_commit_message_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit message is present in the HTML response — no client JS required."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(
        db_session, repo_id, message="Bassline groove at 120 BPM feels right"
    )
    await db_session.commit()

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/commits")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Bassline groove at 120 BPM feels right" in response.text


@pytest.mark.anyio
async def test_commits_page_filter_form_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The filter form carries hx-get so HTMX intercepts submissions."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(db_session, repo_id)
    await db_session.commit()

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/commits")

    assert response.status_code == 200
    assert "hx-get" in response.text


@pytest.mark.anyio
async def test_commits_page_fragment_on_htmx_request(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true returns a bare HTML fragment without the full page shell."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(
        db_session, repo_id, message="Fragment-only commit row"
    )
    await db_session.commit()

    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/commits",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    # No full-page HTML shell in a fragment response.
    assert "<html" not in response.text
    assert "<head" not in response.text
    # The commit content must still be present.
    assert "Fragment-only commit row" in response.text


@pytest.mark.anyio
async def test_commits_page_author_filter_narrows_results(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?author=alice includes only Alice's commits; Bob's message is absent."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(
        db_session, repo_id,
        commit_id=_SHA_ALICE,
        author="alice",
        message="Alice lays down the bass",
    )
    await _seed_commit(
        db_session, repo_id,
        commit_id=_SHA_BOB,
        author="bob",
        message="Bob adds a reverb tail",
    )
    await db_session.commit()

    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/commits?author=alice"
    )

    assert response.status_code == 200
    body = response.text
    assert "Alice lays down the bass" in body
    assert "Bob adds a reverb tail" not in body


@pytest.mark.anyio
async def test_commits_page_pagination_renders_next(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When total commits exceed per_page, the 'Older →' pagination link appears."""
    repo_id = await _seed_repo(db_session)
    # Seed 35 commits — more than the default per_page=30.
    for i in range(35):
        cid = f"{i:040x}"
        await _seed_commit(
            db_session, repo_id,
            commit_id=cid,
            message=f"Commit number {i}",
        )
    await db_session.commit()

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/commits")

    assert response.status_code == 200
    # "Older →" appears as an anchor when there is a next page.
    assert "Older" in response.text
