"""Unit tests for the MuseHub JSON-LD structured data helpers.

Covers issue #454 — jsonld_repo and jsonld_release produce valid
schema.org/MusicComposition and schema.org/MusicRecording dicts.
render_jsonld_script produces a safe, well-formed <script> tag.

Tests:
- test_jsonld_repo_returns_music_composition_type
- test_jsonld_repo_includes_required_fields
- test_jsonld_repo_includes_genre_when_tags_present
- test_jsonld_repo_omits_genre_when_no_tags
- test_jsonld_repo_includes_key_signature_when_present
- test_jsonld_repo_includes_tempo_when_present
- test_jsonld_repo_omits_key_and_tempo_when_absent
- test_jsonld_release_returns_music_recording_type
- test_jsonld_release_includes_required_fields
- test_jsonld_release_includes_genre_from_repo_tags
- test_jsonld_release_falls_back_to_repo_owner_when_no_author
- test_jsonld_release_uses_tag_when_title_empty
- test_render_jsonld_script_wraps_in_script_tag
- test_render_jsonld_script_escapes_closing_tag_xss
- test_render_jsonld_script_preserves_unicode
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from maestro.api.routes.musehub.ui_jsonld import (
    jsonld_release,
    jsonld_repo,
    render_jsonld_script,
)
from maestro.models.musehub import ReleaseDownloadUrls, ReleaseResponse, RepoResponse


# ---------------------------------------------------------------------------
# Fixtures — minimal valid model instances
# ---------------------------------------------------------------------------


def _make_repo(
    *,
    name: str = "Kind of Blue",
    owner: str = "miles_davis",
    description: str = "Landmark modal jazz album",
    tags: list[str] | None = None,
    key_signature: str | None = None,
    tempo_bpm: int | None = None,
) -> RepoResponse:
    """Build a minimal RepoResponse for testing."""
    return RepoResponse(
        repo_id="aabbccdd",
        name=name,
        owner=owner,
        slug="kind-of-blue",
        visibility="public",
        owner_user_id="user-uuid-123",
        clone_url="https://musehub.stori.com/api/v1/musehub/repos/aabbccdd",
        description=description,
        tags=tags or [],
        key_signature=key_signature,
        tempo_bpm=tempo_bpm,
        created_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_release(
    *,
    tag: str = "v1.0",
    title: str = "First Release",
    body: str = "Initial recording session.",
    author: str = "miles_davis",
) -> ReleaseResponse:
    """Build a minimal ReleaseResponse for testing."""
    return ReleaseResponse(
        release_id="release-uuid-456",
        tag=tag,
        title=title,
        body=body,
        commit_id=None,
        download_urls=ReleaseDownloadUrls(),
        author=author,
        created_at=datetime(2024, 3, 1, 9, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# jsonld_repo — MusicComposition
# ---------------------------------------------------------------------------


def test_jsonld_repo_returns_music_composition_type() -> None:
    """JSON-LD for a repo always declares @type MusicComposition."""
    repo = _make_repo()
    data = jsonld_repo(repo, "https://example.com/musehub/ui/miles/kind-of-blue")
    assert data["@type"] == "MusicComposition"
    assert data["@context"] == "https://schema.org"


def test_jsonld_repo_includes_required_fields() -> None:
    """Repo JSON-LD includes name, description, url, dateCreated, creator."""
    repo = _make_repo()
    url = "https://example.com/musehub/ui/miles/kind-of-blue"
    data = jsonld_repo(repo, url)

    assert data["name"] == "Kind of Blue"
    assert data["description"] == "Landmark modal jazz album"
    assert data["url"] == url
    assert "2024-01-15" in str(data["dateCreated"])
    creator = data["creator"]
    assert isinstance(creator, dict)
    assert creator["@type"] == "Person"
    assert creator["name"] == "miles_davis"


def test_jsonld_repo_includes_genre_when_tags_present() -> None:
    """Tags are mapped to the genre field when the repo has tags."""
    repo = _make_repo(tags=["jazz", "modal", "F minor"])
    data = jsonld_repo(repo, "https://example.com/repo")
    assert data["genre"] == ["jazz", "modal", "F minor"]


def test_jsonld_repo_omits_genre_when_no_tags() -> None:
    """The genre field is absent when the repo has no tags."""
    repo = _make_repo(tags=[])
    data = jsonld_repo(repo, "https://example.com/repo")
    assert "genre" not in data


def test_jsonld_repo_includes_key_signature_when_present() -> None:
    """musicalKey is populated when repo has a key_signature."""
    repo = _make_repo(key_signature="Bb major")
    data = jsonld_repo(repo, "https://example.com/repo")
    assert data["musicalKey"] == "Bb major"


def test_jsonld_repo_includes_tempo_when_present() -> None:
    """tempo is populated (as a string) when repo has tempo_bpm."""
    repo = _make_repo(tempo_bpm=120)
    data = jsonld_repo(repo, "https://example.com/repo")
    assert data["tempo"] == "120"


def test_jsonld_repo_omits_key_and_tempo_when_absent() -> None:
    """musicalKey and tempo are absent when the model fields are None."""
    repo = _make_repo(key_signature=None, tempo_bpm=None)
    data = jsonld_repo(repo, "https://example.com/repo")
    assert "musicalKey" not in data
    assert "tempo" not in data


# ---------------------------------------------------------------------------
# jsonld_release — MusicRecording
# ---------------------------------------------------------------------------


def test_jsonld_release_returns_music_recording_type() -> None:
    """JSON-LD for a release always declares @type MusicRecording."""
    release = _make_release()
    repo = _make_repo()
    data = jsonld_release(release, repo, "https://example.com/release/v1.0")
    assert data["@type"] == "MusicRecording"
    assert data["@context"] == "https://schema.org"


def test_jsonld_release_includes_required_fields() -> None:
    """Release JSON-LD includes name, description, url, datePublished, byArtist, inAlbum."""
    release = _make_release()
    repo = _make_repo()
    url = "https://example.com/musehub/ui/miles/kind-of-blue/releases/v1.0"
    data = jsonld_release(release, repo, url)

    assert data["name"] == "First Release"
    assert data["description"] == "Initial recording session."
    assert data["url"] == url
    assert "2024-03-01" in str(data["datePublished"])

    artist = data["byArtist"]
    assert isinstance(artist, dict)
    assert artist["@type"] == "Person"
    assert artist["name"] == "miles_davis"

    album = data["inAlbum"]
    assert isinstance(album, dict)
    assert album["@type"] == "MusicAlbum"
    assert album["name"] == "Kind of Blue"


def test_jsonld_release_includes_genre_from_repo_tags() -> None:
    """Release JSON-LD inherits genre from the parent repo's tags."""
    release = _make_release()
    repo = _make_repo(tags=["bebop", "quintet"])
    data = jsonld_release(release, repo, "https://example.com/release")
    assert data["genre"] == ["bebop", "quintet"]


