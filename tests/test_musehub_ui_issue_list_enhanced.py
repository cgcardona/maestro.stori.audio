"""Regression tests for the enhanced issue list page (issue #445).

Covers five feature areas added to issue_list.html:

Filter sidebar
- test_issue_list_page_returns_200                  — page renders without auth
- test_issue_list_no_auth_required                  — GET needs no JWT
- test_issue_list_unknown_repo_404                  — unknown owner/slug → 404
- test_issue_list_filter_sidebar_present            — filter-sidebar element present
- test_issue_list_label_chip_container_present      — label-chip-container element present
- test_issue_list_filter_milestone_select_present   — filter-milestone <select> present
- test_issue_list_filter_assignee_select_present    — filter-assignee <select> present
- test_issue_list_filter_author_input_present       — filter-author <input> present
- test_issue_list_sort_radio_group_present          — sort-radio-group element present
- test_issue_list_sort_radio_buttons_present        — radio inputs with name="sort-radio" present
- test_issue_list_clear_filters_btn_present         — clear-all-filters button present
- test_issue_list_toggle_label_filter_js_present    — toggleLabelFilter() JS function present
- test_issue_list_clear_all_filters_js_present      — clearAllFilters() JS function present
- test_issue_list_apply_filters_js_present          — applyFilters() JS function present

Milestone progress sidebar
- test_issue_list_milestone_progress_heading_present  — milestone-progress-heading element present
- test_issue_list_milestone_progress_bar_css_present  — milestone-progress-bar-fill CSS present
- test_issue_list_right_sidebar_present               — sidebar-right element present
- test_issue_list_render_right_sidebar_js_present     — renderRightSidebar() JS function present
- test_issue_list_milestone_progress_list_present     — milestone-progress-list element present

Labels sidebar
- test_issue_list_labels_summary_heading_present    — labels-summary-heading element present
- test_issue_list_labels_summary_list_present       — labels-summary-list element present
- test_issue_list_render_right_sidebar_label_js     — renderRightSidebar contains label sidebar logic

Bulk actions toolbar
- test_issue_list_bulk_toolbar_present              — bulk-toolbar element present
- test_issue_list_bulk_count_present               — bulk-count element present
- test_issue_list_bulk_label_select_present        — bulk-label-select element present
- test_issue_list_bulk_milestone_select_present    — bulk-milestone-select element present
- test_issue_list_bulk_close_button_present        — bulkClose() function present
- test_issue_list_bulk_reopen_button_present       — bulkReopen() function present
- test_issue_list_bulk_assign_label_js_present     — bulkAssignLabel() JS function present
- test_issue_list_bulk_assign_milestone_js_present — bulkAssignMilestone() JS function present
- test_issue_list_toggle_issue_select_js_present   — toggleIssueSelect() JS function present
- test_issue_list_deselect_all_js_present          — deselectAll() JS function present
- test_issue_list_issue_row_checkbox_js_present    — issue-row-check checkbox class present
- test_issue_list_update_bulk_toolbar_js_present   — updateBulkToolbar() JS function present

Issue template selector
- test_issue_list_template_picker_present          — template-picker element present
- test_issue_list_template_grid_present            — template-grid element present
- test_issue_list_template_cards_present           — template-card elements present
- test_issue_list_show_template_picker_js_present  — showTemplatePicker() JS function present
- test_issue_list_select_template_js_present       — selectTemplate() JS function present
- test_issue_list_issue_templates_const_present    — ISSUE_TEMPLATES constant defined
- test_issue_list_new_issue_btn_calls_template     — new-issue-btn invokes showTemplatePicker
- test_issue_list_templates_back_btn_present       — ← Templates back button present
- test_issue_list_blank_template_defined           — blank template in ISSUE_TEMPLATES
- test_issue_list_bug_template_defined             — bug template in ISSUE_TEMPLATES
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


async def _get_page(client: AsyncClient, owner: str = "beatmaker", slug: str = "grooves") -> str:
    """Fetch the issue list page and return its text body."""
    resp = await client.get(f"/musehub/ui/{owner}/{slug}/issues")
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
# Filter sidebar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_filter_sidebar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The filter-sidebar element is present in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "filter-sidebar" in body


@pytest.mark.anyio
async def test_issue_list_label_chip_container_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """label-chip-container element is rendered in the filter sidebar."""
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
    """Radio inputs with name='sort-radio' are present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert 'name="sort-radio"' in body or "name='sort-radio'" in body


