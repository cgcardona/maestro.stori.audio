"""Stori version — single source of truth is pyproject.toml.

All version references (app, protocol, MCP server) read from here.
"""

from __future__ import annotations


def _read_version() -> str:
    try:
        from importlib.metadata import version
        return version("maestro-stori")
    except Exception:
        pass
    try:
        from pathlib import Path
        import re
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "0.0.0-unknown"


STORI_VERSION: str = _read_version()

STORI_PROTOCOL_VERSION: str = STORI_VERSION

_version_parts = STORI_VERSION.split(".")
STORI_VERSION_MAJOR: int = int(_version_parts[0]) if len(_version_parts) > 0 else 0
STORI_VERSION_MINOR: int = int(_version_parts[1]) if len(_version_parts) > 1 else 0
STORI_VERSION_PATCH: int = int(_version_parts[2].split("-")[0]) if len(_version_parts) > 2 else 0


def is_compatible(client_version: str) -> bool:
    """Check if a client version is compatible (same major version)."""
    try:
        parts = client_version.split(".")
        client_major = int(parts[0])
        return client_major == STORI_VERSION_MAJOR
    except (ValueError, IndexError):
        return False
