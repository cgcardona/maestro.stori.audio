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
    "section",
    "position",  # canonical field
    "after",     # backwards-compatible alias for position
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

# Position field parsing regexes
# "beat 32" or "at 32" or bare integer/float
_POS_ABSOLUTE_RE = re.compile(
    r"^(?:at\s+)?(?:beat\s+)?(\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)
# "at bar 9" → beat = (9-1)*4 (assumes 4/4; bar calculation is advisory)
_POS_BAR_RE = re.compile(r"^(?:at\s+)?bar\s+(\d+)\s*$", re.IGNORECASE)
# Offset suffix: "+ 4" or "- 4" (beats), may include "bars"
_POS_OFFSET_RE = re.compile(r"([+-])\s*(\d+(?:\.\d+)?)\s*(?:beats?|bars?)?\s*$", re.IGNORECASE)
# Relationship keywords in order of specificity
_POS_KEYWORDS = ("after", "before", "alongside", "between", "within", "last")


@dataclass
class TargetSpec:
    """Parsed Target field."""
    kind: Literal["project", "selection", "track", "region"]
    name: Optional[str] = None


@dataclass
class PositionSpec:
    """Parsed Position (or After) field — where new content sits in the timeline.

    Inspired by CSS pseudo-selectors: a relationship keyword, optional section
    references, and an optional beat offset combine to express any arrangement
    placement a maestro might need.

    Relationships:
      "after"     → start after reference section ends       (sequential append)
      "before"    → start before reference section begins    (insert / transition)
      "alongside" → start at the same beat as reference      (parallel layer)
      "between"   → fill the gap between two sections        (transition bridge)
      "within"    → relative offset inside a section         (nested placement)
      "absolute"  → explicit beat (no project scanning)
      "last"      → after everything currently in the project

    Offset is applied after reference resolution:
      positive → shift right (later)
      negative → shift left (earlier, e.g. pickup into chorus)

    Examples (all parsed from Position: field):
      Position: after intro           → PositionSpec("after",  "intro")
      Position: before chorus - 4    → PositionSpec("before", "chorus", offset=-4)
      Position: alongside verse + 8  → PositionSpec("alongside", "verse", offset=8)
      Position: between intro verse  → PositionSpec("between", "intro", ref2="verse")
      Position: within verse bar 3   → PositionSpec("within",  "verse", offset=8)  # bar3→beat8
      Position: at 32                → PositionSpec("absolute", beat=32)
      Position: last                 → PositionSpec("last")
    """
    kind: Literal["after", "before", "alongside", "between", "within", "absolute", "last"]
    ref: Optional[str] = None          # primary section name
    ref2: Optional[str] = None         # secondary section name (for "between")
    offset: float = 0.0                # beat offset (+/-)
    beat: Optional[float] = None       # for kind="absolute"


# Backwards-compatible alias used by earlier tests and existing code
AfterSpec = PositionSpec


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
    section: Optional[str] = None          # Section label for this prompt's output
    position: Optional[PositionSpec] = None  # Full arrangement positioning (Position: field)
    target: Optional[TargetSpec] = None
    style: Optional[str] = None
    key: Optional[str] = None
    tempo: Optional[int] = None
    roles: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    vibes: list[VibeWeight] = field(default_factory=list)

    @property
    def after(self) -> Optional[PositionSpec]:
        """Backwards-compatible alias for position."""
        return self.position


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

    # Position: is canonical; After: is a backwards-compatible alias.
    # If both are present, Position: wins.
    position = (
        _parse_position(field_blocks.get("position"))
        or _parse_position(field_blocks.get("after"), after_alias=True)
    )

    return ParsedPrompt(
        raw=text,
        mode=mode_val,  # type: ignore[arg-type]
        request=request_val,
        section=_parse_section(field_blocks.get("section")),
        position=position,
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


def _parse_section(lines: Optional[list[str]]) -> Optional[str]:
    """Parse Section field — returns a lowercase label like 'intro', 'verse'."""
    val = _single_value(lines)
    return val.lower().strip() if val else None


def _parse_position(
    lines: Optional[list[str]],
    after_alias: bool = False,
) -> Optional[PositionSpec]:
    """Parse Position: (or After: alias) into a PositionSpec.

    Supports the full arrangement positioning vocabulary:

      last                         → PositionSpec("last")
      after intro                  → PositionSpec("after", ref="intro")
      before chorus                → PositionSpec("before", ref="chorus")
      before chorus - 4            → PositionSpec("before", ref="chorus", offset=-4)
      after intro + 2              → PositionSpec("after",  ref="intro",  offset=2)
      alongside verse              → PositionSpec("alongside", ref="verse")
      alongside verse + 8          → PositionSpec("alongside", ref="verse", offset=8)
      between intro verse          → PositionSpec("between", ref="intro", ref2="verse")
      within verse bar 3           → PositionSpec("within",  ref="verse", offset=8)
      at 32 / beat 32 / 32        → PositionSpec("absolute", beat=32)
      at bar 9                     → PositionSpec("absolute", beat=32)  # (9-1)*4

    When after_alias=True (parsing the legacy After: field), a bare section
    name like "intro" maps to kind="after" rather than raising an error.
    """
    val = _single_value(lines)
    if not val:
        return None
    val = val.strip()

    # ── absolute: "32", "beat 32", "at 32" ──
    m = _POS_ABSOLUTE_RE.match(val)
    if m:
        return PositionSpec(kind="absolute", beat=float(m.group(1)))

    # ── absolute bar: "at bar 9" ──
    m = _POS_BAR_RE.match(val)
    if m:
        bar = int(m.group(1))
        return PositionSpec(kind="absolute", beat=float((bar - 1) * 4))

    # ── "last" ──
    if val.lower() == "last":
        return PositionSpec(kind="last")

    # Extract trailing offset (e.g. "+ 4", "- 2 bars")
    offset = 0.0
    rest = val
    om = _POS_OFFSET_RE.search(rest)
    if om:
        sign = 1.0 if om.group(1) == "+" else -1.0
        offset = sign * float(om.group(2))
        rest = rest[: om.start()].strip()

    rest_lower = rest.lower()

    # ── relationship keywords ──
    for kw in _POS_KEYWORDS:
        if rest_lower.startswith(kw):
            remainder = rest[len(kw):].strip()

            if kw == "last":
                return PositionSpec(kind="last", offset=offset)

            if kw == "between":
                # "between intro verse" → two section names
                parts = remainder.split()
                ref = parts[0].lower() if parts else None
                ref2 = parts[1].lower() if len(parts) > 1 else None
                return PositionSpec(kind="between", ref=ref, ref2=ref2, offset=offset)

            if kw == "within":
                # "within verse bar 3" → ref + optional bar offset
                parts = remainder.split()
                ref = parts[0].lower() if parts else None
                bar_offset = 0.0
                if len(parts) >= 3 and parts[1].lower() == "bar":
                    try:
                        bar_offset = (int(parts[2]) - 1) * 4.0
                    except ValueError:
                        pass
                return PositionSpec(kind="within", ref=ref, offset=offset + bar_offset)

            # after / before / alongside
            ref = remainder.lower() if remainder else None
            kind_val: Literal["after", "before", "alongside", "between", "within", "absolute", "last"] = kw  # type: ignore[assignment]
            return PositionSpec(kind=kind_val, ref=ref, offset=offset)

    # ── Legacy After: alias: bare section name without keyword ──
    if after_alias:
        return PositionSpec(kind="after", ref=val.lower(), offset=offset)

    return None


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
