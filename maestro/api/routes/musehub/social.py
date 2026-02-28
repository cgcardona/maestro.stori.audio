"""Muse Hub social layer — comments, reactions, follows, watches, notifications, forks.

Endpoint summary:
  GET  /musehub/repos/{repo_id}/comments?target_type=&target_id=  — list comments on an object
  POST /musehub/repos/{repo_id}/comments                          — add a comment (auth required)
  DELETE /musehub/repos/{repo_id}/comments/{comment_id}           — soft-delete (auth, owner only)

  GET  /musehub/repos/{repo_id}/reactions?target_type=&target_id= — reaction counts
  POST /musehub/repos/{repo_id}/reactions                         — toggle reaction (auth required)

  GET  /musehub/users/{username}/followers                        — list followers
  POST /musehub/users/{username}/follow                           — follow user (auth required)
  DELETE /musehub/users/{username}/follow                         — unfollow (auth required)

  GET  /musehub/repos/{repo_id}/watches                           — watch count
  POST /musehub/repos/{repo_id}/watch                             — watch repo (auth required)
  DELETE /musehub/repos/{repo_id}/watch                           — unwatch (auth required)

  GET  /musehub/notifications                                     — inbox (auth required)
  POST /musehub/notifications/{notif_id}/read                     — mark read (auth required)
  POST /musehub/notifications/read-all                            — mark all read (auth required)

  POST /musehub/repos/{repo_id}/fork                              — fork repo (auth required)
  GET  /musehub/repos/{repo_id}/forks                             — list forks

  GET  /musehub/feed                                              — activity feed (auth required)

All read endpoints that expose public repo data use optional_token + visibility guard.
Write endpoints always require require_valid_token.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db.musehub_models import (
    MusehubComment,
    MusehubDownloadEvent,
    MusehubFollow,
    MusehubFork,
    MusehubNotification,
    MusehubReaction,
    MusehubStar,
    MusehubViewEvent,
    MusehubWatch,
)
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_EMOJIS = {"👍", "👎", "❤️", "🎵", "🔥", "✨", "🎸", "🥁", "👏", "🎹"}


# ---------------------------------------------------------------------------
# Pydantic schemas (inline — no separate models file needed for thin layer)
# ---------------------------------------------------------------------------


class CommentCreate(BaseModel):
    target_type: str = Field(..., pattern="^(commit|pull_request|issue|release|repo)$")
    target_id: str
    body: str = Field(..., min_length=1, max_length=10000)
    parent_id: str | None = None


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    comment_id: str
    repo_id: str
    target_type: str
    target_id: str
    author: str
    body: str
    parent_id: str | None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class ReactionCreate(BaseModel):
    target_type: str = Field(..., pattern="^(commit|pull_request|issue|comment|repo|release|session)$")
    target_id: str
    emoji: str


class ReactionCount(BaseModel):
    emoji: str
    count: int
    reacted_by_me: bool


class FollowResponse(BaseModel):
    follower_count: int
    following_count: int
    following: bool


class WatchResponse(BaseModel):
    watch_count: int
    watching: bool


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notif_id: str
    event_type: str
    repo_id: str | None
    actor: str
    payload: dict[str, object]
    is_read: bool
    created_at: datetime


class ForkResponse(BaseModel):
    """Fork relationship record — lineage link between source and fork repo.

    ``fork_owner`` and ``fork_slug`` are set by the fork handler (not sourced
    from the ORM) so the client can redirect to the new fork's page without
    a second round-trip.
    """

    model_config = ConfigDict(from_attributes=True)

    fork_id: str
    source_repo_id: str
    fork_repo_id: str
    forked_by: str
    created_at: datetime
    fork_owner: str = ""
    fork_slug: str = ""


class ReactionToggleResult(BaseModel):
    """Result of toggling an emoji reaction — indicates whether it was added or removed."""

    added: bool
    emoji: str


class FollowActionResult(BaseModel):
    """Result of a follow action."""

    following: bool
    username: str


class WatchActionResult(BaseModel):
    """Result of a watch action."""

    watching: bool
    repo_id: str


class NotificationReadResult(BaseModel):
    """Result of marking a single notification as read."""

    read: bool
    notif_id: str


class NotificationsReadAllResult(BaseModel):
    """Result of marking all notifications as read."""

    marked_read: int


class AnalyticsSummaryResult(BaseModel):
    """Aggregated view and download counts for a repo."""

    repo_id: str
    view_count: int
    download_count: int


class ViewAnalyticsDayResult(BaseModel):
    """Daily view count for a single date."""

    date: str
    count: int


class SocialTrendsDayResult(BaseModel):
    """Daily aggregated social engagement counts — stars, forks, and watches.

    Each row represents one calendar day. Missing days (no activity) are filled
    with zeros so the chart always spans the full requested window.
    """

    date: str
    stars: int
    forks: int
    watches: int


class SocialTrendsResult(BaseModel):
    """Aggregate totals and per-day trend data for social engagement on a repo.

    Returned by GET /repos/{id}/analytics/social. Designed for the insights
    dashboard Social Trends chart and the "Who forked this" panel.
    """

    star_count: int
    fork_count: int
    watch_count: int
    trend: list[SocialTrendsDayResult]
    forks_detail: list[ForkResponse]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.get("/repos/{repo_id}/comments", operation_id="listComments", summary="List comments on a target object")
async def list_comments(
    repo_id: str,
    target_type: str = Query(...),
    target_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[CommentResponse]:
    """Return comments for a given target object within a repo."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})

    rows = (await db.execute(
        select(MusehubComment)
        .where(
            MusehubComment.repo_id == repo_id,
            MusehubComment.target_type == target_type,
            MusehubComment.target_id == target_id,
            MusehubComment.is_deleted.is_(False),
        )
        .order_by(MusehubComment.created_at)
        .limit(limit)
    )).scalars().all()
    return [CommentResponse.model_validate(r) for r in rows]


