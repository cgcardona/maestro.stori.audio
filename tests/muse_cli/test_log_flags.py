"""Tests for the extended ``muse log`` flag set (issue #76).

Covers all new flags added to ``_log_async``:
- ``--oneline``       — one line per commit
- ``--stat``          — file-change statistics per commit
- ``--patch``         — path-level diff per commit
- ``--since`` / ``--until`` — date range filtering
- ``--author``        — author substring filter
- ``--emotion`` / ``--section`` / ``--track`` — music-native tag filters

All tests call ``_log_async`` directly with an in-memory SQLite session and
a ``tmp_path`` repo root.  Tag-based tests insert ``MuseCliTag`` rows directly
to avoid depending on ``muse commit --emotion`` (a separate issue).
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.log import (
    CommitDiff,
    _compute_diff,
    _filter_by_tags,
    _load_commits,
    _log_async,
    _render_oneline,
    _render_stat,
    _render_patch,
    parse_date_filter,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliTag


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commits(
    root: pathlib.Path,
    session: AsyncSession,
    messages: list[str],
    file_seed: int = 0,
    author: str = "",
) -> list[str]:
    """Create N commits returning their commit IDs in order (oldest first)."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(root, {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()})
        cid = await _commit_async(message=msg, root=root, session=session)
        # Patch author directly on the DB row if needed
        if author:
            commit = await session.get(MuseCliCommit, cid)
            if commit is not None:
                commit.author = author
                session.add(commit)
                await session.flush()
        commit_ids.append(cid)
    return commit_ids


async def _tag_commit(
    session: AsyncSession,
    repo_id: str,
    commit_id: str,
    tag: str,
) -> None:
    """Insert a MuseCliTag row for testing tag-based filters."""
    session.add(
        MuseCliTag(
            repo_id=repo_id,
            commit_id=commit_id,
            tag=tag,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# parse_date_filter unit tests
# ---------------------------------------------------------------------------


def test_parse_date_filter_iso_date() -> None:
    """ISO date string produces a UTC-aware datetime at midnight."""
    dt = parse_date_filter("2026-01-15")
    assert dt.year == 2026
    assert dt.month == 1
    assert dt.day == 15
    assert dt.tzinfo is not None


def test_parse_date_filter_iso_datetime() -> None:
    """ISO datetime string produces correct UTC-aware datetime."""
    dt = parse_date_filter("2026-06-01T14:30:00")
    assert dt.hour == 14
    assert dt.minute == 30


def test_parse_date_filter_relative_days() -> None:
    """'N days ago' produces a datetime N days before now."""
    before = datetime.now(timezone.utc) - timedelta(days=7)
    dt = parse_date_filter("7 days ago")
    assert abs((dt - before).total_seconds()) < 60


def test_parse_date_filter_relative_weeks() -> None:
    """'N weeks ago' produces a datetime N*7 days before now."""
    before = datetime.now(timezone.utc) - timedelta(weeks=2)
    dt = parse_date_filter("2 weeks ago")
    assert abs((dt - before).total_seconds()) < 60


def test_parse_date_filter_relative_months() -> None:
    """'N months ago' approximates N*30 days before now."""
    before = datetime.now(timezone.utc) - timedelta(days=30)
    dt = parse_date_filter("1 month ago")
    assert abs((dt - before).total_seconds()) < 60


def test_parse_date_filter_today() -> None:
    """'today' returns today at midnight UTC."""
    dt = parse_date_filter("today")
    now = datetime.now(timezone.utc)
    assert dt.year == now.year
    assert dt.month == now.month
    assert dt.day == now.day
    assert dt.hour == 0


def test_parse_date_filter_yesterday() -> None:
    """'yesterday' returns yesterday at midnight UTC."""
    dt = parse_date_filter("yesterday")
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    assert dt.day == yesterday.day


def test_parse_date_filter_invalid_raises() -> None:
    """An unrecognised string raises ValueError."""
    with pytest.raises(ValueError, match="Cannot parse date"):
        parse_date_filter("not a date")


# ---------------------------------------------------------------------------
# test_log_oneline_format
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_oneline_format(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--oneline`` shows exactly one line per commit with short id and message."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2", "take 3"])

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        oneline=True,
    )
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]

    # Three commits → three lines
    assert len(lines) == 3
    # Each line starts with the short commit id
    for cid, msg in zip(reversed(cids), ["take 3", "take 2", "take 1"]):
        matching = [l for l in lines if l.startswith(cid[:8])]
        assert matching, f"No oneline entry for commit {cid[:8]}"
        assert msg in matching[0]


