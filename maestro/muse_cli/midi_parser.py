"""MIDI and MusicXML parsing for ``muse import``.

Converts standard music file formats into Muse's internal note representation:
a list of :class:`NoteEvent` objects and a :class:`MuseImportData` container.

Supported formats
-----------------
- ``.mid`` / ``.midi`` — Standard MIDI File via ``mido``
- ``.xml`` / ``.musicxml`` — MusicXML via Python's built-in ``xml.etree.ElementTree``

Named result types registered in ``docs/reference/type_contracts.md``:
- ``MuseImportData``
- ``NoteEvent``
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)

#: File extensions accepted by this module.
SUPPORTED_MIDI_EXTENSIONS = {".mid", ".midi"}
SUPPORTED_XML_EXTENSIONS = {".xml", ".musicxml"}
SUPPORTED_EXTENSIONS = SUPPORTED_MIDI_EXTENSIONS | SUPPORTED_XML_EXTENSIONS


@dataclasses.dataclass
class NoteEvent:
    """A single sounding note extracted from an imported file."""

    pitch: int
    velocity: int
    start_tick: int
    duration_ticks: int
    channel: int
    channel_name: str


@dataclasses.dataclass
class MuseImportData:
    """All data extracted from a single imported music file."""

    source_path: pathlib.Path
    format: str
    ticks_per_beat: int
    tempo_bpm: float
    notes: list[NoteEvent]
    tracks: list[str]
    raw_meta: dict[str, Any]


def parse_file(path: pathlib.Path) -> MuseImportData:
    """Dispatch to the correct parser based on file extension.

    Raises:
        ValueError: When the extension is not in :data:`SUPPORTED_EXTENSIONS`.
        FileNotFoundError: When the file does not exist.
        RuntimeError: When the file is malformed.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    ext = path.suffix.lower()
    if ext in SUPPORTED_MIDI_EXTENSIONS:
        return parse_midi_file(path)
    if ext in SUPPORTED_XML_EXTENSIONS:
        return parse_musicxml_file(path)
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    raise ValueError(
        f"Unsupported file extension '{path.suffix}'.  Supported: {supported}"
    )


def parse_midi_file(path: pathlib.Path) -> MuseImportData:
    """Parse a Standard MIDI File into a :class:`MuseImportData`.

    Uses ``mido``.  Note-on with velocity=0 is treated as note-off.

    Raises:
        RuntimeError: When ``mido`` cannot read the file.
    """
    try:
        import mido
    except ImportError:
        raise RuntimeError(
            "mido is required for MIDI import.  "
            "It is pre-installed in the Maestro Docker image."
        )

    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:
        raise RuntimeError(f"Cannot parse MIDI file '{path}': {exc}") from exc

    ticks_per_beat = int(mid.ticks_per_beat)
    tempo_us: int = 500_000  # 120 BPM default
    notes: list[NoteEvent] = []
    # (channel, pitch) -> (start_tick, velocity)
    active: dict[tuple[int, int], tuple[int, int]] = {}

    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo_us = msg.tempo
            elif msg.type == "note_on" and msg.velocity > 0:
                active[(msg.channel, msg.note)] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if key in active:
                    start, vel = active.pop(key)
                    notes.append(
                        NoteEvent(
                            pitch=msg.note,
                            velocity=vel,
                            start_tick=start,
                            duration_ticks=max(abs_tick - start, 1),
                            channel=msg.channel,
                            channel_name=f"ch{msg.channel}",
                        )
                    )

    # Notes never closed — truncate to duration 1
    for (ch, pitch), (start, vel) in active.items():
        notes.append(
            NoteEvent(
                pitch=pitch,
                velocity=vel,
                start_tick=start,
                duration_ticks=1,
                channel=ch,
                channel_name=f"ch{ch}",
            )
        )

    tempo_bpm = 60_000_000 / tempo_us
    tracks = _unique_ordered([n.channel_name for n in notes])

    logger.debug(
        "✅ Parsed MIDI %s: %d notes, %d tracks, %.1f BPM",
        path.name, len(notes), len(tracks), tempo_bpm,
    )
    return MuseImportData(
        source_path=path,
        format="midi",
        ticks_per_beat=ticks_per_beat,
        tempo_bpm=tempo_bpm,
        notes=notes,
        tracks=tracks,
        raw_meta={"num_tracks": len(mid.tracks)},
    )


