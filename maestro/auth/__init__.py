"""
Maestro Authentication Module

Provides JWT-based access token generation and validation.
"""
from __future__ import annotations

from maestro.auth.tokens import (
    generate_access_code,
    validate_access_code,
    get_user_id_from_token,
    hash_token,
    AccessCodeError,
)
from maestro.auth.dependencies import optional_token, require_valid_token, require_device_id

__all__ = [
    "generate_access_code",
    "validate_access_code",
    "get_user_id_from_token",
    "hash_token",
    "AccessCodeError",
    "optional_token",
    "require_valid_token",
    "require_device_id",
]
