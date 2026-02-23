"""Stori Protocol version and compatibility."""

from __future__ import annotations

STORI_PROTOCOL_VERSION = "1.0.0"

STORI_PROTOCOL_MAJOR = 1
STORI_PROTOCOL_MINOR = 0
STORI_PROTOCOL_PATCH = 0


def is_compatible(client_version: str) -> bool:
    """Check if a client protocol version is compatible.

    Compatibility rule: same major version.  Minor/patch differences
    are forwards-compatible (server may emit events the client ignores).
    """
    try:
        parts = client_version.split(".")
        client_major = int(parts[0])
        return client_major == STORI_PROTOCOL_MAJOR
    except (ValueError, IndexError):
        return False
