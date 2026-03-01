"""Muse Hub Analysis API — agent-friendly structured JSON for all musical dimensions.

Endpoint summary:
  GET /musehub/repos/{repo_id}/analysis/{ref}                       — all 13 dimensions
  GET /musehub/repos/{repo_id}/analysis/{ref}/emotion-map           — emotion map (issue #227)
  GET /musehub/repos/{repo_id}/analysis/{ref}/emotion-diff?base=X   — 8-axis emotion diff (issue #420)
  GET /musehub/repos/{repo_id}/analysis/{ref}/dynamics/page         — per-track dynamics page
  GET /musehub/repos/{repo_id}/analysis/{ref}/{dimension}           — one dimension

Supported dimensions (13):
  harmony, dynamics, motifs, form, groove, emotion, chord-map, contour,
  key, tempo, meter, similarity, divergence

Query params (both endpoints):
  ?track=<instrument>   — restrict analysis to a named instrument track
  ?section=<label>      — restrict analysis to a named musical section (e.g. chorus)

Cache semantics:
  Responses include ETag (MD5 of dimension + ref) and Last-Modified headers.
  Agents may use these to avoid re-fetching unchanged analysis.

Auth: all endpoints require a valid JWT Bearer token (inherited from the
musehub router-level dependency).  No business logic lives here — all
analysis is delegated to :mod:`maestro.services.musehub_analysis`.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.models.musehub_analysis import (
    ALL_DIMENSIONS,
    AggregateAnalysisResponse,
    AnalysisResponse,
    DynamicsPageData,
    EmotionDiffResponse,
    EmotionMapResponse,
    HarmonyAnalysisResponse,
    RecallResponse,
)
from maestro.services import musehub_analysis, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

_LAST_MODIFIED = datetime(2026, 1, 1, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _etag(repo_id: str, ref: str, dimension: str) -> str:
    """Derive a stable ETag for a dimension+ref combination."""
    raw = f"{repo_id}:{ref}:{dimension}"
    return f'"{hashlib.md5(raw.encode()).hexdigest()}"'  # noqa: S324 — non-crypto use


@router.get(
    "/repos/{repo_id}/analysis/{ref}",
    response_model=AggregateAnalysisResponse,
    operation_id="getAnalysis",
    summary="Aggregate analysis — all 13 musical dimensions for a ref",
    description=(
        "Returns structured JSON for all 13 musical dimensions of a Muse commit ref "
        "in a single response.  Agents that need a full musical picture should prefer "
        "this endpoint over 13 sequential per-dimension requests."
    ),
)
async def get_aggregate_analysis(
    repo_id: str,
    ref: str,
    response: Response,
    track: str | None = Query(None, description="Instrument track filter, e.g. 'bass', 'keys'"),
    section: str | None = Query(None, description="Section filter, e.g. 'chorus', 'verse_1'"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> AggregateAnalysisResponse:
    """Return all 13 dimension analyses for a Muse repo ref.

    The response envelope carries ``computed_at``, ``ref``, and per-dimension
    :class:`~maestro.models.musehub_analysis.AnalysisResponse` entries.
    Use ``?track=`` and ``?section=`` to narrow analysis to a specific instrument
    or musical section.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = musehub_analysis.compute_aggregate_analysis(
        repo_id=repo_id,
        ref=ref,
        track=track,
        section=section,
    )

    etag = _etag(repo_id, ref, "aggregate")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result


