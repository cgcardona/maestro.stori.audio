"""Wire-format TypedDicts for the execution plan JSON produced by the planner LLM.

These mirror the Pydantic models in ``models.py`` but represent the *input*
shape — the raw JSON dict before Pydantic validates and coerces it.  Using
precise TypedDicts here lets the static type system catch mismatches at
call-sites (test helpers, macro expansion, MCP adapters) without hiding
structural intent behind generic ``dict[str, Any]`` or covariant ``Mapping``.

Boundary validators (``validate_plan_json``) deliberately accept
``Mapping[str, object]`` because their job is to verify *unknown* data.
Everything that already knows it has a plan dict uses ``PlanJsonDict``.
"""

from __future__ import annotations

from typing import TypedDict


class GenerationStepDict(TypedDict, total=False):
    """Wire format for one ``GenerationStep`` in the planner JSON.

    Required at runtime (enforced by Pydantic): ``role``, ``style``,
    ``tempo``, ``bars``.  All others are optional.
    """

    role: str
    style: str
    tempo: int
    bars: int
    key: str
    constraints: dict[str, object]
    trackName: str  # noqa: N815


class EditStepDict(TypedDict, total=False):
    """Wire format for one ``EditStep`` (track/region creation) in the planner JSON.

    Required at runtime: ``action``.  ``name`` required for ``add_track``;
    ``track`` + ``bars`` required for ``add_region``.
    """

    action: str
    name: str
    track: str
    barStart: int  # noqa: N815
    bars: int


class MixStepDict(TypedDict, total=False):
    """Wire format for one ``MixStep`` (insert/send/volume/pan) in the planner JSON.

    Required at runtime: ``action``, ``track``.  Additional fields vary by action.
    """

    action: str
    track: str
    type: str
    bus: str
    value: float


class PlanJsonDict(TypedDict, total=False):
    """Complete wire format for a planner LLM response.

    Mirrors ``ExecutionPlanSchema``.  ``total=False`` because any key may be
    absent in partial or invalid LLM output — Pydantic enforces required fields
    at validation time.
    """

    generations: list[GenerationStepDict]
    edits: list[EditStepDict]
    mix: list[MixStepDict]
    explanation: str
