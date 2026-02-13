"""
Entity context for LLM prompts (Cursor-of-DAWs).

Centralizes the "Available entities" block injected into EDITING and other
tool-calling flows so the LLM can reference tracks/regions/buses by ID or name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.state_store import StateStore


def build_entity_context_for_llm(store: "StateStore") -> str:
    """
    Build the "Available entities in the project" string for LLM system messages.

    Used by the EDITING handler so the model can reference existing entities
    by trackId/regionId/busId or by trackName/regionName (server resolves).
    """
    registry = store.registry
    tracks_info = [{"name": t.name, "id": t.id} for t in registry.list_tracks()]
    regions_info = [
        {"name": r.name, "id": r.id, "trackId": r.parent_id}
        for r in registry.list_regions()
    ]
    buses_info = [{"name": b.name, "id": b.id} for b in registry.list_buses()]

    example_track_id = tracks_info[0]["id"] if tracks_info else "abc-123"
    example_track_name = tracks_info[0]["name"] if tracks_info else "My Track"

    return (
        "Available entities in the project:\n"
        f"- Tracks: {tracks_info or '(none)'}\n"
        f"- Regions: {regions_info or '(none)'}\n"
        f"- Buses: {buses_info or '(none)'}\n\n"
        "When referencing existing entities, you can use either:\n"
        "1. The trackId/regionId/busId directly (preferred), OR\n"
        "2. The trackName/regionName - the server will resolve it to the correct ID.\n"
        f'Example: stori_add_midi_region(trackId="{example_track_id}", ...) '
        f'or stori_add_midi_region(trackName="{example_track_name}", ...)'
    )
