"""Tests for ``muse timeline`` — service layer, CLI flags, and output format.

Test strategy
-------------
- Service layer (:func:`build_timeline`) is tested with real SQLite commits
  and tags to verify chronological ordering, tag extraction, and summaries.
- CLI command (``_timeline_async``) is tested with an in-memory SQLite session
  and a minimal ``.muse/`` layout, exercising every flag combination.
- CLI integration tests use ``typer.testing.CliRunner`` against the full ``muse``
  app for argument parsing and exit code verification.
- Renderers (``_render_text``, ``_render_json``) are tested directly via capsys.

Naming: ``test_<behavior>_<scenario>`` throughout.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401 — registers models with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.timeline import (
    _activity_bar,
    _entry_to_dict,
    _render_json,
    _render_text,
    _timeline_async,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot, MuseCliTag
from maestro.services.muse_timeline import (
    MuseTimelineEntry,
    MuseTimelineResult,
    _extract_prefix,
    _group_tags_by_commit,
    _make_entry,
    build_timeline,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int) -> datetime:
    """Return a UTC datetime at midnight on the given date."""
    return datetime(year, month, day, tzinfo=timezone.utc)


def _repo_id() -> str:
    return str(uuid.uuid4())


def _snapshot_id() -> str:
    return uuid.uuid4().hex * 2  # 64-char hex


def _commit_id() -> str:
    return uuid.uuid4().hex * 2  # 64-char hex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all Muse tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _init_muse_repo(
    root: pathlib.Path,
    branch: str = "main",
    repo_id: str | None = None,
) -> str:
    """Create a minimal ``.muse/`` directory tree with a repo.json."""
    rid = repo_id or _repo_id()
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _set_head(root: pathlib.Path, commit_id: str, branch: str = "main") -> None:
    """Write *commit_id* into the branch ref file."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text(commit_id)


async def _insert_snapshot(session: AsyncSession) -> str:
    """Insert a blank snapshot and return its ID."""
    sid = _snapshot_id()
    snap = MuseCliSnapshot(snapshot_id=sid, manifest={})
    session.add(snap)
    await session.flush()
    return sid


async def _insert_commit(
    session: AsyncSession,
    repo_id: str,
    branch: str,
    message: str,
    committed_at: datetime,
    parent_id: str | None = None,
) -> MuseCliCommit:
    """Insert a commit row and return the ORM object."""
    sid = await _insert_snapshot(session)
    cid = _commit_id()
    commit = MuseCliCommit(
        commit_id=cid,
        repo_id=repo_id,
        branch=branch,
        message=message,
        committed_at=committed_at,
        parent_commit_id=parent_id,
        snapshot_id=sid,
    )
    session.add(commit)
    await session.flush()
    return commit


async def _attach_tag(
    session: AsyncSession,
    repo_id: str,
    commit_id: str,
    tag: str,
) -> MuseCliTag:
    """Attach a tag to a commit."""
    t = MuseCliTag(repo_id=repo_id, commit_id=commit_id, tag=tag)
    session.add(t)
    await session.flush()
    return t


# ---------------------------------------------------------------------------
# Unit — service helpers
# ---------------------------------------------------------------------------


def test_extract_prefix_matches_known_prefix() -> None:
    """_extract_prefix returns the value after a matching prefix."""
    assert _extract_prefix("emotion:melancholic", "emotion:") == "melancholic"
    assert _extract_prefix("section:chorus", "section:") == "chorus"
    assert _extract_prefix("track:bass", "track:") == "bass"


def test_extract_prefix_returns_none_for_mismatch() -> None:
    """_extract_prefix returns None when the prefix doesn't match."""
    assert _extract_prefix("stage:rough-mix", "emotion:") is None
    assert _extract_prefix("", "emotion:") is None


def test_group_tags_by_commit_groups_correctly() -> None:
    """_group_tags_by_commit maps commit IDs to their tag lists."""
    tags = [
        MuseCliTag(repo_id="r1", commit_id="aaa", tag="emotion:joyful"),
        MuseCliTag(repo_id="r1", commit_id="aaa", tag="section:chorus"),
        MuseCliTag(repo_id="r1", commit_id="bbb", tag="emotion:melancholic"),
    ]
    grouped = _group_tags_by_commit(tags)
    assert set(grouped["aaa"]) == {"emotion:joyful", "section:chorus"}
    assert grouped["bbb"] == ["emotion:melancholic"]


