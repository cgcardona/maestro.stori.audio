"""oEmbed discovery endpoint for MuseHub embeddable player widgets.

oEmbed is a standard protocol (https://oembed.com/) that lets CMSes and
blogging platforms auto-embed rich content when a user pastes a URL.  By
exposing ``GET /oembed?url={embed_url}`` we enable Wordpress, Ghost, and
any oEmbed-aware platform to automatically convert a MuseHub embed URL
into an ``<iframe>`` snippet.

Endpoint summary:
  GET  /musehub/oembed?url={url}&maxwidth={w}&maxheight={h}
  GET  /musehub/oembed/commit?url={url}&maxwidth={w}&maxheight={h}

Both endpoints return JSON conforming to the oEmbed rich response type
(https://oembed.com/#section2.3.4) extended with ``musehub:*`` fields that
carry musical metadata extracted from the referenced commit.

Standard response fields (https://oembed.com/#section2.3):
  version, type, title, author_name, author_url,
  provider_name, provider_url,
  thumbnail_url, thumbnail_width, thumbnail_height,
  html, width, height

MuseHub extension fields (prefixed ``musehub:`` per oEmbed convention):
  musehub:key            — tonal centre, e.g. "G major"
  musehub:tempo_bpm      — integer BPM
  musehub:time_signature — e.g. "4/4"
  musehub:duration_beats — total beat count as integer
  musehub:instruments    — list of instrument role names
  musehub:license        — SPDX or free-text licence
  musehub:genre          — list of genre tags
  musehub:commit_id      — short commit SHA (8 chars)
  musehub:audio_url      — render URL for audio preview

URL patterns accepted:
  /musehub/ui/{repo_id}/embed/{ref}         → /oembed
  /musehub/ui/{repo_id}/commit/{commit_sha} → /oembed/commit

Returns 404 for non-matching URLs so oEmbed consumers distinguish
supported from unsupported URLs gracefully.
"""
from __future__ import annotations

import logging
import re
from typing import TypedDict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musehub-oembed"])

_EMBED_URL_PATTERN = re.compile(
    r"/musehub/ui/(?P<repo_id>[^/]+)/embed/(?P<ref>[^/?#]+)"
)
_COMMIT_URL_PATTERN = re.compile(
    r"/musehub/ui/(?P<repo_id>[^/]+)/commit/(?P<sha>[^/?#]+)"
)

_DEFAULT_WIDTH = 560
_DEFAULT_HEIGHT = 152
_MAX_WIDTH = 1200
_MAX_HEIGHT = 400

_PROVIDER_NAME = "MuseHub"
_PROVIDER_URL = "https://musehub.stori.app"

# Functional TypedDict form required because field names contain colons (e.g. "musehub:key"),
# which are not valid Python identifiers for the class-based TypedDict syntax.
OEmbedPayload = TypedDict(
    "OEmbedPayload",
    {
        "version": str,
        "type": str,
        "title": str,
        "author_name": str | None,
        "author_url": str,
        "provider_name": str,
        "provider_url": str,
        "thumbnail_url": str,
        "thumbnail_width": int,
        "thumbnail_height": int,
        "html": str,
        "width": int,
        "height": int,
        "musehub:key": str | None,
        "musehub:tempo_bpm": int | None,
        "musehub:time_signature": str | None,
        "musehub:duration_beats": int | None,
        "musehub:instruments": list[str] | None,
        "musehub:license": str | None,
        "musehub:genre": list[str] | None,
        "musehub:commit_id": str,
        "musehub:audio_url": str,
    },
)