@pytest.mark.anyio
async def test_log_oneline_head_marker(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--oneline`` HEAD commit line includes the branch marker."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["first", "second"])

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        oneline=True,
    )
    out = capsys.readouterr().out
    head_line = [l for l in out.splitlines() if cids[1][:8] in l][0]
    assert "(HEAD -> main)" in head_line
    # Older commit must NOT have the HEAD marker
    old_lines = [l for l in out.splitlines() if cids[0][:8] in l]
    assert old_lines
    assert "(HEAD -> main)" not in old_lines[0]


# ---------------------------------------------------------------------------
# test_log_since_until_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_since_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--since`` excludes commits older than the cutoff date."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["old", "recent"])

    # Back-date the first commit to 10 days ago
    old_commit = await muse_cli_db_session.get(MuseCliCommit, cids[0])
    assert old_commit is not None
    old_commit.committed_at = datetime.now(timezone.utc) - timedelta(days=10)
    muse_cli_db_session.add(old_commit)
    await muse_cli_db_session.flush()

    since_dt = datetime.now(timezone.utc) - timedelta(days=5)

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        since=since_dt,
    )
    out = capsys.readouterr().out

    assert cids[1] in out      # recent commit present
    assert cids[0] not in out  # old commit excluded


@pytest.mark.anyio
async def test_log_until_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--until`` excludes commits after the cutoff date."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["past", "future"])

    # Forward-date the second commit to 10 days from now
    future_commit = await muse_cli_db_session.get(MuseCliCommit, cids[1])
    assert future_commit is not None
    future_commit.committed_at = datetime.now(timezone.utc) + timedelta(days=10)
    muse_cli_db_session.add(future_commit)
    await muse_cli_db_session.flush()

    until_dt = datetime.now(timezone.utc) + timedelta(days=1)

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        until=until_dt,
    )
    out = capsys.readouterr().out

    assert cids[0] in out       # past commit present
    assert cids[1] not in out   # future commit excluded


@pytest.mark.anyio
async def test_log_since_until_combined(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--since`` and ``--until`` combined narrow to an exact window."""
    _init_muse_repo(tmp_path)
    now = datetime.now(timezone.utc)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["very old", "in window", "very new"]
    )

    # Arrange timestamps: very old = 20 days ago, in window = 5 days ago, very new = now
    commits = [await muse_cli_db_session.get(MuseCliCommit, cid) for cid in cids]
    assert all(c is not None for c in commits)
    commits[0].committed_at = now - timedelta(days=20)   # type: ignore[union-attr]
    commits[1].committed_at = now - timedelta(days=5)    # type: ignore[union-attr]
    commits[2].committed_at = now                        # type: ignore[union-attr]
    for c in commits:
        muse_cli_db_session.add(c)
    await muse_cli_db_session.flush()

    since_dt = now - timedelta(days=10)
    until_dt = now - timedelta(days=1)

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        since=since_dt,
        until=until_dt,
    )
    out = capsys.readouterr().out

    assert cids[1] in out       # in window
    assert cids[0] not in out   # too old
    assert cids[2] not in out   # too new


# ---------------------------------------------------------------------------
# test_log_author_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_author_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--author`` returns only commits by the matching author."""
    _init_muse_repo(tmp_path)
    cids_a = await _make_commits(
        tmp_path, muse_cli_db_session, ["alice track"], file_seed=0, author="alice"
    )
    cids_b = await _make_commits(
        tmp_path, muse_cli_db_session, ["bob track"], file_seed=10, author="bob"
    )

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        author="alice",
    )
    out = capsys.readouterr().out

    assert cids_a[0] in out
    assert cids_b[0] not in out


@pytest.mark.anyio
async def test_log_author_filter_case_insensitive(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Author filter is case-insensitive."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["track"], author="Alice"
    )

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        author="alice",  # lowercase query, uppercase author
    )
    out = capsys.readouterr().out
    assert cids[0] in out


