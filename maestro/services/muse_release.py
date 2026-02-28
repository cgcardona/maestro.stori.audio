"""Muse Release Service — export a tagged commit as distribution-ready artifacts.

This service is the music-native publish step: given a tag (applied via
``muse tag add``) it resolves the tagged commit, fetches its MIDI snapshot,
and renders it to audio/MIDI artifacts for distribution.

Boundary contract:
- Input:  tag string, snapshot manifest, output directory, release options.
- Output: ``ReleaseResult`` — paths written, manifest JSON path, format,
          commit short ID, and a ``stubbed`` flag when Storpheus audio render
          is not yet available.
- Side effects:  Writes files under ``output_dir``.  Never modifies the Muse
  repository or the database.

Output layout::

    <output_dir>/
        release-manifest.json        # always written; includes SHA-256 checksums
        audio/<commit8>.<format>     # --render-audio
        midi/<stem>.mid (zipped)     # --render-midi  → midi-bundle.zip
        stems/<stem>.<format>        # --export-stems (one file per track)

Storpheus render status:
  The Storpheus service exposes MIDI generation at ``POST /generate``.
  A dedicated ``POST /render`` endpoint (MIDI-in → audio-out) is planned but
  not yet deployed.  Until that endpoint ships this module performs a health
  check, then copies the first MIDI file to the audio output path as a stub.
  When ``/render`` is available, replace ``_render_midi_to_audio`` with a
  real POST call and set ``stubbed=False`` on the result.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ReleaseAudioFormat(str, Enum):
    """Audio output format for release artifacts."""

    WAV = "wav"
    MP3 = "mp3"
    FLAC = "flac"


@dataclass(frozen=True)
class ReleaseArtifact:
    """A single file produced during a release operation.

    Attributes:
        path: Absolute path of the written file.
        sha256: SHA-256 hex digest of the file contents.
        size_bytes: File size in bytes.
        role: Human-readable role label (e.g. ``"audio"``, ``"midi-bundle"``,
              ``"stem"``, ``"manifest"``).
    """

    path: pathlib.Path
    sha256: str
    size_bytes: int
    role: str


@dataclass
class ReleaseResult:
    """Result of a ``muse release`` operation.

    Attributes:
        tag: The release tag string (e.g. ``"v1.0"``).
        commit_id: Full commit ID of the released snapshot.
        output_dir: Root directory where all artifacts were written.
        manifest_path: Path to the ``release-manifest.json`` file.
        artifacts: All files produced (audio, MIDI bundle, stems, manifest).
        audio_format: Audio format used for rendered files.
        stubbed: True when the Storpheus ``/render`` endpoint is not yet
            available and MIDI was copied as an audio placeholder.
    """

    tag: str
    commit_id: str
    output_dir: pathlib.Path
    manifest_path: pathlib.Path
    artifacts: list[ReleaseArtifact] = field(default_factory=list)
    audio_format: ReleaseAudioFormat = ReleaseAudioFormat.WAV
    stubbed: bool = True


class StorpheusReleaseUnavailableError(Exception):
    """Raised when Storpheus is not reachable and audio rendering is requested.

    The CLI catches this and surfaces a clear human-readable message rather
    than an unhandled traceback.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_MIDI_SUFFIXES: frozenset[str] = frozenset({".mid", ".midi"})


