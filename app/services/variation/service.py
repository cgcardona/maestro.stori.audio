"""VariationService — compute musical variations between base and proposed states."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
)
from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)
from app.services.variation.note_matching import NoteMatch, match_notes
from app.services.variation.labels import (
    _beat_to_bar,
    _generate_bar_label,
    _detect_change_tags,
)

logger = logging.getLogger(__name__)


class VariationService:
    """Service for computing musical variations proposed by Muse.

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
        base_notes: list[NoteDict],
        proposed_notes: list[NoteDict],
        region_id: str,
        track_id: str,
        intent: str,
        explanation: str | None = None,
        variation_id: str | None = None,
        region_start_beat: float = 0.0,
        cc_events: list[CCEventDict] | None = None,
        pitch_bends: list[PitchBendDict] | None = None,
        aftertouch: list[AftertouchDict] | None = None,
    ) -> Variation:
        """Compute a Variation between base and proposed note states.

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
            region_start_beat: Absolute beat position of the region in the
                project timeline.  Added to phrase start_beat/end_beat so
                they represent absolute project positions.

        Returns:
            A Variation object with phrases grouped by bar range
        """
        variation_id = variation_id or str(uuid.uuid4())

        matches = match_notes(base_notes, proposed_notes)
        changes = [m for m in matches if not m.is_unchanged]

        if not changes:
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

        min_beat = float("inf")
        max_beat = float("-inf")

        for match in changes:
            note = match.base_note or match.proposed_note
            if note:
                start = note.get("start_beat", 0) + region_start_beat
                dur = note.get("duration_beats", 0.5)
                min_beat = min(min_beat, start)
                max_beat = max(max_beat, start + dur)

        if min_beat == float("inf"):
            min_beat = 0.0
        if max_beat == float("-inf"):
            max_beat = 0.0

        phrases = self._group_into_phrases(
            changes=changes,
            region_id=region_id,
            track_id=track_id,
            region_start_beat=region_start_beat,
            cc_events=cc_events or [],
            pitch_bends=pitch_bends or [],
            aftertouch=aftertouch or [],
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
        region_start_beat: float = 0.0,
        cc_events: list[CCEventDict] | None = None,
        pitch_bends: list[PitchBendDict] | None = None,
        aftertouch: list[AftertouchDict] | None = None,
    ) -> list[Phrase]:
        """Group note changes into musical phrases by bar range.

        Args:
            changes: Note matches representing additions/removals/modifications.
            region_id: Region the changes belong to.
            track_id: Track the region belongs to.
            region_start_beat: Absolute beat position of the region in the
                project timeline.  Added to the phrase's ``start_beat`` /
                ``end_beat`` so the frontend receives absolute project
                positions.  Note start_beat values inside ``noteChanges``
                remain region-relative (matching standard MIDI region storage).
            cc_events: MIDI CC events for this region (region-relative beats).
            pitch_bends: Pitch bend events for this region (region-relative beats).
            aftertouch: Channel/poly aftertouch events (region-relative beats).
        """
        if not changes:
            return []

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

        cc_by_phrase: dict[int, list[CCEventDict]] = {}
        for cc_ev in (cc_events or []):
            beat = cc_ev.get("beat", 0)
            idx = int(beat // beats_per_phrase)
            cc_by_phrase.setdefault(idx, []).append(cc_ev)

        pb_by_phrase: dict[int, list[PitchBendDict]] = {}
        for pb_ev in (pitch_bends or []):
            beat = pb_ev.get("beat", 0)
            idx = int(beat // beats_per_phrase)
            pb_by_phrase.setdefault(idx, []).append(pb_ev)

        at_by_phrase: dict[int, list[AftertouchDict]] = {}
        for at_ev in (aftertouch or []):
            beat = at_ev.get("beat", 0)
            idx = int(beat // beats_per_phrase)
            at_by_phrase.setdefault(idx, []).append(at_ev)

        for idx in set(cc_by_phrase) | set(pb_by_phrase) | set(at_by_phrase):
            if idx not in phrase_groups:
                phrase_groups[idx] = []

        phrases = []
        for phrase_index in sorted(phrase_groups.keys()):
            group = phrase_groups[phrase_index]

            rel_start = phrase_index * beats_per_phrase
            rel_end = rel_start + beats_per_phrase

            abs_start = rel_start + region_start_beat
            abs_end = rel_end + region_start_beat

            start_bar = _beat_to_bar(abs_start, self.beats_per_bar)
            end_bar = _beat_to_bar(abs_end - 0.01, self.beats_per_bar)

            note_changes = []
            for match in group:
                note_change = self._match_to_note_change(match)
                note_changes.append(note_change)

            tags = _detect_change_tags(note_changes)

            controller_changes: list[dict[str, Any]] = []
            for cc_ev in cc_by_phrase.get(phrase_index, []):
                controller_changes.append({
                    "kind": "cc",
                    "cc": cc_ev.get("cc"),
                    "beat": cc_ev.get("beat", 0),
                    "value": cc_ev.get("value", 0),
                })
            for pb_ev in pb_by_phrase.get(phrase_index, []):
                controller_changes.append({
                    "kind": "pitch_bend",
                    "beat": pb_ev.get("beat", 0),
                    "value": pb_ev.get("value", 0),
                })
            for at_ev in at_by_phrase.get(phrase_index, []):
                entry: dict[str, Any] = {
                    "kind": "aftertouch",
                    "beat": at_ev.get("beat", 0),
                    "value": at_ev.get("value", 0),
                }
                if "pitch" in at_ev:
                    entry["pitch"] = at_ev["pitch"]
                controller_changes.append(entry)

            phrase = Phrase(
                phrase_id=str(uuid.uuid4()),
                track_id=track_id,
                region_id=region_id,
                start_beat=abs_start,
                end_beat=abs_end,
                label=_generate_bar_label(start_bar, end_bar),
                note_changes=note_changes,
                controller_changes=controller_changes,
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
            return NoteChange(
                note_id=note_id,
                change_type="modified",
                before=MidiNoteSnapshot.from_note_dict(match.base_note),
                after=MidiNoteSnapshot.from_note_dict(match.proposed_note),
            )
        raise ValueError("Malformed NoteMatch: missing base_note or proposed_note")

    def compute_multi_region_variation(
        self,
        base_regions: dict[str, list[NoteDict]],
        proposed_regions: dict[str, list[NoteDict]],
        track_regions: dict[str, str],
        intent: str,
        explanation: str | None = None,
        region_start_beats: dict[str, float] | None = None,
        region_cc: dict[str, list[CCEventDict]] | None = None,
        region_pitch_bends: dict[str, list[PitchBendDict]] | None = None,
        region_aftertouch: dict[str, list[AftertouchDict]] | None = None,
    ) -> Variation:
        """Compute a Variation across multiple regions, each potentially on a different track.

        Args:
            base_regions: Mapping of region_id to original notes
            proposed_regions: Mapping of region_id to Muse's proposed notes
            track_regions: Mapping of region_id to its server-assigned track_id.
                           Every region must have an entry here so phrases carry the
                           correct trackId (not a single shared value).
            intent: User intent
            explanation: Optional Muse explanation
            region_start_beats: Mapping of region_id to the region's absolute start
                beat in the project timeline.  Used to convert phrase start_beat /
                end_beat to absolute project positions.

        Returns:
            A Variation with phrases across all regions, each carrying the right trackId
        """
        variation_id = str(uuid.uuid4())
        all_phrases = []
        affected_regions = []
        affected_track_set: set[str] = set()
        _region_offsets = region_start_beats or {}
        _region_cc = region_cc or {}
        _region_pb = region_pitch_bends or {}
        _region_at = region_aftertouch or {}

        min_beat = float("inf")
        max_beat = float("-inf")

        all_region_ids = set(base_regions.keys()) | set(proposed_regions.keys())

        for region_id in all_region_ids:
            base_notes = base_regions.get(region_id, [])
            proposed_notes = proposed_regions.get(region_id, [])

            matches = match_notes(base_notes, proposed_notes)
            changes = [m for m in matches if not m.is_unchanged]

            region_track_id = track_regions.get(region_id, "unknown")
            region_offset = _region_offsets.get(region_id, 0.0)
            r_cc = _region_cc.get(region_id, [])
            r_pb = _region_pb.get(region_id, [])
            r_at = _region_at.get(region_id, [])

            has_content = bool(changes) or bool(r_cc) or bool(r_pb) or bool(r_at)
            if has_content:
                affected_regions.append(region_id)
                affected_track_set.add(region_track_id)

                for match in changes:
                    note = match.base_note or match.proposed_note
                    if note:
                        start = note.get("start_beat", 0) + region_offset
                        dur = note.get("duration_beats", 0.5)
                        min_beat = min(min_beat, start)
                        max_beat = max(max_beat, start + dur)

                phrases = self._group_into_phrases(
                    changes=changes,
                    region_id=region_id,
                    track_id=region_track_id,
                    region_start_beat=region_offset,
                    cc_events=r_cc,
                    pitch_bends=r_pb,
                    aftertouch=r_at,
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
            affected_tracks=list(affected_track_set),
            affected_regions=affected_regions,
            beat_range=(min_beat, max_beat),
            phrases=all_phrases,
        )


# Singleton instance
_variation_service: VariationService | None = None


def get_variation_service() -> VariationService:
    """Get the singleton VariationService instance."""
    global _variation_service
    if _variation_service is None:
        _variation_service = VariationService()
    return _variation_service
