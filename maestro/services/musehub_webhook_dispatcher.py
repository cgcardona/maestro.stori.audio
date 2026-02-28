"""Muse Hub webhook dispatcher — event-driven HTTP notification delivery.

This module is the single point responsible for delivering webhook events to
registered subscriber URLs.  It is called by route handlers after a state-
changing operation completes (push, issue create/close, PR create/merge, etc.).

Delivery contract:
- HTTP POST to the subscriber's ``url`` with a JSON payload.
- ``Content-Type: application/json``
- ``X-MuseHub-Event: <event_type>`` header identifying the event.
- ``X-MuseHub-Delivery: <delivery_id>`` header for idempotency.
- ``X-MuseHub-Signature: sha256=<hmac_hex>`` header when ``secret`` is set.
- Retry policy: up to 3 attempts with exponential back-off (1 s, 2 s, 4 s).
- Each attempt is logged as a separate ``MusehubWebhookDelivery`` row.

Boundary rules (same as all musehub services):
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic models from maestro.models.musehub.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.db.database import AsyncSessionLocal
from maestro.models.musehub import (
    WebhookDeliveryResponse,
    WebhookEventPayload,
    WebhookResponse,
)

logger = logging.getLogger(__name__)

# Maximum delivery attempts per dispatch call (initial + 2 retries).
_MAX_ATTEMPTS = 3
# Base back-off in seconds; doubled on each retry.
_BACKOFF_BASE = 1.0
# HTTP timeout per outbound POST attempt.
_REQUEST_TIMEOUT = 10.0


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _sign_payload(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 signature for ``body`` using ``secret``.

    Returns the hex digest prefixed with ``sha256=``, matching GitHub's
    webhook signature convention so existing verification libraries work.
    """
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _to_webhook_response(row: db.MusehubWebhook) -> WebhookResponse:
    return WebhookResponse(
        webhook_id=row.webhook_id,
        repo_id=row.repo_id,
        url=row.url,
        events=list(row.events or []),
        active=row.active,
        created_at=row.created_at,
    )


