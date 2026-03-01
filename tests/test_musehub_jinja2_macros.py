"""Tests for MuseHub Jinja2 custom filters and component macros.

Verifies that each filter function produces correct output and that
macros render the expected HTML structure when loaded through a real
Jinja2 Environment.
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

_TEMPLATE_DIR = Path(__file__).parent.parent / "maestro" / "templates"


@pytest.fixture()
def jinja_env() -> Environment:
    """Return a Jinja2 Environment with all MuseHub filters registered."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,
    )
    register_musehub_filters(env)
    return env


# ---------------------------------------------------------------------------
# _fmtdate
# ---------------------------------------------------------------------------


def test_fmtdate_formats_datetime() -> None:
    result = _fmtdate(datetime(2025, 1, 15, 10, 30, 0))
    assert result == "Jan 15, 2025"


def test_fmtdate_formats_iso_string() -> None:
    result = _fmtdate("2025-01-15T10:00:00Z")
    assert result == "Jan 15, 2025"


def test_fmtdate_none_returns_empty() -> None:
    assert _fmtdate(None) == ""


def test_fmtdate_formats_single_digit_day() -> None:
    result = _fmtdate(datetime(2025, 3, 5, 0, 0, 0))
    assert result == "Mar 5, 2025"


# ---------------------------------------------------------------------------
# _fmtrelative
# ---------------------------------------------------------------------------


def test_fmtrelative_seconds() -> None:
    value = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert _fmtrelative(value) == "just now"


def test_fmtrelative_minutes() -> None:
    value = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _fmtrelative(value) == "5 minutes ago"


def test_fmtrelative_singular_minute() -> None:
    value = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert _fmtrelative(value) == "1 minute ago"


def test_fmtrelative_hours() -> None:
    value = datetime.now(timezone.utc) - timedelta(hours=2)
    assert _fmtrelative(value) == "2 hours ago"


def test_fmtrelative_singular_hour() -> None:
    value = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _fmtrelative(value) == "1 hour ago"


def test_fmtrelative_days() -> None:
    value = datetime.now(timezone.utc) - timedelta(days=3)
    assert _fmtrelative(value) == "3 days ago"


def test_fmtrelative_none_returns_empty() -> None:
    assert _fmtrelative(None) == ""


def test_fmtrelative_iso_string() -> None:
    value = datetime.now(timezone.utc) - timedelta(hours=1, seconds=5)
    iso = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = _fmtrelative(iso)
    assert result == "1 hour ago"


# ---------------------------------------------------------------------------
# _shortsha
# ---------------------------------------------------------------------------


def test_shortsha_returns_8_chars() -> None:
    assert _shortsha("a1b2c3d4e5f6g7h8") == "a1b2c3d4"


def test_shortsha_none_returns_empty() -> None:
    assert _shortsha(None) == ""


def test_shortsha_empty_returns_empty() -> None:
    assert _shortsha("") == ""


def test_shortsha_exact_8_chars() -> None:
    assert _shortsha("a1b2c3d4") == "a1b2c3d4"


# ---------------------------------------------------------------------------
# _label_text_color
# ---------------------------------------------------------------------------


def test_label_text_color_dark_bg() -> None:
    assert _label_text_color("#000000") == "#fff"


def test_label_text_color_light_bg() -> None:
    assert _label_text_color("#ffffff") == "#000"


def test_label_text_color_mid_green() -> None:
    # #3fb950 luminance ≈ 0.535 (> 0.5 threshold) → dark text gives better contrast
    assert _label_text_color("#3fb950") == "#000"


def test_label_text_color_yellow() -> None:
    # Bright yellow #FFFF00: luminance > 0.5 → black text
    assert _label_text_color("#FFFF00") == "#000"


def test_label_text_color_invalid_hex() -> None:
    # Non-6-char hex falls back to black
    assert _label_text_color("#abc") == "#000"


def test_label_text_color_strips_hash() -> None:
    # Should work with or without leading #
    result_with = _label_text_color("#000000")
    assert result_with == "#fff"


# ---------------------------------------------------------------------------
# Environment registration
# ---------------------------------------------------------------------------


def test_jinja2_env_has_fmtdate_filter(jinja_env: Environment) -> None:
    assert "fmtdate" in jinja_env.filters


def test_jinja2_env_has_fmtrelative_filter(jinja_env: Environment) -> None:
    assert "fmtrelative" in jinja_env.filters


def test_jinja2_env_has_shortsha_filter(jinja_env: Environment) -> None:
    assert "shortsha" in jinja_env.filters


def test_jinja2_env_has_label_text_color_filter(jinja_env: Environment) -> None:
    assert "label_text_color" in jinja_env.filters


