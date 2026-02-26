"""Entity reference resolution and validation against the entity registry."""

from __future__ import annotations

import logging

from app.core.entity_registry import EntityRegistry
from app.core.tool_validation.models import EntityResolutionResult, ValidationError
from app.core.tool_validation.constants import _ENTITY_CREATING_SKIP

logger = logging.getLogger(__name__)


def _find_closest_match(
    query: str,
    candidates: list[str],
    threshold: float = 0.6,
) -> str | None:
    """Find the closest matching string from candidates using simple similarity."""
    if not candidates:
        return None

    query_lower = query.lower()

    for c in candidates:
        c_lower = c.lower()
        if c_lower.startswith(query_lower) or query_lower.startswith(c_lower):
            return c

    for c in candidates:
        if query_lower in c.lower() or c.lower() in query_lower:
            return c

    best_match = None
    best_score = 0.0

    for c in candidates:
        c_lower = c.lower()
        query_chars = set(query_lower)
        c_chars = set(c_lower)

        if not query_chars or not c_chars:
            continue

        intersection = len(query_chars & c_chars)
        union = len(query_chars | c_chars)
        score = intersection / union

        if score > best_score and score >= threshold:
            best_score = score
            best_match = c

    return best_match


def _resolve_and_validate_entities(
    tool_name: str,
    params: dict[str, object],
    registry: EntityRegistry,
) -> EntityResolutionResult:
    """
    Resolve entity name aliases to IDs and validate that referenced entities exist.

    Returns a dict with keys: params, errors, warnings.
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []
    resolved = params.copy()

    def _track_suggestions() -> list[str]:
        return [t.name for t in registry.list_tracks()]

    def _region_suggestions(track_id: str | None = None) -> list[str]:
        if track_id:
            return [
                f"{r.name} (regionId: {r.id})"
                for r in registry.get_track_regions(track_id)
            ]
        return [f"{r.name} (regionId: {r.id})" for r in registry.list_regions()]

    def _bus_suggestions() -> list[str]:
        return [b.name for b in registry.list_buses()]

    # trackName → trackId resolution
    if "trackName" in resolved and "trackId" not in resolved:
        _raw_track_name = resolved["trackName"]
        if isinstance(_raw_track_name, str):
            track_name: str = _raw_track_name
            track_id = registry.resolve_track(track_name)
            if track_id:
                resolved["trackId"] = track_id
                logger.debug(f"🔗 Resolved trackName '{track_name}' → {track_id[:8]}")
            else:
                available = _track_suggestions()
                suggestion = ""
                if available:
                    closest = _find_closest_match(track_name, available)
                    suggestion = (
                        f" Did you mean '{closest}'?"
                        if closest
                        else f" Available tracks: {', '.join(available[:5])}"
                    )
                errors.append(ValidationError(
                    field="trackName",
                    message=f"Track '{track_name}' not found.{suggestion}",
                    code="ENTITY_NOT_FOUND",
                ))

    skip_fields = _ENTITY_CREATING_SKIP.get(tool_name, set())

    # Validate trackId
    if "trackId" in resolved and "trackId" not in skip_fields:
        _raw_track_id = resolved["trackId"]
        if isinstance(_raw_track_id, str):
            track_id_val: str = _raw_track_id
            if not registry.exists_track(track_id_val):
                resolved_id = registry.resolve_track(track_id_val)
                if resolved_id:
                    resolved["trackId"] = resolved_id
                else:
                    available = _track_suggestions()
                    suggestion = ""
                    if available:
                        closest = _find_closest_match(track_id_val, available)
                        suggestion = (
                            f" Did you mean '{closest}'?"
                            if closest
                            else f" Available: {', '.join(available[:5])}"
                        )
                    errors.append(ValidationError(
                        field="trackId",
                        message=f"Track '{track_id_val}' not found.{suggestion}",
                        code="ENTITY_NOT_FOUND",
                    ))

    # Validate regionId
    if "regionId" in resolved and "regionId" not in skip_fields:
        _raw_region_id = resolved["regionId"]
        if isinstance(_raw_region_id, str):
            region_id: str = _raw_region_id
            if not registry.exists_region(region_id):
                resolved_id = registry.resolve_region(region_id)
                if resolved_id:
                    resolved["regionId"] = resolved_id
                else:
                    _raw_track_id2 = resolved.get("trackId")
                    cur_track_id: str | None = _raw_track_id2 if isinstance(_raw_track_id2, str) else None
                    suggestion = ""
                    # Detect the common hallucination: LLM passed a trackId as regionId
                    if registry.exists_track(region_id):
                        track_regions = _region_suggestions(region_id)
                        if track_regions:
                            suggestion = (
                                f" NOTE: '{region_id[:8]}...' is a trackId, not a regionId."
                                f" Use the regionId from stori_add_midi_region."
                                f" Regions on this track: {', '.join(track_regions[:3])}"
                            )
                        else:
                            suggestion = (
                                f" NOTE: '{region_id[:8]}...' is a trackId, not a regionId."
                                " Call stori_add_midi_region first to create a region."
                            )
                    else:
                        available = _region_suggestions(cur_track_id)
                        if available:
                            closest = _find_closest_match(region_id, available)
                            suggestion = (
                                f" Did you mean '{closest}'?"
                                if closest
                                else f" Available: {', '.join(available[:5])}"
                            )
                    errors.append(ValidationError(
                        field="regionId",
                        message=f"Region '{region_id}' not found.{suggestion}",
                        code="ENTITY_NOT_FOUND",
                    ))

    # Validate busId
    if "busId" in resolved and "busId" not in skip_fields:
        _raw_bus_id = resolved["busId"]
        if isinstance(_raw_bus_id, str):
            bus_id: str = _raw_bus_id
            if not registry.exists_bus(bus_id):
                resolved_id = registry.resolve_bus(bus_id)
                if resolved_id:
                    resolved["busId"] = resolved_id
                else:
                    available = _bus_suggestions()
                    suggestion = ""
                    if available:
                        closest = _find_closest_match(bus_id, available)
                        suggestion = (
                            f" Did you mean '{closest}'?"
                            if closest
                            else f" Available: {', '.join(available[:5])}"
                        )
                    errors.append(ValidationError(
                        field="busId",
                        message=f"Bus '{bus_id}' not found.{suggestion}",
                        code="ENTITY_NOT_FOUND",
                    ))

    return {"params": resolved, "errors": errors, "warnings": warnings}


def resolve_tool_entities(
    tool_name: str,
    params: dict[str, object],
    registry: EntityRegistry,
) -> dict[str, object]:
    """Resolve entity names to IDs in tool call params.

    Public wrapper around ``_resolve_and_validate_entities`` that returns
    only the resolved params dict.  Callers who need validation errors
    should use ``validate_tool_call`` instead.
    """
    return _resolve_and_validate_entities(tool_name, params, registry)["params"]
