"""Tests for ``muse form`` -- CLI interface, flag parsing, and stub output format.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised
end-to-end.

Async core tests call the internal async functions directly with an in-memory
SQLite session (the stub does not query the DB; the session satisfies the
signature contract only).
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401  -- registers MuseCli* with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.form import (
    FormAnalysisResult,
    FormHistoryEntry,
    FormSection,
    ROLE_LABELS,
    _VALID_ROLES,
    _form_detect_async,
    _form_history_async,
    _form_set_async,
    _render_form_text,
    _render_history_text,
    _render_map_text,
    _sections_to_form_string,
    _stub_form_sections,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout with one commit ref."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text(
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    )
    return rid


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub form analysis does not query it)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Unit -- stub data
# ---------------------------------------------------------------------------


def test_stub_form_sections_returns_sections() -> None:
    """Stub produces at least one section."""
    sections = _stub_form_sections()
    assert len(sections) > 0


def test_stub_form_sections_have_valid_structure() -> None:
    """Every stub section has label, role, and a sequential index."""
    sections = _stub_form_sections()
    for i, sec in enumerate(sections):
        assert isinstance(sec["label"], str)
        assert isinstance(sec["role"], str)
        assert sec["index"] == i


def test_stub_form_sections_include_verse_and_chorus_roles() -> None:
    """Stub includes verse and chorus roles -- the minimal pop form."""
    sections = _stub_form_sections()
    roles = {s["role"] for s in sections}
    assert "verse" in roles
    assert "chorus" in roles


def test_sections_to_form_string_pipe_separated() -> None:
    """_sections_to_form_string produces a pipe-separated label sequence."""
    sections = [
        FormSection(label="intro", role="intro", index=0),
        FormSection(label="A", role="verse", index=1),
        FormSection(label="B", role="chorus", index=2),
    ]
    result = _sections_to_form_string(sections)
    assert result == "intro | A | B"


def test_role_labels_constant_is_non_empty() -> None:
    """ROLE_LABELS contains the standard section vocabulary."""
    assert "verse" in ROLE_LABELS
    assert "chorus" in ROLE_LABELS
    assert "bridge" in ROLE_LABELS


def test_valid_roles_frozenset_matches_role_labels() -> None:
    """_VALID_ROLES is the frozenset of ROLE_LABELS."""
    assert _VALID_ROLES == frozenset(ROLE_LABELS)


# ---------------------------------------------------------------------------
# Unit -- renderers
# ---------------------------------------------------------------------------


def test_render_form_text_contains_commit_and_form() -> None:
    """_render_form_text includes the commit ref and form string."""
    result = FormAnalysisResult(
        commit="a1b2c3d4",
        branch="main",
        form_string="intro | A | B | outro",
        sections=[
            FormSection(label="intro", role="intro", index=0),
            FormSection(label="A", role="verse", index=1),
            FormSection(label="B", role="chorus", index=2),
            FormSection(label="outro", role="outro", index=3),
        ],
        source="stub",
    )
    text = _render_form_text(result)
    assert "a1b2c3d4" in text
    assert "intro | A | B | outro" in text
    assert "Sections:" in text


def test_render_map_text_contains_commit() -> None:
    """_render_map_text includes the commit ref."""
    result = FormAnalysisResult(
        commit="deadbeef",
        branch="main",
        form_string="A | B | A",
        sections=[
            FormSection(label="A", role="verse", index=0),
            FormSection(label="B", role="chorus", index=1),
            FormSection(label="A", role="verse", index=2),
        ],
        source="stub",
    )
    text = _render_map_text(result)
    assert "deadbeef" in text
    assert "A" in text
    assert "B" in text


def test_render_history_text_formats_entries() -> None:
    """_render_history_text shows position, commit, and form string."""
    entry = FormHistoryEntry(
        position=1,
        result=FormAnalysisResult(
            commit="abc123",
            branch="main",
            form_string="A | B | A",
            sections=[],
            source="stub",
        ),
    )
    text = _render_history_text([entry])
    assert "#1" in text
    assert "abc123" in text
    assert "A | B | A" in text


def test_render_history_text_empty_returns_placeholder() -> None:
    """_render_history_text with no entries returns a placeholder string."""
    text = _render_history_text([])
    assert "no form history" in text.lower()


# ---------------------------------------------------------------------------
# Async core -- _form_detect_async (test_form_detects_verse_chorus_structure)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_form_detects_verse_chorus_structure(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_detect_async returns a result with verse and chorus in the stub form."""
    _init_muse_repo(tmp_path)
    result = await _form_detect_async(
        root=tmp_path, session=db_session, commit=None
    )
    assert result["source"] == "stub"
    roles = {s["role"] for s in result["sections"]}
    assert "verse" in roles
    assert "chorus" in roles
    assert " | " in result["form_string"]


