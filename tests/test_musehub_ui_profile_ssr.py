"""SSR tests for the Muse Hub user profile page (issue #563).

Verifies that profile data is rendered server-side — username, bio, and
heatmap cells appear in the raw HTML response without requiring JavaScript.

Covers:
- test_profile_page_renders_username_server_side  — username in HTML
- test_profile_page_renders_bio_server_side       — bio text in HTML
- test_profile_page_shows_heatmap_grid            — heatmap-cell elements present
- test_profile_page_no_inline_html_builder        — _render_profile_html removed
- test_profile_page_unknown_user_404              — unknown username → 404
- test_profile_page_json_path_still_works         — ?format=json returns JSON
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_profile(
    db: AsyncSession,
    username: str = "testuser",
    bio: str | None = "A musician on Muse Hub",
) -> MusehubProfile:
    """Seed a MusehubProfile and return it."""
    profile = MusehubProfile(
        user_id=f"uid-{username}",
        username=username,
        bio=bio,
        pinned_repo_ids=[],
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# Tests — SSR verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_page_renders_username_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Username must appear in the raw HTML — no JS execution required.

    Confirms the profile header is Jinja2-rendered (SSR), not JS-rendered.
    """
    await _make_profile(db_session, username="jazzcat")
    response = await client.get("/musehub/ui/users/jazzcat")
    assert response.status_code == 200
    assert "jazzcat" in response.text


@pytest.mark.anyio
async def test_profile_page_renders_bio_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Bio text must appear in the raw HTML — server-side rendered."""
    await _make_profile(db_session, username="bioperson", bio="Jazz guitarist from NYC")
    response = await client.get("/musehub/ui/users/bioperson")
    assert response.status_code == 200
    assert "Jazz guitarist from NYC" in response.text


@pytest.mark.anyio
async def test_profile_page_shows_heatmap_grid(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Heatmap cells are present in the SSR HTML.

    With 52 full weeks + partial, we expect the heatmap-cell class to appear
    in the HTML for the CSS grid.
    """
    await _make_profile(db_session, username="heatmapper")
    response = await client.get("/musehub/ui/users/heatmapper")
    assert response.status_code == 200
    assert "heatmap-cell" in response.text


@pytest.mark.anyio
async def test_profile_page_no_inline_html_builder(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """_render_profile_html must no longer be referenced in the module.

    The inline HTML builder was a ~310-line f-string anti-pattern.  After
    SSR migration it must be deleted entirely.
    """
    import maestro.api.routes.musehub.ui_user_profile as mod

    assert not hasattr(mod, "_render_profile_html"), (
        "_render_profile_html must be deleted — the SSR migration replaced it "
        "with a Jinja2 template rendered server-side."
    )


@pytest.mark.anyio
async def test_profile_page_unknown_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET an unknown username returns 404."""
    response = await client.get("/musehub/ui/users/nobody-here-xyz-999")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_page_json_path_still_works(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns EnhancedProfileResponse with expected top-level keys."""
    await _make_profile(db_session, username="jsonuser", bio="Machine-readable profile")
    response = await client.get("/musehub/ui/users/jsonuser?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "jsonuser"
    assert "heatmap" in data
    assert "badges" in data
    assert "pinnedRepos" in data
    assert "activity" in data
