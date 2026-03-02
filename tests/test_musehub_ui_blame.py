"""Tests for the Muse Hub blame UI page (SSR).

Covers:
- test_blame_page_renders — GET /musehub/ui/{owner}/{slug}/blame/{ref}/{path} returns 200 HTML
- test_blame_page_no_auth_required — page accessible without a JWT
- test_blame_page_unknown_repo_404 — bad owner/slug returns 404
- test_blame_page_contains_table_headers — HTML contains blame table column headers
- test_blame_page_contains_filter_bar — HTML includes track/beat filter controls
- test_blame_page_contains_breadcrumb — breadcrumb links owner, repo_slug, ref, and filename
- test_blame_page_contains_piano_roll_link — quick-link to the piano-roll page present
- test_blame_page_contains_commits_link — quick-link to the commit list present
- test_blame_json_response — Accept: application/json returns BlameResponse JSON
- test_blame_json_has_entries_key — JSON body contains 'entries' and 'totalEntries' keys
- test_blame_json_format_param — ?format=json returns JSON without Accept header
- test_blame_page_path_in_server_context — file path present in server-rendered HTML
- test_blame_page_ref_in_server_context — commit ref present in server-rendered HTML
- test_blame_page_server_side_render_present — page renders blame table server-side (no apiFetch for data)
- test_blame_page_filter_bar_track_options — track <select> lists standard instrument names
- test_blame_page_commit_sha_link — commit-sha class present in SSR template
- test_blame_page_velocity_bar_present — velocity bar element present in SSR template
- test_blame_page_beat_range_column — beat range column rendered server-side
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db_session: AsyncSession,
    *,
    owner: str = "testuser",
    slug: str = "test-beats",
    visibility: str = "public",
) -> str:
    """Seed a minimal repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id="00000000-0000-0000-0000-000000000001",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _add_commit(db_session: AsyncSession, repo_id: str) -> None:
    """Seed a single commit so blame entries are non-empty."""
    from datetime import datetime, timezone

    commit = MusehubCommit(
        repo_id=repo_id,
        commit_id="abc1234567890abcdef",
        message="Add jazz piano chords",
        author="testuser",
        branch="main",
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()


_OWNER = "testuser"
_SLUG = "test-beats"
_REF = "abc1234567890abcdef"
_PATH = "tracks/piano.mid"


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/blame/{ref}/{path} must return 200 HTML."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_blame_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blame page must be accessible without an Authorization header."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_blame_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug must return 404."""
    url = f"/musehub/ui/nobody/no-repo/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_blame_page_contains_table_headers(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Rendered HTML must contain the blame table column headers when entries exist."""
    repo_id = await _make_repo(db_session)
    await _add_commit(db_session, repo_id)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "Commit" in body
    assert "Author" in body
    assert "Track" in body
    assert "Pitch" in body
    assert "Velocity" in body


@pytest.mark.anyio
async def test_blame_page_contains_filter_bar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Rendered HTML must include the track and beat-range filter controls."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "blame-track-sel" in body
    assert "blame-beat-start" in body
    assert "blame-beat-end" in body


@pytest.mark.anyio
async def test_blame_page_contains_breadcrumb(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Breadcrumb must reference owner, repo slug, ref, and filename."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert _OWNER in body
    assert _SLUG in body
    assert _REF[:8] in body
    assert "piano.mid" in body


@pytest.mark.anyio
async def test_blame_page_contains_piano_roll_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page must include a quick-link to the piano-roll view for the same file."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "piano-roll" in body


@pytest.mark.anyio
async def test_blame_page_contains_commits_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page must include a quick-link to the commits list."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "/commits" in body


# ---------------------------------------------------------------------------
# JSON content negotiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Accept: application/json must return a JSON response (not HTML)."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_blame_json_has_entries_key(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response must contain 'entries' and 'totalEntries' keys."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url, headers={"Accept": "application/json"})
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert "totalEntries" in data
    assert isinstance(data["entries"], list)
    assert isinstance(data["totalEntries"], int)


@pytest.mark.anyio
async def test_blame_json_format_param(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json must return JSON without an Accept header."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}?format=json"
    response = await client.get(url)
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    assert "entries" in data


# ---------------------------------------------------------------------------
# JS context variable injection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_page_path_in_server_context(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The MIDI file path must appear in the server-rendered HTML."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert _PATH in body


@pytest.mark.anyio
async def test_blame_page_ref_in_server_context(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The commit ref (short form) must appear in the server-rendered HTML."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # short_ref is the first 8 chars; full ref also appears in breadcrumb links
    assert _REF[:8] in body


@pytest.mark.anyio
async def test_blame_page_server_side_render_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blame content must be rendered server-side — filter form and blame div in HTML."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    # Filter form is present (server-rendered)
    assert "blame-filter-bar" in body
    # Table structure or empty state always rendered server-side (no loading placeholder)
    assert "blame-header" in body
    assert "Loading" not in body


# ---------------------------------------------------------------------------
# UI element assertions in JS template strings
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_page_filter_bar_track_options(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Track <select> must list standard instrument track names."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    for instrument in ("piano", "bass", "drums", "keys"):
        assert instrument in body


@pytest.mark.anyio
async def test_blame_page_pitch_badge_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """pitch-badge CSS class must appear in the SSR blame table."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "pitch-badge" in body


@pytest.mark.anyio
async def test_blame_page_commit_sha_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """commit-sha CSS class must appear in the server-rendered blame table."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "commit-sha" in body


@pytest.mark.anyio
async def test_blame_page_velocity_bar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Velocity bar element must appear in the JS table template."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "velocity-bar" in body
    assert "velocity-fill" in body


@pytest.mark.anyio
async def test_blame_page_beat_range_column(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Beat range column must appear in the server-rendered blame table."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "beat-range" in body
    # Filter form uses HTML name attributes for beat range inputs
    assert "blame-beat-start" in body
    assert "blame-beat-end" in body
