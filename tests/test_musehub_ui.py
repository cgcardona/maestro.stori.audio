"""Tests for Muse Hub web UI endpoints.

Covers issue #217 (compare view):
- test_compare_page_renders         — GET /musehub/ui/{owner}/{slug}/compare/{base}...{head} returns 200
- test_compare_page_no_auth_required — compare page accessible without JWT
- test_compare_page_invalid_ref_404 — refs without ... separator return 404
- test_compare_page_unknown_owner_404 — unknown owner/slug returns 404
- test_compare_page_includes_radar  — page contains radar chart JavaScript
- test_compare_page_includes_piano_roll — page contains piano roll JS
- test_compare_page_includes_emotion_diff — page contains emotion diff elements
- test_compare_page_includes_commit_list — page contains commit list JS
- test_compare_page_includes_create_pr_button — page contains "Create Pull Request"
- test_compare_json_response        — ?format=json returns structured context
- test_compare_unknown_ref_404      — unknown ref returns 404


Covers acceptance criteria from issue #206 (commit list page):
- test_commits_list_page_returns_200              — GET /{owner}/{repo}/commits returns HTML
- test_commits_list_page_shows_commit_sha        — SHA of seeded commit appears in page
- test_commits_list_page_shows_commit_message    — message appears in page
- test_commits_list_page_dag_indicator           — DAG node element present
- test_commits_list_page_pagination_links        — Older/Newer nav links present when multi-page
- test_commits_list_page_branch_selector         — branch <select> present when branches exist
- test_commits_list_page_json_content_negotiation — ?format=json returns CommitListResponse
- test_commits_list_page_json_pagination         — ?format=json&per_page=1&page=2 returns page 2
- test_commits_list_page_branch_filter_html      — ?branch=main filters to that branch
- test_commits_list_page_empty_state             — repo with no commits shows empty state
- test_commits_list_page_merge_indicator         — merge commit shows merge indicator
- test_commits_list_page_graph_link              — link to DAG graph page present

Covers the minimum acceptance criteria from issue #43 and issue #232:
- test_ui_repo_page_returns_200        — GET /musehub/ui/{repo_id} returns HTML
- test_ui_commit_page_shows_artifact_links — commit page HTML mentions img/download
- test_ui_pr_list_page_returns_200     — PR list page renders without error
- test_ui_issue_list_page_returns_200          — Issue list page renders without error
- test_ui_issue_list_has_open_closed_tabs      — Open/Closed tab buttons present (#299)
- test_ui_issue_list_has_sort_controls         — Sort buttons (newest/oldest/most-commented) present (#299)
- test_ui_issue_list_has_label_filter_js       — Client-side label filter JS present (#299)
- test_ui_issue_list_has_body_preview_js       — Body preview helper and CSS class present (#299)
- test_ui_issue_detail_has_comment_section     — Comment thread section below issue body (#289)
- test_ui_issue_detail_has_render_comments_js  — buildCommentThread() renders the comment thread (#289)
- test_ui_issue_detail_has_submit_comment_js   — submitComment() posts new comments (#289)
- test_ui_issue_detail_has_delete_comment_js   — deleteComment() removes own comments (#289)
- test_ui_issue_detail_has_reply_support_js    — startReply() enables threaded replies (#289)
- test_ui_issue_detail_comment_section_below_body — comment section follows issue body card (#289)
- test_ui_pr_list_has_comment_badge_js         — PR list has comment count badge JS (#298)
- test_ui_pr_list_has_reaction_pills_js        — PR list has reaction pills JS (#298)
- test_ui_issue_list_has_reaction_pills_js     — Issue list has reaction pills JS (#298)
- test_ui_issue_list_eager_social_signals      — Issue list eagerly pre-fetches social signals (#298)
- test_context_page_renders            — context viewer page returns 200 HTML
- test_context_json_response           — JSON returns MuseHubContextResponse structure
- test_context_includes_musical_state  — response includes active_tracks field
- test_context_unknown_ref_404         — nonexistent ref returns 404

Covers acceptance criteria from issue #204 (tree browser):
- test_tree_root_lists_directories
- test_tree_subdirectory_lists_files
- test_tree_file_icons_by_type
- test_tree_breadcrumbs_correct
- test_tree_json_response
- test_tree_unknown_ref_404

Covers acceptance criteria from issue #244 (embed player):
- test_embed_page_renders              — GET /musehub/ui/{repo_id}/embed/{ref} returns 200
- test_embed_no_auth_required          — Public embed accessible without JWT
- test_embed_page_x_frame_options      — Response sets X-Frame-Options: ALLOWALL
- test_embed_page_contains_player_ui   — Player elements present in embed HTML

Covers issue #227 (emotion map page), migrated to owner/slug routing in #348:
- test_emotion_page_renders            — GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/emotion returns 200
- test_emotion_page_no_auth_required   — emotion UI page accessible without JWT
- test_emotion_page_includes_charts    — page embeds valence-arousal plot and axis labels
- test_emotion_page_includes_filters   — page includes primary emotion and confidence display
- test_emotion_json_response           — JSON endpoint returns emotion map with required fields
- test_emotion_trajectory              — cross-commit trajectory data is present and ordered
- test_emotion_drift_distances         — drift list has one entry per consecutive commit pair

Covers issue #292 (rich event cards in activity feed):
- test_feed_page_returns_200                 — GET /musehub/ui/feed returns 200 HTML
- test_feed_page_no_raw_json_payload         — page does not render raw JSON.stringify of payload
- test_feed_page_has_event_meta_for_all_types — EVENT_META covers all 8 event types
- test_feed_page_has_data_notif_id_attribute  — cards carry data-notif-id for mark-as-read hook
- test_feed_page_has_unread_indicator        — unread highlight border logic present
- test_feed_page_has_actor_avatar_logic      — actorAvatar / actorHsl helper present
- test_feed_page_has_relative_timestamp      — fmtRelative called in card rendering

Covers issue #293 (mark-as-read UX in activity feed):
- test_feed_page_has_mark_one_read_function          — markOneRead() defined for per-card action
- test_feed_page_has_mark_all_read_function          — markAllRead() defined for bulk action
- test_feed_page_has_decrement_nav_badge_function    — decrementNavBadge() keeps badge in sync
- test_feed_page_mark_read_btn_targets_notification_endpoint — calls POST /notifications/{id}/read
- test_feed_page_mark_all_btn_targets_read_all_endpoint      — calls POST /notifications/read-all
- test_feed_page_mark_all_btn_present_in_template    — mark-all-read-btn element in page HTML
- test_feed_page_mark_read_updates_nav_badge         — nav-notif-badge updated after mark-all

UI routes require no JWT auth (they return HTML shells whose JS handles auth).
The HTML content tests assert structural markers present in every rendered page.

Covers issue #290 (release detail comment threads):
- test_ui_release_detail_has_comment_section        — Discussion section present in HTML
- test_ui_release_detail_has_render_comments_js     — renderComments/submitComment/deleteComment JS present
- test_ui_release_detail_comment_uses_release_target_type — target_type='release' used in JS
- test_ui_release_detail_has_reply_thread_js        — toggleReplyForm/submitReply for thread support

Covers issue #286 (commit comment threads):
- test_commit_page_has_comment_section_html     — comments-section container present in HTML
- test_commit_page_has_comment_js_functions     — renderComments/submitComment/deleteComment/loadComments JS present
- test_commit_page_comment_calls_load_on_startup — loadComments() called at page startup
- test_commit_page_comment_uses_correct_api_path — fetches /comments?target_type=commit
- test_commit_page_comment_has_avatar_logic     — avatarColor() HSL helper present
- test_commit_page_comment_has_new_comment_form — new-comment textarea form present
- test_commit_page_comment_has_discussion_heading — "Discussion" heading present

Covers regression for PR #282 (owner/slug URL scheme):
- test_ui_nav_links_use_owner_slug_not_uuid_*  — every page handler injects
  ``const base = '/musehub/ui/{owner}/{slug}'`` not a UUID-based path.
- test_ui_unknown_owner_slug_returns_404        — bad owner/slug → 404.

Covers issue #221 (analysis dashboard):
- test_analysis_dashboard_renders               — GET /musehub/ui/{owner}/{slug}/analysis/{ref} returns 200
- test_analysis_dashboard_no_auth_required      — accessible without JWT
- test_analysis_dashboard_all_dimension_labels  — 10 dimension labels present in HTML
- test_analysis_dashboard_sparkline_logic_present — sparkline JS present
- test_analysis_dashboard_card_links_to_dimensions — /analysis/ path in page
See also test_musehub_analysis.py::test_analysis_aggregate_endpoint_returns_all_dimensions

Covers issue #208 (branch list and tag browser):
- test_branches_page_lists_all          — GET /musehub/ui/{owner}/{slug}/branches returns 200 HTML
- test_branches_default_marked          — default branch badge present in JSON response
- test_branches_compare_link            — compare link JS present on branches page
- test_branches_new_pr_button           — new pull request link JS present
- test_branches_json_response           — JSON returns BranchDetailListResponse with ahead/behind
- test_tags_page_lists_all             — GET /musehub/ui/{owner}/{slug}/tags returns 200 HTML
- test_tags_namespace_filter           — namespace filter JS present on tags page
- test_tags_json_response              — JSON returns TagListResponse with namespace grouping

Covers issue #211 (audio player — listen page):
- test_listen_page_renders                     — GET /musehub/ui/{owner}/{slug}/listen/{ref} returns 200
- test_listen_page_no_auth_required            — listen page accessible without JWT
- test_listen_page_contains_waveform_ui        — waveform container and controls present
- test_listen_page_contains_play_button        — play button element present in HTML
- test_listen_page_contains_speed_selector     — speed selector element present
- test_listen_page_contains_ab_loop_ui         — A/B loop controls present
- test_listen_page_loads_wavesurfer_vendor     — page loads vendored wavesurfer.min.js (no CDN)
- test_listen_page_loads_audio_player_js       — page loads audio-player.js component script
- test_listen_track_page_renders               — GET /musehub/ui/{owner}/{slug}/listen/{ref}/{path} returns 200
- test_listen_track_page_has_track_path_in_js  — track path injected into page JS context
- test_listen_page_unknown_repo_404            — bad owner/slug → 404
- test_listen_page_keyboard_shortcuts_documented — keyboard shortcuts mentioned in page
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubBranch,
    MusehubCommit,
    MusehubFollow,
    MusehubFork,
    MusehubObject,
    MusehubProfile,
    MusehubRelease,
    MusehubRepo,
    MusehubSession,
    MusehubStar,
    MusehubWatch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        owner="testuser",
        slug="test-beats",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


_TEST_USER_ID = "550e8400-e29b-41d4-a716-446655440000"


async def _make_profile(db_session: AsyncSession, username: str = "testmusician") -> MusehubProfile:
    """Seed a minimal profile and return it."""
    profile = MusehubProfile(
        user_id=_TEST_USER_ID,
        username=username,
        bio="Test bio",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _make_public_repo(db_session: AsyncSession) -> str:
    """Seed a public repo for the test user and return its repo_id."""
    repo = MusehubRepo(
        name="public-beats",
        owner="testuser",
        slug="public-beats",
        visibility="public",
        owner_user_id=_TEST_USER_ID,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# UI route tests (no auth required — routes return HTML)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_repo_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id} returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Verify shared chrome is present
    assert "Muse Hub" in body
    assert repo_id[:8] in body
    # Verify page-specific JS is injected (repo home page — stats bar + audio player)
    assert "stats-bar" in body or "loadStats" in body


@pytest.mark.anyio
async def test_ui_commit_page_shows_artifact_links(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/commits/{commit_id} returns HTML with img/download markers."""
    repo_id = await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # The JS function that renders artifacts must be in the page
    assert "artifactHtml" in body
    # Inline img pattern for .webp artifacts
    assert "<img" in body
    # Download pattern for .mid and other binary artifacts
    assert "Download" in body
    # Audio player pattern
    assert "<audio" in body


