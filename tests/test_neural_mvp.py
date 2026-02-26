"""
Tests for Neural MIDI MVP components.

Tests:
1. EmotionVector schema and operations
2. REMI tokenizer encode/decode
3. NeuralMelodyGenerator interface
4. MelodyNeuralBackend integration
5. HuggingFace backend integration
"""
from __future__ import annotations

import pytest
from app.contracts.json_types import NoteDict
from app.core.emotion_vector import (
    EmotionVector,
    emotion_to_constraints,
    get_emotion_preset,
    get_refinement_delta,
    emotion_vector_from_stori_prompt,
    EMOTION_PRESETS,
)
from app.services.neural.tokenizer import MidiTokenizer, TokenizerConfig
from app.services.neural.melody_generator import (
    NeuralMelodyGenerator,
    MelodyGenerationRequest,
    MockNeuralMelodyBackend,
)
from app.services.neural.huggingface_melody import (
    HuggingFaceMelodyBackend,
    HF_MODELS,
)
from app.services.neural.text2midi_backend import (
    emotion_to_text_description,
)


class TestEmotionVector:
    """Test EmotionVector schema and operations."""
    
    def test_default_values(self) -> None:

        """Should have sensible defaults."""
        ev = EmotionVector()
        assert ev.energy == 0.5
        assert ev.valence == 0.0
        assert ev.tension == 0.3
        assert ev.intimacy == 0.5
        assert ev.motion == 0.5
    
    def test_clamping(self) -> None:

        """Should clamp values to valid ranges."""
        ev = EmotionVector(
            energy=2.0,  # Should clamp to 1.0
            valence=-5.0,  # Should clamp to -1.0
            tension=-0.5,  # Should clamp to 0.0
            intimacy=1.5,  # Should clamp to 1.0
            motion=0.5,
        )
        assert ev.energy == 1.0
        assert ev.valence == -1.0
        assert ev.tension == 0.0
        assert ev.intimacy == 1.0
    
    def test_to_conditioning_vector(self) -> None:

        """Should convert to list for model input."""
        ev = EmotionVector(energy=0.8, valence=0.5, tension=0.3, intimacy=0.2, motion=0.9)
        vec = ev.to_conditioning_vector()
        assert len(vec) == 5
        assert vec == [0.8, 0.5, 0.3, 0.2, 0.9]
    
    def test_apply_delta(self) -> None:

        """Should apply delta mutations correctly."""
        ev = EmotionVector(energy=0.5, valence=0.0, tension=0.3, intimacy=0.5, motion=0.5)
        
        # Make it sadder
        new_ev = ev.apply_delta({"valence": -0.3})
        assert new_ev.valence == -0.3
        assert new_ev.energy == 0.5  # Unchanged
        
        # Delta is clamped
        very_sad = ev.apply_delta({"valence": -2.0})
        assert very_sad.valence == -1.0
    
    def test_distance(self) -> None:

        """Should calculate euclidean distance."""
        ev1 = EmotionVector(energy=0.0, valence=0.0, tension=0.0, intimacy=0.0, motion=0.0)
        ev2 = EmotionVector(energy=1.0, valence=0.0, tension=0.0, intimacy=0.0, motion=0.0)
        
        assert ev1.distance(ev2) == 1.0
        assert ev1.distance(ev1) == 0.0
    
    def test_serialization(self) -> None:

        """Should serialize and deserialize correctly."""
        ev = EmotionVector(energy=0.7, valence=-0.3, tension=0.5, intimacy=0.8, motion=0.4)
        
        d = ev.to_dict()
        restored = EmotionVector.from_dict(d)
        
        assert restored.energy == ev.energy
        assert restored.valence == ev.valence
        assert restored.tension == ev.tension
        assert restored.intimacy == ev.intimacy
        assert restored.motion == ev.motion


