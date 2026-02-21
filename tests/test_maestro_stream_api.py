"""Tests for maestro streaming API endpoint (app/api/routes/maestro.py).

Covers:
- POST /api/v1/maestro/stream (SSE streaming, budget checks, conversation history)
- POST /api/v1/maestro/preview (plan preview)
- GET /api/v1/validate-token (token validation)
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_sse_events(body: str) -> list[dict]:
    """Parse SSE event stream body into list of dicts."""
    events = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _make_maestro_body(**overrides) -> dict:
    base = {"prompt": "make a beat", "mode": "generate"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Token validation endpoint
# ---------------------------------------------------------------------------


class TestValidateToken:
    """GET /api/v1/validate-token"""

    @pytest.mark.anyio
    async def test_validate_token_returns_valid(self, client, auth_headers, test_user):
        resp = await client.get("/api/v1/validate-token", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert "expiresAt" in data
        assert "expiresInSeconds" in data
        assert "budgetRemaining" in data

    @pytest.mark.anyio
    async def test_validate_token_no_auth_401(self, client, db_session):
        resp = await client.get("/api/v1/validate-token")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Compose stream endpoint
# ---------------------------------------------------------------------------


class TestComposeStreamEndpoint:
    """POST /api/v1/maestro/stream"""

    @pytest.mark.anyio
    async def test_maestro_stream_returns_sse_content_type(self, client, auth_headers, test_user):
        """Stream endpoint returns text/event-stream with expected headers."""
        async def fake_orchestrate(*args, **kwargs):
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "state", "state": "composing"})
            yield await sse_event({"type": "complete", "success": True, "toolCalls": []})

        with patch("app.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_maestro_stream_yields_state_and_complete(self, client, auth_headers, test_user):
        """Happy-path: orchestrate yields SSE events that are forwarded."""
        async def fake_orchestrate(*args, **kwargs):
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "state", "state": "editing"})
            yield await sse_event({"type": "toolCall", "name": "stori_set_tempo", "params": {"tempo": 120}})
            yield await sse_event({"type": "complete", "success": True, "toolCalls": []})

        with patch("app.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        events = parse_sse_events(resp.text)
        types = [e["type"] for e in events]
        assert "state" in types
        assert "toolCall" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_maestro_stream_budget_insufficient_402(self, client, auth_headers, test_user, db_session):
        """When budget is insufficient, return 402."""
        from app.services.budget import InsufficientBudgetError

        with patch("app.api.routes.maestro.check_budget", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = InsufficientBudgetError(0, 100)
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        assert resp.status_code == 402
        data = resp.json()
        assert "Insufficient budget" in data["detail"]["error"]

    @pytest.mark.anyio
    async def test_maestro_stream_budget_deduction(self, client, auth_headers, test_user, db_session):
        """Budget deduction runs after successful streaming; no budgetUpdate SSE event is emitted."""
        mock_deduct = AsyncMock(return_value=(test_user, MagicMock()))

        async def fake_orchestrate(*args, **kwargs):
            usage_tracker = kwargs.get("usage_tracker")
            if usage_tracker:
                usage_tracker.add(100, 50)
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "complete", "success": True})

        with (
            patch("app.api.routes.maestro.orchestrate", side_effect=fake_orchestrate),
            patch("app.api.routes.maestro.deduct_budget", mock_deduct),
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
        assert not any(e.get("type") == "budgetUpdate" for e in events)
        # No X-Budget-Remaining header
        assert "x-budget-remaining" not in {k.lower() for k in resp.headers}

    @pytest.mark.anyio
    async def test_maestro_stream_error_yields_error_event(self, client, auth_headers, test_user):
        """When orchestration raises, the stream yields an error event."""
        async def failing_orchestrate(*args, **kwargs):
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "state", "state": "editing"})
            raise RuntimeError("backend exploded")

        with patch("app.api.routes.maestro.orchestrate", side_effect=failing_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(),
                headers=auth_headers,
            )
        events = parse_sse_events(resp.text)
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "backend exploded" in error_events[0].get("message", "")

    @pytest.mark.anyio
    async def test_maestro_stream_loads_conversation_history(self, client, auth_headers, test_user, db_session):
        """When conversation_id is provided, loads history from DB."""
        from app.db.models import Conversation, ConversationMessage

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

        captured_history = {}

        async def spy_orchestrate(*args, **kwargs):
            captured_history["history"] = kwargs.get("conversation_history", [])
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "complete", "success": True})

        with patch("app.api.routes.maestro.orchestrate", side_effect=spy_orchestrate):
            resp = await client.post(
                "/api/v1/maestro/stream",
                json=_make_maestro_body(conversation_id=conv_id),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert len(captured_history.get("history", [])) >= 1
        assert captured_history["history"][0]["content"] == "previous prompt"

    @pytest.mark.anyio
    async def test_maestro_stream_no_auth_401(self, client, db_session):
        resp = await client.post("/api/v1/maestro/stream", json=_make_maestro_body())
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_maestro_stream_no_budget_header(self, client, auth_headers, test_user):
        """X-Budget-Remaining header is not emitted; frontend polls /budget/status instead."""
        async def fake_orchestrate(*args, **kwargs):
            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "complete", "success": True})

        with patch("app.api.routes.maestro.orchestrate", side_effect=fake_orchestrate):
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
    async def test_preview_composing_returns_plan(self, client, auth_headers, test_user):
        """COMPOSING intent returns preview_available=True with plan."""
        from app.core.intent import IntentResult, Intent, Slots, SSEState

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
            patch("app.api.routes.maestro.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.api.routes.maestro.preview_plan", new_callable=AsyncMock, return_value={"steps": []}),
            patch("app.api.routes.maestro.LLMClient") as mock_cls,
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
    async def test_preview_non_composing_returns_unavailable(self, client, auth_headers, test_user):
        """Non-COMPOSING intent returns preview_available=False."""
        from app.core.intent import IntentResult, Intent, Slots, SSEState

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
            patch("app.api.routes.maestro.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.api.routes.maestro.LLMClient") as mock_cls,
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
