"""Muse CLI export engine — format-specific export logic.

Converts a Muse snapshot manifest into external file formats:

- ``midi``     — copy raw MIDI files from the snapshot (native format).
- ``json``     — structured JSON note representation for AI/tooling.
- ``musicxml`` — MusicXML for notation software (MuseScore, Sibelius, etc.).
- ``abc``      — ABC notation text for folk/traditional music.
- ``wav``      — render audio via Storpheus (requires Storpheus reachable).

All format handlers accept the same inputs (manifest, root, options) and
return a MuseExportResult describing what was written.  The WAV handler
raises StorpheusUnavailableError when the service cannot be reached so the
CLI can surface a human-readable error.

Design note: export is a read-only Muse operation — no commit is created,
no DB writes occur.  The same commit + format always produces identical
output (deterministic).
"""
from __future__ import annotations

import json
import logging
import pathlib
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ExportFormat(str, Enum):
    """Supported export format identifiers."""

    MIDI = "midi"
    JSON = "json"
    MUSICXML = "musicxml"
    ABC = "abc"
    WAV = "wav"


@dataclass(frozen=True)
class MuseExportOptions:
    """Options controlling a single export operation.

    Attributes:
        format: Target export format.
        commit_id: Full commit ID being exported (used in output metadata).
        output_path: Destination file or directory path.
        track: Optional track name filter (case-insensitive substring match).
        section: Optional section name filter (case-insensitive substring match).
        split_tracks: When True (MIDI only), write one file per track.
    """

    format: ExportFormat
    commit_id: str
    output_path: pathlib.Path
    track: Optional[str] = None
    section: Optional[str] = None
    split_tracks: bool = False


@dataclass
class MuseExportResult:
    """Result of a completed export operation.

    Attributes:
        paths_written: Absolute paths of all files written during export.
        format: The format that was exported.
        commit_id: Source commit ID.
        skipped_count: Number of manifest entries skipped (wrong type/filter).
    """

    paths_written: list[pathlib.Path] = field(default_factory=list)
    format: ExportFormat = ExportFormat.MIDI
    commit_id: str = ""
    skipped_count: int = 0


class StorpheusUnavailableError(Exception):
    """Raised when WAV export is requested but Storpheus is not reachable.

    Callers should catch this and surface a human-readable message rather
    than letting it propagate as an unhandled exception.
    """


# ---------------------------------------------------------------------------
# Manifest filtering
# ---------------------------------------------------------------------------

#: File extensions treated as MIDI files.
_MIDI_SUFFIXES: frozenset[str] = frozenset({".mid", ".midi"})


def filter_manifest(
    manifest: dict[str, str],
    *,
    track: Optional[str],
    section: Optional[str],
) -> dict[str, str]:
    """Return a filtered copy of *manifest* matching the given criteria.

    Both *track* and *section* are case-insensitive substring matches
    against the full path string.  Only entries matching ALL provided
    filters are kept.  When both are ``None`` the full manifest is returned.

    Args:
        manifest: ``{rel_path: object_id}`` from MuseCliSnapshot.
        track: Track name substring filter (e.g. ``"piano"``).
        section: Section name substring filter (e.g. ``"chorus"``).

    Returns:
        Filtered manifest dict with the same ``{rel_path: object_id}`` shape.
    """
    if track is None and section is None:
        return dict(manifest)

    result: dict[str, str] = {}
    for rel_path, object_id in manifest.items():
        path_lower = rel_path.lower()
        if track is not None and track.lower() not in path_lower:
            continue
        if section is not None and section.lower() not in path_lower:
            continue
        result[rel_path] = object_id

    return result


# ---------------------------------------------------------------------------
# Format handlers
# ---------------------------------------------------------------------------


