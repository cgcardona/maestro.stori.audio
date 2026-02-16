"""
Variation models for the Stori Composer.

A Variation represents a proposed musical change that can be reviewed
before being applied. It consists of phrases (grouped changes) that can
be independently accepted or rejected.

Key concepts:
- NoteChange: A single note change (added/removed/modified)
- Phrase: A group of related note changes (e.g., same bar range)
- Variation: The complete proposal with all phrases
"""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class MidiNoteSnapshot(BaseModel):
    """
    Snapshot of a MIDI note's properties at a point in time.
    
    Used in NoteVariation to capture before/after state.
    """
    pitch: int = Field(..., ge=0, le=127, description="MIDI note number (0-127)")
    start_beat: float = Field(..., ge=0, description="Start position in beats")
    duration_beats: float = Field(..., gt=0, description="Duration in beats")
    velocity: int = Field(default=100, ge=0, le=127, description="Note velocity (0-127)")
    channel: int = Field(default=0, ge=0, le=15, description="MIDI channel (0-15)")
    
    @classmethod
    def from_note_dict(cls, note: dict) -> "MidiNoteSnapshot":
        """Create a snapshot from a note dictionary.

        Expects snake_case keys: ``start_beat``, ``duration_beats``.
        """
        return cls(
            pitch=note.get("pitch", 60),
            start_beat=note.get("start_beat", 0),
            duration_beats=note.get("duration_beats", 0.5),
            velocity=note.get("velocity", 100),
            channel=note.get("channel", 0),
        )
    
    def to_note_dict(self) -> dict:
        """Convert to a note dictionary (snake_case — matches model_dump format)."""
        return {
            "pitch": self.pitch,
            "start_beat": self.start_beat,
            "duration_beats": self.duration_beats,
            "velocity": self.velocity,
            "channel": self.channel,
        }


# Change type literals
ChangeType = Literal["added", "removed", "modified"]


class NoteChange(BaseModel):
    """
    A single note change within a variation.
    
    Represents one of:
    - added: A new note (before=None, after=note)
    - removed: A deleted note (before=note, after=None)
    - modified: A changed note (before=original, after=new)
    """
    note_id: str = Field(..., description="Unique identifier for this note change")
    change_type: ChangeType = Field(..., description="Type of change: added, removed, or modified")
    before: Optional[MidiNoteSnapshot] = Field(
        default=None,
        description="Original note state (None for 'added')"
    )
    after: Optional[MidiNoteSnapshot] = Field(
        default=None,
        description="Proposed note state (None for 'removed')"
    )
    
    def model_post_init(self, __context) -> None:
        """Validate that before/after match change_type."""
        if self.change_type == "added" and self.before is not None:
            raise ValueError("'added' notes must have before=None")
        if self.change_type == "removed" and self.after is not None:
            raise ValueError("'removed' notes must have after=None")
        if self.change_type == "modified":
            if self.before is None or self.after is None:
                raise ValueError("'modified' notes must have both before and after")


# Variation tags for categorizing changes
VariationTag = Literal[
    "pitchChange",
    "rhythmChange", 
    "velocityChange",
    "harmonyChange",
    "scaleChange",
    "densityChange",
    "registerChange",
    "articulationChange",
]


class Phrase(BaseModel):
    """
    A musical phrase representing a group of related note changes.
    
    Phrases are independently reviewable and appliable. They group
    changes by musical context (bar range, region) so musicians can
    accept or reject changes phrase by phrase.
    """
    phrase_id: str = Field(..., description="Unique identifier for this phrase")
    track_id: str = Field(..., description="Track containing the changes")
    region_id: str = Field(..., description="Region containing the changes")
    
    start_beat: float = Field(..., ge=0, description="Start beat of the affected range")
    end_beat: float = Field(..., gt=0, description="End beat of the affected range")
    label: str = Field(..., description="Human-readable label (e.g., 'Bars 5-8')")
    
    note_changes: list[NoteChange] = Field(
        default_factory=list,
        description="List of note changes in this phrase"
    )
    controller_changes: list[dict] = Field(
        default_factory=list,
        description="List of MIDI CC changes (future use)"
    )
    
    explanation: Optional[str] = Field(
        default=None,
        description="Muse-generated explanation of the changes"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags categorizing the type of changes"
    )
    
    @property
    def added_count(self) -> int:
        """Count of added notes."""
        return sum(1 for nc in self.note_changes if nc.change_type == "added")
    
    @property
    def removed_count(self) -> int:
        """Count of removed notes."""
        return sum(1 for nc in self.note_changes if nc.change_type == "removed")
    
    @property
    def modified_count(self) -> int:
        """Count of modified notes."""
        return sum(1 for nc in self.note_changes if nc.change_type == "modified")
    
    @property
    def is_empty(self) -> bool:
        """Check if phrase has no changes."""
        return len(self.note_changes) == 0 and len(self.controller_changes) == 0


