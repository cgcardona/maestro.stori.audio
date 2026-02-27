"""Request/response Pydantic models for conversation routes."""
from __future__ import annotations

from pydantic import Field

from maestro.contracts.llm_types import UsageStats
from maestro.contracts.project_types import ProjectContext
from maestro.models.base import CamelModel


class ConversationCreateRequest(CamelModel):
    """Request to create a new conversation."""
    title: str = Field(default="New Conversation", max_length=255)
    project_id: str | None = Field(default=None, description="Project UUID to link conversation to")
    project_context: ProjectContext | None = Field(default=None)


class ConversationUpdateRequest(CamelModel):
    """Request to update conversation metadata."""
    title: str | None = Field(None, max_length=255)
    project_id: str | None = Field(None, description="Project UUID (set to 'null' string to unlink)")


class ToolCallInfo(CamelModel):
    """
    Tool call information in flat format (hybrid of OpenAI + our storage).

    Storage format: {id, type, name, arguments} - flat for easy API consumption
    LLM format: {id, type, function: {name, arguments}} - nested OpenAI standard
    """
    id: str | None = None
    type: str = "function"
    name: str
    arguments: dict[str, object]


class MessageInfo(CamelModel):
    """Message information in conversation responses."""
    model_config = {"from_attributes": True, **CamelModel.model_config}

    id: str
    role: str
    content: str
    timestamp: str
    model_used: str | None = None
    tokens_used: UsageStats | None = None
    cost: float
    tool_calls: list[ToolCallInfo] | None = None
    sse_events: list[dict[str, object]] | None = None
    actions: list[dict[str, object]] | None = None


class ConversationResponse(CamelModel):
    """Full conversation with messages."""
    id: str
    title: str
    project_id: str | None = None
    created_at: str
    updated_at: str
    is_archived: bool
    project_context: ProjectContext | None = None
    messages: list[MessageInfo] = []


class ConversationListItem(CamelModel):
    """Conversation list item (without messages)."""
    id: str
    title: str
    project_id: str | None = None
    created_at: str
    updated_at: str
    is_archived: bool
    message_count: int
    preview: str


class ConversationListResponse(CamelModel):
    """Paginated list of conversations."""
    conversations: list[ConversationListItem]
    total: int
    limit: int
    offset: int


class SearchResultItem(CamelModel):
    """Search result item."""
    id: str
    title: str
    preview: str
    updated_at: str
    relevance_score: float = 1.0


class SearchResponse(CamelModel):
    """Search results."""
    results: list[SearchResultItem]


class ConversationUpdateResponse(CamelModel):
    """Confirmation of a successful ``PATCH /conversations/{id}`` operation.

    Contains only the fields that may have changed — the caller already knows
    the full conversation from a prior ``GET`` and only needs to reconcile the
    delta.  Immutable fields (``created_at``, ``is_archived``, ``messages``,
    etc.) are intentionally omitted to keep the response minimal.

    Wire format: camelCase (via ``CamelModel``) — e.g. ``projectId``,
    ``updatedAt``.

    Attributes:
        id: UUID of the conversation that was updated.
        title: Current title of the conversation after the update.  If the
            request did not supply a new title, this echoes the existing value.
        project_id: UUID of the project the conversation is now linked to, or
            ``None`` if the conversation was unlinked (client sent
            ``project_id: "null"``).
        updated_at: ISO-8601 UTC timestamp of the moment the record was last
            modified.  Refreshed on every successful PATCH.
    """

    id: str = Field(description="UUID of the conversation that was updated.")
    title: str = Field(
        description="Current title after the update (echoes existing value if not changed)."
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "UUID of the linked project, or None if the conversation was unlinked "
            "(client sent project_id: 'null')."
        ),
    )
    updated_at: str = Field(
        description="ISO-8601 UTC timestamp of when the record was last modified."
    )