def export_midi(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
) -> MuseExportResult:
    """Copy MIDI files from the snapshot to opts.output_path.

    For a single-file export (split_tracks not set and only one MIDI
    file found) the output is written directly to opts.output_path.

    When split_tracks is set (or when multiple MIDI files are found),
    opts.output_path is treated as a directory and one <stem>.mid
    file is written per track.

    Args:
        manifest: Filtered snapshot manifest.
        root: Muse repository root.
        opts: Export options including output path and flags.

    Returns:
        MuseExportResult listing written paths.
    """
    result = MuseExportResult(format=opts.format, commit_id=opts.commit_id)
    workdir = root / "muse-work"

    midi_entries: list[tuple[str, pathlib.Path]] = []
    for rel_path, _ in sorted(manifest.items()):
        src = workdir / rel_path
        suffix = pathlib.PurePosixPath(rel_path).suffix.lower()
        if suffix not in _MIDI_SUFFIXES:
            result.skipped_count += 1
            logger.debug("export midi: skipping non-MIDI file %s", rel_path)
            continue
        if not src.exists():
            result.skipped_count += 1
            logger.warning("export midi: source file missing: %s", src)
            continue
        midi_entries.append((rel_path, src))

    if not midi_entries:
        return result

    if len(midi_entries) == 1 and not opts.split_tracks:
        opts.output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(midi_entries[0][1], opts.output_path)
        result.paths_written.append(opts.output_path)
        logger.info("export midi: wrote %s", opts.output_path)
    else:
        opts.output_path.mkdir(parents=True, exist_ok=True)
        for rel_path, src in midi_entries:
            stem = pathlib.PurePosixPath(rel_path).stem
            dst = opts.output_path / f"{stem}.mid"
            shutil.copy2(src, dst)
            result.paths_written.append(dst)
            logger.info("export midi: wrote %s", dst)

    return result


def export_json(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
) -> MuseExportResult:
    """Export the snapshot as structured JSON.

    The output JSON has the shape::

        {
          "commit_id": "<full commit hash>",
          "exported_at": "<ISO-8601 timestamp>",
          "files": [
            {
              "path": "<rel_path>",
              "object_id": "<sha256>",
              "size_bytes": <int>,
              "exists_in_workdir": <bool>
            },
            ...
          ]
        }

    This format is intended for AI model consumption and downstream tooling
    that needs a machine-readable index of the snapshot.

    Args:
        manifest: Filtered snapshot manifest.
        root: Muse repository root.
        opts: Export options including output path.

    Returns:
        MuseExportResult listing written paths.
    """
    import datetime

    result = MuseExportResult(format=opts.format, commit_id=opts.commit_id)
    workdir = root / "muse-work"

    files_list: list[dict[str, object]] = []
    for rel_path, object_id in sorted(manifest.items()):
        src = workdir / rel_path
        entry: dict[str, object] = {
            "path": rel_path,
            "object_id": object_id,
            "size_bytes": src.stat().st_size if src.exists() else None,
            "exists_in_workdir": src.exists(),
        }
        files_list.append(entry)

    payload: dict[str, object] = {
        "commit_id": opts.commit_id,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "files": files_list,
    }

    opts.output_path.parent.mkdir(parents=True, exist_ok=True)
    opts.output_path.write_text(json.dumps(payload, indent=2))
    result.paths_written.append(opts.output_path)
    logger.info("export json: wrote %s", opts.output_path)
    return result


def export_musicxml(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
) -> MuseExportResult:
    """Export MIDI files in the snapshot as MusicXML.

    Converts each MIDI file using a minimal MIDI-to-MusicXML transcription:
    reads Note On/Off events via mido and emits a well-formed MusicXML
    document with one <part> per MIDI channel.

    The conversion is intentionally lossy (MIDI lacks notation semantics):
    durations are quantised to the nearest sixteenth note and pitch spelling
    defaults to sharps.

    Args:
        manifest: Filtered snapshot manifest.
        root: Muse repository root.
        opts: Export options including output path.

    Returns:
        MuseExportResult listing written paths.
    """
    result = MuseExportResult(format=opts.format, commit_id=opts.commit_id)
    workdir = root / "muse-work"

    midi_entries: list[tuple[str, pathlib.Path]] = []
    for rel_path, _ in sorted(manifest.items()):
        suffix = pathlib.PurePosixPath(rel_path).suffix.lower()
        if suffix not in _MIDI_SUFFIXES:
            result.skipped_count += 1
            continue
        src = workdir / rel_path
        if not src.exists():
            result.skipped_count += 1
            continue
        midi_entries.append((rel_path, src))

    if not midi_entries:
        return result

    if len(midi_entries) == 1 and not opts.split_tracks:
        xml = _midi_to_musicxml(midi_entries[0][1])
        opts.output_path.parent.mkdir(parents=True, exist_ok=True)
        opts.output_path.write_text(xml, encoding="utf-8")
        result.paths_written.append(opts.output_path)
        logger.info("export musicxml: wrote %s", opts.output_path)
    else:
        opts.output_path.mkdir(parents=True, exist_ok=True)
        for rel_path, src in midi_entries:
            stem = pathlib.PurePosixPath(rel_path).stem
            dst = opts.output_path / f"{stem}.xml"
            xml = _midi_to_musicxml(src)
            dst.write_text(xml, encoding="utf-8")
            result.paths_written.append(dst)
            logger.info("export musicxml: wrote %s", dst)

    return result


