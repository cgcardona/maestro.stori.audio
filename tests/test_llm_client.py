"""Tests for the LLM client (app/core/llm_client.py).

Covers: LLMClient init, chat, chat_completion, chat_completion_stream,
_parse_response, _enable_prompt_caching, enforce_single_tool.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.expansion import ToolCall
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

    def test_zero_tool_calls(self):
        resp = LLMResponse(content="Hello", tool_calls=[])
        result = enforce_single_tool(resp)
        assert len(result.tool_calls) == 0

    def test_one_tool_call(self):
        resp = LLMResponse(tool_calls=[ToolCall(name="foo", params={})])
        result = enforce_single_tool(resp)
        assert len(result.tool_calls) == 1

    def test_multiple_tool_calls_truncated(self):
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

    def test_has_tool_calls_true(self):
        resp = LLMResponse(tool_calls=[ToolCall(name="foo", params={})])
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self):
        resp = LLMResponse(content="text only")
        assert resp.has_tool_calls is False


# ---------------------------------------------------------------------------
# LLMClient construction
# ---------------------------------------------------------------------------


class TestLLMClientInit:

    @patch("app.core.llm_client.settings")
    def test_default_init(self, mock_settings):
        mock_settings.llm_provider = "openrouter"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.llm_model = "anthropic/claude-3.7-sonnet"
        mock_settings.llm_timeout = 60
        client = LLMClient()
        assert client.provider == "openrouter"
        assert client.api_key == "sk-test"
        assert client.model == "anthropic/claude-3.7-sonnet"

    def test_custom_init(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="custom-key",
            model="openai/o1-mini",
            timeout=30,
        )
        assert client.api_key == "custom-key"
        assert client.model == "openai/o1-mini"
        assert client.timeout == 30

    def test_supports_reasoning(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="k",
            model="anthropic/claude-3.7-sonnet",
        )
        assert client.supports_reasoning() is True

        client2 = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="k",
            model="gpt-4o",
        )
        assert client2.supports_reasoning() is False

    def test_base_url_openrouter(self):
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

    def test_parse_content_only(self):
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

    def test_parse_with_tool_calls(self):
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

    def test_parse_empty_response(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="test"
        )
        data: dict[str, object] = {"choices": [{}]}
        result = client._parse_response(data)
        assert result.content is None
        assert len(result.tool_calls) == 0

    def test_parse_malformed_tool_call(self):
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

    def test_non_claude_model_unchanged(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k", model="openai/gpt-4o"
        )
        messages = [{"role": "system", "content": "You are helpful"}]
        cached_msgs, cached_tools = client._enable_prompt_caching(messages, tools=None)
        assert cached_msgs == messages

    def test_claude_with_tools_disabled(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k",
            model="anthropic/claude-3.7-sonnet",
        )
        messages = [{"role": "system", "content": "sys"}]
        tools = [{"type": "function", "function": {"name": "test"}}]
        cached_msgs, cached_tools = client._enable_prompt_caching(messages, tools=tools)
        # Should not modify messages when tools present
        assert cached_msgs == messages
        assert cached_tools == tools

    def test_claude_pure_chat_caching_enabled(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k",
            model="anthropic/claude-3.7-sonnet",
        )
        messages = [
            {"role": "system", "content": "You are a music AI"},
            {"role": "user", "content": "Hello"},
        ]
        cached_msgs, cached_tools = client._enable_prompt_caching(messages, tools=None)
        # System message should have cache_control
        assert cached_msgs[0]["content"][0]["cache_control"]["type"] == "ephemeral"
        assert cached_tools is None

    def test_claude_with_tool_messages_disabled(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER, api_key="k",
            model="anthropic/claude-3.7-sonnet",
        )
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "content": "result"},
        ]
        cached_msgs, cached_tools = client._enable_prompt_caching(messages, tools=None)
        # Should NOT enable caching because tool messages are present
        assert cached_msgs == messages


# ---------------------------------------------------------------------------
# chat_completion (mocked HTTP)
# ---------------------------------------------------------------------------


class TestChatCompletion:

    @pytest.mark.anyio
    async def test_successful_completion(self):
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
    async def test_retry_on_500(self):
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
    async def test_chat_with_context(self):
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
            user="Set tempo to 120",
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
    async def test_returns_client(self, mock_settings):
        mock_settings.llm_provider = "openrouter"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.llm_model = "test"
        mock_settings.llm_timeout = 30
        client = await get_llm_client()
        assert isinstance(client, LLMClient)
        await client.close()
