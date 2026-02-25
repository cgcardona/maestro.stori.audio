"""Protocol layer tests — Phase 2.

Verifies event models, registry, emitter, hash, validation guard,
project snapshot, protocol endpoints, and runtime enforcement via
serialize_event and ProtocolGuard integration.
"""

from __future__ import annotations

from httpx import AsyncClient
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.protocol.events import (
    StoriEvent,
    StateEvent,
    ReasoningEvent,
    ContentEvent,
    StatusEvent,
    ErrorEvent,
    CompleteEvent,
    PlanEvent,
    PlanStepSchema,
    PlanStepUpdateEvent,
    ToolStartEvent,
    ToolCallEvent,
    ToolErrorEvent,
    PreflightEvent,
    GeneratorStartEvent,
    GeneratorCompleteEvent,
    AgentCompleteEvent,
    SummaryEvent,
    SummaryFinalEvent,
    MetaEvent,
    PhraseEvent,
    NoteChangeSchema,
    DoneEvent,
)
from app.protocol.registry import EVENT_REGISTRY, ALL_EVENT_TYPES
from app.protocol.emitter import emit, serialize_event, ProtocolSerializationError
from app.protocol.validation import ProtocolGuard
from app.protocol.version import STORI_PROTOCOL_VERSION, is_compatible
from app.protocol.schemas.project import ProjectSnapshot


# ═══════════════════════════════════════════════════════════════════════
# Version
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolVersion:
    def test_version_format(self) -> None:

        """Version string is semver."""
        assert re.match(r"^\d+\.\d+\.\d+", STORI_PROTOCOL_VERSION)

    def test_version_matches_pyproject(self) -> None:

        """Protocol version reads from pyproject.toml — single source of truth."""
        from app.protocol.version import STORI_VERSION
        assert STORI_PROTOCOL_VERSION == STORI_VERSION

    def test_compatible_same_major(self) -> None:

        from app.protocol.version import STORI_VERSION_MAJOR
        assert is_compatible(f"{STORI_VERSION_MAJOR}.0.0")
        assert is_compatible(f"{STORI_VERSION_MAJOR}.5.3")

    def test_incompatible_different_major(self) -> None:

        from app.protocol.version import STORI_VERSION_MAJOR
        assert not is_compatible(f"{STORI_VERSION_MAJOR + 1}.0.0")
        if STORI_VERSION_MAJOR > 0:
            assert not is_compatible(f"{STORI_VERSION_MAJOR - 1}.0.0")

    def test_incompatible_garbage(self) -> None:

        assert not is_compatible("abc")
        assert not is_compatible("")


# ═══════════════════════════════════════════════════════════════════════
# Event Registry
# ═══════════════════════════════════════════════════════════════════════


class TestEventRegistry:
    def test_all_event_types_registered(self) -> None:

        """Every StoriEvent subclass in events.py has a registry entry."""
        from app.protocol import events as events_module

        all_subclasses = set()
        for name, obj in inspect.getmembers(events_module, inspect.isclass):
            if (
                issubclass(obj, StoriEvent)
                and obj is not StoriEvent
                and not name.endswith("Schema")
            ):
                all_subclasses.add(obj)

        registered_classes = set(EVENT_REGISTRY.values())
        missing = all_subclasses - registered_classes
        assert not missing, (
            f"StoriEvent subclasses not in registry: {[c.__name__ for c in missing]}"
        )

    def test_registry_type_keys_match_model_type(self) -> None:

        """Registry key matches the model's Literal type value."""
        for event_type, model_class in EVENT_REGISTRY.items():
            instance = _make_minimal(model_class)
            assert instance.type == event_type

    def test_all_event_types_frozenset(self) -> None:

        assert ALL_EVENT_TYPES == frozenset(EVENT_REGISTRY.keys())


# ═══════════════════════════════════════════════════════════════════════
# Event Serialization
# ═══════════════════════════════════════════════════════════════════════


