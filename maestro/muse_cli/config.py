"""Muse CLI configuration helpers.

Reads ``[auth] token`` from ``.muse/config.toml`` in the local repository
and exposes it to CLI commands that need to authenticate against a remote
Muse Hub.

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

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.toml"
_MUSE_DIR = ".muse"


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
    root = (repo_root or pathlib.Path.cwd()).resolve()
    config_path = root / _MUSE_DIR / _CONFIG_FILENAME

    if not config_path.is_file():
        logger.debug("⚠️ No %s found at %s", _CONFIG_FILENAME, config_path)
        return None

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to parse %s: %s", config_path, exc)
        return None

    token: object = data.get("auth", {}).get("token", "")
    if not isinstance(token, str) or not token.strip():
        logger.debug("⚠️ [auth] token missing or empty in %s", config_path)
        return None

    logger.debug("✅ Auth token loaded from %s (Bearer ***)", config_path)
    return token.strip()