# ---------------------------------------------------------------------------
# test_log_emotion_section_track_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_emotion_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--emotion`` retains only commits tagged with emotion:<value>."""
    repo_id = _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["sad verse", "happy chorus"]
    )
    await _tag_commit(muse_cli_db_session, repo_id, cids[0], "emotion:melancholic")
    await _tag_commit(muse_cli_db_session, repo_id, cids[1], "emotion:euphoric")

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        emotion="melancholic",
    )
    out = capsys.readouterr().out

    assert cids[0] in out
    assert cids[1] not in out


@pytest.mark.anyio
async def test_log_section_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--section`` retains only commits tagged with section:<value>."""
    repo_id = _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["chorus take", "verse take"]
    )
    await _tag_commit(muse_cli_db_session, repo_id, cids[0], "section:chorus")
    await _tag_commit(muse_cli_db_session, repo_id, cids[1], "section:verse")

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        section="chorus",
    )
    out = capsys.readouterr().out

    assert cids[0] in out
    assert cids[1] not in out


@pytest.mark.anyio
async def test_log_track_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--track`` retains only commits tagged with track:<value>."""
    repo_id = _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["drums pattern", "bass line"]
    )
    await _tag_commit(muse_cli_db_session, repo_id, cids[0], "track:drums")
    await _tag_commit(muse_cli_db_session, repo_id, cids[1], "track:bass")

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        track="drums",
    )
    out = capsys.readouterr().out

    assert cids[0] in out
    assert cids[1] not in out


@pytest.mark.anyio
async def test_log_emotion_section_combined(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--emotion`` AND ``--section`` together apply as AND filter."""
    repo_id = _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path,
        muse_cli_db_session,
        ["match both", "only emotion", "only section", "neither"],
    )
    # cids[0]: both tags
    await _tag_commit(muse_cli_db_session, repo_id, cids[0], "emotion:melancholic")
    await _tag_commit(muse_cli_db_session, repo_id, cids[0], "section:chorus")
    # cids[1]: emotion only
    await _tag_commit(muse_cli_db_session, repo_id, cids[1], "emotion:melancholic")
    # cids[2]: section only
    await _tag_commit(muse_cli_db_session, repo_id, cids[2], "section:chorus")
    # cids[3]: no tags

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        emotion="melancholic",
        section="chorus",
    )
    out = capsys.readouterr().out

    assert cids[0] in out       # has both
    assert cids[1] not in out   # only emotion
    assert cids[2] not in out   # only section
    assert cids[3] not in out   # neither


# ---------------------------------------------------------------------------
# test_log_stat_format
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_stat_format(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--stat`` shows file-change summary with 'N files changed' line."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2"])

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        stat=True,
    )
    out = capsys.readouterr().out

    # Both commit IDs in output
    assert cids[0] in out
    assert cids[1] in out
    # File change summary present
    assert "file" in out and "changed" in out


@pytest.mark.anyio
async def test_log_stat_shows_added_files(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--stat`` lists individual added files per commit."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"song.mid": b"MIDI"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1,
        graph=False,
        stat=True,
    )
    out = capsys.readouterr().out
    assert "song.mid" in out
    assert "added" in out


# ---------------------------------------------------------------------------
# test_log_patch_format
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_patch_format(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--patch`` shows ``--- /dev/null`` / ``+++`` diff lines for added files."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"MIDI"})
    cids = [await _commit_async(message="add drums", root=tmp_path, session=muse_cli_db_session)]

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1,
        graph=False,
        patch=True,
    )
    out = capsys.readouterr().out

    assert cids[0] in out
    assert "--- /dev/null" in out
    assert "+++ drums.mid" in out


@pytest.mark.anyio
async def test_log_patch_removed_files(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--patch`` shows ``+++ /dev/null`` for removed files."""
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir(exist_ok=True)

    # First commit: two files
    (workdir / "a.mid").write_bytes(b"A")
    (workdir / "b.mid").write_bytes(b"B")
    await _commit_async(message="two files", root=tmp_path, session=muse_cli_db_session)

    # Second commit: remove b.mid
    (workdir / "b.mid").unlink()
    cids2 = [await _commit_async(message="remove b", root=tmp_path, session=muse_cli_db_session)]

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1,
        graph=False,
        patch=True,
    )
    out = capsys.readouterr().out

    assert "b.mid" in out
    assert "+++ /dev/null" in out


