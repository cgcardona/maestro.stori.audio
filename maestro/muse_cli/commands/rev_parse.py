"""muse rev-parse — resolve a revision expression to a commit ID.

Translates a symbolic revision expression into a concrete commit ID, mirroring
``git rev-parse`` semantics for the Muse VCS.  Designed to be used both
interactively and as a plumbing primitive that other commands can call
internally to resolve user-supplied refs.

Supported revision expressions
--------------------------------
- ``HEAD``          — current branch tip
- ``<branch>``      — tip of a named branch
- ``<commit_id>``   — full or abbreviated (prefix) commit ID
- ``HEAD~N``        — N parents back from HEAD
- ``<branch>~N``    — N parents back from branch tip

Flags
------
- ``--short``       — print only the first 8 characters of the resolved ID
- ``--verify``      — exit 1 when the expression does not resolve (default:
                      print nothing and exit 0)
- ``--abbrev-ref``  — print the branch name rather than the commit ID
                      (meaningful for HEAD and branch refs; for a raw commit ID
                      the branch of that commit is printed)

Result type: ``RevParseResult`` — see ``docs/reference/type_contracts.md``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

# Regex: "HEAD~3", "main~1", "a1b2c3~2" etc.
_TILDE_RE = re.compile(r"^(.+?)~(\d+)$")


# ---------------------------------------------------------------------------
# Named result type (registered in docs/reference/type_contracts.md)
# ---------------------------------------------------------------------------


class RevParseResult:
    """Resolved output of a revision expression.

    Returned by ``_resolve_revision`` so that callers have access to both the
    full commit ID and its branch without re-querying the database.  Treat as
    an immutable value object — all fields are set in ``__init__`` and never
    mutated.

    Fields
    ------
    commit_id : str
        Full 64-character hex commit ID.
    branch : str
        Branch that the commit lives on (may differ from the expression when a
        raw commit ID spanning multiple branches is resolved).
    revision_expr : str
        The original expression that was resolved (useful for error messages).
    """

    __slots__ = ("commit_id", "branch", "revision_expr")

    def __init__(self, commit_id: str, branch: str, revision_expr: str) -> None:
        self.commit_id = commit_id
        self.branch = branch
        self.revision_expr = revision_expr

    def __repr__(self) -> str:
        return (
            f"RevParseResult(commit_id={self.commit_id[:8]!r},"
            f" branch={self.branch!r},"
            f" revision_expr={self.revision_expr!r})"
        )


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _get_branch_tip(
    session: AsyncSession,
    repo_id: str,
    branch: str,
) -> MuseCliCommit | None:
    """Return the most-recent commit on *branch*, or ``None`` if none exist."""
    result = await session.execute(
        select(MuseCliCommit)
        .where(MuseCliCommit.repo_id == repo_id, MuseCliCommit.branch == branch)
        .order_by(MuseCliCommit.committed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _resolve_commit_by_id(
    session: AsyncSession,
    repo_id: str,
    ref: str,
) -> MuseCliCommit | None:
    """Resolve *ref* as an exact or prefix-matched commit ID.

    Tries an exact primary-key lookup first; falls back to a prefix scan
    (acceptable for CLI latency — commit tables are shallow in typical usage).
    """
    commit = await session.get(MuseCliCommit, ref)
    if commit is not None:
        return commit
    # Abbreviated prefix match
    result = await session.execute(
        select(MuseCliCommit).where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.commit_id.startswith(ref),
        )
    )
    return result.scalars().first()


async def _walk_parents(
    session: AsyncSession,
    start: MuseCliCommit,
    steps: int,
) -> MuseCliCommit | None:
    """Walk *steps* parent hops from *start*, returning the ancestor or None.

    ``steps=0`` returns *start* unchanged.  Each step follows
    ``parent_commit_id``; if a parent is missing from the DB the walk stops
    and ``None`` is returned.
    """
    current: MuseCliCommit = start
    for _ in range(steps):
        if current.parent_commit_id is None:
            logger.debug("⚠️ Parent chain exhausted after %d step(s)", steps)
            return None
        parent = await session.get(MuseCliCommit, current.parent_commit_id)
        if parent is None:
            logger.warning(
                "⚠️ Parent commit %s not found in DB — chain broken",
                current.parent_commit_id[:8],
            )
            return None
        current = parent
    return current


async def _branch_exists_on_disk(muse_dir: pathlib.Path, name: str) -> bool:
    """Return True when a ref file exists for *name* under ``.muse/refs/heads/``."""
    return (muse_dir / "refs" / "heads" / name).exists()


async def resolve_revision(
    session: AsyncSession,
    repo_id: str,
    current_branch: str,
    muse_dir: pathlib.Path,
    revision_expr: str,
) -> RevParseResult | None:
    """Resolve *revision_expr* to a ``RevParseResult``, or return ``None``.

    This is the public plumbing primitive used by ``muse rev-parse`` and
    intended for reuse by other commands that accept revision arguments.

    Resolution order
    ----------------
    1. Strip a ``~N`` suffix and record *steps*.
    2. Resolve the base token:
       a. ``HEAD``              → tip of *current_branch*
       b. Named branch (ref file exists)  → tip of that branch
       c. Commit ID / prefix   → exact or prefix match
    3. Walk *steps* parent hops from the resolved base.
    4. Return ``RevParseResult`` or ``None`` when unresolvable.
    """
    # Step 1 — parse tilde suffix
    steps = 0
    base = revision_expr
    m = _TILDE_RE.match(revision_expr)
    if m:
        base = m.group(1)
        steps = int(m.group(2))

    # Step 2 — resolve base token to a commit
    commit: MuseCliCommit | None = None

    if base.upper() == "HEAD":
        commit = await _get_branch_tip(session, repo_id, current_branch)
        resolved_branch = current_branch
    elif await _branch_exists_on_disk(muse_dir, base):
        commit = await _get_branch_tip(session, repo_id, base)
        resolved_branch = base
    else:
        commit = await _resolve_commit_by_id(session, repo_id, base)
        resolved_branch = commit.branch if commit is not None else ""

    if commit is None:
        return None

    # Step 3 — walk parent chain
    ancestor = await _walk_parents(session, commit, steps)
    if ancestor is None:
        return None

    return RevParseResult(
        commit_id=ancestor.commit_id,
        branch=ancestor.branch,
        revision_expr=revision_expr,
    )


async def _rev_parse_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    revision: str,
    short: bool,
    verify: bool,
    abbrev_ref: bool,
) -> None:
    """Core rev-parse logic — fully injectable for tests.

    Reads repo state from ``.muse/``, resolves *revision*, and writes output
    via ``typer.echo``.  Raises ``typer.Exit`` on resolution failure when
    ``--verify`` is set.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    current_branch = head_ref.rsplit("/", 1)[-1]         # "main"

    result = await resolve_revision(
        session=session,
        repo_id=repo_id,
        current_branch=current_branch,
        muse_dir=muse_dir,
        revision_expr=revision,
    )

    if result is None:
        if verify:
            typer.echo(f"fatal: Not a valid revision: {revision!r}", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        # --verify not set: print nothing, exit 0
        return

    if abbrev_ref:
        typer.echo(result.branch)
    elif short:
        typer.echo(result.commit_id[:8])
    else:
        typer.echo(result.commit_id)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def rev_parse(
    ctx: typer.Context,
    revision: str = typer.Argument(..., help="Revision expression to resolve."),
    short: bool = typer.Option(
        False,
        "--short",
        help="Print only the first 8 characters of the commit ID.",
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Exit 1 if the revision does not resolve (default: print nothing).",
    ),
    abbrev_ref: bool = typer.Option(
        False,
        "--abbrev-ref",
        help="Print the branch name instead of the commit ID.",
    ),
) -> None:
    """Resolve a revision expression to a commit ID.

    Examples::

        muse rev-parse HEAD
        muse rev-parse HEAD~2
        muse rev-parse --short HEAD
        muse rev-parse --abbrev-ref HEAD
        muse rev-parse --verify nonexistent
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _rev_parse_async(
                root=root,
                session=session,
                revision=revision,
                short=short,
                verify=verify,
                abbrev_ref=abbrev_ref,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse rev-parse failed: {exc}")
        logger.error("❌ muse rev-parse error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
