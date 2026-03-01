"""Webhook secret encryption — AES-256 envelope encryption for musehub_webhooks.secret.

Webhook signing secrets must be recoverable at delivery time (so we can compute the
HMAC-SHA256 header for subscribers).  One-way hashing (bcrypt/SHA256) is therefore
not an option.  Instead we use Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256
under the hood, equivalent security to AES-256 for the threat model here) keyed with
STORI_WEBHOOK_SECRET_KEY from the environment.

Encryption contract
-------------------
- ``encrypt_secret(plaintext)`` → base64url-encoded Fernet token (str).
- ``decrypt_secret(ciphertext)`` → original plaintext str.
- Both functions are pure (no I/O) and synchronous.
- When STORI_WEBHOOK_SECRET_KEY is not configured, the functions are transparent
  pass-throughs so local dev works without extra setup (see warning in decrypt).

Key management
--------------
Generate a key once and store it in the environment:

    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Set STORI_WEBHOOK_SECRET_KEY to that value in your .env or secret manager.
Rotate keys by re-encrypting all secrets and updating the env var; Fernet tokens
carry the key version so future decryption needs the matching key.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from maestro.config import settings

logger = logging.getLogger(__name__)

# Lazily initialised Fernet instance — None when the key is not configured.
_fernet: Fernet | None = None
_fernet_initialised = False


def _get_fernet() -> Fernet | None:
    """Return the singleton Fernet instance, initialising it on first call.

    Returns None when STORI_WEBHOOK_SECRET_KEY is not set (local dev fallback).
    """
    global _fernet, _fernet_initialised
    if _fernet_initialised:
        return _fernet
    _fernet_initialised = True
    key = settings.webhook_secret_key
    if not key:
        logger.warning(
            "⚠️ STORI_WEBHOOK_SECRET_KEY is not set — webhook secrets stored as plaintext. "
            "Set this key in production to encrypt secrets at rest."
        )
        return None
    _fernet = Fernet(key.encode())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a webhook signing secret for storage in the database.

    Returns a Fernet token (base64url string) when a key is configured, or the
    original plaintext when STORI_WEBHOOK_SECRET_KEY is absent (dev fallback).
    Empty secrets are returned as-is regardless of key configuration.
    """
    if not plaintext:
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    token: bytes = fernet.encrypt(plaintext.encode())
    return token.decode()


_FERNET_TOKEN_PREFIX = "gAAAAAB"


def is_fernet_token(value: str) -> bool:
    """Return True if *value* looks like a Fernet token.

    Fernet tokens are base64url-encoded and always begin with "gAAAAAB" (the
    binary magic bytes 0x80 encoded in URL-safe base64).  We use this prefix
    to distinguish already-encrypted values from legacy plaintext secrets.
    """
    return value.startswith(_FERNET_TOKEN_PREFIX)


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a webhook signing secret retrieved from the database.

    Accepts a Fernet token produced by ``encrypt_secret``.  Returns the original
    plaintext when a key is configured, or the value unchanged when no key is set
    (matching the dev fallback in ``encrypt_secret``).

    **Transparent migration fallback:** if decryption fails with ``InvalidToken``
    and the value does not look like a Fernet token (i.e. it is a pre-migration
    plaintext secret), the plaintext is returned as-is with a deprecation warning.
    This prevents hard failures on existing webhooks while
    ``scripts/migrate_webhook_secrets.py`` (or the automatic transparent path) has
    not yet re-encrypted every row.  Once all rows are migrated the fallback is
    never triggered.

    Raises ``ValueError`` only when the value *looks like* a Fernet token but
    cannot be decrypted — which indicates a genuine key mismatch or corruption.
    Empty values are returned as-is.
    """
    if not ciphertext:
        return ciphertext
    fernet = _get_fernet()
    if fernet is None:
        return ciphertext
    try:
        plaintext: bytes = fernet.decrypt(ciphertext.encode())
        return plaintext.decode()
    except InvalidToken as exc:
        if not is_fernet_token(ciphertext):
            # Legacy plaintext secret stored before encryption was enabled.
            # Return it as-is so existing webhooks keep working; callers should
            # re-encrypt by calling encrypt_secret() and persisting the result.
            logger.warning(
                "⚠️ Webhook secret appears to be unencrypted plaintext. "
                "Run scripts/migrate_webhook_secrets.py to encrypt all legacy "
                "secrets. This fallback will be removed in a future release."
            )
            return ciphertext
        raise ValueError(
            "Failed to decrypt webhook secret — the value may have been encrypted "
            "with a different key or is corrupt. Check STORI_WEBHOOK_SECRET_KEY."
        ) from exc
