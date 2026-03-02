"""SSR tests for the Muse Hub branches and tags pages (issue #571).

Verifies that ``GET /musehub/ui/{owner}/{repo_slug}/branches`` and
``GET /musehub/ui/{owner}/{repo_slug}/tags`` render data server-side rather
than relying on client-side JavaScript fetches.

Tests:
- test_branches_page_renders_branch_name_server_side
  — Seed a branch, GET the page, assert name appears in HTML
- test_branches_page_marks_default_branch
  — Default branch has "default" badge in server-rendered HTML
- test_branches_htmx_fragment_path
  — GET with HX-Request: true returns fragment without full page chrome
- test_branches_page_empty_state_when_no_branches
  — No branches → empty-state rendered server-side
- test_branches_page_compare_link_for_non_default
  — Non-default branch renders a Compare action link
- test_tags_page_renders_tag_name_server_side
  — Seed a release/tag, GET the page, assert tag name in HTML
- test_tags_page_empty_state_when_no_tags
  — No releases → empty-state rendered server-side
- test_tags_page_namespace_filter
  — ?namespace=emotion filters to only tags in that namespace
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubRelease, MusehubRepo

_OWNER = "composer"
_SLUG = "symphony-draft"
_USER_ID = "550e8400-e29b-41d4-a716-446655440000"  # matches test_user fixture


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id string."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id=_USER_ID,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_branch(
    db: AsyncSession,
    repo_id: str,
    *,
    name: str = "main",
    head_commit_id: str | None = None,
) -> MusehubBranch:
    """Seed a branch and return the ORM object."""
    branch = MusehubBranch(
        repo_id=repo_id,
        name=name,
        head_commit_id=head_commit_id,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


async def _make_release(
    db: AsyncSession,
    repo_id: str,
    *,
    tag: str = "v1.0",
    title: str = "First release",
    commit_id: str | None = None,
) -> MusehubRelease:
    """Seed a release (tag source) and return the ORM object."""
    release = MusehubRelease(
        repo_id=repo_id,
        tag=tag,
        title=title,
        commit_id=commit_id,
        author=_OWNER,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return release


# ---------------------------------------------------------------------------
# Branches SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_branches_page_renders_branch_name_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Branch name appears in the HTML without a client-side JS round-trip."""
    repo_id = await _make_repo(db_session)
    await _make_branch(db_session, repo_id, name="feat/ssr-migration")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/branches", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "feat/ssr-migration" in body
    assert "branch-row" in body


@pytest.mark.anyio
async def test_branches_page_marks_default_branch(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """The default branch ("main") shows the 'default' badge in server-rendered HTML."""
    repo_id = await _make_repo(db_session)
    await _make_branch(db_session, repo_id, name="main")
    await _make_branch(db_session, repo_id, name="feat/other")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/branches", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "main" in body
    assert "default" in body


@pytest.mark.anyio
async def test_branches_htmx_fragment_path(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """GET with HX-Request: true returns only the branch rows fragment, not the full page."""
    repo_id = await _make_repo(db_session)
    await _make_branch(db_session, repo_id, name="feat/htmx-swap")
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/branches", headers=htmx_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "feat/htmx-swap" in body
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body


@pytest.mark.anyio
async def test_branches_page_empty_state_when_no_branches(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Empty branch list renders the empty-state component server-side (no JS fetch needed)."""
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/branches", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "No branches" in body or "empty-state" in body
    assert 'class="branch-row"' not in body


@pytest.mark.anyio
async def test_branches_page_compare_link_for_non_default(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Non-default branches render a Compare action link pointing to the diff URL."""
    repo_id = await _make_repo(db_session)
    await _make_branch(db_session, repo_id, name="main")
    await _make_branch(db_session, repo_id, name="feat/new-bridge")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/branches", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "feat/new-bridge" in body
    assert "compare" in body.lower() or "Compare" in body


# ---------------------------------------------------------------------------
# Tags SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tags_page_renders_tag_name_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Tag name appears in the HTML without a client-side JS round-trip."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="v2.0", title="Major release")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/tags", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "v2.0" in body
    assert "tag-row" in body or "tag" in body.lower()


@pytest.mark.anyio
async def test_tags_page_empty_state_when_no_tags(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Empty tag list renders the empty-state component server-side."""
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/tags", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "No tags" in body or "empty-state" in body


@pytest.mark.anyio
async def test_tags_page_namespace_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """?namespace=emotion shows only emotion-namespaced tags, hiding version tags."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="emotion:happy", title="Happy mood tag")
    await _make_release(db_session, repo_id, tag="v1.0", title="Version release")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/tags?namespace=emotion", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "emotion:happy" in body
    assert "v1.0" not in body
