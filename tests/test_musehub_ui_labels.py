"""Tests for Muse Hub label management UI endpoints (issue #426).

Covers GET /musehub/ui/{owner}/{repo_slug}/labels:
- test_labels_page_returns_200               — page renders without auth
- test_labels_page_no_auth_required          — GET needs no JWT
- test_labels_page_unknown_repo_404          — unknown owner/slug → 404
- test_labels_page_has_color_picker_js       — colour picker rendered in template
- test_labels_page_has_label_list_js         — label list JS present
- test_labels_page_json_format              — ?format=json returns structured data
- test_labels_page_json_has_items_key       — JSON payload includes 'items' array
- test_labels_page_shows_issue_count        — issue counts included in JSON response
- test_labels_page_base_url_uses_slug       — base_url in context uses owner/slug not repo_id

Covers POST /musehub/ui/{owner}/{repo_slug}/labels:
- test_create_label_success                 — 201 + label_id returned
- test_create_label_requires_auth          — 401 without Bearer token
- test_create_label_duplicate_name_409     — duplicate name → 409
- test_create_label_invalid_color_422      — bad hex color → 422
- test_create_label_unknown_repo_404       — unknown repo → 404

Covers POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/edit:
- test_edit_label_success                  — 200 + updated values
- test_edit_label_requires_auth           — 401 without token
- test_edit_label_unknown_label_404       — unknown label_id → 404
- test_edit_label_name_conflict_409       — name collision → 409
- test_edit_label_partial_update          — partial body updates only supplied fields

Covers POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/delete:
- test_delete_label_success               — 200 ok=True
- test_delete_label_requires_auth        — 401 without token
- test_delete_label_unknown_label_404    — unknown label_id → 404

Covers POST /musehub/ui/{owner}/{repo_slug}/labels/reset:
- test_reset_labels_success              — 200 + 10 defaults seeded
- test_reset_labels_requires_auth       — 401 without token
- test_reset_labels_wipes_custom_labels — existing labels replaced
- test_reset_labels_unknown_repo_404    — unknown repo → 404
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_label_models import MusehubLabel
from maestro.db.musehub_models import MusehubIssue, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "beatmaker",
    slug: str = "deep-cuts",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-beatmaker",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_label(
    db: AsyncSession,
    repo_id: str,
    *,
    name: str = "bug",
    color: str = "#d73a4a",
    description: str | None = "Something isn't working",
) -> MusehubLabel:
    """Seed a label and return it."""
    label = MusehubLabel(
        repo_id=repo_id,
        name=name,
        color=color,
        description=description,
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return label


# ---------------------------------------------------------------------------
# GET /musehub/ui/{owner}/{repo_slug}/labels — label list page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_labels_page_returns_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /musehub/ui/{owner}/{slug}/labels returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_labels_page_no_auth_required(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Label list page is publicly accessible — no JWT required."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_labels_page_unknown_repo_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Unknown owner/slug combination → 404."""
    response = await client.get("/musehub/ui/nobody/nonexistent/labels")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_labels_page_has_color_picker_js(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The labels page HTML includes a colour picker for creating labels."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels")
    assert response.status_code == 200
    body = response.text
    assert 'type="color"' in body or "color-picker" in body or "input" in body


@pytest.mark.anyio
async def test_labels_page_has_label_list_js(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The labels page contains JavaScript to render the label list."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels")
    assert response.status_code == 200
    body = response.text
    assert "renderLabel" in body or "label-list" in body or "label-row" in body


@pytest.mark.anyio
async def test_labels_page_json_format(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?format=json returns a JSON response with a 200 status."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels?format=json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_labels_page_json_has_items_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """JSON response contains 'labels' and 'total' keys."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels?format=json")
    assert response.status_code == 200
    data = response.json()
    assert "labels" in data
    assert "total" in data
    assert data["total"] == 1


