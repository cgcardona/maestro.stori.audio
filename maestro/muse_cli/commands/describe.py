"""muse describe — generate a structured description of what changed musically.

Compares a commit against its parent (or two commits via ``--compare``) and
produces a structured description of the snapshot diff: which files changed
and how many.  Depth controls verbosity:

- **brief**    — one-line summary (commit ID + file count)
- **standard** — commit message, changed files list, dimensions (default)
- **verbose**  — standard plus parent commit info and full file paths

NOTE: Full harmonic/melodic analysis (identifying chord progressions, melodic
motifs, rhythmic changes) requires ``muse harmony`` and ``muse motif`` — both
planned enhancements tracked in follow-up issues.  This implementation uses
the snapshot manifest diff as a structural proxy: the set of files added,
removed, or modified between two commits.

Output formats
--------------
Default (human-readable)::

    Commit abc1234: "Add piano melody to verse"
    Changed files: 2 (beat.mid, keys.mid)
    Dimensions analyzed: structural (2 files modified)
    Note: Full harmonic/melodic analysis requires muse harmony and muse motif (planned)

JSON (``--json``)::

    {
      "commit": "abc1234...",
      "message": "Add piano melody to verse",
      "depth": "standard",
      "changed_files": ["beat.mid", "keys.mid"],
      "added_files": [],
      "removed_files": [],
      "dimensions": ["structural"],
      "file_count": 2,
      "parent": "def5678...",
      "note": "Full harmonic/melodic analysis requires muse harmony and muse motif (planned)"
    }

Auto-tag (``--auto-tag``)
--------------------------
When ``--auto-tag`` is given, a suggested tag is printed (or included in JSON)
based on the file count and dimensions.  This is a heuristic stub — a full
tagger would classify by musical dimension (rhythm, harmony, melody, etc.)
using instrument metadata.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from enum import Enum
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

logger = logging.getLogger(__name__)

app = typer.Typer()

_LLM_NOTE = (
    "Full harmonic/melodic analysis requires muse harmony and muse motif (planned)"
)


# ---------------------------------------------------------------------------
# Depth enum
# ---------------------------------------------------------------------------


class DescribeDepth(str, Enum):
    """Verbosity level for ``muse describe`` output."""

    brief = "brief"
    standard = "standard"
    verbose = "verbose"


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


class DescribeResult:
    """Structured description of what changed between two commits.

    Returned by ``_describe_async`` and consumed by both the human-readable
    renderer and the JSON serialiser.
    """

    def __init__(
        self,
        *,
        commit_id: str,
        message: str,
        depth: DescribeDepth,
        parent_id: str | None,
        compare_commit_id: str | None,
        changed_files: list[str],
        added_files: list[str],
        removed_files: list[str],
        dimensions: list[str],
        auto_tag: str | None,
    ) -> None:
        self.commit_id = commit_id
        self.message = message
        self.depth = depth
        self.parent_id = parent_id
        self.compare_commit_id = compare_commit_id
        self.changed_files = changed_files
        self.added_files = added_files
        self.removed_files = removed_files
        self.dimensions = dimensions
        self.auto_tag = auto_tag

    def file_count(self) -> int:
        return len(self.changed_files) + len(self.added_files) + len(self.removed_files)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-compatible dict."""
        result: dict[str, object] = {
            "commit": self.commit_id,
            "message": self.message,
            "depth": self.depth.value,
            "changed_files": self.changed_files,
            "added_files": self.added_files,
            "removed_files": self.removed_files,
            "dimensions": self.dimensions,
            "file_count": self.file_count(),
            "parent": self.parent_id,
            "note": _LLM_NOTE,
        }
        if self.compare_commit_id is not None:
            result["compare_commit"] = self.compare_commit_id
        if self.auto_tag is not None:
            result["auto_tag"] = self.auto_tag
        return result


# ---------------------------------------------------------------------------
# Snapshot diff helpers
# ---------------------------------------------------------------------------