# ---------------------------------------------------------------------------
# test_compute_diff unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compute_diff_root_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Root commit (no parent) treats all files as added."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"a.mid": b"A", "b.mid": b"B"})
    cid = await _commit_async(message="root", root=tmp_path, session=muse_cli_db_session)

    commit = await muse_cli_db_session.get(MuseCliCommit, cid)
    assert commit is not None
    diff = await _compute_diff(muse_cli_db_session, commit)

    assert set(diff.added) == {"a.mid", "b.mid"}
    assert diff.removed == []
    assert diff.changed == []


@pytest.mark.anyio
async def test_compute_diff_added_and_removed(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Diff between two commits correctly classifies added and removed files."""
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir(exist_ok=True)

    (workdir / "keep.mid").write_bytes(b"KEEP")
    (workdir / "gone.mid").write_bytes(b"GONE")
    await _commit_async(message="v1", root=tmp_path, session=muse_cli_db_session)

    (workdir / "gone.mid").unlink()
    (workdir / "new.mid").write_bytes(b"NEW")
    cid2 = await _commit_async(message="v2", root=tmp_path, session=muse_cli_db_session)

    commit2 = await muse_cli_db_session.get(MuseCliCommit, cid2)
    assert commit2 is not None
    diff = await _compute_diff(muse_cli_db_session, commit2)

    assert "new.mid" in diff.added
    assert "gone.mid" in diff.removed
    assert diff.changed == []


# ---------------------------------------------------------------------------
# Flag combination tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_oneline_with_author_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--oneline`` and ``--author`` can be combined: one line per matching commit."""
    _init_muse_repo(tmp_path)
    cids_a = await _make_commits(
        tmp_path, muse_cli_db_session, ["alice work"], file_seed=0, author="alice"
    )
    cids_b = await _make_commits(
        tmp_path, muse_cli_db_session, ["bob work"], file_seed=5, author="bob"
    )

    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        oneline=True,
        author="alice",
    )
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]

    assert len(lines) == 1
    assert cids_a[0][:8] in lines[0]
    assert cids_b[0][:8] not in out


@pytest.mark.anyio
async def test_log_since_with_oneline(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--since`` combined with ``--oneline`` filters correctly in oneline format."""
    _init_muse_repo(tmp_path)
    now = datetime.now(timezone.utc)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["old", "new"])

    # Back-date first commit
    old_commit = await muse_cli_db_session.get(MuseCliCommit, cids[0])
    assert old_commit is not None
    old_commit.committed_at = now - timedelta(days=10)
    muse_cli_db_session.add(old_commit)
    await muse_cli_db_session.flush()

    since_dt = now - timedelta(days=3)
    capsys.readouterr()
    await _log_async(
        root=tmp_path,
        session=muse_cli_db_session,
        limit=1000,
        graph=False,
        oneline=True,
        since=since_dt,
    )
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]

    assert len(lines) == 1
    assert cids[1][:8] in lines[0]


@pytest.mark.anyio
async def test_log_no_results_after_filter_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When filters eliminate all commits, exits 0 with friendly message."""
    import typer

    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["take 1"])

    # Set since to future so all commits are excluded
    future = datetime.now(timezone.utc) + timedelta(days=100)

    with pytest.raises(typer.Exit) as exc_info:
        await _log_async(
            root=tmp_path,
            session=muse_cli_db_session,
            limit=1000,
            graph=False,
            since=future,
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "No commits yet" in out


# ---------------------------------------------------------------------------
# CommitDiff.total_files property test
# ---------------------------------------------------------------------------


def test_commit_diff_total_files() -> None:
    """CommitDiff.total_files sums added + removed + changed."""
    diff = CommitDiff(
        added=["a.mid", "b.mid"],
        removed=["c.mid"],
        changed=["d.mid", "e.mid"],
    )
    assert diff.total_files == 5


def test_commit_diff_empty() -> None:
    """CommitDiff with no changes has total_files == 0."""
    diff = CommitDiff(added=[], removed=[], changed=[])
    assert diff.total_files == 0
