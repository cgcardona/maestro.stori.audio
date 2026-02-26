"""Role-to-existing-track matching for plan execution."""

from __future__ import annotations

from typing_extensions import TypedDict

from app.contracts.project_types import ProjectContext
from app.core.plan_schemas import ExecutionPlanSchema


class TrackMatchDict(TypedDict, total=False):
    """A project track entry matched to a generation role.

    ``name`` and ``id`` are always present; ``gmProgram``, ``instrument``,
    and ``inferred_role`` are present when available from the project state.
    """

    name: str
    id: str
    gmProgram: int | None
    instrument: str
    inferred_role: str | None

_ROLE_INSTRUMENT_HINTS: dict[str, set[str]] = {
    "melody": {"organ", "piano", "guitar", "flute", "sax", "saxophone",
               "trumpet", "violin", "synth", "lead", "keys", "keyboard",
               "harmonica", "clarinet", "oboe", "fiddle", "mandolin"},
    "bass": {"bass"},
    "drums": {"drums", "drum", "percussion", "kit"},
    "chords": {"organ", "piano", "guitar", "keys", "keyboard", "chord",
               "rhodes", "wurlitzer", "clavinet", "harpsichord"},
    "pads": {"pad", "strings", "ambient"},
    "arp": {"arp", "synth"},
    "lead": {"lead", "synth", "organ", "piano", "guitar"},
}


def _match_roles_to_existing_tracks(
    roles: set[str],
    project_state: ProjectContext,
) -> dict[str, TrackMatchDict]:
    """Map generation roles to existing project tracks.

    Priority order: inferred role → exact name → instrument keyword heuristic.
    Returns dict of role → {name, id, gmProgram} for matched roles.
    """
    from app.core.entity_context import infer_track_role

    tracks = project_state.get("tracks", [])
    if not tracks:
        return {}

    existing: list[TrackMatchDict] = []
    for t in tracks:
        tname = t.get("name", "")
        gm = t.get("gmProgram")
        drum_kit = t.get("drumKitId")
        inferred_role = t.get("role") or infer_track_role(tname, gm, drum_kit)
        existing.append({
            "name": tname,
            "id": t.get("id", ""),
            "gmProgram": gm,
            "instrument": str(t.get("instrument", "")),
            "inferred_role": inferred_role,
        })

    matched: dict[str, TrackMatchDict] = {}
    claimed_ids: set[str] = set()

    # Pass 1: inferred-role match
    for role in sorted(roles):
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            if track["inferred_role"] == role:
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    # Pass 2: exact name match
    for role in sorted(roles - set(matched)):
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            name_lower = track["name"].lower()
            if name_lower == role or role in name_lower:
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    # Pass 3: instrument-keyword heuristic
    for role in sorted(roles - set(matched)):
        hints = _ROLE_INSTRUMENT_HINTS.get(role, set())
        if not hints:
            continue
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            name_lower = track["name"].lower()
            inst_lower = (track.get("instrument") or "").lower()
            if any(h in name_lower or h in inst_lower for h in hints):
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    return matched


def _build_role_to_track_map(
    plan: ExecutionPlanSchema,
    project_state: ProjectContext | None = None,
) -> dict[str, str]:
    """Build a mapping from generation role to actual track name.

    Checks existing project tracks first, then falls back to plan edits.
    Prevents creating duplicate tracks when the project already has matching instruments.
    """
    role_to_track: dict[str, str] = {}

    gen_roles = {g.role.lower() for g in plan.generations}
    if project_state:
        existing_match = _match_roles_to_existing_tracks(gen_roles, project_state)
        for role, info in existing_match.items():
            role_to_track[role] = info["name"]

    track_names: list[str] = [
        edit.name
        for edit in plan.edits
        if edit.action == "add_track" and edit.name
    ]

    all_roles = {"drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"}

    for role in all_roles:
        if role in role_to_track:
            continue
        for track_name in track_names:
            track_lower = track_name.lower()
            if track_lower == role or role in track_lower:
                role_to_track[role] = track_name
                break
        if role not in role_to_track:
            role_to_track[role] = role.capitalize()

    return role_to_track
