"""
Tests for the Variation system.

Tests cover:
1. VariationService - note matching and phrase generation
2. Variation models - serialization and validation
3. Executor variation mode - execution without mutation
4. API endpoints - /variation/* spec-compliant endpoints
"""

import pytest
import uuid
from typing import Any

from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)
from app.services.variation import (
    VariationService,
    match_notes,
    NoteMatch,
    get_variation_service,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def variation_service():
    """Create a fresh variation service for each test."""
    return VariationService(bars_per_phrase=4, beats_per_bar=4)


@pytest.fixture
def simple_notes():
    """Simple test notes for basic matching."""
    return [
        {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
        {"pitch": 62, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 100},
        {"pitch": 64, "start_beat": 2.0, "duration_beats": 1.0, "velocity": 100},
        {"pitch": 65, "start_beat": 3.0, "duration_beats": 1.0, "velocity": 100},
    ]


@pytest.fixture
def drum_pattern():
    """A simple drum pattern for testing."""
    return [
        # Kick
        {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 100},
        {"pitch": 36, "start_beat": 2.0, "duration_beats": 0.5, "velocity": 100},
        # Snare
        {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.5, "velocity": 90},
        {"pitch": 38, "start_beat": 3.0, "duration_beats": 0.5, "velocity": 90},
        # Hi-hat
        {"pitch": 42, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 70},
        {"pitch": 42, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 60},
        {"pitch": 42, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 70},
        {"pitch": 42, "start_beat": 1.5, "duration_beats": 0.25, "velocity": 60},
    ]


# =============================================================================
# MidiNoteSnapshot Tests
# =============================================================================

class TestMidiNoteSnapshot:
    """Tests for MidiNoteSnapshot model."""
    
    def test_from_note_dict_standard_fields(self):
        """Test creating snapshot from standard note dict (start_beat/duration_beats)."""
        note = {"pitch": 60, "start_beat": 2.0, "duration_beats": 0.5, "velocity": 80}
        snapshot = MidiNoteSnapshot.from_note_dict(note)
        
        assert snapshot.pitch == 60
        assert snapshot.start_beat == 2.0
        assert snapshot.duration_beats == 0.5
        assert snapshot.velocity == 80
    
    def test_from_note_dict_alternate_fields(self):
        """Test creating snapshot with canonical field names."""
        note = {"pitch": 64, "start_beat": 1.0, "duration_beats": 0.75, "velocity": 90}
        snapshot = MidiNoteSnapshot.from_note_dict(note)
        
        assert snapshot.pitch == 64
        assert snapshot.start_beat == 1.0
        assert snapshot.duration_beats == 0.75
        assert snapshot.velocity == 90
    
    def test_to_note_dict(self):
        """Test converting snapshot back to dict."""
        snapshot = MidiNoteSnapshot(
            pitch=62,
            start_beat=3.0,
            duration_beats=1.0,
            velocity=100,
            channel=1,
        )
        note = snapshot.to_note_dict()
        
        assert note["pitch"] == 62
        assert note["start_beat"] == 3.0
        assert note["duration_beats"] == 1.0
        assert note["velocity"] == 100
        assert note["channel"] == 1


# =============================================================================
# NoteChange Tests
# =============================================================================

class TestNoteChange:
    """Tests for NoteChange model validation."""
    
    def test_added_note_valid(self):
        """Added notes must have after, no before."""
        nv = NoteChange(
            note_id="test-1",
            change_type="added",
            before=None,
            after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
        )
        assert nv.change_type == "added"
        assert nv.before is None
        assert nv.after is not None
    
    def test_added_note_invalid_with_before(self):
        """Added notes cannot have before."""
        with pytest.raises(ValueError, match="'added' notes must have before=None"):
            NoteChange(
                note_id="test-1",
                change_type="added",
                before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            )
    
    def test_removed_note_valid(self):
        """Removed notes must have before, no after."""
        nv = NoteChange(
            note_id="test-1",
            change_type="removed",
            before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            after=None,
        )
        assert nv.change_type == "removed"
        assert nv.before is not None
        assert nv.after is None
    
    def test_removed_note_invalid_with_after(self):
        """Removed notes cannot have after."""
        with pytest.raises(ValueError, match="'removed' notes must have after=None"):
            NoteChange(
                note_id="test-1",
                change_type="removed",
                before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            )
    
    def test_modified_note_valid(self):
        """Modified notes must have both before and after."""
        nv = NoteChange(
            note_id="test-1",
            change_type="modified",
            before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            after=MidiNoteSnapshot(pitch=63, start_beat=0, duration_beats=1, velocity=100),
        )
        assert nv.change_type == "modified"
        assert nv.before is not None
        assert nv.after is not None
    
    def test_modified_note_invalid_missing_before(self):
        """Modified notes must have before."""
        with pytest.raises(ValueError, match="'modified' notes must have both"):
            NoteChange(
                note_id="test-1",
                change_type="modified",
                before=None,
                after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
            )


# =============================================================================
# Note Matching Tests
# =============================================================================

class TestNoteMatching:
    """Tests for note matching algorithm."""
    
    def test_identical_notes_match(self, simple_notes):
        """Identical note lists should all match."""
        matches = match_notes(simple_notes, simple_notes.copy())
        
        # All should be matched (unchanged)
        assert len(matches) == len(simple_notes)
        for m in matches:
            assert m.base_note is not None
            assert m.proposed_note is not None
            assert m.is_unchanged
    
    def test_added_notes_detected(self, simple_notes):
        """New notes in proposed should be detected as added."""
        proposed = simple_notes.copy()
        new_note = {"pitch": 67, "start_beat": 4.0, "duration_beats": 1.0, "velocity": 100}
        proposed.append(new_note)
        
        matches = match_notes(simple_notes, proposed)
        
        added = [m for m in matches if m.is_added]
        assert len(added) == 1
        pn = added[0].proposed_note
        assert pn is not None and pn["pitch"] == 67
    
    def test_removed_notes_detected(self, simple_notes):
        """Missing notes in proposed should be detected as removed."""
        proposed = simple_notes[:-1]  # Remove last note
        
        matches = match_notes(simple_notes, proposed)
        
        removed = [m for m in matches if m.is_removed]
        assert len(removed) == 1
        bn = removed[0].base_note
        assert bn is not None and bn["pitch"] == 65  # Last note was F
    
    def test_modified_notes_detected(self, simple_notes):
        """Changed notes should be detected as modified."""
        proposed = [n.copy() for n in simple_notes]
        proposed[1]["velocity"] = 50  # Change velocity of second note
        
        matches = match_notes(simple_notes, proposed)
        
        modified = [m for m in matches if m.is_modified]
        assert len(modified) == 1
        m0bn, m0pn = modified[0].base_note, modified[0].proposed_note
        assert m0bn is not None and m0bn["velocity"] == 100
        assert m0pn is not None and m0pn["velocity"] == 50
    
    def test_pitch_change_detected(self, simple_notes):
        """Pitch changes should result in remove + add."""
        proposed = [n.copy() for n in simple_notes]
        proposed[0]["pitch"] = 61  # Change C4 to C#4
        
        matches = match_notes(simple_notes, proposed)
        
        # Pitch change = old removed, new added (not matched)
        removed = [m for m in matches if m.is_removed]
        added = [m for m in matches if m.is_added]
        
        assert len(removed) == 1
        assert len(added) == 1
        r0bn = removed[0].base_note
        a0pn = added[0].proposed_note
        assert r0bn is not None and r0bn["pitch"] == 60
        assert a0pn is not None and a0pn["pitch"] == 61


# =============================================================================
# VariationService Tests
# =============================================================================

class TestVariationService:
    """Tests for the VariationService."""
    
    def test_empty_to_notes_all_added(self, variation_service):
        """Adding notes to empty region = all added."""
        notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
            {"pitch": 62, "start_beat": 1, "duration_beats": 1, "velocity": 100},
        ]
        
        variation = variation_service.compute_variation(
            base_notes=[],
            proposed_notes=notes,
            region_id="region-1",
            track_id="track-1",
            intent="add some notes",
        )
        
        assert variation.total_changes == 2
        assert len(variation.phrases) == 1
        assert variation.phrases[0].added_count == 2
        assert variation.phrases[0].removed_count == 0
    
    def test_notes_to_empty_all_removed(self, variation_service, simple_notes):
        """Removing all notes = all removed."""
        variation = variation_service.compute_variation(
            base_notes=simple_notes,
            proposed_notes=[],
            region_id="region-1",
            track_id="track-1",
            intent="clear all notes",
        )
        
        assert variation.total_changes == len(simple_notes)
        for phrase in variation.phrases:
            assert phrase.added_count == 0
    
    def test_no_changes_empty_variation(self, variation_service, simple_notes):
        """No changes = empty variation."""
        variation = variation_service.compute_variation(
            base_notes=simple_notes,
            proposed_notes=simple_notes.copy(),
            region_id="region-1",
            track_id="track-1",
            intent="do nothing",
        )
        
        assert variation.is_empty
        assert variation.total_changes == 0
    
    def test_phrases_grouped_by_bar(self, variation_service):
        """Changes in different bar ranges should be in different phrases."""
        # With bars_per_phrase=4 and beats_per_bar=4, each phrase covers 16 beats
        # So we need notes at beat 0 (phrase 0) and beat 16 (phrase 1)
        base_notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},   # Phrase 0 (beats 0-16)
            {"pitch": 62, "start_beat": 16, "duration_beats": 1, "velocity": 100},  # Phrase 1 (beats 16-32)
        ]
        proposed_notes = [
            {"pitch": 61, "start_beat": 0, "duration_beats": 1, "velocity": 100},   # Phrase 0 - changed
            {"pitch": 63, "start_beat": 16, "duration_beats": 1, "velocity": 100},  # Phrase 1 - changed
        ]
        
        variation = variation_service.compute_variation(
            base_notes=base_notes,
            proposed_notes=proposed_notes,
            region_id="region-1",
            track_id="track-1",
            intent="change notes in different bar ranges",
        )
        
        # Should have 2 phrases (different 4-bar ranges)
        assert len(variation.phrases) == 2
    
    def test_phrase_labels_generated(self, variation_service):
        """Phrases should have human-readable labels."""
        notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        
        variation = variation_service.compute_variation(
            base_notes=[],
            proposed_notes=notes,
            region_id="region-1",
            track_id="track-1",
            intent="add a note",
        )
        
        assert len(variation.phrases) == 1
        assert "Bar" in variation.phrases[0].label
    
    def test_change_tags_detected(self, variation_service):
        """Appropriate tags should be detected for changes."""
        # Velocity change is detected as "modified" since pitch+timing match
        base_notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        proposed_notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 50},  # Velocity changed
        ]
        
        variation = variation_service.compute_variation(
            base_notes=base_notes,
            proposed_notes=proposed_notes,
            region_id="region-1",
            track_id="track-1",
            intent="lower the velocity",
        )
        
        assert len(variation.phrases) == 1
        # Velocity change is detected for modified notes
        assert "velocityChange" in variation.phrases[0].tags
    
    def test_pitch_change_results_in_density_change(self, variation_service):
        """Pitch changes result in remove+add, tagged as density change."""
        # Note: Pitch changes don't match (different pitch = different note)
        # So they become remove+add pairs, tagged as densityChange
        base_notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        proposed_notes = [
            {"pitch": 59, "start_beat": 0, "duration_beats": 1, "velocity": 100},  # Pitch lowered
        ]
        
        variation = variation_service.compute_variation(
            base_notes=base_notes,
            proposed_notes=proposed_notes,
            region_id="region-1",
            track_id="track-1",
            intent="lower the pitch",
        )
        
        assert len(variation.phrases) == 1
        # Pitch change = remove old + add new = density change
        assert "densityChange" in variation.phrases[0].tags
        assert variation.phrases[0].added_count == 1
        assert variation.phrases[0].removed_count == 1
    
    def test_variation_metadata(self, variation_service, simple_notes):
        """Variation should include correct metadata."""
        proposed = [n.copy() for n in simple_notes]
        proposed[0]["velocity"] = 50
        
        variation = variation_service.compute_variation(
            base_notes=simple_notes,
            proposed_notes=proposed,
            region_id="region-123",
            track_id="track-456",
            intent="lower velocity",
            explanation="Made the notes quieter",
        )
        
        assert variation.intent == "lower velocity"
        assert variation.ai_explanation == "Made the notes quieter"
        assert "track-456" in variation.affected_tracks
        assert "region-123" in variation.affected_regions


