"""Convert validated ExecutionPlanSchema to ToolCall sequences."""

from __future__ import annotations

from typing_extensions import TypedDict

from app.contracts.project_types import ProjectContext
from app.core.expansion import ToolCall
from app.core.gm_instruments import infer_gm_program
from app.core.plan_schemas import EditStep, ExecutionPlanSchema, GenerationStep, MixStep
from app.core.planner.track_matching import _build_role_to_track_map


class _ExistingTrackInfo(TypedDict, total=False):
    """Cached info for a track already present in the DAW project."""

    id: str
    name: str
    gmProgram: int | None


def _beats_per_bar(project_state: ProjectContext | None) -> int:
    """Extract beats per bar from project state, defaulting to 4."""
    if project_state:
        ts = project_state.get("time_signature") or project_state.get("timeSignature")
        if isinstance(ts, (list, tuple)) and len(ts) >= 1:
            return int(ts[0])
        if isinstance(ts, dict):
            return int(ts.get("numerator", 4))
        if isinstance(ts, str) and "/" in ts:
            return int(ts.split("/")[0])
    return 4


def _schema_to_tool_calls(
    plan: ExecutionPlanSchema,
    region_start_offset: float = 0.0,
    project_state: ProjectContext | None = None,
) -> list[ToolCall]:
    """
    Convert validated plan schema to ToolCalls.

    Tool calls are grouped contiguously by track:
      1. stori_add_midi_track (creation)
      2. stori_set_track_color / stori_set_track_icon (styling)
      3. stori_add_midi_region (region creation)
      4. stori_generate_midi (content generation)
      5. stori_add_insert_effect (insert effects)

    After per-track groups:
      6. stori_ensure_bus + stori_add_send (shared bus routing)
      7. stori_set_track_volume / stori_set_track_pan (mix adjustments)

    Args:
        region_start_offset: Beat offset applied to every new region's startBeat.
        project_state: When provided, existing tracks are reused (skipping
            add_track/set_color/set_icon) and their UUIDs are attached.
    """
    from app.core.track_styling import get_track_styling

    project_state = project_state or {}
    bpb = _beats_per_bar(project_state)

    existing_tracks: dict[str, _ExistingTrackInfo] = {}
    for t in project_state.get("tracks", []):
        name = t.get("name", "")
        if name:
            gm_raw = t.get("gmProgram")
            existing_tracks[name.lower()] = {
                "id": str(t.get("id") or ""),
                "name": str(name),
                "gmProgram": int(gm_raw) if isinstance(gm_raw, int) else None,
            }

    role_to_track = _build_role_to_track_map(plan, project_state)

    _role_mapped_existing: set[str] = set()
    for role, target_name in role_to_track.items():
        if target_name.lower() in existing_tracks:
            _role_mapped_existing.add(role)

    edits_by_track: dict[str, list[EditStep]] = {}
    regions_by_track: dict[str, list[EditStep]] = {}
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            edits_by_track.setdefault(edit.name.lower(), []).append(edit)
        elif edit.action == "add_region" and edit.track:
            resolved = role_to_track.get(edit.track.lower(), edit.track)
            regions_by_track.setdefault(resolved.lower(), []).append(edit)

    inserts_by_track: dict[str, list[MixStep]] = {}
    sends: list[MixStep] = []
    buses: set[str] = set()
    volume_pan: list[MixStep] = []
    for mix in plan.mix:
        if mix.action == "add_insert" and mix.type:
            inserts_by_track.setdefault(mix.track.lower(), []).append(mix)
        elif mix.action == "add_send" and mix.bus:
            sends.append(mix)
            buses.add(mix.bus)
        elif mix.action in ("set_volume", "set_pan"):
            volume_pan.append(mix)

    ordered_tracks: list[str] = []
    seen_lower: set[str] = set()

    for gen in plan.generations:
        tname = gen.trackName or role_to_track.get(gen.role, gen.role.capitalize())
        if tname.lower() not in seen_lower:
            ordered_tracks.append(tname)
            seen_lower.add(tname.lower())

    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            if edit.name.lower() not in seen_lower:
                ordered_tracks.append(edit.name)
                seen_lower.add(edit.name.lower())

    tool_calls: list[ToolCall] = []
    _new_track_idx = 0

    for track_name in ordered_tracks:
        t_lower = track_name.lower()
        is_existing = t_lower in existing_tracks
        is_role_mapped = t_lower in _role_mapped_existing

        # 1. Track creation (color + icon + gmProgram inline so the
        #    frontend can create the track in a single atomic step).
        if not is_existing and not is_role_mapped and t_lower in edits_by_track:
            styling = get_track_styling(track_name, rotation_index=_new_track_idx)
            _new_track_idx += 1
            track_params: dict[str, object] = {
                "name": track_name,
                "color": styling["color"],
                "icon": styling["icon"],
            }
            gm = infer_gm_program(track_name)
            if gm is not None:
                track_params["gmProgram"] = gm
            tool_calls.append(ToolCall(name="stori_add_midi_track", params=track_params))

        # 3. Region creation
        for edit in regions_by_track.get(t_lower, []):
            if not edit.track:
                continue
            bar_start = edit.barStart or 0
            resolved_track = role_to_track.get(edit.track.lower(), edit.track)
            region_params: dict[str, object] = {
                "name": resolved_track,
                "trackName": resolved_track,
                "startBeat": bar_start * bpb + region_start_offset,
                "durationBeats": (edit.bars or 4) * bpb,
            }
            existing = existing_tracks.get(resolved_track.lower())
            if existing and existing["id"]:
                region_params["trackId"] = existing["id"]
            tool_calls.append(ToolCall(name="stori_add_midi_region", params=region_params))

        # 4. Content generation
        for gen in plan.generations:
            gen_track = gen.trackName or role_to_track.get(gen.role, gen.role.capitalize())
            if gen_track.lower() != t_lower:
                continue
            normalized_style = gen.style.replace("_", " ").strip() if gen.style else ""
            gen_params: dict[str, object] = {
                "role": gen.role,
                "style": normalized_style,
                "tempo": gen.tempo,
                "bars": gen.bars,
                "key": gen.key or "",
                "trackName": gen_track,
                "constraints": gen.constraints or {},
            }
            existing = existing_tracks.get(gen_track.lower())
            if existing and existing["id"]:
                gen_params["trackId"] = existing["id"]
            tool_calls.append(ToolCall(name="stori_generate_midi", params=gen_params))

        # 5. Insert effects
        for mix in inserts_by_track.get(t_lower, []):
            tool_calls.append(ToolCall(
                name="stori_add_insert_effect",
                params={"trackName": mix.track, "type": mix.type},
            ))

    # 6. Shared buses + sends
    for bus_name in sorted(buses):
        tool_calls.append(ToolCall(name="stori_ensure_bus", params={"name": bus_name}))

    for mix in sends:
        tool_calls.append(ToolCall(
            name="stori_add_send",
            params={"trackName": mix.track, "busName": mix.bus},
        ))

    # 7. Mix adjustments
    for mix in volume_pan:
        if mix.action == "set_volume" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_volume",
                params={"trackName": mix.track, "volume": mix.value},
            ))
        elif mix.action == "set_pan" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_pan",
                params={"trackName": mix.track, "pan": mix.value},
            ))

    return tool_calls
