"""SQLAlchemy ORM model for Muse Hub collaborators.

Collaborators are users granted explicit push/admin access to a repo beyond
the owner. Permission levels: read | write | admin (default: write).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from maestro.db.database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class MusehubCollaborator(Base):
    """A collaborator record granting a user explicit access to a repo.

    ``permission`` is one of "read" | "write" | "admin"; defaults to "write".
    ``invited_by`` references the user who extended the invitation (nullable —
    some collaborators may be added programmatically without an inviter).
    ``accepted_at`` is null until the invited user explicitly accepts.
    """

    __tablename__ = "musehub_collaborators"
    __table_args__ = (
        UniqueConstraint("repo_id", "user_id", name="uq_musehub_collaborators_repo_user"),
        Index("ix_musehub_collaborators_repo_id", "repo_id"),
        Index("ix_musehub_collaborators_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("maestro_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Permission level: "read" | "write" | "admin"
    permission: Mapped[str] = mapped_column(String(20), nullable=False, default="write")
    invited_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("maestro_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    # Null until the invited user accepts the invitation
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
