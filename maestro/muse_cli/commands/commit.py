"""muse commit — filesystem snapshot commit with deterministic object IDs.

Algorithm
---------
1. Resolve repo root via ``require_repo()``.
2. Read ``repo_id`` from ``.muse/repo.json`` and current branch from
   ``.muse/HEAD``.
3. Walk ``muse-work/`` — hash each file with ``sha256(file_bytes)`` to
   produce an ``object_id``.
4. Build snapshot manifest: ``{rel_path → object_id}``.
5. Compute ``snapshot_id = sha256(sorted(path:object_id pairs))``.
6. If the current branch HEAD already points to a commit with the same
   ``snapshot_id``, print "Nothing to commit, working tree clean" and
   exit 0 (unless ``--allow-empty`` is set).
7. Compute ``commit_id = sha256(sorted(parent_ids) | snapshot_id | message | timestamp)``.
8. Persist to Postgres: upsert ``object`` rows → upsert ``snapshot`` row → insert ``commit`` row.
9. Update ``.muse/refs/heads/<branch>`` to the new ``commit_id``.

Music-domain flags
------------------
``--section TEXT``
    Tag the commit as belonging to a musical section (e.g. ``verse``,
    ``chorus``, ``bridge``).  Stored in ``commit_metadata["section"]``.

``--track TEXT``
    Tag the commit as affecting a specific instrument track (e.g. ``drums``,
    ``bass``, ``keys``).  Stored in ``commit_metadata["track"]``.

``--emotion TEXT``
    Attach an emotion vector label to the commit (e.g. ``joyful``,
    ``melancholic``, ``tense``).  Stored in ``commit_metadata["emotion"]``.
    Foundation for future ``muse log --emotion melancholic`` queries.

``--co-author TEXT``
    Add a ``Co-authored-by: Name <email>`` trailer to the commit message
    (for collaborative sessions).

``--allow-empty``
    Allow committing even when the working tree has not changed since HEAD.
    Useful for milestone markers and metadata-only annotations.

``--amend``
    Fold working-tree changes into the most recent commit, equivalent to
    running ``muse amend``.  Music-domain flags apply to the amended commit.

``--no-verify``
    Bypass pre-commit hooks.  Accepted for forward-compatibility; currently
    a no-op because the hook system has not been implemented yet.

``--from-batch <path>``
-----------------------
When this flag is provided, the commit pipeline reads ``muse-batch.json``
and restricts the snapshot to only the files listed in the manifest's
``files`` array.  The ``commit_message_suggestion`` from the batch is used
as the commit message, making this a fast path for::

    muse commit --from-batch muse-batch.json

without needing to specify ``-m``.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    get_head_snapshot_id,
    insert_commit,
    open_session,
    upsert_object,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.object_store import write_object_from_path
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
    hash_file,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Batch manifest helpers
# ---------------------------------------------------------------------------


def load_muse_batch(batch_path: pathlib.Path) -> dict[str, object]:
    """Read and validate a muse-batch.json file.

    Returns the parsed dict.  Raises ``typer.Exit`` with ``USER_ERROR`` if
    the file is missing or malformed so the Typer callback surfaces a clean
    message.
    """
    if not batch_path.exists():
        typer.echo(f"❌ muse-batch.json not found: {batch_path}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    try:
        data: dict[str, object] = json.loads(batch_path.read_text())
    except json.JSONDecodeError as exc:
        typer.echo(f"❌ Invalid JSON in {batch_path}: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    return data


def build_snapshot_manifest_from_batch(
    batch_data: dict[str, object],
    repo_root: pathlib.Path,
) -> dict[str, str]:
    """Build a snapshot manifest restricted to files listed in a muse-batch.

    Only files that actually exist on disk are included — missing files are
    silently skipped (the batch may reference files from a different machine
    or a partial run).

    ``batch_data["files"]`` entries use paths relative to the repo root
    (e.g. ``"muse-work/tracks/drums/jazz_4b_abc.mid"``).  The returned
    manifest uses paths relative to ``muse-work/`` so it is compatible with
    ``build_snapshot_manifest``.

    Returns a ``{rel_path: object_id}`` dict where *rel_path* is relative to
    ``muse-work/``.
    """
    workdir = repo_root / "muse-work"
    raw_files = batch_data.get("files", [])
    files: list[dict[str, object]] = list(raw_files) if isinstance(raw_files, list) else []
    manifest: dict[str, str] = {}

    for entry in files:
        raw_path = str(entry.get("path", ""))
        # Paths in the batch are relative to repo root, e.g. muse-work/tracks/…
        abs_path = repo_root / raw_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        # Key in the manifest is relative to muse-work/
        try:
            rel = abs_path.relative_to(workdir).as_posix()
        except ValueError:
            # File is outside muse-work/ — skip
            continue
        manifest[rel] = hash_file(abs_path)

    return manifest


# ---------------------------------------------------------------------------
# Music metadata helpers
# ---------------------------------------------------------------------------


def _append_co_author(message: str, co_author: str) -> str:
    """Append a Co-authored-by trailer to *message*.

    Follows the Git convention: a blank line separates the message body from
    trailers.  Multiple calls are safe — each appends a new line.
    """
    trailer = f"Co-authored-by: {co_author}"
    return f"{message}\n\n{trailer}" if message else trailer


async def _apply_commit_music_metadata(
    *,
    session: AsyncSession,
    commit_id: str,
    section: str | None,
    track: str | None,
    emotion: str | None,
) -> None:
    """Merge music-domain flags into commit_metadata for *commit_id*.

    Preserves existing metadata keys (e.g. ``tempo_bpm`` written by
    ``muse tempo --set``) — only the supplied non-None keys are overwritten.
    Skips silently when no music flags were provided.
    """
    if not any([section, track, emotion]):
        return

    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        logger.warning("⚠️ Commit %s not found for metadata update", commit_id[:8])
        return

    metadata: dict[str, object] = dict(commit.commit_metadata or {})
    if section is not None:
        metadata["section"] = section
    if track is not None:
        metadata["track"] = track
    if emotion is not None:
        metadata["emotion"] = emotion

    commit.commit_metadata = metadata
    flag_modified(commit, "commit_metadata")
    session.add(commit)
    logger.debug(
        "✅ Applied music metadata to %s: section=%r track=%r emotion=%r",
        commit_id[:8],
        section,
        track,
        emotion,
    )


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _commit_async(
    *,
    message: str,
    root: pathlib.Path,
    session: AsyncSession,
    batch_path: pathlib.Path | None = None,
    section: str | None = None,
    track: str | None = None,
    emotion: str | None = None,
    co_author: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Run the commit pipeline and return the new ``commit_id``.

    All filesystem and DB side-effects are isolated in this coroutine so
    tests can inject an in-memory SQLite session and a ``tmp_path`` root
    without touching a real database.

    When *batch_path* is provided the commit is restricted to files listed in
    ``muse-batch.json`` and the ``commit_message_suggestion`` from the batch
    overrides *message*.

    Music-domain flags:
    - *section* / *track* / *emotion* — stored in ``commit_metadata``.
    - *co_author* — appended to the commit message as a Co-authored-by trailer.
    - *allow_empty* — when ``True``, bypasses the "nothing to commit" guard
      so callers can record milestone commits or metadata-only annotations.

    Raises ``typer.Exit`` with the appropriate exit code on user errors so
    the Typer callback surfaces a clean message rather than a traceback.
    """
    muse_dir = root / ".muse"

    # ── Guard: block commit while a conflicted merge is in progress ──────
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo(
            "❌ You have unresolved merge conflicts.\n"
            "   Fix conflicts in the listed files, then run 'muse commit'."
        )
        for path in sorted(merge_state.conflict_paths):
            typer.echo(f"\tboth modified:   {path}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]                 # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    parent_commit_id: str | None = None
    if ref_path.exists():
        raw = ref_path.read_text().strip()
        if raw:
            parent_commit_id = raw

    parent_ids = [parent_commit_id] if parent_commit_id else []

    # ── Build snapshot manifest ──────────────────────────────────────────
    workdir = root / "muse-work"

    if batch_path is not None:
        # Fast path: restrict snapshot to files listed in muse-batch.json
        batch_data = load_muse_batch(batch_path)
        suggestion = str(batch_data.get("commit_message_suggestion", "")).strip()
        if suggestion:
            message = suggestion
        manifest = build_snapshot_manifest_from_batch(batch_data, root)
        if not manifest:
            typer.echo(
                "⚠️  No files from muse-batch.json found on disk — nothing to commit.\n"
                f"     Batch: {batch_path}"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        # Standard path: walk the entire muse-work/ directory
        if not workdir.exists():
            typer.echo(
                "⚠️  No muse-work/ directory found. Generate some artifacts first.\n"
                "     Tip: run the Maestro stress test to populate muse-work/."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        manifest = build_snapshot_manifest(workdir)
        if not manifest:
            typer.echo("⚠️  muse-work/ is empty — nothing to commit.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    # ── Nothing-to-commit guard (bypassable via --allow-empty) ───────────
    if not allow_empty:
        last_snapshot_id = await get_head_snapshot_id(session, repo_id, branch)
        if last_snapshot_id == snapshot_id:
            typer.echo("Nothing to commit, working tree clean")
            raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Apply Co-authored-by trailer ─────────────────────────────────────
    if co_author:
        message = _append_co_author(message, co_author)

    # ── Deterministic commit ID ──────────────────────────────────────────
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )

    # ── Build music metadata dict ─────────────────────────────────────────
    commit_metadata: dict[str, object] | None = None
    music_keys = {k: v for k, v in [("section", section), ("track", track), ("emotion", emotion)] if v is not None}
    if music_keys:
        commit_metadata = dict(music_keys)

    # ── Persist objects ──────────────────────────────────────────────────
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)
        # Write the file into the local content-addressed store so that
        # ``muse read-tree`` and ``muse reset --hard`` can reconstruct
        # muse-work/ from any historical snapshot.  Path-based copy avoids
        # loading large blobs (audio previews, dense MIDI renders) into memory.
        write_object_from_path(root, object_id, file_path)

    # ── Persist snapshot ─────────────────────────────────────────────────
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)
    # Flush now so the snapshot row exists in the DB transaction before the
    # commit row's FK constraint is checked on insert.
    await session.flush()

    # ── Persist commit ───────────────────────────────────────────────────
    new_commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_commit_id,
        snapshot_id=snapshot_id,
        message=message,
        author="",
        committed_at=committed_at,
        commit_metadata=commit_metadata,
    )
    await insert_commit(session, new_commit)

    # ── Update branch HEAD pointer ────────────────────────────────────────
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id)

    typer.echo(f"✅ [{branch} {commit_id[:8]}] {message}")
    logger.info("✅ muse commit %s on %r: %s", commit_id[:8], branch, message)
    return commit_id


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def commit(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(
        None, "-m", "--message", help="Commit message."
    ),
    from_batch: Optional[str] = typer.Option(
        None,
        "--from-batch",
        help=(
            "Path to muse-batch.json produced by the stress test.  "
            "Uses commit_message_suggestion from the batch and snapshots only "
            "the files listed in files[].  Overrides -m when present."
        ),
    ),
    amend: bool = typer.Option(
        False,
        "--amend",
        help=(
            "Fold working-tree changes into the most recent commit.  "
            "Equivalent to running 'muse amend'.  Music-domain flags "
            "(--section, --track, --emotion, --co-author) apply to the "
            "amended commit."
        ),
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help=(
            "Bypass pre-commit hooks.  Currently a no-op — accepted for "
            "forward-compatibility with the planned hook system."
        ),
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help=(
            "Tag this commit as belonging to a musical section "
            "(e.g. verse, chorus, bridge).  Stored in commit_metadata and "
            "queryable via 'muse log --section <value>'."
        ),
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help=(
            "Tag this commit as affecting a specific instrument track "
            "(e.g. drums, bass, keys).  Stored in commit_metadata and "
            "queryable via 'muse log --track <value>'."
        ),
    ),
    emotion: Optional[str] = typer.Option(
        None,
        "--emotion",
        help=(
            "Attach an emotion vector label to this commit "
            "(e.g. joyful, melancholic, tense).  Foundation for future "
            "'muse log --emotion melancholic' queries."
        ),
    ),
    co_author: Optional[str] = typer.Option(
        None,
        "--co-author",
        help=(
            "Add a Co-authored-by trailer to the commit message.  "
            "Use 'Name <email>' format for Git-compatible attribution."
        ),
    ),
    allow_empty: bool = typer.Option(
        False,
        "--allow-empty",
        help=(
            "Allow committing even when the working tree has not changed "
            "since HEAD.  Useful for milestone markers or metadata-only "
            "annotations (e.g. 'muse commit --allow-empty --emotion joyful')."
        ),
    ),
) -> None:
    """Record the current muse-work/ state as a new version in history."""
    if no_verify:
        logger.debug("⚠️ --no-verify supplied; hook system not yet implemented — proceeding")

    root = require_repo()

    if amend:
        # Delegate to the amend pipeline then apply music metadata.
        from maestro.muse_cli.commands.amend import _amend_async

        # Determine if co_author needs to be merged into the amend message.
        # _amend_async resolves the effective message internally from HEAD or -m;
        # we pre-compute the co_author trailer here so we can pass a fully-formed
        # message string regardless of the no-edit path.
        async def _run_amend() -> None:
            async with open_session() as session:
                # Resolve effective message for co_author appending.
                # When -m is provided, use it directly.
                # When --amend without -m, _amend_async will use the HEAD message
                # (no_edit path).  We need to read HEAD here so we can append
                # the co_author trailer before passing to _amend_async.
                effective_message = message
                if co_author:
                    if effective_message is None:
                        # Load HEAD commit message so we can append the trailer.
                        muse_dir = root / ".muse"
                        head_ref = (muse_dir / "HEAD").read_text().strip()
                        ref_path = muse_dir / pathlib.Path(head_ref)
                        if ref_path.exists():
                            head_commit_id = ref_path.read_text().strip()
                            if head_commit_id:
                                head_commit = await session.get(MuseCliCommit, head_commit_id)
                                if head_commit:
                                    effective_message = head_commit.message
                    effective_message = _append_co_author(effective_message or "", co_author)

                # When we've computed a final message, pass no_edit=False so
                # _amend_async uses it verbatim.  Otherwise let _amend_async
                # fall through to its own no_edit logic.
                use_no_edit = effective_message is None
                commit_id = await _amend_async(
                    message=effective_message,
                    no_edit=use_no_edit,
                    reset_author=False,
                    root=root,
                    session=session,
                )

                # Apply music-domain metadata after the amend.
                await _apply_commit_music_metadata(
                    session=session,
                    commit_id=commit_id,
                    section=section,
                    track=track,
                    emotion=emotion,
                )

        try:
            asyncio.run(_run_amend())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse commit --amend failed: {exc}")
            logger.error("❌ muse commit --amend error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # ── Standard (non-amend) path ─────────────────────────────────────────
    # Validate that at least one of -m or --from-batch is provided.
    if from_batch is None and message is None and not allow_empty:
        typer.echo("❌ Provide either -m MESSAGE or --from-batch PATH.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    batch_path = pathlib.Path(from_batch) if from_batch is not None else None

    # message may be None when --from-batch is used; _commit_async will
    # replace it with commit_message_suggestion from the batch.
    effective_message = message or ""

    async def _run() -> None:
        async with open_session() as session:
            await _commit_async(
                message=effective_message,
                root=root,
                session=session,
                batch_path=batch_path,
                section=section,
                track=track,
                emotion=emotion,
                co_author=co_author,
                allow_empty=allow_empty,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse commit failed: {exc}")
        logger.error("❌ muse commit error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
