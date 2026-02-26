"""Rejection sampling loop for generation quality control."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class RejectionSamplingResult:
    """Result of a rejection sampling loop."""
    best_result: object | None  # Whatever generate_fn returns as first element
    best_score: float
    attempts: int
    accepted: bool
    all_scores: list[float]


def rejection_sample(
    generate_fn: Callable[..., Any],
    scorer_fn: Callable[..., Any],
    *,
    max_attempts: int = 6,
    accept_threshold: float = 0.75,
    early_stop_threshold: float = 0.85,
) -> RejectionSamplingResult:
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
