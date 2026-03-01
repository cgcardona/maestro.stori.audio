"""Muse Hub notification inbox UI page.

Serves the authenticated notification inbox at ``/musehub/ui/notifications``.

Endpoint:
  GET /musehub/ui/notifications
    - HTML (default): full SSR paginated, filterable notification inbox
      with HTMX filter swaps.  Authenticated users see their notifications
      server-side rendered on the first load; unauthenticated users see a
      login prompt.
    - HTMX (``HX-Request: true``): returns only the notification rows
      fragment so the filter form can swap the list in-place.
    - JSON (``?format=json`` or ``Accept: application/json``): structured
      ``NotificationsPageResponse`` for agent consumption.

Query parameters (HTML and JSON):
  type_filter  Filter by notification event type (e.g. ``mention``, ``watch``,
               ``fork``).  Omit to show all types.
  unread_only  Show only unread notifications (default: ``false``).
  page         Page number (1-indexed, default: 1).
  per_page     Items per page (1–100, default: 25).
  format       Force ``json`` response regardless of Accept header.

Auth: JWT is optional for HTML responses.  When a valid JWT is present the
handler fetches and renders notification data server-side.  When absent, the
page renders a login prompt.  The JSON path enforces auth directly because
there is no HTML shell to fall back to.

Auto-discovered by ``maestro.api.routes.musehub.__init__`` because this
module exposes a ``router`` attribute.  No changes to ``__init__.py`` needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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

from maestro.api.routes.musehub.htmx_helpers import htmx_fragment_or_full
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.auth.dependencies import TokenClaims, optional_token
from maestro.db import get_db
from maestro.db.musehub_models import MusehubNotification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui-notifications"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Register a relative-time filter so templates can render ``created_at``
# strings as human-readable durations without a client-side JavaScript call.
_EVENT_TYPES = [
    "comment",
    "mention",
    "pr_opened",
    "pr_merged",
    "issue_opened",
    "issue_closed",
    "new_commit",
    "new_follower",
    "watch",
    "fork",
]


def _fmt_relative(value: str) -> str:
    """Format an ISO 8601 datetime string as a human-readable relative duration.

    Used as a Jinja2 template filter (``| fmtrelative``) so notification
    timestamps are displayed as "5m ago" instead of raw ISO strings.

    Handles both tz-aware (e.g. "2026-03-01T19:00:00+00:00") and tz-naive
    (e.g. "2026-03-01T19:00:00") strings so SQLite — which strips timezone info
    on roundtrip — doesn't cause a TypeError at comparison time.
    Returns the raw value unchanged if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            now: datetime = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        diff = int((now - dt).total_seconds())
        if diff < 60:
            return "just now"
        if diff < 3600:
            return f"{diff // 60}m ago"
        if diff < 86400:
            return f"{diff // 3600}h ago"
        return f"{diff // 86400}d ago"
    except (ValueError, AttributeError):
        return str(value)


templates.env.filters["fmtrelative"] = _fmt_relative


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
    type_filter: str | None = Query(
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
    """Render the SSR notification inbox or return paginated JSON for agents.

    HTML path (default):
      - Authenticated: fetches notifications server-side and renders the full
        inbox with HTMX filter support.  HTMX requests (``HX-Request: true``)
        return only the notification rows fragment for in-place swaps.
      - Unauthenticated: renders a login prompt without touching the DB.

    JSON path (``?format=json`` or ``Accept: application/json``):
      - Requires a valid JWT.  Returns ``NotificationsPageResponse``.

    Filters applied additively: ``type_filter`` narrows by event type and
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

    if wants_json and claims is not None:
        user_id: str = claims.get("sub", "")
        json_data = await _build_notifications_page(
            db=db,
            user_id=user_id,
            type_filter=type_filter,
            unread_only=unread_only,
            page=page,
            per_page=per_page,
        )
        return await negotiate_response(
            request=request,
            template_name="musehub/pages/notifications.html",
            context={},
            templates=templates,
            json_data=json_data,
            format_param=format,
        )

    # HTML path: SSR when authenticated, login prompt otherwise.
    if claims is None:
        ctx: dict[str, object] = {
            "title": "Notifications",
            "authenticated": False,
            "current_page": "notifications",
        }
        return templates.TemplateResponse(
            request, "musehub/pages/notifications.html", ctx
        )

    user_id = claims.get("sub", "")
    notif_page = await _build_notifications_page(
        db=db,
        user_id=user_id,
        type_filter=type_filter,
        unread_only=unread_only,
        page=page,
        per_page=per_page,
    )
    # Serialize to camelCase dicts so templates access notif.notifId etc.
    notif_dicts = [
        n.model_dump(by_alias=True) for n in notif_page.notifications
    ]
    ctx = {
        "title": "Notifications",
        "authenticated": True,
        "current_page": "notifications",
        "notifications": notif_dicts,
        "total": notif_page.total,
        "unread_count": notif_page.unread_count,
        "type_filter": type_filter,
        "unread_only": unread_only,
        "page": page,
        "per_page": per_page,
        "total_pages": notif_page.total_pages,
        "event_types": _EVENT_TYPES,
    }
    return await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/notifications.html",
        fragment_template="musehub/fragments/notification_rows.html",
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
