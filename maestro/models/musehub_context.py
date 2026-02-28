"""Pydantic v2 request/response models for the agent context endpoint.

The context endpoint (GET /musehub/repos/{repo_id}/context) is the canonical
agent entry point for composition sessions.  These models define the wire
format that agents receive when they ask: "what do I need to know to start
composing in this project?"

Depth levels control how much data is returned:
- ``brief``    — fits in ~2 K tokens; musical state + 3 history entries
- ``standard`` — fits in ~8 K tokens; full state + 10 history + analysis
- ``verbose``  — uncapped; all history, full issue/PR bodies, full analysis
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field

from maestro.models.base import CamelModel


class ContextDepth(str, Enum):
    """Depth level controlling how much data the context endpoint returns."""

    brief = "brief"
    standard = "standard"
    verbose = "verbose"


class ContextFormat(str, Enum):
    """Wire format for the context response."""

    json = "json"
    yaml = "yaml"


# ---------------------------------------------------------------------------
# Sub-sections of the context response
# ---------------------------------------------------------------------------


class MusicalStateContext(CamelModel):
    """Current musical state of the project at the resolved ref.

    All optional fields require Storpheus MIDI analysis and are None until
    that integration is complete. Agents must handle None gracefully.
    """

    active_tracks: list[str] = Field(
        default_factory=list,
        description="Track names derived from the snapshot manifest",
    )
    key: Annotated[str | None, Field(description="Detected key (e.g. 'F# minor')")] = None
    mode: Annotated[str | None, Field(description="Detected mode (e.g. 'dorian')")] = None
    tempo_bpm: Annotated[int | None, Field(description="Tempo in beats per minute")] = None
    time_signature: Annotated[str | None, Field(description="Time signature (e.g. '4/4')")] = None
    form: Annotated[str | None, Field(description="Detected form (e.g. 'AABA', 'verse-chorus')")] = None
    emotion: Annotated[str | None, Field(description="Emotional character (e.g. 'melancholic')")] = None


class HistoryEntryContext(CamelModel):
    """A single commit entry in the composition history."""

    commit_id: str
    message: str
    author: str
    timestamp: str = Field(description="ISO-8601 UTC timestamp")
    active_tracks: list[str] = Field(default_factory=list)


class AnalysisSummaryContext(CamelModel):
    """Per-dimension analysis highlights for the current state.

    All fields are None until Storpheus MIDI analysis integration is complete.
    Agents should treat None as "unknown" and compose without relying on these.
    """

    key_finding: Annotated[
        str | None, Field(description="Key and mode detection summary")
    ] = None
    chord_progression: Annotated[
        list[str] | None,
        Field(description="Detected chord progression (e.g. ['Cm', 'Ab', 'Eb', 'Bb'])"),
    ] = None
    groove_score: Annotated[
        float | None, Field(description="Groove quality score [0.0, 1.0]")
    ] = None
    emotion: Annotated[str | None, Field(description="Emotional character")] = None
    harmonic_tension: Annotated[
        str | None,
        Field(description="Harmonic tension assessment ('low', 'medium', 'high')"),
    ] = None
    melodic_contour: Annotated[
        str | None, Field(description="Melodic contour description")
    ] = None


class ActivePRContext(CamelModel):
    """An open pull request that may inform composition decisions."""

    pr_id: str
    title: str
    from_branch: str
    to_branch: str
    state: str
    body: str = Field(
        default="",
        description="PR description; included at standard/verbose depth only",
    )


class OpenIssueContext(CamelModel):
    """An open issue that may describe desired compositional changes."""

    issue_id: str
    number: int
    title: str
    labels: list[str] = Field(default_factory=list)
    body: str = Field(
        default="",
        description="Issue body; included at verbose depth only",
    )


# ---------------------------------------------------------------------------
# Top-level context response
# ---------------------------------------------------------------------------


class AgentContextResponse(CamelModel):
    """Complete agent context document for one project ref.

    This is the top-level response from GET /musehub/repos/{repo_id}/context.
    It is self-contained: an agent receiving only this document has everything
    it needs to generate structurally and stylistically coherent music.

    ``depth`` controls how much data is returned; ``format`` is echoed from
    the request so agents can validate they received what they asked for.
    """

    repo_id: str
    ref: str = Field(description="The resolved branch name or commit ID")
    depth: str = Field(description="Depth level used to build this response")
    musical_state: MusicalStateContext
    history: list[HistoryEntryContext] = Field(default_factory=list)
    analysis: AnalysisSummaryContext
    active_prs: list[ActivePRContext] = Field(default_factory=list)
    open_issues: list[OpenIssueContext] = Field(default_factory=list)
    suggestions: list[str] = Field(
        default_factory=list,
        description="AI-generated suggestions for next compositional actions",
    )
