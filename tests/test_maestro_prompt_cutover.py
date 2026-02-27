"""Tests for the MAESTRO PROMPT hard cutover.

Verifies:
1. Parser accepts MAESTRO PROMPT and rejects STORI PROMPT with a typed error.
2. Near-miss headers (wrong case, extra spaces, underscore) are rejected.
3. BOM and leading whitespace are tolerated before the header.
4. Prompt pool contains only MAESTRO PROMPT headers.
5. No legacy "STORI PROMPT" symbols remain in production code or docs.
"""
from __future__ import annotations

import pathlib

import pytest

from maestro.prompts import MaestroPrompt, parse_prompt
from maestro.prompts.errors import UnsupportedPromptHeader


# ─── Parser: MAESTRO PROMPT acceptance ──────────────────────────────────────


class TestMaestroPromptAccepted:
    """Valid MAESTRO PROMPT inputs are parsed correctly."""

    def test_basic_compose(self) -> None:
        result = parse_prompt("MAESTRO PROMPT\nMode: compose\nRequest: make a beat")
        assert isinstance(result, MaestroPrompt)
        assert result.mode == "compose"
        assert result.request == "make a beat"

    def test_with_bom(self) -> None:
        result = parse_prompt("\ufeffMAESTRO PROMPT\nMode: compose\nRequest: go")
        assert isinstance(result, MaestroPrompt)
        assert result.mode == "compose"

    def test_with_leading_whitespace_lines(self) -> None:
        result = parse_prompt("\n\n  \nMAESTRO PROMPT\nMode: compose\nRequest: go")
        assert isinstance(result, MaestroPrompt)

    def test_trailing_whitespace_on_header(self) -> None:
        result = parse_prompt("MAESTRO PROMPT   \nMode: compose\nRequest: go")
        assert isinstance(result, MaestroPrompt)


# ─── Parser: STORI PROMPT rejection ────────────────────────────────────────


class TestStoriPromptRejected:
    """Legacy STORI PROMPT header raises UnsupportedPromptHeader."""

    def test_exact_stori_prompt(self) -> None:
        with pytest.raises(UnsupportedPromptHeader) as exc_info:
            parse_prompt("STORI PROMPT\nMode: compose\nRequest: go")
        assert exc_info.value.header == "STORI PROMPT"
        assert "MAESTRO PROMPT" in str(exc_info.value)

    def test_stori_prompt_lowercase(self) -> None:
        with pytest.raises(UnsupportedPromptHeader):
            parse_prompt("stori prompt\nMode: compose\nRequest: go")

    def test_stori_prompt_mixed_case(self) -> None:
        with pytest.raises(UnsupportedPromptHeader):
            parse_prompt("Stori Prompt\nMode: compose\nRequest: go")

    def test_stori_prompt_with_bom(self) -> None:
        with pytest.raises(UnsupportedPromptHeader):
            parse_prompt("\ufeffSTORI PROMPT\nMode: compose\nRequest: go")


# ─── Parser: near-miss header rejection ─────────────────────────────────────


class TestNearMissHeadersRejected:
    """Typos and variations of MAESTRO PROMPT fall through to NL (return None)."""

    def test_lowercase_maestro_prompt(self) -> None:
        assert parse_prompt("maestro prompt\nMode: compose\nRequest: go") is None

    def test_mixed_case_maestro_prompt(self) -> None:
        assert parse_prompt("Maestro Prompt\nMode: compose\nRequest: go") is None

    def test_underscore_maestro_prompt(self) -> None:
        assert parse_prompt("MAESTRO_PROMPT\nMode: compose\nRequest: go") is None

    def test_double_space_maestro_prompt(self) -> None:
        assert parse_prompt("MAESTRO  PROMPT\nMode: compose\nRequest: go") is None

    def test_extra_word(self) -> None:
        assert parse_prompt("MAESTRO PROMPT V2\nMode: compose\nRequest: go") is None


# ─── Prompt pool guard ──────────────────────────────────────────────────────


class TestPromptPoolGuard:
    """Every curated prompt in the pool must use MAESTRO PROMPT header."""

    def test_all_fullprompts_use_maestro_header(self) -> None:
        from maestro.data.maestro_ui.prompt_pool import PROMPT_POOL

        for item in PROMPT_POOL:
            assert item.full_prompt.startswith("MAESTRO PROMPT"), (
                f"Pool item '{item.id}' uses legacy header — "
                f"starts with: {item.full_prompt[:30]!r}"
            )

    def test_no_stori_prompt_in_pool(self) -> None:
        from maestro.data.maestro_ui.prompt_pool import PROMPT_POOL

        for item in PROMPT_POOL:
            assert "STORI PROMPT" not in item.full_prompt, (
                f"Pool item '{item.id}' still contains 'STORI PROMPT'"
            )


# ─── Repo-wide no-legacy guard ──────────────────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_LEGACY_PATTERNS = [
    "STORI PROMPT",
    "stori_prompt_spec",
    "StoriPrompt",
]

_ALLOWLIST_PATHS = {
    "maestro/prompts/errors.py",
    "maestro/prompts/parser.py",
    "maestro/api/routes/maestro.py",
    "tests/test_maestro_prompt_cutover.py",
}

_ALLOWLIST_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}


def _scan_files() -> list[tuple[str, int, str, str]]:
    """Scan .py and .md files for legacy patterns.

    Returns list of (relative_path, line_number, line_content, pattern).
    """
    hits: list[tuple[str, int, str, str]] = []

    for ext in ("py", "md"):
        for path in _REPO_ROOT.rglob(f"*.{ext}"):
            rel = str(path.relative_to(_REPO_ROOT))

            if any(part in _ALLOWLIST_DIRS for part in path.parts):
                continue
            if rel in _ALLOWLIST_PATHS:
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                for pattern in _LEGACY_PATTERNS:
                    if pattern in line:
                        hits.append((rel, i, line.strip(), pattern))

    return hits


class TestNoLegacyPatterns:
    """No production code or docs should reference the legacy prompt name."""

    def test_no_stori_prompt_in_codebase(self) -> None:
        hits = _scan_files()
        if hits:
            report = "\n".join(
                f"  {path}:{lineno}: {pattern!r} in: {line[:120]}"
                for path, lineno, line, pattern in hits
            )
            pytest.fail(
                f"Found {len(hits)} legacy pattern(s) in production code/docs:\n{report}"
            )