class Variation(BaseModel):
    """
    A complete variation proposal from Muse.
    
    Contains all proposed musical changes organized into phrases, along with
    metadata about the intent and affected regions. Each phrase can be
    independently reviewed and accepted.
    """
    variation_id: str = Field(..., description="Unique identifier for this variation")
    intent: str = Field(..., description="The user intent that generated this variation")
    ai_explanation: Optional[str] = Field(
        default=None,
        description="Muse-generated summary of what the variation does"
    )
    
    affected_tracks: list[str] = Field(
        default_factory=list,
        description="List of track IDs affected by this variation"
    )
    affected_regions: list[str] = Field(
        default_factory=list,
        description="List of region IDs affected by this variation"
    )
    beat_range: tuple[float, float] = Field(
        ...,
        description="(start_beat, end_beat) of affected musical range"
    )
    
    phrases: list[Phrase] = Field(
        default_factory=list,
        description="List of musical phrases containing the changes"
    )
    
    @property
    def total_changes(self) -> int:
        """Total number of note changes across all phrases."""
        return sum(len(p.note_changes) for p in self.phrases)
    
    @property
    def note_counts(self) -> dict[str, int]:
        """
        Get counts of added, removed, and modified notes across all phrases.
        
        Returns:
            Dictionary with keys: added, removed, modified
        """
        added = 0
        removed = 0
        modified = 0
        
        for phrase in self.phrases:
            added += phrase.added_count
            removed += phrase.removed_count
            modified += phrase.modified_count
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }
    
    @property
    def is_empty(self) -> bool:
        """Check if variation has no changes."""
        return all(p.is_empty for p in self.phrases)
    
    def get_phrase(self, phrase_id: str) -> Optional[Phrase]:
        """Get a phrase by ID."""
        for phrase in self.phrases:
            if phrase.phrase_id == phrase_id:
                return phrase
        return None
    
    def get_accepted_notes(self, accepted_phrase_ids: list[str]) -> list[dict]:
        """
        Get the proposed notes from accepted phrases.
        
        Returns notes in dictionary format ready for application.
        """
        notes = []
        for phrase in self.phrases:
            if phrase.phrase_id in accepted_phrase_ids:
                for nc in phrase.note_changes:
                    if nc.change_type in ("added", "modified") and nc.after:
                        notes.append(nc.after.to_note_dict())
        return notes
    
    def get_removed_note_ids(self, accepted_phrase_ids: list[str]) -> list[str]:
        """
        Get IDs of notes to remove from accepted phrases.
        
        Returns note_ids for removed and modified notes.
        """
        note_ids = []
        for phrase in self.phrases:
            if phrase.phrase_id in accepted_phrase_ids:
                for nc in phrase.note_changes:
                    if nc.change_type in ("removed", "modified"):
                        note_ids.append(nc.note_id)
        return note_ids


# Response models for API endpoints

class ProposeVariationResponse(BaseModel):
    """
    Immediate response from POST /variation/propose (spec-compliant).
    
    Returns variation metadata immediately, before streaming starts.
    """
    variation_id: str = Field(..., description="UUID of the variation")
    project_id: str = Field(..., description="UUID of the project")
    base_state_id: str = Field(..., description="Base state version used")
    intent: str = Field(..., description="User intent")
    ai_explanation: Optional[str] = Field(
        default=None,
        description="Muse-generated explanation (may be null initially)"
    )
    stream_url: str = Field(
        ...,
        description="URL for SSE stream to receive phrases"
    )


class CommitVariationResponse(BaseModel):
    """
    Response from POST /variation/commit (spec-compliant).
    
    Returns details about applied phrases and updated regions.
    """
    project_id: str = Field(..., description="UUID of the project")
    new_state_id: str = Field(..., description="New state version after commit")
    applied_phrase_ids: list[str] = Field(
        ...,
        description="IDs of phrases that were applied"
    )
    undo_label: str = Field(
        ...,
        description="Label for undo stack (e.g., 'Accept Variation: make that minor')"
    )
    updated_regions: list[dict] = Field(
        default_factory=list,
        description=(
            "Full MIDI state for each affected region after commit. "
            "Each entry: {region_id, track_id, notes: [{pitch, start_beat, duration_beats, velocity, channel}]}."
        ),
    )
