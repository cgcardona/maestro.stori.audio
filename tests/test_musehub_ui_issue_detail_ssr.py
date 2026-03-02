"""Tests for the SSR issue detail page — HTMX SSR + comment threading (issue #568).

Covers server-side rendering of issue body, comment thread, HTMX fragment
responses, status action buttons, sidebar, and 404 handling.

Test areas:
  Basic rendering
  - test_issue_detail_renders_title_server_side
  - test_issue_detail_unknown_number_404

  SSR body content
  - test_issue_detail_renders_body_markdown
  - test_issue_detail_empty_body_shows_placeholder

  Comments
  - test_issue_detail_renders_comments_server_side
  - test_issue_detail_no_comments_shows_placeholder

  HTMX attributes
  - test_issue_detail_comment_form_has_hx_post
  - test_issue_detail_close_button_has_hx_post
  - test_issue_detail_reopen_button_has_hx_post

  HTMX fragment
  - test_issue_detail_htmx_request_returns_comment_fragment
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubIssue, MusehubIssueComment, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "songwriter",
    slug: str = "melodies",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-songwriter",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_issue(
    db: AsyncSession,
    repo_id: str,
    *,
    number: int = 1,
    title: str = "Verse needs a bridge",
    body: str = "The verse feels incomplete.",
    state: str = "open",
    author: str = "songwriter",
    labels: list[str] | None = None,
) -> MusehubIssue:
    """Seed an issue and return it."""
    issue = MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body=body,
        state=state,
        labels=labels or [],
        author=author,
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


async def _make_comment(
    db: AsyncSession,
    issue_id: str,
    repo_id: str,
    *,
    author: str = "producer",
    body: str = "Good point.",
    parent_id: str | None = None,
) -> MusehubIssueComment:
    """Seed a comment and return it."""
    comment = MusehubIssueComment(
        issue_id=issue_id,
        repo_id=repo_id,
        author=author,
        body=body,
        parent_id=parent_id,
        musical_refs=[],
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def _get_detail(
    client: AsyncClient,
    number: int = 1,
    owner: str = "songwriter",
    slug: str = "melodies",
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Fetch the issue detail page; return (status_code, body_text)."""
    resp = await client.get(
        f"/musehub/ui/{owner}/{slug}/issues/{number}",
        headers=headers or {},
    )
    return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_detail_renders_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue title appears in the HTML rendered on the server."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, title="Chorus hook is off-key")

    status, body = await _get_detail(client)

    assert status == 200
    assert "Chorus hook is off-key" in body


@pytest.mark.anyio
async def test_issue_detail_unknown_number_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A non-existent issue number returns 404."""
    await _make_repo(db_session)

    resp = await client.get("/musehub/ui/songwriter/melodies/issues/999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSR body content
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_detail_renders_body_markdown(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue body with Markdown bold is rendered as <strong> in the HTML."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, body="The **bass line** needs work.")

    status, body = await _get_detail(client)

    assert status == 200
    assert "<strong>bass line</strong>" in body


@pytest.mark.anyio
async def test_issue_detail_empty_body_shows_placeholder(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An issue with empty body renders the 'No description provided' placeholder."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, body="")

    status, body = await _get_detail(client)

    assert status == 200
    assert "No description provided" in body


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_detail_renders_comments_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A seeded comment body appears in the rendered HTML."""
    repo_id = await _make_repo(db_session)
    issue = await _make_issue(db_session, repo_id)
    await _make_comment(db_session, issue.issue_id, repo_id, body="Agreed, bridge it up!")

    status, body = await _get_detail(client)

    assert status == 200
    assert "Agreed, bridge it up!" in body


@pytest.mark.anyio
async def test_issue_detail_no_comments_shows_placeholder(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When there are no comments the placeholder text is rendered."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id)

    status, body = await _get_detail(client)

    assert status == 200
    assert "No comments yet" in body


# ---------------------------------------------------------------------------
# HTMX attributes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_detail_comment_form_has_hx_post(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The comment form exposes an hx-post attribute for HTMX submission."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id)

    status, body = await _get_detail(client)

    assert status == 200
    assert "hx-post" in body


@pytest.mark.anyio
async def test_issue_detail_close_button_has_hx_post(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An open issue renders a close button form with hx-post."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, state="open")

    status, body = await _get_detail(client)

    assert status == 200
    assert "Close issue" in body
    assert "hx-post" in body


@pytest.mark.anyio
async def test_issue_detail_reopen_button_has_hx_post(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A closed issue renders a reopen button form with hx-post."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, state="closed")

    status, body = await _get_detail(client)

    assert status == 200
    assert "Reopen issue" in body
    assert "hx-post" in body


# ---------------------------------------------------------------------------
# HTMX fragment
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_detail_htmx_request_returns_comment_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with HX-Request: true returns the comment fragment (no full page shell)."""
    repo_id = await _make_repo(db_session)
    issue = await _make_issue(db_session, repo_id)
    await _make_comment(db_session, issue.issue_id, repo_id, body="Fragment comment here.")

    status, body = await _get_detail(client, headers={"HX-Request": "true"})

    assert status == 200
    assert "Fragment comment here." in body
    # Fragment must not include the full page chrome
    assert "<html" not in body
    assert "<!DOCTYPE" not in body
