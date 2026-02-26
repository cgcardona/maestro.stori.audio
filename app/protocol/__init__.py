"""Maestro Protocol — single source of truth for the FE ↔ BE wire contract.

Public re-exports for convenience:

    from app.protocol import (
        MAESTRO_VERSION,
        MaestroEvent,
        emit,
        parse_event,
        ProtocolSerializationError,
    )
"""
from __future__ import annotations

from app.protocol.version import MAESTRO_VERSION
from app.protocol.events import MaestroEvent
from app.protocol.emitter import emit, parse_event, ProtocolSerializationError

__all__ = [
    "MAESTRO_VERSION",
    "MaestroEvent",
    "emit",
    "parse_event",
    "ProtocolSerializationError",
]
