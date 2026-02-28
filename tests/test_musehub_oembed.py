"""Tests for the MuseHub oEmbed discovery endpoint.

Covers acceptance criteria from issue #244:
- test_oembed_endpoint          — GET /oembed returns valid JSON with HTML embed code
- test_oembed_unknown_url_404   — Invalid / unrecognised URL returns 404
- test_oembed_iframe_content    — Returned HTML is an <iframe> pointing to embed route
- test_oembed_respects_maxwidth — maxwidth parameter is reflected in returned iframe width
- test_oembed_xml_format_501    — Non-JSON format returns 501

The /oembed endpoint requires no auth — oEmbed consumers (CMSes, blog platforms)
call it without user credentials to discover embed metadata.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_oembed_endpoint(client: AsyncClient) -> None:
    """GET /oembed with a valid embed URL returns 200 JSON with oEmbed fields."""
    repo_id = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
    ref = "abc1234567890"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]

    data = response.json()
    assert data["version"] == "1.0"
    assert data["type"] == "rich"
    assert "title" in data
    assert data["provider_name"] == "Muse Hub"
    assert "html" in data
    assert isinstance(data["width"], int)
    assert isinstance(data["height"], int)


@pytest.mark.anyio
async def test_oembed_unknown_url_404(client: AsyncClient) -> None:
    """GET /oembed with a URL that doesn't match an embed pattern returns 404."""
    response = await client.get("/oembed?url=https://example.com/not-musehub")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_oembed_iframe_content(client: AsyncClient) -> None:
    """The HTML field returned by /oembed is an <iframe> pointing to the embed route."""
    repo_id = "11112222-3333-4444-5555-666677778888"
    ref = "deadbeef1234"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code == 200

    html = response.json()["html"]
    assert "<iframe" in html
    assert f"/musehub/ui/{repo_id}/embed/{ref}" in html
    assert "</iframe>" in html


@pytest.mark.anyio
async def test_oembed_respects_maxwidth(client: AsyncClient) -> None:
    """maxwidth query parameter is reflected as the iframe width attribute."""
    repo_id = "aaaabbbb-1111-2222-3333-ccccddddeeee"
    ref = "cafebabe"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}&maxwidth=400")
    assert response.status_code == 200

    data = response.json()
    assert data["width"] == 400
    assert 'width="400"' in data["html"]


@pytest.mark.anyio
async def test_oembed_xml_format_501(client: AsyncClient) -> None:
    """Requesting XML format returns 501 Not Implemented."""
    repo_id = "aaaabbbb-cccc-dddd-eeee-000011112222"
    ref = "feedface"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}&format=xml")
    assert response.status_code == 501


@pytest.mark.anyio
async def test_oembed_no_auth_required(client: AsyncClient) -> None:
    """oEmbed endpoint must not require a JWT — CMS platforms call it unauthenticated."""
    repo_id = "bbbbcccc-dddd-eeee-ffff-000011112222"
    ref = "aabbccdd"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_oembed_title_contains_short_ref(client: AsyncClient) -> None:
    """oEmbed title includes the first 8 characters of the ref for human readability."""
    repo_id = "ccccdddd-eeee-ffff-0000-111122223333"
    ref = "1234567890abcdef"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code == 200

    data = response.json()
    assert ref[:8] in data["title"]
