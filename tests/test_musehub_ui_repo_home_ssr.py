"""SSR tests for the MuseHub repo home page (issue #575).

Covers GET /musehub/ui/{owner}/{repo_slug} after SSR migration:

- test_repo_home_renders_repo_description_server_side
    Seed repo with description, GET home, assert description in HTML body.

- test_repo_home_renders_file_tree_server_side
    Seed a file tree entry via a commit object, assert filename in HTML.

- test_repo_home_branch_picker_has_hx_get
    Branch select form carries ``hx-get`` attribute pointing to repo base URL.

- test_repo_home_htmx_fragment_on_branch_switch
    GET with ``HX-Request: true`` → file tree fragment (no <html> wrapper).

- test_repo_home_shows_tempo_bpm
    Repo with tempo_bpm set → BPM value appears in sidebar HTML.

- test_repo_home_empty_tree_shows_empty_state
    Empty repo (no objects) → empty state message in HTML.

- test_repo_home_json_format_returns_json
    GET with ``?format=json`` → JSON response with camelCase keys.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubObject, MusehubRepo

pytestmark = pytest.mark.anyio

_OWNER = "ssr-home-owner"
_SLUG = "ssr-home-repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    *,
    description: str = "A test music repo",
    key_signature: str | None = None,
    tempo_bpm: int | None = None,
) -> str:
    """Seed a minimal public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id="ssr-home-owner-uid",
        description=description,
        key_signature=key_signature,
        tempo_bpm=tempo_bpm,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _add_object(
    db: AsyncSession,
    repo_id: str,
    path: str,
    *,
    size_bytes: int = 1024,
) -> None:
    """Seed a MusehubObject so the file tree has entries."""
    import uuid

    obj = MusehubObject(
        object_id=f"sha256:{uuid.uuid4().hex}",
        repo_id=repo_id,
        path=path,
        size_bytes=size_bytes,
        disk_path=f"/tmp/test/{path}",
    )
    db.add(obj)
    await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_repo_home_renders_repo_description_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seed a repo with a description, GET the home page, assert description in HTML.

    The SSR migration means the description must be in the initial HTML response —
    not fetched by JavaScript after page load.
    """
    description = "Jazz standards arranged for modern quartet"
    await _make_repo(db_session, description=description)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}")
    assert resp.status_code == 200
    assert description in resp.text


async def test_repo_home_renders_file_tree_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seed a file object, GET the home page, assert filename appears in HTML.

    The SSR migration means the file tree is rendered server-side.
    This test fails if the handler omits ``tree`` from the template context.
    """
    repo_id = await _make_repo(db_session)
    await _add_object(db_session, repo_id, "bass_line.mid")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}")
    assert resp.status_code == 200
    assert "bass_line.mid" in resp.text


async def test_repo_home_branch_picker_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch picker form carries ``hx-get`` for HTMX branch switching.

    The form submits via HTMX (not a full page reload), targeting ``#file-tree``
    so only the file tree updates when the user switches branches.
    """
    await _make_repo(db_session)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}")
    assert resp.status_code == 200
    assert "hx-get" in resp.text


async def test_repo_home_htmx_fragment_on_branch_switch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with ``HX-Request: true`` returns only the bare file tree fragment.

    The fragment must not contain a full HTML document shell (<html>, <head>)
    — it is swapped directly into ``#file-tree`` by HTMX on branch change.
    """
    repo_id = await _make_repo(db_session)
    await _add_object(db_session, repo_id, "melody.mid")

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "melody.mid" in body
    assert "<html" not in body
    assert "<head" not in body


async def test_repo_home_shows_tempo_bpm(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo with tempo_bpm set → BPM value appears in the rendered HTML sidebar.

    The SSR migration must pass repo metadata to the template so music-specific
    properties (key, tempo) are visible without a client-side API call.
    """
    await _make_repo(db_session, tempo_bpm=132)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}")
    assert resp.status_code == 200
    assert "132" in resp.text
    assert "BPM" in resp.text


async def test_repo_home_empty_tree_shows_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Empty repo (no objects) renders an empty state message in the file tree area.

    The file_tree fragment falls through to the empty_state macro when
    ``tree`` is an empty list.
    """
    await _make_repo(db_session)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}")
    assert resp.status_code == 200
    # empty_state macro renders an icon + "Empty repository" message
    assert "Empty repository" in resp.text


async def test_repo_home_json_format_returns_json(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with ``?format=json`` returns a JSON response with camelCase keys.

    The JSON shortcut preserves backward compatibility for API consumers
    (agents, curl scripts) that rely on the structured repo data.
    """
    description = "Jazz standards for JSON test"
    await _make_repo(db_session, description=description)

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}?format=json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert data["slug"] == _SLUG
    assert data["owner"] == _OWNER
    assert data["description"] == description
