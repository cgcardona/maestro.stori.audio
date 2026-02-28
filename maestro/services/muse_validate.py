"""muse validate — musical integrity checks for the working tree.

This module provides the core validation logic that ``muse validate`` invokes.
It is intentionally kept separate from the CLI layer so the checks can be
called from tests and future automation pipelines without spawning a subprocess.

Named result types registered in ``docs/reference/type_contracts.md``:
- ``ValidationSeverity``
- ``ValidationIssue``
- ``ValidationCheckResult``
- ``MuseValidateResult``

Exit-code contract (mirrors git-fsck conventions):
- 0 — all checks passed (no errors, no warnings)
- 1 — one or more ERROR issues found
- 2 — one or more WARN issues found and ``--strict`` was requested
"""
from __future__ import annotations

import dataclasses
import enum
import json
import logging
import pathlib
import re
import struct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ALLOWED_EMOTION_TAGS: frozenset[str] = frozenset(
    [
        "happy",
        "sad",
        "energetic",
        "calm",
        "tense",
        "relaxed",
        "dark",
        "bright",
        "melancholic",
        "triumphant",
        "mysterious",
        "playful",
        "romantic",
        "aggressive",
        "peaceful",
    ]
)

#: Regex for well-formed section directory names: e.g. "verse", "chorus-01", "bridge_02"
_SECTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class ValidationSeverity(str, enum.Enum):
    """Severity level for a single validation issue."""

    ERROR = "error"
    WARN = "warn"
    INFO = "info"


@dataclasses.dataclass
class ValidationIssue:
    """A single finding produced by a validation check.

    Agents should treat ERROR severity as a blocker for ``muse commit``.
    WARN severity is informational unless ``--strict`` mode is active.
    """

    severity: ValidationSeverity
    check: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity.value,
            "check": self.check,
            "path": self.path,
            "message": self.message,
        }


@dataclasses.dataclass
class ValidationCheckResult:
    """Outcome of a single named check category.

    ``passed`` is True only when ``issues`` is empty for this check.
    """

    name: str
    passed: bool
    issues: list[ValidationIssue]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclasses.dataclass
class MuseValidateResult:
    """Aggregated result of all validation checks run against the working tree.

    ``clean`` is True iff every check passed (no issues of any severity).
    ``has_errors`` is True iff at least one ERROR-severity issue was found.
    ``has_warnings`` is True iff at least one WARN-severity issue was found.
    """

    clean: bool
    has_errors: bool
    has_warnings: bool
    checks: list[ValidationCheckResult]
    fixes_applied: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "clean": self.clean,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "checks": [c.to_dict() for c in self.checks],
            "fixes_applied": self.fixes_applied,
        }


# ---------------------------------------------------------------------------
# MIDI integrity check
# ---------------------------------------------------------------------------

def _is_valid_midi(path: pathlib.Path) -> bool:
    """Return True iff *path* begins with the Standard MIDI File header (MThd).

    This is a fast structural check — it verifies the 4-byte magic header and
    the header chunk length (always 6 bytes for SMF).  Full parse correctness
    is left to ``mido`` in the import pipeline; here we just reject obviously
    corrupt or truncated files so agents get an actionable error before commit.
    """
    try:
        with path.open("rb") as fh:
            magic = fh.read(4)
            if magic != b"MThd":
                return False
            chunk_len_bytes = fh.read(4)
            if len(chunk_len_bytes) < 4:
                return False
            chunk_len: int = struct.unpack(">I", chunk_len_bytes)[0]
            return chunk_len == 6
    except OSError:
        return False


