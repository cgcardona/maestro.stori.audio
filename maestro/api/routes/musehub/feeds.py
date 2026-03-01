"""Muse Hub RSS/Atom feed route handlers.

Endpoint summary:
  GET /musehub/repos/{repo_id}/feed.rss      — RSS 2.0 feed of recent commits
  GET /musehub/repos/{repo_id}/releases.rss  — RSS 2.0 feed of releases
  GET /musehub/repos/{repo_id}/issues.rss    — RSS 2.0 feed of open issues
  GET /musehub/repos/{repo_id}/feed.atom     — Atom 1.0 feed of recent commits

All feed endpoints are restricted to **public** repos only. Private repos return
403 Forbidden. Feed consumers (aggregators, agent subscribers) poll these URLs
without credentials — adding auth would break standard feed readers.

No business logic lives here. Persistence is delegated to:
  - maestro.services.musehub_repository  (commits)
  - maestro.services.musehub_releases    (releases)
  - maestro.services.musehub_issues      (issues)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import optional_token, TokenClaims
from maestro.db import get_db
from maestro.models.musehub import CommitResponse, IssueResponse, ReleaseResponse, RepoResponse
from maestro.services import musehub_issues, musehub_releases, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

_COMMIT_FEED_LIMIT = 50
_RELEASE_FEED_LIMIT = 50
_ISSUE_FEED_LIMIT = 50


# ── Access control ────────────────────────────────────────────────────────────


def _require_public(repo: RepoResponse | None) -> RepoResponse:
    """Raise 404 when repo is absent; 403 when it is private.

    Feed endpoints are public-only by design — standard feed readers cannot
    send Authorization headers, so private repos must return 403 rather than
    401 to avoid infinite auth redirect loops in readers.
    """
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Feeds are only available for public repos.",
        )
    return repo


# ── XML helpers ───────────────────────────────────────────────────────────────


def _rss_pub_date(dt: datetime) -> str:
    """Format a datetime as RFC 2822 for RSS <pubDate>.

    Naive datetimes are treated as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def _atom_date(dt: datetime) -> str:
    """Format a datetime as RFC 3339 for Atom <updated>/<published>.

    Naive datetimes are treated as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit_description(commit: CommitResponse) -> str:
    """Build a human-readable description for a commit feed item.

    Includes the full commit message; callers may surface tempo/key metadata
    from the repo if it is available at the call site.
    """
    return escape(commit.message)


def _build_rss_envelope(title: str, link: str, description: str, items: list[str]) -> str:
    """Wrap feed items in an RSS 2.0 <channel> envelope."""
    items_xml = "\n    ".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{escape(title)}</title>\n"
        f"    <link>{escape(link)}</link>\n"
        f"    <description>{escape(description)}</description>\n"
        f"    {items_xml}\n"
        "  </channel>\n"
        "</rss>"
    )


def _commit_rss_item(commit: CommitResponse, owner: str, slug: str) -> str:
    """Render a single commit as an RSS <item>."""
    title = escape(commit.message[:80])
    link = escape(f"/musehub/ui/{owner}/{slug}/commits/{commit.commit_id}")
    description = _commit_description(commit)
    pub_date = _rss_pub_date(commit.timestamp)
    guid = escape(commit.commit_id)
    return (
        "<item>\n"
        f"      <title>{title}</title>\n"
        f"      <link>{link}</link>\n"
        f"      <description>{description}</description>\n"
        f"      <pubDate>{pub_date}</pubDate>\n"
        f"      <guid isPermaLink=\"false\">{guid}</guid>\n"
        "    </item>"
    )


def _release_rss_item(release: ReleaseResponse, owner: str, slug: str) -> str:
    """Render a single release as an RSS <item>, with optional mp3 <enclosure>."""
    title = escape(f"Release {release.tag}: {release.title}")
    link = escape(f"/musehub/ui/{owner}/{slug}/releases/{release.tag}")
    description = escape(release.body or "")
    pub_date = _rss_pub_date(release.created_at)
    guid = escape(release.release_id)

    enclosure_xml = ""
    mp3_url = release.download_urls.mp3 if release.download_urls else None
    if mp3_url:
        enclosure_xml = (
            f'\n      <enclosure url="{escape(mp3_url)}" type="audio/mpeg" length="0"/>'
        )

    return (
        "<item>\n"
        f"      <title>{title}</title>\n"
        f"      <link>{link}</link>\n"
        f"      <description>{description}</description>\n"
        f"      <pubDate>{pub_date}</pubDate>\n"
        f"      <guid isPermaLink=\"false\">{guid}</guid>"
        f"{enclosure_xml}\n"
        "    </item>"
    )


def _issue_rss_item(issue: IssueResponse, owner: str, slug: str) -> str:
    """Render a single issue as an RSS <item>."""
    title = escape(issue.title)
    link = escape(f"/musehub/ui/{owner}/{slug}/issues/{issue.number}")
    description = escape(issue.body or "")
    pub_date = _rss_pub_date(issue.created_at)
    guid = escape(issue.issue_id)
    return (
        "<item>\n"
        f"      <title>{title}</title>\n"
        f"      <link>{link}</link>\n"
        f"      <description>{description}</description>\n"
        f"      <pubDate>{pub_date}</pubDate>\n"
        f"      <guid isPermaLink=\"false\">{guid}</guid>\n"
        "    </item>"
    )


def _build_atom_envelope(
    title: str,
    feed_id: str,
    updated: str,
    entries: list[str],
) -> str:
    """Wrap Atom entries in a <feed> envelope (Atom 1.0, RFC 4287)."""
    entries_xml = "\n  ".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{escape(title)}</title>\n"
        f"  <id>{escape(feed_id)}</id>\n"
        f"  <updated>{updated}</updated>\n"
        f"  {entries_xml}\n"
        "</feed>"
    )


def _commit_atom_entry(commit: CommitResponse, owner: str, slug: str) -> str:
    """Render a single commit as an Atom <entry>."""
    title = escape(commit.message[:80])
    link = escape(f"/musehub/ui/{owner}/{slug}/commits/{commit.commit_id}")
    entry_id = escape(f"tag:musehub:{commit.commit_id}")
    updated = _atom_date(commit.timestamp)
    summary = escape(commit.message)
    return (
        "<entry>\n"
        f"    <title>{title}</title>\n"
        f'    <link href="{link}"/>\n'
        f"    <id>{entry_id}</id>\n"
        f"    <updated>{updated}</updated>\n"
        f"    <summary>{summary}</summary>\n"
        "  </entry>"
    )


# ── Route handlers ────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/feed.rss",
    operation_id="getCommitFeedRss",
    summary="RSS 2.0 feed of recent commits for a public repo",
    responses={
        200: {"description": "RSS 2.0 XML feed", "content": {"application/rss+xml": {}}},
        403: {"description": "Repo is private — feeds require public visibility"},
        404: {"description": "Repo not found"},
    },
)
async def get_commit_feed_rss(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Return an RSS 2.0 feed of the most recent commits for a public repo.

    Feed consumers (aggregators, AI agents) may subscribe to this URL without
    credentials. Up to 50 commits are included, newest first.

    Returns 403 for private repos; feed readers cannot supply credentials.
    """
    raw_repo = await musehub_repository.get_repo(db, repo_id)
    repo = _require_public(raw_repo)

    commits, _ = await musehub_repository.list_commits(db, repo_id, limit=_COMMIT_FEED_LIMIT)
    items = [_commit_rss_item(c, repo.owner, repo.slug) for c in commits]

    feed_link = f"/musehub/ui/{repo.owner}/{repo.slug}"
    xml = _build_rss_envelope(
        title=f"{repo.owner}/{repo.slug} commits",
        link=feed_link,
        description=repo.description or f"Recent commits for {repo.owner}/{repo.slug}",
        items=items,
    )
    logger.debug("✅ Served commit RSS feed for repo %s (%d items)", repo_id, len(items))
    return Response(content=xml, media_type="application/rss+xml")


