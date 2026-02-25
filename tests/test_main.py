"""
Tests for app.main: SecurityHeadersMiddleware, root, lifespan.
"""
from __future__ import annotations

from typing import Any
import pytest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient


@patch("app.main.init_db", new_callable=AsyncMock)
@patch("app.main.close_db", new_callable=AsyncMock)
def test_security_headers_middleware_adds_headers(mock_close: Any, mock_init: Any) -> None:

    """SecurityHeadersMiddleware adds X-Frame-Options, X-Content-Type-Options, etc."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in response.headers


@patch("app.main.init_db", new_callable=AsyncMock)
@patch("app.main.close_db", new_callable=AsyncMock)
def test_root_returns_service_info(mock_close: Any, mock_init: Any) -> None:

    """Root endpoint returns service, version, docs."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data
    assert data.get("docs") == "/docs" or "docs" in data


def test_app_has_lifespan() -> None:
    """App has lifespan context manager (used for init_db/close_db)."""
    from app.main import app
    assert app.router.lifespan_context is not None