def _build_oembed_payload(
    *,
    repo_id: str,
    ref: str,
    short_ref: str,
    width: int,
    height: int,
    embed_path: str,
    title: str,
    owner: str = "",
) -> OEmbedPayload:
    """Construct the full oEmbed + MuseHub extension payload dict.

    All ``musehub:*`` extension fields default to ``None`` because the embed
    URL alone carries no musical metadata — the consumer is expected to treat
    missing fields as unknown/not-yet-rendered rather than as errors.  A future
    PR will wire these to the Muse VCS read path so live commit metadata populates
    them automatically.

    Args:
        repo_id:    Repository UUID string.
        ref:        Full commit ref / branch name / tag.
        short_ref:  First 8 characters of ``ref`` for display purposes.
        width:      Iframe pixel width (already clamped to allowed range).
        height:     Iframe pixel height (already clamped to allowed range).
        embed_path: URL path for the iframe ``src`` attribute.
        title:      Human-readable composition title.
        owner:      Repository owner username (empty string if unknown).

    Returns:
        dict ready to serialise as JSON for the oEmbed response body.
    """
    iframe_html = (
        f'<iframe src="{embed_path}" '
        f'width="{width}" height="{height}" '
        'frameborder="0" allowtransparency="true" '
        'allow="autoplay" scrolling="no" '
        f'title="{title}">'
        "</iframe>"
    )

    author_url = (
        f"{_PROVIDER_URL}/musehub/ui/users/{owner}" if owner else _PROVIDER_URL
    )
    thumbnail_url = f"{_PROVIDER_URL}/static/thumbnails/{repo_id}/{short_ref}.png"
    audio_url = f"{_PROVIDER_URL}/api/v1/musehub/repos/{repo_id}/render/{ref}"

    return {
        # ── Standard oEmbed fields ────────────────────────────────────────
        "version": "1.0",
        "type": "rich",
        "title": title,
        "author_name": owner or None,
        "author_url": author_url,
        "provider_name": _PROVIDER_NAME,
        "provider_url": _PROVIDER_URL,
        "thumbnail_url": thumbnail_url,
        "thumbnail_width": 480,
        "thumbnail_height": 270,
        "html": iframe_html,
        "width": width,
        "height": height,
        # ── MuseHub extension fields ──────────────────────────────────────
        # These are populated from commit metadata when available.
        # None values indicate the information is not yet resolved.
        "musehub:key": None,
        "musehub:tempo_bpm": None,
        "musehub:time_signature": None,
        "musehub:duration_beats": None,
        "musehub:instruments": None,
        "musehub:license": None,
        "musehub:genre": None,
        "musehub:commit_id": short_ref,
        "musehub:audio_url": audio_url,
    }


@router.get("/oembed", summary="oEmbed discovery for MuseHub embed URLs")
async def oembed_endpoint(
    url: str = Query(..., description="The MuseHub embed URL to resolve"),
    maxwidth: int = Query(
        _DEFAULT_WIDTH, ge=100, le=_MAX_WIDTH, description="Maximum iframe width in pixels"
    ),
    maxheight: int = Query(
        _DEFAULT_HEIGHT, ge=80, le=_MAX_HEIGHT, description="Maximum iframe height in pixels"
    ),
    format: str = Query("json", description="Response format — only 'json' is supported"),
) -> JSONResponse:
    """Return an oEmbed JSON response for a MuseHub embed URL.

    Why this exists: oEmbed-aware platforms (Wordpress, Ghost, Notion, etc.)
    call this endpoint automatically when a user pastes a MuseHub embed URL,
    then inject the returned ``html`` field as an ``<iframe>`` into the page.
    The ``musehub:*`` extension fields expose musical metadata that enriches
    the embed card wherever the consumer supports custom oEmbed fields.

    Contract:
    - ``url`` must contain a path matching ``/musehub/ui/{repo_id}/embed/{ref}``.
    - Returns 404 if the URL does not match the embed pattern.
    - Returns 501 if ``format`` is not ``json``.
    - Width and height are clamped to [100, 1200] and [80, 400] respectively.
    - ``musehub:*`` fields are present but may be ``null`` when metadata is unavailable.

    Args:
        url:       Full or path-only MuseHub embed URL to resolve.
        maxwidth:  Desired maximum iframe width (default 560px).
        maxheight: Desired maximum iframe height (default 152px).
        format:    oEmbed response format — only ``json`` is supported.

    Returns:
        JSONResponse with oEmbed rich type payload including ``musehub:*`` extensions.

    Raises:
        HTTPException 404: URL does not match a MuseHub embed URL pattern.
        HTTPException 501: Requested format is not ``json``.
    """
    if format.lower() != "json":
        raise HTTPException(status_code=501, detail="Only JSON format is supported")

    match = _EMBED_URL_PATTERN.search(url)
    if not match:
        logger.warning("⚠️ oEmbed: unrecognised embed URL pattern — %s", url)
        raise HTTPException(
            status_code=404,
            detail="URL does not match a MuseHub embed URL. "
            "Expected format: /musehub/ui/{repo_id}/embed/{ref}",
        )

    repo_id = match.group("repo_id")
    ref = match.group("ref")
    short_ref = ref[:8] if len(ref) >= 8 else ref

    width = min(maxwidth, _MAX_WIDTH)
    height = min(maxheight, _MAX_HEIGHT)

    embed_path = f"/musehub/ui/{repo_id}/embed/{ref}"
    title = f"MuseHub Composition {short_ref}"

    payload = _build_oembed_payload(
        repo_id=repo_id,
        ref=ref,
        short_ref=short_ref,
        width=width,
        height=height,
        embed_path=embed_path,
        title=title,
    )
    logger.info("✅ oEmbed resolved — repo=%s ref=%s", repo_id, short_ref)
    return JSONResponse(content=payload)


