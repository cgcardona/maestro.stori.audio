"""Regression tests for the musehub.js cleanup (issue #586).

Verifies that displaced client-side rendering code has been removed from
musehub.js and all Jinja2 templates after the complete HTMX/SSR migration.

Covers:
- test_musehub_js_does_not_contain_render_rows — renderRows not in musehub.js
- test_musehub_js_does_not_contain_build_bulk_toolbar — buildBulkToolbar not in musehub.js
- test_musehub_js_does_not_contain_render_filter_sidebar — renderFilterSidebar not in musehub.js
- test_musehub_js_file_size_under_target — file < 20 KB after cleanup
- test_explore_base_html_deleted — explore_base.html has been removed
- test_all_template_page_script_blocks_empty_or_minimal — no legacy data-fetching
  patterns survive in any page template
"""

from __future__ import annotations

import pathlib

import pytest


# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_MUSEHUB_JS = _REPO_ROOT / "maestro" / "templates" / "musehub" / "static" / "musehub.js"
_TEMPLATE_ROOT = _REPO_ROOT / "maestro" / "templates" / "musehub"
_EXPLORE_BASE = _TEMPLATE_ROOT / "explore_base.html"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _musehub_js_text() -> str:
    """Return the full text of musehub.js, or empty string if absent."""
    if not _MUSEHUB_JS.exists():
        return ""
    return _MUSEHUB_JS.read_text(encoding="utf-8")


def _all_page_templates() -> list[pathlib.Path]:
    """Return all .html files under the musehub pages/ directory."""
    pages_dir = _TEMPLATE_ROOT / "pages"
    return list(pages_dir.glob("*.html"))


# ── Tests: musehub.js dead-code removal ───────────────────────────────────────


def test_musehub_js_does_not_contain_render_rows() -> None:
    """renderRows was the main issue-list DOM renderer — now replaced by Jinja2."""
    assert "renderRows" not in _musehub_js_text(), (
        "renderRows() still present in musehub.js — it must be removed; "
        "issue list HTML is now server-rendered."
    )


def test_musehub_js_does_not_contain_build_bulk_toolbar() -> None:
    """buildBulkToolbar was a client-side bulk-action UI builder — now gone."""
    assert "buildBulkToolbar" not in _musehub_js_text(), (
        "buildBulkToolbar() still present in musehub.js — it must be removed; "
        "bulk toolbar HTML is now part of the server-rendered issue list fragment."
    )


def test_musehub_js_does_not_contain_render_filter_sidebar() -> None:
    """renderFilterSidebar was a client-side sidebar renderer — now Jinja2."""
    assert "renderFilterSidebar" not in _musehub_js_text(), (
        "renderFilterSidebar() still present in musehub.js — it must be removed; "
        "filter sidebar is now rendered server-side."
    )


def test_musehub_js_does_not_contain_load_issues() -> None:
    """loadIssues was the client-side data-fetcher — replaced by route handlers."""
    assert "function loadIssues" not in _musehub_js_text(), (
        "loadIssues() still present in musehub.js — it must be removed; "
        "issue data is now fetched server-side by the FastAPI route."
    )


def test_musehub_js_does_not_contain_load_labels() -> None:
    """loadLabels was a client-side data-fetcher for issue-list sidebar."""
    assert "function loadLabels" not in _musehub_js_text(), (
        "loadLabels() still present in musehub.js — replaced by server-side rendering."
    )


def test_musehub_js_does_not_contain_render_right_sidebar() -> None:
    """renderRightSidebar was a client-side renderer — now handled by Jinja2."""
    assert "renderRightSidebar" not in _musehub_js_text(), (
        "renderRightSidebar() still present in musehub.js — must be removed."
    )


def test_musehub_js_file_size_under_target() -> None:
    """File must be under 20 KB after cleanup (was 3–4 K lines before migration)."""
    if not _MUSEHUB_JS.exists():
        pytest.skip("musehub.js not found")
    size_bytes = _MUSEHUB_JS.stat().st_size
    limit_bytes = 20 * 1024  # 20 KB
    assert size_bytes < limit_bytes, (
        f"musehub.js is {size_bytes:,} bytes — exceeds 20 KB cleanup target. "
        "Audit the file and remove any remaining dead code."
    )


# ── Tests: template cleanup ────────────────────────────────────────────────────


def test_explore_base_html_deleted() -> None:
    """explore_base.html must be deleted — no template extends it any more."""
    assert not _EXPLORE_BASE.exists(), (
        "explore_base.html still exists at "
        f"{_EXPLORE_BASE} — it is no longer referenced by any "
        "template and should be removed."
    )


def test_all_template_page_script_blocks_empty_or_minimal() -> None:
    """No page template should contain legacy client-side data-fetching patterns.

    After the SSR/HTMX migration, the displaced fetch functions and state
    variables must not appear in any {% block page_script %} section.
    This is a regression guard: if a future merge re-introduces these patterns
    the test will catch it immediately.
    """
    # Patterns that were removed as part of the HTMX migration.
    # Their presence in any template indicates un-migrated client-side rendering.
    displaced_patterns: list[str] = [
        "function loadIssues",
        "function loadLabels",
        "function loadMilestones",
        "function renderRows",
        "function renderFilterSidebar",
        "function renderRightSidebar",
        "function buildBulkToolbar",
        "function buildTemplatePicker",
        "function loadCommentCounts",
        "function loadReactionSummaries",
        "function loadStashes",
        "function loadNotifications",
        "function loadReleases",
        "function loadSessions",
        "function loadCollaborators",
        "function loadSettings",
        "function loadActivity",
        "const allIssues",
        "let allIssues",
        "var allIssues",
        "const cachedOpen",
        "let cachedOpen",
        "const cachedClosed",
        "let cachedClosed",
        "const allLabels",
        "let allLabels",
        "const allMilestones",
        "let allMilestones",
    ]

    violations: list[str] = []
    for template in _all_page_templates():
        text = template.read_text(encoding="utf-8")
        for pattern in displaced_patterns:
            if pattern in text:
                violations.append(f"{template.name}: found '{pattern}'")

    assert not violations, (
        "Legacy client-side rendering patterns found in page templates.\n"
        "These functions/variables were displaced by server-side rendering "
        "and must be removed:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )
