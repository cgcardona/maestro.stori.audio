"""Tests for health endpoints."""
import pytest


@pytest.mark.anyio
async def test_health_check(client):
    """Test basic health check endpoint."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "ok"
    assert "service" in data
    assert "version" in data


@pytest.mark.anyio
async def test_health_response_structure(client):
    """Health response has expected keys for probes."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    required = {"status", "service", "version", "tagline"}
    for key in required:
        assert key in data, f"Missing key: {key}"
    assert data["status"] == "ok"
    assert data["tagline"] == "the infinite music machine"


@pytest.mark.anyio
async def test_root_endpoint(client):
    """Test root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert "service" in data
    assert "docs" in data
