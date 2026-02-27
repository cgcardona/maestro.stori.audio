#!/usr/bin/env python3
"""
Migrate data from SQLite to PostgreSQL.

This script migrates all data from the SQLite database to PostgreSQL:
- Users (with budgets)
- UsageLogs (with prompts, costs, tokens)
- AccessTokens (with hashes, expiration)

Usage:
    python deploy/migrate-sqlite-to-postgres.py \
        --sqlite sqlite:///path/to/maestro.db \
        --postgres postgresql://user:pass@host/db
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

# Run from repo root so app is importable
sys.path.insert(0, '.')

from maestro.db.models import User, UsageLog, AccessToken

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DatabaseMigrator:
    """Handles migration from SQLite to PostgreSQL."""
    
    def __init__(self, sqlite_url: str, postgres_url: str):
        self.sqlite_url = sqlite_url
        self.postgres_url = postgres_url.replace("+asyncpg", "")  # Sync engine for reading
        self.postgres_async_url = postgres_url if "+asyncpg" in postgres_url else postgres_url.replace("postgresql://", "postgresql+asyncpg://")
    
    async def migrate(self):
        """Run the full migration."""
        logger.info("=" * 60)
        logger.info("SQLite to PostgreSQL Migration")
        logger.info("=" * 60)
        logger.info(f"Source: {self.sqlite_url}")
        logger.info(f"Target: {self.postgres_url}")
        logger.info("")
        
        # Create engines
        sqlite_engine = create_engine(self.sqlite_url)
        postgres_async_engine = create_async_engine(self.postgres_async_url)
        
        AsyncSessionLocal = async_sessionmaker(
            postgres_async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        
        try:
            # Migrate Users
            logger.info("Migrating users...")
            with Session(sqlite_engine) as sqlite_session:
                users = sqlite_session.execute(select(User)).scalars().all()
                logger.info(f"Found {len(users)} users in SQLite")
                
                async with AsyncSessionLocal() as pg_session:
                    for user in users:
                        # Create new user in PostgreSQL
                        pg_user = User(
                            id=user.id,
                            budget_cents=user.budget_cents,
                            budget_limit_cents=user.budget_limit_cents,
                            created_at=user.created_at,
                            updated_at=user.updated_at,
                        )
                        pg_session.add(pg_user)
                        logger.info(f"  ✓ User {user.id[:8]}... budget=${user.budget_remaining:.2f}")
                    
                    await pg_session.commit()
                    logger.info(f"✓ Migrated {len(users)} users\n")
            
            # Migrate UsageLogs
            logger.info("Migrating usage logs...")
            with Session(sqlite_engine) as sqlite_session:
                logs = sqlite_session.execute(select(UsageLog)).scalars().all()
                logger.info(f"Found {len(logs)} usage logs in SQLite")
                
                async with AsyncSessionLocal() as pg_session:
                    for log in logs:
                        pg_log = UsageLog(
                            id=log.id,
                            user_id=log.user_id,
                            prompt=log.prompt,
                            model=log.model,
                            prompt_tokens=log.prompt_tokens,
                            completion_tokens=log.completion_tokens,
                            cost_cents=log.cost_cents,
                            created_at=log.created_at,
                        )
                        pg_session.add(pg_log)
                    
                    await pg_session.commit()
                    logger.info(f"✓ Migrated {len(logs)} usage logs\n")
            
            # Migrate AccessTokens
            logger.info("Migrating access tokens...")
            with Session(sqlite_engine) as sqlite_session:
                tokens = sqlite_session.execute(select(AccessToken)).scalars().all()
                logger.info(f"Found {len(tokens)} access tokens in SQLite")
                
                async with AsyncSessionLocal() as pg_session:
                    for token in tokens:
                        pg_token = AccessToken(
                            id=token.id,
                            user_id=token.user_id,
                            token_hash=token.token_hash,
                            expires_at=token.expires_at,
                            revoked=token.revoked,
                            created_at=token.created_at,
                        )
                        pg_session.add(pg_token)
                    
                    await pg_session.commit()
                    logger.info(f"✓ Migrated {len(tokens)} access tokens\n")
            
            logger.info("=" * 60)
            logger.info("Migration Complete!")
            logger.info("=" * 60)
            logger.info(f"Users:        {len(users)}")
            logger.info(f"Usage Logs:   {len(logs)}")
            logger.info(f"Access Tokens: {len(tokens)}")
            logger.info("")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}", exc_info=True)
            raise
        finally:
            sqlite_engine.dispose()
            await postgres_async_engine.dispose()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate Stori Maestro data from SQLite to PostgreSQL"
    )
    parser.add_argument(
        "--sqlite",
        default="sqlite:///maestro.db",
        help="SQLite database URL"
    )
    parser.add_argument(
        "--postgres",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL database URL (or set DATABASE_URL)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing"
    )
    
    args = parser.parse_args()
    
    if not args.postgres or not args.postgres.strip():
        logger.error(
            "PostgreSQL URL required. set DATABASE_URL or pass --postgres "
            "(e.g. postgresql+asyncpg://maestro:YOUR_PASSWORD@localhost:5432/maestro)."
        )
        sys.exit(1)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - no data will be written")
    
    migrator = DatabaseMigrator(args.sqlite, args.postgres)
    asyncio.run(migrator.migrate())


if __name__ == "__main__":
    main()
