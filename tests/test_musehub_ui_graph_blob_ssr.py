"""SSR tests for the graph (DAG) page and blob viewer — issue #584.

Verifies that:
- graph_page() injects ``window.__graphData`` and renders commit/branch counts server-side.
- blob_page() renders text file content (line-numbered table) server-side.
- blob_page() renders MIDI player shell with data-midi-url.
- blob_page() renders binary download link when file is binary.
- blob_page() returns 200 with blob_found=False context when object is absent (not 404,
  since the page itself is valid — the JS fallback handles missing files).

Covers:
- test_graph_page_sets_graph_data_js_global
- test_graph_page_shows_commit_count
- test_blob_page_renders_file_content_server_side
- test_blob_page_renders_line_numbers
- test_blob_page_shows_file_size
- test_blob_page_binary_shows_download_link
- test_blob_page_midi_shows_player_shell
- test_blob_page_unknown_path_no_ssr
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubObject, MusehubRepo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OWNER = "graphblobssr584"
_SLUG = "graph-blob-ssr-584"

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_repo(db: AsyncSession) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        repo_id=str(uuid.uuid4()),
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id=str(uuid.uuid4()),
    )
    db.add(repo)
    await db.flush()
    return str(repo.repo_id)


async def _seed_commit(
    db: AsyncSession,
    repo_id: str,
    *,
    commit_id: str | None = None,
    author: str = "alice",
    message: str = "Initial commit",
    branch: str = "main",
) -> str:
    """Seed a commit row and return the commit_id."""
    cid = commit_id or (uuid.uuid4().hex + uuid.uuid4().hex)[:40]
    db.add(
        MusehubCommit(
            commit_id=cid,
            repo_id=repo_id,
            branch=branch,
            parent_ids=[],
            message=message,
            author=author,
            timestamp=datetime.now(timezone.utc),
            snapshot_id=None,
        )
    )
    await db.flush()
    return cid


async def _seed_branch(db: AsyncSession, repo_id: str, head_id: str, name: str = "main") -> None:
    """Seed a branch row."""
    db.add(MusehubBranch(repo_id=repo_id, name=name, head_commit_id=head_id))
    await db.flush()


async def _seed_object(
    db: AsyncSession,
    repo_id: str,
    *,
    path: str,
    disk_path: str,
    size_bytes: int = 0,
) -> str:
    """Seed a MusehubObject row and return its object_id."""
    oid = "sha256:" + uuid.uuid4().hex
    db.add(
        MusehubObject(
            object_id=oid,
            repo_id=repo_id,
            path=path,
            size_bytes=size_bytes,
            disk_path=disk_path,
        )
    )
    await db.flush()
    return oid


# ---------------------------------------------------------------------------
# Graph page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_page_sets_graph_data_js_global(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """window.__graphData is injected into the graph page HTML server-side.

    The DAG renderer reads this global on load to skip an extra API round-trip.
    Its presence in the initial HTML is the SSR contract for this page.
    """
    repo_id = await _seed_repo(db_session)
    cid = await _seed_commit(db_session, repo_id)
    await _seed_branch(db_session, repo_id, cid)

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/graph")
    assert response.status_code == 200
    assert "window.__graphData" in response.text


@pytest.mark.anyio
async def test_graph_page_shows_commit_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit count appears in the server-rendered HTML header.

    The count must be visible before JavaScript runs so users and crawlers see
    accurate metadata.
    """
    repo_id = await _seed_repo(db_session)
    cid1 = await _seed_commit(db_session, repo_id, message="First commit")
    cid2 = await _seed_commit(db_session, repo_id, message="Second commit")
    await _seed_branch(db_session, repo_id, cid2)

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/graph")
    assert response.status_code == 200
    # "2 commits" should appear in the SSR metadata span
    assert "2 commits" in response.text