def test_make_entry_parses_emotion_and_section_and_tracks() -> None:
    """_make_entry extracts emotion, sections, and tracks from tags."""
    snap_id = _snapshot_id()
    commit = MuseCliCommit(
        commit_id="a" * 64,
        repo_id="r1",
        branch="main",
        message="Add chorus melody",
        committed_at=_utc(2026, 2, 3),
        snapshot_id=snap_id,
    )
    tags = ["emotion:joyful", "section:chorus", "track:keys", "track:vocals"]
    entry = _make_entry(commit, tags)

    assert entry.short_id == "aaaaaaa"
    assert entry.emotion == "joyful"
    assert entry.sections == ("chorus",)
    assert set(entry.tracks) == {"keys", "vocals"}
    assert entry.activity == 2  # two tracks


def test_make_entry_defaults_when_no_tags() -> None:
    """_make_entry with no tags sets emotion=None, sections/tracks empty, activity=1."""
    snap_id = _snapshot_id()
    commit = MuseCliCommit(
        commit_id="b" * 64,
        repo_id="r1",
        branch="main",
        message="Initial take",
        committed_at=_utc(2026, 2, 1),
        snapshot_id=snap_id,
    )
    entry = _make_entry(commit, [])
    assert entry.emotion is None
    assert entry.sections == ()
    assert entry.tracks == ()
    assert entry.activity == 1


# ---------------------------------------------------------------------------
# Unit — activity_bar
# ---------------------------------------------------------------------------


def test_activity_bar_max_activity_produces_max_blocks() -> None:
    """The most-active commit gets the maximum block count."""
    bar = _activity_bar(10, 10)
    assert len(bar) == 10
    assert bar == "█" * 10


def test_activity_bar_scales_proportionally() -> None:
    """Half-max activity should produce ~half the blocks."""
    bar = _activity_bar(5, 10)
    assert 4 <= len(bar) <= 6  # allow rounding


def test_activity_bar_zero_max_returns_minimum() -> None:
    """Zero max_activity is handled gracefully with min blocks."""
    bar = _activity_bar(0, 0)
    assert len(bar) >= 1


