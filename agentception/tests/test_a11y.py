"""Accessibility (a11y) smoke tests for AgentCeption templates.

Verifies that structural a11y requirements are present in the rendered HTML:
- base.html has the skip-link and main-content anchor
- modal templates include keydown.escape handlers

Run targeted:
    pytest agentception/tests/test_a11y.py -v
"""
from __future__ import annotations

import pathlib

import pytest


_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"


def _read(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# base.html structural requirements
# ---------------------------------------------------------------------------


def test_base_has_skip_link_class() -> None:
    """base.html must contain a skip-link element for keyboard users."""
    content = _read("base.html")
    assert 'class="skip-link"' in content, (
        'base.html is missing <a class="skip-link"> skip-to-content anchor'
    )


def test_base_has_main_content_id() -> None:
    """base.html <main> must carry id='main-content' so the skip link target exists."""
    content = _read("base.html")
    assert 'id="main-content"' in content, (
        'base.html <main> is missing id="main-content"'
    )


def test_base_skip_link_points_to_main_content() -> None:
    """The skip link href must match the main-content anchor."""
    content = _read("base.html")
    assert 'href="#main-content"' in content, (
        'Skip link in base.html must use href="#main-content"'
    )


# ---------------------------------------------------------------------------
# Modal Escape-key handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "overview.html",
        "agent.html",
        "roles.html",
    ],
)
def test_modal_template_has_keydown_escape(template: str) -> None:
    """Every template that contains a modal must handle the Escape key."""
    content = _read(template)
    assert "keydown.escape" in content, (
        f"{template} has a modal but is missing @keydown.escape handler"
    )


# ---------------------------------------------------------------------------
# Modal click-outside (backdrop click) handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "overview.html",
        "agent.html",
        "roles.html",
    ],
)
def test_modal_template_has_click_self(template: str) -> None:
    """Modal backdrops must close on click outside (click.self)."""
    content = _read(template)
    assert "click.self" in content, (
        f"{template} has a modal backdrop but is missing @click.self handler"
    )


# ---------------------------------------------------------------------------
# ARIA on modal backdrops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "overview.html",
        "agent.html",
        "roles.html",
    ],
)
def test_modal_template_has_aria_modal(template: str) -> None:
    """Modal elements must carry role='dialog' and aria-modal='true'."""
    content = _read(template)
    assert 'role="dialog"' in content, (
        f"{template} modal is missing role='dialog'"
    )
    assert 'aria-modal="true"' in content, (
        f"{template} modal is missing aria-modal='true'"
    )


# ---------------------------------------------------------------------------
# Spawn page — keyboard accessibility on interactive div elements
# ---------------------------------------------------------------------------


def test_spawn_issue_cards_have_tabindex() -> None:
    """Issue cards in spawn.html must be keyboard-reachable via tabindex."""
    content = _read("spawn.html")
    assert "tabindex" in content, (
        "spawn.html issue cards or role options are missing tabindex for keyboard navigation"
    )


def test_spawn_issue_cards_have_keydown_handler() -> None:
    """Issue cards must respond to Enter/Space for keyboard activation."""
    content = _read("spawn.html")
    assert "keydown.enter" in content, (
        "spawn.html is missing keydown.enter handler on interactive div elements"
    )


# ---------------------------------------------------------------------------
# Config page — tab widget ARIA completeness
# ---------------------------------------------------------------------------


def test_config_tab_buttons_have_ids() -> None:
    """Config sidebar tab buttons must have id attributes for aria-labelledby pairing."""
    content = _read("config.html")
    assert 'id="tab-btn-allocation"' in content
    assert 'id="tab-btn-labels"' in content
    assert 'id="tab-btn-ab"' in content
    assert 'id="tab-btn-projects"' in content


def test_config_panels_have_aria_labelledby() -> None:
    """Config tabpanels must reference their controlling button via aria-labelledby."""
    content = _read("config.html")
    assert 'aria-labelledby="tab-btn-allocation"' in content
    assert 'aria-labelledby="tab-btn-labels"' in content
    assert 'aria-labelledby="tab-btn-ab"' in content
    assert 'aria-labelledby="tab-btn-projects"' in content