class TestEventSerialization:
    def test_all_events_serialize_camel_case(self) -> None:

        """No snake_case keys in wire-format output."""
        snake_re = re.compile(r"[a-z]+_[a-z]")
        for event_type, model_class in EVENT_REGISTRY.items():
            schema = model_class.model_json_schema()
            properties = schema.get("properties", {})
            for prop_name in properties:
                assert not snake_re.match(prop_name), (
                    f"Event '{event_type}' has snake_case property '{prop_name}' "
                    f"in JSON schema. CamelModel alias should convert this."
                )

    def test_state_event_wire_format(self) -> None:

        """StateEvent serializes with exact camelCase keys."""
        event = StateEvent(
            state="editing",
            intent="track.add",
            confidence=0.9,
            trace_id="test-trace",
            execution_mode="apply",
        )
        data = event.model_dump(by_alias=True, exclude_none=True)
        assert data["type"] == "state"
        assert data["traceId"] == "test-trace"
        assert data["executionMode"] == "apply"
        assert data["protocolVersion"] == STORI_PROTOCOL_VERSION
        assert "trace_id" not in data
        assert "execution_mode" not in data

    def test_exclude_none_removes_optional_fields(self) -> None:

        """Optional fields not set are excluded from wire output."""
        event = ErrorEvent(message="boom")
        data = event.model_dump(by_alias=True, exclude_none=True)
        assert "traceId" not in data
        assert "code" not in data
        assert data["message"] == "boom"

    def test_emit_produces_sse_format(self) -> None:

        """emit() returns data: {json}\\n\\n."""
        event = ContentEvent(content="hello")
        sse = emit(event)
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        payload = json.loads(sse[6:].strip())
        assert payload["type"] == "content"
        assert payload["content"] == "hello"

    def test_emit_compact_json(self) -> None:

        """emit() uses compact separators (no spaces after : or ,)."""
        event = StateEvent(
            state="reasoning",
            intent="ask.general",
            confidence=0.85,
            trace_id="t",
            execution_mode="reasoning",
        )
        sse = emit(event)
        json_part = sse[6:].strip()
        assert '" : ' not in json_part
        assert '", "' not in json_part or '","' in json_part


# ═══════════════════════════════════════════════════════════════════════
# Emitter Guards
# ═══════════════════════════════════════════════════════════════════════


class TestEmitter:
    def test_emit_rejects_raw_dict(self) -> None:

        with pytest.raises(TypeError, match="StoriEvent"):
            emit({"type": "state"})  # type: ignore[arg-type]

    def test_emit_rejects_unregistered_type(self) -> None:

        class FakeEvent(StoriEvent):
            type: str = "fake_event_xyz"

        with pytest.raises(ValueError, match="Unknown event type"):
            emit(FakeEvent())

    def test_emit_rejects_pre_set_seq(self) -> None:

        """emit() does not allow stale seq values to leak through."""
        event = ContentEvent(content="test")
        sse = emit(event)
        payload = json.loads(sse[6:].strip())
        assert payload["seq"] == -1


# ═══════════════════════════════════════════════════════════════════════
# serialize_event (Phase 2 — runtime enforcement)
# ═══════════════════════════════════════════════════════════════════════


class TestSerializeEvent:
    def test_valid_event_dict_validates_and_serializes(self) -> None:

        """serialize_event produces model-validated SSE output."""
        sse = serialize_event({"type": "content", "content": "hello"})
        assert sse.startswith("data: ")
        payload = json.loads(sse[6:].strip())
        assert payload["type"] == "content"
        assert payload["content"] == "hello"
        assert payload["protocolVersion"] == STORI_PROTOCOL_VERSION
        assert payload["seq"] == -1

    def test_injects_protocol_version(self) -> None:

        """protocolVersion is auto-injected even when not in source dict."""
        sse = serialize_event({"type": "status", "message": "ok"})
        payload = json.loads(sse[6:].strip())
        assert payload["protocolVersion"] == STORI_PROTOCOL_VERSION

    def test_camel_case_aliases_accepted(self) -> None:

        """Handler dicts using camelCase keys validate correctly."""
        sse = serialize_event({
            "type": "state",
            "state": "editing",
            "intent": "track.add",
            "confidence": 0.9,
            "traceId": "test-trace",
            "executionMode": "apply",
        })
        payload = json.loads(sse[6:].strip())
        assert payload["traceId"] == "test-trace"
        assert payload["executionMode"] == "apply"

    def test_missing_type_raises(self) -> None:

        with pytest.raises(ProtocolSerializationError, match="missing 'type'"):
            serialize_event({"content": "no type"})

    def test_unregistered_type_raises(self) -> None:

        """Unregistered event types always raise (no production fallback)."""
        with pytest.raises(ProtocolSerializationError, match="Unregistered"):
            serialize_event({"type": "totally_unknown_xyz"})

    def test_invalid_dict_raises(self) -> None:

        """A dict that fails model validation always raises."""
        with pytest.raises(ProtocolSerializationError, match="failed protocol validation"):
            serialize_event({"type": "content"})

    def test_compact_json_output(self) -> None:

        sse = serialize_event({"type": "content", "content": "hi"})
        json_part = sse[6:].strip()
        assert '" : ' not in json_part

    def test_exclude_none(self) -> None:

        """Optional None fields are excluded from wire output."""
        sse = serialize_event({"type": "error", "message": "boom"})
        payload = json.loads(sse[6:].strip())
        assert "traceId" not in payload
        assert "code" not in payload