@pytest.mark.anyio
async def test_graph_page_shows_branch_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch count appears in the server-rendered HTML header."""
    repo_id = await _seed_repo(db_session)
    cid = await _seed_commit(db_session, repo_id)
    await _seed_branch(db_session, repo_id, cid, name="main")
    await _seed_branch(db_session, repo_id, cid, name="feat/jazz")

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/graph")
    assert response.status_code == 200
    assert "2 branches" in response.text


# ---------------------------------------------------------------------------
# Blob page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blob_page_renders_file_content_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: object,
) -> None:
    """Text file content is rendered in the initial HTML without JS.

    The line-numbered table must be present in the server response body so
    non-JS clients can read file content.
    """
    import tempfile as _tempfile

    repo_id = await _seed_repo(db_session)

    # Write a real file so blob_page() can read its content.
    disk = _tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    disk.write("print('hello')\n")
    disk.close()

    try:
        await _seed_object(
            db_session,
            repo_id,
            path="main.py",
            disk_path=disk.name,
            size_bytes=16,
        )

        response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/main.py")
        assert response.status_code == 200
        body = response.text
        # File content must appear in the SSR HTML (inside the line table)
        assert "print" in body
        assert "hello" in body
    finally:
        os.unlink(disk.name)


@pytest.mark.anyio
async def test_blob_page_renders_line_numbers(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Line number anchors (#L1) are present in the server-rendered table.

    Allows direct linking to individual lines without JavaScript.
    """
    import tempfile as _tempfile

    repo_id = await _seed_repo(db_session)

    disk = _tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    disk.write("line one\nline two\n")
    disk.close()

    try:
        await _seed_object(
            db_session,
            repo_id,
            path="code.py",
            disk_path=disk.name,
            size_bytes=18,
        )

        response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/code.py")
        assert response.status_code == 200
        body = response.text
        assert 'id="L1"' in body
        assert 'href="#L1"' in body
    finally:
        os.unlink(disk.name)


@pytest.mark.anyio
async def test_blob_page_shows_file_size(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """File size appears in the server-rendered blob header.

    Users can see the file size immediately without waiting for the JS fetch.
    """
    import tempfile as _tempfile

    repo_id = await _seed_repo(db_session)

    disk = _tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    disk.write("a" * 2048)
    disk.close()

    try:
        await _seed_object(
            db_session,
            repo_id,
            path="readme.txt",
            disk_path=disk.name,
            size_bytes=2048,
        )

        response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/readme.txt")
        assert response.status_code == 200
        # filesizeformat renders 2048 bytes as "2.0 KB"
        assert "2.0 KB" in response.text
    finally:
        os.unlink(disk.name)


@pytest.mark.anyio
async def test_blob_page_binary_shows_download_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Binary files show a download link instead of a line-numbered table.

    The download link is server-rendered so non-JS clients can retrieve the
    file even when the hex-dump JS renderer is unavailable.
    """
    import tempfile as _tempfile

    repo_id = await _seed_repo(db_session)

    disk = _tempfile.NamedTemporaryFile(mode="wb", suffix=".webp", delete=False)
    disk.write(b"\x00\x01\x02\x03")
    disk.close()

    try:
        await _seed_object(
            db_session,
            repo_id,
            path="image.webp",
            disk_path=disk.name,
            size_bytes=4,
        )

        response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/image.webp")
        assert response.status_code == 200
        body = response.text
        # SSR renders binary download link, no line table
        assert "Download raw" in body or "download" in body.lower()
        assert 'id="L1"' not in body
    finally:
        os.unlink(disk.name)


@pytest.mark.anyio
async def test_blob_page_midi_shows_player_shell(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """MIDI files render a player shell with data-midi-url set server-side.

    The ``#midi-player`` div and its ``data-midi-url`` attribute must be
    present in the initial HTML so a JS MIDI player can attach without an
    extra API call to discover the raw URL.
    """
    import tempfile as _tempfile

    repo_id = await _seed_repo(db_session)

    disk = _tempfile.NamedTemporaryFile(mode="wb", suffix=".mid", delete=False)
    disk.write(b"MThd")
    disk.close()

    try:
        await _seed_object(
            db_session,
            repo_id,
            path="track.mid",
            disk_path=disk.name,
            size_bytes=4,
        )

        response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/track.mid")
        assert response.status_code == 200
        body = response.text
        assert "midi-player" in body
        assert "data-midi-url" in body
    finally:
        os.unlink(disk.name)


@pytest.mark.anyio
async def test_blob_page_unknown_path_no_ssr(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A path with no matching object returns 200 with blob_found=false.

    The page shell renders but no SSR blob content is present — the JS
    fallback fetches metadata and shows an appropriate error.  We do NOT
    raise a 404 at the UI layer so that the page chrome (nav, breadcrumb)
    stays intact for the user.
    """
    await _seed_repo(db_session)

    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/blob/main/nonexistent.py")
    assert response.status_code == 200
    # No SSR blob content block when object is absent — the id="blob-ssr-content" div
    # must NOT appear (note: the string 'blob-ssr-content' may appear in JS code).
    assert 'id="blob-ssr-content"' not in response.text
    # JS guard variable must be set to false so the JS fallback runs
    assert "ssrBlobRendered = false" in response.text
