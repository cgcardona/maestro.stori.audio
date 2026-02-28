"""Tests for ``muse release`` — export a tagged commit as release artifacts.

Verifies:
- ``build_release`` writes a release-manifest.json when called with no flags.
- ``build_release --render-midi`` produces a zip archive of all MIDI files.
- ``build_release --render-audio`` copies the MIDI as an audio stub.
- ``build_release --export-stems`` produces per-track audio stubs.
- ``build_release`` raises ``ValueError`` when no MIDI files exist in snapshot.
- ``build_release`` raises ``StorpheusReleaseUnavailableError`` when Storpheus
  is down and audio rendering is requested.
- ``_resolve_tag_to_commit`` resolves a tag to the most recent commit.
- ``_resolve_tag_to_commit`` falls back to prefix lookup when tag not found.
- ``_release_async`` (regression): resolves tag, fetches manifest, delegates.
- Boundary seal (AST): ``from __future__ import annotations`` present.
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import uuid
import zipfile
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as cli_models  # noqa: F401 — register tables
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot, MuseCliTag
from maestro.services.muse_release import (
    ReleaseAudioFormat,
    ReleaseResult,
    StorpheusReleaseUnavailableError,
    _collect_midi_paths,
    _sha256_file,
    build_release,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all CLI tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def repo_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Muse repo structure under *tmp_path*."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "HEAD").write_text("refs/heads/main")
    refs_dir = muse_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def repo_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def write_repo_json(repo_root: pathlib.Path, repo_id: str) -> None:
    """Write .muse/repo.json with a stable repo_id."""
    (repo_root / ".muse" / "repo.json").write_text(json.dumps({"repo_id": repo_id}))


@pytest.fixture
def midi_repo(repo_root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Create a muse-work/ directory with two MIDI stub files.

    Returns a dict mapping relative path strings to absolute Path objects.
    """
    workdir = repo_root / "muse-work"
    workdir.mkdir()
    paths: dict[str, pathlib.Path] = {}
    for name in ("piano.mid", "bass.mid"):
        p = workdir / name
        p.write_bytes(b"MIDI")
        paths[name] = p
    return paths


async def _insert_commit_with_tag(
    session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
    tag: str,
    manifest: dict[str, str] | None = None,
    commit_id_char: str = "b",
) -> str:
    """Insert a commit + snapshot + tag; return the commit_id."""
    object_id = "c" * 64
    snapshot_id = ("s" + commit_id_char) * 32
    commit_id = commit_id_char * 64

    if not manifest:
        manifest = {"piano.mid": object_id}

    session.add(MuseCliObject(object_id=object_id, size_bytes=4))
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest=manifest))
    await session.flush()

    committed_at = datetime.datetime.now(datetime.timezone.utc)
    session.add(
        MuseCliCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch="main",
            parent_commit_id=None,
            parent2_commit_id=None,
            snapshot_id=snapshot_id,
            message="tagged commit",
            author="",
            committed_at=committed_at,
        )
    )
    await session.flush()

    session.add(MuseCliTag(repo_id=repo_id, commit_id=commit_id, tag=tag))
    await session.flush()

    # Update HEAD pointer
    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id)
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests — service layer (build_release)
# ---------------------------------------------------------------------------


def test_build_release_writes_manifest_only(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """build_release writes release-manifest.json even when no flags are set."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    with patch(
        "maestro.services.muse_release._check_storpheus_reachable"
    ):  # not called — no audio flags
        result = build_release(
            tag="v1.0",
            commit_id="b" * 64,
            manifest=manifest,
            root=repo_root,
            output_dir=output_dir,
            render_audio=False,
            render_midi=False,
            export_stems=False,
        )

    assert result.manifest_path.exists()
    data = json.loads(result.manifest_path.read_text())
    assert data["tag"] == "v1.0"
    assert data["commit_id"] == "b" * 64
    assert data["commit_short"] == "b" * 8
    assert "released_at" in data
    assert isinstance(data["files"], list)


def test_build_release_render_midi_produces_zip(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """build_release --render-midi produces a zip containing all MIDI files."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    result = build_release(
        tag="v1.0",
        commit_id="b" * 64,
        manifest=manifest,
        root=repo_root,
        output_dir=output_dir,
        render_midi=True,
    )

    bundle_path = output_dir / "midi" / "midi-bundle.zip"
    assert bundle_path.exists()
    assert any(a.role == "midi-bundle" for a in result.artifacts)

    with zipfile.ZipFile(bundle_path) as zf:
        names = zf.namelist()
    assert "piano.mid" in names
    assert "bass.mid" in names


