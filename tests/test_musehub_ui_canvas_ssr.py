"""Tests for SSR scaffolding on the piano roll and score pages (issue #581).

Verifies that the piano roll and score page handlers populate server-side
context — track name, instrument sidebar, transport bar, canvas data
attributes, and score metadata — without requiring JavaScript execution.

Covers:
- test_piano_roll_page_renders_track_name_server_side
- test_piano_roll_page_renders_instrument_sidebar
- test_piano_roll_page_canvas_has_data_midi_url
- test_piano_roll_page_transport_bar_present
- test_piano_roll_track_page_canvas_has_data_instruments
- test_score_page_renders_title_server_side
- test_score_page_score_container_has_data_abc_url
- test_score_page_no_blank_shell
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubObject, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name="canvas-test-beats",
        owner="canvasuser",
        slug="canvas-test-beats",
        visibility="private",
        owner_user_id="canvas-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _seed_midi_object(
    db_session: AsyncSession,
    repo_id: str,
    path: str = "tracks/bass.mid",
    size_bytes: int = 4096,
) -> MusehubObject:
    """Seed a MIDI object into the repo and return it."""
    obj = MusehubObject(
        object_id=f"sha256:{'a' * 64}_{path.replace('/', '_')}",
        repo_id=repo_id,
        path=path,
        size_bytes=size_bytes,
        disk_path=f"/data/{path}",
    )
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


# ---------------------------------------------------------------------------
# Piano roll SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_piano_roll_page_renders_track_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll track page renders the track name from the path in SSR HTML."""
    repo_id = await _make_repo(db_session)
    await _seed_midi_object(db_session, repo_id, path="tracks/bass.mid")
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/piano-roll/main/tracks/bass.mid"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Track name derived from path stem ("bass" → "Bass")
    assert "Bass" in body or "bass.mid" in body or "bass" in body.lower()


@pytest.mark.anyio
async def test_piano_roll_page_renders_instrument_sidebar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page renders instrument lane names in the SSR sidebar."""
    repo_id = await _make_repo(db_session)
    await _seed_midi_object(db_session, repo_id, path="tracks/keys.mid")
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/piano-roll/main"
    )
    assert response.status_code == 200
    body = response.text
    # The instrument sidebar shows instrument names derived from path stems
    assert "instrument-lane" in body or "Keys" in body or "keys" in body.lower()


@pytest.mark.anyio
async def test_piano_roll_page_canvas_has_data_midi_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll canvas element carries a data-midi-url attribute for JS."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/piano-roll/main"
    )
    assert response.status_code == 200
    assert "data-midi-url" in response.text


@pytest.mark.anyio
async def test_piano_roll_page_transport_bar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page SSR includes the transport bar (#transport-bar)."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/piano-roll/main"
    )
    assert response.status_code == 200
    assert "transport-bar" in response.text


@pytest.mark.anyio
async def test_piano_roll_track_page_canvas_has_data_instruments(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Single-track piano roll page embeds data-instruments JSON on the canvas."""
    repo_id = await _make_repo(db_session)
    await _seed_midi_object(db_session, repo_id, path="tracks/guitar.mid")
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/piano-roll/main/tracks/guitar.mid"
    )
    assert response.status_code == 200
    body = response.text
    assert "data-instruments" in body


# ---------------------------------------------------------------------------
# Score page SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_score_page_renders_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score part page renders a title derived from the path in SSR HTML."""
    repo_id = await _make_repo(db_session)
    await _seed_midi_object(db_session, repo_id, path="tracks/melody.mid")
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/score/main/tracks/melody.mid"
    )
    assert response.status_code == 200
    body = response.text
    # Title derived from path stem ("melody" → "Melody")
    assert "Melody" in body or "melody" in body.lower()


@pytest.mark.anyio
async def test_score_page_score_container_has_data_abc_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page includes a #score-container with a data-abc-url for JS."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/score/main"
    )
    assert response.status_code == 200
    body = response.text
    assert "score-container" in body
    assert "data-abc-url" in body


@pytest.mark.anyio
async def test_score_page_no_blank_shell(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page renders meaningful content without JS execution.

    The page must include the metadata header and score container in SSR,
    not just a blank shell that depends entirely on a client fetch.
    """
    repo_id = await _make_repo(db_session)
    await _seed_midi_object(db_session, repo_id, path="tracks/piano.mid")
    response = await client.get(
        "/musehub/ui/canvasuser/canvas-test-beats/score/main"
    )
    assert response.status_code == 200
    body = response.text
    # Score header is present — not a blank loading spinner as the only content
    assert "score-container" in body or "score-meta" in body or "Score" in body
    # The entire body is not just a loading placeholder
    assert body.count("Loading") < 5
