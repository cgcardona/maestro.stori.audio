"""Tool-related models for the Stori Maestro."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.contracts.json_types import JSONObject


class MidiNote(BaseModel):
    """A single MIDI note."""
    pitch: int = Field(..., ge=0, le=127, description="MIDI note number (0-127)")
    start_beat: float = Field(..., ge=0, description="Start position in beats")
    duration_beats: float = Field(..., gt=0, description="Duration in beats")
    velocity: int = Field(default=100, ge=0, le=127, description="Note velocity (0-127)")
    channel: int = Field(default=0, ge=0, le=15, description="MIDI channel (0-15)")


class AutomationPoint(BaseModel):
    """A point in an automation curve."""
    beat: float = Field(..., ge=0, description="Position in beats")
    value: float = Field(..., ge=0, le=1, description="Normalized value (0-1)")
    curve: str = Field(default="Linear", description="Curve type: Linear, Smooth, Step, Exp, Log, S-Curve")
    tension: float = Field(default=0, ge=-1, le=1, description="Curve tension")


class ControllerEvent(BaseModel):
    """MIDI CC event."""
    controller: int = Field(..., ge=0, le=127, description="CC number")
    value: int = Field(..., ge=0, le=127, description="CC value")
    time: float = Field(..., ge=0, description="Time in beats")
    channel: int = Field(default=0, ge=0, le=15, description="MIDI channel")


class PitchBendEvent(BaseModel):
    """MIDI pitch bend event."""
    value: int = Field(..., ge=-8192, le=8191, description="Pitch bend value")
    time: float = Field(..., ge=0, description="Time in beats")
    channel: int = Field(default=0, ge=0, le=15, description="MIDI channel")


class ToolResult(BaseModel):
    """Result from executing a tool.

    ``result`` is typed ``dict[str, object]`` rather than ``JSONObject`` because
    Pydantic cannot resolve JSONValue's recursive forward refs across module
    boundaries — see json_types.py for the Pydantic compatibility rule.
    """

    tool_call_id: str = Field(..., description="ID of the tool call this responds to")
    success: bool = Field(..., description="Whether the tool executed successfully")
    result: dict[str, object] | None = Field(default=None, description="Tool result data")
    error: str | None = Field(default=None, description="Error message if failed")


class DAWToolCall(BaseModel):
    """A DAW tool call to be executed by Swift."""
    tool: str = Field(..., description="Tool name (e.g., stori_add_midi_track, stori_add_notes)")
    params: dict[str, object] = Field(..., description="Tool parameters")
