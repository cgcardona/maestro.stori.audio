"""Tests for MuseHub Jinja2 server-side filters and component macros.

Covers every filter function in jinja2_filters.py and spot-checks macro
rendering via a real Jinja2 Environment backed by the on-disk template tree.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from maestro.api.routes.musehub.jinja2_filters import (
    _fmtdate,
    _fmtrelative,
    _label_text_color,
    _shortsha,
    register_musehub_filters,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jinja_env() -> Environment:
    """Real Jinja2 Environment pointed at the MuseHub template tree."""
    template_dir = Path(__file__).parent.parent / "maestro" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    register_musehub_filters(env)
    return env


# ---------------------------------------------------------------------------
# _fmtdate filter
# ---------------------------------------------------------------------------


def test_fmtdate_formats_datetime() -> None:
    result = _fmtdate(datetime(2025, 1, 15, 10, 0, 0))
    assert result == "Jan 15, 2025"


def test_fmtdate_formats_iso_string() -> None:
    result = _fmtdate("2025-01-15T10:00:00Z")
    assert result == "Jan 15, 2025"


def test_fmtdate_none_returns_empty() -> None:
    assert _fmtdate(None) == ""


def test_fmtdate_iso_string_with_offset() -> None:
    result = _fmtdate("2025-06-01T08:00:00+00:00")
    assert result == "Jun 1, 2025"


# ---------------------------------------------------------------------------
# _fmtrelative filter
# ---------------------------------------------------------------------------


def test_fmtrelative_seconds() -> None:
    value = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _fmtrelative(value) == "just now"


def test_fmtrelative_one_minute() -> None:
    value = datetime.now(timezone.utc) - timedelta(seconds=90)
    assert _fmtrelative(value) == "1 minute ago"


def test_fmtrelative_hours() -> None:
    value = datetime.now(timezone.utc) - timedelta(hours=2)
    assert _fmtrelative(value) == "2 hours ago"


def test_fmtrelative_one_hour() -> None:
    value = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _fmtrelative(value) == "1 hour ago"


def test_fmtrelative_days() -> None:
    value = datetime.now(timezone.utc) - timedelta(days=3)
    assert _fmtrelative(value) == "3 days ago"


def test_fmtrelative_one_day() -> None:
    value = datetime.now(timezone.utc) - timedelta(days=1)
    assert _fmtrelative(value) == "1 day ago"


def test_fmtrelative_none_returns_empty() -> None:
    assert _fmtrelative(None) == ""


def test_fmtrelative_iso_string() -> None:
    value = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    result = _fmtrelative(value)
    assert result == "5 hours ago"


def test_fmtrelative_naive_datetime_treated_as_utc() -> None:
    """Timezone-naive datetimes are assumed UTC per docstring contract."""
    value = datetime.utcnow() - timedelta(minutes=30)
    result = _fmtrelative(value)
    assert result == "30 minutes ago"


# ---------------------------------------------------------------------------
# _shortsha filter
# ---------------------------------------------------------------------------


def test_shortsha_returns_8_chars() -> None:
    assert _shortsha("a1b2c3d4e5f6") == "a1b2c3d4"


def test_shortsha_already_short() -> None:
    assert _shortsha("abc") == "abc"


def test_shortsha_none_returns_empty() -> None:
    assert _shortsha(None) == ""


def test_shortsha_empty_string_returns_empty() -> None:
    assert _shortsha("") == ""


def test_shortsha_exact_8_chars() -> None:
    assert _shortsha("12345678") == "12345678"


# ---------------------------------------------------------------------------
# _label_text_color filter
# ---------------------------------------------------------------------------


def test_label_text_color_dark_bg() -> None:
    assert _label_text_color("#000000") == "#fff"


def test_label_text_color_light_bg() -> None:
    assert _label_text_color("#ffffff") == "#000"


def test_label_text_color_mid_green() -> None:
    """Bright green (#3fb950) has high luminance — dark text is more readable."""
    assert _label_text_color("#3fb950") == "#000"


def test_label_text_color_without_hash() -> None:
    assert _label_text_color("ffffff") == "#000"


def test_label_text_color_malformed_returns_dark() -> None:
    assert _label_text_color("#xyz") == "#000"


def test_label_text_color_red() -> None:
    assert _label_text_color("#ff0000") == "#fff"


# ---------------------------------------------------------------------------
# register_musehub_filters — environment registration
# ---------------------------------------------------------------------------


def test_jinja2_env_has_fmtdate_filter(jinja_env: Environment) -> None:
    assert "fmtdate" in jinja_env.filters


def test_jinja2_env_has_fmtrelative_filter(jinja_env: Environment) -> None:
    assert "fmtrelative" in jinja_env.filters


def test_jinja2_env_has_shortsha_filter(jinja_env: Environment) -> None:
    assert "shortsha" in jinja_env.filters


def test_jinja2_env_has_label_text_color_filter(jinja_env: Environment) -> None:
    assert "label_text_color" in jinja_env.filters


def test_fmtdate_filter_via_env(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string('{{ "2025-01-15T10:00:00Z" | fmtdate }}')
    assert tmpl.render() == "Jan 15, 2025"


def test_shortsha_filter_via_env(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string('{{ sha | shortsha }}')
    assert tmpl.render(sha="abcdef1234567890") == "abcdef12"


# ---------------------------------------------------------------------------
# Macro rendering — issue_row
# ---------------------------------------------------------------------------


class _FakeIssue:
    issueId = "i-1"
    number = 42
    title = "Fix timing issue in drum track"
    state = "open"
    labels: list[str] = ["bug", "audio"]
    createdAt = "2025-01-15T10:00:00Z"
    author = "alice"


def test_issue_row_macro_renders_title(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/issue.html")
    macro = tmpl.module.issue_row  # type: ignore[attr-defined]
    html = macro(_FakeIssue(), base_url="/musehub/ui/alice/myrepo")
    assert "Fix timing issue in drum track" in html


def test_issue_row_macro_renders_issue_number(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/issue.html")
    macro = tmpl.module.issue_row  # type: ignore[attr-defined]
    html = macro(_FakeIssue(), base_url="/musehub/ui/alice/myrepo")
    assert "#42" in html


def test_issue_row_macro_renders_date(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/issue.html")
    macro = tmpl.module.issue_row  # type: ignore[attr-defined]
    html = macro(_FakeIssue(), base_url="/musehub/ui/alice/myrepo")
    assert "Jan 15, 2025" in html


# ---------------------------------------------------------------------------
# Macro rendering — pagination
# ---------------------------------------------------------------------------


def test_pagination_macro_renders_prev_next(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/pagination.html")
    macro = tmpl.module.pagination  # type: ignore[attr-defined]
    html = macro(page=2, total_pages=5, url="/musehub/ui/alice/myrepo/issues")
    assert "Prev" in html
    assert "Next" in html
    assert "Page 2 of 5" in html


def test_pagination_macro_hidden_on_single_page(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/pagination.html")
    macro = tmpl.module.pagination  # type: ignore[attr-defined]
    html = macro(page=1, total_pages=1, url="/musehub/ui/alice/myrepo/issues")
    assert html.strip() == ""


def test_pagination_macro_no_prev_on_first_page(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/pagination.html")
    macro = tmpl.module.pagination  # type: ignore[attr-defined]
    html = macro(page=1, total_pages=3, url="/musehub/ui/alice/myrepo/issues")
    assert "Prev" not in html
    assert "Next" in html


# ---------------------------------------------------------------------------
# Macro rendering — empty_state
# ---------------------------------------------------------------------------


def test_empty_state_macro_renders_action(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/empty_state.html")
    macro = tmpl.module.empty_state  # type: ignore[attr-defined]
    html = macro(
        "📭",
        "No issues yet",
        "Open an issue to start tracking work.",
        action_url="/new",
        action_label="Open an issue",
    )
    assert "No issues yet" in html
    assert "Open an issue" in html
    assert 'href="/new"' in html


def test_empty_state_macro_no_action_when_url_missing(jinja_env: Environment) -> None:
    tmpl = jinja_env.get_template("musehub/macros/empty_state.html")
    macro = tmpl.module.empty_state  # type: ignore[attr-defined]
    html = macro("📭", "No issues yet", "Nothing here.")
    assert "btn" not in html
