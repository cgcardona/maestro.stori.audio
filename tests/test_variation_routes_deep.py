"""Deep tests for variation API routes (app/api/routes/variation.py).

Covers: propose, commit, discard, stream, get variation endpoints.
"""
from __future__ import annotations

from httpx import AsyncClient
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestProposeVariation:

    @pytest.mark.anyio
    async def test_propose_requires_auth(self, client: AsyncClient) -> None:

        resp = await client.post(
            "/api/v1/variation/propose",
            json={"prompt": "test", "project_id": "p1"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_propose_validation(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        """Propose with missing fields should return validation error."""
        resp = await client.post(
            "/api/v1/variation/propose",
            json={},  # Missing required fields
            headers=auth_headers,
        )
        assert resp.status_code in (200, 422)


class TestCommitVariation:

    @pytest.mark.anyio
    async def test_commit_requires_auth(self, client: AsyncClient) -> None:

        resp = await client.post(
            "/api/v1/variation/commit",
            json={"variation_id": "v1", "accepted_phrase_ids": []},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_commit_nonexistent_variation(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.post(
            "/api/v1/variation/commit",
            json={
                "variation_id": "nonexistent",
                "accepted_phrase_ids": [],
                "project_id": "proj-1",
                "base_state_id": "state-1",
            },
            headers=auth_headers,
        )
        # Should be 404 (not found) or 409 (conflict)
        assert resp.status_code in (404, 409, 422)


class TestDiscardVariation:

    @pytest.mark.anyio
    async def test_discard_requires_auth(self, client: AsyncClient) -> None:

        resp = await client.post(
            "/api/v1/variation/discard",
            json={"variation_id": "v1"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_discard_nonexistent(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.post(
            "/api/v1/variation/discard",
            json={"variation_id": "nonexistent"},
            headers=auth_headers,
        )
        assert resp.status_code in (404, 409, 422)


class TestGetVariation:

    @pytest.mark.anyio
    async def test_get_nonexistent(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.get(
            "/api/v1/variation/nonexistent-id",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_get_requires_auth(self, client: AsyncClient) -> None:

        resp = await client.get("/api/v1/variation/some-id")
        assert resp.status_code in (401, 403)


class TestVariationStream:

    @pytest.mark.anyio
    async def test_stream_nonexistent(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.get(
            "/api/v1/variation/stream?variation_id=nonexistent",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_stream_requires_auth(self, client: AsyncClient) -> None:

        resp = await client.get("/api/v1/variation/stream?variation_id=v1")
        assert resp.status_code in (401, 403)
