"""muse init — initialise a new Muse repository.

Creates the ``.muse/`` directory tree in the current working directory and
writes all identity/configuration files that subsequent commands depend on:

    .muse/
        repo.json        repo_id (UUID), schema_version, created_at
        HEAD             text pointer → refs/heads/main
        refs/heads/main  empty (no commits yet)
        config.toml      [user] [auth] [remotes] stubs

``--force`` reinitialises an existing repo while preserving the existing
``repo_id`` so that remote-tracking metadata stays coherent.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
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


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-initialise even if this is already a Muse repository.",
    ),
) -> None:
    """Initialise a new Muse repository in the current directory."""
    cwd = pathlib.Path.cwd()
    muse_dir = cwd / ".muse"

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
        repo_json: dict[str, str] = {
            "repo_id": repo_id,
            "schema_version": _SCHEMA_VERSION,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        (muse_dir / "repo.json").write_text(json.dumps(repo_json, indent=2) + "\n")

        # HEAD — current branch pointer
        (muse_dir / "HEAD").write_text("refs/heads/main\n")

        # refs/heads/main — empty = no commits on this branch yet
        ref_file = muse_dir / "refs" / "heads" / "main"
        if not ref_file.exists() or force:
            ref_file.write_text("")

        # config.toml — only written on fresh init (not overwritten on --force)
        # so existing remote/user config is preserved.
        config_path = muse_dir / "config.toml"
        if not config_path.exists():
            config_path.write_text(_DEFAULT_CONFIG_TOML)

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
    typer.echo(f"✅ {action} Muse repository in {muse_dir}")
    logger.info("✅ %s Muse repository in %s (repo_id=%s)", action, muse_dir, repo_id)
