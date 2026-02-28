"""Tests for the Muse Hub export endpoint and musehub_exporter service.

Covers every acceptance criterion from issue #243:
- GET /musehub/repos/{repo_id}/export/{ref}?format=midi returns a .mid file
- GET /musehub/repos/{repo_id}/export/{ref}?format=json returns valid JSON
- split_tracks=true bundles artifacts into a ZIP with per-track files
- sections filter restricts artifacts to matching path substrings
- Unknown format string returns 422 Unprocessable Entity
- Unresolvable ref returns 404
- No matching artifacts for a format returns 404

All tests use the shared fixtures from conftest.py.
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services.musehub_exporter import (
    ExportFormat,
    ExportResult,
    export_repo_at_ref,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIDI_BYTES = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0"  # minimal valid MIDI header
_MP3_BYTES = b"\xff\xfb\x90\x00" + b"\x00" * 60  # minimal MP3 frame marker


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "export-test") -> str:
    r = await client.post("/api/v1/musehub/repos", json={"name": name, "owner": "testuser"}, headers=auth_headers)
    assert r.status_code == 201
    repo_id: str = r.json()["repoId"]
    return repo_id


async def _push_with_objects(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    commit_id: str,
    objects: list[dict[str, object]],
    tmp_dir: str,
) -> None:
    """Push a commit + objects and patch musehub_sync.settings to use tmp_dir."""
    with patch("maestro.services.musehub_sync.settings") as mock_cfg:
        mock_cfg.musehub_objects_dir = tmp_dir
        r = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "test commit",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": objects,
                "force": False,
            },
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# test_export_midi — MIDI export returns a .mid file
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_midi(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A single MIDI object → direct .mid download with correct MIME type."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _create_repo(client, auth_headers, "midi-export")
        await _push_with_objects(
            client,
            auth_headers,
            repo_id,
            commit_id="c-midi-001",
            objects=[
                {
                    "objectId": "sha256:midi001",
                    "path": "tracks/bass.mid",
                    "contentB64": _b64(_MIDI_BYTES),
                }
            ],
            tmp_dir=tmp,
        )

        with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
            from maestro.models.musehub import CommitResponse, ObjectMetaResponse
            from datetime import datetime, timezone

            mock_repo.get_commit = AsyncMock(
                return_value=CommitResponse(
                    commit_id="c-midi-001",
                    branch="main",
                    parent_ids=[],
                    message="test",
                    author="alice",
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    snapshot_id=None,
                )
            )
            mock_repo.list_branches = AsyncMock(return_value=[])
            meta = ObjectMetaResponse(
                object_id="sha256:midi001",
                path="tracks/bass.mid",
                size_bytes=len(_MIDI_BYTES),
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            mock_repo.list_objects = AsyncMock(return_value=[meta])

            from maestro.db import musehub_models as db_models

            disk_path = str(Path(tmp) / "sha256_midi001.mid")
            Path(disk_path).write_bytes(_MIDI_BYTES)

            fake_row = db_models.MusehubObject()
            fake_row.object_id = "sha256:midi001"
            fake_row.path = "tracks/bass.mid"
            fake_row.disk_path = disk_path
            fake_row.size_bytes = len(_MIDI_BYTES)
            mock_repo.get_object_row = AsyncMock(return_value=fake_row)

            r = await client.get(
                f"/api/v1/musehub/repos/{repo_id}/export/c-midi-001?format=midi",
                headers=auth_headers,
            )

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/midi"
    assert "bass.mid" in r.headers.get("content-disposition", "")
    assert r.content == _MIDI_BYTES


# ---------------------------------------------------------------------------
# test_export_json — JSON export returns valid JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_json(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """format=json returns a valid JSON document with commit and object metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _create_repo(client, auth_headers, "json-export")

        with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
            from maestro.models.musehub import CommitResponse, ObjectMetaResponse
            from datetime import datetime, timezone

            mock_repo.get_commit = AsyncMock(
                return_value=CommitResponse(
                    commit_id="c-json-001",
                    branch="main",
                    parent_ids=[],
                    message="json export test",
                    author="bob",
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    snapshot_id=None,
                )
            )
            mock_repo.list_branches = AsyncMock(return_value=[])
            mock_repo.list_objects = AsyncMock(
                return_value=[
                    ObjectMetaResponse(
                        object_id="sha256:obj001",
                        path="tracks/keys.mid",
                        size_bytes=100,
                        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    )
                ]
            )

            r = await client.get(
                f"/api/v1/musehub/repos/{repo_id}/export/c-json-001?format=json",
                headers=auth_headers,
            )

    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    payload = json.loads(r.content)
    assert payload["commit_id"] == "c-json-001"
    assert payload["repo_id"] == repo_id
    assert len(payload["objects"]) == 1
    assert payload["objects"][0]["path"] == "tracks/keys.mid"


