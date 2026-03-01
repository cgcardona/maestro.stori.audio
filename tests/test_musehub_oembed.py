"""Tests for the MuseHub oEmbed discovery endpoint.

Covers acceptance criteria from issue #244 (original) and issue #440 (rich metadata):
- test_oembed_endpoint              — GET /oembed returns valid JSON with HTML embed code
- test_oembed_unknown_url_404       — Invalid / unrecognised URL returns 404
- test_oembed_iframe_content        — Returned HTML is an <iframe> pointing to embed route
- test_oembed_respects_maxwidth     — maxwidth parameter is reflected in returned iframe width
- test_oembed_xml_format_501        — Non-JSON format returns 501
- test_oembed_musehub_extension_fields — Response includes all musehub:* extension fields
- test_oembed_standard_fields_complete — Response has all required standard oEmbed fields
- test_oembed_commit_endpoint       — GET /oembed/commit returns 200 for commit URLs
- test_oembed_commit_unknown_url_404 — /oembed/commit returns 404 for non-commit URLs
- test_oembed_commit_iframe_uses_sha — /oembed/commit iframe src contains the commit SHA
- test_oembed_commit_xml_format_501 — /oembed/commit returns 501 for XML format

The /oembed and /oembed/commit endpoints require no auth — oEmbed consumers
(CMSes, blog platforms) call them without user credentials to discover embed metadata.
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
    assert data["provider_name"] == "MuseHub"
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


@pytest.mark.anyio
async def test_oembed_musehub_extension_fields(client: AsyncClient) -> None:
    """Response includes all musehub:* extension fields defined in issue #440."""
    repo_id = "ddddeeee-ffff-0000-1111-222233334444"
    ref = "beefcafe1234"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code == 200

    data = response.json()
    # All musehub:* extension fields must be present (may be null if not yet resolved)
    extension_fields = [
        "musehub:key",
        "musehub:tempo_bpm",
        "musehub:time_signature",
        "musehub:duration_beats",
        "musehub:instruments",
        "musehub:license",
        "musehub:genre",
        "musehub:commit_id",
        "musehub:audio_url",
    ]
    for field in extension_fields:
        assert field in data, f"Missing extension field: {field}"

    # commit_id is always populated from the ref
    assert data["musehub:commit_id"] == ref[:8]
    # audio_url must be a string URL pointing to the render endpoint
    assert isinstance(data["musehub:audio_url"], str)
    assert repo_id in data["musehub:audio_url"]


@pytest.mark.anyio
async def test_oembed_standard_fields_complete(client: AsyncClient) -> None:
    """Response contains the full set of standard oEmbed rich-type fields."""
    repo_id = "eeeeffff-0000-1111-2222-333344445555"
    ref = "d00dcafe"
    embed_url = f"/musehub/ui/{repo_id}/embed/{ref}"

    response = await client.get(f"/oembed?url={embed_url}")
    assert response.status_code == 200

    data = response.json()
    required_fields = [
        "version", "type", "title",
        "provider_name", "provider_url",
        "thumbnail_url", "thumbnail_width", "thumbnail_height",
        "html", "width", "height",
    ]
    for field in required_fields:
        assert field in data, f"Missing required oEmbed field: {field}"

    assert data["provider_name"] == "MuseHub"
    assert data["provider_url"] == "https://musehub.stori.app"
    assert isinstance(data["thumbnail_width"], int)
    assert isinstance(data["thumbnail_height"], int)
    assert repo_id in data["thumbnail_url"]


@pytest.mark.anyio
async def test_oembed_commit_endpoint(client: AsyncClient) -> None:
    """GET /oembed/commit returns 200 JSON for a valid commit URL."""
    repo_id = "aaaabbbb-cccc-dddd-eeee-111122223333"
    sha = "abc1234567890def"
    commit_url = f"/musehub/ui/{repo_id}/commit/{sha}"

    response = await client.get(f"/oembed/commit?url={commit_url}")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]

    data = response.json()
    assert data["version"] == "1.0"
    assert data["type"] == "rich"
    assert sha[:8] in data["title"]
    assert data["provider_name"] == "MuseHub"
    assert "html" in data
    assert data["musehub:commit_id"] == sha[:8]


@pytest.mark.anyio
async def test_oembed_commit_unknown_url_404(client: AsyncClient) -> None:
    """/oembed/commit returns 404 for a URL that doesn't match a commit pattern."""
    response = await client.get("/oembed/commit?url=https://example.com/not-a-commit")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_oembed_commit_iframe_uses_sha(client: AsyncClient) -> None:
    """The /oembed/commit endpoint's iframe src contains the commit SHA as the ref."""
    repo_id = "bbbbcccc-dddd-eeee-ffff-111122223333"
    sha = "deadbeefcafe0001"
    commit_url = f"/musehub/ui/{repo_id}/commit/{sha}"

    response = await client.get(f"/oembed/commit?url={commit_url}")
    assert response.status_code == 200

    html = response.json()["html"]
    # The embed player uses the full SHA as the ref so the snapshot is pinned
    assert sha in html
    assert f"/musehub/ui/{repo_id}/embed/{sha}" in html


@pytest.mark.anyio
async def test_oembed_commit_xml_format_501(client: AsyncClient) -> None:
    """/oembed/commit returns 501 for non-JSON format requests."""
    repo_id = "ccccdddd-eeee-ffff-0000-111122223333"
    sha = "cafebabe12345678"
    commit_url = f"/musehub/ui/{repo_id}/commit/{sha}"

    response = await client.get(f"/oembed/commit?url={commit_url}&format=xml")
    assert response.status_code == 501
