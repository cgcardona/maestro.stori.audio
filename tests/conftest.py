"""Worktree conftest — makes shared fixtures available for worktree tests.

Pytest discovers conftest.py by traversing up from the test file, not from rootdir.
Since this worktree lives outside /app, we must explicitly import the shared fixtures
from the main repo's tests/conftest.py using importlib, then re-export them so
pytest discovers them in this conftest's local namespace.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Ensure the main app code is importable when running from this worktree.
_APP_ROOT = Path("/app")
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

# Load the main repo's conftest.py as a module named _main_conftest to avoid
# the naming collision that occurs when pytest also loads it as "conftest".
_main_conftest_path = _APP_ROOT / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_main_conftest", _main_conftest_path)
assert _spec is not None and _spec.loader is not None
_main_conftest = importlib.util.module_from_spec(_spec)
sys.modules["_main_conftest"] = _main_conftest
_spec.loader.exec_module(_main_conftest)  # type: ignore[union-attr]

# Re-export every pytest fixture so this conftest acts as a transparent proxy.
# Pytest collects fixtures from module globals; assigning them here makes them
# available to all tests discovered under this directory.
from _main_conftest import (  # noqa: E402  # type: ignore[import]
    anyio_backend,
    client,
    db_session,
    pytest_configure,
    _disable_storpheus_hard_gate,
    _reset_variation_store,
)

__all__ = [
    "anyio_backend",
    "client",
    "db_session",
    "pytest_configure",
    "_disable_storpheus_hard_gate",
    "_reset_variation_store",
]