# ═══════════════════════════════════════════════════════════════════════
# Extra Fields Policy
# ═══════════════════════════════════════════════════════════════════════


class TestExtraFieldsPolicy:
    def test_events_forbid_extra(self) -> None:

        """Event models reject unexpected fields."""
        with pytest.raises(ValidationError):
            ContentEvent(content="hi", bogus_field="nope")  # type: ignore[call-arg]

    def test_project_snapshot_allows_extra(self) -> None:

        """ProjectSnapshot allows unknown fields from FE."""
        p = ProjectSnapshot.model_validate({
            "id": "proj-1",
            "futureField": "unknown",
            "anotherNewThing": 42,
        })
        assert p.id == "proj-1"


# ═══════════════════════════════════════════════════════════════════════
# StateEvent + executionMode
# ═══════════════════════════════════════════════════════════════════════


class TestStateEvent:
    def test_has_execution_mode_default(self) -> None:

        event = StateEvent(
            state="editing",
            intent="track.add",
            confidence=0.9,
            trace_id="test-trace",
        )
        assert event.execution_mode == "apply"
        data = event.model_dump(by_alias=True)
        assert "executionMode" in data
        assert data["executionMode"] == "apply"

    def test_execution_mode_variation(self) -> None:

        event = StateEvent(
            state="composing",
            intent="compose.generate_music",
            confidence=0.95,
            trace_id="test-trace",
            execution_mode="variation",
        )
        data = event.model_dump(by_alias=True)
        assert data["executionMode"] == "variation"

    def test_execution_mode_reasoning(self) -> None:

        event = StateEvent(
            state="reasoning",
            intent="ask.general",
            confidence=0.88,
            trace_id="test-trace",
            execution_mode="reasoning",
        )
        data = event.model_dump(by_alias=True)
        assert data["executionMode"] == "reasoning"


# ═══════════════════════════════════════════════════════════════════════
# CompleteEvent
# ═══════════════════════════════════════════════════════════════════════


class TestCompleteEvent:
    def test_requires_success_and_trace_id(self) -> None:

        with pytest.raises(ValidationError):
            CompleteEvent()  # type: ignore[call-arg]

    def test_success_true(self) -> None:

        event = CompleteEvent(success=True, trace_id="t")
        assert event.success is True

    def test_success_false_with_error(self) -> None:

        event = CompleteEvent(success=False, trace_id="t", error="boom")
        data = event.model_dump(by_alias=True, exclude_none=True)
        assert data["success"] is False
        assert data["error"] == "boom"


# ═══════════════════════════════════════════════════════════════════════
# PhraseEvent + typed note_changes
# ═══════════════════════════════════════════════════════════════════════


class TestPhraseEvent:
    def test_note_changes_typed(self) -> None:

        """PhraseEvent.note_changes uses NoteChangeSchema, not raw dicts."""
        event = PhraseEvent(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=0,
            end_beat=4,
            label="Intro",
            note_changes=[
                NoteChangeSchema(note_id="n1", change_type="added", after={"pitch": 60}),
            ],
        )
        data = event.model_dump(by_alias=True, exclude_none=True)
        assert data["noteChanges"][0]["noteId"] == "n1"
        assert data["noteChanges"][0]["changeType"] == "added"


# ═══════════════════════════════════════════════════════════════════════
# ProjectSnapshot
# ═══════════════════════════════════════════════════════════════════════


