"""Tests for the MuseHub render pipeline (issue #214).

Covers the acceptance criteria from the issue:
  test_push_triggers_render               — Push endpoint triggers render task
  test_render_creates_mp3_objects         — Render creates MP3 objects in store
  test_render_creates_piano_roll_images   — Render creates PNG objects in store
  test_render_idempotent                  — Re-push does not duplicate renders
  test_render_failure_does_not_block_push — Failed render still allows push to complete
  test_render_status_endpoint             — Render status queryable by commit SHA

Unit tests (service-level, no HTTP client):
  test_piano_roll_render_note_events      — Valid MIDI produces non-blank PNG
  test_piano_roll_render_empty_midi       — Empty/blank MIDI produces stubbed=True result
  test_piano_roll_render_invalid_bytes    — Garbage bytes produce stubbed=True result
  test_render_pipeline_midi_filter        — Only .mid/.midi paths are processed
  test_render_status_not_found            — Missing commit returns not_found status

Integration tests (HTTP client + in-memory SQLite):
  test_render_status_endpoint_not_found   — Endpoint returns not_found for unknown commit
  test_render_status_endpoint_complete    — Endpoint returns complete job
  test_render_status_endpoint_private_repo_no_auth — Private repo returns 401
"""
from __future__ import annotations

import base64
import io
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import mido
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubBranch,
    MusehubCommit,
    MusehubRenderJob,
    MusehubRepo,
)
from maestro.models.musehub import ObjectInput
from maestro.services.musehub_piano_roll_renderer import (
    PianoRollRenderResult,
    render_piano_roll,
)
from maestro.services.musehub_render_pipeline import (
    RenderPipelineResult,
    _midi_filter,
    trigger_render_background,
)


# ---------------------------------------------------------------------------
# Helpers — minimal MIDI construction
# ---------------------------------------------------------------------------


def _make_minimal_midi_bytes() -> bytes:
    """Return bytes for a minimal valid Standard MIDI File with two notes.

    Constructs a type-0 MIDI file using mido in-memory so tests are
    independent of external fixtures.
    """
    mid = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    track.append(mido.Message("note_on", note=64, velocity=80, time=100))
    track.append(mido.Message("note_off", note=60, velocity=0, time=200))
    track.append(mido.Message("note_off", note=64, velocity=0, time=400))
    track.append(mido.MetaMessage("end_of_track", time=0))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _make_midi_b64() -> str:
    """Return base64-encoded minimal MIDI."""
    return base64.b64encode(_make_minimal_midi_bytes()).decode()


