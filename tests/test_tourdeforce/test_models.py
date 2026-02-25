"""Tests for data models and utilities."""

from __future__ import annotations

from stori_tourdeforce.models import (
    Event,
    MidiMetrics,
    RunResult,
    RunStatus,
    TraceContext,
    make_run_id,
    sha256_payload,
)


class TestTraceContext:

    def test_trace_id_generated(self) -> None:

        t = TraceContext()
        assert t.trace_id.startswith("t_")

    def test_span_hierarchy(self) -> None:

        t = TraceContext()
        s1 = t.new_span("outer")
        assert t.current_span == s1
        assert t.parent_span == ""

        s2 = t.new_span("inner")
        assert t.current_span == s2
        assert t.parent_span == s1

        ended = t.end_span()
        assert ended == s2
        assert t.current_span == s1


class TestEvent:

    def test_to_json(self) -> None:

        e = Event(
            ts="2026-02-24T00:00:00Z",
            run_id="r_000001",
            scenario="test",
            component="client",
            event_type="timing",
            trace_id="t_abc",
            span_id="s_123",
            parent_span_id="",
            severity="INFO",
            tags={"key": "val"},
            data={"ms": 42},
        )
        j = e.to_json()
        assert '"run_id": "r_000001"' in j
        assert '"ms": 42' in j


class TestRunId:

    def test_format(self) -> None:

        assert make_run_id(0) == "r_000000"
        assert make_run_id(42) == "r_000042"
        assert make_run_id(999999) == "r_999999"


class TestSha256:

    def test_string_hash(self) -> None:

        h = sha256_payload("hello")
        assert h.startswith("sha256:")
        assert len(h) == 7 + 64

    def test_bytes_hash(self) -> None:

        h = sha256_payload(b"hello")
        assert h.startswith("sha256:")

    def test_deterministic(self) -> None:

        h1 = sha256_payload("test payload")
        h2 = sha256_payload("test payload")
        assert h1 == h2


class TestMidiMetrics:

    def test_to_dict(self) -> None:

        m = MidiMetrics(note_count_total=100, quality_score=75.0)
        d = m.to_dict()
        assert d["note_count_total"] == 100
        assert d["quality_score"] == 75.0

    def test_pitch_range_serialization(self) -> None:

        m = MidiMetrics(pitch_range={"all": (24, 96)})
        d = m.to_dict()
        assert d["pitch_range"]["all"] == [24, 96]


class TestRunResult:

    def test_default_status(self) -> None:

        r = RunResult(run_id="r_000001", status=RunStatus.SUCCESS)
        assert r.status == RunStatus.SUCCESS
        assert r.sse_events == []
        assert r.midi_metrics == {}

    def test_error_fields(self) -> None:

        r = RunResult(
            run_id="r_000002",
            status=RunStatus.MAESTRO_ERROR,
            error_type="timeout",
            error_message="Stream timed out",
        )
        assert r.error_type == "timeout"
