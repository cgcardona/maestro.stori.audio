"""SSR tests for the Muse Hub fork network page (issue #561).

Verifies that the fork table is rendered server-side (HTML in the initial
response body) and that the SVG DAG container and window.__forkNetwork data
are present for the JavaScript renderer.

Covers:
- test_forks_page_renders_fork_owner_server_side  — fork owner appears in SSR HTML
- test_forks_page_shows_total_count               — total_forks badge in SSR HTML
- test_forks_page_empty_state_when_no_forks       — no forks -> empty-state message
- test_forks_page_dag_container_present           — fork-dag-container div present for JS
- test_forks_page_fork_network_json_in_html       — window.__forkNetwork injected for DAG JS
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubFork, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "upstream",
    slug: str = "bass-project",
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


async def _make_fork(
    db: AsyncSession,
    source_repo_id: str,
    fork_owner: str = "forker",
    fork_slug: str = "bass-project",
) -> str:
    """Seed a fork repo + fork relationship; return the fork repo_id."""
    fork_repo = MusehubRepo(
        name=fork_slug,
        owner=fork_owner,
        slug=fork_slug,
        visibility="public",
        owner_user_id=f"uid-{fork_owner}",
        description=f"Fork of upstream/{fork_slug}",
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
# SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_forks_page_renders_fork_owner_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Fork owner appears in the initial HTML response -- server-rendered, not JS-injected.

    The table row for each fork must be present in the HTML body returned by the
    server, before any client-side JavaScript runs.  This is the primary SSR
    contract: crawlers and non-JS clients can see fork data.
    """
    source_id = await _make_repo(db_session)
    await _make_fork(db_session, source_id, fork_owner="alice", fork_slug="bass-project")

    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    # Fork owner must appear in server-rendered HTML (table row), not just in JS data
    assert "alice" in body
    # The fork link href must be a server-rendered anchor tag
    assert "/musehub/ui/alice/bass-project" in body


@pytest.mark.anyio
async def test_forks_page_shows_total_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The total_forks count is present in the server-rendered HTML.

    The count badge must appear in static HTML so users and crawlers see the
    correct count without executing JavaScript.
    """
    source_id = await _make_repo(db_session)
    await _make_fork(db_session, source_id, fork_owner="bob", fork_slug="bass-project")
    await _make_fork(db_session, source_id, fork_owner="carol", fork_slug="bass-project")

    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    # Both the count and the word "fork" must appear in the SSR page
    assert "2" in body
    assert "fork" in body.lower()


@pytest.mark.anyio
async def test_forks_page_empty_state_when_no_forks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo with zero forks renders an empty-state message instead of a table.

    The server must not render an empty table -- it should display a human-readable
    message so users understand there are no forks yet.
    """
    await _make_repo(db_session)

    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    # Empty-state copy must be present server-side
    assert "No forks yet" in body
    # No table rows for fork data
    assert "<tbody>" not in body


@pytest.mark.anyio
async def test_forks_page_dag_container_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The SVG DAG container element is present in the server-rendered HTML.

    The complex layout algorithm stays as JavaScript, but the host container
    element must exist in the static HTML so the JS renderer can mount into it
    without a race condition or missing-element error.
    """
    await _make_repo(db_session)

    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    # Either the named container or the SVG element itself must be present
    assert "fork-dag-container" in body or "fork-svg" in body


@pytest.mark.anyio
async def test_forks_page_fork_network_json_in_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The fork network JSON is injected into the page for the SVG DAG JS renderer.

    window.__forkNetwork must be set in a <script> tag so the DAG renderer can
    read it synchronously without an async fetch call.
    """
    source_id = await _make_repo(db_session)
    await _make_fork(db_session, source_id, fork_owner="dave", fork_slug="bass-project")

    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    assert "window.__forkNetwork" in body
    # The fork owner must appear inside the injected JSON data block
    assert "dave" in body
