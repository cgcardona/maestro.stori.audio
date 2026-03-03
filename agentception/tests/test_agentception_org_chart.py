"""Tests for the /org-chart page and POST /api/org/select-preset endpoint.

Covers:
- GET /org-chart renders the page with preset cards
- POST /api/org/select-preset persists the selection and returns a refreshed partial
- POST /api/org/select-preset rejects unknown preset IDs with HTTP 422

Run targeted:
    docker compose exec agentception pytest agentception/tests/test_agentception_org_chart.py -v
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentception.app import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Synchronous test client wrapping the full FastAPI app."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def tmp_pipeline_config(tmp_path: Path) -> Generator[Path, None, None]:
    """Write a minimal pipeline-config.json to a temp dir and patch settings.repo_dir."""
    config = {"active_org": None}
    config_dir = tmp_path / ".cursor"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "pipeline-config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    with patch("agentception.routes.ui.org_chart.settings") as mock_settings:
        mock_settings.repo_dir = tmp_path
        yield config_file


@pytest.fixture()
def sample_presets() -> list[dict[str, object]]:
    """Minimal preset list for mocking _load_presets."""
    return [
        {
            "id": "solo-cto",
            "name": "Solo CTO",
            "description": "Minimal setup.",
            "tiers": {
                "leadership": ["cto"],
                "workers": ["python-developer", "pr-reviewer"],
            },
        },
        {
            "id": "small-team",
            "name": "Small Team",
            "description": "A focused squad.",
            "tiers": {
                "leadership": ["cto", "vp-engineering"],
                "workers": ["python-developer", "frontend-developer", "test-engineer"],
            },
        },
        {
            "id": "full",
            "name": "Full Org",
            "description": "Complete ten-slot org.",
            "tiers": {
                "leadership": ["cto", "vp-engineering", "vp-qa"],
                "workers": ["python-developer", "frontend-developer", "typescript-developer"],
            },
        },
    ]


class TestOrgChartPage:
    """GET /org-chart — full-page render."""

    def test_org_chart_returns_200(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """GET /org-chart should return HTTP 200 with a valid HTML body."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/org-chart")

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_org_chart_contains_preset_names(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """Preset card names should appear in the rendered HTML."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/org-chart")

        body = resp.text
        assert "Solo CTO" in body
        assert "Small Team" in body
        assert "Full Org" in body

    def test_org_chart_contains_right_panel_shell(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """The right panel shell div must exist for future issues #829 and #830."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/org-chart")

        assert 'id="org-right-panel"' in resp.text

    def test_org_chart_marks_active_preset(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """When active_org is set in config, the matching card should have the active class."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "small-team"},
            ),
        ):
            resp = client.get("/org-chart")

        assert "org-preset-card--active" in resp.text

    def test_org_chart_empty_presets_degrades_gracefully(
        self,
        client: TestClient,
    ) -> None:
        """If no presets are found, the page should still return 200 without crashing."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=[]),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/org-chart")

        assert resp.status_code == 200


class TestSelectPreset:
    """POST /api/org/select-preset — preset persistence."""

    def test_select_preset_persists_active_org(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
        tmp_pipeline_config: Path,
    ) -> None:
        """Selecting a valid preset should write active_org to pipeline-config.json."""
        with patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets):
            resp = client.post("/api/org/select-preset", data={"preset_id": "solo-cto"})

        assert resp.status_code == 200
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        assert written.get("active_org") == "solo-cto"

    def test_select_preset_returns_refreshed_partial(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
        tmp_pipeline_config: Path,
    ) -> None:
        """The response should contain the preset list partial with the new active card."""
        with patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets):
            resp = client.post("/api/org/select-preset", data={"preset_id": "full"})

        assert resp.status_code == 200
        body = resp.text
        assert "org-preset-list" in body
        assert "Full Org" in body

    def test_select_preset_rejects_unknown_id(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """An unknown preset_id should return HTTP 422."""
        with patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets):
            resp = client.post(
                "/api/org/select-preset",
                data={"preset_id": "does-not-exist"},
            )

        assert resp.status_code == 422

    def test_select_preset_active_card_marked(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
        tmp_pipeline_config: Path,
    ) -> None:
        """After selection, the returned partial should mark the chosen card as active."""
        with patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets):
            resp = client.post("/api/org/select-preset", data={"preset_id": "small-team"})

        assert resp.status_code == 200
        assert "org-preset-card--active" in resp.text
