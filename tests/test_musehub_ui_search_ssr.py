"""SSR tests for Muse Hub search pages — issue #577.

Verifies that global_search_page() and search_page() render results
server-side in Jinja2 templates without requiring JavaScript execution.
Tests assert on HTML content directly returned by the server.

Covers GET /musehub/ui/search (global search):
- test_global_search_renders_results_server_side
- test_global_search_no_results_shows_empty_state
- test_global_search_short_query_shows_prompt
- test_global_search_htmx_fragment_path
- test_global_search_empty_query_shows_prompt

Covers GET /musehub/ui/{owner}/{repo_slug}/search (repo-scoped search):
- test_repo_search_form_populated_server_side
- test_repo_search_short_query_shows_prompt
- test_repo_search_htmx_fragment_returns_no_html
- test_repo_search_no_results_shows_empty_state
- test_repo_search_results_rendered_server_side
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubRepo
from maestro.muse_cli import models as _cli_models  # noqa: F401 — register tables
from maestro.muse_cli.db import insert_commit, upsert_snapshot
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "search_ssr_artist",
    slug: str = "search-ssr-album",
    visibility: str = "public",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id=f"uid-{owner}",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_musehub_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    commit_id: str,
    message: str,
    branch: str = "main",
    author: str = "musician",
) -> None:
    """Seed a MusehubCommit (used by global_search via musehub_commits table)."""
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_ids=[],
        message=message,
        author=author,
        timestamp=datetime.now(tz=timezone.utc),
    )
    db_branch = MusehubBranch(
        repo_id=repo_id,
        name=branch,
        head_commit_id=commit_id,
    )
    db.add(commit)
    db.add(db_branch)
    await db.commit()


async def _make_cli_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    message: str,
    branch: str = "main",
    author: str = "musician",
) -> str:
    """Seed a MuseCliCommit (used by in-repo search via muse_commits table).

    Returns the generated commit_id.
    """
    manifest: dict[str, str] = {"track.mid": "deadbeef"}
    snap_id = compute_snapshot_id(manifest)
    await upsert_snapshot(db, manifest=manifest, snapshot_id=snap_id)
    committed_at = datetime.now(tz=timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[],
        snapshot_id=snap_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=None,
        snapshot_id=snap_id,
        message=message,
        author=author,
        committed_at=committed_at,
    )
    await insert_commit(db, commit)
    await db.flush()
    return commit_id


# ---------------------------------------------------------------------------
# Global search — GET /musehub/ui/search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_renders_results_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit message appears in the HTML returned by the server (not injected by JS)."""
    repo_id = await _make_repo(db_session)
    await _make_musehub_commit(
        db_session,
        repo_id,
        commit_id="aabbccddeeff00112233445566778899aabbccd1",
        message="beatbox groove pattern in D minor",
    )
    response = await client.get("/musehub/ui/search?q=beatbox")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "beatbox" in response.text


@pytest.mark.anyio
async def test_global_search_no_results_shows_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A query with no matches renders the empty-state block."""
    await _make_repo(db_session, owner="search_noresult", slug="noresult-album")
    response = await client.get("/musehub/ui/search?q=zzznomatch")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "No results" in response.text


@pytest.mark.anyio
async def test_global_search_short_query_shows_prompt(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A single-character query renders the 'Enter at least 2 characters' prompt."""
    response = await client.get("/musehub/ui/search?q=a")
    assert response.status_code == 200
    assert "Enter at least 2 characters" in response.text


@pytest.mark.anyio
async def test_global_search_empty_query_shows_prompt(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An empty query renders the prompt without running any DB search."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code == 200
    assert "Enter at least 2 characters" in response.text


@pytest.mark.anyio
async def test_global_search_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true causes the handler to return only the fragment — no <html> shell."""
    repo_id = await _make_repo(db_session, owner="htmx_search_artist", slug="htmx-search-album")
    await _make_musehub_commit(
        db_session,
        repo_id,
        commit_id="aabbccddeeff00112233445566778899aabbccd2",
        message="funky bassline in Eb",
    )
    response = await client.get(
        "/musehub/ui/search?q=funky",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "funky" in response.text


# ---------------------------------------------------------------------------
# Repo-scoped search — GET /musehub/ui/{owner}/{repo_slug}/search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_repo_search_form_populated_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The query value is rendered server-side into the search form input (not by JS)."""
    await _make_repo(db_session, owner="repo_search_artist", slug="repo-search-album")
    response = await client.get(
        "/musehub/ui/repo_search_artist/repo-search-album/search?q=jazzcore"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # query value is SSR-populated into the input element
    assert "jazzcore" in response.text


@pytest.mark.anyio
async def test_repo_search_short_query_shows_prompt(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A single-character query renders the 'Enter at least 2 characters' prompt."""
    await _make_repo(
        db_session, owner="repo_search_short", slug="repo-search-short-album"
    )
    response = await client.get(
        "/musehub/ui/repo_search_short/repo-search-short-album/search?q=x"
    )
    assert response.status_code == 200
    assert "Enter at least 2 characters" in response.text


@pytest.mark.anyio
async def test_repo_search_htmx_fragment_returns_no_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true causes the handler to return only the fragment — no <html> shell."""
    await _make_repo(
        db_session, owner="htmx_repo_search", slug="htmx-repo-search-album"
    )
    response = await client.get(
        "/musehub/ui/htmx_repo_search/htmx-repo-search-album/search?q=zzznomatch",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    # The fragment contains the SSR-rendered empty state (no JS needed)
    assert "No results" in response.text


@pytest.mark.anyio
async def test_repo_search_no_results_shows_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A query with no matches renders the empty-state block server-side."""
    await _make_repo(
        db_session, owner="repo_search_empty", slug="repo-search-empty-album"
    )
    response = await client.get(
        "/musehub/ui/repo_search_empty/repo-search-empty-album/search?q=zzznomatch"
    )
    assert response.status_code == 200
    assert "No results" in response.text


@pytest.mark.anyio
async def test_repo_search_results_rendered_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit message appears in the HTML for in-repo keyword search (SSR via MuseCliCommit)."""
    repo_id = await _make_repo(
        db_session, owner="repo_search_ssr", slug="repo-search-ssr-album"
    )
    await _make_cli_commit(
        db_session,
        repo_id,
        message="soulful groove rhythm section unique term",
    )
    response = await client.get(
        "/musehub/ui/repo_search_ssr/repo-search-ssr-album/search?q=soulful+groove"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "soulful" in response.text
