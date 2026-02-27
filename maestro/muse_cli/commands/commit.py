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
   exit 0.
7. Compute ``commit_id = sha256(sorted(parent_ids) | snapshot_id | message | timestamp)``.
8. Persist to Postgres: upsert ``object`` rows → upsert ``snapshot`` row → insert ``commit`` row.
9. Update ``.muse/refs/heads/<branch>`` to the new ``commit_id``.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    get_head_snapshot_id,
    insert_commit,
    open_session,
    upsert_object,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _commit_async(
    *,
    message: str,
    root: pathlib.Path,
    session: AsyncSession,
) -> str:
    """Run the commit pipeline and return the new ``commit_id``.

    All filesystem and DB side-effects are isolated in this coroutine so
    tests can inject an in-memory SQLite session and a ``tmp_path`` root
    without touching a real database.

    Raises ``typer.Exit`` with the appropriate exit code on user errors so
    the Typer callback surfaces a clean message rather than a traceback.
    """
    muse_dir = root / ".muse"

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

    # ── Walk working directory ───────────────────────────────────────────
    workdir = root / "muse-work"
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

    # ── Nothing-to-commit guard ──────────────────────────────────────────
    last_snapshot_id = await get_head_snapshot_id(session, repo_id, branch)
    if last_snapshot_id == snapshot_id:
        typer.echo("Nothing to commit, working tree clean")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Deterministic commit ID ──────────────────────────────────────────
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )

    # ── Persist objects ──────────────────────────────────────────────────
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)

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
    message: str = typer.Option(..., "-m", "--message", help="Commit message."),
) -> None:
    """Record the current muse-work/ state as a new version in history."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _commit_async(message=message, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse commit failed: {exc}")
        logger.error("❌ muse commit error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

