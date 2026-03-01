"""Muse Hub sitemap.xml and robots.txt generation.

Endpoint summary:
  GET /sitemap.xml  — XML sitemap of all public MuseHub content (repos, users, topics, releases)
  GET /robots.txt   — crawl policy for search engines and AI agents

These endpoints live at the top level (no /api/v1 prefix) so standard crawlers
and search engines find them at the expected paths.

Registration: registered in ``maestro.main`` directly:
  app.include_router(sitemap_routes.router, tags=["musehub-sitemap"])
NOT via the musehub __init__.py auto-discovery (these are not /musehub/ sub-paths).

Sitemap format follows the sitemaps.org protocol (XML namespace
http://www.sitemaps.org/schemas/sitemap/0.9).  Priority and changefreq are
assigned by content type:
  repos    → priority 0.8, daily (active) or monthly (inactive > 90 days)
  profiles → priority 0.7, weekly
  topics   → priority 0.6, weekly
  releases → priority 0.5, monthly
  static   → priority 0.9, monthly

The sitemap is capped at 50,000 URLs per the sitemaps.org spec.  When
content grows beyond that limit add a sitemap index endpoint (sitemapindex XML)
and paginate via ?page=N — the infrastructure is already designed for it.

Performance: all queries use lightweight column projections (no ORM lazy-loading).
The endpoint is unauthenticated and suitable for public crawlers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, outerjoin, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import get_db
from maestro.db import musehub_models as db

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum URLs per sitemap index (sitemaps.org spec).
_SITEMAP_URL_LIMIT = 50_000

# Static pages always present in the sitemap regardless of DB content.
_STATIC_PAGES: list[tuple[str, str, str]] = [
    # (path, changefreq, priority)
    ("/musehub/ui/explore", "daily", "0.9"),
    ("/musehub/ui/trending", "daily", "0.9"),
    ("/musehub/ui/topics", "weekly", "0.6"),
]

# Agents known to index content for discovery — granted explicit Allow in robots.txt.
_KNOWN_AGENT_BOTS = [
    "Googlebot",
    "GPTBot",
    "ClaudeBot",
    "CursorBot",
    "anthropic-ai",
    "CCBot",
    "Omgilibot",
    "PerplexityBot",
]


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 date string (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _to_date(dt: datetime | None) -> str:
    """Format a datetime as YYYY-MM-DD for the sitemap lastmod field.

    Falls back to today's date when the timestamp is unavailable.
    """
    if dt is None:
        return _utcnow_iso()
    return dt.strftime("%Y-%m-%d")


def _is_inactive(dt: datetime | None) -> bool:
    """Return True when the last-activity timestamp is older than 90 days.

    Used to assign 'monthly' changefreq to dormant repos instead of 'daily',
    reducing unnecessary crawl budget consumption.
    """
    if dt is None:
        return True
    age = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
    return age.days > 90


def _build_sitemap_xml(entries: list[dict[str, str]]) -> bytes:
    """Serialise a list of URL entries into a UTF-8 encoded sitemap XML document.

    Each entry dict may contain: loc (required), lastmod, changefreq, priority.
    Returns raw bytes suitable for a ``Response(content=..., media_type="application/xml")``.
    """
    urlset = Element(
        "urlset",
        xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
    )
    for entry in entries:
        url_el = SubElement(urlset, "url")
        SubElement(url_el, "loc").text = entry["loc"]
        if "lastmod" in entry:
            SubElement(url_el, "lastmod").text = entry["lastmod"]
        if "changefreq" in entry:
            SubElement(url_el, "changefreq").text = entry["changefreq"]
        if "priority" in entry:
            SubElement(url_el, "priority").text = entry["priority"]

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        urlset, encoding="unicode"
    ).encode("utf-8")
    return xml_bytes


async def _fetch_sitemap_entries(
    session: AsyncSession,
    base_url: str,
) -> list[dict[str, str]]:
    """Query all public content and return URL entries for the sitemap.

    Queries (in order):
      1. Public repos — joined with commits to get latest activity timestamp.
      2. Public user profiles — username used in the profile URL.
      3. Distinct topic tags — aggregated from public repo tags.
      4. Releases — per public repo.

    Args:
        session:  Async SQLAlchemy session.
        base_url: Scheme + host of the server (e.g. ``https://musehub.stori.app``),
                  used as the URL prefix for all sitemap loc entries.

    Returns:
        List of URL entry dicts, ordered static → repos → profiles → topics → releases.
    """
    entries: list[dict[str, str]] = []

    # ── 1. Static pages ───────────────────────────────────────────────────────
    today = _utcnow_iso()
    for path, changefreq, priority in _STATIC_PAGES:
        entries.append(
            {
                "loc": f"{base_url}{path}",
                "lastmod": today,
                "changefreq": changefreq,
                "priority": priority,
            }
        )

    # ── 2. Public repos (with latest commit timestamp) ────────────────────────
    latest_commit_col = func.max(db.MusehubCommit.timestamp).label("latest_commit")
    repo_q = (
        select(
            db.MusehubRepo.owner,
            db.MusehubRepo.slug,
            db.MusehubRepo.created_at,
            latest_commit_col,
        )
        .select_from(
            outerjoin(
                db.MusehubRepo,
                db.MusehubCommit,
                db.MusehubRepo.repo_id == db.MusehubCommit.repo_id,
            )
        )
        .where(db.MusehubRepo.visibility == "public")
        .group_by(
            db.MusehubRepo.repo_id,
            db.MusehubRepo.owner,
            db.MusehubRepo.slug,
            db.MusehubRepo.created_at,
        )
        .limit(_SITEMAP_URL_LIMIT)
    )
    repo_rows = await session.execute(repo_q)
    repo_entries: list[tuple[str, str, datetime | None]] = []
    for owner, slug, created_at, latest_commit in repo_rows:
        activity_ts: datetime | None = latest_commit or created_at
        repo_entries.append((owner, slug, activity_ts))
        changefreq = "monthly" if _is_inactive(activity_ts) else "daily"
        lastmod = _to_date(activity_ts)
        entries.append(
            {
                "loc": f"{base_url}/musehub/ui/{owner}/{slug}",
                "lastmod": lastmod,
                "changefreq": changefreq,
                "priority": "0.8",
            }
        )
        entries.append(
            {
                "loc": f"{base_url}/musehub/ui/{owner}/{slug}/commits",
                "lastmod": lastmod,
                "changefreq": changefreq,
                "priority": "0.7",
            }
        )
        entries.append(
            {
                "loc": f"{base_url}/musehub/ui/{owner}/{slug}/issues",
                "lastmod": lastmod,
                "changefreq": "weekly",
                "priority": "0.5",
            }
        )

    # ── 3. Public user profiles ───────────────────────────────────────────────
    profile_q = select(db.MusehubProfile.username, db.MusehubProfile.updated_at).limit(
        _SITEMAP_URL_LIMIT
    )
    profile_rows = await session.execute(profile_q)
    profile_count = 0
    for username, updated_at in profile_rows:
        profile_count += 1
        entries.append(
            {
                "loc": f"{base_url}/musehub/ui/users/{username}",
                "lastmod": _to_date(updated_at),
                "changefreq": "weekly",
                "priority": "0.7",
            }
        )

    # ── 4. Topic pages from public repo tags ──────────────────────────────────
    tag_q = select(db.MusehubRepo.tags).where(db.MusehubRepo.visibility == "public")
    tag_rows = await session.execute(tag_q)
    seen_topics: set[str] = set()
    for (tags,) in tag_rows:
        for tag in tags or []:
            t = str(tag).lower().strip()
            if t and t not in seen_topics:
                seen_topics.add(t)
                entries.append(
                    {
                        "loc": f"{base_url}/musehub/ui/topics/{t}",
                        "changefreq": "weekly",
                        "priority": "0.6",
                    }
                )

    # ── 5. Release pages ──────────────────────────────────────────────────────
    release_q = (
        select(
            db.MusehubRepo.owner,
            db.MusehubRepo.slug,
            db.MusehubRelease.tag,
            db.MusehubRelease.created_at,
        )
        .join(db.MusehubRelease, db.MusehubRepo.repo_id == db.MusehubRelease.repo_id)
        .where(db.MusehubRepo.visibility == "public")
        .limit(_SITEMAP_URL_LIMIT)
    )
    release_rows = await session.execute(release_q)
    for owner, slug, tag, created_at in release_rows:
        entries.append(
            {
                "loc": f"{base_url}/musehub/ui/{owner}/{slug}/releases/{tag}",
                "lastmod": _to_date(created_at),
                "changefreq": "monthly",
                "priority": "0.5",
            }
        )

    logger.info(
        "✅ Sitemap built — %d URLs (%d repos, %d profiles, %d topics)",
        len(entries),
        len(repo_entries),
        profile_count,
        len(seen_topics),
    )
    return entries[: _SITEMAP_URL_LIMIT]


@router.get(
    "/sitemap.xml",
    response_class=Response,
    operation_id="getSitemap",
    summary="XML sitemap of all public MuseHub content",
    tags=["musehub-sitemap"],
)
async def get_sitemap(
    request: Request,
    db_session: AsyncSession = Depends(get_db),
) -> Response:
    """Return an XML sitemap of all public MuseHub content.

    Why this exists: search engines and AI discovery agents use sitemap.xml to
    find and index all indexable URLs without crawling every page.  Including
    repos, profiles, topics, and releases here ensures new content is indexed
    quickly after it is published.

    The ``base_url`` is derived from the incoming request so the sitemap works
    correctly in dev, staging, and production without hardcoded hostnames.

    Changefreq heuristic:
    - Active repos (commit in the last 90 days) → daily.
    - Inactive repos → monthly.
    - Profiles, topics → weekly.
    - Releases → monthly (published artefacts rarely change).

    Capped at 50,000 URLs per the sitemaps.org spec.
    """
    base = str(request.base_url).rstrip("/")
    entries = await _fetch_sitemap_entries(db_session, base)
    xml_bytes = _build_sitemap_xml(entries)
    return Response(content=xml_bytes, media_type="application/xml")


@router.get(
    "/robots.txt",
    response_class=PlainTextResponse,
    operation_id="getRobotsTxt",
    summary="Crawl policy for search engines and AI agents",
    tags=["musehub-sitemap"],
)
async def get_robots_txt(request: Request) -> PlainTextResponse:
    """Return robots.txt crawl directives for the MuseHub site.

    Why this exists: without robots.txt, well-behaved crawlers assume nothing
    is allowed.  We explicitly open public MuseHub content to all crawlers
    (including AI agents) while protecting user-private pages (settings,
    notifications) from indexing.

    Special-case ``Allow`` directives for named AI agents acknowledge that
    MuseHub is designed for agent-native discovery alongside human browsers.

    The Sitemap declaration at the bottom lets crawlers find all indexable
    content without brute-force path guessing.
    """
    base = str(request.base_url).rstrip("/")
    sitemap_url = f"{base}/sitemap.xml"

    agent_allows = "\n".join(
        f"User-agent: {bot}\nAllow: /musehub/\n" for bot in _KNOWN_AGENT_BOTS
    )

    body = f"""\
User-agent: *
Allow: /musehub/ui/
Disallow: /musehub/ui/*/settings
Disallow: /musehub/ui/notifications
Disallow: /api/

{agent_allows}
Sitemap: {sitemap_url}
"""
    return PlainTextResponse(content=body)
