"""Project state snapshot schema.

Validates the project payload sent by the Stori macOS app.
Uses extra="allow" so unknown fields from newer FE versions
pass through without breaking older backends.

Critical fields are typed and validated.  Non-critical fields
are Optional with sensible defaults.
"""

from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict, Field

from app.models.base import CamelModel


class NoteSnapshot(CamelModel):
    """Single MIDI note in a region."""

    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    pitch: int = Field(ge=0, le=127)
    start_beat: float = Field(ge=0)
    duration_beats: float = Field(gt=0)
    velocity: int = Field(default=100, ge=0, le=127)
    channel: int = Field(default=0, ge=0, le=15)


class RegionSnapshot(CamelModel):
    """Single MIDI region in a track."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None
    start_beat: float = Field(default=0.0, ge=0)
    duration_beats: Optional[float] = Field(default=None, gt=0)
    note_count: Optional[int] = None
    notes: Optional[list[NoteSnapshot]] = None


class TrackSnapshot(CamelModel):
    """Single track in a project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None
    gm_program: Optional[int] = Field(default=None, ge=0, le=127)
    drum_kit_id: Optional[str] = None
    is_drums: Optional[bool] = None
    volume: Optional[float] = Field(default=None, ge=0.0, le=1.5)
    pan: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    muted: Optional[bool] = None
    solo: Optional[bool] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    regions: list[RegionSnapshot] = Field(default_factory=list)


class BusSnapshot(CamelModel):
    """Single bus in a project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None


class ProjectSnapshot(CamelModel):
    """Project state snapshot from the Stori macOS app.

    Validated but permissive: extra fields allowed, most fields optional.
    Only ``id`` is required — an empty project with just an ID is valid.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None
    tempo: Optional[float] = Field(default=None, ge=40, le=240)
    key: Optional[str] = None
    time_signature: Optional[str] = None
    schema_version: Optional[int] = None
    tracks: list[TrackSnapshot] = Field(default_factory=list)
    buses: list[BusSnapshot] = Field(default_factory=list)
