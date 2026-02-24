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


def main() -> int:
    print("=" * 60)
    print("Stori Maestro — Architectural Boundary Check")
    print("=" * 60)

    check_variation_service_isolation()
    check_muse_compute_purity()
    check_apply_no_store_lookup()
    check_muse_import_boundary()
    check_apply_no_registry()

    print()
    if ERRORS:
        print(f"💥 {len(ERRORS)} boundary violation(s) found")
        return 1

    print("✅ All boundary rules verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