class TestEmotionPresets:
    """Test emotion presets and refinement mappings."""
    
    def test_presets_exist(self) -> None:

        """Should have expected presets."""
        assert "happy" in EMOTION_PRESETS
        assert "sad" in EMOTION_PRESETS
        assert "chorus" in EMOTION_PRESETS
        assert "verse" in EMOTION_PRESETS
        assert "indie_folk" in EMOTION_PRESETS
    
    def test_get_preset(self) -> None:

        """Should retrieve presets correctly."""
        happy = get_emotion_preset("happy")
        assert happy.valence > 0.5
        assert happy.energy > 0.5
        
        sad = get_emotion_preset("sad")
        assert sad.valence < 0.0
        assert sad.energy < 0.5
    
    def test_get_preset_unknown(self) -> None:

        """Should return neutral for unknown preset."""
        unknown = get_emotion_preset("unknown_preset_xyz")
        neutral = EMOTION_PRESETS["neutral"]
        assert unknown.energy == neutral.energy
    
    def test_refinement_deltas(self) -> None:

        """Should have expected refinement deltas."""
        delta = get_refinement_delta("sadder")
        assert delta is not None
        assert delta["valence"] < 0
        
        delta = get_refinement_delta("more intense")
        assert delta is not None
        assert delta["energy"] > 0
    
    def test_refinement_delta_unknown(self) -> None:

        """Should return None for unknown command."""
        assert get_refinement_delta("make it purple") is None


class TestEmotionToConstraints:
    """Test emotion vector to generation constraints mapping."""
    
    def test_high_energy_high_density(self) -> None:

        """High energy + motion should produce high density."""
        ev = EmotionVector(energy=0.9, motion=0.9)
        constraints = emotion_to_constraints(ev)
        
        assert constraints.drum_density > 0.7
        assert constraints.velocity_floor > 70
    
    def test_low_energy_low_density(self) -> None:

        """Low energy + motion should produce low density."""
        ev = EmotionVector(energy=0.2, motion=0.2)
        constraints = emotion_to_constraints(ev)
        
        assert constraints.drum_density < 0.3
        assert constraints.velocity_floor < 60
    
    def test_positive_valence_high_register(self) -> None:

        """Positive valence should raise register center."""
        bright = EmotionVector(valence=0.8)
        dark = EmotionVector(valence=-0.8)
        
        bright_c = emotion_to_constraints(bright)
        dark_c = emotion_to_constraints(dark)
        
        assert bright_c.register_center > dark_c.register_center
    
    def test_high_tension_extensions(self) -> None:

        """High tension should enable chord extensions."""
        tense = EmotionVector(tension=0.8)
        relaxed = EmotionVector(tension=0.2)
        
        tense_c = emotion_to_constraints(tense)
        relaxed_c = emotion_to_constraints(relaxed)
        
        assert tense_c.chord_extensions is True
        assert relaxed_c.chord_extensions is False


def _note(p: int, s: float, d: float, v: int) -> NoteDict:
    """Build a NoteDict for tokenizer tests."""
    return NoteDict(pitch=p, start_beat=s, duration_beats=d, velocity=v)


class TestMidiTokenizer:
    """Test REMI tokenization."""

    def test_vocab_size(self) -> None:

        """Should have reasonable vocab size."""
        tokenizer = MidiTokenizer()
        assert tokenizer.get_vocab_size() > 100
        assert tokenizer.get_vocab_size() < 500
    
    def test_special_tokens(self) -> None:

        """Should have special token IDs."""
        tokenizer = MidiTokenizer()
        assert tokenizer.get_pad_token_id() == 0
        assert tokenizer.get_bos_token_id() == 1
        assert tokenizer.get_eos_token_id() == 2
    
    def test_encode_decode_roundtrip(self) -> None:

        """Should roundtrip notes through tokenization."""
        tokenizer = MidiTokenizer()
        original_notes: list[NoteDict] = [
            _note(60, 0.0, 1.0, 80),
            _note(64, 1.0, 0.5, 90),
            _note(67, 2.0, 1.5, 70),
        ]
        
        # Encode
        token_ids = tokenizer.encode(original_notes, bars=1)
        assert len(token_ids) > 0
        
        # Decode
        decoded_notes = tokenizer.decode(token_ids)
        
        # Should have same number of notes
        assert len(decoded_notes) == len(original_notes)
        
        # Pitches should match exactly
        original_pitches = sorted([n["pitch"] for n in original_notes])
        decoded_pitches = sorted([n["pitch"] for n in decoded_notes])
        assert original_pitches == decoded_pitches
    
    def test_encode_multi_bar(self) -> None:

        """Should handle multi-bar sequences."""
        tokenizer = MidiTokenizer()
        notes: list[NoteDict] = [
            _note(60, 0.0, 1.0, 80),   # Bar 0
            _note(62, 4.0, 1.0, 80),   # Bar 1
            _note(64, 8.0, 1.0, 80),   # Bar 2
        ]
        
        token_ids = tokenizer.encode(notes, bars=3)
        decoded = tokenizer.decode(token_ids)
        
        assert len(decoded) == 3
        
        # Check bar assignment
        assert decoded[0]["start_beat"] < 4
        assert 4 <= decoded[1]["start_beat"] < 8
        assert decoded[2]["start_beat"] >= 8
    
    def test_encode_to_tokens_readable(self) -> None:

        """Should produce readable token strings."""
        tokenizer = MidiTokenizer()
        notes: list[NoteDict] = [_note(60, 0.0, 1.0, 80)]
        tokens = tokenizer.encode_to_tokens(notes, bars=1)
        
        assert "BAR" in tokens
        assert any("POS_" in t for t in tokens)
        assert any("PITCH_60" in t for t in tokens)


