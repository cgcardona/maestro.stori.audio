"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from agentception.models import PipelineState
from agentception.poller import get_state

_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE.parent / "templates"))

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    """Dashboard overview — live agent hierarchy tree and GitHub board sidebar.

    Renders on every request with the latest polled state. The page connects
    to ``GET /events`` via SSE to receive live updates without reloading.
    """
    state = get_state() or PipelineState.empty()
    return _TEMPLATES.TemplateResponse(request, "overview.html", {"state": state})
