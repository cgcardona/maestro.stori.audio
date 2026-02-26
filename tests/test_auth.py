"""Tests for authentication and access token functionality."""
from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from collections.abc import Generator

import pytest
import time
import jwt
from datetime import datetime, timezone
from unittest.mock import patch

from app.auth.tokens import (
    generate_access_code,
    validate_access_code,
    get_token_expiration,
    AccessCodeError,
)
from app.config import settings


# Test secret for unit tests
TEST_SECRET = "test_secret_key_for_unit_tests_only_32chars"


@pytest.fixture(autouse=True)
def mock_settings() -> Generator[None, None, None]:
    """Mock settings to use test secret."""
    with patch.object(settings, "access_token_secret", TEST_SECRET):
        yield


class TestGenerateAccessCode:
    """Tests for access code generation."""
    
    def test_generate_with_hours(self) -> None:

        """Should generate valid token with hours duration."""
        token = generate_access_code(duration_hours=24)
        assert token is not None
        assert len(token) > 50  # JWT tokens are reasonably long
        
        # Verify it's a valid JWT
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
        assert payload["type"] == "access"
        assert "iat" in payload
        assert "exp" in payload
        
        # Check expiration is approximately 24 hours from now
        exp = payload["exp"]
        iat = payload["iat"]
        assert exp - iat == pytest.approx(24 * 3600, abs=5)
    
    def test_generate_with_days(self) -> None:

        """Should generate valid token with days duration."""
        token = generate_access_code(duration_days=7)
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
        
        exp = payload["exp"]
        iat = payload["iat"]
        assert exp - iat == pytest.approx(7 * 24 * 3600, abs=5)
    
    def test_generate_with_minutes(self) -> None:

        """Should generate valid token with minutes duration."""
        token = generate_access_code(duration_minutes=30)
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
        
        exp = payload["exp"]
        iat = payload["iat"]
        assert exp - iat == pytest.approx(30 * 60, abs=5)
    
    def test_generate_combined_duration(self) -> None:

        """Should combine multiple duration units."""
        token = generate_access_code(duration_days=1, duration_hours=12)
        payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
        
        exp = payload["exp"]
        iat = payload["iat"]
        expected_seconds = (24 + 12) * 3600  # 36 hours
        assert exp - iat == pytest.approx(expected_seconds, abs=5)
    
    def test_generate_no_duration_raises(self) -> None:

        """Should raise error when no duration specified."""
        with pytest.raises(AccessCodeError, match="Must specify at least one of"):
            generate_access_code()
    
    def test_generate_no_secret_raises(self) -> None:

        """Should raise error when secret not configured."""
        with patch.object(settings, "access_token_secret", None):
            with pytest.raises(AccessCodeError, match="ACCESS_TOKEN_SECRET not configured"):
                generate_access_code(duration_hours=1)


class TestValidateAccessCode:
    """Tests for access code validation."""
    
    def test_validate_valid_token(self) -> None:

        """Should successfully validate a valid token."""
        token = generate_access_code(duration_hours=1)
        claims = validate_access_code(token)
        
        assert claims["type"] == "access"
        assert "iat" in claims
        assert "exp" in claims

    def test_token_generate_validate_roundtrip(self) -> None:

        """Generate then validate: claims match (sub, exp > iat, type)."""
        user_id = "user-roundtrip-123"
        token = generate_access_code(user_id=user_id, duration_hours=24)
        claims = validate_access_code(token)
        assert claims["type"] == "access"
        assert claims.get("sub") == user_id
        assert claims["exp"] > claims["iat"]
        assert claims["exp"] - claims["iat"] == pytest.approx(24 * 3600, abs=5)
    
    def test_validate_expired_token(self) -> None:

        """Should reject expired token."""
        # Create a token that's already expired
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "type": "access",
            "iat": now - 3600,  # Issued 1 hour ago
            "exp": now - 1,      # Expired 1 second ago
        }
        token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
        
        with pytest.raises(AccessCodeError, match="expired"):
            validate_access_code(token)
    
    def test_validate_invalid_signature(self) -> None:

        """Should reject token with invalid signature."""
        token = generate_access_code(duration_hours=1)
        
        # Tamper with the token
        parts = token.split(".")
        parts[2] = parts[2][:-5] + "XXXXX"  # Corrupt signature
        tampered_token = ".".join(parts)
        
        with pytest.raises(AccessCodeError, match="Invalid access code"):
            validate_access_code(tampered_token)
    
    def test_validate_wrong_secret(self) -> None:

        """Should reject token signed with different secret."""
        payload = {
            "type": "access",
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(datetime.now(timezone.utc).timestamp()) + 3600,
        }
        # Use ≥32 bytes to avoid PyJWT InsecureKeyLengthWarning for HS256
        token = jwt.encode(
            payload, "wrong_secret_key_32_bytes_long!!!!!!!!", algorithm="HS256"
        )
        
        with pytest.raises(AccessCodeError, match="Invalid access code"):
            validate_access_code(token)
    
    def test_validate_wrong_type(self) -> None:

        """Should reject token with wrong type."""
        payload = {
            "type": "refresh",  # Wrong type
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(datetime.now(timezone.utc).timestamp()) + 3600,
        }
        token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
        
        with pytest.raises(AccessCodeError, match="Invalid token type"):
            validate_access_code(token)
    
    def test_validate_malformed_token(self) -> None:

        """Should reject malformed token."""
        with pytest.raises(AccessCodeError, match="Invalid access code"):
            validate_access_code("not.a.valid.jwt.token")
    
    def test_validate_empty_token(self) -> None:

        """Should reject empty token."""
        with pytest.raises(AccessCodeError):
            validate_access_code("")


