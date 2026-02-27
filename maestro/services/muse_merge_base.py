"""Muse Merge Base — find the lowest common ancestor of two variations.

Equivalent to ``git merge-base A B``. Walks both lineages and returns
the closest common ancestor variation_id.

Boundary rules:
  - Must NOT import StateStore, executor, MCP tools, or handlers.
  - May import muse_repository (for lineage queries).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.services import muse_repository

logger = logging.getLogger(__name__)


async def find_merge_base(
    session: AsyncSession,
    a: str,
    b: str,
) -> str | None:
    """Find the lowest common ancestor of two variation lineages.

    Walks ``parent_variation_id`` chains for both ``a`` and ``b``,
    then returns the most recent variation_id that appears in both
    lineages.  Returns None if the variations share no common history.

    Deterministic: for the same inputs, always returns the same result.
    """
    lineage_a = await muse_repository.get_lineage(session, a)
    lineage_b = await muse_repository.get_lineage(session, b)

    if not lineage_a or not lineage_b:
        return None

    ids_a = {n.variation_id for n in lineage_a}

    for node in reversed(lineage_b):
        if node.variation_id in ids_a:
            logger.info(
                "✅ Merge base found: %s (between %s and %s)",
                node.variation_id[:8], a[:8], b[:8],
            )
            return node.variation_id

    return None
