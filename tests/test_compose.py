"""Tests for the Cursor-of-DAWs execution layer.

Tests the ExecutionContext dataclass and its properties.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from maestro.core.executor import ExecutionContext
from maestro.core.state_store import StateStore, Transaction
from maestro.core.tracing import TraceContext


class TestExecutionContext:
    """Test ExecutionContext dataclass."""

    def test_empty_context(self) -> None:

        """New context should have empty results."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        assert ctx.results == []

    def test_add_result(self) -> None:

        """Adding a result should append to results."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        ctx.add_result("stori_play", success=True, output={"ok": True})
        assert len(ctx.results) == 1
        assert ctx.results[0].tool_name == "stori_play"


class TestExecutionContextProperties:
    """Test ExecutionContext computed properties."""

    def test_all_successful_true(self) -> None:

        """all_successful should be True when all results succeed."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        ctx.add_result("stori_play", success=True, output={})
        ctx.add_result("stori_stop", success=True, output={})
        assert ctx.all_successful is True

    def test_all_successful_false(self) -> None:

        """all_successful should be False when any result fails."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        ctx.add_result("stori_play", success=True, output={})
        ctx.add_result("stori_stop", success=False, output={}, error="fail")
        assert ctx.all_successful is False
        assert "stori_stop" in ctx.failed_tools
