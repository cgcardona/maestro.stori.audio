"""Muse Rebase Service — replay commits onto a new base.

Algorithm
---------
1. Find the merge-base (LCA) of the current branch HEAD and ``<upstream>``.
2. Collect the commits on the current branch that are *not* ancestors of
   ``<upstream>`` (i.e., commits added since the LCA), ordered oldest first.
3. For each such commit, compute the snapshot delta relative to its own parent,
   then apply that delta on top of the current ``onto`` tip (which starts as
   ``<upstream>`` HEAD and advances after each successful replay).
4. Insert a new commit record (new commit_id because the parent has changed).
5. Advance the branch pointer to the final replayed commit.

Because Muse snapshots are content-addressed manifests (``{path: object_id}``),
``apply_delta`` is a pure dict operation — no byte-level merge required.

State file: ``.muse/REBASE_STATE.json``
-----------------------------------------
Written when a conflict is detected mid-replay so ``--continue`` and ``--abort``
can resume or abandon the operation:

.. code-block:: json

    {
        "upstream_commit": "abc123...",
        "base_commit":     "def456...",
        "original_branch": "feature",
        "original_head":   "ghi789...",
        "commits_to_replay": ["cid1", "cid2", "cid3"],
        "current_onto":    "abc123...",
        "completed_pairs": [["cid1", "new_cid1"]],
        "current_commit":  "cid2",
        "conflict_paths":  ["beat.mid"]
    }

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules or maestro_* handlers.
  - May import muse_cli.db, muse_cli.models, muse_cli.merge_engine,
    muse_cli.snapshot.

Domain analogy: a producer has 10 "fixup" commits from a late-night session.
``muse rebase dev`` replays them cleanly onto the ``dev`` tip, producing a
linear history — the musical narrative stays readable as a sequence of
intentional variations.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import (
    get_commit_snapshot_manifest,
    insert_commit,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import (
    detect_conflicts,
    diff_snapshots,
)
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id

logger = logging.getLogger(__name__)

_REBASE_STATE_FILENAME = "REBASE_STATE.json"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RebaseCommitPair:
    """Maps an original commit to its replayed replacement.

    Attributes:
        original_commit_id: SHA of the commit that existed before the rebase.
        new_commit_id: SHA of the freshly-replayed commit with the new parent.
    """

    original_commit_id: str
    new_commit_id: str


@dataclass(frozen=True)
class RebaseResult:
    """Outcome of a ``muse rebase`` operation.

    Attributes:
        branch: The branch that was rebased.
        upstream: The upstream ref used as the new base.
        upstream_commit_id: Resolved commit ID of the upstream tip.
        base_commit_id: LCA commit where the histories diverged.
        replayed: Ordered list of (original, new) commit pairs.
        conflict_paths: Paths with unresolved conflicts (empty on success).
        aborted: True when ``--abort`` cleared an in-progress rebase.
        noop: True when no commits needed to be replayed.
        autosquash_applied: True when ``--autosquash`` reordered commits.
    """

    branch: str
    upstream: str
    upstream_commit_id: str
    base_commit_id: str
    replayed: tuple[RebaseCommitPair, ...]
    conflict_paths: tuple[str, ...]
    aborted: bool
    noop: bool
    autosquash_applied: bool


# ---------------------------------------------------------------------------
# RebaseState — on-disk session record
# ---------------------------------------------------------------------------


@dataclass
class RebaseState:
    """Describes an in-progress rebase with optional conflict information.

    Attributes:
        upstream_commit: The tip of the upstream branch used as the new base.
        base_commit: LCA where ours and upstream diverged.
        original_branch: Name of the branch being rebased.
        original_head: Branch HEAD before the rebase started (for ``--abort``).
        commits_to_replay: All original commits to replay, oldest first.
        current_onto: The commit ID of the current "onto" tip.
        completed_pairs: Pairs of (original, new) commit IDs already replayed.
        current_commit: The commit being applied when a conflict was detected.
        conflict_paths: Paths with unresolved conflicts (empty when none).
    """

    upstream_commit: str
    base_commit: str
    original_branch: str
    original_head: str
    commits_to_replay: list[str] = field(default_factory=list)
    current_onto: str = ""
    completed_pairs: list[list[str]] = field(default_factory=list)
    current_commit: str = ""
    conflict_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def read_rebase_state(root: pathlib.Path) -> RebaseState | None:
    """Return :class:`RebaseState` if a rebase is in progress, else ``None``.

    Reads ``.muse/REBASE_STATE.json``.  Returns ``None`` when the file does
    not exist or cannot be parsed.

    Args:
        root: Repository root (directory containing ``.muse/``).
    """
    state_path = root / ".muse" / _REBASE_STATE_FILENAME
    if not state_path.exists():
        return None
    try:
        raw: dict[str, object] = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("⚠️ Failed to read %s: %s", _REBASE_STATE_FILENAME, exc)
        return None

    def _str(key: str, default: str = "") -> str:
        v = raw.get(key, default)
        return str(v) if v is not None else default

    def _strlist(key: str) -> list[str]:
        v = raw.get(key, [])
        return [str(x) for x in v] if isinstance(v, list) else []

    def _pairlist(key: str) -> list[list[str]]:
        v = raw.get(key, [])
        if not isinstance(v, list):
            return []
        result: list[list[str]] = []
        for item in v:
            if isinstance(item, list) and len(item) == 2:
                result.append([str(item[0]), str(item[1])])
        return result

    return RebaseState(
        upstream_commit=_str("upstream_commit"),
        base_commit=_str("base_commit"),
        original_branch=_str("original_branch"),
        original_head=_str("original_head"),
        commits_to_replay=_strlist("commits_to_replay"),
        current_onto=_str("current_onto"),
        completed_pairs=_pairlist("completed_pairs"),
        current_commit=_str("current_commit"),
        conflict_paths=_strlist("conflict_paths"),
    )


def write_rebase_state(root: pathlib.Path, state: RebaseState) -> None:
    """Persist *state* to ``.muse/REBASE_STATE.json``.

    Args:
        root:  Repository root.
        state: Current rebase session state.
    """
    state_path = root / ".muse" / _REBASE_STATE_FILENAME
    data: dict[str, object] = {
        "upstream_commit": state.upstream_commit,
        "base_commit": state.base_commit,
        "original_branch": state.original_branch,
        "original_head": state.original_head,
        "commits_to_replay": state.commits_to_replay,
        "current_onto": state.current_onto,
        "completed_pairs": state.completed_pairs,
        "current_commit": state.current_commit,
        "conflict_paths": state.conflict_paths,
    }
    state_path.write_text(json.dumps(data, indent=2))
    logger.info(
        "✅ Wrote REBASE_STATE.json (%d remaining, %d done)",
        len(state.commits_to_replay),
        len(state.completed_pairs),
    )


def clear_rebase_state(root: pathlib.Path) -> None:
    """Remove ``.muse/REBASE_STATE.json`` after a successful or aborted rebase."""
    state_path = root / ".muse" / _REBASE_STATE_FILENAME
    if state_path.exists():
        state_path.unlink()
        logger.debug("✅ Cleared REBASE_STATE.json")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def compute_delta(
    parent_manifest: dict[str, str],
    commit_manifest: dict[str, str],
) -> tuple[dict[str, str], set[str]]:
    """Compute the file-level changes introduced by a single commit.

    Args:
        parent_manifest: Snapshot of the commit's parent.
        commit_manifest: Snapshot of the commit itself.

    Returns:
        Tuple of (additions_and_modifications, deletions):
        - additions_and_modifications: ``{path: object_id}`` for paths added
          or changed in *commit_manifest* relative to *parent_manifest*.
        - deletions: Set of paths present in *parent_manifest* but absent from
          *commit_manifest*.
    """
    changed_paths = diff_snapshots(parent_manifest, commit_manifest)
    additions: dict[str, str] = {}
    deletions: set[str] = set()
    for path in changed_paths:
        if path in commit_manifest:
            additions[path] = commit_manifest[path]
        else:
            deletions.add(path)
    return additions, deletions


def apply_delta(
    onto_manifest: dict[str, str],
    additions: dict[str, str],
    deletions: set[str],
) -> dict[str, str]:
    """Apply a commit delta onto an ``onto`` snapshot manifest.

    Produces a new manifest that represents the onto-manifest with the
    same file changes that the original commit introduced over its parent.

    Args:
        onto_manifest: The current tip manifest to patch.
        additions: Paths added or changed by the original commit.
        deletions: Paths removed by the original commit.

    Returns:
        New manifest dict (copy of *onto_manifest* with delta applied).
    """
    result = dict(onto_manifest)
    result.update(additions)
    for path in deletions:
        result.pop(path, None)
    return result


def detect_rebase_conflicts(
    onto_manifest: dict[str, str],
    prev_onto_manifest: dict[str, str],
    additions: dict[str, str],
    deletions: set[str],
) -> set[str]:
    """Identify conflicts between the commit delta and changes on ``onto``.

    A conflict occurs when a path was changed both in the commit being replayed
    (relative to its parent) and in the onto branch (relative to the base).

    Args:
        onto_manifest: Current onto tip.
        prev_onto_manifest: The onto state just before this replay step
            (i.e. the merge base or the previous onto tip).
        additions: Paths added/modified by the commit being replayed.
        deletions: Paths deleted by the commit being replayed.

    Returns:
        Set of conflicting paths.
    """
    onto_changed = diff_snapshots(prev_onto_manifest, onto_manifest)
    commit_changed = set(additions.keys()) | deletions
    return detect_conflicts(onto_changed, commit_changed)


async def _collect_branch_commits_since_base(
    session: AsyncSession,
    head_commit_id: str,
    base_commit_id: str,
) -> list[MuseCliCommit]:
    """Collect commits reachable from *head_commit_id* but not from *base_commit_id*.

    Returns them in topological order, oldest first (replay order).  Merge
    commits (two parents) are included as single units; their second parent is
    not traversed — i.e. only the primary-parent chain is followed.

    Args:
        session: Open async DB session.
        head_commit_id: The current branch HEAD.
        base_commit_id: The LCA — commits at or before this are excluded.

    Returns:
        List of :class:`MuseCliCommit` rows in replay order.
    """
    commits_reversed: list[MuseCliCommit] = []
    seen: set[str] = set()
    queue: deque[str] = deque([head_commit_id])

    while queue:
        cid = queue.popleft()
        if cid in seen or cid == base_commit_id:
            continue
        seen.add(cid)
        commit = await session.get(MuseCliCommit, cid)
        if commit is None:
            break
        commits_reversed.append(commit)
        if commit.parent_commit_id and commit.parent_commit_id != base_commit_id:
            queue.append(commit.parent_commit_id)
        elif commit.parent_commit_id == base_commit_id:
            # Include this commit but stop traversal here
            pass

    # BFS gives newest-first; reverse to get oldest-first replay order.
    return list(reversed(commits_reversed))


async def _find_merge_base_rebase(
    session: AsyncSession,
    commit_id_a: str,
    commit_id_b: str,
) -> str | None:
    """Lowest common ancestor of two commits — thin wrapper used by the rebase.

    Args:
        session: Open async DB session.
        commit_id_a: First commit ID (current branch HEAD).
        commit_id_b: Second commit ID (upstream tip).

    Returns:
        LCA commit ID, or ``None`` if histories are disjoint.
    """
    from maestro.muse_cli.merge_engine import find_merge_base

    return await find_merge_base(session, commit_id_a, commit_id_b)


def apply_autosquash(commits: list[MuseCliCommit]) -> tuple[list[MuseCliCommit], bool]:
    """Reorder and flag fixup commits for autosquash.

    Detects commits whose message starts with ``fixup! <msg>`` and moves them
    immediately after the matching commit (matched by prefix of ``<msg>``).

    Args:
        commits: Commits in replay order (oldest first).

    Returns:
        Tuple of (reordered_commits, was_reordered).
    """
    # Build index of message → position for non-fixup commits
    reordered: list[MuseCliCommit] = []
    fixups: dict[str, list[MuseCliCommit]] = {}

    for commit in commits:
        if commit.message.startswith("fixup! "):
            target_msg = commit.message[len("fixup! "):]
            fixups.setdefault(target_msg, []).append(commit)
        else:
            reordered.append(commit)

    if not fixups:
        return commits, False

    # Insert fixup commits immediately after their targets
    result: list[MuseCliCommit] = []
    for commit in reordered:
        result.append(commit)
        # Match by prefix of commit message
        for target_msg, fixup_list in list(fixups.items()):
            if commit.message.startswith(target_msg):
                result.extend(fixup_list)
                del fixups[target_msg]

    # Any unmatched fixups go at the end
    for fixup_list in fixups.values():
        result.extend(fixup_list)

    return result, True


# ---------------------------------------------------------------------------
# Interactive plan
# ---------------------------------------------------------------------------


class InteractivePlan:
    """A parsed interactive rebase plan.

    Plan lines have the format::

        <action> <short-sha> <message>

    Supported actions: ``pick``, ``squash``, ``drop``.

    Attributes:
        entries: List of (action, commit_id, message) tuples in plan order.
    """

    VALID_ACTIONS = frozenset({"pick", "squash", "drop", "fixup", "reword"})

    def __init__(
        self,
        entries: list[tuple[str, str, str]],
    ) -> None:
        """Create a plan from parsed entries.

        Args:
            entries: List of (action, commit_id_prefix, message) tuples.
        """
        self.entries = entries

    @classmethod
    def from_text(cls, text: str) -> InteractivePlan:
        """Parse a plan from the editor text.

        Lines starting with ``#`` are comments and are ignored.  Blank lines
        are ignored.  Each non-comment line must be ``<action> <sha> <msg>``.

        Args:
            text: Raw plan text as produced by :meth:`to_text`.

        Returns:
            Parsed :class:`InteractivePlan`.

        Raises:
            ValueError: If a line has an unrecognised action or missing fields.
        """
        entries: list[tuple[str, str, str]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) < 2:
                raise ValueError(f"Invalid plan line: {raw_line!r}")
            action = parts[0].lower()
            sha = parts[1]
            msg = parts[2] if len(parts) > 2 else ""
            if action not in cls.VALID_ACTIONS:
                raise ValueError(f"Unknown action {action!r} in line: {raw_line!r}")
            entries.append((action, sha, msg))
        return cls(entries)

    @classmethod
    def from_commits(cls, commits: list[MuseCliCommit]) -> InteractivePlan:
        """Build a default plan (all ``pick``) from a list of commits.

        Args:
            commits: Commits in replay order.

        Returns:
            :class:`InteractivePlan` with one ``pick`` entry per commit.
        """
        entries: list[tuple[str, str, str]] = []
        for commit in commits:
            entries.append(("pick", commit.commit_id[:8], commit.message))
        return cls(entries)

    def to_text(self) -> str:
        """Render the plan to a human-editable text format."""
        lines = [
            "# Interactive rebase plan.",
            "# Actions: pick, squash (fold into previous), drop (skip), fixup (squash no msg), reword",
            "# Lines starting with '#' are ignored.",
            "",
        ]
        for action, sha, msg in self.entries:
            lines.append(f"{action} {sha} {msg}")
        return "\n".join(lines) + "\n"

    def resolve_against(
        self, commits: list[MuseCliCommit]
    ) -> list[tuple[str, MuseCliCommit]]:
        """Match plan entries to the full commit list by SHA prefix.

        Args:
            commits: Original commits list (source of truth for full SHA).

        Returns:
            List of (action, commit) pairs in plan order, excluding dropped
            commits.

        Raises:
            ValueError: If a plan SHA prefix matches no commit or is ambiguous.
        """
        resolved: list[tuple[str, MuseCliCommit]] = []
        for action, sha_prefix, _msg in self.entries:
            if action == "drop":
                continue
            matches = [c for c in commits if c.commit_id.startswith(sha_prefix)]
            if not matches:
                raise ValueError(
                    f"Plan SHA {sha_prefix!r} does not match any commit in the rebase range."
                )
            if len(matches) > 1:
                raise ValueError(
                    f"Plan SHA {sha_prefix!r} is ambiguous — matches {len(matches)} commits."
                )
            resolved.append((action, matches[0]))
        return resolved


# ---------------------------------------------------------------------------
# Async rebase core — single-step replay
# ---------------------------------------------------------------------------


async def _replay_one_commit(
    *,
    session: AsyncSession,
    commit: MuseCliCommit,
    onto_manifest: dict[str, str],
    prev_onto_manifest: dict[str, str],
    onto_commit_id: str,
    branch: str,
) -> tuple[str, dict[str, str], list[str]]:
    """Replay a single commit onto the current onto tip.

    Computes the delta the original commit introduced over its parent, applies
    it to *onto_manifest*, detects conflicts, persists the new snapshot, and
    inserts a new commit record.

    Args:
        session: Open async DB session.
        commit: The original commit to replay.
        onto_manifest: Snapshot manifest of the current onto tip.
        prev_onto_manifest: Manifest of the onto base (for conflict detection).
        onto_commit_id: Commit ID of the current onto tip.
        branch: Branch being rebased (for the new commit record).

    Returns:
        Tuple of (new_commit_id, new_onto_manifest, conflict_paths):
        - ``new_commit_id``: SHA of the newly inserted commit.
        - ``new_onto_manifest``: Updated manifest for the next step.
        - ``conflict_paths``: Empty list on success; non-empty on conflict.
    """
    # Resolve parent manifest for the original commit
    parent_manifest: dict[str, str] = {}
    if commit.parent_commit_id:
        loaded = await get_commit_snapshot_manifest(session, commit.parent_commit_id)
        if loaded is not None:
            parent_manifest = loaded

    commit_manifest = await get_commit_snapshot_manifest(session, commit.commit_id)
    if commit_manifest is None:
        commit_manifest = {}

    additions, deletions = compute_delta(parent_manifest, commit_manifest)

    conflict_paths = detect_rebase_conflicts(
        onto_manifest=onto_manifest,
        prev_onto_manifest=prev_onto_manifest,
        additions=additions,
        deletions=deletions,
    )
    if conflict_paths:
        return "", onto_manifest, sorted(conflict_paths)

    new_manifest = apply_delta(onto_manifest, additions, deletions)
    new_snapshot_id = compute_snapshot_id(new_manifest)
    await upsert_snapshot(session, manifest=new_manifest, snapshot_id=new_snapshot_id)
    await session.flush()

    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=[onto_commit_id],
        snapshot_id=new_snapshot_id,
        message=commit.message,
        committed_at_iso=committed_at.isoformat(),
    )

    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=commit.repo_id,
        branch=branch,
        parent_commit_id=onto_commit_id,
        snapshot_id=new_snapshot_id,
        message=commit.message,
        author=commit.author,
        committed_at=committed_at,
        commit_metadata=commit.commit_metadata,
    )
    await insert_commit(session, new_commit)

    return new_commit_id, new_manifest, []


# ---------------------------------------------------------------------------
# Async rebase core — full pipeline
# ---------------------------------------------------------------------------


async def _rebase_async(
    *,
    upstream: str,
    root: pathlib.Path,
    session: AsyncSession,
    interactive: bool = False,
    autosquash: bool = False,
    rebase_merges: bool = False,
) -> RebaseResult:
    """Run the rebase pipeline.

    All filesystem and DB side-effects are isolated here so tests can inject
    an in-memory SQLite session and a ``tmp_path`` root without touching a
    real database.

    Args:
        upstream: Branch name or commit ID to rebase onto.
        root: Repository root (directory containing ``.muse/``).
        session: Open async DB session.
        interactive: When ``True``, open $EDITOR with the rebase plan before
            executing.  The edited plan controls action, order, and squash
            behaviour.
        autosquash: When ``True``, automatically detect ``fixup!`` commits and
            move them after their matching target commit.
        rebase_merges: When ``True``, preserve merge commits during replay
            (stub — see implementation note below).

    Returns:
        :class:`RebaseResult` describing what happened.

    Raises:
        ``typer.Exit`` with an appropriate exit code on user-facing errors.
    """
    import json as _json

    muse_dir = root / ".muse"

    # ── Guard: rebase already in progress ───────────────────────────────
    if read_rebase_state(root) is not None:
        typer.echo(
            "❌ Rebase in progress. Use --continue to resume or --abort to cancel."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = _json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()
    current_branch = head_ref.rsplit("/", 1)[-1]
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    ours_commit_id = our_ref_path.read_text().strip() if our_ref_path.exists() else ""
    if not ours_commit_id:
        typer.echo("❌ Current branch has no commits. Cannot rebase.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Resolve upstream ─────────────────────────────────────────────────
    # Try as a branch name first, then as a raw commit ID
    upstream_ref_path = muse_dir / "refs" / "heads" / upstream
    if upstream_ref_path.exists():
        upstream_commit_id = upstream_ref_path.read_text().strip()
    else:
        # Might be a raw commit ID
        candidate = await session.get(MuseCliCommit, upstream)
        if candidate is None:
            typer.echo(
                f"❌ Upstream {upstream!r} is not a known branch or commit ID."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        upstream_commit_id = candidate.commit_id

    # ── Already up-to-date guard ─────────────────────────────────────────
    if ours_commit_id == upstream_commit_id:
        typer.echo("Already up-to-date.")
        return RebaseResult(
            branch=current_branch,
            upstream=upstream,
            upstream_commit_id=upstream_commit_id,
            base_commit_id=upstream_commit_id,
            replayed=(),
            conflict_paths=(),
            aborted=False,
            noop=True,
            autosquash_applied=False,
        )

    # ── Find merge base ──────────────────────────────────────────────────
    base_commit_id = await _find_merge_base_rebase(
        session, ours_commit_id, upstream_commit_id
    )
    if base_commit_id is None:
        typer.echo(
            f"❌ Cannot find a common ancestor between current branch and {upstream!r}. "
            "Histories are disjoint — use 'muse merge' instead."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Fast-forward: current branch IS the base → nothing to replay ─────
    if base_commit_id == ours_commit_id:
        # Current branch is behind upstream — just advance the pointer
        our_ref_path.write_text(upstream_commit_id)
        typer.echo(
            f"✅ Fast-forward: {current_branch} → {upstream_commit_id[:8]}"
        )
        return RebaseResult(
            branch=current_branch,
            upstream=upstream,
            upstream_commit_id=upstream_commit_id,
            base_commit_id=base_commit_id,
            replayed=(),
            conflict_paths=(),
            aborted=False,
            noop=True,
            autosquash_applied=False,
        )

    # ── Already up-to-date: upstream IS the base → we are ahead ──────────
    if base_commit_id == upstream_commit_id:
        typer.echo("Already up-to-date.")
        return RebaseResult(
            branch=current_branch,
            upstream=upstream,
            upstream_commit_id=upstream_commit_id,
            base_commit_id=base_commit_id,
            replayed=(),
            conflict_paths=(),
            aborted=False,
            noop=True,
            autosquash_applied=False,
        )

    # ── Collect commits to replay ─────────────────────────────────────────
    commits_to_replay = await _collect_branch_commits_since_base(
        session, ours_commit_id, base_commit_id
    )

    if not commits_to_replay:
        typer.echo("Nothing to rebase.")
        return RebaseResult(
            branch=current_branch,
            upstream=upstream,
            upstream_commit_id=upstream_commit_id,
            base_commit_id=base_commit_id,
            replayed=(),
            conflict_paths=(),
            aborted=False,
            noop=True,
            autosquash_applied=False,
        )

    autosquash_applied = False

    # ── Autosquash ────────────────────────────────────────────────────────
    if autosquash:
        commits_to_replay, autosquash_applied = apply_autosquash(commits_to_replay)

    # ── Interactive plan ──────────────────────────────────────────────────
    plan_actions: list[tuple[str, MuseCliCommit]] = [
        ("pick", c) for c in commits_to_replay
    ]

    if interactive:
        import os
        import subprocess
        import tempfile

        plan = InteractivePlan.from_commits(commits_to_replay)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".rebase-plan", delete=False
        ) as tf:
            tf.write(plan.to_text())
            tf_path = tf.name

        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))
        result = subprocess.run([editor, tf_path])
        if result.returncode != 0:
            typer.echo("⚠️ Editor exited with non-zero code — rebase aborted.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        edited_text = pathlib.Path(tf_path).read_text()
        pathlib.Path(tf_path).unlink(missing_ok=True)

        try:
            edited_plan = InteractivePlan.from_text(edited_text)
            plan_actions = edited_plan.resolve_against(commits_to_replay)
        except ValueError as exc:
            typer.echo(f"❌ Invalid rebase plan: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        if not plan_actions:
            typer.echo("Nothing to do.")
            raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Resolve base and upstream manifests ───────────────────────────────
    base_manifest = await get_commit_snapshot_manifest(session, base_commit_id) or {}
    onto_manifest = (
        await get_commit_snapshot_manifest(session, upstream_commit_id) or {}
    )
    onto_commit_id = upstream_commit_id
    prev_onto_manifest = base_manifest

    completed_pairs: list[RebaseCommitPair] = []
    pending_squash_manifest: dict[str, str] | None = None
    pending_squash_commits: list[MuseCliCommit] = []

    for action, commit in plan_actions:
        if action == "drop":
            continue

        if action in ("squash", "fixup"):
            # Accumulate into squash group
            pending_squash_commits.append(commit)
            if pending_squash_manifest is None:
                # First in group — compute its delta
                parent_manifest: dict[str, str] = {}
                if commit.parent_commit_id:
                    loaded = await get_commit_snapshot_manifest(
                        session, commit.parent_commit_id
                    )
                    if loaded is not None:
                        parent_manifest = loaded
                commit_manifest = (
                    await get_commit_snapshot_manifest(session, commit.commit_id) or {}
                )
                additions, deletions = compute_delta(parent_manifest, commit_manifest)
                pending_squash_manifest = apply_delta(
                    onto_manifest, additions, deletions
                )
            else:
                # Add this commit's changes on top of the squash in progress
                parent_manifest_sq: dict[str, str] = {}
                if commit.parent_commit_id:
                    loaded_sq = await get_commit_snapshot_manifest(
                        session, commit.parent_commit_id
                    )
                    if loaded_sq is not None:
                        parent_manifest_sq = loaded_sq
                commit_manifest_sq = (
                    await get_commit_snapshot_manifest(session, commit.commit_id) or {}
                )
                additions_sq, deletions_sq = compute_delta(
                    parent_manifest_sq, commit_manifest_sq
                )
                pending_squash_manifest = apply_delta(
                    pending_squash_manifest, additions_sq, deletions_sq
                )
            continue

        # ── Flush pending squash group (if any) ───────────────────────
        if pending_squash_commits and pending_squash_manifest is not None:
            squash_message = pending_squash_commits[0].message
            squash_snap_id = compute_snapshot_id(pending_squash_manifest)
            await upsert_snapshot(
                session, manifest=pending_squash_manifest, snapshot_id=squash_snap_id
            )
            await session.flush()

            squash_at = datetime.datetime.now(datetime.timezone.utc)
            squash_commit_id = compute_commit_id(
                parent_ids=[onto_commit_id],
                snapshot_id=squash_snap_id,
                message=squash_message,
                committed_at_iso=squash_at.isoformat(),
            )
            squash_commit = MuseCliCommit(
                commit_id=squash_commit_id,
                repo_id=pending_squash_commits[0].repo_id,
                branch=current_branch,
                parent_commit_id=onto_commit_id,
                snapshot_id=squash_snap_id,
                message=squash_message,
                author=pending_squash_commits[0].author,
                committed_at=squash_at,
            )
            await insert_commit(session, squash_commit)

            for orig in pending_squash_commits:
                completed_pairs.append(
                    RebaseCommitPair(
                        original_commit_id=orig.commit_id,
                        new_commit_id=squash_commit_id,
                    )
                )
            onto_manifest = pending_squash_manifest
            onto_commit_id = squash_commit_id
            pending_squash_commits = []
            pending_squash_manifest = None

        # ── Normal pick ────────────────────────────────────────────────
        new_commit_id, new_manifest, conflict_paths_list = await _replay_one_commit(
            session=session,
            commit=commit,
            onto_manifest=onto_manifest,
            prev_onto_manifest=prev_onto_manifest,
            onto_commit_id=onto_commit_id,
            branch=current_branch,
        )

        if conflict_paths_list:
            # Persist state and exit with conflict
            remaining_ids = [
                c.commit_id
                for _, c in plan_actions[
                    plan_actions.index((action, commit)) + 1 :
                ]
            ]
            state = RebaseState(
                upstream_commit=upstream_commit_id,
                base_commit=base_commit_id,
                original_branch=current_branch,
                original_head=ours_commit_id,
                commits_to_replay=remaining_ids,
                current_onto=onto_commit_id,
                completed_pairs=[
                    [p.original_commit_id, p.new_commit_id] for p in completed_pairs
                ],
                current_commit=commit.commit_id,
                conflict_paths=conflict_paths_list,
            )
            write_rebase_state(root, state)

            typer.echo(
                f"❌ Conflict while replaying {commit.commit_id[:8]} ({commit.message!r}):\n"
                + "\n".join(f"\tboth modified: {p}" for p in conflict_paths_list)
                + "\nResolve conflicts, then run 'muse rebase --continue'."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        completed_pairs.append(
            RebaseCommitPair(
                original_commit_id=commit.commit_id,
                new_commit_id=new_commit_id,
            )
        )
        prev_onto_manifest = onto_manifest
        onto_manifest = new_manifest
        onto_commit_id = new_commit_id

    # ── Flush any trailing squash group ──────────────────────────────────
    if pending_squash_commits and pending_squash_manifest is not None:
        squash_message = pending_squash_commits[0].message
        squash_snap_id = compute_snapshot_id(pending_squash_manifest)
        await upsert_snapshot(
            session, manifest=pending_squash_manifest, snapshot_id=squash_snap_id
        )
        await session.flush()

        squash_at = datetime.datetime.now(datetime.timezone.utc)
        squash_commit_id = compute_commit_id(
            parent_ids=[onto_commit_id],
            snapshot_id=squash_snap_id,
            message=squash_message,
            committed_at_iso=squash_at.isoformat(),
        )
        squash_commit = MuseCliCommit(
            commit_id=squash_commit_id,
            repo_id=pending_squash_commits[0].repo_id,
            branch=current_branch,
            parent_commit_id=onto_commit_id,
            snapshot_id=squash_snap_id,
            message=squash_message,
            author=pending_squash_commits[0].author,
            committed_at=squash_at,
        )
        await insert_commit(session, squash_commit)

        for orig in pending_squash_commits:
            completed_pairs.append(
                RebaseCommitPair(
                    original_commit_id=orig.commit_id,
                    new_commit_id=squash_commit_id,
                )
            )
        onto_commit_id = squash_commit_id

    # ── Advance branch pointer ────────────────────────────────────────────
    our_ref_path.write_text(onto_commit_id)

    typer.echo(
        f"✅ Rebased {len(completed_pairs)} commit(s) onto {upstream!r} "
        f"[{current_branch} {onto_commit_id[:8]}]"
    )
    logger.info(
        "✅ muse rebase: %d commit(s) replayed onto %r (%s), branch %r now at %s",
        len(completed_pairs),
        upstream,
        upstream_commit_id[:8],
        current_branch,
        onto_commit_id[:8],
    )

    return RebaseResult(
        branch=current_branch,
        upstream=upstream,
        upstream_commit_id=upstream_commit_id,
        base_commit_id=base_commit_id,
        replayed=tuple(completed_pairs),
        conflict_paths=(),
        aborted=False,
        noop=False,
        autosquash_applied=autosquash_applied,
    )


async def _rebase_continue_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> RebaseResult:
    """Resume a rebase that was paused due to a conflict.

    Reads ``REBASE_STATE.json``, assumes the conflicted commit has been resolved
    manually, creates a new commit from the current ``onto`` state, and
    continues replaying the remaining commits.

    Args:
        root: Repository root.
        session: Open async DB session.

    Returns:
        :class:`RebaseResult` describing the completed rebase.

    Raises:
        ``typer.Exit``: If no rebase is in progress or conflicts remain.
    """
    import json as _json

    rebase_state = read_rebase_state(root)
    if rebase_state is None:
        typer.echo("❌ No rebase in progress. Nothing to continue.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if rebase_state.conflict_paths:
        typer.echo(
            f"❌ {len(rebase_state.conflict_paths)} conflict(s) not yet resolved:\n"
            + "\n".join(
                f"\tboth modified: {p}" for p in rebase_state.conflict_paths
            )
            + "\nResolve conflicts manually, then run 'muse rebase --continue'."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    repo_data: dict[str, str] = _json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    current_branch = rebase_state.original_branch
    head_ref = f"refs/heads/{current_branch}"
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    completed_pairs: list[RebaseCommitPair] = [
        RebaseCommitPair(original_commit_id=p[0], new_commit_id=p[1])
        for p in rebase_state.completed_pairs
    ]

    onto_commit_id = rebase_state.current_onto
    onto_manifest = (
        await get_commit_snapshot_manifest(session, onto_commit_id) or {}
    )

    # Replay the remaining commits
    for orig_cid in rebase_state.commits_to_replay:
        commit = await session.get(MuseCliCommit, orig_cid)
        if commit is None:
            typer.echo(f"⚠️ Commit {orig_cid[:8]} not found — skipping.")
            continue

        parent_manifest: dict[str, str] = {}
        if commit.parent_commit_id:
            loaded = await get_commit_snapshot_manifest(
                session, commit.parent_commit_id
            )
            if loaded is not None:
                parent_manifest = loaded

        commit_manifest = (
            await get_commit_snapshot_manifest(session, commit.commit_id) or {}
        )
        additions, deletions = compute_delta(parent_manifest, commit_manifest)
        new_manifest = apply_delta(onto_manifest, additions, deletions)
        new_snapshot_id = compute_snapshot_id(new_manifest)
        await upsert_snapshot(
            session, manifest=new_manifest, snapshot_id=new_snapshot_id
        )
        await session.flush()

        committed_at = datetime.datetime.now(datetime.timezone.utc)
        new_commit_id = compute_commit_id(
            parent_ids=[onto_commit_id],
            snapshot_id=new_snapshot_id,
            message=commit.message,
            committed_at_iso=committed_at.isoformat(),
        )
        new_commit = MuseCliCommit(
            commit_id=new_commit_id,
            repo_id=repo_id,
            branch=current_branch,
            parent_commit_id=onto_commit_id,
            snapshot_id=new_snapshot_id,
            message=commit.message,
            author=commit.author,
            committed_at=committed_at,
            commit_metadata=commit.commit_metadata,
        )
        await insert_commit(session, new_commit)

        completed_pairs.append(
            RebaseCommitPair(
                original_commit_id=orig_cid,
                new_commit_id=new_commit_id,
            )
        )
        onto_manifest = new_manifest
        onto_commit_id = new_commit_id

    # Advance branch pointer and clear state
    our_ref_path.write_text(onto_commit_id)
    clear_rebase_state(root)

    upstream_commit_id = rebase_state.upstream_commit
    base_commit_id = rebase_state.base_commit

    typer.echo(
        f"✅ Rebase continued: {len(completed_pairs)} commit(s) applied "
        f"[{current_branch} {onto_commit_id[:8]}]"
    )
    logger.info(
        "✅ muse rebase --continue: %d commit(s) on %r, now at %s",
        len(completed_pairs),
        current_branch,
        onto_commit_id[:8],
    )

    return RebaseResult(
        branch=current_branch,
        upstream=rebase_state.upstream_commit,
        upstream_commit_id=upstream_commit_id,
        base_commit_id=base_commit_id,
        replayed=tuple(completed_pairs),
        conflict_paths=(),
        aborted=False,
        noop=False,
        autosquash_applied=False,
    )


async def _rebase_abort_async(
    *,
    root: pathlib.Path,
) -> RebaseResult:
    """Abort an in-progress rebase and restore the branch to its original HEAD.

    Reads ``REBASE_STATE.json``, restores the branch pointer to
    ``original_head``, and removes the state file.

    Args:
        root: Repository root.

    Returns:
        :class:`RebaseResult` with ``aborted=True``.

    Raises:
        ``typer.Exit``: If no rebase is in progress.
    """
    rebase_state = read_rebase_state(root)
    if rebase_state is None:
        typer.echo("❌ No rebase in progress. Nothing to abort.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    current_branch = rebase_state.original_branch
    head_ref = f"refs/heads/{current_branch}"
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    our_ref_path.parent.mkdir(parents=True, exist_ok=True)
    our_ref_path.write_text(rebase_state.original_head)

    clear_rebase_state(root)

    typer.echo(
        f"✅ Rebase aborted. Branch {current_branch!r} restored to "
        f"{rebase_state.original_head[:8]}."
    )
    logger.info(
        "✅ muse rebase --abort: %r restored to %s",
        current_branch,
        rebase_state.original_head[:8],
    )

    return RebaseResult(
        branch=current_branch,
        upstream="",
        upstream_commit_id=rebase_state.upstream_commit,
        base_commit_id=rebase_state.base_commit,
        replayed=(),
        conflict_paths=(),
        aborted=True,
        noop=False,
        autosquash_applied=False,
    )
