#!/usr/bin/env python3
"""Architectural boundary checker for Stori Maestro.

Run locally or in CI to verify that internal module boundaries have not
been violated.  Exits non-zero on any violation.

Usage:
    python scripts/check_boundaries.py
    docker compose exec maestro python scripts/check_boundaries.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ERRORS: list[str] = []


def _error(msg: str) -> None:
    ERRORS.append(msg)
    print(f"  ❌ {msg}", file=sys.stderr)


def _collect_imports(filepath: Path) -> list[str]:
    """Return all imported module strings from a Python file (top-level + lazy)."""
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _function_imports(filepath: Path, func_name: str) -> list[str]:
    """Return imports that appear inside a specific function body."""
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            imports: list[str] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        imports.append(alias.name)
                elif isinstance(child, ast.ImportFrom):
                    if child.module:
                        imports.append(child.module)
            return imports
    return []


def _function_params(filepath: Path, func_name: str) -> list[str]:
    """Return parameter names for a top-level function."""
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return [arg.arg for arg in node.args.args + node.args.kwonlyargs]
    return []


def _collect_import_names(filepath: Path) -> list[str]:
    """Return all imported symbol names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.append(alias.name)
    return names


# ── Rule 1: VariationService must not import StateStore or EntityRegistry ──

def check_variation_service_isolation() -> None:
    print("\n[Rule 1] VariationService must not import StateStore or EntityRegistry")
    variation_dir = ROOT / "app" / "services" / "variation"
    forbidden = {"app.core.state_store", "app.core.entity_registry"}

    for py_file in variation_dir.rglob("*.py"):
        imports = _collect_imports(py_file)
        for imp in imports:
            for fb in forbidden:
                if imp.startswith(fb):
                    rel = py_file.relative_to(ROOT)
                    _error(f"{rel} imports {imp}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 2: compute_variation_from_context must not have a `store` param ──

def check_muse_compute_purity() -> None:
    print("\n[Rule 2] compute_variation_from_context must be store-free")
    filepath = ROOT / "app" / "core" / "executor" / "variation.py"

    params = _function_params(filepath, "compute_variation_from_context")
    if "store" in params:
        _error("compute_variation_from_context has a 'store' parameter")

    func_imports = _function_imports(filepath, "compute_variation_from_context")
    forbidden = {"app.core.state_store", "app.core.entity_registry"}
    for imp in func_imports:
        for fb in forbidden:
            if imp.startswith(fb):
                _error(f"compute_variation_from_context imports {imp}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 3: apply_variation_phrases must not call get_or_create_store ──

def check_apply_no_store_lookup() -> None:
    print("\n[Rule 3] apply_variation_phrases must not import get_or_create_store")
    filepath = ROOT / "app" / "core" / "executor" / "apply.py"

    imports = _collect_imports(filepath)
    for imp in imports:
        if "get_or_create_store" in imp:
            _error(f"apply.py imports get_or_create_store")
            break

    source = filepath.read_text()
    if "get_or_create_store" in source:
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            if "get_or_create_store" in line and not line.strip().startswith("#"):
                if "TYPE_CHECKING" not in line:
                    _error(f"apply.py:{i} references get_or_create_store")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 4: No Muse-specific imports above maestro_composing ──

def check_muse_import_boundary() -> None:
    print("\n[Rule 4] Modules above maestro_composing must not import Muse models directly")
    muse_modules = {
        "app.models.variation",
        "app.services.variation",
    }

    filepath = ROOT / "app" / "core" / "maestro_handlers.py"
    imports = _collect_imports(filepath)
    for imp in imports:
        for mm in muse_modules:
            if imp.startswith(mm):
                _error(f"maestro_handlers.py imports {imp}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 5: apply_variation_phrases must not access store.registry ──

def check_apply_no_registry() -> None:
    print("\n[Rule 5] apply_variation_phrases must not access store.registry")
    filepath = ROOT / "app" / "core" / "executor" / "apply.py"

    tree = ast.parse(filepath.read_text())
    source_lines = filepath.read_text().splitlines()

    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for ln in range(node.lineno, node.end_lineno + 1):  # type: ignore[union-attr]
                docstring_lines.add(ln)

    for i, line in enumerate(source_lines, 1):
        if i in docstring_lines:
            continue
        if line.strip().startswith("#"):
            continue
        if "store.registry" in line or ".registry." in line:
            _error(f"apply.py:{i} accesses store.registry: {line.strip()}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 6: VariationContext must not contain StateStore ──

def check_variation_context_data_only() -> None:
    print("\n[Rule 6] VariationContext must not contain StateStore")
    filepath = ROOT / "app" / "core" / "executor" / "models.py"

    tree = ast.parse(filepath.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "VariationContext":
            for child in ast.walk(node):
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    if child.target.id == "store":
                        _error("VariationContext has a 'store' field")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 7: muse_repository must not import StateStore or executor ──

def check_muse_repository_isolation() -> None:
    print("\n[Rule 7] muse_repository must not import StateStore or executor")
    filepath = ROOT / "app" / "services" / "muse_repository.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_modules = {"app.core.state_store", "app.core.executor"}
    for imp in imports:
        for fb in forbidden_modules:
            if imp.startswith(fb):
                _error(f"muse_repository.py imports {imp}")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_repository.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 8: compute_variation_from_context must not import executor modules ──

def check_compute_no_executor_imports() -> None:
    print("\n[Rule 8] compute_variation_from_context must not import executor modules")
    filepath = ROOT / "app" / "core" / "executor" / "variation.py"

    func_imports = _function_imports(filepath, "compute_variation_from_context")
    for imp in func_imports:
        if "executor" in imp and "models" not in imp:
            _error(f"compute_variation_from_context imports executor module: {imp}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 9: muse_replay must not import StateStore, executor, or LLM ──

def check_muse_replay_isolation() -> None:
    print("\n[Rule 9] muse_replay must not import StateStore, executor, or LLM handlers")
    filepath = ROOT / "app" / "services" / "muse_replay.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_replay.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_replay.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 10: muse_drift must not import StateStore, executor, or LLM ──

def check_muse_drift_isolation() -> None:
    print("\n[Rule 10] muse_drift must not import StateStore, executor, or LLM handlers")
    filepath = ROOT / "app" / "services" / "muse_drift.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_drift.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_drift.py imports forbidden name: {n}")

    tree = ast.parse(filepath.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "get_or_create_store":
                _error("muse_drift.py calls get_or_create_store")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 11: controller matching must not import handlers ──

def check_controller_matching_isolation() -> None:
    print("\n[Rule 11] note_matching must not import handlers or StateStore")
    filepath = ROOT / "app" / "services" / "variation" / "note_matching.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"note_matching.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
    for n in names:
        if n in forbidden_names:
            _error(f"note_matching.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 12: commit route must stay orchestration-thin ──

def check_commit_route_thinness() -> None:
    print("\n[Rule 12] commit route must not import drift internals (only public API)")
    filepath = ROOT / "app" / "api" / "routes" / "variation" / "commit.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)

    allowed_drift = {"app.services.muse_drift", "app.services.muse_replay"}
    allowed_executor = {
        "app.core.executor",
        "app.core.executor.snapshots",
    }
    allowed_state = {"app.core.state_store"}

    for imp in imports:
        if "muse_drift" in imp or "muse_replay" in imp:
            if imp not in allowed_drift:
                _error(f"commit.py imports drift internal: {imp}")

        if "executor" in imp and imp not in allowed_executor:
            _error(f"commit.py imports executor internal: {imp}")

    names = _collect_import_names(filepath)
    allowed_drift_names = {
        "compute_drift_report", "CommitConflictPayload",
        "reconstruct_head_snapshot",
        "capture_base_snapshot",
    }
    forbidden_drift_internals = {"_fingerprint", "_combined_fingerprint", "RegionDriftSummary"}
    for n in names:
        if n in forbidden_drift_internals:
            _error(f"commit.py imports drift internal name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 13: muse_checkout must not import StateStore, executor, or handlers ──

def check_muse_checkout_isolation() -> None:
    print("\n[Rule 13] muse_checkout must not import StateStore, executor, or handlers")
    filepath = ROOT / "app" / "services" / "muse_checkout.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_checkout.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_checkout.py imports forbidden name: {n}")

    tree = ast.parse(filepath.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "get_or_create_store":
                _error("muse_checkout.py calls get_or_create_store")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 14: muse_checkout_executor must not import handlers or VariationService ──

def check_muse_checkout_executor_isolation() -> None:
    print("\n[Rule 14] muse_checkout_executor must not import handlers or VariationService")
    filepath = ROOT / "app" / "services" / "muse_checkout_executor.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {
        "maestro_handlers", "maestro_editing", "maestro_composing",
        "muse_replay", "variation.service", "variation.compute",
    }
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_checkout_executor.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"VariationService", "compute_variation_from_context"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_checkout_executor.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 15: muse_merge_base must not import StateStore, executor, or handlers ──

def check_muse_merge_base_isolation() -> None:
    print("\n[Rule 15] muse_merge_base must not import StateStore, executor, or handlers")
    filepath = ROOT / "app" / "services" / "muse_merge_base.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {
        "state_store", "executor", "maestro_handlers", "maestro_editing",
        "maestro_composing", "mcp",
    }
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_merge_base.py imports {imp} (contains '{fb}')")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 16: muse_merge must not import StateStore, executor, or handlers ──

def check_muse_merge_isolation() -> None:
    print("\n[Rule 16] muse_merge must not import StateStore, executor, MCP, or handlers")
    filepath = ROOT / "app" / "services" / "muse_merge.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {
        "state_store", "executor", "maestro_handlers", "maestro_editing",
        "maestro_composing", "mcp", "tool_names",
    }
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_merge.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_merge.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


# ── Rule 17: muse_log_graph must be a pure projection layer ──

def check_muse_log_graph_isolation() -> None:
    print("\n[Rule 17] muse_log_graph must not import StateStore, executor, handlers, or engines")
    filepath = ROOT / "app" / "services" / "muse_log_graph.py"
    if not filepath.exists():
        print("  ⚠️ File not found (skipping)")
        return

    imports = _collect_imports(filepath)
    forbidden_fragments = {
        "state_store", "executor", "maestro_handlers", "maestro_editing",
        "maestro_composing", "mcp", "muse_drift", "muse_merge",
        "muse_checkout", "muse_replay",
    }
    for imp in imports:
        for fb in forbidden_fragments:
            if fb in imp:
                _error(f"muse_log_graph.py imports {imp} (contains '{fb}')")

    names = _collect_import_names(filepath)
    forbidden_names = {"StateStore", "get_or_create_store"}
    for n in names:
        if n in forbidden_names:
            _error(f"muse_log_graph.py imports forbidden name: {n}")

    if not ERRORS:
        print("  ✅ Clean")


def main() -> int:
    print("=" * 60)
    print("Stori Maestro — Architectural Boundary Check")
    print("=" * 60)

    check_variation_service_isolation()
    check_muse_compute_purity()
    check_apply_no_store_lookup()
    check_muse_import_boundary()
    check_apply_no_registry()
    check_variation_context_data_only()
    check_muse_repository_isolation()
    check_compute_no_executor_imports()
    check_muse_replay_isolation()
    check_muse_drift_isolation()
    check_controller_matching_isolation()
    check_commit_route_thinness()
    check_muse_checkout_isolation()
    check_muse_checkout_executor_isolation()
    check_muse_merge_base_isolation()
    check_muse_merge_isolation()
    check_muse_log_graph_isolation()

    print()
    if ERRORS:
        print(f"💥 {len(ERRORS)} boundary violation(s) found")
        return 1

    print("✅ All boundary rules verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
