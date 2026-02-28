"""Tests for the Muse Hub raw file download endpoint.

Covers every acceptance criterion from issue #245:
- test_raw_midi_correct_mime       — .mid served with audio/midi
- test_raw_mp3_correct_mime        — .mp3 served with audio/mpeg
- test_raw_wav_correct_mime        — .wav served with audio/wav
- test_raw_json_correct_mime       — .json served with application/json
- test_raw_webp_correct_mime       — .webp served with image/webp
- test_raw_xml_correct_mime        — .xml served with application/xml
- test_raw_404_unknown_path        — nonexistent path returns 404
- test_raw_404_unknown_repo        — nonexistent repo_id returns 404
- test_raw_public_no_auth          — public repo accessible without JWT
- test_raw_private_requires_auth   — private repo returns 401 without JWT
- test_raw_private_with_auth       — private repo accessible with valid JWT
- test_raw_range_request           — Range request returns 206 with partial content
- test_raw_content_disposition     — Content-Disposition header carries filename
- test_raw_accept_ranges_header    — Accept-Ranges: bytes is present in response

The endpoint under test:
  GET /api/v1/musehub/repos/{repo_id}/raw/{ref}/{path:path}
"""
from __future__ import annotations

import os
import tempfile

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubObject, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, *, visibility: str = "public") -> str:
    """Seed a minimal Muse Hub repo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        owner="testuser",
        slug="test-beats",
        visibility=visibility,
        owner_user_id="test-owner",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_object(
    db: AsyncSession,
    repo_id: str,
    *,
    path: str,
    content: bytes = b"FAKE_CONTENT",
    tmp_dir: str,
) -> str:
    """Write content to a temp file, seed an object row, return the object_id."""
    filename = os.path.basename(path)
    disk_path = os.path.join(tmp_dir, filename)
    with open(disk_path, "wb") as fh:
        fh.write(content)

    obj = MusehubObject(
        object_id=f"sha256:test-{filename}",
        repo_id=repo_id,
        path=path,
        size_bytes=len(content),
        disk_path=disk_path,
    )
    db.add(obj)
    await db.commit()
    return str(obj.object_id)


# ---------------------------------------------------------------------------
# MIME type tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_midi_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.mid file is served with Content-Type: audio/midi."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="tracks/bass.mid", tmp_dir=tmp)

        resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/raw/main/tracks/bass.mid")

    assert resp.status_code == 200
    assert "audio/midi" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_raw_mp3_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.mp3 file is served with Content-Type: audio/mpeg."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="mix/final.mp3", tmp_dir=tmp)

        resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/raw/main/mix/final.mp3")

    assert resp.status_code == 200
    assert "audio/mpeg" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_raw_wav_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.wav file is served with Content-Type: audio/wav."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="stems/drums.wav", tmp_dir=tmp)

        resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/raw/main/stems/drums.wav")

    assert resp.status_code == 200
    assert "audio/wav" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_raw_json_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.json file is served with Content-Type: application/json."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(
            db_session,
            repo_id,
            path="metadata/track.json",
            content=b'{"bpm": 120}',
            tmp_dir=tmp,
        )

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/metadata/track.json"
        )

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_raw_webp_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.webp file is served with Content-Type: image/webp."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="previews/piano_roll.webp", tmp_dir=tmp)

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/previews/piano_roll.webp"
        )

    assert resp.status_code == 200
    assert "image/webp" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_raw_xml_correct_mime(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """.xml file is served with Content-Type: application/xml."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(
            db_session,
            repo_id,
            path="scores/piece.xml",
            content=b"<score></score>",
            tmp_dir=tmp,
        )

        resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/raw/main/scores/piece.xml")

    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# 404 / error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_404_unknown_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Path that has no matching object returns 404."""
    repo_id = await _make_repo(db_session, visibility="public")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/raw/main/does/not/exist.mid"
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_raw_404_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Nonexistent repo_id returns 404 immediately."""
    resp = await client.get(
        "/api/v1/musehub/repos/nonexistent-repo-uuid/raw/main/track.mid"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_public_no_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Public repo files are accessible without any Authorization header."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="tracks/open.mid", tmp_dir=tmp)

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/tracks/open.mid"
        )

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_raw_private_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Private repo raw download without a JWT returns 401."""
    repo_id = await _make_repo(db_session, visibility="private")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/raw/main/tracks/secret.mid"
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_raw_private_with_auth(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Private repo raw download WITH a valid JWT returns 200."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="private")
        await _make_object(db_session, repo_id, path="tracks/secret.mid", tmp_dir=tmp)

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/tracks/secret.mid",
            headers={"Authorization": auth_headers["Authorization"]},
        )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Header correctness
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_content_disposition(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Response carries a Content-Disposition header with the original filename."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(
            db_session, repo_id, path="tracks/groove_42.mid", tmp_dir=tmp
        )

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/tracks/groove_42.mid"
        )

    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "groove_42.mid" in cd


@pytest.mark.anyio
async def test_raw_accept_ranges_header(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Response includes Accept-Ranges: bytes so Range requests are signalled."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(db_session, repo_id, path="mix/audio.mp3", tmp_dir=tmp)

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/mix/audio.mp3"
        )

    assert resp.status_code == 200
    assert resp.headers.get("accept-ranges") == "bytes"


@pytest.mark.anyio
async def test_raw_range_request(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Range request returns 206 Partial Content with the requested byte slice."""
    content = b"HELLO_WORLD_AUDIO_DATA_BYTES_HERE"
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _make_repo(db_session, visibility="public")
        await _make_object(
            db_session,
            repo_id,
            path="mix/partial.mp3",
            content=content,
            tmp_dir=tmp,
        )

        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/raw/main/mix/partial.mp3",
            headers={"Range": "bytes=0-4"},
        )

    assert resp.status_code == 206
    assert resp.content == content[:5]