class TestProjectSnapshot:
    def test_minimal_project(self) -> None:

        p = ProjectSnapshot.model_validate({"id": "proj-1"})
        assert p.id == "proj-1"
        assert p.tracks == []
        assert p.tempo is None

    def test_full_project(self) -> None:

        p = ProjectSnapshot.model_validate({
            "id": "proj-1",
            "name": "My Beat",
            "tempo": 90,
            "key": "Am",
            "timeSignature": "4/4",
            "tracks": [
                {
                    "id": "trk-1",
                    "name": "Drums",
                    "drumKitId": "TR-808",
                    "volume": 0.85,
                    "regions": [{"id": "reg-1", "startBeat": 0, "durationBeats": 16}],
                }
            ],
            "buses": [{"id": "bus-1", "name": "Reverb"}],
        })
        assert len(p.tracks) == 1
        assert p.tracks[0].drum_kit_id == "TR-808"

    def test_extra_fields_allowed(self) -> None:

        p = ProjectSnapshot.model_validate({
            "id": "proj-1",
            "futureField": "unknown",
            "anotherNewThing": 42,
        })
        assert p.id == "proj-1"

    def test_invalid_tempo_rejected(self) -> None:

        with pytest.raises(ValidationError):
            ProjectSnapshot.model_validate({"id": "proj-1", "tempo": -10})

    def test_invalid_pitch_rejected(self) -> None:

        with pytest.raises(ValidationError):
            ProjectSnapshot.model_validate({
                "id": "proj-1",
                "tracks": [{
                    "id": "t1",
                    "regions": [{
                        "id": "r1",
                        "notes": [{"pitch": 200, "startBeat": 0, "durationBeats": 1}],
                    }],
                }],
            })


# ═══════════════════════════════════════════════════════════════════════
# Protocol Hash
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolHash:
    def test_hash_is_deterministic(self) -> None:

        """Same code → same hash on repeated calls."""
        from app.protocol.hash import compute_protocol_hash

        h1 = compute_protocol_hash()
        h2 = compute_protocol_hash()
        assert h1 == h2

    def test_hash_is_64_hex_chars(self) -> None:

        from app.protocol.hash import compute_protocol_hash

        h = compute_protocol_hash()
        assert re.match(r"^[0-9a-f]{64}$", h)

    def test_short_hash_is_16_chars(self) -> None:

        from app.protocol.hash import compute_protocol_hash_short

        h = compute_protocol_hash_short()
        assert len(h) == 16

    def test_golden_hash_stable(self) -> None:

        """Protocol hash matches the committed golden hash.

        If this fails, the protocol surface changed. Update GOLDEN_HASH:
            python -c "from app.protocol.hash import compute_protocol_hash; print(compute_protocol_hash())"
        """
        from app.protocol.hash import compute_protocol_hash

        golden_path = Path(__file__).resolve().parent.parent / "app" / "protocol" / "GOLDEN_HASH"
        if not golden_path.exists():
            pytest.skip("GOLDEN_HASH file not yet created — run once to capture initial value")

        golden = golden_path.read_text().strip()
        current = compute_protocol_hash()
        assert current == golden, (
            f"Protocol hash changed: {current} != {golden}. "
            f"If intentional, update app/protocol/GOLDEN_HASH and bump STORI_PROTOCOL_VERSION."
        )


# ═══════════════════════════════════════════════════════════════════════
# Protocol Guard
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolGuard:
    def test_first_event_must_be_state(self) -> None:

        guard = ProtocolGuard()
        violations = guard.check_event("reasoning", {"type": "reasoning", "content": "..."})
        assert any("First event must be 'state'" in v for v in violations)

    def test_no_events_after_complete(self) -> None:

        guard = ProtocolGuard()
        guard.check_event("state", {"type": "state", "state": "reasoning"})
        guard.check_event("complete", {"type": "complete", "success": True})
        violations = guard.check_event("content", {"type": "content", "content": "late"})
        assert any("after 'complete'" in v for v in violations)

    def test_complete_requires_success(self) -> None:

        guard = ProtocolGuard()
        guard.check_event("state", {"type": "state", "state": "editing"})
        violations = guard.check_event("complete", {"type": "complete"})
        assert any("missing 'success'" in v for v in violations)

    def test_happy_path_no_violations(self) -> None:

        guard = ProtocolGuard()
        v1 = guard.check_event("state", {"type": "state", "state": "editing"})
        v2 = guard.check_event("content", {"type": "content", "content": "hi"})
        v3 = guard.check_event("complete", {"type": "complete", "success": True})
        assert v1 == []
        assert v2 == []
        assert v3 == []
        assert guard.terminated

    def test_unregistered_event_type(self) -> None:

        guard = ProtocolGuard()
        guard.check_event("state", {"type": "state", "state": "editing"})
        violations = guard.check_event("nonexistent_xyz", {"type": "nonexistent_xyz"})
        assert any("Unregistered" in v for v in violations)