class TestNeuralMelodyGenerator:
    """Test neural melody generator."""
    
    @pytest.mark.asyncio
    async def test_generator_available(self) -> None:

        """Mock backend should always be available."""
        generator = NeuralMelodyGenerator()
        assert await generator.is_available()
    
    @pytest.mark.asyncio
    async def test_generate_basic(self) -> None:

        """Should generate notes with basic parameters."""
        generator = NeuralMelodyGenerator()
        
        result = await generator.generate(
            bars=4,
            tempo=120,
            key="C",
            chords=["C", "G", "Am", "F"],
        )
        
        assert result.success
        assert len(result.notes) > 0
        assert result.model_used == "mock_neural"
    
    @pytest.mark.asyncio
    async def test_generate_with_emotion(self) -> None:

        """Should condition on emotion vector."""
        generator = NeuralMelodyGenerator()
        
        # High energy
        high_energy = EmotionVector(energy=0.9, motion=0.9)
        result_high = await generator.generate(
            bars=4,
            tempo=120,
            key="C",
            emotion_vector=high_energy,
        )
        
        # Low energy
        low_energy = EmotionVector(energy=0.2, motion=0.2)
        result_low = await generator.generate(
            bars=4,
            tempo=120,
            key="C",
            emotion_vector=low_energy,
        )
        
        # High energy should produce more notes (mock behavior)
        assert result_high.success
        assert result_low.success
        # The mock generator uses emotion to affect density
        # This is a weak test but validates the interface
        assert len(result_high.notes) >= len(result_low.notes) * 0.5
    
    @pytest.mark.asyncio
    async def test_generate_metadata(self) -> None:

        """Should include emotion vector in metadata."""
        generator = NeuralMelodyGenerator()
        ev = EmotionVector(energy=0.7, valence=0.3)
        
        result = await generator.generate(
            bars=2,
            tempo=100,
            key="Am",
            emotion_vector=ev,
        )
        
        assert result.success
        assert "emotion_vector" in result.metadata
        assert result.metadata["emotion_vector"]["energy"] == 0.7


class TestMockNeuralBackend:
    """Test the mock neural backend directly."""
    
    @pytest.mark.asyncio
    async def test_respects_register_constraints(self) -> None:

        """Should respect register center from emotion."""
        backend = MockNeuralMelodyBackend()
        
        # High valence = high register
        high_request = MelodyGenerationRequest(
            bars=4,
            tempo=120,
            key="C",
            chords=["C", "G", "Am", "F"],
            emotion_vector=EmotionVector(valence=0.8),
        )
        
        # Low valence = low register
        low_request = MelodyGenerationRequest(
            bars=4,
            tempo=120,
            key="C",
            chords=["C", "G", "Am", "F"],
            emotion_vector=EmotionVector(valence=-0.8),
        )
        
        high_result = await backend.generate(high_request)
        low_result = await backend.generate(low_request)
        
        assert high_result.success
        assert low_result.success
        
        # Calculate average pitch
        high_avg = sum(n["pitch"] for n in high_result.notes) / len(high_result.notes)
        low_avg = sum(n["pitch"] for n in low_result.notes) / len(low_result.notes)
        
        # High valence should produce higher average pitch
        assert high_avg > low_avg


