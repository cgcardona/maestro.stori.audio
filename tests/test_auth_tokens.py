"""
Tests for app.auth.tokens — JWT generation and validation.

Covers:
  1.  hash_token
  2.  generate_access_code — duration variants, admin flag, user_id
  3.  validate_access_code — happy path, expiry, wrong type, tampered signature
  4.  create_access_token — alias compatibility
  5.  get_user_id_from_token — with and without sub
  6.  get_token_expiration — valid and malformed tokens
  7.  AccessCodeError — raised correctly
  8.  Secret not configured — raises cleanly

JWT operations require STORI_ACCESS_TOKEN_SECRET.  We monkeypatch settings
rather than relying on env, so these tests are hermetic.
"""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import jwt
import pytest

from app.auth.tokens import (
    AccessCodeError,
    create_access_token,
    generate_access_code,
    get_token_expiration,
    get_user_id_from_token,
    hash_token,
    validate_access_code,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-for-unit-tests-only-32char"
_ALGO = "HS256"


def _patch_settings():
    """Patch the settings object so tokens can be generated in tests."""
    return patch(
        "app.auth.tokens.settings",
        access_token_secret=_SECRET,
        access_token_algorithm=_ALGO,
    )


def _make_token(**kwargs) -> str:
    with _patch_settings():
        return generate_access_code(**kwargs)


# ===========================================================================
# 1. hash_token
# ===========================================================================

class TestHashToken:
    def test_returns_64_char_hex(self):
        h = hash_token("sometoken")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert hash_token("abc") == hash_token("abc")

    def test_different_tokens_different_hash(self):
        assert hash_token("token_a") != hash_token("token_b")

    def test_empty_string(self):
        h = hash_token("")
        assert len(h) == 64

    def test_sha256_digest(self):
        import hashlib
        expected = hashlib.sha256(b"hello").hexdigest()
        assert hash_token("hello") == expected


# ===========================================================================
# 2. generate_access_code
# ===========================================================================

class TestGenerateAccessCode:
    def test_returns_string(self):
        token = _make_token(duration_hours=1)
        assert isinstance(token, str)

    def test_duration_hours(self):
        token = _make_token(duration_hours=24)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert payload["type"] == "access"
        # exp should be roughly 24 hours from now
        age = payload["exp"] - payload["iat"]
        assert abs(age - 86400) < 5  # within 5 seconds

    def test_duration_days(self):
        token = _make_token(duration_days=7)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        age = payload["exp"] - payload["iat"]
        assert abs(age - 7 * 86400) < 5

    def test_duration_minutes(self):
        token = _make_token(duration_minutes=30)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        age = payload["exp"] - payload["iat"]
        assert abs(age - 1800) < 5

    def test_combined_duration(self):
        """Hours + days + minutes are summed."""
        token = _make_token(duration_hours=1, duration_days=1, duration_minutes=30)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        age = payload["exp"] - payload["iat"]
        expected = 3600 + 86400 + 1800
        assert abs(age - expected) < 5

    def test_no_duration_raises(self):
        with _patch_settings():
            with pytest.raises(AccessCodeError, match="duration"):
                generate_access_code()

    def test_user_id_in_sub(self):
        token = _make_token(duration_hours=1, user_id="user-123")
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert payload["sub"] == "user-123"

    def test_no_user_id_no_sub(self):
        token = _make_token(duration_hours=1)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert "sub" not in payload

    def test_admin_flag_adds_role(self):
        token = _make_token(duration_hours=1, is_admin=True)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert payload.get("role") == "admin"

    def test_non_admin_has_no_role(self):
        token = _make_token(duration_hours=1, is_admin=False)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert "role" not in payload

    def test_missing_secret_raises(self):
        with patch(
            "app.auth.tokens.settings",
            access_token_secret=None,
            access_token_algorithm=_ALGO,
        ):
            with pytest.raises(AccessCodeError, match="STORI_ACCESS_TOKEN_SECRET"):
                generate_access_code(duration_hours=1)

    def test_iat_is_recent(self):
        before = int(time.time()) - 2
        token = _make_token(duration_hours=1)
        with _patch_settings():
            payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        assert payload["iat"] >= before


# ===========================================================================
# 3. validate_access_code
# ===========================================================================

class TestValidateAccessCode:
    def test_valid_token_returns_payload(self):
        token = _make_token(duration_hours=1)
        with _patch_settings():
            payload = validate_access_code(token)
        assert payload["type"] == "access"

    def test_valid_token_with_user_id(self):
        token = _make_token(duration_hours=1, user_id="u-abc")
        with _patch_settings():
            payload = validate_access_code(token)
        assert payload["sub"] == "u-abc"

    def test_expired_token_raises(self):
        """Token expired 1 second ago should raise AccessCodeError."""
        now = int(time.time())
        payload = {
            "type": "access",
            "iat": now - 10,
            "exp": now - 1,  # already expired
        }
        expired_token = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        with _patch_settings():
            with pytest.raises(AccessCodeError, match="expired"):
                validate_access_code(expired_token)

    def test_wrong_type_raises(self):
        """Token with type != 'access' must be rejected."""
        now = int(time.time())
        payload = {
            "type": "refresh",
            "iat": now,
            "exp": now + 3600,
        }
        token = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        with _patch_settings():
            with pytest.raises(AccessCodeError, match="type"):
                validate_access_code(token)

    def test_tampered_signature_raises(self):
        token = _make_token(duration_hours=1)
        bad_token = token[:-4] + "XXXX"
        with _patch_settings():
            with pytest.raises(AccessCodeError):
                validate_access_code(bad_token)

    def test_garbage_string_raises(self):
        with _patch_settings():
            with pytest.raises(AccessCodeError):
                validate_access_code("not.a.jwt")

    def test_wrong_secret_raises(self):
        token = _make_token(duration_hours=1)
        with patch(
            "app.auth.tokens.settings",
            access_token_secret="different-secret",
            access_token_algorithm=_ALGO,
        ):
            with pytest.raises(AccessCodeError):
                validate_access_code(token)


# ===========================================================================
# 4. create_access_token (alias)
# ===========================================================================

class TestCreateAccessToken:
    def test_alias_returns_valid_token(self):
        with _patch_settings():
            token = create_access_token(expires_hours=1)
            payload = validate_access_code(token)
        assert payload["type"] == "access"

    def test_alias_passes_user_id(self):
        with _patch_settings():
            token = create_access_token(user_id="u-xyz", expires_hours=1)
            payload = validate_access_code(token)
        assert payload.get("sub") == "u-xyz"

    def test_alias_passes_admin(self):
        with _patch_settings():
            token = create_access_token(expires_days=1, is_admin=True)
            payload = validate_access_code(token)
        assert payload.get("role") == "admin"


# ===========================================================================
# 5. get_user_id_from_token
# ===========================================================================

class TestGetUserIdFromToken:
    def test_returns_user_id_when_present(self):
        token = _make_token(duration_hours=1, user_id="u-999")
        result = get_user_id_from_token(token)
        assert result == "u-999"

    def test_returns_none_when_no_sub(self):
        token = _make_token(duration_hours=1)
        result = get_user_id_from_token(token)
        assert result is None

    def test_garbage_token_returns_none(self):
        result = get_user_id_from_token("not.a.jwt")
        assert result is None

    def test_expired_token_still_returns_user_id(self):
        """get_user_id_from_token decodes without verifying — works on expired tokens."""
        now = int(time.time())
        payload = {"type": "access", "iat": now - 100, "exp": now - 1, "sub": "u-exp"}
        expired_token = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        result = get_user_id_from_token(expired_token)
        assert result == "u-exp"


# ===========================================================================
# 6. get_token_expiration
# ===========================================================================

class TestGetTokenExpiration:
    def test_returns_utc_datetime(self):
        token = _make_token(duration_hours=2)
        exp = get_token_expiration(token)
        assert isinstance(exp, datetime)
        assert exp.tzinfo == timezone.utc

    def test_expiration_roughly_correct(self):
        token = _make_token(duration_hours=1)
        exp = get_token_expiration(token)
        now = datetime.now(timezone.utc)
        diff = (exp - now).total_seconds()
        assert 3590 < diff < 3610  # ~1 hour, ±10 seconds

    def test_malformed_token_raises(self):
        with pytest.raises(AccessCodeError):
            get_token_expiration("garbage")

    def test_token_without_exp_raises(self):
        """A JWT without 'exp' claim should raise AccessCodeError."""
        payload = {"type": "access", "iat": int(time.time())}
        no_exp_token = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        with pytest.raises(AccessCodeError, match="expiration"):
            get_token_expiration(no_exp_token)