def parse_musicxml_file(path: pathlib.Path) -> MuseImportData:
    """Parse a MusicXML ``<score-partwise>`` file into a :class:`MuseImportData`.

    Raises:
        RuntimeError: When the XML is invalid or not a recognisable MusicXML document.
    """
    try:
        tree = ET.parse(str(path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Cannot parse MusicXML file '{path}': {exc}") from exc

    root = tree.getroot()

    # Strip XML namespace prefix, e.g. {http://www.musicxml.org/…}element → element
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[: root.tag.index("}") + 1]

    def t(name: str) -> str:
        return f"{ns}{name}"

    if root.tag not in (t("score-partwise"), "score-partwise"):
        raise RuntimeError(
            f"Unrecognised MusicXML root element '{root.tag}'.  "
            "Expected <score-partwise>."
        )

    tempo_bpm = 120.0
    for direction in root.iter(t("direction")):
        sound = direction.find(t("sound"))
        if sound is not None:
            raw = sound.get("tempo")
            if raw is not None:
                try:
                    tempo_bpm = float(raw)
                    break
                except ValueError:
                    pass

    ticks_per_beat = 480  # internal default for MusicXML

    part_names: list[str] = []
    for pn in root.iter(t("part-name")):
        name = (pn.text or "").strip()
        part_names.append(name or f"Part {len(part_names) + 1}")

    _STEP_SEMITONE: dict[str, int] = {
        "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
    }

    notes: list[NoteEvent] = []
    parts = root.findall(t("part"))

    for ch_idx, part_el in enumerate(parts):
        channel_name = part_names[ch_idx] if ch_idx < len(part_names) else f"ch{ch_idx}"
        abs_tick = 0
        divisions = 1

        for measure_el in part_el.findall(t("measure")):
            attrs = measure_el.find(t("attributes"))
            if attrs is not None:
                div_el = attrs.find(t("divisions"))
                if div_el is not None and div_el.text:
                    try:
                        divisions = int(div_el.text)
                    except ValueError:
                        pass

            measure_tick = abs_tick

            for note_el in measure_el.findall(t("note")):
                dur_el = note_el.find(t("duration"))
                dur_xml = int(dur_el.text) if dur_el is not None and dur_el.text else 0
                dur_ticks = int(dur_xml * ticks_per_beat / max(divisions, 1))

                if note_el.find(t("rest")) is not None:
                    measure_tick += dur_ticks
                    continue

                pitch_el = note_el.find(t("pitch"))
                if pitch_el is None:
                    measure_tick += dur_ticks
                    continue

                step_el = pitch_el.find(t("step"))
                oct_el = pitch_el.find(t("octave"))
                alt_el = pitch_el.find(t("alter"))

                step = (step_el.text or "C").strip() if step_el is not None else "C"
                octave = int(oct_el.text or "4") if oct_el is not None else 4
                alter = int(float(alt_el.text or "0")) if alt_el is not None else 0

                pitch = max(0, min(127, (octave + 1) * 12 + _STEP_SEMITONE.get(step, 0) + alter))
                is_chord = note_el.find(t("chord")) is not None
                note_start = measure_tick
                if not is_chord:
                    measure_tick += dur_ticks

                notes.append(
                    NoteEvent(
                        pitch=pitch,
                        velocity=80,
                        start_tick=note_start,
                        duration_ticks=max(dur_ticks, 1),
                        channel=ch_idx,
                        channel_name=channel_name,
                    )
                )

            abs_tick = measure_tick

    tracks = _unique_ordered([n.channel_name for n in notes])
    logger.debug(
        "✅ Parsed MusicXML %s: %d notes, %d parts, %.1f BPM",
        path.name, len(notes), len(parts), tempo_bpm,
    )
    return MuseImportData(
        source_path=path,
        format="musicxml",
        ticks_per_beat=ticks_per_beat,
        tempo_bpm=tempo_bpm,
        notes=notes,
        tracks=tracks,
        raw_meta={"num_parts": len(parts), "part_names": part_names},
    )


def apply_track_map(notes: list[NoteEvent], track_map: dict[str, str]) -> list[NoteEvent]:
    """Return notes with ``channel_name`` fields remapped per *track_map*.

    Keys may be ``"ch<N>"`` or bare channel number strings.
    Notes for unmapped channels are returned unchanged.
    """
    normalised: dict[int, str] = {}
    for key, name in track_map.items():
        k = key.strip()
        try:
            ch = int(k[2:]) if k.startswith("ch") else int(k)
            normalised[ch] = name
        except ValueError:
            logger.warning("⚠️ Ignoring invalid track-map key %r", key)

    result: list[NoteEvent] = []
    for note in notes:
        if note.channel in normalised:
            result.append(dataclasses.replace(note, channel_name=normalised[note.channel]))
        else:
            result.append(note)
    return result


def parse_track_map_arg(raw: str) -> dict[str, str]:
    """Parse ``"ch0=bass,ch1=piano"`` into ``{"ch0": "bass", "ch1": "piano"}``.

    Raises:
        ValueError: When any pair is not in ``KEY=VALUE`` format.
    """
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(
                f"Invalid track-map entry {pair!r}.  Expected KEY=VALUE (e.g. ch0=bass)."
            )
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()
    return result


def analyze_import(data: MuseImportData) -> str:
    """Return a multi-line analysis of *data* covering harmonic, rhythmic, and dynamic dimensions."""
    notes = data.notes
    if not notes:
        return "  (no notes found — file may be empty or contain only meta events)"

    _NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    pitches = [n.pitch for n in notes]
    pitch_counts: dict[int, int] = {}
    for p in pitches:
        pitch_counts[p] = pitch_counts.get(p, 0) + 1
    top_pitches = sorted(pitch_counts, key=lambda p: -pitch_counts[p])[:5]
    top_str = ", ".join(
        f"{_NOTE_NAMES[p % 12]}{p // 12 - 1}({pitch_counts[p]}x)" for p in top_pitches
    )
    pitch_min, pitch_max = min(pitches), max(pitches)

    total = len(notes)
    span_ticks = max(n.start_tick + n.duration_ticks for n in notes)
    span_beats = span_ticks / max(data.ticks_per_beat, 1)
    density = total / max(span_beats, 0.001)

    velocities = [n.velocity for n in notes]
    avg_vel = sum(velocities) / len(velocities)
    vel_min, vel_max = min(velocities), max(velocities)

    def _band(v: float) -> str:
        if v < 40: return "pp (very soft)"
        if v < 65: return "p (soft)"
        if v < 85: return "mp/mf (medium)"
        if v < 105: return "f (loud)"
        return "ff (very loud)"

    track_summary = ", ".join(data.tracks) if data.tracks else "(none)"
    return "\n".join([
        f"  Format:      {data.format}",
        f"  Tempo:       {data.tempo_bpm:.1f} BPM",
        f"  Tracks:      {track_summary}",
        "",
        "  ── Harmonic ──────────────────────────────────",
        f"  Pitch range: {_NOTE_NAMES[pitch_min % 12]}{pitch_min // 12 - 1}"
        f"–{_NOTE_NAMES[pitch_max % 12]}{pitch_max // 12 - 1}",
        f"  Top pitches: {top_str}",
        "",
        "  ── Rhythmic ──────────────────────────────────",
        f"  Notes:       {total}",
        f"  Span:        {span_beats:.1f} beats",
        f"  Density:     {density:.1f} notes/beat",
        "",
        "  ── Dynamic ───────────────────────────────────",
        f"  Velocity:    avg={avg_vel:.0f}, min={vel_min}, max={vel_max}",
        f"  Character:   {_band(avg_vel)}",
    ])


def _unique_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
