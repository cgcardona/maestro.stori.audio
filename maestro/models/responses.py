"""Response models for the Maestro API."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum

from maestro.contracts.json_types import ToolCallDict


class SSEMessageType(str, Enum):
    """Types of SSE messages sent to the client."""
    STATUS = "status"
    REASONING = "reasoning"
    GENERATING = "generating"
    GENERATED = "generated"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETE = "complete"
    ERROR = "error"


class SSEStatus(BaseModel):
    """Status update message."""
    type: Literal["status"] = "status"
    message: str


class SSEReasoning(BaseModel):
    """LLM reasoning/chain-of-thought message."""
    type: Literal["reasoning"] = "reasoning"
    content: str


class SSEGenerating(BaseModel):
    """Music generation in progress."""
    type: Literal["generating"] = "generating"
    tool: str
    params: dict[str, object]


class SSEGenerated(BaseModel):
    """Music generation complete."""
    type: Literal["generated"] = "generated"
    tool: str
    noteCount: int
    metadata: dict[str, object] | None = None


class SSEToolCall(BaseModel):
    """DAW tool call for Swift to execute."""
    type: Literal["tool_call"] = "tool_call"
    tool: str
    params: dict[str, object]


class SSEToolResult(BaseModel):
    """Result from tool execution."""
    type: Literal["tool_result"] = "tool_result"
    tool: str
    success: bool
    result: dict[str, object] | None = None


class SSEComplete(BaseModel):
    """Composition complete."""
    type: Literal["complete"] = "complete"
    success: bool
    tool_calls: list[ToolCallDict]
    summary: str | None = None


class SSEError(BaseModel):
    """Error message."""
    type: Literal["error"] = "error"
    message: str
    code: str | None = None


# Union type for all SSE messages
SSEMessage = SSEStatus | SSEReasoning | SSEGenerating | SSEGenerated | SSEToolCall | SSEToolResult | SSEComplete | SSEError


class MaestroResponse(BaseModel):
    """Non-streaming maestro response."""
    success: bool
    tool_calls: list[ToolCallDict]
    raw_response: str | None = None
    error: str | None = None
