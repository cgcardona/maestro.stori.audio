"""Muse Hub webhook subscription route handlers.

Endpoint summary:
  POST   /musehub/repos/{repo_id}/webhooks                          — register a webhook
  GET    /musehub/repos/{repo_id}/webhooks                          — list webhooks
  DELETE /musehub/repos/{repo_id}/webhooks/{webhook_id}             — remove a webhook
  GET    /musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries  — delivery history

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_webhook_dispatcher.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    WEBHOOK_EVENT_TYPES,
    WebhookCreate,
    WebhookDeliveryListResponse,
    WebhookListResponse,
    WebhookResponse,
)
from maestro.services import musehub_repository, musehub_webhook_dispatcher

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/webhooks",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWebhook",
    summary="Register a webhook subscription for a repo",
)
async def create_webhook(
    repo_id: str,
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> WebhookResponse:
    """Register a new webhook that will receive HTTP POSTs for the requested event types.

    ``events`` must be a non-empty subset of: push, pull_request, issue,
    release, branch, tag, session, analysis.  Unknown event types are rejected
    with HTTP 422.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    unknown = [e for e in body.events if e not in WEBHOOK_EVENT_TYPES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown event types: {unknown}. Valid types: {sorted(WEBHOOK_EVENT_TYPES)}",
        )

    webhook = await musehub_webhook_dispatcher.create_webhook(
        db,
        repo_id=repo_id,
        url=body.url,
        events=body.events,
        secret=body.secret,
    )
    await db.commit()
    return webhook


@router.get(
    "/repos/{repo_id}/webhooks",
    response_model=WebhookListResponse,
    operation_id="listWebhooks",
    summary="List webhook subscriptions for a repo",
)
async def list_webhooks(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> WebhookListResponse:
    """Return all registered webhooks for the given repo, ordered by creation time."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    webhooks = await musehub_webhook_dispatcher.list_webhooks(db, repo_id)
    return WebhookListResponse(webhooks=webhooks)


@router.delete(
    "/repos/{repo_id}/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteWebhook",
    summary="Delete a webhook subscription",
)
async def delete_webhook(
    repo_id: str,
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> None:
    """Remove a webhook subscription.  All delivery history is also deleted (cascade)."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    deleted = await musehub_webhook_dispatcher.delete_webhook(db, repo_id, webhook_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    await db.commit()


@router.get(
    "/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
    response_model=WebhookDeliveryListResponse,
    operation_id="listWebhookDeliveries",
    summary="List delivery history for a webhook",
)
async def list_deliveries(
    repo_id: str,
    webhook_id: str,
    limit: int = Query(50, ge=1, le=200, description="Max delivery records to return"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> WebhookDeliveryListResponse:
    """Return delivery attempts for a webhook, newest first.

    Each attempt (including retries) is a separate record.  Use ``limit`` to
    page through the history.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    webhook = await musehub_webhook_dispatcher.get_webhook(db, repo_id, webhook_id)
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    deliveries = await musehub_webhook_dispatcher.list_deliveries(db, webhook_id, limit=limit)
    return WebhookDeliveryListResponse(deliveries=deliveries)
