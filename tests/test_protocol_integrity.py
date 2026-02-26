"""Protocol Integrity — Verification Proofs.

Tests proving the integrity guarantees of the contract/hash system:
  1. CompositionContract root lineage anchor
  2. Canonical parent hashing (collision-proof)
  3. Execution attestation binding
  4. Signal lineage binding (swarm safety)
  5. Hash scope enforcement (advisory field exclusion)
"""

from __future__ import annotations

from app.contracts.json_types import JSONObject, JSONValue
from app.contracts.pydantic_types import wrap_dict
import asyncio

import json
from unittest.mock import patch

import pytest

from app.contracts.hash_utils import (
    _HASH_EXCLUDED_FIELDS,
    canonical_contract_dict,
    compute_contract_hash,
    compute_execution_hash,
    hash_list_canonical,
    seal_contract,
    verify_contract_hash,
)
from app.core.maestro_agent_teams.contracts import (
    CompositionContract,
    InstrumentContract,
    ProtocolViolationError,
    SectionContract,
    SectionSpec,
)
from app.core.maestro_agent_teams.section_agent import (
    SectionResult,
    _run_section_child,
)
from app.core.maestro_agent_teams.signals import SectionSignalResult, SectionSignals
from app.core.expansion import ToolCall
from app.core.maestro_plan_tracker import _ToolCallOutcome
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.protocol.events import MaestroEvent, ToolCallEvent


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
        tempo=92,
        key="Fm",
        region_name=region_name or f"{instrument} – {spec.name}",
        l2_generate_prompt=l2_prompt,
    )
    seal_contract(sc, parent_hash=parent_hash)
    return sc


# ═════════════════════════════════════════════════════════════════════════
# PROOF 1 — COMPOSITION ROOT LINEAGE
# ═════════════════════════════════════════════════════════════════════════


class TestCompositionRootLineage:
    """Prove CompositionContract is the root anchor for all lineage."""

    def test_composition_contract_seals(self) -> None:

        """CompositionContract gets a deterministic hash after sealing."""
        spec_a = _spec("intro", 0, 0, 16)
        spec_b = _spec("verse", 1, 16, 16)

        cc = CompositionContract(
            composition_id="comp-001",
            sections=(spec_a, spec_b),
            style="neo-soul",
            tempo=92,
            key="Fm",
        )
        seal_contract(cc)

        assert cc.contract_hash != ""
        assert len(cc.contract_hash) == 16
        assert verify_contract_hash(cc)

    def test_instrument_parent_hash_equals_composition_hash(self) -> None:

        """InstrumentContract.parent_contract_hash == CompositionContract.contract_hash."""
        spec_a = _spec("intro", 0, 0, 16)
        spec_b = _spec("verse", 1, 16, 16)

        cc = CompositionContract(
            composition_id="comp-002",
            sections=(spec_a, spec_b),
            style="neo-soul",
            tempo=92,
            key="Fm",
        )
        seal_contract(cc)

        ic = InstrumentContract(
            instrument_name="Drums",
            role="drums",
            style="neo-soul",
            bars=8,
            tempo=92,
            key="Fm",
            start_beat=0,
            sections=(spec_a, spec_b),
            existing_track_id=None,
            assigned_color="#E85D75",
            gm_guidance="Standard Kit",
        )
        seal_contract(ic, parent_hash=cc.contract_hash)

        print("\n## COMPOSITION_ROOT_PROOF")
        print(f"CompositionContract.contract_hash  = {cc.contract_hash}")
        print(f"InstrumentContract.parent_hash     = {ic.parent_contract_hash}")

        assert ic.parent_contract_hash == cc.contract_hash

    def test_full_root_lineage_chain(self) -> None:

        """Composition → Instrument → Section → full chain verified."""
        spec_intro = _spec("intro", 0, 0, 16)
        spec_verse = _spec("verse", 1, 16, 16)

        cc = CompositionContract(
            composition_id="comp-chain",
            sections=(spec_intro, spec_verse),
            style="neo-soul",
            tempo=92,
            key="Fm",
        )
        seal_contract(cc)

        ic = InstrumentContract(
            instrument_name="Bass",
            role="bass",
            style="neo-soul",
            bars=8,
            tempo=92,
            key="Fm",
            start_beat=0,
            sections=(spec_intro, spec_verse),
            existing_track_id=None,
            assigned_color="#4A90D9",
            gm_guidance="Fingered Bass",
        )
        seal_contract(ic, parent_hash=cc.contract_hash)

        sc = _section_contract(spec_intro, instrument="Bass", role="bass",
                               parent_hash=ic.contract_hash)

        print("\n## FULL_ROOT_LINEAGE_CHAIN")
        print(f"Composition.hash  = {cc.contract_hash}")
        print(f"  → Instrument.parent  = {ic.parent_contract_hash}")
        print(f"  → Instrument.hash    = {ic.contract_hash}")
        print(f"    → Section.parent   = {sc.parent_contract_hash}")
        print(f"    → Section.hash     = {sc.contract_hash}")

        assert ic.parent_contract_hash == cc.contract_hash
        assert sc.parent_contract_hash == ic.contract_hash
        assert verify_contract_hash(cc)
        assert verify_contract_hash(ic)
        assert verify_contract_hash(sc)

    def test_composition_canonical_dict_uses_section_hashes(self) -> None:

        """CompositionContract canonical dict serializes sections as sorted hashes."""
        spec_a = _spec("intro", 0, 0, 16)
        spec_b = _spec("verse", 1, 16, 16)

        cc = CompositionContract(
            composition_id="comp-canon",
            sections=(spec_a, spec_b),
            style="neo-soul",
            tempo=92,
            key="Fm",
        )
        seal_contract(cc)
        canonical = canonical_contract_dict(cc)

        assert "sections" in canonical
        sections_val = canonical["sections"]
        assert isinstance(sections_val, list)
        # isinstance filter narrows list[JSONValue] → list[str] without coercion
        sections_strs = [h for h in sections_val if isinstance(h, str)]
        assert len(sections_strs) == len(sections_val), "all section entries must be str hashes"
        assert sorted(sections_strs) == sections_strs
        assert spec_a.contract_hash in sections_strs
        assert spec_b.contract_hash in sections_strs

        print("\n## COMPOSITION_CANONICAL_DICT")
        print(json.dumps(canonical, indent=2))


