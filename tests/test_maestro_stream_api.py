"""Tests for maestro streaming API endpoint (app/api/routes/maestro.py).

Covers:
- POST /api/v1/maestro/stream (SSE streaming, budget checks, conversation history)
- POST /api/v1/maestro/preview (plan preview)
- GET /api/v1/validate-token (token validation)
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.contracts.llm_types import ChatMessage
from maestro.contracts.pydantic_types import wrap_dict
from maestro.core.maestro_handlers import UsageTracker
from maestro.db.models import User
from maestro.protocol.emitter import ProtocolSerializationError, emit, parse_event
from maestro.protocol.events import (
    CompleteEvent,
    ErrorEvent,
    MaestroEvent,
    StateEvent,
    ToolCallEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_sse_events(body: str) -> list[MaestroEvent]:
    """Parse SSE event stream body into typed MaestroEvent instances."""
    events: list[MaestroEvent] = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                events.append(parse_event(data))
            except (json.JSONDecodeError, ProtocolSerializationError):
                pass
    return events


def _make_maestro_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"prompt": "make a beat", "mode": "generate"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Token validation endpoint
# ---------------------------------------------------------------------------


class TestValidateToken:
    """GET /api/v1/validate-token"""

    @pytest.mark.anyio
    async def test_validate_token_returns_valid(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.get("/api/v1/validate-token", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert "expiresAt" in data
        assert "expiresInSeconds" in data
        assert "budgetRemaining" in data

    @pytest.mark.anyio
    async def test_validate_token_no_auth_401(self, client: AsyncClient, db_session: AsyncSession) -> None:

        resp = await client.get("/api/v1/validate-token")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Compose stream endpoint
# ---------------------------------------------------------------------------


class TestComposeStreamEndpoint:
    """POST /api/v1/maestro/stream"""

    @pytest.mark.anyio
    async def test_maestro_stream_returns_sse_content_type(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """Stream endpoint returns text/event-stream with expected headers."""
        async def fake_orchestrate(*args: object, **kwargs: object) -> AsyncGenerator[str, None]:

            yield emit(StateEvent(state="composing", intent="compose", confidence=0.9, trace_id="t-0"))
            yield emit(CompleteEvent(success=True, trace_id="t-0"))

        with patch("maestro.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_maestro_stream_yields_state_and_complete(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """Happy-path: orchestrate yields SSE events that are forwarded."""
        async def fake_orchestrate(*args: object, **kwargs: object) -> AsyncGenerator[str, None]:

            yield emit(StateEvent(state="editing", intent="track.add", confidence=0.9, trace_id="t-1"))
            yield emit(ToolCallEvent(id="tc-1", name="stori_set_tempo", params=wrap_dict({"tempo": 120})))
            yield emit(CompleteEvent(success=True, trace_id="t-1", tool_calls=[]))

        with patch("maestro.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        events = parse_sse_events(resp.text)
        types = [e.type for e in events]
        assert "state" in types
        assert "toolCall" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_maestro_stream_budget_insufficient_402(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User, db_session: AsyncSession) -> None:

        """When budget is insufficient, return 402."""
        from maestro.services.budget import InsufficientBudgetError

        with patch("maestro.api.routes.maestro.check_budget", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = InsufficientBudgetError(0, 100)
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 402
        data = resp.json()
        assert "Insufficient budget" in data["detail"]["message"]

    @pytest.mark.anyio
    async def test_maestro_stream_budget_deduction(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User, db_session: AsyncSession) -> None:

        """Budget deduction runs after successful streaming; no budgetUpdate SSE event is emitted."""
        mock_deduct = AsyncMock(return_value=(test_user, MagicMock()))

        async def fake_orchestrate(
            prompt: str,
            project_context: object = None,
            model: str | None = None,
            usage_tracker: UsageTracker | None = None,
            **kwargs: object,
        ) -> AsyncGenerator[str, None]:

            if usage_tracker:
                usage_tracker.add(100, 50)
            yield emit(StateEvent(state="editing", intent="track.add", confidence=0.9, trace_id="t-1"))
            yield emit(CompleteEvent(success=True, trace_id="t-1"))

        with (
            patch("maestro.api.routes.maestro.orchestrate", side_effect=fake_orchestrate),
            patch("maestro.api.routes.maestro.deduct_budget", mock_deduct),
        ):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        # Deduction must have run
        mock_deduct.assert_called_once()
        # No budgetUpdate event is emitted — frontend polls /budget/status instead
        events = parse_sse_events(resp.text)
        assert not any(e.type == "budgetUpdate" for e in events)
        # No X-Budget-Remaining header
        assert "x-budget-remaining" not in {k.lower() for k in resp.headers}

    @pytest.mark.anyio
    async def test_maestro_stream_error_yields_error_event(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """When orchestration raises, the stream yields an error event."""
        async def failing_orchestrate(*args: object, **kwargs: object) -> AsyncGenerator[str, None]:

            yield emit(StateEvent(state="editing", intent="track.add", confidence=0.9, trace_id="t-1"))
            raise RuntimeError("backend exploded")

        with patch("maestro.api.routes.maestro.orchestrate", side_effect=failing_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        events = parse_sse_events(resp.text)
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) >= 1
        assert "backend exploded" in error_events[0].message

    @pytest.mark.anyio
    async def test_maestro_stream_loads_conversation_history(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User, db_session: AsyncSession) -> None:

        """When conversation_id is provided, loads history from DB."""
        from maestro.db.models import Conversation, ConversationMessage

        conv_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        conv = Conversation(
            id=conv_id,
            user_id=test_user.id,
            title="Test conv",
        )
        db_session.add(conv)
        await db_session.flush()

        msg = ConversationMessage(
            conversation_id=conv_id,
            role="user",
            content="previous prompt",
        )
        db_session.add(msg)
        await db_session.commit()

        captured_history: list[ChatMessage] = []

        async def spy_orchestrate(
            prompt: str,
            project_context: object = None,
            model: str | None = None,
            usage_tracker: UsageTracker | None = None,
            conversation_id: str | None = None,
            user_id: str | None = None,
            conversation_history: list[ChatMessage] | None = None,
            **kwargs: object,
        ) -> AsyncGenerator[str, None]:

            captured_history.clear()
            captured_history.extend(conversation_history or [])
            yield emit(StateEvent(state="editing", intent="track.add", confidence=0.9, trace_id="t-1"))
            yield emit(CompleteEvent(success=True, trace_id="t-1"))

        with patch("maestro.api.routes.maestro.orchestrate", side_effect=spy_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(conversation_id=conv_id),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert len(captured_history) >= 1
        assert captured_history[0]["content"] == "previous prompt"

    @pytest.mark.anyio
    async def test_maestro_stream_no_auth_401(self, client: AsyncClient, db_session: AsyncSession) -> None:

        resp = await client.post("/api/v1/maestro/stream", json=_make_maestro_body())
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_maestro_stream_no_budget_header(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """X-Budget-Remaining header is not emitted; frontend polls /budget/status instead."""
        async def fake_orchestrate(*args: object, **kwargs: object) -> AsyncGenerator[str, None]:

            yield emit(StateEvent(state="editing", intent="track.add", confidence=0.9, trace_id="t-1"))
            yield emit(CompleteEvent(success=True, trace_id="t-1"))

        with patch("maestro.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "x-budget-remaining" not in resp.headers


# ---------------------------------------------------------------------------
# Compose preview endpoint
# ---------------------------------------------------------------------------


class TestComposePreviewEndpoint:
    """POST /api/v1/maestro/preview"""

    @pytest.mark.anyio
    async def test_preview_composing_returns_plan(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """COMPOSING intent returns preview_available=True with plan."""
        from maestro.core.intent import IntentResult, Intent, Slots, SSEState

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )

        with (
            patch("maestro.api.routes.maestro.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("maestro.api.routes.maestro.preview_plan", new_callable=AsyncMock, return_value={"steps": []}),
            patch("maestro.api.routes.maestro.LLMClient") as mock_cls,
        ):
            mock_llm = MagicMock()
            mock_llm.close = AsyncMock()
            mock_cls.return_value = mock_llm

            resp = await client.post(
                "/api/v1/maestro/preview",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["previewAvailable"] is True
        assert data["sseState"] == "composing"

    @pytest.mark.anyio
    async def test_preview_non_composing_returns_unavailable(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """Non-COMPOSING intent returns preview_available=False."""
        from maestro.core.intent import IntentResult, Intent, Slots, SSEState

        fake_route = IntentResult(
            intent=Intent.UNKNOWN,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

        with (
            patch("maestro.api.routes.maestro.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("maestro.api.routes.maestro.LLMClient") as mock_cls,
        ):
            mock_llm = MagicMock()
            mock_llm.close = AsyncMock()
            mock_cls.return_value = mock_llm

            resp = await client.post(
                "/api/v1/maestro/preview",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["previewAvailable"] is False
