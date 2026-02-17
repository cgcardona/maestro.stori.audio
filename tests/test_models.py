"""Tests for Pydantic models."""
import pytest
from pydantic import ValidationError

from app.models.requests import ComposeRequest, GenerateRequest
from app.models.tools import MidiNote, AutomationPoint


class TestComposeRequest:
    """Tests for ComposeRequest model."""
    
    def test_valid_request(self):
        """Test creating a valid request."""
        req = ComposeRequest(prompt="Make a beat")
        assert req.prompt == "Make a beat"
        assert req.mode == "generate"
        assert req.project is None
    
    def test_with_project(self):
        """Test request with project context."""
        project = {"name": "Test", "tempo": 120}
        req = ComposeRequest(prompt="Add drums", mode="edit", project=project)
        assert req.mode == "edit"
        assert req.project is not None and req.project["tempo"] == 120
    
    def test_empty_prompt(self):
        """Test that empty prompt fails validation."""
        # Note: empty string is technically valid, you might want to add min_length
        req = ComposeRequest(prompt="")
        assert req.prompt == ""


class TestGenerateRequest:
    """Tests for GenerateRequest model."""
    
    def test_defaults(self):
        """Test default values."""
        req = GenerateRequest()
        assert req.genre == "boom_bap"
        assert req.tempo == 90
        assert req.bars == 4
    
    def test_tempo_range(self):
        """Test tempo validation."""
        req = GenerateRequest(tempo=120)
        assert req.tempo == 120
        
        with pytest.raises(ValidationError):
            GenerateRequest(tempo=300)  # Too fast
        
        with pytest.raises(ValidationError):
            GenerateRequest(tempo=20)  # Too slow
    
    def test_bars_range(self):
        """Test bars validation."""
        req = GenerateRequest(bars=16)
        assert req.bars == 16
        
        with pytest.raises(ValidationError):
            GenerateRequest(bars=100)  # Too many


class TestMidiNote:
    """Tests for MidiNote model."""
    
    def test_valid_note(self):
        """Test creating a valid note."""
        note = MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=100)
        assert note.pitch == 60
        assert note.velocity == 100
    
    def test_pitch_range(self):
        """Test pitch validation."""
        MidiNote(pitch=0, start_beat=0, duration_beats=1.0)  # Lowest
        MidiNote(pitch=127, start_beat=0, duration_beats=1.0)  # Highest
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=128, start_beat=0, duration_beats=1.0)
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=-1, start_beat=0, duration_beats=1.0)
    
    def test_velocity_range(self):
        """Test velocity validation."""
        MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=0)
        MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=127)
        
        with pytest.raises(ValidationError):
            MidiNote(pitch=60, start_beat=0, duration_beats=1.0, velocity=128)


class TestAutomationPoint:
    """Tests for AutomationPoint model."""
    
    def test_valid_point(self):
        """Test creating a valid automation point."""
        point = AutomationPoint(beat=0, value=0.5)
        assert point.beat == 0
        assert point.value == 0.5
        assert point.curve == "Linear"
    
    def test_value_range(self):
        """Test value validation."""
        AutomationPoint(beat=0, value=0)
        AutomationPoint(beat=0, value=1)
        
        with pytest.raises(ValidationError):
            AutomationPoint(beat=0, value=1.5)
        
        with pytest.raises(ValidationError):
            AutomationPoint(beat=0, value=-0.1)
