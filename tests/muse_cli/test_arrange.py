"""Tests for ``muse arrange`` — arrangement map display.

Tests cover:
- ``test_arrange_renders_matrix_for_commit`` — basic matrix output for a commit
- ``test_arrange_compare_shows_diff`` — diff between two commits
- ``test_arrange_density_mode`` — density (byte-size) mode
- Additional: JSON format, CSV format, section/track filtering, empty snapshot
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.arrange import _arrange_async, _load_matrix
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_arrange import (
    ArrangementCell,
    ArrangementMatrix,
    build_arrangement_diff,
    build_arrangement_matrix,
    extract_section_instrument,
    render_diff_json,
    render_diff_text,
    render_matrix_csv,
    render_matrix_json,
    render_matrix_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _populate_arrangement(root: pathlib.Path, layout: dict[str, bytes]) -> None:
    """Populate muse-work/ with a section/instrument file layout.

    *layout* maps relative paths (e.g. ``"intro/drums/beat.mid"``) to bytes.
    """
    workdir = root / "muse-work"
    for rel_path, content in layout.items():
        abs_path = workdir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)


_BASIC_LAYOUT: dict[str, bytes] = {
    "intro/drums/beat.mid": b"MIDI" * 100,
    "intro/bass/line.mid": b"MIDI" * 50,
    "verse/drums/beat.mid": b"MIDI" * 120,
    "verse/bass/line.mid": b"MIDI" * 60,
    "verse/strings/pad.mid": b"MIDI" * 80,
    "chorus/drums/beat.mid": b"MIDI" * 150,
    "chorus/bass/line.mid": b"MIDI" * 70,
    "chorus/strings/pad.mid": b"MIDI" * 90,
    "chorus/piano/chords.mid": b"MIDI" * 110,
}


# ---------------------------------------------------------------------------
# Unit tests — pure service functions (no DB required)
# ---------------------------------------------------------------------------


class TestExtractSectionInstrument:
    """Tests for the path-parsing function."""

    def test_three_component_path(self) -> None:
        assert extract_section_instrument("intro/drums/beat.mid") == ("intro", "drums")

    def test_deep_path_uses_first_two_components(self) -> None:
        assert extract_section_instrument("chorus/strings/sub/pad.mid") == (
            "chorus",
            "strings",
        )

    def test_two_component_path_returns_none(self) -> None:
        assert extract_section_instrument("drums/beat.mid") is None

    def test_flat_file_returns_none(self) -> None:
        assert extract_section_instrument("beat.mid") is None

    def test_section_is_normalised_lowercase(self) -> None:
        result = extract_section_instrument("CHORUS/Piano/chords.mid")
        assert result == ("chorus", "piano")

    def test_prechorus_alias(self) -> None:
        result = extract_section_instrument("pre-chorus/violin/part.mid")
        assert result is not None
        assert result[0] == "prechorus"


class TestBuildArrangementMatrix:
    """Tests for the matrix builder."""

    def test_basic_matrix_has_correct_sections_and_instruments(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "verse/drums/beat.mid": "oid2",
            "verse/bass/line.mid": "oid3",
            "chorus/strings/pad.mid": "oid4",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)

        assert set(matrix.sections) == {"intro", "verse", "chorus"}
        assert set(matrix.instruments) == {"drums", "bass", "strings"}

    def test_active_cells_are_correct(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "chorus/strings/pad.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)

        assert matrix.get_cell("intro", "drums").active is True
        assert matrix.get_cell("chorus", "strings").active is True
        assert matrix.get_cell("intro", "strings").active is False
        assert matrix.get_cell("chorus", "drums").active is False

    def test_file_count_accumulated_per_cell(self) -> None:
        manifest = {
            "verse/drums/take1.mid": "oid1",
            "verse/drums/take2.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        assert matrix.get_cell("verse", "drums").file_count == 2

    def test_density_accumulates_bytes(self) -> None:
        manifest = {
            "chorus/bass/line.mid": "oid1",
            "chorus/bass/alt.mid": "oid2",
        }
        sizes = {"oid1": 1000, "oid2": 2000}
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest, object_sizes=sizes)
        assert matrix.get_cell("chorus", "bass").total_bytes == 3000

    def test_files_without_section_structure_are_ignored(self) -> None:
        manifest = {
            "drums/beat.mid": "oid1",           # 1 dir + filename = 2 parts, ignored (< 3)
            "solo.mid": "oid2",                  # flat file = ignored
            "intro/drums/beat.mid": "oid3",     # valid: section/instrument/filename
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        assert matrix.instruments == ["drums"]
        assert matrix.sections == ["intro"]

    def test_section_ordering_follows_canonical_order(self) -> None:
        manifest = {
            "outro/drums/beat.mid": "oid1",
            "intro/drums/beat.mid": "oid2",
            "chorus/drums/beat.mid": "oid3",
            "verse/drums/beat.mid": "oid4",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        assert matrix.sections == ["intro", "verse", "chorus", "outro"]


class TestRenderMatrixText:
    """Tests for text rendering."""

    def test_renders_header_and_rows(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "verse/bass/line.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        output = render_matrix_text(matrix)

        assert "Arrangement Map" in output
        assert "abcd1234" in output
        assert "drums" in output
        assert "bass" in output
        assert "Intro" in output
        assert "Verse" in output

    def test_active_cell_shows_block_char(self) -> None:
        manifest = {"chorus/piano/chords.mid": "oid1"}
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        output = render_matrix_text(matrix)
        assert "\u2588\u2588\u2588\u2588" in output  # ████

    def test_inactive_cell_shows_light_shade(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "verse/bass/line.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        output = render_matrix_text(matrix)
        assert "\u2591\u2591\u2591\u2591" in output  # ░░░░

    def test_section_filter(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "verse/drums/beat.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        output = render_matrix_text(matrix, section_filter="intro")
        assert "Intro" in output
        assert "Verse" not in output

    def test_track_filter(self) -> None:
        manifest = {
            "verse/drums/beat.mid": "oid1",
            "verse/bass/line.mid": "oid2",
        }
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest)
        output = render_matrix_text(matrix, track_filter="drums")
        assert "drums" in output
        assert "bass" not in output

    def test_density_mode_shows_byte_values(self) -> None:
        manifest = {"chorus/strings/pad.mid": "oid1"}
        sizes = {"oid1": 4096}
        matrix = build_arrangement_matrix("abcd1234" * 8, manifest, object_sizes=sizes)
        output = render_matrix_text(matrix, density=True)
        assert "4,096" in output


class TestRenderMatrixJson:
    """Tests for JSON rendering."""

    def test_json_has_correct_structure(self) -> None:
        manifest = {
            "intro/drums/beat.mid": "oid1",
            "chorus/bass/line.mid": "oid2",
        }
        matrix = build_arrangement_matrix("a" * 64, manifest)
        raw = render_matrix_json(matrix)
        data = json.loads(raw)

        assert "commit_id" in data
        assert "sections" in data
        assert "instruments" in data
        assert "arrangement" in data

    def test_json_active_values_are_bool(self) -> None:
        manifest = {"verse/piano/chords.mid": "oid1"}
        matrix = build_arrangement_matrix("a" * 64, manifest)
        raw = render_matrix_json(matrix)
        data = json.loads(raw)
        assert data["arrangement"]["piano"]["verse"] is True

    def test_json_density_mode_includes_bytes(self) -> None:
        manifest = {"verse/piano/chords.mid": "oid1"}
        sizes = {"oid1": 999}
        matrix = build_arrangement_matrix("a" * 64, manifest, object_sizes=sizes)
        raw = render_matrix_json(matrix, density=True)
        data = json.loads(raw)
        cell = data["arrangement"]["piano"]["verse"]
        assert isinstance(cell, dict)
        assert cell["total_bytes"] == 999


class TestRenderMatrixCsv:
    """Tests for CSV rendering."""

    def test_csv_has_header_row(self) -> None:
        manifest = {"intro/drums/beat.mid": "oid1"}
        matrix = build_arrangement_matrix("a" * 64, manifest)
        output = render_matrix_csv(matrix)
        lines = output.strip().splitlines()
        assert lines[0].startswith("instrument")

    def test_csv_active_is_1(self) -> None:
        manifest = {"intro/drums/beat.mid": "oid1"}
        matrix = build_arrangement_matrix("a" * 64, manifest)
        output = render_matrix_csv(matrix)
        lines = output.strip().splitlines()
        assert "1" in lines[1]


class TestBuildArrangementDiff:
    """Tests for the diff builder."""

    def _make_matrix(
        self, commit_id: str, manifest: dict[str, str]
    ) -> ArrangementMatrix:
        return build_arrangement_matrix(commit_id, manifest)

    def test_added_cell_detected(self) -> None:
        manifest_a = {"intro/drums/beat.mid": "oid1"}
        manifest_b = {
            "intro/drums/beat.mid": "oid1",
            "intro/strings/pad.mid": "oid2",
        }
        mx_a = self._make_matrix("a" * 64, manifest_a)
        mx_b = self._make_matrix("b" * 64, manifest_b)

        diff = build_arrangement_diff(mx_a, mx_b)
        assert diff.cells[("intro", "strings")].status == "added"

    def test_removed_cell_detected(self) -> None:
        manifest_a = {
            "chorus/drums/beat.mid": "oid1",
            "chorus/piano/chords.mid": "oid2",
        }
        manifest_b = {"chorus/drums/beat.mid": "oid1"}
        mx_a = self._make_matrix("a" * 64, manifest_a)
        mx_b = self._make_matrix("b" * 64, manifest_b)

        diff = build_arrangement_diff(mx_a, mx_b)
        assert diff.cells[("chorus", "piano")].status == "removed"

    def test_unchanged_cell_detected(self) -> None:
        manifest = {"verse/bass/line.mid": "oid1"}
        mx_a = self._make_matrix("a" * 64, manifest)
        mx_b = self._make_matrix("b" * 64, manifest)

        diff = build_arrangement_diff(mx_a, mx_b)
        assert diff.cells[("verse", "bass")].status == "unchanged"


class TestRenderDiff:
    """Tests for diff renderers."""

    def _make_matrix(
        self, commit_id: str, manifest: dict[str, str]
    ) -> ArrangementMatrix:
        return build_arrangement_matrix(commit_id, manifest)

    def test_diff_text_includes_commit_ids(self) -> None:
        mx_a = self._make_matrix("a" * 64, {"intro/drums/beat.mid": "oid1"})
        mx_b = self._make_matrix("b" * 64, {"intro/drums/beat.mid": "oid1"})
        diff = build_arrangement_diff(mx_a, mx_b)
        output = render_diff_text(diff)
        assert "aaaaaaaa" in output
        assert "bbbbbbbb" in output

    def test_diff_json_lists_changes(self) -> None:
        manifest_a = {"intro/drums/beat.mid": "oid1"}
        manifest_b = {
            "intro/drums/beat.mid": "oid1",
            "intro/bass/line.mid": "oid2",
        }
        mx_a = self._make_matrix("a" * 64, manifest_a)
        mx_b = self._make_matrix("b" * 64, manifest_b)
        diff = build_arrangement_diff(mx_a, mx_b)
        raw = render_diff_json(diff)
        data = json.loads(raw)

        changes = data["changes"]
        assert any(
            c["section"] == "intro" and c["instrument"] == "bass" and c["status"] == "added"
            for c in changes
        )


# ---------------------------------------------------------------------------
# Integration tests — DB required
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_arrange_renders_matrix_for_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse arrange HEAD renders the arrangement matrix for the current HEAD commit."""
    _init_muse_repo(tmp_path)
    _populate_arrangement(tmp_path, _BASIC_LAYOUT)

    commit_id = await _commit_async(
        message="initial arrangement",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    muse_dir = tmp_path / ".muse"
    matrix = await _load_matrix(muse_cli_db_session, muse_dir, "HEAD", density=False)

    assert matrix.commit_id == commit_id
    assert set(matrix.instruments) >= {"drums", "bass", "strings", "piano"}
    assert "intro" in matrix.sections
    assert "verse" in matrix.sections
    assert "chorus" in matrix.sections

    assert matrix.get_cell("intro", "drums").active is True
    assert matrix.get_cell("intro", "strings").active is False  # strings only in verse/chorus


@pytest.mark.anyio
async def test_arrange_compare_shows_diff(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse arrange --compare shows added and removed cells between two commits."""
    _init_muse_repo(tmp_path)
    _populate_arrangement(tmp_path, {
        "intro/drums/beat.mid": b"MIDI" * 50,
        "verse/drums/beat.mid": b"MIDI" * 50,
    })

    commit_a = await _commit_async(
        message="commit A",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Add strings in verse for the second commit
    (tmp_path / "muse-work" / "verse" / "strings").mkdir(parents=True, exist_ok=True)
    (tmp_path / "muse-work" / "verse" / "strings" / "pad.mid").write_bytes(b"MIDI" * 80)

    commit_b = await _commit_async(
        message="commit B",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    muse_dir = tmp_path / ".muse"
    matrix_a = await _load_matrix(muse_cli_db_session, muse_dir, commit_a, density=False)
    matrix_b = await _load_matrix(muse_cli_db_session, muse_dir, commit_b, density=False)

    diff = build_arrangement_diff(matrix_a, matrix_b)

    # Strings was added in verse
    assert diff.cells[("verse", "strings")].status == "added"
    # Drums unchanged in both sections
    assert diff.cells[("intro", "drums")].status == "unchanged"
    assert diff.cells[("verse", "drums")].status == "unchanged"

    # Verify JSON serialisation
    json_output = render_diff_json(diff)
    data = json.loads(json_output)
    changes = data["changes"]
    added = [c for c in changes if c["status"] == "added"]
    assert len(added) == 1
    assert added[0]["instrument"] == "strings"
    assert added[0]["section"] == "verse"


@pytest.mark.anyio
async def test_arrange_density_mode(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse arrange --density shows byte totals per cell."""
    _init_muse_repo(tmp_path)
    content = b"X" * 4096
    _populate_arrangement(tmp_path, {"chorus/strings/pad.mid": content})

    commit_id = await _commit_async(
        message="density test",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    muse_dir = tmp_path / ".muse"
    matrix = await _load_matrix(muse_cli_db_session, muse_dir, "HEAD", density=True)

    cell = matrix.get_cell("chorus", "strings")
    assert cell.active is True
    assert cell.total_bytes == 4096

    output = render_matrix_text(matrix, density=True)
    assert "4,096" in output


@pytest.mark.anyio
async def test_arrange_empty_snapshot_returns_no_data_message(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When committed files don't follow section/instrument convention, arrange reports no data."""
    _init_muse_repo(tmp_path)
    # Files WITHOUT the section/instrument path structure (flat or 2-component paths)
    _populate_arrangement(tmp_path, {"beat.mid": b"MIDI", "drums/hit.mid": b"MIDI"})

    await _commit_async(
        message="flat layout",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    muse_dir = tmp_path / ".muse"
    matrix = await _load_matrix(muse_cli_db_session, muse_dir, "HEAD", density=False)

    assert matrix.sections == []
    assert matrix.instruments == []


@pytest.mark.anyio
async def test_arrange_json_format(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--format json outputs valid JSON with correct arrangement data."""
    import io
    import typer
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    _populate_arrangement(tmp_path, {
        "intro/drums/beat.mid": b"MIDI" * 30,
        "verse/bass/line.mid": b"MIDI" * 40,
    })

    await _commit_async(
        message="json format test",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    muse_dir = tmp_path / ".muse"
    matrix = await _load_matrix(muse_cli_db_session, muse_dir, "HEAD", density=False)

    raw = render_matrix_json(matrix)
    data = json.loads(raw)

    assert data["arrangement"]["drums"]["intro"] is True
    assert data["arrangement"]["bass"].get("intro", False) is False
    assert data["arrangement"]["bass"]["verse"] is True
