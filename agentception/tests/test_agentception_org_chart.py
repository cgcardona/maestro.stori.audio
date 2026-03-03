"""Tests for the org-chart page and its builder API endpoints.

Covers:
- GET /org-chart renders the page with preset cards and builder panel
- POST /api/org/select-preset persists the selection and returns a refreshed partial
- POST /api/org/select-preset rejects unknown preset IDs with HTTP 422
- GET /api/roles/taxonomy returns grouped role taxonomy
- POST /api/org/roles/add adds a role to the active org
- DELETE /api/org/roles/{slug} removes a role from the active org
- POST /api/org/roles/{slug}/phases updates phase assignments
- POST /api/org/templates saves the current org as a named preset

Run targeted:
    docker compose exec agentception pytest agentception/tests/test_agentception_org_chart.py -v
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

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


class TestRolesTaxonomy:
    """GET /api/roles/taxonomy — role taxonomy endpoint."""

    def test_taxonomy_returns_200(self, client: TestClient) -> None:
        """GET /api/roles/taxonomy should return HTTP 200 with JSON."""
        resp = client.get("/api/roles/taxonomy")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_taxonomy_has_all_tiers(self, client: TestClient) -> None:
        """Response must contain c_suite, vp, and worker tiers."""
        resp = client.get("/api/roles/taxonomy")
        data = resp.json()
        assert "tiers" in data
        tiers = data["tiers"]
        assert "c_suite" in tiers
        assert "vp" in tiers
        assert "worker" in tiers

    def test_taxonomy_tier_has_label_and_roles(self, client: TestClient) -> None:
        """Each tier entry must have a 'label' string and a 'roles' list."""
        resp = client.get("/api/roles/taxonomy")
        tiers = resp.json()["tiers"]
        for tier_key, tier_data in tiers.items():
            assert "label" in tier_data, f"tier {tier_key!r} missing 'label'"
            assert "roles" in tier_data, f"tier {tier_key!r} missing 'roles'"
            assert isinstance(tier_data["roles"], list)

    def test_taxonomy_contains_known_roles(self, client: TestClient) -> None:
        """Known roles like 'cto' and 'python-developer' must appear in the taxonomy."""
        resp = client.get("/api/roles/taxonomy")
        tiers = resp.json()["tiers"]
        all_roles: list[str] = []
        for tier_data in tiers.values():
            all_roles.extend(tier_data["roles"])
        assert "cto" in all_roles
        assert "python-developer" in all_roles
        assert "vp-engineering" in all_roles


class TestAddRole:
    """POST /api/org/roles/add — add role to active builder org."""

    def test_add_role_returns_200(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Adding a valid role should return HTTP 200 with an HTML role list."""
        resp = client.post("/api/org/roles/add", data={"slug": "python-developer"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_add_role_persists_to_config(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """After adding a role, pipeline-config.json must contain that slug."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        roles = written.get("active_org_roles", [])
        slugs = [r["slug"] for r in roles]
        assert "cto" in slugs

    def test_add_role_role_card_in_response(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """The response HTML should contain the added role's slug."""
        resp = client.post("/api/org/roles/add", data={"slug": "python-developer"})
        assert "python-developer" in resp.text

    def test_add_role_duplicate_is_idempotent(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Adding the same role twice should not create duplicates in config."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        client.post("/api/org/roles/add", data={"slug": "cto"})
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        roles = written.get("active_org_roles", [])
        cto_entries = [r for r in roles if r["slug"] == "cto"]
        assert len(cto_entries) == 1

    def test_add_role_unknown_slug_returns_422(
        self,
        client: TestClient,
    ) -> None:
        """An unknown role slug should return HTTP 422."""
        resp = client.post("/api/org/roles/add", data={"slug": "not-a-real-role"})
        assert resp.status_code == 422


class TestRemoveRole:
    """DELETE /api/org/roles/{slug} — remove role from active builder org."""

    def test_remove_role_returns_200(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Removing a role that exists should return HTTP 200."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        resp = client.delete("/api/org/roles/cto")
        assert resp.status_code == 200

    def test_remove_role_removes_from_config(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """After removing a role, it must no longer appear in pipeline-config.json."""
        client.post("/api/org/roles/add", data={"slug": "python-developer"})
        client.delete("/api/org/roles/python-developer")
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        roles = written.get("active_org_roles", [])
        slugs = [r["slug"] for r in roles]
        assert "python-developer" not in slugs

    def test_remove_role_nonexistent_is_idempotent(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Removing a slug not in the list should silently succeed (HTTP 200)."""
        resp = client.delete("/api/org/roles/nonexistent-slug")
        assert resp.status_code == 200

    def test_remove_role_returns_html_list(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Response must be HTML containing the role list container."""
        resp = client.delete("/api/org/roles/cto")
        assert "text/html" in resp.headers["content-type"]
        assert "org-role-list" in resp.text


class TestUpdateRolePhases:
    """POST /api/org/roles/{slug}/phases — update phase assignments."""

    def test_update_phases_returns_200(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Updating phases for an existing role should return HTTP 200."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        resp = client.post(
            "/api/org/roles/cto/phases",
            data={"phases": ["ac-workflow/1-setup"]},
        )
        assert resp.status_code == 200

    def test_update_phases_persists_to_config(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """After updating phases, the assigned phases must appear in pipeline-config.json."""
        client.post("/api/org/roles/add", data={"slug": "vp-engineering"})
        client.post(
            "/api/org/roles/vp-engineering/phases",
            data={"phases": ["phase-a", "phase-b"]},
        )
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        roles = written.get("active_org_roles", [])
        vp = next((r for r in roles if r["slug"] == "vp-engineering"), None)
        assert vp is not None
        assert "phase-a" in vp["assigned_phases"]
        assert "phase-b" in vp["assigned_phases"]

    def test_update_phases_clears_when_empty(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Submitting no phases should clear the assigned_phases list."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        client.post("/api/org/roles/cto/phases", data={"phases": ["phase-a"]})
        # Now clear by sending no phases
        client.post("/api/org/roles/cto/phases", data={})
        written = json.loads(tmp_pipeline_config.read_text(encoding="utf-8"))
        roles = written.get("active_org_roles", [])
        cto = next((r for r in roles if r["slug"] == "cto"), None)
        assert cto is not None
        assert cto["assigned_phases"] == []

    def test_update_phases_returns_404_for_missing_role(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Updating phases for a slug not in the active org should return HTTP 404."""
        resp = client.post(
            "/api/org/roles/nonexistent/phases",
            data={"phases": ["phase-a"]},
        )
        assert resp.status_code == 404


class TestSaveTemplate:
    """POST /api/org/templates — save current org as a named preset."""

    def test_save_template_returns_200(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
        tmp_path: Path,
    ) -> None:
        """Saving a valid template should return HTTP 200 with preset list HTML."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        with patch(
            "agentception.routes.ui.org_chart._PRESETS_PATH",
            tmp_path / "org-presets.yaml",
        ):
            (tmp_path / "org-presets.yaml").write_text(
                "presets: []\n", encoding="utf-8"
            )
            resp = client.post(
                "/api/org/templates",
                data={"name": "My Template"},
            )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_save_template_blank_name_returns_422(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """A blank template name should return HTTP 422."""
        client.post("/api/org/roles/add", data={"slug": "cto"})
        resp = client.post("/api/org/templates", data={"name": "   "})
        assert resp.status_code == 422

    def test_save_template_empty_roles_returns_422(
        self,
        client: TestClient,
        tmp_pipeline_config: Path,
    ) -> None:
        """Saving a template with no roles should return HTTP 422."""
        resp = client.post("/api/org/templates", data={"name": "Empty"})
        assert resp.status_code == 422
