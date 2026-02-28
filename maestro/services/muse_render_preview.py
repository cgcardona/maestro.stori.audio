"""Muse Render-Preview Service — MIDI → audio preview via Storpheus.

Converts a Muse commit snapshot (a manifest of MIDI files) into a rendered
audio file by delegating to the Storpheus render endpoint.  This is the
commit-aware counterpart to ``muse play``: instead of playing whatever file
the user points to, it first resolves a commit, extracts its MIDI files, and
dispatches them for audio rendering.

Boundary contract:
- Input:  ``dict[str, str]`` snapshot manifest (rel_path → object_id)
- Output: ``RenderPreviewResult`` — path written, format, commit short ID, and
          a ``stubbed`` flag that signals whether a full Storpheus render
          actually occurred or whether the MIDI was copied as a placeholder.
- Side effects:  Writes exactly one file to the caller-supplied ``output_path``.
  Never touches the Muse repository or the database.

Storpheus render status:
  The Storpheus service exposes MIDI *generation* at ``POST /generate``.
  A dedicated ``POST /render`` endpoint (MIDI-in → audio-out) is planned but
  not yet deployed.  Until that endpoint ships this module performs a
  health-check to confirm Storpheus is reachable, then writes the first MIDI
  file from the manifest to the output path (format: wav stub).

  When ``/render`` is available, replace ``_render_via_storpheus`` with a
  real POST call and set ``stubbed=False`` on the result.
"""
from __future__ import annotations

import logging
import pathlib
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PreviewFormat(str, Enum):
    """Audio format for the rendered preview file."""

    WAV = "wav"
    MP3 = "mp3"
    FLAC = "flac"


@dataclass(frozen=True)
class RenderPreviewResult:
    """Result of a single render-preview operation.

    Attributes:
        output_path: Absolute path of the file written to disk.
        format: Audio format that was rendered.
        commit_id: Full commit ID whose snapshot was rendered.
        midi_files_used: Number of MIDI files from the snapshot used for rendering.
        skipped_count: Manifest entries skipped (wrong type / missing on disk).
        stubbed: True when the Storpheus ``/render`` endpoint is not yet
            available and a MIDI file was copied in its place.  Consumers
            should surface this to the user so they understand the file is
            not a full audio render.
    """

    output_path: pathlib.Path
    format: PreviewFormat
    commit_id: str
    midi_files_used: int
    skipped_count: int
    stubbed: bool = True