def _to_delivery_response(row: db.MusehubWebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse(
        delivery_id=row.delivery_id,
        webhook_id=row.webhook_id,
        event_type=row.event_type,
        attempt=row.attempt,
        success=row.success,
        response_status=row.response_status,
        response_body=row.response_body,
        delivered_at=row.delivered_at,
    )


# ---------------------------------------------------------------------------
# Webhook CRUD
# ---------------------------------------------------------------------------


async def create_webhook(
    session: AsyncSession,
    *,
    repo_id: str,
    url: str,
    events: list[str],
    secret: str,
) -> WebhookResponse:
    """Persist a new webhook subscription and return its wire representation.

    The webhook is created in active state.  The caller must commit the session.
    """
    webhook = db.MusehubWebhook(
        repo_id=repo_id,
        url=url,
        events=events,
        secret=secret,
        active=True,
    )
    session.add(webhook)
    await session.flush()
    await session.refresh(webhook)
    logger.info("✅ Registered webhook %s for repo %s → %s", webhook.webhook_id, repo_id, url)
    return _to_webhook_response(webhook)


async def list_webhooks(
    session: AsyncSession,
    repo_id: str,
) -> list[WebhookResponse]:
    """Return all webhook subscriptions for a repo, ordered by created_at."""
    stmt = (
        select(db.MusehubWebhook)
        .where(db.MusehubWebhook.repo_id == repo_id)
        .order_by(db.MusehubWebhook.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_webhook_response(r) for r in rows]


async def get_webhook(
    session: AsyncSession,
    repo_id: str,
    webhook_id: str,
) -> WebhookResponse | None:
    """Return a single webhook by ID, or None if not found in this repo."""
    stmt = select(db.MusehubWebhook).where(
        db.MusehubWebhook.repo_id == repo_id,
        db.MusehubWebhook.webhook_id == webhook_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _to_webhook_response(row)


async def delete_webhook(
    session: AsyncSession,
    repo_id: str,
    webhook_id: str,
) -> bool:
    """Delete a webhook by ID.  Returns True if deleted, False if not found.

    The caller must commit the session after a True result.
    """
    stmt = select(db.MusehubWebhook).where(
        db.MusehubWebhook.repo_id == repo_id,
        db.MusehubWebhook.webhook_id == webhook_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    logger.info("✅ Deleted webhook %s from repo %s", webhook_id, repo_id)
    return True


async def list_deliveries(
    session: AsyncSession,
    webhook_id: str,
    *,
    limit: int = 50,
) -> list[WebhookDeliveryResponse]:
    """Return delivery history for a webhook, newest first.

    ``limit`` caps the number of rows returned (default 50, max 200).
    """
    stmt = (
        select(db.MusehubWebhookDelivery)
        .where(db.MusehubWebhookDelivery.webhook_id == webhook_id)
        .order_by(db.MusehubWebhookDelivery.delivered_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_delivery_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _attempt_delivery(
    client: httpx.AsyncClient,
    *,
    webhook: db.MusehubWebhook,
    event_type: str,
    payload_bytes: bytes,
    delivery_id: str,
    attempt: int,
) -> tuple[bool, int, str]:
    """Execute one HTTP POST attempt and return (success, status_code, body_snippet).

    Returns (False, 0, error_message) when the request fails at the transport
    layer (timeout, DNS failure, connection refused).
    """
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-MuseHub-Event": event_type,
        "X-MuseHub-Delivery": delivery_id,
        "User-Agent": "MuseHub-Webhook/1.0",
    }
    if webhook.secret:
        headers["X-MuseHub-Signature"] = _sign_payload(webhook.secret, payload_bytes)

    try:
        resp = await client.post(
            webhook.url,
            content=payload_bytes,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        success = resp.is_success
        return success, resp.status_code, resp.text[:512]
    except httpx.TimeoutException as exc:
        return False, 0, f"timeout: {exc}"
    except httpx.RequestError as exc:
        return False, 0, f"request error: {exc}"


async def dispatch_event(
    session: AsyncSession,
    *,
    repo_id: str,
    event_type: str,
    payload: WebhookEventPayload,
) -> None:
    """Dispatch a webhook event to all active subscribers for ``repo_id``.

    Called by route handlers after a state-changing operation.  Does NOT block
    the caller's HTTP response — this function handles its own retries internally
    and logs each attempt to ``musehub_webhook_deliveries``.

    The ``payload`` dict is serialised to camelCase JSON before delivery.  It
    should use snake_case keys; the serialiser converts them automatically via
    the Pydantic CamelModel aliases so that wire format is consistent with the
    rest of the MuseHub API.

    Delivery is best-effort: failures are logged but never raised.
    """
    stmt = select(db.MusehubWebhook).where(
        db.MusehubWebhook.repo_id == repo_id,
        db.MusehubWebhook.active.is_(True),
    )
    webhooks = (await session.execute(stmt)).scalars().all()

    active = [w for w in webhooks if event_type in (w.events or [])]
    if not active:
        return

    payload_bytes = json.dumps(payload, default=str).encode()

    async with httpx.AsyncClient() as client:
        for webhook in active:
            delivery_id = _new_uuid()
            success = False
            status_code = 0
            response_body = ""

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                success, status_code, response_body = await _attempt_delivery(
                    client,
                    webhook=webhook,
                    event_type=event_type,
                    payload_bytes=payload_bytes,
                    delivery_id=delivery_id,
                    attempt=attempt,
                )

                delivery_row = db.MusehubWebhookDelivery(
                    webhook_id=webhook.webhook_id,
                    event_type=event_type,
                    attempt=attempt,
                    success=success,
                    response_status=status_code,
                    response_body=response_body,
                )
                session.add(delivery_row)
                await session.flush()

                if success:
                    logger.info(
                        "✅ Webhook %s delivered '%s' on attempt %d (status %d)",
                        webhook.webhook_id,
                        event_type,
                        attempt,
                        status_code,
                    )
                    break

                if attempt < _MAX_ATTEMPTS:
                    backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "⚠️ Webhook %s attempt %d failed (status %d) — retrying in %.1fs",
                        webhook.webhook_id,
                        attempt,
                        status_code,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "❌ Webhook %s delivery failed after %d attempts (last status %d)",
                        webhook.webhook_id,
                        _MAX_ATTEMPTS,
                        status_code,
                    )


async def dispatch_event_background(
    repo_id: str,
    event_type: str,
    payload: WebhookEventPayload,
) -> None:
    """Fire-and-forget webhook dispatch that manages its own DB session.

    Intended for use with FastAPI ``BackgroundTasks`` so that webhook delivery
    does not block the HTTP response.  Errors are logged but never re-raised.

    Usage in a route handler::

        background_tasks.add_task(
            dispatch_event_background,
            repo_id=repo_id,
            event_type="push",
            payload={"repoId": repo_id, "branch": branch, ...},
        )
    """
    try:
        async with AsyncSessionLocal() as session:
            await dispatch_event(session, repo_id=repo_id, event_type=event_type, payload=payload)
            await session.commit()
    except Exception as exc:
        logger.error(
            "❌ Background webhook dispatch failed for repo %s event '%s': %s",
            repo_id,
            event_type,
            exc,
        )
