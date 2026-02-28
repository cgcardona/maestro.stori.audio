"""Muse Hub release persistence adapter — single point of DB access for releases.

This module is the ONLY place that touches the ``musehub_releases`` table.
Route handlers delegate here; no business logic lives in routes.

Releases tie a human-readable tag (e.g. "v1.0") to a commit snapshot and
carry Markdown release notes plus a structured map of download package URLs.
Tags are unique per repo — creating a duplicate tag raises ``ValueError``.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
- May import the packager to resolve download URLs.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import ReleaseDownloadUrls, ReleaseListResponse, ReleaseResponse
from maestro.services.musehub_release_packager import build_empty_download_urls

logger = logging.getLogger(__name__)


def _urls_from_json(raw: dict[str, str]) -> ReleaseDownloadUrls:
    """Coerce the JSON blob stored in ``download_urls`` to a typed model."""
    return ReleaseDownloadUrls(
        midi_bundle=raw.get("midi_bundle"),
        stems=raw.get("stems"),
        mp3=raw.get("mp3"),
        musicxml=raw.get("musicxml"),
        metadata=raw.get("metadata"),
    )


def _to_release_response(row: db.MusehubRelease) -> ReleaseResponse:
    raw: dict[str, str] = row.download_urls if isinstance(row.download_urls, dict) else {}
    return ReleaseResponse(
        release_id=row.release_id,
        tag=row.tag,
        title=row.title,
        body=row.body,
        commit_id=row.commit_id,
        download_urls=_urls_from_json(raw),
        author=row.author,
        created_at=row.created_at,
    )


async def _tag_exists(session: AsyncSession, repo_id: str, tag: str) -> bool:
    """Return True if a release with this tag already exists for the repo."""
    stmt = select(db.MusehubRelease.release_id).where(
        db.MusehubRelease.repo_id == repo_id,
        db.MusehubRelease.tag == tag,
    )
    result = (await session.execute(stmt)).scalar_one_or_none()
    return result is not None


async def create_release(
    session: AsyncSession,
    *,
    repo_id: str,
    tag: str,
    title: str,
    body: str,
    commit_id: str | None,
    download_urls: ReleaseDownloadUrls | None = None,
    author: str = "",
) -> ReleaseResponse:
    """Persist a new release and return its wire representation.

    ``tag`` must be unique per repo. Raises ``ValueError`` if a release with
    the same tag already exists. The caller is responsible for committing the
    session after this call.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.
        tag: Version tag (e.g. "v1.0") — unique per repo.
        title: Human-readable release title.
        body: Markdown release notes.
        commit_id: Optional commit to pin this release to.
        download_urls: Pre-built download URL map; defaults to empty URLs.
        author: Display name or identifier of the user publishing this release.

    Returns:
        ``ReleaseResponse`` with all fields populated.

    Raises:
        ValueError: If a release with ``tag`` already exists for ``repo_id``.
    """
    if await _tag_exists(session, repo_id, tag):
        raise ValueError(f"Release tag '{tag}' already exists for repo {repo_id}")

    urls = download_urls or build_empty_download_urls()
    urls_dict: dict[str, str] = {
        k: v
        for k, v in {
            "midi_bundle": urls.midi_bundle,
            "stems": urls.stems,
            "mp3": urls.mp3,
            "musicxml": urls.musicxml,
            "metadata": urls.metadata,
        }.items()
        if v is not None
    }

    release = db.MusehubRelease(
        repo_id=repo_id,
        tag=tag,
        title=title,
        body=body,
        commit_id=commit_id,
        download_urls=urls_dict,
        author=author,
    )
    session.add(release)
    await session.flush()
    await session.refresh(release)
    logger.info("✅ Created release %s for repo %s: %s", tag, repo_id, title)
    return _to_release_response(release)


async def list_releases(
    session: AsyncSession,
    repo_id: str,
) -> list[ReleaseResponse]:
    """Return all releases for a repo, ordered newest first.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.

    Returns:
        List of ``ReleaseResponse`` objects ordered by ``created_at`` descending.
    """
    stmt = (
        select(db.MusehubRelease)
        .where(db.MusehubRelease.repo_id == repo_id)
        .order_by(db.MusehubRelease.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_release_response(r) for r in rows]


async def get_release_by_tag(
    session: AsyncSession,
    repo_id: str,
    tag: str,
) -> ReleaseResponse | None:
    """Return a release by its tag for the given repo, or ``None`` if not found.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.
        tag: Version tag to look up (e.g. "v1.0").

    Returns:
        ``ReleaseResponse`` if found, otherwise ``None``.
    """
    stmt = select(db.MusehubRelease).where(
        db.MusehubRelease.repo_id == repo_id,
        db.MusehubRelease.tag == tag,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _to_release_response(row)


async def get_latest_release(
    session: AsyncSession,
    repo_id: str,
) -> ReleaseResponse | None:
    """Return the most recently created release for a repo, or ``None``.

    Used to populate the "Latest release" badge on the repo home page.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.

    Returns:
        The newest ``ReleaseResponse`` or ``None`` if no releases exist.
    """
    stmt = (
        select(db.MusehubRelease)
        .where(db.MusehubRelease.repo_id == repo_id)
        .order_by(db.MusehubRelease.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _to_release_response(row)


async def get_release_list_response(
    session: AsyncSession,
    repo_id: str,
) -> ReleaseListResponse:
    """Convenience wrapper that returns a ``ReleaseListResponse`` directly.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.

    Returns:
        ``ReleaseListResponse`` containing all releases newest first.
    """
    releases = await list_releases(session, repo_id)
    return ReleaseListResponse(releases=releases)
