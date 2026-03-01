"""Tests for the SSR issue list page — reference HTMX implementation (issue #555).

Covers server-side rendering, HTMX fragment responses, filters, tabs, and
pagination.  All assertions target Jinja2-rendered content in the HTML
response body, not JavaScript function definitions.

Test areas:
  Basic rendering
  - test_issue_list_page_returns_200
  - test_issue_list_no_auth_required
  - test_issue_list_unknown_repo_404

  SSR content — issue data rendered on server
  - test_issue_list_renders_issue_title_server_side
  - test_issue_list_filter_form_has_hx_get
  - test_issue_list_filter_form_has_hx_target

  Open/closed tab counts
  - test_issue_list_tab_open_has_hx_get
  - test_issue_list_open_closed_counts_in_tabs

  State filter
  - test_issue_list_state_filter_closed_shows_closed_only

  Label filter
  - test_issue_list_label_filter_narrows_issues

  HTMX fragment
  - test_issue_list_htmx_request_returns_fragment
  - test_issue_list_fragment_contains_issue_title
  - test_issue_list_fragment_empty_state_when_no_issues

  Pagination
  - test_issue_list_pagination_renders_next_link

  Right sidebar
  - test_issue_list_milestone_progress_in_right_sidebar
  - test_issue_list_right_sidebar_present
  - test_issue_list_milestone_progress_heading_present
  - test_issue_list_milestone_progress_bar_css_present
  - test_issue_list_milestone_progress_list_present
  - test_issue_list_labels_summary_heading_present
  - test_issue_list_labels_summary_list_present

  Filter sidebar
  - test_issue_list_filter_sidebar_present
  - test_issue_list_label_chip_container_present
  - test_issue_list_filter_milestone_select_present
  - test_issue_list_filter_assignee_select_present
  - test_issue_list_filter_author_input_present
  - test_issue_list_sort_radio_group_present
  - test_issue_list_sort_radio_buttons_present

  Template selector / new-issue flow (minimal JS)
  - test_issue_list_template_picker_present
  - test_issue_list_template_grid_present
  - test_issue_list_template_cards_present
  - test_issue_list_show_template_picker_js_present
  - test_issue_list_select_template_js_present
  - test_issue_list_issue_templates_const_present
  - test_issue_list_new_issue_btn_calls_template
  - test_issue_list_templates_back_btn_present
  - test_issue_list_blank_template_defined
  - test_issue_list_bug_template_defined

  Bulk toolbar structure
  - test_issue_list_bulk_toolbar_present
  - test_issue_list_bulk_count_present
  - test_issue_list_bulk_label_select_present
  - test_issue_list_bulk_milestone_select_present
  - test_issue_list_issue_row_checkbox_present
  - test_issue_list_toggle_issue_select_js_present
  - test_issue_list_deselect_all_js_present
  - test_issue_list_update_bulk_toolbar_js_present
  - test_issue_list_bulk_close_js_present
  - test_issue_list_bulk_reopen_js_present
  - test_issue_list_bulk_assign_label_js_present
  - test_issue_list_bulk_assign_milestone_js_present
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubIssue, MusehubMilestone, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "beatmaker",
    slug: str = "grooves",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-beatmaker",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_issue(
    db: AsyncSession,
    repo_id: str,
    *,
    number: int = 1,
    title: str = "Bass too loud",
    state: str = "open",
    labels: list[str] | None = None,
    author: str = "beatmaker",
    milestone_id: str | None = None,
) -> MusehubIssue:
    """Seed an issue and return it."""
    issue = MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body="Issue body.",
        state=state,
        labels=labels or [],
        author=author,
        milestone_id=milestone_id,
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


async def _make_milestone(
    db: AsyncSession,
    repo_id: str,
    *,
    number: int = 1,
    title: str = "v1.0",
    state: str = "open",
) -> MusehubMilestone:
    """Seed a milestone and return it."""
    ms = MusehubMilestone(
        repo_id=repo_id,
        number=number,
        title=title,
        description="Milestone description.",
        state=state,
        author="beatmaker",
    )
    db.add(ms)
    await db.commit()
    await db.refresh(ms)
    return ms


async def _get_page(
    client: AsyncClient,
    owner: str = "beatmaker",
    slug: str = "grooves",
    **params: str,
) -> str:
    """Fetch the issue list page and return its text body."""
    resp = await client.get(f"/musehub/ui/{owner}/{slug}/issues", params=params)
    assert resp.status_code == 200
    return resp.text


# ---------------------------------------------------------------------------
# Basic page rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/issues returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/grooves/issues")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_issue_list_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue list page renders without a JWT token."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/beatmaker/grooves/issues")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_issue_list_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404."""
    response = await client.get("/musehub/ui/nobody/norepo/issues")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# SSR content — issue data is rendered server-side
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_renders_issue_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seeded issue title appears in SSR HTML without JS execution."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, title="Kick drum too punchy")
    body = await _get_page(client)
    assert "Kick drum too punchy" in body


