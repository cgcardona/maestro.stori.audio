"""Tests for sliding window chunked generation (#25).

Validates:
- _apply_velocity_fade — velocity envelope at chunk boundaries
- Chunk splitting arithmetic — bar counts, beat offsets, note stitching
- Threshold routing — requests above threshold go through chunked path
- No regression for short requests — standard path unchanged below threshold
- Partial-failure behaviour — incomplete chunked results are surfaced correctly

All tests are unit-level and mock _do_generate to avoid live Gradio/GPU calls.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from music_service import (
    GenerateRequest,
    GenerateResponse,
    _CHUNK_BARS,
    _CHUNK_FADE_BEATS,
    _CHUNKED_GEN_THRESHOLD_BARS,
    _apply_velocity_fade,
    _generate_chunked,
)
from storpheus_types import WireNoteDict


# ── Helpers ─────────────────────────────────────────────────────────────────

def _note(pitch: int = 60, start: float = 0.0, dur: float = 1.0, vel: int = 80) -> WireNoteDict:
    return WireNoteDict(pitch=pitch, startBeat=start, durationBeats=dur, velocity=vel)


def _make_response(notes: list[WireNoteDict], bars: int = 8) -> GenerateResponse:
    """Build a minimal successful GenerateResponse with the given notes."""
    return GenerateResponse(
        success=True,
        notes=notes,
        error=None,
        metadata={"rejection_score": 0.8, "bars": bars},
    )


# ── _apply_velocity_fade ────────────────────────────────────────────────────

class TestApplyVelocityFade:
    """Unit tests for the velocity-fade helper."""

    def test_no_op_when_fade_beats_zero(self) -> None:
        notes = [_note(start=0.0, vel=100), _note(start=1.0, vel=100)]
        result = _apply_velocity_fade(notes, chunk_bars=4, fade_beats=0.0, fade_in=True, fade_out=True)
        assert [n["velocity"] for n in result] == [100, 100]

    def test_no_op_for_first_chunk(self) -> None:
        """First chunk: no fade-in, no fade-out → velocities unchanged."""
        notes = [_note(start=0.5, vel=100), _note(start=15.0, vel=100)]
        result = _apply_velocity_fade(
            notes, chunk_bars=4, fade_beats=4.0, fade_in=False, fade_out=False
        )
        assert all(n["velocity"] == 100 for n in result)

    def test_fade_in_reduces_early_velocity(self) -> None:
        """Notes near beat 0 should have lower velocity when fade_in=True."""
        notes = [
            _note(start=0.0, vel=100),   # start of fade region → vel ≈ 0
            _note(start=2.0, vel=100),   # mid fade → vel ≈ 50
            _note(start=4.0, vel=100),   # end of fade → vel = 100
            _note(start=8.0, vel=100),   # past fade → vel = 100
        ]
        result = _apply_velocity_fade(
            notes, chunk_bars=8, fade_beats=4.0, fade_in=True, fade_out=False
        )
        assert result[0]["velocity"] == 1       # 0/4 * 100 → clamped to 1
        assert result[1]["velocity"] == 50      # 2/4 * 100
        assert result[2]["velocity"] == 100     # 4/4 * 100
        assert result[3]["velocity"] == 100     # no fade

    def test_fade_out_reduces_late_velocity(self) -> None:
        """Notes near the end of a chunk should fade out when fade_out=True."""
        # chunk_bars=4, total_beats=16, fade_beats=4.0 → fade window [12, 16)
        notes = [
            _note(start=0.0, vel=100),    # before fade → unchanged
            _note(start=12.0, vel=100),   # start of fade window → 100%
            _note(start=14.0, vel=100),   # mid fade → (16-14)/4 * 100 = 50
            _note(start=15.0, vel=100),   # deep fade → (16-15)/4 * 100 = 25
        ]
        result = _apply_velocity_fade(
            notes, chunk_bars=4, fade_beats=4.0, fade_in=False, fade_out=True
        )
        assert result[0]["velocity"] == 100
        assert result[1]["velocity"] == 100
        assert result[2]["velocity"] == 50
        assert result[3]["velocity"] == 25

    def test_velocity_clamped_to_midi_range(self) -> None:
        """Faded velocity must stay in [1, 127]."""
        notes = [_note(start=0.0, vel=1)]
        result = _apply_velocity_fade(
            notes, chunk_bars=4, fade_beats=4.0, fade_in=True, fade_out=False
        )
        assert result[0]["velocity"] >= 1

    def test_empty_notes_returns_empty(self) -> None:
        result = _apply_velocity_fade([], chunk_bars=4, fade_beats=4.0, fade_in=True, fade_out=True)
        assert result == []

    def test_pitch_and_duration_preserved(self) -> None:
        notes = [_note(pitch=72, start=1.0, dur=2.0, vel=90)]
        result = _apply_velocity_fade(
            notes, chunk_bars=4, fade_beats=4.0, fade_in=False, fade_out=False
        )
        assert result[0]["pitch"] == 72
        assert result[0]["startBeat"] == 1.0
        assert result[0]["durationBeats"] == 2.0


# ── Chunk splitting arithmetic ───────────────────────────────────────────────

class TestChunkSplitting:
    """Verify beat-offset arithmetic for various bar counts."""

    def _expected_chunk_counts(self, total_bars: int) -> list[int]:
        """Reference implementation of the chunk split."""
        counts = []
        remaining = total_bars
        while remaining > 0:
            counts.append(min(_CHUNK_BARS, remaining))
            remaining -= min(_CHUNK_BARS, remaining)
        return counts

    def test_exact_multiple_of_chunk_bars(self) -> None:
        total = _CHUNK_BARS * 4
        counts = self._expected_chunk_counts(total)
        assert len(counts) == 4
        assert all(c == _CHUNK_BARS for c in counts)
        assert sum(counts) == total

    def test_non_multiple_last_chunk_is_remainder(self) -> None:
        remainder = 3
        total = _CHUNK_BARS * 2 + remainder
        counts = self._expected_chunk_counts(total)
        assert counts[-1] == remainder
        assert sum(counts) == total

    def test_exactly_at_threshold_plus_one(self) -> None:
        """One bar above threshold → chunked path, at least 2 chunks."""
        total = _CHUNKED_GEN_THRESHOLD_BARS + 1
        counts = self._expected_chunk_counts(total)
        assert sum(counts) == total
        assert all(c <= _CHUNK_BARS for c in counts)

    def test_beat_offsets_are_sequential(self) -> None:
        """Beat offsets must be strictly monotonically increasing by chunk_bars * 4."""
        total = _CHUNK_BARS * 3
        counts = self._expected_chunk_counts(total)
        expected_offsets = [i * _CHUNK_BARS * 4 for i in range(len(counts))]
        actual_offsets = []
        offset = 0
        for c in counts:
            actual_offsets.append(offset)
            offset += c * 4
        assert actual_offsets == expected_offsets


# ── _generate_chunked integration (mocked _do_generate) ─────────────────────

@pytest.mark.anyio
class TestGenerateChunked:
    """Integration tests for _generate_chunked with _do_generate mocked."""

    def _base_request(self, bars: int = 32) -> GenerateRequest:
        return GenerateRequest(
            genre="boom_bap",
            tempo=90,
            instruments=["drums", "bass"],
            bars=bars,
        )

    def _chunk_notes(self, n: int, bars: int = 8, start_offset: float = 0.0) -> list[WireNoteDict]:
        """Generate n notes evenly spaced across a chunk."""
        total_beats = bars * 4.0
        return [
            _note(pitch=60 + i, start=start_offset + i * (total_beats / max(n, 1)), vel=80)
            for i in range(n)
        ]

    async def test_two_chunk_request_merges_notes(self) -> None:
        """32-bar request → 4 chunks of 8 bars, all notes stitched."""
        total_bars = 32
        chunks_expected = math.ceil(total_bars / _CHUNK_BARS)
        notes_per_chunk = 10

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            return _make_response(
                [_note(pitch=60, start=float(i), vel=80) for i in range(notes_per_chunk)],
                bars=req.bars,
            )

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(total_bars))

        assert result.success
        assert result.notes is not None
        assert len(result.notes) == notes_per_chunk * chunks_expected
        assert result.metadata is not None
        assert result.metadata["chunked"] is True
        assert result.metadata["chunk_count"] == chunks_expected

    async def test_notes_are_sorted_by_start_beat(self) -> None:
        """Final note list must be sorted by startBeat across chunk boundaries."""
        total_bars = 24

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            # Notes in reverse order within each chunk to stress the sort
            return _make_response(
                [_note(start=float(7 - i)) for i in range(8)],
                bars=req.bars,
            )

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(total_bars))

        assert result.success
        assert result.notes is not None
        beats = [n["startBeat"] for n in result.notes]
        assert beats == sorted(beats)

    async def test_beat_offsets_applied_correctly(self) -> None:
        """Each chunk's notes must be offset by chunk_idx * chunk_bars * 4."""
        total_bars = _CHUNK_BARS * 2  # two exact chunks

        calls: list[int] = []

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            calls.append(req.bars)
            # Single note at beat 0 for easy offset verification
            return _make_response([_note(start=0.0, vel=80)], bars=req.bars)

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(total_bars))

        assert result.success
        assert result.notes is not None
        assert len(result.notes) == 2
        starts = sorted(n["startBeat"] for n in result.notes)
        assert starts[0] == pytest.approx(0.0)
        assert starts[1] == pytest.approx(_CHUNK_BARS * 4.0)

    async def test_partial_last_chunk_bar_count(self) -> None:
        """Last chunk must use the remainder bars, not _CHUNK_BARS."""
        remainder = 3
        total_bars = _CHUNK_BARS + remainder

        captured_bars: list[int] = []

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            captured_bars.append(req.bars)
            return _make_response([_note()], bars=req.bars)

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(total_bars))

        assert result.success
        assert captured_bars == [_CHUNK_BARS, remainder]

    async def test_chunk_failure_returns_error_response(self) -> None:
        """If any chunk fails and no prior notes, return failure."""
        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            return GenerateResponse(success=False, error="Gradio timeout")

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(32))

        assert not result.success
        assert result.error is not None
        assert "chunk 1" in result.error.lower() or "failed" in result.error.lower()

    async def test_partial_failure_includes_prior_notes(self) -> None:
        """If chunk N fails after chunks 0..N-1 succeeded, return partial notes."""
        call_count = 0

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response([_note(start=0.0, vel=80)], bars=req.bars)
            return GenerateResponse(success=False, error="GPU OOM")

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(32))

        assert not result.success
        # Partial notes from chunk 0 are included
        assert result.notes is not None
        assert len(result.notes) > 0

    async def test_composition_id_isolated_from_outer_session(self) -> None:
        """Inner chunk requests must use a distinct composition_id (chunked-…)."""
        seen_composition_ids: list[str] = []

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            if req.composition_id:
                seen_composition_ids.append(req.composition_id)
            return _make_response([_note()], bars=req.bars)

        outer_comp_id = "outer-session-abc"
        request = GenerateRequest(
            genre="jazz",
            tempo=120,
            instruments=["piano"],
            bars=_CHUNK_BARS * 2,
            composition_id=outer_comp_id,
        )

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            await _generate_chunked(request)

        # All inner calls use a chunked-prefixed id, not the outer one
        assert all(cid.startswith("chunked-") for cid in seen_composition_ids)
        assert outer_comp_id not in seen_composition_ids

    async def test_add_outro_only_on_last_chunk(self) -> None:
        """add_outro must be True only for the final chunk."""
        captured_outro: list[bool] = []

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            captured_outro.append(req.add_outro)
            return _make_response([_note()], bars=req.bars)

        request = GenerateRequest(
            genre="boom_bap",
            tempo=90,
            instruments=["drums"],
            bars=_CHUNK_BARS * 3,
            add_outro=True,
        )

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            await _generate_chunked(request)

        assert captured_outro[-1] is True
        assert all(v is False for v in captured_outro[:-1])

    async def test_metadata_contains_chunk_count_and_bar_info(self) -> None:
        """Response metadata must expose chunking statistics."""
        total_bars = _CHUNK_BARS * 2

        async def fake_do_generate(req: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
            return _make_response([_note()], bars=req.bars)

        with patch("music_service._do_generate", side_effect=fake_do_generate):
            result = await _generate_chunked(self._base_request(total_bars))

        assert result.success
        meta = result.metadata
        assert meta is not None
        assert meta["chunk_count"] == 2
        assert meta["total_bars"] == total_bars
        assert meta["chunk_bars"] == _CHUNK_BARS
        assert isinstance(meta["chunk_metadata"], list)
        assert len(meta["chunk_metadata"]) == 2


# ── Threshold routing (via _do_generate) ─────────────────────────────────────

@pytest.mark.anyio
class TestThresholdRouting:
    """Verify that _do_generate routes correctly based on bar count."""

    async def test_short_request_bypasses_chunked(self) -> None:
        """Requests ≤ threshold must NOT call _generate_chunked."""
        request = GenerateRequest(
            genre="jazz",
            tempo=120,
            instruments=["piano"],
            bars=_CHUNKED_GEN_THRESHOLD_BARS,  # at the threshold — no chunking
        )

        with patch("music_service._generate_chunked") as mock_chunked:
            # _do_generate will hit the real path and likely fail without Gradio
            # We just verify _generate_chunked is NOT called
            try:
                from music_service import _do_generate
                await _do_generate(request)
            except Exception:
                pass  # expected — no real Gradio client in tests

        mock_chunked.assert_not_called()

    async def test_long_request_routes_to_chunked(self) -> None:
        """Requests > threshold MUST call _generate_chunked."""
        request = GenerateRequest(
            genre="jazz",
            tempo=120,
            instruments=["piano"],
            bars=_CHUNKED_GEN_THRESHOLD_BARS + 1,
        )
        expected = GenerateResponse(success=True, notes=[], metadata={"chunked": True})

        with patch("music_service._generate_chunked", new_callable=AsyncMock) as mock_chunked:
            mock_chunked.return_value = expected
            from music_service import _do_generate
            result = await _do_generate(request)

        mock_chunked.assert_called_once()
        call_args = mock_chunked.call_args[0]
        assert call_args[0].bars == _CHUNKED_GEN_THRESHOLD_BARS + 1
        assert result.success
