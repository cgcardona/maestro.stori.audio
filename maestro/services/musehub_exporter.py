"""Muse Hub export service — format conversion and ZIP packaging.

Resolves a repo ref (commit ID or branch name) and packages stored artifacts
into a downloadable payload. The export is deterministic for a given
repo + ref + format + options combination.

Format support:
  midi      — returns .mid artifacts directly or ZIPed for split_tracks
  json      — serialises commit metadata + object index as JSON
  musicxml  — returns .xml/.musicxml/.mxl artifacts (pass-through)
  abc       — returns .abc artifacts (pass-through)
  wav       — returns .wav artifacts (pass-through)
  mp3       — returns .mp3 artifacts (pass-through)

For split_tracks=True or when multiple artifacts match the requested format,
all files are bundled into a ZIP archive. Single-artifact exports are returned
as the raw file with the appropriate MIME type.

Boundary rules:
  - Must NOT import from maestro.core.* (no intent/pipeline logic here).
  - Must NOT call external services (no Storpheus, no OpenRouter).
  - Reads from DB via musehub_repository; reads file bytes directly from disk.
"""
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services import musehub_repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ExportFormat(str, Enum):
    """Supported export formats for a Muse Hub repo snapshot.

    midi      — Audio MIDI (.mid); the native Muse format.
    json      — Commit metadata + object index; machine-readable for agents.
    musicxml  — MusicXML (.xml/.musicxml/.mxl); notation-app interchange.
    abc       — ABC notation (.abc); plain-text music representation.
    wav       — PCM audio (.wav); lossless render.
    mp3       — Compressed audio (.mp3); delivery format.
    """

    midi = "midi"
    json = "json"
    musicxml = "musicxml"
    abc = "abc"
    wav = "wav"
    mp3 = "mp3"


@dataclass(frozen=True)
class ExportResult:
    """Fully packaged export artifact ready for streaming to the client.

    content      — Raw bytes of the file or ZIP archive.
    content_type — MIME type for the HTTP response Content-Type header.
    filename     — Suggested filename for Content-Disposition: attachment.
    """

    content: bytes
    content_type: str
    filename: str


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Maps ExportFormat → file extensions whose objects are candidates for that format
_FORMAT_EXTENSIONS: dict[ExportFormat, tuple[str, ...]] = {
    ExportFormat.midi: (".mid", ".midi"),
    ExportFormat.musicxml: (".xml", ".musicxml", ".mxl"),
    ExportFormat.abc: (".abc",),
    ExportFormat.wav: (".wav",),
    ExportFormat.mp3: (".mp3",),
    # json is handled separately — no on-disk extension match needed
    ExportFormat.json: (),
}

_FORMAT_MIME: dict[ExportFormat, str] = {
    ExportFormat.midi: "audio/midi",
    ExportFormat.json: "application/json",
    ExportFormat.musicxml: "application/vnd.recordare.musicxml+xml",
    ExportFormat.abc: "text/plain; charset=utf-8",
    ExportFormat.wav: "audio/wav",
    ExportFormat.mp3: "audio/mpeg",
}


# ---------------------------------------------------------------------------
# Ref resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_ref(
    session: AsyncSession,
    repo_id: str,
    ref: str,
) -> str | None:
    """Resolve a ref string to a commit_id.

    Tries in order:
    1. Direct commit_id lookup.
    2. Branch head lookup (ref treated as branch name).

    Returns None if the ref cannot be resolved to any known commit.
    """
    commit = await musehub_repository.get_commit(session, repo_id, ref)
    if commit is not None:
        return commit.commit_id

    branches = await musehub_repository.list_branches(session, repo_id)
    for branch in branches:
        if branch.name == ref and branch.head_commit_id:
            return branch.head_commit_id

    return None


# ---------------------------------------------------------------------------
# Section filtering
# ---------------------------------------------------------------------------


def _matches_sections(path: str, sections: list[str] | None) -> bool:
    """Return True if the artifact path should be included given the section filter.

    When ``sections`` is None or empty, all paths pass. Otherwise, the path
    must contain at least one section name as a path component or substring.
    Section names are compared case-insensitively.
    """
    if not sections:
        return True
    path_lower = path.lower()
    return any(s.lower() in path_lower for s in sections)


# ---------------------------------------------------------------------------
# Format builders
# ---------------------------------------------------------------------------


