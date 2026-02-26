"""Wire-format TypedDicts for the execution plan JSON produced by the planner LLM.

These types represent the **input** shape — the raw JSON dict returned by the
planner LLM and passed to ``validate_plan_json`` / ``build_plan_from_dict`` —
before Pydantic coerces and validates it into the domain models in
``app/core/plan_schemas/models.py``.

Design rationale
----------------
Using precise TypedDicts here lets the static type system catch structural
mismatches at call-sites (test helpers, macro expansion, MCP adapters) without
hiding intent behind a generic ``dict[str, Any]`` or a covariant ``Mapping``.

Relationship to Pydantic models
--------------------------------
Each TypedDict mirrors its Pydantic counterpart:

  ``GenerationStepDict``  →  ``GenerationStep`` (Pydantic)
  ``EditStepDict``        →  ``EditStep``        (Pydantic)
  ``MixStepDict``         →  ``MixStep``         (Pydantic)
  ``PlanJsonDict``        →  ``ExecutionPlanSchema`` (Pydantic)

All TypedDicts use ``total=False`` because the LLM may emit partial objects;
Pydantic enforces required fields at validation time.  Downstream code that
holds a *validated* plan should work with the domain models, not these dicts.

Boundary rule
-------------
``validate_plan_json`` (boundary validator) accepts ``Mapping[str, object]``
because its job is to classify *arbitrary* LLM output as valid/invalid.
Everything that already *knows* it has a plan dict — helpers, tests, macros —
uses ``PlanJsonDict`` for structural guarantees.
"""

from __future__ import annotations

from typing import TypedDict


class GenerationStepDict(TypedDict, total=False):
    """Wire format for one MIDI generation step in the planner JSON.

    Mirrors ``app.core.plan_schemas.models.GenerationStep``.

    Required at runtime (Pydantic-enforced): ``role``, ``style``, ``tempo``,
    ``bars``.  All other fields are optional.

    Fields
    ------
    role
        Instrument role — one of the ``GenerationRole`` literals
        (``"drums"``, ``"bass"``, ``"chords"``, ``"melody"``, ``"arp"``,
        ``"pads"``, ``"fx"``, ``"lead"``).
    style
        Normalised style tag (e.g. ``"boom_bap"``, ``"house"``, ``"lofi"``).
        Pydantic lower-cases and underscores the value.
    tempo
        Project tempo in BPM (30–300).
    bars
        Number of bars to generate (1–64).
    key
        Root key (e.g. ``"Am"``, ``"F#"``, ``"G minor"``).  Required for
        melodic instruments; optional but warned-on if absent.
    constraints
        Open-shape per-role generation hints (density, syncopation, swing …).
        Populated from the emotion vector during composing.
    trackName
        Override track display name when the ``role`` is a generic category
        (e.g. ``"Banjo"`` for role ``"melody"``).
    """

    role: str
    style: str
    tempo: int
    bars: int
    key: str
    constraints: dict[str, object]
    trackName: str  # noqa: N815


class EditStepDict(TypedDict, total=False):
    """Wire format for one DAW edit step (track/region creation) in the planner JSON.

    Mirrors ``app.core.plan_schemas.models.EditStep``.

    Required at runtime (Pydantic-enforced): ``action``.  Additional fields
    depend on the action:

    - ``add_track``  → ``name`` required
    - ``add_region`` → ``track`` + ``bars`` required; ``barStart`` defaults to 0

    Fields
    ------
    action
        Edit action type — ``"add_track"`` or ``"add_region"``.
    name
        Display name for the new track (``add_track``).
    track
        Target track name for region creation (``add_region``).
    barStart
        Zero-indexed start bar for the region (``add_region``).  Defaults to 0.
    bars
        Duration in bars (``add_region``, 1–64).
    """

    action: str
    name: str
    track: str
    barStart: int  # noqa: N815
    bars: int


class MixStepDict(TypedDict, total=False):
    """Wire format for one mixing/effects step in the planner JSON.

    Mirrors ``app.core.plan_schemas.models.MixStep``.

    Required at runtime (Pydantic-enforced): ``action``, ``track``.  Additional
    fields depend on the action:

    - ``add_insert`` → ``type`` required (normalised to known effect names)
    - ``add_send``   → ``bus`` required
    - ``set_volume`` / ``set_pan`` → ``value`` required

    Fields
    ------
    action
        Mix action — ``"add_insert"``, ``"add_send"``, ``"set_volume"``, or
        ``"set_pan"``.
    track
        Target track display name.
    type
        Effect type for ``add_insert`` (e.g. ``"compressor"``, ``"eq"``,
        ``"reverb"``).  Pydantic validates against a known-effect allow-list.
    bus
        Bus name for ``add_send``.
    value
        Numeric value for volume (dB) or pan (-100 to 100).
    """

    action: str
    track: str
    type: str
    bus: str
    value: float


class PlanJsonDict(TypedDict, total=False):
    """Complete wire format for a planner LLM response.

    Mirrors ``app.core.plan_schemas.models.ExecutionPlanSchema``.

    This is the root type passed to ``build_plan_from_dict`` and used by test
    helpers and macro expansion code that constructs plan fixtures directly.
    Runtime validation is still delegated to Pydantic — this TypedDict ensures
    structural correctness at construction time, not at execution time.

    Fields
    ------
    generations
        Ordered list of MIDI generation steps.  Each entry maps to a Storpheus
        ``/generate`` call via the planner executor.
    edits
        Ordered list of DAW edit steps (track and region creation).
    mix
        Ordered list of mixing/effects steps applied after generation.
    explanation
        LLM-provided explanation of the plan.  Logged but never executed.
    """

    generations: list[GenerationStepDict]
    edits: list[EditStepDict]
    mix: list[MixStepDict]
    explanation: str
