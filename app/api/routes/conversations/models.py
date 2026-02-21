"""Request/response Pydantic models for conversation routes."""

from typing import Optional

from pydantic import Field

from app.models.base import CamelModel


class ConversationCreateRequest(CamelModel):
    """Request to create a new conversation."""
    title: str = Field(default="New Conversation", max_length=255)
    project_id: Optional[str] = Field(default=None, description="Project UUID to link conversation to")
    project_context: Optional[dict] = Field(default=None)


class ConversationUpdateRequest(CamelModel):
    """Request to update conversation metadata."""
    title: Optional[str] = Field(None, max_length=255)
    project_id: Optional[str] = Field(None, description="Project UUID (set to 'null' string to unlink)")


class ToolCallInfo(CamelModel):
    """
    Tool call information in flat format (hybrid of OpenAI + our storage).

    Storage format: {id, type, name, arguments} - flat for easy API consumption
    LLM format: {id, type, function: {name, arguments}} - nested OpenAI standard
    """
    id: Optional[str] = None
    type: str = "function"
    name: str
    arguments: dict


class MessageInfo(CamelModel):
    """Message information in conversation responses."""
    model_config = {"from_attributes": True, **CamelModel.model_config}

    id: str
    role: str
    content: str
    timestamp: str
    model_used: Optional[str] = None
    tokens_used: Optional[dict] = None
    cost: float
    tool_calls: Optional[list[ToolCallInfo]] = None
    sse_events: Optional[list[dict]] = None
    actions: Optional[list[dict]] = None


class ConversationResponse(CamelModel):
    """Full conversation with messages."""
    id: str
    title: str
    project_id: Optional[str] = None
    created_at: str
    updated_at: str
    is_archived: bool
    project_context: Optional[dict] = None
    messages: list[MessageInfo] = []


class ConversationListItem(CamelModel):
    """Conversation list item (without messages)."""
    id: str
    title: str
    project_id: Optional[str] = None
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
