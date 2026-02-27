"""Exit-code contract and exception types for the Muse CLI."""
from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    """Standardised CLI exit codes.

    0 — success
    1 — user error (bad arguments, invalid input)
    2 — repo-not-found / config invalid
    3 — server / internal error
    """

    SUCCESS = 0
    USER_ERROR = 1
    REPO_NOT_FOUND = 2
    INTERNAL_ERROR = 3


class MuseCLIError(Exception):
    """Base exception for Muse CLI errors."""

    def __init__(self, message: str, exit_code: ExitCode = ExitCode.INTERNAL_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class RepoNotFoundError(MuseCLIError):
    """Raised when the current directory is not a Muse repository."""

    def __init__(self, message: str = "Not a Muse repository. Run `muse init`.") -> None:
        super().__init__(message, exit_code=ExitCode.REPO_NOT_FOUND)