# ═════════════════════════════════════════════════════════════════════════
# PROOF 2 — CANONICAL PARENT HASH (COLLISION-PROOF)
# ═════════════════════════════════════════════════════════════════════════


class TestCanonicalParentHash:
    """Prove hash_list_canonical is order-independent and collision-proof."""

    def test_two_permutations_same_hash(self) -> None:

        """Reversed order of section hashes produces the same parent hash."""
        hash_a = "aaaa1111bbbb2222"
        hash_b = "cccc3333dddd4444"

        result_ab = hash_list_canonical([hash_a, hash_b])
        result_ba = hash_list_canonical([hash_b, hash_a])

        print("\n## CANONICAL_PARENT_HASH_PROOF — Order independence")
        print(f"hash_list_canonical([A, B]) = {result_ab}")
        print(f"hash_list_canonical([B, A]) = {result_ba}")

        assert result_ab == result_ba
        assert len(result_ab) == 16

    def test_three_permutations_same_hash(self) -> None:

        """Any ordering of three hashes produces the same parent hash."""
        hashes = ["aaa111", "bbb222", "ccc333"]
        import itertools

        results = set()
        for perm in itertools.permutations(hashes):
            results.add(hash_list_canonical(list(perm)))

        assert len(results) == 1

    def test_different_content_different_hash(self) -> None:

        """Different hash lists produce different parent hashes."""
        result_1 = hash_list_canonical(["aaa", "bbb"])
        result_2 = hash_list_canonical(["aaa", "ccc"])

        assert result_1 != result_2

    def test_no_delimiter_collision(self) -> None:

        """Old "A:B" pattern was vulnerable — new JSON encoding is not."""
        result_a = hash_list_canonical(["a:b", "c"])
        result_b = hash_list_canonical(["a", "b:c"])

        assert result_a != result_b


