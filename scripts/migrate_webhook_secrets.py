"""One-time migration script: encrypt plaintext webhook secrets.

Context
-------
PR #336 added Fernet envelope encryption to ``musehub_webhooks.secret`` via
``maestro.services.musehub_webhook_crypto``.  Existing rows written before
STORI_WEBHOOK_SECRET_KEY was set contain plaintext secrets.  When the key is
first enabled in production, ``decrypt_secret()`` would normally raise a
ValueError for those rows.  PR #347 added a transparent fallback, but the
recommended path is to run this script once to encrypt every legacy row before
or immediately after enabling the key.

Behaviour
---------
- Reads every row in ``musehub_webhooks`` where ``secret != ''``.
- Detects whether each value is already a Fernet token (starts with "gAAAAAB").
- If not, encrypts the plaintext and writes the token back.
- Idempotent: safe to run multiple times; already-encrypted rows are skipped.
- Exits with a summary count and a non-zero exit code only on unexpected errors.

Usage
-----
Run inside the container (bind mount makes this file available):

    docker compose exec maestro python3 /app/scripts/migrate_webhook_secrets.py

Requires STORI_WEBHOOK_SECRET_KEY to be set; exits with an error if absent.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.config import settings
from maestro.db.musehub_models import MusehubWebhook
from maestro.services.musehub_webhook_crypto import encrypt_secret, is_fernet_token

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def migrate(db: AsyncSession) -> tuple[int, int]:
    """Encrypt all plaintext secrets.

    Returns ``(migrated, skipped)`` counts where *migrated* is the number of
    rows updated and *skipped* is the number already encrypted.
    """
    result = await db.execute(
        select(MusehubWebhook).where(MusehubWebhook.secret != "")
    )
    webhooks = result.scalars().all()

    migrated = 0
    skipped = 0

    for webhook in webhooks:
        if is_fernet_token(webhook.secret):
            skipped += 1
            continue

        encrypted = encrypt_secret(webhook.secret)
        await db.execute(
            update(MusehubWebhook)
            .where(MusehubWebhook.webhook_id == webhook.webhook_id)
            .values(secret=encrypted)
        )
        logger.info("✅ Migrated webhook %s", webhook.webhook_id)
        migrated += 1

    await db.commit()
    return migrated, skipped


async def main() -> None:
    if not settings.webhook_secret_key:
        logger.error(
            "❌ STORI_WEBHOOK_SECRET_KEY is not set. "
            "Set this environment variable before running the migration."
        )
        sys.exit(1)

    db_url: str = settings.database_url or ""
    engine = create_async_engine(db_url, echo=False)
    async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        migrated, skipped = await migrate(db)

    await engine.dispose()

    logger.info(
        "✅ Migration complete — %d secret(s) encrypted, %d already encrypted (skipped).",
        migrated,
        skipped,
    )

    if migrated == 0 and skipped == 0:
        logger.info("ℹ️  No webhooks with non-empty secrets found.")


if __name__ == "__main__":
    asyncio.run(main())