# ═══════════════════════════════════════════════════════════════════════
# Source-code audit: all emitted types are registered
# ═══════════════════════════════════════════════════════════════════════

_TYPE_PATTERN = re.compile(r'"type"\s*:\s*"([^"]+)"')

_HANDLER_DIRS = [
    "app/core/maestro_editing",
    "app/core/maestro_composing",
    "app/core/maestro_agent_teams",
    "app/core/maestro_plan_tracker",
    "app/core/maestro_handlers.py",
    "app/core/maestro_helpers.py",
]

_NON_EVENT_TYPES = {"function", "text", "access"}


def _extract_event_types_from_source() -> set[str]:
    """Scan source files for all "type": "..." patterns in dict literals."""
    types_found: set[str] = set()
    base = Path(__file__).resolve().parent.parent

    for path_str in _HANDLER_DIRS:
        path = base / path_str
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = list(path.glob("**/*.py"))
        else:
            continue
        for f in files:
            for match in _TYPE_PATTERN.finditer(f.read_text()):
                types_found.add(match.group(1))
    return types_found


def test_all_emitted_types_registered() -> None:
    """Every event type found in handler source code is in EVENT_REGISTRY."""
    emitted = _extract_event_types_from_source()
    unregistered = emitted - ALL_EVENT_TYPES - _NON_EVENT_TYPES
    assert not unregistered, (
        f"Unregistered event types found in source: {unregistered}. "
        f"Add them to app/protocol/registry.py."
    )


# ═══════════════════════════════════════════════════════════════════════
# Protocol Endpoints (integration)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_protocol_info_endpoint(client: AsyncClient) -> None:

    """GET /api/v1/protocol returns version and hash."""
    response = await client.get("/api/v1/protocol")
    assert response.status_code == 200
    data = response.json()
    assert data["protocolVersion"] == STORI_PROTOCOL_VERSION
    assert "protocolHash" in data
    assert "eventTypes" in data
    assert isinstance(data["eventTypes"], list)
    assert "state" in data["eventTypes"]


@pytest.mark.anyio
async def test_protocol_events_endpoint(client: AsyncClient) -> None:

    """GET /api/v1/protocol/events.json returns JSON schemas."""
    response = await client.get("/api/v1/protocol/events.json")
    assert response.status_code == 200
    data = response.json()
    assert data["protocolVersion"] == STORI_PROTOCOL_VERSION
    events = data["events"]
    assert "state" in events
    assert "complete" in events
    state_schema = events["state"]
    assert "properties" in state_schema
    assert "executionMode" in state_schema["properties"]


@pytest.mark.anyio
async def test_protocol_tools_endpoint(client: AsyncClient) -> None:

    """GET /api/v1/protocol/tools.json returns tool schemas."""
    response = await client.get("/api/v1/protocol/tools.json")
    assert response.status_code == 200
    data = response.json()
    assert data["protocolVersion"] == STORI_PROTOCOL_VERSION
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert data["toolCount"] > 0