class TestHuggingFaceBackend:
    """Test HuggingFace melody backend."""
    
    def test_models_defined(self) -> None:

        """Should have models configured."""
        assert "skytnt" in HF_MODELS
        assert "giant" in HF_MODELS
        assert "orpheus" in HF_MODELS
    
    def test_model_config(self) -> None:

        """Should have valid model configurations."""
        config = HF_MODELS["skytnt"]
        assert config.model_id == "skytnt/midi-model-tv2o-medium"
        assert config.max_tokens > 0
    
    def test_emotion_to_params_high_energy(self) -> None:

        """High energy should increase temperature."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        high_energy = EmotionVector(energy=0.9, tension=0.7)
        low_energy = EmotionVector(energy=0.2, tension=0.2)
        
        high_params = backend._emotion_to_hf_params(high_energy, bars=4)
        low_params = backend._emotion_to_hf_params(low_energy, bars=4)
        
        # Higher energy/tension should mean higher temperature
        assert high_params["temperature"] > low_params["temperature"]
    
    def test_emotion_to_params_motion_affects_tokens(self) -> None:

        """Higher motion should request more tokens."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        high_motion = EmotionVector(motion=0.9)
        low_motion = EmotionVector(motion=0.2)
        
        high_params = backend._emotion_to_hf_params(high_motion, bars=4)
        low_params = backend._emotion_to_hf_params(low_motion, bars=4)
        
        assert high_params["max_tokens"] > low_params["max_tokens"]
    
    def test_emotion_to_params_intimacy_affects_top_p(self) -> None:

        """Higher intimacy should lower top_p (more focused)."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        intimate = EmotionVector(intimacy=0.9)
        distant = EmotionVector(intimacy=0.1)
        
        intimate_params = backend._emotion_to_hf_params(intimate, bars=4)
        distant_params = backend._emotion_to_hf_params(distant, bars=4)
        
        # More intimate = more focused = lower top_p
        assert intimate_params["top_p"] < distant_params["top_p"]
    
    @pytest.mark.asyncio
    async def test_fallback_when_no_api_key(self) -> None:

        """Should fall back to mock when no API key."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        request = MelodyGenerationRequest(
            bars=4,
            tempo=120,
            key="C",
            chords=["C", "G", "Am", "F"],
            emotion_vector=EmotionVector(),
        )
        
        result = await backend.generate(request)
        
        # Should succeed via fallback
        assert result.success
        assert "fallback" in result.model_used or "mock" in result.model_used
        assert len(result.notes) > 0
    
    @pytest.mark.asyncio
    async def test_is_available_without_key(self) -> None:

        """Should report unavailable without API key."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        assert await backend.is_available() is False
    
    def test_chord_to_midi(self) -> None:

        """Should convert chord symbols to MIDI notes."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        assert backend._chord_to_midi("C") == 60
        assert backend._chord_to_midi("G") == 67
        assert backend._chord_to_midi("Am") == 69
        assert backend._chord_to_midi("F#m") == 66
        assert backend._chord_to_midi("Bb") == 70
    
    def test_apply_emotion_postprocess(self) -> None:

        """Should adjust notes based on emotion."""
        backend = HuggingFaceMelodyBackend(api_key=None)
        
        notes: list[NoteDict] = [
            {"pitch": 72, "start_beat": 0, "duration_beats": 1, "velocity": 80},
            {"pitch": 74, "start_beat": 1, "duration_beats": 1, "velocity": 90},
        ]
        
        # Low energy emotion should lower velocity
        low_energy = EmotionVector(energy=0.2)
        processed = backend._apply_emotion_postprocess(notes, low_energy)
        
        # Velocities should be adjusted down
        avg_original = sum(n["velocity"] for n in notes) / len(notes)
        avg_processed = sum(n["velocity"] for n in processed) / len(processed)
        assert avg_processed < avg_original


class TestNeuralGeneratorWithHuggingFace:
    """Test NeuralMelodyGenerator with HuggingFace backend."""
    
    @pytest.mark.asyncio
    async def test_generator_with_hf_backend(self) -> None:

        """Should work with HuggingFace backend."""
        hf_backend = HuggingFaceMelodyBackend(api_key=None)  # Will use fallback
        generator = NeuralMelodyGenerator(backend=hf_backend)
        
        result = await generator.generate(
            bars=4,
            tempo=120,
            key="Am",
            chords=["Am", "F", "C", "G"],
            emotion_vector=EmotionVector(energy=0.7, valence=-0.2),
        )
        
        assert result.success
        assert len(result.notes) > 0


