"""
Conversation route package.

Provides CRUD operations and message streaming for conversation threads.

Public re-exports (used by existing tests and external code):
    normalize_tool_arguments, build_conversation_history_for_llm, sse_event
"""

from fastapi import APIRouter

from app.api.routes.conversations import crud, messages
from app.api.routes.conversations.helpers import (
    normalize_tool_arguments,
    build_conversation_history_for_llm,
    sse_event,
)

router = APIRouter()
# search must come before the {conversation_id} catch-all
router.include_router(crud.router)
router.include_router(messages.router)

__all__ = [
    "router",
    "normalize_tool_arguments",
    "build_conversation_history_for_llm",
    "sse_event",
]
