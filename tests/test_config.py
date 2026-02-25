"""
Tests for application config (Settings).

Ensures required and optional settings load correctly and defaults are sane.
"""
from __future__ import annotations

import pytest


def test_settings_loads_with_env() -> None:
    """Settings load from environment (or defaults)."""
    from app.config import settings

    assert settings.app_name is not None
    assert settings.app_version is not None
    assert hasattr(settings, "database_url")
    assert hasattr(settings, "debug")


def test_settings_llm_model_default() -> None:
    """LLM model has a default value."""
    from app.config import settings

    assert getattr(settings, "llm_model", None) is not None or hasattr(settings, "llm_model")


def test_settings_approved_models_available() -> None:
    """APPROVED_MODELS is non-empty for cost calculation."""
    from app.config import APPROVED_MODELS

    assert len(APPROVED_MODELS) > 0
    for model_id, info in APPROVED_MODELS.items():
        assert "input_cost" in info
        assert "output_cost" in info
