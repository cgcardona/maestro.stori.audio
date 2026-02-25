"""Advisory target-scope check for structured prompt requests."""

from __future__ import annotations

from typing import Any

from app.core.entity_registry import EntityRegistry


def _check_target_scope(
    tool_name: str,
    params: dict[str, Any],
    target_scope: tuple[str, str | None],
    registry: EntityRegistry | None,
) -> list[str]:
    """
    Emit advisory warnings when a tool call operates outside the structured
    prompt's Target scope. Never blocks execution.

    Args:
        target_scope: (kind, name) e.g. ("track", "Bass")
    """
    kind, name = target_scope

    if kind == "project":
        return []

    if not name:
        return []

    warnings: list[str] = []

    if kind == "track":
        track_id = params.get("trackId")
        track_name = params.get("trackName") or params.get("name")

        if track_name and track_name.lower() != name.lower():
            warnings.append(
                f"Target scope is track:{name} but tool call references track '{track_name}'"
            )
        elif track_id and registry:
            target_id = registry.resolve_track(name)
            if target_id and track_id != target_id:
                warnings.append(
                    f"Target scope is track:{name} but tool call references a different track"
                )

    elif kind == "region":
        region_name = params.get("name")
        if region_name and region_name.lower() != name.lower():
            warnings.append(
                f"Target scope is region:{name} but tool call references region '{region_name}'"
            )

    return warnings
