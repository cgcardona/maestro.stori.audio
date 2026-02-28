"""Muse Transpose Service — apply MIDI pitch transposition as a Muse commit.

Provides:

- ``parse_interval`` — convert "+3", "up-minor3rd", "down-perfect5th" to signed semitones.
- ``update_key_metadata`` — transpose a key string (e.g. "Eb major" → "F major").
- ``transpose_midi_bytes`` — pure function: raw MIDI bytes → transposed bytes.
- ``apply_transpose_to_workdir`` — apply transposition to all MIDI files in muse-work/.
- ``TransposeResult`` — named result type for ``muse transpose`` output.

MIDI transposition rules:
- Note-On (0x9n) and Note-Off (0x8n) events on non-drum channels are shifted.
- Channel 9 (MIDI drum channel) is always excluded — drums are unpitched.
- Notes are clamped to [0, 127] to stay within MIDI range.
- All non-note events (tempo, program change, CC, sysex) are preserved verbatim.

Boundary rules:
- Must NOT import StateStore, EntityRegistry, or app.core.*.
- Must NOT import LLM handlers or maestro_* modules.
- Pure data — no FastAPI, no DB access, no side effects beyond file I/O in
  ``apply_transpose_to_workdir``.
"""
from __future__ import annotations

import logging
import pathlib
import struct
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Named interval → absolute semitone count (direction supplied by up-/down- prefix)
_NAMED_INTERVALS: dict[str, int] = {
    "unison": 0,
    "minor2nd": 1,
    "min2nd": 1,
    "major2nd": 2,
    "maj2nd": 2,
    "minor3rd": 3,
    "min3rd": 3,
    "major3rd": 4,
    "maj3rd": 4,
    "perfect4th": 5,
    "perf4th": 5,
    "p4th": 5,
    "augmented4th": 6,
    "aug4th": 6,
    "tritone": 6,
    "diminished5th": 6,
    "dim5th": 6,
    "perfect5th": 7,
    "perf5th": 7,
    "p5th": 7,
    "minor6th": 8,
    "min6th": 8,
    "major6th": 9,
    "maj6th": 9,
    "minor7th": 10,
    "min7th": 10,
    "major7th": 11,
    "maj7th": 11,
    "octave": 12,
}

# MIDI channel 9 (0-indexed) is the universal drum channel.
_DRUM_CHANNEL = 9

# Note name → semitone (C=0, chromatic ascending)
_NOTE_TO_SEMITONE: dict[str, int] = {
    "c": 0,
    "c#": 1,
    "db": 1,
    "d": 2,
    "d#": 3,
    "eb": 3,
    "e": 4,
    "fb": 4,
    "f": 5,
    "e#": 5,
    "f#": 6,
    "gb": 6,
    "g": 7,
    "g#": 8,
    "ab": 8,
    "a": 9,
    "a#": 10,
    "bb": 10,
    "b": 11,
    "cb": 11,
}

# Preferred note name for each semitone (0=C … 11=B).
# Uses flats for accidentals matching common Western music notation.
_SEMITONE_TO_NOTE: list[str] = [
    "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"
]


# ---------------------------------------------------------------------------
# Named result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransposeResult:
    """Result of a ``muse transpose <interval> [<commit>]`` operation.

    Records the source commit, the interval applied, which MIDI files were
    modified, the new commit ID (``None`` in dry-run mode), and key metadata
    before and after.

    Agent use case: after transposition, agents can inspect ``new_commit_id``
    to verify the commit was created and ``new_key`` to update their musical
    context about the current key center.  ``files_modified`` tells the agent
    which tracks changed so it can selectively re-render only those tracks.
    """

    source_commit_id: str
    """Commit that was the source of the transposition."""

    semitones: int
    """Signed semitone offset applied (positive = up, negative = down)."""

    files_modified: list[str] = field(default_factory=list)
    """Relative paths of MIDI files that had notes transposed."""

    files_skipped: list[str] = field(default_factory=list)
    """Relative paths of non-MIDI or excluded files."""

    new_commit_id: str | None = None
    """New commit ID created for the transposed snapshot. None in dry-run mode."""

    original_key: str | None = None
    """Key metadata before transposition, or None if not annotated."""

    new_key: str | None = None
    """Updated key metadata after transposition, or None if original was absent."""

    dry_run: bool = False
    """True if this was a dry-run (no files written, no commit created)."""


# ---------------------------------------------------------------------------
# Interval parsing
# ---------------------------------------------------------------------------


