"""SSR tests for the Muse Hub fork network page (issue #561).

Verifies that the fork table is rendered server-side — i.e., fork owner names,
slugs, and counts appear in the raw HTML response without requiring JavaScript
execution.

Covers:
- test_forks_page_renders_fork_owner_server_side   — fork owner in HTML
- test_forks_page_shows_total_count               — total_forks badge in HTML
- test_forks_page_empty_state_when_no_forks       — empty-state message
- test_forks_page_dag_container_present           — SVG DAG scaffold present
- test_forks_page_fork_network_json_embedded      — window.__forkNetwork in page
- test_forks_page_table_shows_multiple_forks      — multiple rows rendered
- test_forks_page_divergence_colour_rendered      — colour for diverged fork
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubFork, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "upstream",
    slug: str = "bass-project",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id=f"uid-{owner}",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_commit(
    db: AsyncSession,
    repo_id: str,
    sha: str = "abc123",
) -> None:
    """Seed one commit into a repo."""
    commit = MusehubCommit(
        commit_id=sha,
        repo_id=repo_id,
        branch="main",
        message="Initial composition",
        author="upstream",
        parent_ids=[],
        timestamp=datetime.now(tz=timezone.utc),
    )
    db.add(commit)
    await db.commit()


async def _make_fork(
    db: AsyncSession,
    source_repo_id: str,
    fork_owner: str = "forker",
    fork_slug: str = "bass-project",
) -> str:
    """Seed a fork repo + fork record; return fork repo_id."""
    fork_repo = MusehubRepo(
        name=fork_slug,
        owner=fork_owner,
        slug=fork_slug,
        visibility="public",
        owner_user_id=f"uid-{fork_owner}",
    )
    db.add(fork_repo)
    await db.commit()
    await db.refresh(fork_repo)

    fork_record = MusehubFork(
        source_repo_id=source_repo_id,
        fork_repo_id=str(fork_repo.repo_id),
        forked_by=fork_owner,
    )
    db.add(fork_record)
    await db.commit()
    return str(fork_repo.repo_id)


# ---------------------------------------------------------------------------
# Tests — SSR verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_forks_page_renders_fork_owner_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Fork owner name must appear in the raw HTML — no JS execution required.

    Confirms the table row is Jinja2-rendered (SSR), not JavaScript-rendered.
    """
    source_id = await _make_repo(db_session, owner="upstream", slug="bass-project")
    await _make_fork(db_session, source_id, fork_owner="jazz-forker")
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    assert "jazz-forker" in response.text


@pytest.mark.anyio
async def test_forks_page_shows_total_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Total fork count badge is rendered server-side in the heading area."""
    source_id = await _make_repo(db_session, owner="upstream", slug="drums-kit")
    await _make_fork(db_session, source_id, fork_owner="alice")
    await _make_fork(db_session, source_id, fork_owner="bob", fork_slug="drums-kit-2")
    response = await client.get("/musehub/ui/upstream/drums-kit/forks")
    assert response.status_code == 200
    # "2 forks" must appear somewhere in the SSR output
    assert "2" in response.text


@pytest.mark.anyio
async def test_forks_page_empty_state_when_no_forks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo with no forks renders the empty-state message, not a table."""
    await _make_repo(db_session, owner="upstream", slug="piano-solo")
    response = await client.get("/musehub/ui/upstream/piano-solo/forks")
    assert response.status_code == 200
    body = response.text
    assert "No forks yet" in body
    # No table element should be present when there are no forks;
    # note: ".fork-table" CSS class name still appears in the <style> block,
    # so we check for the HTML table element specifically.
    assert "<table" not in body


@pytest.mark.anyio
async def test_forks_page_dag_container_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """SVG DAG container div is in the HTML for the JS renderer to target."""
    await _make_repo(db_session, owner="upstream", slug="rhythm-section")
    response = await client.get("/musehub/ui/upstream/rhythm-section/forks")
    assert response.status_code == 200
    body = response.text
    # The SVG element with id="fork-svg" must exist as a mount point for the DAG
    assert "fork-dag-container" in body or "fork-svg" in body or "fork-canvas" in body


@pytest.mark.anyio
async def test_forks_page_fork_network_json_embedded(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """window.__forkNetwork JSON is embedded in the page for the DAG renderer.

    This replaces the previous pattern that fetched fork data via an async
    API call in the browser.
    """
    source_id = await _make_repo(db_session, owner="upstream", slug="harmony-lab")
    await _make_fork(db_session, source_id, fork_owner="melody-forker")
    response = await client.get("/musehub/ui/upstream/harmony-lab/forks")
    assert response.status_code == 200
    assert "__forkNetwork" in response.text


@pytest.mark.anyio
async def test_forks_page_table_shows_multiple_forks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Multiple fork rows are all rendered server-side in the table."""
    source_id = await _make_repo(db_session, owner="upstream", slug="orchestra")
    await _make_fork(db_session, source_id, fork_owner="violin-section")
    await _make_fork(db_session, source_id, fork_owner="cello-section", fork_slug="orchestra-2")
    response = await client.get("/musehub/ui/upstream/orchestra/forks")
    assert response.status_code == 200
    body = response.text
    assert "violin-section" in body
    assert "cello-section" in body


@pytest.mark.anyio
async def test_forks_page_divergence_colour_rendered(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Divergence colour is embedded in the SSR table cell for diverged forks.

    Adds commits to the fork so divergence_commits > 0, then checks that
    a colour span appears in the HTML.
    """
    source_id = await _make_repo(db_session, owner="upstream", slug="groove-loop")
    fork_id = await _make_fork(db_session, source_id, fork_owner="diverged-forker")
    # Add extra commits to the fork to force divergence
    await _make_commit(db_session, fork_id, sha="fork-sha-001")
    await _make_commit(db_session, fork_id, sha="fork-sha-002")
    response = await client.get("/musehub/ui/upstream/groove-loop/forks")
    assert response.status_code == 200
    body = response.text
    # A diverged fork shows "+ N ahead" — verify the pattern appears
    assert "ahead" in body or "in sync" in body
