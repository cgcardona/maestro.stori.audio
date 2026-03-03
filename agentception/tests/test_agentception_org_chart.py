"""Tests for the /org-chart page, POST /api/org/select-preset, and GET /api/org/tree.

Covers:
- GET /org-chart renders the page with preset cards and D3 tree panel
- POST /api/org/select-preset persists the selection and returns a refreshed partial
- POST /api/org/select-preset rejects unknown preset IDs with HTTP 422
- GET /api/org/tree returns a hierarchical JSON tree for the active preset
- GET /api/org/tree returns 404 when no active preset is selected

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


class TestOrgTree:
    """GET /api/org/tree — D3 tree data endpoint."""

    def test_org_tree_returns_404_when_no_active_org(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """When no active org is set, the endpoint should return HTTP 404."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/api/org/tree")

        assert resp.status_code == 404

    def test_org_tree_returns_200_for_active_preset(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """When an active preset is set, the endpoint should return HTTP 200."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "solo-cto"},
            ),
            patch(
                "agentception.routes.ui.org_chart._load_taxonomy_role_index",
                return_value={},
            ),
        ):
            resp = client.get("/api/org/tree")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

    def test_org_tree_root_name_matches_preset(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """The root node name should match the selected preset's display name."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "solo-cto"},
            ),
            patch(
                "agentception.routes.ui.org_chart._load_taxonomy_role_index",
                return_value={},
            ),
        ):
            resp = client.get("/api/org/tree")

        data = resp.json()
        assert data["name"] == "Solo CTO"
        assert data["id"] == "solo-cto"
        assert data["tier"] == "org"

    def test_org_tree_contains_tier_children(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """The tree should contain leadership and workers tier children."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "solo-cto"},
            ),
            patch(
                "agentception.routes.ui.org_chart._load_taxonomy_role_index",
                return_value={},
            ),
        ):
            resp = client.get("/api/org/tree")

        data = resp.json()
        tier_ids = {child["id"] for child in data["children"]}
        assert "leadership" in tier_ids
        assert "workers" in tier_ids

    def test_org_tree_roles_include_slug_and_tier(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """Each role node must include slug, name, tier, assigned_phases, and figures fields."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "small-team"},
            ),
            patch(
                "agentception.routes.ui.org_chart._load_taxonomy_role_index",
                return_value={
                    "cto": {"tier": "C-Suite", "label": "CTO", "title": "Chief Technology Officer", "compatible_figures": ["turing", "shannon"]},
                    "vp-engineering": {"tier": "VP", "label": "VP Engineering", "title": "VP of Engineering", "compatible_figures": ["dijkstra"]},
                    "python-developer": {"tier": "Worker", "label": "Python Developer", "title": "Python Developer", "compatible_figures": []},
                },
            ),
        ):
            resp = client.get("/api/org/tree")

        data = resp.json()
        leadership = next(c for c in data["children"] if c["id"] == "leadership")
        cto_role = next((r for r in leadership["roles"] if r["slug"] == "cto"), None)
        assert cto_role is not None
        assert cto_role["tier"] == "C-Suite"
        assert cto_role["name"] == "CTO"
        assert "assigned_phases" in cto_role
        assert isinstance(cto_role["assigned_phases"], list)
        assert "figures" in cto_role
        assert cto_role["figures"] == ["turing", "shannon"]

    def test_org_tree_figures_capped_at_two(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """Even if a role has many compatible figures, the endpoint returns at most 2."""
        many_figures = ["fig1", "fig2", "fig3", "fig4", "fig5"]
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "solo-cto"},
            ),
            patch(
                "agentception.routes.ui.org_chart._load_taxonomy_role_index",
                return_value={
                    "cto": {"tier": "C-Suite", "label": "CTO", "title": "CTO", "compatible_figures": many_figures},
                    "python-developer": {"tier": "Worker", "label": "Python Dev", "title": "Python Dev", "compatible_figures": []},
                    "pr-reviewer": {"tier": "Worker", "label": "PR Reviewer", "title": "PR Reviewer", "compatible_figures": []},
                },
            ),
        ):
            resp = client.get("/api/org/tree")

        data = resp.json()
        leadership = next(c for c in data["children"] if c["id"] == "leadership")
        cto_role = next(r for r in leadership["roles"] if r["slug"] == "cto")
        assert len(cto_role["figures"]) <= 2

    def test_org_tree_returns_404_for_unknown_active_org(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """If active_org references a preset not in the presets list, return HTTP 404."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch(
                "agentception.routes.ui.org_chart._read_pipeline_config",
                return_value={"active_org": "does-not-exist"},
            ),
        ):
            resp = client.get("/api/org/tree")

        assert resp.status_code == 404

    def test_org_tree_org_tree_panel_present_in_page(
        self,
        client: TestClient,
        sample_presets: list[dict[str, object]],
    ) -> None:
        """The org-chart page HTML must contain the #org-tree-panel div."""
        with (
            patch("agentception.routes.ui.org_chart._load_presets", return_value=sample_presets),
            patch("agentception.routes.ui.org_chart._read_pipeline_config", return_value={}),
        ):
            resp = client.get("/org-chart")

        assert resp.status_code == 200
        assert 'id="org-tree-panel"' in resp.text