# ---------------------------------------------------------------------------
# Unit — service: build_timeline
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_timeline_outputs_chronological_history(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """build_timeline returns entries in oldest-first order.

    Regression test: ensures chronological ordering is preserved regardless
    of DB insertion order.
    """
    rid = _repo_id()
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    c2 = await _insert_commit(db_session, rid, "main", "Add bass", _utc(2026, 2, 2), c1.commit_id)
    c3 = await _insert_commit(db_session, rid, "main", "Chorus", _utc(2026, 2, 3), c2.commit_id)
    await db_session.commit()

    result = await build_timeline(
        db_session, repo_id=rid, branch="main", head_commit_id=c3.commit_id
    )

    assert result.total_commits == 3
    assert result.entries[0].commit_id == c1.commit_id   # oldest first
    assert result.entries[1].commit_id == c2.commit_id
    assert result.entries[2].commit_id == c3.commit_id   # newest last


@pytest.mark.anyio
async def test_timeline_empty_when_no_commits(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """build_timeline with a non-existent head_commit_id returns an empty result."""
    rid = _repo_id()
    result = await build_timeline(
        db_session, repo_id=rid, branch="main", head_commit_id="nonexistent"
    )
    assert result.total_commits == 0
    assert result.entries == ()


@pytest.mark.anyio
async def test_timeline_extracts_emotion_arc(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """build_timeline computes emotion_arc as ordered unique emotion labels."""
    rid = _repo_id()
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "emotion:melancholic")
    c2 = await _insert_commit(db_session, rid, "main", "Chorus", _utc(2026, 2, 2), c1.commit_id)
    await _attach_tag(db_session, rid, c2.commit_id, "emotion:joyful")
    c3 = await _insert_commit(db_session, rid, "main", "Bridge", _utc(2026, 2, 3), c2.commit_id)
    await _attach_tag(db_session, rid, c3.commit_id, "emotion:tense")
    await db_session.commit()

    result = await build_timeline(
        db_session, repo_id=rid, branch="main", head_commit_id=c3.commit_id
    )
    assert result.emotion_arc == ("melancholic", "joyful", "tense")


@pytest.mark.anyio
async def test_timeline_deduplicated_emotion_arc(
    db_session: AsyncSession,
) -> None:
    """build_timeline deduplicates emotion_arc — repeated emotions appear once."""
    rid = _repo_id()
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "emotion:melancholic")
    c2 = await _insert_commit(db_session, rid, "main", "Add keys", _utc(2026, 2, 2), c1.commit_id)
    await _attach_tag(db_session, rid, c2.commit_id, "emotion:melancholic")
    await db_session.commit()

    result = await build_timeline(
        db_session, repo_id=rid, branch="main", head_commit_id=c2.commit_id
    )
    assert result.emotion_arc == ("melancholic",)


@pytest.mark.anyio
async def test_timeline_section_order(
    db_session: AsyncSession,
) -> None:
    """build_timeline records section_order in order of first appearance."""
    rid = _repo_id()
    c1 = await _insert_commit(db_session, rid, "main", "Verse 1", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "section:verse")
    c2 = await _insert_commit(db_session, rid, "main", "Chorus", _utc(2026, 2, 2), c1.commit_id)
    await _attach_tag(db_session, rid, c2.commit_id, "section:chorus")
    await db_session.commit()

    result = await build_timeline(
        db_session, repo_id=rid, branch="main", head_commit_id=c2.commit_id
    )
    assert result.section_order == ("verse", "chorus")


@pytest.mark.anyio
async def test_timeline_limit_caps_entries(
    db_session: AsyncSession,
) -> None:
    """build_timeline respects the limit parameter."""
    rid = _repo_id()
    prev_id: str | None = None
    last_commit: MuseCliCommit | None = None
    for i in range(5):
        c = await _insert_commit(
            db_session, rid, "main", f"commit {i}", _utc(2026, 2, i + 1), prev_id
        )
        prev_id = c.commit_id
        last_commit = c
    await db_session.commit()

    assert last_commit is not None
    result = await build_timeline(
        db_session, repo_id=rid, branch="main",
        head_commit_id=last_commit.commit_id, limit=3
    )
    assert result.total_commits == 3


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def test_render_text_outputs_header(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_text prints branch name and commit count header."""
    entry = MuseTimelineEntry(
        commit_id="a" * 64,
        short_id="aaaaaaa",
        committed_at=_utc(2026, 2, 1),
        message="Initial drums",
        emotion=None,
        sections=(),
        tracks=(),
        activity=1,
    )
    result = MuseTimelineResult(
        entries=(entry,),
        branch="main",
        emotion_arc=(),
        section_order=(),
        total_commits=1,
    )
    _render_text(result, show_emotion=False, show_sections=False, show_tracks=False)
    out = capsys.readouterr().out
    assert "main" in out
    assert "1 commit" in out
    assert "aaaaaaa" in out
    assert "2026-02-01" in out


def test_render_text_no_commits(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_text with empty entries prints the empty message."""
    result = MuseTimelineResult(
        entries=(), branch="main", emotion_arc=(), section_order=(), total_commits=0
    )
    _render_text(result, show_emotion=False, show_sections=False, show_tracks=False)
    out = capsys.readouterr().out
    assert "No commits" in out


def test_render_text_show_emotion_column(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_text with show_emotion=True includes emotion values in output."""
    entry = MuseTimelineEntry(
        commit_id="b" * 64,
        short_id="bbbbbbb",
        committed_at=_utc(2026, 2, 2),
        message="Add chorus",
        emotion="joyful",
        sections=("chorus",),
        tracks=("keys",),
        activity=1,
    )
    result = MuseTimelineResult(
        entries=(entry,),
        branch="main",
        emotion_arc=("joyful",),
        section_order=("chorus",),
        total_commits=1,
    )
    _render_text(result, show_emotion=True, show_sections=False, show_tracks=False)
    out = capsys.readouterr().out
    assert "joyful" in out
    assert "Emotion arc" in out


def test_render_text_show_sections_header(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_text with show_sections=True prints section header lines."""
    e1 = MuseTimelineEntry(
        commit_id="c" * 64,
        short_id="ccccccc",
        committed_at=_utc(2026, 2, 1),
        message="Verse start",
        emotion=None,
        sections=("verse",),
        tracks=(),
        activity=1,
    )
    e2 = MuseTimelineEntry(
        commit_id="d" * 64,
        short_id="ddddddd",
        committed_at=_utc(2026, 2, 2),
        message="Chorus start",
        emotion=None,
        sections=("chorus",),
        tracks=(),
        activity=1,
    )
    result = MuseTimelineResult(
        entries=(e1, e2),
        branch="main",
        emotion_arc=(),
        section_order=("verse", "chorus"),
        total_commits=2,
    )
    _render_text(result, show_emotion=False, show_sections=True, show_tracks=False)
    out = capsys.readouterr().out
    assert "verse" in out
    assert "chorus" in out
    assert "──" in out


def test_render_text_show_tracks_column(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_text with show_tracks=True includes track names in output."""
    entry = MuseTimelineEntry(
        commit_id="e" * 64,
        short_id="eeeeeee",
        committed_at=_utc(2026, 2, 1),
        message="Add bass",
        emotion=None,
        sections=(),
        tracks=("bass", "drums"),
        activity=2,
    )
    result = MuseTimelineResult(
        entries=(entry,),
        branch="main",
        emotion_arc=(),
        section_order=(),
        total_commits=1,
    )
    _render_text(result, show_emotion=False, show_sections=False, show_tracks=True)
    out = capsys.readouterr().out
    assert "bass" in out
    assert "drums" in out


def test_render_json_is_valid_and_complete(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_json emits valid JSON with expected top-level keys and entry fields."""
    entry = MuseTimelineEntry(
        commit_id="f" * 64,
        short_id="fffffff",
        committed_at=_utc(2026, 2, 3),
        message="Chorus melody",
        emotion="joyful",
        sections=("chorus",),
        tracks=("keys", "vocals"),
        activity=2,
    )
    result = MuseTimelineResult(
        entries=(entry,),
        branch="main",
        emotion_arc=("joyful",),
        section_order=("chorus",),
        total_commits=1,
    )
    _render_json(result)
    raw = capsys.readouterr().out
    payload = json.loads(raw)

    assert payload["branch"] == "main"
    assert payload["total_commits"] == 1
    assert payload["emotion_arc"] == ["joyful"]
    assert payload["section_order"] == ["chorus"]
    assert len(payload["entries"]) == 1

    e = payload["entries"][0]
    assert e["short_id"] == "fffffff"
    assert e["emotion"] == "joyful"
    assert e["sections"] == ["chorus"]
    assert set(e["tracks"]) == {"keys", "vocals"}
    assert e["activity"] == 2


def test_entry_to_dict_serializes_all_fields() -> None:
    """_entry_to_dict includes all required fields with correct types."""
    entry = MuseTimelineEntry(
        commit_id="a" * 64,
        short_id="aaaaaaa",
        committed_at=_utc(2026, 2, 1),
        message="Init",
        emotion=None,
        sections=(),
        tracks=(),
        activity=1,
    )
    d = _entry_to_dict(entry)
    assert "commit_id" in d
    assert "short_id" in d
    assert "committed_at" in d
    assert "message" in d
    assert "emotion" in d
    assert "sections" in d
    assert "tracks" in d
    assert "activity" in d
    assert d["emotion"] is None
    assert isinstance(d["sections"], list)
    assert isinstance(d["tracks"], list)


# ---------------------------------------------------------------------------
# Async core — _timeline_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_timeline_async_default_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async default text mode shows all commits chronologically."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    c2 = await _insert_commit(db_session, rid, "main", "Bass", _utc(2026, 2, 2), c1.commit_id)
    await db_session.commit()
    _set_head(tmp_path, c2.commit_id)

    result = await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=False,
        show_sections=False,
        show_tracks=False,
        as_json=False,
        limit=1000,
    )

    assert result.total_commits == 2
    assert result.entries[0].commit_id == c1.commit_id  # oldest first
    out = capsys.readouterr().out
    assert "main" in out
    assert "2026-02-01" in out


@pytest.mark.anyio
async def test_timeline_async_json_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async --json emits valid JSON with all entries."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    c2 = await _insert_commit(db_session, rid, "main", "Chorus", _utc(2026, 2, 2), c1.commit_id)
    await db_session.commit()
    _set_head(tmp_path, c2.commit_id)

    await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=False,
        show_sections=False,
        show_tracks=False,
        as_json=True,
        limit=1000,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["total_commits"] == 2
    assert len(payload["entries"]) == 2


@pytest.mark.anyio
async def test_timeline_async_no_commits_exits_success(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async exits 0 with an informative message when branch has no commits."""
    _init_muse_repo(tmp_path)
    # No _set_head — branch ref stays empty.

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _timeline_async(
            root=tmp_path,
            session=db_session,
            commit_range=None,
            show_emotion=False,
            show_sections=False,
            show_tracks=False,
            as_json=False,
            limit=1000,
        )
    assert exc_info.value.exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "empty" in out.lower() or "no commits" in out.lower()


@pytest.mark.anyio
async def test_timeline_async_commit_range_reserved_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async emits a stub warning when commit_range is supplied."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Init", _utc(2026, 2, 1))
    await db_session.commit()
    _set_head(tmp_path, c1.commit_id)

    await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range="HEAD~5..HEAD",
        show_emotion=False,
        show_sections=False,
        show_tracks=False,
        as_json=False,
        limit=1000,
    )

    out = capsys.readouterr().out
    assert "reserved" in out.lower() or "HEAD~5..HEAD" in out


@pytest.mark.anyio
async def test_timeline_async_emotion_flag_shows_tags(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async --emotion includes emotion values in text output."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Intro", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "emotion:melancholic")
    await db_session.commit()
    _set_head(tmp_path, c1.commit_id)

    await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=True,
        show_sections=False,
        show_tracks=False,
        as_json=False,
        limit=1000,
    )

    out = capsys.readouterr().out
    assert "melancholic" in out


@pytest.mark.anyio
async def test_timeline_async_sections_flag_groups_commits(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async --sections prints section header lines."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Verse", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "section:verse")
    c2 = await _insert_commit(db_session, rid, "main", "Chorus", _utc(2026, 2, 2), c1.commit_id)
    await _attach_tag(db_session, rid, c2.commit_id, "section:chorus")
    await db_session.commit()
    _set_head(tmp_path, c2.commit_id)

    await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=False,
        show_sections=True,
        show_tracks=False,
        as_json=False,
        limit=1000,
    )

    out = capsys.readouterr().out
    assert "verse" in out
    assert "chorus" in out
    assert "──" in out


@pytest.mark.anyio
async def test_timeline_async_tracks_flag_shows_track_column(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async --tracks includes track names in text output."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Add bass", _utc(2026, 2, 1))
    await _attach_tag(db_session, rid, c1.commit_id, "track:bass")
    await _attach_tag(db_session, rid, c1.commit_id, "track:drums")
    await db_session.commit()
    _set_head(tmp_path, c1.commit_id)

    await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=False,
        show_sections=False,
        show_tracks=True,
        as_json=False,
        limit=1000,
    )

    out = capsys.readouterr().out
    assert "bass" in out
    assert "drums" in out


@pytest.mark.anyio
async def test_timeline_async_graceful_with_no_metadata_tags(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_timeline_async renders commits with no tags without crashing (shows '—')."""
    rid = _init_muse_repo(tmp_path)
    c1 = await _insert_commit(db_session, rid, "main", "Raw commit", _utc(2026, 2, 1))
    await db_session.commit()
    _set_head(tmp_path, c1.commit_id)

    result = await _timeline_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        show_emotion=True,
        show_sections=True,
        show_tracks=True,
        as_json=False,
        limit=1000,
    )
    # Should not crash and should show the commit.
    assert result.total_commits == 1
    out = capsys.readouterr().out
    assert "Raw commit" in out


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_timeline_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse timeline`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["timeline"], catch_exceptions=False)
    finally:
        os.chdir(prev)
    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_timeline_help_lists_all_flags() -> None:
    """``muse timeline --help`` shows all documented flags."""
    result = runner.invoke(cli, ["timeline", "--help"])
    assert result.exit_code == 0
    for flag in ("--emotion", "--sections", "--tracks", "--json", "--limit"):
        assert flag in result.output, f"Flag '{flag}' not in help"


def test_cli_timeline_appears_in_muse_help() -> None:
    """``muse --help`` lists the timeline subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "timeline" in result.output
