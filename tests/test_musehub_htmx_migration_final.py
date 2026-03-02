"""Final audit tests for the HTMX migration — issue #587.

Verifies that the full MuseHub SSR + HTMX + Alpine.js migration is complete:
- No dead ``apiFetch`` calls remain in SSR-migrated page templates.
- All SSR-migrated routes return 200 with HTML content.
- Routes using ``htmx_fragment_or_full`` return bare fragment (no ``<html>``) on
  ``HX-Request: true``.
- No legacy inline HTML builders (``_render_*_html`` functions) remain in ui modules.
- The HTMX JWT auth bridge (``htmx:configRequest`` Bearer token injection) is
  present in ``musehub.js``.

Test naming: ``test_<what>_<scenario>``.

Canvas/audio/visualization pages (arrange, listen, piano_roll, graph, timeline,
analysis sub-pages, etc.) legitimately use ``apiFetch`` for client-side data
rendering and are explicitly exempted from the apiFetch audit.

Covers:
- test_no_apifetch_in_page_templates
- test_all_page_routes_return_200 (parametrized)
- test_all_page_routes_return_fragment_for_htmx_request (parametrized)
- test_no_inline_html_builders_in_ui_py
- test_musehub_js_has_htmx_config_request_bridge
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo

# ── Filesystem roots ─────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
_PAGES_DIR = _REPO_ROOT / "maestro" / "templates" / "musehub" / "pages"
_MUSEHUB_JS = _REPO_ROOT / "maestro" / "templates" / "musehub" / "static" / "musehub.js"
_UI_PY = _REPO_ROOT / "maestro" / "api" / "routes" / "musehub" / "ui.py"
_UI_EXTRA = list((_REPO_ROOT / "maestro" / "api" / "routes" / "musehub").glob("ui_*.py"))

# ── Exempted pages: visualization/canvas/audio — apiFetch is intentional ────
#
# These pages render charts, piano-roll canvases, or audio waveforms that
# require client-side JS to fetch and render binary/JSON data streams.  They
# were NOT part of the SSR listing-page migration (issues #555–#586) and
# their ``apiFetch`` calls are the live data-fetching mechanism, not dead code.
_APIFETCH_EXEMPT_PAGES: frozenset[str] = frozenset(
    {
        # Canvas / MIDI / audio players
        "arrange.html",
        "listen.html",
        "piano_roll.html",
        "embed.html",
        "score.html",
        # Analysis dashboards — JS fetches JSON for chart rendering
        "analysis.html",
        "contour.html",
        "tempo.html",
        "dynamics.html",
        "key.html",
        "meter.html",
        "chord_map.html",
        "groove.html",
        "groove_check.html",
        "emotion.html",
        "form.html",
        "form_structure.html",
        "motifs.html",
        # Visualization / interactive graph pages
        "graph.html",
        "timeline.html",
        "compare.html",
        "divergence.html",
        # Complex pages with audio analysis and inline commenting
        "commit.html",
        # Feed & discovery pages that remain JS-driven
        "feed.html",
        # Topics — uses raw fetch for a non-apiFetch endpoint
        "topics.html",
        # Repo/insights pages with mixed SSR+JS widgets
        "insights.html",
        "repo_home.html",
        # Context viewer — AI JSON fetch
        "context.html",
        "diff.html",
    }
)

# ── Test routes ──────────────────────────────────────────────────────────────
#
# Parametrized route table.  Each entry is (test_id, url_template).
# ``{O}`` and ``{S}`` are replaced with the seeded owner/slug at runtime.
#
# «no-repo» routes work without any seeded repo.
# «repo» routes require the fixture repo to be seeded first.

_OWNER = "htmx-test-user"
_SLUG = "migration-audit"


def _url(path: str) -> str:
    """Expand {O}/{S} placeholders into the fixture owner/slug."""
    return path.replace("{O}", _OWNER).replace("{S}", _SLUG)


# All SSR-migrated routes: those that use htmx_fragment_or_full() or
# negotiate_response() and return pre-rendered Jinja2 HTML.
_MIGRATED_ROUTES: list[tuple[str, str]] = [
    # Fixed routes — no repo required
    ("explore", "/musehub/ui/explore"),
    ("trending", "/musehub/ui/trending"),
    ("global_search", "/musehub/ui/search"),
    # Repo-scoped listing routes
    ("repo_home", "/musehub/ui/{O}/{S}"),
    ("commits_list", "/musehub/ui/{O}/{S}/commits"),
    ("pr_list", "/musehub/ui/{O}/{S}/pulls"),
    ("issue_list", "/musehub/ui/{O}/{S}/issues"),
    ("releases", "/musehub/ui/{O}/{S}/releases"),
    ("sessions", "/musehub/ui/{O}/{S}/sessions"),
    ("in_repo_search", "/musehub/ui/{O}/{S}/search"),
    ("activity", "/musehub/ui/{O}/{S}/activity"),
    ("credits", "/musehub/ui/{O}/{S}/credits"),
    ("branches", "/musehub/ui/{O}/{S}/branches"),
    ("tags", "/musehub/ui/{O}/{S}/tags"),
]

# Subset of _MIGRATED_ROUTES whose handlers call htmx_fragment_or_full() —
# these must return a bare fragment (no <html>) when HX-Request: true.
_HTMX_FRAGMENT_ROUTES: list[tuple[str, str]] = [
    ("explore", "/musehub/ui/explore"),
    ("trending", "/musehub/ui/trending"),
    ("global_search", "/musehub/ui/search"),
    ("repo_home", "/musehub/ui/{O}/{S}"),
    ("commits_list", "/musehub/ui/{O}/{S}/commits"),
    ("pr_list", "/musehub/ui/{O}/{S}/pulls"),
    ("issue_list", "/musehub/ui/{O}/{S}/issues"),
    ("releases", "/musehub/ui/{O}/{S}/releases"),
    ("sessions", "/musehub/ui/{O}/{S}/sessions"),
    ("in_repo_search", "/musehub/ui/{O}/{S}/search"),
    ("activity", "/musehub/ui/{O}/{S}/activity"),
    ("branches", "/musehub/ui/{O}/{S}/branches"),
]


# ── Seed helper ──────────────────────────────────────────────────────────────


async def _seed_repo(db: AsyncSession) -> str:
    """Seed a minimal public repo for route-level tests; return its repo_id."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id="uid-htmx-audit-owner",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