class TestText2MidiBackend:
    """Test text2midi emotion-to-text conversion."""
    
    def test_emotion_to_text_high_energy(self) -> None:

        """High energy should produce Presto tempo description."""
        high_energy = EmotionVector(energy=0.9)
        text = emotion_to_text_description(high_energy, key="C", tempo=140)
        
        assert "Presto" in text
        assert "energetic" in text.lower()
    
    def test_emotion_to_text_low_energy(self) -> None:

        """Low energy should produce Adagio tempo description."""
        low_energy = EmotionVector(energy=0.1)
        text = emotion_to_text_description(low_energy, key="Am", tempo=60)
        
        assert "Adagio" in text
        assert "contemplative" in text.lower()
    
    def test_emotion_to_text_positive_valence(self) -> None:

        """Positive valence should indicate major and joyful."""
        happy = EmotionVector(valence=0.8)
        text = emotion_to_text_description(happy, key="G", tempo=120)
        
        assert "major" in text.lower()
        assert "joyful" in text.lower() or "hopeful" in text.lower()
    
    def test_emotion_to_text_negative_valence(self) -> None:

        """Negative valence should indicate minor and melancholic."""
        sad = EmotionVector(valence=-0.7)
        text = emotion_to_text_description(sad, key="Dm", tempo=80)
        
        assert "minor" in text.lower()
        assert "melancholic" in text.lower() or "dark" in text.lower()
    
    def test_emotion_to_text_high_tension(self) -> None:

        """High tension should indicate dramatic moments."""
        tense = EmotionVector(tension=0.8)
        text = emotion_to_text_description(tense, key="Em", tempo=100)
        
        assert "tension" in text.lower() or "dramatic" in text.lower()
    
    def test_emotion_to_text_high_intimacy(self) -> None:

        """High intimacy should indicate sparse arrangement."""
        intimate = EmotionVector(intimacy=0.9)
        text = emotion_to_text_description(intimate, key="F", tempo=90)
        
        assert "sparse" in text.lower() or "intimate" in text.lower()
    
    def test_emotion_to_text_includes_key_and_tempo(self) -> None:

        """Should include the provided key and tempo."""
        ev = EmotionVector()
        text = emotion_to_text_description(ev, key="Bb", tempo=128)
        
        assert "Bb" in text
        assert "128" in text
    
    def test_emotion_to_text_includes_style(self) -> None:

        """Should include the provided style."""
        ev = EmotionVector()
        text = emotion_to_text_description(ev, key="C", tempo=120, style="jazz")
        
        assert "jazz" in text.lower()
    
    def test_emotion_to_text_includes_instrument(self) -> None:

        """Should include the provided instrument."""
        ev = EmotionVector()
        text = emotion_to_text_description(ev, key="C", tempo=120, instrument="guitar")
        
        assert "guitar" in text.lower()


# =============================================================================
# emotion_vector_from_stori_prompt — new function tests
# =============================================================================


