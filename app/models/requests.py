"""Request models for the Stori Maestro API."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Any

# Generous limit — comfortably fits long STORI PROMPT YAML with Maestro dimensions.
# The nginx layer guards against large binary payloads; this catches oversized text.
_MAX_PROMPT_BYTES = 32_768   # 32 KB


class MaestroRequest(BaseModel):
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
    project: Optional[dict[str, Any]] = Field(
        default=None,
        description="Current project state (for edit mode)"
    )
    model: Optional[str] = Field(
        default=None,
        description="LLM model to use (e.g., 'anthropic/claude-3.5-sonnet'). Uses default if not specified."
    )
    store_prompt: bool = Field(
        default=True,
        description="Whether to store the prompt for training data. Set to False to opt out."
    )
    conversation_id: Optional[str] = Field(
        default=None,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="Conversation ID for multi-turn sessions. State persists across requests with same ID."
    )

    @field_validator("prompt")
    @classmethod
    def no_null_bytes(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Prompt must not contain null bytes")
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "prompt": "Create a chill lo-fi beat at 85 BPM in A minor",
                "mode": "generate",
                "project": None,
                "model": None,
                "store_prompt": True,
                "conversation_id": None
            }
        }
    )


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
    key: Optional[str] = Field(
        default=None,
        description="Musical key (e.g., 'Am', 'C', 'F#m')"
    )


class ProposeVariationRequest(BaseModel):
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
    scope: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional scope limiting the variation to specific tracks/regions/beat_range. "
            "Keys: track_ids (list), region_ids (list), beat_range (tuple of floats)"
        )
    )
    options: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional execution options. "
            "Keys: phrase_grouping (str), bar_size (int), stream (bool)"
        )
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Idempotency key for the request"
    )
    model: Optional[str] = Field(
        default=None,
        description="LLM model to use. Uses default if not specified."
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "project_id": "proj-123",
                "base_state_id": "state-456",
                "intent": "make that minor",
                "scope": {
                    "track_ids": ["track-1"],
                    "region_ids": None,
                    "beat_range": [4.0, 8.0]
                },
                "options": {
                    "phrase_grouping": "bars",
                    "bar_size": 4,
                    "stream": True
                },
                "request_id": "req-789"
            }
        }
    )


class CommitVariationRequest(BaseModel):
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
        description="List of phrase IDs to apply (order does not matter)"
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Idempotency key for the request"
    )
    variation_data: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Deprecated: variation data is now loaded from VariationStore. "
            "Retained for backward compatibility with older clients."
        ),
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "project_id": "proj-123",
                "base_state_id": "state-456",
                "variation_id": "var-789",
                "accepted_phrase_ids": ["phrase-1", "phrase-3"],
                "request_id": "req-abc",
                "variation_data": {}
            }
        }
    )


class DiscardVariationRequest(BaseModel):
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
    request_id: Optional[str] = Field(
        default=None,
        description="Idempotency key for the request"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "project_id": "proj-123",
                "variation_id": "var-789",
                "request_id": "req-abc"
            }
        }
    )
