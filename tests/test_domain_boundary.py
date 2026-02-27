"""Domain-bleed regression tests — enforce the DAW adapter boundary.

These static tests ensure that Maestro core does not bleed Stori-specific
vocabulary into its internals.  They run without network or Docker.

Three categories:
  a) **Forbidden imports** — Maestro core must not import maestro.daw.stori
     except in DI/wiring bootstrap modules.
  b) **Naming** — no ``class Stori*`` or ``def stori_*`` in Maestro core
     (except the adapter package).
  c) **Wire compatibility** — tool list endpoint returns the expected
     tool names; SSE event JSON shapes are unchanged.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "maestro"
CORE = APP / "core"
SERVICES = APP / "services"
API = APP / "api"
PROTOCOL = APP / "protocol"
MODELS = APP / "models"

# Modules that are allowed to import from maestro.daw.stori (DI / wiring / bootstrap)
_ALLOWED_STORI_IMPORTERS: frozenset[str] = frozenset({
    "maestro/main.py",
    "maestro/mcp/server.py",
    "maestro/mcp/stdio_server.py",
    "maestro/mcp/__init__.py",
    "maestro/mcp/tools/__init__.py",
    "maestro/core/tools/__init__.py",
    "maestro/core/executor/phases.py",
    "maestro/daw/stori/adapter.py",
    "maestro/daw/stori/tool_registry.py",
    "maestro/daw/stori/tool_schemas.py",
    "maestro/daw/stori/tool_names.py",
    "maestro/daw/stori/phase_map.py",
    "maestro/daw/stori/validation.py",
    "maestro/daw/stori/tools/__init__.py",
    "maestro/daw/stori/__init__.py",
    "maestro/daw/__init__.py",
    "maestro/daw/ports.py",
})


def _py_files_in(directory: Path) -> list[Path]:
    """Recursively collect .py files, skipping __pycache__."""
    return sorted(
        p for p in directory.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _relative(path: Path) -> str:
    """Return path relative to repo root as posix string."""
    return str(path.relative_to(ROOT))


# =========================================================================
# a) Forbidden imports: Maestro core must not import maestro.daw.stori
# =========================================================================


def _has_stori_import(path: Path) -> list[str]:
    """Return import strings that reference app.daw.stori in a file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "maestro.daw.stori" in node.module:
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "maestro.daw.stori" in alias.name:
                    violations.append(f"import {alias.name}")
    return violations


def _core_py_files() -> list[Path]:
    """All .py files in Maestro core packages (excluding DI/wiring)."""
    result: list[Path] = []
    for directory in [CORE, SERVICES, API, PROTOCOL, MODELS]:
        for p in _py_files_in(directory):
            rel = _relative(p)
            if rel not in _ALLOWED_STORI_IMPORTERS:
                result.append(p)
    return result


class TestForbiddenImports:
    """Maestro core must not import maestro.daw.stori except in wiring."""

    def test_no_stori_imports_in_core(self) -> None:
        """Core packages must not import from maestro.daw.stori."""
        violations: list[str] = []
        for path in _core_py_files():
            hits = _has_stori_import(path)
            if hits:
                rel = _relative(path)
                for h in hits:
                    violations.append(f"{rel}: {h}")

        if violations:
            msg = "Forbidden app.daw.stori imports in Maestro core:\n"
            msg += "\n".join(f"  - {v}" for v in violations)
            pytest.fail(msg)


# =========================================================================
# b) Naming: no class Stori* or def stori_* in Maestro core
# =========================================================================

_CLASS_RE = re.compile(r"^\s*class\s+Stori\w*", re.MULTILINE)
_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+stori_\w*", re.MULTILINE)

_NAMING_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "maestro/daw",
})


def _is_naming_excluded(path: Path) -> bool:
    """True if the file is in an excluded directory or in the allowed set."""
    rel = _relative(path)
    return any(rel.startswith(d) for d in _NAMING_EXCLUDED_DIRS)


