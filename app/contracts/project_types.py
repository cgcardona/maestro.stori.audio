"""Typed structures for DAW project state snapshots.

The macOS Stori DAW sends project state as JSON on every request.
These TypedDicts describe the exact shape so callers can access
fields without ``dict[str, Any]``.

All fields are optional (``total=False``) because the DAW may omit
any field.  Wire format is camelCase.
"""
from __future__ import annotations

from typing_extensions import TypedDict

from app.contracts.json_types import NoteDict


class ProjectRegion(TypedDict, total=False):
    """A MIDI region inside a track."""

    id: str
    name: str
    startBeat: float
    durationBeats: float
    noteCount: int
    notes: list[NoteDict]


class MixerSettingsDict(TypedDict, total=False):
    """Mixer settings for a track (DAW wire format)."""

    volume: float
    pan: float
    isMuted: bool
    isSolo: bool


class AutomationLaneDict(TypedDict, total=False):
    """An automation lane on a track."""

    id: str
    parameter: str
    points: list[dict[str, float]]


class ProjectTrack(TypedDict, total=False):
    """A track in the DAW project.

    The track's own identifier is ``id``.  ``trackId`` is reserved for
    foreign-key references in tool-call params and event payloads
    (e.g. ``stori_add_midi_region(trackId=…)``).

    ``gmProgram`` and ``drumKitId`` are nullable (sent as ``null`` when
    not applicable, e.g. ``gmProgram`` is ``null`` on a drum track).
    """

    id: str
    name: str
    gmProgram: int | None
    drumKitId: str | None
    isDrums: bool
    volume: float
    pan: float
    muted: bool
    solo: bool
    color: str
    icon: str
    role: str
    regions: list[ProjectRegion]
    mixerSettings: MixerSettingsDict
    automationLanes: list[AutomationLaneDict]


class BusDict(TypedDict, total=False):
    """An audio bus."""

    id: str
    name: str


class TimeSignatureDict(TypedDict):
    """Time signature in structured form (sent by some DAW versions)."""

    numerator: int
    denominator: int


class ProjectContext(TypedDict, total=False):
    """Full project snapshot from the Stori macOS app.

    This is the canonical type for every ``project_context`` parameter
    in the codebase.  ``timeSignature`` may arrive as ``"4/4"`` (string)
    or ``{"numerator": 4, "denominator": 4}`` (dict).
    """

    id: str
    name: str
    tempo: int
    key: str
    timeSignature: str | TimeSignatureDict
    tracks: list[ProjectTrack]
    buses: list[BusDict]
