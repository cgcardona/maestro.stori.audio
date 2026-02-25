"""Stori Protocol — single source of truth for the FE ↔ BE wire contract.

Public re-exports for convenience:

    from app.protocol import STORI_VERSION, STORI_PROTOCOL_VERSION, emit, serialize_event, StoriEvent
"""
from __future__ import annotations

from app.protocol.version import STORI_VERSION, STORI_PROTOCOL_VERSION
from app.protocol.events import StoriEvent
from app.protocol.emitter import emit, serialize_event, ProtocolSerializationError

__all__ = [
    "STORI_VERSION",
    "STORI_PROTOCOL_VERSION",
    "StoriEvent",
    "emit",
    "serialize_event",
    "ProtocolSerializationError",
]