@router.get(
    "/repos/{repo_id}/analysis/{ref}/emotion-map",
    response_model=EmotionMapResponse,
    summary="Emotion map — energy/valence/tension/darkness across time and commits",
    description=(
        "Returns a full emotion map for a Muse repo ref, combining:\n"
        "- **Per-beat evolution**: how energy, valence, tension, and darkness "
        "change beat-by-beat within this ref.\n"
        "- **Cross-commit trajectory**: aggregated emotion vectors for the 5 most "
        "recent ancestor commits plus HEAD, enabling cross-version comparison.\n"
        "- **Drift distances**: Euclidean distance in emotion space between "
        "consecutive commits, with the dominant-change axis identified.\n"
        "- **Narrative**: auto-generated text describing the emotional journey.\n"
        "- **Source**: whether emotion data is explicit (tags), inferred, or mixed.\n\n"
        "Use ``?track=`` and ``?section=`` to restrict analysis to a specific "
        "instrument or musical section."
    ),
)
async def get_emotion_map(
    repo_id: str,
    ref: str,
    response: Response,
    track: str | None = Query(None, description="Instrument track filter, e.g. 'bass', 'keys'"),
    section: str | None = Query(None, description="Section filter, e.g. 'chorus', 'verse_1'"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> EmotionMapResponse:
    """Return the full emotion map for a Muse repo ref.

    The response combines intra-ref per-beat evolution, cross-commit trajectory,
    drift distances, narrative text, and source attribution — everything the
    MuseHub emotion map page needs in a single authenticated request.

    Emotion vectors use four normalised axes (all 0.0–1.0):
    - ``energy``   — compositional drive/activity level
    - ``valence``  — brightness/positivity (0=dark, 1=bright)
    - ``tension``  — harmonic and rhythmic tension
    - ``darkness`` — brooding/ominous quality (inversely correlated with valence)
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    result = musehub_analysis.compute_emotion_map(
        repo_id=repo_id,
        ref=ref,
        track=track,
        section=section,
    )

    etag = _etag(repo_id, ref, "emotion-map")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result


# NOTE: emotion-diff is registered HERE (before the generic {dimension} catch-all)
# because FastAPI matches routes in registration order — a literal segment like
# "emotion-diff" does NOT automatically take precedence over a path parameter.
@router.get(
    "/repos/{repo_id}/analysis/{ref}/emotion-diff",
    response_model=EmotionDiffResponse,
    operation_id="getEmotionDiff",
    summary="Emotion diff — 8-axis emotional radar comparing two Muse refs",
    description=(
        "Returns an 8-axis emotional diff between ``ref`` (head) and ``base`` (baseline). "
        "The eight axes are: valence, energy, tension, complexity, warmth, brightness, "
        "darkness, and playfulness — all normalised to [0, 1].\n\n"
        "``delta`` is the signed per-axis difference (head − base); positive means the "
        "head commit increased that emotional dimension.\n\n"
        "``interpretation`` provides a natural-language summary of the dominant shifts "
        "for human and agent readability.\n\n"
        "Maps to the ``muse emotion-diff`` CLI command and the PR detail emotion radar."
    ),
)
async def get_emotion_diff(
    repo_id: str,
    ref: str,
    response: Response,
    base: str = Query(
        ...,
        description="Base ref to compare against, e.g. 'main', 'main~1', or a commit SHA",
    ),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> EmotionDiffResponse:
    """Return an 8-axis emotional diff between two Muse commit refs.

    Compares the emotional character of ``ref`` (head) against ``base``,
    returning per-axis emotion vectors for each ref, their signed delta, and a
    natural-language interpretation of the dominant shifts.

    Requires authentication — emotion diff reveals musical context that may be
    private to the repo owner.

    The eight dimensions extend the four-axis emotion-map model with
    ``complexity``, ``warmth``, ``brightness``, and ``playfulness`` so the PR
    detail page can render a full radar chart without a separate request.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    result = musehub_analysis.compute_emotion_diff(
        repo_id=repo_id,
        head_ref=ref,
        base_ref=base,
    )

    etag = _etag(repo_id, f"{base}..{ref}", "emotion-diff")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result


@router.get(
    "/repos/{repo_id}/analysis/{ref}/recall",
    response_model=RecallResponse,
    operation_id="getAnalysisRecall",
    summary="Semantic recall — find commits similar to a natural-language query",
    description=(
        "Queries the musical feature vector store for commits semantically similar "
        "to a natural-language description.\n\n"
        "**Example:** ``?q=jazzy+chord+progression+with+swing+groove``\n\n"
        "Results are ranked by cosine similarity in the 128-dim musical feature "
        "embedding space.  Each match includes the commit ID, message, branch, "
        "similarity score (0–1), and the musical dimensions most responsible for "
        "the match.\n\n"
        "Use ``?limit=N`` to control how many results are returned (default 10, max 50). "
        "Authentication is required — private repos are never surfaced without a valid "
        "Bearer token."
    ),
)
async def get_analysis_recall(
    repo_id: str,
    ref: str,
    response: Response,
    q: str = Query(..., description="Natural-language query, e.g. 'jazzy chord progression with swing'"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results (1–50)"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> RecallResponse:
    """Return commits semantically matching a natural-language query.

    Embeds ``q`` into the 128-dim musical feature space and retrieves the
    ``limit`` most similar commits reachable from ``ref``.  Authentication is
    required unconditionally — the recall index may surface private content.

    The response is deterministic for a given (repo_id, ref, q) triple so
    agents receive consistent results across retries without hitting the
    vector store redundantly.

    Args:
        repo_id: Muse Hub repo UUID.
        ref:     Muse commit ref scoping the search.
        q:       Natural-language query string.
        limit:   Result count cap (1–50, default 10).
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    result = musehub_analysis.compute_recall(
        repo_id=repo_id,
        ref=ref,
        query=q,
        limit=limit,
    )

    etag = _etag(repo_id, ref, f"recall:{q}")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=30"
    return result


@router.get(
    "/repos/{repo_id}/analysis/{ref}/{dimension}",
    response_model=AnalysisResponse,
    operation_id="getAnalysisDimension",
    summary="Single-dimension analysis for a Muse ref",
    description=(
        "Returns structured JSON for one of the 13 supported musical dimensions. "
        "Supported dimensions: harmony, dynamics, motifs, form, groove, emotion, "
        "chord-map, contour, key, tempo, meter, similarity, divergence. "
        "Returns 404 for unknown dimension names."
    ),
)
async def get_dimension_analysis(
    repo_id: str,
    ref: str,
    dimension: str,
    response: Response,
    track: str | None = Query(None, description="Instrument track filter, e.g. 'bass', 'keys'"),
    section: str | None = Query(None, description="Section filter, e.g. 'chorus', 'verse_1'"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> AnalysisResponse:
    """Return analysis for one musical dimension of a Muse repo ref.

    The ``dimension`` path parameter must be one of the 13 supported values.
    Returns HTTP 404 for unknown dimension names so agents receive a clear
    signal rather than a generic 422 validation error.

    The ``data`` field in the response is the dimension-specific typed model
    (e.g. :class:`~maestro.models.musehub_analysis.HarmonyData` for ``harmony``).
    """
    if dimension not in ALL_DIMENSIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dimension {dimension!r}. Supported: {', '.join(ALL_DIMENSIONS)}",
        )

    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = musehub_analysis.compute_analysis_response(
        repo_id=repo_id,
        dimension=dimension,
        ref=ref,
        track=track,
        section=section,
    )

    etag = _etag(repo_id, ref, dimension)
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result


@router.get(
    "/repos/{repo_id}/analysis/{ref}/dynamics/page",
    response_model=DynamicsPageData,
    operation_id="getAnalysisDynamicsPage",
    summary="Per-track dynamics page data for the Dynamics Analysis page",
    description=(
        "Returns enriched per-track dynamic analysis: velocity profiles, arc "
        "classifications, peak velocity, velocity range, and cross-track loudness "
        "data.  Consumed by the Dynamics Analysis web page and by AI agents that "
        "need per-track dynamic context for orchestration decisions. "
        "Use ``?track=<name>`` to restrict to a single instrument track. "
        "Use ``?section=<label>`` to restrict to a musical section."
    ),
)
async def get_dynamics_page_data(
    repo_id: str,
    ref: str,
    response: Response,
    track: str | None = Query(None, description="Instrument track filter, e.g. 'bass', 'keys'"),
    section: str | None = Query(None, description="Section filter, e.g. 'chorus', 'verse_1'"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> DynamicsPageData:
    """Return per-track dynamics data for the Dynamics Analysis web page.

    Unlike the single-dimension ``dynamics`` endpoint (which returns aggregate
    metrics for the whole piece), this endpoint returns one
    :class:`~maestro.models.musehub_analysis.TrackDynamicsProfile` per active
    instrument track so the page can render individual velocity graphs and arc
    badges.

    Cache semantics match the other analysis endpoints: ETag is derived from
    ``repo_id``, ``ref``, and the ``"dynamics-page"`` sentinel.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = musehub_analysis.compute_dynamics_page_data(
        repo_id=repo_id,
        ref=ref,
        track=track,
        section=section,
    )

    etag = _etag(repo_id, ref, "dynamics-page")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result


# Dedicated harmony router — must be included BEFORE the main analysis router in
# __init__.py so this specific path takes priority over the generic /{dimension}
# catch-all route.  See: maestro/api/routes/musehub/__init__.py.
harmony_router = APIRouter()


@harmony_router.get(
    "/repos/{repo_id}/analysis/{ref}/harmony",
    response_model=HarmonyAnalysisResponse,
    operation_id="getAnalysisHarmony",
    summary="Harmonic analysis — Roman numerals, cadences, and modulations for a ref",
    description=(
        "Returns a Roman-numeral-centric harmonic analysis of a Muse commit ref. "
        "Maps to the ``muse harmony --ref {ref}`` CLI command.\n\n"
        "Unlike the generic ``/analysis/{ref}/harmony`` dimension (which returns "
        "raw chord symbols and a tension curve), this endpoint returns:\n\n"
        "- **key** and **mode**: detected tonal centre and scale type\n"
        "- **roman_numerals**: each chord event labelled with scale degree, root, "
        "quality, and tonal function (tonic / subdominant / dominant)\n"
        "- **cadences**: detected phrase-ending cadence types and their beat positions\n"
        "- **modulations**: key-area changes with from/to key and pivot chord\n"
        "- **harmonic_rhythm_bpm**: rate of chord changes in chords per minute\n\n"
        "Agents use this to compose harmonically coherent continuations that respect "
        "existing tonal narrative, cadence structure, and phrase boundaries. "
        "Use ``?track=<instrument>`` or ``?section=<label>`` to narrow the scope."
    ),
)
async def get_harmony_analysis(
    repo_id: str,
    ref: str,
    response: Response,
    track: str | None = Query(None, description="Instrument track filter, e.g. 'bass', 'keys'"),
    section: str | None = Query(None, description="Section filter, e.g. 'chorus', 'verse_1'"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> HarmonyAnalysisResponse:
    """Return dedicated harmonic analysis for a Muse repo ref.

    Provides a Roman-numeral view of the harmonic content — scale degrees,
    tonal functions, cadence positions, and detected modulations — structured
    for agent consumption.  Maps to ``muse harmony --ref {ref}``.

    Access control mirrors the other analysis endpoints: public repos are
    accessible without authentication; private repos require a valid JWT.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = musehub_analysis.compute_harmony_analysis(
        repo_id=repo_id,
        ref=ref,
        track=track,
        section=section,
    )

    etag = _etag(repo_id, ref, "harmony")
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = _LAST_MODIFIED
    response.headers["Cache-Control"] = "private, max-age=60"
    return result
