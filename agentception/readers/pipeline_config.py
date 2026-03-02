"""Pipeline configuration reader/writer for AgentCeption.

The canonical source of truth for pipeline allocation parameters is
``.cursor/pipeline-config.json`` in the repository root.  The CTO and
Engineering VP role files read this file at the start of every loop/seed
cycle instead of relying on hardcoded constants.

The dashboard exposes GET/PUT ``/api/config`` routes (see
``agentception/routes/api.py``) so operators can adjust allocation without
restarting the service.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentception.config import settings

# Default values mirror the spec exactly — used when the file is absent.
_DEFAULTS: dict[str, Any] = {
    "max_eng_vps": 1,
    "max_qa_vps": 1,
    "pool_size_per_vp": 4,
    "active_labels_order": [
        "agentception/0-scaffold",
        "agentception/1-controls",
        "agentception/2-telemetry",
        "agentception/3-roles",
        "agentception/4-intelligence",
        "agentception/5-scaling",
        "agentception/6-generalization",
    ],
}

_CONFIG_PATH: Path = settings.repo_dir / ".cursor" / "pipeline-config.json"


def _config_path() -> Path:
    """Return the resolved path to ``pipeline-config.json``.

    Factored out so tests can patch ``_config_path`` without touching the
    module-level constant (which is evaluated at import time and therefore
    hard to redirect in unit tests).
    """
    return _CONFIG_PATH


async def read_pipeline_config() -> dict[str, Any]:
    """Read and return the pipeline configuration from disk.

    Falls back to :data:`_DEFAULTS` when the config file does not exist yet,
    giving callers a safe, fully-typed dict in all environments.

    Returns
    -------
    dict
        Keys: ``max_eng_vps`` (int), ``max_qa_vps`` (int),
        ``pool_size_per_vp`` (int), ``active_labels_order`` (list[str]).
    """
    path = _config_path()
    if not path.exists():
        return dict(_DEFAULTS)
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


async def write_pipeline_config(config: dict[str, Any]) -> dict[str, Any]:
    """Persist *config* to disk and return the saved value.

    The file is written with 2-space indentation so it remains human-readable.
    The parent directory is created automatically when it does not exist.

    Parameters
    ----------
    config:
        Mapping to persist.  Callers are responsible for schema validation
        before calling this function (the ``PUT /api/config`` route validates
        via Pydantic).

    Returns
    -------
    dict
        The config dict that was written, identical to *config*.
    """
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config
