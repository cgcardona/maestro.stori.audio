"""
Entity Registry for Stori Maestro (Cursor-of-DAWs).

This is the **authoritative source of truth** for entity IDs in a project/conversation.

Key principles:
1. Server generates all entity IDs (tracks, regions, buses)
2. LLM uses names/descriptions to reference entities
3. Server resolves names → IDs deterministically
4. All entity references are validated before execution

This eliminates the "fabricated ID" failure mode where LLMs invent entity IDs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from app.contracts.project_types import ProjectContext

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """Types of entities that can be tracked."""
    TRACK = "track"
    REGION = "region"
    BUS = "bus"
    PROJECT = "project"


@dataclass
class EntityMetadata:
    """Typed metadata for tracks, regions, and buses.

    Wraps the raw metadata dict from the DAW with typed accessors
    for well-known fields.  Unknown keys are preserved in ``extra``
    for round-trip serialization.  (Named to avoid collision with
    app.contracts.json_types.EntityMetadataDict.)
    """

    start_beat: float = 0.0
    duration_beats: float = 0.0
    instrument: str = ""
    color: str = ""

    extra: dict[str, str | int | float | bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, object] | None) -> EntityMetadata:
        if not raw:
            return cls()
        known = {"startBeat", "durationBeats", "instrument", "color"}
        raw_start = raw.get("startBeat", 0)
        raw_dur = raw.get("durationBeats", 0)
        return cls(
            start_beat=float(raw_start) if isinstance(raw_start, (int, float)) else 0.0,
            duration_beats=float(raw_dur) if isinstance(raw_dur, (int, float)) else 0.0,
            instrument=str(raw.get("instrument", "")),
            color=str(raw.get("color", "")),
            extra={k: v for k, v in raw.items()
                   if k not in known and isinstance(v, (str, int, float, bool))},
        )

    def to_dict(self) -> dict[str, str | int | float]:
        d: dict[str, str | int | float] = {}
        if self.start_beat:
            d["startBeat"] = self.start_beat
        if self.duration_beats:
            d["durationBeats"] = self.duration_beats
        if self.instrument:
            d["instrument"] = self.instrument
        if self.color:
            d["color"] = self.color
        for k, v in self.extra.items():
            if isinstance(v, (str, int, float)):
                d[k] = v
        return d

    def get(self, key: str, default: float = 0.0) -> float:
        """Backwards-compat accessor for beat-related lookups."""
        if key == "startBeat":
            return self.start_beat or default
        if key == "durationBeats":
            return self.duration_beats or default
        val = self.extra.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        return default


@dataclass
class EntityInfo:
    """Information about a registered entity."""
    id: str
    entity_type: EntityType
    name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: EntityMetadata = field(default_factory=EntityMetadata)

    parent_id: str | None = None

    owner_agent_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.entity_type.value,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata.to_dict(),
            "parent_id": self.parent_id,
            "owner_agent_id": self.owner_agent_id,
        }


def _coerce_metadata(raw: EntityMetadata | dict[str, object] | None) -> EntityMetadata:
    """Accept either form and always return EntityMetadata."""
    if isinstance(raw, EntityMetadata):
        return raw
    return EntityMetadata.from_dict(raw)


class EntityRegistry:
    """
    Server-side registry for entity tracking.
    
    Responsibilities:
    1. Generate UUIDs for new entities
    2. Track name → ID mappings
    3. Resolve references (by name, ID, or description)
    4. Validate entity existence
    
    Usage:
        registry = EntityRegistry()
        
        # Creating entities
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Main Pattern", parent_track_id=track_id)
        
        # Resolving references
        resolved_id = registry.resolve_track("drums")  # Case-insensitive
        resolved_id = registry.resolve_track("abc-123")  # Exact ID match
        
        # Validation
        if registry.exists_track(some_id):
            ...
    """
    
    def __init__(self, project_id: str | None = None):
        """
        Initialize a new entity registry.
        
        Args:
            project_id: Optional project ID for scoping. If None, generates one.
        """
        self.project_id = project_id or str(uuid.uuid4())
        
        # Entity storage by type
        self._tracks: dict[str, EntityInfo] = {}  # id -> EntityInfo
        self._regions: dict[str, EntityInfo] = {}  # id -> EntityInfo
        self._buses: dict[str, EntityInfo] = {}   # id -> EntityInfo
        
        # Name indexes for fast lookup (lowercase name -> id)
        self._track_names: dict[str, str] = {}
        self._region_names: dict[str, str] = {}
        self._bus_names: dict[str, str] = {}
        
        # Track → regions mapping for hierarchical lookup
        self._track_regions: dict[str, list[str]] = {}  # track_id -> [region_ids]
        
        logger.debug(f"🏗️ EntityRegistry initialized for project {self.project_id[:8]}")
    
    # =========================================================================
    # Registry Management
    # =========================================================================

    def clear(self) -> None:
        """Remove all entities from the registry.

        Called before ``sync_from_project_state`` to ensure the registry
        reflects exactly the current project — no stale tracks or regions
        from a previous project or deleted entities.
        """
        self._tracks.clear()
        self._regions.clear()
        self._buses.clear()
        self._track_names.clear()
        self._region_names.clear()
        self._bus_names.clear()
        self._track_regions.clear()

    # =========================================================================
    # Entity Creation
    # =========================================================================
    
    def create_track(
        self,
        name: str,
        track_id: str | None = None,
        metadata: EntityMetadata | dict[str, object] | None = None,
        owner_agent_id: str | None = None,
    ) -> str:
        """Create and register a new track."""
        track_id = track_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=track_id,
            entity_type=EntityType.TRACK,
            name=name,
            metadata=_coerce_metadata(metadata),
            owner_agent_id=owner_agent_id,
        )
        
        self._tracks[track_id] = entity
        self._track_names[name.lower()] = track_id
        self._track_regions[track_id] = []
        
        logger.debug(f"🎹 Registered track: {name} → {track_id[:8]}")
        return track_id
    
    def find_overlapping_region(
        self,
        parent_track_id: str,
        start_beat: int | float,
        duration_beats: int | float,
    ) -> str | None:
        """Return the ID of an existing region that occupies the same beat range, or None."""
        for rid in self._track_regions.get(parent_track_id, []):
            existing = self._regions.get(rid)
            if not existing:
                continue
            e_start = existing.metadata.start_beat
            e_dur = existing.metadata.duration_beats
            if int(e_start) == int(start_beat) and int(e_dur) == int(duration_beats):
                return rid
        return None

    def create_region(
        self,
        name: str,
        parent_track_id: str,
        region_id: str | None = None,
        metadata: EntityMetadata | dict[str, object] | None = None,
        owner_agent_id: str | None = None,
    ) -> str:
        """
        Create and register a new region.

        Idempotent: if a region already occupies the same beat range on the
        same track, the existing region ID is returned instead of creating a
        duplicate.  This prevents collision errors when retry loops cause
        duplicate region creation calls.
        """
        if parent_track_id not in self._tracks:
            raise ValueError(f"Parent track {parent_track_id} not found")

        meta: EntityMetadata = _coerce_metadata(metadata)

        has_beat_range = meta.start_beat or meta.duration_beats
        existing_id = (
            self.find_overlapping_region(
                parent_track_id, meta.start_beat, meta.duration_beats,
            )
            if has_beat_range
            else None
        )
        if existing_id is not None:
            logger.info(
                f"📍 Region already exists at beat {meta.start_beat}-"
                f"{meta.start_beat + meta.duration_beats} "
                f"on track {parent_track_id[:8]} — returning existing {existing_id[:8]}"
            )
            return existing_id
        
        region_id = region_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=region_id,
            entity_type=EntityType.REGION,
            name=name,
            parent_id=parent_track_id,
            metadata=meta,
            owner_agent_id=owner_agent_id,
        )
        
        self._regions[region_id] = entity
        self._region_names[name.lower()] = region_id
        self._track_regions[parent_track_id].append(region_id)
        
        logger.debug(f"📍 Registered region: {name} → {region_id[:8]} (track: {parent_track_id[:8]})")
        return region_id
    
    def create_bus(
        self,
        name: str,
        bus_id: str | None = None,
        metadata: EntityMetadata | dict[str, object] | None = None,
    ) -> str:
        """Create and register a new bus."""
        bus_id = bus_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=bus_id,
            entity_type=EntityType.BUS,
            name=name,
            metadata=_coerce_metadata(metadata),
        )
        
        self._buses[bus_id] = entity
        self._bus_names[name.lower()] = bus_id
        
        logger.debug(f"🔊 Registered bus: {name} → {bus_id[:8]}")
        return bus_id
    
    # =========================================================================
    # Entity Resolution (Name/ID → ID)
    # =========================================================================
    
    def resolve_track(self, name_or_id: str, exact: bool = False) -> str | None:
        """
        Resolve a track reference to its ID.
        
        Checks in order:
        1. Exact ID match
        2. Case-insensitive name match
        3. Fuzzy name match (for typos) - only if exact=False
        
        Args:
            name_or_id: Track name or ID
            exact: If True, only match exact ID or exact name (case-insensitive).
                   Use exact=True when checking for duplicates before creation.
            
        Returns:
            Track ID if found, None otherwise
        """
        # Try exact ID match
        if name_or_id in self._tracks:
            return name_or_id
        
        # Try case-insensitive name match
        name_lower = name_or_id.lower()
        if name_lower in self._track_names:
            return self._track_names[name_lower]
        
        # Try partial/fuzzy match (for "drums" matching "Drums Track")
        # Only if exact=False - fuzzy matching is for lookups, not creation
        if not exact:
            for stored_name, track_id in self._track_names.items():
                if name_lower in stored_name or stored_name in name_lower:
                    return track_id
        
        return None
    
    def resolve_region(
        self,
        name_or_id: str,
        parent_track: str | None = None,
    ) -> str | None:
        """
        Resolve a region reference to its ID.
        
        Args:
            name_or_id: Region name or ID
            parent_track: Optional parent track ID/name for scoping
            
        Returns:
            Region ID if found, None otherwise
        """
        # Try exact ID match
        if name_or_id in self._regions:
            return name_or_id
        
        name_lower = name_or_id.lower()
        
        # If parent specified, search within that track's regions first
        # This handles the case of multiple regions with the same name on different tracks
        if parent_track:
            parent_id = self.resolve_track(parent_track)
            if parent_id and parent_id in self._track_regions:
                for region_id in self._track_regions[parent_id]:
                    region = self._regions.get(region_id)
                    if region and region.name.lower() == name_lower:
                        return region_id
            # Fall through to name lookup only if no parent match found
        
        # Try case-insensitive name match (global)
        if name_lower in self._region_names:
            region_id = self._region_names[name_lower]
            return region_id
        
        # No match found - try parent track search as fallback
        if parent_track:
            parent_id = self.resolve_track(parent_track)
            if parent_id and parent_id in self._track_regions:
                for region_id in self._track_regions[parent_id]:
                    region = self._regions[region_id]
                    if region.name.lower() == name_lower:
                        return region_id
        
        return None
    
    def resolve_bus(self, name_or_id: str) -> str | None:
        """
        Resolve a bus reference to its ID.
        
        Args:
            name_or_id: Bus name or ID
            
        Returns:
            Bus ID if found, None otherwise
        """
        # Try exact ID match
        if name_or_id in self._buses:
            return name_or_id
        
        # Try case-insensitive name match
        name_lower = name_or_id.lower()
        if name_lower in self._bus_names:
            return self._bus_names[name_lower]
        
        return None
    
    def get_or_create_bus(self, name: str) -> str:
        """
        Get existing bus by name or create a new one.
        
        Implements the "ensure bus exists" pattern.
        
        Args:
            name: Bus name
            
        Returns:
            Bus ID (existing or newly created)
        """
        existing = self.resolve_bus(name)
        if existing:
            return existing
        return self.create_bus(name)
    
    # =========================================================================
    # Entity Existence Checks
    # =========================================================================
    
    def exists_track(self, track_id: str) -> bool:
        """Check if a track ID exists."""
        return track_id in self._tracks
    
    def exists_region(self, region_id: str) -> bool:
        """Check if a region ID exists."""
        return region_id in self._regions
    
    def exists_bus(self, bus_id: str) -> bool:
        """Check if a bus ID exists."""
        return bus_id in self._buses
    
    # =========================================================================
    # Entity Retrieval
    # =========================================================================
    
    def get_track(self, track_id: str) -> EntityInfo | None:
        """Get track info by ID."""
        return self._tracks.get(track_id)
    
    def get_region(self, region_id: str) -> EntityInfo | None:
        """Get region info by ID."""
        return self._regions.get(region_id)
    
    def get_bus(self, bus_id: str) -> EntityInfo | None:
        """Get bus info by ID."""
        return self._buses.get(bus_id)
    
    def get_track_regions(self, track_id: str) -> list[EntityInfo]:
        """Get all regions for a track."""
        region_ids = self._track_regions.get(track_id, [])
        return [self._regions[rid] for rid in region_ids if rid in self._regions]
    
    def get_latest_region_for_track(self, track_id: str) -> str | None:
        """Get the most recently created region for a track."""
        region_ids = self._track_regions.get(track_id, [])
        if not region_ids:
            return None
        return region_ids[-1]  # Last created
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def list_tracks(self) -> list[EntityInfo]:
        """list all registered tracks."""
        return list(self._tracks.values())
    
    def list_regions(self) -> list[EntityInfo]:
        """list all registered regions."""
        return list(self._regions.values())
    
    def list_buses(self) -> list[EntityInfo]:
        """list all registered buses."""
        return list(self._buses.values())
    
    def agent_manifest(
        self,
        track_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Compact text manifest of entities for injection into LLM context.

        When ``track_id`` is given, only that track and its regions are
        included.  When ``agent_id`` is given, only entities owned by
        that agent are included — this prevents cross-agent contamination
        (e.g. Strings regions leaking into the Bass agent manifest).
        """
        lines: list[str] = ["ENTITY REGISTRY (authoritative IDs — use these, never guess):"]

        tracks = self.list_tracks()
        if track_id:
            tracks = [t for t in tracks if t.id == track_id]
        if agent_id:
            tracks = [t for t in tracks if t.owner_agent_id == agent_id]
        if not tracks:
            lines.append("  (no tracks yet)")
            return "\n".join(lines)

        for t in tracks:
            meta = t.metadata
            extra = ""
            drum_kit = meta.extra.get("drumKitId")
            gm_prog = meta.extra.get("gmProgram")
            if drum_kit:
                extra = f", drumKit={drum_kit}"
            elif gm_prog is not None and gm_prog != "":
                extra = f", gm={gm_prog}"
            lines.append(f"  Track \"{t.name}\" → trackId='{t.id}'{extra}")

            region_ids = self._track_regions.get(t.id, [])
            for rid in region_ids:
                r = self._regions.get(rid)
                if not r:
                    continue
                if agent_id and r.owner_agent_id != agent_id:
                    continue
                start = r.metadata.start_beat
                dur = r.metadata.duration_beats
                lines.append(
                    f"    Region \"{r.name}\" → regionId='{r.id}' "
                    f"(beat {start}–{int(start) + int(dur)})"
                )
        return "\n".join(lines)

    # =========================================================================
    # Synchronization with Client State
    # =========================================================================
    
    def sync_from_project_state(self, project_state: ProjectContext) -> None:
        """
        Sync registry with client-reported project state.
        
        This is called at the start of each request to ensure the registry
        reflects the current DAW state.
        
        Args:
            project_state: Project state from client (tracks, regions, etc.)
        """
        # Sync tracks
        for track in project_state.get("tracks", []):
            track_id = track.get("id")
            track_name = track.get("name", "")
            
            if track_id and track_id not in self._tracks:
                self.create_track(
                    name=track_name,
                    track_id=track_id,
                    metadata=dict(track),
                )
        
        # Sync regions
        for track in project_state.get("tracks", []):
            track_id = track.get("id")
            if not track_id:
                continue
                
            for region in track.get("regions", []):
                region_id = region.get("id")
                region_name = region.get("name", "")
                
                if region_id and region_id not in self._regions:
                    try:
                        self.create_region(
                            name=region_name,
                            parent_track_id=track_id,
                            region_id=region_id,
                            metadata=dict(region),
                        )
                    except ValueError:
                        logger.warning(f"⚠️ Could not sync region {region_id}: parent track not found")
        
        # Sync buses
        for bus in project_state.get("buses", []):
            bus_id = bus.get("id")
            bus_name = bus.get("name", "")
            
            if bus_id and bus_id not in self._buses:
                self.create_bus(
                    name=bus_name,
                    bus_id=bus_id,
                    metadata=dict(bus),
                )
        
        logger.info(
            f"📊 Registry synced: {len(self._tracks)} tracks, "
            f"{len(self._regions)} regions, {len(self._buses)} buses"
        )
    
    # =========================================================================
    # Serialization
    # =========================================================================
    
    def to_dict(self) -> dict[str, object]:
        """Serialize registry state."""
        return {
            "project_id": self.project_id,
            "tracks": {tid: e.to_dict() for tid, e in self._tracks.items()},
            "regions": {rid: e.to_dict() for rid, e in self._regions.items()},
            "buses": {bid: e.to_dict() for bid, e in self._buses.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EntityRegistry:
        """Deserialize registry state."""
        registry = cls(project_id=str(data.get("project_id", "")))

        raw_tracks = data.get("tracks", {})
        if isinstance(raw_tracks, dict):
            for tid, tdata in raw_tracks.items():
                td: dict[str, object] = tdata if isinstance(tdata, dict) else {}
                name = str(td.get("name", ""))
                raw_meta = td.get("metadata")
                meta = EntityMetadata.from_dict(raw_meta if isinstance(raw_meta, dict) else None)
                raw_owner = td.get("owner_agent_id")
                registry._tracks[tid] = EntityInfo(
                    id=tid,
                    entity_type=EntityType.TRACK,
                    name=name,
                    metadata=meta,
                    owner_agent_id=str(raw_owner) if raw_owner is not None else None,
                )
                registry._track_names[name.lower()] = tid
                registry._track_regions[tid] = []

        raw_regions = data.get("regions", {})
        if isinstance(raw_regions, dict):
            for rid, rdata in raw_regions.items():
                rd: dict[str, object] = rdata if isinstance(rdata, dict) else {}
                name = str(rd.get("name", ""))
                raw_parent = rd.get("parent_id")
                parent_id = str(raw_parent) if raw_parent is not None else None
                raw_meta = rd.get("metadata")
                meta = EntityMetadata.from_dict(raw_meta if isinstance(raw_meta, dict) else None)
                raw_owner = rd.get("owner_agent_id")
                registry._regions[rid] = EntityInfo(
                    id=rid,
                    entity_type=EntityType.REGION,
                    name=name,
                    parent_id=parent_id,
                    metadata=meta,
                    owner_agent_id=str(raw_owner) if raw_owner is not None else None,
                )
                registry._region_names[name.lower()] = rid
                if parent_id and parent_id in registry._track_regions:
                    registry._track_regions[parent_id].append(rid)

        raw_buses = data.get("buses", {})
        if isinstance(raw_buses, dict):
            for bid, bdata in raw_buses.items():
                bd: dict[str, object] = bdata if isinstance(bdata, dict) else {}
                name = str(bd.get("name", ""))
                raw_meta = bd.get("metadata")
                meta = EntityMetadata.from_dict(raw_meta if isinstance(raw_meta, dict) else None)
                raw_owner = bd.get("owner_agent_id")
                registry._buses[bid] = EntityInfo(
                    id=bid,
                    entity_type=EntityType.BUS,
                    name=name,
                    metadata=meta,
                    owner_agent_id=str(raw_owner) if raw_owner is not None else None,
                )
                registry._bus_names[name.lower()] = bid

        return registry


# =============================================================================
# Convenience Functions
# =============================================================================

def create_registry_from_context(project_state: ProjectContext | None = None) -> EntityRegistry:
    """
    Create a new registry and optionally sync with project state.
    
    This is the main factory function for creating registries.
    
    Args:
        project_state: Optional project state from client
        
    Returns:
        Initialized EntityRegistry
    """
    registry = EntityRegistry()
    
    if project_state:
        registry.sync_from_project_state(project_state)
    
    return registry