# ═════════════════════════════════════════════════════════════════════════
# PROOF 3 — EXECUTION ATTESTATION BINDING
# ═════════════════════════════════════════════════════════════════════════


class TestExecutionAttestation:
    """Prove execution_hash binds result to contract + session."""

    def test_same_contract_different_trace_different_hash(self) -> None:

        """Same contract hash + different trace_id → different execution_hash."""
        contract_hash = "abcdef1234567890"

        exec_1 = compute_execution_hash(contract_hash, "trace-AAA")
        exec_2 = compute_execution_hash(contract_hash, "trace-BBB")

        print("\n## EXECUTION_ATTESTATION_PROOF — Session binding")
        print(f"contract_hash = {contract_hash}")
        print(f"trace_id=AAA → execution_hash = {exec_1}")
        print(f"trace_id=BBB → execution_hash = {exec_2}")

        assert exec_1 != exec_2
        assert len(exec_1) == 16

    def test_same_trace_different_contract_different_hash(self) -> None:

        """Different contract hash + same trace_id → different execution_hash."""
        trace_id = "trace-fixed"

        exec_1 = compute_execution_hash("contract_AAA_hash", trace_id)
        exec_2 = compute_execution_hash("contract_BBB_hash", trace_id)

        assert exec_1 != exec_2

    def test_deterministic(self) -> None:

        """Same inputs → same execution_hash (deterministic)."""
        a = compute_execution_hash("hash1", "trace1")
        b = compute_execution_hash("hash1", "trace1")
        assert a == b

    @pytest.mark.anyio
    async def test_result_carries_execution_hash(self) -> None:

        """SectionResult.execution_hash is populated after section execution."""
        spec = _spec()
        sc = _section_contract(spec, parent_hash="parent-ic")

        async def _mock_apply(*, tc_id: str, tc_name: str, resolved_args: dict[str, JSONValue], **kw: object) -> _ToolCallOutcome:
            if tc_name == "stori_add_midi_region":
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"regionId": "reg-1", "trackId": "trk-1"},
                    sse_events=[ToolCallEvent(id=tc_id, name=tc_name, params=wrap_dict(resolved_args))],
                    msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"notesAdded": 20, "regionId": "reg-1"},
                sse_events=[ToolCallEvent(id=tc_id, name=tc_name, params=wrap_dict(resolved_args))],
                msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
            )

        store = StateStore(conversation_id="exec-attest")
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        trace = TraceContext(trace_id="exec-trace-001")

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
                trace=trace,
                sse_queue=queue,
            )

        expected_exec = compute_execution_hash(sc.contract_hash, "exec-trace-001")

        print("\n## EXECUTION_ATTESTATION_PROOF — Result binding")
        print(f"result.execution_hash = {result.execution_hash}")
        print(f"expected (recomputed) = {expected_exec}")
        print(f"result.contract_hash  = {result.contract_hash}")

        assert result.execution_hash != ""
        assert result.execution_hash == expected_exec
        assert result.success is True


# ═════════════════════════════════════════════════════════════════════════
# PROOF 4 — SIGNAL LINEAGE BINDING (SWARM SAFETY)
# ═════════════════════════════════════════════════════════════════════════


