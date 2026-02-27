"""Rejection sampling loop for generation quality control."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

_R = TypeVar("_R")  # result type returned by generate_fn
_N = TypeVar("_N")  # notes type passed between generate_fn and scorer_fn


@dataclass
class RejectionSamplingResult(Generic[_R]):
    """Result of a rejection sampling loop.

    Generic over ``_R``, the result type returned as the first element of
    each ``generate_fn()`` call.  ``best_result`` is ``None`` only when
    every generation attempt produced an empty result.
    """

    best_result: _R | None
    best_score: float
    attempts: int
    accepted: bool
    all_scores: list[float] = field(default_factory=list)


def rejection_sample(
    generate_fn: Callable[[], tuple[_R, _N | None]],
    scorer_fn: Callable[[_N], tuple[float, object]],
    *,
    max_attempts: int = 6,
    accept_threshold: float = 0.75,
    early_stop_threshold: float = 0.85,
) -> RejectionSamplingResult[_R]:
    """
    Rejection sampling loop with early stopping.

    Args:
        generate_fn: Callable returning (result, notes) tuple.
        scorer_fn: Callable taking notes and returning (score, repair_msgs).
        max_attempts: Maximum number of generation attempts.
        accept_threshold: Minimum score to accept.
        early_stop_threshold: Score above which we stop immediately.

    Returns:
        RejectionSamplingResult with the best result and metrics.
    """
    best_result = None
    best_score = -1.0
    all_scores: list[float] = []

    for attempt in range(max_attempts):
        result, notes = generate_fn()
        if not result or not notes:
            continue

        score, _ = scorer_fn(notes)
        all_scores.append(score)

        if score > best_score:
            best_score = score
            best_result = result

        if score >= early_stop_threshold:
            logger.info(f"Rejection sampling: early stop at attempt {attempt + 1}, score {score:.3f}")
            return RejectionSamplingResult(
                best_result=best_result,
                best_score=best_score,
                attempts=attempt + 1,
                accepted=True,
                all_scores=all_scores,
            )

    accepted = best_score >= accept_threshold
    logger.info(f"Rejection sampling: {len(all_scores)} attempts, best score {best_score:.3f}, accepted={accepted}")
    return RejectionSamplingResult(
        best_result=best_result,
        best_score=best_score,
        attempts=len(all_scores),
        accepted=accepted,
        all_scores=all_scores,
    )
