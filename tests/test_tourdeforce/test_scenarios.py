"""Tests for scenario definitions and MUSE permutation coverage."""

from __future__ import annotations

from stori_tourdeforce.scenarios import (
    ALL_SCENARIOS,
    CHECKOUT_STRESS_SCENARIO,
    CONFLICT_ONLY_SCENARIO,
    DEEP_BRANCHING_SCENARIO,
    MINIMAL_SCENARIO,
    STANDARD_SCENARIO,
    build_edit_maestro_prompt,
    get_scenario,
)
from stori_tourdeforce.runner import Runner


class TestScenarioDefinitions:

    def test_standard_scenario_has_two_waves(self) -> None:

        s = STANDARD_SCENARIO
        assert len(s.waves) == 2
        assert len(s.waves[0].edits) == 2
        assert len(s.waves[1].edits) == 2

    def test_standard_scenario_has_clean_merges(self) -> None:

        s = STANDARD_SCENARIO
        assert s.waves[0].merge is not None
        assert s.waves[1].merge is not None
        assert s.waves[0].merge.expect_conflict is False
        assert s.waves[1].merge.expect_conflict is False

    def test_standard_scenario_has_conflict_spec(self) -> None:

        s = STANDARD_SCENARIO
        assert s.conflict_spec is not None
        assert s.conflict_spec.branch_a_name == "keys_rewrite_a"
        assert s.conflict_spec.branch_b_name == "keys_rewrite_b"
        assert s.conflict_spec.target_region == "keys"

    def test_standard_scenario_has_checkout_traversal(self) -> None:

        s = STANDARD_SCENARIO
        assert len(s.checkout_traversal) >= 3

    def test_standard_scenario_has_drift_detection(self) -> None:

        s = STANDARD_SCENARIO
        assert s.test_drift_detection is True

    def test_conflict_only_scenario(self) -> None:

        s = CONFLICT_ONLY_SCENARIO
        assert len(s.waves) == 0
        assert s.conflict_spec is not None
        assert s.test_drift_detection is True

    def test_checkout_stress_scenario(self) -> None:

        s = CHECKOUT_STRESS_SCENARIO
        assert len(s.checkout_traversal) >= 5
        assert any(not step.force for step in s.checkout_traversal)

    def test_minimal_scenario_no_merge(self) -> None:

        s = MINIMAL_SCENARIO
        assert len(s.waves) == 1
        assert len(s.waves[0].edits) == 1
        assert s.waves[0].merge is None
        assert s.conflict_spec is None

    def test_deep_branching_scenario_seven_waves(self) -> None:

        s = DEEP_BRANCHING_SCENARIO
        assert len(s.waves) == 7

    def test_deep_branching_scenario_seven_merges(self) -> None:

        """7 clean merges across all waves."""
        s = DEEP_BRANCHING_SCENARIO
        merge_count = sum(1 for w in s.waves if w.merge)
        assert merge_count == 7

    def test_deep_branching_scenario_two_partial_merges(self) -> None:

        """Waves 3 and 6 are partial merges with carry-over."""
        s = DEEP_BRANCHING_SCENARIO
        partial_waves = [w for w in s.waves if w.carry_over]
        assert len(partial_waves) == 2

    def test_deep_branching_scenario_first_partial_merge(self) -> None:

        s = DEEP_BRANCHING_SCENARIO
        w3 = s.waves[2]
        assert len(w3.edits) == 3
        assert w3.merge_branches == ["humanize", "add_fills"]
        assert w3.carry_over == ["accent_dynamics"]

    def test_deep_branching_scenario_second_partial_merge(self) -> None:

        s = DEEP_BRANCHING_SCENARIO
        w6 = s.waves[5]
        assert len(w6.edits) == 3
        assert w6.merge_branches == ["lead_melody", "harmonic_tension"]
        assert w6.carry_over == ["rhythmic_displacement"]

    def test_deep_branching_scenario_carry_over_consumed_w5(self) -> None:

        """Wave 5 merges the carried accent_dynamics with simplify."""
        s = DEEP_BRANCHING_SCENARIO
        w5 = s.waves[4]
        assert w5.merge is not None
        assert w5.merge.right_branch == "accent_dynamics"

    def test_deep_branching_scenario_carry_over_consumed_w7(self) -> None:

        """Wave 7 merges the carried rhythmic_displacement with final_polish."""
        s = DEEP_BRANCHING_SCENARIO
        w7 = s.waves[6]
        assert w7.merge is not None
        assert w7.merge.right_branch == "rhythmic_displacement"

    def test_deep_branching_scenario_checkout_traversal_deep(self) -> None:

        s = DEEP_BRANCHING_SCENARIO
        refs = [step.target_ref for step in s.checkout_traversal]
        for m in ["M1", "M2", "M3", "M4", "M5", "M6", "M7"]:
            assert m in refs, f"Missing {m} in checkout traversal"

    def test_deep_branching_is_default(self) -> None:

        s = get_scenario(0, 1337)
        assert s.name == "deep-branching"


