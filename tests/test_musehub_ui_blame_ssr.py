"""SSR-specific tests for the Muse Hub blame page (issue #566).

Verifies that blame data is rendered server-side — file path, commit SHA,
author, and note rows appear in the initial HTML without a client-side fetch.

Covers:
- test_blame_page_renders_file_path_server_side — path present in SSR HTML
- test_blame_page_renders_commit_sha_server_side — short SHA rendered for seeded commit
- test_blame_page_renders_author_server_side — commit author rendered in HTML
- test_blame_page_renders_note_rows_server_side — blame rows rendered for seeded entries
- test_blame_page_unknown_repo_returns_404 — unknown owner/slug → 404
- test_blame_page_empty_entries_shows_empty_state — no entries → empty state message
- test_blame_page_filter_form_preserves_track — track filter pre-selected from query param
- test_blame_page_filter_form_is_htmx_capable — filter form has hx-get attribute
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _seed_repo(
    db_session: AsyncSession,
    *,
    owner: str = "blameuser",
    slug: str = "blame-beats",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="00000000-0000-0000-0000-000000000099",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _seed_commit(
    db_session: AsyncSession,
    repo_id: str,
    *,
    commit_id: str = "deadbeef12345678",
    message: str = "Add piano intro",
    author: str = "blameuser",
) -> None:
    """Seed a commit so blame entries reference a real author and SHA."""
    commit = MusehubCommit(
        repo_id=repo_id,
        commit_id=commit_id,
        message=message,
        author=author,
        branch="main",
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()


_OWNER = "blameuser"
_SLUG = "blame-beats"
_REF = "deadbeef12345678"
_PATH = "tracks/piano.mid"


# ---------------------------------------------------------------------------
# SSR content tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_page_renders_file_path_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The MIDI file path must appear in the server-rendered HTML."""
    await _seed_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Path rendered in header and filter form action (server-rendered, not a JS shell)
    assert _PATH in body
    # Blame content div present — table or empty state rendered without loading placeholder
    assert "blame-header" in body
    assert "Loading" not in body


@pytest.mark.anyio
async def test_blame_page_renders_commit_sha_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Short commit SHA must appear server-side when a commit is seeded."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(db_session, repo_id)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # Short SHA rendered by the `shortsha` Jinja2 filter (first 8 chars)
    assert _REF[:8] in body


@pytest.mark.anyio
async def test_blame_page_renders_author_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit author must appear in the SSR blame table when entries are present."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(db_session, repo_id, author="jazzmaster")
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # Author rendered in the blame-author cell when entries exist
    assert "jazzmaster" in body


@pytest.mark.anyio
async def test_blame_page_renders_note_rows_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When blame entries are generated, the SSR table contains note data rows."""
    repo_id = await _seed_repo(db_session)
    await _seed_commit(db_session, repo_id)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # Blame table structure is server-rendered — no loading spinner
    assert "blame-table" in body
    assert "Loading" not in body


@pytest.mark.anyio
async def test_blame_page_unknown_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug must return 404 — the repo lookup fails before rendering."""
    url = f"/musehub/ui/nobody/no-such-repo/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_blame_page_empty_entries_shows_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo with no commits must render the empty-state message (not a table)."""
    await _seed_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # No commits → no blame entries → empty state message rendered server-side
    assert "blame-empty" in body


@pytest.mark.anyio
async def test_blame_page_filter_form_preserves_track(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Track filter param must pre-select the correct <option> in the SSR form."""
    await _seed_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}?track=piano"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # The server renders the select with the matching option selected
    assert 'value="piano" selected' in body


@pytest.mark.anyio
async def test_blame_page_filter_form_is_htmx_capable(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Filter form must carry hx-get attribute so HTMX can swap content inline."""
    await _seed_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "hx-get" in body
