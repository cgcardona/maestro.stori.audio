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
from maestro.models.musehub import (
    ReleaseAssetDownloadCount,
    ReleaseAssetResponse,
    ReleaseDownloadStatsResponse,
    ReleaseDownloadUrls,
    ReleaseListResponse,
    ReleaseResponse,
)
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


# ── Release asset helpers ─────────────────────────────────────────────────────


def _to_asset_response(row: db.MusehubReleaseAsset) -> ReleaseAssetResponse:
    """Convert a ``MusehubReleaseAsset`` ORM row to its wire representation."""
    return ReleaseAssetResponse(
        asset_id=row.asset_id,
        release_id=row.release_id,
        name=row.name,
        label=row.label,
        content_type=row.content_type,
        size=row.size,
        download_url=row.download_url,
        download_count=row.download_count,
        created_at=row.created_at,
    )


async def attach_asset(
    session: AsyncSession,
    *,
    release_id: str,
    repo_id: str,
    name: str,
    label: str = "",
    content_type: str = "",
    size: int = 0,
    download_url: str,
) -> ReleaseAssetResponse:
    """Attach a new downloadable asset to an existing release.

    The caller is responsible for committing the session after this call.

    Args:
        session: Active async DB session.
        release_id: UUID of the release to attach the asset to.
        repo_id: UUID of the owning repo (denormalised for efficient queries).
        name: Filename shown in the UI.
        label: Optional human-readable label (e.g. "MIDI Bundle").
        content_type: MIME type of the artifact.
        size: File size in bytes; 0 when unknown.
        download_url: Direct download URL for the artifact.

    Returns:
        ``ReleaseAssetResponse`` for the newly created asset.
    """
    asset = db.MusehubReleaseAsset(
        release_id=release_id,
        repo_id=repo_id,
        name=name,
        label=label,
        content_type=content_type,
        size=size,
        download_url=download_url,
    )
    session.add(asset)
    await session.flush()
    await session.refresh(asset)
    logger.info("✅ Attached asset %r to release %s", name, release_id)
    return _to_asset_response(asset)


async def get_asset(
    session: AsyncSession,
    asset_id: str,
) -> db.MusehubReleaseAsset | None:
    """Return the ``MusehubReleaseAsset`` row for ``asset_id``, or ``None``.

    Used by route handlers to validate that the asset belongs to the
    expected release before performing mutations.
    """
    stmt = select(db.MusehubReleaseAsset).where(
        db.MusehubReleaseAsset.asset_id == asset_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def remove_asset(
    session: AsyncSession,
    asset_id: str,
) -> bool:
    """Delete a release asset by its ID.

    The caller is responsible for committing the session after this call.

    Args:
        session: Active async DB session.
        asset_id: UUID of the asset to remove.

    Returns:
        ``True`` if the asset was found and deleted; ``False`` if not found.
    """
    row = await get_asset(session, asset_id)
    if row is None:
        return False
    await session.delete(row)
    logger.info("✅ Removed asset %s", asset_id)
    return True


async def get_download_stats(
    session: AsyncSession,
    release_id: str,
    tag: str,
) -> ReleaseDownloadStatsResponse:
    """Return per-asset download counts for a release.

    Args:
        session: Active async DB session.
        release_id: UUID of the release.
        tag: Version tag — echoed back in the response for convenience.

    Returns:
        ``ReleaseDownloadStatsResponse`` with per-asset counts and total.
    """
    stmt = (
        select(db.MusehubReleaseAsset)
        .where(db.MusehubReleaseAsset.release_id == release_id)
        .order_by(db.MusehubReleaseAsset.created_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    asset_counts = [
        ReleaseAssetDownloadCount(
            asset_id=row.asset_id,
            name=row.name,
            label=row.label,
            download_count=row.download_count,
        )
        for row in rows
    ]
    total = sum(a.download_count for a in asset_counts)
    return ReleaseDownloadStatsResponse(
        release_id=release_id,
        tag=tag,
        assets=asset_counts,
        total_downloads=total,
    )
