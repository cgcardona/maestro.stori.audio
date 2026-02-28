"""muse meter — read or set the time signature of a Muse CLI commit.

Commands
--------

Read the stored time signature for HEAD::

    muse meter

Read the stored time signature for a specific commit::

    muse meter <commit-sha>

Set the time signature on HEAD::

    muse meter --set 7/8

Auto-detect from MIDI files in the current working tree::

    muse meter --detect

Show meter annotations across the commit history::

    muse meter --history

Detect tracks with conflicting (polyrhythmic) time signatures::

    muse meter --polyrhythm

Time Signature Format
---------------------
``<numerator>/<denominator>`` where denominator is a power of 2 (1, 2, 4, 8, 16, …).
Examples: ``4/4``, ``3/4``, ``7/8``, ``5/4``, ``12/8``, ``6/8``.

Storage
-------
The time signature is stored as the ``meter`` key inside the
``extra_metadata`` JSON blob on the ``muse_cli_commits`` row.  No new
columns are added; the blob is extensible for future annotations (tempo,
key, etc.).

MIDI Detection
--------------
``--detect`` scans ``.mid`` / ``.midi`` files in ``muse-work/`` for MIDI
time-signature meta events (``0xFF 0x58``).  The first event found across
all files wins.  If no event is present the time signature is reported as
unknown (``?``).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import pathlib
import re

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    get_commit_extra_metadata,
    open_session,
    set_commit_extra_metadata_key,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

# ──────────────────────────────────────────────────────────────────────────────
# Domain types  (registered in docs/reference/type_contracts.md)
# ──────────────────────────────────────────────────────────────────────────────

_METADATA_KEY = "meter"

_TIME_SIG_RE = re.compile(r"^(\d+)/(\d+)$")


@dataclasses.dataclass(frozen=True)
class MuseMeterReadResult:
    """Result of reading a time-signature annotation from a single commit.

    Attributes:
        commit_id:      Full 64-char sha256 commit identifier.
        time_signature: Time signature string (e.g. ``"4/4"``), or
                        ``None`` when no annotation is stored.
    """

    commit_id: str
    time_signature: str | None


@dataclasses.dataclass(frozen=True)
class MuseMeterHistoryEntry:
    """A single entry in the per-commit meter history.

    Attributes:
        commit_id:      Full 64-char sha256 commit identifier.
        time_signature: Stored time signature, or ``None`` if not annotated.
        message:        Commit message.
    """

    commit_id: str
    time_signature: str | None
    message: str


@dataclasses.dataclass(frozen=True)
class MusePolyrhythmResult:
    """Result of polyrhythm detection across MIDI files in the working tree.

    Attributes:
        commit_id:        Commit that was inspected (HEAD by default).
        signatures_by_file: Mapping of relative file path to detected time
                            signature string (``"?"`` if undetectable).
        is_polyrhythmic:  ``True`` when two or more distinct, known time
                          signatures are present simultaneously.
    """

    commit_id: str
    signatures_by_file: dict[str, str]
    is_polyrhythmic: bool


# ──────────────────────────────────────────────────────────────────────────────
# Time-signature validation
# ──────────────────────────────────────────────────────────────────────────────


def validate_time_signature(raw: str) -> str:
    """Parse and validate a time signature string like ``"4/4"`` or ``"7/8"``.

    The denominator must be a power of two (1 through 128).  Returns the
    canonical string (stripped) on success; raises ``ValueError`` on failure.
    """
    raw = raw.strip()
    m = _TIME_SIG_RE.match(raw)
    if not m:
        raise ValueError(
            f"Invalid time signature {raw!r}. "
            "Expected <numerator>/<denominator>, e.g. '4/4' or '7/8'."
        )
    numerator = int(m.group(1))
    denominator = int(m.group(2))
    if numerator < 1:
        raise ValueError(f"Numerator must be ≥ 1, got {numerator}.")
    if denominator < 1 or (denominator & (denominator - 1)) != 0:
        raise ValueError(
            f"Denominator must be a power of 2 (1, 2, 4, 8, 16, …), got {denominator}."
        )
    return raw


# ──────────────────────────────────────────────────────────────────────────────
# MIDI time-signature detection
# ──────────────────────────────────────────────────────────────────────────────


def detect_midi_time_signature(midi_bytes: bytes) -> str | None:
    """Scan raw MIDI bytes for the first time-signature meta event (0xFF 0x58).

    Returns a ``"numerator/denominator"`` string or ``None`` when no event
    is found.

    MIDI time-signature meta event layout (after the variable-length delta):
        0xFF  — meta event marker
        0x58  — time signature type
        0x04  — data length (always 4)
        nn    — numerator
        dd    — denominator exponent (denominator = 2^dd)
        cc    — MIDI clocks per metronome tick
        bb    — number of 32nd notes per 24 MIDI clocks
    """
    i = 0
    n = len(midi_bytes)
    # Skip the 14-byte MIDI file header (MThd chunk) if present.
    if midi_bytes[:4] == b"MThd":
        i = 14  # MThd + 4 (length) + 6 (header data) + first MTrk lead

    while i < n - 5:
        if midi_bytes[i] == 0xFF and midi_bytes[i + 1] == 0x58:
            length_byte = midi_bytes[i + 2]
            if length_byte == 4 and i + 6 < n:
                numerator = midi_bytes[i + 3]
                denominator_exp = midi_bytes[i + 4]
                denominator = 2**denominator_exp
                if numerator >= 1 and denominator >= 1:
                    return f"{numerator}/{denominator}"
        i += 1
    return None


def scan_workdir_for_time_signatures(
    workdir: pathlib.Path,
) -> dict[str, str]:
    """Scan all MIDI files under *workdir* for time-signature meta events.

    Returns a dict mapping each MIDI file's path (relative to *workdir*)
    to its detected time signature, or ``"?"`` when none is found.
    Only ``.mid`` and ``.midi`` extensions are scanned.
    """
    results: dict[str, str] = {}
    if not workdir.exists():
        return results
    for midi_path in sorted(workdir.rglob("*.mid")) + sorted(workdir.rglob("*.midi")):
        try:
            midi_bytes = midi_path.read_bytes()
        except OSError:
            continue
        sig = detect_midi_time_signature(midi_bytes)
        rel = midi_path.relative_to(workdir).as_posix()
        results[rel] = sig if sig is not None else "?"
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Repo HEAD resolution
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_head_commit_id(root: pathlib.Path) -> str | None:
    """Return the HEAD commit ID from the ``.muse/`` ref files, or ``None``."""
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)
    if not ref_path.exists():
        return None
    raw = ref_path.read_text().strip()
    return raw or None


# ──────────────────────────────────────────────────────────────────────────────
# Async core functions (fully injectable for tests)
# ──────────────────────────────────────────────────────────────────────────────


async def _resolve_commit_id(
    session: AsyncSession,
    root: pathlib.Path,
    commit_ref: str | None,
) -> str:
    """Resolve *commit_ref* to a full 64-char commit ID.

    If *commit_ref* is ``None`` or ``"HEAD"``, resolves from the branch ref.
    Otherwise treats *commit_ref* as a (possibly abbreviated) commit ID and
    fetches the full ID from the DB.

    Raises ``typer.Exit(USER_ERROR)`` when the ref cannot be resolved.
    """
    if commit_ref is None or commit_ref.upper() == "HEAD":
        cid = _resolve_head_commit_id(root)
        if cid is None:
            typer.echo("❌ No commits on this branch yet.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return cid

    # Abbreviated or full commit ID — look up in DB.
    if len(commit_ref) == 64:
        row = await session.get(MuseCliCommit, commit_ref)
        if row is None:
            typer.echo(f"❌ Commit {commit_ref[:8]} not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return row.commit_id

    # Prefix search for abbreviated IDs.
    from sqlalchemy.future import select

    result = await session.execute(
        select(MuseCliCommit.commit_id).where(
            MuseCliCommit.commit_id.startswith(commit_ref)
        )
    )
    rows = result.scalars().all()
    if not rows:
        typer.echo(f"❌ Commit {commit_ref!r} not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if len(rows) > 1:
        typer.echo(
            f"❌ Ambiguous commit prefix {commit_ref!r} — matches {len(rows)} commits."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)
    return rows[0]


async def _meter_read_async(
    *,
    session: AsyncSession,
    root: pathlib.Path,
    commit_ref: str | None,
) -> MuseMeterReadResult:
    """Read the stored time signature for a commit.

    Returns a :class:`MuseMeterReadResult`.  Does not write to the DB.
    """
    commit_id = await _resolve_commit_id(session, root, commit_ref)
    metadata = await get_commit_extra_metadata(session, commit_id)
    time_sig: str | None = None
    if metadata:
        raw = metadata.get(_METADATA_KEY)
        if isinstance(raw, str):
            time_sig = raw
    return MuseMeterReadResult(commit_id=commit_id, time_signature=time_sig)


async def _meter_set_async(
    *,
    session: AsyncSession,
    root: pathlib.Path,
    commit_ref: str | None,
    time_signature: str,
) -> str:
    """Store *time_signature* as the meter annotation on *commit_ref*.

    Returns the full commit ID on success.  Raises ``typer.Exit`` on error.
    """
    commit_id = await _resolve_commit_id(session, root, commit_ref)
    ok = await set_commit_extra_metadata_key(
        session, commit_id=commit_id, key=_METADATA_KEY, value=time_signature
    )
    if not ok:
        typer.echo(f"❌ Failed to update commit {commit_id[:8]}.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    return commit_id


async def _meter_history_async(
    *,
    session: AsyncSession,
    root: pathlib.Path,
) -> list[MuseMeterHistoryEntry]:
    """Walk the commit chain from HEAD and collect meter annotations.

    Returns a list of :class:`MuseMeterHistoryEntry` newest-first.
    """
    head_id = _resolve_head_commit_id(root)
    if head_id is None:
        return []

    entries: list[MuseMeterHistoryEntry] = []
    current_id: str | None = head_id
    while current_id:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            break
        metadata = commit.extra_metadata or {}
        raw = metadata.get(_METADATA_KEY) if isinstance(metadata, dict) else None
        time_sig: str | None = raw if isinstance(raw, str) else None
        entries.append(
            MuseMeterHistoryEntry(
                commit_id=commit.commit_id,
                time_signature=time_sig,
                message=commit.message,
            )
        )
        current_id = commit.parent_commit_id
    return entries


async def _meter_polyrhythm_async(
    *,
    session: AsyncSession,
    root: pathlib.Path,
    commit_ref: str | None,
) -> MusePolyrhythmResult:
    """Detect polyrhythm by scanning MIDI files in the current working tree.

    Because Muse CLI stores content hashes (not raw bytes) in snapshots, live
    MIDI scanning is performed against the files currently in ``muse-work/``.
    The *commit_ref* is used only to record which commit the result pertains to.
    """
    commit_id = await _resolve_commit_id(session, root, commit_ref)
    workdir = root / "muse-work"
    sigs = scan_workdir_for_time_signatures(workdir)
    known = {s for s in sigs.values() if s != "?"}
    is_poly = len(known) > 1
    return MusePolyrhythmResult(
        commit_id=commit_id,
        signatures_by_file=sigs,
        is_polyrhythmic=is_poly,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Output renderers
# ──────────────────────────────────────────────────────────────────────────────


def _render_read(result: MuseMeterReadResult) -> None:
    """Print time signature for a single commit."""
    sig = result.time_signature or "(not set)"
    typer.echo(f"commit {result.commit_id[:8]}")
    typer.echo(f"meter  {sig}")


class _UnsetType:
    """Sentinel type for 'not yet seen' in history rendering."""


_UNSET = _UnsetType()


def _render_history(entries: list[MuseMeterHistoryEntry]) -> None:
    """Print meter history newest-first, highlighting changes."""
    if not entries:
        typer.echo("No commits on this branch yet.")
        return
    prev_sig: str | _UnsetType = _UNSET
    for entry in entries:
        sig = entry.time_signature or "(not set)"
        changed = sig != prev_sig
        marker = " ← changed" if changed and prev_sig is not _UNSET else ""
        typer.echo(f"{entry.commit_id[:8]}  {sig:<12}  {entry.message[:50]}{marker}")
        prev_sig = sig


def _render_polyrhythm(result: MusePolyrhythmResult) -> None:
    """Print polyrhythm detection results."""
    if not result.signatures_by_file:
        typer.echo("No MIDI files found in muse-work/.")
        return
    if result.is_polyrhythmic:
        typer.echo(
            "⚠️  Polyrhythm detected — multiple time signatures in this commit:"
        )
    else:
        typer.echo("✅ No polyrhythm — all MIDI files share the same time signature.")
    typer.echo("")
    for path, sig in sorted(result.signatures_by_file.items()):
        typer.echo(f"  {sig:<12}  {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Typer command
# ──────────────────────────────────────────────────────────────────────────────


@app.callback(invoke_without_command=True)
def meter(
    ctx: typer.Context,
    commit: str | None = typer.Argument(
        None,
        help="Target commit (full or abbreviated SHA, or 'HEAD'). Defaults to HEAD.",
        metavar="COMMIT",
    ),
    set_sig: str | None = typer.Option(
        None,
        "--set",
        help="Set the time signature, e.g. '4/4' or '7/8'.",
        metavar="TIME_SIG",
    ),
    detect: bool = typer.Option(
        False,
        "--detect",
        help="Auto-detect time signature from MIDI meta events in muse-work/.",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="Show meter annotations across all commits on the current branch.",
    ),
    polyrhythm: bool = typer.Option(
        False,
        "--polyrhythm",
        help="Detect tracks with conflicting time signatures in muse-work/.",
    ),
) -> None:
    """Read or set the time signature annotation for a commit."""
    root = require_repo()

    # ── Mutual exclusion ─────────────────────────────────────────────────────
    flags_given = sum([set_sig is not None, detect, history, polyrhythm])
    if flags_given > 1:
        typer.echo(
            "❌ Only one of --set, --detect, --history, --polyrhythm may be used at a time."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── --history (no commit arg needed) ─────────────────────────────────────
    if history:

        async def _run_history() -> None:
            async with open_session() as session:
                entries = await _meter_history_async(session=session, root=root)
            _render_history(entries)

        try:
            asyncio.run(_run_history())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse meter --history failed: {exc}")
            logger.error("❌ muse meter history error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # ── --polyrhythm ─────────────────────────────────────────────────────────
    if polyrhythm:

        async def _run_polyrhythm() -> None:
            async with open_session() as session:
                result = await _meter_polyrhythm_async(
                    session=session, root=root, commit_ref=commit
                )
            _render_polyrhythm(result)

        try:
            asyncio.run(_run_polyrhythm())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse meter --polyrhythm failed: {exc}")
            logger.error("❌ muse meter polyrhythm error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # ── --set <time-sig> ─────────────────────────────────────────────────────
    if set_sig is not None:
        try:
            canonical = validate_time_signature(set_sig)
        except ValueError as exc:
            typer.echo(f"❌ {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        async def _run_set() -> None:
            async with open_session() as session:
                commit_id = await _meter_set_async(
                    session=session,
                    root=root,
                    commit_ref=commit,
                    time_signature=canonical,
                )
            typer.echo(f"✅ Set meter={canonical!r} on commit {commit_id[:8]}")

        try:
            asyncio.run(_run_set())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse meter --set failed: {exc}")
            logger.error("❌ muse meter set error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # ── --detect ─────────────────────────────────────────────────────────────
    if detect:
        workdir = root / "muse-work"
        sigs = scan_workdir_for_time_signatures(workdir)
        if not sigs:
            typer.echo("⚠️ No MIDI files found in muse-work/.")
            raise typer.Exit(code=ExitCode.SUCCESS)

        # Find the most common known signature.
        known = [s for s in sigs.values() if s != "?"]
        detected: str | None = None
        if known:
            from collections import Counter
            detected = Counter(known).most_common(1)[0][0]
            typer.echo(f"✅ Detected time signature: {detected}")
        else:
            typer.echo("⚠️ No MIDI time-signature meta events found in muse-work/ files.")
            raise typer.Exit(code=ExitCode.SUCCESS)

        # Auto-store the detected value on the target commit.
        async def _run_detect() -> None:
            async with open_session() as session:
                assert detected is not None
                commit_id = await _meter_set_async(
                    session=session,
                    root=root,
                    commit_ref=commit,
                    time_signature=detected,
                )
            typer.echo(f"✅ Stored meter={detected!r} on commit {commit_id[:8]}")

        try:
            asyncio.run(_run_detect())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse meter --detect failed: {exc}")
            logger.error("❌ muse meter detect error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # ── Default: read ─────────────────────────────────────────────────────────
    async def _run_read() -> None:
        async with open_session() as session:
            result = await _meter_read_async(
                session=session, root=root, commit_ref=commit
            )
        _render_read(result)

    try:
        asyncio.run(_run_read())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse meter failed: {exc}")
        logger.error("❌ muse meter error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
