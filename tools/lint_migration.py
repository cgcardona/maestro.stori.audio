#!/usr/bin/env python3
"""Migration linter — guards against structural corruption in Alembic migration files.

Parallel PR agents all write to the same consolidated migration file
(alembic/versions/0001_consolidated_schema.py). Regex-based conflict
resolution can produce structurally invalid Python: duplicate
``def downgrade()`` blocks, interleaved create_table calls, or outright
syntax errors.

This script is the enforcement gate. Run it in CI on every PR to dev
or main. Any non-zero exit means the migration must be fixed before merge.

Usage (CI):
    python tools/lint_migration.py

Usage (local):
    python tools/lint_migration.py
    python tools/lint_migration.py alembic/versions/0001_consolidated_schema.py

Exit codes:
    0 — all checks pass
    1 — one or more checks failed (errors printed to stderr)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


MIGRATION_PATH = Path("alembic/versions/0001_consolidated_schema.py")


def lint_migration(path: Path) -> list[str]:
    """Lint a single Alembic migration file.

    Returns a list of human-readable error strings. Empty list means clean.

    Checks performed:
    1. File exists and is readable.
    2. File parses as valid Python (catches merge-conflict syntax debris).
    3. Exactly one ``def upgrade()`` function definition.
    4. Exactly one ``def downgrade()`` function definition.
       Duplicate definitions indicate a failed parallel-PR conflict resolution
       where both sides' downgrade blocks were kept.
    5. Neither ``upgrade`` nor ``downgrade`` body is empty (pass-only is a
       sign of a half-applied conflict resolution).
    """
    errors: list[str] = []

    if not path.exists():
        errors.append(f"Migration file not found: {path}")
        return errors

    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append(
            f"SyntaxError in {path}: {exc.msg} (line {exc.lineno})\n"
            f"  Likely cause: unresolved git merge conflict markers."
        )
        return errors

    upgrade_defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        and _is_top_level(node, tree)
    ]
    downgrade_defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        and _is_top_level(node, tree)
    ]

    if len(upgrade_defs) == 0:
        errors.append(f"{path}: missing def upgrade() — migration is incomplete.")
    elif len(upgrade_defs) > 1:
        lines = ", ".join(str(n.lineno) for n in upgrade_defs)
        errors.append(
            f"{path}: {len(upgrade_defs)} def upgrade() blocks found at lines {lines}. "
            f"Only one is allowed. This is a parallel-PR merge conflict artifact — "
            f"keep the union of all create_table calls inside a single upgrade()."
        )

    if len(downgrade_defs) == 0:
        errors.append(f"{path}: missing def downgrade() — migration is incomplete.")
    elif len(downgrade_defs) > 1:
        lines = ", ".join(str(n.lineno) for n in downgrade_defs)
        errors.append(
            f"{path}: {len(downgrade_defs)} def downgrade() blocks found at lines {lines}. "
            f"Only one is allowed. This is a parallel-PR merge conflict artifact — "
            f"keep the union of all drop_table calls inside a single downgrade()."
        )

    if len(upgrade_defs) == 1 and _is_pass_only(upgrade_defs[0]):
        errors.append(
            f"{path}: def upgrade() contains only `pass`. "
            f"Verify the conflict resolution preserved all create_table calls."
        )

    if len(downgrade_defs) == 1 and _is_pass_only(downgrade_defs[0]):
        errors.append(
            f"{path}: def downgrade() contains only `pass`. "
            f"Verify the conflict resolution preserved all drop_table calls."
        )

    return errors


def _is_top_level(node: ast.FunctionDef, tree: ast.Module) -> bool:
    """Return True iff node is a direct child of the module (not nested)."""
    return node in ast.walk(tree) and any(
        child is node for child in ast.iter_child_nodes(tree)
    )


def _is_pass_only(func: ast.FunctionDef) -> bool:
    """Return True iff the function body is effectively empty (only Pass nodes)."""
    meaningful = [
        stmt for stmt in func.body
        if not isinstance(stmt, (ast.Pass, ast.Expr))
        or (
            isinstance(stmt, ast.Expr)
            and not isinstance(stmt.value, ast.Constant)
        )
    ]
    return len(meaningful) == 0


def main() -> int:
    """Entry point for CI and local use."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else MIGRATION_PATH

    errors = lint_migration(path)

    if errors:
        print(f"❌ Migration lint FAILED — {len(errors)} error(s) in {path}:", file=sys.stderr)
        for error in errors:
            print(f"  • {error}", file=sys.stderr)
        return 1

    print(f"✅ Migration lint passed: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
