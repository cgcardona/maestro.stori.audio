"""
In-Memory Variation Store (v1).

Stores VariationRecord and PhraseRecord objects for the lifecycle
of a variation proposal.

For v1, this is in-memory (dict-based). For production, swap with
Redis or PostgreSQL backend behind the same interface.

Key design:
    - One VariationRecord per proposal
    - Phrases stored as they're generated (streaming-friendly)
    - State transitions enforced via state_machine.assert_transition()
    - Sequence counter managed here (single source of truth)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from app.variation.core.event_envelope import PhrasePayload
from app.variation.core.state_machine import (
    VariationStatus,
    assert_transition,
    InvalidTransitionError,
    is_terminal,
)
from app.variation.core.event_envelope import SequenceCounter

logger = logging.getLogger(__name__)


@dataclass
class PhraseRecord:
    """A stored phrase within a variation."""

    phrase_id: str
    variation_id: str
    sequence: int
    track_id: str
    region_id: str
    beat_start: float
    beat_end: float
    label: str
    diff_json: PhrasePayload
    ai_explanation: str | None = None
    tags: list[str] = field(default_factory=list)
    # Region position — populated at store time so commit can build updatedRegions
    # without re-querying the compose-phase StateStore.
    region_start_beat: float | None = None
    region_duration_beats: float | None = None
    region_name: str | None = None


@dataclass
class VariationRecord:
    """
    A variation proposal record.

    Tracks the full lifecycle from CREATED through terminal state.
    """

    variation_id: str
    project_id: str
    base_state_id: str
    intent: str
    status: VariationStatus = VariationStatus.CREATED
    ai_explanation: str | None = None
    affected_tracks: list[str] = field(default_factory=list)
    affected_regions: list[str] = field(default_factory=list)
    phrases: list[PhraseRecord] = field(default_factory=list)
    sequence_counter: SequenceCounter = field(default_factory=SequenceCounter)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = None
    # The StateStore conversation_id from the compose phase. Stored so that commit
    # can look up the same store (and its notes) rather than creating a fresh one.
    conversation_id: str = ""

    def next_sequence(self) -> int:
        """Get the next sequence number for this variation's event stream."""
        return self.sequence_counter.next()

    @property
    def last_sequence(self) -> int:
        """Get the last emitted sequence number."""
        return self.sequence_counter.current

    def transition_to(self, new_status: VariationStatus) -> None:
        """
        Transition to a new status with state machine validation.

        Raises InvalidTransitionError if the transition is not allowed.
        """
        assert_transition(self.status, new_status)
        old_status = self.status
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        logger.info(
            f"Variation {self.variation_id[:8]}: "
            f"{old_status.value} → {new_status.value}"
        )

    def add_phrase(self, phrase: PhraseRecord) -> None:
        """Add a phrase to the variation."""
        self.phrases.append(phrase)

    def get_phrase(self, phrase_id: str) -> PhraseRecord | None:
        """Get a phrase by ID."""
        for phrase in self.phrases:
            if phrase.phrase_id == phrase_id:
                return phrase
        return None

    def get_phrase_ids(self) -> list[str]:
        """Get all phrase IDs in sequence order."""
        return [p.phrase_id for p in sorted(self.phrases, key=lambda p: p.sequence)]


class VariationStore:
    """
    In-memory store for variation records.

    Thread-safe is not strictly required for asyncio (single event loop),
    but we keep operations atomic where possible.
    """

    def __init__(self) -> None:
        self._records: dict[str, VariationRecord] = {}

    def create(
        self,
        project_id: str,
        base_state_id: str,
        intent: str,
        variation_id: str | None = None,
        conversation_id: str = "",
    ) -> VariationRecord:
        """
        Create a new variation record in CREATED state.

        Returns the created record.
        """
        variation_id = variation_id or str(uuid.uuid4())

        if variation_id in self._records:
            raise ValueError(f"Variation {variation_id} already exists")

        record = VariationRecord(
            variation_id=variation_id,
            project_id=project_id,
            base_state_id=base_state_id,
            intent=intent,
            conversation_id=conversation_id,
        )

        self._records[variation_id] = record

        logger.info(
            f"Created variation {variation_id[:8]} for project {project_id[:8]}"
        )
        return record

    def get(self, variation_id: str) -> VariationRecord | None:
        """Get a variation record by ID. Returns None if not found."""
        return self._records.get(variation_id)

    def get_or_raise(self, variation_id: str) -> VariationRecord:
        """Get a variation record by ID. Raises KeyError if not found."""
        record = self._records.get(variation_id)
        if record is None:
            raise KeyError(f"Variation {variation_id} not found")
        return record

    def transition(
        self,
        variation_id: str,
        new_status: VariationStatus,
    ) -> VariationRecord:
        """
        Transition a variation to a new status.

        Raises KeyError if not found, InvalidTransitionError if invalid.
        """
        record = self.get_or_raise(variation_id)
        record.transition_to(new_status)
        return record

    def delete(self, variation_id: str) -> bool:
        """Delete a variation record. Returns True if it existed."""
        return self._records.pop(variation_id, None) is not None

    def list_for_project(
        self,
        project_id: str,
        status: VariationStatus | None = None,
    ) -> list[VariationRecord]:
        """list variations for a project, optionally filtered by status."""
        results = [
            r for r in self._records.values()
            if r.project_id == project_id
        ]
        if status is not None:
            results = [r for r in results if r.status == status]
        return sorted(results, key=lambda r: r.created_at, reverse=True)

    def cleanup_expired(self, max_age_seconds: int = 3600) -> int:
        """
        Expire and clean up old non-terminal variations.

        Returns count of expired records.
        """
        now = datetime.now(timezone.utc)
        expired_count = 0

        for record in list(self._records.values()):
            if is_terminal(record.status):
                continue

            age = (now - record.created_at).total_seconds()
            if age > max_age_seconds:
                try:
                    record.transition_to(VariationStatus.EXPIRED)
                    expired_count += 1
                    logger.info(
                        f"Expired variation {record.variation_id[:8]} "
                        f"(age: {age:.0f}s)"
                    )
                except InvalidTransitionError:
                    pass

        return expired_count

    def clear(self) -> None:
        """Clear all records (for testing)."""
        self._records.clear()

    @property
    def count(self) -> int:
        """Total number of records."""
        return len(self._records)


# Singleton instance
_store: VariationStore | None = None


def get_variation_store() -> VariationStore:
    """Get the singleton VariationStore instance."""
    global _store
    if _store is None:
        _store = VariationStore()
    return _store


def reset_variation_store() -> None:
    """Reset the singleton (for testing)."""
    global _store
    if _store is not None:
        _store.clear()
    _store = None
