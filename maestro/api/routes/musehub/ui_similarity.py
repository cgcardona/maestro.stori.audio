"""Muse Hub musical similarity page.

Serves the ``/{owner}/{repo_slug}/similarity/{base}...{head}`` UI page that
visualises the 10-dimension musical similarity vector between two Muse refs.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/similarity/{refs}
    refs encodes ``base...head`` (same convention as the compare page).
    HTML (default) → interactive similarity report with server-side SVG radar chart.
    JSON (``?format=json`` or ``Accept: application/json``)
         → raw :class:`~maestro.models.musehub_analysis.RefSimilarityResponse`.

Why a dedicated page instead of reusing compare:
  The compare page shows *divergence* (how much changed). This page shows
  *similarity* (how musically alike two refs are) — an inverted framing that
  is more useful when evaluating whether a variation stays true to a reference.
  The 10-dimension spider chart with base=solid and perfect=dashed allows a
  producer to immediately see which musical axes fell short of perfect similarity.

SSR approach:
  The radar SVG is generated server-side in Python (using stdlib ``math``) and
  embedded directly in the Jinja2 template. No client-side JS chart library is
  required — HTMX handles any dynamic interactions.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.models.musehub_analysis import RefSimilarityDimensions
from maestro.services import musehub_analysis, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# 10 musical dimensions in radar order — (model_attr, display_label)
_SIMILARITY_DIMENSIONS: list[tuple[str, str]] = [
    ("pitch_distribution", "Pitch"),
    ("rhythm_pattern", "Rhythm"),
    ("tempo", "Tempo"),
    ("dynamics", "Dynamics"),
    ("harmonic_content", "Harmony"),
    ("form", "Form"),
    ("instrument_blend", "Blend"),
    ("groove", "Groove"),
    ("contour", "Contour"),
    ("emotion", "Emotion"),
]


def _interpret_similarity_badge(overall: float) -> dict[str, str]:
    """Return label/color/bg for the overall similarity score badge."""
    if overall >= 0.90:
        return {"label": "Nearly Identical", "color": "#3fb950", "bg": "#0d2a12"}
    if overall >= 0.75:
        return {"label": "Very Similar", "color": "#58a6ff", "bg": "#0d1d3a"}
    if overall >= 0.60:
        return {"label": "Moderately Similar", "color": "#f0883e", "bg": "#341a00"}
    if overall >= 0.45:
        return {"label": "Somewhat Different", "color": "#ffa657", "bg": "#2d1c00"}
    return {"label": "Very Different", "color": "#f85149", "bg": "#3d0000"}


def _build_similarity_radar_svg(dims: RefSimilarityDimensions) -> str:
    """Generate a dual-polygon radar SVG (score vs. perfect) server-side.

    The similarity scores are rendered as a filled blue polygon; the perfect
    (1.0) reference ring is a dashed orange stroke.  Both polygons share the
    same 10-axis grid so producers can see at a glance where similarity drops.
    """
    cx, cy, r = 190, 190, 150
    n = len(_SIMILARITY_DIMENSIONS)
    scores = [getattr(dims, attr) for attr, _ in _SIMILARITY_DIMENSIONS]

    def _angle(i: int) -> float:
        return (i / n) * 2 * math.pi - math.pi / 2

    def _pt(score: float, i: int) -> tuple[float, float]:
        a = _angle(i)
        return (cx + score * r * math.cos(a), cy + score * r * math.sin(a))

    # Grid rings at 0.25 / 0.5 / 0.75 / 1.0
    grid_parts: list[str] = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + frac * r * math.cos(_angle(i)):.1f},"
            f"{cy + frac * r * math.sin(_angle(i)):.1f}"
            for i in range(n)
        )
        grid_parts.append(
            f'<polygon points="{pts}" fill="none" stroke="#21262d" stroke-width="1"/>'
        )

    # Axis spokes
    spoke_parts: list[str] = []
    for i in range(n):
        ex = cx + r * math.cos(_angle(i))
        ey = cy + r * math.sin(_angle(i))
        spoke_parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}"'
            f' stroke="#30363d" stroke-width="1"/>'
        )

    # Axis labels
    label_parts: list[str] = []
    for i, (_, label) in enumerate(_SIMILARITY_DIMENSIONS):
        lx = cx + (r + 26) * math.cos(_angle(i))
        ly = cy + (r + 26) * math.sin(_angle(i))
        label_parts.append(
            f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="middle"'
            f' font-size="11" fill="#8b949e" font-family="system-ui">{label}</text>'
        )

    # Score polygon (actual similarity values)
    score_pts_list = [_pt(s, i) for i, s in enumerate(scores)]
    score_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in score_pts_list)

    # Perfect ring (1.0 reference)
    perfect_pts_list = [_pt(1.0, i) for i in range(n)]
    perfect_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in perfect_pts_list)

    # Vertex dots on score polygon
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#58a6ff"'
        f' stroke="#0d1117" stroke-width="2"/>'
        for x, y in score_pts_list
    )

    return (
        '<svg viewBox="0 0 380 380" xmlns="http://www.w3.org/2000/svg"'
        ' style="width:100%;max-width:380px;display:block;margin:0 auto"'
        ' role="img" aria-label="10-axis musical similarity radar">'
        + "".join(grid_parts)
        + "".join(spoke_parts)
        + f'<polygon points="{perfect_poly}" fill="none"'
        f' stroke="#f0883e" stroke-width="2" stroke-dasharray="5,3"/>'
        + f'<polygon points="{score_poly}" fill="rgba(88,166,255,0.12)"'
        f' stroke="#58a6ff" stroke-width="2"/>'
        + dots
        + "".join(label_parts)
        + "</svg>"
    )


async def _resolve_repo(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[str, str]:
    """Resolve owner+slug to (repo_id, base_url); raise 404 if not found."""
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), f"/musehub/ui/{owner}/{repo_slug}"


@router.get(
    "/{owner}/{repo_slug}/similarity/{refs}",
    summary="Muse Hub musical similarity score page",
)
async def similarity_page(
    request: Request,
    owner: str,
    repo_slug: str,
    refs: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the musical similarity report between two Muse refs.

    ``refs`` encodes the two refs as ``base...head``, matching the URL
    convention used by the compare page. The 10-dimension radar chart is
    generated server-side: the score polygon is solid blue; the perfect-
    similarity (1.0) ring is a dashed orange overlay — the gap shows where
    similarity falls short.

    Content negotiation:
    - HTML (default): SSR Jinja2 template with embedded SVG radar.
    - JSON (``Accept: application/json`` or ``?format=json``):
      returns the full :class:`~maestro.models.musehub_analysis.RefSimilarityResponse`.

    Returns 404 when:
    - The repo owner/slug is unknown.
    - The ``refs`` value does not contain the ``...`` separator.

    Agent use case: call with ``?format=json`` to obtain a machine-readable
    similarity vector before deciding whether to generate additional variation
    material. An ``overall_similarity`` below 0.75 signals that the two refs
    have diverged significantly and a merge should be reviewed carefully.
    """
    if "..." not in refs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Invalid similarity spec '{refs}' — expected format: base...head",
        )
    base_ref, head_ref = refs.split("...", 1)
    if not base_ref or not head_ref:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Both base and head refs must be non-empty",
        )

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    similarity = musehub_analysis.compute_ref_similarity(
        repo_id=repo_id, base_ref=base_ref, compare_ref=head_ref
    )

    # Pre-compute server-side SVG and badge so the template is purely declarative.
    radar_svg = _build_similarity_radar_svg(similarity.dimensions)
    badge = _interpret_similarity_badge(similarity.overall_similarity)
    overall_pct = round(similarity.overall_similarity * 100)

    # Build per-dimension rows for the breakdown table
    dim_rows: list[dict[str, object]] = []
    for attr, label in _SIMILARITY_DIMENSIONS:
        score: float = getattr(similarity.dimensions, attr)
        pct = round(score * 100)
        bar_color = (
            "#3fb950" if pct >= 90
            else "#58a6ff" if pct >= 75
            else "#f0883e" if pct >= 60
            else "#f85149"
        )
        dim_rows.append({"label": label, "pct": pct, "bar_color": bar_color, "score": score})

    diff_url = f"{base_url}/compare/{base_ref}...{head_ref}"
    create_pr_url = f"{base_url}/pulls/new?base={base_ref}&head={head_ref}"

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "refs": refs,
        "base_url": base_url,
        "current_page": "similarity",
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "similarity", "url": ""},
            {"label": f"{base_ref}...{head_ref}", "url": ""},
        ],
        # SSR data
        "radar_svg": radar_svg,
        "badge": badge,
        "overall_pct": overall_pct,
        "interpretation": similarity.interpretation,
        "dim_rows": dim_rows,
        "diff_url": diff_url,
        "create_pr_url": create_pr_url,
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/similarity.html",
        context=context,
        templates=templates,
        json_data=similarity,
        format_param=format,
    )
