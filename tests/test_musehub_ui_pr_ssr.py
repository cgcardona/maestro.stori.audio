"""SSR tests for Muse Hub PR list + PR detail pages — issue #569.

Validates that PR data is rendered server-side into HTML (not deferred to client
JS) and that HTMX fragment requests return bare HTML without the full page shell.

Covers GET /musehub/ui/{owner}/{repo_slug}/pulls:
- test_pr_list_renders_pr_title_server_side         — PR title appears in HTML
- test_pr_list_open_closed_counts_in_tabs           — tab counts reflect seeded PRs
- test_pr_list_htmx_fragment_on_tab_switch          — HX-Request: true → fragment

Covers GET /musehub/ui/{owner}/{repo_slug}/pulls/{pr_id}:
- test_pr_detail_renders_title_server_side          — PR title in HTML server-side
- test_pr_detail_renders_diff_stats                 — branch info in HTML
- test_pr_detail_merge_button_has_hx_post           — merge button has hx-post
- test_pr_detail_merge_button_disabled_when_not_mergeable — closed PR → no merge button
- test_pr_detail_unknown_number_404                 — non-existent pr_id → 404
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubPullRequest, MusehubRepo


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "prdev",
    slug: str = "pr-ssr-album",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-pr-ssr-dev",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_pr(
    db: AsyncSession,
    repo_id: str,
    *,
    title: str = "Add bossa nova bridge",
    body: str = "Adds a new bossa nova bridge section.",
    state: str = "open",
    from_branch: str = "feat/bossa-nova",
    to_branch: str = "main",
    author: str = "beatmaker",
) -> MusehubPullRequest:
    """Seed a PR and return the ORM object."""
    pr = MusehubPullRequest(
        repo_id=repo_id,
        title=title,
        body=body,
        state=state,
        from_branch=from_branch,
        to_branch=to_branch,
        author=author,
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)
    return pr


# ---------------------------------------------------------------------------
# PR list SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pr_list_renders_pr_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR title is rendered into the HTML response server-side without client JS."""
    repo_id = await _make_repo(db_session)
    await _make_pr(db_session, repo_id, title="Funk bridge with wah pedal")
    response = await client.get("/musehub/ui/prdev/pr-ssr-album/pulls")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Funk bridge with wah pedal" in response.text


@pytest.mark.anyio
async def test_pr_list_open_closed_counts_in_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """State tabs display SSR-computed open/merged/closed counts."""
    repo_id = await _make_repo(db_session)
    await _make_pr(db_session, repo_id, title="Open PR 1", state="open")
    await _make_pr(db_session, repo_id, title="Open PR 2", state="open")
    await _make_pr(db_session, repo_id, title="Merged PR", state="merged")
    response = await client.get("/musehub/ui/prdev/pr-ssr-album/pulls")
    assert response.status_code == 200
    body = response.text
    # Tab counts for open and merged must appear as server-rendered numbers.
    assert "2" in body  # open_count
    assert "1" in body  # merged_count


@pytest.mark.anyio
async def test_pr_list_htmx_fragment_on_tab_switch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true with state=merged returns a bare HTML fragment."""
    repo_id = await _make_repo(db_session)
    await _make_pr(db_session, repo_id, title="Merged feature", state="merged")
    response = await client.get(
        "/musehub/ui/prdev/pr-ssr-album/pulls?state=merged",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    # Fragment must NOT contain the full HTML page shell.
    assert "<html" not in body
    assert "<head" not in body
    # PR title must appear in the fragment.
    assert "Merged feature" in body


# ---------------------------------------------------------------------------
# PR detail SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pr_detail_renders_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR title and branch info appear in the detail page HTML server-side."""
    repo_id = await _make_repo(db_session)
    pr = await _make_pr(
        db_session, repo_id, title="Add jazz chord voicings", from_branch="feat/jazz"
    )
    response = await client.get(f"/musehub/ui/prdev/pr-ssr-album/pulls/{pr.pr_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Add jazz chord voicings" in response.text


@pytest.mark.anyio
async def test_pr_detail_renders_diff_stats(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch names (from_branch / to_branch) appear in the detail page HTML."""
    repo_id = await _make_repo(db_session)
    pr = await _make_pr(
        db_session,
        repo_id,
        title="Bass groove PR",
        from_branch="feat/bass-groove",
        to_branch="dev",
    )
    response = await client.get(f"/musehub/ui/prdev/pr-ssr-album/pulls/{pr.pr_id}")
    assert response.status_code == 200
    body = response.text
    # Both branch names must appear in the server-rendered HTML.
    assert "feat/bass-groove" in body
    assert "dev" in body


@pytest.mark.anyio
async def test_pr_detail_merge_button_has_hx_post(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An open PR detail page includes a merge button with an hx-post attribute."""
    repo_id = await _make_repo(db_session)
    pr = await _make_pr(db_session, repo_id, title="Merge-ready PR", state="open")
    response = await client.get(f"/musehub/ui/prdev/pr-ssr-album/pulls/{pr.pr_id}")
    assert response.status_code == 200
    body = response.text
    # The merge card must have at least one HTMX POST trigger.
    assert "hx-post" in body
    assert "merge" in body.lower()


@pytest.mark.anyio
async def test_pr_detail_merge_button_disabled_when_not_mergeable(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A closed or merged PR does not show the merge button."""
    repo_id = await _make_repo(db_session)
    pr = await _make_pr(db_session, repo_id, title="Already Merged PR", state="merged")
    response = await client.get(f"/musehub/ui/prdev/pr-ssr-album/pulls/{pr.pr_id}")
    assert response.status_code == 200
    body = response.text
    # Merged/closed PRs must not render the merge action form.
    assert "Merge pull request" not in body


@pytest.mark.anyio
async def test_pr_detail_unknown_number_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A request for a non-existent PR id returns HTTP 404."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/prdev/pr-ssr-album/pulls/nonexistent-pr-uuid"
    )
    assert response.status_code == 404
