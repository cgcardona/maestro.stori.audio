"""Muse CLI configuration helpers.

Reads and writes ``.muse/config.toml`` — the local repository configuration file.

The config file supports:
- ``[auth] token`` — bearer token for Muse Hub authentication (NEVER logged).
- ``[remotes.<name>] url`` — remote Hub URL for push/pull sync.

Token lifecycle (MVP):
  1. User obtains a token via ``POST /auth/token``.
  2. User stores it in ``.muse/config.toml`` under ``[auth] token = "..."``
  3. CLI commands that contact the Hub read the token here automatically.

Security note: ``.muse/config.toml`` should be added to ``.gitignore`` to
prevent the token from being committed to version control.
"""
from __future__ import annotations

import logging
import pathlib
import tomllib
from typing import TypedDict

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.toml"
_MUSE_DIR = ".muse"


# ---------------------------------------------------------------------------
# TypedDicts for structured config data
# ---------------------------------------------------------------------------


class RemoteConfig(TypedDict):
    """A single configured remote — name and URL."""

    name: str
    url: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _config_path(repo_root: pathlib.Path | None) -> pathlib.Path:
    """Return the path to .muse/config.toml for the given (or cwd) root."""
    root = (repo_root or pathlib.Path.cwd()).resolve()
    return root / _MUSE_DIR / _CONFIG_FILENAME


def _load_config(config_path: pathlib.Path) -> dict[str, object]:
    """Load and parse config.toml; return empty dict if absent or unreadable."""
    if not config_path.is_file():
        return {}
    try:
        with config_path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to parse %s: %s", config_path, exc)
        return {}


def _dump_toml(data: dict[str, object]) -> str:
    """Serialize a two-level TOML dict (tables of string values) to text.

    Handles the subset of TOML used by .muse/config.toml:
    - Top-level tables (``[section]``) whose values are strings.
    - Nested tables (``[section.subsection]``) whose values are strings.

    Values of other types are coerced to strings and stored as TOML strings.
    The ``[auth]`` section is always written first so the file is stable.
    """
    lines: list[str] = []

    def _write_table(heading: str, mapping: dict[str, object]) -> None:
        lines.append(f"[{heading}]")
        for key, val in mapping.items():
            if isinstance(val, str):
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            else:
                lines.append(f"{key} = {val!r}")
        lines.append("")

    # Auth section first (stable ordering)
    if "auth" in data:
        auth_section = data["auth"]
        if isinstance(auth_section, dict):
            _write_table("auth", auth_section)

    # Remotes (nested tables: [remotes.<name>])
    if "remotes" in data:
        remotes_section = data["remotes"]
        if isinstance(remotes_section, dict):
            for remote_name in sorted(remotes_section):
                remote_cfg = remotes_section[remote_name]
                if isinstance(remote_cfg, dict):
                    _write_table(f"remotes.{remote_name}", remote_cfg)

    # Any other top-level sections
    for key, val in data.items():
        if key in ("auth", "remotes"):
            continue
        if isinstance(val, dict):
            _write_table(key, val)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def get_auth_token(repo_root: pathlib.Path | None = None) -> str | None:
    """Read ``[auth] token`` from ``.muse/config.toml``.

    Returns the token string if present and non-empty, or ``None`` if the
    file does not exist, ``[auth]`` is absent, or ``token`` is empty/missing.

    The token value is NEVER logged — log lines mask it as ``"Bearer ***"``.

    Args:
        repo_root: Explicit repository root.  Defaults to the current working
                   directory.  In tests, pass a ``tmp_path`` fixture value.

    Returns:
        The raw token string, or ``None``.
    """
    config_path = _config_path(repo_root)

    if not config_path.is_file():
        logger.debug("⚠️ No %s found at %s", _CONFIG_FILENAME, config_path)
        return None

    data = _load_config(config_path)
    auth_section = data.get("auth", {})
    token: object = auth_section.get("token", "") if isinstance(auth_section, dict) else ""
    if not isinstance(token, str) or not token.strip():
        logger.debug("⚠️ [auth] token missing or empty in %s", config_path)
        return None

    logger.debug("✅ Auth token loaded from %s (Bearer ***)", config_path)
    return token.strip()


