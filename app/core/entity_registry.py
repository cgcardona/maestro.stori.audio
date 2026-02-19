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
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """Types of entities that can be tracked."""
    TRACK = "track"
    REGION = "region"
    BUS = "bus"
    PROJECT = "project"


@dataclass
class EntityInfo:
    """Information about a registered entity."""
    id: str
    entity_type: EntityType
    name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # For regions: which track they belong to
    parent_id: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.entity_type.value,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "parent_id": self.parent_id,
        }


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
    
    def __init__(self, project_id: Optional[str] = None):
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
        track_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Create and register a new track.
        
        Args:
            name: Display name for the track
            track_id: Optional pre-generated ID (for client sync)
            metadata: Optional additional metadata
            
        Returns:
            The track ID (generated or provided)
        """
        track_id = track_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=track_id,
            entity_type=EntityType.TRACK,
            name=name,
            metadata=metadata or {},
        )
        
        self._tracks[track_id] = entity
        self._track_names[name.lower()] = track_id
        self._track_regions[track_id] = []
        
        logger.debug(f"🎹 Registered track: {name} → {track_id[:8]}")
        return track_id
    
    def create_region(
        self,
        name: str,
        parent_track_id: str,
        region_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Create and register a new region.
        
        Args:
            name: Display name for the region
            parent_track_id: ID of the parent track
            region_id: Optional pre-generated ID
            metadata: Optional additional metadata (startBeat, durationBeats, etc.)
            
        Returns:
            The region ID
            
        Raises:
            ValueError: If parent track doesn't exist
        """
        if parent_track_id not in self._tracks:
            raise ValueError(f"Parent track {parent_track_id} not found")
        
        region_id = region_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=region_id,
            entity_type=EntityType.REGION,
            name=name,
            parent_id=parent_track_id,
            metadata=metadata or {},
        )
        
        self._regions[region_id] = entity
        self._region_names[name.lower()] = region_id
        self._track_regions[parent_track_id].append(region_id)
        
        logger.debug(f"📍 Registered region: {name} → {region_id[:8]} (track: {parent_track_id[:8]})")
        return region_id
    
    def create_bus(
        self,
        name: str,
        bus_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Create and register a new bus.
        
        Args:
            name: Display name for the bus
            bus_id: Optional pre-generated ID
            metadata: Optional additional metadata
            
        Returns:
            The bus ID
        """
        bus_id = bus_id or str(uuid.uuid4())
        
        entity = EntityInfo(
            id=bus_id,
            entity_type=EntityType.BUS,
            name=name,
            metadata=metadata or {},
        )
        
        self._buses[bus_id] = entity
        self._bus_names[name.lower()] = bus_id
        
        logger.debug(f"🔊 Registered bus: {name} → {bus_id[:8]}")
        return bus_id
    
    # =========================================================================
    # Entity Resolution (Name/ID → ID)
    # =========================================================================
    
    def resolve_track(self, name_or_id: str, exact: bool = False) -> Optional[str]:
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
        parent_track: Optional[str] = None,
    ) -> Optional[str]:
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
    
    def resolve_bus(self, name_or_id: str) -> Optional[str]:
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
    
    def get_track(self, track_id: str) -> Optional[EntityInfo]:
        """Get track info by ID."""
        return self._tracks.get(track_id)
    
    def get_region(self, region_id: str) -> Optional[EntityInfo]:
        """Get region info by ID."""
        return self._regions.get(region_id)
    
    def get_bus(self, bus_id: str) -> Optional[EntityInfo]:
        """Get bus info by ID."""
        return self._buses.get(bus_id)
    
    def get_track_regions(self, track_id: str) -> list[EntityInfo]:
        """Get all regions for a track."""
        region_ids = self._track_regions.get(track_id, [])
        return [self._regions[rid] for rid in region_ids if rid in self._regions]
    
    def get_latest_region_for_track(self, track_id: str) -> Optional[str]:
        """Get the most recently created region for a track."""
        region_ids = self._track_regions.get(track_id, [])
        if not region_ids:
            return None
        return region_ids[-1]  # Last created
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def list_tracks(self) -> list[EntityInfo]:
        """List all registered tracks."""
        return list(self._tracks.values())
    
    def list_regions(self) -> list[EntityInfo]:
        """List all registered regions."""
        return list(self._regions.values())
    
    def list_buses(self) -> list[EntityInfo]:
        """List all registered buses."""
        return list(self._buses.values())
    
    # =========================================================================
    # Synchronization with Client State
    # =========================================================================
    
    def sync_from_project_state(self, project_state: dict[str, Any]) -> None:
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
                    metadata=track,
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
                            metadata=region,
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
                    metadata=bus,
                )
        
        logger.info(
            f"📊 Registry synced: {len(self._tracks)} tracks, "
            f"{len(self._regions)} regions, {len(self._buses)} buses"
        )
    
    # =========================================================================
    # Serialization
    # =========================================================================
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize registry state."""
        return {
            "project_id": self.project_id,
            "tracks": {tid: e.to_dict() for tid, e in self._tracks.items()},
            "regions": {rid: e.to_dict() for rid, e in self._regions.items()},
            "buses": {bid: e.to_dict() for bid, e in self._buses.items()},
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EntityRegistry":
        """Deserialize registry state."""
        registry = cls(project_id=data.get("project_id"))
        
        # Restore tracks
        for tid, tdata in data.get("tracks", {}).items():
            registry._tracks[tid] = EntityInfo(
                id=tid,
                entity_type=EntityType.TRACK,
                name=tdata.get("name", ""),
                metadata=tdata.get("metadata", {}),
            )
            registry._track_names[tdata.get("name", "").lower()] = tid
            registry._track_regions[tid] = []
        
        # Restore regions
        for rid, rdata in data.get("regions", {}).items():
            parent_id = rdata.get("parent_id")
            registry._regions[rid] = EntityInfo(
                id=rid,
                entity_type=EntityType.REGION,
                name=rdata.get("name", ""),
                parent_id=parent_id,
                metadata=rdata.get("metadata", {}),
            )
            registry._region_names[rdata.get("name", "").lower()] = rid
            if parent_id and parent_id in registry._track_regions:
                registry._track_regions[parent_id].append(rid)
        
        # Restore buses
        for bid, bdata in data.get("buses", {}).items():
            registry._buses[bid] = EntityInfo(
                id=bid,
                entity_type=EntityType.BUS,
                name=bdata.get("name", ""),
                metadata=bdata.get("metadata", {}),
            )
            registry._bus_names[bdata.get("name", "").lower()] = bid
        
        return registry


# =============================================================================
# Convenience Functions
# =============================================================================

def create_registry_from_context(project_state: Optional[dict[str, Any]] = None) -> EntityRegistry:
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
