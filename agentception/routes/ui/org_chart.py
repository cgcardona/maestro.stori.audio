"""UI routes: Org Chart page with preset org picker and D3 tree endpoint.

Provides:
- ``GET /org-chart`` — full-page render of the org chart with a left-panel
  preset picker and a right-panel shell (``#org-right-panel``) reserved for
  the interactive builder (#829) and D3 tree (#830).
- ``POST /api/org/select-preset`` — HTMX endpoint that persists the chosen
  preset to ``pipeline-config.json`` under the ``active_org`` key and returns
  a refreshed left-panel partial so the active card updates in-place.
- ``GET /api/org/tree`` — returns the active preset as a hierarchical JSON
  tree consumed by the D3 tree visualization in ``org_chart_tree.js``.

Preset definitions are loaded from ``org-presets.yaml`` at the repo root.
The file is read once per request (fast — ~1 KB) so hot-editing the YAML
during development takes effect without a service restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypedDict

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from agentception.config import settings
from agentception.models import OrgTreeNode, OrgTreeRole
from ._shared import _TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter()

# Path to the org-presets YAML, resolved relative to the repo root so it works
# both on the host and inside Docker (where the repo is mounted at /repo).
_PRESETS_PATH = Path(__file__).parent.parent.parent.parent / "org-presets.yaml"


def _load_presets() -> list[dict[str, Any]]:
    """Read and parse org-presets.yaml, returning the ``presets`` list.

    Returns an empty list if the file is missing or malformed so the UI
    degrades gracefully rather than raising a 500.
    """
    try:
        raw: object = yaml.safe_load(_PRESETS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        presets: object = raw.get("presets", [])
        if not isinstance(presets, list):
            return []
        return [p for p in presets if isinstance(p, dict)]
    except Exception as exc:
        logger.warning("⚠️ Could not load org-presets.yaml: %s", exc)
        return []


def _pipeline_config_path() -> Path:
    """Return the canonical path to pipeline-config.json for the active repo."""
    return settings.repo_dir / ".cursor" / "pipeline-config.json"


def _read_pipeline_config() -> dict[str, Any]:
    """Read pipeline-config.json, returning an empty dict on any error."""
    path = _pipeline_config_path()
    if not path.exists():
        return {}
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("⚠️ Could not read pipeline-config.json: %s", exc)
        return {}


def _write_pipeline_config(data: dict[str, Any]) -> None:
    """Persist *data* back to pipeline-config.json atomically via a rename."""
    path = _pipeline_config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.rename(path)


@router.get("/org-chart", response_class=HTMLResponse)
async def org_chart_page(request: Request) -> HTMLResponse:
    """Org Chart page — preset picker (left) + builder shell (right).

    Renders the full org-chart layout.  The right panel (``#org-right-panel``)
    is an empty shell that issues #829 and #830 will populate with the
    interactive builder and D3 tree respectively.
    """
    presets = _load_presets()
    config = _read_pipeline_config()
    active_org: object = config.get("active_org")
    active_org_id: str | None = active_org if isinstance(active_org, str) else None

    return _TEMPLATES.TemplateResponse(
        request,
        "org_chart.html",
        {
            "presets": presets,
            "active_org_id": active_org_id,
        },
    )


# Path to role-taxonomy.yaml — used by /api/org/tree to enrich role nodes.
_TAXONOMY_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "gen_prompts" / "role-taxonomy.yaml"

# Tier label mapping from taxonomy level ids to display strings.
_TIER_LABELS: dict[str, str] = {
    "c_suite": "C-Suite",
    "vp_level": "VP",
    "workers": "Worker",
}


class _RoleIndexEntry(TypedDict):
    """Enriched metadata for a single role slug from role-taxonomy.yaml."""

    tier: str
    compatible_figures: list[str]
    label: str
    title: str



def _load_taxonomy_role_index() -> dict[str, _RoleIndexEntry]:
    """Build a flat slug → role-metadata dict from role-taxonomy.yaml.

    Returns an empty dict if the taxonomy file is missing or malformed so the
    tree endpoint degrades gracefully.  Each value includes ``tier`` (display
    string) and ``compatible_figures`` (list of strings).
    """
    try:
        raw: object = yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        index: dict[str, _RoleIndexEntry] = {}
        for level in raw.get("levels", []):
            if not isinstance(level, dict):
                continue
            level_id = str(level.get("id", ""))
            tier_label = _TIER_LABELS.get(level_id, level_id)
            for role in level.get("roles", []):
                if not isinstance(role, dict):
                    continue
                slug = str(role.get("slug", ""))
                if slug:
                    figures: object = role.get("compatible_figures", [])
                    index[slug] = {
                        "tier": tier_label,
                        "compatible_figures": [str(f) for f in figures] if isinstance(figures, list) else [],
                        "label": str(role.get("label", slug)),
                        "title": str(role.get("title", slug)),
                    }
        return index
    except Exception as exc:
        logger.warning("⚠️ Could not load role-taxonomy.yaml: %s", exc)
        return {}


@router.get("/api/org/tree", response_class=JSONResponse)
async def org_tree() -> JSONResponse:
    """Return the active preset org as a hierarchical JSON tree for the D3 visualization.

    Reads the active preset from pipeline-config.json, enriches each role with
    tier and compatible-figure data from role-taxonomy.yaml, and returns a nested
    ``OrgTreeNode`` tree.  The root node is the preset itself; children are the
    tier groups (leadership / workers); each tier's ``roles`` list holds the role
    cards rendered by ``org_chart_tree.js``.

    Returns HTTP 404 when no preset is selected or the active preset id is unknown.
    """
    config = _read_pipeline_config()
    active_org: object = config.get("active_org")
    if not isinstance(active_org, str) or not active_org:
        raise HTTPException(status_code=404, detail="No active org selected. Choose a preset first.")

    presets = _load_presets()
    preset = next((p for p in presets if p.get("id") == active_org), None)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Active preset {active_org!r} not found in org-presets.yaml")

    role_index = _load_taxonomy_role_index()

    def _make_role(slug: str) -> OrgTreeRole:
        meta: _RoleIndexEntry = role_index.get(slug, _RoleIndexEntry(tier="Worker", compatible_figures=[], label=slug, title=slug))
        figures: list[str] = meta.get("compatible_figures", [])
        return OrgTreeRole(
            slug=slug,
            name=meta.get("label", slug),
            tier=meta.get("tier", "Worker"),
            assigned_phases=[],
            figures=figures[:2],
        )

    tiers_raw: object = preset.get("tiers", {})
    if not isinstance(tiers_raw, dict):
        tiers_raw = {}

    tier_children: list[OrgTreeNode] = []
    tier_display = {"leadership": "Leadership", "workers": "Workers"}
    for tier_key in ("leadership", "workers"):
        slugs: object = tiers_raw.get(tier_key, [])
        if not isinstance(slugs, list):
            continue
        roles = [_make_role(str(s)) for s in slugs]
        tier_children.append(
            OrgTreeNode(
                name=tier_display.get(tier_key, tier_key.title()),
                id=tier_key,
                tier=tier_key,
                roles=roles,
                children=[],
            )
        )

    root = OrgTreeNode(
        name=str(preset.get("name", active_org)),
        id=active_org,
        tier="org",
        roles=[],
        children=tier_children,
    )

    logger.info("✅ /api/org/tree built for preset %r (%d tiers)", active_org, len(tier_children))
    return JSONResponse(content=root.model_dump())


@router.post("/api/org/select-preset", response_class=HTMLResponse)
async def select_preset(
    request: Request,
    preset_id: str = Form(...),
) -> HTMLResponse:
    """Persist the chosen preset to pipeline-config.json and return a refreshed partial.

    Called by HTMX when the user clicks a preset card.  Writes ``active_org``
    to ``pipeline-config.json`` and swaps the left panel so the selected card
    gets the ``active`` CSS class without a full-page reload.

    Returns HTTP 422 when *preset_id* does not match any known preset so HTMX
    can surface the error in the toast system.
    """
    presets = _load_presets()
    preset_ids = {p["id"] for p in presets if "id" in p}

    if preset_id not in preset_ids:
        logger.warning("⚠️ Unknown preset_id requested: %s", preset_id)
        raise HTTPException(status_code=422, detail=f"Unknown preset: {preset_id!r}")

    config = _read_pipeline_config()
    config["active_org"] = preset_id
    try:
        _write_pipeline_config(config)
        logger.info("✅ Active org set to %r", preset_id)
    except Exception as exc:
        logger.error("❌ Failed to write pipeline-config.json: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to persist preset selection") from exc

    return _TEMPLATES.TemplateResponse(
        request,
        "_org_preset_list.html",
        {
            "presets": presets,
            "active_org_id": preset_id,
        },
    )
