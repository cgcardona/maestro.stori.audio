"""Tests for scripts/seed_narratives.py.

Verifies that the narrative seed script is importable, structurally correct,
and that its entry point (main) is callable without hitting the database.

These are unit-level checks — they do not require a running Postgres instance.
The DB-dependent seeding logic is covered by examining the data-structure
constants and ensuring all referenced model classes are importable and used
correctly.
"""
from __future__ import annotations

import importlib
import inspect
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_module() -> types.ModuleType:
    """Import seed_narratives via importlib so tests work regardless of
    whether 'scripts' is on sys.path."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    return importlib.import_module("seed_narratives")


# ---------------------------------------------------------------------------
# Import and structure tests
# ---------------------------------------------------------------------------

class TestModuleImport:
    """The script must be importable and expose the required public API."""

    def test_module_imports_without_error(self) -> None:
        mod = _load_module()
        assert mod is not None

    def test_main_function_exists(self) -> None:
        mod = _load_module()
        assert hasattr(mod, "main"), "seed_narratives must expose a main() function"

    def test_main_is_coroutine(self) -> None:
        mod = _load_module()
        assert inspect.iscoroutinefunction(mod.main), "main() must be async"

    def test_seed_narratives_function_exists(self) -> None:
        mod = _load_module()
        assert hasattr(mod, "seed_narratives"), (
            "seed_narratives() orchestrator function must be defined"
        )

    def test_seed_narratives_is_coroutine(self) -> None:
        mod = _load_module()
        assert inspect.iscoroutinefunction(mod.seed_narratives)

    def test_scenario_functions_exist(self) -> None:
        mod = _load_module()
        expected = [
            "_seed_bach_remix_war",
            "_seed_chopin_coltrane",
            "_seed_ragtime_edm",
            "_seed_community_chaos",
            "_seed_goldberg_milestone",
        ]
        for fn_name in expected:
            assert hasattr(mod, fn_name), f"Expected function {fn_name!r} not found"
            assert inspect.iscoroutinefunction(getattr(mod, fn_name)), (
                f"{fn_name}() must be async"
            )


# ---------------------------------------------------------------------------
# Constant / sentinel tests
# ---------------------------------------------------------------------------

class TestConstants:
    """Stable IDs must be defined and follow the naming convention."""

    def test_sentinel_repo_id_defined(self) -> None:
        mod = _load_module()
        assert hasattr(mod, "SENTINEL_REPO_ID")
        sid: str = mod.SENTINEL_REPO_ID
        assert isinstance(sid, str) and len(sid) > 0

    def test_all_narrative_repo_ids_defined(self) -> None:
        mod = _load_module()
        expected_attrs = [
            "REPO_NEO_BAROQUE", "REPO_NEO_BAROQUE_FORK",
            "REPO_NOCTURNE",
            "REPO_RAGTIME_EDM",
            "REPO_COMMUNITY_JAM",
            "REPO_GOLDBERG",
        ]
        for attr in expected_attrs:
            assert hasattr(mod, attr), f"Constant {attr!r} must be defined"
            val: str = getattr(mod, attr)
            assert isinstance(val, str) and len(val) > 0

    def test_sentinel_matches_first_narrative_repo(self) -> None:
        mod = _load_module()
        assert mod.SENTINEL_REPO_ID == mod.REPO_NEO_BAROQUE, (
            "SENTINEL_REPO_ID must match REPO_NEO_BAROQUE (first scenario)"
        )

    def test_user_ids_present(self) -> None:
        mod = _load_module()
        for user_attr in ("GABRIEL", "MARCUS", "YUKI", "FATOU", "PIERRE", "SOFIA", "AALIYA", "CHEN"):
            assert hasattr(mod, user_attr), f"User constant {user_attr!r} must be defined"

    def test_repo_ids_are_unique(self) -> None:
        mod = _load_module()
        repo_ids = [
            mod.REPO_NEO_BAROQUE, mod.REPO_NEO_BAROQUE_FORK,
            mod.REPO_NOCTURNE, mod.REPO_RAGTIME_EDM,
            mod.REPO_COMMUNITY_JAM, mod.REPO_GOLDBERG,
        ]
        assert len(repo_ids) == len(set(repo_ids)), "All narrative repo IDs must be unique"

    def test_repo_ids_do_not_collide_with_musehub_ids(self) -> None:
        """Narrative IDs must not overlap with the seed_musehub.py stable IDs."""
        mod = _load_module()
        musehub_ids = {
            "repo-neo-soul-00000001", "repo-modal-jazz-000001",
            "repo-ambient-textures-1", "repo-afrobeat-grooves-1",
            "repo-microtonal-etudes1", "repo-drum-machine-00001",
            "repo-chanson-minimale-1", "repo-granular-studies-1",
            "repo-funk-suite-0000001", "repo-jazz-trio-0000001",
            "repo-neo-soul-fork-0001", "repo-ambient-fork-0001",
        }
        narrative_ids = {
            mod.REPO_NEO_BAROQUE, mod.REPO_NEO_BAROQUE_FORK,
            mod.REPO_NOCTURNE, mod.REPO_RAGTIME_EDM,
            mod.REPO_COMMUNITY_JAM, mod.REPO_GOLDBERG,
        }
        overlap = narrative_ids & musehub_ids
        assert not overlap, f"Narrative repo IDs overlap with seed_musehub IDs: {overlap}"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    """Internal helper functions must be present and return correct types."""

    def test_sha_returns_hex_string(self) -> None:
        mod = _load_module()
        result: str = mod._sha("test-seed")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_uid_returns_uuid_string(self) -> None:
        mod = _load_module()
        result: str = mod._uid("test-uid-seed")
        assert isinstance(result, str)
        assert len(result) == 36
        parts = result.split("-")
        assert len(parts) == 5, "Expected UUID4 format with 5 hyphen-separated groups"

    def test_now_returns_datetime(self) -> None:
        from datetime import datetime, timezone
        mod = _load_module()
        result: Any = mod._now()
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc

    def test_now_offset(self) -> None:
        from datetime import timezone
        mod = _load_module()
        t0 = mod._now(days=0)
        t7 = mod._now(days=7)
        delta = (t0 - t7).total_seconds()
        assert 604700 < delta < 604900, "7-day offset should be ~604800 seconds"

    def test_sha_is_deterministic(self) -> None:
        mod = _load_module()
        assert mod._sha("foo") == mod._sha("foo")
        assert mod._sha("foo") != mod._sha("bar")

    def test_uid_is_deterministic(self) -> None:
        mod = _load_module()
        assert mod._uid("foo") == mod._uid("foo")
        assert mod._uid("foo") != mod._uid("bar")


# ---------------------------------------------------------------------------
# Narrative content tests (count/coverage checks on hard-coded data)
# ---------------------------------------------------------------------------

def _find_list_elts(src: str, var_name: str) -> list[Any] | None:
    """Return the elements of the first list/dict assignment to ``var_name``
    in the given source, handling both plain ``Assign`` and annotated
    ``AnnAssign`` nodes (which mypy-typed code generates).
    """
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    value = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == var_name:
                value = node.value
        if value is not None:
            if isinstance(value, (ast.List, ast.Dict)):
                if isinstance(value, ast.List):
                    return value.elts
                return list(value.keys)  # ast.Dict.keys is list[expr | None]
    return None


class TestScenarioContent:
    """Validate the narrative content counts match the issue specification."""

    def test_bach_pr_comment_count(self) -> None:
        """Bach Remix War must have exactly 15 PR comments."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_bach_remix_war)
        elts = _find_list_elts(src, "pr_comment_thread")
        assert elts is not None, "pr_comment_thread list not found in _seed_bach_remix_war"
        assert len(elts) == 15, f"Bach Remix War requires 15 PR comments, got {len(elts)}"

    def test_chopin_coltrane_comment_count(self) -> None:
        """Chopin+Coltrane must have exactly 20 PR comments."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_chopin_coltrane)
        elts = _find_list_elts(src, "resolution_debate")
        assert elts is not None, "resolution_debate list not found in _seed_chopin_coltrane"
        assert len(elts) == 20, f"Chopin+Coltrane requires 20 comments, got {len(elts)}"

    def test_ragtime_commit_count(self) -> None:
        """Ragtime EDM must have exactly 8 commits."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_ragtime_edm)
        elts = _find_list_elts(src, "ragtime_commits")
        assert elts is not None, "ragtime_commits list not found in _seed_ragtime_edm"
        assert len(elts) == 8, f"Ragtime EDM requires 8 commits, got {len(elts)}"

    def test_community_pr_count(self) -> None:
        """Community Chaos must have exactly 5 PR configs."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_community_chaos)
        elts = _find_list_elts(src, "pr_configs")
        assert elts is not None, "pr_configs list not found in _seed_community_chaos"
        assert len(elts) == 5, f"Community Chaos requires 5 PRs, got {len(elts)}"

    def test_community_key_debate_count(self) -> None:
        """Community Chaos key debate must have exactly 25 comments."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_community_chaos)
        elts = _find_list_elts(src, "key_debate")
        assert elts is not None, "key_debate list not found in _seed_community_chaos"
        assert len(elts) == 25, f"Community Chaos key debate requires 25 comments, got {len(elts)}"

    def test_goldberg_var25_debate_count(self) -> None:
        """Goldberg Var 25 debate must have exactly 18 comments."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_goldberg_milestone)
        elts = _find_list_elts(src, "var25_debate")
        assert elts is not None, "var25_debate list not found in _seed_goldberg_milestone"
        assert len(elts) == 18, f"Goldberg Var 25 debate requires 18 comments, got {len(elts)}"

    def test_goldberg_variation_metadata_coverage(self) -> None:
        """All 30 Goldberg variations must have metadata entries."""
        import inspect
        mod = _load_module()
        src = inspect.getsource(mod._seed_goldberg_milestone)
        elts = _find_list_elts(src, "variation_metadata")
        assert elts is not None, "variation_metadata dict not found in _seed_goldberg_milestone"
        assert len(elts) == 30, f"Goldberg requires metadata for 30 variations, got {len(elts)}"

    def test_goldberg_28_of_30_done(self) -> None:
        """The Goldberg scenario marks variations 1-28 as closed, 29-30 as open."""
        mod = _load_module()
        import ast, inspect
        src = inspect.getsource(mod._seed_goldberg_milestone)
        # Check for 'n <= 28' logic pattern
        assert "n <= 28" in src, (
            "Goldberg scenario must use 'n <= 28' to mark 28 variations as done"
        )


# ---------------------------------------------------------------------------
# Callable entry point test (mocked DB)
# ---------------------------------------------------------------------------

class TestMainCallable:
    """main() must be callable without errors (DB mocked out)."""

    @pytest.mark.anyio
    async def test_main_is_callable_with_mocked_db(self) -> None:
        """Verify main() can be called; mock away the DB engine."""
        mod = _load_module()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 1))
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_sessionmaker = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        with (
            patch.object(mod, "create_async_engine", return_value=mock_engine),
            patch.object(mod, "sessionmaker", return_value=mock_sessionmaker),
        ):
            # With already-seeded sentinel (scalar=1), seed_narratives skips
            await mod.main()

    @pytest.mark.anyio
    async def test_seed_narratives_skip_when_already_seeded(self) -> None:
        """seed_narratives() prints skip message when sentinel row exists."""
        mod = _load_module()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=1)  # already seeded
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        # Should return early without error
        await mod.seed_narratives(mock_db, force=False)
        # Commit should NOT be called when skipping
        mock_db.commit.assert_not_called()