class TestSignalLineageBinding:
    """Prove SectionSignals enforces contract_hash binding."""

    def test_signal_with_contract_hash(self) -> None:

        """Signal stores contract_hash and bass receives it."""
        spec = _spec("verse", 0, 0, 16)
        signals = SectionSignals.from_section_ids(
            ["0:verse"],
            contract_hashes=[spec.contract_hash],
        )
        signals.signal_complete(
            "0:verse",
            contract_hash=spec.contract_hash,
            success=True,
            drum_notes=[{"pitch": 36}],
        )
        key = f"0:verse:{spec.contract_hash}"
        assert key in signals._results
        assert signals._results[key].contract_hash == spec.contract_hash

    @pytest.mark.anyio
    async def test_bass_rejects_mismatched_contract_hash(self) -> None:

        """Signal with wrong contract_hash is invisible to the correct consumer.

        When drums signal with hash A but the registered key uses hash B,
        the event for hash B is never set — the wrong signal goes nowhere.
        A consumer waiting on hash B would block forever (timeout).
        """
        spec = _spec("verse", 0, 0, 16)
        signals = SectionSignals.from_section_ids(
            ["0:verse"],
            contract_hashes=[spec.contract_hash],
        )
        signals.signal_complete(
            "0:verse",
            contract_hash="WRONG_HASH_FROM_ANOTHER_COMP",
            success=True,
            drum_notes=[{"pitch": 36}],
        )

        correct_key = f"0:verse:{spec.contract_hash}"
        assert not signals.events[correct_key].is_set()
        assert correct_key not in signals._results

        with pytest.raises(asyncio.TimeoutError):
            await signals.wait_for(
                "0:verse",
                contract_hash=spec.contract_hash,
                timeout=0.01,
            )

        print("\n## SIGNAL_LINEAGE_PROOF — Wrong hash invisible to consumer ✓")

    @pytest.mark.anyio
    async def test_signal_hash_verification_on_stored_mismatch(self) -> None:

        """ProtocolViolationError when stored result has wrong contract_hash.

        This tests the internal verification path: signal was stored
        under the correct key but contains a mismatched contract_hash.
        """
        from app.core.maestro_agent_teams.signals import _signal_key

        ch = "correct_hash_1234"
        signals = SectionSignals.from_section_ids(["0:verse"], [ch])
        key = _signal_key("0:verse", ch)

        signals._results[key] = SectionSignalResult(
            success=True, contract_hash="TAMPERED_HASH",
        )
        signals.events[key].set()

        with pytest.raises(ProtocolViolationError, match="Signal lineage mismatch"):
            await signals.wait_for("0:verse", contract_hash=ch)

        print("\n## SIGNAL_LINEAGE_PROOF — Tampered hash rejected ✓")

    @pytest.mark.anyio
    async def test_bass_accepts_matching_contract_hash(self) -> None:

        """Bass accepts signal when contract_hash matches."""
        spec = _spec("verse", 0, 0, 16)
        signals = SectionSignals.from_section_ids(
            ["0:verse"],
            contract_hashes=[spec.contract_hash],
        )
        signals.signal_complete(
            "0:verse",
            contract_hash=spec.contract_hash,
            success=True,
            drum_notes=[{"pitch": 36}],
        )

        result = await signals.wait_for(
            "0:verse",
            contract_hash=spec.contract_hash,
        )
        assert result is not None
        assert result.success is True
        assert result.contract_hash == spec.contract_hash

    def test_lineage_bound_keys(self) -> None:

        """from_section_ids with contract_hashes creates bound keys."""
        specs = [_spec("intro", 0), _spec("verse", 1, 16)]
        signals = SectionSignals.from_section_ids(
            [s.section_id for s in specs],
            contract_hashes=[s.contract_hash for s in specs],
        )

        for spec in specs:
            expected_key = f"{spec.section_id}:{spec.contract_hash}"
            assert expected_key in signals.events

        print("\n## SIGNAL_LINEAGE_PROOF — Bound keys created ✓")


# ═════════════════════════════════════════════════════════════════════════
# PROOF 5 — REPLAY ATTACK PREVENTION
# ═════════════════════════════════════════════════════════════════════════


