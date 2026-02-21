"""
Tests for track styling (color and icon assignment).
"""

import pytest
from app.core.track_styling import (
    get_random_track_color,
    infer_track_icon,
    get_track_styling,
    TRACK_COLORS,
)


class TestTrackColors:
    """Test track color generation."""
    
    def test_random_color_is_hex(self):
        """Should return a valid hex color."""
        color = get_random_track_color()
        assert color.startswith("#")
        assert len(color) == 7
    
    def test_random_color_from_palette(self):
        """Should select from predefined palette."""
        color = get_random_track_color()
        assert color in TRACK_COLORS
    
    def test_random_colors_vary(self):
        """Should return different colors (with high probability)."""
        colors = {get_random_track_color() for _ in range(50)}
        # With 20 colors, should get at least 10 different ones in 50 tries
        assert len(colors) >= 10
    
    def test_colors_match_frontend(self):
        """Colors should match frontend Tailwind palette."""
        # Check that we have the exact FE colors
        assert "#3B82F6" in TRACK_COLORS  # Blue
        assert "#EF4444" in TRACK_COLORS  # Red
        assert "#10B981" in TRACK_COLORS  # Green
        assert "#F59E0B" in TRACK_COLORS  # Yellow
        assert "#8B5CF6" in TRACK_COLORS  # Purple
        assert "#EC4899" in TRACK_COLORS  # Pink
        assert "#F97316" in TRACK_COLORS  # Orange
        assert "#14B8A6" in TRACK_COLORS  # Teal
        assert "#6366F1" in TRACK_COLORS  # Indigo
        assert "#6B7280" in TRACK_COLORS  # Gray


class TestTrackIcons:
    """Test track icon inference."""
    
    def test_drum_track_icon(self):
        """Drum tracks should get instrument.drum icon."""
        assert infer_track_icon("Drums") == "instrument.drum"
        assert infer_track_icon("Jam Drums") == "instrument.drum"
        assert infer_track_icon("Kick") == "instrument.drum"
        assert infer_track_icon("Snare") == "instrument.drum"

    def test_bass_track_icon(self):
        """Bass tracks should get waveform.path icon."""
        assert infer_track_icon("Bass") == "waveform.path"
        assert infer_track_icon("Funky Bass") == "waveform.path"
        assert infer_track_icon("Sub Bass") == "waveform.path"

    def test_piano_track_icon(self):
        """Piano tracks should get pianokeys icon."""
        assert infer_track_icon("Piano") == "pianokeys"
        assert infer_track_icon("Rhodes Keys") == "pianokeys"
        assert infer_track_icon("Electric Piano") == "pianokeys"

    def test_guitar_track_icon(self):
        """Guitar tracks should get guitars.fill icon."""
        assert infer_track_icon("Guitar") == "guitars.fill"
        assert infer_track_icon("Guitar Solo") == "guitars.fill"
        assert infer_track_icon("Acoustic Guitar") == "guitars.fill"

    def test_vocal_track_icon(self):
        """Vocal tracks should get music.mic icon."""
        assert infer_track_icon("Vocals") == "music.mic"
        assert infer_track_icon("Lead Vocal") == "music.mic"
        assert infer_track_icon("Voice") == "music.mic"

    def test_synth_track_icon(self):
        """Synth tracks should get waveform icon; pad alone gets waveform.circle.fill."""
        assert infer_track_icon("Synth") == "waveform"
        assert infer_track_icon("Lead Synth") == "waveform"
        assert infer_track_icon("Pad Synth") == "waveform"
        assert infer_track_icon("Pads") == "waveform.circle.fill"

    def test_chord_track_icon(self):
        """Chord tracks should get pianokeys icon (chords → keyboard role)."""
        assert infer_track_icon("Chords") == "pianokeys"
        assert infer_track_icon("Harmony") == "music.note.list"

    def test_fx_track_icon(self):
        """FX tracks should get sparkles icon."""
        assert infer_track_icon("FX") == "sparkles"
        assert infer_track_icon("Sound Effects") == "wand.and.rays"

    def test_strings_track_icon(self):
        """String/orchestral tracks should get instrument.violin icon."""
        assert infer_track_icon("Strings") == "instrument.violin"
        assert infer_track_icon("Violin") == "instrument.violin"
        assert infer_track_icon("Cello Section") == "instrument.violin"

    def test_brass_track_icon(self):
        """Brass tracks should get instrument.trumpet icon."""
        assert infer_track_icon("Brass") == "instrument.trumpet"
        assert infer_track_icon("Trumpet") == "instrument.trumpet"
        assert infer_track_icon("French Horn") == "instrument.trumpet"

    def test_reed_track_icon(self):
        """Reed/woodwind tracks should get instrument.saxophone icon."""
        assert infer_track_icon("Alto Sax") == "instrument.saxophone"
        assert infer_track_icon("Clarinet") == "instrument.saxophone"

    def test_flute_track_icon(self):
        """Flute/pipe tracks should get instrument.flute icon."""
        assert infer_track_icon("Flute") == "instrument.flute"
        assert infer_track_icon("Recorder") == "instrument.flute"

    def test_mallet_track_icon(self):
        """Mallet/chromatic percussion tracks should get instrument.xylophone icon."""
        assert infer_track_icon("Marimba") == "instrument.xylophone"
        assert infer_track_icon("Xylophone") == "instrument.xylophone"
        assert infer_track_icon("Bells") == "instrument.xylophone"

    def test_organ_track_icon(self):
        """Organ tracks should get music.note.house.fill icon."""
        assert infer_track_icon("Organ") == "music.note.house.fill"
        assert infer_track_icon("Hammond Organ") == "music.note.house.fill"

    def test_default_icon(self):
        """Unknown tracks should get default waveform icon."""
        assert infer_track_icon("Unknown Thing") == "waveform"
        assert infer_track_icon("") == "waveform"

    def test_case_insensitive(self):
        """Icon matching should be case insensitive."""
        assert infer_track_icon("DRUMS") == "instrument.drum"
        assert infer_track_icon("PiAnO") == "pianokeys"


class TestTrackStyling:
    """Test combined styling."""
    
    def test_get_track_styling(self):
        """Should return both color and icon."""
        styling = get_track_styling("Drums")

        assert "color" in styling
        assert "icon" in styling
        assert styling["color"].startswith("#")
        assert styling["icon"] == "instrument.drum"

    def test_styling_varies_by_name(self):
        """Different tracks should get appropriate icons."""
        drum_styling = get_track_styling("Drums")
        bass_styling = get_track_styling("Bass")
        piano_styling = get_track_styling("Piano")

        assert drum_styling["icon"] == "instrument.drum"
        assert bass_styling["icon"] == "waveform.path"
        assert piano_styling["icon"] == "pianokeys"