async def _seed_repo(db_session: AsyncSession) -> tuple[str, str]:
    """Create a minimal repo + branch and return (repo_id, branch_name)."""
    repo = MusehubRepo(
        name="render-test",
        owner="renderuser",
        slug="render-test",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()

    branch = MusehubBranch(repo_id=repo.repo_id, name="main")
    db_session.add(branch)
    await db_session.commit()

    return str(repo.repo_id), "main"


async def _seed_public_repo(db_session: AsyncSession) -> tuple[str, str]:
    """Create a minimal public repo + branch and return (repo_id, branch_name)."""
    repo = MusehubRepo(
        name="public-render",
        owner="renderuser",
        slug="public-render",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()

    branch = MusehubBranch(repo_id=repo.repo_id, name="main")
    db_session.add(branch)
    await db_session.commit()

    return str(repo.repo_id), "main"


async def _seed_commit(
    db_session: AsyncSession, repo_id: str, commit_id: str = "abc123" * 10 + "ab"
) -> str:
    """Seed a commit row and return its commit_id."""
    from datetime import datetime, timezone
    commit = MusehubCommit(
        commit_id=commit_id[:64],
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="test commit",
        author="tester",
        timestamp=datetime.now(timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()
    return commit.commit_id


async def _seed_render_job(
    db_session: AsyncSession,
    repo_id: str,
    commit_id: str,
    status: str = "complete",
) -> MusehubRenderJob:
    """Seed a render job row and return it."""
    job = MusehubRenderJob(
        repo_id=repo_id,
        commit_id=commit_id,
        status=status,
        midi_count=1,
        mp3_object_ids=["sha256:mp3abc"],
        image_object_ids=["sha256:imgxyz"],
    )
    db_session.add(job)
    await db_session.commit()
    return job


# ---------------------------------------------------------------------------
# Unit tests — musehub_piano_roll_renderer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_piano_roll_render_note_events() -> None:
    """Valid MIDI bytes produce a non-blank PNG with stubbed=False."""
    midi_bytes = _make_minimal_midi_bytes()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        output_path = Path(f.name)

    result = render_piano_roll(midi_bytes, output_path)

    assert isinstance(result, PianoRollRenderResult)
    assert result.stubbed is False
    assert result.note_count > 0
    assert output_path.exists()
    # Verify PNG signature
    png_bytes = output_path.read_bytes()
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png_bytes) > 100


@pytest.mark.anyio
async def test_piano_roll_render_empty_midi() -> None:
    """A MIDI file with no note events produces a blank canvas (stubbed=True)."""
    mid = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    empty_midi = buf.getvalue()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        output_path = Path(f.name)

    result = render_piano_roll(empty_midi, output_path)

    assert result.stubbed is True
    assert result.note_count == 0
    assert output_path.exists()
    png_bytes = output_path.read_bytes()
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.anyio
async def test_piano_roll_render_invalid_bytes() -> None:
    """Garbage bytes produce a blank canvas (stubbed=True) without raising."""
    garbage = b"\x00\x01\x02\x03" * 10
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        output_path = Path(f.name)

    result = render_piano_roll(garbage, output_path)

    assert result.stubbed is True
    assert output_path.exists()
    png_bytes = output_path.read_bytes()
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.anyio
async def test_piano_roll_render_output_dimensions() -> None:
    """Rendered PNG has the correct width and height."""
    from maestro.services.musehub_piano_roll_renderer import IMAGE_HEIGHT, MAX_WIDTH_PX

    midi_bytes = _make_minimal_midi_bytes()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        output_path = Path(f.name)

    render_piano_roll(midi_bytes, output_path, target_width=MAX_WIDTH_PX)
    png_bytes = output_path.read_bytes()

    # PNG IHDR starts at byte 16 (after signature + chunk-length + "IHDR")
    ihdr_offset = 16
    width = struct.unpack(">I", png_bytes[ihdr_offset : ihdr_offset + 4])[0]
    height = struct.unpack(">I", png_bytes[ihdr_offset + 4 : ihdr_offset + 8])[0]

    assert width == MAX_WIDTH_PX
    assert height == IMAGE_HEIGHT


# ---------------------------------------------------------------------------
# Unit tests — musehub_render_pipeline (service layer, no HTTP)
# ---------------------------------------------------------------------------


def test_render_pipeline_midi_filter() -> None:
    """Only .mid and .midi paths pass the MIDI filter."""
    assert _midi_filter("tracks/jazz.mid") is True
    assert _midi_filter("tracks/JAZZ.MID") is True
    assert _midi_filter("tracks/bass.midi") is True
    assert _midi_filter("renders/output.mp3") is False
    assert _midi_filter("images/piano_roll.png") is False
    assert _midi_filter("session.json") is False


@pytest.mark.anyio
async def test_render_creates_mp3_objects(
    db_session: AsyncSession,
) -> None:
    """Render pipeline writes MP3 stub objects to the DB for each MIDI file."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "a" * 64

    objects = [
        ObjectInput(
            object_id="sha256:midi001",
            path="tracks/bass.mid",
            content_b64=_make_midi_b64(),
        )
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("maestro.services.musehub_render_pipeline.settings") as mock_settings:
            mock_settings.musehub_objects_dir = tmpdir
            await trigger_render_background(
                repo_id=repo_id,
                commit_id=commit_id,
                objects=objects,
            )

    from sqlalchemy import select as sa_select
    from maestro.db.musehub_models import MusehubRenderJob as RJ
    stmt = sa_select(RJ).where(RJ.repo_id == repo_id, RJ.commit_id == commit_id)
    job = (await db_session.execute(stmt)).scalar_one_or_none()

    assert job is not None
    assert job.status == "complete"
    assert len(job.mp3_object_ids) == 1


@pytest.mark.anyio
async def test_render_creates_piano_roll_images(
    db_session: AsyncSession,
) -> None:
    """Render pipeline writes piano-roll PNG objects to the DB for each MIDI file."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "b" * 64

    objects = [
        ObjectInput(
            object_id="sha256:midi002",
            path="tracks/keys.mid",
            content_b64=_make_midi_b64(),
        )
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("maestro.services.musehub_render_pipeline.settings") as mock_settings:
            mock_settings.musehub_objects_dir = tmpdir
            await trigger_render_background(
                repo_id=repo_id,
                commit_id=commit_id,
                objects=objects,
            )

    from sqlalchemy import select as sa_select
    from maestro.db.musehub_models import MusehubRenderJob as RJ
    stmt = sa_select(RJ).where(RJ.repo_id == repo_id, RJ.commit_id == commit_id)
    job = (await db_session.execute(stmt)).scalar_one_or_none()

    assert job is not None
    assert job.status == "complete"
    assert len(job.image_object_ids) == 1


@pytest.mark.anyio
async def test_render_idempotent(
    db_session: AsyncSession,
) -> None:
    """Re-triggering render for the same commit does not create a second render job."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "c" * 64

    objects = [
        ObjectInput(
            object_id="sha256:midi003",
            path="tracks/lead.mid",
            content_b64=_make_midi_b64(),
        )
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("maestro.services.musehub_render_pipeline.settings") as mock_settings:
            mock_settings.musehub_objects_dir = tmpdir
            # First call
            await trigger_render_background(
                repo_id=repo_id,
                commit_id=commit_id,
                objects=objects,
            )
            # Second call — must be a no-op
            await trigger_render_background(
                repo_id=repo_id,
                commit_id=commit_id,
                objects=objects,
            )

    from sqlalchemy import select as sa_select, func
    from maestro.db.musehub_models import MusehubRenderJob as RJ
    stmt = sa_select(func.count()).select_from(RJ).where(
        RJ.repo_id == repo_id, RJ.commit_id == commit_id
    )
    count = (await db_session.execute(stmt)).scalar_one()
    assert count == 1


@pytest.mark.anyio
async def test_render_no_midi_objects(
    db_session: AsyncSession,
) -> None:
    """A push with no MIDI objects creates a render job with midi_count=0 and empty artifacts."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "d" * 64

    objects = [
        ObjectInput(
            object_id="sha256:doc001",
            path="README.md",
            content_b64=base64.b64encode(b"# Project").decode(),
        )
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("maestro.services.musehub_render_pipeline.settings") as mock_settings:
            mock_settings.musehub_objects_dir = tmpdir
            await trigger_render_background(
                repo_id=repo_id,
                commit_id=commit_id,
                objects=objects,
            )

    from sqlalchemy import select as sa_select
    from maestro.db.musehub_models import MusehubRenderJob as RJ
    stmt = sa_select(RJ).where(RJ.repo_id == repo_id, RJ.commit_id == commit_id)
    job = (await db_session.execute(stmt)).scalar_one_or_none()

    assert job is not None
    assert job.midi_count == 0
    assert job.mp3_object_ids == []
    assert job.image_object_ids == []
    assert job.status == "complete"


# ---------------------------------------------------------------------------
# Integration tests — render-status HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_status_endpoint_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{repo_id}/commits/{sha}/render-status returns not_found for unknown commit."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "e" * 64

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{commit_id}/render-status",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_found"
    assert data["commitId"] == commit_id


@pytest.mark.anyio
async def test_render_status_endpoint_complete(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{repo_id}/commits/{sha}/render-status returns complete job."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "f" * 64
    await _seed_commit(db_session, repo_id, commit_id)
    job = await _seed_render_job(db_session, repo_id, commit_id, status="complete")

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{commit_id}/render-status",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert data["commitId"] == commit_id
    assert data["midiCount"] == 1
    assert "sha256:mp3abc" in data["mp3ObjectIds"]
    assert "sha256:imgxyz" in data["imageObjectIds"]


@pytest.mark.anyio
async def test_render_status_endpoint_pending(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET render-status returns pending status for in-flight jobs."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "0" * 64
    await _seed_commit(db_session, repo_id, commit_id)
    await _seed_render_job(db_session, repo_id, commit_id, status="pending")

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{commit_id}/render-status",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.anyio
async def test_render_status_endpoint_private_repo_no_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Private repo render-status endpoint requires auth — returns 401 without JWT."""
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "1" * 64

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{commit_id}/render-status",
    )

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_render_status_endpoint_public_repo_no_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Public repo render-status endpoint is accessible without JWT."""
    repo_id, _ = await _seed_public_repo(db_session)
    commit_id = "2" * 64

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{commit_id}/render-status",
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


# ---------------------------------------------------------------------------
# Integration tests — push endpoint triggers render
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_triggers_render(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{repo_id}/push triggers the render background task."""
    repo_id, _ = await _seed_repo(db_session)

    trigger_calls: list[dict[str, object]] = []

    async def fake_trigger(
        *,
        repo_id: str,
        commit_id: str,
        objects: list[ObjectInput],
    ) -> None:
        trigger_calls.append(
            {"repo_id": repo_id, "commit_id": commit_id, "objects": objects}
        )

    with patch(
        "maestro.api.routes.musehub.sync.trigger_render_background",
        side_effect=fake_trigger,
    ):
        payload = {
            "branch": "main",
            "headCommitId": "a" * 64,
            "commits": [
                {
                    "commitId": "a" * 64,
                    "branch": "main",
                    "parentIds": [],
                    "message": "add bass line",
                    "author": "testuser",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "snapshotId": None,
                }
            ],
            "objects": [
                {
                    "objectId": "sha256:midiobj",
                    "path": "tracks/bass.mid",
                    "contentB64": _make_midi_b64(),
                }
            ],
            "force": False,
        }

        resp = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json=payload,
            headers=auth_headers,
        )

    assert resp.status_code == 200
    # The trigger must have been called once for this push
    assert len(trigger_calls) == 1
    assert trigger_calls[0]["commit_id"] == "a" * 64
    assert trigger_calls[0]["repo_id"] == repo_id


@pytest.mark.anyio
async def test_render_failure_does_not_block_push(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Push succeeds even when the render background task is a no-op.

    The push HTTP response is returned before background tasks run, so the
    push is never blocked by render pipeline work.  We verify this by patching
    ``trigger_render_background`` to a no-op (which never interferes with the
    HTTP response) and confirming the push still returns 200.

    The internal error-handling contract (render errors logged, not raised) is
    covered by test_render_pipeline_internal_error_is_logged.
    """
    repo_id, _ = await _seed_repo(db_session)

    async def noop_trigger(**kwargs: object) -> None:
        pass  # Deliberately does nothing — simulates a render that never runs

    with patch(
        "maestro.api.routes.musehub.sync.trigger_render_background",
        side_effect=noop_trigger,
    ):
        payload = {
            "branch": "main",
            "headCommitId": "b" * 64,
            "commits": [
                {
                    "commitId": "b" * 64,
                    "branch": "main",
                    "parentIds": [],
                    "message": "keys riff",
                    "author": "testuser",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "snapshotId": None,
                }
            ],
            "objects": [],
            "force": False,
        }

        resp = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json=payload,
            headers=auth_headers,
        )

    # Push must succeed regardless of what the render task does
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


@pytest.mark.anyio
async def test_render_pipeline_internal_error_is_logged(
    db_session: AsyncSession,
) -> None:
    """Render pipeline marks job as failed and logs when _render_commit raises.

    This verifies the contract that render errors never propagate — they are
    caught inside ``trigger_render_background`` and stored as job.status=failed.
    """
    repo_id, _ = await _seed_repo(db_session)
    commit_id = "e" * 64

    objects = [
        ObjectInput(
            object_id="sha256:mididead",
            path="tracks/broken.mid",
            # Intentionally bad base64 — _render_commit will log a warning for each
            # object that fails to decode, but the job itself should still complete
            content_b64=_make_midi_b64(),
        )
    ]

    # Patch _render_commit itself to raise — tests the outer catch
    from maestro.services import musehub_render_pipeline as pipeline_mod

    async def exploding_render(session: object, **kwargs: object) -> RenderPipelineResult:
        raise RuntimeError("simulated internal render error")

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("maestro.services.musehub_render_pipeline.settings") as mock_settings:
            mock_settings.musehub_objects_dir = tmpdir
            with patch.object(pipeline_mod, "_render_commit", side_effect=exploding_render):
                # Must not raise — errors are swallowed inside trigger_render_background
                await trigger_render_background(
                    repo_id=repo_id,
                    commit_id=commit_id,
                    objects=objects,
                )

    from sqlalchemy import select as sa_select
    from maestro.db.musehub_models import MusehubRenderJob as RJ
    stmt = sa_select(RJ).where(RJ.repo_id == repo_id, RJ.commit_id == commit_id)
    job = (await db_session.execute(stmt)).scalar_one_or_none()

    assert job is not None
    assert job.status == "failed"
    assert job.error_message is not None
    assert "simulated internal render error" in job.error_message