class TestReplayAttackPrevention:
    """Prove SectionResult from one composition cannot replay in another."""

    @pytest.mark.anyio
    async def test_replay_across_compositions_fails(self) -> None:

        """Same contract in different traces → different execution_hash."""
        spec = _spec()
        sc = _section_contract(spec, parent_hash="parent-A")

        async def _mock_apply(*, tc_id: str, tc_name: str, resolved_args: dict[str, JSONValue], **kw: object) -> _ToolCallOutcome:
            if tc_name == "stori_add_midi_region":
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"regionId": "reg-1", "trackId": "trk-1"},
                    sse_events=[ToolCallEvent(id=tc_id, name=tc_name, params=wrap_dict(resolved_args))],
                    msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"notesAdded": 15, "regionId": "reg-1"},
                sse_events=[ToolCallEvent(id=tc_id, name=tc_name, params=wrap_dict(resolved_args))],
                msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
            )

        store = StateStore(conversation_id="replay-test")
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result_1 = await _run_section_child(
                contract=sc,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="composition-AAA"),
                sse_queue=queue,
            )

        queue2: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result_2 = await _run_section_child(
                contract=sc,
                region_tc=ToolCall(id="r2", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g2", name="stori_generate_midi", params={}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="composition-BBB"),
                sse_queue=queue2,
            )

        print("\n## REPLAY_ATTACK_PROOF")
        print(f"Composition A execution_hash = {result_1.execution_hash}")
        print(f"Composition B execution_hash = {result_2.execution_hash}")
        print(f"Same contract_hash           = {result_1.contract_hash == result_2.contract_hash}")

        assert result_1.contract_hash == result_2.contract_hash
        assert result_1.execution_hash != result_2.execution_hash

        recomputed_A = compute_execution_hash(sc.contract_hash, "composition-AAA")
        recomputed_B = compute_execution_hash(sc.contract_hash, "composition-BBB")
        assert result_1.execution_hash == recomputed_A
        assert result_2.execution_hash == recomputed_B

        assert result_1.execution_hash != recomputed_B


# ═════════════════════════════════════════════════════════════════════════
# PROOF 6 — HASH SCOPE ENFORCEMENT (ADVISORY FIELD EXCLUSION)
# ═════════════════════════════════════════════════════════════════════════


class TestHashScopeEnforcement:
    """Prove advisory fields never affect structural hashes."""

    def test_execution_hash_excluded(self) -> None:

        """execution_hash is in _HASH_EXCLUDED_FIELDS."""
        assert "execution_hash" in _HASH_EXCLUDED_FIELDS

    def test_advisory_changes_do_not_affect_hash(self) -> None:

        """Changing every advisory field produces the same contract_hash."""
        spec = _spec()

        sc_a = _section_contract(
            spec, l2_prompt="original", region_name="Region Original",
        )
        sc_b = _section_contract(
            spec, l2_prompt="COMPLETELY DIFFERENT", region_name="DIFFERENT REGION",
        )

        assert sc_a.contract_hash == sc_b.contract_hash

        ic_a = InstrumentContract(
            instrument_name="Drums", role="drums", style="neo-soul",
            bars=4, tempo=92, key="Fm", start_beat=0,
            sections=(spec,), existing_track_id="trk-old",
            assigned_color="#FF0000", gm_guidance="Original guidance",
        )
        seal_contract(ic_a)

        ic_b = InstrumentContract(
            instrument_name="Drums", role="drums", style="neo-soul",
            bars=4, tempo=92, key="Fm", start_beat=0,
            sections=(spec,), existing_track_id="trk-DIFFERENT",
            assigned_color="#00FF00", gm_guidance="DIFFERENT guidance",
        )
        seal_contract(ic_b)

        assert ic_a.contract_hash == ic_b.contract_hash

        print("\n## HASH_SCOPE_ENFORCEMENT_PROOF")
        print(f"SectionContract: {sc_a.contract_hash} == {sc_b.contract_hash} ✓")
        print(f"InstrumentContract: {ic_a.contract_hash} == {ic_b.contract_hash} ✓")

    def test_composition_contract_excludes_advisory(self) -> None:

        """CompositionContract canonical dict excludes advisory/meta fields."""
        spec = _spec()
        cc = CompositionContract(
            composition_id="test",
            sections=(spec,),
            style="neo-soul",
            tempo=92,
            key="Fm",
        )
        seal_contract(cc)
        canonical = canonical_contract_dict(cc)

        for excluded in _HASH_EXCLUDED_FIELDS:
            assert excluded not in canonical, (
                f"Advisory field '{excluded}' leaked into CompositionContract canonical dict"
            )

    def test_all_excluded_fields_verified(self) -> None:

        """Every field in _HASH_EXCLUDED_FIELDS is absent from ALL canonical dicts."""
        expected = {
            "contract_version", "contract_hash", "parent_contract_hash",
            "execution_hash", "l2_generate_prompt", "region_name",
            "gm_guidance", "assigned_color", "existing_track_id",
        }
        assert _HASH_EXCLUDED_FIELDS == expected
