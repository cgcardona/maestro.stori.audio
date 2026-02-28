"""muse arrange [<commit>] — display the arrangement map (instrument activity over sections).

Shows which instruments are active in which musical sections for a given
commit.  The arrangement is derived from the committed snapshot manifest:
files must follow the path convention ``<section>/<instrument>/<filename>``
(relative to ``muse-work/``).

Flags
-----
- ``[COMMIT]``          — target commit (default: HEAD)
- ``--section TEXT``    — show only a specific section's instrumentation
- ``--track TEXT``      — show only a specific instrument's section participation
- ``--compare A B``     — diff two arrangements
- ``--density``         — show byte-size density instead of binary active/inactive
- ``--format text|json|csv`` — output format (default: text)
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import get_commit_snapshot_manifest, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject
from maestro.services.muse_arrange import (
    ArrangementMatrix,
    build_arrangement_diff,
    build_arrangement_matrix,
    render_diff_json,
    render_diff_text,
    render_matrix_csv,
    render_matrix_json,
    render_matrix_text,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_HEX_CHARS = frozenset("0123456789abcdef")


def _looks_like_commit_prefix(s: str) -> bool:
    """Return True if *s* is a 4-64 char lowercase hex string."""
    lower = s.lower()
    return 4 <= len(lower) <= 64 and all(c in _HEX_CHARS for c in lower)


async def _resolve_commit_id(
    session: AsyncSession,
    muse_dir: pathlib.Path,
    ref_or_prefix: str,
) -> str:
    """Resolve HEAD, branch name, or commit-ID prefix to a full commit_id."""
    if ref_or_prefix.upper() == "HEAD" or not _looks_like_commit_prefix(ref_or_prefix):
        head_ref = (muse_dir / "HEAD").read_text().strip()

        if ref_or_prefix.upper() == "HEAD":
            ref_path = muse_dir / pathlib.Path(head_ref)
        else:
            ref_path = muse_dir / "refs" / "heads" / ref_or_prefix

        if not ref_path.exists():
            typer.echo(f"No commits yet or reference '{ref_or_prefix}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        commit_id = ref_path.read_text().strip()
        if not commit_id:
            typer.echo(f"Reference '{ref_or_prefix}' has no commits yet.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return commit_id

    prefix = ref_or_prefix.lower()
    result = await session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id.startswith(prefix))
    )
    commits = list(result.scalars().all())

    if not commits:
        typer.echo(f"No commit found matching prefix '{prefix[:8]}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if len(commits) > 1:
        typer.echo(f"Ambiguous prefix '{prefix[:8]}' - matches {len(commits)} commits:")
        for c in commits:
            typer.echo(f"   {c.commit_id[:8]}  {c.message[:60]}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    return commits[0].commit_id


async def _load_object_sizes(
    session: AsyncSession,
    manifest: dict[str, str],
) -> dict[str, int]:
    """Return {object_id: size_bytes} for all objects in *manifest*."""
    object_ids = list(set(manifest.values()))
    if not object_ids:
        return {}

    result = await session.execute(
        select(MuseCliObject).where(MuseCliObject.object_id.in_(object_ids))
    )
    return {obj.object_id: obj.size_bytes for obj in result.scalars().all()}


async def _load_matrix(
    session: AsyncSession,
    muse_dir: pathlib.Path,
    ref: str,
    density: bool,
) -> ArrangementMatrix:
    """Load a commit manifest and build the arrangement matrix."""
    commit_id = await _resolve_commit_id(session, muse_dir, ref)
    manifest = await get_commit_snapshot_manifest(session, commit_id)

    if manifest is None:
        typer.echo(f"Could not load snapshot for commit {commit_id[:8]}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    object_sizes: dict[str, int] | None = None
    if density:
        object_sizes = await _load_object_sizes(session, manifest)

    return build_arrangement_matrix(commit_id, manifest, object_sizes)


async def _arrange_async(
    root: pathlib.Path,
    session: AsyncSession,
    commit: str,
    compare_a: str | None,
    compare_b: str | None,
    section_filter: str | None,
    track_filter: str | None,
    density: bool,
    output_format: str,
) -> None:
    """Core arrange logic - fully injectable for unit tests."""
    muse_dir = root / ".muse"

    if compare_a is not None and compare_b is not None:
        matrix_a = await _load_matrix(session, muse_dir, compare_a, density)
        matrix_b = await _load_matrix(session, muse_dir, compare_b, density)
        diff = build_arrangement_diff(matrix_a, matrix_b)

        if output_format == "json":
            typer.echo(render_diff_json(diff))
        else:
            typer.echo(render_diff_text(diff))
        return

    matrix = await _load_matrix(session, muse_dir, commit, density)

    if not matrix.sections and not matrix.instruments:
        typer.echo(
            f"Arrangement Map - commit {matrix.commit_id[:8]}\n\n"
            "No section-annotated files found.\n"
            "Files must follow the path convention: <section>/<instrument>/<filename>"
        )
        return

    if output_format == "json":
        typer.echo(
            render_matrix_json(
                matrix,
                density=density,
                section_filter=section_filter,
                track_filter=track_filter,
            )
        )
    elif output_format == "csv":
        typer.echo(
            render_matrix_csv(
                matrix,
                density=density,
                section_filter=section_filter,
                track_filter=track_filter,
            )
        )
    else:
        typer.echo(
            render_matrix_text(
                matrix,
                density=density,
                section_filter=section_filter,
                track_filter=track_filter,
            )
        )


@app.callback(invoke_without_command=True)
def arrange(
    ctx: typer.Context,
    commit: str = typer.Argument(
        default="HEAD",
        help="Commit reference: HEAD, branch name, or commit-ID prefix.",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Show only a specific section's instrumentation.",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Show only a specific instrument's section participation.",
    ),
    compare: Optional[list[str]] = typer.Option(
        None,
        "--compare",
        help="Diff two arrangements. Provide --compare twice.",
    ),
    density: bool = typer.Option(
        False,
        "--density",
        help="Show byte-size density per cell instead of binary active/inactive.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text (default), json, or csv.",
    ),
) -> None:
    """Display the arrangement map: instrument activity over sections."""
    if output_format not in ("text", "json", "csv"):
        typer.echo(f"Unknown format '{output_format}'. Choose: text, json, csv.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    compare_a: str | None = None
    compare_b: str | None = None

    if compare:
        if len(compare) != 2:
            typer.echo(
                "--compare requires exactly two commit references.\n"
                "   Use: --compare <commit-a> --compare <commit-b>"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        compare_a, compare_b = compare[0], compare[1]

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _arrange_async(
                root=root,
                session=session,
                commit=commit,
                compare_a=compare_a,
                compare_b=compare_b,
                section_filter=section,
                track_filter=track,
                density=density,
                output_format=output_format,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse arrange failed: {exc}")
        logger.error("muse arrange error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
