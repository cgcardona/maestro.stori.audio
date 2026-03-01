"""JSON-LD structured data helpers for Muse Hub UI pages.

Produces machine-readable schema.org metadata for repo landing pages
(MusicComposition) and release detail pages (MusicRecording).  Injecting
JSON-LD makes these pages visible to search engines and music discovery
services that consume schema.org data without requiring any API keys or
crawl-budget negotiation.

Both helper functions are pure (no I/O, no side effects) so they can be
called from any route handler without blocking the event loop.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maestro.models.musehub import ReleaseResponse, RepoResponse

logger = logging.getLogger(__name__)

# Schema.org context URL — standard prefix for all structured data types.
_SCHEMA_CONTEXT = "https://schema.org"


def jsonld_repo(repo: RepoResponse, page_url: str) -> dict[str, object]:
    """Return a schema.org/MusicComposition JSON-LD dict for a repo.

    MusicComposition maps well to a Muse Hub repo because a repo represents
    a versioned musical work — it has a name, creator, genre tags, and a
    creation date.  Search engines and music discovery bots index this data
    to surface repos in relevant queries.

    Args:
        repo: Full repo response model (owner, name, description, tags, etc.).
        page_url: Canonical absolute URL of the repo landing page,
                  e.g. ``https://musehub.stori.com/musehub/ui/miles/kind-of-blue``.

    Returns:
        JSON-LD dict ready for ``json.dumps()`` and embedding in a
        ``<script type="application/ld+json">`` tag.
    """
    data: dict[str, object] = {
        "@context": _SCHEMA_CONTEXT,
        "@type": "MusicComposition",
        "name": repo.name,
        "description": repo.description or "",
        "url": page_url,
        "dateCreated": repo.created_at.isoformat(),
        "creator": {
            "@type": "Person",
            "name": repo.owner,
        },
    }

    if repo.tags:
        data["genre"] = repo.tags

    if repo.key_signature:
        data["musicalKey"] = repo.key_signature

    if repo.tempo_bpm is not None:
        data["tempo"] = str(repo.tempo_bpm)

    return data


def jsonld_release(
    release: ReleaseResponse,
    repo: RepoResponse,
    page_url: str,
) -> dict[str, object]:
    """Return a schema.org/MusicRecording JSON-LD dict for a release.

    MusicRecording represents a specific recorded version of a composition —
    analogous to a Muse Hub release (a tagged snapshot with download packages).
    Linking MusicRecording back to its parent MusicComposition (the repo) lets
    indexers understand the work hierarchy.

    Args:
        release: Full release response model (tag, title, body, author, etc.).
        repo: Parent repo (used to populate ``inAlbum`` and ``byArtist``).
        page_url: Canonical absolute URL of the release detail page,
                  e.g. ``https://musehub.stori.com/musehub/ui/miles/kind-of-blue/releases/v1.0``.

    Returns:
        JSON-LD dict ready for ``json.dumps()`` and embedding in a
        ``<script type="application/ld+json">`` tag.
    """
    data: dict[str, object] = {
        "@context": _SCHEMA_CONTEXT,
        "@type": "MusicRecording",
        "name": release.title or release.tag,
        "description": release.body or "",
        "url": page_url,
        "datePublished": release.created_at.isoformat(),
        "byArtist": {
            "@type": "Person",
            "name": release.author or repo.owner,
        },
        "inAlbum": {
            "@type": "MusicAlbum",
            "name": repo.name,
        },
    }

    if repo.tags:
        data["genre"] = repo.tags

    return data


def render_jsonld_script(data: dict[str, object]) -> str:
    """Render a JSON-LD dict as a safe ``<script type="application/ld+json">`` tag.

    Uses ``json.dumps`` with ``ensure_ascii=False`` to preserve Unicode characters
    in musical titles, and escapes ``</script>`` sequences to prevent XSS via
    template injection.

    Args:
        data: JSON-LD dict from ``jsonld_repo`` or ``jsonld_release``.

    Returns:
        A complete ``<script>`` tag string, safe for verbatim insertion into HTML.
    """
    serialised = json.dumps(data, ensure_ascii=False, default=str)
    # Prevent premature </script> tag termination — a known JSON-in-HTML XSS vector.
    serialised = serialised.replace("</", r"<\/")
    return f'<script type="application/ld+json">{serialised}</script>'