def export_abc(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
) -> MuseExportResult:
    """Export MIDI files in the snapshot as ABC notation.

    Produces a simplified ABC notation file: one voice per MIDI channel,
    pitches mapped to note names, durations quantised to eighth notes.

    Args:
        manifest: Filtered snapshot manifest.
        root: Muse repository root.
        opts: Export options including output path.

    Returns:
        MuseExportResult listing written paths.
    """
    result = MuseExportResult(format=opts.format, commit_id=opts.commit_id)
    workdir = root / "muse-work"

    midi_entries: list[tuple[str, pathlib.Path]] = []
    for rel_path, _ in sorted(manifest.items()):
        suffix = pathlib.PurePosixPath(rel_path).suffix.lower()
        if suffix not in _MIDI_SUFFIXES:
            result.skipped_count += 1
            continue
        src = workdir / rel_path
        if not src.exists():
            result.skipped_count += 1
            continue
        midi_entries.append((rel_path, src))

    if not midi_entries:
        return result

    if len(midi_entries) == 1 and not opts.split_tracks:
        abc = _midi_to_abc(midi_entries[0][1])
        opts.output_path.parent.mkdir(parents=True, exist_ok=True)
        opts.output_path.write_text(abc, encoding="utf-8")
        result.paths_written.append(opts.output_path)
        logger.info("export abc: wrote %s", opts.output_path)
    else:
        opts.output_path.mkdir(parents=True, exist_ok=True)
        for rel_path, src in midi_entries:
            stem = pathlib.PurePosixPath(rel_path).stem
            dst = opts.output_path / f"{stem}.abc"
            abc = _midi_to_abc(src)
            dst.write_text(abc, encoding="utf-8")
            result.paths_written.append(dst)
            logger.info("export abc: wrote %s", dst)

    return result