def _diff_manifests(
    base: dict[str, str],
    target: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Compute the diff between two snapshot manifests.

    Returns ``(changed, added, removed)`` where each entry is a relative
    file path (as stored in the manifest keys).

    - *changed*  — path exists in both manifests but object_id differs
    - *added*    — path exists in *target* but not *base*
    - *removed*  — path exists in *base* but not *target*
    """
    all_paths = set(base) | set(target)
    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    for path in sorted(all_paths):
        base_obj = base.get(path)
        target_obj = target.get(path)
        if base_obj is None:
            added.append(path)
        elif target_obj is None:
            removed.append(path)
        elif base_obj != target_obj:
            changed.append(path)
    return changed, added, removed


def _infer_dimensions(
    changed: list[str],
    added: list[str],
    removed: list[str],
    requested: list[str],
) -> list[str]:
    """Infer musical dimensions from the file diff.

    This is a heuristic stub — always returns ``["structural"]`` with the
    file count as context.  A full implementation would inspect MIDI metadata
    to classify changes as rhythmic, harmonic, or melodic.
    """
    if requested:
        return [d.strip() for d in requested if d.strip()]
    total = len(changed) + len(added) + len(removed)
    if total == 0:
        return []
    return [f"structural ({total} file{'s' if total != 1 else ''} modified)"]


def _suggest_tag(dimensions: list[str], file_count: int) -> str:
    """Return a heuristic tag based on dimensions and file count.

    Stub implementation — a full tagger would classify by instrument and
    MIDI content.
    """
    if file_count == 0:
        return "no-change"
    if file_count == 1:
        return "single-file-edit"
    if file_count <= 3:
        return "minor-revision"
    return "major-revision"


# ---------------------------------------------------------------------------
# Async core — fully injectable for tests
# ---------------------------------------------------------------------------


async def _load_commit_with_snapshot(
    session: AsyncSession,
    commit_id: str,
) -> tuple[MuseCliCommit, dict[str, str]] | None:
    """Load a commit and its snapshot manifest.

    Returns ``None`` when either the commit or its snapshot is missing from
    the database (e.g. the repo is in a partially-consistent state).
    """
    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        return None
    snapshot = await session.get(MuseCliSnapshot, commit.snapshot_id)
    if snapshot is None:
        logger.warning(
            "⚠️ Snapshot %s for commit %s not found",
            commit.snapshot_id[:8],
            commit_id[:8],
        )
        return None
    return commit, dict(snapshot.manifest)


async def _resolve_head_commit_id(root: pathlib.Path) -> str | None:
    """Read the HEAD commit ID from the ``.muse/`` directory."""
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)
    if not ref_path.exists():
        return None
    value = ref_path.read_text().strip()
    return value or None


async def _describe_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_id: str | None,
    compare_a: str | None,
    compare_b: str | None,
    depth: DescribeDepth,
    dimensions_raw: str | None,
    as_json: bool,
    auto_tag: bool,
) -> DescribeResult:
    """Core describe logic — fully injectable for tests.

    Resolution order for which commits to diff:

    1. ``--compare A B`` — compare A against B explicitly.
    2. ``<commit>`` positional argument — compare that commit against its parent.
    3. No argument — compare HEAD against its parent.

    Raises ``typer.Exit`` with an appropriate exit code on error.
    """
    requested_dimensions = [d.strip() for d in (dimensions_raw or "").split(",") if d.strip()]

    # --- resolve target commit ------------------------------------------
    if compare_a and compare_b:
        # Explicit compare mode: diff A → B
        pair_a = await _load_commit_with_snapshot(session, compare_a)
        if pair_a is None:
            typer.echo(f"❌ Commit not found: {compare_a}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        pair_b = await _load_commit_with_snapshot(session, compare_b)
        if pair_b is None:
            typer.echo(f"❌ Commit not found: {compare_b}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        commit_a, manifest_a = pair_a
        commit_b, manifest_b = pair_b

        changed, added, removed = _diff_manifests(manifest_a, manifest_b)
        dims = _infer_dimensions(changed, added, removed, requested_dimensions)
        tag = _suggest_tag(dims, len(changed) + len(added) + len(removed)) if auto_tag else None

        return DescribeResult(
            commit_id=commit_b.commit_id,
            message=commit_b.message,
            depth=depth,
            parent_id=commit_a.commit_id,
            compare_commit_id=commit_a.commit_id,
            changed_files=changed,
            added_files=added,
            removed_files=removed,
            dimensions=dims,
            auto_tag=tag,
        )

    # --- single commit (or HEAD) mode -----------------------------------
    target_id = commit_id or await _resolve_head_commit_id(root)
    if not target_id:
        typer.echo("No commits yet on this branch.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    pair_target = await _load_commit_with_snapshot(session, target_id)
    if pair_target is None:
        typer.echo(f"❌ Commit not found: {target_id}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    target_commit, target_manifest = pair_target
    parent_id = target_commit.parent_commit_id

    if parent_id:
        pair_parent = await _load_commit_with_snapshot(session, parent_id)
        if pair_parent is None:
            # Parent referenced but missing — treat as empty base
            logger.warning("⚠️ Parent commit %s not found; treating as empty", parent_id[:8])
            parent_manifest: dict[str, str] = {}
        else:
            _, parent_manifest = pair_parent
    else:
        # Root commit — everything in the snapshot is "added"
        parent_manifest = {}

    changed, added, removed = _diff_manifests(parent_manifest, target_manifest)
    dims = _infer_dimensions(changed, added, removed, requested_dimensions)
    tag = _suggest_tag(dims, len(changed) + len(added) + len(removed)) if auto_tag else None

    return DescribeResult(
        commit_id=target_commit.commit_id,
        message=target_commit.message,
        depth=depth,
        parent_id=parent_id,
        compare_commit_id=None,
        changed_files=changed,
        added_files=added,
        removed_files=removed,
        dimensions=dims,
        auto_tag=tag,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_brief(result: DescribeResult) -> None:
    """One-line summary: short commit ID + total file count."""
    short_id = result.commit_id[:8]
    count = result.file_count()
    typer.echo(f"Commit {short_id}: {count} file change{'s' if count != 1 else ''}")
    if result.auto_tag:
        typer.echo(f"Tag: {result.auto_tag}")


def _render_standard(result: DescribeResult) -> None:
    """Standard output: commit message, changed files, dimensions."""
    short_id = result.commit_id[:8]
    count = result.file_count()
    typer.echo(f'Commit {short_id}: "{result.message}"')

    # Collect all changed paths for display
    all_changed = result.changed_files + result.added_files + result.removed_files
    file_names = [pathlib.Path(p).name for p in all_changed]
    files_str = (", ".join(file_names)) if file_names else "none"
    typer.echo(f"Changed files: {count} ({files_str})")

    dims_str = ", ".join(result.dimensions) if result.dimensions else "none"
    typer.echo(f"Dimensions analyzed: {dims_str}")

    if result.auto_tag:
        typer.echo(f"Tag: {result.auto_tag}")

    typer.echo(f"Note: {_LLM_NOTE}")


def _render_verbose(result: DescribeResult) -> None:
    """Verbose output: adds parent commit and full file paths."""
    typer.echo(f"Commit:  {result.commit_id}")
    typer.echo(f'Message: "{result.message}"')
    if result.parent_id:
        typer.echo(f"Parent:  {result.parent_id}")
    if result.compare_commit_id:
        typer.echo(f"Compare: {result.compare_commit_id} → {result.commit_id}")

    typer.echo("")
    count = result.file_count()
    typer.echo(f"Changed files ({count}):")
    for p in result.changed_files:
        typer.echo(f"  M  {p}")
    for p in result.added_files:
        typer.echo(f"  A  {p}")
    for p in result.removed_files:
        typer.echo(f"  D  {p}")
    if count == 0:
        typer.echo("  (no changes)")

    typer.echo("")
    dims_str = ", ".join(result.dimensions) if result.dimensions else "none"
    typer.echo(f"Dimensions: {dims_str}")

    if result.auto_tag:
        typer.echo(f"Tag:        {result.auto_tag}")

    typer.echo(f"\nNote: {_LLM_NOTE}")


def _render_result(result: DescribeResult, *, as_json: bool) -> None:
    """Dispatch to the appropriate renderer."""
    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    if result.depth == DescribeDepth.brief:
        _render_brief(result)
    elif result.depth == DescribeDepth.verbose:
        _render_verbose(result)
    else:
        _render_standard(result)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def describe(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        default=None,
        help="Commit ID to describe. Defaults to HEAD.",
    ),
    compare: Optional[list[str]] = typer.Option(
        default=None,
        help="Compare two commits: --compare COMMIT_A COMMIT_B.",
    ),
    depth: DescribeDepth = typer.Option(
        DescribeDepth.standard,
        "--depth",
        help="Output verbosity: brief, standard (default), or verbose.",
    ),
    dimensions: Optional[str] = typer.Option(
        None,
        "--dimensions",
        help="Comma-separated dimensions to analyze (e.g. 'rhythm,harmony'). "
        "Currently informational; full analysis is a planned enhancement.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON.",
    ),
    auto_tag: bool = typer.Option(
        False,
        "--auto-tag",
        help="Suggest a heuristic tag based on the change scope.",
    ),
) -> None:
    """Describe what changed musically in a commit.

    Compares the specified commit (default: HEAD) against its parent and
    outputs a structured description of the snapshot diff.

    NOTE: Full harmonic/melodic analysis is a planned enhancement.
    Current output is based on file-level snapshot diffs only.
    """
    root = require_repo()

    # Validate --compare: exactly 0 or 2 values
    compare_a: str | None = None
    compare_b: str | None = None
    if compare:
        if len(compare) != 2:
            typer.echo("❌ --compare requires exactly two commit IDs: --compare A B")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        compare_a, compare_b = compare[0], compare[1]

    async def _run() -> None:
        async with open_session() as session:
            result = await _describe_async(
                root=root,
                session=session,
                commit_id=commit,
                compare_a=compare_a,
                compare_b=compare_b,
                depth=depth,
                dimensions_raw=dimensions,
                as_json=json_output,
                auto_tag=auto_tag,
            )
            _render_result(result, as_json=json_output)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse describe failed: {exc}")
        logger.error("❌ muse describe error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
