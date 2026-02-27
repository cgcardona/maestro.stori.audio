"""Pattern-based intent rules and slot extraction."""

from __future__ import annotations

import re

from maestro.core.intent_config import Intent
from maestro.core.intent.models import Rule, Slots
from maestro.core.intent.normalization import _num

RULES: list[Rule] = [
    # Transport
    Rule("play", Intent.PLAY, re.compile(r"^(play|start|begin)$"), 0.99),
    Rule("stop", Intent.STOP, re.compile(r"^(stop|pause)$"), 0.99),

    # UI
    Rule("show_panel", Intent.UI_SHOW_PANEL,
         re.compile(r"^(show|hide|open|close) (the )?(mixer|inspector|piano ?roll|step ?sequencer)\b"), 0.92),
    Rule("zoom_pct", Intent.UI_SET_ZOOM,
         re.compile(r"^set (the )?zoom( to)? (?P<pct>\d+)%$"), 0.9),
    Rule("zoom_dir", Intent.UI_SET_ZOOM,
         re.compile(r"^zoom (in|out)$"), 0.95),

    # Project
    Rule("tempo", Intent.PROJECT_SET_TEMPO,
         re.compile(r"^(set|change) (the )?(tempo|bpm)( to)? (?P<bpm>\d{2,3})(\.\d+)?$|^(tempo|bpm) (?P<bpm2>\d{2,3})(\.\d+)?$"), 0.93,
         "extract_tempo"),
    Rule("key", Intent.PROJECT_SET_KEY,
         re.compile(r"^(set|change) (the )?key( to)? (?P<key>[A-Ga-g][#b]?m?)"), 0.90),

    # Track
    Rule("add_track", Intent.TRACK_ADD,
         re.compile(r"^(add|create) (a )?(new )?(midi )?(\w+(\s+\w+)*\s+)?(track|drum track|bass track|piano track|guitar track|melody track)\b"), 0.82),
    Rule("rename_track", Intent.TRACK_RENAME,
         re.compile(r"^(rename|name) (the )?(.+? )?(track)?\b"), 0.75),
    Rule("mute_track", Intent.TRACK_MUTE,
         re.compile(r"^(mute|unmute) (the )?(.+? )?(track)?\b"), 0.85),
    Rule("solo_track", Intent.TRACK_SOLO,
         re.compile(r"^(solo|unsolo) (the )?(.+? )?(track)?\b"), 0.85),
    Rule("set_volume", Intent.TRACK_SET_VOLUME,
         re.compile(r"^set (the )?(.+? )?(volume|level)( to)? (?P<vol>-?\d+(\.\d+)?)\s*(db)?\b"), 0.85,
         "extract_volume"),
    Rule("set_pan", Intent.TRACK_SET_PAN,
         re.compile(r"^(pan|set pan)( (the )?(.+? )?(track)?)?( to)? (?P<pan>-?\d+|left|right|center)\b"), 0.85),
    Rule("set_icon", Intent.TRACK_SET_ICON,
         re.compile(r"^set (the )?.*icon\b"), 0.75),
    Rule("set_color", Intent.TRACK_SET_COLOR,
         re.compile(r"^set (the )?.*color\b"), 0.75),

    # Effects
    Rule("add_effect", Intent.FX_ADD_INSERT,
         re.compile(r"^add (a )?(compressor|eq|reverb|delay|chorus|flanger|phaser|distortion|limiter|gate)( to)?"), 0.80),

    # Notes
    Rule("quantize", Intent.NOTES_QUANTIZE,
         re.compile(r"^quantize"), 0.85),
    Rule("swing", Intent.NOTES_SWING,
         re.compile(r"^(add )?swing"), 0.85),
]


def _extract_slots(rule: Rule, m: re.Match[str], raw: str, norm: str) -> Slots:
    """Extract slots from a matched rule."""
    if rule.intent == Intent.PROJECT_SET_TEMPO:
        bpm = m.group("bpm") or m.groupdict().get("bpm2")
        return Slots(target_type="project", action="set_tempo", amount=_num(bpm) if bpm else None, amount_unit="bpm", value_str=bpm)

    if rule.intent == Intent.UI_SET_ZOOM:
        pct = m.groupdict().get("pct")
        if pct:
            return Slots(target_type="ui", action="zoom", amount=_num(pct), amount_unit="percent", value_str=pct)
        d = norm.split()[-1]
        return Slots(target_type="ui", action="zoom", direction=d, value_str=raw)

    if rule.intent == Intent.TRACK_ADD:
        return Slots(target_type="track", action="add", value_str=raw)

    if rule.intent == Intent.UI_SHOW_PANEL:
        panel = m.group(3)
        verb = m.group(1)
        return Slots(target_type="panel", target_name=panel.replace(" ", "_"), action="show",
                     extras={"visible": verb in ("show", "open")}, value_str=raw)

    if rule.intent == Intent.TRACK_SET_VOLUME:
        vol = m.groupdict().get("vol")
        return Slots(target_type="track", action="set_volume", amount=_num(vol) if vol else None, amount_unit="dB", value_str=raw)

    if rule.intent == Intent.TRACK_SET_PAN:
        pan = m.groupdict().get("pan")
        return Slots(target_type="track", action="set_pan", value_str=pan)

    return Slots(value_str=raw)