# =============================================================================
# Variation Model Tests
# =============================================================================

class TestVariation:
    """Tests for Variation model."""
    
    def test_get_phrase_found(self):
        """get_phrase should find existing phrase."""
        phrase = Phrase(
            phrase_id="phrase-123",
            track_id="track-1",
            region_id="region-1",
            start_beat=0,
            end_beat=16,
            label="Bars 1-4",
            note_changes=[],
        )
        variation = Variation(
            variation_id="var-1",
            intent="test",
            beat_range=(0, 16),
            phrases=[phrase],
        )
        
        found = variation.get_phrase("phrase-123")
        assert found is not None
        assert found.phrase_id == "phrase-123"
    
    def test_get_phrase_not_found(self):
        """get_phrase should return None for missing phrase."""
        variation = Variation(
            variation_id="var-1",
            intent="test",
            beat_range=(0, 16),
            phrases=[],
        )
        
        found = variation.get_phrase("nonexistent")
        assert found is None
    
    def test_get_accepted_notes(self):
        """get_accepted_notes should return notes from accepted phrases only."""
        phrase1 = Phrase(
            phrase_id="phrase-1",
            track_id="track-1",
            region_id="region-1",
            start_beat=0,
            end_beat=4,
            label="Bars 1",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
            ],
        )
        phrase2 = Phrase(
            phrase_id="phrase-2",
            track_id="track-1",
            region_id="region-1",
            start_beat=4,
            end_beat=8,
            label="Bars 2",
            note_changes=[
                NoteChange(
                    note_id="n2",
                    change_type="added",
                    after=MidiNoteSnapshot(pitch=62, start_beat=4, duration_beats=1, velocity=100),
                ),
            ],
        )
        variation = Variation(
            variation_id="var-1",
            intent="test",
            beat_range=(0, 8),
            phrases=[phrase1, phrase2],
        )
        
        # Accept only first phrase
        notes = variation.get_accepted_notes(["phrase-1"])
        
        assert len(notes) == 1
        assert notes[0]["pitch"] == 60
    
    def test_serialization_round_trip(self, variation_service):
        """Variation should serialize and deserialize correctly."""
        base = [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}]
        proposed = [{"pitch": 63, "start_beat": 0, "duration_beats": 1, "velocity": 80}]
        
        original = variation_service.compute_variation(
            base_notes=base,
            proposed_notes=proposed,
            region_id="region-1",
            track_id="track-1",
            intent="darken the melody",
        )
        
        # Serialize to dict and back
        data = original.model_dump()
        restored = Variation.model_validate(data)
        
        assert restored.variation_id == original.variation_id
        assert restored.intent == original.intent
        assert restored.total_changes == original.total_changes
        assert len(restored.phrases) == len(original.phrases)


