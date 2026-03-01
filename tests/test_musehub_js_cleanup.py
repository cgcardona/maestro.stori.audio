"""Tests enforcing the musehub.js + template cleanup after HTMX SSR migration.

These tests guard against dead client-side rendering code creeping back into
the shared utility file and validate that obsolete assets (explore_base.html)
have been permanently removed.

Related issue: #586 — audit + trim musehub.js + remove dead templates.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Root of the repository templates and static assets.
_TEMPLATE_ROOT = Path(__file__).parent.parent / "maestro" / "templates" / "musehub"
_MUSEHUB_JS = _TEMPLATE_ROOT / "static" / "musehub.js"

# Canvas / audio pages that legitimately keep page_script blocks with JS.
# These pages drive WaveSurfer, ABC.js, piano-roll canvas, or the audio player
# and cannot be server-side-rendered without significant infrastructure work.
_ALLOWED_SCRIPT_PAGES = {
    "arrange.html",     # DAW canvas
    "listen.html",      # WaveSurfer audio player
    "embed.html",       # iframe audio player
    "piano_roll.html",  # piano-roll canvas
    "score.html",       # ABC.js score renderer
    "graph.html",       # commit DAG canvas
    "blob.html",        # hex/text blob viewer
}


# ---------------------------------------------------------------------------
# musehub.js content guards
# ---------------------------------------------------------------------------


def test_musehub_js_does_not_contain_render_rows() -> None:
    """renderRows() must not appear in musehub.js.

    renderRows was a client-side DOM renderer for the issue list.
    After SSR migration it belongs only in page-specific script blocks
    (if at all), never in the shared utility file.
    """
    source = _MUSEHUB_JS.read_text()
    assert "renderRows" not in source, (
        "renderRows() found in musehub.js — remove it; "
        "issue list rendering is now server-side."
    )


def test_musehub_js_does_not_contain_build_bulk_toolbar() -> None:
    """buildBulkToolbar() must not appear in musehub.js.

    buildBulkToolbar was a client-side DOM builder for the issue list bulk
    action bar.  It was replaced by server-rendered HTML fragments.
    """
    source = _MUSEHUB_JS.read_text()
    assert "buildBulkToolbar" not in source, (
        "buildBulkToolbar() found in musehub.js — remove it; "
        "the bulk toolbar is now server-rendered."
    )


def test_musehub_js_does_not_contain_render_filter_sidebar() -> None:
    """renderFilterSidebar() must not appear in musehub.js.

    renderFilterSidebar was a client-side sidebar builder for the issue list.
    It was replaced by a Jinja2 template fragment.
    """
    source = _MUSEHUB_JS.read_text()
    assert "renderFilterSidebar" not in source, (
        "renderFilterSidebar() found in musehub.js — remove it; "
        "the filter sidebar is now server-rendered."
    )


def test_musehub_js_does_not_contain_client_side_issue_loaders() -> None:
    """Page-specific data-fetching functions must not appear in musehub.js.

    Functions like loadIssues, loadLabels, loadMilestones were page-specific
    apiFetch wrappers that belong — at most — in page-specific script blocks,
    not in the shared utility file loaded on every page.
    """
    source = _MUSEHUB_JS.read_text()
    dead_functions = [
        "loadIssues",
        "loadLabels",
        "loadMilestones",
        "loadStashes",
        "loadNotifications",
        "loadReleases",
        "loadSessions",
        "loadCollaborators",
        "loadSettings",
        "loadCredits",
        "loadActivity",
    ]
    for fn in dead_functions:
        assert fn not in source, (
            f"{fn}() found in musehub.js — page-specific loaders must not "
            "live in the shared utility file."
        )


def test_musehub_js_does_not_contain_client_side_state_vars() -> None:
    """Client-side issue-list state variables must not appear in musehub.js.

    allIssues, cachedOpen, cachedClosed were global JS state stores for the
    client-side issue list.  Server-side rendering replaced them with URL query
    parameters, so they must not exist in the shared utility file.
    """
    source = _MUSEHUB_JS.read_text()
    dead_vars = ["allIssues", "cachedOpen", "cachedClosed", "allLabels", "allMilestones"]
    for var in dead_vars:
        assert var not in source, (
            f"{var} found in musehub.js — client-side state variables must not "
            "live in the shared utility file; use URL query params instead."
        )


def test_musehub_js_file_size_under_target() -> None:
    """musehub.js must be under 20 KB (unminified).

    After the SSR migration the file should contain only:
      - auth helpers + HTMX JWT bridge
      - formatting helpers (fmtDate, shortSha, …)
      - initRepoNav (repo header card)
      - audio player controls
      - commit message parser
      - reaction bar
    Large page-specific logic bloats the file loaded on every page.
    """
    size = _MUSEHUB_JS.stat().st_size
    assert size < 20_000, (
        f"musehub.js is {size} bytes — exceeds the 20 KB target. "
        "Move page-specific logic to page-level script blocks."
    )


# ---------------------------------------------------------------------------
# Dead template cleanup
# ---------------------------------------------------------------------------


def test_explore_base_html_deleted() -> None:
    """explore_base.html must not exist in the template tree.

    explore_base.html was an old standalone HTML page (no Jinja2 base
    extension) used by the explore and trending pages before the SSR migration.
    Issue #576 migrated explore to base.html; issue #586 removed the trending
    page's dependency, making explore_base.html unreachable.  It must be gone.
    """
    path = _TEMPLATE_ROOT / "explore_base.html"
    assert not path.exists(), (
        "explore_base.html still exists — it should have been deleted as part "
        "of the HTMX SSR migration (issues #576 and #586)."
    )


# ---------------------------------------------------------------------------
# musehub.js function inventory
# ---------------------------------------------------------------------------


def test_musehub_js_retains_htmx_bridge() -> None:
    """The HTMX JWT bridge added in #552 must still be present in musehub.js.

    htmx:configRequest injects the Bearer token on every HTMX request so
    mutations stay authenticated without per-page setup.
    """
    source = _MUSEHUB_JS.read_text()
    assert "htmx:configRequest" in source, (
        "HTMX JWT bridge (htmx:configRequest) missing from musehub.js — "
        "it must be present for authenticated HTMX mutations to work."
    )
    assert "htmx:afterSwap" in source, (
        "htmx:afterSwap hook missing from musehub.js — "
        "it is required to re-run initRepoNav after HTMX page swaps."
    )


def test_musehub_js_retains_core_helpers() -> None:
    """Core utility functions required by the majority of page templates must remain.

    These functions are shared across 10+ templates and may not be removed
    until every caller is migrated to server-side equivalents.
    """
    source = _MUSEHUB_JS.read_text()
    required = [
        "getToken",        # HTMX JWT bridge + auth-gated UI elements
        "apiFetch",        # mutation calls (reactions, follow, etc.)
        "initRepoNav",     # repo header card enrichment
        "escHtml",         # XSS-safe DOM injection in remaining JS pages
        "fmtDate",         # date formatting in remaining JS pages
    ]
    for fn in required:
        assert fn in source, (
            f"{fn} missing from musehub.js — it is still required by active templates."
        )
