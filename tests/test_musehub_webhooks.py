"""Tests for Muse Hub webhook subscription endpoints and dispatch.

Covers every acceptance criterion from issue #247 and #422:
- POST /musehub/repos/{repo_id}/webhooks registers a webhook with URL and events
- GET  /musehub/repos/{repo_id}/webhooks lists registered webhooks
- DELETE /musehub/repos/{repo_id}/webhooks/{webhook_id} removes a webhook
- GET /musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries lists delivery history
- POST /musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/{id}/redeliver retries delivery
- HMAC-SHA256 signature computation is correct
- Webhook dispatch fires for matching events
- Delivery logging records success/failure per attempt
- Retries attempted on failure (up to _MAX_ATTEMPTS)
- Webhooks require valid JWT

All tests use shared ``client``, ``auth_headers``, and ``db_session`` fixtures
from conftest.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any  # used for MagicMock return annotations only
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.models.musehub import IssueEventPayload, PushEventPayload
from maestro.services import musehub_webhook_dispatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    name: str = "webhook-test-repo",
) -> str:
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id: str = resp.json()["repoId"]
    return repo_id


async def _create_webhook(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    secret: str = "",
) -> dict[str, Any]:
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        json={"url": url, "events": events or ["push"], "secret": secret},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data: dict[str, Any] = resp.json()
    return data


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/webhooks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_webhook_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /webhooks registers a webhook subscription and returns 201."""
    repo_id = await _create_repo(client, auth_headers, "create-wh-repo")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        json={"url": "https://example.com/hook", "events": ["push", "issue"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["repoId"] == repo_id
    assert data["url"] == "https://example.com/hook"
    assert set(data["events"]) == {"push", "issue"}
    assert data["active"] is True
    assert "webhookId" in data


@pytest.mark.anyio
async def test_create_webhook_unknown_event_type_returns_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /webhooks with an unknown event type is rejected with 422."""
    repo_id = await _create_repo(client, auth_headers, "bad-event-repo")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        json={"url": "https://example.com/hook", "events": ["not_a_real_event"]},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_create_webhook_unknown_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /webhooks for a non-existent repo returns 404."""
    resp = await client.post(
        "/api/v1/musehub/repos/does-not-exist/webhooks",
        json={"url": "https://example.com/hook", "events": ["push"]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/webhooks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_webhooks_returns_registered_webhooks(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /webhooks returns all registered webhooks for a repo."""
    repo_id = await _create_repo(client, auth_headers, "list-wh-repo")
    await _create_webhook(client, auth_headers, repo_id, url="https://a.example.com/hook", events=["push"])
    await _create_webhook(client, auth_headers, repo_id, url="https://b.example.com/hook", events=["issue"])

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    webhooks = resp.json()["webhooks"]
    assert len(webhooks) == 2
    urls = {w["url"] for w in webhooks}
    assert urls == {"https://a.example.com/hook", "https://b.example.com/hook"}


@pytest.mark.anyio
async def test_list_webhooks_empty_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /webhooks for a repo with no webhooks returns an empty list."""
    repo_id = await _create_repo(client, auth_headers, "empty-wh-repo")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["webhooks"] == []


# ---------------------------------------------------------------------------
# DELETE /musehub/repos/{repo_id}/webhooks/{webhook_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_webhook_removes_subscription(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /webhooks/{id} removes the webhook and returns 204."""
    repo_id = await _create_repo(client, auth_headers, "del-wh-repo")
    wh = await _create_webhook(client, auth_headers, repo_id)
    webhook_id = wh["webhookId"]

    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 204

    # Verify it's gone
    list_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        headers=auth_headers,
    )
    assert list_resp.json()["webhooks"] == []


@pytest.mark.anyio
async def test_delete_webhook_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /webhooks/{id} for a non-existent webhook returns 404."""
    repo_id = await _create_repo(client, auth_headers, "del-missing-wh-repo")
    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/does-not-exist",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_deliveries_empty_on_new_webhook(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /deliveries returns an empty list for a newly created webhook."""
    repo_id = await _create_repo(client, auth_headers, "deliveries-repo")
    wh = await _create_webhook(client, auth_headers, repo_id)
    webhook_id = wh["webhookId"]

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["deliveries"] == []


@pytest.mark.anyio
async def test_list_deliveries_not_found_webhook_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /deliveries for a non-existent webhook returns 404."""
    repo_id = await _create_repo(client, auth_headers, "deliveries-404-repo")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/missing-id/deliveries",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth requirements
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_webhook_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /webhooks without JWT returns 401."""
    repo_id = await _create_repo(client, auth_headers, "auth-wh-repo")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks",
        json={"url": "https://example.com/hook", "events": ["push"]},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_webhooks_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /webhooks without JWT returns 401."""
    repo_id = await _create_repo(client, auth_headers, "auth-list-wh-repo")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/webhooks")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_delete_webhook_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /webhooks/{id} without JWT returns 401."""
    repo_id = await _create_repo(client, auth_headers, "auth-del-wh-repo")
    wh = await _create_webhook(client, auth_headers, repo_id)
    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{wh['webhookId']}",
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# HMAC-SHA256 signature
# ---------------------------------------------------------------------------


def test_webhook_signature_correct() -> None:
    """_sign_payload computes HMAC-SHA256 matching the reference implementation."""
    secret = "my-super-secret"
    body = b'{"repoId": "abc", "event": "push"}'
    expected_mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected = f"sha256={expected_mac}"

    result = musehub_webhook_dispatcher._sign_payload(secret, body)
    assert result == expected


def test_webhook_signature_empty_secret_still_signs() -> None:
    """_sign_payload with empty secret produces a sha256 value (not skipped)."""
    body = b'{"test": true}'
    result = musehub_webhook_dispatcher._sign_payload("", body)
    assert result.startswith("sha256=")
    assert len(result) > len("sha256=")


# ---------------------------------------------------------------------------
# Dispatch logic (unit tests with mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_event_delivers_to_matching_webhooks(
    db_session: AsyncSession,
) -> None:
    """dispatch_event POSTs to webhooks subscribed to the given event type."""
    from maestro.services import musehub_webhook_dispatcher as disp

    await disp.create_webhook(
        db_session,
        repo_id="repo-abc",
        url="https://example.com/push-hook",
        events=["push"],
        secret="",
    )
    await disp.create_webhook(
        db_session,
        repo_id="repo-abc",
        url="https://example.com/issue-hook",
        events=["issue"],
        secret="",
    )
    await db_session.flush()

    posted_urls: list[str] = []

    async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
        posted_urls.append(url)
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        return mock_resp

    push_payload: PushEventPayload = {
        "repoId": "repo-abc",
        "branch": "main",
        "headCommitId": "abc123",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = _fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id="repo-abc",
            event_type="push",
            payload=push_payload,
        )

    assert posted_urls == ["https://example.com/push-hook"]


@pytest.mark.anyio
async def test_dispatch_event_skips_non_matching_event(
    db_session: AsyncSession,
) -> None:
    """dispatch_event does not POST when no webhook subscribes to the event type."""
    from maestro.services import musehub_webhook_dispatcher as disp

    await disp.create_webhook(
        db_session,
        repo_id="repo-xyz",
        url="https://example.com/hook",
        events=["issue"],
        secret="",
    )
    await db_session.flush()

    posted_urls: list[str] = []

    async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
        posted_urls.append(url)
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        return mock_resp

    push_payload: PushEventPayload = {
        "repoId": "repo-xyz",
        "branch": "main",
        "headCommitId": "xyz789",
        "pushedBy": "test-user",
        "commitCount": 0,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = _fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id="repo-xyz",
            event_type="push",
            payload=push_payload,
        )

    assert posted_urls == []


@pytest.mark.anyio
async def test_dispatch_event_logs_delivery_on_success(
    db_session: AsyncSession,
) -> None:
    """dispatch_event creates a MusehubWebhookDelivery row on a successful delivery."""
    from maestro.services import musehub_webhook_dispatcher as disp
    from maestro.db import musehub_models as db_models
    from sqlalchemy import select

    wh = await disp.create_webhook(
        db_session,
        repo_id="repo-log",
        url="https://log.example.com/hook",
        events=["push"],
        secret="",
    )
    await db_session.flush()

    async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "accepted"
        return mock_resp

    log_payload: PushEventPayload = {
        "repoId": "repo-log",
        "branch": "main",
        "headCommitId": "log123",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = _fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id="repo-log",
            event_type="push",
            payload=log_payload,
        )

    stmt = select(db_models.MusehubWebhookDelivery).where(
        db_models.MusehubWebhookDelivery.webhook_id == wh.webhook_id
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].success is True
    assert rows[0].response_status == 200
    assert rows[0].event_type == "push"
    assert rows[0].attempt == 1


@pytest.mark.anyio
async def test_webhook_retry_on_failure_logs_multiple_attempts(
    db_session: AsyncSession,
) -> None:
    """dispatch_event retries up to _MAX_ATTEMPTS and logs each attempt."""
    from maestro.services import musehub_webhook_dispatcher as disp
    from maestro.db import musehub_models as db_models
    from sqlalchemy import select

    wh = await disp.create_webhook(
        db_session,
        repo_id="repo-retry",
        url="https://retry.example.com/hook",
        events=["push"],
        secret="",
    )
    await db_session.flush()

    attempt_count = 0

    async def _always_fail(url: str, **kwargs: Any) -> MagicMock:
        nonlocal attempt_count
        attempt_count += 1
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 503
        mock_resp.text = "service unavailable"
        return mock_resp

    retry_payload: PushEventPayload = {
        "repoId": "repo-retry",
        "branch": "main",
        "headCommitId": "retry123",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    with (
        patch("httpx.AsyncClient") as mock_client_cls,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_client = AsyncMock()
        mock_client.post = _always_fail
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id="repo-retry",
            event_type="push",
            payload=retry_payload,
        )

    assert attempt_count == disp._MAX_ATTEMPTS

    stmt = select(db_models.MusehubWebhookDelivery).where(
        db_models.MusehubWebhookDelivery.webhook_id == wh.webhook_id
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == disp._MAX_ATTEMPTS
    for row in rows:
        assert row.success is False
        assert row.response_status == 503


@pytest.mark.anyio
async def test_webhook_delivery_logging_records_failure_status(
    db_session: AsyncSession,
) -> None:
    """Delivery rows record response_status=0 for network-level failures."""
    import httpx
    from maestro.services import musehub_webhook_dispatcher as disp
    from maestro.db import musehub_models as db_models
    from sqlalchemy import select

    wh = await disp.create_webhook(
        db_session,
        repo_id="repo-net-err",
        url="https://unreachable.example.com/hook",
        events=["issue"],
        secret="",
    )
    await db_session.flush()

    async def _raise_network_error(url: str, **kwargs: Any) -> None:
        raise httpx.ConnectError("Connection refused")

    net_err_payload: IssueEventPayload = {
        "repoId": "repo-net-err",
        "action": "opened",
        "issueId": "issue-001",
        "number": 1,
        "title": "Test issue",
        "state": "open",
    }

    with (
        patch("httpx.AsyncClient") as mock_client_cls,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_client = AsyncMock()
        mock_client.post = _raise_network_error
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id="repo-net-err",
            event_type="issue",
            payload=net_err_payload,
        )

    stmt = select(db_models.MusehubWebhookDelivery).where(
        db_models.MusehubWebhookDelivery.webhook_id == wh.webhook_id
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == disp._MAX_ATTEMPTS
    for row in rows:
        assert row.success is False
        assert row.response_status == 0


# ---------------------------------------------------------------------------
# Delivery history via API
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_deliveries_via_api_after_dispatch(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /deliveries reflects delivery rows written by dispatch_event."""
    from maestro.services import musehub_webhook_dispatcher as disp

    repo_id = await _create_repo(client, auth_headers, "delivery-api-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id, events=["push"])
    webhook_id = wh_data["webhookId"]

    async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        return mock_resp

    api_payload: PushEventPayload = {
        "repoId": repo_id,
        "branch": "main",
        "headCommitId": "api123",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = _fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await disp.dispatch_event(
            db_session,
            repo_id=repo_id,
            event_type="push",
            payload=api_payload,
        )

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    deliveries = resp.json()["deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["eventType"] == "push"
    assert deliveries[0]["success"] is True
    assert deliveries[0]["responseStatus"] == 200


# ---------------------------------------------------------------------------
# Webhook secret encryption (issue #271)
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip_with_key() -> None:
    """encrypt_secret / decrypt_secret round-trips plaintext correctly when a key is set."""
    from unittest.mock import patch
    from cryptography.fernet import Fernet
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()
    # Patch settings so the module picks up the test key on next initialisation.
    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key
            plaintext = "super-secret-hmac-key-for-subscriber"
            ciphertext = crypto.encrypt_secret(plaintext)
            # Ciphertext must differ from plaintext — we encrypted it.
            assert ciphertext != plaintext
            # Round-trip must recover original value.
            recovered = crypto.decrypt_secret(ciphertext)
            assert recovered == plaintext


def test_encrypt_decrypt_empty_secret_passthrough() -> None:
    """Empty secrets are passed through unchanged (no encryption needed)."""
    from maestro.services import musehub_webhook_crypto as crypto

    assert crypto.encrypt_secret("") == ""
    assert crypto.decrypt_secret("") == ""


def test_decrypt_invalid_token_raises_value_error() -> None:
    """decrypt_secret raises ValueError when a Fernet-prefixed token is corrupt/wrong-key.

    Values that *look* like Fernet tokens (prefix "gAAAAAB") but cannot be
    decrypted are genuine key-mismatch or corruption errors — we surface them
    rather than silently falling back so operators notice misconfiguration.
    """
    from unittest.mock import patch
    from cryptography.fernet import Fernet
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()
    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key
            # Encrypt something so fernet is initialised, then pass a corrupted
            # token that carries the Fernet prefix — this should raise ValueError.
            crypto.encrypt_secret("seed")
            corrupt_fernet_token = "gAAAAABthis-looks-like-fernet-but-is-corrupt"
            with pytest.raises(ValueError, match="Failed to decrypt webhook secret"):
                crypto.decrypt_secret(corrupt_fernet_token)


def test_decrypt_plaintext_secret_returns_value_when_key_set() -> None:
    """decrypt_secret returns a plaintext secret as-is when it lacks the Fernet prefix.

    This is the transparent migration fallback (issue #347): secrets written
    before STORI_WEBHOOK_SECRET_KEY was enabled do not start with "gAAAAAB".
    Rather than raising ValueError and breaking all existing webhooks, we
    return the plaintext and emit a deprecation warning so operators know they
    need to run the migration script.
    """
    from unittest.mock import patch
    from cryptography.fernet import Fernet
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()
    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key
            crypto.encrypt_secret("seed")  # initialise singleton
            plaintext_secret = "pre-migration-plaintext-secret"
            # Must NOT start with "gAAAAAB" (simulates a legacy row)
            assert not plaintext_secret.startswith("gAAAAAB")
            result = crypto.decrypt_secret(plaintext_secret)
            assert result == plaintext_secret


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/{id}/redeliver
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redeliver_delivery_succeeds(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /redeliver replays the original payload and returns success=True on 2xx."""
    from maestro.services import musehub_webhook_dispatcher as disp

    repo_id = await _create_repo(client, auth_headers, "redeliver-ok-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id, events=["push"])
    webhook_id = wh_data["webhookId"]

    push_payload: PushEventPayload = {
        "repoId": repo_id,
        "branch": "main",
        "headCommitId": "redeliv01",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    async def _fail_then_ok(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 503
        mock_resp.text = "unavailable"
        return mock_resp

    # Create an initial (failed) delivery so we have a delivery_id.
    with (
        patch("httpx.AsyncClient") as mock_cls,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_client = AsyncMock()
        mock_client.post = _fail_then_ok
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        await disp.dispatch_event(db_session, repo_id=repo_id, event_type="push", payload=push_payload)
    await db_session.commit()

    # Get the first (failed) delivery ID.
    deliveries_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
        headers=auth_headers,
    )
    assert deliveries_resp.status_code == 200
    deliveries = deliveries_resp.json()["deliveries"]
    assert len(deliveries) > 0
    delivery_id = deliveries[0]["deliveryId"]

    # Now redeliver — this time the subscriber returns 200.
    async def _ok(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "accepted"
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls2:
        mock_client2 = AsyncMock()
        mock_client2.post = _ok
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_cls2.return_value = mock_client2

        redeliver_resp = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/{delivery_id}/redeliver",
            headers=auth_headers,
        )

    assert redeliver_resp.status_code == 200
    data = redeliver_resp.json()
    assert data["originalDeliveryId"] == delivery_id
    assert data["webhookId"] == webhook_id
    assert data["success"] is True
    assert data["responseStatus"] == 200


@pytest.mark.anyio
async def test_redeliver_delivery_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /redeliver for a non-existent delivery_id returns 404."""
    repo_id = await _create_repo(client, auth_headers, "redeliver-404-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id)
    webhook_id = wh_data["webhookId"]

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/does-not-exist/redeliver",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_redeliver_delivery_wrong_webhook_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /redeliver with a wrong webhook_id returns 404."""
    repo_id = await _create_repo(client, auth_headers, "redeliver-wrong-wh-repo")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/no-such-webhook/deliveries/some-delivery/redeliver",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_redeliver_delivery_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /redeliver without JWT returns 401."""
    repo_id = await _create_repo(client, auth_headers, "redeliver-auth-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id)
    webhook_id = wh_data["webhookId"]

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/some-id/redeliver",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_redeliver_delivery_stores_new_delivery_row(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /redeliver persists new delivery rows without mutating the original."""
    from sqlalchemy import select
    from maestro.db import musehub_models as db_models
    from maestro.services import musehub_webhook_dispatcher as disp

    repo_id = await _create_repo(client, auth_headers, "redeliver-rows-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id, events=["push"])
    webhook_id = wh_data["webhookId"]

    push_payload: PushEventPayload = {
        "repoId": repo_id,
        "branch": "main",
        "headCommitId": "rows-test",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    async def _ok(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        return mock_resp

    # Initial delivery.
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _ok
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        await disp.dispatch_event(db_session, repo_id=repo_id, event_type="push", payload=push_payload)
    await db_session.commit()

    deliveries_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
        headers=auth_headers,
    )
    delivery_id = deliveries_resp.json()["deliveries"][0]["deliveryId"]

    # Redeliver.
    with patch("httpx.AsyncClient") as mock_cls2:
        mock_client2 = AsyncMock()
        mock_client2.post = _ok
        mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
        mock_client2.__aexit__ = AsyncMock(return_value=False)
        mock_cls2.return_value = mock_client2
        await client.post(
            f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries/{delivery_id}/redeliver",
            headers=auth_headers,
        )

    await db_session.commit()

    # Two delivery rows should exist: the original + the redeliver attempt.
    stmt = select(db_models.MusehubWebhookDelivery).where(
        db_models.MusehubWebhookDelivery.webhook_id == webhook_id
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2

    # Original row unchanged — the redeliver adds a brand-new row.
    original = next(r for r in rows if r.delivery_id == delivery_id)
    assert original.success is True

    # New row also has the stored payload.
    new_row = next(r for r in rows if r.delivery_id != delivery_id)
    assert new_row.payload != ""
    assert new_row.event_type == "push"


@pytest.mark.anyio
async def test_list_deliveries_includes_payload_field(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /deliveries returns a ``payload`` field on each delivery."""
    from maestro.services import musehub_webhook_dispatcher as disp

    repo_id = await _create_repo(client, auth_headers, "delivery-payload-repo")
    wh_data = await _create_webhook(client, auth_headers, repo_id, events=["push"])
    webhook_id = wh_data["webhookId"]

    push_payload: PushEventPayload = {
        "repoId": repo_id,
        "branch": "main",
        "headCommitId": "pay123",
        "pushedBy": "test-user",
        "commitCount": 1,
    }

    async def _ok(url: str, **kwargs: Any) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _ok
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        await disp.dispatch_event(db_session, repo_id=repo_id, event_type="push", payload=push_payload)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    delivery = resp.json()["deliveries"][0]
    assert "payload" in delivery
    assert delivery["payload"] != ""


def test_is_fernet_token_detects_prefix() -> None:
    """is_fernet_token correctly distinguishes Fernet tokens from plaintext."""
    from cryptography.fernet import Fernet
    from maestro.services.musehub_webhook_crypto import encrypt_secret, is_fernet_token

    from unittest.mock import patch
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()
    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key
            token = encrypt_secret("some-secret")

    assert is_fernet_token(token)
    assert not is_fernet_token("plaintext-secret")
    assert not is_fernet_token("")
    assert not is_fernet_token("not-starting-with-gAAAAAB")


def test_migrate_webhook_secrets_logic() -> None:
    """Core migration logic: plaintext rows are re-encrypted; already-encrypted rows skipped.

    This test exercises the detection + re-encryption logic in isolation,
    mirroring what scripts/migrate_webhook_secrets.py does in production.
    """
    from cryptography.fernet import Fernet
    from maestro.services.musehub_webhook_crypto import encrypt_secret, is_fernet_token

    from unittest.mock import patch
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()
    plaintext = "legacy-plaintext-hmac-key"
    already_fernet: str

    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key
            already_fernet = encrypt_secret("already-encrypted")

    # Simulate the per-row migration decision.
    secrets = [plaintext, already_fernet, ""]

    migrated = []
    skipped = []
    for secret in secrets:
        if not secret or is_fernet_token(secret):
            skipped.append(secret)
        else:
            with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
                with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
                    mock_settings.webhook_secret_key = test_key
                    migrated.append(encrypt_secret(secret))

    # Plaintext row was migrated; already-encrypted and empty rows were skipped.
    assert len(migrated) == 1
    assert is_fernet_token(migrated[0])
    assert len(skipped) == 2  # already_fernet + empty


def test_encrypt_decrypt_no_key_passthrough() -> None:
    """When STORI_WEBHOOK_SECRET_KEY is absent, encrypt/decrypt are transparent."""
    from unittest.mock import patch
    from maestro.services import musehub_webhook_crypto as crypto

    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = None
            plaintext = "my-secret"
            assert crypto.encrypt_secret(plaintext) == plaintext
            assert crypto.decrypt_secret(plaintext) == plaintext


@pytest.mark.anyio
async def test_webhook_delivery_with_encrypted_secret_produces_correct_hmac(
    db_session: AsyncSession,
) -> None:
    """dispatch_event decrypts the stored secret before computing the HMAC signature."""
    from unittest.mock import patch
    from cryptography.fernet import Fernet
    from maestro.services import musehub_webhook_dispatcher as disp
    from maestro.services import musehub_webhook_crypto as crypto

    test_key = Fernet.generate_key().decode()

    # Reset the module-level singleton so our test key is used.
    with patch.object(crypto, "_fernet", None), patch.object(crypto, "_fernet_initialised", False):
        with patch("maestro.services.musehub_webhook_crypto.settings") as mock_settings:
            mock_settings.webhook_secret_key = test_key

            plaintext_secret = "delivery-hmac-secret"
            await disp.create_webhook(
                db_session,
                repo_id="repo-encrypted",
                url="https://encrypted.example.com/hook",
                events=["push"],
                secret=plaintext_secret,
            )
            await db_session.flush()

            received_headers: dict[str, str] = {}

            async def _capture_headers(url: str, **kwargs: Any) -> MagicMock:
                received_headers.update(kwargs.get("headers", {}))
                mock_resp = MagicMock()
                mock_resp.is_success = True
                mock_resp.status_code = 200
                mock_resp.text = "ok"
                return mock_resp

            push_payload: PushEventPayload = {
                "repoId": "repo-encrypted",
                "branch": "main",
                "headCommitId": "enc123",
                "pushedBy": "test-user",
                "commitCount": 1,
            }

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = _capture_headers
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                payload_bytes = json.dumps(push_payload, default=str).encode()
                await disp.dispatch_event(
                    db_session,
                    repo_id="repo-encrypted",
                    event_type="push",
                    payload=push_payload,
                )

    # The signature header must match what the subscriber computes from the plaintext secret.
    expected_mac = hmac.new(plaintext_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    expected_sig = f"sha256={expected_mac}"
    assert received_headers.get("X-MuseHub-Signature") == expected_sig
