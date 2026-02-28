"""muse init — initialise a new Muse repository.

Creates the ``.muse/`` directory tree in the current working directory and
writes all identity/configuration files that subsequent commands depend on.

Normal (non-bare) layout::

    .muse/
        repo.json        repo_id (UUID), schema_version, created_at, bare flag
        HEAD             text pointer → refs/heads/<branch>
        refs/heads/<branch>  empty (no commits yet)
        config.toml      [user] [auth] [remotes] stubs
    muse-work/           working-tree root (absent for --bare repos)

Bare layout (``--bare``)::

    .muse/
        repo.json        … bare = true …
        HEAD             refs/heads/<branch>
        refs/heads/<branch>

Bare repositories have no ``muse-work/`` directory.  They are used as
Muse Hub remotes — objects and refs only, no live working copy.

Flags
-----
``--bare``
    Initialise as a bare repository (no ``muse-work/`` checkout).
    Writes ``bare = true`` into ``.muse/config.toml``.
``--template <path>``
    Copy the contents of *path* into ``muse-work/`` after creating the
    directory structure.  Useful for studio project templates.
``--default-branch TEXT``
    Name of the initial branch (default: ``main``).
``--force``
    Re-initialise even if a ``.muse/`` directory already exists.
    Preserves the existing ``repo_id`` so remote-tracking metadata stays
    coherent.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
import uuid

import typer

from maestro.muse_cli._repo import find_repo_root
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()

_SCHEMA_VERSION = "1"

# Default config.toml written on first init; intentionally minimal.
_DEFAULT_CONFIG_TOML = """\
[user]
name = ""
email = ""

[auth]
token = ""

[remotes]
"""

_BARE_CONFIG_TOML = """\
[core]
bare = true

[user]
name = ""
email = ""

[auth]
token = ""

[remotes]
"""


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-initialise even if this is already a Muse repository.",
    ),
    bare: bool = typer.Option(
        False,
        "--bare",
        help=(
            "Initialise as a bare repository (no muse-work/ checkout). "
            "Used for remote/server-side repos that store objects and refs "
            "but no working copy."
        ),
    ),
    template: str | None = typer.Option(
        None,
        "--template",
        metavar="PATH",
        help=(
            "Copy the contents of PATH into muse-work/ after initialisation. "
            "Lets studios pre-populate a standard folder structure "
            "(e.g. drums/, bass/, keys/, vocals/) for every new project."
        ),
    ),
    default_branch: str = typer.Option(
        "main",
        "--default-branch",
        metavar="BRANCH",
        help="Name of the initial branch (default: main).",
    ),
) -> None:
    """Initialise a new Muse repository in the current directory."""
    cwd = pathlib.Path.cwd()
    muse_dir = cwd / ".muse"

    # Validate template path early before doing any filesystem work.
    template_path: pathlib.Path | None = None
    if template is not None:
        template_path = pathlib.Path(template)
        if not template_path.is_dir():
            typer.echo(
                f"❌ Template path does not exist or is not a directory: {template_path}"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    # Check if a .muse/ already exists anywhere in cwd (not parents).
    # We deliberately only check the *immediate* cwd, not parents, so that
    # `muse init` inside a nested sub-directory works as expected.
    already_exists = muse_dir.is_dir()

    if already_exists and not force:
        typer.echo(
            f"Already a Muse repository at {cwd}.\n"
            "Use --force to reinitialise."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # On reinitialise: preserve the existing repo_id for remote-tracking
    # coherence — a force-init must not break an existing push target.
    existing_repo_id: str | None = None
    if force and already_exists:
        repo_json_path = muse_dir / "repo.json"
        if repo_json_path.exists():
            try:
                existing_repo_id = json.loads(repo_json_path.read_text()).get("repo_id")
            except (json.JSONDecodeError, OSError):
                pass  # Corrupt file — generate a fresh ID.

    # --- Create directory structure ---
    # Wrap all filesystem writes in a single OSError handler so that
    # PermissionError (e.g. CWD is not writable, common when running
    # `docker compose exec maestro muse init` from /app/) produces a clean
    # user-facing message instead of a raw Python traceback.
    try:
        (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)

        # repo.json — identity file
        repo_id = existing_repo_id or str(uuid.uuid4())
        repo_json: dict[str, str | bool] = {
            "repo_id": repo_id,
            "schema_version": _SCHEMA_VERSION,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if bare:
            repo_json["bare"] = True
        (muse_dir / "repo.json").write_text(json.dumps(repo_json, indent=2) + "\n")

        # HEAD — current branch pointer (uses --default-branch name)
        (muse_dir / "HEAD").write_text(f"refs/heads/{default_branch}\n")

        # refs/heads/<branch> — empty = no commits on this branch yet
        ref_file = muse_dir / "refs" / "heads" / default_branch
        if not ref_file.exists() or force:
            ref_file.write_text("")

        # config.toml — only written on fresh init (not overwritten on --force)
        # so existing remote/user config is preserved.
        config_path = muse_dir / "config.toml"
        if not config_path.exists():
            config_path.write_text(_BARE_CONFIG_TOML if bare else _DEFAULT_CONFIG_TOML)

        # muse-work/ — working-tree root (skipped for bare repos)
        if not bare:
            work_dir = cwd / "muse-work"
            work_dir.mkdir(exist_ok=True)

            # --template: copy template contents into muse-work/
            if template_path is not None:
                for item in template_path.iterdir():
                    dest = work_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)

    except PermissionError:
        typer.echo(
            f"❌ Permission denied: cannot write to {cwd}.\n"
            "Run `muse init` from a directory you have write access to.\n"
            "Tip: if running inside Docker, create a writable directory first:\n"
            "  docker compose exec maestro sh -c "
            '"mkdir -p /tmp/my-project && cd /tmp/my-project && python -m maestro.muse_cli.app init"'
        )
        logger.error("❌ Permission denied creating .muse/ in %s", cwd)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except OSError as exc:
        typer.echo(f"❌ Failed to initialise repository: {exc}")
        logger.error("❌ OSError creating .muse/ in %s: %s", cwd, exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    action = "Reinitialised" if (force and already_exists) else "Initialised"
    kind = "bare " if bare else ""
    typer.echo(f"✅ {action} {kind}Muse repository in {muse_dir}")
    logger.info(
        "✅ %s %sMuse repository in %s (repo_id=%s, branch=%s)",
        action,
        kind,
        muse_dir,
        repo_id,
        default_branch,
    )
