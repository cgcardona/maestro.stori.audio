"""API routes: GitHub issue/PR HTMX partials (comments, CI checks, reviews)."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from starlette.requests import Request

from agentception.routes.ui._shared import _TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/issues/{number}/comments")
async def issue_comments_partial(request: Request, number: int) -> object:
    """HTMX partial: render comments for issue #{number}.

    Lazily fetches from GitHub so the issue detail page loads without blocking.
    """
    from agentception.readers.github import get_issue_comments

    comments: list[dict[str, object]] = []
    try:
        comments = await get_issue_comments(number)
    except Exception as exc:
        logger.warning("⚠️  get_issue_comments(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/issue_comments.html",
        {"comments": comments},
    )


@router.get("/prs/{number}/checks")
async def pr_checks_partial(request: Request, number: int) -> object:
    """HTMX partial: render CI check statuses for PR #{number}."""
    from agentception.readers.github import get_pr_checks

    checks: list[dict[str, object]] = []
    error: str | None = None
    try:
        checks = await get_pr_checks(number)
    except Exception as exc:
        error = str(exc)
        logger.warning("⚠️  get_pr_checks(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/pr_checks.html",
        {"checks": checks, "error": error},
    )


@router.get("/prs/{number}/reviews")
async def pr_reviews_partial(request: Request, number: int) -> object:
    """HTMX partial: render review decisions for PR #{number}."""
    from agentception.readers.github import get_pr_reviews

    reviews: list[dict[str, object]] = []
    error: str | None = None
    try:
        reviews = await get_pr_reviews(number)
    except Exception as exc:
        error = str(exc)
        logger.warning("⚠️  get_pr_reviews(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/pr_reviews.html",
        {"reviews": reviews, "error": error},
    )