class TestNaming:
    """No class Stori* or def stori_* in Maestro core packages."""

    def test_no_stori_class_in_core(self) -> None:
        violations: list[str] = []
        for path in _core_py_files():
            if _is_naming_excluded(path):
                continue
            source = path.read_text(encoding="utf-8")
            for match in _CLASS_RE.finditer(source):
                violations.append(f"{_relative(path)}: {match.group().strip()}")

        if violations:
            msg = "Stori-branded class names found in Maestro core:\n"
            msg += "\n".join(f"  - {v}" for v in violations)
            pytest.fail(msg)

    def test_no_stori_function_in_core(self) -> None:
        """No functions named stori_* outside the DAW adapter."""
        violations: list[str] = []
        for path in _core_py_files():
            if _is_naming_excluded(path):
                continue
            source = path.read_text(encoding="utf-8")
            for match in _FUNC_RE.finditer(source):
                violations.append(f"{_relative(path)}: {match.group().strip()}")

        if violations:
            msg = "stori_* function names found in Maestro core:\n"
            msg += "\n".join(f"  - {v}" for v in violations)
            pytest.fail(msg)


# =========================================================================
# c) Wire compatibility: tool list and SSE events
# =========================================================================


class TestWireCompatibility:
    """The wire contract (tool names, event types) must be unchanged."""

    def test_tool_list_unchanged(self) -> None:
        """All expected stori_* tool names are present in the registry."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS

        tool_names = {t["name"] for t in MCP_TOOLS}
        expected_core_tools = {
            "stori_read_project",
            "stori_create_project",
            "stori_set_tempo",
            "stori_set_key",
            "stori_add_midi_track",
            "stori_add_midi_region",
            "stori_add_notes",
            "stori_clear_notes",
            "stori_generate_midi",
            "stori_play",
            "stori_stop",
            "stori_add_insert_effect",
            "stori_add_send",
            "stori_ensure_bus",
            "stori_add_automation",
        }
        missing = expected_core_tools - tool_names
        assert not missing, f"Missing expected tools: {missing}"

    def test_all_tools_prefixed(self) -> None:
        """Every MCP tool name starts with stori_ (DAW vocabulary)."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS

        for tool in MCP_TOOLS:
            assert tool["name"].startswith("stori_"), (
                f"Tool {tool['name']} should start with 'stori_'"
            )

    def test_server_side_tools_subset(self) -> None:
        """SERVER_SIDE_TOOLS is a proper subset of all tool names."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS, SERVER_SIDE_TOOLS

        all_names = {t["name"] for t in MCP_TOOLS}
        assert SERVER_SIDE_TOOLS <= all_names
        assert "stori_generate_midi" in SERVER_SIDE_TOOLS

    def test_event_registry_unchanged(self) -> None:
        """All expected SSE event types are registered."""
        from maestro.protocol.registry import ALL_EVENT_TYPES

        expected = {
            "state", "reasoning", "reasoningEnd", "content",
            "status", "error", "complete", "plan", "planStepUpdate",
            "toolStart", "toolCall", "toolError",
            "preflight", "generatorStart", "generatorComplete",
            "agentComplete", "summary", "summary.final",
            "meta", "phrase", "done",
        }
        missing = expected - ALL_EVENT_TYPES
        assert not missing, f"Missing event types: {missing}"

    def test_mcp_server_name_unchanged(self) -> None:
        """The MCP server reports 'stori-daw' as its name (wire contract)."""
        from unittest.mock import MagicMock, patch

        with patch("maestro.config.get_settings") as mock_settings:
            from maestro.protocol.version import MAESTRO_VERSION
            mock_settings.return_value = MagicMock(app_version=MAESTRO_VERSION)
            from maestro.mcp.server import MaestroMCPServer
            server = MaestroMCPServer()

        info = server.get_server_info()
        assert info["name"] == "stori-daw"

    def test_daw_adapter_protocol(self) -> None:
        """StoriDAWAdapter satisfies the DAWAdapter protocol."""
        from maestro.daw.ports import DAWAdapter
        from maestro.daw.stori.adapter import StoriDAWAdapter

        assert isinstance(StoriDAWAdapter, type)
        adapter = StoriDAWAdapter()
        assert isinstance(adapter, DAWAdapter)
        assert len(adapter.registry.mcp_tools) > 0
        assert adapter.phase_for_tool("stori_set_tempo") == "setup"
        assert adapter.phase_for_tool("stori_add_notes") == "instrument"
        assert adapter.phase_for_tool("stori_ensure_bus") == "mixing"
