"""
Tests for the Variation State Machine.

Covers all valid transitions, invalid transitions, terminal states,
and helper predicates per the v1 canonical spec.
"""
from __future__ import annotations

from typing import Any
import pytest

from app.variation.core.state_machine import (
    VariationStatus,
    assert_transition,
    InvalidTransitionError,
    is_terminal,
    can_commit,
    can_discard,
    TERMINAL_STATES,
)


# =============================================================================
# Valid Transitions
# =============================================================================


class TestValidTransitions:
    """Test all allowed state transitions."""

    def test_created_to_streaming(self) -> None:

        """CREATED → STREAMING (start_generation)."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)

    def test_created_to_discarded(self) -> None:

        """CREATED → DISCARDED (discard before generation)."""
        assert_transition(VariationStatus.CREATED, VariationStatus.DISCARDED)

    def test_created_to_failed(self) -> None:

        """CREATED → FAILED (validation error)."""
        assert_transition(VariationStatus.CREATED, VariationStatus.FAILED)

    def test_created_to_expired(self) -> None:

        """CREATED → EXPIRED (TTL cleanup)."""
        assert_transition(VariationStatus.CREATED, VariationStatus.EXPIRED)

    def test_streaming_to_ready(self) -> None:

        """STREAMING → READY (generation complete)."""
        assert_transition(VariationStatus.STREAMING, VariationStatus.READY)

    def test_streaming_to_discarded(self) -> None:

        """STREAMING → DISCARDED (cancel during generation)."""
        assert_transition(VariationStatus.STREAMING, VariationStatus.DISCARDED)

    def test_streaming_to_failed(self) -> None:

        """STREAMING → FAILED (generation error)."""
        assert_transition(VariationStatus.STREAMING, VariationStatus.FAILED)

    def test_streaming_to_expired(self) -> None:

        """STREAMING → EXPIRED (TTL cleanup during generation)."""
        assert_transition(VariationStatus.STREAMING, VariationStatus.EXPIRED)

    def test_ready_to_committed(self) -> None:

        """READY → COMMITTED (commit accepted phrases)."""
        assert_transition(VariationStatus.READY, VariationStatus.COMMITTED)

    def test_ready_to_discarded(self) -> None:

        """READY → DISCARDED (user discards after review)."""
        assert_transition(VariationStatus.READY, VariationStatus.DISCARDED)

    def test_ready_to_failed(self) -> None:

        """READY → FAILED (commit error)."""
        assert_transition(VariationStatus.READY, VariationStatus.FAILED)

    def test_ready_to_expired(self) -> None:

        """READY → EXPIRED (TTL after review)."""
        assert_transition(VariationStatus.READY, VariationStatus.EXPIRED)


# =============================================================================
# Invalid Transitions
# =============================================================================


class TestInvalidTransitions:
    """Test that invalid transitions raise InvalidTransitionError."""

    def test_created_to_committed_blocked(self) -> None:

        """Cannot commit from CREATED (must go through STREAMING → READY)."""
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(VariationStatus.CREATED, VariationStatus.COMMITTED)
        assert exc_info.value.from_state == VariationStatus.CREATED
        assert exc_info.value.to_state == VariationStatus.COMMITTED

    def test_created_to_ready_blocked(self) -> None:

        """Cannot skip STREAMING and go directly to READY."""
        with pytest.raises(InvalidTransitionError):
            assert_transition(VariationStatus.CREATED, VariationStatus.READY)

    def test_streaming_to_committed_blocked(self) -> None:

        """v1 does NOT support early-commit during STREAMING."""
        with pytest.raises(InvalidTransitionError):
            assert_transition(VariationStatus.STREAMING, VariationStatus.COMMITTED)

    def test_streaming_to_created_blocked(self) -> None:

        """Cannot go backwards to CREATED."""
        with pytest.raises(InvalidTransitionError):
            assert_transition(VariationStatus.STREAMING, VariationStatus.CREATED)

    def test_ready_to_streaming_blocked(self) -> None:

        """Cannot go backwards to STREAMING."""
        with pytest.raises(InvalidTransitionError):
            assert_transition(VariationStatus.READY, VariationStatus.STREAMING)

    def test_ready_to_created_blocked(self) -> None:

        """Cannot go backwards to CREATED."""
        with pytest.raises(InvalidTransitionError):
            assert_transition(VariationStatus.READY, VariationStatus.CREATED)

    @pytest.mark.parametrize("terminal_state", list(TERMINAL_STATES))
    def test_terminal_states_have_no_transitions(self, terminal_state: Any) -> None:

        """Terminal states cannot transition to anything."""
        for target in VariationStatus:
            if target == terminal_state:
                continue
            with pytest.raises(InvalidTransitionError):
                assert_transition(terminal_state, target)

    def test_self_transition_blocked(self) -> None:

        """Cannot transition to the same state."""
        for status in VariationStatus:
            with pytest.raises(InvalidTransitionError):
                assert_transition(status, status)


# =============================================================================
# Terminal State Checks
# =============================================================================


class TestTerminalStates:
    """Test terminal state detection."""

    def test_committed_is_terminal(self) -> None:

        assert is_terminal(VariationStatus.COMMITTED) is True

    def test_discarded_is_terminal(self) -> None:

        assert is_terminal(VariationStatus.DISCARDED) is True

    def test_failed_is_terminal(self) -> None:

        assert is_terminal(VariationStatus.FAILED) is True

    def test_expired_is_terminal(self) -> None:

        assert is_terminal(VariationStatus.EXPIRED) is True

    def test_created_is_not_terminal(self) -> None:

        assert is_terminal(VariationStatus.CREATED) is False

    def test_streaming_is_not_terminal(self) -> None:

        assert is_terminal(VariationStatus.STREAMING) is False

    def test_ready_is_not_terminal(self) -> None:

        assert is_terminal(VariationStatus.READY) is False


# =============================================================================
# Helper Predicates
# =============================================================================


class TestPredicates:
    """Test helper predicate functions."""

    def test_can_commit_only_from_ready(self) -> None:

        """Commit is only allowed from READY."""
        assert can_commit(VariationStatus.READY) is True
        assert can_commit(VariationStatus.CREATED) is False
        assert can_commit(VariationStatus.STREAMING) is False
        assert can_commit(VariationStatus.COMMITTED) is False
        assert can_commit(VariationStatus.DISCARDED) is False

    def test_can_discard_from_non_terminal(self) -> None:

        """Discard is allowed from CREATED, STREAMING, READY."""
        assert can_discard(VariationStatus.CREATED) is True
        assert can_discard(VariationStatus.STREAMING) is True
        assert can_discard(VariationStatus.READY) is True
        assert can_discard(VariationStatus.COMMITTED) is False
        assert can_discard(VariationStatus.DISCARDED) is False
        assert can_discard(VariationStatus.FAILED) is False


# =============================================================================
# Full Lifecycle Tests
# =============================================================================


class TestLifecycle:
    """Test complete lifecycle paths through the state machine."""

    def test_happy_path(self) -> None:

        """CREATED → STREAMING → READY → COMMITTED."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)
        assert_transition(VariationStatus.STREAMING, VariationStatus.READY)
        assert_transition(VariationStatus.READY, VariationStatus.COMMITTED)

    def test_discard_during_streaming(self) -> None:

        """CREATED → STREAMING → DISCARDED."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)
        assert_transition(VariationStatus.STREAMING, VariationStatus.DISCARDED)

    def test_discard_after_ready(self) -> None:

        """CREATED → STREAMING → READY → DISCARDED."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)
        assert_transition(VariationStatus.STREAMING, VariationStatus.READY)
        assert_transition(VariationStatus.READY, VariationStatus.DISCARDED)

    def test_failure_during_generation(self) -> None:

        """CREATED → STREAMING → FAILED."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)
        assert_transition(VariationStatus.STREAMING, VariationStatus.FAILED)

    def test_failure_during_commit(self) -> None:

        """CREATED → STREAMING → READY → FAILED."""
        assert_transition(VariationStatus.CREATED, VariationStatus.STREAMING)
        assert_transition(VariationStatus.STREAMING, VariationStatus.READY)
        assert_transition(VariationStatus.READY, VariationStatus.FAILED)

    def test_immediate_discard(self) -> None:

        """CREATED → DISCARDED (discard before generation starts)."""
        assert_transition(VariationStatus.CREATED, VariationStatus.DISCARDED)


# =============================================================================
# Error Message Tests
# =============================================================================


class TestErrorMessages:
    """Test error message formatting."""

    def test_error_contains_states(self) -> None:

        """Error message should contain both state names."""
        try:
            assert_transition(VariationStatus.CREATED, VariationStatus.COMMITTED)
        except InvalidTransitionError as e:
            assert "created" in str(e)
            assert "committed" in str(e)
            assert e.from_state == VariationStatus.CREATED
            assert e.to_state == VariationStatus.COMMITTED
