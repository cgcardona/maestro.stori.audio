"""Structured prompt parser for Maestro.

Format: a sentinel header line followed by a YAML document.

    MAESTRO PROMPT
    <YAML body>

The sentinel ``MAESTRO PROMPT`` (exact, case-sensitive) is the trigger.
Everything after it must be valid YAML.  If YAML parsing fails the prompt
is treated as natural language and the NL pipeline handles it.

Routing fields (parsed deterministically by Python):
    Mode, Section, Position, Target, Style, Key, Tempo, Role, Constraints,
    Vibe, Request

Maestro dimensions (all other top-level keys):
    Harmony, Melody, Rhythm, Dynamics, Orchestration, Effects, Expression,
    Texture, Form, Automation, … and any future fields.

    These land in MaestroPrompt.extensions and are injected verbatim into
    the Maestro LLM system prompt as YAML.  The vocabulary is open — invent
    new dimensions and they work immediately.

See docs/protocol/maestro_prompt_spec.md for the full specification.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from maestro.contracts.json_types import JSONValue, jint
from maestro.prompts.base import (
    AfterSpec,
    PositionSpec,
    PromptConstraints,
    TargetSpec,
    VibeWeight,
)
from maestro.prompts.errors import InvalidMaestroPrompt, UnsupportedPromptHeader
from maestro.prompts.maestro import MaestroPrompt

import yaml  # PyYAML ships no py.typed marker

logger = logging.getLogger(__name__)

# ─── Sentinels ────────────────────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^\s*MAESTRO PROMPT\s*$")
_LEGACY_HEADER_RE = re.compile(r"^\s*STORI\s+PROMPT\s*$", re.IGNORECASE)

# ─── Routing field set (lowercase) ───────────────────────────────────────────

_ROUTING_FIELDS = frozenset({
    "mode", "section", "position", "after",
    "target", "style", "key", "tempo", "energy",
    "role", "constraints", "vibe", "request",
})

# ─── Regexes ─────────────────────────────────────────────────────────────────

_TEMPO_RE = re.compile(r"(\d+)\s*(bpm)?", re.IGNORECASE)
_VIBE_X_WEIGHT_RE = re.compile(r"^(.+?)\s+x(\d+)\s*$", re.IGNORECASE)
_VIBE_COLON_WEIGHT_RE = re.compile(r"^(.+?):(\d+)\s*$")
_LIST_BULLET_RE = re.compile(r"^\s*-\s+")

# Position parsing
_POS_ABSOLUTE_RE = re.compile(
    r"^(?:at\s+)?(?:beat\s+)?(\d+(?:\.\d+)?)\s*$", re.IGNORECASE,
)
_POS_BAR_RE = re.compile(r"^(?:at\s+)?bar\s+(\d+)\s*$", re.IGNORECASE)
_POS_OFFSET_RE = re.compile(
    r"([+-])\s*(\d+(?:\.\d+)?)\s*(?:beats?|bars?)?\s*$", re.IGNORECASE,
)
_POS_KEYWORDS = ("after", "before", "alongside", "between", "within", "last")


def _as_mode(raw: str) -> Literal["compose", "edit", "ask"]:
    """Narrow a pre-validated mode string to the mode Literal type.

    Callers **must** have already confirmed ``raw.lower()`` is in
    ``("compose", "edit", "ask")`` before calling this; the fallthrough
    returns ``"compose"`` as the default mode.
    """
    lower = raw.lower()
    if lower == "edit":
        return "edit"
    if lower == "ask":
        return "ask"
    return "compose"


# ─── Public API ───────────────────────────────────────────────────────────────


def parse_prompt(text: str) -> MaestroPrompt | None:
    """Parse a Maestro Structured Prompt.

    Returns ``MaestroPrompt`` on success, ``None`` when the text is natural
    language (no header found).

    Raises:
        UnsupportedPromptHeader: if the header is the legacy ``STORI PROMPT``.
        InvalidMaestroPrompt: if the header is valid but YAML/fields are not.
    """
    if not text or not text.strip():
        return None

    # Strip leading BOM (U+FEFF) — the sanitizer does this too, but
    # belt-and-suspenders for callers that bypass sanitisation.
    cleaned = text.lstrip("\ufeff")
    lines = cleaned.strip().splitlines()

    # Sentinel must be the first non-empty line
    header_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            if _HEADER_RE.match(stripped):
                header_idx = i
            elif _LEGACY_HEADER_RE.match(stripped):
                raise UnsupportedPromptHeader("STORI PROMPT")
            break

    if header_idx < 0:
        return None

    body = "\n".join(lines[header_idx + 1:])

    try:
        raw_data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        logger.debug("Maestro Prompt YAML parse failed — treating as NL: %s", exc)
        return None

    if not isinstance(raw_data, dict):
        return None

    # Normalise all top-level keys to lowercase
    data: dict[str, JSONValue] = {str(k).lower(): v for k, v in raw_data.items()}

    # Required: Mode
    mode_raw = _str(data.get("mode"))
    if not mode_raw or mode_raw.lower() not in ("compose", "edit", "ask"):
        return None

    # Request: required for edit/ask, optional for compose (synthesized)
    request_val = _str(data.get("request"))
    if not request_val:
        if mode_raw.lower() == "compose":
            parts: list[str] = []
            if data.get("style"):
                parts.append(str(data["style"]))
            if data.get("role"):
                roles_raw = data["role"]
                if isinstance(roles_raw, list):
                    parts.append(", ".join(str(r) for r in roles_raw))
                else:
                    parts.append(str(roles_raw))
            request_val = f"Compose {' '.join(parts)}".strip() if parts else "Compose music"
        else:
            return None

    # Position wins over After when both present
    position = (
        _parse_position(_str(data.get("position")))
        or _parse_position(_str(data.get("after")), after_alias=True)
    )

    extensions = {k: v for k, v in data.items() if k not in _ROUTING_FIELDS}

    return MaestroPrompt(
        raw=text,
        mode=_as_mode(mode_raw),
        request=request_val,
        section=_str(data.get("section"), lower=True),
        position=position,
        target=_parse_target(_str(data.get("target"))),
        style=_str(data.get("style")),
        key=_str(data.get("key")),
        tempo=_parse_tempo(data.get("tempo")),
        energy=_str(data.get("energy"), lower=True),
        roles=_parse_roles(data.get("role")),
        constraints=_parse_constraints(data.get("constraints")),
        vibes=_parse_vibes(data.get("vibe")),
        extensions=extensions,
    )


# ─── Field parsers ────────────────────────────────────────────────────────────


def _str(v: JSONValue, lower: bool = False) -> str | None:
    """Coerce a YAML scalar to a stripped string, or None."""
    if v is None:
        return None
    s = str(v).strip()
    return (s.lower() if lower else s) or None


def _parse_target(val: str | None) -> TargetSpec | None:
    if not val:
        return None
    v = val.lower().strip()
    if v == "project":
        return TargetSpec(kind="project")
    if v == "selection":
        return TargetSpec(kind="selection")
    for kind in ("track", "region"):
        if v.startswith(f"{kind}:"):
            name = val[len(kind) + 1:].strip()
            return TargetSpec(kind=kind, name=name or None)
    return None


def _parse_tempo(v: JSONValue) -> int | None:
    """Accept integer, float, or string like '92 bpm'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = _TEMPO_RE.search(str(v))
    return int(m.group(1)) if m else None


