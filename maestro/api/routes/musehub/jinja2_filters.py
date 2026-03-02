"""Jinja2 server-side filter functions for MuseHub templates.

Registers filters on a Jinja2 Environment so every MuseHub page template
can call them without duplicating JavaScript utility functions:

    {{ issue.created_at | fmtdate }}        → "Jan 15, 2025"
    {{ commit.timestamp | fmtrelative }}    → "3 hours ago"
    {{ commit.commit_id | shortsha }}       → "a1b2c3d4"
    {{ label.color | label_text_color }}    → "#000" or "#fff"
    {{ issue.body | markdown }}             → safe HTML (bold, italic, code, links)

Call ``register_musehub_filters(templates.env)`` once, immediately after the
``Jinja2Templates`` instance is created, to make these filters available in
every template rendered by that instance.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from jinja2 import Environment


def _fmtdate(value: datetime | str | None) -> str:
    """Format a datetime or ISO-8601 string as 'Jan 15, 2025'.

    Returns an empty string for None so templates can write
    ``{{ x | fmtdate }}`` without an explicit null check.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.strftime("%b %-d, %Y")


def _fmtrelative(value: datetime | str | None) -> str:
    """Format a datetime as a human-relative string: '3 hours ago'.

    Computes the delta from UTC now.  Returns an empty string for None.
    Timezone-naive datetimes are assumed to be UTC.
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

    Returns an empty string for None or empty input so templates never
    render ``None`` in place of a commit hash.
    """
    if not value:
        return ""
    return value[:8]


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int | None) -> str:
    """Convert a MIDI pitch value (0–127) to a note name string, e.g. 60 → 'C4'.

    Returns '—' for None so templates never render 'None' in place of a note name.
    """
    if midi is None:
        return "—"
    octave = midi // 12 - 1
    name = _NOTE_NAMES[midi % 12]
    return f"{name}{octave}"


def _markdown(value: str | None) -> str:
    """Render a Markdown string to safe HTML using only the standard library.

    Supports bold (``**text**``), italic (``*text*``), inline code (`` `code` ``),
    fenced code blocks, links (``[text](url)``), and converts newlines to ``<br>``.
    All input is HTML-escaped before pattern substitution so the output is
    XSS-safe without requiring an external sanitiser.

    Returns an empty string for None so templates can write
    ``{{ x | markdown }}`` without an explicit null check.
    """
    if not value:
        return ""

    # Split on fenced code blocks first so we don't modify code content.
    parts: list[tuple[str, str]] = []
    code_pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    last = 0
    for m in code_pattern.finditer(value):
        parts.append(("text", value[last : m.start()]))
        parts.append(("code", m.group(1)))
        last = m.end()
    parts.append(("text", value[last:]))

    result_parts: list[str] = []
    for kind, chunk in parts:
        if kind == "code":
            result_parts.append(f"<pre><code>{html.escape(chunk)}</code></pre>")
        else:
            escaped = html.escape(chunk)
            # Bold before italic so **text** takes priority over *text*
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
            escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
            escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
            # Links: [text](url) — only allow http/https schemes
            def _link(m: re.Match[str]) -> str:
                link_text = m.group(1)
                url = m.group(2)
                if re.match(r"^https?://", url):
                    return f'<a href="{html.escape(url)}" rel="noopener noreferrer">{link_text}</a>'
                return html.escape(m.group(0))

            escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, escaped)
            escaped = escaped.replace("\n", "<br>")
            result_parts.append(escaped)

    return "".join(result_parts)


def _label_text_color(hex_bg: str) -> str:
    """Return '#000' or '#fff' for readable contrast against a hex background.

    Uses WCAG relative luminance (W3C simplified formula) to decide whether
    dark or light text produces better contrast.  Falls back to '#000' for
    malformed input.
    """
    hex_bg = hex_bg.lstrip("#")
    if len(hex_bg) != 6:
        return "#000"
    r, g, b = int(hex_bg[0:2], 16), int(hex_bg[2:4], 16), int(hex_bg[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000" if luminance > 0.5 else "#fff"


def register_musehub_filters(env: Environment) -> None:
    """Register all MuseHub custom Jinja2 filters on *env*.

    Call this once after constructing a ``Jinja2Templates`` instance:

        templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
        register_musehub_filters(templates.env)

    Every template rendered by that instance can then use ``fmtdate``,
    ``fmtrelative``, ``shortsha``, and ``label_text_color`` as filters.
    """
    env.filters["fmtdate"] = _fmtdate
    env.filters["fmtrelative"] = _fmtrelative
    env.filters["shortsha"] = _shortsha
    env.filters["label_text_color"] = _label_text_color
    env.filters["note_name"] = _note_name
    env.filters["markdown"] = _markdown
