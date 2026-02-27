"""Scenario definitions — branching/merge workflows for Tour de Force runs.

Each scenario defines a sequence of MUSE operations to exercise
after the initial compose step. Scenarios produce diverse edit types
and multi-branch merge patterns, including deliberate conflict paths.

MUSE permutations covered:
  - commit (save_variation + set_head)
  - branch (save_variation with parent_variation_id)
  - clean merge (disjoint regions -> auto-resolve)
  - partial merge (merge a subset of wave branches, carry the rest)
  - conflict merge (same region, overlapping notes -> 409)
  - checkout traversal (time-travel across the DAG)
  - drift detection (checkout without force -> 409 when dirty)
  - force checkout (override drift)
  - graph export (log -> ASCII + JSON + Mermaid)
  - lineage walking (parent chain traversal)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EditStep:
    """A single edit operation in a scenario."""

    branch_name: str
    edit_prompt: str
    target_track: str
    edit_type: str


@dataclass(frozen=True)
class MergeStep:
    """A merge operation combining two branches."""

    left_branch: str
    right_branch: str
    label: str
    expect_conflict: bool = False


@dataclass(frozen=True)
class CheckoutStep:
    """A checkout traversal operation."""

    target_ref: str  # symbolic: "C1", "M1", etc. — resolved at runtime
    force: bool = True
    expect_blocked: bool = False  # True -> expect 409 drift block


@dataclass(frozen=True)
class ConflictBranchSpec:
    """Specification for creating a deliberate merge conflict.

    Both branches modify the SAME region with overlapping note positions
    but different content, guaranteeing a conflict.
    """

    branch_a_name: str
    branch_b_name: str
    target_region: str
    target_track: str
    branch_a_prompt: str
    branch_b_prompt: str


@dataclass(frozen=True)
class Wave:
    """A wave of parallel edit branches followed by an optional merge.

    Supports partial merges: ``merge_branches`` selects which branches to
    merge (defaults to all).  Unmerged branches are carried forward via
    ``carry_over`` and injected into the next wave's available commit pool.
    """

    edits: list[EditStep] = field(default_factory=list)
    merge: MergeStep | None = None
    merge_branches: list[str] | None = None
    carry_over: list[str] | None = None


@dataclass(frozen=True)
class Scenario:
    """A complete scenario: compose + N waves + conflicts + checkouts."""

    name: str
    description: str
    waves: list[Wave] = field(default_factory=list)
    conflict_spec: ConflictBranchSpec | None = None
    checkout_traversal: list[CheckoutStep] = field(default_factory=list)
    test_drift_detection: bool = False


# ── Edit Prompts ──────────────────────────────────────────────────────────

EDIT_PROMPTS = {
    "bass_tighten": (
        "MOTIVE EDIT: tighten the bass groove, add subtle syncopation, "
        "keep the original key feel. Do not change drums."
    ),
    "drums_variation": (
        "MOTIVE EDIT: add ghost notes to the drums, introduce a ride cymbal pattern, "
        "keep the kick pattern but add hi-hat variation. Do not change bass or keys."
    ),
    "keys_reharm": (
        "MOTIVE EDIT: reharmonize the keys part using extended chords (9ths, 11ths), "
        "add subtle voice leading. Do not change drums or bass."
    ),
    "arrangement_extend": (
        "MOTIVE EDIT: extend the arrangement by adding a B section that contrasts "
        "with the main groove. Add a 4-bar transition fill."
    ),
    "humanize": (
        "MOTIVE EDIT: humanize the timing — add subtle swing and micro-timing "
        "variations. Vary velocities for a more organic feel."
    ),
    "simplify": (
        "MOTIVE EDIT: simplify the arrangement — remove unnecessary notes, "
        "create more space. Less is more."
    ),
    "accent_dynamics": (
        "MOTIVE EDIT: add dynamic accents — create crescendos and decrescendos "
        "across the phrase. Make it breathe."
    ),
    "add_fills": (
        "MOTIVE EDIT: add drum fills at the end of every 4-bar phrase. "
        "Use the existing kit sounds. Do not change other instruments."
    ),
    "keys_rewrite_a": (
        "MOTIVE EDIT: completely rewrite the keys melody — use a descending "
        "chromatic line starting from C5 with staccato articulation. "
        "Do not change drums or bass."
    ),
    "keys_rewrite_b": (
        "MOTIVE EDIT: completely rewrite the keys melody — use an ascending "
        "whole-tone scale starting from C4 with legato sustain. "
        "Do not change drums or bass."
    ),
    "bass_octave_drop": (
        "MOTIVE EDIT: drop the bass an octave lower, add sub-bass weight. "
        "Keep the rhythmic pattern intact. Do not change drums."
    ),
    "strings_pad": (
        "MOTIVE EDIT: add sustained string pad in the background — slow attack, "
        "long release, following the chord changes. Warm and cinematic."
    ),
    "percussion_layer": (
        "MOTIVE EDIT: layer additional percussion — shaker, tambourine, or congas. "
        "Complement the existing drum pattern without clashing."
    ),
    "lead_melody": (
        "MOTIVE EDIT: compose a lead melody line — singable, memorable, "
        "with call-and-response phrasing over the existing harmony."
    ),
    "rhythmic_displacement": (
        "MOTIVE EDIT: displace the rhythmic feel — shift accents by an eighth note, "
        "create a subtle polyrhythmic tension against the kick."
    ),
    "harmonic_tension": (
        "MOTIVE EDIT: add harmonic tension — use tritone substitutions and passing "
        "diminished chords. Build and resolve tension over 8 bars."
    ),
    "final_polish": (
        "MOTIVE EDIT: final polish pass — balance velocities across all instruments, "
        "ensure smooth transitions, tighten any loose timing."
    ),
}


# ── Deep Branching Scenario (rich DAG with partial merges) ────────────────
#
# Target Mermaid topology (6 clean merges + 1 conflict merge = 7 merge layers):
#
#   C1 ─┬─ bass_tighten ──┐
#        └─ drums_variation ┘ → M1 (rhythm)
#                                ├─ keys_reharm ─────┐
#                                └─ arrangement_ext ──┘ → M2 (arrangement)
#                                                        ├─ humanize ──────┐
#                                                        ├─ add_fills ─────┘ → M3 (feel, partial)
#                                                        └─ accent_dynamics ── (carried)
#                                                                 M3 ├─ strings_pad ──┐
#                                                                    └─ percussion ───┘ → M4 (texture)
#                                                                           M4 ├─ simplify ──────────────┐
#                                                                              └─ accent_dynamics (carry) ┘ → M5 (polish)
#                                                                                    M5 ├─ lead_melody ──────────┐
#                                                                                       ├─ harmonic_tension ─────┘ → M6 (final, partial)
#                                                                                       └─ rhythmic_displacement ── (carried)
#                                                                                              M6 ├─ final_polish ──────────────┐
#                                                                                                 └─ rhythmic_displacement (carry) ┘ → M7 (master)
#                                                                                                       M7 ├─ keys_rewrite_a ──┐
#                                                                                                          └─ keys_rewrite_b ──┘ → conflict merge

DEEP_BRANCHING_SCENARIO = Scenario(
    name="deep-branching",
    description=(
        "Insane DAG: compose -> C1 -> "
        "W1 (bass_tighten + drums_variation -> M1 rhythm) -> "
        "W2 (keys_reharm + arrangement_extend -> M2 arrangement) -> "
        "W3 (humanize + add_fills + accent_dynamics -> partial M3, carry accent) -> "
        "W4 (strings_pad + percussion_layer -> M4 texture) -> "
        "W5 (simplify + carried accent_dynamics -> M5 polish) -> "
        "W6 (lead_melody + harmonic_tension + rhythmic_displacement -> partial M6, carry rhythmic) -> "
        "W7 (final_polish + carried rhythmic_displacement -> M7 master) -> "
        "conflict (keys_rewrite_a vs keys_rewrite_b) -> "
        "checkout traversal (C1 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> C1)"
    ),
    waves=[
        # W1: Rhythm foundation ─────────────────────────────────────────────
        Wave(
            edits=[
                EditStep("bass_tighten", EDIT_PROMPTS["bass_tighten"], "bass", "tighten"),
                EditStep("drums_variation", EDIT_PROMPTS["drums_variation"], "drums", "variation"),
            ],
            merge=MergeStep("bass_tighten", "drums_variation", "M1: rhythm merge"),
        ),
        # W2: Arrangement layer ─────────────────────────────────────────────
        Wave(
            edits=[
                EditStep("keys_reharm", EDIT_PROMPTS["keys_reharm"], "keys", "reharmonize"),
                EditStep("arrangement_extend", EDIT_PROMPTS["arrangement_extend"], "arrangement", "extend"),
            ],
            merge=MergeStep("keys_reharm", "arrangement_extend", "M2: arrangement merge"),
        ),
        # W3: Feel (3-way fan-out, partial merge, carry accent) ─────────────
        Wave(
            edits=[
                EditStep("humanize", EDIT_PROMPTS["humanize"], "all", "humanize"),
                EditStep("add_fills", EDIT_PROMPTS["add_fills"], "drums", "fills"),
                EditStep("accent_dynamics", EDIT_PROMPTS["accent_dynamics"], "all", "accent"),
            ],
            merge=MergeStep("humanize", "add_fills", "M3: feel merge (partial)"),
            merge_branches=["humanize", "add_fills"],
            carry_over=["accent_dynamics"],
        ),
        # W4: Texture (new instruments) ─────────────────────────────────────
        Wave(
            edits=[
                EditStep("strings_pad", EDIT_PROMPTS["strings_pad"], "strings", "pad"),
                EditStep("percussion_layer", EDIT_PROMPTS["percussion_layer"], "percussion", "layer"),
            ],
            merge=MergeStep("strings_pad", "percussion_layer", "M4: texture merge"),
        ),
        # W5: Polish (simplify + carried accent_dynamics) ───────────────────
        Wave(
            edits=[
                EditStep("simplify", EDIT_PROMPTS["simplify"], "all", "simplify"),
            ],
            merge=MergeStep("simplify", "accent_dynamics", "M5: polish merge (+ carried accent)"),
        ),
        # W6: Melodic layer (3-way fan-out, partial merge, carry rhythmic) ──
        Wave(
            edits=[
                EditStep("lead_melody", EDIT_PROMPTS["lead_melody"], "lead", "melody"),
                EditStep("harmonic_tension", EDIT_PROMPTS["harmonic_tension"], "keys", "tension"),
                EditStep("rhythmic_displacement", EDIT_PROMPTS["rhythmic_displacement"], "all", "displacement"),
            ],
            merge=MergeStep("lead_melody", "harmonic_tension", "M6: melodic merge (partial)"),
            merge_branches=["lead_melody", "harmonic_tension"],
            carry_over=["rhythmic_displacement"],
        ),
        # W7: Master (final_polish + carried rhythmic_displacement) ─────────
        Wave(
            edits=[
                EditStep("final_polish", EDIT_PROMPTS["final_polish"], "all", "polish"),
            ],
            merge=MergeStep("final_polish", "rhythmic_displacement", "M7: master merge (+ carried rhythmic)"),
        ),
    ],
    conflict_spec=ConflictBranchSpec(
        branch_a_name="keys_rewrite_a",
        branch_b_name="keys_rewrite_b",
        target_region="keys",
        target_track="keys",
        branch_a_prompt=EDIT_PROMPTS["keys_rewrite_a"],
        branch_b_prompt=EDIT_PROMPTS["keys_rewrite_b"],
    ),
    checkout_traversal=[
        CheckoutStep(target_ref="C1", force=True),
        CheckoutStep(target_ref="M1", force=True),
        CheckoutStep(target_ref="M2", force=True),
        CheckoutStep(target_ref="M3", force=True),
        CheckoutStep(target_ref="M4", force=True),
        CheckoutStep(target_ref="M5", force=True),
        CheckoutStep(target_ref="M6", force=True),
        CheckoutStep(target_ref="M7", force=True),
        CheckoutStep(target_ref="C1", force=True),
    ],
    test_drift_detection=True,
)


# ── Standard Scenario (migrated to waves) ────────────────────────────────

STANDARD_SCENARIO = Scenario(
    name="compose->commit->edit->branch->merge->conflict->checkout",
    description=(
        "Full lifecycle: compose -> commit C1 -> "
        "branch bass_tighten + drums_variation -> clean merge M1 -> "
        "branch keys_reharm + arrangement_extend -> clean merge M2 -> "
        "conflict (keys_rewrite_a vs keys_rewrite_b) -> "
        "checkout traversal (C1 -> M1 -> M2 -> C1)"
    ),
    waves=[
        Wave(
            edits=[
                EditStep("bass_tighten", EDIT_PROMPTS["bass_tighten"], "bass", "tighten"),
                EditStep("drums_variation", EDIT_PROMPTS["drums_variation"], "drums", "variation"),
            ],
            merge=MergeStep("bass_tighten", "drums_variation", "M1: bass+drums merge"),
        ),
        Wave(
            edits=[
                EditStep("keys_reharm", EDIT_PROMPTS["keys_reharm"], "keys", "reharmonize"),
                EditStep("arrangement_extend", EDIT_PROMPTS["arrangement_extend"], "arrangement", "extend"),
            ],
            merge=MergeStep("keys_reharm", "arrangement_extend", "M2: keys+arrangement merge"),
        ),
    ],
    conflict_spec=ConflictBranchSpec(
        branch_a_name="keys_rewrite_a",
        branch_b_name="keys_rewrite_b",
        target_region="keys",
        target_track="keys",
        branch_a_prompt=EDIT_PROMPTS["keys_rewrite_a"],
        branch_b_prompt=EDIT_PROMPTS["keys_rewrite_b"],
    ),
    checkout_traversal=[
        CheckoutStep(target_ref="C1", force=True),
        CheckoutStep(target_ref="M1", force=True),
        CheckoutStep(target_ref="M2", force=True),
        CheckoutStep(target_ref="C1", force=True),
    ],
    test_drift_detection=True,
)


CONFLICT_ONLY_SCENARIO = Scenario(
    name="compose->conflict->recover",
    description=(
        "Conflict-focused: compose -> commit C1 -> "
        "two branches rewrite same keys region -> merge conflict (409) -> "
        "record conflict details -> checkout back to C1 -> force checkout"
    ),
    conflict_spec=ConflictBranchSpec(
        branch_a_name="keys_rewrite_a",
        branch_b_name="keys_rewrite_b",
        target_region="keys",
        target_track="keys",
        branch_a_prompt=EDIT_PROMPTS["keys_rewrite_a"],
        branch_b_prompt=EDIT_PROMPTS["keys_rewrite_b"],
    ),
    checkout_traversal=[
        CheckoutStep(target_ref="C1", force=True),
    ],
    test_drift_detection=True,
)


CHECKOUT_STRESS_SCENARIO = Scenario(
    name="compose->branch->checkout-stress",
    description=(
        "Checkout stress test: compose -> two branches -> "
        "rapid checkout traversal across all commits -> "
        "drift detection (non-force checkout) -> force recovery"
    ),
    waves=[
        Wave(
            edits=[
                EditStep("simplify", EDIT_PROMPTS["simplify"], "all", "simplify"),
                EditStep("accent_dynamics", EDIT_PROMPTS["accent_dynamics"], "all", "accent"),
            ],
            merge=MergeStep("simplify", "accent_dynamics", "merge: simplify+accent"),
        ),
    ],
    checkout_traversal=[
        CheckoutStep(target_ref="C1", force=True),
        CheckoutStep(target_ref="C2_simplify", force=True),
        CheckoutStep(target_ref="C3_accent_dynamics", force=True),
        CheckoutStep(target_ref="M1", force=True),
        CheckoutStep(target_ref="C1", force=False, expect_blocked=False),
        CheckoutStep(target_ref="C1", force=True),
    ],
    test_drift_detection=True,
)


MINIMAL_SCENARIO = Scenario(
    name="compose->commit->edit",
    description="Minimal: compose -> commit -> one edit branch -> commit",
    waves=[
        Wave(
            edits=[
                EditStep("humanize", EDIT_PROMPTS["humanize"], "all", "humanize"),
            ],
        ),
    ],
)


ALL_SCENARIOS = [
    DEEP_BRANCHING_SCENARIO,
    STANDARD_SCENARIO,
    CONFLICT_ONLY_SCENARIO,
    CHECKOUT_STRESS_SCENARIO,
    MINIMAL_SCENARIO,
]


def get_scenario(run_index: int, seed: int) -> Scenario:
    """Select scenario based on run index — cycles through all scenarios."""
    return ALL_SCENARIOS[run_index % len(ALL_SCENARIOS)]


# ── MAESTRO PROMPT builder ─────────────────────────────────────────────────


def build_edit_maestro_prompt(
    request: str,
    roles: list[str],
    *,
    style: str = "boom bap",
    key: str = "Am",
    tempo: int = 90,
    bars: int = 8,
) -> str:
    """Wrap an edit description into a structured MAESTRO PROMPT.

    The ``Mode: compose`` sentinel routes the prompt through the full
    Storpheus generation pipeline (Intent.GENERATE_MUSIC) instead of the
    EDITING path which only exposes mixing tools.
    """
    role_yaml = ", ".join(roles)
    request_lines = request.strip().replace("\n", "\n  ")
    return (
        "MAESTRO PROMPT\n"
        "Mode: compose\n"
        f"Style: {style}\n"
        f"Key: {key}\n"
        f"Tempo: {tempo}\n"
        f"Role: [{role_yaml}]\n"
        "Constraints:\n"
        f"  bars: {bars}\n"
        "Request: |\n"
        f"  Build on the existing composition shown in the project state.\n"
        f"  {request_lines}\n"
    )