class TestEmotionVectorFromStoriPrompt:
    """Tests for the STORI PROMPT → EmotionVector parser."""

    def test_empty_string_returns_neutral(self) -> None:

        """Empty input returns the neutral preset."""
        ev = emotion_vector_from_stori_prompt("")
        neutral = EMOTION_PRESETS["neutral"]
        assert abs(ev.energy - neutral.energy) < 0.01

    def test_none_equivalent_empty_string(self) -> None:

        """Empty input does not raise and returns a valid EmotionVector."""
        ev = emotion_vector_from_stori_prompt("")
        assert 0.0 <= ev.energy <= 1.0
        assert -1.0 <= ev.valence <= 1.0

    def test_section_verse_lowers_energy(self) -> None:

        """A Verse section preset has lower energy than the Chorus preset."""
        verse_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nSection: Verse")
        chorus_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nSection: Chorus")
        assert verse_ev.energy < chorus_ev.energy

    def test_section_drop_raises_energy_and_motion(self) -> None:

        """Drop section should produce high energy and motion."""
        ev = emotion_vector_from_stori_prompt("STORI PROMPT\nSection: Drop")
        assert ev.energy > 0.7
        assert ev.motion > 0.7

    def test_dark_vibe_lowers_valence(self) -> None:

        """'dark' in Vibe should push valence below neutral."""
        ev = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Dark")
        assert ev.valence < 0.0

    def test_euphoric_vibe_raises_energy_and_valence(self) -> None:

        """'euphoric' in Vibe should push energy and valence well above neutral."""
        neutral = emotion_vector_from_stori_prompt("")
        ev = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Euphoric")
        assert ev.energy > neutral.energy + 0.1
        assert ev.valence > neutral.valence + 0.2

    def test_energy_low_lowers_energy_axis(self) -> None:

        """'Energy: Low' should produce a lower energy than 'Energy: High'."""
        low_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nEnergy: Low")
        high_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nEnergy: High")
        assert low_ev.energy < high_ev.energy
        assert low_ev.motion < high_ev.motion

    def test_energy_very_high_exceeds_high(self) -> None:

        """'Energy: Very High' should produce higher energy than 'Energy: High'."""
        high_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nEnergy: High")
        very_high_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nEnergy: Very High")
        assert very_high_ev.energy >= high_ev.energy

    def test_genre_lofi_raises_intimacy(self) -> None:

        """Lofi style should yield higher intimacy than a generic prompt."""
        lofi_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nStyle: Lofi Hip-Hop")
        neutral_ev = emotion_vector_from_stori_prompt("")
        assert lofi_ev.intimacy > neutral_ev.intimacy

    def test_genre_edm_raises_energy(self) -> None:

        """EDM style should yield higher energy than lofi."""
        edm_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nStyle: EDM")
        lofi_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nStyle: Lofi")
        assert edm_ev.energy > lofi_ev.energy

    def test_multiple_vibe_keywords_blend(self) -> None:

        """Multiple vibe keywords should blend — result between individual extremes."""
        dark_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Dark")
        bright_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Bright")
        blend_ev = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Dark, Bright")
        # Blend should sit between the two extremes
        lo, hi = sorted([dark_ev.valence, bright_ev.valence])
        assert lo <= blend_ev.valence <= hi + 0.15  # small tolerance for blending math

    def test_full_stori_prompt_melancholic_verse(self) -> None:

        """A full lofi verse prompt should produce low energy, high intimacy, negative valence."""
        ev = emotion_vector_from_stori_prompt(
            "STORI PROMPT\n"
            "Section: Verse\n"
            "Style: Lofi Hip-Hop\n"
            "Vibe: Melancholic, warm, nostalgic\n"
            "Energy: Low\n"
            "BPM: 85\n"
            "Key: Am\n"
            "Bars: 8"
        )
        assert ev.energy < 0.5
        assert ev.intimacy > 0.5
        assert ev.valence < 0.2  # warm+nostalgic+melancholic blend is slightly negative

    def test_full_stori_prompt_euphoric_drop(self) -> None:

        """A euphoric EDM drop prompt should produce high energy, high motion, positive valence."""
        ev = emotion_vector_from_stori_prompt(
            "STORI PROMPT\n"
            "Section: Drop\n"
            "Style: EDM\n"
            "Vibe: Euphoric, explosive, driving\n"
            "Energy: Very High"
        )
        assert ev.energy > 0.7
        assert ev.motion > 0.7
        assert ev.valence > 0.3

    def test_contrasting_prompts_are_distinct(self) -> None:

        """Two opposed prompts must produce clearly different vectors."""
        melancholic = emotion_vector_from_stori_prompt(
            "STORI PROMPT\nVibe: Melancholic\nEnergy: Low"
        )
        triumphant = emotion_vector_from_stori_prompt(
            "STORI PROMPT\nVibe: Triumphant\nEnergy: High"
        )
        assert melancholic.distance(triumphant) > 0.3

    def test_output_always_in_valid_range(self) -> None:

        """All axes should always be within their valid ranges regardless of input."""
        prompts = [
            "STORI PROMPT\nVibe: Explosive, euphoric, driving\nEnergy: Very High",
            "STORI PROMPT\nVibe: Peaceful, calm, minimal\nEnergy: Very Low",
            "STORI PROMPT\nSection: Drop\nStyle: Metal\nVibe: Aggressive, intense",
            "not a stori prompt at all",
            "",
        ]
        for text in prompts:
            ev = emotion_vector_from_stori_prompt(text)
            assert 0.0 <= ev.energy <= 1.0, f"energy out of range for: {text!r}"
            assert -1.0 <= ev.valence <= 1.0, f"valence out of range for: {text!r}"
            assert 0.0 <= ev.tension <= 1.0, f"tension out of range for: {text!r}"
            assert 0.0 <= ev.intimacy <= 1.0, f"intimacy out of range for: {text!r}"
            assert 0.0 <= ev.motion <= 1.0, f"motion out of range for: {text!r}"

    def test_unknown_fields_ignored_gracefully(self) -> None:

        """Unrecognised STORI PROMPT fields do not crash the parser."""
        ev = emotion_vector_from_stori_prompt(
            "STORI PROMPT\nBPM: 120\nKey: Cm\nBars: 8\nRequest: |"
        )
        assert isinstance(ev, EmotionVector)

    def test_case_insensitive_keys(self) -> None:

        """Field keys are matched case-insensitively."""
        ev1 = emotion_vector_from_stori_prompt("STORI PROMPT\nVibe: Calm")
        ev2 = emotion_vector_from_stori_prompt("STORI PROMPT\nvibe: calm")
        # Both should produce the same result
        assert abs(ev1.energy - ev2.energy) < 0.01
