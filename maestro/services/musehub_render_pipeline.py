"""MuseHub render pipeline — auto-generate MP3 and piano-roll images on commit push.

Orchestrates the asynchronous artifact generation triggered by every successful
push to MuseHub.  For each new commit the pipeline:

1. Checks for an existing render job (idempotency guard — re-pushing the same
   commit SHA skips a duplicate render).
2. Creates a ``musehub_render_jobs`` row with ``status=pending``.
3. Discovers MIDI objects in the push payload (``path`` ends with ``.mid``
   or ``.midi``).
4. For each MIDI object:
   a. Generates a piano-roll PNG image via ``musehub_piano_roll_renderer``.
   b. Generates an MP3 audio preview stub via the same logic as
      ``muse_render_preview`` (MIDI copy; replaced with a real Storpheus
      ``POST /render`` call when that endpoint ships).
5. Stores each generated artifact as a new ``musehub_objects`` row.
6. Updates the job status to ``complete`` or ``failed``.

The pipeline runs as a FastAPI ``BackgroundTask`` so it never blocks the push
HTTP response.  Failures are logged but not re-raised — a failed render is
recoverable; the push is not rolled back.

Boundary rules (same as musehub_sync):
  - Must NOT import state stores, SSE queues, or LLM clients.
  - Must NOT import maestro.core.* modules.
  - May import ORM models from maestro.db.musehub_models.
  - May import Pydantic models from maestro.models.musehub.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings
from maestro.db import musehub_models as db
from maestro.db.database import AsyncSessionLocal
from maestro.models.musehub import ObjectInput
from maestro.services.musehub_piano_roll_renderer import render_piano_roll

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderPipelineResult:
    """Summary of a single render pipeline execution for one commit.

    Attributes:
        commit_id: The Muse commit SHA that was rendered.
        status: Final job status: ``"complete"`` or ``"failed"``.
        midi_count: Number of MIDI objects discovered in the push payload.
        mp3_object_ids: Object IDs of generated MP3 (or stub) artifacts.
        image_object_ids: Object IDs of generated piano-roll PNG artifacts.
        error_message: Non-empty only when status is ``"failed"``.
    """

    commit_id: str
    status: str
    midi_count: int
    mp3_object_ids: list[str] = field(default_factory=list)
    image_object_ids: list[str] = field(default_factory=list)
    error_message: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _content_sha256(data: bytes) -> str:
    """Return a ``sha256:<hex>`` content-addressed ID for ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _render_dir(repo_id: str) -> Path:
    """Return the directory where rendered artifacts are written for a repo."""
    return Path(settings.musehub_objects_dir) / repo_id / "renders"


def _midi_filter(path: str) -> bool:
    """Return True when the object path looks like a MIDI file."""
    lower = path.lower()
    return lower.endswith(".mid") or lower.endswith(".midi")


