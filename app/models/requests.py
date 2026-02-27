"""Request models for the Stori Maestro API."""
from __future__ import annotations

import logging
from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict

from app.contracts.json_types import JSONValue
from app.contracts.project_types import ProjectContext
from app.models.base import CamelModel

logger = logging.getLogger(__name__)


class ProposeVariationScopeDict(TypedDict, total=False):
    """Optional scope that constrains a variation proposal to a subset of the project.

    When ``trackIds`` or ``regionIds`` are provided, the variation only affects
    those tracks/regions.  ``beatRange`` limits generation to a specific beat window.
    """

    trackIds: list[str]
    regionIds: list[str]
    beatRange: tuple[float, float]


class ProposeVariationOptionsDict(TypedDict, total=False):
    """Execution options for a variation proposal.

    All keys are optional — omit to use server-side defaults.
    """

    phraseGrouping: str
    barSize: int
    stream: bool

# Generous limit — comfortably fits long STORI PROMPT YAML with Maestro dimensions.
# The nginx layer guards against large binary payloads; this catches oversized text.
_MAX_PROMPT_BYTES = 32_768   # 32 KB


class MaestroRequest(CamelModel):
    """Request to compose or modify music.
    
    The backend determines execution mode from intent classification:
    COMPOSING -> variation (human review), EDITING -> apply (immediate).
    """
    
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_PROMPT_BYTES,
        description="Natural language description of what to create or modify",
        examples=["Make a chill boom bap beat at 90 BPM with dusty drums"]
    )
    mode: str = Field(
        default="generate",
        description="Composition mode: 'generate' for new, 'edit' for modifications"
    )
    project: ProjectContext | None = Field(
        default=None,
        description="Current project state from the DAW",
    )
    model: str | None = Field(
        default=None,
        description="LLM model to use (e.g., 'anthropic/claude-3.5-sonnet'). Uses default if not specified."
    )
    store_prompt: bool = Field(
        default=True,
        description="Whether to store the prompt for training data. set to False to opt out."
    )
    conversation_id: str | None = Field(
        default=None,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="Conversation ID for multi-turn sessions. State persists across requests with same ID."
    )
    quality_preset: str | None = Field(
        default=None,
        pattern=r'^(fast|balanced|quality)$',
        description="Orpheus quality preset: 'fast', 'balanced', or 'quality'. Default 'quality'. Use 'fast' or 'balanced' only for rapid iteration.",
    )

    @field_validator("prompt")
    @classmethod
    def no_null_bytes(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Prompt must not contain null bytes")
        return v

    @field_validator("project", mode="before")
    @classmethod
    def validate_project_snapshot(cls, v: JSONValue) -> JSONValue:
        """Validate the project payload against the ProjectSnapshot schema.

        Catches structural issues (invalid pitch, out-of-range tempo, etc.)
        at the request boundary.  On validation failure the payload is
        nullified so downstream handlers operate on a clean slate.
        """
        if v is None or not isinstance(v, dict):
            return v
        from app.protocol.schemas.project import ProjectSnapshot

        try:
            ProjectSnapshot.model_validate(v)
        except Exception as exc:
            logger.error(f"❌ Project payload failed ProjectSnapshot validation: {exc}")
            return None
        return v


class GenerateRequest(BaseModel):
    """Request to generate MIDI via Orpheus."""
    
    genre: str = Field(
        default="boom_bap",
        description="Musical genre/style"
    )
    tempo: int = Field(
        default=90,
        ge=40,
        le=240,
        description="Tempo in BPM"
    )
    instruments: list[str] = Field(
        default=["drums", "bass"],
        description="Instruments to generate"
    )
    bars: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Number of bars to generate"
    )
    key: str | None = Field(
        default=None,
        description="Musical key (e.g., 'Am', 'C', 'F#m')"
    )


class ProposeVariationRequest(CamelModel):
    """
    Request to propose a variation (spec-compliant endpoint).
    
    Corresponds to POST /variation/propose in the Muse Variation Specification.
    """
    
    project_id: str = Field(
        ...,
        description="UUID of the project"
    )
    base_state_id: str = Field(
        ...,
        description="Monotonic project version (UUID or int) for optimistic concurrency"
    )
    intent: str = Field(
        ...,
        description="User intent describing the desired transformation"
    )
    scope: ProposeVariationScopeDict | None = Field(
        default=None,
        description=(
            "Optional scope limiting the variation to specific tracks/regions/beat_range. "
            "Keys: trackIds (list), regionIds (list), beatRange (tuple of floats)"
        )
    )
    options: ProposeVariationOptionsDict | None = Field(
        default=None,
        description=(
            "Optional execution options. "
            "Keys: phraseGrouping (str), barSize (int), stream (bool)"
        )
    )
    request_id: str | None = Field(
        default=None,
        description="Idempotency key for the request"
    )
    model: str | None = Field(
        default=None,
        description="LLM model to use. Uses default if not specified."
    )


class CommitVariationRequest(CamelModel):
    """
    Request to commit (accept) selected phrases from a variation.
    
    Corresponds to POST /variation/commit in the Muse Variation Specification.
    """
    
    project_id: str = Field(
        ...,
        description="UUID of the project"
    )
    base_state_id: str = Field(
        ...,
        description="Base state version for optimistic concurrency check"
    )
    variation_id: str = Field(
        ...,
        description="ID of the variation being committed"
    )
    accepted_phrase_ids: list[str] = Field(
        ...,
        description="list of phrase IDs to apply (order does not matter)"
    )
    request_id: str | None = Field(
        default=None,
        description="Idempotency key for the request"
    )
    force: bool = Field(
        default=False,
        description="Bypass drift safety check (use when working tree divergence is intentional)"
    )


class DiscardVariationRequest(CamelModel):
    """
    Request to discard a variation without applying.
    
    Corresponds to POST /variation/discard in the Muse Variation Specification.
    """
    
    project_id: str = Field(
        ...,
        description="UUID of the project"
    )
    variation_id: str = Field(
        ...,
        description="ID of the variation being discarded"
    )
    request_id: str | None = Field(
        default=None,
        description="Idempotency key for the request"
    )
