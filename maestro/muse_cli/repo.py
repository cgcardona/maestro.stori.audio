"""Public API for Muse CLI repository detection.

This module is the stable, importable surface for ``find_repo_root()`` and
``require_repo_root()``.  All internal commands continue to import from
``_repo`` (the original private module); this public re-export exists so
external tooling and new commands can depend on a name that is not
prefixed with an underscore.

Issue #46 specifies ``maestro.muse_cli.repo`` as the canonical location.
"""
from __future__ import annotations

from maestro.muse_cli._repo import (
    find_repo_root,
    require_repo,
    require_repo_root,
)

__all__ = [
    "find_repo_root",
    "require_repo",
    "require_repo_root",
]