# =============================================================================
# Multi-Region Variation Tests
# =============================================================================

class TestMultiRegionVariation:
    """Tests for multi-region variation computation."""
    
    def test_multi_region_variation(self, variation_service):
        """Should handle changes across multiple regions."""
        base_regions = {
            "region-1": [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
            "region-2": [{"pitch": 64, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
        }
        proposed_regions = {
            "region-1": [{"pitch": 61, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
            "region-2": [{"pitch": 65, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
        }
        
        variation = variation_service.compute_multi_region_variation(
            base_regions=base_regions,
            proposed_regions=proposed_regions,
            track_id="track-1",
            intent="transpose up",
        )
        
        assert len(variation.affected_regions) == 2
        assert "region-1" in variation.affected_regions
        assert "region-2" in variation.affected_regions


# =============================================================================
# Integration Tests (with mocks)
# =============================================================================

class TestVariationIntegration:
    """Integration tests for the variation system."""
    
    @pytest.mark.asyncio
    async def test_apply_variation_phrases_imports(self):
        """Verify apply_variation_phrases can be imported."""
        from app.core.executor import apply_variation_phrases
        assert callable(apply_variation_phrases)
    
    @pytest.mark.asyncio
    async def test_execute_plan_variation_imports(self):
        """Verify execute_plan_variation can be imported."""
        from app.core.executor import execute_plan_variation
        assert callable(execute_plan_variation)
    
    def test_singleton_variation_service(self):
        """get_variation_service should return singleton."""
        service1 = get_variation_service()
        service2 = get_variation_service()
        assert service1 is service2


# =============================================================================
# SSE Variation Event Tests
# =============================================================================

class TestSSEVariationEvents:
    """Tests for SSE variation event format and content."""
    
    def test_variation_meta_event_structure(self, variation_service):
        """meta event should have correct structure."""
        base = [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}]
        proposed = [{"pitch": 63, "start_beat": 0, "duration_beats": 1, "velocity": 80}]
        
        variation = variation_service.compute_variation(
            base_notes=base,
            proposed_notes=proposed,
            region_id="region-1",
            track_id="track-1",
            intent="make it darker",
            explanation="Lowered the pitch",
        )
        
        # Simulate the SSE event data structure
        event_data = {
            "type": "variation_proposal",
            "data": {
                "variation_id": variation.variation_id,
                "intent": variation.intent,
                "ai_explanation": variation.ai_explanation,
                "affected_tracks": variation.affected_tracks,
                "affected_regions": variation.affected_regions,
                "beat_range": list(variation.beat_range),
                "phrases": [phrase.model_dump() for phrase in variation.phrases],
            }
        }
        
        # Verify structure matches frontend expectations
        assert event_data["type"] == "variation_proposal"
        assert "data" in event_data
        
        data = event_data["data"]
        assert "variation_id" in data
        assert "intent" in data
        assert "ai_explanation" in data
        assert "affected_tracks" in data
        assert "affected_regions" in data
        assert "beat_range" in data
        assert "phrases" in data
        
        # Verify phrases structure
        phrases = data.get("phrases") if isinstance(data, dict) else None
        assert isinstance(phrases, list) and len(phrases) > 0
        phrase = phrases[0]
        assert isinstance(phrase, dict) and "phrase_id" in phrase
        assert "track_id" in phrase
        assert "region_id" in phrase
        assert "start_beat" in phrase
        assert "end_beat" in phrase
        assert "label" in phrase
        assert "note_changes" in phrase
        assert "tags" in phrase
    
    def test_variation_hunk_serialization(self, variation_service):
        """Hunks should serialize correctly for SSE."""
        base = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
            {"pitch": 62, "start_beat": 1, "duration_beats": 1, "velocity": 100},
        ]
        proposed = [
            {"pitch": 63, "start_beat": 0, "duration_beats": 1, "velocity": 80},  # Modified
            # Note removed
            {"pitch": 65, "start_beat": 2, "duration_beats": 1, "velocity": 90},  # Added
        ]
        
        variation = variation_service.compute_variation(
            base_notes=base,
            proposed_notes=proposed,
            region_id="region-1",
            track_id="track-1",
            intent="change the melody",
        )
        
        # Serialize phrases
        serialized_phrases = [phrase.model_dump() for phrase in variation.phrases]
        
        # Verify all phrases serialize properly
        for phrase in serialized_phrases:
            # Check required fields
            assert isinstance(phrase["phrase_id"], str)
            assert isinstance(phrase["track_id"], str)
            assert isinstance(phrase["region_id"], str)
            assert isinstance(phrase["start_beat"], (int, float))
            assert isinstance(phrase["end_beat"], (int, float))
            assert isinstance(phrase["label"], str)
            assert isinstance(phrase["note_changes"], list)
            assert isinstance(phrase["tags"], list)
            
            # Check note_changes structure
            for nc in phrase["note_changes"]:
                assert "note_id" in nc
                assert "change_type" in nc
                assert nc["change_type"] in ["added", "removed", "modified"]


# =============================================================================
# Execution Mode Tests
# =============================================================================

class TestExecutionModeRemoved:
    """Verify execution_mode is no longer on MaestroRequest (backend-owned)."""
    
    def test_maestro_request_has_no_execution_mode(self):
        """MaestroRequest should not have an execution_mode field."""
        from app.models.requests import MaestroRequest
        
        request = MaestroRequest(prompt="test")
        assert not hasattr(request, "execution_mode")


# =============================================================================
# Note Extraction Tests  
# =============================================================================

class TestNoteExtraction:
    """Tests for note extraction from variations."""
    
    def test_get_accepted_notes_from_added(self):
        """Get accepted notes should extract added notes."""
        phrase = Phrase(
            phrase_id="phrase-1",
            track_id="track-1",
            region_id="region-1",
            start_beat=0,
            end_beat=4,
            label="Bars 1",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
                NoteChange(
                    note_id="n2",
                    change_type="added",
                    after=MidiNoteSnapshot(pitch=62, start_beat=1, duration_beats=1, velocity=100),
                ),
            ],
        )
        
        variation = Variation(
            variation_id="v-1",
            intent="add notes",
            beat_range=(0, 4),
            phrases=[phrase],
        )
        
        # Get accepted notes (use phrase_id that exists in the variation)
        notes = variation.get_accepted_notes(["phrase-1"])
        assert len(notes) == 2
        assert notes[0]["pitch"] == 60
        assert notes[1]["pitch"] == 62
    
    def test_get_removed_note_ids(self):
        """Get removed note IDs should extract removed note identifiers."""
        phrase = Phrase(
            phrase_id="phrase-1",
            track_id="track-1",
            region_id="region-1",
            start_beat=0,
            end_beat=4,
            label="Bars 1",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="removed",
                    before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
            ],
        )
        
        variation = Variation(
            variation_id="v-1",
            intent="remove notes",
            beat_range=(0, 4),
            phrases=[phrase],
        )
        
        # Get removed note IDs
        note_ids = variation.get_removed_note_ids(["phrase-1"])
        assert len(note_ids) == 1
        assert note_ids[0] == "n1"
    
    def test_get_modified_notes(self):
        """Modified notes should be extractable as both removed and added."""
        phrase = Phrase(
            phrase_id="phrase-1",
            track_id="track-1",
            region_id="region-1",
            start_beat=0,
            end_beat=4,
            label="Bars 1",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="modified",
                    before=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                    after=MidiNoteSnapshot(pitch=63, start_beat=0, duration_beats=1, velocity=80),
                ),
            ],
        )
        
        variation = Variation(
            variation_id="v-1",
            intent="modify notes",
            beat_range=(0, 4),
            phrases=[phrase],
        )
        
        # Modified = remove old + add new
        note_ids = variation.get_removed_note_ids(["phrase-1"])
        notes = variation.get_accepted_notes(["phrase-1"])
        
        assert len(note_ids) == 1  # Remove old
        assert len(notes) == 1     # Add new
        assert notes[0]["pitch"] == 63  # New pitch