"""Muse Hub topics browsing UI pages.

Serves two routes from a single module — both render the same template with a
``mode`` switch that distinguishes the index view from the per-tag detail view:

  GET /musehub/ui/topics          — topics index (grid + curated groups + search)
  GET /musehub/ui/topics/{tag}    — single topic detail (featured repos + repo grid)

Content negotiation (one URL, two audiences):
  HTML (default) — server-side rendered via Jinja2 using ``musehub/pages/topics.html``.
    HTMX partial requests (``HX-Request: true``) receive only the inner fragment:
    ``fragments/topic_grid.html`` (index mode) or ``fragments/topic_repos.html``
    (detail mode), enabling client-side partial updates without a full reload.
  JSON (``?format=json`` or ``Accept: application/json``) — returns the
  appropriate Pydantic response model for machine consumption.

Auth: no JWT required — topics pages are public read-only surfaces.

Agent use case:
  Call ``GET /musehub/ui/topics?format=json`` to get a ranked list of all
  topics with repo counts, plus curated Genres/Instruments/Eras groupings.
  Call ``GET /musehub/ui/topics/{tag}?format=json`` to get the paginated repo
  list for a specific tag — same contract as the API endpoint but accessible
  from the UI URL for agents that read the human-facing surface.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import Text, desc, func, outerjoin, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

<<<<<<< HEAD
from maestro.api.routes.musehub._templates import templates
from maestro.api.routes.musehub.negotiate import negotiate_response
=======
from maestro.api.routes.musehub.negotiate import htmx_fragment_or_full
>>>>>>> origin/dev
from maestro.api.routes.musehub.topics import TopicItem, TopicReposResponse
from maestro.auth.dependencies import TokenClaims, optional_token
from maestro.db import get_db
from maestro.db import musehub_models as db
from maestro.models.base import CamelModel
from maestro.models.musehub import ExploreRepoResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

# ---------------------------------------------------------------------------
# Curated topic groups — surfaced on the index page for quick navigation.
# Each entry is (display_label, list_of_slugs). Only slugs with at least one
# public repo will carry a non-zero repo_count when rendered.
# ---------------------------------------------------------------------------

_CURATED_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Genres",
        [
            "jazz", "blues", "rock", "classical", "electronic", "ambient",
            "hip-hop", "folk", "soul", "funk", "reggae", "country", "metal",
            "punk", "indie", "r-and-b", "pop", "bossa-nova", "afrobeats",
        ],
    ),
    (
        "Instruments",
        [
            "piano", "guitar", "bass", "drums", "violin", "saxophone",
            "trumpet", "flute", "synth", "cello", "clarinet", "harp", "organ",
        ],
    ),
    (
        "Eras",
        [
            "baroque", "classical-era", "romantic", "modern", "contemporary",
            "renaissance", "medieval", "20th-century", "neo-classical",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CuratedGroup(CamelModel):
    """A labelled set of topic items surfaced on the topics index page.

    Each group maps a human-readable category (Genres, Instruments, Eras) to
    the topic slugs that belong to it, pre-populated with live repo counts so
    the client can show or hide empty buckets without a second fetch.
    """

    label: str = Field(..., description="Display label for the group (e.g. 'Genres')")
    topics: list[TopicItem] = Field(
        ..., description="Topics in this group with their current repo_count"
    )


class TopicsIndexResponse(CamelModel):
    """JSON response for GET /musehub/ui/topics.

    ``all_topics`` is the full ranked list of every topic that exists on at
    least one public repo, sorted by popularity (repo_count desc).
    ``curated_groups`` overlays the same data into Genres / Instruments / Eras
    buckets — useful for navigation sidebars and landing-page discovery widgets.
    Agents should prefer this endpoint over the raw API when they need both the
    ranked list and the grouped view in a single round-trip.
    """

    all_topics: list[TopicItem] = Field(
        ..., description="All topics sorted by repo_count desc"
    )
    curated_groups: list[CuratedGroup] = Field(
        ..., description="Curated topic groups for index-page navigation"
    )
    total: int = Field(..., ge=0, description="Total number of distinct topics")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_all_topics(db_session: AsyncSession) -> list[TopicItem]:
    """Aggregate all topic slugs from public repos, sorted by popularity.

    Performs a full scan of ``musehub_repos.tags`` and counts tag occurrences
    in Python — safe across PostgreSQL and SQLite (used in tests).  Private
    repo tags are excluded so callers cannot infer private repo existence.
    """
    rows = await db_session.execute(
        select(db.MusehubRepo.tags).where(db.MusehubRepo.visibility == "public")
    )
    counts: dict[str, int] = {}
    for (tags,) in rows:
        for tag in tags or []:
            t = str(tag).lower()
            counts[t] = counts.get(t, 0) + 1

    return [
        TopicItem(name=name, repo_count=count)
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _build_curated_groups(all_topics: list[TopicItem]) -> list[CuratedGroup]:
    """Build curated topic groups, populating repo_count from the aggregated list.

    Topics that have zero public repos still appear in the group with
    ``repo_count=0`` so the UI can render them as disabled or faded — callers
    can filter these out client-side if they prefer a sparse presentation.
    """
    count_map: dict[str, int] = {t.name: t.repo_count for t in all_topics}
    groups: list[CuratedGroup] = []
    for label, slugs in _CURATED_GROUPS:
        items = [
            TopicItem(name=slug, repo_count=count_map.get(slug, 0))
            for slug in slugs
        ]
        groups.append(CuratedGroup(label=label, topics=items))
    return groups


async def _fetch_topic_repos(
    db_session: AsyncSession,
    tag: str,
    sort: str = "stars",
    page: int = 1,
    page_size: int = 24,
) -> TopicReposResponse:
    """Fetch paginated repos tagged with ``tag``, mirroring the API logic.

    Duplicates the query from ``topics.list_repos_by_topic`` intentionally:
    the UI layer owns its own DB access to stay decoupled from the API layer's
    internal implementation.  Sort options: ``stars`` or ``updated``.
    """
    tag_lower = tag.lower()
    offset = (max(page, 1) - 1) * page_size

    star_count_col = func.count(db.MusehubStar.star_id).label("star_count")
    latest_commit_col = func.max(db.MusehubCommit.timestamp).label("latest_commit")

    base_q = (
        select(db.MusehubRepo, star_count_col, latest_commit_col)
        .select_from(
            outerjoin(
                outerjoin(
                    db.MusehubRepo,
                    db.MusehubStar,
                    db.MusehubRepo.repo_id == db.MusehubStar.repo_id,
                ),
                db.MusehubCommit,
                db.MusehubRepo.repo_id == db.MusehubCommit.repo_id,
            )
        )
        .where(db.MusehubRepo.visibility == "public")
        .where(
            # Cross-engine compat: cast JSON array to text and search for the
            # tag wrapped in quotes to prevent substring false positives.
            func.cast(db.MusehubRepo.tags, Text).ilike(f'%"{tag_lower}"%')
        )
        .group_by(db.MusehubRepo.repo_id)
    )

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db_session.execute(count_q)).scalar_one()

    if sort == "updated":
        base_q = base_q.order_by(desc("latest_commit"), desc(db.MusehubRepo.created_at))
    else:
        base_q = base_q.order_by(desc("star_count"), desc(db.MusehubRepo.created_at))

    rows = (await db_session.execute(base_q.offset(offset).limit(page_size))).all()

    repos = [
        ExploreRepoResult(
            repo_id=row.MusehubRepo.repo_id,
            name=row.MusehubRepo.name,
            owner=row.MusehubRepo.owner,
            slug=row.MusehubRepo.slug,
            owner_user_id=row.MusehubRepo.owner_user_id,
            description=row.MusehubRepo.description or "",
            tags=list(row.MusehubRepo.tags or []),
            key_signature=row.MusehubRepo.key_signature,
            tempo_bpm=row.MusehubRepo.tempo_bpm,
            star_count=row.star_count or 0,
            commit_count=0,
            created_at=row.MusehubRepo.created_at,
        )
        for row in rows
    ]

    return TopicReposResponse(
        tag=tag_lower, repos=repos, total=total, page=page, page_size=page_size
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get(
    "/topics",
    summary="Muse Hub topics index — grid of all topics with curated groups and search",
)
async def topics_index_page(
    request: Request,
    format: str | None = Query(
        None, description="Force response format: 'json' or omit for HTML"
    ),
    db_session: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> StarletteResponse:
    """Render the topics index page (SSR) or return structured JSON.

    HTML (default):
        A two-column layout — ranked topic grid on the left, curated category
        groups (Genres, Instruments, Eras) on the right.  All topic data is
        server-side rendered — no client-side fetch required.  HTMX requests
        (``HX-Request: true``) receive only the ``topic_grid`` fragment for
        partial-page updates.

    JSON (``?format=json`` or ``Accept: application/json``):
        Returns ``TopicsIndexResponse`` with:
        - ``allTopics``     — all topics ranked by repo_count (most popular first).
        - ``curatedGroups`` — Genres, Instruments, Eras groupings with counts.
        - ``total``         — distinct topic count.

    No JWT required — the topics index is a public discovery surface.
    """
    all_topics = await _fetch_all_topics(db_session)
    curated_groups = _build_curated_groups(all_topics)
    json_data = TopicsIndexResponse(
        all_topics=all_topics,
        curated_groups=curated_groups,
        total=len(all_topics),
    )

    logger.info("✅ Topics index UI: %d distinct topics", len(all_topics))

    ctx: dict[str, object] = {
        "mode": "index",
        "current_page": "topics",
        "topics": all_topics,
        "curated_groups": curated_groups,
        "total": len(all_topics),
        "breadcrumb_items": [{"label": "Topics", "url": "/musehub/ui/topics"}],
    }
    return await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/topics.html",
        fragment_template="musehub/fragments/topic_grid.html",
        json_data=json_data,
        format_param=format,
    )


@router.get(
    "/topics/{tag}",
    summary="Muse Hub single topic page — featured repos and paginated repo grid",
)
async def topic_detail_page(
    request: Request,
    tag: str,
    sort: str = Query(
        "stars",
        description=(
            "Sort order: 'stars' (most starred first) | "
            "'updated' (most recently committed first)"
        ),
    ),
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(24, ge=1, le=100, description="Repos per page"),
    format: str | None = Query(
        None, description="Force response format: 'json' or omit for HTML"
    ),
    db_session: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> StarletteResponse:
    """Render the topic detail page (SSR) for a single tag slug.

    HTML (default):
        Two sections rendered server-side:
        1. Featured repos — the top-3 most-starred repos for this topic,
           displayed as prominent cards with description and star count.
        2. Full repo grid — paginated, sortable (stars|updated) repo cards
           matching the tag.
        HTMX requests (``HX-Request: true``) receive only the ``topic_repos``
        fragment, enabling sort/pagination updates without a full reload.

    JSON (``?format=json`` or ``Accept: application/json``):
        Returns ``TopicReposResponse`` (tag, repos, total, page, page_size).
        This mirrors the API endpoint contract — agents can use this URL
        interchangeably with ``/api/v1/musehub/topics/{tag}/repos``.

    Sort options: ``stars`` (default) | ``updated``.
    Invalid sort values silently fall back to ``stars``.
    No JWT required — topic detail pages are publicly accessible.
    """
    if sort not in ("stars", "updated"):
        sort = "stars"

    topic_data = await _fetch_topic_repos(
        db_session, tag, sort=sort, page=page, page_size=page_size
    )

    logger.info(
        "✅ Topic detail UI: tag=%r sort=%s page=%d repos=%d total=%d",
        tag.lower(),
        sort,
        page,
        len(topic_data.repos),
        topic_data.total,
    )

    total_pages = max(1, (topic_data.total + page_size - 1) // page_size)
    ctx: dict[str, object] = {
        "mode": "topic",
        "tag": tag.lower(),
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "repos": topic_data.repos,
        "total": topic_data.total,
        "total_pages": total_pages,
        "current_page": "topics",
        "breadcrumb_items": [
            {"label": "Topics", "url": "/musehub/ui/topics"},
            {"label": f"#{tag.lower()}", "url": ""},
        ],
    }
    return await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/topics.html",
        fragment_template="musehub/fragments/topic_repos.html",
        json_data=topic_data,
        format_param=format,
    )
