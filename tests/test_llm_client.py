"""Tests for the LLM client (app/core/llm_client.py).

Covers: LLMClient init, chat, chat_completion, chat_completion_stream,
_parse_response, _enable_prompt_caching, enforce_single_tool.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.expansion import ToolCall
from app.contracts.llm_types import ChatMessage
from app.core.llm_client import (
    LLMClient,
    LLMProvider,
    LLMResponse,
    enforce_single_tool,
    get_llm_client,
)


# ---------------------------------------------------------------------------
# enforce_single_tool
# ---------------------------------------------------------------------------


class TestEnforceSingleTool:

    def test_zero_tool_calls(self) -> None:

        resp = LLMResponse(content="Hello", tool_calls=[])
        result = enforce_single_tool(resp)
        assert len(result.tool_calls) == 0

    def test_one_tool_call(self) -> None:

        resp = LLMResponse(tool_calls=[ToolCall(name="foo", params={})])
        result = enforce_single_tool(resp)
        assert len(result.tool_calls) == 1

    def test_multiple_tool_calls_truncated(self) -> None:

        calls = [
            ToolCall(name="first", params={}),
            ToolCall(name="second", params={}),
            ToolCall(name="third", params={}),
        ]
        resp = LLMResponse(tool_calls=calls)
        result = enforce_single_tool(resp)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "first"


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------


class TestLLMResponse:

    def test_has_tool_calls_true(self) -> None:

        resp = LLMResponse(tool_calls=[ToolCall(name="foo", params={})])
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self) -> None:

        resp = LLMResponse(content="text only")
        assert resp.has_tool_calls is False


# ---------------------------------------------------------------------------
# LLMClient construction
# ---------------------------------------------------------------------------


class TestLLMClientInit:

    @patch("app.core.llm_client.settings")
    def test_default_init(self, mock_settings: MagicMock) -> None:

        mock_settings.llm_provider = "openrouter"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.llm_timeout = 60
        client = LLMClient()
        assert client.provider == "openrouter"
        assert client.api_key == "sk-test"
        assert client.model == "anthropic/claude-sonnet-4.6"

    def test_custom_init(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="custom-key",
            model="openai/o1-mini",
            timeout=30,
        )
        assert client.api_key == "custom-key"
        assert client.model == "openai/o1-mini"
        assert client.timeout == 30

    def test_supports_reasoning(self) -> None:

        """Only SUPPORTED_MODELS (sonnet-4.6 and opus-4.6) support reasoning."""
        for model in ("anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6"):
            client = LLMClient(
                provider=LLMProvider.OPENROUTER,
                api_key="k",
                model=model,
            )
            assert client.supports_reasoning() is True, f"{model} should support reasoning"

        for model in ("gpt-4o", "anthropic/claude-3.7-sonnet", "openai/o1-mini"):
            client = LLMClient(
                provider=LLMProvider.OPENROUTER,
                api_key="k",
                model=model,
            )
            assert client.supports_reasoning() is False, f"{model} should not support reasoning"

    def test_base_url_openrouter(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="k",
            model="test",
        )
        assert "openrouter.ai" in client.base_url


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:

    def test_parse_content_only(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test"
        )
        data = {
            "choices": [{
                "message": {"content": "Hello world"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = client._parse_response(data)
        assert result.content == "Hello world"
        assert result.finish_reason == "stop"
        assert len(result.tool_calls) == 0

    def test_parse_with_tool_calls(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test"
        )
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "function": {
                            "name": "stori_set_tempo",
                            "arguments": '{"tempo": 120}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }
        result = client._parse_response(data)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "stori_set_tempo"
        assert result.tool_calls[0].params == {"tempo": 120}
        assert result.tool_calls[0].id == "call_abc"

    def test_parse_empty_response(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test"
        )
        data: dict[str, object] = {"choices": [{}]}
        result = client._parse_response(data)
        assert result.content is None
        assert len(result.tool_calls) == 0

    def test_parse_malformed_tool_call(self) -> None:

        """Malformed tool calls should be skipped gracefully."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test"
        )
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {"name": "bad", "arguments": "not-json{"},
                    }],
                },
            }],
        }
        result = client._parse_response(data)
        # Should handle gracefully (either parse or skip)
        assert isinstance(result, LLMResponse)


# ---------------------------------------------------------------------------
# _enable_prompt_caching
# ---------------------------------------------------------------------------