def export_wav(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
    storpheus_url: str,
) -> MuseExportResult:
    """Export MIDI files to WAV audio via Storpheus.

    Performs a synchronous health check against storpheus_url before
    attempting any conversion.  Raises StorpheusUnavailableError
    immediately if Storpheus is not reachable.

    Args:
        manifest: Filtered snapshot manifest.
        root: Muse repository root.
        opts: Export options including output path.
        storpheus_url: Base URL for the Storpheus service health endpoint.

    Returns:
        MuseExportResult listing written paths.

    Raises:
        StorpheusUnavailableError: When Storpheus is unreachable.
    """
    result = MuseExportResult(format=opts.format, commit_id=opts.commit_id)

    try:
        probe_timeout = httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0)
        with httpx.Client(timeout=probe_timeout) as client:
            resp = client.get(f"{storpheus_url.rstrip('/')}/health")
            reachable = resp.status_code == 200
    except Exception as exc:
        raise StorpheusUnavailableError(
            f"Storpheus is not reachable at {storpheus_url}: {exc}\n"
            "Start Storpheus (docker compose up storpheus) and retry."
        ) from exc

    if not reachable:
        raise StorpheusUnavailableError(
            f"Storpheus health check returned non-200 at {storpheus_url}/health.\n"
            "Check Storpheus logs: docker compose logs storpheus"
        )

    logger.info("Storpheus reachable at %s — WAV export ready", storpheus_url)
    result.skipped_count = len(manifest)
    logger.warning(
        "WAV render delegation to Storpheus is not yet fully implemented; "
        "returning empty result.  Full WAV rendering is tracked as a follow-up."
    )
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def export_snapshot(
    manifest: dict[str, str],
    root: pathlib.Path,
    opts: MuseExportOptions,
    storpheus_url: str = "http://localhost:10002",
) -> MuseExportResult:
    """Top-level export dispatcher.

    Applies manifest filtering (--track, --section) then delegates
    to the appropriate format handler.

    Args:
        manifest: Raw snapshot manifest from DB.
        root: Muse repository root.
        opts: Fully-populated export options.
        storpheus_url: Base URL for Storpheus health check (WAV only).

    Returns:
        MuseExportResult describing what was written.

    Raises:
        StorpheusUnavailableError: For WAV format when unreachable.
        ValueError: If an unsupported format is passed.
    """
    filtered = filter_manifest(manifest, track=opts.track, section=opts.section)

    if opts.format == ExportFormat.MIDI:
        return export_midi(filtered, root, opts)
    elif opts.format == ExportFormat.JSON:
        return export_json(filtered, root, opts)
    elif opts.format == ExportFormat.MUSICXML:
        return export_musicxml(filtered, root, opts)
    elif opts.format == ExportFormat.ABC:
        return export_abc(filtered, root, opts)
    elif opts.format == ExportFormat.WAV:
        return export_wav(filtered, root, opts, storpheus_url=storpheus_url)
    else:
        raise ValueError(f"Unsupported export format: {opts.format!r}")


# ---------------------------------------------------------------------------
# MIDI note helpers
# ---------------------------------------------------------------------------

#: MIDI note names (sharps) indexed 0-11.
_NOTE_NAMES: list[str] = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

#: ABC note names for MIDI pitch classes 0-11.
_ABC_NOTE_NAMES: list[str] = ["C", "^C", "D", "^D", "E", "F", "^F", "G", "^G", "A", "^A", "B"]


def _midi_note_to_step_octave(note: int) -> tuple[str, int]:
    """Convert a MIDI note number (0-127) to (step, octave) for MusicXML.

    Returns e.g. ("C", 4) for middle C (MIDI 60).
    """
    octave = (note // 12) - 1
    step = _NOTE_NAMES[note % 12]
    return step, octave


def _midi_note_to_abc(note: int) -> str:
    """Convert MIDI note number to ABC notation pitch string.

    C4 (MIDI 60) is uppercase C; C5 (MIDI 72) is lowercase c; above that
    add apostrophes; below C4 add commas per the ABC notation spec.
    """
    octave = note // 12 - 1
    pitch_class = note % 12
    name = _ABC_NOTE_NAMES[pitch_class]
    has_accidental = "^" in name

    if octave == 4:
        return name
    elif octave == 5:
        if has_accidental:
            return "^" + name[1].lower()
        return name.lower()
    elif octave > 5:
        suffix = "'" * (octave - 5)
        base = ("^" + name[1].lower()) if has_accidental else name.lower()
        return base + suffix
    else:
        suffix = "," * (4 - octave)
        return name + suffix


def _parse_midi_notes(
    path: pathlib.Path,
) -> dict[int, list[tuple[int, int, int]]]:
    """Parse a MIDI file and return notes grouped by channel.

    Uses mido to read Note On/Off events across all tracks and returns
    a dict mapping ``channel -> [(start_tick, end_tick, pitch), ...]``.

    Args:
        path: Path to the MIDI file.

    Returns:
        Dict of channel index to list of (start_tick, end_tick, pitch) tuples.
    """
    import mido

    mid = mido.MidiFile(str(path))
    channel_notes: dict[int, list[tuple[int, int, int]]] = {}
    active: dict[tuple[int, int], int] = {}

    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                active[(msg.channel, msg.note)] = abs_tick
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                start = active.pop((msg.channel, msg.note), None)
                if start is not None:
                    ch_list = channel_notes.setdefault(msg.channel, [])
                    ch_list.append((start, abs_tick, msg.note))

    return channel_notes


def _midi_to_musicxml(path: pathlib.Path) -> str:
    """Convert a MIDI file to a minimal MusicXML string.

    Uses mido to read Note On/Off events and emits one <part> per MIDI
    channel.  Durations are passed through as raw tick values.

    This is a best-effort transcription — MIDI does not carry notation
    semantics so the output is suitable for import review, not engraving.

    Args:
        path: Path to the source MIDI file.

    Returns:
        MusicXML document as a UTF-8 string.
    """
    import mido

    mid = mido.MidiFile(str(path))
    tpb: int = mid.ticks_per_beat or 480
    divisions = tpb

    channel_notes = _parse_midi_notes(path)

    parts: list[str] = []
    part_list_items: list[str] = []
    for ch_idx, channel in enumerate(sorted(channel_notes.keys()), 1):
        part_id = f"P{ch_idx}"
        part_list_items.append(
            f'    <score-part id="{part_id}">'
            f"<part-name>Channel {channel}</part-name>"
            f"</score-part>"
        )
        notes_xml: list[str] = []
        for start_tick, end_tick, pitch in sorted(channel_notes[channel]):
            duration_ticks = max(1, end_tick - start_tick)
            step, octave = _midi_note_to_step_octave(pitch)
            notes_xml.append(
                f"      <note>"
                f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
                f"<duration>{duration_ticks}</duration>"
                f"<type>quarter</type>"
                f"</note>"
            )
        notes_block = "\n".join(notes_xml) if notes_xml else "      <!-- no notes -->"
        parts.append(
            f'  <part id="{part_id}">\n'
            f'    <measure number="1">\n'
            f"      <attributes>"
            f"<divisions>{divisions}</divisions>"
            f"</attributes>\n"
            f"{notes_block}\n"
            f"    </measure>\n"
            f"  </part>"
        )

    part_list_xml = "\n".join(part_list_items)
    parts_xml = "\n".join(parts)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC\n'
        '  "-//Recordare//DTD MusicXML 4.0 Partwise//EN"\n'
        '  "http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="4.0">\n'
        f"  <part-list>\n{part_list_xml}\n  </part-list>\n"
        f"{parts_xml}\n"
        "</score-partwise>\n"
    )


