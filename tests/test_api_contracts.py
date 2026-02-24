"""
API contract tests: status codes and response shape for every public route.

These tests ensure the API surface is stable and documented. Every endpoint
must return the expected status and response keys so clients and open source
contributors can rely on the contract.

Uses client and auth_headers from conftest (in-memory DB).
"""
import pytest
from unittest.mock import AsyncMock, patch


# =============================================================================
# Public routes (no auth)
# =============================================================================

class TestRootEndpoint:
    """GET / — service info."""

    @pytest.mark.anyio
    async def test_status_200(self, client):
        response = await client.get("/")
        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_response_has_required_keys(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert "version" in data
        assert "docs" in data


class TestHealthEndpoint:
    """GET /api/v1/health — basic liveness."""

    @pytest.mark.anyio
    async def test_status_200(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_response_has_required_keys(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        required = {"status", "service", "version", "tagline"}
        for key in required:
            assert key in data, f"Missing key: {key}"
        assert data["status"] == "ok"
        assert data["tagline"] == "the infinite music machine"


class TestHealthFullEndpoint:
    """GET /api/v1/health/full — dependencies (Orpheus, LLM, S3).

    Orpheus and S3 are mocked so these contract tests verify shape and status
    codes without making real network calls (which would hang in CI).
    """

    @pytest.mark.anyio
    async def test_status_200_or_503(self, client):
        with patch(
            "app.services.orpheus.OrpheusClient.health_check",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = await client.get("/api/v1/health/full")
        assert response.status_code in (200, 503)

    @pytest.mark.anyio
    async def test_response_has_status_and_dependencies(self, client):
        with patch(
            "app.services.orpheus.OrpheusClient.health_check",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = await client.get("/api/v1/health/full")
        data = response.json()
        assert "status" in data
        assert "dependencies" in data
        assert isinstance(data["dependencies"], dict)


class TestModelsEndpoint:
    """GET /api/v1/models — list models (no auth)."""

    @pytest.mark.anyio
    async def test_status_200(self, client):
        response = await client.get("/api/v1/models")
        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_response_has_models_and_default(self, client):
        response = await client.get("/api/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert "defaultModel" in data
        assert isinstance(data["models"], list)
        if data["models"]:
            m = data["models"][0]
            assert "id" in m
            assert "name" in m
            assert "costPer1mInput" in m
            assert "costPer1mOutput" in m


# =============================================================================
# Protected routes — 401 without token
# =============================================================================

class TestComposeStreamRequiresAuth:
    """POST /api/v1/maestro/stream."""

    @pytest.mark.anyio
    async def test_no_auth_returns_401(self, client):
        response = await client.post(
            "/api/v1/maestro/stream",
            json={"prompt": "play", "project": {}},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_invalid_token_returns_401(self, client):
        response = await client.post(
            "/api/v1/maestro/stream",
            headers={"Authorization": "Bearer invalid-token"},
            json={"prompt": "play", "project": {}},
        )
        assert response.status_code == 401


class TestValidateTokenRequiresAuth:
    """GET /api/v1/validate-token."""

    @pytest.mark.anyio
    async def test_no_auth_returns_401(self, client):
        response = await client.get("/api/v1/validate-token")
        assert response.status_code == 401


class TestConversationsRequireAuth:
    """Conversation endpoints require JWT."""

    @pytest.mark.anyio
    async def test_post_conversations_401_without_auth(self, client):
        response = await client.post(
            "/api/v1/conversations",
            json={"title": "Test"},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_get_conversations_401_without_auth(self, client):
        response = await client.get("/api/v1/conversations")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_get_conversations_search_401_without_auth(self, client):
        response = await client.get("/api/v1/conversations/search?q=test")
        assert response.status_code == 401


class TestVariationRequireAuth:
    """Variation endpoints require JWT."""

    @pytest.mark.anyio
    async def test_post_variation_propose_401_without_auth(self, client):
        response = await client.post(
            "/api/v1/variation/propose",
            json={
                "intent": "make it louder",
                "project_id": "proj-1",
                "project_state": {},
                "base_state_id": "state-1",
            },
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_get_variation_stream_401_without_auth(self, client):
        response = await client.get("/api/v1/variation/stream?variation_id=v1")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_post_variation_commit_401_without_auth(self, client):
        response = await client.post(
            "/api/v1/variation/commit",
            json={
                "project_id": "p1",
                "base_state_id": "s1",
                "variation_id": "v1",
                "accepted_phrase_ids": [],
            },
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_post_variation_discard_401_without_auth(self, client):
        response = await client.post(
            "/api/v1/variation/discard",
            json={"variation_id": "v1", "project_id": "p1"},
        )
        assert response.status_code == 401


class TestUsersMeRequiresAuth:
    """GET /api/v1/users/me requires JWT."""

    @pytest.mark.anyio
    async def test_no_auth_returns_401(self, client):
        response = await client.get("/api/v1/users/me")
        assert response.status_code == 401


class TestMcpRequireAuth:
    """MCP HTTP endpoints require JWT (prefix /api/v1/mcp)."""

    @pytest.mark.anyio
    async def test_get_tools_401_without_auth(self, client):
        response = await client.get("/api/v1/mcp/tools")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_get_tool_by_name_401_without_auth(self, client):
        response = await client.get("/api/v1/mcp/tools/stori_play")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_post_tool_call_401_without_auth(self, client):
        response = await client.post(
            "/api/v1/mcp/tools/stori_play/call",
            json={"name": "stori_play", "arguments": {}},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_get_info_401_without_auth(self, client):
        response = await client.get("/api/v1/mcp/info")
        assert response.status_code == 401


# =============================================================================
# Conversations — with auth: status + response shape
# =============================================================================

class TestConversationsWithAuth:
    """Conversation CRUD with valid JWT."""

    @pytest.mark.anyio
    async def test_create_conversation_201_and_shape(self, client, auth_headers):
        response = await client.post(
            "/api/v1/conversations",
            headers=auth_headers,
            json={"title": "Contract Test Conv", "project_context": {"tempo": 120}},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["title"] == "Contract Test Conv"
        assert "createdAt" in data
        assert "updatedAt" in data
        assert "isArchived" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)

    @pytest.mark.anyio
    async def test_list_conversations_200_and_shape(self, client, auth_headers):
        response = await client.get("/api/v1/conversations", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert isinstance(data["conversations"], list)

    @pytest.mark.anyio
    async def test_search_conversations_200_and_shape(self, client, auth_headers):
        response = await client.get(
            "/api/v1/conversations/search?q=test",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert isinstance(data["results"], list)

    @pytest.mark.anyio
    async def test_get_conversation_200_and_shape(self, client, auth_headers):
        # Create one first
        create = await client.post(
            "/api/v1/conversations",
            headers=auth_headers,
            json={"title": "Get Me"},
        )
        assert create.status_code == 201
        cid = create.json()["id"]
        response = await client.get(
            f"/api/v1/conversations/{cid}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == cid
        assert "title" in data
        assert "messages" in data
        assert "createdAt" in data
        assert "updatedAt" in data


# =============================================================================
# Users — register (public), me (auth)
# =============================================================================

class TestUsersRegister:
    """POST /api/v1/users/register — no auth."""

    @pytest.mark.anyio
    async def test_register_200_or_201_and_shape(self, client):
        import uuid
        uid = str(uuid.uuid4())
        response = await client.post(
            "/api/v1/users/register",
            json={"user_id": uid},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert "userId" in data
        assert data["userId"] == uid
        assert "budgetRemaining" in data
        assert "budgetLimit" in data
        assert "usageCount" in data or "createdAt" in data

    @pytest.mark.anyio
    async def test_register_invalid_uuid_400(self, client):
        response = await client.post(
            "/api/v1/users/register",
            json={"user_id": "not-a-uuid"},
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data


class TestUsersMeWithAuth:
    """GET /api/v1/users/me — with JWT."""

    @pytest.mark.anyio
    async def test_me_200_and_shape(self, client, auth_headers):
        response = await client.get("/api/v1/users/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "userId" in data
        assert "budgetRemaining" in data
        assert "budgetLimit" in data
        assert "usageCount" in data or "createdAt" in data


# =============================================================================
# Variation — with auth: propose returns 200 + shape (may 402 if no budget)
# =============================================================================

class TestVariationProposeWithAuth:
    """POST /api/v1/variation/propose with valid JWT."""

    @pytest.mark.anyio
    async def test_propose_accepts_request_and_returns_structured_response(
        self, client, auth_headers
    ):
        response = await client.post(
            "/api/v1/variation/propose",
            headers=auth_headers,
            json={
                "intent": "make the drums louder",
                "project_id": "contract-test-project",
                "project_state": {"tracks": [], "regions": []},
                "base_state_id": "initial",
            },
        )
        # 200 success; 402 budget; 409 state conflict/invalid state_id; 500 backend error
        assert response.status_code in (200, 402, 409, 500)
        if response.status_code == 200:
            data = response.json()
            assert "variationId" in data
            assert "streamUrl" in data or "phrases" in data or "meta" in data
        if response.status_code == 409:
            data = response.json()
            assert "detail" in data


# =============================================================================
# Assets — require X-Device-ID (400 without), 200/503 with
# =============================================================================

class TestAssetsRequireDeviceId:
    """Asset routes require X-Device-ID header (UUID)."""

    @pytest.mark.anyio
    async def test_list_drum_kits_400_without_device_id(self, client):
        response = await client.get("/api/v1/assets/drum-kits")
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_list_soundfonts_400_without_device_id(self, client):
        response = await client.get("/api/v1/assets/soundfonts")
        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_list_drum_kits_200_or_503_with_device_id(self, client):
        response = await client.get(
            "/api/v1/assets/drum-kits",
            headers={"X-Device-ID": "550e8400-e29b-41d4-a716-446655440000"},
        )
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    @pytest.mark.anyio
    async def test_list_soundfonts_200_or_503_with_device_id(self, client):
        response = await client.get(
            "/api/v1/assets/soundfonts",
            headers={"X-Device-ID": "550e8400-e29b-41d4-a716-446655440000"},
        )
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)


# =============================================================================
# MCP — with auth: list tools and info shape
# =============================================================================

class TestMcpWithAuth:
    """MCP endpoints with valid JWT."""

    @pytest.mark.anyio
    async def test_get_tools_200_and_shape(self, client, auth_headers):
        response = await client.get("/api/v1/mcp/tools", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        if data["tools"]:
            t = data["tools"][0]
            assert "name" in t

    @pytest.mark.anyio
    async def test_get_info_200_and_shape(self, client, auth_headers):
        response = await client.get("/api/v1/mcp/info", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "name" in data or "version" in data or "server" in data

    @pytest.mark.anyio
    async def test_get_tool_by_name_200_or_error_shape(self, client, auth_headers):
        response = await client.get(
            "/api/v1/mcp/tools/stori_play",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        if "error" in data:
            assert "Tool not found" in data["error"] or "not found" in data["error"].lower()
        else:
            assert "name" in data

    @pytest.mark.anyio
    async def test_post_tool_call_200_and_shape(self, client, auth_headers):
        response = await client.post(
            "/api/v1/mcp/tools/stori_play/call",
            headers=auth_headers,
            json={"name": "stori_play", "arguments": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "content" in data
        assert isinstance(data["content"], list)


# =============================================================================
# Error response contract (401 and 422)
# =============================================================================

class TestUnauthorizedResponseShape:
    """401 responses have a consistent shape for clients."""

    @pytest.mark.anyio
    async def test_401_has_detail(self, client):
        response = await client.post(
            "/api/v1/maestro/stream",
            json={"prompt": "play", "project": {}},
        )
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data


class TestValidationErrorShape:
    """422 validation errors have a structured body."""

    @pytest.mark.anyio
    async def test_validation_error_422_has_detail(self, client):
        response = await client.post(
            "/api/v1/users/register",
            json={},
        )
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], list)
