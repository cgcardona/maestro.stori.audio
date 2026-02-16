"""
Variation State Machine (v1 Canonical).

Explicit state transitions for the Muse/Variation lifecycle.
Never mutate variation status directly — always go through assert_transition().

States:
    CREATED   — Variation record exists; generation not started
    STREAMING — Generation in progress; events flowing
    READY     — Generation complete; all phrases emitted; safe to commit
    COMMITTED — Accepted phrases applied; canonical state advanced
    DISCARDED — Variation canceled; no canonical mutation
    FAILED    — Terminal error; no canonical mutation
    EXPIRED   — TTL cleanup; no canonical mutation

Invariants:
    1. No mutation of canonical state during CREATED/STREAMING/READY.
    2. base_state_id is recorded at CREATED, validated at COMMIT.
    3. Commit is only allowed from READY.
    4. Discard is always safe from CREATED/STREAMING/READY.
    5. Terminal states (COMMITTED/DISCARDED/FAILED/EXPIRED) are final.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class VariationStatus(str, Enum):
    """Canonical variation lifecycle states."""

    CREATED = "created"
    STREAMING = "streaming"
    READY = "ready"
    COMMITTED = "committed"
    DISCARDED = "discarded"
    FAILED = "failed"
    EXPIRED = "expired"


# Terminal states — no further transitions allowed.
TERMINAL_STATES: frozenset[VariationStatus] = frozenset({
    VariationStatus.COMMITTED,
    VariationStatus.DISCARDED,
    VariationStatus.FAILED,
    VariationStatus.EXPIRED,
})

# Allowed transitions: from_state -> set of valid to_states.
_TRANSITIONS: dict[VariationStatus, frozenset[VariationStatus]] = {
    VariationStatus.CREATED: frozenset({
        VariationStatus.STREAMING,
        VariationStatus.DISCARDED,
        VariationStatus.FAILED,
        VariationStatus.EXPIRED,
    }),
    VariationStatus.STREAMING: frozenset({
        VariationStatus.READY,
        VariationStatus.DISCARDED,
        VariationStatus.FAILED,
        VariationStatus.EXPIRED,
    }),
    VariationStatus.READY: frozenset({
        VariationStatus.COMMITTED,
        VariationStatus.DISCARDED,
        VariationStatus.FAILED,
        VariationStatus.EXPIRED,
    }),
    # Terminal states have no outgoing transitions.
    VariationStatus.COMMITTED: frozenset(),
    VariationStatus.DISCARDED: frozenset(),
    VariationStatus.FAILED: frozenset(),
    VariationStatus.EXPIRED: frozenset(),
}


class InvalidTransitionError(Exception):
    """Raised when a state transition violates the state machine."""

    def __init__(self, from_state: VariationStatus, to_state: VariationStatus):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition: {from_state.value} → {to_state.value}"
        )


def assert_transition(
    from_state: VariationStatus,
    to_state: VariationStatus,
) -> None:
    """
    Validate that a state transition is allowed.

    Raises InvalidTransitionError if the transition violates the state machine.
    """
    allowed = _TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise InvalidTransitionError(from_state, to_state)


def is_terminal(status: VariationStatus) -> bool:
    """Check if a status is terminal (no further transitions)."""
    return status in TERMINAL_STATES


def can_commit(status: VariationStatus) -> bool:
    """Check if a variation can be committed from the given status."""
    return status == VariationStatus.READY


def can_discard(status: VariationStatus) -> bool:
    """Check if a variation can be discarded from the given status."""
    return status in {
        VariationStatus.CREATED,
        VariationStatus.STREAMING,
        VariationStatus.READY,
    }