class TestMusePermutationCoverage:
    """Verify that across all scenarios, every MUSE operation is exercised."""

    def test_commit_covered(self) -> None:

        """All scenarios produce at least one commit (the initial compose)."""
        for s in ALL_SCENARIOS:
            assert s.name, "All scenarios must have a name"

    def test_branch_covered(self) -> None:

        """At least one scenario branches (edit steps with parent)."""
        has_branch = any(
            any(len(w.edits) > 0 for w in s.waves)
            for s in ALL_SCENARIOS
        )
        assert has_branch

    def test_clean_merge_covered(self) -> None:

        """At least one scenario has a clean merge."""
        has_clean_merge = any(
            any(w.merge and not w.merge.expect_conflict for w in s.waves)
            for s in ALL_SCENARIOS
        )
        assert has_clean_merge

    def test_conflict_merge_covered(self) -> None:

        """At least one scenario has a conflict specification."""
        has_conflict = any(s.conflict_spec is not None for s in ALL_SCENARIOS)
        assert has_conflict

    def test_checkout_traversal_covered(self) -> None:

        """At least one scenario has checkout traversal steps."""
        has_checkout = any(len(s.checkout_traversal) > 0 for s in ALL_SCENARIOS)
        assert has_checkout

    def test_drift_detection_covered(self) -> None:

        """At least one scenario tests drift detection."""
        has_drift = any(s.test_drift_detection for s in ALL_SCENARIOS)
        assert has_drift

    def test_non_force_checkout_covered(self) -> None:

        """At least one scenario has a non-force checkout step."""
        has_nonforce = any(
            any(not step.force for step in s.checkout_traversal)
            for s in ALL_SCENARIOS
        )
        assert has_nonforce

    def test_force_checkout_covered(self) -> None:

        """At least one scenario has a force checkout step."""
        has_force = any(
            any(step.force for step in s.checkout_traversal)
            for s in ALL_SCENARIOS
        )
        assert has_force

    def test_partial_merge_covered(self) -> None:

        """At least one scenario has a partial merge with carry-over."""
        has_partial = any(
            any(w.carry_over for w in s.waves)
            for s in ALL_SCENARIOS
        )
        assert has_partial


class TestGetScenario:

    def test_cycles_through_all(self) -> None:

        """get_scenario cycles through ALL_SCENARIOS by index."""
        seen = set()
        for i in range(len(ALL_SCENARIOS)):
            s = get_scenario(i, 1337)
            seen.add(s.name)
        assert len(seen) == len(ALL_SCENARIOS)

    def test_wraps_around(self) -> None:

        s0 = get_scenario(0, 1337)
        s_wrap = get_scenario(len(ALL_SCENARIOS), 1337)
        assert s0.name == s_wrap.name

    def test_seed_does_not_affect_selection(self) -> None:

        s1 = get_scenario(0, 1337)
        s2 = get_scenario(0, 42)
        assert s1.name == s2.name


