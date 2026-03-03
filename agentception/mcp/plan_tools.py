"""AgentCeption MCP plan tools — schema inspection and spec validation.

Provides two MCP-exposed functions:

``plan_get_schema()``
    Returns the JSON Schema for :class:`~agentception.models.PlanSpec`.
    The schema is generated from the Pydantic model at call time and cached
    for the process lifetime so repeated ``tools/call`` invocations are fast.

``plan_validate_spec(spec_json)``
    Parses a JSON string and validates it against :class:`~agentception.models.PlanSpec`.
    Returns a structured result dict indicating success or failure with
    human-readable error messages.

Both functions are pure (no side effects beyond the schema cache) and
synchronous — they perform no I/O and must never block the event loop.

Boundary constraint: zero imports from maestro, muse, kly, or storpheus.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from agentception.models import PlanSpec

logger = logging.getLogger(__name__)

# Module-level cache: populated on the first call to plan_get_schema().
_schema_cache: dict[str, object] | None = None


def plan_get_schema() -> dict[str, object]:
    """Return the JSON Schema for PlanSpec.

    The schema is generated once from the Pydantic model and cached for the
    process lifetime.  Callers receive a reference to the cached dict — do
    not mutate it.

    Returns:
        A ``dict[str, object]`` containing the full JSON Schema for
        :class:`~agentception.models.PlanSpec`, including all nested
        definitions for ``PlanPhase`` and ``PlanIssue``.
    """
    global _schema_cache
    if _schema_cache is None:
        raw: dict[str, object] = PlanSpec.model_json_schema()
        _schema_cache = raw
        logger.debug("✅ PlanSpec JSON schema generated and cached")
    return _schema_cache


def plan_validate_spec(spec_json: str) -> dict[str, object]:
    """Validate a JSON string against the PlanSpec schema.

    Parses ``spec_json`` as JSON and attempts to construct a
    :class:`~agentception.models.PlanSpec` from the parsed data.
    Pydantic's full validation stack runs — including the phase DAG
    invariant checker — so any structural or semantic error is reported.

    Args:
        spec_json: A UTF-8 JSON string expected to represent a PlanSpec.

    Returns:
        On success: ``{"valid": True, "spec": <serialised PlanSpec dict>}``
        On JSON parse failure: ``{"valid": False, "errors": ["JSON parse error: ..."]}``
        On Pydantic validation failure: ``{"valid": False, "errors": [<list of error strings>]}``

    Never raises — all errors are captured and returned in the result dict
    so that the MCP caller receives a well-formed tool result in every case.
    """
    try:
        raw: object = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        logger.warning("⚠️ plan_validate_spec: JSON parse error — %s", exc)
        return {"valid": False, "errors": [f"JSON parse error: {exc}"]}

    try:
        spec = PlanSpec.model_validate(raw)
    except ValidationError as exc:
        errors: list[str] = [
            f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        ]
        logger.info("ℹ️ plan_validate_spec: validation failed — %d error(s)", len(errors))
        return {"valid": False, "errors": errors}
    except Exception as exc:
        logger.warning("⚠️ plan_validate_spec: unexpected error — %s", exc)
        return {"valid": False, "errors": [f"Validation error: {exc}"]}

    logger.debug("✅ plan_validate_spec: spec is valid")
    return {"valid": True, "spec": spec.model_dump()}