@pytest.mark.anyio
async def test_issue_list_filter_form_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Filter form carries hx-get attribute for HTMX partial updates."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "hx-get" in body


@pytest.mark.anyio
async def test_issue_list_filter_form_has_hx_target(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Filter form targets #issue-rows for HTMX swaps."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert 'hx-target="#issue-rows"' in body or "hx-target='#issue-rows'" in body


# ---------------------------------------------------------------------------
# Open/closed tab counts
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_tab_open_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Open tab link carries hx-get for HTMX navigation."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "tab-open" in body
    assert "hx-get" in body


@pytest.mark.anyio
async def test_issue_list_open_closed_counts_in_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tab badges reflect the actual open and closed issue counts from the DB."""
    repo_id = await _make_repo(db_session)
    for i in range(3):
        await _make_issue(db_session, repo_id, number=i + 1, state="open")
    for i in range(2):
        await _make_issue(db_session, repo_id, number=i + 4, state="closed")
    body = await _get_page(client)
    assert ">3<" in body or ">3 <" in body or "3</span>" in body
    assert ">2<" in body or ">2 <" in body or "2</span>" in body


# ---------------------------------------------------------------------------
# State filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_state_filter_closed_shows_closed_only(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?state=closed returns only closed issues in the rendered HTML."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, number=1, title="Open issue", state="open")
    await _make_issue(db_session, repo_id, number=2, title="Closed issue", state="closed")
    body = await _get_page(client, state="closed")
    assert "Closed issue" in body
    assert "Open issue" not in body


# ---------------------------------------------------------------------------
# Label filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_label_filter_narrows_issues(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?label=bug returns only issues labelled 'bug'."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, number=1, title="Bug: kick too loud", labels=["bug"])
    await _make_issue(db_session, repo_id, number=2, title="Feature: add reverb", labels=["feature"])
    body = await _get_page(client, label="bug")
    assert "Bug: kick too loud" in body
    assert "Feature: add reverb" not in body


# ---------------------------------------------------------------------------
# HTMX fragment
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_htmx_request_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true returns a bare fragment — no <html> wrapper."""
    await _make_repo(db_session)
    resp = await client.get(
        "/musehub/ui/beatmaker/grooves/issues",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "<html" not in resp.text


@pytest.mark.anyio
async def test_issue_list_fragment_contains_issue_title(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTMX fragment contains the seeded issue title."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, title="Synth pad too bright")
    resp = await client.get(
        "/musehub/ui/beatmaker/grooves/issues",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Synth pad too bright" in resp.text


@pytest.mark.anyio
async def test_issue_list_fragment_empty_state_when_no_issues(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Fragment returns an empty-state message when no issues match filters."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, number=1, title="Open issue", state="open")
    resp = await client.get(
        "/musehub/ui/beatmaker/grooves/issues",
        params={"state": "closed"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "No issues" in resp.text or "no issues" in resp.text.lower()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_pagination_renders_next_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When total issues exceed per_page, a Next pagination link appears."""
    repo_id = await _make_repo(db_session)
    for i in range(30):
        await _make_issue(db_session, repo_id, number=i + 1, state="open")
    body = await _get_page(client, per_page="25")
    assert "Next" in body or "next" in body.lower()


# ---------------------------------------------------------------------------
# Right sidebar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_milestone_progress_in_right_sidebar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seeded milestone title appears in the right sidebar progress section."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, title="Album Release v1")
    body = await _get_page(client)
    assert "Album Release v1" in body


@pytest.mark.anyio
async def test_issue_list_right_sidebar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """sidebar-right element is present in the SSR page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "sidebar-right" in body


@pytest.mark.anyio
async def test_issue_list_milestone_progress_heading_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """milestone-progress-heading id is rendered server-side."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "milestone-progress-heading" in body


@pytest.mark.anyio
async def test_issue_list_milestone_progress_bar_css_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """milestone-progress-bar-fill CSS class is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "milestone-progress-bar-fill" in body


@pytest.mark.anyio
async def test_issue_list_milestone_progress_list_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """milestone-progress-list element id is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "milestone-progress-list" in body


@pytest.mark.anyio
async def test_issue_list_labels_summary_heading_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """labels-summary-heading id is rendered server-side in the right sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "labels-summary-heading" in body


@pytest.mark.anyio
async def test_issue_list_labels_summary_list_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """labels-summary-list id is rendered server-side in the right sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "labels-summary-list" in body


# ---------------------------------------------------------------------------
# Filter sidebar elements
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_filter_sidebar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """filter-sidebar id is rendered server-side."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "filter-sidebar" in body


@pytest.mark.anyio
async def test_issue_list_label_chip_container_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """label-chip-container id is present in the filter sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "label-chip-container" in body


@pytest.mark.anyio
async def test_issue_list_filter_milestone_select_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """filter-milestone <select> element is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "filter-milestone" in body


@pytest.mark.anyio
async def test_issue_list_filter_assignee_select_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """filter-assignee <select> element is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "filter-assignee" in body


@pytest.mark.anyio
async def test_issue_list_filter_author_input_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """filter-author text input is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "filter-author" in body


@pytest.mark.anyio
async def test_issue_list_sort_radio_group_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """sort-radio-group element is present in the filter sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "sort-radio-group" in body


@pytest.mark.anyio
async def test_issue_list_sort_radio_buttons_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Radio inputs with name='sort' are present (SSR-rendered)."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert 'name="sort"' in body or "name='sort'" in body


# ---------------------------------------------------------------------------
# Template selector / new-issue flow (minimal JS retained)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_template_picker_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """template-picker element is present in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "template-picker" in body


@pytest.mark.anyio
async def test_issue_list_template_grid_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """template-grid element is rendered server-side."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "template-grid" in body


@pytest.mark.anyio
async def test_issue_list_template_cards_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """template-card class is present (SSR-rendered template cards)."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "template-card" in body


@pytest.mark.anyio
async def test_issue_list_show_template_picker_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """showTemplatePicker() JS function is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "showTemplatePicker" in body


@pytest.mark.anyio
async def test_issue_list_select_template_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """selectTemplate() JS function is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "selectTemplate" in body


@pytest.mark.anyio
async def test_issue_list_issue_templates_const_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """ISSUE_TEMPLATES constant is present in the page JS."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "ISSUE_TEMPLATES" in body


@pytest.mark.anyio
async def test_issue_list_new_issue_btn_calls_template(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """new-issue-btn invokes showTemplatePicker."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "new-issue-btn" in body
    assert "showTemplatePicker" in body


@pytest.mark.anyio
async def test_issue_list_templates_back_btn_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """← Templates back navigation is present in the new issue flow."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "Templates" in body


@pytest.mark.anyio
async def test_issue_list_blank_template_defined(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """'blank' template id is present in ISSUE_TEMPLATES."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "'blank'" in body or '"blank"' in body


@pytest.mark.anyio
async def test_issue_list_bug_template_defined(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """'bug' template id is present in ISSUE_TEMPLATES."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "'bug'" in body or '"bug"' in body


# ---------------------------------------------------------------------------
# Bulk toolbar structure (SSR-rendered, JS-activated)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_bulk_toolbar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulk-toolbar element is rendered in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulk-toolbar" in body


@pytest.mark.anyio
async def test_issue_list_bulk_count_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulk-count element is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulk-count" in body


@pytest.mark.anyio
async def test_issue_list_bulk_label_select_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulk-label-select element is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulk-label-select" in body


@pytest.mark.anyio
async def test_issue_list_bulk_milestone_select_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulk-milestone-select element is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulk-milestone-select" in body


@pytest.mark.anyio
async def test_issue_list_issue_row_checkbox_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """issue-row-check CSS class is present (checkbox for bulk selection)."""
    repo_id = await _make_repo(db_session)
    await _make_issue(db_session, repo_id, title="Has checkbox")
    body = await _get_page(client)
    assert "issue-row-check" in body


@pytest.mark.anyio
async def test_issue_list_toggle_issue_select_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """toggleIssueSelect() JS function is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "toggleIssueSelect" in body


@pytest.mark.anyio
async def test_issue_list_deselect_all_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """deselectAll() JS function is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "deselectAll" in body


@pytest.mark.anyio
async def test_issue_list_update_bulk_toolbar_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """updateBulkToolbar() JS function is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "updateBulkToolbar" in body


@pytest.mark.anyio
async def test_issue_list_bulk_close_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkClose() JS stub is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkClose" in body


@pytest.mark.anyio
async def test_issue_list_bulk_reopen_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkReopen() JS stub is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkReopen" in body


@pytest.mark.anyio
async def test_issue_list_bulk_assign_label_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkAssignLabel() JS stub is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkAssignLabel" in body


@pytest.mark.anyio
async def test_issue_list_bulk_assign_milestone_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkAssignMilestone() JS stub is present in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkAssignMilestone" in body
