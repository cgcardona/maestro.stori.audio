"""Muse Hub emotion-diff UI page.

Serves the ``/{owner}/{repo_slug}/emotion-diff/{base}...{head}`` page that
visualises the 8-axis emotional shift between two Muse refs.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/emotion-diff/{refs}
    refs encodes ``base...head`` (same convention as the compare page).
    HTML (default) → interactive emotion-diff report with side-by-side
                     server-rendered SVG radar charts, delta bar chart,
                     and CSS timeline bars.
    JSON (``?format=json`` or ``Accept: application/json``)
         → raw :class:`~maestro.models.musehub_analysis.EmotionDiffResponse`.

Why a dedicated page instead of reusing the PR detail emotion widget:
  The PR detail page embeds the emotion radar as one panel among many. This
  page gives the full-screen emotion-diff view with per-ref 8D radar charts,
  a delta bar chart, a prose interpretation, and "Listen to comparison" buttons
  — features that do not fit in the PR detail sidebar.

SSR approach:
  Both 8D radar SVGs (base + head) are generated server-side in Python (using
  stdlib ``math``) and embedded directly in the Jinja2 template. The delta
  bar chart and any timeline bars are pure CSS/HTML — no client-side JS chart
  library required. HTMX handles any dynamic interactions.

Auto-discovered by the package ``__init__.py`` — do NOT edit that file.
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
from maestro.models.musehub_analysis import EmotionVector8D
from maestro.services import musehub_analysis, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# 8 emotional dimensions in radar order — (model_attr, display_label, description)
_EMOTION_DIMENSIONS: list[tuple[str, str, str]] = [
    ("valence",     "Valence",     "dark/negative → bright/positive"),
    ("energy",      "Energy",      "passive/still → active/driving"),
    ("tension",     "Tension",     "relaxed → tense/dissonant"),
    ("complexity",  "Complexity",  "sparse/simple → dense/complex"),
    ("warmth",      "Warmth",      "cold/sterile → warm/intimate"),
    ("brightness",  "Brightness",  "dark/dull → bright/shimmering"),
    ("darkness",    "Darkness",    "luminous → brooding/ominous"),
    ("playfulness", "Playfulness", "serious/solemn → playful/whimsical"),
]


def _build_emotion_radar_svg(vec: EmotionVector8D, color: str, ref_label: str) -> str:
    """Generate a single 8-axis emotion radar SVG server-side.

    A filled polygon at ``color`` opacity shows the absolute emotion vector;
    vertex dots mark each axis value. The ref label is centred inside the
    spider to identify which commit the chart represents.
    """
    cx, cy, r = 160, 160, 120
    n = len(_EMOTION_DIMENSIONS)
    scores = [getattr(vec, attr) for attr, _, _ in _EMOTION_DIMENSIONS]

    def _angle(i: int) -> float:
        return (i / n) * 2 * math.pi - math.pi / 2

    def _pt(score: float, i: int) -> tuple[float, float]:
        a = _angle(i)
        return (cx + score * r * math.cos(a), cy + score * r * math.sin(a))

    # Grid rings
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
    for i, (_, label, _) in enumerate(_EMOTION_DIMENSIONS):
        lx = cx + (r + 24) * math.cos(_angle(i))
        ly = cy + (r + 24) * math.sin(_angle(i))
        label_parts.append(
            f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="middle"'
            f' font-size="10" fill="#8b949e" font-family="system-ui">{label}</text>'
        )

    # Data polygon
    pts_list = [_pt(s, i) for i, s in enumerate(scores)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_list)

    # Vertex dots
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"'
        f' stroke="#0d1117" stroke-width="1.5"/>'
        for x, y in pts_list
    )

    # Short centre label
    short_label = ref_label[:8] + "…" if len(ref_label) > 10 else ref_label

    return (
        '<svg viewBox="0 0 320 320" xmlns="http://www.w3.org/2000/svg"'
        f' style="width:100%;max-width:320px;display:block;margin:0 auto" role="img"'
        f' aria-label="8-axis emotion radar for {ref_label}">'
        + "".join(grid_parts)
        + "".join(spoke_parts)
        + f'<polygon points="{poly}" fill="{color}20" stroke="{color}" stroke-width="2"/>'
        + dots
        + "".join(label_parts)
        + f'<text x="{cx}" y="{cy + 4}" text-anchor="middle"'
        f' font-size="10" fill="{color}" font-family="monospace">{short_label}</text>'
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
    "/{owner}/{repo_slug}/emotion-diff/{refs}",
    summary="Muse Hub emotion-diff page — 8-axis emotional shift between two refs",
)
async def emotion_diff_page(
    request: Request,
    owner: str,
    repo_slug: str,
    refs: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the 8-axis emotional diff page between two Muse refs.

    ``refs`` encodes the two refs as ``base...head``, matching the URL
    convention used by the compare and similarity pages. The page renders:

    - Side-by-side 8-dimension radar charts (server-side SVG, one per ref)
    - A delta bar chart per axis: green = increase, red = decrease
    - A prose interpretation of the dominant emotional shifts
    - "Listen to comparison" buttons for both refs

    Content negotiation:
    - HTML (default): SSR Jinja2 template with embedded SVGs.
    - JSON (``Accept: application/json`` or ``?format=json``):
      returns the raw :class:`~maestro.models.musehub_analysis.EmotionDiffResponse`.

    Returns 404 when:
    - The repo owner/slug is unknown.
    - The ``refs`` value does not contain the ``...`` separator.

    Agent use case: call with ``?format=json`` to obtain a machine-readable
    emotion-diff payload for programmatic analysis of emotional shifts between
    two commits — e.g. to decide whether a generative commit increased tension
    relative to the main branch without opening the browser.
    """
    if "..." not in refs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Invalid emotion-diff spec '{refs}' — expected format: base...head",
        )
    base_ref, head_ref = refs.split("...", 1)
    if not base_ref or not head_ref:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Both base and head refs must be non-empty",
        )

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    diff = musehub_analysis.compute_emotion_diff(
        repo_id=repo_id,
        head_ref=head_ref,
        base_ref=base_ref,
    )

    # Pre-compute server-side SVGs for both refs
    base_radar_svg = _build_emotion_radar_svg(diff.base_emotion, "#58a6ff", base_ref)
    head_radar_svg = _build_emotion_radar_svg(diff.head_emotion, "#f0883e", head_ref)

    # Build per-axis delta rows for the breakdown table
    delta_rows: list[dict[str, object]] = []
    for attr, label, _ in _EMOTION_DIMENSIONS:
        delta: float = getattr(diff.delta, attr)
        pct = round(delta * 100)
        abs_delta = abs(delta)
        if abs_delta < 0.04:
            color = "#8b949e"
            direction = "unchanged"
        elif delta > 0:
            color = "#3fb950"
            direction = "▲ increase"
        else:
            color = "#f85149"
            direction = "▼ decrease"
        bar_width_pct = min(50, round(abs(pct) / 2))
        bar_left_pct = 50 if delta >= 0 else (50 - bar_width_pct)
        sign = "+" if delta >= 0 else ""
        delta_rows.append({
            "label": label,
            "pct": pct,
            "sign": sign,
            "color": color,
            "direction": direction,
            "bar_width_pct": bar_width_pct,
            "bar_left_pct": bar_left_pct,
        })

    # Dimension descriptions for the axis key
    dim_descriptions = [
        {"label": label, "desc": desc}
        for _, label, desc in _EMOTION_DIMENSIONS
    ]

    listen_base_url = f"{base_url}/listen/{base_ref}"
    listen_head_url = f"{base_url}/listen/{head_ref}"
    compare_url = f"{base_url}/compare/{base_ref}...{head_ref}"

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "refs": refs,
        "base_url": base_url,
        "current_page": "emotion-diff",
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "emotion-diff", "url": ""},
            {"label": f"{base_ref}...{head_ref}", "url": ""},
        ],
        # SSR data
        "base_radar_svg": base_radar_svg,
        "head_radar_svg": head_radar_svg,
        "interpretation": diff.interpretation,
        "delta_rows": delta_rows,
        "dim_descriptions": dim_descriptions,
        "listen_base_url": listen_base_url,
        "listen_head_url": listen_head_url,
        "compare_url": compare_url,
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/emotion_diff.html",
        context=context,
        templates=templates,
        json_data=diff,
        format_param=format,
    )