@pytest.mark.anyio
async def test_form_detect_resolves_commit_ref(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_detect_async includes the abbreviated commit SHA in the result."""
    _init_muse_repo(tmp_path)
    result = await _form_detect_async(
        root=tmp_path, session=db_session, commit=None
    )
    assert result["commit"] == "a1b2c3d4"
    assert result["branch"] == "main"


@pytest.mark.anyio
async def test_form_detect_explicit_commit_ref(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """An explicit commit ref is preserved in the result unchanged."""
    _init_muse_repo(tmp_path)
    result = await _form_detect_async(
        root=tmp_path, session=db_session, commit="deadbeef"
    )
    assert result["commit"] == "deadbeef"


@pytest.mark.anyio
async def test_form_detect_json_serializable(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """FormAnalysisResult returned by detect is fully JSON-serialisable."""
    _init_muse_repo(tmp_path)
    result = await _form_detect_async(
        root=tmp_path, session=db_session, commit=None
    )
    serialised = json.dumps(dict(result))
    parsed = json.loads(serialised)
    assert parsed["form_string"] == result["form_string"]
    assert isinstance(parsed["sections"], list)


# ---------------------------------------------------------------------------
# Async core -- _form_set_async (test_form_set_stores_annotation)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_form_set_stores_annotation(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_set_async persists the annotation to .muse/form_annotation.json."""
    _init_muse_repo(tmp_path)
    result = await _form_set_async(
        root=tmp_path, session=db_session, form_value="verse-chorus-verse-chorus"
    )
    assert result["source"] == "annotation"
    annotation_path = tmp_path / ".muse" / "form_annotation.json"
    assert annotation_path.exists()
    data = json.loads(annotation_path.read_text())
    assert data["source"] == "annotation"
    assert "verse" in data["form_string"].lower()
    assert "chorus" in data["form_string"].lower()


@pytest.mark.anyio
async def test_form_set_pipe_separated_form(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_set_async parses pipe-separated form strings correctly."""
    _init_muse_repo(tmp_path)
    result = await _form_set_async(
        root=tmp_path,
        session=db_session,
        form_value="Intro | A | B | A | Bridge | A | Outro",
    )
    assert result["source"] == "annotation"
    labels = [s["label"] for s in result["sections"]]
    assert labels == ["Intro", "A", "B", "A", "Bridge", "A", "Outro"]


@pytest.mark.anyio
async def test_form_set_aaba_shorthand(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_set_async handles space-separated shorthand like 'A A B A'."""
    _init_muse_repo(tmp_path)
    result = await _form_set_async(
        root=tmp_path, session=db_session, form_value="A A B A"
    )
    labels = [s["label"] for s in result["sections"]]
    assert labels == ["A", "A", "B", "A"]
    assert result["form_string"] == "A | A | B | A"


# ---------------------------------------------------------------------------
# Async core -- _form_history_async (test_form_history_shows_restructure)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_form_history_shows_restructure(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_form_history_async returns at least one entry with form data."""
    _init_muse_repo(tmp_path)
    entries = await _form_history_async(root=tmp_path, session=db_session)
    assert len(entries) >= 1
    first = entries[0]
    assert first["position"] == 1
    assert " | " in first["result"]["form_string"]


@pytest.mark.anyio
async def test_form_history_entry_has_commit_and_branch(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """History entries carry commit and branch metadata."""
    _init_muse_repo(tmp_path)
    entries = await _form_history_async(root=tmp_path, session=db_session)
    first = entries[0]
    assert first["result"]["commit"] != ""
    assert first["result"]["branch"] == "main"


# ---------------------------------------------------------------------------
# CLI integration -- CliRunner
# ---------------------------------------------------------------------------


def test_cli_form_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse form`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["form"], catch_exceptions=False)
    finally:
        os.chdir(prev)
    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_form_help_lists_flags() -> None:
    """``muse form --help`` shows all documented flags."""
    result = runner.invoke(cli, ["form", "--help"])
    assert result.exit_code == 0
    for flag in ("--set", "--detect", "--map", "--history", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help"


def test_cli_form_appears_in_muse_help() -> None:
    """``muse --help`` lists the form subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "form" in result.output