def parse_interval(interval_str: str) -> int:
    """Parse an interval string to a signed semitone count.

    Accepts:
    - Signed integers: ``"+3"``, ``"-5"``, ``"12"`` (no sign = positive).
    - Named intervals: ``"up-minor3rd"``, ``"down-perfect5th"``, ``"up-octave"``.

    Named interval format is ``"<direction>-<name>"`` where direction is
    ``"up"`` (positive) or ``"down"`` (negative), and name is one of the keys
    in ``_NAMED_INTERVALS``.

    Args:
        interval_str: Interval descriptor from the CLI argument.

    Returns:
        Signed semitone count. Positive = up, negative = down.

    Raises:
        ValueError: If the interval string cannot be parsed.
    """
    s = interval_str.strip()
    try:
        return int(s)
    except ValueError:
        pass

    lower = s.lower()
    if lower.startswith("up-"):
        direction = 1
        name = lower[3:]
    elif lower.startswith("down-"):
        direction = -1
        name = lower[5:]
    else:
        raise ValueError(
            f"Cannot parse interval {s!r}. "
            "Use a signed integer (+3, -5) or a named interval "
            "(up-minor3rd, down-perfect5th, up-octave)."
        )

    semitones = _NAMED_INTERVALS.get(name)
    if semitones is None:
        valid = ", ".join(sorted(_NAMED_INTERVALS))
        raise ValueError(
            f"Unknown interval name {name!r}. "
            f"Valid names: {valid}"
        )
    return direction * semitones


# ---------------------------------------------------------------------------
# Key metadata update
# ---------------------------------------------------------------------------


def update_key_metadata(key_str: str, semitones: int) -> str:
    """Transpose a key string by *semitones* and return the updated key name.

    Parses strings in the format ``"<note> <mode>"`` (e.g. ``"Eb major"``,
    ``"F# minor"``).  The root note is transposed; the mode string is preserved
    verbatim.  Unrecognized root notes are returned unchanged so callers can
    safely pass arbitrary metadata strings without crashing.

    Args:
        key_str:   Key string to transpose (e.g. ``"Eb major"``).
        semitones: Signed semitone offset.

    Returns:
        Updated key string (e.g. ``"F major"`` after +2 from ``"Eb major"``).
    """
    parts = key_str.strip().split()
    if not parts:
        return key_str

    root_name = parts[0]
    mode_parts = parts[1:]

    semitone_val = _NOTE_TO_SEMITONE.get(root_name.lower())
    if semitone_val is None:
        logger.debug("⚠️ Unknown key root %r — returning key string unchanged", root_name)
        return key_str

    new_semitone = (semitone_val + semitones) % 12
    new_root = _SEMITONE_TO_NOTE[new_semitone]
    return " ".join([new_root] + mode_parts)