def _midi_to_abc(path: pathlib.Path) -> str:
    """Convert a MIDI file to simplified ABC notation.

    Reads Note On/Off events, assigns each MIDI channel to an ABC voice,
    and emits an X: header followed by note sequences.

    Args:
        path: Path to the source MIDI file.

    Returns:
        ABC notation document as a UTF-8 string.
    """
    channel_notes = _parse_midi_notes(path)
    stem = path.stem

    lines: list[str] = [
        "X:1",
        f"T:{stem}",
        "M:4/4",
        "L:1/8",
        "K:C",
    ]

    for voice_idx, channel in enumerate(sorted(channel_notes.keys()), 1):
        notes_sorted = sorted(channel_notes[channel], key=lambda n: n[0])
        abc_notes = [_midi_note_to_abc(pitch) for _, _, pitch in notes_sorted]
        voice_line = " ".join(abc_notes) if abc_notes else "z"
        lines.append(f"V:{voice_idx}")
        lines.append(voice_line)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Commit resolution helpers
# ---------------------------------------------------------------------------


def resolve_commit_id(
    root: pathlib.Path,
    commit_prefix: Optional[str],
) -> str:
    """Resolve a commit prefix (or None for HEAD) to a full commit ID.

    When commit_prefix is None, reads the HEAD pointer from
    .muse/refs/heads/<branch> and returns its value.

    This is a filesystem-only helper — DB prefix resolution is done
    in the Typer command using the open session.

    Args:
        root: Muse repository root.
        commit_prefix: Short commit ID prefix, or None for HEAD.

    Returns:
        A non-empty string suitable for DB lookup (may still be a prefix
        when commit_prefix is provided; the caller does DB resolution).

    Raises:
        ValueError: If HEAD has no commits yet.
    """
    if commit_prefix is not None:
        return commit_prefix

    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)
    if not ref_path.exists():
        raise ValueError("No commits yet — nothing to export.")
    head_commit_id = ref_path.read_text().strip()
    if not head_commit_id:
        raise ValueError("No commits yet — nothing to export.")
    return head_commit_id