# ---------------------------------------------------------------------------
# Macro rendering — issue_row
# ---------------------------------------------------------------------------


def test_issue_row_macro_renders_title(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/issue.html" import issue_row %}'
        "{{ issue_row(issue, base_url='/musehub/ui/owner/repo') }}"
    )
    issue = {
        "issueId": "123",
        "number": 42,
        "title": "Fix the groove",
        "state": "open",
        "labels": [],
        "createdAt": "2025-01-15T10:00:00Z",
        "author": "alice",
    }
    html = tmpl.render(issue=issue)
    assert "Fix the groove" in html
    assert "#42" in html


def test_issue_row_macro_renders_label(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/issue.html" import issue_row %}'
        "{{ issue_row(issue, base_url='/musehub/ui/o/r', labels=labels) }}"
    )
    issue = {
        "issueId": "5",
        "number": 5,
        "title": "Bug",
        "state": "open",
        "labels": ["bug"],
        "createdAt": "2025-01-01T00:00:00Z",
        "author": None,
    }
    labels = [{"name": "bug", "color": "#d73a4a"}]
    html = tmpl.render(issue=issue, labels=labels)
    assert "bug" in html


# ---------------------------------------------------------------------------
# Macro rendering — pagination
# ---------------------------------------------------------------------------


def test_pagination_macro_renders_prev_next(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/pagination.html" import pagination %}'
        "{{ pagination(page=2, total_pages=5, url='/issues') }}"
    )
    html = tmpl.render()
    assert "← Prev" in html
    assert "Next →" in html
    assert "Page 2 of 5" in html


def test_pagination_macro_hidden_on_single_page(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/pagination.html" import pagination %}'
        "{{ pagination(page=1, total_pages=1, url='/issues') }}"
    )
    html = tmpl.render()
    assert "pagination" not in html


def test_pagination_macro_no_prev_on_first_page(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/pagination.html" import pagination %}'
        "{{ pagination(page=1, total_pages=3, url='/issues') }}"
    )
    html = tmpl.render()
    assert "← Prev" not in html
    assert "Next →" in html


def test_pagination_macro_no_next_on_last_page(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/pagination.html" import pagination %}'
        "{{ pagination(page=3, total_pages=3, url='/issues') }}"
    )
    html = tmpl.render()
    assert "Next →" not in html
    assert "← Prev" in html


# ---------------------------------------------------------------------------
# Macro rendering — empty_state
# ---------------------------------------------------------------------------


def test_empty_state_macro_renders_action(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/empty_state.html" import empty_state %}'
        "{{ empty_state(icon='🎵', title='No commits', desc='Push one to start.', "
        "action_url='/new', action_label='Create') }}"
    )
    html = tmpl.render()
    assert "No commits" in html
    assert "Push one to start." in html
    assert 'href="/new"' in html
    assert "Create" in html


def test_empty_state_macro_no_action_when_omitted(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/empty_state.html" import empty_state %}'
        "{{ empty_state(icon='🎵', title='Nothing here', desc='Come back later.') }}"
    )
    html = tmpl.render()
    assert "Nothing here" in html
    assert "<a " not in html


# ---------------------------------------------------------------------------
# Macro rendering — commit_row
# ---------------------------------------------------------------------------


def test_commit_row_macro_renders_sha(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/commit.html" import commit_row %}'
        "{{ commit_row(commit, base_url='/musehub/ui/o/r') }}"
    )
    commit = {
        "commitId": "a1b2c3d4e5f6",
        "message": "Initial commit",
        "author": "bob",
        "timestamp": "2025-01-01T00:00:00Z",
    }
    html = tmpl.render(commit=commit)
    assert "a1b2c3d4" in html
    assert "Initial commit" in html


# ---------------------------------------------------------------------------
# Macro rendering — label_chip
# ---------------------------------------------------------------------------


def test_label_chip_macro_renders_name(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/label.html" import label_chip %}'
        "{{ label_chip(name='enhancement', color='#a2eeef') }}"
    )
    html = tmpl.render()
    assert "enhancement" in html
    assert "#a2eeef" in html


# ---------------------------------------------------------------------------
# Macro rendering — milestone_progress
# ---------------------------------------------------------------------------


def test_milestone_progress_macro_renders_percentage(jinja_env: Environment) -> None:
    tmpl = jinja_env.from_string(
        '{% from "musehub/macros/milestone.html" import milestone_progress %}'
        "{{ milestone_progress(milestone) }}"
    )
    milestone = {
        "milestoneId": "m1",
        "title": "v1.0",
        "openIssues": 2,
        "closedIssues": 8,
    }
    html = tmpl.render(milestone=milestone)
    assert "v1.0" in html
    assert "80%" in html
    assert "8 closed" in html
    assert "2 open" in html
