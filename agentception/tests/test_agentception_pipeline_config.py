"""Tests for pipeline-config.json reader/writer and API endpoints.

Covers:
- read_pipeline_config returns defaults when file is absent
- write_pipeline_config persists values and returns them
- GET /api/config returns current config
- PUT /api/config validates schema and persists changes
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agentception.app import app
from agentception.models import PipelineConfig
from agentception.readers.pipeline_config import (
    _DEFAULTS,
    read_pipeline_config,
    write_pipeline_config,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests for read_pipeline_config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_pipeline_config_returns_defaults_when_file_absent(
    tmp_path: Path,
) -> None:
    """read_pipeline_config returns the built-in defaults when the config file does not exist."""
    missing = tmp_path / "nonexistent" / "pipeline-config.json"
    with patch("agentception.readers.pipeline_config._config_path", return_value=missing):
        result = await read_pipeline_config()

    assert result.max_eng_vps == _DEFAULTS["max_eng_vps"]
    assert result.max_qa_vps == _DEFAULTS["max_qa_vps"]
    assert result.pool_size_per_vp == _DEFAULTS["pool_size_per_vp"]
    assert result.active_labels_order == _DEFAULTS["active_labels_order"]


@pytest.mark.anyio
async def test_read_pipeline_config_reads_file_when_present(tmp_path: Path) -> None:
    """read_pipeline_config parses the config file and returns a validated PipelineConfig."""
    config_file = tmp_path / "pipeline-config.json"
    custom = {
        "max_eng_vps": 2,
        "max_qa_vps": 3,
        "pool_size_per_vp": 6,
        "active_labels_order": ["agentception/0-scaffold", "agentception/1-controls"],
    }
    config_file.write_text(json.dumps(custom), encoding="utf-8")

    with patch("agentception.readers.pipeline_config._config_path", return_value=config_file):
        result = await read_pipeline_config()

    assert result.max_eng_vps == 2
    assert result.max_qa_vps == 3
    assert result.pool_size_per_vp == 6
    assert result.active_labels_order == ["agentception/0-scaffold", "agentception/1-controls"]


# ---------------------------------------------------------------------------
# Unit tests for write_pipeline_config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_pipeline_config_persists(tmp_path: Path) -> None:
    """write_pipeline_config writes the config to disk and returns it."""
    config_file = tmp_path / ".cursor" / "pipeline-config.json"
    config = PipelineConfig(
        max_eng_vps=1,
        max_qa_vps=1,
        pool_size_per_vp=4,
        active_labels_order=["agentception/0-scaffold"],
    )

    with patch("agentception.readers.pipeline_config._config_path", return_value=config_file):
        returned = await write_pipeline_config(config)

    assert returned == config
    assert config_file.exists()
    on_disk = json.loads(config_file.read_text(encoding="utf-8"))
    assert on_disk["max_eng_vps"] == 1
    assert on_disk["active_labels_order"] == ["agentception/0-scaffold"]


@pytest.mark.anyio
async def test_write_pipeline_config_creates_parent_dirs(tmp_path: Path) -> None:
    """write_pipeline_config creates intermediate directories automatically."""
    nested = tmp_path / "deep" / "nested" / "pipeline-config.json"
    config = PipelineConfig(
        max_eng_vps=1,
        max_qa_vps=1,
        pool_size_per_vp=4,
        active_labels_order=[],
    )
    with patch("agentception.readers.pipeline_config._config_path", return_value=nested):
        await write_pipeline_config(config)

    assert nested.exists()


# ---------------------------------------------------------------------------
# API integration tests — GET /api/config
# ---------------------------------------------------------------------------


def test_config_api_get_returns_defaults() -> None:
    """GET /api/config returns built-in defaults when config file is absent."""
    default_config = PipelineConfig.model_validate(_DEFAULTS)
    with patch(
        "agentception.routes.api.read_pipeline_config",
        new_callable=AsyncMock,
        return_value=default_config,
    ):
        response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["max_eng_vps"] == _DEFAULTS["max_eng_vps"]
    assert body["pool_size_per_vp"] == _DEFAULTS["pool_size_per_vp"]
    assert body["active_labels_order"] == _DEFAULTS["active_labels_order"]


def test_config_api_get_returns_custom_values() -> None:
    """GET /api/config returns the current values from the config file."""
    custom_config = PipelineConfig(
        max_eng_vps=2,
        max_qa_vps=2,
        pool_size_per_vp=8,
        active_labels_order=["agentception/0-scaffold"],
    )
    with patch(
        "agentception.routes.api.read_pipeline_config",
        new_callable=AsyncMock,
        return_value=custom_config,
    ):
        response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["max_eng_vps"] == 2
    assert body["pool_size_per_vp"] == 8
    assert body["active_labels_order"] == ["agentception/0-scaffold"]


# ---------------------------------------------------------------------------
# API integration tests — PUT /api/config
# ---------------------------------------------------------------------------


def test_config_api_put_validates_schema_and_persists() -> None:
    """PUT /api/config validates the body and returns the saved config."""
    payload = {
        "max_eng_vps": 1,
        "max_qa_vps": 1,
        "pool_size_per_vp": 4,
        "active_labels_order": [
            "agentception/0-scaffold",
            "agentception/1-controls",
        ],
    }
    saved_config = PipelineConfig.model_validate(payload)
    with patch(
        "agentception.routes.api.write_pipeline_config",
        new_callable=AsyncMock,
        return_value=saved_config,
    ):
        response = client.put("/api/config", json=payload)

    assert response.status_code == 200
    assert response.json() == payload


def test_config_api_put_rejects_missing_fields() -> None:
    """PUT /api/config returns 422 when required fields are absent."""
    incomplete = {"max_eng_vps": 1}  # missing max_qa_vps, pool_size_per_vp, active_labels_order
    response = client.put("/api/config", json=incomplete)
    assert response.status_code == 422


def test_config_api_put_rejects_wrong_types() -> None:
    """PUT /api/config returns 422 when field types are wrong."""
    bad = {
        "max_eng_vps": "one",  # should be int
        "max_qa_vps": 1,
        "pool_size_per_vp": 4,
        "active_labels_order": [],
    }
    response = client.put("/api/config", json=bad)
    assert response.status_code == 422