def _collect_midi_paths(
    manifest: dict[str, str],
    root: pathlib.Path,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> tuple[list[pathlib.Path], int]:
    """Collect MIDI file paths from the snapshot manifest.

    Applies optional track/section substring filters.  Missing files are
    counted in the skipped total and logged at WARNING level.

    Args:
        manifest: ``{rel_path: object_id}`` snapshot manifest.
        root: Muse repository root; MIDI files live under ``<root>/muse-work/``.
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
            logger.warning("⚠️ release: MIDI source missing: %s", src)
            continue
        midi_paths.append(src)

    return midi_paths, skipped


def _sha256_file(path: pathlib.Path) -> str:
    """Compute the SHA-256 hex digest of *path*.

    Reads the file in 64 KiB chunks to avoid loading large audio files into
    memory at once.

    Args:
        path: File to hash.

    Returns:
        Lowercase hex digest string (64 chars).
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_artifact(path: pathlib.Path, role: str) -> ReleaseArtifact:
    """Build a ``ReleaseArtifact`` for a file that was just written.

    Args:
        path: Absolute path of the written file (must exist).
        role: Role label for the artifact.

    Returns:
        ReleaseArtifact with SHA-256 checksum and size.
    """
    return ReleaseArtifact(
        path=path,
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        role=role,
    )


def _check_storpheus_reachable(storpheus_url: str) -> None:
    """Probe Storpheus health endpoint; raise ``StorpheusReleaseUnavailableError`` if down.

    Uses a 3-second probe timeout so the CLI fails fast when Storpheus is not
    running rather than hanging for the full generation timeout.

    Args:
        storpheus_url: Base URL for the Storpheus service.

    Raises:
        StorpheusReleaseUnavailableError: When the service is unreachable.
    """
    probe_timeout = httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0)
    try:
        with httpx.Client(timeout=probe_timeout) as client:
            resp = client.get(f"{storpheus_url.rstrip('/')}/health")
            reachable = resp.status_code == 200
    except Exception as exc:
        raise StorpheusReleaseUnavailableError(
            f"Storpheus is not reachable at {storpheus_url}: {exc}\n"
            "Start Storpheus (docker compose up storpheus) and retry."
        ) from exc

    if not reachable:
        raise StorpheusReleaseUnavailableError(
            f"Storpheus health check returned non-200 at {storpheus_url}/health.\n"
            "Check Storpheus logs: docker compose logs storpheus"
        )


