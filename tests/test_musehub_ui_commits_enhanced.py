"""Regression tests for the enhanced commits list page (issue #446).

Covers the four feature areas added to commits_list_page():

Filter bar
- test_commits_enhanced_filter_bar_present          — filter-bar HTML element present
- test_commits_enhanced_author_dropdown_present      — author <select> with 'All authors' default
- test_commits_enhanced_date_picker_inputs_present   — dateFrom / dateTo date inputs present
- test_commits_enhanced_search_input_present         — message search <input> present
- test_commits_enhanced_tag_filter_input_present     — tag filter <input> present

Server-side filtering
- test_commits_enhanced_author_filter_narrows_results — ?author= returns only that author's commits
- test_commits_enhanced_author_filter_excludes_others — commits by other authors absent
- test_commits_enhanced_search_filter_matches_message — ?q= matches substring in commit message
- test_commits_enhanced_search_filter_excludes_others — non-matching commits absent
- test_commits_enhanced_date_from_filter             — ?dateFrom= excludes older commits
- test_commits_enhanced_tag_filter_matches_tag       — ?tag=emotion:funky matches message substring

Compare mode
- test_commits_enhanced_compare_toggle_btn_present   — compare-toggle-btn button present
- test_commits_enhanced_compare_strip_present        — compare-strip container present
- test_commits_enhanced_compare_check_inputs_present — compare-check checkboxes per row
- test_commits_enhanced_compare_js_function          — toggleCompareMode() JS function present

Metadata badges (client-side JS)
- test_commits_enhanced_meta_badges_container_present — meta-badges span present per row
- test_commits_enhanced_badge_js_extract_function    — extractBadges() JS function present
- test_commits_enhanced_chip_css_classes_present     — chip-tempo / chip-key / chip-emotion CSS defined

Mini-lane
- test_commits_enhanced_dag_merge_arm_present        — dag-merge-arm element on merge commits
- test_commits_enhanced_mini_lane_dag_col_present    — dag-col column present

Pagination with active filters
- test_commits_enhanced_pagination_preserves_filters — page links carry active filter params
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubRepo

# ── Constants ─────────────────────────────────────────────────────────────────

_OWNER = "enhancedowner"
_SLUG = "enhanced-commits"

_SHA_ALICE_1 = "a1" + "0" * 38
_SHA_ALICE_2 = "a2" + "0" * 38
_SHA_BOB_1   = "b1" + "0" * 38
_SHA_MERGE   = "cc" + "0" * 38

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_repo(db: AsyncSession) -> str:
    """Seed a public repo with 4 commits from 2 authors and return repo_id."""
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
    repo_id = str(repo.repo_id)

    branch = MusehubBranch(repo_id=repo_id, name="main", head_commit_id=_SHA_MERGE)
    db.add(branch)

    # Alice: two commits with music metadata in messages
    db.add(MusehubCommit(
        commit_id=_SHA_ALICE_1,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Add walking bass line 120 BPM Cm emotion:funky",
        author="alice",
        timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc),
    ))
    db.add(MusehubCommit(
        commit_id=_SHA_ALICE_2,
        repo_id=repo_id,
        branch="main",
        parent_ids=[_SHA_ALICE_1],
        message="Refine rhodes chord voicings stage:chorus",
        author="alice",
        timestamp=datetime(2026, 2, 15, tzinfo=timezone.utc),
    ))
    # Bob: one commit
    db.add(MusehubCommit(
        commit_id=_SHA_BOB_1,
        repo_id=repo_id,
        branch="main",
        parent_ids=[_SHA_ALICE_2],
        message="Add jazz drums groove 90 BPM Gm",
        author="bob",
        timestamp=datetime(2026, 3, 1, tzinfo=timezone.utc),
    ))
    # Merge commit
    db.add(MusehubCommit(
        commit_id=_SHA_MERGE,
        repo_id=repo_id,
        branch="main",
        parent_ids=[_SHA_ALICE_2, _SHA_BOB_1],
        message="Merge feat/drums into main",
        author="alice",
        timestamp=datetime(2026, 3, 2, tzinfo=timezone.utc),
    ))

    await db.commit()
    return repo_id


def _url(path: str = "") -> str:
    return f"/musehub/ui/{_OWNER}/{_SLUG}/commits{path}"


# ── Filter bar HTML ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_filter_bar_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """filter-bar container is rendered on the commits list page."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "filter-bar" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_author_dropdown_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Author <select> dropdown is present and includes 'All authors' option."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "All authors" in resp.text
    # Both authors appear as options
    assert "alice" in resp.text
    assert "bob" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_date_picker_inputs_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """dateFrom and dateTo date inputs are present in the filter bar."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert 'type="date"' in resp.text
    assert "dateFrom" in resp.text
    assert "dateTo" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_search_input_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Full-text message search input is present in the filter bar."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert 'name="q"' in resp.text
    assert "keyword in message" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_tag_filter_input_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Tag filter input is present in the filter bar."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert 'name="tag"' in resp.text
    assert "emotion:" in resp.text  # placeholder hint text


# ── Server-side filtering ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_author_filter_narrows_results(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?author=bob returns only bob's commits."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?author=bob")
    assert resp.status_code == 200
    assert _SHA_BOB_1[:8] in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_author_filter_excludes_others(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Commits by authors other than the filtered author do not appear."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?author=bob")
    assert resp.status_code == 200
    # Alice's commit SHA should not appear
    assert _SHA_ALICE_1[:8] not in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_search_filter_matches_message(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?q=walking+bass returns the commit containing that substring."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?q=walking+bass")
    assert resp.status_code == 200
    assert _SHA_ALICE_1[:8] in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_search_filter_excludes_others(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?q= excludes commits that do not match the search term."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?q=walking+bass")
    assert resp.status_code == 200
    # Bob's drums commit should not appear
    assert _SHA_BOB_1[:8] not in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_date_from_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?dateFrom=2026-03-01 excludes commits before that date."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?dateFrom=2026-03-01")
    assert resp.status_code == 200
    # Only Bob (2026-03-01) and merge (2026-03-02) should appear
    assert _SHA_BOB_1[:8] in resp.text
    assert _SHA_MERGE[:8] in resp.text
    # Alice's January commit should not appear
    assert _SHA_ALICE_1[:8] not in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_tag_filter_matches_tag(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?tag=emotion:funky matches the commit containing that tag string."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?tag=emotion%3Afunky")
    assert resp.status_code == 200
    assert _SHA_ALICE_1[:8] in resp.text
    # The commit without the tag should not appear
    assert _SHA_BOB_1[:8] not in resp.text


# ── Compare mode ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_compare_toggle_btn_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Compare toggle button is present in the toolbar."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "compare-toggle-btn" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_compare_strip_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """compare-strip container is present (initially hidden via CSS/JS)."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "compare-strip" in resp.text
    assert "compare-link" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_compare_check_inputs_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Per-row compare checkboxes are rendered for each commit."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "compare-check" in resp.text
    assert "compare-col" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_compare_js_function(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """toggleCompareMode() and onCompareCheck() JS functions are defined."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "toggleCompareMode" in resp.text
    assert "onCompareCheck" in resp.text
    assert "updateCompareStrip" in resp.text


# ── Metadata badge JS ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_meta_badges_container_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """meta-badges span is rendered inside each commit row."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "meta-badges" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_badge_js_extract_function(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """extractBadges() and renderBadges() JS functions are defined."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "extractBadges" in resp.text
    assert "renderBadges" in resp.text
    assert "TEMPO_RE" in resp.text
    assert "EMOTION_RE" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_chip_css_classes_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """CSS classes for metadata chips are defined in the page <style>."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "chip-tempo" in resp.text
    assert "chip-key" in resp.text
    assert "chip-emotion" in resp.text
    assert "chip-stage" in resp.text
    assert "chip-instr" in resp.text


# ── Mini-lane DAG ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_dag_merge_arm_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """dag-merge-arm element is rendered for merge commits."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "dag-merge-arm" in resp.text


@pytest.mark.anyio
async def test_commits_enhanced_mini_lane_dag_col_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """dag-col column is rendered for every commit row."""
    await _seed_repo(db_session)
    resp = await client.get(_url())
    assert resp.status_code == 200
    assert "dag-col" in resp.text
    assert "dag-node" in resp.text


# ── Pagination preserves filters ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_commits_enhanced_pagination_preserves_filters(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Pagination links forward active filter params so state persists across pages."""
    await _seed_repo(db_session)
    resp = await client.get(_url() + "?author=alice&per_page=1&page=1")
    assert resp.status_code == 200
    body = resp.text
    # Older link should carry author=alice
    assert "author=alice" in body
    assert "page=2" in body
