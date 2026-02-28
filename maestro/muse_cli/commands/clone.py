"""muse clone <url> [directory] — clone a Muse Hub repository locally.

Clone algorithm
---------------
1. Parse *url* to derive the default target directory name (last URL path
   component, stripped of trailing slashes).
2. Resolve the effective target directory (explicit *directory* arg or the
   derived default).  Abort if it already exists.
3. Create the target directory and initialise ``.muse/`` structure (mirroring
   ``muse init`` without re-invoking it):
       <target>/.muse/repo.json        — stub; repo_id written after Hub reply
       <target>/.muse/HEAD             — refs/heads/<effective_branch>
       <target>/.muse/refs/heads/      — ref files populated after clone
       <target>/.muse/config.toml      — origin remote set to *url*
4. POST to ``<url>/clone`` with branch, depth, single_track parameters.
5. Write ``repo_id`` returned by Hub into ``.muse/repo.json``.
6. Store returned commits and object descriptors in local Postgres.
7. Update ``.muse/refs/heads/<branch>`` to the remote HEAD commit ID.
8. Write remote-tracking pointer to ``.muse/remotes/origin/<branch>``.
9. Unless ``--no-checkout``, create ``muse-work/`` in the target directory.

Exit codes:
  0 — success
  1 — user error (target directory exists, bad args)
  3 — network / server error
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
import uuid

import httpx
import typer

from maestro.muse_cli.config import set_remote, set_remote_head
from maestro.muse_cli.db import (
    open_session,
    store_pulled_commit,
    store_pulled_object,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import (
    CloneRequest,
    CloneResponse,
    MuseHubClient,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_SCHEMA_VERSION = "1"

_DEFAULT_CONFIG_TOML = """\
[user]
name = ""
email = ""

[auth]
token = ""

[remotes]
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_directory_name(url: str) -> str:
    """Derive a directory name from a Hub URL.

    Strips trailing slashes and returns the last path component.  Falls back
    to ``"muse-clone"`` when the URL has no meaningful path segment.

    Args:
        url: Muse Hub repo URL (e.g. ``"https://hub.stori.app/repos/my-project"``).

    Returns:
        A suitable local directory name string.
    """
    stripped = url.rstrip("/")
    last = stripped.rsplit("/", 1)[-1]
    return last if last and last not in ("repos", "musehub") else "muse-clone"