class StorpheusRenderUnavailableError(Exception):
    """Raised when Storpheus is not reachable and a render is requested.

    The CLI catches this and surfaces a clear human-readable message rather
    than an unhandled traceback.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MIDI_SUFFIXES: frozenset[str] = frozenset({".mid", ".midi"})


def _collect_midi_files(
    manifest: dict[str, str],
    root: pathlib.Path,
    track: Optional[str],
    section: Optional[str],
) -> tuple[list[pathlib.Path], int]:
    """Walk the snapshot manifest and return (midi_paths, skipped_count).

    Applies optional ``track`` and ``section`` substring filters before
    collecting.  Files that pass the filter but are absent on disk are
    counted in ``skipped_count`` and logged at WARNING level.

    Args:
        manifest: ``{rel_path: object_id}`` snapshot manifest.
        root: Muse repository root — MIDI files live under ``<root>/muse-work/``.
        track: Optional case-insensitive track name substring filter.
        section: Optional case-insensitive section name substring filter.

    Returns:
        Tuple of (list[pathlib.Path], skipped_count).
    """
    workdir = root / "muse-work"
    midi_paths: list[pathlib.Path] = []
    skipped = 0

    for rel_path in sorted(manifest.keys()):
        path_lower = rel_path.lower()
        if track is not None and track.lower() not in path_lower:
            skipped += 1
            continue
        if section is not None and section.lower() not in path_lower:
            skipped += 1
            continue
        if pathlib.PurePosixPath(rel_path).suffix.lower() not in _MIDI_SUFFIXES:
            skipped += 1
            continue
        src = workdir / rel_path
        if not src.exists():
            skipped += 1
            logger.warning("⚠️ render-preview: MIDI source missing: %s", src)
            continue
        midi_paths.append(src)

    return midi_paths, skipped


def _check_storpheus_reachable(storpheus_url: str) -> None:
    """Probe Storpheus health endpoint; raise StorpheusRenderUnavailableError if down.

    Uses a short (3 s) probe timeout so the CLI fails quickly when Storpheus
    is not running rather than hanging for the full generation timeout.

    Args:
        storpheus_url: Base URL for the Storpheus service (e.g. ``http://storpheus:10002``).

    Raises:
        StorpheusRenderUnavailableError: If the service is unreachable or returns non-200.
    """
    probe_timeout = httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0)
    try:
        with httpx.Client(timeout=probe_timeout) as client:
            resp = client.get(f"{storpheus_url.rstrip('/')}/health")
            reachable = resp.status_code == 200
    except Exception as exc:
        raise StorpheusRenderUnavailableError(
            f"Storpheus is not reachable at {storpheus_url}: {exc}\n"
            "Start Storpheus (docker compose up storpheus) and retry."
        ) from exc

    if not reachable:
        raise StorpheusRenderUnavailableError(
            f"Storpheus health check returned non-200 at {storpheus_url}/health.\n"
            "Check Storpheus logs: docker compose logs storpheus"
        )


def _render_via_storpheus(
    midi_path: pathlib.Path,
    output_path: pathlib.Path,
    fmt: PreviewFormat,
    storpheus_url: str,
) -> bool:
    """Attempt to render *midi_path* to audio via Storpheus ``POST /render``.

    This is a *stub implementation*: the Storpheus ``/render`` endpoint
    (MIDI-in → audio-out) is not yet deployed.  Until it ships the function
    copies the MIDI file to ``output_path`` as a placeholder and returns
    ``True`` (stubbed=True).

    When the endpoint is available, implement:
        POST {storpheus_url}/render
        Content-Type: multipart/form-data
        Body: {midi: <bytes>, format: <fmt.value>}
        → writes response body to output_path, returns False (stubbed=False).

    Args:
        midi_path: Source MIDI file path.
        output_path: Destination audio file path.
        fmt: Target audio format.
        storpheus_url: Storpheus base URL.

    Returns:
        True when the output is a MIDI stub (no real audio render occurred).
    """
    logger.warning(
        "⚠️ Storpheus /render endpoint not yet available — "
        "copying MIDI as placeholder for %s",
        output_path.name,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(midi_path, output_path)
    logger.info(
        "✅ render-preview stub: %s copied to %s [format=%s]",
        midi_path.name,
        output_path,
        fmt.value,
    )
    return True  # stubbed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_preview(
    manifest: dict[str, str],
    root: pathlib.Path,
    commit_id: str,
    output_path: pathlib.Path,
    fmt: PreviewFormat = PreviewFormat.WAV,
    track: Optional[str] = None,
    section: Optional[str] = None,
    storpheus_url: str = "http://localhost:10002",
) -> RenderPreviewResult:
    """Render a commit snapshot to an audio preview file.

    Entry point for the ``muse render-preview`` command.  Collects MIDI
    files from the snapshot, checks that Storpheus is reachable, then
    delegates to ``_render_via_storpheus`` for the actual audio conversion.

    When the Storpheus ``/render`` endpoint is not yet available, the first
    matching MIDI file is copied to ``output_path`` as a placeholder and
    ``RenderPreviewResult.stubbed`` is set to ``True``.

    Args:
        manifest: ``{rel_path: object_id}`` snapshot manifest from the DB.
        root: Muse repository root.
        commit_id: Full commit ID being previewed (used for result metadata).
        output_path: Destination file path for the rendered audio.
        fmt: Audio format for the rendered preview (wav / mp3 / flac).
        track: Optional track name filter (case-insensitive substring).
        section: Optional section name filter (case-insensitive substring).
        storpheus_url: Storpheus base URL (overridable in tests).

    Returns:
        RenderPreviewResult describing what was written.

    Raises:
        StorpheusRenderUnavailableError: When Storpheus health check fails.
        ValueError: When no MIDI files are found in the (filtered) snapshot.
    """
    midi_paths, skipped = _collect_midi_files(manifest, root, track, section)

    if not midi_paths:
        raise ValueError(
            f"No MIDI files found in snapshot for commit {commit_id[:8]}. "
            "Use --track / --section to widen the filter, or check muse-work/."
        )

    _check_storpheus_reachable(storpheus_url)

    # Render the first MIDI file.  When /render supports multi-track mixing,
    # pass all midi_paths and merge the output here.
    primary_midi = midi_paths[0]
    stubbed = _render_via_storpheus(primary_midi, output_path, fmt, storpheus_url)

    logger.info(
        "✅ muse render-preview: commit=%s format=%s output=%s midi_files=%d stubbed=%s",
        commit_id[:8],
        fmt.value,
        output_path,
        len(midi_paths),
        stubbed,
    )

    return RenderPreviewResult(
        output_path=output_path,
        format=fmt,
        commit_id=commit_id,
        midi_files_used=len(midi_paths),
        skipped_count=skipped,
        stubbed=stubbed,
    )