async def _object_exists(session: AsyncSession, object_id: str) -> bool:
    """Return True when an object with this ID already exists in the DB."""
    stmt = select(db.MusehubObject.object_id).where(
        db.MusehubObject.object_id == object_id
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _store_object(
    session: AsyncSession,
    *,
    repo_id: str,
    object_id: str,
    path: str,
    disk_path: Path,
    data: bytes,
) -> None:
    """Write artifact bytes to disk and upsert the musehub_objects row.

    Idempotent: if the object already exists (same content-addressed ID) the
    row is skipped; the disk file is still overwritten to ensure consistency.
    """
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_bytes(data)

    if await _object_exists(session, object_id):
        logger.info("ℹ️ Object %s already exists — skipping DB insert", object_id)
        return

    row = db.MusehubObject(
        object_id=object_id,
        repo_id=repo_id,
        path=path,
        size_bytes=len(data),
        disk_path=str(disk_path),
    )
    session.add(row)


async def _render_job_exists(
    session: AsyncSession, *, repo_id: str, commit_id: str
) -> bool:
    """Return True when a render job row already exists for this commit."""
    stmt = select(db.MusehubRenderJob.render_job_id).where(
        db.MusehubRenderJob.repo_id == repo_id,
        db.MusehubRenderJob.commit_id == commit_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


def _make_stub_mp3(midi_bytes: bytes, output_path: Path) -> bytes:
    """Copy MIDI bytes to the output path as an MP3 stub.

    Storpheus ``POST /render`` (MIDI-in → audio-out) is not yet deployed.
    Until it ships, the MIDI file is copied verbatim as a placeholder.  The
    ``stubbed`` contract from ``muse_render_preview`` is mirrored here.

    Args:
        midi_bytes: Raw MIDI file bytes.
        output_path: Destination path for the stub file.

    Returns:
        The bytes written (identical to ``midi_bytes``).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(midi_bytes)
    logger.warning(
        "⚠️ Storpheus /render not yet available — writing MIDI stub at %s", output_path
    )
    return midi_bytes


# ---------------------------------------------------------------------------
# Core render logic
# ---------------------------------------------------------------------------


async def _render_commit(
    session: AsyncSession,
    *,
    repo_id: str,
    commit_id: str,
    objects: list[ObjectInput],
) -> RenderPipelineResult:
    """Perform the full render pipeline for a single commit.

    Discovers MIDI objects in ``objects``, renders piano-roll PNGs and MP3
    stubs, stores artifacts, and returns a ``RenderPipelineResult``.

    This function creates DB rows but does NOT commit the session — that is the
    caller's responsibility (keeping it composable with the job status update).
    """
    import base64

    midi_objects = [o for o in objects if _midi_filter(o.path)]
    render_dir = _render_dir(repo_id)
    mp3_ids: list[str] = []
    image_ids: list[str] = []

    for idx, obj in enumerate(midi_objects):
        try:
            midi_bytes = base64.b64decode(obj.content_b64)
        except Exception as exc:
            logger.warning(
                "⚠️ Could not decode base64 for object %s: %s", obj.object_id, exc
            )
            continue

        stem = Path(obj.path).stem

        # ── Piano-roll PNG ──────────────────────────────────────────────────
        pr_filename = f"{commit_id[:8]}_{stem}_piano_roll.png"
        pr_disk_path = render_dir / pr_filename

        try:
            pr_result = render_piano_roll(
                midi_bytes=midi_bytes,
                output_path=pr_disk_path,
                track_index=idx,
            )
            pr_bytes = pr_disk_path.read_bytes()
            pr_object_id = _content_sha256(pr_bytes)
            pr_path = f"renders/{pr_filename}"
            await _store_object(
                session,
                repo_id=repo_id,
                object_id=pr_object_id,
                path=pr_path,
                disk_path=pr_disk_path,
                data=pr_bytes,
            )
            image_ids.append(pr_object_id)
            logger.info(
                "✅ Piano-roll rendered: commit=%s track=%s notes=%d stubbed=%s",
                commit_id[:8],
                stem,
                pr_result.note_count,
                pr_result.stubbed,
            )
        except Exception as exc:
            logger.error(
                "❌ Piano-roll render failed for %s (commit=%s): %s",
                obj.path,
                commit_id[:8],
                exc,
            )

        # ── MP3 stub ────────────────────────────────────────────────────────
        mp3_filename = f"{commit_id[:8]}_{stem}.mp3"
        mp3_disk_path = render_dir / mp3_filename

        try:
            mp3_bytes = _make_stub_mp3(midi_bytes, mp3_disk_path)
            mp3_object_id = _content_sha256(mp3_bytes)
            mp3_path = f"renders/{mp3_filename}"
            await _store_object(
                session,
                repo_id=repo_id,
                object_id=mp3_object_id,
                path=mp3_path,
                disk_path=mp3_disk_path,
                data=mp3_bytes,
            )
            mp3_ids.append(mp3_object_id)
        except Exception as exc:
            logger.error(
                "❌ MP3 stub generation failed for %s (commit=%s): %s",
                obj.path,
                commit_id[:8],
                exc,
            )

    return RenderPipelineResult(
        commit_id=commit_id,
        status="complete",
        midi_count=len(midi_objects),
        mp3_object_ids=mp3_ids,
        image_object_ids=image_ids,
    )


# ---------------------------------------------------------------------------
# Public background-task entry point
# ---------------------------------------------------------------------------


async def trigger_render_background(
    *,
    repo_id: str,
    commit_id: str,
    objects: list[ObjectInput],
) -> None:
    """Background task: render artifacts for a pushed commit and persist them.

    Designed for use with FastAPI ``BackgroundTasks``::

        background_tasks.add_task(
            trigger_render_background,
            repo_id=repo_id,
            commit_id=head_commit_id,
            objects=body.objects,
        )

    Idempotency: if a render job already exists for ``(repo_id, commit_id)``
    the function returns immediately without creating a duplicate.

    Failures are logged but never raised — a failed render does not block the
    push or affect subsequent requests.

    Args:
        repo_id: UUID of the MuseHub repository.
        commit_id: SHA of the pushed commit being rendered.
        objects: Object payloads from the push request (may include non-MIDI).
    """
    try:
        async with AsyncSessionLocal() as session:
            # Idempotency gate
            if await _render_job_exists(session, repo_id=repo_id, commit_id=commit_id):
                logger.info(
                    "ℹ️ Render job already exists for commit=%s — skipping",
                    commit_id[:8],
                )
                return

            # Create pending job row
            midi_objects = [o for o in objects if _midi_filter(o.path)]
            job = db.MusehubRenderJob(
                repo_id=repo_id,
                commit_id=commit_id,
                status="rendering",
                midi_count=len(midi_objects),
            )
            session.add(job)
            await session.flush()  # Obtain PK before proceeding

            logger.info(
                "✅ Render job created: commit=%s midi_files=%d",
                commit_id[:8],
                len(midi_objects),
            )

            try:
                result = await _render_commit(
                    session,
                    repo_id=repo_id,
                    commit_id=commit_id,
                    objects=objects,
                )
                job.status = result.status
                job.mp3_object_ids = result.mp3_object_ids
                job.image_object_ids = result.image_object_ids
            except Exception as exc:
                job.status = "failed"
                job.error_message = str(exc)
                logger.error(
                    "❌ Render pipeline failed for commit=%s: %s",
                    commit_id[:8],
                    exc,
                )

            await session.commit()
            logger.info(
                "✅ Render complete: commit=%s status=%s mp3=%d images=%d",
                commit_id[:8],
                job.status,
                len(job.mp3_object_ids or []),
                len(job.image_object_ids or []),
            )

    except Exception as exc:
        logger.error(
            "❌ Render background task failed for commit=%s: %s",
            commit_id[:8],
            exc,
        )
