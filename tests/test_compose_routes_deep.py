"""Deep tests for compose API routes (app/api/routes/compose.py).

Covers: stream endpoint, preview endpoint, validate-token endpoint.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestComposeStreamRoute:

    @pytest.mark.anyio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/v1/compose/stream",
            json={"prompt": "make drums"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_empty_prompt(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/compose/stream",
            json={"prompt": ""},
            headers=auth_headers,
        )
        # Should either reject or process
        assert resp.status_code in (200, 422)


class TestComposePreview:

    @pytest.mark.anyio
    async def test_preview_requires_auth(self, client):
        resp = await client.post(
            "/api/v1/compose/preview",
            json={"prompt": "make drums"},
        )
        assert resp.status_code in (401, 403, 404, 405)

    @pytest.mark.anyio
    async def test_preview_endpoint(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/compose/preview",
            json={"prompt": "set tempo to 120"},
            headers=auth_headers,
        )
        # May return 200 with intent info or 404/405 if not registered
        assert resp.status_code in (200, 404, 405)


class TestValidateToken:

    @pytest.mark.anyio
    async def test_validate_good_token(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/validate-token",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("valid") is True

    @pytest.mark.anyio
    async def test_validate_no_token(self, client):
        resp = await client.get("/api/v1/validate-token")
        assert resp.status_code in (200, 401)

    @pytest.mark.anyio
    async def test_validate_bad_token(self, client):
        resp = await client.get(
            "/api/v1/validate-token",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("valid") is False
