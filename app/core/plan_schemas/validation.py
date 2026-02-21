"""Plan JSON extraction and validation from LLM responses."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.plan_schemas.models import ExecutionPlanSchema, PlanValidationResult

logger = logging.getLogger(__name__)


def validate_plan_json(raw_json: dict[str, Any]) -> PlanValidationResult:
    """Validate a raw JSON dict against the execution plan schema."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        plan = ExecutionPlanSchema.model_validate(raw_json)
        if plan.is_empty():
            warnings.append("Plan is empty - no actions to execute")
        return PlanValidationResult(valid=True, plan=plan, errors=[], warnings=warnings, raw_json=raw_json)

    except Exception as e:
        if hasattr(e, 'errors'):
            for err in e.errors():
                loc = ' → '.join(str(x) for x in err.get('loc', []))
                msg = err.get('msg', 'Unknown error')
                errors.append(f"{loc}: {msg}")
        else:
            errors.append(str(e))

        return PlanValidationResult(valid=False, plan=None, errors=errors, warnings=warnings, raw_json=raw_json)


def extract_and_validate_plan(llm_response: str) -> PlanValidationResult:
    """
    Extract JSON from an LLM response and validate it as an execution plan.

    Handles: pure JSON, markdown code fences, JSON with surrounding text,
    multiple JSON objects, and common JSON formatting issues from LLMs.
    """
    if not llm_response or not llm_response.strip():
        return PlanValidationResult(valid=False, errors=["Empty LLM response"], raw_json=None)

    text = llm_response.strip()

    # Strategy 1: markdown code fence extraction
    for pattern in [r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```']:
        for match in re.findall(pattern, text, re.IGNORECASE):
            try:
                raw_json = json.loads(match.strip())
                if isinstance(raw_json, dict):
                    return validate_plan_json(raw_json)
            except json.JSONDecodeError:
                continue

    # Strategy 2: brace-matched JSON candidates
    for candidate in _extract_json_candidates(text):
        try:
            raw_json = json.loads(candidate)
            if isinstance(raw_json, dict) and _looks_like_plan(raw_json):
                return validate_plan_json(raw_json)
        except json.JSONDecodeError:
            continue

    # Strategy 3: outermost braces with light repair
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = _fix_common_json_issues(text[start:end + 1])
        try:
            raw_json = json.loads(json_str)
            if isinstance(raw_json, dict):
                return validate_plan_json(raw_json)
        except json.JSONDecodeError as e:
            return PlanValidationResult(valid=False, errors=[f"Invalid JSON after extraction: {e}"], raw_json=None)

    return PlanValidationResult(valid=False, errors=["No valid JSON object found in LLM response"], raw_json=None)


def _extract_json_candidates(text: str) -> list[str]:
    """Extract all potential JSON object strings from text using brace matching."""
    candidates: list[str] = []
    i = 0

    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            in_string = False
            escape_next = False

            for j in range(i, len(text)):
                char = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            candidates.append(text[start:j + 1])
                            i = j
                            break
        i += 1

    return candidates


def _looks_like_plan(obj: dict) -> bool:
    """Check whether a dict looks like an execution plan."""
    plan_keys = {"generations", "edits", "mix"}
    if set(obj.keys()) & plan_keys:
        return True
    for value in obj.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if "role" in value[0] or "action" in value[0]:
                return True
    return False


def _fix_common_json_issues(json_str: str) -> str:
    """Fix trailing commas before } or ] in LLM-generated JSON."""
    return re.sub(r',\s*([}\]])', r'\1', json_str)
