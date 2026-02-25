"""
Structured prompt parser for Stori Maestro.

Format: a sentinel header line followed by a YAML document.

    STORI PROMPT
    <YAML body>

The sentinel "STORI PROMPT" (case-insensitive) is the trigger. Everything
after it must be valid YAML. If YAML parsing fails the prompt is treated as
natural language and the NL pipeline handles it — no fallback, no guessing.

Routing fields (parsed deterministically by Python):
    Mode, Section, Position, Target, Style, Key, Tempo, Role, Constraints,
    Vibe, Request

Maestro dimensions (all other top-level keys):
    Harmony, Melody, Rhythm, Dynamics, Orchestration, Effects, Expression,
    Texture, Form, Automation, … and any future fields.

    These land in ParsedPrompt.extensions and are injected verbatim into the
    Maestro LLM system prompt as YAML. The vocabulary is open — invent new
    dimensions and they work immediately.

See docs/protocol/stori-prompt-spec.md for the full specification.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

# ─── Sentinel ────────────────────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^\s*stori\s+prompt\s*$", re.IGNORECASE)

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


# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class TargetSpec:
    kind: Literal["project", "selection", "track", "region"]
    name: str | None = None


@dataclass
class PositionSpec:
    """Arrangement placement.

    kind        description
    ──────────  ────────────────────────────────────────────────────────────
    after       sequential — start after ref section ends
    before      insert / pickup — start before ref section begins
    alongside   parallel layer — same start beat as ref
    between     transition bridge — fills gap between ref and ref2
    within      nested — relative offset inside ref
    absolute    explicit beat number
    last        after all existing content in the project
    """
    kind: Literal["after", "before", "alongside", "between", "within", "absolute", "last"]
    ref: str | None = None
    ref2: str | None = None
    offset: float = 0.0
    beat: float | None = None


# Backwards-compatible alias
AfterSpec = PositionSpec


@dataclass
class VibeWeight:
    vibe: str
    weight: int = 1


@dataclass
class ParsedPrompt:
    """Parsed Stori Structured Prompt.

    Routing fields are typed attributes. All other top-level YAML keys land in
    ``extensions`` and are injected verbatim into the Maestro LLM system prompt.
    """
    raw: str
    mode: Literal["compose", "edit", "ask"]
    request: str
    section: str | None = None
    position: PositionSpec | None = None
    target: TargetSpec | None = None
    style: str | None = None
    key: str | None = None
    tempo: int | None = None
    energy: str | None = None
    roles: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    vibes: list[VibeWeight] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    @property
    def after(self) -> PositionSpec | None:
        """Backwards-compatible alias for position."""
        return self.position

    @property
    def has_maestro_fields(self) -> bool:
        return bool(self.extensions)


# ─── Public API ───────────────────────────────────────────────────────────────


def parse_prompt(text: str) -> ParsedPrompt | None:
    """Parse a Stori Structured Prompt.

    Returns ParsedPrompt on success, None to fall through to the NL pipeline.

    The body after the sentinel must be valid YAML. Invalid YAML → None.
    """
    if not text or not text.strip():
        return None

    lines = text.strip().splitlines()

    # Sentinel must be the first non-empty line
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip():
            if _HEADER_RE.match(line.strip()):
                header_idx = i
            break

    if header_idx < 0:
        return None

    body = "\n".join(lines[header_idx + 1:])

    try:
        raw_data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        logger.debug("Stori Prompt YAML parse failed — treating as NL: %s", exc)
        return None

    if not isinstance(raw_data, dict):
        return None

    # Normalise all top-level keys to lowercase
    data: dict[str, Any] = {str(k).lower(): v for k, v in raw_data.items()}

    # Required: Mode
    mode_raw = _str(data.get("mode"))
    if not mode_raw or mode_raw.lower() not in ("compose", "edit", "ask"):
        return None

    # Request: required for edit/ask, optional for compose (synthesized from dimensions)
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

    return ParsedPrompt(
        raw=text,
        mode=mode_raw.lower(),  # type: ignore[arg-type]
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


def _str(v: Any, lower: bool = False) -> str | None:
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


def _parse_tempo(v: Any) -> int | None:
    """Accept integer, float, or string like '92 bpm'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = _TEMPO_RE.search(str(v))
    return int(m.group(1)) if m else None


def _parse_roles(v: Any) -> list[str]:
    """Role: string | list[str]."""
    if v is None:
        return []
    if isinstance(v, list):
        return [_LIST_BULLET_RE.sub("", str(i)).strip() for i in v if str(i).strip()]
    # Inline comma-separated string
    return [p.strip() for p in str(v).split(",") if p.strip()]


def _parse_constraints(v: Any) -> dict[str, Any]:
    """Constraints: dict | list of {k: v} dicts | string."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return {str(k).lower(): val for k, val in v.items()}
    if isinstance(v, list):
        out: dict[str, Any] = {}
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


def _parse_vibes(v: Any) -> list[VibeWeight]:
    """Vibe: string | list[str | {name: weight}].

    Weight syntax (in string items):
      "dusty x3"    → VibeWeight("dusty", 3)    — readable shorthand
      "dusty:3"     → VibeWeight("dusty", 3)    — colon, no space
      {"dusty": 3}  → VibeWeight("dusty", 3)    — YAML dict
    """
    if v is None:
        return []

    raw: list[Any] = []
    if isinstance(v, list):
        raw = v
    elif isinstance(v, str):
        raw = [p.strip() for p in v.split(",") if p.strip()]
    else:
        raw = [v]

    vibes: list[VibeWeight] = []
    for item in raw:
        if isinstance(item, dict):
            # {name: weight} form
            for name, weight in item.items():
                vibes.append(VibeWeight(vibe=str(name).strip().lower(), weight=int(weight)))
            continue
        s = _LIST_BULLET_RE.sub("", str(item)).strip()
        if not s:
            continue
        # "dusty x3"
        m = _VIBE_X_WEIGHT_RE.match(s)
        if m:
            vibes.append(VibeWeight(vibe=m.group(1).strip().lower(), weight=int(m.group(2))))
            continue
        # "dusty:3" (no space around colon)
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
            kind_val: Literal["after", "before", "alongside", "between", "within", "absolute", "last"] = kw  # type: ignore[assignment]  # validated by _POSITION_KEYWORDS
            return PositionSpec(kind=kind_val, ref=remainder.lower() or None, offset=offset)

    if after_alias:
        return PositionSpec(kind="after", ref=val.lower(), offset=offset)
    return None


def _coerce(v: str) -> Any:
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v
