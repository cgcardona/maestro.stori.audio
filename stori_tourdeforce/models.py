"""Data models — event envelope, run state, metrics, scenarios."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


# ── Enums ──────────────────────────────────────────────────────────────────


class Component(str, Enum):
    MAESTRO = "maestro"
    STORPHEUS = "storpheus"
    MUSE = "muse"
    PROMPT_SERVICE = "prompt_service"
    CLIENT = "client"


class EventType(str, Enum):
    HTTP_REQUEST = "http_request"
    HTTP_RESPONSE = "http_response"
    SSE_EVENT = "sse_event"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MIDI_METRIC = "midi_metric"
    MUSE_COMMIT = "muse_commit"
    MUSE_BRANCH = "muse_branch"
    MUSE_MERGE = "muse_merge"
    ERROR = "error"
    TIMING = "timing"
    RUN_START = "run_start"
    RUN_END = "run_end"


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class RunStatus(str, Enum):
    SUCCESS = "success"
    MAESTRO_ERROR = "maestro_error"
    STORPHEUS_ERROR = "storpheus_error"
    MIDI_QUALITY_FAIL = "midi_quality_fail"
    MUSE_ERROR = "muse_error"
    MERGE_CONFLICT = "merge_conflict"
    TIMEOUT = "timeout"
    PROMPT_ERROR = "prompt_error"


# ── Unified Event Envelope ────────────────────────────────────────────────


@dataclass
class Event:
    """Unified event envelope — everything gets recorded as one of these."""

    ts: str
    run_id: str
    scenario: str
    component: str
    event_type: str
    trace_id: str
    span_id: str
    parent_span_id: str
    severity: str
    tags: dict[str, str]
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "component": self.component,
            "event_type": self.event_type,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "severity": self.severity,
            "tags": self.tags,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()


# ── Trace Context ─────────────────────────────────────────────────────────


@dataclass
class TraceContext:
    """Per-run trace context with span hierarchy."""

    trace_id: str = field(default_factory=lambda: f"t_{uuid.uuid4().hex[:16]}")
    _span_stack: list[str] = field(default_factory=list)

    @property
    def current_span(self) -> str:
        return self._span_stack[-1] if self._span_stack else ""

    def new_span(self, label: str = "") -> str:
        span_id = f"s_{uuid.uuid4().hex[:12]}"
        self._span_stack.append(span_id)
        return span_id

    def end_span(self) -> str:
        return self._span_stack.pop() if self._span_stack else ""

    @property
    def parent_span(self) -> str:
        return self._span_stack[-2] if len(self._span_stack) >= 2 else ""


# ── Prompt Models ─────────────────────────────────────────────────────────


@dataclass
class Prompt:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── SSE Event (parsed) ───────────────────────────────────────────────────


@dataclass
class ParsedSSEEvent:
    """A parsed SSE event from the Maestro stream."""

    event_type: str
    data: dict[str, Any]
    raw: str
    seq: int = -1


# ── Run Result ────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    """Complete result of a single Tour de Force run."""

    run_id: str
    status: RunStatus
    prompt: Prompt | None = None
    start_ts: str = ""
    end_ts: str = ""
    duration_ms: float = 0.0
    seed: int = 0
    scenario: str = "compose->commit->edit->branch->merge"

    # Maestro
    sse_events: list[ParsedSSEEvent] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    maestro_trace_id: str = ""
    intent: str = ""
    execution_mode: str = ""

    # Orpheus
    storpheus_job_id: str = ""
    storpheus_queue_wait_ms: float = 0.0
    storpheus_infer_ms: float = 0.0
    storpheus_total_ms: float = 0.0
    storpheus_retries: int = 0
    storpheus_output_bytes: int = 0
    storpheus_note_count: int = 0

    # MIDI
    midi_metrics: dict[str, Any] = field(default_factory=dict)
    midi_path: str = ""

    # MUSE
    muse_commit_ids: list[str] = field(default_factory=list)
    muse_merge_ids: list[str] = field(default_factory=list)
    muse_branch_names: list[str] = field(default_factory=list)
    muse_conflict_count: int = 0
    muse_checkout_count: int = 0
    muse_checkout_blocked: int = 0
    muse_drift_detected: bool = False
    muse_force_recoveries: int = 0

    # Errors
    error_type: str = ""
    error_message: str = ""
    last_sse_events: list[dict[str, Any]] = field(default_factory=list)

    # Artifacts (downloaded from Storpheus)
    artifact_files: list[str] = field(default_factory=list)

    # Payload hashes
    payload_hashes: dict[str, str] = field(default_factory=dict)


# ── MIDI Metrics ──────────────────────────────────────────────────────────


@dataclass
class MidiMetrics:
    """Comprehensive MIDI quality metrics."""

    # Basic structure
    duration_sec: float = 0.0
    tempo: float = 120.0
    time_sig_changes: int = 0
    key_signature: str = ""
    track_count: int = 0
    instrument_count: int = 0
    note_count_total: int = 0
    notes_per_track: dict[str, int] = field(default_factory=dict)
    polyphony_estimate: float = 0.0

    # Musical plausibility
    pitch_range: dict[str, tuple[int, int]] = field(default_factory=dict)
    velocity_mean: float = 0.0
    velocity_stdev: float = 0.0
    velocity_range: tuple[int, int] = (0, 127)
    rhythmic_density_per_bar: list[float] = field(default_factory=list)
    note_length_distribution: dict[str, float] = field(default_factory=dict)
    ioi_distribution: dict[str, float] = field(default_factory=dict)
    repetition_score: float = 0.0

    # Harmonic coherence
    pitch_class_entropy: float = 0.0
    chord_change_rate: float = 0.0

    # Humanization
    timing_deviation: float = 0.0
    velocity_variance_pattern: float = 0.0

    # Garbage checks
    zero_length_notes: int = 0
    extreme_pitches: int = 0
    impossible_velocities: int = 0
    note_spam_regions: int = 0
    empty_tracks: int = 0

    # Composite score
    quality_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_sec": self.duration_sec,
            "tempo": self.tempo,
            "time_sig_changes": self.time_sig_changes,
            "key_signature": self.key_signature,
            "track_count": self.track_count,
            "instrument_count": self.instrument_count,
            "note_count_total": self.note_count_total,
            "notes_per_track": self.notes_per_track,
            "polyphony_estimate": self.polyphony_estimate,
            "pitch_range": {k: list(v) for k, v in self.pitch_range.items()},
            "velocity_mean": self.velocity_mean,
            "velocity_stdev": self.velocity_stdev,
            "velocity_range": list(self.velocity_range),
            "rhythmic_density_per_bar": self.rhythmic_density_per_bar,
            "note_length_distribution": self.note_length_distribution,
            "ioi_distribution": self.ioi_distribution,
            "repetition_score": self.repetition_score,
            "pitch_class_entropy": self.pitch_class_entropy,
            "chord_change_rate": self.chord_change_rate,
            "timing_deviation": self.timing_deviation,
            "velocity_variance_pattern": self.velocity_variance_pattern,
            "zero_length_notes": self.zero_length_notes,
            "extreme_pitches": self.extreme_pitches,
            "impossible_velocities": self.impossible_velocities,
            "note_spam_regions": self.note_spam_regions,
            "empty_tracks": self.empty_tracks,
            "quality_score": self.quality_score,
        }


# ── Utilities ─────────────────────────────────────────────────────────────


def stable_hash(*args: Any) -> int:
    """Deterministic hash for prompt selection and content addressing."""
    raw = json.dumps(args, sort_keys=True, default=str)
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16)


def sha256_payload(data: bytes | str) -> str:
    """SHA-256 hash of a payload, returned as sha256:hex."""
    if isinstance(data, str):
        data = data.encode()
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def make_run_id(index: int) -> str:
    return f"r_{index:06d}"
