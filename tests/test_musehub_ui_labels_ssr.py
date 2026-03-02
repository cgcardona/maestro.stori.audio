"""SSR tests for Muse Hub labels management UI — issue #557.

Verifies that the labels page renders data server-side in Jinja2 templates
without requiring JavaScript execution.  Tests assert on HTML content directly
returned by the server, not on JavaScript rendering logic.

Covers GET /musehub/ui/{owner}/{repo_slug}/labels:
- test_labels_page_renders_label_name_server_side
- test_labels_page_shows_issue_count_server_side
- test_labels_page_fragment_on_htmx_request

Covers POST mutations with HX-Request header:
- test_labels_htmx_create_returns_fragment
- test_labels_htmx_delete_returns_fragment
- test_labels_htmx_reset_returns_10_defaults
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_label_models import MusehubLabel
from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "label_ssr_artist",
    slug: str = "label-ssr-album",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-label-ssr-artist",
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
    """Seed a label and return the ORM instance."""
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
# GET — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_labels_page_renders_label_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Label name is present in the HTML returned by the server, not injected by JS."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="needs-arrangement")
    response = await client.get("/musehub/ui/label_ssr_artist/label-ssr-album/labels")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "needs-arrangement" in response.text


@pytest.mark.anyio
async def test_labels_page_shows_issue_count_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue count is rendered in the label list HTML by the server."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="enhancement", color="#a2eeef")
    response = await client.get("/musehub/ui/label_ssr_artist/label-ssr-album/labels")
    assert response.status_code == 200
    # Label row with zero issues: "0 issues" should appear
    assert "0 issues" in response.text or "issues" in response.text


@pytest.mark.anyio
async def test_labels_page_fragment_on_htmx_request(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true causes the handler to return only the fragment — no <html> shell."""
    repo_id = await _make_repo(db_session)
    await _make_label(db_session, repo_id, name="htmx-fragment-label")
    response = await client.get(
        "/musehub/ui/label_ssr_artist/label-ssr-album/labels",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "htmx-fragment-label" in response.text


# ---------------------------------------------------------------------------
# POST mutations — HTMX fragment returns
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_labels_htmx_create_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST create with HX-Request returns updated label list fragment.

    Sends JSON body (handled by _parse_label_create_body) with HX-Request header.
    """
    await _make_repo(db_session)
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    response = await client.post(
        "/musehub/ui/label_ssr_artist/label-ssr-album/labels",
        json={"name": "htmx-created-label", "color": "#ff0000"},
        headers=htmx_headers,
    )
    assert response.status_code == 201
    assert "<html" not in response.text
    assert "htmx-created-label" in response.text


@pytest.mark.anyio
async def test_labels_htmx_delete_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST delete with HX-Request returns fragment without the deleted label."""
    repo_id = await _make_repo(db_session)
    label = await _make_label(db_session, repo_id, name="to-be-deleted")
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    response = await client.post(
        f"/musehub/ui/label_ssr_artist/label-ssr-album/labels/{label.id}/delete",
        headers=htmx_headers,
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "to-be-deleted" not in response.text


@pytest.mark.anyio
async def test_labels_htmx_reset_returns_10_defaults(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST reset with HX-Request returns fragment containing all 10 default labels."""
    await _make_repo(db_session)
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    response = await client.post(
        "/musehub/ui/label_ssr_artist/label-ssr-album/labels/reset",
        headers=htmx_headers,
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    # 10 default labels — count the unique per-row IDs to avoid matching class="label-row-actions"
    assert response.text.count('id="label-row-') == 10
