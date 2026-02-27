"""Maestro Protocol — single source of truth for the FE ↔ BE wire contract.

Public re-exports for convenience:

    from maestro.protocol import (
        MAESTRO_VERSION,
        MaestroEvent,
        emit,
        parse_event,
        ProtocolSerializationError,
    )
"""
from __future__ import annotations

from maestro.protocol.version import MAESTRO_VERSION
from maestro.protocol.events import MaestroEvent
from maestro.protocol.emitter import emit, parse_event, ProtocolSerializationError

__all__ = [
    "MAESTRO_VERSION",
    "MaestroEvent",
    "emit",
    "parse_event",
    "ProtocolSerializationError",
]
