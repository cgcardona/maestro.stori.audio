"""COMPOSING and REASONING handlers — Maestro ↔ Muse integration boundary.

This package is the **only** place where Maestro's orchestration layer
touches Muse's variation/commit subsystem.  No code above this package
(e.g. ``maestro_handlers.py``) should import Muse-specific types
(``Variation``, ``VariationStore``, ``VariationService``, ``Phrase``,
``NoteChange``).

Muse Interface Contract
-----------------------
Maestro provides:
    - Immutable snapshots of region data (via ``capture_base_snapshot`` /
      ``capture_proposed_snapshot`` from ``app.core.executor.snapshots``).
    - ``base_state_id`` and ``conversation_id`` as opaque strings.
    - ``region_metadata`` dict mapping ``region_id`` to ``{startBeat,
      durationBeats, name}`` — built from the StateStore registry by the
      caller before invoking ``_store_variation``.

Muse returns:
    - ``Variation`` objects with phrases for SSE streaming.
    - ``CommitResult`` via ``apply_variation_phrases``.

Muse MUST NOT:
    - Call ``get_or_create_store()`` or access ``StateStore`` directly.
    - Read ``StateStore._region_notes`` — base/proposed notes are provided
      as parameters.
    - Access ``EntityRegistry`` — region metadata is provided by the caller.
    - Depend on ``conversation_id`` for StateStore lookups.
"""

from app.core.maestro_composing.storage import _store_variation
from app.core.maestro_composing.fallback import (
    _create_editing_fallback_route,
    _retry_composing_as_editing,
)
from app.core.maestro_composing.reasoning import _handle_reasoning
from app.core.maestro_composing.composing import (
    _handle_composing,
    _handle_composing_with_agent_teams,
)

__all__ = [
    "_store_variation",
    "_create_editing_fallback_route",
    "_retry_composing_as_editing",
    "_handle_reasoning",
    "_handle_composing",
    "_handle_composing_with_agent_teams",
]
