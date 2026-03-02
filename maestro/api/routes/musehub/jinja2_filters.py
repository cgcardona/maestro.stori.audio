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


def _filesizeformat(value: int | float | None) -> str:
    """Format a byte count as a human-readable file size string.

    Examples: 0 → "0 B", 1536 → "1.5 KB", 2097152 → "2.0 MB".
    Returns "0 B" for None or zero.
    """
    if not value or value <= 0:
        return "0 B"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"  # unreachable but satisfies mypy


def _markdown(value: str | None) -> str:
    """Convert a Markdown string to safe HTML for rendering in templates.

    Handles: headings (h1–h3 → h2–h4 to preserve page hierarchy), bold,
    italic, inline code, fenced code blocks, bullet/ordered lists,
    blockquotes, horizontal rules, and safe links (https:// and / only).

    Returns an empty string for None.  All user content is HTML-escaped
    before re-inserting markup — no XSS vectors.
    """
    if not value:
        return ""

    import html as _html

    def esc(s: str) -> str:
        return _html.escape(s)

    def inline_markup(s: str) -> str:
        import re

        s = re.sub(
            r"\[([^\]]+)\]\(((?:https?://|/)[^)]*)\)",
            lambda m: f'<a href="{esc(m.group(2))}" rel="noopener">{esc(m.group(1))}</a>',
            s,
        )
        s = re.sub(r"`([^`]+)`", lambda m: f"<code>{esc(m.group(1))}</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{esc(m.group(1))}</strong>", s)
        s = re.sub(r"\*([^*]+)\*", lambda m: f"<em>{esc(m.group(1))}</em>", s)
        return s

    import re

    lines = value.split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_list: str | None = None
    in_bq = False

    def flush_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def flush_bq() -> None:
        nonlocal in_bq
        if in_bq:
            out.append("</blockquote>")
            in_bq = False

    for line in lines:
        if line.startswith("```"):
            if not in_code:
                flush_list()
                flush_bq()
                in_code = True
                code_lang = esc(line[3:].strip())
                code_lines = []
            else:
                lang_attr = f' data-lang="{code_lang}"' if code_lang else ""
                out.append(
                    f'<pre class="code-block"{lang_attr}><code>{"".join(esc(l) + chr(10) for l in code_lines)}</code></pre>'
                )
                in_code = False
                code_lang = ""
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue

        if line.startswith("> "):
            flush_list()
            if not in_bq:
                out.append('<blockquote class="md-blockquote">')
                in_bq = True
            out.append(f"<p>{inline_markup(esc(line[2:]))}</p>")
            continue
        flush_bq()

        if re.match(r"^(\*\*\*|---|___)\s*$", line):
            flush_list()
            out.append("<hr>")
            continue

        h_match = re.match(r"^(#{1,3})\s+(.+)", line)
        if h_match:
            flush_list()
            level = len(h_match.group(1)) + 1  # h1 → h2, h2 → h3, h3 → h4
            out.append(f"<h{level} class='md-h{level}'>{inline_markup(esc(h_match.group(2)))}</h{level}>")
            continue

        ul_match = re.match(r"^[-*+]\s+(.+)", line)
        if ul_match:
            if in_list != "ul":
                if in_list:
                    out.append(f"</{in_list}>")
                out.append('<ul class="md-list">')
                in_list = "ul"
            out.append(f"<li>{inline_markup(esc(ul_match.group(1)))}</li>")
            continue

        ol_match = re.match(r"^\d+\.\s+(.+)", line)
        if ol_match:
            if in_list != "ol":
                if in_list:
                    out.append(f"</{in_list}>")
                out.append('<ol class="md-list">')
                in_list = "ol"
            out.append(f"<li>{inline_markup(esc(ol_match.group(1)))}</li>")
            continue

        flush_list()

        if not line.strip():
            out.append("<br>")
            continue

        out.append(f"<p class='md-p'>{inline_markup(esc(line))}</p>")

    if in_code and code_lines:
        out.append(f'<pre class="code-block"><code>{"".join(esc(l) + chr(10) for l in code_lines)}</code></pre>')
    flush_list()
    flush_bq()

    return "\n".join(out)


def register_musehub_filters(env: Environment) -> None:
    """Register all MuseHub custom Jinja2 filters on *env*.

    Call this once after constructing a ``Jinja2Templates`` instance:

        templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
        register_musehub_filters(templates.env)

    Every template rendered by that instance can then use ``fmtdate``,
    ``fmtrelative``, ``shortsha``, ``label_text_color``, ``filesizeformat``,
    and ``markdown`` as filters.
    """
    env.filters["fmtdate"] = _fmtdate
    env.filters["fmtrelative"] = _fmtrelative
    env.filters["shortsha"] = _shortsha
    env.filters["label_text_color"] = _label_text_color
    env.filters["note_name"] = _note_name
    env.filters["filesizeformat"] = _filesizeformat
    env.filters["markdown"] = _markdown
