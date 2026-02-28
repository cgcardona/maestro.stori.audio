"""Muse Arrange — arrangement map analysis for committed snapshots.

Builds an *arrangement matrix* from the file manifest of a Muse commit:
rows = instruments, columns = sections.  Each cell records whether the
instrument is active in that section and, in density mode, how many bytes
of MIDI data it contributed (a byte-count proxy for note density).

**Path convention:**
Files in ``muse-work/`` that carry section metadata must follow::

    <section>/<instrument>/<filename>

Examples::

    intro/drums/beat.mid          → section=intro,   instrument=drums
    chorus/strings/pad.mid        → section=chorus,  instrument=strings
    verse/bass/line_v1.mid        → section=verse,   instrument=bass

Files with fewer than two path components are uncategorised and excluded
from the arrangement matrix.

**Outputs:**
- Text (``--format text``) — Unicode block-char matrix, human-readable
- JSON (``--format json``) — structured dict, AI-agent-consumable
- CSV  (``--format csv``)  — spreadsheet-ready rows

**Compare mode (``--compare commit-a commit-b``):**
Produces an :class:`ArrangementDiff` showing which ``(section, instrument)``
cells were added, removed, or unchanged between the two commits.

Why this matters for AI orchestration:
    An AI agent can call ``muse arrange --format json HEAD`` before generating
    a new string part to see exactly which sections already have strings,
    preventing doubling mistakes and enabling coherent orchestration decisions.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path parsing
# ---------------------------------------------------------------------------

_SECTION_ORDER: list[str] = [
    "intro", "verse", "prechorus", "pre-chorus", "prechoruse",
    "chorus", "bridge", "outro", "breakdown", "drop", "hook",
]

_SECTION_ALIASES: dict[str, str] = {
    "pre-chorus": "prechorus",
    "prechoruse": "prechorus",
    "pre_chorus": "prechorus",
}


def _normalise_section(raw: str) -> str:
    """Lower-case and apply known aliases to section names."""
    lower = raw.lower().strip()
    return _SECTION_ALIASES.get(lower, lower)


def extract_section_instrument(rel_path: str) -> tuple[str, str] | None:
    """Parse *rel_path* (relative to ``muse-work/``) into ``(section, instrument)``.

    Returns ``None`` when the path does not have at least two directory
    components (i.e. it cannot be mapped to a section + instrument pair).

    The path is expected to follow the canonical convention::

        <section>/<instrument>/<filename>

    Only the first two components are used; any deeper nesting is ignored.
    The section name is normalised via :func:`_normalise_section`.

    Examples::

        "intro/drums/beat.mid"     → ("intro", "drums")
        "chorus/strings/pad.mid"   → ("chorus", "strings")
        "bass/riff.mid"            → None  # only one directory component
        "solo.mid"                 → None  # flat file
    """
    parts = rel_path.replace("\\", "/").split("/")
    # Need at least section + instrument + filename (≥ 3 parts)
    # but also accept section + filename (2 parts) where the first is
    # the section and the second is the filename (not an instrument — skip).
    # We require exactly ≥ 3 parts: parts[0]=section, parts[1]=instrument
    if len(parts) < 3:
        return None
    section = _normalise_section(parts[0])
    instrument = parts[1].lower().strip()
    if not section or not instrument:
        return None
    return section, instrument


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArrangementCell:
    """Single cell in the arrangement matrix: one (section, instrument) pair.

    ``active`` is ``True`` when at least one file exists for this pair.
    ``file_count`` counts distinct files (useful when multiple takes exist).
    ``total_bytes`` sums the object sizes — used as a note-density proxy in
    ``--density`` mode.
    """

    section: str
    instrument: str
    active: bool
    file_count: int = 0
    total_bytes: int = 0

    @property
    def density_score(self) -> float:
        """Normalised byte density — raw ``total_bytes`` exposed for callers."""
        return float(self.total_bytes)


@dataclass
class ArrangementMatrix:
    """Full arrangement matrix for a single commit.

    Attributes:
        commit_id: The 64-char commit SHA used to build this matrix.
        sections:  Ordered list of section names (columns).
        instruments: Ordered list of instrument names (rows).
        cells: Mapping ``(section, instrument) → ArrangementCell``.
    """

    commit_id: str
    sections: list[str]
    instruments: list[str]
    cells: dict[tuple[str, str], ArrangementCell] = field(default_factory=dict)

    def get_cell(self, section: str, instrument: str) -> ArrangementCell:
        """Return the cell for *(section, instrument)*, defaulting to inactive."""
        key = (section, instrument)
        return self.cells.get(
            key, ArrangementCell(section=section, instrument=instrument, active=False)
        )


@dataclass(frozen=True)
class ArrangementDiffCell:
    """Change status of a single cell between two commits.

    ``status`` is one of:
    - ``"added"``   — active in commit-b, absent in commit-a
    - ``"removed"`` — active in commit-a, absent in commit-b
    - ``"unchanged"`` — same active/inactive state in both commits
    """

    section: str
    instrument: str
    status: Literal["added", "removed", "unchanged"]
    cell_a: ArrangementCell
    cell_b: ArrangementCell


@dataclass
class ArrangementDiff:
    """Diff of two arrangement matrices (commit-a → commit-b).

    Attributes:
        commit_id_a: Commit SHA for the baseline (left side).
        commit_id_b: Commit SHA for the target (right side).
        sections:    Union of section names across both matrices.
        instruments: Union of instrument names across both matrices.
        cells:       Mapping ``(section, instrument) → ArrangementDiffCell``.
    """

    commit_id_a: str
    commit_id_b: str
    sections: list[str]
    instruments: list[str]
    cells: dict[tuple[str, str], ArrangementDiffCell] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------


def build_arrangement_matrix(
    commit_id: str,
    manifest: dict[str, str],
    object_sizes: dict[str, int] | None = None,
) -> ArrangementMatrix:
    """Build an :class:`ArrangementMatrix` from a snapshot *manifest*.

    Parameters
    ----------
    commit_id:
        The commit SHA the manifest was taken from (stored on the matrix
        for display and JSON serialisation).
    manifest:
        A ``{rel_path: object_id}`` mapping as returned by
        :func:`maestro.muse_cli.db.get_commit_snapshot_manifest`.
        Paths are relative to ``muse-work/``.
    object_sizes:
        Optional ``{object_id: size_bytes}`` map.  When provided, each
        cell accumulates the byte sizes of its files so that
        ``--density`` mode can report them.  Missing entries default to 0.

    Returns
    -------
    ArrangementMatrix
        A matrix with sections and instruments ordered: first by the
        canonical section ordering defined in ``_SECTION_ORDER``, with any
        unknown sections appended alphabetically.  Instruments are sorted
        alphabetically.
    """
    sizes = object_sizes or {}

    # Accumulate counts and byte totals per (section, instrument) cell.
    counts: dict[tuple[str, str], int] = {}
    bytes_: dict[tuple[str, str], int] = {}

    for rel_path, object_id in manifest.items():
        parsed = extract_section_instrument(rel_path)
        if parsed is None:
            continue
        section, instrument = parsed
        key = (section, instrument)
        counts[key] = counts.get(key, 0) + 1
        bytes_[key] = bytes_.get(key, 0) + sizes.get(object_id, 0)

    # Derive ordered section and instrument lists.
    all_sections = {k[0] for k in counts}
    all_instruments = {k[1] for k in counts}

    sections = _order_sections(all_sections)
    instruments = sorted(all_instruments)

    cells: dict[tuple[str, str], ArrangementCell] = {}
    for key, count in counts.items():
        section, instrument = key
        cells[key] = ArrangementCell(
            section=section,
            instrument=instrument,
            active=True,
            file_count=count,
            total_bytes=bytes_.get(key, 0),
        )

    return ArrangementMatrix(
        commit_id=commit_id,
        sections=sections,
        instruments=instruments,
        cells=cells,
    )


def _order_sections(sections: set[str]) -> list[str]:
    """Order sections by canonical musical position, with unknowns appended."""
    known_order = [s for s in _SECTION_ORDER if s in sections]
    unknown = sorted(sections - set(_SECTION_ORDER))
    return known_order + unknown


# ---------------------------------------------------------------------------
# Renderers — text
# ---------------------------------------------------------------------------

_ACTIVE_CHAR = "████"
_INACTIVE_CHAR = "░░░░"


def render_matrix_text(
    matrix: ArrangementMatrix,
    *,
    density: bool = False,
    section_filter: str | None = None,
    track_filter: str | None = None,
) -> str:
    """Render *matrix* as a human-readable text table.

    Each row is an instrument; each column is a section.  Active cells
    show ``████``; inactive cells show ``░░░░``.  In ``density`` mode each
    cell shows the total byte size instead.

    Parameters
    ----------
    density:
        When ``True``, show byte totals per cell instead of block chars.
    section_filter:
        If set, include only the named section (case-insensitive).
    track_filter:
        If set, include only the named instrument/track (case-insensitive).
    """
    sections = _apply_section_filter(matrix.sections, section_filter)
    instruments = _apply_track_filter(matrix.instruments, track_filter)

    if not sections or not instruments:
        return f"Arrangement Map — commit {matrix.commit_id[:8]}\n\n(no data for the given filters)"

    short_id = matrix.commit_id[:8]
    lines: list[str] = [f"Arrangement Map — commit {short_id}", ""]

    # Column widths
    instr_width = max((len(i) for i in instruments), default=8) + 2
    col_width = max(max((len(s) for s in sections), default=4), 4) + 2

    # Header row
    header = " " * instr_width
    for section in sections:
        header += section.capitalize().center(col_width)
    lines.append(header)

    # Data rows
    for instrument in instruments:
        row = instrument.ljust(instr_width)
        for section in sections:
            cell = matrix.get_cell(section, instrument)
            if density:
                cell_text = f"{cell.total_bytes:,}" if cell.active else "-"
            else:
                cell_text = _ACTIVE_CHAR if cell.active else _INACTIVE_CHAR
            row += cell_text.center(col_width)
        lines.append(row)

    return "\n".join(lines)


def render_matrix_json(
    matrix: ArrangementMatrix,
    *,
    density: bool = False,
    section_filter: str | None = None,
    track_filter: str | None = None,
) -> str:
    """Serialise *matrix* as a JSON string suitable for AI agent consumption."""
    sections = _apply_section_filter(matrix.sections, section_filter)
    instruments = _apply_track_filter(matrix.instruments, track_filter)

    matrix_data: dict[str, dict[str, object]] = {}
    for instrument in instruments:
        matrix_data[instrument] = {}
        for section in sections:
            cell = matrix.get_cell(section, instrument)
            if density:
                matrix_data[instrument][section] = {
                    "active": cell.active,
                    "file_count": cell.file_count,
                    "total_bytes": cell.total_bytes,
                }
            else:
                matrix_data[instrument][section] = cell.active

    payload: dict[str, object] = {
        "commit_id": matrix.commit_id,
        "sections": sections,
        "instruments": instruments,
        "arrangement": matrix_data,
    }
    return json.dumps(payload, indent=2)


def render_matrix_csv(
    matrix: ArrangementMatrix,
    *,
    density: bool = False,
    section_filter: str | None = None,
    track_filter: str | None = None,
) -> str:
    """Serialise *matrix* as CSV with instrument as the first column."""
    sections = _apply_section_filter(matrix.sections, section_filter)
    instruments = _apply_track_filter(matrix.instruments, track_filter)

    buf = io.StringIO()
    writer = csv.writer(buf)

    header = ["instrument"] + sections
    writer.writerow(header)

    for instrument in instruments:
        row: list[object] = [instrument]
        for section in sections:
            cell = matrix.get_cell(section, instrument)
            if density:
                row.append(cell.total_bytes if cell.active else 0)
            else:
                row.append(1 if cell.active else 0)
        writer.writerow(row)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Diff builder + renderer
# ---------------------------------------------------------------------------


def build_arrangement_diff(
    matrix_a: ArrangementMatrix,
    matrix_b: ArrangementMatrix,
) -> ArrangementDiff:
    """Compute a cell-by-cell diff of two arrangement matrices.

    Sections and instruments are the union of both matrices' sets, ordered
    by the canonical section order for sections and alphabetically for
    instruments.
    """
    all_sections = set(matrix_a.sections) | set(matrix_b.sections)
    all_instruments = set(matrix_a.instruments) | set(matrix_b.instruments)

    sections = _order_sections(all_sections)
    instruments = sorted(all_instruments)

    diff_cells: dict[tuple[str, str], ArrangementDiffCell] = {}

    for section in sections:
        for instrument in instruments:
            cell_a = matrix_a.get_cell(section, instrument)
            cell_b = matrix_b.get_cell(section, instrument)

            if not cell_a.active and cell_b.active:
                status: Literal["added", "removed", "unchanged"] = "added"
            elif cell_a.active and not cell_b.active:
                status = "removed"
            else:
                status = "unchanged"

            key = (section, instrument)
            diff_cells[key] = ArrangementDiffCell(
                section=section,
                instrument=instrument,
                status=status,
                cell_a=cell_a,
                cell_b=cell_b,
            )

    return ArrangementDiff(
        commit_id_a=matrix_a.commit_id,
        commit_id_b=matrix_b.commit_id,
        sections=sections,
        instruments=instruments,
        cells=diff_cells,
    )


_DIFF_SYMBOLS: dict[str, str] = {
    "added": "+",
    "removed": "-",
    "unchanged": " ",
}


def render_diff_text(diff: ArrangementDiff) -> str:
    """Render *diff* as a human-readable side-by-side comparison.

    ``+`` = cell added in commit-b, ``-`` = cell removed, `` `` = unchanged.
    Only rows with at least one changed cell are shown.
    """
    a_short = diff.commit_id_a[:8]
    b_short = diff.commit_id_b[:8]
    lines: list[str] = [
        f"Arrangement Diff — {a_short} → {b_short}",
        "",
    ]

    # Compute column widths
    instr_width = max((len(i) for i in diff.instruments), default=8) + 2
    col_width = max(max((len(s) for s in diff.sections), default=4), 4) + 2

    header = " " * instr_width
    for section in diff.sections:
        header += section.capitalize().center(col_width)
    lines.append(header)

    changed_rows: list[tuple[str, bool]] = []
    for instrument in diff.instruments:
        row = instrument.ljust(instr_width)
        has_change = False
        for section in diff.sections:
            cell_diff = diff.cells.get((section, instrument))
            if cell_diff is None:
                symbol = " "
                cell_char = _INACTIVE_CHAR
            else:
                symbol = _DIFF_SYMBOLS[cell_diff.status]
                if cell_diff.status == "unchanged":
                    cell_char = _ACTIVE_CHAR if cell_diff.cell_b.active else _INACTIVE_CHAR
                elif cell_diff.status == "added":
                    cell_char = f"+{_ACTIVE_CHAR}"
                    has_change = True
                else:
                    cell_char = f"-{_ACTIVE_CHAR}"
                    has_change = True
            row += cell_char.center(col_width)
        changed_rows.append((row, has_change))

    # Show all rows but mark changed ones; if all unchanged, show a note.
    any_changes = any(has for _, has in changed_rows)
    for row, _ in changed_rows:
        lines.append(row)

    if not any_changes:
        lines.append("")
        lines.append("(no arrangement changes between these commits)")

    return "\n".join(lines)


def render_diff_json(diff: ArrangementDiff) -> str:
    """Serialise *diff* as a JSON string."""
    changes: list[dict[str, object]] = []
    for key, cell_diff in diff.cells.items():
        if cell_diff.status != "unchanged":
            changes.append(
                {
                    "section": key[0],
                    "instrument": key[1],
                    "status": cell_diff.status,
                }
            )

    payload: dict[str, object] = {
        "commit_id_a": diff.commit_id_a,
        "commit_id_b": diff.commit_id_b,
        "sections": diff.sections,
        "instruments": diff.instruments,
        "changes": changes,
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _apply_section_filter(sections: list[str], section_filter: str | None) -> list[str]:
    """Return the filtered section list.  ``None`` means no filter."""
    if section_filter is None:
        return sections
    normalised = _normalise_section(section_filter)
    return [s for s in sections if s == normalised]


def _apply_track_filter(instruments: list[str], track_filter: str | None) -> list[str]:
    """Return the filtered instrument list.  ``None`` means no filter."""
    if track_filter is None:
        return instruments
    lower = track_filter.lower().strip()
    return [i for i in instruments if i == lower]
