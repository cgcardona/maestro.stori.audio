"""Tests for Muse Hub fork network UI endpoint (issue #431).

Covers GET /musehub/ui/{owner}/{repo_slug}/forks:

- test_forks_page_returns_200                  — page renders without auth
- test_forks_page_no_auth_required             — no JWT needed for HTML shell
- test_forks_page_has_svg_dag_markup           — SVG DAG scaffold present in HTML
- test_forks_page_has_legend                   — divergence colour legend present
- test_forks_page_has_compare_button_js        — "Compare" action JS present
- test_forks_page_has_contribute_upstream_js   — "Contribute upstream" action JS present
- test_forks_page_json_response                — ?format=json returns ForkNetworkResponse
- test_forks_page_json_has_root_and_total      — JSON contains root and totalForks fields
- test_forks_page_json_children_present        — fork children appear in JSON root.children
- test_forks_page_json_divergence_computed     — divergence_commits field is non-negative int
- test_forks_page_unknown_repo_404             — unknown owner/slug → 404
- test_forks_page_base_url_in_html             — HTML uses owner/slug base URL pattern
- test_forks_page_json_empty_repo              — repo with no forks returns total_forks=0
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from maestro.db.musehub_models import MusehubCommit, MusehubFork, MusehubRepo


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


async def _make_commit(db: AsyncSession, repo_id: str, sha: str = "abc123", branch: str = "main") -> None:
    """Seed a single commit into a repo."""
    commit = MusehubCommit(
        commit_id=sha,
        repo_id=repo_id,
        branch=branch,
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
    """Seed a fork repo and fork relationship; return fork's repo_id."""
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
# Tests — HTML shell
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_forks_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/forks returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_forks_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Fork network page is publicly accessible — no JWT needed."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_forks_page_has_svg_dag_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page HTML includes an SVG element as the DAG scaffold."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    assert "fork-svg" in body or "fork-canvas" in body


@pytest.mark.anyio
async def test_forks_page_has_legend(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page contains a divergence colour legend."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    body = response.text
    assert "legend" in body or "In sync" in body or "ahead" in body


@pytest.mark.anyio
async def test_forks_page_has_compare_button_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page JavaScript includes Compare action."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    assert "Compare" in response.text


@pytest.mark.anyio
async def test_forks_page_has_contribute_upstream_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page JavaScript includes Contribute upstream action."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    assert "Contribute upstream" in response.text or "contribute" in response.text.lower()


@pytest.mark.anyio
async def test_forks_page_base_url_in_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML uses the owner/slug base URL, not raw repo_id UUIDs."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks")
    assert response.status_code == 200
    assert "/musehub/ui/upstream/bass-project" in response.text


# ---------------------------------------------------------------------------
# Tests — JSON path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_forks_page_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns HTTP 200 with application/json content-type."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_forks_page_json_has_root_and_total(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response contains root node and totalForks counter."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks?format=json")
    assert response.status_code == 200
    data = response.json()
    assert "root" in data
    assert "totalForks" in data


@pytest.mark.anyio
async def test_forks_page_json_children_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A fork repo appears as a child node in the JSON root.children list."""
    source_id = await _make_repo(db_session)
    await _make_fork(db_session, source_id, fork_owner="alice", fork_slug="bass-project")
    response = await client.get("/musehub/ui/upstream/bass-project/forks?format=json")
    assert response.status_code == 200
    data = response.json()
    children = data["root"]["children"]
    assert len(children) == 1
    assert children[0]["owner"] == "alice"
    assert children[0]["repoSlug"] == "bass-project"


@pytest.mark.anyio
async def test_forks_page_json_divergence_computed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """divergenceCommits is a non-negative integer for each fork child."""
    source_id  = await _make_repo(db_session)
    fork_id    = await _make_fork(db_session, source_id, fork_owner="bob", fork_slug="bass-project")
    # Add a commit to the fork so divergence > 0
    await _make_commit(db_session, fork_id, sha="fork-commit-001")
    response = await client.get("/musehub/ui/upstream/bass-project/forks?format=json")
    assert response.status_code == 200
    data = response.json()
    children = data["root"]["children"]
    assert len(children) == 1
    div = children[0]["divergenceCommits"]
    assert isinstance(div, int)
    assert div >= 0


@pytest.mark.anyio
async def test_forks_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404."""
    response = await client.get("/musehub/ui/nobody/nonexistent/forks")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_forks_page_json_empty_repo(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo with no forks returns totalForks=0 and empty children list."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/upstream/bass-project/forks?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["totalForks"] == 0
    assert data["root"]["children"] == []
