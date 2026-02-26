"""Muse Checkout Executor — apply a CheckoutPlan to StateStore.

Dispatches each tool call in the plan through the existing StateStore
mutation methods, producing SSE-compatible events that the DAW
processes identically to normal editing execution.

Boundary rules:
  - Must NOT import LLM handlers or maestro_* modules.
  - Must NOT import VariationService.
  - Must NOT import muse_replay internals.
  - May import tool_names, state_store, tracing, muse_checkout, muse_drift.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from app.contracts.json_types import AftertouchDict, CCEventDict, JSONValue, NoteDict, PitchBendDict, is_note_dict, jfloat, jint
from app.core.state_store import StateStore, Transaction
from app.core.tool_names import ToolName
from app.core.tracing import TraceContext, trace_span
from app.services.muse_checkout import CheckoutPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckoutExecutionResult:
    """Immutable summary returned after executing a ``CheckoutPlan``.

    Callers inspect ``success`` / ``is_noop`` to decide whether to commit the
    checkout transaction and emit the Muse SSE ``checkout`` event.

    Attributes:
        project_id: Project the checkout was applied to.
        target_variation_id: Variation UUID that was restored.
        executed: Number of tool calls that ran without error.
        failed: Number of tool calls that raised an exception.
        plan_hash: SHA-256 prefix of the ``CheckoutPlan`` for idempotency logs.
        events: SSE-compatible event dicts emitted during execution; forwarded
            to the Muse SSE stream by the route handler.
    """

    project_id: str
    target_variation_id: str
    executed: int
    failed: int
    plan_hash: str
    events: tuple[dict[str, JSONValue], ...] = ()

    @property
    def success(self) -> bool:
        """``True`` when all tool calls executed without error (and at least one ran)."""
        return self.failed == 0 and self.executed > 0

    @property
    def is_noop(self) -> bool:
        """``True`` when the plan had no tool calls (working tree already matched target)."""
        return self.executed == 0 and self.failed == 0


def execute_checkout_plan(
    *,
    checkout_plan: CheckoutPlan,
    store: StateStore,
    trace: TraceContext,
    emit_sse: bool = True,
) -> CheckoutExecutionResult:
    """Execute a CheckoutPlan by dispatching tool calls to StateStore.

    Each tool call in the plan is applied in deterministic order:
    ``stori_clear_notes`` → ``stori_add_notes`` → controllers.

    The ``store`` parameter is typed as ``Any`` to avoid importing
    ``StateStore`` directly — the caller passes a concrete store
    instance.  The executor calls its public methods only.

    Args:
        checkout_plan: The plan to execute (pure data).
        store: StateStore instance (duck-typed).
        trace: TraceContext for logging and spans.
        emit_sse: When True, collect SSE-compatible events.
    """
    if checkout_plan.is_noop:
        logger.info("✅ Checkout is no-op — nothing to execute")
        return CheckoutExecutionResult(
            project_id=checkout_plan.project_id,
            target_variation_id=checkout_plan.target_variation_id,
            executed=0,
            failed=0,
            plan_hash=checkout_plan.plan_hash(),
        )

    executed = 0
    failed = 0
    events: list[dict[str, JSONValue]] = []

    txn = store.begin_transaction(
        f"checkout:{checkout_plan.target_variation_id[:8]}",
    )

    try:
        with trace_span(trace, "checkout_execution", {
            "target": checkout_plan.target_variation_id,
            "call_count": len(checkout_plan.tool_calls),
        }):
            for call in checkout_plan.tool_calls:
                tool = call["tool"]
                args = call["arguments"]
                call_id = str(uuid.uuid4())

                try:
                    with trace_span(trace, f"checkout_tool:{tool}"):
                        _dispatch_tool(tool, args, store, txn)

                    if emit_sse:
                        events.append({
                            "type": "toolCall",
                            "id": call_id,
                            "tool": tool,
                            "params": args,
                        })
                    executed += 1

                except Exception as e:
                    failed += 1
                    logger.error("❌ Checkout tool failed: %s — %s", tool, e)
                    if emit_sse:
                        events.append({
                            "type": "toolError",
                            "id": call_id,
                            "tool": tool,
                            "error": str(e),
                        })

        if failed == 0:
            store.commit(txn)
            logger.info(
                "✅ Checkout executed: %d calls, target=%s",
                executed, checkout_plan.target_variation_id[:8],
            )
        else:
            store.rollback(txn)
            logger.warning(
                "⚠️ Checkout rolled back: %d executed, %d failed",
                executed, failed,
            )

    except Exception:
        store.rollback(txn)
        raise

    return CheckoutExecutionResult(
        project_id=checkout_plan.project_id,
        target_variation_id=checkout_plan.target_variation_id,
        executed=executed,
        failed=failed,
        plan_hash=checkout_plan.plan_hash(),
        events=tuple(events),
    )


def _make_cc_event(cc_num: int, e: dict[str, JSONValue]) -> CCEventDict:
    return {"cc": cc_num, "beat": jfloat(e.get("beat")), "value": jint(e.get("value"))}


def _make_pb_event(e: dict[str, JSONValue]) -> PitchBendDict:
    return {"beat": jfloat(e.get("beat")), "value": jint(e.get("value"))}


def _make_at_event(e: dict[str, JSONValue]) -> AftertouchDict:
    return {"beat": jfloat(e.get("beat")), "value": jint(e.get("value"))}


def _dispatch_tool(
    tool: str,
    args: dict[str, JSONValue],
    store: StateStore,
    txn: Transaction,
) -> None:
    """Dispatch a single checkout tool call to StateStore methods."""
    _rid_raw = args.get("regionId", "")
    region_id = _rid_raw if isinstance(_rid_raw, str) else ""

    if tool == ToolName.CLEAR_NOTES.value:
        current = store.get_region_notes(region_id)
        if current:
            store.remove_notes(region_id, current, transaction=txn)

    elif tool == ToolName.ADD_NOTES.value:
        _notes_raw = args.get("notes", [])
        notes: list[NoteDict] = (
            [n for n in _notes_raw if is_note_dict(n)]
            if isinstance(_notes_raw, list) else []
        )
        if notes:
            store.add_notes(region_id, notes, transaction=txn)

    elif tool == ToolName.ADD_MIDI_CC.value:
        _cc_raw = args.get("cc", 0)
        cc_num = int(_cc_raw) if isinstance(_cc_raw, (int, float)) else 0
        _events_raw = args.get("events", [])
        raw_events = [e for e in _events_raw if isinstance(e, dict)] if isinstance(_events_raw, list) else []
        cc_events: list[CCEventDict] = [
            _make_cc_event(cc_num, e)
            for e in raw_events
        ]
        if cc_events:
            store.add_cc(region_id, cc_events)

    elif tool == ToolName.ADD_PITCH_BEND.value:
        _pb_raw = args.get("events", [])
        raw_pb = [e for e in _pb_raw if isinstance(e, dict)] if isinstance(_pb_raw, list) else []
        pb_events: list[PitchBendDict] = [_make_pb_event(e) for e in raw_pb]
        if pb_events:
            store.add_pitch_bends(region_id, pb_events)

    elif tool == ToolName.ADD_AFTERTOUCH.value:
        _at_raw = args.get("events", [])
        raw_at = [e for e in _at_raw if isinstance(e, dict)] if isinstance(_at_raw, list) else []
        at_events: list[AftertouchDict] = [_make_at_event(e) for e in raw_at]
        if at_events:
            store.add_aftertouch(region_id, at_events)

    else:
        raise ValueError(f"Unsupported checkout tool: {tool}")