def _render_midi_to_audio(
    midi_path: pathlib.Path,
    output_path: pathlib.Path,
    fmt: ReleaseAudioFormat,
    storpheus_url: str,
) -> bool:
    """Render a MIDI file to audio via Storpheus ``POST /render``.

    This is a *stub implementation*: the Storpheus ``/render`` endpoint
    (MIDI-in → audio-out) is not yet deployed.  Until it ships the function
    copies the MIDI file to ``output_path`` as a placeholder and returns
    ``True`` (stubbed=True).

    When the endpoint is available, implement::

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
        "✅ release stub: %s copied to %s [format=%s]",
        midi_path.name,
        output_path,
        fmt.value,
    )
    return True


def _write_release_manifest(
    output_dir: pathlib.Path,
    tag: str,
    commit_id: str,
    audio_format: ReleaseAudioFormat,
    artifacts: list[ReleaseArtifact],
    stubbed: bool,
) -> pathlib.Path:
    """Write the ``release-manifest.json`` to *output_dir*.

    The manifest is the authoritative index of everything produced by
    ``muse release``.  It is always the last artifact written so that its
    presence signals a complete, consistent release directory.

    Manifest shape::

        {
          "tag": "v1.0",
          "commit_id": "<full sha>",
          "commit_short": "<8-char>",
          "released_at": "<ISO-8601 UTC>",
          "audio_format": "wav",
          "stubbed": false,
          "files": [
            {"path": "audio/abc123.wav", "sha256": "...", "size_bytes": ..., "role": "audio"},
            ...
          ]
        }

    Args:
        output_dir: Root release directory.
        tag: Release tag string.
        commit_id: Full commit ID.
        audio_format: Audio format used for rendered files.
        artifacts: All non-manifest artifacts already written.
        stubbed: Whether audio renders are stub copies.

    Returns:
        Path of the written manifest file.
    """
    manifest_path = output_dir / "release-manifest.json"
    files_list = [
        {
            "path": str(a.path.relative_to(output_dir)),
            "sha256": a.sha256,
            "size_bytes": a.size_bytes,
            "role": a.role,
        }
        for a in artifacts
    ]
    payload: dict[str, object] = {
        "tag": tag,
        "commit_id": commit_id,
        "commit_short": commit_id[:8],
        "released_at": datetime.now(timezone.utc).isoformat(),
        "audio_format": audio_format.value,
        "stubbed": stubbed,
        "files": files_list,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2))
    logger.info("✅ release: manifest written to %s", manifest_path)
    return manifest_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_release(
    *,
    tag: str,
    commit_id: str,
    manifest: dict[str, str],
    root: pathlib.Path,
    output_dir: pathlib.Path,
    audio_format: ReleaseAudioFormat = ReleaseAudioFormat.WAV,
    render_audio: bool = False,
    render_midi: bool = False,
    export_stems: bool = False,
    storpheus_url: str = "http://localhost:10002",
) -> ReleaseResult:
    """Build a release artifact bundle from a tagged Muse commit snapshot.

    Entry point for the ``muse release`` command.  Depending on the flags
    passed it:

    - Renders the primary MIDI file to audio via Storpheus (``render_audio``).
    - Bundles all MIDI files into a zip archive (``render_midi``).
    - Exports each MIDI file as a separate audio stem (``export_stems``).
    - Always writes a ``release-manifest.json`` with SHA-256 checksums.

    At least one of ``render_audio``, ``render_midi``, or ``export_stems``
    must be True; otherwise only the manifest is written (which is useful
    for dry-run validation but not an interesting release).

    Args:
        tag: Release tag string (e.g. ``"v1.0"``).
        commit_id: Full commit ID of the snapshot to release.
        manifest: ``{rel_path: object_id}`` snapshot manifest.
        root: Muse repository root.
        output_dir: Destination directory for all artifacts.
        audio_format: Audio format for rendered files (wav / mp3 / flac).
        render_audio: Render primary MIDI to a single audio file.
        render_midi: Bundle all MIDI files into a zip archive.
        export_stems: Export each MIDI track as a separate audio file.
        storpheus_url: Storpheus base URL (overridable in tests).

    Returns:
        ReleaseResult describing everything that was written.

    Raises:
        StorpheusReleaseUnavailableError: When audio render is requested and
            Storpheus health check fails.
        ValueError: When no MIDI files are found in the snapshot.
    """
    midi_paths, _skipped = _collect_midi_paths(manifest, root)

    if not midi_paths:
        raise ValueError(
            f"No MIDI files found in snapshot for commit {commit_id[:8]}. "
            "Check muse-work/ for MIDI content before releasing."
        )

    needs_storpheus = render_audio or export_stems
    if needs_storpheus:
        _check_storpheus_reachable(storpheus_url)

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[ReleaseArtifact] = []
    any_stubbed = False

    # --- Render audio: primary MIDI → single audio file ---
    if render_audio:
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        primary_midi = midi_paths[0]
        audio_out = audio_dir / f"{commit_id[:8]}.{audio_format.value}"
        stubbed = _render_midi_to_audio(primary_midi, audio_out, audio_format, storpheus_url)
        if stubbed:
            any_stubbed = True
        artifacts.append(_make_artifact(audio_out, "audio"))
        logger.info(
            "✅ release: audio rendered %s → %s [stubbed=%s]",
            primary_midi.name,
            audio_out,
            stubbed,
        )

    # --- Render MIDI bundle: all MIDI files → zip archive ---
    if render_midi:
        midi_dir = output_dir / "midi"
        midi_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = midi_dir / "midi-bundle.zip"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for midi_path in midi_paths:
                zf.write(midi_path, arcname=midi_path.name)
        artifacts.append(_make_artifact(bundle_path, "midi-bundle"))
        logger.info(
            "✅ release: MIDI bundle written to %s (%d files)", bundle_path, len(midi_paths)
        )

    # --- Export stems: each MIDI file → separate audio file ---
    if export_stems:
        stems_dir = output_dir / "stems"
        stems_dir.mkdir(parents=True, exist_ok=True)
        for midi_path in midi_paths:
            stem_out = stems_dir / f"{midi_path.stem}.{audio_format.value}"
            stubbed = _render_midi_to_audio(midi_path, stem_out, audio_format, storpheus_url)
            if stubbed:
                any_stubbed = True
            artifacts.append(_make_artifact(stem_out, "stem"))
            logger.info(
                "✅ release: stem exported %s → %s [stubbed=%s]",
                midi_path.name,
                stem_out,
                stubbed,
            )

    # Always write manifest last — its presence signals a complete release.
    manifest_path = _write_release_manifest(
        output_dir=output_dir,
        tag=tag,
        commit_id=commit_id,
        audio_format=audio_format,
        artifacts=artifacts,
        stubbed=any_stubbed,
    )
    manifest_artifact = _make_artifact(manifest_path, "manifest")
    artifacts.append(manifest_artifact)

    logger.info(
        "✅ muse release: tag=%r commit=%s output_dir=%s artifacts=%d stubbed=%s",
        tag,
        commit_id[:8],
        output_dir,
        len(artifacts),
        any_stubbed,
    )

    return ReleaseResult(
        tag=tag,
        commit_id=commit_id,
        output_dir=output_dir,
        manifest_path=manifest_path,
        artifacts=artifacts,
        audio_format=audio_format,
        stubbed=any_stubbed,
    )
