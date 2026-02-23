"""Protocol Lockdown Phase II — Verification Proofs.

Each test produces concrete artifacts proving the contract lineage
system is correct.  No mocks on the contract layer itself — only
on I/O boundaries (tool execution, LLM).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import types
from dataclasses import fields
from unittest.mock import patch

import pytest

from app.contracts.hash_utils import (
    _HASH_EXCLUDED_FIELDS,
    canonical_contract_dict,
    compute_contract_hash,
    seal_contract,
    verify_contract_hash,
)
from app.core.maestro_agent_teams.contracts import (
    CompositionContract,
    ExecutionServices,
    InstrumentContract,
    RuntimeContext,
    SectionContract,
    SectionSpec,
)
from app.core.maestro_agent_teams.section_agent import (
    SectionResult,
    _run_section_child,
)
from app.core.expansion import ToolCall
from app.core.maestro_plan_tracker import _ToolCallOutcome
from app.core.state_store import StateStore
from app.core.tracing import TraceContext


def _spec(name: str = "verse", index: int = 0, start: int = 0, beats: int = 16) -> SectionSpec:
    s = SectionSpec(
        section_id=f"{index}:{name}",
        name=name,
        index=index,
        start_beat=start,
        duration_beats=beats,
        bars=max(1, beats // 4),
        character=f"Test {name}",
        role_brief=f"Test brief for {name}",
    )
    seal_contract(s)
    return s


def _section_contract(
    spec: SectionSpec,
    track_id: str = "trk-1",
    instrument: str = "Drums",
    role: str = "drums",
    parent_hash: str = "",
    l2_prompt: str = "",
    region_name: str = "",
) -> SectionContract:
    sc = SectionContract(
        section=spec,
        track_id=track_id,
        instrument_name=instrument,
        role=role,
        style="neo-soul",
        tempo=92.0,
        key="Fm",
        region_name=region_name or f"{instrument} – {spec.name}",
        l2_generate_prompt=l2_prompt,
    )
    seal_contract(sc, parent_hash=parent_hash)
    return sc


# ═════════════════════════════════════════════════════════════════════════
# SECTION 1 — HASH CANONICALIZATION PROOF
# ═════════════════════════════════════════════════════════════════════════


class TestHashCanonicalization:
    """Prove hashes are deterministic and advisory-field-independent."""

    def test_deterministic_hash_two_runs(self):
        """Identical input → identical hash across two independent constructions."""
        spec = _spec()

        sc1 = _section_contract(spec, l2_prompt="prompt A", region_name="Region A")
        sc2 = _section_contract(spec, l2_prompt="prompt A", region_name="Region A")

        canonical1 = canonical_contract_dict(sc1)
        canonical2 = canonical_contract_dict(sc2)
        raw_json1 = json.dumps(canonical1, separators=(",", ":"), sort_keys=True)
        raw_json2 = json.dumps(canonical2, separators=(",", ":"), sort_keys=True)
        full_sha1 = hashlib.sha256(raw_json1.encode()).hexdigest()
        full_sha2 = hashlib.sha256(raw_json2.encode()).hexdigest()

        print("\n## HASH_CANONICALIZATION_PROOF — Determinism")
        print(f"canonical_contract_dict(sc1) =\n{json.dumps(canonical1, indent=2)}")
        print(f"\nraw JSON = {raw_json1}")
        print(f"full SHA256 = {full_sha1}")
        print(f"contract_hash = {sc1.contract_hash}")
        print(f"\nRun 1 hash: {sc1.contract_hash}")
        print(f"Run 2 hash: {sc2.contract_hash}")

        assert raw_json1 == raw_json2
        assert full_sha1 == full_sha2
        assert sc1.contract_hash == sc2.contract_hash
        assert len(sc1.contract_hash) == 16

    def test_advisory_field_does_not_change_hash(self):
        """Changing l2_generate_prompt or region_name must NOT change hash."""
        spec = _spec()

        sc_original = _section_contract(spec, l2_prompt="", region_name="Original")
        sc_advisory = _section_contract(spec, l2_prompt="COMPLETELY DIFFERENT PROMPT", region_name="DIFFERENT REGION")

        print("\n## HASH_CANONICALIZATION_PROOF — Advisory Independence")
        print(f"Original  l2_generate_prompt = ''")
        print(f"Modified  l2_generate_prompt = 'COMPLETELY DIFFERENT PROMPT'")
        print(f"Original  region_name = 'Original'")
        print(f"Modified  region_name = 'DIFFERENT REGION'")
        print(f"Original  hash = {sc_original.contract_hash}")
        print(f"Modified  hash = {sc_advisory.contract_hash}")

        assert sc_original.contract_hash == sc_advisory.contract_hash


# ═════════════════════════════════════════════════════════════════════════
# SECTION 2 — LINEAGE CHAIN PROOF
# ═════════════════════════════════════════════════════════════════════════


class TestLineageChain:
    """Prove parent→child hash linkage across L1→L2→L3."""

    def test_full_lineage_chain(self):
        """Two-section, one-instrument lineage chain with CompositionContract root."""
        spec_intro = _spec("intro", index=0, start=0, beats=16)
        spec_verse = _spec("verse", index=1, start=16, beats=16)

        cc = CompositionContract(
            composition_id="test-comp-001",
            sections=(spec_intro, spec_verse),
            style="neo-soul",
            tempo=92.0,
            key="Fm",
        )
        seal_contract(cc)

        ic = InstrumentContract(
            instrument_name="Drums",
            role="drums",
            style="neo-soul",
            bars=8,
            tempo=92.0,
            key="Fm",
            start_beat=0,
            sections=(spec_intro, spec_verse),
            existing_track_id=None,
            assigned_color="#E85D75",
            gm_guidance="Standard Kit",
        )
        seal_contract(ic, parent_hash=cc.contract_hash)

        sc_intro = _section_contract(spec_intro, parent_hash=ic.contract_hash)
        sc_verse = _section_contract(spec_verse, parent_hash=ic.contract_hash)

        print("\n## LINEAGE_CHAIN_PROOF")
        print(f"CompositionContract.contract_hash = {cc.contract_hash}")
        print(f"SectionSpec[intro].contract_hash  = {spec_intro.contract_hash}")
        print(f"SectionSpec[verse].contract_hash  = {spec_verse.contract_hash}")
        print(f"InstrumentContract.parent_hash    = {ic.parent_contract_hash}")
        print(f"InstrumentContract.contract_hash  = {ic.contract_hash}")
        print(f"SectionContract[intro].parent_hash = {sc_intro.parent_contract_hash}")
        print(f"SectionContract[intro].contract_hash = {sc_intro.contract_hash}")
        print(f"SectionContract[verse].parent_hash = {sc_verse.parent_contract_hash}")
        print(f"SectionContract[verse].contract_hash = {sc_verse.contract_hash}")

        assert cc.contract_hash != ""
        assert ic.parent_contract_hash == cc.contract_hash
        assert sc_intro.parent_contract_hash == ic.contract_hash
        assert sc_verse.parent_contract_hash == ic.contract_hash
        assert verify_contract_hash(cc)
        assert verify_contract_hash(ic)
        assert verify_contract_hash(sc_intro)
        assert verify_contract_hash(sc_verse)
        assert sc_intro.contract_hash != sc_verse.contract_hash


# ═════════════════════════════════════════════════════════════════════════
# SECTION 3 — HASH TAMPER TEST
# ═════════════════════════════════════════════════════════════════════════


class TestHashTamper:
    """Prove that tampered contracts halt execution."""

    @pytest.mark.anyio
    async def test_tampered_tempo_raises(self):
        """Mutating tempo after sealing causes _run_section_child to raise."""
        spec = _spec()
        sc = _section_contract(spec)

        stored_hash = sc.contract_hash
        assert verify_contract_hash(sc)

        object.__setattr__(sc, "tempo", 999.0)

        assert not verify_contract_hash(sc)

        store = StateStore(conversation_id="tamper-test")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        print("\n## HASH_TAMPER_TEST")
        print(f"Original tempo: 92.0")
        print(f"Tampered tempo: {sc.tempo}")
        print(f"Stored hash: {stored_hash}")
        print(f"Recomputed hash: {compute_contract_hash(sc)}")
        print(f"verify_contract_hash: {verify_contract_hash(sc)}")

        with pytest.raises(ValueError, match="Protocol violation.*hash mismatch") as exc_info:
            await _run_section_child(
                contract=sc,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="tamper-test"),
                sse_queue=queue,
            )

        print(f"Exception type: {type(exc_info.value).__name__}")
        print(f"Exception message: {exc_info.value}")
        print("Execution HALTED. ✓")

    @pytest.mark.anyio
    async def test_missing_hash_raises(self):
        """Contract with empty contract_hash is rejected."""
        spec = _spec()
        sc = SectionContract(
            section=spec,
            track_id="trk-1",
            instrument_name="Drums",
            role="drums",
            style="neo-soul",
            tempo=92.0,
            key="Fm",
            region_name="Drums – verse",
        )

        store = StateStore(conversation_id="no-hash-test")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        with pytest.raises(ValueError, match="Protocol violation.*no contract_hash"):
            await _run_section_child(
                contract=sc,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="no-hash-test"),
                sse_queue=queue,
            )

        print("\n## HASH_TAMPER_TEST — Missing hash")
        print("Empty contract_hash rejected. ✓")


# ═════════════════════════════════════════════════════════════════════════
# SECTION 4 — SINGLE SECTION PATH LOCKDOWN
# ═════════════════════════════════════════════════════════════════════════


class TestSingleSectionLockdown:
    """Prove single-section uses _dispatch_section_children with contract override."""

    @pytest.mark.anyio
    async def test_contract_overrides_llm_start_beat(self):
        """LLM proposes wrong startBeat; contract forces the correct value."""
        spec = _spec("verse", index=0, start=0, beats=16)
        sc = _section_contract(spec, parent_hash="test-parent")

        captured_params: dict = {}

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                captured_params["region"] = dict(resolved_args)
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"regionId": "reg-1", "trackId": "trk-1"},
                    sse_events=[{"type": "toolCall", "name": tc_name}],
                    msg_call={}, msg_result={},
                )
            if tc_name == "stori_generate_midi":
                captured_params["generate"] = dict(resolved_args)
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"notesAdded": 20, "regionId": "reg-1"},
                    sse_events=[{"type": "toolCall", "name": tc_name}],
                    msg_call={}, msg_result={},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={},
                sse_events=[], msg_call={}, msg_result={},
            )

        store = StateStore(conversation_id="lockdown-test")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        bad_region_tc = ToolCall(
            id="r1", name="stori_add_midi_region",
            params={"trackId": "trk-1", "startBeat": 999, "durationBeats": 64},
        )
        bad_gen_tc = ToolCall(
            id="g1", name="stori_generate_midi",
            params={"start_beat": 999, "bars": 16},
        )

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=sc,
                region_tc=bad_region_tc,
                generate_tc=bad_gen_tc,
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="lockdown-test"),
                sse_queue=queue,
            )

        print("\n## SINGLE_SECTION_LOCKDOWN_PROOF")
        print(f"LLM proposed startBeat = 999")
        print(f"LLM proposed durationBeats = 64")
        print(f"Contract startBeat = {sc.start_beat}")
        print(f"Contract durationBeats = {sc.duration_beats}")
        print(f"Final region startBeat = {captured_params['region']['startBeat']}")
        print(f"Final region durationBeats = {captured_params['region']['durationBeats']}")
        print(f"Final generate start_beat = {captured_params['generate']['start_beat']}")
        print(f"Final generate bars = {captured_params['generate']['bars']}")

        assert captured_params["region"]["startBeat"] == 0, "Contract override failed"
        assert captured_params["region"]["durationBeats"] == 16, "Contract override failed"
        assert captured_params["generate"]["start_beat"] == 0, "Contract override failed"
        assert captured_params["generate"]["bars"] == 4, "Contract override failed"


# ═════════════════════════════════════════════════════════════════════════
# SECTION 5 — RUNTIME CONTEXT FREEZE PROOF
# ═════════════════════════════════════════════════════════════════════════


class TestRuntimeContextFreeze:
    """Prove RuntimeContext.emotion_vector is deeply frozen."""

    def test_emotion_vector_type_is_frozen_tuple(self):
        """emotion_vector is stored as tuple[tuple[str, float], ...]."""
        from app.core.emotion_vector import EmotionVector

        ev = EmotionVector(energy=0.8, valence=0.3, tension=0.4, intimacy=0.5, motion=0.6)
        frozen = RuntimeContext.freeze_emotion_vector(ev)
        ctx = RuntimeContext(raw_prompt="test", emotion_vector=frozen)

        print("\n## RUNTIME_CONTEXT_IMMUTABILITY_PROOF")
        print(f"type(ctx.emotion_vector) = {type(ctx.emotion_vector).__name__}")
        print(f"value = {ctx.emotion_vector}")

        assert isinstance(ctx.emotion_vector, tuple)
        for pair in ctx.emotion_vector:
            assert isinstance(pair, tuple)
            assert len(pair) == 2
            assert isinstance(pair[0], str)
            assert isinstance(pair[1], float)

    def test_frozen_tuple_mutation_raises(self):
        """Attempting to mutate the frozen tuple raises TypeError."""
        from app.core.emotion_vector import EmotionVector

        ev = EmotionVector(energy=0.8, valence=0.3)
        frozen = RuntimeContext.freeze_emotion_vector(ev)
        ctx = RuntimeContext(raw_prompt="test", emotion_vector=frozen)

        with pytest.raises(TypeError):
            ctx.emotion_vector[0][1] = 999  # type: ignore[index]

        print("Mutation ctx.emotion_vector[0][1] = 999 → TypeError ✓")

    def test_composition_context_returns_mapping_proxy(self):
        """to_composition_context returns MappingProxyType — no dict mutation."""
        from app.core.emotion_vector import EmotionVector

        ev = EmotionVector(energy=0.8, valence=0.3)
        frozen = RuntimeContext.freeze_emotion_vector(ev)
        ctx = RuntimeContext(raw_prompt="test", emotion_vector=frozen)

        d = ctx.to_composition_context()
        assert isinstance(d, types.MappingProxyType)

        with pytest.raises(TypeError):
            d["emotion_vector"] = "hacked"  # type: ignore[index]

        print("Mutation ctx['emotion_vector'] = 'hacked' → TypeError ✓")

    def test_frozen_dataclass_attribute_mutation_raises(self):
        """RuntimeContext is frozen — field assignment raises."""
        ctx = RuntimeContext(raw_prompt="test")

        with pytest.raises(AttributeError):
            ctx.emotion_vector = ((("energy", 999.0),))  # type: ignore[misc]

        print("Mutation ctx.emotion_vector = ... → AttributeError ✓")


# ═════════════════════════════════════════════════════════════════════════
# SECTION 6 — HASH FIELD AUDIT
# ═════════════════════════════════════════════════════════════════════════


class TestHashFieldAudit:
    """Prove advisory fields are excluded from canonical dicts."""

    def test_section_spec_keys(self):
        spec = _spec()
        canonical = canonical_contract_dict(spec)
        keys = sorted(canonical.keys())

        print("\n## HASH_FIELD_AUDIT — SectionSpec")
        print(f"Included keys: {keys}")

        assert "contract_version" not in keys
        assert "contract_hash" not in keys
        for excluded in _HASH_EXCLUDED_FIELDS:
            assert excluded not in keys, f"Advisory field '{excluded}' leaked into hash"

    def test_instrument_contract_keys(self):
        spec = _spec()
        ic = InstrumentContract(
            instrument_name="Drums", role="drums", style="neo-soul",
            bars=4, tempo=92.0, key="Fm", start_beat=0,
            sections=(spec,), existing_track_id="trk-99",
            assigned_color="#FF0000", gm_guidance="GM guidance text",
        )
        seal_contract(ic)
        canonical = canonical_contract_dict(ic)
        keys = sorted(canonical.keys())

        print("\n## HASH_FIELD_AUDIT — InstrumentContract")
        print(f"Included keys: {keys}")

        assert "gm_guidance" not in keys
        assert "assigned_color" not in keys
        assert "existing_track_id" not in keys
        assert "contract_hash" not in keys
        assert "parent_contract_hash" not in keys
        assert "contract_version" not in keys

    def test_section_contract_keys(self):
        spec = _spec()
        sc = _section_contract(spec)
        canonical = canonical_contract_dict(sc)
        keys = sorted(canonical.keys())

        print("\n## HASH_FIELD_AUDIT — SectionContract")
        print(f"Included keys: {keys}")

        assert "l2_generate_prompt" not in keys
        assert "region_name" not in keys
        assert "contract_hash" not in keys
        assert "parent_contract_hash" not in keys
        assert "contract_version" not in keys

    def test_full_exclusion_audit(self):
        """Every field in _HASH_EXCLUDED_FIELDS is absent from ALL canonical dicts."""
        spec = _spec()
        ic = InstrumentContract(
            instrument_name="X", role="r", style="s", bars=4,
            tempo=120.0, key="C", start_beat=0, sections=(spec,),
            existing_track_id="t", assigned_color="#000", gm_guidance="g",
        )
        seal_contract(ic)
        sc = _section_contract(spec)

        for obj, label in [(spec, "SectionSpec"), (ic, "InstrumentContract"), (sc, "SectionContract")]:
            canonical = canonical_contract_dict(obj)
            for excluded in _HASH_EXCLUDED_FIELDS:
                assert excluded not in canonical, (
                    f"FAIL: '{excluded}' found in {label} canonical dict"
                )

        print("\n## HASH_FIELD_AUDIT — Full exclusion check PASSED")
        print(f"Excluded fields verified absent: {sorted(_HASH_EXCLUDED_FIELDS)}")


# ═════════════════════════════════════════════════════════════════════════
# SECTION 7 — EXECUTION ATTESTATION
# ═════════════════════════════════════════════════════════════════════════


class TestExecutionAttestation:
    """Prove SectionResult carries correct lineage after execution."""

    @pytest.mark.anyio
    async def test_result_carries_contract_lineage(self):
        """SectionResult.contract_hash matches the contract that produced it."""
        spec = _spec()
        sc = _section_contract(spec, parent_hash="parent-ic-hash")

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"regionId": "reg-1", "trackId": "trk-1"},
                    sse_events=[{"type": "toolCall", "name": tc_name}],
                    msg_call={}, msg_result={},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"notesAdded": 30, "regionId": "reg-1"},
                sse_events=[{"type": "toolCall", "name": tc_name}],
                msg_call={}, msg_result={},
            )

        store = StateStore(conversation_id="attest-test")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=sc,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="attest-test"),
                sse_queue=queue,
            )

        print("\n## EXECUTION_ATTESTATION_PROOF")
        print(f"result.success = {result.success}")
        print(f"result.section_name = {result.section_name}")
        print(f"result.notes_generated = {result.notes_generated}")
        print(f"result.contract_hash = {result.contract_hash}")
        print(f"result.parent_contract_hash = {result.parent_contract_hash}")
        print(f"contract.contract_hash = {sc.contract_hash}")
        print(f"contract.parent_contract_hash = {sc.parent_contract_hash}")

        assert result.contract_hash == sc.contract_hash
        assert result.parent_contract_hash == sc.parent_contract_hash
        assert result.contract_hash != ""
        assert result.parent_contract_hash == "parent-ic-hash"
        assert result.success is True
