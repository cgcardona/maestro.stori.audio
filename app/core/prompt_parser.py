"""
Structured prompt parser for Stori Composer.

Detects and extracts structured fields from the Stori Prompt format.
See docs/protocol/stori-prompt-spec.md for the full specification.

This is a purely additive fast path. If the prompt is not in the structured
format, parse_prompt() returns None and the existing NL pipeline handles it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# Known field headers (lowercase) in declaration order.
# "request" is always last and consumes everything remaining.
_FIELD_NAMES = (
    "mode",
    "target",
    "style",
    "key",
    "tempo",
    "role",
    "constraints",
    "vibe",
    "request",
)

_HEADER_RE = re.compile(r"^\s*stori\s+prompt\s*$", re.IGNORECASE)

# Matches a field header line: "FieldName:" with optional inline value
_FIELD_RE = re.compile(
    r"^(" + "|".join(_FIELD_NAMES) + r")\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)

_VALID_MODES = frozenset({"compose", "edit", "ask"})
_VALID_TARGET_KINDS: set[str] = {"project", "selection", "track", "region"}

_TEMPO_RE = re.compile(r"(\d+)\s*(bpm)?", re.IGNORECASE)
_VIBE_WEIGHT_RE = re.compile(r"^(.+?)\s*:\s*(\d+)\s*$")
_LIST_BULLET_RE = re.compile(r"^\s*-\s+")


@dataclass
class TargetSpec:
    """Parsed Target field."""
    kind: Literal["project", "selection", "track", "region"]
    name: Optional[str] = None


@dataclass
class VibeWeight:
    """A single vibe entry with optional weight."""
    vibe: str
    weight: int = 1


@dataclass
class ParsedPrompt:
    """Result of successfully parsing a Stori structured prompt."""
    raw: str
    mode: Literal["compose", "edit", "ask"]
    request: str
    target: Optional[TargetSpec] = None
    style: Optional[str] = None
    key: Optional[str] = None
    tempo: Optional[int] = None
    roles: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    vibes: list[VibeWeight] = field(default_factory=list)


# ─── Public API ──────────────────────────────────────────────────────────────


def parse_prompt(text: str) -> Optional[ParsedPrompt]:
    """
    Parse a Stori structured prompt from raw text.

    Returns ParsedPrompt if the text is a valid structured prompt, or None
    if it is not (allowing the caller to fall through to the NL pipeline).

    A valid structured prompt must:
    - Begin with the header line "STORI PROMPT" (case-insensitive)
    - Contain at least Mode and Request fields
    - Have a valid Mode value (compose | edit | ask)
    """
    if not text or not text.strip():
        return None

    lines = text.strip().splitlines()

    # Header detection — must be the first non-empty line
    first_line = ""
    first_line_idx = 0
    for i, line in enumerate(lines):
        if line.strip():
            first_line = line.strip()
            first_line_idx = i
            break

    if not _HEADER_RE.match(first_line):
        return None

    # Collect field blocks: {field_name_lower: [content_lines]}
    field_blocks = _extract_field_blocks(lines[first_line_idx + 1:])

    # Mode is required
    mode_val = _single_value(field_blocks.get("mode"))
    if mode_val is None:
        return None
    mode_val = mode_val.lower()
    if mode_val not in _VALID_MODES:
        return None

    # Request is required
    request_val = _block_text(field_blocks.get("request"))
    if not request_val:
        return None

    return ParsedPrompt(
        raw=text,
        mode=mode_val,  # type: ignore[arg-type]
        request=request_val,
        target=_parse_target(field_blocks.get("target")),
        style=_single_value(field_blocks.get("style")),
        key=_single_value(field_blocks.get("key")),
        tempo=_parse_tempo(field_blocks.get("tempo")),
        roles=_parse_list(field_blocks.get("role")),
        constraints=_parse_constraints(field_blocks.get("constraints")),
        vibes=_parse_vibes(field_blocks.get("vibe")),
    )


# ─── Field block extraction ─────────────────────────────────────────────────


def _extract_field_blocks(lines: list[str]) -> dict[str, list[str]]:
    """
    Walk lines after the header and group them by field.

    Each field header ("Mode:", "Target:", etc.) starts a new block.
    Lines before any field header are ignored. "Request:" is special:
    it captures everything from its header to the end of input.
    """
    blocks: dict[str, list[str]] = {}
    current_field: Optional[str] = None
    current_lines: list[str] = []

    for line in lines:
        m = _FIELD_RE.match(line)
        if m:
            # Save previous block
            if current_field is not None:
                blocks[current_field] = current_lines

            current_field = m.group(1).lower()
            inline_value = m.group(2)
            current_lines = [inline_value] if inline_value else []

            # "request" consumes everything remaining, but we still
            # collect line-by-line so multi-line requests work.
            if current_field == "request":
                continue
        else:
            if current_field is not None:
                current_lines.append(line)

    # Save last block
    if current_field is not None:
        blocks[current_field] = current_lines

    return blocks


# ─── Per-field parsers ───────────────────────────────────────────────────────


def _single_value(lines: Optional[list[str]]) -> Optional[str]:
    """Extract a single-line scalar value, stripping whitespace."""
    if not lines:
        return None
    combined = " ".join(l.strip() for l in lines if l.strip())
    return combined if combined else None


def _block_text(lines: Optional[list[str]]) -> Optional[str]:
    """Join lines preserving newlines (for Request field)."""
    if not lines:
        return None
    text = "\n".join(lines).strip()
    return text if text else None


def _parse_target(lines: Optional[list[str]]) -> Optional[TargetSpec]:
    """Parse Target field: project | selection | track:<name> | region:<name>."""
    val = _single_value(lines)
    if not val:
        return None

    val_lower = val.lower().strip()

    if val_lower == "project":
        return TargetSpec(kind="project")
    if val_lower == "selection":
        return TargetSpec(kind="selection")

    # track:<name> or region:<name>
    for kind in ("track", "region"):
        prefix = f"{kind}:"
        if val_lower.startswith(prefix):
            name = val[len(prefix):].strip()
            if name:
                return TargetSpec(kind=kind, name=name)  # type: ignore[arg-type]

    return None


def _parse_tempo(lines: Optional[list[str]]) -> Optional[int]:
    """Parse Tempo field: "126", "126 bpm", "126bpm"."""
    val = _single_value(lines)
    if not val:
        return None
    m = _TEMPO_RE.search(val)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_list(lines: Optional[list[str]]) -> list[str]:
    """
    Parse a list field that supports:
    - Inline comma-separated: "kick, bass, arp"
    - YAML-style bullets: "- kick\\n- bass\\n- arp"
    - Mix of both
    """
    if not lines:
        return []

    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Remove leading bullet
        stripped = _LIST_BULLET_RE.sub("", stripped).strip()
        if not stripped:
            continue

        # Split by commas
        parts = [p.strip() for p in stripped.split(",")]
        items.extend(p for p in parts if p)

    return items


def _parse_constraints(lines: Optional[list[str]]) -> dict[str, Any]:
    """
    Parse Constraints field into a dict.

    Supports:
    - "key: value" pairs (YAML-style)
    - Bare items without colon become flags: {"no reverb": True}
    """
    if not lines:
        return {}

    constraints: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Remove leading bullet
        stripped = _LIST_BULLET_RE.sub("", stripped).strip()
        if not stripped:
            continue

        # Try key: value split (only first colon)
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k and v:
                # Try to coerce numeric values
                constraints[k] = _coerce_value(v)
                continue

        # Bare item → flag
        constraints[stripped.lower()] = True

    return constraints


def _coerce_value(v: str) -> Any:
    """Try to coerce a string value to int, float, or leave as string."""
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _parse_vibes(lines: Optional[list[str]]) -> list[VibeWeight]:
    """
    Parse Vibe field into weighted entries.

    Supports:
    - "darker" → VibeWeight("darker", 1)
    - "darker:2" → VibeWeight("darker", 2)
    - Inline comma-separated or YAML-style lists
    """
    if not lines:
        return []

    vibes: list[VibeWeight] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Remove leading bullet
        stripped = _LIST_BULLET_RE.sub("", stripped).strip()
        if not stripped:
            continue

        # Split by commas for inline lists
        parts = [p.strip() for p in stripped.split(",")]
        for part in parts:
            if not part:
                continue

            m = _VIBE_WEIGHT_RE.match(part)
            if m:
                vibes.append(VibeWeight(
                    vibe=m.group(1).strip().lower(),
                    weight=int(m.group(2)),
                ))
            else:
                vibes.append(VibeWeight(vibe=part.strip().lower()))

    return vibes
