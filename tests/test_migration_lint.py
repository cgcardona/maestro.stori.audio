"""Tests for tools/lint_migration.py — migration structural linter.

Regression suite for issue #273: 0001_consolidated_schema.py accumulated
duplicate def downgrade() blocks and syntax errors from parallel PR conflict
resolution. These tests ensure the linter catches every class of corruption
that can arise from automated conflict merges.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# Add tools/ to path so we can import lint_migration directly
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from lint_migration import lint_migration  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def write_migration(tmp_path: Path, source: str) -> Path:
    """Write a migration source string to a temp file and return the path."""
    path = tmp_path / "0001_test_migration.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


# ── regression: the exact class of corruption from issue #273 ─────────────────


def test_duplicate_downgrade_detected(tmp_path: Path) -> None:
    """Two def downgrade() blocks — the primary failure mode from issue #273."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("users")

        def downgrade() -> None:
            op.drop_table("users")

        def downgrade() -> None:
            op.drop_table("users")
        """,
    )
    errors = lint_migration(path)
    assert len(errors) == 1
    assert "2 def downgrade() blocks" in errors[0]
    assert "parallel-PR merge conflict artifact" in errors[0]


def test_duplicate_upgrade_detected(tmp_path: Path) -> None:
    """Two def upgrade() blocks — same failure mode applied to the upgrade path."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("users")

        def upgrade() -> None:
            op.create_table("users")

        def downgrade() -> None:
            op.drop_table("users")
        """,
    )
    errors = lint_migration(path)
    assert len(errors) == 1
    assert "2 def upgrade() blocks" in errors[0]


def test_syntax_error_detected(tmp_path: Path) -> None:
    """Unresolved merge conflict markers produce SyntaxErrors."""
    path = write_migration(
        tmp_path,
        """
        <<<<<<< HEAD
        def upgrade() -> None:
            op.create_table("users")
        =======
        def upgrade() -> None:
            op.create_table("accounts")
        >>>>>>> other-branch
        """,
    )
    errors = lint_migration(path)
    assert len(errors) == 1
    assert "SyntaxError" in errors[0]
    assert "unresolved git merge conflict" in errors[0].lower() or "merge conflict" in errors[0].lower()


def test_missing_upgrade_detected(tmp_path: Path) -> None:
    """Migration with no upgrade() is incomplete."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def downgrade() -> None:
            op.drop_table("users")
        """,
    )
    errors = lint_migration(path)
    assert any("missing def upgrade()" in e for e in errors)


def test_missing_downgrade_detected(tmp_path: Path) -> None:
    """Migration with no downgrade() is incomplete."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("users")
        """,
    )
    errors = lint_migration(path)
    assert any("missing def downgrade()" in e for e in errors)


def test_pass_only_upgrade_detected(tmp_path: Path) -> None:
    """An upgrade() that is just `pass` means content was lost in conflict resolution."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            pass

        def downgrade() -> None:
            op.drop_table("users")
        """,
    )
    errors = lint_migration(path)
    assert any("only `pass`" in e for e in errors)


def test_pass_only_downgrade_detected(tmp_path: Path) -> None:
    """A downgrade() that is just `pass` means content was lost in conflict resolution."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("users")

        def downgrade() -> None:
            pass
        """,
    )
    errors = lint_migration(path)
    assert any("only `pass`" in e for e in errors)


def test_missing_file_detected(tmp_path: Path) -> None:
    """A missing migration file is flagged immediately."""
    path = tmp_path / "nonexistent.py"
    errors = lint_migration(path)
    assert len(errors) == 1
    assert "not found" in errors[0]


# ── clean migration passes all checks ─────────────────────────────────────────


def test_clean_migration_passes(tmp_path: Path) -> None:
    """A correctly structured migration produces no errors."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op
        import sqlalchemy as sa

        revision = "0001"
        down_revision = None
        branch_labels = None
        depends_on = None

        def upgrade() -> None:
            op.create_table(
                "users",
                sa.Column("id", sa.String(36), nullable=False),
                sa.PrimaryKeyConstraint("id"),
            )

        def downgrade() -> None:
            op.drop_table("users")
        """,
    )
    errors = lint_migration(path)
    assert errors == []


def test_production_migration_file_is_clean() -> None:
    """The actual production migration passes all linter checks.

    This is the primary regression guard for issue #273.
    If this test fails, the migration has been corrupted and must be fixed
    before merging to dev.
    """
    production_path = Path(__file__).parent.parent / "alembic" / "versions" / "0001_consolidated_schema.py"
    assert production_path.exists(), (
        f"Production migration not found at {production_path}. "
        "Ensure you are running tests from the project root."
    )
    errors = lint_migration(production_path)
    assert errors == [], (
        f"Production migration has {len(errors)} error(s):\n"
        + "\n".join(f"  • {e}" for e in errors)
    )


# ── multiple errors reported at once ─────────────────────────────────────────


def test_multiple_duplicate_defs_both_reported(tmp_path: Path) -> None:
    """Both duplicate upgrade() and duplicate downgrade() are reported together."""
    path = write_migration(
        tmp_path,
        """
        from alembic import op

        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("a")

        def upgrade() -> None:
            op.create_table("b")

        def downgrade() -> None:
            op.drop_table("a")

        def downgrade() -> None:
            op.drop_table("b")
        """,
    )
    errors = lint_migration(path)
    assert len(errors) == 2
    assert any("upgrade" in e for e in errors)
    assert any("downgrade" in e for e in errors)