# ---------------------------------------------------------------------------
# test_export_split_tracks_zip — split_tracks produces a ZIP
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_split_tracks_zip(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """split_tracks=true with two MIDI objects produces a ZIP with both files."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _create_repo(client, auth_headers, "zip-export")

        bass_path = str(Path(tmp) / "bass.mid")
        keys_path = str(Path(tmp) / "keys.mid")
        Path(bass_path).write_bytes(_MIDI_BYTES)
        Path(keys_path).write_bytes(_MIDI_BYTES + b"\x00")

        with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
            from maestro.models.musehub import CommitResponse, ObjectMetaResponse
            from datetime import datetime, timezone
            from maestro.db import musehub_models as db_models

            mock_repo.get_commit = AsyncMock(
                return_value=CommitResponse(
                    commit_id="c-zip-001",
                    branch="main",
                    parent_ids=[],
                    message="zip test",
                    author="carol",
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    snapshot_id=None,
                )
            )
            mock_repo.list_branches = AsyncMock(return_value=[])
            mock_repo.list_objects = AsyncMock(
                return_value=[
                    ObjectMetaResponse(
                        object_id="sha256:bass",
                        path="tracks/bass.mid",
                        size_bytes=len(_MIDI_BYTES),
                        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    ),
                    ObjectMetaResponse(
                        object_id="sha256:keys",
                        path="tracks/keys.mid",
                        size_bytes=len(_MIDI_BYTES) + 1,
                        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    ),
                ]
            )

            def _fake_get_object_row(
                session: object, repo_id: str, object_id: str
            ) -> db_models.MusehubObject:
                row = db_models.MusehubObject()
                row.object_id = object_id
                if object_id == "sha256:bass":
                    row.path = "tracks/bass.mid"
                    row.disk_path = bass_path
                else:
                    row.path = "tracks/keys.mid"
                    row.disk_path = keys_path
                row.size_bytes = 0
                return row

            mock_repo.get_object_row = AsyncMock(side_effect=_fake_get_object_row)

            r = await client.get(
                f"/api/v1/musehub/repos/{repo_id}/export/c-zip-001?format=midi&splitTracks=true",
                headers=auth_headers,
            )

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "bass.mid" in names
    assert "keys.mid" in names


# ---------------------------------------------------------------------------
# test_export_section_filter — sections param filters artifacts by path substring
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_section_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """sections=verse includes only artifacts whose path contains 'verse'."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_id = await _create_repo(client, auth_headers, "section-export")

        verse_path = str(Path(tmp) / "verse_bass.mid")
        chorus_path = str(Path(tmp) / "chorus_bass.mid")
        Path(verse_path).write_bytes(_MIDI_BYTES)
        Path(chorus_path).write_bytes(_MIDI_BYTES + b"\x01")

        with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
            from maestro.models.musehub import CommitResponse, ObjectMetaResponse
            from datetime import datetime, timezone
            from maestro.db import musehub_models as db_models

            mock_repo.get_commit = AsyncMock(
                return_value=CommitResponse(
                    commit_id="c-sec-001",
                    branch="main",
                    parent_ids=[],
                    message="section test",
                    author="dave",
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    snapshot_id=None,
                )
            )
            mock_repo.list_branches = AsyncMock(return_value=[])
            mock_repo.list_objects = AsyncMock(
                return_value=[
                    ObjectMetaResponse(
                        object_id="sha256:verse",
                        path="tracks/verse_bass.mid",
                        size_bytes=len(_MIDI_BYTES),
                        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    ),
                    ObjectMetaResponse(
                        object_id="sha256:chorus",
                        path="tracks/chorus_bass.mid",
                        size_bytes=len(_MIDI_BYTES) + 1,
                        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    ),
                ]
            )

            verse_row = db_models.MusehubObject()
            verse_row.object_id = "sha256:verse"
            verse_row.path = "tracks/verse_bass.mid"
            verse_row.disk_path = verse_path
            verse_row.size_bytes = len(_MIDI_BYTES)
            mock_repo.get_object_row = AsyncMock(return_value=verse_row)

            r = await client.get(
                f"/api/v1/musehub/repos/{repo_id}/export/c-sec-001?format=midi&sections=verse",
                headers=auth_headers,
            )

    assert r.status_code == 200
    assert r.content == _MIDI_BYTES
    assert "verse_bass.mid" in r.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# test_export_unknown_format_422 — invalid format returns 422
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_unknown_format_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """An unrecognised format query param returns HTTP 422."""
    repo_id = await _create_repo(client, auth_headers, "bad-format")
    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/export/main?format=flac",
        headers=auth_headers,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# test_export_ref_not_found_404 — unresolvable ref returns 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_ref_not_found_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A ref that does not match any commit or branch returns HTTP 404."""
    repo_id = await _create_repo(client, auth_headers, "missing-ref")

    with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
        mock_repo.get_commit = AsyncMock(return_value=None)
        mock_repo.list_branches = AsyncMock(return_value=[])

        r = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/export/nonexistent-sha?format=midi",
            headers=auth_headers,
        )

    assert r.status_code == 404
    assert "nonexistent-sha" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Unit tests for export_repo_at_ref service function
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_service_returns_ref_not_found_sentinel() -> None:
    """export_repo_at_ref returns 'ref_not_found' when the ref resolves to nothing."""
    from unittest.mock import MagicMock

    mock_session = MagicMock()

    with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
        mock_repo.get_commit = AsyncMock(return_value=None)
        mock_repo.list_branches = AsyncMock(return_value=[])

        result = await export_repo_at_ref(
            mock_session,
            repo_id="repo-x",
            ref="deadbeef",
            format=ExportFormat.midi,
        )

    assert result == "ref_not_found"


