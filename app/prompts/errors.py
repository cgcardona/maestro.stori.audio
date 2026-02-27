"""Prompt parsing errors.

These are raised by the parser and caught by route handlers to produce
structured 400 responses.  They are *not* logged as server errors — they
represent invalid client input.
"""
from __future__ import annotations


class PromptParseError(Exception):
    """Base class for all prompt-parsing failures."""


class UnsupportedPromptHeader(PromptParseError):
    """The prompt starts with a recognised but unsupported header.

    Raised when the first non-empty line matches a known legacy sentinel
    (e.g. ``STORI PROMPT``) that is no longer accepted.
    """

    def __init__(self, header: str) -> None:
        self.header = header
        super().__init__(
            f"Structured prompts must start with 'MAESTRO PROMPT'. "
            f"Found: '{header}'."
        )


class InvalidMaestroPrompt(PromptParseError):
    """The prompt has the correct header but invalid YAML or field values.

    ``details`` lists every validation failure so the caller can report
    them all at once instead of one-at-a-time whack-a-mole.
    """

    def __init__(self, details: list[str]) -> None:
        self.details = details
        joined = "; ".join(details)
        super().__init__(f"Invalid MAESTRO PROMPT: {joined}")
