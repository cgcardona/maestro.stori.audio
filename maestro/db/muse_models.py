"""SQLAlchemy ORM models for Muse persistent variation history.

Tables:
- variations: Top-level variation proposals with lineage tracking
- phrases: Independently reviewable musical phrases within a variation
- note_changes: Individual note-level diffs within a phrase
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from maestro.contracts.json_types import AftertouchDict, CCEventDict, NoteDict, PitchBendDict
from maestro.db.database import Base
from maestro.db.models import generate_uuid, utc_now


class Variation(Base):
    """A persisted variation proposal with lineage tracking."""

    __tablename__ = "variations"

    variation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    base_state_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    affected_tracks: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    affected_regions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    beat_range_start: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    beat_range_end: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Lineage (Phase 5) ────────────────────────────────────────────
    parent_variation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("variations.variation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent2_variation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("variations.variation_id", ondelete="SET NULL"),
        nullable=True,
    )
    commit_state_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_head: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    phrases: Mapped[list["Phrase"]] = relationship(
        "Phrase",
        back_populates="variation",
        cascade="all, delete-orphan",
        order_by="Phrase.sequence",
    )
    children: Mapped[list["Variation"]] = relationship(
        "Variation",
        backref="parent",
        remote_side=[variation_id],
        foreign_keys=[parent_variation_id],
    )

    def __repr__(self) -> str:
        return f"<Variation {self.variation_id[:8]} status={self.status} head={self.is_head}>"


class Phrase(Base):
    """A persisted musical phrase within a variation."""

    __tablename__ = "phrases"

    phrase_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    variation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("variations.variation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    track_id: Mapped[str] = mapped_column(String(36), nullable=False)
    region_id: Mapped[str] = mapped_column(String(36), nullable=False)
    start_beat: Mapped[float] = mapped_column(Float, nullable=False)
    end_beat: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    cc_events: Mapped[list[CCEventDict] | None] = mapped_column(JSON, nullable=True)
    pitch_bends: Mapped[list[PitchBendDict] | None] = mapped_column(JSON, nullable=True)
    aftertouch: Mapped[list[AftertouchDict] | None] = mapped_column(JSON, nullable=True)

    region_start_beat: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_duration_beats: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    variation: Mapped["Variation"] = relationship("Variation", back_populates="phrases")
    note_changes: Mapped[list["NoteChange"]] = relationship(
        "NoteChange",
        back_populates="phrase",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Phrase {self.phrase_id[:8]} {self.label}>"


class NoteChange(Base):
    """A persisted note-level diff within a phrase."""

    __tablename__ = "note_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    phrase_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("phrases.phrase_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    before_json: Mapped[NoteDict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[NoteDict | None] = mapped_column(JSON, nullable=True)

    phrase: Mapped["Phrase"] = relationship("Phrase", back_populates="note_changes")

    def __repr__(self) -> str:
        return f"<NoteChange {self.id[:8]} {self.change_type}>"