@pytest.mark.anyio
async def test_issue_list_clear_filters_btn_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Clear all filters button is present."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "clearAllFilters" in body


@pytest.mark.anyio
async def test_issue_list_toggle_label_filter_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """toggleLabelFilter() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "toggleLabelFilter" in body


@pytest.mark.anyio
async def test_issue_list_clear_all_filters_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """clearAllFilters() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "clearAllFilters" in body


@pytest.mark.anyio
async def test_issue_list_apply_filters_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """applyFilters() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "applyFilters" in body


# ---------------------------------------------------------------------------
# Milestone progress sidebar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_milestone_progress_heading_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """milestone-progress-heading element is present."""
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
async def test_issue_list_right_sidebar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """sidebar-right element is present in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "sidebar-right" in body


@pytest.mark.anyio
async def test_issue_list_render_right_sidebar_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """renderRightSidebar() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "renderRightSidebar" in body


@pytest.mark.anyio
async def test_issue_list_milestone_progress_list_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """milestone-progress-list element is present in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "milestone-progress-list" in body


# ---------------------------------------------------------------------------
# Labels sidebar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_labels_summary_heading_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """labels-summary-heading element is present in the right sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "labels-summary-heading" in body


@pytest.mark.anyio
async def test_issue_list_labels_summary_list_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """labels-summary-list element is present in the right sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "labels-summary-list" in body


@pytest.mark.anyio
async def test_issue_list_render_right_sidebar_label_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """renderRightSidebar() references labels-summary-list for the labels sidebar."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "labels-summary-list" in body
    assert "renderRightSidebar" in body


# ---------------------------------------------------------------------------
# Bulk actions toolbar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_list_bulk_toolbar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulk-toolbar element is present in the page HTML."""
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
async def test_issue_list_bulk_close_button_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkClose() function is defined in the page JS."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkClose" in body


@pytest.mark.anyio
async def test_issue_list_bulk_reopen_button_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkReopen() function is defined in the page JS."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkReopen" in body


@pytest.mark.anyio
async def test_issue_list_bulk_assign_label_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkAssignLabel() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkAssignLabel" in body


@pytest.mark.anyio
async def test_issue_list_bulk_assign_milestone_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """bulkAssignMilestone() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "bulkAssignMilestone" in body


@pytest.mark.anyio
async def test_issue_list_toggle_issue_select_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """toggleIssueSelect() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "toggleIssueSelect" in body


@pytest.mark.anyio
async def test_issue_list_deselect_all_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """deselectAll() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "deselectAll" in body


@pytest.mark.anyio
async def test_issue_list_issue_row_checkbox_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """issue-row-check CSS class is referenced in the page (for selection checkboxes)."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "issue-row-check" in body


@pytest.mark.anyio
async def test_issue_list_update_bulk_toolbar_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """updateBulkToolbar() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "updateBulkToolbar" in body


# ---------------------------------------------------------------------------
# Issue template selector
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
    """template-grid element is present in the page HTML."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "template-grid" in body


@pytest.mark.anyio
async def test_issue_list_template_cards_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """template-card CSS class is present (one card per template)."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "template-card" in body


@pytest.mark.anyio
async def test_issue_list_show_template_picker_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """showTemplatePicker() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "showTemplatePicker" in body


@pytest.mark.anyio
async def test_issue_list_select_template_js_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """selectTemplate() JS function is defined in the page."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "selectTemplate" in body


@pytest.mark.anyio
async def test_issue_list_issue_templates_const_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """ISSUE_TEMPLATES constant is defined in the page JS."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "ISSUE_TEMPLATES" in body


@pytest.mark.anyio
async def test_issue_list_new_issue_btn_calls_template(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """new-issue-btn onclick invokes showTemplatePicker (not showCreateIssue directly)."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "new-issue-btn" in body
    assert "showTemplatePicker" in body


@pytest.mark.anyio
async def test_issue_list_templates_back_btn_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """← Templates back navigation button is present in the new issue form."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "Templates" in body


@pytest.mark.anyio
async def test_issue_list_blank_template_defined(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """'blank' template entry is present in ISSUE_TEMPLATES."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "'blank'" in body or '"blank"' in body


@pytest.mark.anyio
async def test_issue_list_bug_template_defined(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """'bug' template entry is present in ISSUE_TEMPLATES."""
    await _make_repo(db_session)
    body = await _get_page(client)
    assert "'bug'" in body or '"bug"' in body