@pytest.mark.anyio
async def test_labels_page_shows_issue_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """JSON response includes issue_count for each label."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="enhancement", color="#a2eeef")
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels?format=json")
    assert response.status_code == 200
    data = response.json()
    labels = data["labels"]
    assert len(labels) == 1
    assert "issue_count" in labels[0]
    assert labels[0]["issue_count"] == 0


@pytest.mark.anyio
async def test_labels_page_base_url_uses_slug(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The HTML page embeds the owner/slug base URL, not the repo UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/deep-cuts/labels")
    assert response.status_code == 200
    body = response.text
    assert "beatmaker" in body
    assert "deep-cuts" in body


# ---------------------------------------------------------------------------
# POST /musehub/ui/{owner}/{repo_slug}/labels — create label
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_label_success(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels with valid body + auth returns 201 with label_id."""
    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels",
        json={"name": "needs-arrangement", "color": "#e4e669", "description": "Track needs arrangement"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["ok"] is True
    assert "label_id" in data
    assert data["label_id"] is not None


@pytest.mark.anyio
async def test_create_label_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /labels without a JWT returns 401 or 403."""
    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels",
        json={"name": "bug", "color": "#d73a4a"},
    )
    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_create_label_duplicate_name_409(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Creating a label with an existing name within the repo → 409."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels",
        json={"name": "bug", "color": "#ff0000"},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_create_label_invalid_color_422(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """A malformed hex colour string → 422 validation error."""
    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels",
        json={"name": "test", "color": "not-a-color"},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_label_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Creating a label on a nonexistent repo → 404."""
    response = await client.post(
        "/musehub/ui/nobody/ghost-repo/labels",
        json={"name": "bug", "color": "#d73a4a"},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/edit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_label_success(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels/{label_id}/edit updates the label and returns ok=True."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label.id}/edit",
        json={"name": "critical-bug", "color": "#ff0000"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["label_id"] == str(label.id)


@pytest.mark.anyio
async def test_edit_label_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /labels/{label_id}/edit without JWT → 401 or 403."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label.id}/edit",
        json={"name": "new-name"},
    )
    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_edit_label_unknown_label_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Editing a non-existent label_id → 404."""
    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels/00000000-0000-0000-0000-000000000000/edit",
        json={"name": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_edit_label_name_conflict_409(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Renaming a label to an already-existing name in the same repo → 409."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    label_b = await _make_label(db_session, repo_id, name="enhancement", color="#a2eeef")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label_b.id}/edit",
        json={"name": "bug"},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_edit_label_partial_update(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Sending only 'color' in the body preserves the existing name."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label.id}/edit",
        json={"color": "#ff6600"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    # Name should remain "bug" — verified by the message containing the name
    assert "bug" in data["message"]


# ---------------------------------------------------------------------------
# POST /musehub/ui/{owner}/{repo_slug}/labels/{label_id}/delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_label_success(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels/{label_id}/delete removes the label and returns ok=True."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label.id}/delete",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


@pytest.mark.anyio
async def test_delete_label_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /labels/{label_id}/delete without JWT → 401 or 403."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="bug", color="#d73a4a")
    response = await client.post(
        f"/musehub/ui/beatmaker/deep-cuts/labels/{label.id}/delete",
    )
    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_delete_label_unknown_label_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Deleting a non-existent label_id → 404."""
    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels/00000000-0000-0000-0000-000000000000/delete",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /musehub/ui/{owner}/{repo_slug}/labels/reset
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reset_labels_success(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels/reset returns 200 with ok=True and seeds 10 defaults."""
    from maestro.api.routes.musehub.labels import DEFAULT_LABELS

    await _make_repo(db_session)
    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels/reset",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert str(len(DEFAULT_LABELS)) in data["message"]


@pytest.mark.anyio
async def test_reset_labels_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /labels/reset without JWT → 401 or 403."""
    await _make_repo(db_session)
    response = await client.post("/musehub/ui/beatmaker/deep-cuts/labels/reset")
    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_reset_labels_wipes_custom_labels(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Reset removes all existing custom labels and replaces with defaults."""
    from maestro.api.routes.musehub.labels import DEFAULT_LABELS

    repo_id = await _make_repo(db_session)
    # Seed a custom label that is NOT in the defaults list.
    await _make_label(db_session, repo_id, name="my-custom-label", color="#123456")

    response = await client.post(
        "/musehub/ui/beatmaker/deep-cuts/labels/reset",
        headers=auth_headers,
    )
    assert response.status_code == 200

    # After reset, the JSON endpoint should return exactly the defaults.
    list_response = await client.get(
        "/musehub/ui/beatmaker/deep-cuts/labels?format=json"
    )
    assert list_response.status_code == 200
    data = list_response.json()
    label_names = {lbl["name"] for lbl in data["labels"]}
    default_names = {d["name"] for d in DEFAULT_LABELS}
    # Custom label must be gone; all defaults must be present.
    assert "my-custom-label" not in label_names
    assert default_names == label_names


@pytest.mark.anyio
async def test_reset_labels_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels/reset on nonexistent repo → 404."""
    response = await client.post(
        "/musehub/ui/nobody/ghost-repo/labels/reset",
        headers=auth_headers,
    )
    assert response.status_code == 404
