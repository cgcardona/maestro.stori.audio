"""muse bisect — binary search for the commit that introduced a regression.

Music-domain analogue of ``git bisect``.  Given a known-good and a known-bad
commit on the history of a Muse repository, this command binary-searches the
ancestry path to identify the exact commit that first introduced a rhythmic
drift, mix regression, or other quality regression.

Subcommands
-----------
``muse bisect start``
    Begin a bisect session.  Records the pre-bisect HEAD ref in
    ``.muse/BISECT_STATE.json`` so ``reset`` can restore it.  Blocked if a
    merge is in progress (``.muse/MERGE_STATE.json`` exists).

``muse bisect good <commit>``
    Mark *commit* as known-good.  If both good and bad are set, checks out
    the midpoint commit into muse-work/ and reports how many steps remain.

``muse bisect bad <commit>``
    Mark *commit* as known-bad.  Same auto-advance logic as ``good``.

``muse bisect run <cmd>``
    Automate the bisect loop.  Runs *cmd* in a shell after each checkout;
    exit 0 → good, exit 1 (or non-zero) → bad.  Stops when the culprit is
    identified.

``muse bisect reset``
    End the session: restore ``.muse/HEAD`` and muse-work/ to the
    pre-bisect state, then remove BISECT_STATE.json.

``muse bisect log``
    Print the bisect log (what has been tested and with what verdict).

Session state
-------------
Persisted in ``.muse/BISECT_STATE.json`` so the session survives across shell
invocations.  ``muse bisect start`` blocks if the file already exists.

Exit codes
----------
0  — success (or culprit identified)
1  — user error (bad args, session already active, commit not found)
2  — not a Muse repository
3  — internal error
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import pathlib
import shlex
import subprocess

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.object_store import read_object
from maestro.services.muse_bisect import (
    BisectState,
    BisectStepResult,
    advance_bisect,
    clear_bisect_state,
    get_commits_between,
    pick_midpoint,
    read_bisect_state,
    write_bisect_state,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Binary search for the commit that introduced a regression.")

# Minimum abbreviated commit SHA length accepted as user input.
_MIN_SHA_PREFIX = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_commit_id(root: pathlib.Path, ref: str) -> str:
    """Resolve *ref* to a full commit ID from filesystem refs.

    Accepts:
    - ``"HEAD"`` — reads ``.muse/HEAD`` → resolves the symbolic ref.
    - A branch name — reads ``.muse/refs/heads/<branch>``.
    - An abbreviated or full commit SHA — returned as-is (validated later).

    Args:
        root: Repository root.
        ref:  Commit reference string from the user.

    Returns:
        The commit ID string (may be an abbreviation; DB validates).
    """
    muse_dir = root / ".muse"

    if ref.upper() == "HEAD":
        head_content = (muse_dir / "HEAD").read_text().strip()
        if head_content.startswith("refs/"):
            ref_path = muse_dir / pathlib.Path(head_content)
            return ref_path.read_text().strip() if ref_path.exists() else head_content
        return head_content

    # Try branch name first.
    branch_path = muse_dir / "refs" / "heads" / ref
    if branch_path.exists():
        return branch_path.read_text().strip()

    # Assume it's a commit SHA.
    return ref


async def _checkout_snapshot_into_workdir(
    session: AsyncSession,
    root: pathlib.Path,
    commit_id: str,
) -> int:
    """Hydrate muse-work/ from the snapshot attached to *commit_id*.

    Reads the snapshot manifest from the DB, then writes each object from
    ``.muse/objects/`` into muse-work/ (resetting the directory first).

    Returns the number of files written (0 if the snapshot is empty).

    Args:
        session:   Open async DB session.
        root:      Repository root.
        commit_id: Target commit whose snapshot to check out.
    """
    from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

    commit: MuseCliCommit | None = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        typer.echo(f"❌ Commit {commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot: MuseCliSnapshot | None = await session.get(MuseCliSnapshot, commit.snapshot_id)
    if snapshot is None:
        typer.echo(
            f"❌ Snapshot {commit.snapshot_id[:8]} for commit {commit_id[:8]} "
            "not found in database."
        )
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    manifest: dict[str, str] = dict(snapshot.manifest)

    workdir = root / "muse-work"

    # Clear muse-work/ before populating.
    if workdir.exists():
        for existing_file in sorted(workdir.rglob("*")):
            if existing_file.is_file():
                existing_file.unlink()
        for d in sorted(workdir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass

    workdir.mkdir(parents=True, exist_ok=True)

    files_written = 0
    for rel_path, object_id in sorted(manifest.items()):
        content = read_object(root, object_id)
        if content is None:
            logger.warning("⚠️  Object %s missing from local store; skipping", object_id[:8])
            continue
        dest = workdir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        files_written += 1

    return files_written


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@app.command("start")
def bisect_start() -> None:
    """Begin a bisect session from the current HEAD.

    Records the pre-bisect HEAD ref and commit ID in BISECT_STATE.json.
    Fails if a bisect or merge is already in progress.
    """
    root = require_repo()
    muse_dir = root / ".muse"

    # Guard: block if merge in progress.
    merge_state_path = muse_dir / "MERGE_STATE.json"
    if merge_state_path.exists():
        typer.echo("❌ Merge in progress. Resolve it before starting bisect.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Guard: block if bisect already active.
    existing = read_bisect_state(root)
    if existing is not None:
        typer.echo(
            "❌ Bisect already in progress.\n"
            "   Run 'muse bisect reset' to end the current session first."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Capture current HEAD.
    head_ref = (muse_dir / "HEAD").read_text().strip()  # e.g. "refs/heads/main"
    pre_bisect_commit = ""
    if head_ref.startswith("refs/"):
        ref_path = muse_dir / pathlib.Path(head_ref)
        if ref_path.exists():
            pre_bisect_commit = ref_path.read_text().strip()
    else:
        pre_bisect_commit = head_ref  # detached HEAD — store the commit ID directly

    state = BisectState(
        good=None,
        bad=None,
        current=None,
        tested={},
        pre_bisect_ref=head_ref,
        pre_bisect_commit=pre_bisect_commit,
    )
    write_bisect_state(root, state)

    typer.echo(
        "✅ Bisect session started.\n"
        "   Now mark a good commit:  muse bisect good <commit>\n"
        "   And a bad commit:        muse bisect bad <commit>"
    )
    logger.info("✅ muse bisect start (pre_bisect_ref=%r commit=%s)", head_ref, pre_bisect_commit[:8] if pre_bisect_commit else "none")


# ---------------------------------------------------------------------------
# good / bad  (shared implementation)
# ---------------------------------------------------------------------------


def _bisect_mark(root: pathlib.Path, ref: str, verdict: str) -> None:
    """Core logic for ``muse bisect good`` and ``muse bisect bad``.

    Resolves *ref* to a commit ID, records the verdict, advances the binary
    search, and checks out the next midpoint into muse-work/.

    Args:
        root:    Repository root.
        ref:     Commit reference from the user (SHA, branch name, ``HEAD``).
        verdict: Either ``"good"`` or ``"bad"``.
    """
    state = read_bisect_state(root)
    if state is None:
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commit_id = _resolve_commit_id(root, ref)
    if len(commit_id) < _MIN_SHA_PREFIX:
        typer.echo(f"❌ Commit ref '{ref}' could not be resolved to a valid commit ID.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> BisectStepResult:
        async with open_session() as session:
            # Validate commit exists in DB (find by prefix if abbreviated).
            from maestro.muse_cli.db import find_commits_by_prefix
            from maestro.muse_cli.models import MuseCliCommit

            if len(commit_id) < 64:
                matches = await find_commits_by_prefix(session, commit_id)
                if not matches:
                    typer.echo(f"❌ No commit found matching '{commit_id[:8]}'.")
                    raise typer.Exit(code=ExitCode.USER_ERROR)
                full_id = matches[0].commit_id
            else:
                row: MuseCliCommit | None = await session.get(MuseCliCommit, commit_id)
                if row is None:
                    typer.echo(f"❌ Commit {commit_id[:8]} not found in database.")
                    raise typer.Exit(code=ExitCode.USER_ERROR)
                full_id = commit_id

            result = await advance_bisect(
                session=session,
                root=root,
                commit_id=full_id,
                verdict=verdict,
            )

            # If a next commit is identified, check it out.
            if result.next_commit is not None:
                files = await _checkout_snapshot_into_workdir(
                    session, root, result.next_commit
                )
                logger.info(
                    "✅ muse bisect: checked out %s into muse-work/ (%d files)",
                    result.next_commit[:8],
                    files,
                )

            return result

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse bisect {verdict} failed: {exc}")
        logger.error("❌ muse bisect %s error: %s", verdict, exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(result.message)

    if result.culprit is not None:
        logger.info("🎯 muse bisect culprit identified: %s", result.culprit[:8])


@app.command("good")
def bisect_good(
    commit: str = typer.Argument(
        "HEAD",
        help="Commit to mark as good. Accepts HEAD, branch name, full or abbreviated SHA.",
    ),
) -> None:
    """Mark a commit as known-good and advance the binary search."""
    root = require_repo()
    _bisect_mark(root, commit, "good")


@app.command("bad")
def bisect_bad(
    commit: str = typer.Argument(
        "HEAD",
        help="Commit to mark as bad. Accepts HEAD, branch name, full or abbreviated SHA.",
    ),
) -> None:
    """Mark a commit as known-bad and advance the binary search."""
    root = require_repo()
    _bisect_mark(root, commit, "bad")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command("run")
def bisect_run(
    cmd: str = typer.Argument(..., help="Shell command to test each midpoint commit."),
    max_steps: int = typer.Option(
        50,
        "--max-steps",
        help="Safety limit: abort after this many test iterations.",
    ),
) -> None:
    """Automate the bisect loop by running a command after each checkout.

    The command is executed in a shell.  Exit code 0 → good; any non-zero
    exit code → bad.  The loop stops when the culprit commit is identified
    or when --max-steps iterations are exhausted.

    Music example::

        muse bisect run python check_groove.py
    """
    root = require_repo()

    state = read_bisect_state(root)
    if state is None:
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if state.good is None or state.bad is None:
        typer.echo(
            "❌ Both good and bad commits must be set before running 'muse bisect run'.\n"
            "   Mark them first:  muse bisect good <commit>  /  muse bisect bad <commit>"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    steps = 0
    while steps < max_steps:
        steps += 1

        # Determine the current commit to test.
        current_state = read_bisect_state(root)
        if current_state is None:
            typer.echo("❌ Bisect session disappeared unexpectedly.")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

        if current_state.current is None:
            # First iteration: compute the initial midpoint.
            # Capture the non-None state fields before entering the nested async scope
            # so mypy can reason about them without re-checking the union.
            _init_good: str = current_state.good or ""
            _init_bad: str = current_state.bad or ""
            _init_state: BisectState = current_state

            async def _get_initial() -> BisectStepResult:
                async with open_session() as session:
                    candidates = await get_commits_between(session, _init_good, _init_bad)
                    mid = pick_midpoint(candidates)
                    if mid is None:
                        return BisectStepResult(
                            culprit=_init_bad,
                            next_commit=None,
                            remaining=0,
                            message=(
                                f"🎯 Bisect complete! First bad commit: "
                                f"{_init_bad[:8]}\n"
                                "Run 'muse bisect reset' to restore your workspace."
                            ),
                        )
                    # Record current and check it out.
                    _init_state.current = mid.commit_id
                    write_bisect_state(root, _init_state)
                    files = await _checkout_snapshot_into_workdir(session, root, mid.commit_id)
                    logger.info(
                        "✅ bisect run: checked out %s (%d files)", mid.commit_id[:8], files
                    )
                    remaining = len(candidates)
                    est_steps = math.ceil(math.log2(remaining + 1)) if remaining > 0 else 0
                    return BisectStepResult(
                        culprit=None,
                        next_commit=mid.commit_id,
                        remaining=remaining,
                        message=(
                            f"Checking {mid.commit_id[:8]} "
                            f"(~{est_steps} step(s), {remaining} in range)"
                        ),
                    )

            try:
                init_result = asyncio.run(_get_initial())
            except typer.Exit:
                raise
            except Exception as exc:
                typer.echo(f"❌ muse bisect run (init) failed: {exc}")
                logger.error("❌ bisect run init error: %s", exc, exc_info=True)
                raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

            if init_result.culprit is not None:
                typer.echo(init_result.message)
                return

            typer.echo(init_result.message)

        # Re-read state (current is now set).
        current_state = read_bisect_state(root)
        if current_state is None or current_state.current is None:
            typer.echo("❌ Could not determine current commit to test.")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

        test_commit = current_state.current
        typer.echo(f"⟳  Testing {test_commit[:8]}…")

        # Run the user's test command.
        proc = subprocess.run(cmd, shell=True, cwd=str(root))
        verdict = "good" if proc.returncode == 0 else "bad"
        typer.echo(f"   exit={proc.returncode} → {verdict}")

        # Advance the state machine.
        async def _advance(cid: str, v: str) -> BisectStepResult:
            async with open_session() as session:
                result = await advance_bisect(session=session, root=root, commit_id=cid, verdict=v)
                if result.next_commit is not None:
                    await _checkout_snapshot_into_workdir(session, root, result.next_commit)
                return result

        try:
            step_result = asyncio.run(_advance(test_commit, verdict))
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse bisect run (advance) failed: {exc}")
            logger.error("❌ bisect run advance error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

        typer.echo(step_result.message)

        if step_result.culprit is not None:
            logger.info("🎯 bisect run identified culprit: %s", step_result.culprit[:8])
            return

    typer.echo(
        f"⚠️  Safety limit reached ({max_steps} steps). "
        "Bisect session is still active; inspect manually."
    )
    raise typer.Exit(code=ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


@app.command("reset")
def bisect_reset() -> None:
    """End the bisect session and restore the pre-bisect HEAD.

    Restores ``.muse/HEAD`` to the ref it pointed at before ``muse bisect
    start`` was called, repopulates muse-work/ from that snapshot (if
    objects are available in the local store), and removes BISECT_STATE.json.
    """
    root = require_repo()

    state = read_bisect_state(root)
    if state is None:
        typer.echo("⚠️  No bisect session in progress. Nothing to reset.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    muse_dir = root / ".muse"

    # Restore HEAD.
    if state.pre_bisect_ref:
        (muse_dir / "HEAD").write_text(f"{state.pre_bisect_ref}\n")
        logger.info("✅ bisect reset: HEAD restored to %r", state.pre_bisect_ref)
    else:
        logger.warning("⚠️  pre_bisect_ref missing from BISECT_STATE.json — HEAD not restored")

    # Restore muse-work/ from pre-bisect snapshot if possible.
    if state.pre_bisect_commit:
        async def _restore() -> int:
            async with open_session() as session:
                return await _checkout_snapshot_into_workdir(
                    session, root, state.pre_bisect_commit
                )

        try:
            files = asyncio.run(_restore())
            typer.echo(f"✅ muse-work/ restored ({files} file(s)) from pre-bisect snapshot.")
        except typer.Exit:
            pass  # Commit not found — leave muse-work/ as-is; not fatal.
        except Exception as exc:
            typer.echo(f"⚠️  Could not restore muse-work/: {exc}")
            logger.warning("⚠️  bisect reset restore failed: %s", exc)
    else:
        typer.echo("⚠️  No pre-bisect commit recorded; muse-work/ not restored.")

    # Remove state file.
    clear_bisect_state(root)
    typer.echo("✅ Bisect session ended.")
    logger.info("✅ muse bisect reset complete")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command("log")
def bisect_log(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON for agent consumption.",
    ),
) -> None:
    """Show the bisect log — verdicts recorded so far and current bounds."""
    root = require_repo()

    state = read_bisect_state(root)
    if state is None:
        typer.echo("No bisect session in progress.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    if json_output:
        data: dict[str, object] = {
            "good": state.good,
            "bad": state.bad,
            "current": state.current,
            "tested": state.tested,
            "pre_bisect_ref": state.pre_bisect_ref,
            "pre_bisect_commit": state.pre_bisect_commit,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    typer.echo("Bisect session state:")
    typer.echo(f"  good:    {state.good or '(not set)'}")
    typer.echo(f"  bad:     {state.bad or '(not set)'}")
    typer.echo(f"  current: {state.current or '(not set)'}")
    typer.echo(f"  tested ({len(state.tested)} commit(s)):")
    for cid, verdict in sorted(state.tested.items()):
        typer.echo(f"    {cid[:8]}  {verdict}")
