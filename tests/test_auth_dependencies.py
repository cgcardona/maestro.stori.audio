"""
Tests for FastAPI auth dependencies: require_device_id and require_valid_token.

Ensures asset endpoints reject bad device IDs and protected endpoints
reject missing/expired/revoked tokens.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.dependencies import require_device_id, require_valid_token


# =============================================================================
# require_device_id
# =============================================================================

@pytest.mark.asyncio
async def test_require_device_id_missing():
    """Missing X-Device-ID raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        await require_device_id(x_device_id=None)
    assert exc_info.value.status_code == 400
    assert "X-Device-ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_device_id_empty_string():
    """Empty X-Device-ID raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        await require_device_id(x_device_id="   ")
    assert exc_info.value.status_code == 400
    assert "X-Device-ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_device_id_invalid_uuid():
    """Non-UUID X-Device-ID raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        await require_device_id(x_device_id="not-a-uuid")
    assert exc_info.value.status_code == 400
    assert "Invalid" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_device_id_valid_uuid():
    """Valid UUID returns the stripped value."""
    device_id = str(uuid.uuid4())
    result = await require_device_id(x_device_id=f"  {device_id}  ")
    assert result == device_id


@pytest.mark.asyncio
async def test_require_device_id_valid_uuid_no_whitespace():
    """Valid UUID with no whitespace returns as-is."""
    device_id = str(uuid.uuid4())
    result = await require_device_id(x_device_id=device_id)
    assert result == device_id


# =============================================================================
# require_valid_token
# =============================================================================

@pytest.mark.asyncio
async def test_require_valid_token_missing_credentials():
    """Missing Authorization header raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        await require_valid_token(credentials=None)
    assert exc_info.value.status_code == 401
    assert "Access code required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_valid_token_invalid_token():
    """Invalid token string raises 401."""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid.jwt.here")
    with pytest.raises(HTTPException) as exc_info:
        await require_valid_token(credentials=creds)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_valid_token_expired_token():
    """Expired token raises 401."""
    import jwt
    from app.config import settings
    with patch.object(settings, "access_token_secret", "test_secret_32chars_for_unit_tests!!"):
        secret = "test_secret_32chars_for_unit_tests!!"
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {"type": "access", "sub": "user-1", "iat": now - 3600, "exp": now - 1}
        token = jwt.encode(payload, secret, algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with patch.object(settings, "access_token_secret", "test_secret_32chars_for_unit_tests!!"):
        with pytest.raises(HTTPException) as exc_info:
            await require_valid_token(credentials=creds)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_valid_token_revoked_returns_401():
    """Revoked token raises 401 when revocation check returns True."""
    from app.auth.tokens import generate_access_code

    token = generate_access_code(duration_hours=1)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("app.auth.dependencies._check_and_register_token", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True  # Simulate revoked
        with pytest.raises(HTTPException) as exc_info:
            await require_valid_token(credentials=creds)
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()