@router.get(
    "/repos/{repo_id}/releases.rss",
    operation_id="getReleasesFeedRss",
    summary="RSS 2.0 feed of releases for a public repo",
    responses={
        200: {"description": "RSS 2.0 XML feed", "content": {"application/rss+xml": {}}},
        403: {"description": "Repo is private — feeds require public visibility"},
        404: {"description": "Repo not found"},
    },
)
async def get_releases_feed_rss(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Return an RSS 2.0 feed of the most recent releases for a public repo.

    Each item includes the release tag, title, notes body, and an optional
    mp3 <enclosure> when a rendered audio download is available.
    """
    raw_repo = await musehub_repository.get_repo(db, repo_id)
    repo = _require_public(raw_repo)

    releases = await musehub_releases.list_releases(db, repo_id)
    releases = releases[:_RELEASE_FEED_LIMIT]
    items = [_release_rss_item(r, repo.owner, repo.slug) for r in releases]

    feed_link = f"/musehub/ui/{repo.owner}/{repo.slug}/releases"
    xml = _build_rss_envelope(
        title=f"{repo.owner}/{repo.slug} releases",
        link=feed_link,
        description=f"Releases for {repo.owner}/{repo.slug}",
        items=items,
    )
    logger.debug("✅ Served releases RSS feed for repo %s (%d items)", repo_id, len(items))
    return Response(content=xml, media_type="application/rss+xml")


@router.get(
    "/repos/{repo_id}/issues.rss",
    operation_id="getIssuesFeedRss",
    summary="RSS 2.0 feed of open issues for a public repo",
    responses={
        200: {"description": "RSS 2.0 XML feed", "content": {"application/rss+xml": {}}},
        403: {"description": "Repo is private — feeds require public visibility"},
        404: {"description": "Repo not found"},
    },
)
async def get_issues_feed_rss(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Return an RSS 2.0 feed of open issues for a public repo.

    Only open issues are included. Issues are ordered by issue number
    (ascending). Up to 50 issues are included.
    """
    raw_repo = await musehub_repository.get_repo(db, repo_id)
    repo = _require_public(raw_repo)

    issues = await musehub_issues.list_issues(db, repo_id, state="open")
    issues = issues[:_ISSUE_FEED_LIMIT]
    items = [_issue_rss_item(i, repo.owner, repo.slug) for i in issues]

    feed_link = f"/musehub/ui/{repo.owner}/{repo.slug}/issues"
    xml = _build_rss_envelope(
        title=f"{repo.owner}/{repo.slug} issues",
        link=feed_link,
        description=f"Open issues for {repo.owner}/{repo.slug}",
        items=items,
    )
    logger.debug("✅ Served issues RSS feed for repo %s (%d items)", repo_id, len(items))
    return Response(content=xml, media_type="application/rss+xml")


@router.get(
    "/repos/{repo_id}/feed.atom",
    operation_id="getCommitFeedAtom",
    summary="Atom 1.0 feed of recent commits for a public repo",
    responses={
        200: {"description": "Atom 1.0 XML feed", "content": {"application/atom+xml": {}}},
        403: {"description": "Repo is private — feeds require public visibility"},
        404: {"description": "Repo not found"},
    },
)
async def get_commit_feed_atom(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Return an Atom 1.0 feed of the most recent commits for a public repo.

    Atom 1.0 (RFC 4287) is the preferred format for machine consumers and
    aggregators that support both Atom and RSS. Content is identical to
    ``/feed.rss`` but formatted per the Atom specification.

    Returns 403 for private repos; feed readers cannot supply credentials.
    """
    raw_repo = await musehub_repository.get_repo(db, repo_id)
    repo = _require_public(raw_repo)

    commits, _ = await musehub_repository.list_commits(db, repo_id, limit=_COMMIT_FEED_LIMIT)
    entries = [_commit_atom_entry(c, repo.owner, repo.slug) for c in commits]

    feed_id = f"tag:musehub:{repo_id}:commits"
    updated = _atom_date(commits[0].timestamp) if commits else _atom_date(
        datetime.now(tz=timezone.utc)
    )

    xml = _build_atom_envelope(
        title=f"{repo.owner}/{repo.slug} commits",
        feed_id=feed_id,
        updated=updated,
        entries=entries,
    )
    logger.debug("✅ Served commit Atom feed for repo %s (%d entries)", repo_id, len(entries))
    return Response(content=xml, media_type="application/atom+xml")
