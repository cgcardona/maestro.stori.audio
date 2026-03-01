"""Muse Attributes — .museattributes parser and merge-strategy resolver.

``.museattributes`` is a per-repository configuration file (placed in the
repository root, next to ``.muse/``) that declares merge strategies for
specific track patterns and musical dimensions.

File format (one rule per line)::

    # comment
    <track-pattern>  <dimension>  <strategy>

Where:
- ``<track-pattern>`` is an fnmatch glob (e.g. ``drums/*``, ``bass/*``, ``*``).
- ``<dimension>`` is a musical dimension name or ``*`` (all dimensions).
- ``<strategy>`` is one of: ``ours``, ``theirs``, ``union``, ``auto``, ``manual``.

Resolution precedence: the *first* matching rule wins.

Example::

    # Drums are always authoritative — keep ours on conflict.
    drums/*  *        ours
    # Accept collaborator keys wholesale.
    keys/*   harmonic theirs
    # Everything else: automatic merge.
    *        *        auto
"""
from __future__ import annotations

import fnmatch
import logging
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

MUSEATTRIBUTES_FILENAME = ".museattributes"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class MergeStrategy(str, Enum):
    """Merge strategy choices for a musical dimension."""

    OURS = "ours"
    THEIRS = "theirs"
    UNION = "union"
    AUTO = "auto"
    MANUAL = "manual"


class MuseAttribute(BaseModel):
    """A single rule parsed from a ``.museattributes`` file."""

    track_pattern: str
    dimension: str
    strategy: MergeStrategy

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_museattributes_file(content: str) -> list[MuseAttribute]:
    """Parse the text content of a ``.museattributes`` file into a list of rules.

    Lines that are empty or start with ``#`` are ignored.  Each rule line must
    contain exactly three whitespace-separated tokens; malformed lines are
    logged as warnings and skipped.

    Args:
        content: Raw text content of the ``.museattributes`` file.

    Returns:
        Ordered list of ``MuseAttribute`` instances (first-match-wins).
    """
    attributes: list[MuseAttribute] = []

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = line.split()
        if len(tokens) != 3:
            logger.warning(
                "⚠️ .museattributes line %d: expected 3 tokens, got %d — skipping: %r",
                lineno,
                len(tokens),
                line,
            )
            continue

        track_pattern, dimension, strategy_raw = tokens

        try:
            strategy = MergeStrategy(strategy_raw.lower())
        except ValueError:
            valid = ", ".join(s.value for s in MergeStrategy)
            logger.warning(
                "⚠️ .museattributes line %d: unknown strategy %r (valid: %s) — skipping",
                lineno,
                strategy_raw,
                valid,
            )
            continue

        attributes.append(
            MuseAttribute(
                track_pattern=track_pattern,
                dimension=dimension,
                strategy=strategy,
            )
        )

    logger.debug("✅ Parsed %d rule(s) from .museattributes", len(attributes))
    return attributes


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_attributes(repo_path: Path) -> list[MuseAttribute]:
    """Load ``.museattributes`` from the repository root.

    Args:
        repo_path: Path to the Muse repository root (the directory that contains
            the ``.muse/`` folder).

    Returns:
        Parsed list of ``MuseAttribute`` rules.  Returns an empty list if the
        file does not exist; never raises.
    """
    attr_file = repo_path / MUSEATTRIBUTES_FILENAME
    if not attr_file.exists():
        logger.debug("ℹ️ No .museattributes found at %s", attr_file)
        return []

    try:
        content = attr_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("⚠️ Could not read .museattributes: %s", exc)
        return []

    return parse_museattributes_file(content)


# ---------------------------------------------------------------------------
# Strategy resolver
# ---------------------------------------------------------------------------


def resolve_strategy(
    attributes: list[MuseAttribute],
    track: str,
    dimension: str,
) -> MergeStrategy:
    """Return the configured ``MergeStrategy`` for a track + dimension pair.

    Iterates through ``attributes`` in order (first-match-wins).  The
    ``track_pattern`` is matched using ``fnmatch`` so patterns like
    ``drums/*``, ``*``, or ``bass/kick`` all work as expected.  The
    ``dimension`` is matched with fnmatch as well, allowing ``*`` to cover
    all dimensions.

    If no rule matches, returns ``MergeStrategy.AUTO`` (the safe default).

    Args:
        attributes: Ordered list of ``MuseAttribute`` rules (from
            ``load_attributes`` or ``parse_museattributes_file``).
        track: Concrete track name to resolve (e.g. ``"drums/kick"``).
        dimension: Musical dimension name (e.g. ``"harmonic"``, ``"rhythmic"``).

    Returns:
        The first matching ``MergeStrategy``, or ``MergeStrategy.AUTO`` when
        no rule matches.
    """
    for attr in attributes:
        track_matches = fnmatch.fnmatch(track, attr.track_pattern)
        dim_matches = fnmatch.fnmatch(dimension, attr.dimension)
        if track_matches and dim_matches:
            logger.debug(
                "✅ .museattributes: track=%r dim=%r matched pattern=%r/%r → %s",
                track,
                dimension,
                attr.track_pattern,
                attr.dimension,
                attr.strategy.value,
            )
            return attr.strategy

    logger.debug(
        "ℹ️ .museattributes: no rule matched track=%r dim=%r — defaulting to auto",
        track,
        dimension,
    )
    return MergeStrategy.AUTO
