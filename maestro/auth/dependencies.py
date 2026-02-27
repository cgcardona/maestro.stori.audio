"""
FastAPI Authentication Dependencies

Provides dependency injection for protecting endpoints with access token validation
and for asset endpoints with device-ID-only (X-Device-ID) validation.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from maestro.auth.tokens import validate_access_code, AccessCodeError, hash_token, TokenClaims as TokenClaims
from maestro.auth.revocation_cache import get_revocation_status, set_revocation_status

logger = logging.getLogger(__name__)

# HTTPBearer extracts the token from "Authorization: Bearer <token>" header
# auto_error=False allows us to provide custom error messages
security = HTTPBearer(auto_error=False)


async def _check_and_register_token(token: str, claims: TokenClaims) -> bool:
    """
    Check if a token has been revoked, registering it if not found.

    Uses a new database session to avoid dependency issues.
    Auto-registers tokens on first use for revocation tracking.

    Returns:
        True if token is revoked, False if valid

    Raises:
        HTTPException 503: If the database is unavailable (fail closed).
    """
    try:
        from maestro.db.database import AsyncSessionLocal
        from maestro.db.models import AccessToken
        from maestro.auth.tokens import get_token_expiration
        
        async with AsyncSessionLocal() as db:
            token_hash = hash_token(token)
            result = await db.execute(
                select(AccessToken).where(AccessToken.token_hash == token_hash)
            )
            access_token = result.scalar_one_or_none()
            
            if access_token is None:
                # Token not in database - register it for revocation tracking
                user_id = claims.get("sub")
                if user_id:
                    try:
                        expires_at = get_token_expiration(token)
                        new_token = AccessToken(
                            user_id=user_id,
                            token_hash=token_hash,
                            expires_at=expires_at,
                            revoked=False,
                        )
                        db.add(new_token)
                        await db.commit()
                        logger.debug(f"Auto-registered token for user {user_id[:8]}...")
                    except Exception as e:
                        # Don't fail if registration fails (user might not exist yet)
                        logger.debug(f"Could not register token: {e}")
                return False
            
            return access_token.revoked
    except Exception as e:
        # Fail closed: if we cannot verify revocation status, reject the request
        logger.error("Token revocation check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify token status. Please try again later.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_valid_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> TokenClaims:
    """
    FastAPI dependency that validates access tokens.
    
    Checks:
    1. Token is present
    2. Token signature is valid
    3. Token has not expired
    4. Token has not been revoked
    
    Usage:
        @router.post("/protected")
        async def protected_endpoint(
            token_claims: dict = Depends(require_valid_token)
        ):
            # token_claims contains: type, iat, exp, sub (optional)
            ...
    
    Returns:
        Decoded token claims dict
        
    Raises:
        HTTPException 401: If token is missing, invalid, expired, or revoked
    """
    if credentials is None:
        logger.warning("Access attempt without token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access code required. Please provide a valid access code.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    
    try:
        claims = validate_access_code(token)
        logger.debug(f"Valid token, expires at {claims['exp']}")
        
        token_hash = hash_token(token)
        cached = get_revocation_status(token_hash)
        if cached is not None:
            if cached:
                logger.warning(f"Revoked token used by user {claims.get('sub', 'unknown')}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access code has been revoked.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return claims

        # Cache miss: check DB (and auto-register new tokens), then cache result
        if await _check_and_register_token(token, claims):
            set_revocation_status(token_hash, True)
            logger.warning(f"Revoked token used by user {claims.get('sub', 'unknown')}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access code has been revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        set_revocation_status(token_hash, False)
        return claims
        
    except AccessCodeError as e:
        logger.warning("Invalid token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access code.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_device_id(
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
) -> str:
    """
    FastAPI dependency for asset endpoints: require a valid X-Device-ID header (UUID).

    Does not require JWT. Used for drum-kit, soundfont, and bundle download endpoints
    so the macOS app can access assets without touching Keychain.

    Returns:
        The validated device ID string (stripped).

    Raises:
        HTTPException 400: If X-Device-ID is missing, empty, or not a valid UUID.
    """
    if not x_device_id or not x_device_id.strip():
        logger.warning("Asset request without X-Device-ID")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Device-ID header required",
        )
    value = x_device_id.strip()
    try:
        uuid.UUID(value)
    except ValueError:
        logger.warning("Invalid X-Device-ID format: %s", value[:32])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Device-ID format",
        )
    return value