# ---------------------------------------------------------------------------
# Remote helpers
# ---------------------------------------------------------------------------


def get_remote(name: str, repo_root: pathlib.Path | None = None) -> str | None:
    """Return the URL for remote *name* from ``[remotes.<name>] url``.

    Returns ``None`` when the config file is absent or the named remote has
    not been configured.  Never raises — callers decide what to do on miss.

    Args:
        name: Remote name (e.g. ``"origin"``).
        repo_root: Repository root.  Defaults to ``Path.cwd()``.

    Returns:
        URL string, or ``None``.
    """
    config_path = _config_path(repo_root)
    data = _load_config(config_path)
    remotes_section = data.get("remotes", {})
    if not isinstance(remotes_section, dict):
        return None
    remote_cfg = remotes_section.get(name, {})
    if not isinstance(remote_cfg, dict):
        return None
    url: object = remote_cfg.get("url", "")
    if not isinstance(url, str) or not url.strip():
        return None
    return url.strip()


def set_remote(
    name: str,
    url: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Write ``[remotes.<name>] url = "<url>"`` to ``.muse/config.toml``.

    Preserves all other sections already in the config file.  Creates the
    ``.muse/`` directory and ``config.toml`` if they do not exist.

    Args:
        name: Remote name (e.g. ``"origin"``).
        url: Remote URL (e.g. ``"https://hub.example.com/musehub/repos/my-repo"``).
        repo_root: Repository root.  Defaults to ``Path.cwd()``.
    """
    config_path = _config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = _load_config(config_path)

    # Ensure the nested structure exists
    if "remotes" not in data or not isinstance(data["remotes"], dict):
        data["remotes"] = {}
    remotes: dict[str, object] = data["remotes"]  # type: ignore[assignment]
    if name not in remotes or not isinstance(remotes[name], dict):
        remotes[name] = {}
    remote_entry: dict[str, object] = remotes[name]  # type: ignore[assignment]
    remote_entry["url"] = url

    config_path.write_text(_dump_toml(data), encoding="utf-8")
    logger.info("✅ Remote %r set to %s", name, url)


def remove_remote(
    name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Remove a named remote and all its tracking refs from ``.muse/``.

    Deletes ``[remotes.<name>]`` from ``config.toml`` and removes the entire
    ``.muse/remotes/<name>/`` directory tree (tracking head files).  Raises
    ``KeyError`` when the remote does not exist so callers can surface a clear
    error message to the user.

    Args:
        name: Remote name to remove (e.g. ``"origin"``).
        repo_root: Repository root.  Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *name* is not a configured remote.
    """
    config_path = _config_path(repo_root)
    data = _load_config(config_path)

    remotes_section = data.get("remotes", {})
    if not isinstance(remotes_section, dict) or name not in remotes_section:
        raise KeyError(name)

    del remotes_section[name]
    data["remotes"] = remotes_section

    config_path.write_text(_dump_toml(data), encoding="utf-8")
    logger.info("✅ Remote %r removed from config", name)

    # Remove tracking refs directory if it exists
    root = (repo_root or pathlib.Path.cwd()).resolve()
    refs_dir = root / _MUSE_DIR / "remotes" / name
    if refs_dir.is_dir():
        import shutil

        shutil.rmtree(refs_dir)
        logger.debug("✅ Removed tracking refs dir %s", refs_dir)


def rename_remote(
    old_name: str,
    new_name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Rename a remote in ``.muse/config.toml`` and move its tracking refs.

    Updates ``[remotes.<old_name>]`` → ``[remotes.<new_name>]`` in config and
    moves ``.muse/remotes/<old_name>/`` → ``.muse/remotes/<new_name>/``.
    Raises ``KeyError`` when *old_name* does not exist.  Raises ``ValueError``
    when *new_name* is already configured.

    Args:
        old_name: Current remote name.
        new_name: Desired new remote name.
        repo_root: Repository root.  Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *old_name* is not a configured remote.
        ValueError: If *new_name* already exists as a remote.
    """
    config_path = _config_path(repo_root)
    data = _load_config(config_path)

    remotes_section = data.get("remotes", {})
    if not isinstance(remotes_section, dict) or old_name not in remotes_section:
        raise KeyError(old_name)
    if new_name in remotes_section:
        raise ValueError(new_name)

    remotes_section[new_name] = remotes_section.pop(old_name)
    data["remotes"] = remotes_section

    config_path.write_text(_dump_toml(data), encoding="utf-8")
    logger.info("✅ Remote %r renamed to %r", old_name, new_name)

    # Move tracking refs directory if it exists
    root = (repo_root or pathlib.Path.cwd()).resolve()
    old_refs_dir = root / _MUSE_DIR / "remotes" / old_name
    new_refs_dir = root / _MUSE_DIR / "remotes" / new_name
    if old_refs_dir.is_dir():
        old_refs_dir.rename(new_refs_dir)
        logger.debug("✅ Moved tracking refs dir %s → %s", old_refs_dir, new_refs_dir)


def list_remotes(repo_root: pathlib.Path | None = None) -> list[RemoteConfig]:
    """Return all configured remotes as :class:`RemoteConfig` dicts.

    Returns an empty list when the config file is absent or contains no
    ``[remotes.*]`` sections.  Sorted alphabetically by remote name.

    Args:
        repo_root: Repository root.  Defaults to ``Path.cwd()``.

    Returns:
        List of ``{"name": str, "url": str}`` dicts.
    """
    config_path = _config_path(repo_root)
    data = _load_config(config_path)
    remotes_section = data.get("remotes", {})
    if not isinstance(remotes_section, dict):
        return []

    result: list[RemoteConfig] = []
    for remote_name in sorted(remotes_section):
        cfg = remotes_section[remote_name]
        if not isinstance(cfg, dict):
            continue
        url_val: object = cfg.get("url", "")
        if isinstance(url_val, str) and url_val.strip():
            result.append(RemoteConfig(name=remote_name, url=url_val.strip()))

    return result


# ---------------------------------------------------------------------------
# Remote tracking-head helpers
# ---------------------------------------------------------------------------


def _remote_head_path(
    remote_name: str,
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """Return the path to the remote tracking pointer file.

    The file lives at ``.muse/remotes/<remote_name>/<branch>`` and contains
    the last known commit_id on that remote branch.
    """
    root = (repo_root or pathlib.Path.cwd()).resolve()
    return root / _MUSE_DIR / "remotes" / remote_name / branch


def get_remote_head(
    remote_name: str,
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> str | None:
    """Return the last-known remote commit ID for *remote_name*/*branch*.

    Returns ``None`` when the tracking pointer file does not exist (i.e. this
    branch has never been pushed/pulled).

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name (e.g. ``"main"``).
        repo_root: Repository root.  Defaults to ``Path.cwd()``.

    Returns:
        Commit ID string, or ``None``.
    """
    pointer = _remote_head_path(remote_name, branch, repo_root)
    if not pointer.is_file():
        return None
    raw = pointer.read_text(encoding="utf-8").strip()
    return raw if raw else None


def set_remote_head(
    remote_name: str,
    branch: str,
    commit_id: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Write the remote tracking pointer for *remote_name*/*branch*.

    Creates the ``.muse/remotes/<remote_name>/`` directory if needed.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name (e.g. ``"main"``).
        commit_id: Commit ID to record as the known remote HEAD.
        repo_root: Repository root.  Defaults to ``Path.cwd()``.
    """
    pointer = _remote_head_path(remote_name, branch, repo_root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(commit_id, encoding="utf-8")
    logger.debug("✅ Remote head %s/%s → %s", remote_name, branch, commit_id[:8])
