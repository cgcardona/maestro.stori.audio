"""
SQLAlchemy ORM models for Stori Maestro.

Tables:
- users: User accounts with budget tracking
- usage_logs: Request history with prompts and costs
- access_tokens: JWT token tracking for revocation
- conversations: Conversation threads per user
- conversation_messages: Messages within conversations
- message_actions: Actions performed during message execution
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc)


class User(Base):
    """
    User account with budget tracking.

    Single-identifier architecture: id is the app's device UUID (generated once
    per install, stored in UserDefaults). The app sends it as user_id in register
    and as X-Device-ID on asset requests; the JWT sub claim should be this same
    UUID so one identity is used everywhere. Budget is tracked in cents.
    """
    __tablename__ = "users"

    # Primary key = device UUID (app-generated, single identifier for this user)
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    
    # Budget tracking (in cents to avoid float precision issues)
    budget_cents: Mapped[int] = mapped_column(
        Integer,
        default=500,  # $5.00 default
        nullable=False,
    )
    budget_limit_cents: Mapped[int] = mapped_column(
        Integer,
        default=500,  # $5.00 default
        nullable=False,
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    
    # Relationships
    usage_logs: Mapped[list["UsageLog"]] = relationship(
        "UsageLog",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    access_tokens: Mapped[list["AccessToken"]] = relationship(
        "AccessToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    
    @property
    def budget_remaining(self) -> float:
        """Get remaining budget in dollars."""
        return self.budget_cents / 100.0

    @property
    def budget_spent(self) -> float:
        """Get spent budget in dollars (limit - remaining)."""
        return (self.budget_limit_cents - self.budget_cents) / 100.0
    
    @property
    def budget_limit(self) -> float:
        """Get budget limit in dollars."""
        return self.budget_limit_cents / 100.0
    
    def __repr__(self) -> str:
        return f"<User {self.id} budget=${self.budget_remaining:.2f}/${self.budget_limit:.2f}>"


class UsageLog(Base):
    """
    Usage log for tracking requests, costs, and prompts.
    
    Prompts are stored for training data unless the user opts out.
    Cost is tracked in cents for precision.
    """
    __tablename__ = "usage_logs"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    
    # Foreign key to user
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Request details
    prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,  # Null if user opted out
    )
    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    
    # Token usage
    prompt_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    
    # Cost in cents (e.g., 15 = $0.15)
    cost_cents: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    
    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="usage_logs")
    
    @property
    def cost(self) -> float:
        """Get cost in dollars."""
        return self.cost_cents / 100.0

    @property
    def total_tokens(self) -> int:
        """Total prompt + completion tokens."""
        return self.prompt_tokens + self.completion_tokens
    
    def __repr__(self) -> str:
        return f"<UsageLog {self.id[:8]} model={self.model} cost=${self.cost:.4f}>"


class AccessToken(Base):
    """
    Access token tracking for potential revocation.
    
    Stores a hash of the JWT token (not the token itself) for security.
    """
    __tablename__ = "access_tokens"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    
    # Foreign key to user
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Token hash (SHA256 of JWT)
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    
    # Token metadata
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    
    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="access_tokens")
    
    def __repr__(self) -> str:
        status = "revoked" if self.revoked else "active"
        return f"<AccessToken {self.id[:8]} user={self.user_id[:8]} {status}>"


class Conversation(Base):
    """
    Conversation thread for chat history.
    
    Each user can have multiple conversations, each containing
    a history of messages with the AI assistant.
    """
    __tablename__ = "conversations"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    
    # Foreign key to user
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Foreign key to project (nullable for global conversations)
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    
    # Conversation metadata
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="New Conversation",
    )
    
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    
    # Project context at conversation start (JSON works for both PostgreSQL and SQLite)
    project_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        index=True,
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.timestamp",
    )
    
    def __repr__(self) -> str:
        return f"<Conversation {self.id[:8]} '{self.title[:30]}' messages={len(self.messages)}>"


class ConversationMessage(Base):
    """
    Individual message within a conversation.
    
    Stores user prompts and assistant responses, including
    token usage, costs, and tool calls.
    """
    __tablename__ = "conversation_messages"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    
    # Foreign key to conversation
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Message details
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # 'user', 'assistant', 'system'
    
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    
    # Model and token tracking (only for assistant messages)
    model_used: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    
    tokens_used: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )  # {"prompt": 1234, "completion": 567}
    
    # Cost in cents
    cost_cents: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    
    # Tool calls made during this message
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    
    # Complete SSE event stream for full replay capability
    # Each event: {"type": "...", "data": {...}, "timestamp": "..."}
    sse_events: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    
    # Additional message metadata (renamed from 'metadata' - reserved by SQLAlchemy)
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    
    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    
    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
    )
    actions: Mapped[list["MessageAction"]] = relationship(
        "MessageAction",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageAction.timestamp",
    )
    
    @property
    def cost(self) -> float:
        """Get cost in dollars."""
        return self.cost_cents / 100.0
    
    def __repr__(self) -> str:
        return f"<Message {self.id[:8]} {self.role} cost=${self.cost:.4f}>"


class MessageAction(Base):
    """
    Action performed during message execution.
    
    Tracks tool calls and their outcomes (track added, region created, etc.)
    for audit trail and potential undo/redo functionality.
    """
    __tablename__ = "message_actions"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    
    # Foreign key to message
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversation_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Action details
    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # track_added, region_created, notes_added, etc.
    
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    
    # Additional context (renamed from 'metadata' - reserved by SQLAlchemy)
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )  # Track ID, region ID, etc.
    
    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    
    # Relationship
    message: Mapped["ConversationMessage"] = relationship(
        "ConversationMessage",
        back_populates="actions",
    )
    
    def __repr__(self) -> str:
        status = "✓" if self.success else "✗"
        return f"<Action {self.id[:8]} {status} {self.action_type}>"
