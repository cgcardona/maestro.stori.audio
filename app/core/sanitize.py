"""
Input sanitisation for user-supplied content.

Runs before prompt construction on every path (stream, MCP, variation).
Strips characters that have no legitimate use in music prompts but could
carry hidden injection payloads.

What we strip:
  - C0/C1 control characters except TAB (U+0009), LF (U+000A), CR (U+000D)
  - Zero-width and invisible formatting characters (ZWSP, ZWJ, BOM, etc.)
  - Runs of 3+ consecutive blank lines collapsed to 2

What we preserve (all are legitimate in STORI PROMPTs or natural language):
  - Em dash —, en dash –, arrows →, bullet •
  - Smart quotes " " ' '
  - All printable Unicode above U+009F that is not in the invisible set
  - Block-scalar whitespace required by YAML (indented lines, trailing newlines)

What we do NOT do:
  - Keyword-based injection filtering — trivially bypassed and corrupts YAML
  - HTML/URL encoding — not relevant for LLM prompts
  - Truncation — max_length is enforced at the Pydantic layer before this runs
"""

import re
import unicodedata

# C0/C1 control chars except TAB (09), LF (0A), CR (0D)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")

# Zero-width / invisible Unicode formatting characters
# ZWSP (200B), ZWNJ (200C), ZWJ (200D), LRM (200E), RLM (200F),
# bidi overrides (202A-202E), function chars (2060-2064),
# deprecated formatting (206A-206F), BOM (FEFF), word joiners (2060, FFFE)
_INVISIBLE_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff]"
)

# 3+ consecutive blank lines → 2
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalise_user_input(raw: str) -> str:
    """
    Normalise user-supplied text for safe inclusion in LLM prompts.

    Safe for both free-form natural language and STORI PROMPT YAML:
    YAML indentation, block scalars, and special music characters are
    preserved; only genuinely invisible or control characters are stripped.
    """
    # NFC — canonical composition so é (e + combining) == é (precomposed)
    text = unicodedata.normalize("NFC", raw)

    # Strip C0/C1 control chars (null bytes, BEL, ESC, etc.)
    text = _CONTROL_RE.sub("", text)

    # Strip zero-width and invisible formatting characters
    text = _INVISIBLE_RE.sub("", text)

    # Normalise line endings to LF only
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Collapse excessive blank lines (padding attacks)
    text = _BLANK_LINES_RE.sub("\n\n", text)

    # Strip trailing whitespace per line (prevents tokenisation anomalies)
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text.strip()
