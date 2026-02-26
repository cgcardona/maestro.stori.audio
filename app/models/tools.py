"""Tool-related models for the Stori Maestro."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.contracts.json_types import JSONObject
from app.contracts.midi_types import (
    BeatDuration,
    BeatPosition,
    MidiAftertouchValue,
    MidiCC,
    MidiCCValue,
    MidiChannel,
    MidiPitch,
    MidiPitchBend,
    MidiVelocity,
)


class MidiNote(BaseModel):
    """A single MIDI note."""

    pitch: MidiPitch = Field(..., description="MIDI note number (0–127)")
    start_beat: BeatPosition = Field(..., description="Start position in beats (≥ 0)")
    duration_beats: BeatDuration = Field(..., description="Duration in beats (> 0)")
    velocity: MidiVelocity = Field(default=100, description="Note velocity (0–127)")
    channel: MidiChannel = Field(default=0, description="MIDI channel (0–15)")


class AutomationPoint(BaseModel):
    """A point in a normalized automation curve (e.g. volume, pan).

    ``value`` is normalized to [0, 1] — not a raw MIDI byte.
    Use ``ControllerEvent`` for MIDI CC data.
    """

    beat: BeatPosition = Field(..., description="Position in beats (≥ 0)")
    value: float = Field(..., ge=0.0, le=1.0, description="Normalized value (0–1)")
    curve: str = Field(
        default="Linear",
        description="Curve type: Linear, Smooth, Step, Exp, Log, S-Curve",
    )
    tension: float = Field(default=0.0, ge=-1.0, le=1.0, description="Curve tension (−1–1)")


class ControllerEvent(BaseModel):
    """MIDI CC event."""

    controller: MidiCC = Field(..., description="CC controller number (0–127)")
    value: MidiCCValue = Field(..., description="CC value (0–127)")
    time: BeatPosition = Field(..., description="Time in beats (≥ 0)")
    channel: MidiChannel = Field(default=0, description="MIDI channel (0–15)")


class PitchBendEvent(BaseModel):
    """MIDI pitch bend event."""

    value: MidiPitchBend = Field(..., description="Pitch bend value (−8192–8191)")
    time: BeatPosition = Field(..., description="Time in beats (≥ 0)")
    channel: MidiChannel = Field(default=0, description="MIDI channel (0–15)")


class AftertouchEvent(BaseModel):
    """MIDI aftertouch event (channel pressure or polyphonic key pressure).

    When ``pitch`` is ``None`` this is *channel pressure* — a single
    pressure value applied to all notes on the channel.  When ``pitch``
    is set this is *polyphonic key pressure* — per-note pressure.
    """

    value: MidiAftertouchValue = Field(..., description="Pressure value (0–127)")
    time: BeatPosition = Field(..., description="Time in beats (≥ 0)")
    channel: MidiChannel = Field(default=0, description="MIDI channel (0–15)")
    pitch: MidiPitch | None = Field(
        default=None,
        description="Note number for poly aftertouch (0–127); None = channel pressure",
    )


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
