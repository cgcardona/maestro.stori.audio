"""Tests for the Muse Hub blame UI page (issue #423).

Covers:
- test_blame_page_renders                  — GET /musehub/ui/{owner}/{slug}/blame/{ref}/{path} returns 200 HTML
- test_blame_page_no_auth_required         — page accessible without a JWT
- test_blame_page_unknown_repo_404         — bad owner/slug returns 404
- test_blame_page_contains_table_headers   — HTML contains blame table column headers
- test_blame_page_contains_filter_bar      — HTML includes track/beat filter controls
- test_blame_page_contains_breadcrumb      — breadcrumb links owner, repo_slug, ref, and filename
- test_blame_page_contains_piano_roll_link — quick-link to the piano-roll page present
- test_blame_page_contains_commits_link    — quick-link to the commit list present
- test_blame_json_response                 — Accept: application/json returns BlameResponse JSON
- test_blame_json_has_entries_key          — JSON body contains 'entries' and 'totalEntries' keys
- test_blame_json_format_param             — ?format=json returns JSON without Accept header
- test_blame_page_path_injected_in_js      — file path passed as JS context variable
- test_blame_page_ref_injected_in_js       — commit ref passed as JS context variable
- test_blame_page_api_fetch_call           — page JS calls blame API endpoint
- test_blame_page_filter_bar_track_options — track <select> lists standard instrument names
- test_blame_page_pitch_name_helper        — pitchName helper renders note names in JS
- test_blame_page_commit_sha_link          — commit SHA links to commit detail page in JS template
- test_blame_page_velocity_bar_present     — velocity bar element present in JS template
- test_blame_page_beat_range_column        — beat range column rendered in JS template
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
    commit = MusehubCommit(
        repo_id=repo_id,
        commit_id="abc1234567890abcdef",
        message="Add jazz piano chords",
        author="testuser",
        branch="main",
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
    """Rendered HTML must contain the blame table column headers."""
    await _make_repo(db_session)
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
async def test_blame_page_path_injected_in_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The MIDI file path must be passed as a JS variable (filePath)."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "filePath" in body
    assert _PATH in body


@pytest.mark.anyio
async def test_blame_page_ref_injected_in_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The commit ref must be passed as a JS variable (ref)."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "const ref" in body
    assert _REF in body


@pytest.mark.anyio
async def test_blame_page_api_fetch_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page JS must call the blame API endpoint to load data."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "/blame/" in body
    assert "apiFetch" in body


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
async def test_blame_page_pitch_name_helper(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page JS must define the pitchName helper for MIDI-to-note-name conversion."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "pitchName" in body
    assert "NOTE_NAMES" in body


@pytest.mark.anyio
async def test_blame_page_commit_sha_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Rendered JS template must generate commit SHA anchor links."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "commit-sha" in body
    assert "commitId" in body


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
    """Beat range column must appear in the JS table template."""
    await _make_repo(db_session)
    url = f"/musehub/ui/{_OWNER}/{_SLUG}/blame/{_REF}/{_PATH}"
    response = await client.get(url)
    assert response.status_code == 200
    body = response.text
    assert "beat-range" in body
    assert "beatStart" in body
    assert "beatEnd" in body
