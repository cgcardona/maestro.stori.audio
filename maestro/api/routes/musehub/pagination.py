"""RFC 8288 Link header pagination helpers for Muse Hub list endpoints.

Why this exists: large repos with hundreds of commits, issues, or PRs are
unusable when list endpoints return everything in a single unbounded response.
This module provides a reusable FastAPI dependency (PaginationParams) and two
Link header builders — one for page-based pagination and one for cursor-based —
so every list endpoint can participate in RFC 8288 navigation without duplicating
the header-construction logic.

Usage (page-based):
    @router.get("/repos/{repo_id}/issues")
    async def list_issues(
        pagination: PaginationParams = Depends(),
        response: Response = None,
        ...
    ) -> IssueListResponse:
        all_issues = await svc.list_issues(db, repo_id)
        page_items, total = paginate_list(all_issues, pagination.page, pagination.per_page)
        response.headers["Link"] = build_link_header(request, total, pagination.page, pagination.per_page)
        return IssueListResponse(issues=page_items, total=total)

Usage (cursor-based, e.g. repos list):
    if result.next_cursor:
        response.headers["Link"] = build_cursor_link_header(request, result.next_cursor, limit)
"""
from __future__ import annotations

import math
from typing import TypeVar
from urllib.parse import urlencode, urljoin

from fastapi import Query, Request

T = TypeVar("T")


class PaginationParams:
    """FastAPI dependency that captures both page-based and cursor-based pagination query params.

    Supports two pagination modes:
    - Page-based: ``?page=N&per_page=N`` — use with ``build_link_header`` and ``paginate_list``.
    - Cursor-based: ``?cursor=X&limit=N`` — use with ``build_cursor_link_header``.

    Both sets of params are always parsed; route handlers pick the mode that matches
    their backing service.  Unused params are silently ignored by the endpoint.
    """

    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number (1-indexed, used with per_page)"),
        per_page: int = Query(20, ge=1, le=100, description="Items per page (used with page)"),
        cursor: str | None = Query(None, description="Opaque cursor string from a previous response"),
        limit: int = Query(20, ge=1, le=200, description="Max items per page (used with cursor)"),
    ) -> None:
        self.page = page
        self.per_page = per_page
        self.cursor = cursor
        self.limit = limit


def build_link_header(request: Request, total: int, page: int, per_page: int) -> str:
    """Build an RFC 8288 Link header string for page-based pagination.

    Always emits rel="first" and rel="last".  Emits rel="prev" when page > 1
    and rel="next" when page < last_page.  Query parameters already present on
    the request URL are preserved; ``page`` and ``per_page`` are overridden.

    Args:
        request: The current FastAPI Request — used to derive the base URL and
                 carry-forward any existing query string parameters.
        total:   Total number of items across all pages (used to compute last page).
        page:    Current 1-indexed page number.
        per_page: Number of items per page.

    Returns:
        A comma-joined RFC 8288 Link header value string, e.g.:
        ``<https://…?page=1&per_page=20>; rel="first", <https://…?page=3&per_page=20>; rel="last"``
    """
    last_page = max(1, math.ceil(total / per_page)) if per_page > 0 else 1
    base_url = str(request.url).split("?")[0]
    existing_params = dict(request.query_params)

    def _page_url(p: int) -> str:
        q = {**existing_params, "page": str(p), "per_page": str(per_page)}
        return f"{base_url}?{urlencode(q)}"

    links: list[str] = []
    links.append(f'<{_page_url(1)}>; rel="first"')
    links.append(f'<{_page_url(last_page)}>; rel="last"')
    if page > 1:
        links.append(f'<{_page_url(page - 1)}>; rel="prev"')
    if page < last_page:
        links.append(f'<{_page_url(page + 1)}>; rel="next"')

    return ", ".join(links)


def build_cursor_link_header(request: Request, next_cursor: str, limit: int) -> str:
    """Build an RFC 8288 Link header with rel="next" for cursor-based pagination.

    Only emits rel="next" — cursor pagination cannot derive "prev", "first", or
    "last" without a separate total count query, which would be expensive for
    repos that may have thousands of items.

    Args:
        request:     The current FastAPI Request — used to derive the base URL.
        next_cursor: Opaque cursor value returned by the backing service.
        limit:       The page size that was used for the current request.

    Returns:
        A single RFC 8288 link: ``<https://…?cursor=X&limit=N>; rel="next"``
    """
    base_url = str(request.url).split("?")[0]
    existing_params = dict(request.query_params)
    q = {**existing_params, "cursor": next_cursor, "limit": str(limit)}
    next_url = f"{base_url}?{urlencode(q)}"
    return f'<{next_url}>; rel="next"'


def paginate_list(items: list[T], page: int, per_page: int) -> tuple[list[T], int]:
    """Slice a list to the requested page window.

    Used for endpoints where the backing service fetches all matching rows (e.g.
    after an in-memory label filter) and the route layer applies pagination.

    Args:
        items:    Full result list from the service layer.
        page:     1-indexed page number requested by the client.
        per_page: Number of items per page.

    Returns:
        A tuple of (page_slice, total) where ``total`` is ``len(items)`` before
        slicing and ``page_slice`` is the subset for this page.
    """
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], total