@pytest.mark.anyio
async def test_export_service_returns_no_matching_objects_sentinel() -> None:
    """export_repo_at_ref returns 'no_matching_objects' when no objects match the format."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from maestro.models.musehub import CommitResponse, ObjectMetaResponse

    mock_session = MagicMock()

    with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
        mock_repo.get_commit = AsyncMock(
            return_value=CommitResponse(
                commit_id="abc123",
                branch="main",
                parent_ids=[],
                message="x",
                author="x",
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                snapshot_id=None,
            )
        )
        mock_repo.list_branches = AsyncMock(return_value=[])
        mock_repo.list_objects = AsyncMock(
            return_value=[
                ObjectMetaResponse(
                    object_id="sha256:img",
                    path="piano_roll.webp",
                    size_bytes=512,
                    created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                )
            ]
        )

        result = await export_repo_at_ref(
            mock_session,
            repo_id="repo-y",
            ref="abc123",
            format=ExportFormat.midi,
        )

    assert result == "no_matching_objects"


@pytest.mark.anyio
async def test_export_service_json_format_no_disk_access() -> None:
    """format=json returns ExportResult with JSON bytes without reading any disk file."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from maestro.models.musehub import CommitResponse, ObjectMetaResponse

    mock_session = MagicMock()

    with patch("maestro.services.musehub_exporter.musehub_repository") as mock_repo:
        mock_repo.get_commit = AsyncMock(
            return_value=CommitResponse(
                commit_id="abc999",
                branch="dev",
                parent_ids=[],
                message="json only",
                author="eve",
                timestamp=datetime(2024, 3, 1, tzinfo=timezone.utc),
                snapshot_id=None,
            )
        )
        mock_repo.list_branches = AsyncMock(return_value=[])
        mock_repo.list_objects = AsyncMock(
            return_value=[
                ObjectMetaResponse(
                    object_id="sha256:mid",
                    path="track.mid",
                    size_bytes=200,
                    created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
                )
            ]
        )

        result = await export_repo_at_ref(
            mock_session,
            repo_id="repo-z",
            ref="abc999",
            format=ExportFormat.json,
        )

    assert isinstance(result, ExportResult)
    assert result.content_type == "application/json"
    parsed = json.loads(result.content)
    assert parsed["commit_id"] == "abc999"
    assert parsed["repo_id"] == "repo-z"
    assert len(parsed["objects"]) == 1