# ---------------------------------------------------------------------------
# Low-level MIDI parsing helpers
# ---------------------------------------------------------------------------


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI variable-length quantity starting at *pos*.

    Returns ``(value, new_pos)`` where *new_pos* points past the last byte
    consumed.  Raises ``IndexError`` if the data is truncated mid-VLQ.

    VLQ encoding: each byte's high bit signals a continuation byte follows.
    The low 7 bits of each byte are concatenated MSB-first to form the value.
    """
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return value, pos


def _get_track_name(track_data: bytes) -> str | None:
    """Extract the track name from a MIDI track chunk's raw event data.

    Scans for the first Track Name meta-event (``0xFF 0x03``) and returns the
    name decoded as latin-1.  Returns ``None`` if no name meta-event is found
    before the stream ends or becomes unparseable.

    This enables the ``--track`` filter in ``muse transpose``: only tracks whose
    name contains the filter substring (case-insensitive) are transposed.
    """
    pos = 0
    length = len(track_data)
    running_status = 0

    while pos < length:
        # Skip delta time (VLQ: bytes with high bit set continue)
        while pos < length and (track_data[pos] & 0x80):
            pos += 1
        if pos >= length:
            break
        pos += 1  # last VLQ byte of delta time

        if pos >= length:
            break

        b = track_data[pos]

        if b == 0xFF:  # meta event
            pos += 1
            if pos >= length:
                break
            meta_type = track_data[pos]
            pos += 1
            try:
                meta_len, pos = _read_vlq(track_data, pos)
            except IndexError:
                break
            if meta_type == 0x03 and pos + meta_len <= length:  # Track Name
                return track_data[pos : pos + meta_len].decode("latin-1")
            pos += meta_len

        elif b == 0xF0 or b == 0xF7:  # sysex
            pos += 1
            try:
                sysex_len, pos = _read_vlq(track_data, pos)
            except IndexError:
                break
            pos += sysex_len

        else:
            # MIDI channel event (may use running status)
            if b & 0x80:
                running_status = b
                pos += 1
            status = running_status
            msg_type = (status >> 4) & 0x0F
            if msg_type in (0x8, 0x9, 0xA, 0xB, 0xE):  # 2 data bytes
                pos += 2
            elif msg_type in (0xC, 0xD):  # 1 data byte
                pos += 1
            else:
                break  # unrecognised — stop scan

    return None


def _transpose_track_data(track_data: bytes, semitones: int) -> bytes:
    """Transpose MIDI notes in a single track's event data.

    Scans the MIDI event stream and modifies Note-On (0x9n) and Note-Off (0x8n)
    events on non-drum channels (channel != 9).  Notes are clamped to [0, 127].
    All other events (meta, sysex, CC, program change, pitch bend, etc.) are
    preserved byte-for-byte.

    The modification is done in-place on a bytearray copy so the track length
    is unchanged — only the note byte values differ.  This guarantees the MTrk
    chunk length header stays valid without re-encoding.

    Args:
        track_data: Raw event bytes from an MTrk chunk (after the 8-byte header).
        semitones:  Signed semitone offset to apply.

    Returns:
        Modified event data of the same length as *track_data*.
    """
    buf = bytearray(track_data)
    pos = 0
    length = len(track_data)
    running_status = 0

    while pos < length:
        # Skip delta time (VLQ)
        while pos < length and (track_data[pos] & 0x80):
            pos += 1
        if pos >= length:
            break
        pos += 1  # last VLQ byte

        if pos >= length:
            break

        b = track_data[pos]

        if b == 0xFF:  # meta event — skip completely
            pos += 1
            if pos >= length:
                break
            pos += 1  # meta type
            try:
                meta_len, pos = _read_vlq(track_data, pos)
            except IndexError:
                break
            pos += meta_len

        elif b == 0xF0 or b == 0xF7:  # sysex — skip completely
            pos += 1
            try:
                sysex_len, pos = _read_vlq(track_data, pos)
            except IndexError:
                break
            pos += sysex_len

        else:
            # MIDI channel message (possibly running status)
            if b & 0x80:
                running_status = b
                pos += 1
            status = running_status
            channel = status & 0x0F
            msg_type = (status >> 4) & 0x0F

            if msg_type in (0x8, 0x9):  # note-off, note-on
                if pos + 1 < length and channel != _DRUM_CHANNEL:
                    original_note = track_data[pos]
                    buf[pos] = max(0, min(127, original_note + semitones))
                pos += 2
            elif msg_type in (0xA, 0xB, 0xE):  # poly pressure, CC, pitch bend
                pos += 2
            elif msg_type in (0xC, 0xD):  # program change, channel pressure
                pos += 1
            else:
                logger.warning(
                    "⚠️ Unknown MIDI event type 0x%X at byte %d — stopping track parse",
                    msg_type,
                    pos,
                )
                break

    return bytes(buf)


# ---------------------------------------------------------------------------
# Public MIDI transposition API
# ---------------------------------------------------------------------------


def transpose_midi_bytes(
    data: bytes,
    semitones: int,
    track_filter: str | None = None,
) -> tuple[bytes, int]:
    """Apply pitch transposition to a MIDI file's raw bytes.

    Parses the standard MIDI file structure (MThd header + MTrk chunks) and
    transposes Note-On/Note-Off events on non-drum channels.  The file
    structure, chunk layout, and all non-note events are preserved exactly.

    When *track_filter* is provided, only MTrk chunks whose Track Name
    meta-event (0xFF 0x03) contains the filter substring (case-insensitive)
    are transposed; other tracks are copied verbatim.

    Args:
        data:          Raw MIDI file bytes.
        semitones:     Signed semitone offset to apply.
        track_filter:  If set, only tracks whose name matches this substring
                       (case-insensitive) are transposed.

    Returns:
        ``(modified_bytes, notes_changed_count)`` where *notes_changed_count*
        is the number of note bytes that were actually modified.  If *data* is
        not a valid MIDI file (no ``MThd`` header), returns ``(data, 0)``.
    """
    if len(data) < 14 or data[:4] != b"MThd":
        return data, 0

    result = bytearray()
    pos = 0

    # MThd: tag(4) + length(4) + format(2) + ntracks(2) + division(2) = 14 bytes
    # The length field itself says how many bytes follow it in the header chunk.
    header_chunk_data_len = struct.unpack(">I", data[4:8])[0]
    header_end = 8 + header_chunk_data_len
    result.extend(data[:header_end])
    pos = header_end

    notes_changed = 0

    while pos + 8 <= len(data):
        chunk_tag = data[pos : pos + 4]
        chunk_len = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        chunk_start = pos + 8
        chunk_end = chunk_start + chunk_len
        chunk_data = data[chunk_start:chunk_end]
        pos = chunk_end

        if chunk_tag != b"MTrk":
            # Non-track chunk (e.g. instrument-specific) — copy verbatim
            result.extend(data[pos - 8 - chunk_len : pos])
            continue

        # Decide whether this track is in scope for transposition
        should_transpose = True
        if track_filter is not None:
            track_name = _get_track_name(chunk_data)
            if track_name is None or track_filter.lower() not in track_name.lower():
                should_transpose = False
                logger.debug(
                    "⚠️ Track %r does not match filter %r — copying verbatim",
                    track_name,
                    track_filter,
                )

        if should_transpose and semitones != 0:
            modified_track = _transpose_track_data(chunk_data, semitones)
            # Count how many note bytes changed
            for orig_byte, new_byte in zip(chunk_data, modified_track):
                if orig_byte != new_byte:
                    notes_changed += 1
        else:
            modified_track = chunk_data

        result.extend(b"MTrk")
        result.extend(struct.pack(">I", len(modified_track)))
        result.extend(modified_track)

    return bytes(result), notes_changed


# ---------------------------------------------------------------------------
# Workdir-level transposition
# ---------------------------------------------------------------------------


def apply_transpose_to_workdir(
    workdir: pathlib.Path,
    semitones: int,
    track_filter: str | None = None,
    section_filter: str | None = None,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """Apply MIDI transposition to all MIDI files under *workdir*.

    Finds all ``.mid`` and ``.midi`` files, transposes them (excluding drum
    channels), and writes modified files back in place unless *dry_run* is set.

    Section filtering is a stub: if *section_filter* is provided a warning is
    logged and the filter is ignored.  Full section-scoped transposition requires
    section boundary markers embedded in the committed MIDI metadata — a future
    enhancement tracked separately.

    Args:
        workdir:        Path to the ``muse-work/`` directory.
        semitones:      Signed semitone offset (positive = up, negative = down).
        track_filter:   Case-insensitive track name substring filter, or None.
        section_filter: Section name filter (stub — ignored with a warning).
        dry_run:        When True, compute what would change but write nothing.

    Returns:
        ``(files_modified, files_skipped)`` — lists of POSIX paths relative
        to *workdir*.  Modified files had at least one note byte changed.
        Skipped files are non-MIDI, unreadable, or had no transposable notes.
    """
    if section_filter is not None:
        logger.warning(
            "⚠️ --section filter is not yet implemented for muse transpose; "
            "transposing all sections. (section=%r)",
            section_filter,
        )

    files_modified: list[str] = []
    files_skipped: list[str] = []

    if not workdir.exists():
        logger.warning("⚠️ muse-work/ directory not found at %s", workdir)
        return files_modified, files_skipped

    for file_path in sorted(workdir.rglob("*")):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        if suffix not in (".mid", ".midi"):
            continue

        rel = file_path.relative_to(workdir).as_posix()
        try:
            original = file_path.read_bytes()
        except OSError as exc:
            logger.warning("⚠️ Cannot read %s: %s", rel, exc)
            files_skipped.append(rel)
            continue

        transposed, notes_changed = transpose_midi_bytes(original, semitones, track_filter)

        if transposed == original or notes_changed == 0:
            logger.debug(
                "⚠️ %s unchanged after transposition (no valid pitched notes found)", rel
            )
            files_skipped.append(rel)
            continue

        if not dry_run:
            try:
                file_path.write_bytes(transposed)
            except OSError as exc:
                logger.error("❌ Cannot write transposed %s: %s", rel, exc)
                files_skipped.append(rel)
                continue

        files_modified.append(rel)
        logger.info(
            "✅ %s %s (%+d semitones, %d note byte(s) changed)",
            "Would transpose" if dry_run else "Transposed",
            rel,
            semitones,
            notes_changed,
        )

    return files_modified, files_skipped
