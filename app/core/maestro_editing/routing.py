"""Routing helpers used by the Maestro orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.contracts.project_types import ProjectContext
from app.core.intent import Intent, IntentResult, SSEState
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)
from app.core.tools import ALL_TOOLS

if TYPE_CHECKING:
    from app.core.prompt_parser import ParsedPrompt


def _project_needs_structure(project_context: ProjectContext) -> bool:
    """Check if the project is empty and needs structural creation.

    Returns True when the project has no tracks, meaning composition
    requests should use EDITING mode (tool_call events) rather than
    COMPOSING mode (variation review) — you can't diff against nothing.
    """
    tracks = project_context.get("tracks", [])
    return len(tracks) == 0


def _is_additive_composition(
    parsed: "ParsedPrompt" | None,
    project_context: ProjectContext,
) -> bool:
    """Detect if a composition request creates a new section (EDITING, not COMPOSING).

    Returns True when the request appends new content (Position: after/last)
    or introduces roles that don't map to existing tracks. In these cases
    EDITING mode is preferred because the content is additive — there is
    nothing to diff against, and COMPOSING with phraseCount: 0 is always a bug.

    STORI PROMPTs with 2+ roles always return True: they spawn Agent Teams
    regardless of whether the named tracks already exist, because the prompt
    always places new timeline content (new regions at a later beat position).
    Routing confidence and existing-track state are both irrelevant here.
    """
    if not parsed:
        return False

    # A structured STORI PROMPT (2+ roles) always runs Agent Teams — even when
    # all tracks exist. The prompt creates new regions at later beat positions,
    # so it is always additive. This prevents the composing/variation pipeline
    # from intercepting STORI PROMPTs and producing clarification questions.
    if parsed.roles and len(parsed.roles) >= 2:
        return True

    if parsed.position and parsed.position.kind in ("after", "last"):
        return True

    existing_names = {
        t.get("name", "").lower()
        for t in project_context.get("tracks", [])
        if t.get("name")
    }
    if parsed.roles:
        for role in parsed.roles:
            if role.lower() not in existing_names:
                return True

    return False


def _create_editing_composition_route(route: "IntentResult") -> "IntentResult":
    """Build an EDITING IntentResult for composition on empty projects.

    When the project has no tracks, composition requests should use EDITING
    mode so structural changes (tracks, regions, instruments, notes) are
    emitted as tool_call events for real-time frontend rendering.
    """
    all_composition_tools = (
        set(_PRIMITIVES_TRACK) | set(_PRIMITIVES_REGION)
        | set(_PRIMITIVES_FX) | set(_PRIMITIVES_MIXING)
        | {"stori_set_tempo", "stori_set_key"}
    )
    return IntentResult(
        intent=route.intent,
        sse_state=SSEState.EDITING,
        confidence=route.confidence,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=all_composition_tools,
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=route.reasons + ("empty_project_override",),
    )
