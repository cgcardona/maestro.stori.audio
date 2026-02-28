"""oEmbed discovery endpoint for MuseHub embeddable player widgets.

oEmbed is a standard protocol (https://oembed.com/) that lets CMSes and
blogging platforms auto-embed rich content when a user pastes a URL.  By
exposing ``GET /oembed?url={embed_url}`` we enable Wordpress, Ghost, and
any oEmbed-aware platform to automatically convert a MuseHub embed URL
into an ``<iframe>`` snippet.

Endpoint summary:
  GET /oembed?url={url}&maxwidth={w}&maxheight={h}

The ``url`` parameter must match the embed URL pattern:
  /musehub/ui/{repo_id}/embed/{ref}

Returns JSON conforming to the oEmbed rich/video response type
(https://oembed.com/#section2.3.4):

  {
    "version":       "1.0",
    "type":          "rich",
    "title":         "MuseHub Composition {ref[:8]}",
    "provider_name": "Muse Hub",
    "provider_url":  "https://musehub.stori.app",
    "width":         560,
    "height":        152,
    "html":          "<iframe ...></iframe>"
  }

A 404 is returned for URLs that do not match the expected embed pattern
so that oEmbed consumers can distinguish supported from unsupported URLs.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musehub-oembed"])

_EMBED_URL_PATTERN = re.compile(
    r"/musehub/ui/(?P<repo_id>[^/]+)/embed/(?P<ref>[^/?#]+)"
)

_DEFAULT_WIDTH = 560
_DEFAULT_HEIGHT = 152
_MAX_WIDTH = 1200
_MAX_HEIGHT = 400


@router.get("/oembed", summary="oEmbed discovery for MuseHub embed URLs")
async def oembed_endpoint(
    url: str = Query(..., description="The MuseHub embed URL to resolve"),
    maxwidth: int = Query(_DEFAULT_WIDTH, ge=100, le=_MAX_WIDTH, description="Maximum iframe width in pixels"),
    maxheight: int = Query(_DEFAULT_HEIGHT, ge=80, le=_MAX_HEIGHT, description="Maximum iframe height in pixels"),
    format: str = Query("json", description="Response format — only 'json' is supported"),
) -> JSONResponse:
    """Return an oEmbed JSON response for a MuseHub embed URL.

    Why this exists: oEmbed-aware platforms (Wordpress, Ghost, Notion, etc.)
    call this endpoint automatically when a user pastes a MuseHub embed URL,
    then inject the returned ``html`` field as an ``<iframe>`` into the page.

    Contract:
    - ``url`` must contain a path matching ``/musehub/ui/{repo_id}/embed/{ref}``.
    - Returns 404 if the URL does not match the embed pattern.
    - Returns 501 if ``format`` is not ``json``.
    - Width and height are clamped to [100, 1200] and [80, 400] respectively.
    - The ``html`` field is an ``<iframe>`` pointing to the embed route which
      sets ``X-Frame-Options: ALLOWALL`` — safe for cross-origin embedding.

    Args:
        url:       Full or path-only MuseHub embed URL to resolve.
        maxwidth:  Desired maximum iframe width (default 560px).
        maxheight: Desired maximum iframe height (default 152px).
        format:    oEmbed response format — only ``json`` is supported.

    Returns:
        JSONResponse with oEmbed rich type payload.

    Raises:
        HTTPException 404: URL does not match a MuseHub embed URL pattern.
        HTTPException 501: Requested format is not ``json``.
    """
    if format.lower() != "json":
        raise HTTPException(status_code=501, detail="Only JSON format is supported")

    match = _EMBED_URL_PATTERN.search(url)
    if not match:
        logger.warning("oEmbed: unrecognised URL pattern — %s", url)
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
    iframe_html = (
        f'<iframe src="{embed_path}" '
        f'width="{width}" height="{height}" '
        'frameborder="0" allowtransparency="true" '
        'allow="autoplay" scrolling="no" '
        f'title="MuseHub Composition {short_ref}">'
        "</iframe>"
    )

    payload: dict[str, str | int] = {
        "version": "1.0",
        "type": "rich",
        "title": f"MuseHub Composition {short_ref}",
        "provider_name": "Muse Hub",
        "provider_url": "https://musehub.stori.app",
        "width": width,
        "height": height,
        "html": iframe_html,
    }
    logger.info("✅ oEmbed resolved — repo=%s ref=%s", repo_id, short_ref)
    return JSONResponse(content=payload)
