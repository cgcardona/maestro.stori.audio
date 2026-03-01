"""Jinja2 custom filters for the MuseHub UI template engine.

Replaces JavaScript utility functions (``escHtml``, ``fmtDate``, ``shortSha``)
that were formerly duplicated across dozens of page templates with server-side
filter functions registered on the shared Jinja2 Environment.

Usage in templates::

    {{ issue.created_at | fmtdate }}         {# "Jan 15, 2025" #}
    {{ commit.commit_id | shortsha }}         {# "a1b2c3d4" #}
    {{ label.color | label_text_color }}      {# "#000" or "#fff" #}
    {{ event.created_at | fmtrelative }}      {# "3 hours ago" #}

Call :func:`register_musehub_filters` once on the ``Jinja2Templates.env``
object at application startup — **before** any templates are rendered.
"""
from __future__ import annotations

from datetime import datetime, timezone

from jinja2 import Environment


def _fmtdate(value: datetime | str | None) -> str:
    """Format a datetime or ISO-8601 string as 'Jan 15, 2025'.

    Returns an empty string for ``None`` so templates can safely call the
    filter on optional fields without an ``{% if %}`` guard.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.strftime("%b %-d, %Y")


def _fmtrelative(value: datetime | str | None) -> str:
    """Format a datetime as a human-relative string ('3 hours ago', '2 days ago').

    Assumes UTC when ``value`` has no timezone info.  Returns an empty string
    for ``None``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


def _shortsha(value: str | None) -> str:
    """Return the first 8 characters of a commit SHA.

    Returns an empty string for ``None`` or empty input.
    """
    if not value:
        return ""
    return value[:8]


def _label_text_color(hex_bg: str) -> str:
    """Return '#000' or '#fff' for readable contrast against the given hex background.

    Uses the WCAG relative luminance formula so text remains legible across
    the full range of GitHub-style label colours.
    """
    hex_bg = hex_bg.lstrip("#")
    if len(hex_bg) != 6:
        return "#000"
    r, g, b = int(hex_bg[0:2], 16), int(hex_bg[2:4], 16), int(hex_bg[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000" if luminance > 0.5 else "#fff"


def register_musehub_filters(env: Environment) -> None:
    """Register all MuseHub custom Jinja2 filters on *env*.

    Call this exactly once at application startup on the ``Jinja2Templates.env``
    object.  Idempotent — re-registering an already-set filter overwrites the
    previous value, which is safe.

    Registered filters:

    * ``fmtdate``        — datetime → 'Jan 15, 2025'
    * ``fmtrelative``    — datetime → '3 hours ago'
    * ``shortsha``       — SHA string → first-8-chars
    * ``label_text_color`` — hex colour → '#000' or '#fff'
    """
    env.filters["fmtdate"] = _fmtdate
    env.filters["fmtrelative"] = _fmtrelative
    env.filters["shortsha"] = _shortsha
    env.filters["label_text_color"] = _label_text_color