@pytest.mark.anyio
async def test_ui_pr_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/pulls returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Pull Requests" in body
    assert "Muse Hub" in body
    # State filter select element must be present in the JS
    assert "state" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_state_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page includes Open, Merged, Closed, and All tab buttons with counts."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    # All four tab IDs must be present
    assert "tab-open" in body
    assert "tab-merged" in body
    assert "tab-closed" in body
    assert "tab-all" in body
    # Tab count placeholder class must be present
    assert "tab-count" in body
    # Tab labels must be present
    assert "Open" in body
    assert "Merged" in body
    assert "Closed" in body
    assert "All" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_body_preview_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page HTML includes bodyPreview JS function for subtitle truncation."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert "bodyPreview" in body
    assert "issue-preview" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_branch_pills(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page HTML includes branch-pill CSS class for from/to branch indicators."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert "branch-pill" in body
    assert "fromBranch" in body
    assert "toBranch" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_sort_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page HTML includes Newest and Oldest sort buttons."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert "Newest" in body
    assert "Oldest" in body
    assert "changeSort" in body
    assert "sort-btn" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_merged_badge_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page JS renders a Merged badge with merge commit short-SHA link for merged PRs."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    # JS logic for merged state and merge commit rendering
    assert "badge-merged" in body
    assert "mergeCommitId" in body
    assert "slice(0, 8)" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_closed_badge_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page JS renders a Closed badge for closed PRs."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert "badge-closed" in body


@pytest.mark.anyio
async def test_ui_issue_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/issues returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Issues" in body
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_ui_issue_list_has_open_closed_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page HTML includes Open and Closed tab buttons and count spans."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    # Tab buttons for open and closed state
    assert "tab-open" in body
    assert "tab-closed" in body
    # Tab count placeholders are rendered client-side; structural markers exist
    assert "tab-count" in body
    assert "Open" in body
    assert "Closed" in body


@pytest.mark.anyio
async def test_ui_issue_list_has_sort_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page HTML includes Newest, Oldest, and Most commented sort buttons."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    assert "Newest" in body
    assert "Oldest" in body
    assert "Most commented" in body
    assert "changeSort" in body


@pytest.mark.anyio
async def test_ui_issue_list_has_label_filter_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page HTML includes client-side label filter logic."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    # JS function for label filtering
    assert "setLabelFilter" in body
    assert "label-pill" in body
    assert "activeLabel" in body


@pytest.mark.anyio
async def test_ui_issue_list_has_body_preview_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page HTML includes bodyPreview helper for truncated body subtitles."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    assert "bodyPreview" in body
    assert "issue-preview" in body


@pytest.mark.anyio
async def test_ui_pr_list_has_comment_badge_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page HTML includes JS for fetching and rendering comment count badges (#298)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    # Comment count badge rendered via loadSocialSignals
    assert "loadSocialSignals" in body
    assert "prCommentCounts" in body
    assert "&#128172;" in body  # 💬 comment icon HTML entity


@pytest.mark.anyio
async def test_ui_pr_list_has_reaction_pills_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page HTML includes JS for fetching and rendering reaction pills (#298)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    # Reaction pills rendered via reactionPills helper
    assert "reactionPills" in body
    assert "prReactions" in body
    # Reactions fetched from the reactions API endpoint
    assert "reactions?target_type=pull_request" in body


@pytest.mark.anyio
async def test_ui_issue_list_has_reaction_pills_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page HTML includes JS for fetching and rendering reaction pills (#298)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    # Reaction summary state and loader
    assert "issueReactions" in body
    assert "loadReactionSummaries" in body
    # Reactions fetched from the reactions API endpoint
    assert "reactions?target_type=issue" in body
    # Reaction pills rendered inline in issue rows
    assert "rxnPills" in body


@pytest.mark.anyio
async def test_ui_issue_list_eager_social_signals(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page eagerly pre-fetches comment counts and reactions on load (#298)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    body = response.text
    # Eager pre-fetch block calls both loaders before rendering rows
    assert "needCounts" in body
    assert "needRxns" in body
    assert "loadCommentCounts" in body
    assert "loadReactionSummaries" in body


@pytest.mark.anyio
async def test_ui_pr_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/pulls/{pr_id} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    pr_id = "some-pr-uuid-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/pulls/{pr_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Merge pull request" in body


@pytest.mark.anyio
async def test_ui_pr_detail_page_has_comment_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page includes threaded comment UI and reaction bar."""
    await _make_repo(db_session)
    pr_id = "some-pr-uuid-comment-test"
    response = await client.get(f"/musehub/ui/testuser/test-beats/pulls/{pr_id}")
    assert response.status_code == 200
    body = response.text
    assert "comment-section" in body
    assert "comment-list" in body
    assert "refreshComments" in body
    assert "submitComment" in body
    assert "deleteComment" in body


@pytest.mark.anyio
async def test_ui_pr_detail_page_has_reaction_bar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page includes a reaction bar that calls loadReactions."""
    await _make_repo(db_session)
    pr_id = "some-pr-uuid-reaction-test"
    response = await client.get(f"/musehub/ui/testuser/test-beats/pulls/{pr_id}")
    assert response.status_code == 200
    body = response.text
    assert "pr-reactions" in body
    assert "loadReactions" in body
    assert "reaction-bar" in body


@pytest.mark.anyio
async def test_pr_detail_shows_diff_radar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page HTML contains the musical diff radar chart container."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls/some-pr-id")
    assert response.status_code == 200
    body = response.text
    # radarSvg function and DIMENSIONS constant must be present
    assert "radarSvg" in body
    assert "DIMENSIONS" in body


@pytest.mark.anyio
async def test_pr_detail_audio_ab(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page HTML contains before/after audio A/B toggle controls."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls/some-pr-id")
    assert response.status_code == 200
    body = response.text
    assert "btn-audio-from" in body
    assert "btn-audio-to" in body
    assert "toggleAudio" in body
    assert "Audio A/B Comparison" in body


@pytest.mark.anyio
async def test_pr_detail_merge_strategies(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page HTML contains the merge strategy selector buttons."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls/some-pr-id")
    assert response.status_code == 200
    body = response.text
    assert "strategy-merge_commit" in body
    assert "strategy-squash" in body
    assert "strategy-rebase" in body
    assert "selectStrategy" in body
    assert "mergePrWithStrategy" in body


@pytest.mark.anyio
async def test_pr_detail_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PR detail page ?format=json returns structured diff data for agent consumption."""
    from datetime import datetime, timezone

    from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubPullRequest

    repo_id = await _make_repo(db_session)
    commit_id = "aabbccddeeff00112233445566778899aabbccdd"
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="feat/blues-riff",
        parent_ids=[],
        message="Add harmonic chord progression in Dm",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/blues-riff",
        head_commit_id=commit_id,
    )
    db_session.add(commit)
    db_session.add(branch)
    import uuid
    pr_id = uuid.uuid4().hex
    pr = MusehubPullRequest(
        pr_id=pr_id,
        repo_id=repo_id,
        title="Add blues riff",
        body="",
        state="open",
        from_branch="feat/blues-riff",
        to_branch="main",
        author="musician",
    )
    db_session.add(pr)
    await db_session.commit()

    response = await client.get(
        f"/musehub/ui/testuser/test-beats/pulls/{pr_id}?format=json",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    # Must include dimension scores and overall score
    assert "dimensions" in data
    assert "overallScore" in data
    assert isinstance(data["dimensions"], list)
    assert len(data["dimensions"]) == 5
    assert data["prId"] == pr_id
    assert data["fromBranch"] == "feat/blues-riff"
    assert data["toBranch"] == "main"
    # Each dimension must have the expected fields
    dim = data["dimensions"][0]
    assert "dimension" in dim
    assert "score" in dim
    assert "level" in dim
    assert "deltaLabel" in dim


# ---------------------------------------------------------------------------
# Tests for musehub_divergence service helpers (issue #384)
# ---------------------------------------------------------------------------


def test_extract_affected_sections_empty_when_no_section_keywords() -> None:
    """affected_sections returns [] when no commit message mentions a section keyword."""
    from maestro.services.musehub_divergence import extract_affected_sections

    messages = (
        "Add jazzy chord progression in Dm",
        "Rework drum pattern for more swing",
        "Fix melody pitch drift on lead synth",
    )
    assert extract_affected_sections(messages) == []


def test_extract_affected_sections_finds_mentioned_keywords() -> None:
    """affected_sections returns only section keywords actually present in commit messages."""
    from maestro.services.musehub_divergence import extract_affected_sections

    messages = (
        "Rewrite chorus melody to be more catchy",
        "Add tension to the bridge section",
        "Clean up drum loop in verse 2",
    )
    result = extract_affected_sections(messages)
    assert "Chorus" in result
    assert "Bridge" in result
    assert "Verse" in result
    # Keywords NOT mentioned should not appear
    assert "Intro" not in result
    assert "Outro" not in result


def test_extract_affected_sections_case_insensitive() -> None:
    """Section keyword matching is case-insensitive."""
    from maestro.services.musehub_divergence import extract_affected_sections

    messages = ("CHORUS rework", "New INTRO material", "bridge transition")
    result = extract_affected_sections(messages)
    assert "Chorus" in result
    assert "Intro" in result
    assert "Bridge" in result


def test_extract_affected_sections_deduplicates() -> None:
    """Each keyword appears at most once even when mentioned in multiple commits."""
    from maestro.services.musehub_divergence import extract_affected_sections

    messages = ("fix chorus", "rewrite chorus progression", "shorten chorus tail")
    result = extract_affected_sections(messages)
    assert result.count("Chorus") == 1


def test_build_pr_diff_response_affected_sections_from_commits() -> None:
    """build_pr_diff_response populates affected_sections from commit messages, not score."""
    from maestro.services.musehub_divergence import (
        MuseHubDimensionDivergence,
        MuseHubDivergenceLevel,
        MuseHubDivergenceResult,
        build_pr_diff_response,
    )

    structural_dim = MuseHubDimensionDivergence(
        dimension="structural",
        level=MuseHubDivergenceLevel.HIGH,
        score=0.9,
        description="High structural divergence",
        branch_a_commits=3,
        branch_b_commits=0,
    )
    other_dims = tuple(
        MuseHubDimensionDivergence(
            dimension=dim,
            level=MuseHubDivergenceLevel.NONE,
            score=0.0,
            description=f"No {dim} changes.",
            branch_a_commits=0,
            branch_b_commits=0,
        )
        for dim in ("melodic", "harmonic", "rhythmic", "dynamic")
    )
    result = MuseHubDivergenceResult(
        repo_id="repo-1",
        branch_a="main",
        branch_b="feat/new-structure",
        common_ancestor="abc123",
        dimensions=(structural_dim,) + other_dims,
        overall_score=0.18,
        # No section keywords in any commit message → affected_sections should be []
        all_messages=("Add chord progression", "Refine drum groove"),
    )

    response = build_pr_diff_response(
        pr_id="pr-abc",
        from_branch="feat/new-structure",
        to_branch="main",
        result=result,
    )

    assert response.affected_sections == []
    assert response.overall_score == 0.18
    assert len(response.dimensions) == 5


def test_build_pr_diff_response_affected_sections_present_when_mentioned() -> None:
    """build_pr_diff_response returns affected_sections when commits mention section keywords."""
    from maestro.services.musehub_divergence import (
        MuseHubDimensionDivergence,
        MuseHubDivergenceLevel,
        MuseHubDivergenceResult,
        build_pr_diff_response,
    )

    dims = tuple(
        MuseHubDimensionDivergence(
            dimension=dim,
            level=MuseHubDivergenceLevel.NONE,
            score=0.0,
            description=f"No {dim} changes.",
            branch_a_commits=0,
            branch_b_commits=0,
        )
        for dim in ("melodic", "harmonic", "rhythmic", "structural", "dynamic")
    )
    result = MuseHubDivergenceResult(
        repo_id="repo-2",
        branch_a="main",
        branch_b="feat/chorus-rework",
        common_ancestor="def456",
        dimensions=dims,
        overall_score=0.0,
        all_messages=("Rework the chorus hook", "Add bridge leading into outro"),
    )

    response = build_pr_diff_response(
        pr_id="pr-def",
        from_branch="feat/chorus-rework",
        to_branch="main",
        result=result,
    )

    assert "Chorus" in response.affected_sections
    assert "Bridge" in response.affected_sections
    assert "Outro" in response.affected_sections
    assert "Verse" not in response.affected_sections


def test_build_zero_diff_response_returns_empty_affected_sections() -> None:
    """build_zero_diff_response always returns [] for affected_sections."""
    from maestro.services.musehub_divergence import build_zero_diff_response

    response = build_zero_diff_response(
        pr_id="pr-zero",
        repo_id="repo-zero",
        from_branch="feat/empty",
        to_branch="main",
    )

    assert response.affected_sections == []
    assert response.overall_score == 0.0
    assert all(d.score == 0.0 for d in response.dimensions)
    assert len(response.dimensions) == 5


@pytest.mark.anyio
async def test_diff_api_affected_sections_empty_without_section_keywords(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /diff returns affected_sections=[] when commit messages mention no section keywords."""
    import uuid
    from datetime import datetime, timezone

    from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubPullRequest

    repo_id = await _make_repo(db_session)
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="feat/harmonic-twist",
        parent_ids=[],
        message="Add jazzy chord progression in Dm",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/harmonic-twist",
        head_commit_id=commit_id,
    )
    pr_id = uuid.uuid4().hex
    pr = MusehubPullRequest(
        pr_id=pr_id,
        repo_id=repo_id,
        title="Harmonic twist",
        body="",
        state="open",
        from_branch="feat/harmonic-twist",
        to_branch="main",
        author="musician",
    )
    db_session.add_all([commit, branch, pr])
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/diff",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["affectedSections"] == []


@pytest.mark.anyio
async def test_diff_api_affected_sections_populated_from_commit_message(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /diff returns affected_sections populated from commit messages mentioning sections."""
    import uuid
    from datetime import datetime, timezone

    from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubPullRequest

    repo_id = await _make_repo(db_session)
    # main branch needs at least one commit for compute_hub_divergence to succeed
    main_commit_id = uuid.uuid4().hex
    main_commit = MusehubCommit(
        commit_id=main_commit_id,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Initial composition",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    main_branch = MusehubBranch(
        repo_id=repo_id,
        name="main",
        head_commit_id=main_commit_id,
    )
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="feat/chorus-rework",
        parent_ids=[main_commit_id],
        message="Rewrite the chorus to be more energetic and add new bridge",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/chorus-rework",
        head_commit_id=commit_id,
    )
    pr_id = uuid.uuid4().hex
    pr = MusehubPullRequest(
        pr_id=pr_id,
        repo_id=repo_id,
        title="Chorus rework",
        body="",
        state="open",
        from_branch="feat/chorus-rework",
        to_branch="main",
        author="musician",
    )
    db_session.add_all([main_commit, main_branch, commit, branch, pr])
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/diff",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    sections = data["affectedSections"]
    assert "Chorus" in sections
    assert "Bridge" in sections
    assert "Verse" not in sections


@pytest.mark.anyio
async def test_ui_diff_json_affected_sections_from_commit_message(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PR detail ?format=json returns affected_sections derived from commit messages."""
    import uuid
    from datetime import datetime, timezone

    from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubPullRequest

    repo_id = await _make_repo(db_session)
    # main branch needs at least one commit for compute_hub_divergence to succeed
    main_commit_id = uuid.uuid4().hex
    main_commit = MusehubCommit(
        commit_id=main_commit_id,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Initial composition",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    main_branch = MusehubBranch(
        repo_id=repo_id,
        name="main",
        head_commit_id=main_commit_id,
    )
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="feat/verse-update",
        parent_ids=[main_commit_id],
        message="Extend verse 2 with new melodic motif",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/verse-update",
        head_commit_id=commit_id,
    )
    pr_id = uuid.uuid4().hex
    pr = MusehubPullRequest(
        pr_id=pr_id,
        repo_id=repo_id,
        title="Verse update",
        body="",
        state="open",
        from_branch="feat/verse-update",
        to_branch="main",
        author="musician",
    )
    db_session.add_all([main_commit, main_branch, commit, branch, pr])
    await db_session.commit()

    response = await client.get(
        f"/musehub/ui/testuser/test-beats/pulls/{pr_id}?format=json",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "Verse" in data["affectedSections"]
    assert "Chorus" not in data["affectedSections"]


@pytest.mark.anyio
async def test_ui_issue_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/issues/{number} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Close issue" in body


@pytest.mark.anyio
async def test_ui_issue_detail_has_comment_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page includes a comment thread section below the issue body (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "comments-container" in body
    assert "Discussion" in body
    assert "comment-input" in body


@pytest.mark.anyio
async def test_ui_issue_detail_has_render_comments_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page embeds buildCommentThread() for rendering the comment thread (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "buildCommentThread" in body
    assert "/comments" in body


@pytest.mark.anyio
async def test_ui_issue_detail_has_submit_comment_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page embeds submitComment() for posting new comments (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "submitComment" in body
    assert "comment-form" in body


@pytest.mark.anyio
async def test_ui_issue_detail_has_delete_comment_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page embeds deleteComment() for removing own comments (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "deleteComment" in body


@pytest.mark.anyio
async def test_ui_issue_detail_has_reply_support_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page embeds startReply() for threaded replies via reply indicator (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "startReply" in body
    assert "parentId" in body


@pytest.mark.anyio
async def test_ui_issue_detail_comment_section_below_body(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Comment section appears after the issue body card in document order (#289)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    close_pos = body.find("closeIssue")
    comments_pos = body.find("comments-container")
    assert close_pos != -1, "closeIssue not found"
    assert comments_pos != -1, "comments-container not found"
    assert comments_pos > close_pos, "comment section must appear after the issue body"


@pytest.mark.anyio
async def test_ui_repo_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """UI routes must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    # Must NOT return 401 — HTML shell has no auth requirement
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_ui_pages_include_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Every UI page embeds the JWT token input form so unauthenticated visitors can sign in."""
    repo_id = await _make_repo(db_session)
    for path in [
        "/musehub/ui/testuser/test-beats",
        "/musehub/ui/testuser/test-beats/pulls",
        "/musehub/ui/testuser/test-beats/issues",
        "/musehub/ui/testuser/test-beats/releases",
    ]:
        response = await client.get(path)
        assert response.status_code == 200
        body = response.text
        # musehub.js (which contains localStorage helpers) must be loaded
        assert "musehub/static/musehub.js" in body
        assert "token-form" in body


@pytest.mark.anyio
async def test_ui_release_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/releases returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Releases" in body
    assert "Muse Hub" in body
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_ui_release_list_page_has_download_buttons(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release list page template includes inline download icon buttons for all package types."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    # All four download package types must appear in the JS template source.
    assert "MIDI" in body
    assert "Stems" in body
    assert "MP3" in body
    assert "MusicXML" in body
    # The helper function that builds download buttons must be present.
    assert "downloadButtons" in body


@pytest.mark.anyio
async def test_ui_release_list_page_has_body_preview(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release list page template includes body preview logic (stripMarkdown / bodyPreview)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    assert "stripMarkdown" in body
    assert "bodyPreview" in body


@pytest.mark.anyio
async def test_ui_release_list_page_has_download_count_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release list page fetches the analytics endpoint to show a download count badge."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    # The page must fetch analytics to populate the download count.
    assert "/analytics" in body
    assert "downloadCount" in body
    assert "download-badge" in body


@pytest.mark.anyio
async def test_ui_release_list_page_has_commit_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release list page links each release's commit_id to the commit detail page."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    # Commit link renders first 8 chars of commit_id via substring(0, 8).
    assert "commitId" in body
    assert "substring(0, 8)" in body or "substring(0,8)" in body
    # Commit detail URL pattern must be embedded in the JS template.
    assert "/commits/" in body


@pytest.mark.anyio
async def test_ui_release_list_page_has_tag_colour_coding(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release list page colour-codes tags: v1.x = green (stable), v0.x = yellow (pre-release)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    # CSS classes for stable and pre-release tags must be present.
    assert "tag-stable" in body
    assert "tag-prerelease" in body
    # The JS function that assigns these classes must be present.
    assert "tagBadgeClass" in body


@pytest.mark.anyio
async def test_ui_release_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/releases/{tag} returns 200 HTML with download section."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Download" in body
    assert "v1.0" in body


@pytest.mark.anyio
async def test_ui_release_detail_has_comment_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release detail page HTML includes the Discussion comment section (issue #290)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    body = response.text
    assert "comments-section" in body
    assert "Discussion" in body


@pytest.mark.anyio
async def test_ui_release_detail_has_render_comments_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release detail page includes renderComments() JavaScript function (issue #290)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    body = response.text
    assert "renderComments" in body
    assert "submitComment" in body
    assert "deleteComment" in body


@pytest.mark.anyio
async def test_ui_release_detail_comment_uses_release_target_type(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release detail page posts comments with target_type='release' (issue #290)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    body = response.text
    assert "target_type: 'release'" in body or "target_type':'release'" in body or "target_type: \"release\"" in body


@pytest.mark.anyio
async def test_ui_release_detail_has_reply_thread_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release detail page includes toggleReplyForm and submitReply for threaded comments (issue #290)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    body = response.text
    assert "toggleReplyForm" in body
    assert "submitReply" in body


@pytest.mark.anyio
async def test_ui_repo_page_shows_releases_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id} includes a Releases navigation button."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "releases" in body.lower()


# ---------------------------------------------------------------------------
# Global search UI page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_ui_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search returns 200 HTML (no auth required — HTML shell)."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Global Search" in body
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_global_search_ui_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search must not return 401 — it is a static HTML shell."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code != 401
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Object listing endpoint tests (JSON, authed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_objects_returns_empty_for_new_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects returns empty list for new repo."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["objects"] == []


@pytest.mark.anyio
async def test_list_objects_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/objects")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_list_objects_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/objects returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/objects",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_object_content_404_for_unknown_object(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects/{unknown}/content returns 404."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects/sha256:notexist/content",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Credits UI page tests (issue #241)
# DAG graph UI page tests (issue #229)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credits_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/credits returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Credits" in body


@pytest.mark.anyio


async def test_graph_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/graph returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "graph" in body.lower()


# ---------------------------------------------------------------------------
# Context viewer tests (issue #232)
# ---------------------------------------------------------------------------

_FIXED_COMMIT_ID = "aabbccdd" * 8  # 64-char hex string


async def _make_repo_with_commit(db_session: AsyncSession) -> tuple[str, str]:
    """Seed a repo with one commit and return (repo_id, commit_id)."""
    repo = MusehubRepo(
        name="jazz-context-test",
        owner="testuser",
        slug="jazz-context-test",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    commit = MusehubCommit(
        commit_id=_FIXED_COMMIT_ID,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Add bass and drums",
        author="test-musician",
        timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()
    return repo_id, _FIXED_COMMIT_ID


@pytest.mark.anyio
async def test_context_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/context/{ref} returns 200 HTML without auth."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(f"/musehub/ui/testuser/jazz-context-test/context/{commit_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "context" in body.lower()
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_credits_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/credits returns JSON with required fields."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "repoId" in body
    assert "contributors" in body
    assert "sort" in body
    assert "totalContributors" in body
    assert body["repoId"] == repo_id
    assert isinstance(body["contributors"], list)
    assert body["sort"] == "count"



@pytest.mark.anyio
async def test_context_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns MuseHubContextResponse."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/{commit_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "repoId" in body

    assert body["repoId"] == repo_id
    assert body["currentBranch"] == "main"
    assert "headCommit" in body
    assert body["headCommit"]["commitId"] == commit_id
    assert body["headCommit"]["author"] == "test-musician"
    assert "musicalState" in body
    assert "history" in body
    assert "missingElements" in body
    assert "suggestions" in body


@pytest.mark.anyio
async def test_credits_empty_state_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Repo with no commits returns empty contributors list and totalContributors=0."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributors"] == []
    assert body["totalContributors"] == 0


@pytest.mark.anyio
async def test_context_includes_musical_state(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:

    """Context response includes musicalState with an activeTracks field."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/{commit_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    musical_state = response.json()["musicalState"]
    assert "activeTracks" in musical_state
    assert isinstance(musical_state["activeTracks"], list)
    # Dimensions requiring MIDI analysis are None at this stage
    assert musical_state["key"] is None
    assert musical_state["tempoBpm"] is None


@pytest.mark.anyio
async def test_context_unknown_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns 404 for unknown ref."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/deadbeef" + "0" * 56,
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_context_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/context/{ref} returns 404 for unknown repo."""
    response = await client.get(
        "/api/v1/musehub/repos/ghost-repo/context/deadbeef" + "0" * 56,
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_context_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/deadbeef" + "0" * 56,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_context_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The context UI page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(f"/musehub/ui/testuser/jazz-context-test/context/{commit_id}")
    assert response.status_code != 401
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Context page additional tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_page_contains_agent_explainer(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Context viewer page includes the 'What the Agent Sees' explainer card."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(f"/musehub/ui/testuser/jazz-context-test/context/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "What the Agent Sees" in body
    assert commit_id[:8] in body


# ---------------------------------------------------------------------------
# Embed player route tests (issue #244)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embed_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/embed/{ref} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_embed_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page must be accessible without an Authorization header (public embedding)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_embed_page_x_frame_options(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page must set X-Frame-Options: ALLOWALL to permit cross-origin framing."""
    repo_id = await _make_repo(db_session)
    ref = "cafebabe1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
    assert response.status_code == 200
    assert response.headers.get("x-frame-options") == "ALLOWALL"


@pytest.mark.anyio
async def test_embed_page_contains_player_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page HTML must contain player elements: play button, progress bar, and Muse Hub link."""
    repo_id = await _make_repo(db_session)
    ref = "feedface0123456789ab"
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "play-btn" in body
    assert "progress-bar" in body
    assert "View on Muse Hub" in body
    assert "audio" in body
    assert repo_id in body

# ---------------------------------------------------------------------------
# Groove check page and endpoint tests (issue #226)

# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/groove-check returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Groove Check" in body


@pytest.mark.anyio
async def test_credits_page_contains_json_ld_injection_slug_route(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page embeds JSON-LD injection logic via slug route."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "application/ld+json" in body
    assert "schema.org" in body
    assert "MusicComposition" in body


@pytest.mark.anyio
async def test_credits_page_contains_sort_options_slug_route(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page includes sort dropdown via slug route."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "Most prolific" in body
    assert "Most recent" in body
    assert "A" in body  # "A – Z" option


@pytest.mark.anyio
async def test_credits_empty_state_message_in_page_slug_route(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page JS includes empty-state message via slug route."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "muse session start" in body


@pytest.mark.anyio
async def test_credits_no_auth_required_slug_route(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page must be accessible without an Authorization header via slug route."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_credits_page_contains_avatar_functions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page includes avatarHsl and avatarCircle JS functions for contributor avatars."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "avatarHsl" in body
    assert "avatarCircle" in body
    assert "border-radius:50%" in body


@pytest.mark.anyio
async def test_credits_page_contains_fetch_profile_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page includes fetchProfile JS function that fetches contributor profiles in parallel."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "fetchProfile" in body
    assert "Promise.all" in body
    assert "/users/" in body


@pytest.mark.anyio
async def test_credits_page_contains_profile_link_pattern(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page links contributor names to their profile pages at /musehub/ui/users/{username}."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/users/" in body
    assert "encodeURIComponent" in body


@pytest.mark.anyio
async def test_groove_check_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check UI page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code != 401
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Object listing endpoint tests (JSON, authed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_page_contains_chart_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page embeds the SVG chart rendering JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "renderGrooveChart" in body
    assert "grooveScore" in body
    assert "driftDelta" in body


@pytest.mark.anyio
async def test_groove_check_page_contains_status_badges(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page HTML includes OK / WARN / FAIL status badge rendering."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "statusBadge" in body
    assert "WARN" in body
    assert "FAIL" in body


@pytest.mark.anyio
async def test_groove_check_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page embeds the JWT token input form so visitors can authenticate."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "token-form" in body
    assert "token-input" in body


@pytest.mark.anyio
async def test_groove_check_endpoint_returns_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/groove-check returns JSON with required fields."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "commitRange" in body
    assert "threshold" in body
    assert "totalCommits" in body
    assert "flaggedCommits" in body
    assert "worstCommit" in body
    assert "entries" in body
    assert isinstance(body["entries"], list)


@pytest.mark.anyio
async def test_graph_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    assert response.status_code != 401


async def test_groove_check_endpoint_entries_have_required_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check entries each contain commit, grooveScore, driftDelta, and status."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?limit=5",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalCommits"] > 0
    entry = body["entries"][0]
    assert "commit" in entry
    assert "grooveScore" in entry
    assert "driftDelta" in entry
    assert "status" in entry
    assert entry["status"] in ("OK", "WARN", "FAIL")
    assert "track" in entry
    assert "midiFiles" in entry


@pytest.mark.anyio
async def test_groove_check_endpoint_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/groove-check returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/groove-check")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_groove_check_endpoint_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/groove-check returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/groove-check",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_groove_check_endpoint_respects_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check endpoint returns at most ``limit`` entries."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalCommits"] <= 3
    assert len(body["entries"]) <= 3


@pytest.mark.anyio
async def test_groove_check_endpoint_custom_threshold(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check endpoint accepts a custom threshold parameter."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?threshold=0.05",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert abs(body["threshold"] - 0.05) < 1e-9


@pytest.mark.anyio
async def test_repo_page_contains_groove_check_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo landing page navigation includes a Groove Check link."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "groove-check" in body


# ---------------------------------------------------------------------------
# User profile page tests (issue #233 — pre-existing from dev, fixed here)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{username} returns 200 HTML for a known profile."""
    await _make_profile(db_session, "rockstar")
    response = await client.get("/musehub/ui/users/rockstar")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "@rockstar" in body
    # Contribution graph JS must be present
    assert "contributionGraph" in body or "contrib-graph" in body


@pytest.mark.anyio
async def test_profile_no_auth_required_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile UI page is publicly accessible without a JWT (returns 200, not 401)."""
    await _make_profile(db_session, "public-user")
    response = await client.get("/musehub/ui/users/public-user")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_profile_unknown_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{unknown} returns 404 for a non-existent profile."""
    response = await client.get("/api/v1/musehub/users/does-not-exist-xyz")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username} returns a valid JSON profile with required fields."""
    await _make_profile(db_session, "jazzmaster")
    response = await client.get("/api/v1/musehub/users/jazzmaster")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "jazzmaster"
    assert "repos" in data
    assert "contributionGraph" in data
    assert "sessionCredits" in data
    assert isinstance(data["sessionCredits"], int)
    assert isinstance(data["contributionGraph"], list)


@pytest.mark.anyio
async def test_profile_lists_repos(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username} includes public repos in the response."""
    await _make_profile(db_session, "beatmaker")
    repo_id = await _make_public_repo(db_session)
    response = await client.get("/api/v1/musehub/users/beatmaker")
    assert response.status_code == 200
    data = response.json()
    repo_ids = [r["repoId"] for r in data["repos"]]
    assert repo_id in repo_ids


@pytest.mark.anyio
async def test_profile_create_and_update(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/users creates a profile; PUT updates it."""
    # Create profile
    resp = await client.post(
        "/api/v1/musehub/users",
        json={"username": "newartist", "bio": "Initial bio"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newartist"
    assert data["bio"] == "Initial bio"

    # Update profile
    resp2 = await client.put(
        "/api/v1/musehub/users/newartist",
        json={"bio": "Updated bio"},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["bio"] == "Updated bio"


@pytest.mark.anyio
async def test_profile_create_duplicate_username_409(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/users returns 409 when username is already taken."""
    await _make_profile(db_session, "takenname")
    resp = await client.post(
        "/api/v1/musehub/users",
        json={"username": "takenname"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_profile_update_403_for_wrong_owner(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PUT /api/v1/musehub/users/{username} returns 403 when caller doesn't own the profile."""
    # Create a profile owned by a DIFFERENT user
    other_profile = MusehubProfile(
        user_id="different-user-id-999",
        username="someoneelse",
        bio="not yours",
        pinned_repo_ids=[],
    )
    db_session.add(other_profile)
    await db_session.commit()

    resp = await client.put(
        "/api/v1/musehub/users/someoneelse",
        json={"bio": "hijacked"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_profile_page_unknown_user_renders_404_inline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{unknown} returns 200 HTML (JS renders 404 inline)."""
    response = await client.get("/musehub/ui/users/ghost-user-xyz")
    # The HTML shell always returns 200 — the JS fetches and handles the API 404
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# Forked repos endpoint tests (issue #303)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_forked_repos_empty_list(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/forks returns empty list when user has no forks."""
    await _make_profile(db_session, "freshuser")
    response = await client.get("/api/v1/musehub/users/freshuser/forks")
    assert response.status_code == 200
    data = response.json()
    assert data["forks"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_profile_forked_repos_returns_forks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/forks returns forked repos with source attribution."""
    await _make_profile(db_session, "forkuser")

    # Seed a source repo owned by another user
    source = MusehubRepo(
        name="original-track",
        owner="original-owner",
        slug="original-track",
        visibility="public",
        owner_user_id="original-owner-id",
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    # Seed the fork repo owned by forkuser
    fork_repo = MusehubRepo(
        name="original-track",
        owner="forkuser",
        slug="original-track",
        visibility="public",
        owner_user_id=_TEST_USER_ID,
    )
    db_session.add(fork_repo)
    await db_session.commit()
    await db_session.refresh(fork_repo)

    # Seed the fork relationship
    fork = MusehubFork(
        source_repo_id=source.repo_id,
        fork_repo_id=fork_repo.repo_id,
        forked_by="forkuser",
    )
    db_session.add(fork)
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/forkuser/forks")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["forks"]) == 1
    entry = data["forks"][0]
    assert entry["sourceOwner"] == "original-owner"
    assert entry["sourceSlug"] == "original-track"
    assert entry["forkRepo"]["owner"] == "forkuser"
    assert entry["forkRepo"]["slug"] == "original-track"
    assert "forkId" in entry
    assert "forkedAt" in entry


@pytest.mark.anyio
async def test_profile_forked_repos_404_for_unknown_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{unknown}/forks returns 404 when user doesn't exist."""
    response = await client.get("/api/v1/musehub/users/ghost-no-profile/forks")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_forked_repos_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/forks is publicly accessible without a JWT."""
    await _make_profile(db_session, "public-forkuser")
    response = await client.get("/api/v1/musehub/users/public-forkuser/forks")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.skip(reason="Pre-existing failure: profile page template missing loadForkedRepos JS — see #539")
@pytest.mark.anyio
async def test_profile_page_has_forked_section_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile HTML page includes the forked repos JS (loadForkedRepos, forked-section)."""
    await _make_profile(db_session, "jsforkuser")
    response = await client.get("/musehub/ui/users/jsforkuser")
    assert response.status_code == 200
    body = response.text
    assert "loadForkedRepos" in body
    assert "forked-section" in body
    assert "API_FORKS" in body
    assert "forked from" in body


# ---------------------------------------------------------------------------
# Starred repos tab (issue #297)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_starred_repos_empty_list(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/starred returns empty list when user has no stars."""
    await _make_profile(db_session, "freshstaruser")
    response = await client.get("/api/v1/musehub/users/freshstaruser/starred")
    assert response.status_code == 200
    data = response.json()
    assert data["starred"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_profile_starred_repos_returns_starred(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/starred returns starred repos with full metadata."""
    await _make_profile(db_session, "stargazeruser")

    repo = MusehubRepo(
        name="awesome-groove",
        owner="someartist",
        slug="awesome-groove",
        visibility="public",
        owner_user_id="someartist-id",
        description="A great groove",
        key_signature="C major",
        tempo_bpm=120,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    star = MusehubStar(
        repo_id=repo.repo_id,
        user_id=_TEST_USER_ID,
    )
    db_session.add(star)
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/stargazeruser/starred")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["starred"]) == 1
    entry = data["starred"][0]
    assert entry["repo"]["owner"] == "someartist"
    assert entry["repo"]["slug"] == "awesome-groove"
    assert entry["repo"]["description"] == "A great groove"
    assert "starId" in entry
    assert "starredAt" in entry


@pytest.mark.anyio
async def test_profile_starred_repos_404_for_unknown_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{unknown}/starred returns 404 when user doesn't exist."""
    response = await client.get("/api/v1/musehub/users/ghost-no-star-profile/starred")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_starred_repos_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/starred is publicly accessible without a JWT."""
    await _make_profile(db_session, "public-staruser")
    response = await client.get("/api/v1/musehub/users/public-staruser/starred")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_profile_starred_repos_ordered_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/starred returns stars newest first."""
    from datetime import timezone

    await _make_profile(db_session, "multistaruser")

    repo_a = MusehubRepo(
        name="track-alpha", owner="artist-a", slug="track-alpha",
        visibility="public", owner_user_id="artist-a-id",
    )
    repo_b = MusehubRepo(
        name="track-beta", owner="artist-b", slug="track-beta",
        visibility="public", owner_user_id="artist-b-id",
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.commit()
    await db_session.refresh(repo_a)
    await db_session.refresh(repo_b)

    import datetime as dt
    star_a = MusehubStar(
        repo_id=repo_a.repo_id,
        user_id=_TEST_USER_ID,
        created_at=dt.datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    star_b = MusehubStar(
        repo_id=repo_b.repo_id,
        user_id=_TEST_USER_ID,
        created_at=dt.datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([star_a, star_b])
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/multistaruser/starred")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    # newest first: star_b (June) before star_a (January)
    assert data["starred"][0]["repo"]["slug"] == "track-beta"
    assert data["starred"][1]["repo"]["slug"] == "track-alpha"


@pytest.mark.skip(reason="Pre-existing failure: profile page template missing loadStarredRepos JS — see #539")
@pytest.mark.anyio
async def test_profile_page_has_starred_section_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile HTML page includes the starred repos JS (loadStarredRepos, starred-section)."""
    await _make_profile(db_session, "jsstaruser")
    response = await client.get("/musehub/ui/users/jsstaruser")
    assert response.status_code == 200
    body = response.text
    assert "loadStarredRepos" in body
    assert "starred-section" in body
    assert "API_STARRED" in body
    assert "starredRepoCardHtml" in body


# ---------------------------------------------------------------------------
# Watched repos tab (issue #300)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_watched_repos_empty_list(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/watched returns empty list when user watches nothing."""
    await _make_profile(db_session, "freshwatchuser")
    response = await client.get("/api/v1/musehub/users/freshwatchuser/watched")
    assert response.status_code == 200
    data = response.json()
    assert data["watched"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_profile_watched_repos_returns_watched(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/watched returns watched repos with full metadata."""
    await _make_profile(db_session, "watcheruser")

    repo = MusehubRepo(
        name="cool-composition",
        owner="composer",
        slug="cool-composition",
        visibility="public",
        owner_user_id="composer-id",
        description="A cool composition",
        key_signature="G minor",
        tempo_bpm=90,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    watch = MusehubWatch(
        repo_id=repo.repo_id,
        user_id=_TEST_USER_ID,
    )
    db_session.add(watch)
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/watcheruser/watched")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["watched"]) == 1
    entry = data["watched"][0]
    assert entry["repo"]["owner"] == "composer"
    assert entry["repo"]["slug"] == "cool-composition"
    assert entry["repo"]["description"] == "A cool composition"
    assert "watchId" in entry
    assert "watchedAt" in entry


@pytest.mark.anyio
async def test_profile_watched_repos_404_for_unknown_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{unknown}/watched returns 404 when user doesn't exist."""
    response = await client.get("/api/v1/musehub/users/ghost-no-watch-profile/watched")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_watched_repos_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/watched is publicly accessible without a JWT."""
    await _make_profile(db_session, "public-watchuser")
    response = await client.get("/api/v1/musehub/users/public-watchuser/watched")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_profile_watched_repos_ordered_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/watched returns watches newest first."""
    import datetime as dt
    from datetime import timezone

    await _make_profile(db_session, "multiwatchuser")

    repo_a = MusehubRepo(
        name="song-alpha", owner="band-a", slug="song-alpha",
        visibility="public", owner_user_id="band-a-id",
    )
    repo_b = MusehubRepo(
        name="song-beta", owner="band-b", slug="song-beta",
        visibility="public", owner_user_id="band-b-id",
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.commit()
    await db_session.refresh(repo_a)
    await db_session.refresh(repo_b)

    watch_a = MusehubWatch(
        repo_id=repo_a.repo_id,
        user_id=_TEST_USER_ID,
        created_at=dt.datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    watch_b = MusehubWatch(
        repo_id=repo_b.repo_id,
        user_id=_TEST_USER_ID,
        created_at=dt.datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([watch_a, watch_b])
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/multiwatchuser/watched")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    # newest first: watch_b (June) before watch_a (January)
    assert data["watched"][0]["repo"]["slug"] == "song-beta"
    assert data["watched"][1]["repo"]["slug"] == "song-alpha"


@pytest.mark.skip(reason="Pre-existing failure: profile page template missing loadWatchedRepos JS — see #539")
@pytest.mark.anyio
async def test_profile_page_has_watched_section_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile HTML page includes the watched repos JS (loadWatchedRepos, watched-section)."""
    await _make_profile(db_session, "jswatchuser")
    response = await client.get("/musehub/ui/users/jswatchuser")
    assert response.status_code == 200
    body = response.text
    assert "loadWatchedRepos" in body
    assert "watched-section" in body
    assert "API_WATCHED" in body


@pytest.mark.anyio
async def test_timeline_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/timeline returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "timeline" in body.lower()
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_timeline_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline UI route must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_timeline_page_contains_layer_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page embeds toggleable layer controls for all four layers."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "Commits" in body
    assert "Emotion" in body
    assert "Sections" in body
    assert "Tracks" in body


@pytest.mark.anyio
async def test_timeline_page_contains_zoom_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page embeds day/week/month/all zoom buttons."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "Day" in body
    assert "Week" in body
    assert "Month" in body
    assert "All" in body


@pytest.mark.anyio
async def test_timeline_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page includes the JWT token input form."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "musehub/static/musehub.js" in body
    assert "token-form" in body


@pytest.mark.anyio
async def test_timeline_page_contains_overlay_toggles(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page must include Sessions, PRs, and Releases layer toggle checkboxes.

    Regression test for issue #307 — before this fix the timeline had no
    overlay markers for repo lifecycle events (sessions, PR merges, releases).
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    # All three new overlay toggle labels must be present.
    assert "Sessions" in body
    assert "PRs" in body
    assert "Releases" in body


@pytest.mark.anyio
async def test_timeline_page_overlay_js_variables(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page script must initialise sessions, mergedPRs, and releases arrays.

    Verifies that the overlay data arrays and the layer toggle state are wired
    up in the page's inline script so the renderer can draw markers.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    # State variables for overlay data must be declared.
    assert "let sessions" in body
    assert "let mergedPRs" in body
    assert "let releases" in body
    # Layer toggle state must include the three new keys.
    assert "sessions: true" in body
    assert "prs: true" in body
    assert "releases: true" in body


@pytest.mark.anyio
async def test_timeline_page_overlay_fetch_calls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page must issue API calls for sessions, merged PRs, and releases.

    Checks that the inline script calls the correct API paths so the browser
    will fetch overlay data when the page loads.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "/sessions" in body
    assert "state=merged" in body
    assert "/releases" in body


@pytest.mark.anyio
async def test_timeline_page_overlay_legend(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page legend must describe the three new overlay marker types."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    # Colour labels in the legend.
    assert "teal" in body.lower()
    assert "gold" in body.lower()


@pytest.mark.anyio
async def test_timeline_pr_markers_use_merged_at_for_positioning(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline JS must use pr.mergedAt (falling back to pr.createdAt) for marker X position.

    Regression test for issue #349: previously the overlay always used
    createdAt, which positioned merge markers at PR *open* time instead of
    merge time, sometimes off by days or weeks.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    # The fix: JS should reference pr.mergedAt and fall back to pr.createdAt.
    assert "pr.mergedAt" in body
    assert "pr.createdAt" in body
    # The tooltip timestamp must also use mergedAt.
    assert "new Date(pr.mergedAt)" in body


@pytest.mark.anyio
async def test_pr_response_includes_merged_at_after_merge(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PRResponse must expose merged_at set to the merge timestamp (not None) after merge.

    Regression test for issue #349: before this fix merged_at was absent from
    PRResponse, forcing the timeline to fall back to createdAt.
    """
    from datetime import datetime, timezone

    from maestro.services import musehub_pull_requests, musehub_repository

    repo_id = await _make_repo(db_session)

    # Create two branches with commits so the merge can proceed.
    import uuid as _uuid

    from maestro.db import musehub_models as dbm

    commit_a_id = _uuid.uuid4().hex
    commit_main_id = _uuid.uuid4().hex

    commit_a = dbm.MusehubCommit(
        commit_id=commit_a_id,
        repo_id=repo_id,
        branch="feat/test-merge",
        parent_ids=[],
        message="test commit on feature branch",
        author="tester",
        timestamp=datetime.now(timezone.utc),
    )
    branch_a = dbm.MusehubBranch(
        repo_id=repo_id, name="feat/test-merge", head_commit_id=commit_a_id
    )
    db_session.add(commit_a)
    db_session.add(branch_a)

    commit_main = dbm.MusehubCommit(
        commit_id=commit_main_id,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="initial commit on main",
        author="tester",
        timestamp=datetime.now(timezone.utc),
    )
    branch_main = dbm.MusehubBranch(
        repo_id=repo_id, name="main", head_commit_id=commit_main_id
    )
    db_session.add(commit_main)
    db_session.add(branch_main)
    await db_session.flush()

    pr = await musehub_pull_requests.create_pr(
        db_session,
        repo_id=repo_id,
        title="Test merge PR",
        from_branch="feat/test-merge",
        to_branch="main",
        body="",
        author="tester",
    )
    await db_session.flush()

    before_merge = datetime.now(timezone.utc)
    merged_pr = await musehub_pull_requests.merge_pr(
        db_session, repo_id, pr.pr_id, merge_strategy="merge_commit"
    )
    after_merge = datetime.now(timezone.utc)

    assert merged_pr.merged_at is not None, "merged_at must be set after merge"
    # merged_at must be a timezone-aware datetime between before and after the merge call.
    merged_at = merged_pr.merged_at
    if merged_at.tzinfo is None:
        merged_at = merged_at.replace(tzinfo=timezone.utc)
    assert before_merge <= merged_at <= after_merge, (
        f"merged_at {merged_at} is outside the expected range [{before_merge}, {after_merge}]"
    )
    assert merged_pr.state == "merged"


# ---------------------------------------------------------------------------
# Embed player route tests (issue #244)
# ---------------------------------------------------------------------------


_UTC = timezone.utc


@pytest.mark.anyio
async def test_graph_page_contains_dag_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page embeds the client-side DAG renderer JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    body = response.text
    assert "renderGraph" in body
    assert "dag-viewport" in body
    assert "dag-svg" in body


@pytest.mark.anyio
async def test_graph_page_contains_session_ring_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page embeds session-marker and reaction-count JavaScript.

    Regression guard for issue #313: ensures the template contains the three
    new client-side components added by this feature — SESSION_RING_COLOR (the
    teal ring constant), buildSessionMap (commit→session index builder), and
    fetchReactions (batch reaction fetcher).  A missing symbol means the graph
    will silently render with no session markers or reaction counts.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    body = response.text
    assert "SESSION_RING_COLOR" in body, "Teal session ring constant missing from graph page"
    assert "buildSessionMap" in body, "buildSessionMap function missing from graph page"
    assert "fetchReactions" in body, "fetchReactions function missing from graph page"


@pytest.mark.anyio
async def test_session_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Sessions" in body
    assert "musehub/static/musehub.js" in body


@pytest.mark.anyio
async def test_session_detail_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions/{session_id} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    session_id = "some-session-uuid-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Recording Session" in body
    assert session_id[:8] in body


@pytest.mark.anyio
async def test_session_detail_participants(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page HTML includes the Participants section."""
    repo_id = await _make_repo(db_session)
    session_id = "participant-session-5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "Participants" in body


@pytest.mark.anyio
async def test_session_detail_commits(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page HTML includes the Commits section."""
    repo_id = await _make_repo(db_session)
    session_id = "commits-session-9012"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "Commits" in body


@pytest.mark.anyio
async def test_session_detail_404_marker(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page renders a 404 error message for unknown session IDs.

    The page itself returns 200 (HTML shell) — the 404 is detected client-side
    when the JS calls the JSON API.  The page must include error-handling JS that
    checks for a 404 response and shows a user-friendly message.
    """
    repo_id = await _make_repo(db_session)
    session_id = "does-not-exist-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    # The JS error handler must check for a 404 and render a "not found" message
    assert "Session not found" in body or "404" in body


@pytest.mark.anyio
async def test_session_detail_has_comment_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must include the Discussion comment section."""
    await _make_repo(db_session)
    session_id = "comment-section-session-001"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "comments-section" in body
    assert "Discussion" in body
    assert "comments-list" in body


@pytest.mark.anyio
async def test_session_detail_comment_section_uses_session_target_type(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail comment API calls must use target_type='session'."""
    await _make_repo(db_session)
    session_id = "target-type-session-002"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "target_type: 'session'" in body or "target_type=\"session\"" in body


@pytest.mark.anyio
async def test_session_detail_has_loadcomments_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must call loadComments() on init."""
    await _make_repo(db_session)
    session_id = "load-comments-session-003"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "loadComments" in body


@pytest.mark.anyio
async def test_session_detail_has_submit_comment_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must include the submitComment JS function."""
    await _make_repo(db_session)
    session_id = "submit-comment-session-004"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "submitComment" in body
    assert "deleteComment" in body


@pytest.mark.anyio
async def test_session_detail_has_render_comments_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must include renderComments() for threaded display."""
    await _make_repo(db_session)
    session_id = "render-comments-session-005"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "renderComments" in body


@pytest.mark.anyio
async def test_session_detail_comment_form_has_new_comment_body(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must include the new-comment-body textarea and submit button."""
    await _make_repo(db_session)
    session_id = "comment-form-session-006"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "new-comment-body" in body
    assert "new-comment-form" in body
    assert "comment-submit-btn" in body


async def _make_session(
    db_session: AsyncSession,
    repo_id: str,
    *,
    started_offset_seconds: int = 0,
    is_active: bool = False,
    intent: str = "jazz composition",
    participants: list[str] | None = None,
    commits: list[str] | None = None,
    notes: str = "",
) -> str:
    """Seed a MusehubSession and return its session_id."""
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta

    started_at = start + timedelta(seconds=started_offset_seconds)
    ended_at = None if is_active else started_at + timedelta(hours=1)
    row = MusehubSession(
        repo_id=repo_id,
        started_at=started_at,
        ended_at=ended_at,
        participants=participants or ["producer-a"],
        commits=commits or [],
        notes=notes,
        intent=intent,
        location="Studio A",
        is_active=is_active,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return str(row.session_id)


@pytest.mark.anyio
async def test_sessions_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns session list with metadata."""
    repo_id = await _make_repo(db_session)
    session_id = await _make_session(db_session, repo_id, intent="jazz solo")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "total" in data
    assert data["total"] == 1
    sess = data["sessions"][0]
    assert sess["sessionId"] == session_id
    assert sess["intent"] == "jazz solo"
    assert sess["location"] == "Studio A"
    assert sess["isActive"] is False
    assert sess["durationSeconds"] == pytest.approx(3600.0)


@pytest.mark.anyio
async def test_sessions_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Sessions are returned newest-first (active sessions appear before ended sessions)."""
    repo_id = await _make_repo(db_session)
    # older ended session
    await _make_session(db_session, repo_id, started_offset_seconds=0, intent="older")
    # newer ended session
    await _make_session(db_session, repo_id, started_offset_seconds=3600, intent="newer")
    # active session (should surface first regardless of time)
    await _make_session(
        db_session, repo_id, started_offset_seconds=100, is_active=True, intent="live"
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 3
    # Active session must come first
    assert sessions[0]["isActive"] is True
    assert sessions[0]["intent"] == "live"
    # Then newest ended session
    assert sessions[1]["intent"] == "newer"
    assert sessions[2]["intent"] == "older"


@pytest.mark.anyio
async def test_sessions_empty_for_new_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns empty list for new repo."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sessions"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_sessions_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/sessions")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_sessions_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/sessions returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_session_returns_201(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/sessions creates a session and returns 201."""
    repo_id = await _make_repo(db_session)
    payload = {
        "participants": ["producer-a", "collab-b"],
        "intent": "house beat experiment",
        "location": "Remote – Berlin",
        "isActive": True,
    }
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["isActive"] is True
    assert data["intent"] == "house beat experiment"
    assert data["location"] == "Remote \u2013 Berlin"
    assert data["participants"] == ["producer-a", "collab-b"]
    assert "sessionId" in data


@pytest.mark.anyio
async def test_stop_session_marks_ended(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/sessions/{session_id}/stop closes a live session."""
    repo_id = await _make_repo(db_session)
    session_id = await _make_session(db_session, repo_id, is_active=True)

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions/{session_id}/stop",
        json={},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["isActive"] is False
    assert data["endedAt"] is not None
    assert data["durationSeconds"] is not None


@pytest.mark.anyio
async def test_active_session_has_null_duration(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Active sessions must have durationSeconds=null (session still in progress)."""
    repo_id = await _make_repo(db_session)
    await _make_session(db_session, repo_id, is_active=True)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sess = response.json()["sessions"][0]
    assert sess["isActive"] is True
    assert sess["durationSeconds"] is None


@pytest.mark.anyio
async def test_session_response_includes_commits_and_notes(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """SessionResponse includes commits list and notes field in the JSON payload."""
    repo_id = await _make_repo(db_session)
    commit_ids = ["abc123", "def456", "ghi789"]
    closing_notes = "Great session, nailed the groove."
    await _make_session(
        db_session,
        repo_id,
        intent="funk groove",
        commits=commit_ids,
        notes=closing_notes,
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sess = response.json()["sessions"][0]
    assert sess["commits"] == commit_ids
    assert sess["notes"] == closing_notes


@pytest.mark.anyio
async def test_session_response_commits_field_present(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Sessions API response includes the 'commits' field for each session.

    Regression guard for issue #313: the graph page uses the session commits
    list to build the session→commit index (buildSessionMap).  If this field
    is absent or empty when commits exist, no session rings will appear on
    the DAG graph.
    """
    repo_id = await _make_repo(db_session)
    commit_ids = ["abc123def456abc123def456abc123de", "feedbeeffeedbeefdead000000000001"]
    row = MusehubSession(
        repo_id=repo_id,
        started_at=datetime(2025, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2025, 3, 1, 11, 0, 0, tzinfo=timezone.utc),
        participants=["artist-a"],
        intent="session with commits",
        location="Studio B",
        is_active=False,
        commits=commit_ids,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert "commits" in sess, "'commits' field missing from SessionResponse"
    assert sess["commits"] == commit_ids, "commits field does not match seeded commit IDs"


@pytest.mark.anyio
async def test_session_response_empty_commits_and_notes_defaults(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """SessionResponse defaults commits to [] and notes to '' when absent."""
    repo_id = await _make_repo(db_session)
    await _make_session(db_session, repo_id, intent="defaults check")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sess = response.json()["sessions"][0]
    assert sess["commits"] == []
    assert sess["notes"] == ""


@pytest.mark.anyio
async def test_session_list_page_contains_avatar_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions list page HTML contains participant avatar JS and CSS class references."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    body = response.text
    # The JS helper that builds avatar stacks must be present in the page
    assert "participant-stack" in body
    assert "participant-avatar" in body
    assert "strHsl" in body


@pytest.mark.anyio
async def test_session_list_page_contains_commit_pill_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions list page HTML contains commit count pill JS reference."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    body = response.text
    assert "session-commit-pill" in body


@pytest.mark.anyio
async def test_session_list_page_contains_live_indicator_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions list page HTML contains pulsing LIVE indicator JS reference."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    body = response.text
    assert "session-live-pulse" in body


@pytest.mark.anyio
async def test_session_list_page_contains_notes_preview_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions list page HTML contains notes preview JS reference."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    body = response.text
    assert "session-notes-preview" in body
    assert "notesPreview" in body


@pytest.mark.anyio
async def test_session_list_page_contains_location_tag_markup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions list page HTML contains location tag JS reference."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    body = response.text
    assert "session-location-tag" in body


async def test_contour_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/analysis/{ref}/contour returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_contour_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Contour analysis page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_contour_page_contains_graph_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Contour page must contain pitch-curve graph, shape badge, and tessitura elements."""
    repo_id = await _make_repo(db_session)
    ref = "cafebabe12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code == 200
    body = response.text
    assert "Melodic Contour" in body
    assert "pitchCurveSvg" in body or "pitchCurve" in body
    assert "Tessitura" in body
    assert "Shape" in body
    assert "track-inp" in body
    assert repo_id in body


@pytest.mark.anyio
async def test_contour_json_response(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/contour returns ContourData.

    Verifies that the JSON response includes shape classification labels and
    the pitch_curve array that the contour page visualises.
    """
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "contour-test-repo", "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id = resp.json()["repoId"]

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/contour",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "contour"
    assert body["ref"] == "main"
    data = body["data"]
    assert "shape" in data
    assert "pitchCurve" in data
    assert "overallDirection" in data
    assert "directionChanges" in data
    assert len(data["pitchCurve"]) > 0
    assert data["shape"] in ("arch", "ascending", "descending", "flat", "wave")


@pytest.mark.anyio
async def test_tempo_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/analysis/{ref}/tempo returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_tempo_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tempo analysis page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_tempo_page_contains_bpm_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tempo page must contain BPM display, stability bar, and tempo-change timeline."""
    repo_id = await _make_repo(db_session)
    ref = "feedface5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code == 200
    body = response.text
    assert "Tempo Analysis" in body
    assert "BPM" in body
    assert "Stability" in body
    assert "tempoChangeSvg" in body or "tempoChanges" in body or "Tempo Changes" in body
    assert repo_id in body


@pytest.mark.anyio
async def test_tempo_json_response(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/tempo returns TempoData.

    Verifies that the JSON response includes BPM, stability, time feel, and
    tempo_changes history that the tempo page visualises.
    """
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "tempo-test-repo", "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id = resp.json()["repoId"]

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/tempo",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "tempo"
    assert body["ref"] == "main"
    data = body["data"]
    assert "bpm" in data
    assert "stability" in data
    assert "timeFeel" in data
    assert "tempoChanges" in data
    assert data["bpm"] > 0
    assert 0.0 <= data["stability"] <= 1.0
    assert isinstance(data["tempoChanges"], list)


# ---------------------------------------------------------------------------
# Form and structure page tests (issue #225)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_form_structure_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/form-structure/{ref} returns 200 HTML without auth."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Form" in body


@pytest.mark.anyio
async def test_form_structure_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form-structure UI page must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_form_structure_page_contains_section_map(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form-structure page embeds section map SVG rendering logic."""
    repo_id = await _make_repo(db_session)
    ref = "cafebabe1234"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "Section Map" in body
    assert "renderSectionMap" in body
    assert "sectionMap" in body


@pytest.mark.anyio
async def test_form_structure_page_contains_repetition_panel(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form-structure page embeds repetition structure panel."""
    repo_id = await _make_repo(db_session)
    ref = "feedface0123"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "Repetition" in body
    assert "renderRepetition" in body


@pytest.mark.anyio
async def test_form_structure_page_contains_heatmap(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form-structure page embeds section comparison heatmap renderer."""
    repo_id = await _make_repo(db_session)
    ref = "deadcafe5678"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "Section Comparison" in body
    assert "renderHeatmap" in body
    assert "sectionComparison" in body


@pytest.mark.anyio
async def test_form_structure_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form-structure page includes the JWT token form and musehub.js auth infrastructure."""
    repo_id = await _make_repo(db_session)
    ref = "babe1234abcd"
    response = await client.get(f"/musehub/ui/{repo_id}/form-structure/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "musehub.js" in body
    assert "token-form" in body


@pytest.mark.anyio
async def test_form_structure_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/form-structure/{ref} returns JSON with required fields."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/form-structure/{ref}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "repoId" in body
    assert "ref" in body
    assert "formLabel" in body
    assert "timeSignature" in body
    assert "beatsPerBar" in body
    assert "totalBars" in body
    assert "sectionMap" in body
    assert "repetitionStructure" in body
    assert "sectionComparison" in body
    assert body["repoId"] == repo_id
    assert body["ref"] == ref


@pytest.mark.anyio
async def test_form_structure_json_section_map_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Each sectionMap entry has label, startBar, endBar, barCount, and colorHint."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/form-structure/{ref}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    sections = body["sectionMap"]
    assert len(sections) > 0
    for sec in sections:
        assert "label" in sec
        assert "function" in sec
        assert "startBar" in sec
        assert "endBar" in sec
        assert "barCount" in sec
        assert "colorHint" in sec
        assert sec["startBar"] >= 1
        assert sec["endBar"] >= sec["startBar"]
        assert sec["barCount"] >= 1


@pytest.mark.anyio
async def test_form_structure_json_heatmap_is_symmetric(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Section comparison heatmap matrix must be square and symmetric with diagonal 1.0."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/form-structure/{ref}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    heatmap = body["sectionComparison"]
    labels = heatmap["labels"]
    matrix = heatmap["matrix"]
    n = len(labels)
    assert len(matrix) == n
    for i in range(n):
        assert len(matrix[i]) == n
        assert matrix[i][i] == 1.0
    for i in range(n):
        for j in range(n):
            assert 0.0 <= matrix[i][j] <= 1.0


@pytest.mark.anyio
async def test_form_structure_json_404_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/form-structure/{ref} returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/form-structure/abc123",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_form_structure_json_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/form-structure/{ref} returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/form-structure/abc123",
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Emotion map page tests (issue #227, migrated to owner/slug routing in #348)
# ---------------------------------------------------------------------------

_EMOTION_REF = "deadbeef12345678"


@pytest.mark.anyio
async def test_emotion_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/emotion returns 200 HTML without auth."""
    await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{_EMOTION_REF}/emotion")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Emotion" in body


@pytest.mark.anyio
async def test_emotion_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion UI page must be accessible without an Authorization header (HTML shell)."""
    await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{_EMOTION_REF}/emotion")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_emotion_page_includes_charts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion page embeds valence-arousal plot helper and axis labels."""
    await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{_EMOTION_REF}/emotion")
    assert response.status_code == 200
    body = response.text
    assert "valenceArousalPlot" in body
    assert "Valence" in body
    assert "Arousal" in body
    assert "Tension" in body


@pytest.mark.anyio
async def test_emotion_page_includes_filters(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion page embeds the primary emotion label and confidence display."""
    await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{_EMOTION_REF}/emotion")
    assert response.status_code == 200
    body = response.text
    assert "primaryEmotion" in body
    assert "confidence" in body


@pytest.mark.anyio
async def test_emotion_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/emotion-map returns required fields."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/{_EMOTION_REF}/emotion-map",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repoId"] == repo_id
    assert body["ref"] == _EMOTION_REF
    assert "computedAt" in body
    assert "summaryVector" in body
    sv = body["summaryVector"]
    for axis in ("energy", "valence", "tension", "darkness"):
        assert axis in sv
        assert 0.0 <= sv[axis] <= 1.0
    assert "evolution" in body
    assert isinstance(body["evolution"], list)
    assert len(body["evolution"]) > 0
    assert "narrative" in body
    assert len(body["narrative"]) > 0
    assert "source" in body


@pytest.mark.anyio
async def test_emotion_trajectory(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Cross-commit trajectory must be a list of commit snapshots with emotion vectors."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/{_EMOTION_REF}/emotion-map",
        headers=auth_headers,
    )
    assert response.status_code == 200
    trajectory = response.json()["trajectory"]
    assert isinstance(trajectory, list)
    assert len(trajectory) >= 2
    for snapshot in trajectory:
        assert "commitId" in snapshot
        assert "message" in snapshot
        assert "primaryEmotion" in snapshot
        vector = snapshot["vector"]
        for axis in ("energy", "valence", "tension", "darkness"):
            assert axis in vector
            assert 0.0 <= vector[axis] <= 1.0


@pytest.mark.anyio
async def test_emotion_drift_distances(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Drift list must have exactly len(trajectory) - 1 entries."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/{_EMOTION_REF}/emotion-map",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    trajectory = body["trajectory"]
    drift = body["drift"]
    assert isinstance(drift, list)
    assert len(drift) == len(trajectory) - 1
    for entry in drift:
        assert "fromCommit" in entry
        assert "toCommit" in entry
        assert "drift" in entry
        assert entry["drift"] >= 0.0
        assert "dominantChange" in entry
        assert entry["dominantChange"] in ("energy", "valence", "tension", "darkness")


# ---------------------------------------------------------------------------
# owner/slug navigation link correctness (regression for PR #282)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must inject owner/slug base URL, not the internal UUID.

    Before the fix, every handler except repo_page used ``const base =
    '/musehub/ui/' + repoId``.  That produced UUID-based hrefs that 404 under
    the new /{owner}/{repo_slug} routing.  This test guards the regression.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    # JS base variable must use owner/slug, not UUID concatenation
    assert '"/musehub/ui/testuser/test-beats"' in body
    # UUID-concatenation pattern must NOT appear
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_commit_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page back-to-repo link must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    commit_id = "abc1234567890123456789012345678901234567"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_graph_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page back-to-repo link must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_pr_list_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page navigation must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_releases_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Releases page navigation must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_unknown_owner_slug_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{unknown-owner}/{unknown-slug} must return 404."""
    response = await client.get("/musehub/ui/nobody/nonexistent-repo")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Issue #199 — Design System Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_design_tokens_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/tokens.css must return 200 with CSS content-type.

    Verifies the design token file is reachable at its canonical static path.
    If this fails, every MuseHub page will render unstyled because the CSS
    custom properties (--bg-base, --color-accent, etc.) will be missing.
    """
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    body = response.text
    assert "--bg-base" in body
    assert "--color-accent" in body
    assert "--dim-harmonic" in body


@pytest.mark.anyio
async def test_components_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/components.css must return 200 with CSS content.

    Verifies the component class file is reachable.  These classes (.card,
    .badge, .btn, etc.) are used on every MuseHub page.
    """
    response = await client.get("/musehub/static/components.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    body = response.text
    assert ".badge" in body
    assert ".btn" in body
    assert ".card" in body


@pytest.mark.anyio
async def test_layout_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/layout.css must return 200."""
    response = await client.get("/musehub/static/layout.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".container" in response.text


@pytest.mark.anyio
async def test_icons_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/icons.css must return 200."""
    response = await client.get("/musehub/static/icons.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".icon-mid" in response.text


@pytest.mark.anyio
async def test_music_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/music.css must return 200."""
    response = await client.get("/musehub/static/music.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".piano-roll" in response.text


@pytest.mark.anyio
async def test_repo_page_uses_design_system(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page HTML must reference all five design system CSS files.

    This is the regression guard for the monolithic _CSS removal.  If the
    _page() helper ever reverts to embedding CSS inline, this test will
    catch it by asserting the external link tags are present.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/static/tokens.css" in body
    assert "/musehub/static/components.css" in body
    assert "/musehub/static/layout.css" in body
    assert "/musehub/static/icons.css" in body
    assert "/musehub/static/music.css" in body


@pytest.mark.anyio
async def test_responsive_meta_tag_present_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_responsive_meta_tag_present_pr_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_responsive_meta_tag_present_issues_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issues page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_design_tokens_css_contains_dimension_colors(
    client: AsyncClient,
) -> None:
    """tokens.css must define all five musical dimension color tokens.

    These tokens are used in piano rolls, radar charts, and diff heatmaps.
    Missing tokens would break analysis page visualisations silently.
    """
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    body = response.text
    for dim in ("harmonic", "rhythmic", "melodic", "structural", "dynamic"):
        assert f"--dim-{dim}:" in body, f"Missing dimension token --dim-{dim}"


@pytest.mark.anyio
async def test_design_tokens_css_contains_track_colors(
    client: AsyncClient,
) -> None:
    """tokens.css must define all 8 track color tokens (--track-0 through --track-7)."""
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    body = response.text
    for i in range(8):
        assert f"--track-{i}:" in body, f"Missing track color token --track-{i}"


@pytest.mark.anyio
async def test_badge_variants_in_components_css(client: AsyncClient) -> None:
    """components.css must define all required badge variants including .badge-clean and .badge-dirty."""
    response = await client.get("/musehub/static/components.css")
    assert response.status_code == 200
    body = response.text
    for variant in ("open", "closed", "merged", "active", "clean", "dirty"):
        assert f".badge-{variant}" in body, f"Missing badge variant .badge-{variant}"


@pytest.mark.anyio
async def test_file_type_icons_in_icons_css(client: AsyncClient) -> None:
    """icons.css must define icon classes for all required file types."""
    response = await client.get("/musehub/static/icons.css")
    assert response.status_code == 200
    body = response.text
    for ext in ("mid", "mp3", "wav", "json", "webp", "xml", "abc"):
        assert f".icon-{ext}" in body, f"Missing file-type icon .icon-{ext}"


@pytest.mark.anyio
async def test_no_inline_css_on_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must NOT embed the old monolithic CSS string inline.

    Regression test: verifies the _CSS removal was not accidentally reverted.
    The old _CSS block contained the literal string 'background: #0d1117'
    inside a <style> tag in the <head>.  After the design system migration,
    all styling comes from external files.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    body = response.text
    # Find the <head> section — inline CSS should not appear there
    head_end = body.find("</head>")
    head_section = body[:head_end] if head_end != -1 else body
    # The old monolithic block started with "box-sizing: border-box"
    # If it appears inside <head>, the migration has been reverted.
    assert "box-sizing: border-box; margin: 0; padding: 0;" not in head_section


# ---------------------------------------------------------------------------
# Analysis dashboard UI tests (issue #221)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analysis_dashboard_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref} returns 200 HTML without a JWT."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Analysis" in body
    assert "test-beats" in body


@pytest.mark.anyio
async def test_analysis_dashboard_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Analysis dashboard HTML shell must be accessible without an Authorization header."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_analysis_dashboard_all_dimension_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dashboard HTML embeds all 10 required dimension card labels in the page script.

    Regression test for issue #221: if any card label is missing the JS template
    will silently skip rendering that dimension, so agents get an incomplete picture.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    for label in ("Key", "Tempo", "Meter", "Chord Map", "Dynamics",
                  "Groove", "Emotion", "Form", "Motifs", "Contour"):
        assert label in body, f"Expected dimension label {label!r} in dashboard HTML"


@pytest.mark.anyio
async def test_analysis_dashboard_sparkline_logic_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dashboard HTML includes sparkline rendering logic for velocity/pitch visualisations."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    assert "sparkline" in body
    assert "velocityCurve" in body or "pitchCurve" in body


@pytest.mark.anyio
async def test_analysis_dashboard_card_links_to_dimensions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Each dimension card must link to the per-dimension analysis detail page.

    The card href is built client-side from ``base + '/analysis/' + ref + '/' + id``,
    so the JS template string must reference ``/analysis/`` as the path segment.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    assert "/analysis/" in body




# ---------------------------------------------------------------------------
# Motifs browser page — issue #224
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_motifs_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/motifs returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_motifs_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs UI page must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_motifs_page_contains_filter_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds client-side track and section filter controls."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "track-filter" in body
    assert "section-filter" in body


@pytest.mark.anyio
async def test_motifs_page_contains_piano_roll_renderer(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds the piano roll renderer JavaScript function."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "pianoRollHtml" in body


@pytest.mark.anyio
async def test_motifs_page_contains_recurrence_grid(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds the recurrence grid (heatmap) renderer."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "recurrenceGridHtml" in body


@pytest.mark.anyio
async def test_motifs_page_shows_transformation_badges(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page includes transformation badge renderer for inversion/retrograde labels."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "transformationsHtml" in body
    assert "inversion" in body


# ---------------------------------------------------------------------------
# Content negotiation & repo home page tests — issue #200 / #203
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_repo_page_html_default(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug} with no Accept header returns HTML by default."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "testuser" in body
    assert "test-beats" in body


@pytest.mark.anyio
async def test_repo_home_shows_stats(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo home page embeds JS that fetches and renders the stats bar."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "stats-bar" in body
    assert "loadStats" in body


@pytest.mark.anyio
async def test_repo_home_recent_commits(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo home page embeds JS that renders the recent commits section."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "recent-commits" in body
    assert "loadRecentCommits" in body


@pytest.mark.anyio
async def test_repo_home_audio_player(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo home page embeds the audio player section and JS loader."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "audio-player-section" in body
    assert "loadAudioPlayer" in body


@pytest.mark.anyio
async def test_repo_page_json_accept(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug} with Accept: application/json returns JSON repo data."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    # RepoResponse fields serialised as camelCase
    assert "repoId" in data or "repo_id" in data or "slug" in data or "name" in data


@pytest.mark.anyio
async def test_commits_page_json_format_param(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/commits?format=json returns JSON commit list."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/commits?format=json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    # CommitListResponse has commits (list) and total (int)
    assert "commits" in data
    assert "total" in data
    assert isinstance(data["commits"], list)


@pytest.mark.anyio
async def test_json_response_camelcase(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response from repo page uses camelCase keys matching API convention."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    # All top-level keys must be camelCase — no underscores allowed in field names
    # (Pydantic by_alias=True serialises snake_case fields as camelCase)
    snake_keys = [k for k in data if "_" in k]
    assert snake_keys == [], f"Expected camelCase keys but found snake_case: {snake_keys}"


@pytest.mark.anyio
async def test_commits_list_html_default(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/commits with no Accept header returns HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/commits")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# Tree browser tests — issue #204
# ---------------------------------------------------------------------------


async def _seed_tree_fixtures(db_session: AsyncSession) -> str:
    """Seed a public repo with a branch and objects for tree browser tests.

    Creates:
    - repo: testuser/tree-test (public)
    - branch: main (head pointing at a dummy commit)
    - objects: tracks/bass.mid, tracks/keys.mp3, metadata.json, cover.webp
    Returns repo_id.
    """
    repo = MusehubRepo(
        name="tree-test",
        owner="testuser",
        slug="tree-test",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()

    commit = MusehubCommit(
        commit_id="abc123def456",
        repo_id=str(repo.repo_id),
        message="initial",
        branch="main",
        author="testuser",
        timestamp=datetime.now(tz=UTC),
    )
    db_session.add(commit)

    branch = MusehubBranch(
        repo_id=str(repo.repo_id),
        name="main",
        head_commit_id="abc123def456",
    )
    db_session.add(branch)

    for path, size in [
        ("tracks/bass.mid", 2048),
        ("tracks/keys.mp3", 8192),
        ("metadata.json", 512),
        ("cover.webp", 4096),
    ]:
        obj = MusehubObject(
            object_id=f"sha256:{path.replace('/', '_')}",
            repo_id=str(repo.repo_id),
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/{path.replace('/', '_')}",
        )
        db_session.add(obj)

    await db_session.commit()
    return str(repo.repo_id)


@pytest.mark.anyio
async def test_tree_root_lists_directories(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo}/tree/{ref} returns 200 HTML with tree JS."""
    await _seed_tree_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/tree-test/tree/main")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "tree" in body
    assert "branch-sel" in body or "ref-selector" in body or "loadTree" in body


@pytest.mark.anyio
async def test_tree_subdirectory_lists_files(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /{owner}/{repo}/tree/{ref}/tracks returns 200 HTML for the subdirectory."""
    await _seed_tree_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/tree-test/tree/main/tracks")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "tracks" in body
    assert "loadTree" in body


@pytest.mark.anyio
async def test_tree_file_icons_by_type(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tree template includes JS that maps extensions to file-type icons."""
    await _seed_tree_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/tree-test/tree/main")
    assert response.status_code == 200
    body = response.text
    # Piano icon for .mid files
    assert ".mid" in body or "midi" in body
    # Waveform icon for .mp3/.wav files
    assert ".mp3" in body or ".wav" in body
    # Braces for .json
    assert ".json" in body
    # Photo for images
    assert ".webp" in body or ".png" in body


@pytest.mark.anyio
async def test_tree_breadcrumbs_correct(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tree page breadcrumb contains owner, repo, tree, and ref."""
    await _seed_tree_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/tree-test/tree/main")
    assert response.status_code == 200
    body = response.text
    assert "testuser" in body
    assert "tree-test" in body
    assert "tree" in body
    assert "main" in body


@pytest.mark.anyio
async def test_tree_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/tree/{ref} returns JSON with tree entries."""
    repo_id = await _seed_tree_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/tree/main"
        f"?owner=testuser&repo_slug=tree-test"
    )
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert data["ref"] == "main"
    assert data["dirPath"] == ""
    # Root should show: 'tracks' dir, 'metadata.json', 'cover.webp'
    names = {e["name"] for e in data["entries"]}
    assert "tracks" in names
    assert "metadata.json" in names
    assert "cover.webp" in names
    # 'bass.mid' should NOT appear at root (it's under tracks/)
    assert "bass.mid" not in names
    # tracks entry must be a directory
    tracks_entry = next(e for e in data["entries"] if e["name"] == "tracks")
    assert tracks_entry["type"] == "dir"
    assert tracks_entry["sizeBytes"] is None


@pytest.mark.anyio
async def test_tree_unknown_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/tree/{unknown_ref} returns 404."""
    repo_id = await _seed_tree_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/tree/does-not-exist"
        f"?owner=testuser&repo_slug=tree-test"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Harmony analysis page tests — issue #222
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_harmony_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/analysis/{ref}/harmony returns 200 HTML without auth."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "harmony" in body.lower()


@pytest.mark.anyio
async def test_harmony_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony analysis page HTML shell must be accessible without a JWT (not 401)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef00001234"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_harmony_page_contains_key_display(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page JS must reference key, mode, and relative-key fields from the API response."""
    repo_id = await _make_repo(db_session)
    ref = "cafe0000000000000001"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    # Client-side JS field references (camelCase from Pydantic CamelModel)
    assert "tonic" in body
    assert "mode" in body
    assert "relativeKey" in body or "relative" in body.lower()
    assert "keyConfidence" in body or "key_confidence" in body.lower()


@pytest.mark.anyio
async def test_harmony_page_contains_chord_timeline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must embed the chord progression timeline renderer."""
    repo_id = await _make_repo(db_session)
    ref = "babe0000000000000002"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "renderChordTimeline" in body
    assert "chordProgression" in body
    assert "Chord Progression Timeline" in body


@pytest.mark.anyio
async def test_harmony_page_contains_tension_curve(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must embed the tension curve SVG renderer."""
    repo_id = await _make_repo(db_session)
    ref = "face0000000000000003"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "renderTensionCurve" in body
    assert "tensionCurve" in body
    assert "Tension Curve" in body


@pytest.mark.anyio
async def test_harmony_page_contains_modulation_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must include the modulation markers section."""
    repo_id = await _make_repo(db_session)
    ref = "feed0000000000000004"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "renderModulationPoints" in body
    assert "modulationPoints" in body
    assert "Modulation Points" in body


@pytest.mark.anyio
async def test_harmony_page_contains_filter_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must include track and section filter dropdowns."""
    repo_id = await _make_repo(db_session)
    ref = "beef0000000000000005"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "track-sel" in body
    assert "section-sel" in body
    assert "setFilter" in body
    # Common track options present
    assert "bass" in body
    assert "All tracks" in body


@pytest.mark.anyio
async def test_harmony_page_contains_key_history(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must include key history across commits section."""
    repo_id = await _make_repo(db_session)
    ref = "0000000000000000dead"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "Key History Across Commits" in body
    assert "loadKeyHistory" in body
    assert "key-history-content" in body


@pytest.mark.anyio
async def test_harmony_page_contains_voice_leading(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must include voice-leading quality indicator."""
    repo_id = await _make_repo(db_session)
    ref = "1111111111111111beef"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert "renderVoiceLeading" in body
    assert "Voice-Leading Quality" in body


@pytest.mark.anyio
async def test_harmony_page_has_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page must include the JWT token form so unauthenticated visitors can sign in.

    Auth state (localStorage / musehub_token) is managed by musehub.js; the
    HTML shell must include the token-form element and the musehub.js script tag.
    """
    repo_id = await _make_repo(db_session)
    ref = "2222222222222222cafe"
    response = await client.get(f"/musehub/ui/{repo_id}/analysis/{ref}/harmony")
    assert response.status_code == 200
    body = response.text
    assert 'id="token-form"' in body
    assert "musehub.js" in body


@pytest.mark.skip(reason="Pre-existing failure: harmony API response missing 'dimension' field — see #540")
@pytest.mark.anyio
async def test_harmony_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/harmony returns full harmonic JSON."""
    repo_id = await _make_repo(db_session)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "harmony"
    assert body["ref"] == "main"
    data = body["data"]
    # Key and mode present
    assert "tonic" in data
    assert "mode" in data
    assert "keyConfidence" in data
    # Chord progression
    assert "chordProgression" in data
    assert isinstance(data["chordProgression"], list)
    if data["chordProgression"]:
        chord = data["chordProgression"][0]
        assert "beat" in chord
        assert "chord" in chord
        assert "function" in chord
        assert "tension" in chord
    # Tension curve
    assert "tensionCurve" in data
    assert isinstance(data["tensionCurve"], list)
    # Modulation points
    assert "modulationPoints" in data
    assert isinstance(data["modulationPoints"], list)
    # Total beats
    assert "totalBeats" in data
    assert data["totalBeats"] > 0

# Listen page tests (issue #213)
# ---------------------------------------------------------------------------


async def _seed_listen_fixtures(db_session: AsyncSession) -> str:
    """Seed a repo with audio objects for listen-page tests; return repo_id."""
    repo = MusehubRepo(
        name="listen-test",
        owner="testuser",
        slug="listen-test",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    for path, size in [
        ("mix/full_mix.mp3", 204800),
        ("tracks/bass.mp3", 51200),
        ("tracks/keys.mp3", 61440),
        ("tracks/bass.webp", 8192),
    ]:
        obj = MusehubObject(
            object_id=f"sha256:{path.replace('/', '_')}",
            repo_id=repo_id,
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/{path.replace('/', '_')}",
        )
        db_session.add(obj)
    await db_session.commit()
    return repo_id


@pytest.mark.anyio
async def test_listen_page_full_mix(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo}/listen/{ref} returns 200 HTML with player UI."""
    await _seed_listen_fixtures(db_session)
    ref = "main"
    response = await client.get(f"/musehub/ui/testuser/listen-test/listen/{ref}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "listen" in body.lower()
    # Full-mix player elements present
    assert "mix-play-btn" in body
    assert "mix-progress-bar" in body


@pytest.mark.anyio
async def test_listen_page_track_listing(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page HTML embeds track-listing JS that renders per-track controls."""
    await _seed_listen_fixtures(db_session)
    ref = "main"
    response = await client.get(f"/musehub/ui/testuser/listen-test/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    # Track-listing JavaScript is embedded
    assert "track-list" in body
    assert "track-play-btn" in body or "playTrack" in body


@pytest.mark.anyio
async def test_listen_page_no_renders_fallback(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page renders a friendly fallback when no audio artifacts exist."""
    # Repo with no objects at all
    repo = MusehubRepo(
        name="silent-repo",
        owner="testuser",
        slug="silent-repo",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()

    response = await client.get("/musehub/ui/testuser/silent-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    # Fallback UI marker present (no-renders state)
    assert "no-renders" in body or "No audio" in body or "hasRenders" in body


@pytest.mark.anyio
async def test_listen_page_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo}/listen/{ref}?format=json returns TrackListingResponse."""
    await _seed_listen_fixtures(db_session)
    ref = "main"
    response = await client.get(
        f"/musehub/ui/testuser/listen-test/listen/{ref}",
        params={"format": "json"},
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert "repoId" in body
    assert "ref" in body
    assert body["ref"] == ref
    assert "tracks" in body
    assert "hasRenders" in body
    assert isinstance(body["tracks"], list)


# ---------------------------------------------------------------------------
# Issue #366 — musehub_listen service function (direct unit tests)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_track_listing_returns_full_mix_and_tracks(
    db_session: AsyncSession,
) -> None:
    """build_track_listing() returns a populated TrackListingResponse with mix + stems."""
    from maestro.services.musehub_listen import build_track_listing

    repo = MusehubRepo(
        name="svc-listen-test",
        owner="svcuser",
        slug="svc-listen-test",
        visibility="public",
        owner_user_id="svc-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    for path, size in [
        ("mix/full_mix.mp3", 204800),
        ("tracks/bass.mp3", 51200),
        ("tracks/keys.mp3", 61440),
        ("tracks/bass.webp", 8192),
    ]:
        obj = MusehubObject(
            object_id=f"sha256:svc_{path.replace('/', '_')}",
            repo_id=repo_id,
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/svc_{path.replace('/', '_')}",
        )
        db_session.add(obj)
    await db_session.commit()

    result = await build_track_listing(db_session, repo_id, "main")

    assert result.has_renders is True
    assert result.repo_id == repo_id
    assert result.ref == "main"
    # full-mix URL points to the mix file (contains "mix" keyword)
    assert result.full_mix_url is not None
    assert "full_mix" in result.full_mix_url or "mix" in result.full_mix_url
    # Two audio tracks (bass.mp3 + keys.mp3); bass.webp is not audio
    assert len(result.tracks) == 3  # mix/full_mix.mp3, tracks/bass.mp3, tracks/keys.mp3
    track_paths = {t.path for t in result.tracks}
    assert "tracks/bass.mp3" in track_paths
    assert "tracks/keys.mp3" in track_paths
    # Piano-roll URL attached to bass.mp3 (matching bass.webp exists)
    bass_track = next(t for t in result.tracks if t.path == "tracks/bass.mp3")
    assert bass_track.piano_roll_url is not None


@pytest.mark.anyio
async def test_build_track_listing_no_audio_returns_empty(
    db_session: AsyncSession,
) -> None:
    """build_track_listing() returns has_renders=False when no audio objects exist."""
    from maestro.services.musehub_listen import build_track_listing

    repo = MusehubRepo(
        name="svc-silent-test",
        owner="svcuser",
        slug="svc-silent-test",
        visibility="public",
        owner_user_id="svc-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    # Only a non-audio object
    obj = MusehubObject(
        object_id="sha256:svc_midi",
        repo_id=repo_id,
        path="tracks/bass.mid",
        size_bytes=1024,
        disk_path="/tmp/svc_bass.mid",
    )
    db_session.add(obj)
    await db_session.commit()

    result = await build_track_listing(db_session, repo_id, "dev")

    assert result.has_renders is False
    assert result.full_mix_url is None
    assert result.tracks == []


@pytest.mark.anyio
async def test_build_track_listing_no_mix_keyword_uses_first_alphabetically(
    db_session: AsyncSession,
) -> None:
    """When no file matches _FULL_MIX_KEYWORDS, the first audio file (by path) is used."""
    from maestro.services.musehub_listen import build_track_listing

    repo = MusehubRepo(
        name="svc-nomix-test",
        owner="svcuser",
        slug="svc-nomix-test",
        visibility="public",
        owner_user_id="svc-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    for path, size in [
        ("tracks/bass.mp3", 51200),
        ("tracks/drums.mp3", 61440),
    ]:
        obj = MusehubObject(
            object_id=f"sha256:svc_nomix_{path.replace('/', '_')}",
            repo_id=repo_id,
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/svc_nomix_{path.replace('/', '_')}",
        )
        db_session.add(obj)
    await db_session.commit()

    result = await build_track_listing(db_session, repo_id, "main")

    assert result.has_renders is True
    # 'tracks/bass.mp3' sorts before 'tracks/drums.mp3'
    assert result.full_mix_url is not None
    assert "bass" in result.full_mix_url


# ---------------------------------------------------------------------------
# Issue #206 — Commit list page
# ---------------------------------------------------------------------------

_COMMIT_LIST_OWNER = "commitowner"
_COMMIT_LIST_SLUG = "commit-list-repo"
_SHA_MAIN_1 = "aa001122334455667788990011223344556677889900"
_SHA_MAIN_2 = "bb001122334455667788990011223344556677889900"
_SHA_MAIN_MERGE = "cc001122334455667788990011223344556677889900"
_SHA_FEAT = "ff001122334455667788990011223344556677889900"


async def _seed_commit_list_repo(
    db_session: AsyncSession,
) -> str:
    """Seed a repo with 2 commits on main, 1 merge commit, and 1 on feat branch."""
    repo = MusehubRepo(
        name=_COMMIT_LIST_SLUG,
        owner=_COMMIT_LIST_OWNER,
        slug=_COMMIT_LIST_SLUG,
        visibility="public",
        owner_user_id="commit-owner-uid",
    )
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)

    branch_main = MusehubBranch(repo_id=repo_id, name="main", head_commit_id=_SHA_MAIN_MERGE)
    branch_feat = MusehubBranch(repo_id=repo_id, name="feat/drums", head_commit_id=_SHA_FEAT)
    db_session.add_all([branch_main, branch_feat])

    now = datetime.now(UTC)
    commits = [
        MusehubCommit(
            commit_id=_SHA_MAIN_1,
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="feat(bass): root commit with walking bass line",
            author="composer@stori.io",
            timestamp=now - timedelta(hours=4),
        ),
        MusehubCommit(
            commit_id=_SHA_MAIN_2,
            repo_id=repo_id,
            branch="main",
            parent_ids=[_SHA_MAIN_1],
            message="feat(keys): add rhodes chord voicings in verse",
            author="composer@stori.io",
            timestamp=now - timedelta(hours=2),
        ),
        MusehubCommit(
            commit_id=_SHA_MAIN_MERGE,
            repo_id=repo_id,
            branch="main",
            parent_ids=[_SHA_MAIN_2, _SHA_FEAT],
            message="merge(feat/drums): integrate drum pattern into main",
            author="composer@stori.io",
            timestamp=now - timedelta(hours=1),
        ),
        MusehubCommit(
            commit_id=_SHA_FEAT,
            repo_id=repo_id,
            branch="feat/drums",
            parent_ids=[_SHA_MAIN_1],
            message="feat(drums): add kick and snare pattern at 120 BPM",
            author="drummer@stori.io",
            timestamp=now - timedelta(hours=3),
        ),
    ]
    db_session.add_all(commits)
    await db_session.commit()
    return repo_id


@pytest.mark.anyio
async def test_commits_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /{owner}/{repo}/commits returns 200 HTML."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Muse Hub" in resp.text


@pytest.mark.anyio
async def test_commits_list_page_shows_commit_sha(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit SHA (first 8 chars) appears in the rendered HTML."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    # All 4 commits should appear (per_page=30 default, total=4)
    assert _SHA_MAIN_1[:8] in resp.text
    assert _SHA_MAIN_2[:8] in resp.text
    assert _SHA_MAIN_MERGE[:8] in resp.text
    assert _SHA_FEAT[:8] in resp.text


@pytest.mark.anyio
async def test_commits_list_page_shows_commit_message(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit messages appear truncated in commit rows."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    assert "walking bass line" in resp.text
    assert "rhodes chord voicings" in resp.text


@pytest.mark.anyio
async def test_commits_list_page_dag_indicator(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DAG node CSS class is present in the HTML for every commit row."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    assert "dag-node" in resp.text
    assert "commit-list-row" in resp.text


@pytest.mark.anyio
async def test_commits_list_page_merge_indicator(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Merge commits display the merge indicator and dag-node-merge class."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    assert "dag-node-merge" in resp.text
    assert "merge" in resp.text.lower()


@pytest.mark.anyio
async def test_commits_list_page_branch_selector(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch <select> dropdown is present when the repo has branches."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    # Select element with branch options
    assert "branch-sel" in resp.text
    assert "main" in resp.text
    assert "feat/drums" in resp.text


@pytest.mark.anyio
async def test_commits_list_page_graph_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Link to the DAG graph page is present."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits")
    assert resp.status_code == 200
    assert "/graph" in resp.text


@pytest.mark.anyio
async def test_commits_list_page_pagination_links(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Pagination nav links appear when total exceeds per_page."""
    await _seed_commit_list_repo(db_session)
    # Request per_page=2 so 4 commits produce 2 pages
    resp = await client.get(
        f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits?per_page=2&page=1"
    )
    assert resp.status_code == 200
    body = resp.text
    # "Older" link should be active (page 1 has no "Newer")
    assert "Older" in body
    # "Newer" should be disabled on page 1
    assert "Newer" in body
    assert "page=2" in body


@pytest.mark.anyio
async def test_commits_list_page_pagination_page2(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page 2 renders with Newer navigation active."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits?per_page=2&page=2"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "page=1" in body  # "Newer" link points back to page 1


@pytest.mark.anyio
async def test_commits_list_page_branch_filter_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?branch=main returns only main-branch commits in HTML."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits?branch=main"
    )
    assert resp.status_code == 200
    body = resp.text
    # main commits appear
    assert _SHA_MAIN_1[:8] in body
    assert _SHA_MAIN_2[:8] in body
    assert _SHA_MAIN_MERGE[:8] in body
    # feat/drums commit should NOT appear when filtered to main
    assert _SHA_FEAT[:8] not in body


@pytest.mark.anyio
async def test_commits_list_page_json_content_negotiation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns CommitListResponse JSON with commits and total."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits?format=json"
    )
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert "commits" in body
    assert "total" in body
    assert body["total"] == 4
    assert len(body["commits"]) == 4
    # Commits are newest first; merge commit has timestamp now-1h (most recent)
    commit_ids = [c["commitId"] for c in body["commits"]]
    assert commit_ids[0] == _SHA_MAIN_MERGE


@pytest.mark.anyio
async def test_commits_list_page_json_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON with per_page=1&page=2 returns the second commit."""
    await _seed_commit_list_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_COMMIT_LIST_OWNER}/{_COMMIT_LIST_SLUG}/commits"
        "?format=json&per_page=1&page=2"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert len(body["commits"]) == 1
    # Page 2 (newest-first) is the second most-recent commit.
    # Newest: _SHA_MAIN_MERGE (now-1h), then _SHA_MAIN_2 (now-2h)
    assert body["commits"][0]["commitId"] == _SHA_MAIN_2


@pytest.mark.anyio
async def test_commits_list_page_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo with no commits shows the empty state message."""
    repo = MusehubRepo(
        name="empty-repo",
        owner="emptyowner",
        slug="empty-repo",
        visibility="public",
        owner_user_id="empty-owner-uid",
    )
    db_session.add(repo)
    await db_session.commit()

    resp = await client.get("/musehub/ui/emptyowner/empty-repo/commits")
    assert resp.status_code == 200
    assert "No commits yet" in resp.text or "muse push" in resp.text


# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Commit detail enhancements — issue #207
# ---------------------------------------------------------------------------


async def _seed_commit_detail_fixtures(
    db_session: AsyncSession,
) -> tuple[str, str, str]:
    """Seed a public repo with a parent commit and a child commit.

    Returns (repo_id, parent_commit_id, child_commit_id).
    """
    repo = MusehubRepo(
        name="commit-detail-test",
        owner="testuser",
        slug="commit-detail-test",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)

    branch = MusehubBranch(
        repo_id=repo_id,
        name="main",
        head_commit_id=None,
    )
    db_session.add(branch)

    parent_commit_id = "aaaa0000111122223333444455556666aaaabbbb"
    child_commit_id  = "bbbb1111222233334444555566667777bbbbcccc"

    parent_commit = MusehubCommit(
        repo_id=repo_id,
        commit_id=parent_commit_id,
        branch="main",
        parent_ids=[],
        message="init: establish harmonic foundation in C major\n\nKey: C major\nBPM: 120\nMeter: 4/4",
        author="testuser",
        timestamp=datetime.now(UTC) - timedelta(hours=2),
        snapshot_id=None,
    )
    child_commit = MusehubCommit(
        repo_id=repo_id,
        commit_id=child_commit_id,
        branch="main",
        parent_ids=[parent_commit_id],
        message="feat(keys): add melodic piano phrase in D minor\n\nKey: D minor\nBPM: 132\nMeter: 3/4\nSection: verse",
        author="testuser",
        timestamp=datetime.now(UTC) - timedelta(hours=1),
        snapshot_id=None,
    )
    db_session.add(parent_commit)
    db_session.add(child_commit)
    await db_session.commit()
    return repo_id, parent_commit_id, child_commit_id


@pytest.mark.anyio
async def test_commit_detail_page_renders_enhanced_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page HTML includes enhanced metadata markers (SHA, parent, child nav)."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(f"/musehub/ui/testuser/commit-detail-test/commits/{sha}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Full SHA copyable button
    assert "copyToClipboard" in body
    assert "copy-btn" in body
    # Child links function
    assert "buildChildLinks" in body
    # Parent links in JS
    assert "parentLinks" in body
    # Dimension diff badges
    assert "dimBadge" in body
    assert "dim-badges-row" in body


@pytest.mark.anyio
async def test_commit_detail_artifact_browser_organized_by_type(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes organized artifact browser with section headings."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(f"/musehub/ui/testuser/commit-detail-test/commits/{sha}")
    assert response.status_code == 200
    body = response.text
    # Organized artifact sections by type
    assert "Piano Rolls" in body
    assert "buildArtifactSections" in body
    assert "METADATA_EXTS" in body
    # Before/After comparison
    assert "buildBeforeAfterAudio" in body
    assert "Before / After" in body


@pytest.mark.anyio
async def test_commit_detail_commit_body_line_breaks(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page renders commit message body with line breaks preserved."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(f"/musehub/ui/testuser/commit-detail-test/commits/{sha}")
    assert response.status_code == 200
    body = response.text
    assert "renderCommitBody" in body
    assert "commit-body-line" in body


@pytest.mark.anyio
async def test_commit_detail_diff_summary_endpoint_returns_five_dimensions(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/commits/{sha}/diff-summary returns 5 dimensions."""
    repo_id, _parent_id, child_id = await _seed_commit_detail_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{child_id}/diff-summary",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["commitId"] == child_id
    assert data["parentId"] == _parent_id
    assert "dimensions" in data
    assert len(data["dimensions"]) == 5
    dim_names = {d["dimension"] for d in data["dimensions"]}
    assert dim_names == {"harmonic", "rhythmic", "melodic", "structural", "dynamic"}
    for dim in data["dimensions"]:
        assert 0.0 <= dim["score"] <= 1.0
        assert dim["label"] in {"none", "low", "medium", "high"}
        assert dim["color"] in {"dim-none", "dim-low", "dim-medium", "dim-high"}
    assert "overallScore" in data
    assert 0.0 <= data["overallScore"] <= 1.0


@pytest.mark.anyio
async def test_commit_detail_diff_summary_root_commit_scores_one(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Diff summary for a root commit (no parent) scores all dimensions at 1.0."""
    repo_id, parent_id, _child_id = await _seed_commit_detail_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{parent_id}/diff-summary",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parentId"] is None
    for dim in data["dimensions"]:
        assert dim["score"] == 1.0
        assert dim["label"] == "high"


@pytest.mark.anyio
async def test_commit_detail_diff_summary_keyword_detection(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Diff summary detects melodic keyword in child commit message."""
    repo_id, _parent_id, child_id = await _seed_commit_detail_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/{child_id}/diff-summary",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    melodic_dim = next(d for d in data["dimensions"] if d["dimension"] == "melodic")
    # child commit message contains "melodic" keyword → non-zero score
    assert melodic_dim["score"] > 0.0


@pytest.mark.anyio
async def test_commit_detail_diff_summary_unknown_commit_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Diff summary for unknown commit ID returns 404."""
    repo_id, _p, _c = await _seed_commit_detail_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits/deadbeefdeadbeefdeadbeef/diff-summary",
        headers=auth_headers,    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Commit comment threads — issue #286
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_page_has_comment_section_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page HTML includes the comment section container (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "comments-section" in body
    assert "comments-list" in body


@pytest.mark.anyio
async def test_commit_page_has_comment_js_functions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page JS includes renderComments, submitComment, deleteComment (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "renderComments" in body
    assert "submitComment" in body
    assert "deleteComment" in body
    assert "showReplyForm" in body
    assert "loadComments" in body


@pytest.mark.anyio
async def test_commit_page_comment_calls_load_on_startup(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page calls loadComments() at startup (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "loadComments()" in body


@pytest.mark.anyio
async def test_commit_page_comment_uses_correct_api_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Comment section fetches /repos/{repo_id}/comments with target_type=commit (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "target_type=commit" in body
    assert "/comments" in body


@pytest.mark.anyio
async def test_commit_page_comment_has_avatar_logic(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page includes deterministic HSL avatar generation (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "avatarColor" in body
    assert "comment-avatar" in body


@pytest.mark.anyio
async def test_commit_page_comment_has_new_comment_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page includes the new-comment textarea form element (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "new-comment-form" in body
    assert "new-comment-body" in body
    assert "comment-submit-btn" in body


@pytest.mark.anyio
async def test_commit_page_comment_has_discussion_heading(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page includes 'Discussion' heading in the comment section (#286)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "Discussion" in body


# ---------------------------------------------------------------------------
# Commit detail enhancements — ref URL links, DB tags in panel, prose
# summary (issue #450)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_page_ref_tags_link_to_url_directly(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """tagPill() links ref: tags with URL values to the URL directly, not
    the namespace search page — so inspiration references are one click away (#450)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    # isRefUrl guard enables direct external URL linking for ref: tags
    assert "isRefUrl" in body
    assert "noopener" in body


@pytest.mark.anyio
async def test_commit_page_muse_tags_panel_passes_commit_tags(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """loadMuseTagsPanel is called with commit.tags so the DB-stored muse_tags
    annotations appear in the metadata panel with proper namespace pill colours (#450)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    # loadMuseTagsPanel must receive commit.tags, not be called with zero arguments
    assert "loadMuseTagsPanel(commit.tags" in body


@pytest.mark.anyio
async def test_commit_page_has_prose_summary(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes the buildProseSummary helper that generates a
    2-sentence AI prose description of musical content from available analysis
    data (#450)."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "buildProseSummary" in body
    assert "commit-prose-summary" in body


# ---------------------------------------------------------------------------
# Audio player — listen page tests (issue #211)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_listen_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/listen/{ref} must return 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_listen_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must be accessible without an Authorization header."""
    await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_listen_page_contains_waveform_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page HTML must contain the waveform container element."""
    await _make_repo(db_session)
    ref = "cafebabe1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "waveform" in body


@pytest.mark.anyio
async def test_listen_page_contains_play_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must include a play button element."""
    await _make_repo(db_session)
    ref = "feed1234abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "play-btn" in body


@pytest.mark.anyio
async def test_listen_page_contains_speed_selector(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must include the playback speed selector element."""
    await _make_repo(db_session)
    ref = "1a2b3c4d5e6f7890"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "speed-sel" in body


@pytest.mark.anyio
async def test_listen_page_contains_ab_loop_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must include A/B loop controls (loop info + clear button)."""
    await _make_repo(db_session)
    ref = "aabbccddeeff0011"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "loop-info" in body
    assert "loop-clear-btn" in body


@pytest.mark.anyio
async def test_listen_page_loads_wavesurfer_vendor(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must load the vendored wavesurfer.min.js — no external CDN."""
    await _make_repo(db_session)
    ref = "112233445566778899"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    # Must reference the local vendor path — never an external CDN URL
    assert "vendor/wavesurfer.min.js" in body
    assert "unpkg.com" not in body
    assert "cdn.jsdelivr.net" not in body
    assert "cdnjs.cloudflare.com" not in body


@pytest.mark.anyio
async def test_listen_page_loads_audio_player_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must load the audio-player.js component wrapper script."""
    await _make_repo(db_session)
    ref = "99aabbccddeeff00"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "audio-player.js" in body


@pytest.mark.anyio
async def test_listen_track_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/listen/{ref}/{path} must return 200."""
    await _make_repo(db_session)
    ref = "feedface0011aabb"
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/listen/{ref}/tracks/bass.mp3"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_listen_track_page_has_track_path_in_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Track path must be injected into the page JS context as TRACK_PATH."""
    await _make_repo(db_session)
    ref = "00aabbccddeeff11"
    track = "tracks/lead-guitar.mp3"
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/listen/{ref}/{track}"
    )
    assert response.status_code == 200
    body = response.text
    assert "TRACK_PATH" in body
    assert "lead-guitar.mp3" in body


@pytest.mark.anyio
async def test_listen_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET listen page with nonexistent owner/slug must return 404."""
    response = await client.get(
        "/musehub/ui/nobody/nonexistent-repo/listen/abc123"
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_listen_page_keyboard_shortcuts_documented(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Listen page must document Space, arrow, and L keyboard shortcuts."""
    await _make_repo(db_session)
    ref = "cafe0011aabb2233"
    response = await client.get(f"/musehub/ui/testuser/test-beats/listen/{ref}")
    assert response.status_code == 200
    body = response.text
    # Keyboard hint section must be present
    assert "Space" in body or "space" in body.lower()
    assert "loop" in body.lower()


# ---------------------------------------------------------------------------
# Compare view (issue #217)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compare_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/compare/{base}...{head} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "main" in body
    assert "feature" in body


@pytest.mark.anyio
async def test_compare_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page is accessible without a JWT token."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_compare_page_invalid_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare path without '...' separator returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/mainfeature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_compare_page_unknown_owner_404(
    client: AsyncClient,
) -> None:
    """Unknown owner/slug combination returns 404 on compare page."""
    response = await client.get("/musehub/ui/nobody/norepo/compare/main...feature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_compare_page_includes_radar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page HTML contains radar chart JavaScript."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "radarSvg" in body
    assert "DIMENSIONS" in body


@pytest.mark.anyio
async def test_compare_page_includes_piano_roll(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page HTML contains piano roll visualisation JavaScript."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "pianoRollSvg" in body
    assert "Piano Roll" in body


@pytest.mark.anyio
async def test_compare_page_includes_emotion_diff(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page HTML contains emotion diff section."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "emotionDiffBar" in body
    assert "Emotion Diff" in body


@pytest.mark.anyio
async def test_compare_page_includes_commit_list(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page HTML contains commit list JavaScript."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "commitRow" in body


@pytest.mark.anyio
async def test_compare_page_includes_create_pr_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare page HTML contains a 'Create Pull Request' call-to-action."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/compare/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "Create Pull Request" in body


@pytest.mark.anyio
async def test_compare_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/compare/{refs}?format=json returns structured JSON."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats/compare/main...feature?format=json"
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert "repoId" in body or "base_ref" in body or "baseRef" in body or "owner" in body


# ---------------------------------------------------------------------------
# Issue #208 — Branch list and tag browser tests
# ---------------------------------------------------------------------------


async def _make_repo_with_branches(
    db_session: AsyncSession,
) -> tuple[str, str, str]:
    """Seed a repo with two branches (main + feature) and return (repo_id, owner, slug)."""
    repo = MusehubRepo(
        name="branch-test",
        owner="testuser",
        slug="branch-test",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)

    main_branch = MusehubBranch(repo_id=repo_id, name="main", head_commit_id="aaa000")
    feat_branch = MusehubBranch(repo_id=repo_id, name="feat/jazz-bridge", head_commit_id="bbb111")
    db_session.add_all([main_branch, feat_branch])

    # Two commits on main, one unique commit on feat/jazz-bridge
    now = datetime.now(UTC)
    c1 = MusehubCommit(
        commit_id="aaa000",
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Initial commit",
        author="composer@stori.com",
        timestamp=now,
    )
    c2 = MusehubCommit(
        commit_id="aaa001",
        repo_id=repo_id,
        branch="main",
        parent_ids=["aaa000"],
        message="Add bridge",
        author="composer@stori.com",
        timestamp=now,
    )
    c3 = MusehubCommit(
        commit_id="bbb111",
        repo_id=repo_id,
        branch="feat/jazz-bridge",
        parent_ids=["aaa000"],
        message="Add jazz chord",
        author="composer@stori.com",
        timestamp=now,
    )
    db_session.add_all([c1, c2, c3])
    await db_session.commit()
    return repo_id, "testuser", "branch-test"


async def _make_repo_with_releases(
    db_session: AsyncSession,
) -> tuple[str, str, str]:
    """Seed a repo with namespaced releases used as tags."""
    repo = MusehubRepo(
        name="tag-test",
        owner="testuser",
        slug="tag-test",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)

    now = datetime.now(UTC)
    releases = [
        MusehubRelease(
            repo_id=repo_id, tag="emotion:happy", title="Happy vibes", body="",
            commit_id="abc001", author="composer", created_at=now, download_urls={},
        ),
        MusehubRelease(
            repo_id=repo_id, tag="genre:jazz", title="Jazz release", body="",
            commit_id="abc002", author="composer", created_at=now, download_urls={},
        ),
        MusehubRelease(
            repo_id=repo_id, tag="v1.0", title="Version 1.0", body="",
            commit_id="abc003", author="composer", created_at=now, download_urls={},
        ),
    ]
    db_session.add_all(releases)
    await db_session.commit()
    return repo_id, "testuser", "tag-test"


@pytest.mark.anyio
async def test_branches_page_lists_all(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/branches returns 200 HTML."""
    await _make_repo_with_branches(db_session)
    resp = await client.get("/musehub/ui/testuser/branch-test/branches")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Muse Hub" in body
    # Page-specific JS identifiers
    assert "branch-row" in body or "branches" in body.lower()


@pytest.mark.anyio
async def test_branches_default_marked(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response marks the default branch with isDefault=true."""
    await _make_repo_with_branches(db_session)
    resp = await client.get(
        "/musehub/ui/testuser/branch-test/branches",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "branches" in data
    default_branches = [b for b in data["branches"] if b.get("isDefault")]
    assert len(default_branches) == 1
    assert default_branches[0]["name"] == "main"


@pytest.mark.anyio
async def test_branches_compare_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branches page HTML contains compare link JavaScript."""
    await _make_repo_with_branches(db_session)
    resp = await client.get("/musehub/ui/testuser/branch-test/branches")
    assert resp.status_code == 200
    body = resp.text
    # The JS template must reference the compare URL pattern
    assert "compare" in body.lower()


@pytest.mark.anyio
async def test_branches_new_pr_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branches page HTML contains New Pull Request link JavaScript."""
    await _make_repo_with_branches(db_session)
    resp = await client.get("/musehub/ui/testuser/branch-test/branches")
    assert resp.status_code == 200
    body = resp.text
    assert "Pull Request" in body


@pytest.mark.anyio
async def test_branches_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response includes branches with ahead/behind counts and divergence placeholder."""
    await _make_repo_with_branches(db_session)
    resp = await client.get(
        "/musehub/ui/testuser/branch-test/branches?format=json",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "branches" in data
    assert "defaultBranch" in data
    assert data["defaultBranch"] == "main"

    branches_by_name = {b["name"]: b for b in data["branches"]}
    assert "main" in branches_by_name
    assert "feat/jazz-bridge" in branches_by_name

    main = branches_by_name["main"]
    assert main["isDefault"] is True
    assert main["aheadCount"] == 0
    assert main["behindCount"] == 0

    feat = branches_by_name["feat/jazz-bridge"]
    assert feat["isDefault"] is False
    # feat has 1 unique commit (bbb111); main has 2 commits (aaa000, aaa001) not shared with feat
    assert feat["aheadCount"] == 1
    assert feat["behindCount"] == 2

    # Divergence is a placeholder (all None)
    div = feat["divergence"]
    assert div["melodic"] is None
    assert div["harmonic"] is None


@pytest.mark.anyio
async def test_tags_page_lists_all(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/tags returns 200 HTML."""
    await _make_repo_with_releases(db_session)
    resp = await client.get("/musehub/ui/testuser/tag-test/tags")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Muse Hub" in body
    assert "Tags" in body


@pytest.mark.anyio
async def test_tags_namespace_filter(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tags page HTML includes namespace filter dropdown JavaScript."""
    await _make_repo_with_releases(db_session)
    resp = await client.get("/musehub/ui/testuser/tag-test/tags")
    assert resp.status_code == 200
    body = resp.text
    # Namespace filter select element is rendered by JS
    assert "ns-filter" in body or "namespace" in body.lower()
    # Namespace icons present
    assert "&#127768;" in body or "emotion" in body


@pytest.mark.anyio
async def test_tags_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response returns TagListResponse with namespace grouping."""
    await _make_repo_with_releases(db_session)
    resp = await client.get(
        "/musehub/ui/testuser/tag-test/tags?format=json",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tags" in data
    assert "namespaces" in data

    # All three releases become tags
    assert len(data["tags"]) == 3

    tags_by_name = {t["tag"]: t for t in data["tags"]}
    assert "emotion:happy" in tags_by_name
    assert "genre:jazz" in tags_by_name
    assert "v1.0" in tags_by_name

    assert tags_by_name["emotion:happy"]["namespace"] == "emotion"
    assert tags_by_name["genre:jazz"]["namespace"] == "genre"
    assert tags_by_name["v1.0"]["namespace"] == "version"

    # Namespaces are sorted
    assert sorted(data["namespaces"]) == data["namespaces"]
    assert "emotion" in data["namespaces"]
    assert "genre" in data["namespaces"]
    assert "version" in data["namespaces"]



# ---------------------------------------------------------------------------
# Arrangement matrix page — issue #212
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Piano roll page tests — issue #209
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_arrange_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/arrange/{ref} returns 200 HTML without a JWT."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/HEAD")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_piano_roll_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/piano-roll/{ref} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/piano-roll/main")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_arrange_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arrangement matrix page is accessible without a JWT (auth handled client-side)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/HEAD")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_arrange_page_contains_musehub(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arrangement matrix page HTML shell contains 'Muse Hub' branding."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/abc1234")
    assert response.status_code == 200
    assert "Muse Hub" in response.text


@pytest.mark.anyio
async def test_arrange_page_contains_grid_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arrangement matrix page embeds the grid rendering JS (renderMatrix or arrange)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/HEAD")
    assert response.status_code == 200
    body = response.text
    assert "renderMatrix" in body or "arrange" in body.lower()


@pytest.mark.anyio
async def test_arrange_page_contains_density_logic(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arrangement matrix page includes density colour logic."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/HEAD")
    assert response.status_code == 200
    body = response.text
    assert "density" in body.lower() or "noteDensity" in body


@pytest.mark.anyio
async def test_arrange_page_contains_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arrangement matrix page includes the JWT token form for client-side auth."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/arrange/HEAD")
    assert response.status_code == 200
    body = response.text
    assert 'id="token-form"' in body
    assert "musehub.js" in body


@pytest.mark.anyio
async def test_arrange_page_unknown_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{unknown}/{slug}/arrange/{ref} returns 404 for unknown repos."""
    response = await client.get("/musehub/ui/unknown-user/no-such-repo/arrange/HEAD")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_commit_detail_json_format_returns_commit_data(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET commit detail page with ?format=json returns CommitResponse JSON."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(
        f"/musehub/ui/testuser/commit-detail-test/commits/{sha}?format=json"
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    # CommitResponse fields in camelCase
    assert "commitId" in data or "commit_id" in data
    assert "message" in data


@pytest.mark.anyio
async def test_commit_detail_page_musical_metadata_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes musical metadata (tempo, key, meter) rendering logic."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(f"/musehub/ui/testuser/commit-detail-test/commits/{sha}")
    assert response.status_code == 200
    body = response.text
    # Musical metadata section in JS
    assert "musicalMeta" in body
    # Meter extraction
    assert "meta.meter" in body
    # Tempo/key rendering
    assert "meta.key" in body
    assert "meta.tempo" in body


@pytest.mark.anyio
async def test_commit_detail_nav_has_parent_and_child_links(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page navigation includes both parent and child commit links."""
    await _seed_commit_detail_fixtures(db_session)
    sha = "bbbb1111222233334444555566667777bbbbcccc"
    response = await client.get(f"/musehub/ui/testuser/commit-detail-test/commits/{sha}")
    assert response.status_code == 200
    body = response.text
    # Both parent and child navigation links rendered in JS
    assert "Parent Commit" in body
    assert "Child Commit" in body


@pytest.mark.anyio
async def test_piano_roll_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll UI page is accessible without a JWT token."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/piano-roll/main")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_piano_roll_page_loads_piano_roll_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page references piano-roll.js script."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/piano-roll/main")
    assert response.status_code == 200
    assert "piano-roll.js" in response.text


@pytest.mark.anyio
async def test_piano_roll_page_contains_canvas(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page embeds a canvas element for rendering."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/piano-roll/main")
    assert response.status_code == 200
    body = response.text
    assert "PianoRoll" in body or "piano-canvas" in body or "piano-roll.js" in body


@pytest.mark.anyio
async def test_piano_roll_page_has_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page includes the JWT token form for unauthenticated visitors."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/piano-roll/main")
    assert response.status_code == 200
    assert 'id="token-form"' in response.text
    assert "musehub.js" in response.text


@pytest.mark.anyio
async def test_piano_roll_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Piano roll page for an unknown repo returns 404."""
    response = await client.get("/musehub/ui/nobody/no-repo/piano-roll/main")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_arrange_tab_in_repo_nav(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo home page navigation includes an 'Arrange' tab link."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    assert "Arrange" in response.text or "arrange" in response.text


@pytest.mark.anyio
async def test_piano_roll_track_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /piano-roll/{ref}/{path} (single track) returns 200."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats/piano-roll/main/tracks/bass.mid"
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_piano_roll_track_page_embeds_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Single-track piano roll page embeds the MIDI file path in the JS context."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats/piano-roll/main/tracks/bass.mid"
    )
    assert response.status_code == 200
    assert "tracks/bass.mid" in response.text


@pytest.mark.anyio
async def test_piano_roll_js_served(client: AsyncClient) -> None:
    """GET /musehub/static/piano-roll.js returns 200 JavaScript."""
    response = await client.get("/musehub/static/piano-roll.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("content-type", "")


@pytest.mark.anyio
async def test_piano_roll_js_contains_renderer(client: AsyncClient) -> None:
    """piano-roll.js exports the PianoRoll.render function."""
    response = await client.get("/musehub/static/piano-roll.js")
    assert response.status_code == 200
    body = response.text
    assert "PianoRoll" in body
    assert "render" in body



async def _seed_blob_fixtures(db_session: AsyncSession) -> str:
    """Seed a public repo with a branch and typed objects for blob viewer tests.

    Creates:
    - repo: testuser/blob-test (public)
    - branch: main
    - objects: tracks/bass.mid, tracks/keys.mp3, metadata.json, cover.webp

    Returns repo_id.
    """
    repo = MusehubRepo(
        name="blob-test",
        owner="testuser",
        slug="blob-test",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()

    commit = MusehubCommit(
        commit_id="blobdeadbeef12",
        repo_id=str(repo.repo_id),
        message="add blob fixtures",
        branch="main",
        author="testuser",
        timestamp=datetime.now(tz=UTC),
    )
    db_session.add(commit)

    branch = MusehubBranch(
        repo_id=str(repo.repo_id),
        name="main",
        head_commit_id="blobdeadbeef12",
    )
    db_session.add(branch)

    for path, size in [
        ("tracks/bass.mid", 2048),
        ("tracks/keys.mp3", 8192),
        ("metadata.json", 512),
        ("cover.webp", 4096),
    ]:
        obj = MusehubObject(
            object_id=f"sha256:blob_{path.replace('/', '_')}",
            repo_id=str(repo.repo_id),
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/blob_{path.replace('/', '_')}",
        )
        db_session.add(obj)

    await db_session.commit()
    return str(repo.repo_id)



@pytest.mark.anyio
async def test_blob_404_unknown_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/blob/{ref}/{path} returns 404 for unknown path."""
    repo_id = await _seed_blob_fixtures(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/blob/main/does/not/exist.mid")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_blob_image_shows_inline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blob page for .webp file includes <img> rendering logic in the template JS."""
    await _seed_blob_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/blob-test/blob/main/cover.webp")
    assert response.status_code == 200
    body = response.text
    # JS template emits <img> for image file type
    assert "<img" in body or "blob-img" in body
    assert "cover.webp" in body


@pytest.mark.anyio
async def test_blob_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/blob/{ref}/{path} returns BlobMetaResponse JSON."""
    repo_id = await _seed_blob_fixtures(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/blob/main/tracks/bass.mid"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "tracks/bass.mid"
    assert data["filename"] == "bass.mid"
    assert data["sizeBytes"] == 2048
    assert data["fileType"] == "midi"
    assert data["sha"].startswith("sha256:")
    assert "/raw/" in data["rawUrl"]
    # MIDI is binary — no content_text
    assert data["contentText"] is None
@pytest.mark.anyio
async def test_blob_json_syntax_highlighted(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blob page for .json file includes syntax-highlighting logic in the template JS."""
    await _seed_blob_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/blob-test/blob/main/metadata.json")
    assert response.status_code == 200
    body = response.text
    # highlightJson function must be present in the template script
    assert "highlightJson" in body or "json-key" in body
    assert "metadata.json" in body


@pytest.mark.anyio
async def test_blob_midi_shows_piano_roll_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo}/blob/{ref}/{path} returns 200 HTML for a .mid file.

    The template's client-side JS must reference the piano roll URL pattern so that
    clicking the page in a browser navigates to the piano roll viewer.
    """
    await _seed_blob_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/blob-test/blob/main/tracks/bass.mid")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # JS in the template constructs piano-roll URLs for MIDI files
    assert "piano-roll" in body or "Piano Roll" in body
    # Filename is embedded in the page context
    assert "bass.mid" in body


@pytest.mark.anyio
async def test_blob_mp3_shows_audio_player(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blob page for .mp3 file includes <audio> rendering logic in the template JS."""
    await _seed_blob_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/blob-test/blob/main/tracks/keys.mp3")
    assert response.status_code == 200
    body = response.text
    # JS template emits <audio> element for audio file type
    assert "<audio" in body or "blob-audio" in body
    assert "keys.mp3" in body


@pytest.mark.anyio
async def test_blob_raw_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Blob page JS constructs a Raw download link via the /raw/ endpoint."""
    await _seed_blob_fixtures(db_session)
    response = await client.get("/musehub/ui/testuser/blob-test/blob/main/tracks/bass.mid")
    assert response.status_code == 200
    body = response.text
    # JS constructs raw URL — the string '/raw/' must appear in the template script
    assert "/raw/" in body


@pytest.mark.anyio
async def test_score_page_contains_legend(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page includes a legend for note symbols."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    body = response.text
    assert "legend" in body or "Note" in body


@pytest.mark.anyio
async def test_score_page_contains_score_meta(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page embeds a score metadata panel (key/tempo/time signature)."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    body = response.text
    assert "score-meta" in body


@pytest.mark.anyio
async def test_score_page_contains_staff_container(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page embeds the SVG staff container markup."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    body = response.text
    assert "staff-container" in body or "staves" in body


@pytest.mark.anyio
async def test_score_page_contains_track_selector(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score page embeds a track selector element."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    body = response.text
    assert "track-selector" in body


@pytest.mark.anyio
async def test_score_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Score UI page must be accessible without an Authorization header."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_score_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/score/{ref} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_score_part_page_includes_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Single-part score page injects the path segment into page data."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main/piano")
    assert response.status_code == 200
    body = response.text
    # scorePath JS variable should be set to the path segment
    assert "piano" in body


@pytest.mark.anyio
async def test_score_part_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/score/{ref}/{path} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/score/main/piano")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_score_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{unknown}/{slug}/score/{ref} returns 404."""
    response = await client.get("/musehub/ui/nobody/no-beats/score/main")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Arrangement matrix page — issue #212
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Piano roll page tests — issue #209
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_commit_page_artifact_auth_uses_blob_proxy(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page must use blob URL proxy for artifact auth, not bare content URLs in src/href.

    Images use data-content-url + hydrateImages(); audio/download use downloadArtifact().
    This prevents 401s caused by the browser sending unauthenticated direct requests.
    """
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    # Images must carry data-content-url (hydrated asynchronously with auth)
    assert "data-content-url" in body
    # No bare /content URL should appear as img src= (would cause 401)
    assert 'src="/api/v1/musehub' not in body
    # Downloads must go through downloadArtifact() JS helper, not bare href
    assert "downloadArtifact" in body
    # hydrateImages and _fetchBlobUrl must be present for the blob proxy pattern
    assert "hydrateImages" in body
    assert "_fetchBlobUrl" in body


# ---------------------------------------------------------------------------
# Reaction bars — issue #296
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reaction_bar_js_in_musehub_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """musehub.js must define loadReactions and toggleReaction for all detail pages."""
    response = await client.get("/musehub/static/musehub.js")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "toggleReaction" in body
    assert "REACTION_BAR_EMOJIS" in body


@pytest.mark.anyio
async def test_reaction_bar_emojis_in_musehub_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """musehub.js reaction bar must include all 8 required emojis."""
    response = await client.get("/musehub/static/musehub.js")
    assert response.status_code == 200
    body = response.text
    for emoji in ["🔥", "❤️", "👏", "✨", "🎵", "🎸", "🎹", "🥁"]:
        assert emoji in body, f"Emoji {emoji!r} missing from musehub.js"


@pytest.mark.anyio
async def test_reaction_bar_commit_page_has_load_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page must call loadReactions for target_type 'commit'."""
    await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "commit-reactions" in body


@pytest.mark.anyio
async def test_reaction_bar_pr_detail_has_load_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR detail page must call loadReactions for target_type 'pull_request'."""
    await _make_repo(db_session)
    pr_id = "some-pr-uuid-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/pulls/{pr_id}")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "pr-reactions" in body


@pytest.mark.anyio
async def test_reaction_bar_issue_detail_has_load_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue detail page must call loadReactions for target_type 'issue'."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "issue-reactions" in body


@pytest.mark.anyio
async def test_reaction_bar_release_detail_has_load_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release detail page must call loadReactions for target_type 'release'."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "release-reactions" in body


@pytest.mark.anyio
async def test_reaction_bar_session_detail_has_load_call(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page must call loadReactions for target_type 'session'."""
    await _make_repo(db_session)
    session_id = "some-session-uuid-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "loadReactions" in body
    assert "session-reactions" in body


@pytest.mark.anyio
async def test_reaction_api_allows_new_emojis(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /reactions with 👏 and 🎹 (new emojis) must be accepted (not 400)."""
    from maestro.db.musehub_models import MusehubRepo
    repo = MusehubRepo(
        name="reaction-test",
        owner="testuser",
        slug="reaction-test",
        visibility="public",
        owner_user_id="reaction-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    token_headers = {"Authorization": "Bearer test-token"}

    for emoji in ["👏", "🎹"]:
        response = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/reactions",
            json={"target_type": "commit", "target_id": "abc123", "emoji": emoji},
            headers=token_headers,
        )
        assert response.status_code not in (400, 422), (
            f"Emoji {emoji!r} rejected by API: {response.status_code} {response.text}"
        )


@pytest.mark.anyio
async def test_reaction_api_allows_release_and_session_target_types(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /reactions must accept 'release' and 'session' as target_type values.

    These target types were added in issue #296 to support reaction bars on
    release_detail and session_detail pages.
    """
    from maestro.db.musehub_models import MusehubRepo
    repo = MusehubRepo(
        name="target-type-test",
        owner="testuser",
        slug="target-type-test",
        visibility="public",
        owner_user_id="target-type-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    token_headers = {"Authorization": "Bearer test-token"}

    for target_type in ["release", "session"]:
        response = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/reactions",
            json={"target_type": target_type, "target_id": "some-id", "emoji": "🔥"},
            headers=token_headers,
        )
        assert response.status_code not in (400, 422), (
            f"target_type {target_type!r} rejected: {response.status_code} {response.text}"
        )


@pytest.mark.anyio
async def test_reaction_bar_css_loaded_on_detail_pages(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Detail pages must load components.css which contains .reaction-bar styles."""
    await _make_repo(db_session)
    pages = [
        "/musehub/ui/testuser/test-beats/pulls/pr-uuid-abc",
        "/musehub/ui/testuser/test-beats/issues/1",
        "/musehub/ui/testuser/test-beats/releases/v1.0",
        "/musehub/ui/testuser/test-beats/sessions/session-uuid-abc",
    ]
    for page in pages:
        response = await client.get(page)
        assert response.status_code == 200
        assert "components.css" in response.text, f"components.css missing from {page}"


@pytest.mark.anyio
async def test_reaction_bar_components_css_has_styles(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """components.css must define .reaction-bar and .reaction-btn CSS classes."""
    response = await client.get("/musehub/static/components.css")
    assert response.status_code == 200
    body = response.text
    assert ".reaction-bar" in body
    assert ".reaction-btn" in body
    assert ".reaction-btn--active" in body
    assert ".reaction-count" in body


# ---------------------------------------------------------------------------
# Feed page tests — issue #292 (rich event cards)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_feed_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/feed returns 200 HTML without requiring a JWT."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Activity Feed" in response.text


@pytest.mark.anyio
async def test_feed_page_no_raw_json_payload(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must not render raw JSON.stringify of notification payload.

    Regression guard for issue #292: the old implementation called
    JSON.stringify(item.payload) directly into the DOM, exposing raw JSON
    to users. The new rich card templates must not do this.
    """
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    assert "JSON.stringify(item.payload" not in body
    assert "JSON.stringify(item" not in body


@pytest.mark.anyio
async def test_feed_page_has_event_meta_for_all_types(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must define EVENT_META entries for all 8 notification event types."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    for event_type in (
        "comment",
        "mention",
        "pr_opened",
        "pr_merged",
        "issue_opened",
        "issue_closed",
        "new_commit",
        "new_follower",
    ):
        assert event_type in body, f"EVENT_META missing entry for '{event_type}'"


@pytest.mark.anyio
async def test_feed_page_has_data_notif_id_attribute(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Each event card must carry a data-notif-id attribute.

    This attribute is the hook that issue #293 (mark-as-read UX) will use to
    attach action buttons to each card without restructuring the DOM.
    """
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "data-notif-id" in response.text


@pytest.mark.anyio
async def test_feed_page_has_unread_indicator(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must include logic to highlight unread cards with a left border."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    assert "is_read" in body
    assert "color-accent" in body


@pytest.mark.anyio
async def test_feed_page_has_actor_avatar_logic(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must render actor avatars using the actorHsl / actorAvatar helpers."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    assert "actorHsl" in body
    assert "actorAvatar" in body


@pytest.mark.anyio
async def test_feed_page_has_relative_timestamp(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must call fmtRelative to render timestamps in a human-readable form."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "fmtRelative" in response.text


# ---------------------------------------------------------------------------
# Mark-as-read UX tests — issue #293
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_feed_page_has_mark_one_read_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must define markOneRead() for per-notification mark-as-read."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "markOneRead" in response.text


@pytest.mark.anyio
async def test_feed_page_has_mark_all_read_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must define markAllRead() for bulk mark-as-read."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "markAllRead" in response.text


@pytest.mark.anyio
async def test_feed_page_has_decrement_nav_badge_function(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must define decrementNavBadge() to keep the nav badge in sync."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "decrementNavBadge" in response.text


@pytest.mark.anyio
async def test_feed_page_mark_read_btn_targets_notification_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """markOneRead() must call POST /notifications/{notif_id}/read."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    assert "/notifications/" in body
    assert "mark-read-btn" in body


@pytest.mark.anyio
async def test_feed_page_mark_all_btn_targets_read_all_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """markAllRead() must call POST /notifications/read-all."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "read-all" in response.text


@pytest.mark.anyio
async def test_feed_page_mark_all_btn_present_in_template(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Feed page must render a 'Mark all as read' button element."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    assert "mark-all-read-btn" in response.text


@pytest.mark.anyio
async def test_feed_page_mark_read_updates_nav_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """After marking all as read, page logic must update nav-notif-badge to hidden."""
    response = await client.get("/musehub/ui/feed")
    assert response.status_code == 200
    body = response.text
    assert "nav-notif-badge" in body
    assert "decrementNavBadge" in body


# ---------------------------------------------------------------------------
# Per-dimension analysis detail pages (issue #332)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/key returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/key")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_key_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Key analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/key")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_key_analysis_page_contains_key_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Key page must contain tonic, mode, relative key, and confidence UI elements."""
    await _make_repo(db_session)
    ref = "cafebabe12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/key")
    assert response.status_code == 200
    body = response.text
    assert "Key Detection" in body
    assert "Relative Key" in body
    assert "Detection Confidence" in body
    assert "Alternate Key" in body


@pytest.mark.anyio
async def test_meter_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/meter returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/meter")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_meter_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Meter analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/meter")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_meter_analysis_page_contains_meter_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Meter page must contain time signature, compound/simple badge, and beat strength UI."""
    await _make_repo(db_session)
    ref = "feedface5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/meter")
    assert response.status_code == 200
    body = response.text
    assert "Meter Analysis" in body
    assert "Time Signature" in body
    assert "Beat Strength Profile" in body
    assert "beatStrengthSvg" in body or "beatStrengthProfile" in body


@pytest.mark.anyio
async def test_chord_map_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/chord-map returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/chord-map")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_chord_map_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Chord-map analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef9999"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/chord-map")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_chord_map_analysis_page_contains_chord_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Chord-map page must contain progression, tension, and beat position UI elements."""
    await _make_repo(db_session)
    ref = "beefdead1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/chord-map")
    assert response.status_code == 200
    body = response.text
    assert "Chord Map" in body
    assert "Total Chords" in body
    assert "Chord Progression" in body
    assert "tension" in body.lower()


@pytest.mark.anyio
async def test_groove_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/groove returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/groove")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_groove_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef4321"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/groove")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_groove_analysis_page_contains_groove_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove page must contain style badge, BPM, swing factor, and groove score UI."""
    await _make_repo(db_session)
    ref = "cafefeed5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/groove")
    assert response.status_code == 200
    body = response.text
    assert "Groove Analysis" in body
    assert "Style" in body
    assert "BPM" in body
    assert "Groove Score" in body
    assert "Swing Factor" in body


@pytest.mark.anyio
async def test_emotion_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/emotion returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/emotion")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_emotion_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef0001"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/emotion")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_emotion_analysis_page_contains_emotion_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion page must contain primary emotion, valence-arousal plot, and tension bar."""
    await _make_repo(db_session)
    ref = "aabbccdd5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/emotion")
    assert response.status_code == 200
    body = response.text
    assert "Emotion Analysis" in body
    assert "Primary Emotion" in body
    assert "Valence" in body or "valence" in body
    assert "Arousal" in body or "arousal" in body
    assert "Tension" in body or "tension" in body


@pytest.mark.anyio
async def test_form_analysis_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/form returns 200 HTML."""
    await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/form")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_form_analysis_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form analysis page must be accessible without a JWT (HTML shell handles auth)."""
    await _make_repo(db_session)
    ref = "deadbeef0002"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/form")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_form_analysis_page_contains_form_data_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form page must contain form label, section timeline, and sections table."""
    await _make_repo(db_session)
    ref = "11223344abcd"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/form")
    assert response.status_code == 200
    body = response.text
    assert "Form Analysis" in body
    assert "Form Timeline" in body or "formLabel" in body
    assert "Sections" in body
    assert "Total Beats" in body


# ---------------------------------------------------------------------------
# Issue #295 — Profile page: followers/following lists with user cards
# ---------------------------------------------------------------------------

# test_profile_page_has_followers_following_tabs
# test_profile_page_has_user_card_js
# test_profile_page_has_switch_tab_js
# test_followers_list_endpoint_returns_200
# test_followers_list_returns_user_cards_for_known_user
# test_following_list_returns_user_cards_for_known_user
# test_followers_list_unknown_user_404
# test_following_list_unknown_user_404
# test_followers_response_includes_following_count
# test_followers_list_empty_for_user_with_no_followers


async def _make_follow(
    db_session: AsyncSession,
    follower_id: str,
    followee_id: str,
) -> MusehubFollow:
    """Seed a follow relationship and return the ORM row."""
    import uuid
    row = MusehubFollow(
        follow_id=str(uuid.uuid4()),
        follower_id=follower_id,
        followee_id=followee_id,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.skip(reason="Pre-existing failure: profile page template missing tab-btn-followers element — see #539")
@pytest.mark.anyio
async def test_profile_page_has_followers_following_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile page must render Followers and Following tab buttons (issue #295)."""
    await _make_profile(db_session, username="tabuser")
    response = await client.get("/musehub/ui/users/tabuser")
    assert response.status_code == 200
    body = response.text
    assert "tab-btn-followers" in body
    assert "tab-btn-following" in body


@pytest.mark.skip(reason="Pre-existing failure: profile page template missing userCardHtml JS — see #539")
@pytest.mark.anyio
async def test_profile_page_has_user_card_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile page must include userCardHtml and loadFollowTab JS helpers (issue #295)."""
    await _make_profile(db_session, username="cardjsuser")
    response = await client.get("/musehub/ui/users/cardjsuser")
    assert response.status_code == 200
    body = response.text
    assert "userCardHtml" in body
    assert "loadFollowTab" in body


@pytest.mark.anyio
async def test_profile_page_has_switch_tab_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile page must include switchTab() to toggle between followers and following (issue #295)."""
    await _make_profile(db_session, username="switchtabuser")
    response = await client.get("/musehub/ui/users/switchtabuser")
    assert response.status_code == 200
    assert "switchTab" in response.text


@pytest.mark.anyio
async def test_followers_list_endpoint_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username}/followers-list returns 200 for known user (issue #295)."""
    await _make_profile(db_session, username="followerlistuser")
    response = await client.get("/api/v1/musehub/users/followerlistuser/followers-list")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.anyio
async def test_followers_list_returns_user_cards_for_known_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """followers-list returns UserCard objects when followers exist (issue #295)."""
    import uuid

    target = MusehubProfile(
        user_id="target-user-fl-01",
        username="flctarget",
        bio="I am the target",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    follower = MusehubProfile(
        user_id="follower-user-fl-01",
        username="flcfollower",
        bio="I am a follower",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    db_session.add(target)
    db_session.add(follower)
    await db_session.flush()
    # Seed a follow row using user_ids (same convention as the seed script)
    await _make_follow(db_session, follower_id="follower-user-fl-01", followee_id="target-user-fl-01")

    response = await client.get("/api/v1/musehub/users/flctarget/followers-list")
    assert response.status_code == 200
    cards = response.json()
    assert len(cards) >= 1
    usernames = [c["username"] for c in cards]
    assert "flcfollower" in usernames


@pytest.mark.anyio
async def test_following_list_returns_user_cards_for_known_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """following-list returns UserCard objects for users that the target follows (issue #295)."""
    actor = MusehubProfile(
        user_id="actor-user-fl-02",
        username="flcactor",
        bio="I follow people",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    followee = MusehubProfile(
        user_id="followee-user-fl-02",
        username="flcfollowee",
        bio="I am followed",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    db_session.add(actor)
    db_session.add(followee)
    await db_session.flush()
    await _make_follow(db_session, follower_id="actor-user-fl-02", followee_id="followee-user-fl-02")

    response = await client.get("/api/v1/musehub/users/flcactor/following-list")
    assert response.status_code == 200
    cards = response.json()
    assert len(cards) >= 1
    usernames = [c["username"] for c in cards]
    assert "flcfollowee" in usernames


@pytest.mark.anyio
async def test_followers_list_unknown_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """followers-list returns 404 when the target username does not exist (issue #295)."""
    response = await client.get("/api/v1/musehub/users/nonexistent-ghost-user/followers-list")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_following_list_unknown_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """following-list returns 404 when the target username does not exist (issue #295)."""
    response = await client.get("/api/v1/musehub/users/nonexistent-ghost-user/following-list")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_followers_response_includes_following_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /users/{username}/followers now includes following_count in response (issue #295)."""
    await _make_profile(db_session, username="followcountuser")
    response = await client.get("/api/v1/musehub/users/followcountuser/followers")
    assert response.status_code == 200
    data = response.json()
    assert "followerCount" in data or "follower_count" in data
    assert "followingCount" in data or "following_count" in data


@pytest.mark.anyio
async def test_followers_list_empty_for_user_with_no_followers(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """followers-list returns an empty list when no one follows the user (issue #295)."""
    await _make_profile(db_session, username="lonelyuser295")
    response = await client.get("/api/v1/musehub/users/lonelyuser295/followers-list")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Issue #450 — Enhanced commit detail: inline audio player, muse_tags panel,
# reactions, comment thread, cross-references
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_page_has_inline_audio_player_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes the inline WaveSurfer.js audio player section.

    The player card must contain the DOM root (inline-player-root), the JS
    functions that mount and control the player (buildInlinePlayer, iapTogglePlay),
    and a "Listen" heading so musicians can identify the hero player.
    """
    repo = MusehubRepo(
        name="audio-player-test",
        owner="audiouser",
        slug="audio-player-test",
        visibility="public",
        owner_user_id="audio-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "c0ffee0000111122223333444455556666c0ffee"
    response = await client.get(f"/musehub/ui/audiouser/audio-player-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "inline-player-root" in body
    assert "buildInlinePlayer" in body
    assert "iapTogglePlay" in body
    assert "iapSwitchTrack" in body
    assert "iapSetVolume" in body
    # WaveSurfer vendor script tag present
    assert "wavesurfer" in body.lower()
    # Heading label
    assert "Listen" in body


@pytest.mark.anyio
async def test_commit_page_inline_player_has_track_selector_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page inline player exposes track selector JS for switching stems."""
    repo = MusehubRepo(
        name="track-sel-test",
        owner="trackuser",
        slug="track-sel-test",
        visibility="public",
        owner_user_id="track-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "aaaa1111bbbb2222cccc3333dddd4444eeee5555"
    response = await client.get(f"/musehub/ui/trackuser/track-sel-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "iap-track-sel" in body or "iapSwitchTrack" in body
    assert "iap-volume" in body


@pytest.mark.anyio
async def test_commit_page_has_muse_tags_panel(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes the muse_tags / analysis metadata panel.

    The panel must contain the DOM container (muse-tags-panel), the loader
    function (loadMuseTagsPanel), pill-rendering helpers (tagPill, muse-pill),
    and fetch calls for the four analysis endpoints (key, tempo, meter, emotion).
    """
    repo = MusehubRepo(
        name="tags-panel-test",
        owner="tagsuser",
        slug="tags-panel-test",
        visibility="public",
        owner_user_id="tags-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/tagsuser/tags-panel-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "muse-tags-panel" in body
    assert "loadMuseTagsPanel" in body
    assert "tagPill" in body
    assert "muse-pill" in body
    assert "/analysis/" in body
    assert "emotion" in body
    assert "Muse Tags" in body


@pytest.mark.anyio
async def test_commit_page_muse_tags_pill_colours_defined(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page CSS includes namespace-specific pill colour classes."""
    repo = MusehubRepo(
        name="pill-colour-test",
        owner="pilluser",
        slug="pill-colour-test",
        visibility="public",
        owner_user_id="pill-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "abcd1234ef567890abcd1234ef567890abcd1234"
    response = await client.get(f"/musehub/ui/pilluser/pill-colour-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    for css_class in ("pill-emotion", "pill-stage", "pill-ref", "pill-key", "pill-tempo", "pill-time"):
        assert css_class in body, f"Expected CSS class {css_class!r} in page body"


@pytest.mark.anyio
async def test_commit_page_has_cross_references_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit detail page includes the cross-references 'Mentioned In' panel.

    The panel must expose the DOM container (xrefs-body), the loader function
    (loadCrossReferences), and headings for PRs, Issues, and Sessions.
    """
    repo = MusehubRepo(
        name="xrefs-test",
        owner="xrefsuser",
        slug="xrefs-test",
        visibility="public",
        owner_user_id="xrefs-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "face000011112222333344445555666677778888"
    response = await client.get(f"/musehub/ui/xrefsuser/xrefs-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "xrefs-body" in body
    assert "loadCrossReferences" in body
    assert "Mentioned In" in body
    assert "Pull Requests" in body
    assert "Issues" in body
    assert "Sessions" in body


@pytest.mark.anyio
async def test_commit_page_context_passes_listen_and_embed_urls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """commit_page() injects listenUrl and embedUrl into the JS page-data block.

    The inline player relies on these server-side values to construct the
    listen-page link and the fallback embed iframe URL without doing an
    additional round-trip to resolve owner/slug.
    """
    repo = MusehubRepo(
        name="url-context-test",
        owner="urluser",
        slug="url-context-test",
        visibility="public",
        owner_user_id="url-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    commit_id = "dead0000beef1111dead0000beef1111dead0000"
    response = await client.get(f"/musehub/ui/urluser/url-context-test/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert "listenUrl" in body
    assert "embedUrl" in body
    assert f"/listen/{commit_id}" in body
    assert f"/embed/{commit_id}" in body


# ---------------------------------------------------------------------------
# Explore page — filter sidebar + inline audio preview (issue #444)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_explore_page_returns_200(
    client: AsyncClient,
) -> None:
    """GET /musehub/ui/explore returns 200 without authentication."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_explore_page_has_filter_sidebar(
    client: AsyncClient,
) -> None:
    """Explore page renders a filter sidebar with sort, license, and clear-filters sections."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "explore-sidebar" in body
    assert "Clear filters" in body
    assert "Sort by" in body
    assert "License" in body


@pytest.mark.anyio
async def test_explore_page_has_sort_options(
    client: AsyncClient,
) -> None:
    """Explore page sidebar includes all four sort radio options."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "Most starred" in body
    assert "Recently updated" in body
    assert "Most forked" in body
    assert "Trending" in body


@pytest.mark.anyio
async def test_explore_page_has_license_options(
    client: AsyncClient,
) -> None:
    """Explore page sidebar includes the expected license filter options."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "CC0" in body
    assert "CC BY" in body
    assert "CC BY-SA" in body
    assert "CC BY-NC" in body
    assert "All Rights Reserved" in body


@pytest.mark.anyio
async def test_explore_page_has_repo_grid(
    client: AsyncClient,
) -> None:
    """Explore page includes the repo grid and JS discover API loader."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "repo-grid" in body
    assert "loadExplore" in body
    assert "DISCOVER_API" in body


@pytest.mark.anyio
async def test_explore_page_has_audio_preview_js(
    client: AsyncClient,
) -> None:
    """Explore page includes the inline hover audio preview JavaScript (500ms delay)."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "card-audio-preview" in body
    assert "toggleCardPreview" in body
    assert "onCardMouseEnter" in body
    assert "500" in body


@pytest.mark.anyio
async def test_explore_page_default_sort_stars(
    client: AsyncClient,
) -> None:
    """Explore page defaults to 'stars' sort when no sort param given."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    # 'stars' radio should be pre-checked (default sort)
    assert 'value="stars"' in body
    assert 'checked' in body


@pytest.mark.anyio
async def test_explore_page_sort_param_honoured(
    client: AsyncClient,
) -> None:
    """Explore page honours the ?sort= query param for pre-selecting a sort option."""
    response = await client.get("/musehub/ui/explore?sort=updated")
    assert response.status_code == 200
    body = response.text
    assert 'value="updated"' in body


@pytest.mark.anyio
async def test_explore_page_no_auth_required(
    client: AsyncClient,
) -> None:
    """Explore page is publicly accessible — no JWT required (zero-friction discovery)."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert response.status_code != 401
    assert response.status_code != 403


@pytest.mark.anyio
async def test_explore_page_chip_toggle_js(
    client: AsyncClient,
) -> None:
    """Explore page includes toggleChip JS for progressive chip filter enhancement."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    body = response.text
    assert "toggleChip" in body
    assert "filter-chip" in body


@pytest.mark.anyio
async def test_explore_page_get_params_preserved(
    client: AsyncClient,
) -> None:
    """Explore page accepts lang, license, topic, sort GET params without error."""
    response = await client.get(
        "/musehub/ui/explore?lang=piano&license=CC0&topic=jazz&sort=stars"
    )
    assert response.status_code == 200
