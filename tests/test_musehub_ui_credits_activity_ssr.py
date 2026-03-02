"""SSR tests for the Muse Hub credits and activity pages (issue #574).

Verifies that ``GET /musehub/ui/{owner}/{repo_slug}/credits`` and
``GET /musehub/ui/{owner}/{repo_slug}/activity`` render data server-side
rather than relying on client-side JavaScript fetches.

Tests:
- test_credits_page_renders_contributor_name_server_side
  — Seed a commit, GET credits page, assert author name in HTML
- test_credits_page_shows_total_contributors
  — Total contributor count is present in SSR HTML
- test_activity_page_renders_event_server_side
  — Seed an event, GET activity page, assert event description in HTML
- test_activity_page_filter_form_has_hx_get
  — Filter form has hx-get attribute for HTMX partial updates
- test_activity_page_htmx_fragment_path
  — HX-Request: true returns fragment only (no <html>)
- test_activity_page_event_type_filter
  — ?event_type=commit_pushed returns only matching events
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubEvent, MusehubRepo

_OWNER = "stori-artist"
_SLUG = "debut-album"
_USER_ID = "550e8400-e29b-41d4-a716-446655440000"  # matches test_user fixture


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a repo and return its repo_id string."""
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


async def _make_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    author: str = "alice",
    message: str = "Add melody",
) -> MusehubCommit:
    """Seed a commit and return the ORM object."""
    commit = MusehubCommit(
        commit_id=str(uuid.uuid4())[:16],
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message=message,
        author=author,
        timestamp=datetime.now(tz=timezone.utc),
    )
    db.add(commit)
    await db.commit()
    await db.refresh(commit)
    return commit


async def _make_event(
    db: AsyncSession,
    repo_id: str,
    *,
    event_type: str = "commit_pushed",
    actor: str = "alice",
    description: str = "Pushed a commit",
) -> MusehubEvent:
    """Seed an activity event and return the ORM object."""
    event = MusehubEvent(
        event_id=str(uuid.uuid4()),
        repo_id=repo_id,
        event_type=event_type,
        actor=actor,
        description=description,
        event_metadata={},
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Credits page SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credits_page_renders_contributor_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Author name appears in the credits HTML response without a JS round-trip.

    The handler aggregates contributor credits from commit history server-side
    and inlines them into the Jinja2 template.
    """
    repo_id = await _make_repo(db_session)
    await _make_commit(db_session, repo_id, author="charlie-contributor")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/credits")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "charlie-contributor" in resp.text


@pytest.mark.anyio
async def test_credits_page_shows_total_contributors(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Total contributor count is rendered server-side in the credits HTML."""
    repo_id = await _make_repo(db_session)
    await _make_commit(db_session, repo_id, author="alice")
    await _make_commit(db_session, repo_id, author="bob")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/credits")
    assert resp.status_code == 200
    # The template renders e.g. "2 contributors"
    assert "2 contributor" in resp.text


# ---------------------------------------------------------------------------
# Activity page SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_activity_page_renders_event_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Event description appears in the activity HTML response without a JS round-trip.

    The handler fetches events from the DB server-side and inlines them
    into the Jinja2 template.
    """
    repo_id = await _make_repo(db_session)
    await _make_event(
        db_session, repo_id,
        event_type="commit_pushed",
        actor="dana",
        description="Pushed feat/synth-bass",
    )

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/activity")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Pushed feat/synth-bass" in resp.text


@pytest.mark.anyio
async def test_activity_page_filter_form_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """The event-type filter form has hx-get for HTMX partial updates."""
    await _make_repo(db_session)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/activity")
    assert resp.status_code == 200
    assert "hx-get" in resp.text


@pytest.mark.anyio
async def test_activity_page_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """GET with HX-Request: true returns the rows fragment, not the full page.

    When HTMX issues a partial swap request it sends HX-Request: true.  The
    response must NOT contain full-page chrome and MUST contain the event rows.
    """
    repo_id = await _make_repo(db_session)
    await _make_event(
        db_session, repo_id,
        event_type="pr_opened",
        actor="eve",
        description="Opened PR: add chorus",
    )

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/activity",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" not in resp.text
    assert "<!DOCTYPE html>" not in resp.text
    assert "Opened PR: add chorus" in resp.text


@pytest.mark.anyio
async def test_activity_page_event_type_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """?event_type=commit_pushed renders only commit_pushed events in the HTML."""
    repo_id = await _make_repo(db_session)
    await _make_event(
        db_session, repo_id,
        event_type="commit_pushed",
        actor="frank",
        description="Pushed commit: add bassline",
    )
    await _make_event(
        db_session, repo_id,
        event_type="pr_merged",
        actor="grace",
        description="Merged PR: horn section",
    )

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/activity",
        params={"event_type": "commit_pushed"},
    )
    assert resp.status_code == 200
    assert "Pushed commit: add bassline" in resp.text
    assert "Merged PR: horn section" not in resp.text
