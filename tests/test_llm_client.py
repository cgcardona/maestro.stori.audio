"""
Tests for app.core.llm_client: LLMResponse, enforce_single_tool, LLMClient (mocked).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.llm_client import (
    LLMResponse,
    ToolCallData,
    enforce_single_tool,
    LLMClient,
    LLMProvider,
)


class TestLLMResponse:
    def test_has_tool_calls_false_when_empty(self):
        r = LLMResponse(content="Hi")
        assert r.has_tool_calls is False

    def test_has_tool_calls_true_when_any(self):
        r = LLMResponse(tool_calls=[ToolCallData("stori_play", {}, "id1")])
        assert r.has_tool_calls is True


class TestEnforceSingleTool:
    def test_returns_unchanged_when_zero_or_one(self):
        r = LLMResponse(content="x")
        assert enforce_single_tool(r) is r
        r1 = LLMResponse(tool_calls=[ToolCallData("a", {}, "1")])
        assert enforce_single_tool(r1) is r1
        assert len(r1.tool_calls) == 1

    def test_keeps_only_first_when_multiple(self):
        r = LLMResponse(
            tool_calls=[
                ToolCallData("first", {}, "1"),
                ToolCallData("second", {}, "2"),
            ]
        )
        out = enforce_single_tool(r)
        assert out is r
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "first"


class TestLLMClient:
    @patch("app.core.llm_client.settings")
    def test_init_uses_settings(self, mock_settings):
        mock_settings.llm_provider = "openrouter"
        mock_settings.llm_model = "anthropic/claude-3.7-sonnet"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.llm_timeout = 60
        client = LLMClient()
        assert client.model == "anthropic/claude-3.7-sonnet"
        assert client.api_key == "sk-test"
        assert client.base_url == "https://openrouter.ai/api"

    def test_supports_reasoning_true_for_reasoning_model(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="x",
            model="anthropic/claude-3.7-sonnet",
        )
        assert client.supports_reasoning() is True
        client.model = "openai/o1"
        assert client.supports_reasoning() is True

    def test_supports_reasoning_false_for_other(self):
        client = LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="x",
            model="anthropic/claude-2.0",
        )
        assert client.supports_reasoning() is False

    @patch("app.core.llm_client.settings")
    def test_get_base_url_openrouter(self, mock_settings):
        mock_settings.llm_provider = "openrouter"
        mock_settings.openrouter_api_key = "k"
        mock_settings.llm_model = "m"
        mock_settings.llm_timeout = 60
        client = LLMClient()
        assert client._get_base_url() == "https://openrouter.ai/api"

    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            client = LLMClient()
            client._client = AsyncMock()
            await client.close()
            assert client._client is None

    @pytest.mark.asyncio
    async def test_chat_completion_returns_parsed_response(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            mock_settings.llm_temperature = 0.1
            mock_settings.llm_max_tokens = 4096
            client = LLMClient()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "Hello", "role": "assistant"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            }
            mock_resp.raise_for_status = MagicMock()
            client._client = MagicMock()
            client._client.post = AsyncMock(return_value=mock_resp)
            out = await client.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
            )
            assert out.content == "Hello"
            assert out.usage["prompt_tokens"] == 1

    @pytest.mark.asyncio
    async def test_chat_builds_messages_and_calls_completion(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            mock_settings.llm_temperature = 0.1
            mock_settings.llm_max_tokens = 4096
            client = LLMClient()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "OK", "role": "assistant"}}],
                "usage": {},
            }
            mock_resp.raise_for_status = MagicMock()
            client._client = MagicMock()
            client._client.post = AsyncMock(return_value=mock_resp)
            out = await client.chat(
                system="You are helpful.",
                user="Hello",
                tools=[],
                tool_choice="none",
                context={},
            )
            assert out.content == "OK"
            call_args = client._client.post.call_args
            payload = call_args[1]["json"]
            assert payload["messages"][0]["content"] == "You are helpful."
            assert payload["messages"][-1]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_chat_includes_project_state_in_context(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            mock_settings.llm_temperature = 0.1
            mock_settings.llm_max_tokens = 4096
            client = LLMClient()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "OK", "role": "assistant"}}],
                "usage": {},
            }
            mock_resp.raise_for_status = MagicMock()
            client._client = MagicMock()
            client._client.post = AsyncMock(return_value=mock_resp)
            await client.chat(
                system="Sys",
                user="User",
                tools=[],
                tool_choice="none",
                context={"project_state": {"tempo": 120}},
            )
            payload = client._client.post.call_args[1]["json"]
            assert any("tempo" in str(m.get("content", "")) for m in payload["messages"])


class TestEnablePromptCaching:
    def test_returns_unchanged_for_non_claude(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "openai/gpt-4"
            mock_settings.llm_timeout = 60
            client = LLMClient()
            msgs = [{"role": "user", "content": "x"}]
            out_msgs, out_tools = client._enable_prompt_caching(msgs, None)
            assert out_msgs == msgs
            assert out_tools is None

    def test_disables_caching_when_tools_present(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "anthropic/claude-3.7-sonnet"
            mock_settings.llm_timeout = 60
            client = LLMClient()
            msgs = [{"role": "system", "content": "Sys"}]
            tools = [{"function": {"name": "x"}}]
            out_msgs, out_tools = client._enable_prompt_caching(msgs, tools)
            assert out_tools == tools
            assert out_msgs == msgs


class TestParseResponse:
    """_parse_response with tool_calls and edge cases."""

    def test_parse_response_tool_calls(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            client = LLMClient()
            data = {
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "stori_play", "arguments": "{}"},
                            },
                            {
                                "id": "call_2",
                                "function": {"name": "stori_set_tempo", "arguments": '{"tempo": 120}'},
                            },
                        ],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
            out = client._parse_response(data)
            assert out.content is None
            assert len(out.tool_calls) == 2
            assert out.tool_calls[0].name == "stori_play"
            assert out.tool_calls[0].arguments == {}
            assert out.tool_calls[1].name == "stori_set_tempo"
            assert out.tool_calls[1].arguments == {"tempo": 120}
            assert out.usage == data["usage"]

    def test_parse_response_tool_call_invalid_json_skipped(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            client = LLMClient()
            data = {
                "choices": [{
                    "message": {
                        "content": "OK",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "stori_play", "arguments": "not json"}},
                        ],
                    },
                    "finish_reason": "stop",
                }],
                "usage": {},
            }
            out = client._parse_response(data)
            assert out.content == "OK"
            assert len(out.tool_calls) == 0


class TestChatCompletionRetry:
    """chat_completion retries on 429/503."""

    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "m"
            mock_settings.llm_timeout = 60
            mock_settings.llm_temperature = 0.1
            mock_settings.llm_max_tokens = 4096
            client = LLMClient()
            import httpx
            fail_resp = MagicMock()
            fail_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("rate limit", request=MagicMock(), response=MagicMock(status_code=429)))
            ok_resp = MagicMock()
            ok_resp.json.return_value = {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {},
            }
            ok_resp.raise_for_status = MagicMock()
            client._client = MagicMock()
            client._client.post = AsyncMock(side_effect=[fail_resp, ok_resp])
            with patch("asyncio.sleep", AsyncMock()):
                out = await client.chat_completion(messages=[{"role": "user", "content": "Hi"}], max_retries=2)
            assert out.content == "OK"
            assert client._client.post.call_count == 2


class TestChatCompletionStream:
    """chat_completion_stream yields reasoning and content deltas."""

    @pytest.mark.asyncio
    async def test_stream_yields_done_with_content(self):
        class FakeStream:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass
            async def aiter_lines(self):
                yield "data: " + __import__("json").dumps({"choices": [{"delta": {"content": "Hi"}}]})
                yield "data: " + __import__("json").dumps({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {}})
                yield "data: [DONE]"

        def make_fake_stream(*args, **kwargs):
            return FakeStream()

        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.llm_provider = "openrouter"
            mock_settings.openrouter_api_key = "k"
            mock_settings.llm_model = "anthropic/claude-3.7-sonnet"
            mock_settings.llm_timeout = 60
            mock_settings.llm_temperature = 0.1
            mock_settings.llm_max_tokens = 4096
            client = LLMClient()
            client._client = MagicMock()
            client._client.stream = MagicMock(side_effect=make_fake_stream)
            chunks = []
            async for c in client.chat_completion_stream(messages=[{"role": "user", "content": "Hi"}]):
                chunks.append(c)
            assert any(ch.get("type") == "content_delta" and ch.get("text") == "Hi" for ch in chunks)
            assert any(ch.get("type") == "done" for ch in chunks)