def _build_zip(
    artifacts: list[tuple[str, bytes]],
) -> bytes:
    """Bundle a list of (filename, content) pairs into an in-memory ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in artifacts:
            zf.writestr(name, data)
    return buf.getvalue()


def _build_json_export(
    repo_id: str,
    ref: str,
    commit_id: str,
    objects: list[dict[str, object]],
) -> bytes:
    """Serialise a commit's metadata and object index to compact JSON bytes.

    The schema is:
        {
          "repo_id": str,
          "ref": str,
          "commit_id": str,
          "objects": [{"object_id": str, "path": str, "size_bytes": int}]
        }
    """
    payload: dict[str, object] = {
        "repo_id": repo_id,
        "ref": ref,
        "commit_id": commit_id,
        "objects": objects,
    }
    return json.dumps(payload, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def export_repo_at_ref(
    session: AsyncSession,
    repo_id: str,
    ref: str,
    format: ExportFormat,  # noqa: A002 — shadows built-in intentionally for clarity
    split_tracks: bool = False,
    sections: list[str] | None = None,
) -> ExportResult | Literal["ref_not_found", "no_matching_objects"]:
    """Package stored artifacts for download at the given commit ref.

    Args:
        session:       Active async DB session.
        repo_id:       Target Muse Hub repository ID.
        ref:           Commit ID or branch name to export from.
        format:        Output format (ExportFormat enum).
        split_tracks:  When True, always bundle into a ZIP even for a single artifact.
        sections:      Optional section name filter; only artifacts whose path
                       contains a listed section name are included.

    Returns:
        ExportResult on success.
        ``"ref_not_found"`` if ref does not resolve to any known commit.
        ``"no_matching_objects"`` if the format filter yields no candidates.

    The ref is validated against known commits and branches. The actual object
    content is read from disk (``disk_path``). Objects missing from disk are
    skipped with a warning rather than causing an error — partial exports are
    preferable to total failures when only some files are missing.
    """
    commit_id = await _resolve_ref(session, repo_id, ref)
    if commit_id is None:
        logger.warning("⚠️ Export ref %r not found in repo %s", ref, repo_id)
        return "ref_not_found"

    repo_objects = await musehub_repository.list_objects(session, repo_id)

    if format is ExportFormat.json:
        filtered = [o for o in repo_objects if _matches_sections(o.path, sections)]
        obj_list: list[dict[str, object]] = [
            {"object_id": o.object_id, "path": o.path, "size_bytes": o.size_bytes}
            for o in filtered
        ]
        content = _build_json_export(repo_id, ref, commit_id, obj_list)
        filename = f"{repo_id}_{ref[:8]}.json"
        return ExportResult(
            content=content,
            content_type=_FORMAT_MIME[ExportFormat.json],
            filename=filename,
        )

    valid_exts = _FORMAT_EXTENSIONS[format]
    candidates = [
        o
        for o in repo_objects
        if os.path.splitext(o.path)[1].lower() in valid_exts
        and _matches_sections(o.path, sections)
    ]

    if not candidates:
        logger.warning(
            "⚠️ No %s artifacts found for repo %s at ref %s", format.value, repo_id, ref
        )
        return "no_matching_objects"

    artifacts: list[tuple[str, bytes]] = []
    for obj in candidates:
        raw_obj = await musehub_repository.get_object_row(session, repo_id, obj.object_id)
        if raw_obj is None or not os.path.exists(raw_obj.disk_path):
            logger.warning(
                "⚠️ Object %s missing from disk (path=%s) — skipping",
                obj.object_id,
                getattr(raw_obj, "disk_path", "unknown"),
            )
            continue
        with open(raw_obj.disk_path, "rb") as fh:
            data = fh.read()
        artifacts.append((os.path.basename(obj.path), data))

    if not artifacts:
        return "no_matching_objects"

    short_ref = ref[:8]
    if len(artifacts) == 1 and not split_tracks:
        filename_single, file_bytes = artifacts[0]
        return ExportResult(
            content=file_bytes,
            content_type=_FORMAT_MIME[format],
            filename=filename_single,
        )

    zip_bytes = _build_zip(artifacts)
    zip_name = f"{repo_id}_{short_ref}_{format.value}.zip"
    logger.info(
        "✅ Export ready: %d artifacts → %s (%d bytes)", len(artifacts), zip_name, len(zip_bytes)
    )
    return ExportResult(
        content=zip_bytes,
        content_type="application/zip",
        filename=zip_name,
    )