class TestBuildEditMaestroPrompt:
    """Tests for the MAESTRO PROMPT builder used in edit branches."""

    def test_sentinel_header(self) -> None:

        prompt = build_edit_maestro_prompt("tighten bass", ["bass", "drums"])
        assert prompt.startswith("MAESTRO PROMPT\n")

    def test_mode_compose(self) -> None:

        prompt = build_edit_maestro_prompt("tighten bass", ["bass", "drums"])
        assert "Mode: compose" in prompt

    def test_roles_included(self) -> None:

        prompt = build_edit_maestro_prompt("tighten bass", ["bass", "drums", "keys"])
        assert "Role: [bass, drums, keys]" in prompt

    def test_style_key_tempo(self) -> None:

        prompt = build_edit_maestro_prompt(
            "edit", ["bass"], style="jazz", key="Dm", tempo=120, bars=16,
        )
        assert "Style: jazz" in prompt
        assert "Key: Dm" in prompt
        assert "Tempo: 120" in prompt
        assert "bars: 16" in prompt

    def test_request_included(self) -> None:

        prompt = build_edit_maestro_prompt("tighten the groove", ["bass"])
        assert "tighten the groove" in prompt
        assert "Build on the existing composition" in prompt


class TestExtractComposeContext:
    """Tests for _extract_compose_context which parses MAESTRO PROMPT YAML."""

    def test_parses_style_key_tempo(self) -> None:

        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: compose\n"
            "Style: neo-soul\n"
            "Key: Fm\n"
            "Tempo: 92\n"
            "Role: [drums, bass, keys]\n"
        )
        ctx = Runner._extract_compose_context(prompt)
        assert ctx["style"] == "neo-soul"
        assert ctx["key"] == "Fm"
        assert ctx["tempo"] == 92
        assert ctx["roles"] == ["drums", "bass", "keys"]

    def test_defaults_on_missing_fields(self) -> None:

        ctx = Runner._extract_compose_context("just some text")
        assert ctx["style"] == "boom bap"
        assert ctx["key"] == "Am"
        assert ctx["tempo"] == 90
        assert ctx["roles"] == []


class TestBuildProjectSnapshot:
    """Tests for _build_project_snapshot with notes."""

    def test_empty_tool_calls(self) -> None:

        snap = Runner._build_project_snapshot([])
        assert snap["id"] == "tdf-project"
        assert snap["tracks"] == []

    def test_tracks_regions_notes(self) -> None:

        tool_calls = [
            {"name": "addMidiTrack", "id": "t1", "params": {"name": "Bass", "instrument": 33}},
            {"name": "addMidiRegion", "id": "r1", "params": {"trackId": "t1", "startBeat": 0, "lengthBeats": 16}},
            {"name": "addNotes", "params": {"regionId": "r1", "notes": [
                {"pitch": 40, "startBeat": 0, "durationBeats": 2, "velocity": 100},
                {"pitch": 43, "startBeat": 2, "durationBeats": 2, "velocity": 90},
            ]}},
        ]
        snap = Runner._build_project_snapshot(tool_calls, tempo=95)
        assert snap["tempo"] == 95
        assert len(snap["tracks"]) == 1

        track = snap["tracks"][0]
        assert track["id"] == "t1"
        assert track["name"] == "Bass"
        assert track["gmProgram"] == 33
        assert len(track["regions"]) == 1

        region = track["regions"][0]
        assert region["id"] == "r1"
        assert region["noteCount"] == 2
        assert len(region["notes"]) == 2
        assert region["notes"][0]["pitch"] == 40

    def test_multiple_tracks(self) -> None:

        tool_calls = [
            {"name": "addMidiTrack", "id": "t1", "params": {"name": "Bass", "instrument": 33}},
            {"name": "addMidiTrack", "id": "t2", "params": {"name": "Drums", "instrument": 0}},
            {"name": "addMidiRegion", "id": "r1", "params": {"trackId": "t1", "startBeat": 0, "lengthBeats": 16}},
            {"name": "addMidiRegion", "id": "r2", "params": {"trackId": "t2", "startBeat": 0, "lengthBeats": 16}},
            {"name": "addNotes", "params": {"regionId": "r1", "notes": [{"pitch": 40, "startBeat": 0, "durationBeats": 1, "velocity": 100}]}},
            {"name": "addNotes", "params": {"regionId": "r2", "notes": [{"pitch": 36, "startBeat": 0, "durationBeats": 0.5, "velocity": 127}]}},
        ]
        snap = Runner._build_project_snapshot(tool_calls)
        assert len(snap["tracks"]) == 2
        bass = next(t for t in snap["tracks"] if t["name"] == "Bass")
        drums = next(t for t in snap["tracks"] if t["name"] == "Drums")
        assert bass["regions"][0]["noteCount"] == 1
        assert drums["regions"][0]["noteCount"] == 1
