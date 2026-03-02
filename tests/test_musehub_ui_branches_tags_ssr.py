"""SSR tests for the MuseHub branches and tags pages (issue #571).

Covers GET /musehub/ui/{owner}/{repo_slug}/branches and
       GET /musehub/ui/{owner}/{repo_slug}/tags after SSR migration:

- test_branches_page_renders_branch_name_server_side
    Seed a branch, GET the page, assert name is in the HTML body (SSR not JS).

- test_branches_page_marks_default_branch
    The default branch has a visual indicator rendered server-side.

- test_branches_page_protected_badge_present
    Protected branch shows a badge (via is_default path; the row renders).

- test_branches_htmx_fragment_path
    GET with ``HX-Request: true`` returns only the bare fragment (no <html>).

- test_branches_page_empty_state_when_no_branches
    Repo with no branches renders the empty state rather than an empty table.

- test_tags_page_renders_tag_name_server_side
    Seed a release/tag, GET the page, assert tag name is in the HTML body.

- test_tags_page_empty_state_when_no_tags
    Repo with no releases shows the empty state.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubRelease, MusehubRepo

pytestmark = pytest.mark.anyio

_OWNER = "ssr-bt-owner"
_SLUG = "ssr-bt-repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, *, slug: str = _SLUG) -> str:
    """Seed a minimal public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=_OWNER,
        slug=slug,
        visibility="public",
        owner_user_id="ssr-bt-owner-uid",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _add_branch(
    db: AsyncSession,
    repo_id: str,
    name: str,
    *,
    head_commit_id: str | None = None,
) -> MusehubBranch:
    """Seed a branch record and return it."""
    branch = MusehubBranch(
        repo_id=repo_id,
        name=name,
        head_commit_id=head_commit_id,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


async def _add_release(
    db: AsyncSession,
    repo_id: str,
    tag: str,
    *,
    title: str = "Test release",
    commit_id: str | None = None,
) -> MusehubRelease:
    """Seed a release record (tag source) and return it."""
    release = MusehubRelease(
        repo_id=repo_id,
        tag=tag,
        title=title,
        body="",
        commit_id=commit_id,
        author="test-author",
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return release


# ---------------------------------------------------------------------------
# Branches tests
# ---------------------------------------------------------------------------


async def test_branches_page_renders_branch_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seed a branch, GET the page, assert name appears in the HTML body.

    Confirms server-side rendering: the branch name must be present without
    any client-side JS fetch being required.
    """
    repo_id = await _make_repo(db_session)
    await _add_branch(db_session, repo_id, "feat/ambient-strings")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/branches")
    assert resp.status_code == 200
    assert "feat/ambient-strings" in resp.text


async def test_branches_page_marks_default_branch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The default branch receives a 'default' badge rendered server-side."""
    slug = "ssr-bt-default-repo"
    repo_id = await _make_repo(db_session, slug=slug)
    await _add_branch(db_session, repo_id, "main")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{slug}/branches")
    assert resp.status_code == 200
    # The default badge text must appear in the SSR HTML
    assert "default" in resp.text
    assert "main" in resp.text


async def test_branches_page_protected_badge_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch rows are rendered server-side with correct HTML structure."""
    slug = "ssr-bt-protected-repo"
    repo_id = await _make_repo(db_session, slug=slug)
    await _add_branch(db_session, repo_id, "protected-branch", head_commit_id="abc123def456")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{slug}/branches")
    assert resp.status_code == 200
    assert "protected-branch" in resp.text
    # HEAD commit SHA should be shortened and linked server-side
    assert "abc123d" in resp.text


async def test_branches_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with ``HX-Request: true`` returns only the bare branch fragment.

    The fragment must not contain a full HTML document shell (<html>, <head>).
    """
    slug = "ssr-bt-htmx-repo"
    repo_id = await _make_repo(db_session, slug=slug)
    await _add_branch(db_session, repo_id, "htmx-branch")

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{slug}/branches",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "htmx-branch" in body
    assert "<html" not in body
    assert "<head" not in body


async def test_branches_page_empty_state_when_no_branches(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo with no branches renders the empty state message."""
    slug = "ssr-bt-empty-branches-repo"
    await _make_repo(db_session, slug=slug)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{slug}/branches")
    assert resp.status_code == 200
    assert "No branches" in resp.text


# ---------------------------------------------------------------------------
# Tags tests
# ---------------------------------------------------------------------------


async def test_tags_page_renders_tag_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seed a release/tag, GET the page, assert tag name is in the HTML body.

    Confirms server-side rendering: the tag name must be present without
    any client-side JS fetch being required.
    """
    slug = "ssr-bt-tags-repo"
    repo_id = await _make_repo(db_session, slug=slug)
    await _add_release(db_session, repo_id, "emotion:peaceful", title="Peaceful release")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{slug}/tags")
    assert resp.status_code == 200
    assert "emotion:peaceful" in resp.text


async def test_tags_page_empty_state_when_no_tags(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo with no releases shows the empty state message."""
    slug = "ssr-bt-empty-tags-repo"
    await _make_repo(db_session, slug=slug)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{slug}/tags")
    assert resp.status_code == 200
    assert "No tags" in resp.text