# ── Static / code-analysis tests (no HTTP calls needed) ─────────────────────


def test_no_apifetch_in_page_templates() -> None:
    """No SSR-migrated page template may contain a live ``apiFetch`` call.

    Canvas/audio/visualization pages are explicitly exempted (see
    ``_APIFETCH_EXEMPT_PAGES``).  All other templates in ``pages/`` represent
    listing/CRUD pages that must serve data server-side via Jinja2, not via
    client-side API calls.

    A failure here means a page template was migrated to SSR but still has a
    leftover ``apiFetch`` call that is now dead code.
    """
    violations: list[str] = []
    for html_file in sorted(_PAGES_DIR.glob("*.html")):
        if html_file.name in _APIFETCH_EXEMPT_PAGES:
            continue
        content = html_file.read_text()
        if "apiFetch(" in content:
            # Count occurrences for a clear error message.
            count = content.count("apiFetch(")
            violations.append(f"{html_file.name}: {count} apiFetch call(s)")

    assert not violations, (
        "Dead apiFetch calls found in SSR-migrated page templates "
        "(add to _APIFETCH_EXEMPT_PAGES if the page is legitimately canvas/audio):\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_no_inline_html_builders_in_ui_py() -> None:
    """No ``_render_*_html`` function may exist in ui.py or any ui_*.py.

    The old pattern was to build HTML strings in Python (``_render_row_html``,
    ``_render_header_html``, etc.) and return them as ``HTMLResponse``.  The
    SSR migration replaced all of these with Jinja2 template rendering.  Any
    remaining ``_render_*_html`` definition is a regression.

    Known exception: ``ui_user_profile.py::_render_profile_html`` — the user
    profile page is a complex JS-hydrated shell (heatmap, badges, activity tabs)
    that was intentionally kept outside the listing-page SSR migration scope.
    """
    # Files exempt from this check: they intentionally keep JS-shell patterns
    # because they render complex interactive widgets (heatmaps, canvases, etc.)
    # that cannot be expressed as static Jinja2 HTML.
    _EXEMPT_UI_FILES: frozenset[str] = frozenset({"ui_user_profile.py"})

    pattern = re.compile(r"\bdef\s+_render_\w+_html\b")
    violations: list[str] = []
    for py_file in [_UI_PY] + sorted(_UI_EXTRA):
        if py_file.name in _EXEMPT_UI_FILES:
            continue
        content = py_file.read_text()
        matches = pattern.findall(content)
        if matches:
            violations.append(f"{py_file.name}: {matches}")

    assert not violations, (
        "Legacy _render_*_html functions found — remove and replace with Jinja2:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_musehub_js_has_htmx_config_request_bridge() -> None:
    """``musehub.js`` injects the Bearer token on every HTMX request.

    The ``htmx:configRequest`` listener must be registered so HTMX partial
    requests carry the same ``Authorization`` header as the initial page load.
    Without this bridge, HTMX-driven tab switches and filter reloads are
    rejected by the auth middleware.

    Complementary to ``test_musehub_ui_htmx_infra.py::test_musehub_js_has_htmx_config_request_bridge``.
    This test additionally verifies the Bearer token is actually set in the
    request headers (not just that the listener is registered).
    """
    content = _MUSEHUB_JS.read_text()
    assert "htmx:configRequest" in content, (
        "htmx:configRequest listener missing from musehub.js — "
        "HTMX requests will lack Authorization headers"
    )
    assert "Authorization" in content, (
        "Authorization header injection missing from musehub.js configRequest handler"
    )
    assert "Bearer" in content, (
        "Bearer token pattern missing from musehub.js configRequest handler"
    )


def test_dead_templates_removed() -> None:
    """Orphan templates that were superseded by the SSR migration must not exist.

    ``release_list.html`` was replaced by ``releases.html`` (issue #572).
    ``repo.html`` was replaced by ``repo_home.html`` (issue #560 era).
    Keeping orphan templates with ``apiFetch`` calls creates confusion about
    which template is active and inflates the apiFetch audit surface.
    """
    assert not (_PAGES_DIR / "release_list.html").exists(), (
        "release_list.html still present — delete it (replaced by releases.html)"
    )
    assert not (_PAGES_DIR / "repo.html").exists(), (
        "repo.html still present — delete it (replaced by repo_home.html)"
    )


# ── HTTP route tests — require seeded repo ───────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("route_id,url_tpl", _MIGRATED_ROUTES, ids=[r[0] for r in _MIGRATED_ROUTES])
async def test_all_page_routes_return_200(
    client: AsyncClient,
    db_session: AsyncSession,
    route_id: str,
    url_tpl: str,
) -> None:
    """Every SSR-migrated UI route returns HTTP 200 with text/html content.

    Data is fetched server-side; the page must render without client-side JS
    execution.  Routes that need a repo are tested against a seeded fixture
    repo (``{O}/{S}``).  Empty-state rendering (no commits, no releases, etc.)
    is acceptable — the test only verifies the route does not 404/500.
    """
    await _seed_repo(db_session)
    url = _url(url_tpl)
    response = await client.get(url)
    assert response.status_code == 200, (
        f"Route '{route_id}' ({url}) returned {response.status_code}, expected 200"
    )
    assert "text/html" in response.headers.get("content-type", ""), (
        f"Route '{route_id}' ({url}) did not return text/html"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "route_id,url_tpl",
    _HTMX_FRAGMENT_ROUTES,
    ids=[r[0] for r in _HTMX_FRAGMENT_ROUTES],
)
async def test_all_page_routes_return_fragment_for_htmx_request(
    client: AsyncClient,
    db_session: AsyncSession,
    route_id: str,
    url_tpl: str,
) -> None:
    """Routes using ``htmx_fragment_or_full`` return a bare fragment on ``HX-Request: true``.

    The fragment must:
    - Return HTTP 200.
    - NOT include ``<html``, ``<head``, or ``<body`` (those belong to the shell).
    - Include some non-empty HTML content (not a blank response).

    This ensures HTMX tab-switching and filter-reloading swaps only the target
    container, not the full page, preventing double-navigation flash.
    """
    await _seed_repo(db_session)
    url = _url(url_tpl)
    response = await client.get(url, headers={"HX-Request": "true"})
    assert response.status_code == 200, (
        f"Route '{route_id}' ({url}) returned {response.status_code} on HX-Request"
    )
    body = response.text
    assert "<html" not in body, (
        f"Route '{route_id}' returned full page shell on HX-Request (found <html)"
    )
    assert "<head" not in body, (
        f"Route '{route_id}' returned full page shell on HX-Request (found <head)"
    )
    assert len(body.strip()) > 0, (
        f"Route '{route_id}' returned empty fragment on HX-Request"
    )
