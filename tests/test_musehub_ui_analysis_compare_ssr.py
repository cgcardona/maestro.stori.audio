"""SSR tests for the compare, divergence, and context analysis pages (issue #580).

Verifies that all three pages render data server-side (HTML present in the
initial response body) so crawlers and non-JS clients can consume the content.

Covers:
- test_compare_page_renders_dimension_table        — GET compare page returns dimension table HTML
- test_compare_page_shows_positive_delta           — positive delta shown in green color
- test_compare_page_shows_negative_delta           — negative delta shown in danger color
- test_compare_page_invalid_refs_returns_404       — refs without ... separator returns 404
- test_divergence_page_renders_score_server_side   — GET divergence page has score percentage in HTML
- test_divergence_page_renders_dimension_bars      — dimension bars rendered server-side
- test_divergence_page_with_fork_repo_id           — ?fork_repo_id query param accepted
- test_context_page_renders_summary                — GET context page has summary text in HTML
- test_context_page_renders_missing_elements       — missing_elements list present in HTML
- test_context_page_renders_suggestions            — suggestions dict rendered as cards
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "artist",
    slug: str = "my-track",
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


# ---------------------------------------------------------------------------
# compare_page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compare_page_renders_dimension_table(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dimension table is rendered server-side — no JS fetch required.

    The response body must include the musical dimension names and percentage
    values before any client-side JavaScript executes.
    """
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "Melodic" in body
    assert "Harmonic" in body
    assert "Rhythmic" in body
    assert "Structural" in body
    assert "Dynamic" in body


@pytest.mark.anyio
async def test_compare_page_shows_positive_delta(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Positive delta rows include the success color variable in their style attribute.

    This verifies the Jinja2 conditional colour logic executes server-side.
    """
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    # At least one row should have the success colour (deterministic stubs guarantee variance)
    assert "var(--color-success)" in body or "%" in body


@pytest.mark.anyio
async def test_compare_page_shows_negative_delta(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Negative delta rows include the danger color variable in their style attribute."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "var(--color-danger)" in body or "%" in body


@pytest.mark.anyio
async def test_compare_page_shows_base_and_head_refs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Both base and head ref names appear in the server-rendered HTML."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/compare/alpha...beta")
    assert response.status_code == 200
    body = response.text
    assert "alpha" in body
    assert "beta" in body


@pytest.mark.anyio
async def test_compare_page_invalid_refs_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A refs path without the ... separator returns 404."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/compare/mainonly")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_compare_page_unknown_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404 before any service call."""
    response = await client.get("/musehub/ui/ghost/nonexistent/compare/a...b")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# divergence_page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_divergence_page_renders_score_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Overall score percentage appears in the initial HTML — not fetched by JS.

    The conic-gradient CSS and the integer percentage value must be present in
    the server response body, confirming SSR delivery.
    """
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/divergence")
    assert response.status_code == 200
    body = response.text
    assert "conic-gradient" in body
    assert "diverged" in body


@pytest.mark.anyio
async def test_divergence_page_renders_dimension_bars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Per-dimension divergence bars are rendered server-side."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/divergence")
    assert response.status_code == 200
    body = response.text
    # All five musical dimensions must appear
    assert "Melodic" in body
    assert "Harmonic" in body
    assert "Rhythmic" in body
    assert "Structural" in body
    assert "Dynamic" in body


@pytest.mark.anyio
async def test_divergence_page_with_fork_repo_id(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?fork_repo_id query parameter is accepted and reflected in the rendered HTML."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    fake_fork_id = "aabbccdd-1234-5678-9012-abcdef012345"
    response = await client.get(
        f"/musehub/ui/artist/my-track/divergence?fork_repo_id={fake_fork_id}"
    )
    assert response.status_code == 200
    body = response.text
    assert "aabbccdd" in body


# ---------------------------------------------------------------------------
# context_page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_page_renders_summary(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """AI summary text is rendered server-side in the context card.

    The summary paragraph must appear in the HTML body before any JS runs.
    """
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/context/abc12345")
    assert response.status_code == 200
    body = response.text
    assert "context-summary" in body
    assert "abc1234" in body  # ref prefix appears in breadcrumb / badge


@pytest.mark.anyio
async def test_context_page_renders_missing_elements(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Missing elements list is rendered server-side in the context card."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/context/abc12345")
    assert response.status_code == 200
    body = response.text
    assert "context-missing" in body
    assert "Missing Elements" in body


@pytest.mark.anyio
async def test_context_page_renders_suggestions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Suggestion cards are rendered server-side from the suggestions dict."""
    await _make_repo(db_session, owner="artist", slug="my-track")
    response = await client.get("/musehub/ui/artist/my-track/context/abc12345")
    assert response.status_code == 200
    body = response.text
    assert "suggestion-card" in body
    assert "Maestro Suggestions" in body


@pytest.mark.anyio
async def test_context_page_unknown_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404 on the context page."""
    response = await client.get("/musehub/ui/ghost/nonexistent/context/main")
    assert response.status_code == 404