@router.post("/repos/{repo_id}/comments", status_code=status.HTTP_201_CREATED,
             operation_id="createComment", summary="Post a comment on a target object")
async def create_comment(
    repo_id: str,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> CommentResponse:
    """Add a comment. Authentication required."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    now = datetime.now(tz=timezone.utc)
    comment = MusehubComment(
        comment_id=str(uuid.uuid4()),
        repo_id=repo_id,
        target_type=body.target_type,
        target_id=body.target_id,
        author=claims.get("sub", ""),
        body=body.body,
        parent_id=body.parent_id,
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return CommentResponse.model_validate(comment)


@router.delete("/repos/{repo_id}/comments/{comment_id}",
               status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteComment", summary="Soft-delete a comment")
async def delete_comment(
    repo_id: str,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> None:
    """Soft-delete a comment. Only the comment's author may delete it."""
    row = (await db.execute(
        select(MusehubComment).where(
            MusehubComment.comment_id == comment_id,
            MusehubComment.repo_id == repo_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if row.author != claims.get("sub", ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your comment")
    row.is_deleted = True
    await db.commit()


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


@router.get("/repos/{repo_id}/reactions", operation_id="getReactions", summary="Get reaction counts for a target")
async def list_reactions(
    repo_id: str,
    target_type: str = Query(...),
    target_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[ReactionCount]:
    """Return emoji reaction counts for a target object."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    rows = (await db.execute(
        select(MusehubReaction.emoji, func.count().label("cnt"))
        .where(
            MusehubReaction.repo_id == repo_id,
            MusehubReaction.target_type == target_type,
            MusehubReaction.target_id == target_id,
        )
        .group_by(MusehubReaction.emoji)
    )).all()

    my_emojis: set[str] = set()
    if claims:
        my_rows = (await db.execute(
            select(MusehubReaction.emoji).where(
                MusehubReaction.repo_id == repo_id,
                MusehubReaction.target_type == target_type,
                MusehubReaction.target_id == target_id,
                MusehubReaction.user_id == claims.get("sub", ""),
            )
        )).scalars().all()
        my_emojis = set(my_rows)

    return [ReactionCount(emoji=row.emoji, count=row.cnt, reacted_by_me=row.emoji in my_emojis)
            for row in rows]


@router.post("/repos/{repo_id}/reactions", status_code=status.HTTP_201_CREATED,
             operation_id="toggleReaction", summary="Toggle a reaction on a target")
async def toggle_reaction(
    repo_id: str,
    body: ReactionCreate,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> ReactionToggleResult:
    """Toggle an emoji reaction. Adds if not present, removes if already reacted."""
    if body.emoji not in _ALLOWED_EMOJIS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Emoji must be one of: {', '.join(_ALLOWED_EMOJIS)}")

    existing = (await db.execute(
        select(MusehubReaction).where(
            MusehubReaction.repo_id == repo_id,
            MusehubReaction.target_type == body.target_type,
            MusehubReaction.target_id == body.target_id,
            MusehubReaction.user_id == claims.get("sub", ""),
            MusehubReaction.emoji == body.emoji,
        )
    )).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        await db.commit()
        return ReactionToggleResult(added=False, emoji=body.emoji)

    reaction = MusehubReaction(
        reaction_id=str(uuid.uuid4()),
        repo_id=repo_id,
        target_type=body.target_type,
        target_id=body.target_id,
        user_id=claims.get("sub", ""),
        emoji=body.emoji,
    )
    db.add(reaction)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    return ReactionToggleResult(added=True, emoji=body.emoji)


# ---------------------------------------------------------------------------
# Follow / Unfollow
# ---------------------------------------------------------------------------


@router.get("/users/{username}/followers", operation_id="getFollowers", summary="Get follower and following counts for a user")
async def get_followers(
    username: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> FollowResponse:
    """Return follower count, following count, and whether the calling user follows this user."""
    from sqlalchemy import or_
    from maestro.db.musehub_models import MusehubProfile

    # Resolve user_id so we can match both username-keyed and user_id-keyed rows.
    profile_row = (await db.execute(
        select(MusehubProfile).where(MusehubProfile.username == username)
    )).scalar_one_or_none()
    user_id = profile_row.user_id if profile_row else username

    follower_count = (await db.execute(
        select(func.count()).where(
            or_(MusehubFollow.followee_id == username, MusehubFollow.followee_id == user_id)
        )
    )).scalar_one()

    following_count = (await db.execute(
        select(func.count()).where(
            or_(MusehubFollow.follower_id == username, MusehubFollow.follower_id == user_id)
        )
    )).scalar_one()

    following = False
    if claims:
        caller_id = claims.get("sub", "")
        following = bool((await db.execute(
            select(MusehubFollow).where(
                MusehubFollow.follower_id == caller_id,
                or_(MusehubFollow.followee_id == username, MusehubFollow.followee_id == user_id),
            )
        )).scalar_one_or_none())
    return FollowResponse(follower_count=follower_count, following_count=following_count, following=following)


@router.post("/users/{username}/follow", status_code=status.HTTP_201_CREATED,
             operation_id="followUser", summary="Follow a user")
async def follow_user(
    username: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> FollowActionResult:
    """Follow another user. Idempotent."""
    if claims.get("sub", "") == username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow yourself")
    follow = MusehubFollow(
        follow_id=str(uuid.uuid4()),
        follower_id=claims.get("sub", ""),
        followee_id=username,
    )
    db.add(follow)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    return FollowActionResult(following=True, username=username)


@router.delete("/users/{username}/follow", status_code=status.HTTP_204_NO_CONTENT,
               operation_id="unfollowUser", summary="Unfollow a user")
async def unfollow_user(
    username: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> None:
    """Unfollow a user. No-op if not following."""
    await db.execute(
        delete(MusehubFollow).where(
            MusehubFollow.follower_id == claims.get("sub", ""),
            MusehubFollow.followee_id == username,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Watch / Unwatch
# ---------------------------------------------------------------------------


@router.get("/repos/{repo_id}/watches", operation_id="getWatchCount", summary="Get watch count for a repo")
async def get_watches(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> WatchResponse:
    """Return watch count and whether the calling user is watching."""
    count = (await db.execute(
        select(func.count()).where(MusehubWatch.repo_id == repo_id)
    )).scalar_one()
    watching = False
    if claims:
        watching = bool((await db.execute(
            select(MusehubWatch).where(
                MusehubWatch.user_id == claims.get("sub", ""),
                MusehubWatch.repo_id == repo_id,
            )
        )).scalar_one_or_none())
    return WatchResponse(watch_count=count, watching=watching)


@router.post("/repos/{repo_id}/watch", status_code=status.HTTP_201_CREATED,
             operation_id="watchRepo", summary="Watch a repo")
async def watch_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> WatchActionResult:
    """Subscribe to repo activity. Idempotent."""
    watch = MusehubWatch(
        watch_id=str(uuid.uuid4()),
        user_id=claims.get("sub", ""),
        repo_id=repo_id,
    )
    db.add(watch)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    return WatchActionResult(watching=True, repo_id=repo_id)


@router.delete("/repos/{repo_id}/watch", status_code=status.HTTP_204_NO_CONTENT,
               operation_id="unwatchRepo", summary="Unwatch a repo")
async def unwatch_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> None:
    """Unsubscribe from repo activity."""
    await db.execute(
        delete(MusehubWatch).where(
            MusehubWatch.user_id == claims.get("sub", ""),
            MusehubWatch.repo_id == repo_id,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.get("/notifications", operation_id="listNotifications", summary="Get notification inbox")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> list[NotificationResponse]:
    """Return the calling user's notification inbox, newest first."""
    q = select(MusehubNotification).where(MusehubNotification.recipient_id == claims.get("sub", ""))
    if unread_only:
        q = q.where(MusehubNotification.is_read.is_(False))
    q = q.order_by(MusehubNotification.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [NotificationResponse.model_validate(r) for r in rows]


@router.post("/notifications/{notif_id}/read", operation_id="markNotificationRead", summary="Mark a notification as read")
async def mark_notification_read(
    notif_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> NotificationReadResult:
    """Mark a single notification as read."""
    row = (await db.execute(
        select(MusehubNotification).where(
            MusehubNotification.notif_id == notif_id,
            MusehubNotification.recipient_id == claims.get("sub", ""),
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    row.is_read = True
    await db.commit()
    return NotificationReadResult(read=True, notif_id=notif_id)


@router.post("/notifications/read-all", operation_id="markAllNotificationsRead", summary="Mark all notifications as read")
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> NotificationsReadAllResult:
    """Mark all of the calling user's notifications as read."""
    rows = (await db.execute(
        select(MusehubNotification).where(
            MusehubNotification.recipient_id == claims.get("sub", ""),
            MusehubNotification.is_read.is_(False),
        )
    )).scalars().all()
    for row in rows:
        row.is_read = True
    await db.commit()
    return NotificationsReadAllResult(marked_read=len(rows))


# ---------------------------------------------------------------------------
# Forks
# ---------------------------------------------------------------------------


@router.get("/repos/{repo_id}/forks", operation_id="listForks", summary="List forks of a repo")
async def list_forks(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[ForkResponse]:
    """Return all forks of a repo."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})
    rows = (await db.execute(
        select(MusehubFork).where(MusehubFork.source_repo_id == repo_id)
    )).scalars().all()
    return [ForkResponse.model_validate(r) for r in rows]


@router.post("/repos/{repo_id}/fork", status_code=status.HTTP_201_CREATED,
             operation_id="forkRepo", summary="Fork a repo")
async def fork_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> ForkResponse:
    """Create a fork of the given repo under the calling user's account.

    Creates a new repo owned by the caller, copies all commits and branches
    from the source into the new repo, then records the fork lineage.
    The fork's description is prefixed with "Fork of {owner}/{slug}" so the
    repo home page can display a "Forked from" badge.
    """
    from maestro.services import musehub_repository as mhr
    from maestro.db.musehub_models import MusehubBranch

    source = await mhr.get_repo(db, repo_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source repo not found")
    if source.visibility != "public":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Can only fork public repos")

    caller: str = claims.get("sub") or ""
    try:
        fork_repo_row = await mhr.create_repo(
            db,
            name=source.name,
            owner=caller,
            visibility=source.visibility,
            owner_user_id=caller,
            description=f"Fork of {source.owner}/{source.slug}",
            tags=list(source.tags) if source.tags else [],
            key_signature=source.key_signature,
            tempo_bpm=source.tempo_bpm,
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A repo with this slug already exists in your namespace")

    now = datetime.now(tz=timezone.utc)
    fork_record = MusehubFork(
        fork_id=str(uuid.uuid4()),
        source_repo_id=repo_id,
        fork_repo_id=fork_repo_row.repo_id,
        forked_by=caller,
        created_at=now,
    )
    db.add(fork_record)

    # Note: commit_id is the global PK so we cannot duplicate commits across repos.
    # Branch head pointers are copied so the fork has the same branch layout;
    # commits are accessible via the source repo lineage link (MusehubFork).
    source_branches = await mhr.list_branches(db, repo_id)
    for branch in source_branches:
        db.add(MusehubBranch(
            repo_id=fork_repo_row.repo_id,
            name=branch.name,
            head_commit_id=branch.head_commit_id,
        ))

    await db.commit()
    await db.refresh(fork_record)
    logger.info("🍴 Fork created source=%s fork=%s by=%s", repo_id, fork_repo_row.repo_id, caller)
    resp = ForkResponse.model_validate(fork_record)
    resp.fork_owner = fork_repo_row.owner
    resp.fork_slug = fork_repo_row.slug
    return resp


# ---------------------------------------------------------------------------
# Analytics — view events and download counts (Phase 5)
# ---------------------------------------------------------------------------


@router.post("/repos/{repo_id}/view", status_code=status.HTTP_204_NO_CONTENT,
             operation_id="recordView", summary="Record a page view for analytics")
async def record_view(
    repo_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> None:
    """Debounced view count increment — one row per (repo, fingerprint, date)."""
    import hashlib
    from datetime import date

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    fingerprint = hashlib.sha256(f"{ip}{ua}".encode()).hexdigest()[:64]
    today = date.today().isoformat()

    view = MusehubViewEvent(
        view_id=str(uuid.uuid4()),
        repo_id=repo_id,
        viewer_fingerprint=fingerprint,
        event_date=today,
    )
    db.add(view)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()  # already viewed today — no-op


@router.get("/repos/{repo_id}/analytics", operation_id="getRepoAnalytics", summary="Repo analytics summary")
async def get_analytics(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> AnalyticsSummaryResult:
    """Return view counts and download counts for a repo."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})

    view_count = (await db.execute(
        select(func.count()).where(MusehubViewEvent.repo_id == repo_id)
    )).scalar_one()

    dl_count = (await db.execute(
        select(func.count()).where(MusehubDownloadEvent.repo_id == repo_id)
    )).scalar_one()

    return AnalyticsSummaryResult(repo_id=repo_id, view_count=view_count, download_count=dl_count)


@router.get("/repos/{repo_id}/analytics/views", operation_id="getRepoViewAnalytics", summary="Daily view counts")
async def get_view_analytics(
    repo_id: str,
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[ViewAnalyticsDayResult]:
    """Return daily view counts for the last N days.

    Aggregates MusehubViewEvent rows by event_date so the insights page can
    render a 30-day traffic sparkline without sending raw event rows over the
    wire.
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=days)
    rows = await db.execute(
        select(MusehubViewEvent.event_date, func.count().label("view_count"))
        .where(MusehubViewEvent.repo_id == repo_id)
        .where(MusehubViewEvent.event_date >= cutoff.isoformat())
        .group_by(MusehubViewEvent.event_date)
        .order_by(MusehubViewEvent.event_date)
    )
    results = rows.all()
    return [ViewAnalyticsDayResult(date=r.event_date, count=r.view_count) for r in results]


@router.get(
    "/repos/{repo_id}/analytics/social",
    operation_id="getRepoSocialAnalytics",
    summary="Social trends — daily stars, forks, and watches",
)
async def get_social_analytics(
    repo_id: str,
    days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> SocialTrendsResult:
    """Return daily social engagement counts for the last N days.

    Aggregates MusehubStar, MusehubFork, and MusehubWatch rows by calendar date
    so the insights page can render a 90-day multi-line trend chart without
    sending raw event rows over the wire. Days with no activity are filled with
    zeros so the chart spans the full window.

    Also returns total counts and fork details (forked_by username) for the
    "Who forked this" panel on the insights dashboard.

    Args:
        repo_id: The repo's UUID.
        days: Window size in days (default 90, max 365).

    Returns:
        SocialTrendsResult with totals, per-day trend list, and fork details.
    """
    from datetime import date, timedelta

    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    cutoff = date.today() - timedelta(days=days)
    cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc)

    # Total counts
    star_count = (await db.execute(
        select(func.count()).where(MusehubStar.repo_id == repo_id)
    )).scalar_one()

    fork_count = (await db.execute(
        select(func.count()).where(MusehubFork.source_repo_id == repo_id)
    )).scalar_one()

    watch_count = (await db.execute(
        select(func.count()).where(MusehubWatch.repo_id == repo_id)
    )).scalar_one()

    # Daily aggregates — func.date() works across SQLite and PostgreSQL
    # and always returns a YYYY-MM-DD string, avoiding dialect type-conversion issues.
    star_rows = (await db.execute(
        select(func.date(MusehubStar.created_at).label("day"), func.count().label("n"))
        .where(MusehubStar.repo_id == repo_id)
        .where(MusehubStar.created_at >= cutoff_dt)
        .group_by(func.date(MusehubStar.created_at))
    )).all()

    fork_rows = (await db.execute(
        select(func.date(MusehubFork.created_at).label("day"), func.count().label("n"))
        .where(MusehubFork.source_repo_id == repo_id)
        .where(MusehubFork.created_at >= cutoff_dt)
        .group_by(func.date(MusehubFork.created_at))
    )).all()

    watch_rows = (await db.execute(
        select(func.date(MusehubWatch.created_at).label("day"), func.count().label("n"))
        .where(MusehubWatch.repo_id == repo_id)
        .where(MusehubWatch.created_at >= cutoff_dt)
        .group_by(func.date(MusehubWatch.created_at))
    )).all()

    # Build day-keyed lookup dicts
    stars_by_day: dict[str, int] = {str(r.day): r.n for r in star_rows}
    forks_by_day: dict[str, int] = {str(r.day): r.n for r in fork_rows}
    watches_by_day: dict[str, int] = {str(r.day): r.n for r in watch_rows}

    # Emit one entry per day in the window, filling zeros for missing days
    trend: list[SocialTrendsDayResult] = []
    for offset in range(days):
        day = cutoff + timedelta(days=offset)
        key = day.isoformat()
        trend.append(SocialTrendsDayResult(
            date=key,
            stars=stars_by_day.get(key, 0),
            forks=forks_by_day.get(key, 0),
            watches=watches_by_day.get(key, 0),
        ))

    # Fork details for "Who forked this" panel
    fork_detail_rows = (await db.execute(
        select(MusehubFork).where(MusehubFork.source_repo_id == repo_id)
        .order_by(MusehubFork.created_at.desc())
    )).scalars().all()

    return SocialTrendsResult(
        star_count=star_count,
        fork_count=fork_count,
        watch_count=watch_count,
        trend=trend,
        forks_detail=[ForkResponse.model_validate(f) for f in fork_detail_rows],
    )


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


@router.get("/feed", operation_id="getActivityFeed", summary="Activity feed for the calling user")
async def get_feed(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> list[NotificationResponse]:
    """Return a chronological activity feed of events from followed users
    and watched repos.

    Currently backed by the notifications table. Future: a dedicated feed
    materialization table for higher-volume use.
    """
    rows = (await db.execute(
        select(MusehubNotification)
        .where(MusehubNotification.recipient_id == claims.get("sub", ""))
        .order_by(MusehubNotification.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [NotificationResponse.model_validate(r) for r in rows]
