"""Muse Hub release packager — builds download package URL maps for releases.

A release exposes music files in multiple formats so musicians and listeners
can download the composition in their preferred format:

- ``midi_bundle``  — full arrangement as a single MIDI file
- ``stems``        — individual track stems as a zip of .mid files
- ``mp3``          — full mix audio render
- ``musicxml``     — notation export in MusicXML format
- ``metadata``     — JSON manifest with tempo, key, and arrangement info

At MVP these URLs are deterministic paths served by the object download
endpoint (``GET /api/v1/musehub/repos/{repo_id}/objects/{object_id}/content``).
For releases that don't have a pinned commit or whose commit has no stored
objects, the URLs are ``None`` — the frontend renders "not available" for those
entries.

This module contains NO database access. It operates purely on the release
and repo identifiers to construct URL strings.
"""
from __future__ import annotations

from maestro.models.musehub import ReleaseDownloadUrls

_BASE_API = "/api/v1/musehub"


def build_download_urls(
    repo_id: str,
    release_id: str,
    *,
    has_midi: bool = False,
    has_stems: bool = False,
    has_mp3: bool = False,
    has_musicxml: bool = False,
) -> ReleaseDownloadUrls:
    """Construct download package URLs for a release.

    Each URL points to a dedicated release-package download endpoint.
    Parameters control which package types are actually available for this
    release — callers should set them based on what stored objects exist for
    the pinned commit.

    Args:
        repo_id: The UUID of the Muse Hub repo.
        release_id: The UUID of the release row.
        has_midi: True if a full MIDI bundle is available.
        has_stems: True if per-track stem files are available.
        has_mp3: True if an MP3 render is available.
        has_musicxml: True if a MusicXML export is available.

    Returns:
        ``ReleaseDownloadUrls`` with URL strings for available packages and
        ``None`` for unavailable ones. The metadata JSON is always available
        when any other package is available.
    """
    base = f"{_BASE_API}/repos/{repo_id}/releases/{release_id}/packages"
    has_any = has_midi or has_stems or has_mp3 or has_musicxml

    return ReleaseDownloadUrls(
        midi_bundle=f"{base}/midi" if has_midi else None,
        stems=f"{base}/stems" if has_stems else None,
        mp3=f"{base}/mp3" if has_mp3 else None,
        musicxml=f"{base}/musicxml" if has_musicxml else None,
        metadata=f"{base}/metadata" if has_any else None,
    )


def build_empty_download_urls() -> ReleaseDownloadUrls:
    """Return a ``ReleaseDownloadUrls`` with all fields set to ``None``.

    Used when a release has no pinned commit or the commit has no stored
    objects yet. Clients should render "not available" for all packages.
    """
    return ReleaseDownloadUrls()