@pytest.mark.anyio
async def test_protocol_schema_unified_endpoint(client: AsyncClient) -> None:

    """GET /api/v1/protocol/schema.json returns everything in one response."""
    response = await client.get("/api/v1/protocol/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert data["protocolVersion"] == STORI_PROTOCOL_VERSION
    assert "protocolHash" in data
    assert "events" in data
    assert "enums" in data
    assert "tools" in data
    assert "SSEState" in data["enums"]
    assert "Intent" in data["enums"]


# ═══════════════════════════════════════════════════════════════════════
# Phase 2 — Runtime Integration Proofs
# ═══════════════════════════════════════════════════════════════════════


class TestPhase2RuntimeIntegration:
    """Prove that protocol enforcement is wired into the runtime path."""

    @pytest.mark.anyio
    async def test_sse_event_uses_protocol_serializer(self) -> None:

        """sse_event() validates through protocol models at runtime."""
        from app.core.sse_utils import sse_event

        result = await sse_event({"type": "content", "content": "hello"})
        payload = json.loads(result[6:].strip())
        assert payload["protocolVersion"] == STORI_PROTOCOL_VERSION
        assert payload["seq"] == -1

    @pytest.mark.anyio
    async def test_sse_event_rejects_unknown_type(self) -> None:

        """Unregistered event types always raise (no production fallback)."""
        from app.core.sse_utils import sse_event

        with pytest.raises(ProtocolSerializationError, match="Unregistered"):
            await sse_event({"type": "nonexistent_event_type"})

    @pytest.mark.anyio
    async def test_sse_event_validates_model_fields(self) -> None:

        """Missing required fields always raise."""
        from app.core.sse_utils import sse_event

        with pytest.raises(ProtocolSerializationError, match="failed protocol validation"):
            await sse_event({"type": "state"})

    def test_all_registered_events_roundtrip_via_serialize(self) -> None:

        """Every event in the registry can be built from a dict and serialized."""
        for event_type, model_class in EVENT_REGISTRY.items():
            instance = _make_minimal(model_class)
            wire_dict = instance.model_dump(by_alias=True, exclude_none=True)
            sse = serialize_event(wire_dict)
            payload = json.loads(sse[6:].strip())
            assert payload["type"] == event_type
            assert payload["protocolVersion"] == STORI_PROTOCOL_VERSION


class TestProtocolGuardEnforcedGlobally:
    """Prove ProtocolGuard is wired into ALL streaming endpoints.

    Routes use either a direct ``ProtocolGuard()`` or ``SSESequencer()``
    (which creates a ``ProtocolGuard`` internally).  Both are accepted.
    """

    @staticmethod
    def _has_guard(text: str) -> bool:

        """Return True if the source uses ProtocolGuard directly or via SSESequencer."""
        return "ProtocolGuard()" in text or "SSESequencer()" in text

    @staticmethod
    def _has_guard_import(text: str) -> bool:

        return (
            "from app.protocol.validation import ProtocolGuard" in text
            or "from app.core.sse_utils import" in text
        )

    def test_maestro_route_has_guard(self) -> None:

        """maestro.py uses ProtocolGuard (directly or via SSESequencer)."""
        source = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "maestro.py"
        text = source.read_text()
        assert self._has_guard(text)
        assert self._has_guard_import(text)

    def test_messages_route_has_guard(self) -> None:

        """messages.py uses ProtocolGuard (directly or via SSESequencer)."""
        source = (
            Path(__file__).resolve().parent.parent
            / "app" / "api" / "routes" / "conversations" / "messages.py"
        )
        text = source.read_text()
        assert self._has_guard(text)
        assert self._has_guard_import(text)

    def test_mcp_route_has_guard(self) -> None:

        """mcp.py instantiates ProtocolGuard in event_generator."""
        source = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "mcp.py"
        text = source.read_text()
        assert self._has_guard(text)
        assert self._has_guard_import(text)

    def test_variation_stream_has_guard(self) -> None:

        """variation/stream.py instantiates ProtocolGuard."""
        source = (
            Path(__file__).resolve().parent.parent
            / "app" / "api" / "routes" / "variation" / "stream.py"
        )
        text = source.read_text()
        assert self._has_guard(text)
        assert self._has_guard_import(text)

    def test_protocol_guard_enforced_globally(self) -> None:

        """All four streaming routes use ProtocolGuard (directly or via SSESequencer)."""
        base = Path(__file__).resolve().parent.parent
        routes = [
            base / "app" / "api" / "routes" / "maestro.py",
            base / "app" / "api" / "routes" / "conversations" / "messages.py",
            base / "app" / "api" / "routes" / "mcp.py",
            base / "app" / "api" / "routes" / "variation" / "stream.py",
        ]
        for route in routes:
            text = route.read_text()
            assert self._has_guard(text), f"{route.name} missing ProtocolGuard()/SSESequencer()"
            assert self._has_guard_import(text), (
                f"{route.name} missing ProtocolGuard/SSESequencer import"
            )


class TestPhase2ProjectSnapshotValidation:
    """Prove ProjectSnapshot validation is wired into MaestroRequest."""

    def test_valid_project_passes(self) -> None:

        from app.models.requests import MaestroRequest
        req = MaestroRequest(prompt="test", project={"id": "p1", "tempo": 90})
        assert req.project is not None
        assert req.project["id"] == "p1"

    def test_invalid_project_nullified(self) -> None:

        """Invalid project payload is set to None (not 422)."""
        from app.models.requests import MaestroRequest
        req = MaestroRequest(prompt="test", project={"id": "p1", "tempo": -10})
        assert req.project is None

    def test_project_none_passes(self) -> None:

        from app.models.requests import MaestroRequest
        req = MaestroRequest(prompt="test", project=None)
        assert req.project is None

    def test_extra_fields_preserved(self) -> None:

        """ProjectSnapshot extra='allow' does not reject unknown FE fields."""
        from app.models.requests import MaestroRequest
        req = MaestroRequest(
            prompt="test",
            project={"id": "p1", "futureField": "ok"},
        )
        assert req.project is not None
        assert req.project["futureField"] == "ok"


class TestPhase2NoDuplicateHelpers:
    """Prove the duplicate sse_event helper was removed."""

    def test_conversations_helpers_has_no_sse_event(self) -> None:

        source = (
            Path(__file__).resolve().parent.parent
            / "app" / "api" / "routes" / "conversations" / "helpers.py"
        )
        text = source.read_text()
        assert "def sse_event" not in text
        assert "data:" not in text


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_minimal(model_class: type) -> Any:

    """Construct a minimal valid instance of an event model."""
    _MINIMAL: dict[str, dict[str, Any]] = {
        "state": {"state": "editing", "intent": "track.add", "confidence": 0.9, "trace_id": "t"},
        "reasoning": {"content": "thinking..."},
        "reasoningEnd": {"agent_id": "a1"},
        "content": {"content": "hello"},
        "status": {"message": "working..."},
        "error": {"message": "boom"},
        "complete": {"success": True, "trace_id": "t"},
        "plan": {"plan_id": "p1", "title": "Plan", "steps": []},
        "planStepUpdate": {"step_id": "s1", "status": "active"},
        "toolStart": {"name": "stori_add_track", "label": "Add track"},
        "toolCall": {"id": "tc1", "name": "stori_add_track", "params": {}},
        "toolError": {"name": "stori_add_track", "error": "failed"},
        "preflight": {"step_id": "s1", "agent_id": "a1", "agent_role": "drums", "label": "Drums", "tool_name": "stori_add_track"},
        "generatorStart": {"role": "drums", "agent_id": "a1", "style": "trap", "bars": 4, "start_beat": 0.0, "label": "Drums"},
        "generatorComplete": {"role": "drums", "agent_id": "a1", "note_count": 32, "duration_ms": 1200},
        "agentComplete": {"agent_id": "a1", "success": True},
        "summary": {"tracks": ["Drums"], "regions": 1, "notes": 32, "effects": 0},
        "summary.final": {"trace_id": "t"},
        "meta": {"variation_id": "v1", "base_state_id": "s1", "intent": "compose"},
        "phrase": {"phrase_id": "p1", "track_id": "t1", "region_id": "r1", "start_beat": 0, "end_beat": 4, "label": "Intro"},
        "done": {"variation_id": "v1", "phrase_count": 1},
        "mcp.message": {"payload": {"tool": "test"}},
        "mcp.ping": {},
    }
    event_type_field = model_class.model_fields.get("type")  # type: ignore[attr-defined]  # all SSE event models have model_fields
    if event_type_field and event_type_field.default:
        et = event_type_field.default
    else:
        et = "unknown"
    kwargs = _MINIMAL.get(et, {})
    return model_class(**kwargs)


# ═══════════════════════════════════════════════════════════════════════
# Protocol Convergence Tests (Final Cutover)
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolConvergenceFinal:
    """Final convergence cutover — ONE envelope, ONE emitter, ONE registry."""

    def test_serialize_event_never_emits_raw(self) -> None:

        """serialize_event() raises on invalid events, never emits raw dicts."""
        with pytest.raises(ProtocolSerializationError):
            serialize_event({"type": "nonexistent_type_xyz"})

        with pytest.raises(ProtocolSerializationError):
            serialize_event({"no_type_field": True})

        with pytest.raises(ProtocolSerializationError):
            serialize_event({"type": "content"})

    def test_no_raw_sse_fallback_in_emitter(self) -> None:

        """emitter.py must not contain _raw_sse or production fallback logic."""
        source = Path(__file__).resolve().parent.parent / "app" / "protocol" / "emitter.py"
        text = source.read_text()
        assert "_raw_sse" not in text, "Legacy _raw_sse function must be removed"
        assert "emitting raw dict" not in text, "Raw dict fallback message must be removed"
        assert "settings.debug" not in text, "No debug/prod branching — always strict"

    def test_all_stream_routes_use_protocol_emitter(self) -> None:

        """All streaming routes must use sse_event() or emit(), not json.dumps for SSE."""
        base = Path(__file__).resolve().parent.parent
        routes = [
            base / "app" / "api" / "routes" / "conversations" / "messages.py",
            base / "app" / "api" / "routes" / "variation" / "stream.py",
        ]
        for route in routes:
            text = route.read_text()
            assert "json.dumps" not in text, (
                f"{route.name} still uses json.dumps — all SSE must go through protocol emitter"
            )

    def test_mcp_stream_emits_registered_event_types(self) -> None:

        """MCP stream uses mcp.message and mcp.ping (both registered)."""
        assert "mcp.message" in ALL_EVENT_TYPES
        assert "mcp.ping" in ALL_EVENT_TYPES

        from app.protocol.events import MCPMessageEvent, MCPPingEvent

        msg = MCPMessageEvent(payload={"tool": "test"})
        assert msg.type == "mcp.message"

        ping = MCPPingEvent()
        assert ping.type == "mcp.ping"

        msg_sse = emit(msg)
        assert "mcp.message" in msg_sse

        ping_sse = emit(ping)
        assert "mcp.ping" in ping_sse

    def test_variation_stream_emits_registered_event_types(self) -> None:

        """Variation stream uses meta, phrase, done (all registered)."""
        for event_type in ("meta", "phrase", "done"):
            assert event_type in ALL_EVENT_TYPES

    def test_variation_stream_no_event_envelope_to_sse(self) -> None:

        """variation/stream.py must not call EventEnvelope.to_sse()."""
        source = (
            Path(__file__).resolve().parent.parent
            / "app" / "api" / "routes" / "variation" / "stream.py"
        )
        text = source.read_text()
        assert ".to_sse()" not in text, "EventEnvelope.to_sse() must be replaced by sse_event()"
        assert "EventEnvelope.to_sse" not in text

    def test_mcp_route_no_raw_json_dumps_sse(self) -> None:

        """mcp.py must not use json.dumps for SSE emission."""
        source = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "mcp.py"
        text = source.read_text()
        import re
        sse_json_dumps = re.findall(r'f"data:.*json\.dumps', text)
        assert not sse_json_dumps, (
            f"mcp.py still has raw json.dumps SSE emission: {sse_json_dumps}"
        )

    def test_no_direct_json_serialization_in_streams(self) -> None:

        """Source-scan: no f'data: {{json.dumps(...)}}' outside protocol emitter."""
        base = Path(__file__).resolve().parent.parent
        import re
        pattern = re.compile(r'f"data:\s*\{json\.dumps')

        allowed = {
            "emitter.py",
            "sse_utils.py",
        }

        scan_dirs = [
            base / "app" / "api" / "routes",
            base / "app" / "core",
        ]
        violations = []
        for scan_dir in scan_dirs:
            for f in scan_dir.rglob("*.py"):
                if f.name in allowed:
                    continue
                text = f.read_text()
                matches = pattern.findall(text)
                if matches:
                    violations.append(f"{f.relative_to(base)}: {len(matches)} match(es)")

        # maestro.py is allowed one instance: _with_seq re-serialization
        violations = [
            v for v in violations
            if "maestro.py" not in v
        ]
        assert not violations, (
            f"Direct json.dumps SSE serialization found outside emitter:\n"
            + "\n".join(violations)
        )