def _parse_roles(v: JSONValue) -> list[str]:
    """Role: string | list[str]."""
    if v is None:
        return []
    if isinstance(v, list):
        return [_LIST_BULLET_RE.sub("", str(i)).strip() for i in v if str(i).strip()]
    return [p.strip() for p in str(v).split(",") if p.strip()]


def _parse_constraints(v: JSONValue) -> PromptConstraints:
    """Constraints: dict | list of {k: v} dicts | string."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return {str(k).lower(): val for k, val in v.items()}
    if isinstance(v, list):
        out: PromptConstraints = {}
        for item in v:
            if isinstance(item, dict):
                for k, val in item.items():
                    out[str(k).lower()] = val
            elif isinstance(item, str):
                s = _LIST_BULLET_RE.sub("", item).strip()
                if ":" in s:
                    k, _, val = s.partition(":")
                    out[k.strip().lower()] = _coerce(val.strip())
                elif s:
                    out[s.lower()] = True
        return out
    return {}


def _parse_vibes(v: JSONValue) -> list[VibeWeight]:
    """Vibe: string | list[str | {name: weight}].

    Weight syntax (in string items):
      "dusty x3"    → VibeWeight("dusty", 3)    — readable shorthand
      "dusty:3"     → VibeWeight("dusty", 3)    — colon, no space
      {"dusty": 3}  → VibeWeight("dusty", 3)    — YAML dict
    """
    if v is None:
        return []

    raw: list[JSONValue] = []
    if isinstance(v, list):
        raw = v
    elif isinstance(v, str):
        raw = [p.strip() for p in v.split(",") if p.strip()]
    else:
        raw = [v]

    vibes: list[VibeWeight] = []
    for item in raw:
        if isinstance(item, dict):
            for name, weight in item.items():
                vibes.append(VibeWeight(vibe=str(name).strip().lower(), weight=jint(weight)))
            continue
        s = _LIST_BULLET_RE.sub("", str(item)).strip()
        if not s:
            continue
        m = _VIBE_X_WEIGHT_RE.match(s)
        if m:
            vibes.append(VibeWeight(vibe=m.group(1).strip().lower(), weight=int(m.group(2))))
            continue
        m2 = _VIBE_COLON_WEIGHT_RE.match(s)
        if m2:
            vibes.append(VibeWeight(vibe=m2.group(1).strip().lower(), weight=int(m2.group(2))))
            continue
        vibes.append(VibeWeight(vibe=s.lower()))
    return vibes


def _parse_position(val: str | None, after_alias: bool = False) -> PositionSpec | None:
    if not val:
        return None
    val = val.strip()

    m = _POS_ABSOLUTE_RE.match(val)
    if m:
        return PositionSpec(kind="absolute", beat=float(m.group(1)))

    m = _POS_BAR_RE.match(val)
    if m:
        return PositionSpec(kind="absolute", beat=float((int(m.group(1)) - 1) * 4))

    if val.lower() == "last":
        return PositionSpec(kind="last")

    offset = 0.0
    rest = val
    om = _POS_OFFSET_RE.search(rest)
    if om:
        offset = (1.0 if om.group(1) == "+" else -1.0) * float(om.group(2))
        rest = rest[: om.start()].strip()

    rest_lower = rest.lower()
    for kw in _POS_KEYWORDS:
        if rest_lower.startswith(kw):
            remainder = rest[len(kw):].strip()
            if kw == "last":
                return PositionSpec(kind="last", offset=offset)
            if kw == "between":
                parts = remainder.split()
                return PositionSpec(
                    kind="between",
                    ref=parts[0].lower() if parts else None,
                    ref2=parts[1].lower() if len(parts) > 1 else None,
                    offset=offset,
                )
            if kw == "within":
                parts = remainder.split()
                ref = parts[0].lower() if parts else None
                bar_off = 0.0
                if len(parts) >= 3 and parts[1].lower() == "bar":
                    try:
                        bar_off = (int(parts[2]) - 1) * 4.0
                    except ValueError:
                        pass
                return PositionSpec(kind="within", ref=ref, offset=offset + bar_off)
            if kw == "after":
                return PositionSpec(kind="after", ref=remainder.lower() or None, offset=offset)
            if kw == "before":
                return PositionSpec(kind="before", ref=remainder.lower() or None, offset=offset)
            return PositionSpec(kind="alongside", ref=remainder.lower() or None, offset=offset)

    if after_alias:
        return PositionSpec(kind="after", ref=val.lower(), offset=offset)
    return None


def _coerce(v: str) -> int | float | str:
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


__all__ = [
    "parse_prompt",
    "AfterSpec",
]