def _init_muse_dir(
    target: pathlib.Path,
    branch: str,
    origin_url: str,
) -> None:
    """Create the ``.muse/`` skeleton inside *target*.

    Writes a stub ``repo.json`` (repo_id filled in after Hub reply),
    ``HEAD`` pointing at *branch*, and ``config.toml`` with the origin remote.
    Does NOT write the branch ref file — that is written after the Hub returns
    the remote HEAD commit ID.

    Args:
        target:     Repository root directory (must exist and be empty).
        branch:     Default branch name (written to HEAD).
        origin_url: Remote URL written as ``[remotes.origin]`` in config.toml.
    """
    muse_dir = target / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (muse_dir / "remotes").mkdir(parents=True, exist_ok=True)

    # Stub repo.json — repo_id is overwritten once Hub responds.
    stub_repo: dict[str, str] = {
        "repo_id": str(uuid.uuid4()),
        "schema_version": _SCHEMA_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (muse_dir / "repo.json").write_text(
        json.dumps(stub_repo, indent=2) + "\n", encoding="utf-8"
    )

    # HEAD pointer
    (muse_dir / "HEAD").write_text(f"refs/heads/{branch}\n", encoding="utf-8")

    # Empty branch ref (no commits yet).
    # Branch names may contain slashes (e.g. "feature/guitar") so we must
    # create the intermediate directory before writing the ref file.
    ref_file = muse_dir / "refs" / "heads" / branch
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text("", encoding="utf-8")

    # config.toml with origin remote
    config_path = muse_dir / "config.toml"
    config_path.write_text(_DEFAULT_CONFIG_TOML, encoding="utf-8")
    # Use set_remote to write the [remotes.origin] section properly.
    set_remote("origin", origin_url, target)


# ---------------------------------------------------------------------------
# Async clone core
# ---------------------------------------------------------------------------


async def _clone_async(
    *,
    url: str,
    directory: str | None,
    depth: int | None,
    branch: str | None,
    single_track: str | None,
    no_checkout: bool,
) -> None:
    """Execute the clone pipeline.

    Raises :class:`typer.Exit` with the appropriate exit code on all error
    paths so the Typer callback can remain thin.

    Args:
        url:          Muse Hub repo URL to clone from.
        directory:    Local target directory path (derived from URL if None).
        depth:        Shallow-clone depth (number of commits to fetch).
        branch:       Branch to clone and check out (Hub default if None).
        single_track: Instrument track filter — only files whose first path
                      component matches this string are downloaded.
        no_checkout:  When True, skip populating ``muse-work/``.
    """
    # ── Resolve target directory ──────────────────────────────────────────
    target_name = directory or _derive_directory_name(url)
    target = pathlib.Path(target_name).resolve()

    if target.exists():
        typer.echo(
            f"❌ Destination '{target}' already exists.\n"
            "   Choose a different directory or remove it first."
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    # ── Determine effective branch (placeholder until Hub responds) ───────
    effective_branch = branch or "main"

    typer.echo(f"Cloning into '{target.name}' …")

    # ── Create target directory and initialise .muse/ ─────────────────────
    try:
        target.mkdir(parents=True, exist_ok=False)
        _init_muse_dir(target, effective_branch, url)
    except PermissionError as exc:
        typer.echo(f"❌ Permission denied creating '{target}': {exc}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
    except OSError as exc:
        typer.echo(f"❌ Failed to create repository at '{target}': {exc}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    # ── HTTP clone request ────────────────────────────────────────────────
    clone_request = CloneRequest(
        branch=branch,
        depth=depth,
        single_track=single_track,
    )

    try:
        async with MuseHubClient(base_url=url, repo_root=target) as hub:
            response = await hub.post("/clone", json=clone_request)

        if response.status_code != 200:
            typer.echo(
                f"❌ Hub rejected clone (HTTP {response.status_code}): {response.text}"
            )
            logger.error(
                "❌ muse clone failed: HTTP %d — %s",
                response.status_code,
                response.text,
            )
            raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    except typer.Exit:
        raise
    except httpx.TimeoutException:
        typer.echo(f"❌ Clone timed out connecting to {url}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
    except httpx.HTTPError as exc:
        typer.echo(f"❌ Network error during clone: {exc}")
        logger.error("❌ muse clone network error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    # ── Parse response ────────────────────────────────────────────────────
    raw_body: object = response.json()
    if not isinstance(raw_body, dict):
        typer.echo("❌ Hub returned unexpected clone response shape.")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    raw_repo_id = raw_body.get("repo_id")
    raw_default_branch = raw_body.get("default_branch", effective_branch)
    raw_remote_head = raw_body.get("remote_head")

    clone_response = CloneResponse(
        repo_id=str(raw_repo_id) if isinstance(raw_repo_id, str) else str(uuid.uuid4()),
        default_branch=str(raw_default_branch) if isinstance(raw_default_branch, str) else effective_branch,
        remote_head=str(raw_remote_head) if isinstance(raw_remote_head, str) else None,
        commits=list(raw_body.get("commits", [])),
        objects=list(raw_body.get("objects", [])),
    )

    repo_id = clone_response["repo_id"]
    resolved_branch = clone_response["default_branch"]
    remote_head = clone_response["remote_head"]

    # ── Write canonical repo_id returned by Hub ───────────────────────────
    muse_dir = target / ".muse"
    repo_json: dict[str, str] = {
        "repo_id": repo_id,
        "schema_version": _SCHEMA_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (muse_dir / "repo.json").write_text(
        json.dumps(repo_json, indent=2) + "\n", encoding="utf-8"
    )

    # Update HEAD to the resolved branch (may differ from our placeholder).
    (muse_dir / "HEAD").write_text(
        f"refs/heads/{resolved_branch}\n", encoding="utf-8"
    )

    # ── Store commits and objects in local DB ─────────────────────────────
    new_commits_count = 0
    new_objects_count = 0

    async with open_session() as session:
        for commit_data in clone_response["commits"]:
            if isinstance(commit_data, dict):
                commit_data_with_repo = dict(commit_data)
                commit_data_with_repo.setdefault("repo_id", repo_id)
                inserted = await store_pulled_commit(session, commit_data_with_repo)
                if inserted:
                    new_commits_count += 1

        for obj_data in clone_response["objects"]:
            if isinstance(obj_data, dict):
                inserted = await store_pulled_object(session, dict(obj_data))
                if inserted:
                    new_objects_count += 1

    # ── Update local branch ref and remote-tracking pointer ───────────────
    if remote_head:
        ref_path = muse_dir / "refs" / "heads" / resolved_branch
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(remote_head, encoding="utf-8")
        set_remote_head("origin", resolved_branch, remote_head, target)

    # ── Populate muse-work/ unless --no-checkout ──────────────────────────
    if not no_checkout:
        work_dir = target / "muse-work"
        work_dir.mkdir(exist_ok=True)
        logger.debug("✅ Created muse-work/ in %s", target)

    # ── Summary ───────────────────────────────────────────────────────────
    depth_note = f" (depth {depth})" if depth is not None else ""
    track_note = f", track={single_track!r}" if single_track else ""
    checkout_note = " (no checkout)" if no_checkout else ""
    typer.echo(
        f"✅ Cloned{depth_note}{track_note}{checkout_note}: "
        f"{new_commits_count} commit(s), {new_objects_count} object(s) "
        f"→ '{target.name}'"
    )
    logger.info(
        "✅ muse clone %s → %s: +%d commits, +%d objects, branch=%s",
        url,
        target,
        new_commits_count,
        new_objects_count,
        resolved_branch,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def clone(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="Muse Hub repository URL to clone from."),
    directory: str | None = typer.Argument(
        None,
        help="Local directory to clone into. Defaults to the repo name from the URL.",
    ),
    depth: int | None = typer.Option(
        None,
        "--depth",
        help="Shallow clone: fetch only the last N commits.",
        min=1,
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Clone and check out a specific branch instead of the Hub default.",
    ),
    single_track: str | None = typer.Option(
        None,
        "--single-track",
        help=(
            "Clone only files matching a specific instrument track "
            "(e.g. 'drums', 'keys'). Filters by first path component."
        ),
    ),
    no_checkout: bool = typer.Option(
        False,
        "--no-checkout",
        help="Set up .muse/ and fetch objects but leave muse-work/ empty.",
    ),
) -> None:
    """Clone a Muse Hub repository into a new local directory.

    Creates a new directory, initialises ``.muse/``, fetches all commits and
    objects from the Hub, and populates ``muse-work/`` with the HEAD snapshot.
    Writes "origin" to ``.muse/config.toml`` pointing at *url*.

    Examples::

        muse clone https://hub.stori.app/repos/my-project
        muse clone https://hub.stori.app/repos/my-project ./collab
        muse clone https://hub.stori.app/repos/my-project --depth 1
        muse clone https://hub.stori.app/repos/my-project --branch feature/guitar
        muse clone https://hub.stori.app/repos/my-project --single-track keys
        muse clone https://hub.stori.app/repos/my-project --no-checkout
    """
    try:
        asyncio.run(
            _clone_async(
                url=url,
                directory=directory,
                depth=depth,
                branch=branch,
                single_track=single_track,
                no_checkout=no_checkout,
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse clone failed: {exc}")
        logger.error("❌ muse clone unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
