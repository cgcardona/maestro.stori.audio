"""Advisory target-scope check for structured prompt requests."""

from __future__ import annotations

from maestro.contracts.json_types import JSONValue
from maestro.core.entity_registry import EntityRegistry


def _check_target_scope(
    tool_name: str,
    params: dict[str, JSONValue],
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
        _tid_raw = params.get("trackId")
        track_id = _tid_raw if isinstance(_tid_raw, str) else None
        _tname_raw = params.get("trackName") or params.get("name")
        track_name = _tname_raw if isinstance(_tname_raw, str) else None

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
        _rname_raw = params.get("name")
        region_name = _rname_raw if isinstance(_rname_raw, str) else None
        if region_name and region_name.lower() != name.lower():
            warnings.append(
                f"Target scope is region:{name} but tool call references region '{region_name}'"
            )

    return warnings
