"""Muse Checkout Engine — translate ReplayPlan into DAW tool calls.

Converts a target snapshot (from replay/reconstruction) into a
deterministic, ordered stream of tool calls that would reconstruct
the target musical state from the current working state.

Pure data translator — does NOT execute tool calls.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules or app.core.executor.*.
  - Must NOT import LLM handlers or maestro_* modules.
  - May import muse_replay (HeadSnapshot), muse_drift (fingerprinting).
  - May import ToolName enum from app.core.tool_names.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing_extensions import TypedDict

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
)
from app.core.tool_names import ToolName
from app.services.muse_drift import _fingerprint, _combined_fingerprint
from app.services.variation.note_matching import (
    match_notes,
    match_cc_events,
    match_pitch_bends,
    match_aftertouch,
)

logger = logging.getLogger(__name__)

REGION_RESET_THRESHOLD = 20


class CheckoutToolCall(TypedDict):
    """A single tool call produced by the checkout planner.

    Structural twin of ``ToolCall`` but serialised as a plain TypedDict so
    it can be stored in frozen dataclasses (tuples) without hashability issues.

    Attributes:
        tool: Canonical ``ToolName`` value string (e.g. ``"stori_add_notes"``).
        arguments: Keyword arguments forwarded verbatim to the executor.
    """

    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class CheckoutPlan:
    """Deterministic, immutable plan for restoring a variation's state.

    Produced by ``build_checkout_plan`` — a pure function that diffs the
    current working tree against the target variation.  Consumed by
    ``execute_checkout_plan`` in ``muse_checkout_executor``.

    Pure data — no side effects, no mutations.

    Attributes:
        project_id: Project the checkout targets.
        target_variation_id: Variation UUID to restore.
        tool_calls: Ordered sequence of ``CheckoutToolCall``s that, when
            executed, transform the working tree into the target state.
        regions_reset: Region UUIDs that required a full clear + re-add because
            the diff exceeded ``REGION_RESET_THRESHOLD`` or had removals.
        fingerprint_target: Expected ``{region_id: sha256}`` fingerprint map
            after execution — used to verify the checkout landed correctly.
    """

    project_id: str
    target_variation_id: str
    tool_calls: tuple[CheckoutToolCall, ...]
    regions_reset: tuple[str, ...]
    fingerprint_target: dict[str, str]

    @property
    def is_noop(self) -> bool:
        """``True`` when the working tree already matches the target (no calls needed)."""
        return len(self.tool_calls) == 0

    def plan_hash(self) -> str:
        """Deterministic hash of the entire plan for idempotency checks."""
        raw = json.dumps(
            {
                "project_id": self.project_id,
                "target": self.target_variation_id,
                "calls": list(self.tool_calls),
                "resets": list(self.regions_reset),
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _make_tool_call(tool: ToolName, arguments: dict[str, object]) -> CheckoutToolCall:
    """Construct a ``CheckoutToolCall`` from a ``ToolName`` enum and argument dict."""
    return {"tool": tool.value, "arguments": arguments}


def _build_region_note_calls(
    region_id: str,
    target_notes: list[NoteDict],
    working_notes: list[NoteDict],
) -> tuple[list[CheckoutToolCall], bool]:
    """Produce tool calls to transition notes from working → target.

    Returns (tool_calls, was_reset).  Uses region reset (clear + add) when
    there are removals/modifications or the diff exceeds the threshold,
    because there is no individual note-remove tool.
    """
    matches = match_notes(working_notes, target_notes)

    added = [m for m in matches if m.is_added]
    removed = [m for m in matches if m.is_removed]
    modified = [m for m in matches if m.is_modified]

    if not added and not removed and not modified:
        return [], False

    total_changes = len(added) + len(removed) + len(modified)
    needs_reset = bool(removed or modified) or total_changes >= REGION_RESET_THRESHOLD

    calls: list[CheckoutToolCall] = []

    if needs_reset:
        calls.append(_make_tool_call(ToolName.CLEAR_NOTES, {"regionId": region_id}))
        if target_notes:
            notes_for_reset: list[NoteDict] = [
                {
                    "pitch": n.get("pitch", 60),
                    "startBeat": n.get("start_beat", 0.0),
                    "durationBeats": n.get("duration_beats", 0.5),
                    "velocity": n.get("velocity", 100),
                }
                for n in target_notes
            ]
            calls.append(_make_tool_call(
                ToolName.ADD_NOTES,
                {"regionId": region_id, "notes": notes_for_reset},
            ))
        return calls, True

    if added:
        notes_to_add: list[NoteDict] = [
            {
                "pitch": m.proposed_note.get("pitch", 60),
                "startBeat": m.proposed_note.get("start_beat", 0.0),
                "durationBeats": m.proposed_note.get("duration_beats", 0.5),
                "velocity": m.proposed_note.get("velocity", 100),
            }
            for m in added
            if m.proposed_note is not None
        ]
        if notes_to_add:
            calls.append(_make_tool_call(
                ToolName.ADD_NOTES,
                {"regionId": region_id, "notes": notes_to_add},
            ))

    return calls, False


def _build_cc_calls(
    region_id: str,
    target_cc: list[CCEventDict],
    working_cc: list[CCEventDict],
) -> list[CheckoutToolCall]:
    matches = match_cc_events(working_cc, target_cc)
    needed = [m for m in matches if m.is_added or m.is_modified]
    if not needed:
        return []

    by_cc: dict[int, list[PitchBendDict]] = {}
    for m in needed:
        ev = m.proposed_event
        if ev is None:
            continue
        raw_cc = ev.get("cc", 0)
        cc_num = int(raw_cc) if isinstance(raw_cc, (int, float, str)) else 0
        by_cc.setdefault(cc_num, []).append(
            {"beat": ev.get("beat", 0.0), "value": ev.get("value", 0)}
        )

    calls: list[CheckoutToolCall] = []
    for cc_num in sorted(by_cc):
        calls.append(_make_tool_call(
            ToolName.ADD_MIDI_CC,
            {"regionId": region_id, "cc": cc_num, "events": by_cc[cc_num]},
        ))
    return calls


def _build_pb_calls(
    region_id: str,
    target_pb: list[PitchBendDict],
    working_pb: list[PitchBendDict],
) -> list[CheckoutToolCall]:
    matches = match_pitch_bends(working_pb, target_pb)
    needed = [m for m in matches if m.is_added or m.is_modified]
    if not needed:
        return []

    events: list[PitchBendDict] = [
        {"beat": m.proposed_event.get("beat", 0.0), "value": m.proposed_event.get("value", 0)}
        for m in needed
        if m.proposed_event is not None
    ]
    return [_make_tool_call(
        ToolName.ADD_PITCH_BEND,
        {"regionId": region_id, "events": events},
    )]


def _build_at_calls(
    region_id: str,
    target_at: list[AftertouchDict],
    working_at: list[AftertouchDict],
) -> list[CheckoutToolCall]:
    matches = match_aftertouch(working_at, target_at)
    needed = [m for m in matches if m.is_added or m.is_modified]
    if not needed:
        return []

    events: list[AftertouchDict] = []
    for m in needed:
        ev = m.proposed_event
        if ev is None:
            continue
        beat = ev.get("beat", 0.0)
        value = ev.get("value", 0)
        pitch_val = ev.get("pitch")
        if isinstance(pitch_val, int):
            pitch_int: int = pitch_val
            entry: AftertouchDict = {"beat": beat, "value": value, "pitch": pitch_int}
        else:
            entry = {"beat": beat, "value": value}
        events.append(entry)
    return [_make_tool_call(
        ToolName.ADD_AFTERTOUCH,
        {"regionId": region_id, "events": events},
    )]


def build_checkout_plan(
    *,
    project_id: str,
    target_variation_id: str,
    target_notes: RegionNotesMap,
    target_cc: RegionCCMap,
    target_pb: RegionPitchBendMap,
    target_at: RegionAftertouchMap,
    working_notes: RegionNotesMap,
    working_cc: RegionCCMap,
    working_pb: RegionPitchBendMap,
    working_at: RegionAftertouchMap,
    track_regions: dict[str, str],
) -> CheckoutPlan:
    """Build a checkout plan that transforms working state → target state.

    Produces an ordered sequence of tool calls:
    1. ``stori_clear_notes`` (region resets, when needed)
    2. ``stori_add_notes``
    3. ``stori_add_midi_cc`` / ``stori_add_pitch_bend`` / ``stori_add_aftertouch``

    Pure function — no I/O, no StateStore.
    """
    all_rids = sorted(
        set(target_notes) | set(target_cc) | set(target_pb) | set(target_at)
        | set(working_notes) | set(working_cc) | set(working_pb) | set(working_at)
    )

    tool_calls: list[CheckoutToolCall] = []
    regions_reset: list[str] = []
    fingerprint_target: dict[str, str] = {}

    for rid in all_rids:
        t_notes = target_notes.get(rid, [])
        w_notes = working_notes.get(rid, [])
        t_cc = target_cc.get(rid, [])
        w_cc = working_cc.get(rid, [])
        t_pb = target_pb.get(rid, [])
        w_pb = working_pb.get(rid, [])
        t_at = target_at.get(rid, [])
        w_at = working_at.get(rid, [])

        fingerprint_target[rid] = _combined_fingerprint(t_notes, t_cc, t_pb, t_at)

        note_calls, was_reset = _build_region_note_calls(rid, t_notes, w_notes)
        if was_reset:
            regions_reset.append(rid)
        tool_calls.extend(note_calls)

        tool_calls.extend(_build_cc_calls(rid, t_cc, w_cc))
        tool_calls.extend(_build_pb_calls(rid, t_pb, w_pb))
        tool_calls.extend(_build_at_calls(rid, t_at, w_at))

    logger.info(
        "✅ Checkout plan: %d tool calls, %d region resets, %d regions",
        len(tool_calls), len(regions_reset), len(all_rids),
    )

    return CheckoutPlan(
        project_id=project_id,
        target_variation_id=target_variation_id,
        tool_calls=tuple(tool_calls),
        regions_reset=tuple(regions_reset),
        fingerprint_target=fingerprint_target,
    )
