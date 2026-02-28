"""Tests for ``muse validate`` — CLI interface, exit codes, and per-check logic.

Coverage strategy
-----------------
- Regression: ``test_validate_exits_nonzero_on_errors`` — would have caught the
  absence of the ``muse validate`` command.
- Unit: each check function in ``maestro.services.muse_validate`` in isolation.
- Integration: the ``run_validate`` orchestrator with real temporary directories.
- CLI layer: ``typer.testing.CliRunner`` against the full ``muse`` app so that
  flag parsing, exit codes, and output format are exercised end-to-end.
- Edge cases: missing workdir, no commits yet, ``--strict`` mode, ``--json`` output.
"""
from __future__ import annotations

import json
import pathlib
import struct
import uuid

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_validate import (
    ALLOWED_EMOTION_TAGS,
    MuseValidateResult,
    ValidationSeverity,
    apply_fixes,
    check_emotion_tags,
    check_manifest_consistency,
    check_midi_integrity,
    check_no_duplicate_tracks,
    check_section_naming,
    run_validate,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_midi(path: pathlib.Path) -> None:
    """Write a minimal, structurally valid Standard MIDI File to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        # MThd header: magic + chunk length (always 6) + format (1) + ntracks (1) + division (96)
        fh.write(b"MThd")
        fh.write(struct.pack(">I", 6))   # chunk length
        fh.write(struct.pack(">H", 1))   # format 1
        fh.write(struct.pack(">H", 1))   # 1 track
        fh.write(struct.pack(">H", 96))  # 96 ticks/beat
        # MTrk header with end-of-track event
        fh.write(b"MTrk")
        fh.write(struct.pack(">I", 4))   # chunk length
        fh.write(b"\x00\xff\x2f\x00")    # delta=0, Meta=EOT


def _make_invalid_midi(path: pathlib.Path) -> None:
    """Write a file that looks like MIDI but has a wrong header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"JUNK" + b"\x00" * 20)


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal ``.muse/`` layout with no commits."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _commit_ref(root: pathlib.Path, branch: str = "main") -> None:
    """Write a fake commit ID into the branch ref so HEAD is non-empty."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4" * 8)


# ---------------------------------------------------------------------------
# Regression test — the single test that would have caught the missing command
# ---------------------------------------------------------------------------


def test_validate_exits_nonzero_on_errors(tmp_path: pathlib.Path) -> None:
    """muse validate must exit non-zero when MIDI integrity errors are found.

    This is the regression test for issue #99: if ``muse validate`` does not
    exist or silently exits 0 on errors, this test fails.
    """
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    _make_invalid_midi(workdir / "bass.mid")

    result = runner.invoke(cli, ["validate"], env={"MUSE_REPO_ROOT": str(tmp_path)})
    assert result.exit_code != 0, (
        "muse validate should exit non-zero when MIDI integrity errors are found"
    )


# ---------------------------------------------------------------------------
# check_midi_integrity
# ---------------------------------------------------------------------------


class TestCheckMidiIntegrity:
    def test_valid_midi_passes(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "bass.mid")
        result = check_midi_integrity(workdir)
        assert result.passed
        assert result.issues == []

    def test_invalid_midi_produces_error(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "corrupted.mid")
        result = check_midi_integrity(workdir)
        assert not result.passed
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.ERROR
        assert "corrupted.mid" in result.issues[0].path

    def test_missing_workdir_is_clean(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        result = check_midi_integrity(workdir)
        assert result.passed

    def test_track_filter_excludes_unmatched_files(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "drums.mid")
        _make_valid_midi(workdir / "bass.mid")
        result = check_midi_integrity(workdir, track_filter="bass")
        # Only bass.mid is checked and it's valid
        assert result.passed

    def test_track_filter_includes_invalid_match(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "drums.mid")
        _make_valid_midi(workdir / "bass.mid")
        result = check_midi_integrity(workdir, track_filter="drums")
        assert not result.passed
        assert any("drums.mid" in i.path for i in result.issues)

    def test_empty_workdir_is_clean(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        result = check_midi_integrity(workdir)
        assert result.passed

    def test_multiple_invalid_files(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "a.mid")
        _make_invalid_midi(workdir / "b.midi")
        result = check_midi_integrity(workdir)
        assert not result.passed
        assert len(result.issues) == 2

    def test_truncated_midi_header(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        path = workdir / "short.mid"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MThd\x00")  # truncated after magic
        result = check_midi_integrity(workdir)
        assert not result.passed

    def test_wrong_chunk_length(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        path = workdir / "badlen.mid"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(b"MThd")
            fh.write(struct.pack(">I", 10))  # wrong: must be 6
            fh.write(b"\x00" * 10)
        result = check_midi_integrity(workdir)
        assert not result.passed


# ---------------------------------------------------------------------------
# check_no_duplicate_tracks
# ---------------------------------------------------------------------------


class TestCheckNoDuplicateTracks:
    def test_no_duplicates_passes(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "bass.mid")
        _make_valid_midi(workdir / "drums.mid")
        result = check_no_duplicate_tracks(workdir)
        assert result.passed

    def test_duplicate_role_produces_warning(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "bass.mid")
        _make_valid_midi(workdir / "bass2.mid")  # same role "bass" after stripping trailing digit
        result = check_no_duplicate_tracks(workdir)
        assert not result.passed
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.WARN
        assert "bass" in result.issues[0].message

    def test_numbered_variants_flagged(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "lead.mid")
        _make_valid_midi(workdir / "lead01.mid")
        _make_valid_midi(workdir / "lead-02.mid")
        result = check_no_duplicate_tracks(workdir)
        assert not result.passed

    def test_missing_workdir_is_clean(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        result = check_no_duplicate_tracks(workdir)
        assert result.passed

    def test_track_filter_scopes_check(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "bass.mid")
        _make_valid_midi(workdir / "bass_alt.mid")
        _make_valid_midi(workdir / "drums.mid")
        result = check_no_duplicate_tracks(workdir, track_filter="drums")
        # Only drums is in scope — no duplicates there
        assert result.passed


# ---------------------------------------------------------------------------
# check_section_naming
# ---------------------------------------------------------------------------


class TestCheckSectionNaming:
    def test_valid_names_pass(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        (workdir / "verse").mkdir(parents=True)
        (workdir / "chorus-01").mkdir()
        (workdir / "bridge_02").mkdir()
        result = check_section_naming(workdir)
        assert result.passed

    def test_uppercase_name_fails(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        (workdir / "Verse").mkdir(parents=True)
        result = check_section_naming(workdir)
        assert not result.passed
        assert any("Verse" in i.path for i in result.issues)
        assert result.issues[0].severity == ValidationSeverity.WARN

    def test_name_with_spaces_fails(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        (workdir / "my section").mkdir(parents=True)
        result = check_section_naming(workdir)
        assert not result.passed

    def test_name_starting_with_digit_fails(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        (workdir / "1verse").mkdir(parents=True)
        result = check_section_naming(workdir)
        assert not result.passed

    def test_missing_workdir_is_clean(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        result = check_section_naming(workdir)
        assert result.passed

    def test_section_filter_excludes_bad_name(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        (workdir / "Verse").mkdir(parents=True)
        (workdir / "chorus").mkdir()
        result = check_section_naming(workdir, section_filter="chorus")
        # Verse is out of scope for the filter
        assert result.passed


# ---------------------------------------------------------------------------
# check_emotion_tags
# ---------------------------------------------------------------------------


class TestCheckEmotionTags:
    def test_no_tag_cache_is_clean(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        result = check_emotion_tags(tmp_path)
        assert result.passed

    def test_valid_tags_pass(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        muse = tmp_path / ".muse"
        tags = [{"tag": "happy"}, {"tag": "calm"}]
        (muse / "tags.json").write_text(json.dumps(tags))
        result = check_emotion_tags(tmp_path)
        assert result.passed

    def test_unknown_tag_produces_warning(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        muse = tmp_path / ".muse"
        tags = [{"tag": "happy"}, {"tag": "funky-fresh"}]
        (muse / "tags.json").write_text(json.dumps(tags))
        result = check_emotion_tags(tmp_path)
        assert not result.passed
        assert any("funky-fresh" in i.message for i in result.issues)
        assert result.issues[0].severity == ValidationSeverity.WARN

    def test_malformed_tag_cache_produces_warning(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        muse = tmp_path / ".muse"
        (muse / "tags.json").write_text("{not valid json")
        result = check_emotion_tags(tmp_path)
        assert not result.passed

    def test_non_list_tag_cache_is_skipped(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        muse = tmp_path / ".muse"
        (muse / "tags.json").write_text(json.dumps({"tag": "happy"}))  # dict, not list
        result = check_emotion_tags(tmp_path)
        assert result.passed

    def test_all_allowed_tags_pass(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        muse = tmp_path / ".muse"
        tags = [{"tag": t} for t in sorted(ALLOWED_EMOTION_TAGS)]
        (muse / "tags.json").write_text(json.dumps(tags))
        result = check_emotion_tags(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# check_manifest_consistency
# ---------------------------------------------------------------------------


class TestCheckManifestConsistency:
    def test_no_commits_is_clean(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        result = check_manifest_consistency(tmp_path)
        assert result.passed

    def test_no_snapshot_cache_is_clean(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        _commit_ref(tmp_path)
        # No .muse/snapshot_manifest.json — check skips gracefully
        result = check_manifest_consistency(tmp_path)
        assert result.passed

    def test_matching_manifest_passes(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        _commit_ref(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "bass.mid")
        # Build a manifest matching the current working tree
        from maestro.muse_cli.snapshot import hash_file
        manifest = {"bass.mid": hash_file(workdir / "bass.mid")}
        (tmp_path / ".muse" / "snapshot_manifest.json").write_text(json.dumps(manifest))
        result = check_manifest_consistency(tmp_path)
        assert result.passed

    def test_orphaned_file_produces_error(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        _commit_ref(tmp_path)
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        # Manifest claims bass.mid exists, but it's not on disk
        manifest = {"bass.mid": "abc123"}
        (tmp_path / ".muse" / "snapshot_manifest.json").write_text(json.dumps(manifest))
        result = check_manifest_consistency(tmp_path)
        assert not result.passed
        assert any(i.severity == ValidationSeverity.ERROR for i in result.issues)
        assert any("bass.mid" in i.path for i in result.issues)

    def test_unregistered_file_produces_warning(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        _commit_ref(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_valid_midi(workdir / "lead.mid")
        # Empty committed manifest — lead.mid is unregistered
        (tmp_path / ".muse" / "snapshot_manifest.json").write_text(json.dumps({}))
        result = check_manifest_consistency(tmp_path)
        assert not result.passed
        assert any(i.severity == ValidationSeverity.WARN for i in result.issues)
        assert any("lead.mid" in i.path for i in result.issues)

    def test_malformed_snapshot_cache_produces_error(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        _commit_ref(tmp_path)
        (tmp_path / ".muse" / "snapshot_manifest.json").write_text("{broken json")
        result = check_manifest_consistency(tmp_path)
        assert not result.passed
        assert any(i.severity == ValidationSeverity.ERROR for i in result.issues)


# ---------------------------------------------------------------------------
# run_validate orchestrator
# ---------------------------------------------------------------------------


class TestRunValidate:
    def test_clean_repo_returns_clean(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        result = run_validate(tmp_path)
        assert result.clean
        assert not result.has_errors
        assert not result.has_warnings

    def test_invalid_midi_makes_has_errors_true(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "corrupt.mid")
        result = run_validate(tmp_path)
        assert not result.clean
        assert result.has_errors

    def test_bad_section_name_makes_has_warnings_true(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        (workdir / "BadSection").mkdir()
        result = run_validate(tmp_path)
        assert not result.clean
        assert result.has_warnings
        assert not result.has_errors

    def test_to_dict_is_serialisable(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        result = run_validate(tmp_path)
        data = result.to_dict()
        # Must be JSON-serialisable without error
        serialised = json.dumps(data)
        assert "checks" in json.loads(serialised)

    def test_track_filter_is_forwarded(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "drums.mid")
        _make_valid_midi(workdir / "bass.mid")
        result = run_validate(tmp_path, track_filter="bass")
        # drums.mid error should be excluded by filter
        assert result.clean

    def test_all_checks_are_present(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        result = run_validate(tmp_path)
        check_names = {c.name for c in result.checks}
        assert "midi_integrity" in check_names
        assert "manifest_consistency" in check_names
        assert "no_duplicate_tracks" in check_names
        assert "section_naming" in check_names
        assert "emotion_tags" in check_names

    def test_fixes_applied_empty_when_no_auto_fix(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        result = run_validate(tmp_path, auto_fix=False)
        assert result.fixes_applied == []


# ---------------------------------------------------------------------------
# apply_fixes
# ---------------------------------------------------------------------------


class TestApplyFixes:
    def test_apply_fixes_returns_list(self, tmp_path: pathlib.Path) -> None:
        from maestro.services.muse_validate import ValidationIssue
        issues = [
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check="midi_integrity",
                path="bass.mid",
                message="Corrupted",
            )
        ]
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        result = apply_fixes(workdir, issues)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestValidateCli:
    def test_clean_repo_exits_0(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        (tmp_path / "muse-work").mkdir()
        result = runner.invoke(
            cli, ["validate"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "pass" in result.output.lower()

    def test_invalid_midi_exits_1(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "corrupt.mid")
        result = runner.invoke(
            cli, ["validate"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_strict_mode_exits_2_on_warnings(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        (workdir / "BadSection").mkdir()
        result = runner.invoke(
            cli, ["validate", "--strict"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 2

    def test_clean_repo_strict_still_exits_0(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        (tmp_path / "muse-work").mkdir()
        result = runner.invoke(
            cli, ["validate", "--strict"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 0

    def test_json_output_is_parseable(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        (tmp_path / "muse-work").mkdir()
        result = runner.invoke(
            cli, ["validate", "--json"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "clean" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)

    def test_json_output_has_issues_on_error(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "bad.mid")
        result = runner.invoke(
            cli, ["validate", "--json"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["has_errors"] is True

    def test_not_a_repo_exits_2(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["validate"], env={"MUSE_REPO_ROOT": str(tmp_path / "nonexistent")}
        )
        assert result.exit_code == ExitCode.REPO_NOT_FOUND

    def test_track_flag_scopes_checks(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        workdir = tmp_path / "muse-work"
        _make_invalid_midi(workdir / "drums.mid")
        _make_valid_midi(workdir / "bass.mid")
        result = runner.invoke(
            cli, ["validate", "--track", "bass"],
            env={"MUSE_REPO_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0

    def test_fix_flag_runs_without_error(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        (tmp_path / "muse-work").mkdir()
        result = runner.invoke(
            cli, ["validate", "--fix"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 0

    def test_help_text_is_accessible(self) -> None:
        result = runner.invoke(cli, ["validate", "--help"])
        assert result.exit_code == 0
        assert "integrity" in result.output.lower() or "validate" in result.output.lower()