def test_jsonld_release_falls_back_to_repo_owner_when_no_author() -> None:
    """byArtist falls back to repo.owner when release.author is empty."""
    release = _make_release(author="")
    repo = _make_repo(owner="coltrane")
    data = jsonld_release(release, repo, "https://example.com/release")
    artist = data["byArtist"]
    assert isinstance(artist, dict)
    assert artist["name"] == "coltrane"


def test_jsonld_release_uses_tag_when_title_empty() -> None:
    """name falls back to the release tag when title is an empty string."""
    release = _make_release(title="", tag="v2.0-beta")
    repo = _make_repo()
    data = jsonld_release(release, repo, "https://example.com/release")
    assert data["name"] == "v2.0-beta"


# ---------------------------------------------------------------------------
# render_jsonld_script
# ---------------------------------------------------------------------------


def test_render_jsonld_script_wraps_in_script_tag() -> None:
    """Rendered output is a <script type="application/ld+json"> tag."""
    data: dict[str, object] = {"@context": "https://schema.org", "@type": "MusicComposition", "name": "Test"}
    script = render_jsonld_script(data)
    assert script.startswith('<script type="application/ld+json">')
    assert script.endswith("</script>")


def test_render_jsonld_script_contains_valid_json() -> None:
    """The content inside the script tag is valid JSON matching the input dict."""
    data: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "MusicComposition",
        "name": "Blue in Green",
    }
    script = render_jsonld_script(data)
    inner = script.removeprefix('<script type="application/ld+json">').removesuffix("</script>")
    parsed = json.loads(inner)
    assert parsed["name"] == "Blue in Green"
    assert parsed["@type"] == "MusicComposition"


def test_render_jsonld_script_escapes_closing_tag_xss() -> None:
    """</script> sequences inside JSON values are escaped to prevent XSS."""
    data: dict[str, object] = {
        "@context": "https://schema.org",
        "name": "Attack</script><script>alert(1)//",
    }
    script = render_jsonld_script(data)
    # Extract only the JSON content between the opening and closing script tags.
    inner = script.removeprefix('<script type="application/ld+json">').removesuffix("</script>")
    # The closing tag sequence must not appear verbatim inside the JSON payload.
    assert "</script>" not in inner


def test_render_jsonld_script_preserves_unicode() -> None:
    """Non-ASCII characters (e.g. accented, CJK) are preserved without escaping."""
    data: dict[str, object] = {
        "@context": "https://schema.org",
        "name": "Caf\u00e9 M\u00fasica \u3084\u307e\u3068",
    }
    script = render_jsonld_script(data)
    assert "Caf\u00e9" in script
    assert "\u3084\u307e\u3068" in script
