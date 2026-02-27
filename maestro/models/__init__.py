"""Pydantic models for the Maestro API."""
from __future__ import annotations

from maestro.models.requests import MaestroRequest, GenerateRequest
from maestro.models.responses import (
    MaestroResponse,
    SSEMessage,
    SSEStatus,
    SSEReasoning,
    SSEToolCall,
    SSEComplete,
    SSEError,
)
from maestro.models.tools import (
    ToolResult,
    MidiNote,
    AutomationPoint,
)

__all__ = [
    "MaestroRequest",
    "GenerateRequest",
    "MaestroResponse",
    "SSEMessage",
    "SSEStatus",
    "SSEReasoning",
    "SSEToolCall",
    "SSEComplete",
    "SSEError",
    "ToolResult",
    "MidiNote",
    "AutomationPoint",
]