@router.get("/oembed/commit", summary="oEmbed discovery for individual MuseHub commits")
async def oembed_commit_endpoint(
    url: str = Query(..., description="The MuseHub commit URL to resolve"),
    maxwidth: int = Query(
        _DEFAULT_WIDTH, ge=100, le=_MAX_WIDTH, description="Maximum iframe width in pixels"
    ),
    maxheight: int = Query(
        _DEFAULT_HEIGHT, ge=80, le=_MAX_HEIGHT, description="Maximum iframe height in pixels"
    ),
    format: str = Query("json", description="Response format — only 'json' is supported"),
) -> JSONResponse:
    """Return an oEmbed JSON response for a MuseHub commit URL.

    Why this exists: individual commits are the atomic unit of composition in
    Muse VCS. This endpoint allows any oEmbed consumer to embed a specific
    commit snapshot — useful for embedding a pinned musical moment in blog posts,
    changelogs, or social previews without the composition evolving under the link.

    Contract:
    - ``url`` must contain a path matching ``/musehub/ui/{repo_id}/commit/{sha}``.
    - Returns 404 if the URL does not match the commit pattern.
    - Returns 501 if ``format`` is not ``json``.
    - The iframe ``src`` points to the standard embed route using the commit SHA as ref,
      so the embedded player always shows the exact snapshot.
    - ``musehub:*`` fields follow the same convention as ``/oembed``.

    Args:
        url:       Full or path-only MuseHub commit URL to resolve.
        maxwidth:  Desired maximum iframe width (default 560px).
        maxheight: Desired maximum iframe height (default 152px).
        format:    oEmbed response format — only ``json`` is supported.

    Returns:
        JSONResponse with oEmbed rich type payload anchored to the given commit SHA.

    Raises:
        HTTPException 404: URL does not match a MuseHub commit URL pattern.
        HTTPException 501: Requested format is not ``json``.
    """
    if format.lower() != "json":
        raise HTTPException(status_code=501, detail="Only JSON format is supported")

    match = _COMMIT_URL_PATTERN.search(url)
    if not match:
        logger.warning("⚠️ oEmbed/commit: unrecognised commit URL pattern — %s", url)
        raise HTTPException(
            status_code=404,
            detail="URL does not match a MuseHub commit URL. "
            "Expected format: /musehub/ui/{repo_id}/commit/{sha}",
        )

    repo_id = match.group("repo_id")
    sha = match.group("sha")
    short_sha = sha[:8] if len(sha) >= 8 else sha

    width = min(maxwidth, _MAX_WIDTH)
    height = min(maxheight, _MAX_HEIGHT)

    # The embed player accepts a commit SHA as the ref — it shows that exact snapshot.
    embed_path = f"/musehub/ui/{repo_id}/embed/{sha}"
    title = f"MuseHub Commit {short_sha}"

    payload = _build_oembed_payload(
        repo_id=repo_id,
        ref=sha,
        short_ref=short_sha,
        width=width,
        height=height,
        embed_path=embed_path,
        title=title,
    )
    logger.info("✅ oEmbed/commit resolved — repo=%s sha=%s", repo_id, short_sha)
    return JSONResponse(content=payload)
