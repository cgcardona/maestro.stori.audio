"""UI routes: Org Chart page with preset org picker.

Provides:
- ``GET /org-chart`` — full-page render of the org chart with a left-panel
  preset picker and a right-panel shell (``#org-right-panel``) reserved for
  the interactive builder (#829) and D3 tree (#830).
- ``POST /api/org/select-preset`` — HTMX endpoint that persists the chosen
  preset to ``pipeline-config.json`` under the ``active_org`` key and returns
  a refreshed left-panel partial so the active card updates in-place.

Preset definitions are loaded from ``org-presets.yaml`` at the repo root.
The file is read once per request (fast — ~1 KB) so hot-editing the YAML
during development takes effect without a service restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from agentception.config import settings
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
