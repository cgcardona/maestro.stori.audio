"""Project state snapshot schema.

Validates the project payload sent by the Stori macOS app.
Uses extra="allow" so unknown fields from newer FE versions
pass through without breaking older backends.

Critical fields are typed and validated.  Non-critical fields
are Optional with sensible defaults.
"""

from __future__ import annotations


from pydantic import ConfigDict, Field

from app.models.base import CamelModel


class NoteSnapshot(CamelModel):
    """Single MIDI note in a region."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    pitch: int = Field(ge=0, le=127)
    start_beat: float = Field(ge=0)
    duration_beats: float = Field(gt=0)
    velocity: int = Field(default=100, ge=0, le=127)
    channel: int = Field(default=0, ge=0, le=15)


class RegionSnapshot(CamelModel):
    """Single MIDI region in a track."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    start_beat: float = Field(default=0.0, ge=0)
    duration_beats: float | None = Field(default=None, gt=0)
    note_count: int | None = None
    notes: list[NoteSnapshot] | None = None


class TrackSnapshot(CamelModel):
    """Single track in a project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    gm_program: int | None = Field(default=None, ge=0, le=127)
    drum_kit_id: str | None = None
    is_drums: bool | None = None
    volume: float | None = Field(default=None, ge=0.0, le=1.5)
    pan: float | None = Field(default=None, ge=0.0, le=1.0)
    muted: bool | None = None
    solo: bool | None = None
    color: str | None = None
    icon: str | None = None
    regions: list[RegionSnapshot] = Field(default_factory=list)


class BusSnapshot(CamelModel):
    """Single bus in a project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None


class ProjectSnapshot(CamelModel):
    """Project state snapshot from the Stori macOS app.

    Validated but permissive: extra fields allowed, most fields optional.
    Only ``id`` is required — an empty project with just an ID is valid.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    tempo: float | None = Field(default=None, ge=40, le=240)
    key: str | None = None
    time_signature: str | None = None
    schema_version: int | None = None
    tracks: list[TrackSnapshot] = Field(default_factory=list)
    buses: list[BusSnapshot] = Field(default_factory=list)