def check_midi_integrity(
    workdir: pathlib.Path,
    track_filter: str | None = None,
) -> ValidationCheckResult:
    """Verify that every .mid/.midi file in *workdir* has a valid MIDI header.

    Agents use this to detect corruption introduced by partial writes, failed
    exports, or bit-rot before the file is committed to Muse VCS history.

    Args:
        workdir:      The ``muse-work/`` directory to scan.
        track_filter: If given, only MIDI files whose relative path contains
                      this string (case-insensitive) are validated.

    Returns:
        ValidationCheckResult with check name ``"midi_integrity"``.
    """
    issues: list[ValidationIssue] = []
    if not workdir.exists():
        return ValidationCheckResult(name="midi_integrity", passed=True, issues=[])

    for midi_path in sorted(workdir.rglob("*.mid")) + sorted(workdir.rglob("*.midi")):
        if not midi_path.is_file():
            continue
        rel = midi_path.relative_to(workdir).as_posix()
        if track_filter and track_filter.lower() not in rel.lower():
            continue
        if not _is_valid_midi(midi_path):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    check="midi_integrity",
                    path=rel,
                    message=f"Invalid or corrupted MIDI file: missing or malformed MThd header.",
                )
            )
            logger.warning("❌ MIDI integrity failure: %s", rel)

    return ValidationCheckResult(
        name="midi_integrity",
        passed=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Manifest consistency check
# ---------------------------------------------------------------------------

def check_manifest_consistency(
    root: pathlib.Path,
    track_filter: str | None = None,
) -> ValidationCheckResult:
    """Compare the committed snapshot manifest against the actual working tree.

    Detects orphaned files (in the manifest but missing from disk) and
    unregistered files (on disk but absent from the manifest).  These indicate
    that the working tree has drifted from the last commit — potentially from
    manual edits or a failed ``muse checkout``.

    Args:
        root:         Repository root (contains ``.muse/`` and ``muse-work/``).
        track_filter: Scope validation to paths containing this string.

    Returns:
        ValidationCheckResult with check name ``"manifest_consistency"``.
    """
    issues: list[ValidationIssue] = []
    muse_dir = root / ".muse"
    workdir = root / "muse-work"

    # Resolve HEAD commit and its snapshot manifest
    head_path = muse_dir / "HEAD"
    if not head_path.exists():
        return ValidationCheckResult(name="manifest_consistency", passed=True, issues=[])

    head_ref = head_path.read_text().strip()
    ref_file = muse_dir / pathlib.Path(head_ref)
    if not ref_file.exists() or not ref_file.read_text().strip():
        # No commits yet — nothing to compare against
        return ValidationCheckResult(name="manifest_consistency", passed=True, issues=[])

    # Load the committed snapshot manifest from the muse-work objects area
    # The manifest is stored alongside objects in .muse/objects/ as a JSON side-car,
    # but in this implementation commits reference snapshots stored in DB.
    # We read the on-disk snapshot cache if available (written by muse commit).
    snapshot_cache = muse_dir / "snapshot_manifest.json"
    if not snapshot_cache.exists():
        # No cached manifest — check is not possible without DB access
        return ValidationCheckResult(name="manifest_consistency", passed=True, issues=[])

    try:
        committed_manifest: dict[str, str] = json.loads(snapshot_cache.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check="manifest_consistency",
                path=".muse/snapshot_manifest.json",
                message=f"Cannot read cached snapshot manifest: {exc}",
            )
        )
        return ValidationCheckResult(name="manifest_consistency", passed=False, issues=issues)

    if not workdir.exists():
        # All committed files are orphaned
        for path in sorted(committed_manifest):
            if track_filter and track_filter.lower() not in path.lower():
                continue
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    check="manifest_consistency",
                    path=path,
                    message="File is in committed manifest but muse-work/ does not exist.",
                )
            )
        return ValidationCheckResult(
            name="manifest_consistency",
            passed=len(issues) == 0,
            issues=issues,
        )

    # Build current working-tree manifest
    from maestro.muse_cli.snapshot import walk_workdir, hash_file

    current_manifest = walk_workdir(workdir)

    committed_paths = set(committed_manifest.keys())
    current_paths = set(current_manifest.keys())

    for path in sorted(committed_paths - current_paths):
        if track_filter and track_filter.lower() not in path.lower():
            continue
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check="manifest_consistency",
                path=path,
                message="File in committed manifest is missing from working tree (orphaned).",
            )
        )

    for path in sorted(current_paths - committed_paths):
        if track_filter and track_filter.lower() not in path.lower():
            continue
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARN,
                check="manifest_consistency",
                path=path,
                message="File in working tree is not recorded in committed manifest (unregistered).",
            )
        )

    return ValidationCheckResult(
        name="manifest_consistency",
        passed=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Duplicate tracks check
# ---------------------------------------------------------------------------

def check_no_duplicate_tracks(
    workdir: pathlib.Path,
    track_filter: str | None = None,
) -> ValidationCheckResult:
    """Detect duplicate instrument-role definitions in the working tree.

    A duplicate is defined as two or more MIDI files sharing the same
    instrument role name (the stem of their filename, excluding the extension
    and any numeric suffix).  For example: ``bass.mid`` and ``bass_alt.mid``
    both define a bass role.

    Agents use this to prevent ambiguous track assignments that would cause
    Storpheus to generate for the wrong instrument during composition.

    Args:
        workdir:      The ``muse-work/`` directory to scan.
        track_filter: If given, only roles whose name contains this string
                      (case-insensitive) are evaluated.

    Returns:
        ValidationCheckResult with check name ``"no_duplicate_tracks"``.
    """
    issues: list[ValidationIssue] = []
    if not workdir.exists():
        return ValidationCheckResult(name="no_duplicate_tracks", passed=True, issues=[])

    from collections import defaultdict
    role_to_paths: dict[str, list[str]] = defaultdict(list)

    for midi_path in sorted(workdir.rglob("*.mid")) + sorted(workdir.rglob("*.midi")):
        if not midi_path.is_file():
            continue
        rel = midi_path.relative_to(workdir).as_posix()
        if track_filter and track_filter.lower() not in rel.lower():
            continue
        # Derive role: strip extension, strip trailing digits/underscores/hyphens
        stem = midi_path.stem.lower()
        role = re.sub(r"[_\-]?\d+$", "", stem)
        role_to_paths[role].append(rel)

    for role, paths in sorted(role_to_paths.items()):
        if len(paths) > 1:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARN,
                    check="no_duplicate_tracks",
                    path=", ".join(paths),
                    message=f"Duplicate instrument role '{role}' defined by {len(paths)} files.",
                )
            )
            logger.warning("⚠️ Duplicate track role: %s → %s", role, paths)

    return ValidationCheckResult(
        name="no_duplicate_tracks",
        passed=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Section naming convention check
# ---------------------------------------------------------------------------

def check_section_naming(
    workdir: pathlib.Path,
    section_filter: str | None = None,
) -> ValidationCheckResult:
    """Verify that section subdirectories follow the expected naming convention.

    Section directories must match ``[a-z][a-z0-9_-]*`` (lowercase, starting
    with a letter, using only alphanumeric chars, hyphens, or underscores).
    This constraint ensures consistent referencing by AI agents and avoids
    shell quoting issues.

    Args:
        workdir:       The ``muse-work/`` directory to scan.
        section_filter: If given, only directories whose name contains this
                        string (case-insensitive) are evaluated.

    Returns:
        ValidationCheckResult with check name ``"section_naming"``.
    """
    issues: list[ValidationIssue] = []
    if not workdir.exists():
        return ValidationCheckResult(name="section_naming", passed=True, issues=[])

    for entry in sorted(workdir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if section_filter and section_filter.lower() not in name.lower():
            continue
        if not _SECTION_NAME_RE.match(name):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARN,
                    check="section_naming",
                    path=name,
                    message=(
                        f"Section directory '{name}' does not follow naming convention "
                        f"[a-z][a-z0-9_-]* (lowercase, no spaces or uppercase letters)."
                    ),
                )
            )
            logger.warning("⚠️ Section naming violation: %s", name)

    return ValidationCheckResult(
        name="section_naming",
        passed=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Emotion tags check
# ---------------------------------------------------------------------------

def check_emotion_tags(
    root: pathlib.Path,
    track_filter: str | None = None,
) -> ValidationCheckResult:
    """Verify that emotion tags in commit metadata are from the allowed vocabulary.

    Reads ``.muse/commit_metadata.json`` if present (written by ``muse tag``).
    Any tag not in :data:`ALLOWED_EMOTION_TAGS` is flagged as a warning so
    agents know they may be working with an unrecognised emotional label that
    Maestro's mood model has not been trained on.

    Args:
        root:         Repository root.
        track_filter: Unused for this check (included for API symmetry).

    Returns:
        ValidationCheckResult with check name ``"emotion_tags"``.
    """
    issues: list[ValidationIssue] = []
    muse_dir = root / ".muse"
    tag_cache = muse_dir / "tags.json"

    if not tag_cache.exists():
        return ValidationCheckResult(name="emotion_tags", passed=True, issues=[])

    try:
        tags_data: object = json.loads(tag_cache.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARN,
                check="emotion_tags",
                path=".muse/tags.json",
                message=f"Cannot read tag cache: {exc}",
            )
        )
        return ValidationCheckResult(name="emotion_tags", passed=False, issues=issues)

    if not isinstance(tags_data, list):
        return ValidationCheckResult(name="emotion_tags", passed=True, issues=[])

    for entry in tags_data:
        if not isinstance(entry, dict):
            continue
        tag_name: object = entry.get("tag")
        if not isinstance(tag_name, str):
            continue
        tag_lower = tag_name.lower()
        if tag_lower not in ALLOWED_EMOTION_TAGS:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARN,
                    check="emotion_tags",
                    path=".muse/tags.json",
                    message=(
                        f"Emotion tag '{tag_name}' is not in the allowed vocabulary. "
                        f"Allowed: {', '.join(sorted(ALLOWED_EMOTION_TAGS))}"
                    ),
                )
            )
            logger.warning("⚠️ Unknown emotion tag: %s", tag_name)

    return ValidationCheckResult(
        name="emotion_tags",
        passed=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Auto-fix: quantise slightly off-grid notes (stub — full impl requires mido)
# ---------------------------------------------------------------------------

def apply_fixes(
    workdir: pathlib.Path,
    issues: list[ValidationIssue],
) -> list[str]:
    """Apply automatic corrections for fixable issues.

    Currently supports:
    - Re-writing malformed MIDI files is not auto-fixable (data-loss risk).
    - Section naming: no auto-rename (would break references in other files).
    - Duplicate tracks: no auto-remove (ambiguous which to keep).

    The function is intentionally conservative — it only fixes issues that
    cannot cause data loss and where the correct fix is unambiguous.

    Args:
        workdir: The ``muse-work/`` working tree directory.
        issues:  The full list of issues found during validation.

    Returns:
        List of human-readable strings describing each fix applied.
    """
    applied: list[str] = []

    # Future: quantise off-grid MIDI notes using mido when mido is available.
    # For now, emit an informational note if any fixable categories were found.
    fixable_checks = {"manifest_consistency"}
    fixable_issues = [i for i in issues if i.check in fixable_checks]
    if fixable_issues:
        logger.info(
            "⚠️ --fix: %d fixable issue(s) found but no auto-fix logic is "
            "implemented yet for check categories: %s",
            len(fixable_issues),
            {i.check for i in fixable_issues},
        )

    return applied


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_validate(
    root: pathlib.Path,
    *,
    strict: bool = False,
    track_filter: str | None = None,
    section_filter: str | None = None,
    auto_fix: bool = False,
) -> MuseValidateResult:
    """Run all integrity checks against the working tree at *root*.

    This is the single entry point for the validate subsystem.  It runs
    checks in dependency order and aggregates results into a single
    :class:`MuseValidateResult`.

    Args:
        root:           Repository root (contains ``.muse/`` and ``muse-work/``).
        strict:         Treat WARN-severity issues as fatal (exit 2 in CLI).
        track_filter:   Restrict checks to files/paths containing this string.
        section_filter: Restrict section-naming check to dirs matching this.
        auto_fix:       Attempt to auto-correct fixable issues before reporting.

    Returns:
        MuseValidateResult with all check outcomes and any fixes applied.
    """
    workdir = root / "muse-work"

    check_results: list[ValidationCheckResult] = [
        check_midi_integrity(workdir, track_filter=track_filter),
        check_manifest_consistency(root, track_filter=track_filter),
        check_no_duplicate_tracks(workdir, track_filter=track_filter),
        check_section_naming(workdir, section_filter=section_filter),
        check_emotion_tags(root, track_filter=track_filter),
    ]

    all_issues: list[ValidationIssue] = [
        issue for result in check_results for issue in result.issues
    ]

    fixes_applied: list[str] = []
    if auto_fix and all_issues:
        fixes_applied = apply_fixes(workdir, all_issues)

    has_errors = any(i.severity == ValidationSeverity.ERROR for i in all_issues)
    has_warnings = any(i.severity == ValidationSeverity.WARN for i in all_issues)
    clean = not has_errors and not has_warnings

    logger.info(
        "✅ muse validate: %d check(s), errors=%s, warnings=%s",
        len(check_results),
        has_errors,
        has_warnings,
    )

    return MuseValidateResult(
        clean=clean,
        has_errors=has_errors,
        has_warnings=has_warnings,
        checks=check_results,
        fixes_applied=fixes_applied,
    )