class TestGetTokenExpiration:
    """Tests for getting token expiration without validation."""
    
    def test_get_expiration(self) -> None:

        """Should return correct expiration datetime."""
        token = generate_access_code(duration_hours=24)
        expiration = get_token_expiration(token)
        
        assert isinstance(expiration, datetime)
        assert expiration.tzinfo is not None
        
        # Should be approximately 24 hours from now
        now = datetime.now(timezone.utc)
        delta = expiration - now
        assert 23 * 3600 < delta.total_seconds() < 25 * 3600
    
    def test_get_expiration_malformed(self) -> None:

        """Should raise error for malformed token."""
        with pytest.raises(AccessCodeError, match="Invalid token format"):
            get_token_expiration("not.a.jwt")


class TestValidateTokenEndpoint:
    """Tests for the /validate-token endpoint."""
    
    @pytest.mark.anyio
    async def test_validate_token_success(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Should return valid response for valid token."""
        token = generate_access_code(duration_hours=1)
        
        response = await client.get(
            "/api/v1/validate-token",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "expiresAt" in data
        assert "expiresInSeconds" in data
        assert data["expiresInSeconds"] > 0
    
    @pytest.mark.anyio
    async def test_validate_token_missing(self, client: AsyncClient) -> None:

        """Should return 401 when token is missing."""
        response = await client.get("/api/v1/validate-token")
        
        assert response.status_code == 401
        assert "Access code required" in response.json()["detail"]
    
    @pytest.mark.anyio
    async def test_validate_token_invalid(self, client: AsyncClient) -> None:

        """Should return 401 for invalid token."""
        response = await client.get(
            "/api/v1/validate-token",
            headers={"Authorization": "Bearer invalid_token"}
        )
        
        assert response.status_code == 401
    
    @pytest.mark.anyio
    async def test_validate_token_expired(self, client: AsyncClient) -> None:

        """Should return 401 for expired token."""
        # Create expired token
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "type": "access",
            "iat": now - 3600,
            "exp": now - 1,
        }
        token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
        
        response = await client.get(
            "/api/v1/validate-token",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()


class TestComposeEndpointAuth:
    """Tests for authentication on the maestro endpoint."""
    
    @pytest.mark.anyio
    async def test_maestro_requires_auth(self, client: AsyncClient) -> None:

        """Should return 401 when accessing maestro without token."""
        response = await client.post(
            "/api/v1/maestro/stream",
            json={"prompt": "Create a melody", "mode": "create"}
        )
        
        assert response.status_code == 401
        assert "Access code required" in response.json()["detail"]
    
    @pytest.mark.anyio
    async def test_maestro_with_invalid_token(self, client: AsyncClient) -> None:

        """Should return 401 with invalid token."""
        response = await client.post(
            "/api/v1/maestro/stream",
            json={"prompt": "Create a melody", "mode": "create"},
            headers={"Authorization": "Bearer invalid_token"}
        )
        
        assert response.status_code == 401
