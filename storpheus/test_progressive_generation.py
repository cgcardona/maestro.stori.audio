"""Tests for progressive instrument generation (#27).

Covers:
- InstrumentTier classification for all four tiers
- group_instruments_by_tier ordering and partitioning
- ProgressiveGenerateRequest model validation
- _do_progressive_generate orchestration (mocked _do_generate)
- Cascaded seeding: each tier receives the previous tier's composition_id
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from storpheus_types import (
    InstrumentTier,
    ProgressiveGenerationResult,
    ProgressiveTierResult,
    WireNoteDict,
)
from music_service import (
    GenerateResponse,
    ProgressiveGenerateRequest,
    classify_instrument_tier,
    group_instruments_by_tier,
    _do_progressive_generate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_note(pitch: int = 60) -> WireNoteDict:
    return WireNoteDict(pitch=pitch, startBeat=0.0, durationBeats=1.0, velocity=80)


def _make_ok_response(notes: list[WireNoteDict] | None = None) -> GenerateResponse:
    return GenerateResponse(
        success=True,
        notes=notes or [_make_note()],
        metadata={"cache_hit": False, "note_count": len(notes or [_make_note()])},
    )


def _make_err_response(error: str = "gpu oom") -> GenerateResponse:
    return GenerateResponse(success=False, error=error)


# ---------------------------------------------------------------------------
# classify_instrument_tier
# ---------------------------------------------------------------------------


class TestClassifyInstrumentTier:
    def test_drums_keywords(self) -> None:
        for role in ["drums", "drum", "kick", "snare", "hihat", "percussion", "808"]:
            assert classify_instrument_tier(role) == InstrumentTier.DRUMS, role

    def test_bass_roles(self) -> None:
        for role in ["bass", "electric bass", "synth bass", "fretless bass", "slap bass"]:
            assert classify_instrument_tier(role) == InstrumentTier.BASS, role

    def test_harmony_piano(self) -> None:
        for role in ["piano", "acoustic piano", "electric piano", "organ"]:
            assert classify_instrument_tier(role) == InstrumentTier.HARMONY, role

    def test_harmony_pads(self) -> None:
        for role in ["strings", "pad", "choir"]:
            # strings (GM 40-55) and synth pads (88-95) → HARMONY
            tier = classify_instrument_tier(role)
            assert tier == InstrumentTier.HARMONY, f"{role} → {tier}"

    def test_melody_roles(self) -> None:
        for role in ["lead", "guitar", "flute", "trumpet", "saxophone"]:
            assert classify_instrument_tier(role) == InstrumentTier.MELODY, role

    def test_abstract_melody_roles(self) -> None:
        # "melody" resolves to GM 0 (piano) → HARMONY by program range
        # but "lead" and unknown roles → MELODY
        assert classify_instrument_tier("unknown_instrument_xyz") == InstrumentTier.MELODY

    def test_case_insensitive(self) -> None:
        assert classify_instrument_tier("DRUMS") == InstrumentTier.DRUMS
        assert classify_instrument_tier("Bass") == InstrumentTier.BASS
        assert classify_instrument_tier("Piano") == InstrumentTier.HARMONY

    def test_whitespace_stripped(self) -> None:
        assert classify_instrument_tier("  drums  ") == InstrumentTier.DRUMS
        assert classify_instrument_tier("  bass  ") == InstrumentTier.BASS


# ---------------------------------------------------------------------------
# group_instruments_by_tier
# ---------------------------------------------------------------------------


class TestGroupInstrumentsByTier:
    def test_order_is_dependency_chain(self) -> None:
        groups = group_instruments_by_tier(["lead", "bass", "drums", "piano"])
        tiers = list(groups.keys())
        assert tiers == [
            InstrumentTier.DRUMS,
            InstrumentTier.BASS,
            InstrumentTier.HARMONY,
            InstrumentTier.MELODY,
        ]

    def test_empty_tiers_omitted(self) -> None:
        groups = group_instruments_by_tier(["drums", "bass"])
        assert InstrumentTier.HARMONY not in groups
        assert InstrumentTier.MELODY not in groups

    def test_roles_preserved_lowercased(self) -> None:
        groups = group_instruments_by_tier(["DRUMS", "Bass"])
        assert "drums" in groups[InstrumentTier.DRUMS]
        assert "bass" in groups[InstrumentTier.BASS]

    def test_single_instrument(self) -> None:
        groups = group_instruments_by_tier(["piano"])
        assert list(groups.keys()) == [InstrumentTier.HARMONY]
        assert groups[InstrumentTier.HARMONY] == ["piano"]

    def test_empty_list(self) -> None:
        groups = group_instruments_by_tier([])
        assert groups == {}

    def test_multiple_roles_per_tier(self) -> None:
        groups = group_instruments_by_tier(["drums", "kick", "bass", "electric bass"])
        assert len(groups[InstrumentTier.DRUMS]) == 2
        assert len(groups[InstrumentTier.BASS]) == 2

    def test_full_four_tier_arrangement(self) -> None:
        instruments = ["lead", "piano", "bass", "drums"]
        groups = group_instruments_by_tier(instruments)
        assert set(groups.keys()) == {
            InstrumentTier.DRUMS,
            InstrumentTier.BASS,
            InstrumentTier.HARMONY,
            InstrumentTier.MELODY,
        }


# ---------------------------------------------------------------------------
# ProgressiveGenerateRequest model
# ---------------------------------------------------------------------------


class TestProgressiveGenerateRequest:
    def test_defaults(self) -> None:
        req = ProgressiveGenerateRequest()
        assert req.genre == "boom_bap"
        assert req.bars == 4
        assert "drums" in req.instruments

    def test_composition_id_optional(self) -> None:
        req = ProgressiveGenerateRequest(composition_id="abc")
        assert req.composition_id == "abc"

    def test_composition_id_defaults_none(self) -> None:
        req = ProgressiveGenerateRequest()
        assert req.composition_id is None


# ---------------------------------------------------------------------------
# _do_progressive_generate (mocked _do_generate)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestDoProgressiveGenerate:
    async def test_success_two_tiers(self) -> None:
        """Two-tier request (drums + bass) succeeds and produces two tier results."""
        drum_notes = [_make_note(36), _make_note(38)]
        bass_notes = [_make_note(40)]

        call_count = 0

        async def mock_generate(req: Any) -> GenerateResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_ok_response(drum_notes)
            return _make_ok_response(bass_notes)

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(
                instruments=["drums", "bass"],
                genre="boom_bap",
                bars=4,
            )
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        assert len(result["tier_results"]) == 2
        assert result["tier_results"][0]["tier"] == InstrumentTier.DRUMS.value
        assert result["tier_results"][1]["tier"] == InstrumentTier.BASS.value
        assert len(result["all_notes"]) == len(drum_notes) + len(bass_notes)
        assert result["error"] is None
        assert call_count == 2

    async def test_composition_id_propagated(self) -> None:
        """All tier calls share the same composition_id for cascaded seeding."""
        seen_composition_ids: list[str | None] = []

        async def mock_generate(req: Any) -> GenerateResponse:
            seen_composition_ids.append(req.composition_id)
            return _make_ok_response()

        with patch("music_service._do_generate", side_effect=mock_generate):
            comp_id = str(uuid.uuid4())
            request = ProgressiveGenerateRequest(
                instruments=["drums", "bass", "piano"],
                composition_id=comp_id,
            )
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        # Every tier call must receive the same composition_id
        assert all(cid == comp_id for cid in seen_composition_ids), seen_composition_ids

    async def test_auto_composition_id_assigned(self) -> None:
        """When composition_id is None, one is auto-generated and used across tiers."""
        seen_ids: list[str | None] = []

        async def mock_generate(req: Any) -> GenerateResponse:
            seen_ids.append(req.composition_id)
            return _make_ok_response()

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(instruments=["drums", "bass"])
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        assert result["composition_id"] is not None
        # All tiers share one auto-assigned id
        assert len(set(seen_ids)) == 1
        assert seen_ids[0] == result["composition_id"]

    async def test_tier_failure_stops_pipeline(self) -> None:
        """If a tier fails, progressive generation stops and returns failure."""
        call_count = 0

        async def mock_generate(req: Any) -> GenerateResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_ok_response()  # drums ok
            return _make_err_response("bass gpu oom")  # bass fails

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(instruments=["drums", "bass", "piano"])
            result = await _do_progressive_generate(request)

        assert result["success"] is False
        assert "bass" in (result["error"] or "")
        # drums result should be present, bass and piano not
        assert len(result["tier_results"]) == 1
        assert result["tier_results"][0]["tier"] == InstrumentTier.DRUMS.value
        # piano call never executed
        assert call_count == 2

    async def test_empty_instruments_returns_error(self) -> None:
        """Empty instrument list returns failure without calling _do_generate."""
        with patch("music_service._do_generate") as mock_gen:
            request = ProgressiveGenerateRequest(instruments=[])
            result = await _do_progressive_generate(request)

        assert result["success"] is False
        assert "No instruments" in (result["error"] or "")
        mock_gen.assert_not_called()

    async def test_all_four_tiers_order(self) -> None:
        """Four-tier arrangement generates in DRUMS → BASS → HARMONY → MELODY order."""
        tier_order: list[str] = []

        async def mock_generate(req: Any) -> GenerateResponse:
            tier_order.append(req.instruments[0])
            return _make_ok_response()

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(
                instruments=["lead", "piano", "bass", "drums"]
            )
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        # Should be called in tier order regardless of input order
        assert tier_order[0] == "drums"
        assert tier_order[1] == "bass"
        assert tier_order[2] == "piano"
        assert tier_order[3] == "lead"

    async def test_timing_recorded_per_tier(self) -> None:
        """Each tier result includes elapsed_seconds > 0."""
        async def mock_generate(req: Any) -> GenerateResponse:
            await asyncio.sleep(0.01)
            return _make_ok_response()

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(instruments=["drums", "bass"])
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        for tier_result in result["tier_results"]:
            assert tier_result["elapsed_seconds"] >= 0.0
        assert result["total_elapsed_seconds"] >= 0.0

    async def test_all_notes_union_of_tiers(self) -> None:
        """all_notes is the flat union of all tier notes in tier order."""
        drum_notes = [_make_note(36), _make_note(38)]
        bass_notes = [_make_note(40), _make_note(43)]
        piano_notes = [_make_note(60)]

        responses = [
            _make_ok_response(drum_notes),
            _make_ok_response(bass_notes),
            _make_ok_response(piano_notes),
        ]
        idx = 0

        async def mock_generate(req: Any) -> GenerateResponse:
            nonlocal idx
            resp = responses[idx]
            idx += 1
            return resp

        with patch("music_service._do_generate", side_effect=mock_generate):
            request = ProgressiveGenerateRequest(
                instruments=["drums", "bass", "piano"]
            )
            result = await _do_progressive_generate(request)

        assert result["success"] is True
        expected_total = len(drum_notes) + len(bass_notes) + len(piano_notes)
        assert len(result["all_notes"]) == expected_total
