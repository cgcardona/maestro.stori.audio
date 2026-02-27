"""Maestro prompt language — structured prompt DSL and parser.

Public API:

    from app.prompts import (
        MaestroPrompt,          # canonical structured prompt type
        StructuredPrompt,       # base class for prompt dialects
        parse_prompt,           # text → MaestroPrompt | None
        TargetSpec,
        PositionSpec,
        AfterSpec,
        VibeWeight,
        PromptParseError,
        UnsupportedPromptHeader,
        InvalidMaestroPrompt,
    )
"""
from __future__ import annotations

from app.prompts.base import (
    AfterSpec,
    MaestroDimensions,
    PositionSpec,
    PromptConstraints,
    StructuredPrompt,
    TargetSpec,
    VibeWeight,
)
from app.prompts.errors import (
    InvalidMaestroPrompt,
    PromptParseError,
    UnsupportedPromptHeader,
)
from app.prompts.maestro import MaestroPrompt
from app.prompts.parser import parse_prompt

__all__ = [
    "AfterSpec",
    "InvalidMaestroPrompt",
    "MaestroDimensions",
    "MaestroPrompt",
    "PositionSpec",
    "PromptConstraints",
    "PromptParseError",
    "StructuredPrompt",
    "TargetSpec",
    "UnsupportedPromptHeader",
    "VibeWeight",
    "parse_prompt",
]
