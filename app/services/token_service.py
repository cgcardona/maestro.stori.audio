"""
Token management service.

Handles token registration, revocation checking, and cleanup.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessToken
from app.auth.tokens import hash_token, get_token_expiration
from app.auth.revocation_cache import clear_revocation_cache

logger = logging.getLogger(__name__)


async def register_token(
    db: AsyncSession,
    token: str,
    user_id: str,
) -> AccessToken:
    """
    Register a new token in the database for revocation tracking.
    
    Args:
        db: Database session
        token: The JWT token string
        user_id: User UUID the token belongs to
        
    Returns:
        The created AccessToken record
    """
    token_hash = hash_token(token)
    expires_at = get_token_expiration(token)
    
    access_token = AccessToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        revoked=False,
    )
    db.add(access_token)
    await db.flush()
    
    logger.info(f"Registered token for user {user_id[:8]}..., expires {expires_at}")
    return access_token


async def is_token_revoked(
    db: AsyncSession,
    token: str,
) -> bool:
    """
    Check if a token has been revoked.
    
    Args:
        db: Database session
        token: The JWT token string
        
    Returns:
        True if token is revoked, False otherwise.
        Returns False if token is not in database (legacy tokens).
    """
    token_hash = hash_token(token)
    
    result = await db.execute(
        select(AccessToken.revoked).where(AccessToken.token_hash == token_hash)
    )
    revoked = result.scalar_one_or_none()
    
    if revoked is None:
        # Token not in database - legacy token, allow it
        return False
    
    return revoked


async def revoke_token(
    db: AsyncSession,
    token: str,
) -> bool:
    """
    Revoke a specific token.
    
    Args:
        db: Database session
        token: The JWT token string to revoke
        
    Returns:
        True if token was found and revoked, False if not found
    """
    token_hash = hash_token(token)
    
    result = await db.execute(
        select(AccessToken).where(AccessToken.token_hash == token_hash)
    )
    access_token = result.scalar_one_or_none()
    
    if access_token is None:
        logger.warning(f"Token not found for revocation: {token_hash[:16]}...")
        return False
    
    access_token.revoked = True
    clear_revocation_cache()
    logger.info(f"Revoked token {access_token.id[:8]}... for user {access_token.user_id[:8]}...")
    return True


async def revoke_all_user_tokens(
    db: AsyncSession,
    user_id: str,
) -> int:
    """
    Revoke all tokens for a user.
    
    Args:
        db: Database session
        user_id: User UUID
        
    Returns:
        Number of tokens revoked
    """
    result = await db.execute(
        select(AccessToken).where(
            AccessToken.user_id == user_id,
            AccessToken.revoked == False,
        )
    )
    tokens = result.scalars().all()
    
    count = 0
    for token in tokens:
        token.revoked = True
        count += 1

    clear_revocation_cache()
    logger.info(f"Revoked {count} tokens for user {user_id[:8]}...")
    return count


async def cleanup_expired_tokens(
    db: AsyncSession,
) -> int:
    """
    Delete expired tokens from the database.
    
    This should be run periodically to keep the table clean.
    
    Returns:
        Number of tokens deleted
    """
    now = datetime.now(timezone.utc)
    
    result = await db.execute(
        delete(AccessToken).where(AccessToken.expires_at < now)
    )
    count = getattr(result, "rowcount", 0) or 0
    if count > 0:
        logger.info(f"Cleaned up {count} expired tokens")
    return int(count)


async def get_user_active_tokens(
    db: AsyncSession,
    user_id: str,
) -> list[AccessToken]:
    """
    Get all active (non-revoked, non-expired) tokens for a user.
    
    Args:
        db: Database session
        user_id: User UUID
        
    Returns:
        List of active AccessToken records
    """
    now = datetime.now(timezone.utc)
    
    result = await db.execute(
        select(AccessToken).where(
            AccessToken.user_id == user_id,
            AccessToken.revoked == False,
            AccessToken.expires_at > now,
        ).order_by(AccessToken.created_at.desc())
    )
    
    return list(result.scalars().all())