def test_build_release_render_audio_produces_stub(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """build_release --render-audio copies MIDI as audio stub when /render not deployed."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    with patch(
        "maestro.services.muse_release._check_storpheus_reachable"
    ):
        result = build_release(
            tag="v1.0",
            commit_id="b" * 64,
            manifest=manifest,
            root=repo_root,
            output_dir=output_dir,
            render_audio=True,
        )

    audio_artifact = next(a for a in result.artifacts if a.role == "audio")
    assert audio_artifact.path.exists()
    assert audio_artifact.path.suffix == ".wav"
    assert result.stubbed is True


def test_build_release_export_stems_produces_per_track_files(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """build_release --export-stems writes one audio file per MIDI track."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    with patch(
        "maestro.services.muse_release._check_storpheus_reachable"
    ):
        result = build_release(
            tag="v1.0",
            commit_id="b" * 64,
            manifest=manifest,
            root=repo_root,
            output_dir=output_dir,
            export_stems=True,
            audio_format=ReleaseAudioFormat.FLAC,
        )

    stem_artifacts = [a for a in result.artifacts if a.role == "stem"]
    assert len(stem_artifacts) == 2
    for a in stem_artifacts:
        assert a.path.suffix == ".flac"
        assert a.path.exists()


def test_build_release_manifest_contains_checksums(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """release-manifest.json includes sha256 checksums for every artifact."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    result = build_release(
        tag="v1.0",
        commit_id="b" * 64,
        manifest=manifest,
        root=repo_root,
        output_dir=output_dir,
        render_midi=True,
    )

    data = json.loads(result.manifest_path.read_text())
    for file_entry in data["files"]:
        assert "sha256" in file_entry
        assert len(file_entry["sha256"]) == 64
        assert "size_bytes" in file_entry
        assert "role" in file_entry


def test_build_release_raises_when_no_midi_files(
    repo_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """build_release raises ValueError when no MIDI files exist in snapshot."""
    workdir = repo_root / "muse-work"
    workdir.mkdir()
    (workdir / "notes.json").write_text("{}")  # not a MIDI file

    manifest = {"notes.json": "c" * 64}
    output_dir = tmp_path / "releases" / "v1.0"

    with pytest.raises(ValueError, match="No MIDI files found"):
        build_release(
            tag="v1.0",
            commit_id="b" * 64,
            manifest=manifest,
            root=repo_root,
            output_dir=output_dir,
            render_audio=True,
        )


def test_build_release_raises_when_storpheus_unreachable(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """build_release raises StorpheusReleaseUnavailableError when Storpheus is down."""
    manifest = {name: "c" * 64 for name in midi_repo}
    output_dir = tmp_path / "releases" / "v1.0"

    with patch(
        "maestro.services.muse_release._check_storpheus_reachable",
        side_effect=StorpheusReleaseUnavailableError("Storpheus is down"),
    ):
        with pytest.raises(StorpheusReleaseUnavailableError):
            build_release(
                tag="v1.0",
                commit_id="b" * 64,
                manifest=manifest,
                root=repo_root,
                output_dir=output_dir,
                render_audio=True,
            )


# ---------------------------------------------------------------------------
# Unit tests — SHA-256 helper
# ---------------------------------------------------------------------------


def test_sha256_file_matches_known_digest(tmp_path: pathlib.Path) -> None:
    """_sha256_file computes the correct SHA-256 for a known byte sequence."""
    import hashlib

    content = b"MIDI content for hashing"
    p = tmp_path / "test.mid"
    p.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert _sha256_file(p) == expected


# ---------------------------------------------------------------------------
# Unit tests — _collect_midi_paths
# ---------------------------------------------------------------------------


def test_collect_midi_paths_filters_by_track(
    repo_root: pathlib.Path,
    midi_repo: dict[str, pathlib.Path],
) -> None:
    """_collect_midi_paths returns only paths matching the track filter."""
    manifest = {name: "c" * 64 for name in midi_repo}
    paths, skipped = _collect_midi_paths(manifest, repo_root, track="piano")

    assert len(paths) == 1
    assert paths[0].name == "piano.mid"
    assert skipped == 1


def test_collect_midi_paths_skips_missing_files(
    repo_root: pathlib.Path,
) -> None:
    """_collect_midi_paths counts missing files in skipped_count."""
    workdir = repo_root / "muse-work"
    workdir.mkdir()
    manifest = {"missing.mid": "c" * 64}  # file does not exist on disk

    paths, skipped = _collect_midi_paths(manifest, repo_root)
    assert paths == []
    assert skipped == 1


# ---------------------------------------------------------------------------
# Integration tests — _resolve_tag_to_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_tag_to_commit_finds_tagged_commit(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """_resolve_tag_to_commit resolves a tag string to the correct commit ID."""
    from maestro.muse_cli.commands.release import _resolve_tag_to_commit

    commit_id = await _insert_commit_with_tag(async_session, repo_id, repo_root, "v1.0")

    resolved = await _resolve_tag_to_commit(async_session, repo_root, "v1.0")
    assert resolved == commit_id


@pytest.mark.anyio
async def test_resolve_tag_to_commit_uses_most_recent_when_ambiguous(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """When multiple commits share a tag, the most recently committed is returned."""
    from maestro.muse_cli.commands.release import _resolve_tag_to_commit

    object_id = "c" * 64
    snap1_id = "s1" * 32
    snap2_id = "s2" * 32
    cid1 = "1" * 64
    cid2 = "2" * 64

    async_session.add(MuseCliObject(object_id=object_id, size_bytes=4))
    async_session.add(MuseCliSnapshot(snapshot_id=snap1_id, manifest={"a.mid": object_id}))
    async_session.add(MuseCliSnapshot(snapshot_id=snap2_id, manifest={"b.mid": object_id}))
    await async_session.flush()

    t1 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc)  # more recent

    async_session.add(
        MuseCliCommit(
            commit_id=cid1, repo_id=repo_id, branch="main",
            parent_commit_id=None, parent2_commit_id=None,
            snapshot_id=snap1_id, message="old", author="", committed_at=t1,
        )
    )
    async_session.add(
        MuseCliCommit(
            commit_id=cid2, repo_id=repo_id, branch="main",
            parent_commit_id=cid1, parent2_commit_id=None,
            snapshot_id=snap2_id, message="newer", author="", committed_at=t2,
        )
    )
    await async_session.flush()

    async_session.add(MuseCliTag(repo_id=repo_id, commit_id=cid1, tag="v1.0"))
    async_session.add(MuseCliTag(repo_id=repo_id, commit_id=cid2, tag="v1.0"))
    await async_session.flush()

    resolved = await _resolve_tag_to_commit(async_session, repo_root, "v1.0")
    assert resolved == cid2


@pytest.mark.anyio
async def test_resolve_tag_to_commit_falls_back_to_prefix(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """_resolve_tag_to_commit falls back to commit prefix lookup when tag absent."""
    from maestro.muse_cli.commands.release import _resolve_tag_to_commit

    commit_id = await _insert_commit_with_tag(async_session, repo_id, repo_root, "v2.0")
    # Use the commit ID prefix directly (not the tag)
    resolved = await _resolve_tag_to_commit(async_session, repo_root, commit_id[:8])
    assert resolved == commit_id


# ---------------------------------------------------------------------------
# Regression test — test_release_resolves_tag_and_exports_manifest
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_release_resolves_tag_and_exports_manifest(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
    midi_repo: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """Regression: _release_async resolves tag, fetches manifest, writes manifest.json.

    This is the primary acceptance criterion for issue #82: a producer runs
    'muse release v1.0' and receives a release-manifest.json pinning the
    tagged snapshot.
    """
    from maestro.muse_cli.commands.release import _release_async

    manifest = {name: "c" * 64 for name in midi_repo}
    commit_id = await _insert_commit_with_tag(
        async_session, repo_id, repo_root, "v1.0", manifest=manifest
    )

    output_dir = tmp_path / "releases" / "v1.0"

    with patch("maestro.config.settings") as mock_settings:
        mock_settings.storpheus_base_url = "http://storpheus:10002"
        result = await _release_async(
            tag="v1.0",
            audio_format=ReleaseAudioFormat.WAV,
            output_dir=output_dir,
            render_audio=False,
            render_midi=True,
            export_stems=False,
            root=repo_root,
            session=async_session,
        )

    assert result.tag == "v1.0"
    assert result.commit_id == commit_id
    assert result.manifest_path.exists()

    data = json.loads(result.manifest_path.read_text())
    assert data["tag"] == "v1.0"
    assert data["commit_id"] == commit_id

    # MIDI bundle should be present
    bundle_artifact = next((a for a in result.artifacts if a.role == "midi-bundle"), None)
    assert bundle_artifact is not None
    assert bundle_artifact.path.exists()

    with zipfile.ZipFile(bundle_artifact.path) as zf:
        names = zf.namelist()
    for midi_name in midi_repo:
        assert midi_name in names


# ---------------------------------------------------------------------------
# Boundary seal
# ---------------------------------------------------------------------------


def test_future_annotations_in_service() -> None:
    """``from __future__ import annotations`` is present in muse_release.py."""
    import maestro.services.muse_release as mod

    src = pathlib.Path(mod.__file__).read_text()
    tree = ast.parse(src)
    future_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
    ]
    assert future_imports, "from __future__ import annotations missing in muse_release.py"


def test_future_annotations_in_command() -> None:
    """``from __future__ import annotations`` is present in commands/release.py."""
    import maestro.muse_cli.commands.release as mod

    src = pathlib.Path(mod.__file__).read_text()
    tree = ast.parse(src)
    future_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
    ]
    assert future_imports, "from __future__ import annotations missing in commands/release.py"