class TestPromptCaching:
    """Tests for _enable_prompt_caching.

    Strategy: tool-schema-only caching via cache_control on the last tool
    definition. OpenRouter does not forward a top-level system array (Anthropic-
    native format) to Anthropic, so system messages stay in the messages array
    unmodified. The function returns a 3-tuple (messages, cached_tools, None).
    """

    CLAUDE_MODEL = "anthropic/claude-sonnet-4.6"

    def test_returns_three_tuple(self) -> None:

        """_enable_prompt_caching always returns a 3-tuple."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        msgs: list[ChatMessage] = [{"role": "user", "content": "hi"}]
        result = client._enable_prompt_caching(msgs, tools=None)
        assert len(result) == 3

    def test_system_blocks_always_none(self) -> None:

        """Third element (system_blocks) is always None — no top-level system injection."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        messages: list[ChatMessage] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        _, _, system_blocks = client._enable_prompt_caching(messages, tools=None)
        assert system_blocks is None

    def test_non_claude_model_passthrough(self) -> None:

        """Non-caching models return messages and tools unchanged — no cache_control added."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="openai/gpt-4o"
        )
        messages: list[ChatMessage] = [{"role": "system", "content": "You are helpful"}]
        tools = [{"type": "function", "function": {"name": "t"}}]
        returned_msgs, returned_tools, system_blocks = client._enable_prompt_caching(
            messages, tools=tools
        )
        assert returned_msgs == messages
        # Tools are returned as-is (no cache_control) so the caller can still use them
        assert returned_tools == tools
        assert "cache_control" not in returned_tools[0]
        assert system_blocks is None

    def test_system_messages_unchanged_for_claude(self) -> None:

        """System messages are NOT modified — they stay in the messages array as-is.

        OpenRouter doesn't forward the Anthropic-native top-level system array,
        so we keep system content in messages where OR handles it correctly.
        """
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        messages: list[ChatMessage] = [
            {"role": "system", "content": "You are a music AI"},
            {"role": "user", "content": "Hello"},
        ]
        returned_msgs, _, _ = client._enable_prompt_caching(messages, tools=None)
        # System message is unchanged — no cache_control added, no wrapping
        assert returned_msgs[0] == {"role": "system", "content": "You are a music AI"}
        assert returned_msgs[1] == {"role": "user", "content": "Hello"}

    def test_multiple_system_messages_all_preserved(self) -> None:

        """Multiple role:system messages (base + project context + entity context) survive."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        messages: list[ChatMessage] = [
            {"role": "system", "content": "base instructions"},
            {"role": "system", "content": "project context"},
            {"role": "system", "content": "entity context"},
            {"role": "user", "content": "add a track"},
        ]
        returned_msgs, _, _ = client._enable_prompt_caching(messages, tools=None)
        assert returned_msgs == messages

    def test_last_tool_gets_cache_control(self) -> None:

        """cache_control is placed on the last tool definition only."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        tools = [
            {"type": "function", "function": {"name": "tool_a"}},
            {"type": "function", "function": {"name": "tool_b"}},
            {"type": "function", "function": {"name": "tool_c"}},
        ]
        user_msgs: list[ChatMessage] = [{"role": "user", "content": "hi"}]
        _, cached_tools, _ = client._enable_prompt_caching(user_msgs, tools=tools)
        assert cached_tools is not None
        assert "cache_control" not in cached_tools[0]
        assert "cache_control" not in cached_tools[1]
        assert cached_tools[2]["cache_control"] == {"type": "ephemeral"}

    def test_no_tools_returns_none_for_cached_tools(self) -> None:

        """When no tools are provided, cached_tools is None."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        user_msgs: list[ChatMessage] = [{"role": "user", "content": "hi"}]
        _, cached_tools, _ = client._enable_prompt_caching(user_msgs, tools=None)
        assert cached_tools is None

    def test_original_tools_not_mutated(self) -> None:

        """The original tools list is not mutated — caching returns copies."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        tools = [{"type": "function", "function": {"name": "tool_a"}}]
        user_msgs: list[ChatMessage] = [{"role": "user", "content": "hi"}]
        _, cached_tools, _ = client._enable_prompt_caching(user_msgs, tools=tools)
        # Cached tools have cache_control; originals do not
        assert "cache_control" not in tools[0]
        assert cached_tools is not None
        assert cached_tools[0]["cache_control"] == {"type": "ephemeral"}

    def test_conversation_history_preserved(self) -> None:

        """Tool result messages and conversation history pass through unchanged."""
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model=self.CLAUDE_MODEL
        )
        messages: list[ChatMessage] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do something"},
            {"role": "tool", "tool_call_id": "tc-1", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]
        returned_msgs, _, _ = client._enable_prompt_caching(messages, tools=None)
        assert returned_msgs == messages


# ---------------------------------------------------------------------------
# chat_completion (mocked HTTP)
# ---------------------------------------------------------------------------


class TestChatCompletion:

    @pytest.mark.anyio
    async def test_successful_completion(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test-model"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "42"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        client._client = mock_client

        result = await client.chat_completion(
            messages=[{"role": "user", "content": "answer"}],
        )
        assert result.content == "42"
        await client.close()

    @pytest.mark.anyio
    async def test_retry_on_500(self) -> None:

        import httpx
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test-model"
        )

        error_response = MagicMock()
        error_response.status_code = 500

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }

        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            httpx.HTTPStatusError("500", request=MagicMock(), response=error_response),
            ok_response,
        ]
        client._client = mock_client

        result = await client.chat_completion(
            messages=[{"role": "user", "content": "retry me"}],
            max_retries=2,
        )
        assert result.content == "ok"
        await client.close()


# ---------------------------------------------------------------------------
# chat (high-level)
# ---------------------------------------------------------------------------


class TestChat:

    @pytest.mark.anyio
    async def test_chat_with_context(self) -> None:

        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test-model"
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "response"}}],
            "usage": {},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        client._client = mock_client

        result = await client.chat(
            system="You are a music AI",
            user="set tempo to 120",
            tools=[{"type": "function", "function": {"name": "stori_set_tempo"}}],
            tool_choice="required",
            context={"project_state": {"tempo": 100}},
        )
        assert result.content == "response"
        await client.close()


# ---------------------------------------------------------------------------
# get_llm_client
# ---------------------------------------------------------------------------


class TestGetLLMClient:

    @pytest.mark.anyio
    @patch("app.core.llm_client.settings")
    async def test_returns_client(self, mock_settings: MagicMock) -> None:

        mock_settings.llm_provider = "openrouter"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.llm_model = "test"
        mock_settings.llm_timeout = 30
        client = await get_llm_client()
        assert isinstance(client, LLMClient)
        await client.close()
