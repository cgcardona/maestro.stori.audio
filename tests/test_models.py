"""Tests for Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from maestro.contracts.project_types import ProjectContext
from maestro.models.requests import MaestroRequest, GenerateRequest
from maestro.models.tools import MidiNote, AutomationPoint


class TestMaestroRequest:
    """Tests for MaestroRequest model."""
    
    def test_valid_request(self) -> None:

        """Test creating a valid request."""
        req = MaestroRequest(prompt="Make a beat")
        assert req.prompt == "Make a beat"
        assert req.mode == "generate"
        assert req.project is None
    
    def test_with_project(self) -> None:

        """Test request with project context."""
        project: ProjectContext = {"id": "proj-1", "name": "Test", "tempo": 120}
        req = MaestroRequest(prompt="Add drums", mode="edit", project=project)
        assert req.mode == "edit"
        assert req.project is not None and req.project["tempo"] == 120
    
    def test_empty_prompt(self) -> None:

        """Empty prompt is rejected — MaestroRequest enforces min_length=1."""
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="string_too_short"):
            MaestroRequest(prompt="")


class TestGenerateRequest:
    """Tests for GenerateRequest model."""
    
    def test_defaults(self) -> None:

        """Test default values."""
        req = GenerateRequest()
        assert req.genre == "boom_bap"
        assert req.tempo == 90
        assert req.bars == 4
    
    def test_tempo_range(self) -> None:

        """Test tempo validation."""
        req = GenerateRequest(tempo=120)
        assert req.tempo == 120
        
        with pytest.raises(ValidationError):
            GenerateRequest(tempo=300)  # Too fast
        
        with pytest.raises(ValidationError):
            GenerateRequest(tempo=20)  # Too slow
    
    def test_bars_range(self) -> None:

        """Test bars validation."""
        req = GenerateRequest(bars=16)
        assert req.bars == 16
        
        with pytest.raises(ValidationError):
            GenerateRequest(bars=100)  # Too many


class TestMidiNote:
    """Tests for MidiNote model."""
    
    def test_valid_note(self) -> None:

        """Test creating a valid note."""
        note = MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=100)
        assert note.pitch == 60
        assert note.velocity == 100
    
    def test_pitch_range(self) -> None:

        """Test pitch validation."""
        MidiNote(pitch=0, start_beat=0, duration_beats=1.0)  # Lowest
        MidiNote(pitch=127, start_beat=0, duration_beats=1.0)  # Highest
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=128, start_beat=0, duration_beats=1.0)
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=-1, start_beat=0, duration_beats=1.0)
    
    def test_velocity_range(self) -> None:

        """Test velocity validation."""
        MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=0)
        MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=127)
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=128)


class TestAutomationPoint:
    """Tests for AutomationPoint model."""
    
    def test_valid_point(self) -> None:

        """Test creating a valid automation point."""
        point = AutomationPoint(beat=0, value=0.5)
        assert point.beat == 0
        assert point.value == 0.5
        assert point.curve == "Linear"
    
    def test_value_range(self) -> None:

        """Test value validation."""
        AutomationPoint(beat=0, value=0)
        AutomationPoint(beat=0, value=1)
        
        with pytest.raises(ValidationError):
            AutomationPoint(beat=0, value=1.5)
        
        with pytest.raises(ValidationError):
            AutomationPoint(beat=0, value=-0.1)
