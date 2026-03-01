"""Muse Hub notification inbox UI page.

Serves the authenticated notification inbox at ``/musehub/ui/notifications``.

Endpoint:
  GET /musehub/ui/notifications
    - HTML (default): paginated, filterable notification inbox with
      mark-as-read and mark-all-read controls.
    - JSON (``?format=json`` or ``Accept: application/json``): structured
      ``NotificationsPageResponse`` for agent consumption.

Query parameters (HTML and JSON):
  type        Filter by notification event type (e.g. ``mention``, ``watch``,
              ``fork``).  Omit to show all types.
  unread_only Show only unread notifications (default: ``false``).
  page        Page number (1-indexed, default: 1).
  per_page    Items per page (1–100, default: 25).
  format      Force ``json`` response regardless of Accept header.

Auth: JWT required for JSON responses (personal data); the HTML shell is
served without auth so the browser can display a JWT entry prompt for users
who are not yet authenticated.  Client-side JavaScript enforces auth via the
``localStorage`` token before fetching notification data from the API.

Auto-discovered by ``maestro.api.routes.musehub.__init__`` because this
module exposes a ``router`` attribute.  No changes to ``__init__.py`` needed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.auth.dependencies import TokenClaims, optional_token
from maestro.db import get_db
from maestro.db.musehub_models import MusehubNotification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui-notifications"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class NotificationItem(BaseModel):
    """A single notification entry in the inbox response.

    ``created_at`` is ISO 8601 so JSON consumers can parse it without knowing
    the server's timezone.  Keys are camelCase for consistency with all other
    MuseHub JSON endpoints.
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    notif_id: str
    event_type: str
    repo_id: str | None
    actor: str
    payload: dict[str, object]
    is_read: bool
    created_at: str


class NotificationsPageResponse(BaseModel):
    """Paginated notification inbox — returned for JSON consumers.

    Includes the notification list, pagination metadata, and the active filter
    state so agents can construct follow-up requests without re-parsing URLs.

    ``unread_count`` reflects the global unread count for the user (not scoped
    to the current type/unread_only filter) so badge displays stay accurate.

    Keys are camelCase (alias_generator) so the JSON contract matches all other
    MuseHub ``negotiate_response`` endpoints.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    notifications: list[NotificationItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    unread_count: int
    type_filter: str | None
    unread_only: bool


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.get(
    "/notifications",
    summary="Muse Hub notification inbox",
)
async def notifications_page(
    request: Request,
    type: str | None = Query(
        None,
        description="Filter by notification event type (e.g. mention, watch, fork)",
    ),
    unread_only: bool = Query(False, description="Show only unread notifications"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(25, ge=1, le=100, description="Items per page"),
    format: str | None = Query(
        None, description="Force response format: 'json' or omit for HTML"
    ),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Render the notification inbox or return paginated JSON for agent consumption.

    Why auth is split between HTML and JSON paths: the HTML shell is always
    returned so the browser can display a JWT entry form for unauthenticated
    users; the JSON path enforces auth directly because there is no shell to
    fall back to and notification data is personal.

    Filters are applied additively: ``type`` narrows by event type and
    ``unread_only`` further restricts to unread rows when both are supplied.
    """
    wants_json = _prefers_json(request, format)

    # JSON callers need real data — enforce auth here.
    if wants_json and claims is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to read notifications.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    context: dict[str, object] = {
        "title": "Notifications",
        "type_filter": type or "",
        "unread_only": unread_only,
        "page": page,
        "per_page": per_page,
        "current_page": "notifications",
    }

    json_data: NotificationsPageResponse | None = None
    if wants_json and claims is not None:
        user_id: str = claims.get("sub", "")
        json_data = await _build_notifications_page(
            db=db,
            user_id=user_id,
            type_filter=type,
            unread_only=unread_only,
            page=page,
            per_page=per_page,
        )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/notifications.html",
        context=context,
        templates=templates,
        json_data=json_data,
        format_param=format,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prefers_json(request: Request, format_param: str | None) -> bool:
    """Return True when the caller prefers a JSON response.

    Mirrors the logic in ``negotiate.py`` without importing the private
    ``_wants_json`` helper.  Decision order: ``?format=json`` param, then
    ``Accept: application/json`` header.
    """
    if format_param == "json":
        return True
    return "application/json" in request.headers.get("accept", "")


async def _build_notifications_page(
    *,
    db: AsyncSession,
    user_id: str,
    type_filter: str | None,
    unread_only: bool,
    page: int,
    per_page: int,
) -> NotificationsPageResponse:
    """Query the DB and assemble a paginated NotificationsPageResponse.

    ``unread_count`` is always the global count for the user — independent of
    ``type_filter`` and ``unread_only`` — so the inbox badge stays accurate
    even when a narrow filter is active.
    """
    base_q = select(MusehubNotification).where(
        MusehubNotification.recipient_id == user_id
    )
    if type_filter:
        base_q = base_q.where(MusehubNotification.event_type == type_filter)
    if unread_only:
        base_q = base_q.where(MusehubNotification.is_read.is_(False))

    total: int = (
        await db.execute(select(func.count()).select_from(base_q.subquery()))
    ).scalar_one()

    unread_count: int = (
        await db.execute(
            select(func.count()).where(
                MusehubNotification.recipient_id == user_id,
                MusehubNotification.is_read.is_(False),
            )
        )
    ).scalar_one()

    offset = (page - 1) * per_page
    rows = (
        await db.execute(
            base_q.order_by(MusehubNotification.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
    ).scalars().all()

    notifications = [
        NotificationItem(
            notif_id=str(r.notif_id),
            event_type=str(r.event_type),
            repo_id=str(r.repo_id) if r.repo_id is not None else None,
            actor=str(r.actor),
            payload=dict(r.payload) if r.payload else {},
            is_read=bool(r.is_read),
            created_at=r.created_at.isoformat()
            if isinstance(r.created_at, datetime)
            else str(r.created_at),
        )
        for r in rows
    ]

    total_pages = max(1, (total + per_page - 1) // per_page)
    return NotificationsPageResponse(
        notifications=notifications,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        unread_count=unread_count,
        type_filter=type_filter,
        unread_only=unread_only,
    )
