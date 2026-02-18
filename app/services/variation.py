"""
Variation Service for the Stori Maestro.

Computes musical variations between base and proposed states,
producing Variation objects that can be reviewed and selectively applied.

Key responsibilities:
1. Match notes between base and proposed states
2. Classify changes as added/removed/modified
3. Group changes into musically meaningful phrases
4. Generate human-readable labels and explanations from Muse
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from typing import Optional

from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)

logger = logging.getLogger(__name__)


# Matching tolerances
TIMING_TOLERANCE_BEATS = 0.05  # Notes within 0.05 beats are considered same timing
PITCH_TOLERANCE = 0  # Exact pitch match required


@dataclass
class NoteMatch:
    """A matched pair of notes (base and proposed)."""
    base_note: Optional[dict]
    proposed_note: Optional[dict]
    base_index: Optional[int]
    proposed_index: Optional[int]
    
    @property
    def is_added(self) -> bool:
        return self.base_note is None and self.proposed_note is not None
    
    @property
    def is_removed(self) -> bool:
        return self.base_note is not None and self.proposed_note is None
    
    @property
    def is_modified(self) -> bool:
        if self.base_note is None or self.proposed_note is None:
            return False
        return self._has_changes()
    
    @property
    def is_unchanged(self) -> bool:
        if self.base_note is None or self.proposed_note is None:
            return False
        return not self._has_changes()
    
    def _has_changes(self) -> bool:
        """Check if there are any differences between base and proposed."""
        if self.base_note is None or self.proposed_note is None:
            return True
        
        # Compare relevant properties
        base_pitch = self.base_note.get("pitch")
        proposed_pitch = self.proposed_note.get("pitch")
        if base_pitch != proposed_pitch:
            return True
        
        base_start = self.base_note.get("start_beat", 0)
        proposed_start = self.proposed_note.get("start_beat", 0)
        if abs(base_start - proposed_start) > TIMING_TOLERANCE_BEATS:
            return True
        
        base_duration = self.base_note.get("duration_beats", 0.5)
        proposed_duration = self.proposed_note.get("duration_beats", 0.5)
        if abs(base_duration - proposed_duration) > TIMING_TOLERANCE_BEATS:
            return True
        
        base_velocity = self.base_note.get("velocity", 100)
        proposed_velocity = self.proposed_note.get("velocity", 100)
        if base_velocity != proposed_velocity:
            return True
        
        return False


def _get_note_key(note: dict) -> tuple[int, float]:
    """Get matching key for a note (pitch, start_beat)."""
    pitch = note.get("pitch", 60)
    start = note.get("start_beat", 0)
    return (pitch, start)


def _notes_match(base_note: dict, proposed_note: dict) -> bool:
    """Check if two notes should be considered the same note."""
    base_pitch = base_note.get("pitch")
    proposed_pitch = proposed_note.get("pitch")
    if base_pitch is None or proposed_pitch is None:
        return False
    if abs(base_pitch - proposed_pitch) > PITCH_TOLERANCE:
        return False
    
    base_start = base_note.get("start_beat", 0)
    proposed_start = proposed_note.get("start_beat", 0)
    
    if abs(base_start - proposed_start) > TIMING_TOLERANCE_BEATS:
        return False
    
    return True


def match_notes(
    base_notes: list[dict],
    proposed_notes: list[dict],
) -> list[NoteMatch]:
    """
    Match notes between base and proposed states.
    
    Uses pitch + timing proximity to match notes. Unmatched base notes
    are marked as removed, unmatched proposed notes as added.
    
    Args:
        base_notes: Original notes
        proposed_notes: Notes after transformation
        
    Returns:
        List of NoteMatch objects representing the alignment
    """
    matches: list[NoteMatch] = []
    
    # Track which notes have been matched
    base_matched = set()
    proposed_matched = set()
    
    # First pass: exact matches (same pitch and timing)
    for bi, base_note in enumerate(base_notes):
        if bi in base_matched:
            continue
            
        for pi, proposed_note in enumerate(proposed_notes):
            if pi in proposed_matched:
                continue
            
            if _notes_match(base_note, proposed_note):
                matches.append(NoteMatch(
                    base_note=base_note,
                    proposed_note=proposed_note,
                    base_index=bi,
                    proposed_index=pi,
                ))
                base_matched.add(bi)
                proposed_matched.add(pi)
                break
    
    # Remaining base notes are removed
    for bi, base_note in enumerate(base_notes):
        if bi not in base_matched:
            matches.append(NoteMatch(
                base_note=base_note,
                proposed_note=None,
                base_index=bi,
                proposed_index=None,
            ))
    
    # Remaining proposed notes are added
    for pi, proposed_note in enumerate(proposed_notes):
        if pi not in proposed_matched:
            matches.append(NoteMatch(
                base_note=None,
                proposed_note=proposed_note,
                base_index=None,
                proposed_index=pi,
            ))
    
    return matches


def _beat_to_bar(beat: float, beats_per_bar: int = 4) -> int:
    """Convert beat position to bar number (1-indexed)."""
    return int(beat // beats_per_bar) + 1


def _generate_bar_label(start_bar: int, end_bar: int) -> str:
    """Generate a human-readable bar range label."""
    if start_bar == end_bar:
        return f"Bar {start_bar}"
    return f"Bars {start_bar}-{end_bar}"


def _detect_change_tags(note_changes: list[NoteChange]) -> list[str]:
    """Detect what types of changes are present in a phrase."""
    tags = set()
    
    for nc in note_changes:
        if nc.change_type == "added":
            tags.add("densityChange")
        elif nc.change_type == "removed":
            tags.add("densityChange")
        elif nc.change_type == "modified":
            if nc.before and nc.after:
                if nc.before.pitch != nc.after.pitch:
                    tags.add("pitchChange")
                    # Check if it's likely a scale/harmony change
                    interval = abs(nc.after.pitch - nc.before.pitch)
                    if interval in (1, 2):  # Semitone or whole tone
                        tags.add("scaleChange")
                    elif interval in (3, 4):  # Minor/major third
                        tags.add("harmonyChange")
                
                if abs(nc.before.start_beat - nc.after.start_beat) > TIMING_TOLERANCE_BEATS:
                    tags.add("rhythmChange")
                
                if abs(nc.before.duration_beats - nc.after.duration_beats) > TIMING_TOLERANCE_BEATS:
                    tags.add("articulationChange")
                
                if nc.before.velocity != nc.after.velocity:
                    tags.add("velocityChange")
                
                # Register change detection
                if abs(nc.before.pitch - nc.after.pitch) >= 12:
                    tags.add("registerChange")
    
    return sorted(tags)


class VariationService:
    """
    Service for computing musical variations proposed by Muse.
    
    Takes base and proposed musical states, identifies changes,
    and organizes them into reviewable phrases.
    
    Usage:
        service = VariationService()
        variation = service.compute_variation(
            base_notes=original_notes,
            proposed_notes=transformed_notes,
            region_id="region-123",
            track_id="track-456",
            intent="make the melody darker",
        )
    """
    
    def __init__(
        self,
        bars_per_phrase: int = 4,
        beats_per_bar: int = 4,
    ):
        """
        Initialize the variation service.
        
        Args:
            bars_per_phrase: Number of bars to group into each phrase
            beats_per_bar: Time signature (beats per bar)
        """
        self.bars_per_phrase = bars_per_phrase
        self.beats_per_bar = beats_per_bar
    
    def compute_variation(
        self,
        base_notes: list[dict],
        proposed_notes: list[dict],
        region_id: str,
        track_id: str,
        intent: str,
        explanation: Optional[str] = None,
        variation_id: Optional[str] = None,
    ) -> Variation:
        """
        Compute a Variation between base and proposed note states.
        
        Analyzes the musical changes Muse is proposing and organizes them
        into independently reviewable phrases.
        
        Args:
            base_notes: Original notes in the region
            proposed_notes: Notes after Muse's transformation
            region_id: ID of the affected region
            track_id: ID of the affected track
            intent: User intent that triggered the transformation
            explanation: Optional Muse explanation of changes
            variation_id: Optional pre-generated variation ID
            
        Returns:
            A Variation object with phrases grouped by bar range
        """
        variation_id = variation_id or str(uuid.uuid4())
        
        # Match notes between states
        matches = match_notes(base_notes, proposed_notes)
        
        # Filter to only changes (not unchanged)
        changes = [m for m in matches if not m.is_unchanged]
        
        if not changes:
            # No changes - return empty variation
            logger.info(f"No changes detected for variation {variation_id[:8]}")
            return Variation(
                variation_id=variation_id,
                intent=intent,
                ai_explanation=explanation,
                affected_tracks=[track_id],
                affected_regions=[region_id],
                beat_range=(0.0, 0.0),
                phrases=[],
            )
        
        # Compute time range from changes
        min_beat = float("inf")
        max_beat = float("-inf")
        
        for match in changes:
            note = match.base_note or match.proposed_note
            if note:
                start = note.get("start_beat", 0)
                dur = note.get("duration_beats", 0.5)
                min_beat = min(min_beat, start)
                max_beat = max(max_beat, start + dur)
        
        if min_beat == float("inf"):
            min_beat = 0.0
        if max_beat == float("-inf"):
            max_beat = 0.0
        
        # Group changes into phrases by bar range
        phrases = self._group_into_phrases(
            changes=changes,
            region_id=region_id,
            track_id=track_id,
        )
        
        logger.info(
            f"Computed variation {variation_id[:8]}: "
            f"{len(changes)} changes in {len(phrases)} phrases"
        )
        
        return Variation(
            variation_id=variation_id,
            intent=intent,
            ai_explanation=explanation,
            affected_tracks=[track_id],
            affected_regions=[region_id],
            beat_range=(min_beat, max_beat),
            phrases=phrases,
        )
    
    def _group_into_phrases(
        self,
        changes: list[NoteMatch],
        region_id: str,
        track_id: str,
    ) -> list[Phrase]:
        """Group note changes into musical phrases by bar range."""
        if not changes:
            return []
        
        # Group by bar range
        beats_per_phrase = self.bars_per_phrase * self.beats_per_bar
        phrase_groups: dict[int, list[NoteMatch]] = {}
        
        for match in changes:
            note = match.base_note or match.proposed_note
            if note:
                start = note.get("start_beat", 0)
                phrase_index = int(start // beats_per_phrase)
                
                if phrase_index not in phrase_groups:
                    phrase_groups[phrase_index] = []
                phrase_groups[phrase_index].append(match)
        
        # Create phrases
        phrases = []
        for phrase_index in sorted(phrase_groups.keys()):
            group = phrase_groups[phrase_index]
            
            # Calculate bar range for label
            start_beat = phrase_index * beats_per_phrase
            end_beat = start_beat + beats_per_phrase
            start_bar = _beat_to_bar(start_beat, self.beats_per_bar)
            end_bar = _beat_to_bar(end_beat - 0.01, self.beats_per_bar)  # -0.01 to avoid off-by-one
            
            # Convert matches to NoteChanges
            note_changes = []
            for match in group:
                note_change = self._match_to_note_change(match)
                note_changes.append(note_change)
            
            # Detect change tags
            tags = _detect_change_tags(note_changes)
            
            phrase = Phrase(
                phrase_id=str(uuid.uuid4()),
                track_id=track_id,
                region_id=region_id,
                start_beat=start_beat,
                end_beat=end_beat,
                label=_generate_bar_label(start_bar, end_bar),
                note_changes=note_changes,
                tags=tags,
            )
            phrases.append(phrase)
        
        return phrases
    
    def _match_to_note_change(self, match: NoteMatch) -> NoteChange:
        """Convert a NoteMatch to a NoteChange."""
        note_id = str(uuid.uuid4())
        
        if match.is_added and match.proposed_note is not None:
            return NoteChange(
                note_id=note_id,
                change_type="added",
                before=None,
                after=MidiNoteSnapshot.from_note_dict(match.proposed_note),
            )
        elif match.is_removed and match.base_note is not None:
            return NoteChange(
                note_id=note_id,
                change_type="removed",
                before=MidiNoteSnapshot.from_note_dict(match.base_note),
                after=None,
            )
        elif match.base_note is not None and match.proposed_note is not None:
            # Modified
            return NoteChange(
                note_id=note_id,
                change_type="modified",
                before=MidiNoteSnapshot.from_note_dict(match.base_note),
                after=MidiNoteSnapshot.from_note_dict(match.proposed_note),
            )
        raise ValueError("Malformed NoteMatch: missing base_note or proposed_note")
    
    def compute_multi_region_variation(
        self,
        base_regions: dict[str, list[dict]],  # region_id -> notes
        proposed_regions: dict[str, list[dict]],
        track_id: str,
        intent: str,
        explanation: Optional[str] = None,
    ) -> Variation:
        """
        Compute a Variation across multiple regions.
        
        Useful when Muse's proposed changes affect multiple regions at once.
        
        Args:
            base_regions: Mapping of region_id to original notes
            proposed_regions: Mapping of region_id to Muse's proposed notes
            track_id: ID of the affected track
            intent: User intent
            explanation: Optional Muse explanation
            
        Returns:
            A Variation with phrases from all regions
        """
        variation_id = str(uuid.uuid4())
        all_phrases = []
        affected_regions = []
        
        min_beat = float("inf")
        max_beat = float("-inf")
        
        # Process each region
        all_region_ids = set(base_regions.keys()) | set(proposed_regions.keys())
        
        for region_id in all_region_ids:
            base_notes = base_regions.get(region_id, [])
            proposed_notes = proposed_regions.get(region_id, [])
            
            matches = match_notes(base_notes, proposed_notes)
            changes = [m for m in matches if not m.is_unchanged]
            
            if changes:
                affected_regions.append(region_id)
                
                # Update time range
                for match in changes:
                    note = match.base_note or match.proposed_note
                    if note:
                        start = note.get("start_beat", 0)
                        dur = note.get("duration_beats", 0.5)
                        min_beat = min(min_beat, start)
                        max_beat = max(max_beat, start + dur)
                
                # Create phrases for this region
                phrases = self._group_into_phrases(
                    changes=changes,
                    region_id=region_id,
                    track_id=track_id,
                )
                all_phrases.extend(phrases)
        
        if min_beat == float("inf"):
            min_beat = 0.0
        if max_beat == float("-inf"):
            max_beat = 0.0
        
        return Variation(
            variation_id=variation_id,
            intent=intent,
            ai_explanation=explanation,
            affected_tracks=[track_id],
            affected_regions=affected_regions,
            beat_range=(min_beat, max_beat),
            phrases=all_phrases,
        )


# Singleton instance
_variation_service: Optional[VariationService] = None


def get_variation_service() -> VariationService:
    """Get the singleton VariationService instance."""
    global _variation_service
    if _variation_service is None:
        _variation_service = VariationService()
    return _variation_service
