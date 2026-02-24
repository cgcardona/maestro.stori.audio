"""Pydantic models for execution plan schemas."""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class GenerationStep(BaseModel):
    """
    A single MIDI generation step.

    Example:
        {"role": "drums", "style": "boom bap", "bars": 8, "tempo": 90, "key": "Cm"}
    """
    role: Literal["drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"] = Field(
        ...,
        description="Musical role: drums, bass, chords, melody, arp, pads, fx, lead"
    )
    style: str = Field(..., min_length=1, max_length=100, description="Style tag: boom_bap, trap, house, lofi, jazz, funk, etc")
    tempo: int = Field(..., ge=30, le=300, description="Tempo in BPM (30-300)")
    bars: int = Field(..., ge=1, le=64, description="Number of bars to generate (1-64)")
    key: Optional[str] = Field(default=None, description="Musical key (e.g., 'Cm', 'F#', 'G minor'). Required for melodic instruments.")
    constraints: Optional[dict[str, Any]] = Field(default=None, description="Additional constraints (density, syncopation, swing, etc.)")
    trackName: Optional[str] = Field(default=None, description="Override track name (e.g. 'Banjo') when role is a generic category like 'melody'")

    @field_validator('key')
    @classmethod
    def validate_key(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            role = info.data.get('role', '')
            if role in ('bass', 'chords', 'melody', 'arp', 'pads', 'lead'):
                logger.warning(f"⚠️ Key not specified for {role} - generation may be inconsistent")
        return v

    @field_validator('style')
    @classmethod
    def normalize_style(cls, v: str) -> str:
        return v.strip().lower().replace(' ', '_')


class EditStep(BaseModel):
    """
    A DAW editing step (track or region creation).

    Examples:
        {"action": "add_track", "name": "Drums"}
        {"action": "add_region", "track": "Drums", "barStart": 0, "bars": 8}
    """
    action: Literal["add_track", "add_region"] = Field(..., description="Edit action type")
    name: Optional[str] = Field(default=None, description="Name for the entity (track or region)")
    track: Optional[str] = Field(default=None, description="Track name/ID (for add_region)")
    barStart: Optional[int] = Field(default=0, ge=0, description="Start bar for region (0-indexed)")
    bars: Optional[int] = Field(default=None, ge=1, le=64, description="Duration in bars (for add_region)")

    @model_validator(mode='after')
    def validate_action_params(self) -> "EditStep":
        if self.action == "add_track":
            if not self.name:
                raise ValueError("add_track requires 'name' field")
        elif self.action == "add_region":
            if not self.track:
                raise ValueError("add_region requires 'track' field")
            if self.bars is None:
                raise ValueError("add_region requires 'bars' field")
        return self


class MixStep(BaseModel):
    """
    A mixing/effects step.

    Example:
        {"action": "add_insert", "track": "Drums", "type": "compressor"}
    """
    action: Literal["add_insert", "add_send", "set_volume", "set_pan"] = Field(..., description="Mix action type")
    track: str = Field(..., description="Target track name/ID")
    type: Optional[str] = Field(default=None, description="Effect type for add_insert: compressor, eq, reverb, delay, chorus, etc.")
    bus: Optional[str] = Field(default=None, description="Bus name/ID for add_send")
    value: Optional[float] = Field(default=None, description="Value for set_volume (dB) or set_pan (-100 to 100)")

    @field_validator('type')
    @classmethod
    def validate_effect_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        valid_effects = {
            "compressor", "eq", "reverb", "delay", "chorus",
            "flanger", "phaser", "distortion", "overdrive",
            "limiter", "gate", "saturator", "filter"
        }
        normalized = v.strip().lower()
        if normalized not in valid_effects:
            logger.warning(f"⚠️ Unknown effect type '{v}', may not be supported")
        return normalized

    @model_validator(mode='after')
    def validate_action_params(self) -> "MixStep":
        if self.action == "add_insert" and not self.type:
            raise ValueError("add_insert requires 'type' field")
        if self.action == "add_send" and not self.bus:
            raise ValueError("add_send requires 'bus' field")
        if self.action in ("set_volume", "set_pan") and self.value is None:
            raise ValueError(f"{self.action} requires 'value' field")
        return self


class ExecutionPlanSchema(BaseModel):
    """Complete execution plan produced by the planner LLM."""
    generations: list[GenerationStep] = Field(default_factory=list, description="MIDI generation steps")
    edits: list[EditStep] = Field(default_factory=list, description="DAW editing steps (track/region creation)")
    mix: list[MixStep] = Field(default_factory=list, description="Mixing/effects steps")
    explanation: Optional[str] = Field(default=None, description="LLM's explanation of the plan (ignored in execution)")

    @model_validator(mode='after')
    def validate_tempo_consistency(self) -> "ExecutionPlanSchema":
        if not self.generations:
            return self
        tempos = [g.tempo for g in self.generations]
        if len(set(tempos)) > 1:
            logger.warning(f"⚠️ Inconsistent tempos in plan: {tempos}")
        return self

    @model_validator(mode='after')
    def validate_key_consistency(self) -> "ExecutionPlanSchema":
        if not self.generations:
            return self
        melodic_keys = [
            g.key for g in self.generations
            if g.role in ('bass', 'chords', 'melody', 'arp', 'pads', 'lead') and g.key
        ]
        if melodic_keys and len(set(melodic_keys)) > 1:
            logger.warning(f"⚠️ Inconsistent keys in plan: {melodic_keys}")
        return self

    @model_validator(mode='after')
    def validate_track_region_ordering(self) -> "ExecutionPlanSchema":
        track_names: set[str] = set()
        for edit in self.edits:
            if edit.action == "add_track" and edit.name:
                track_names.add(edit.name.lower())
            elif edit.action == "add_region" and edit.track:
                logger.debug(f"📋 Region references track '{edit.track}' - assuming it exists or will be created")
        return self

    def is_empty(self) -> bool:
        return not self.generations and not self.edits and not self.mix

    def generation_count(self) -> int:
        return len(self.generations)

    def total_steps(self) -> int:
        return len(self.generations) + len(self.edits) + len(self.mix)


class PlanValidationResult(BaseModel):
    """Result of plan validation."""
    valid: bool
    plan: Optional[ExecutionPlanSchema] = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_json: Optional[dict] = None
