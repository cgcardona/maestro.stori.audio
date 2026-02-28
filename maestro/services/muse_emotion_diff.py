"""Muse Emotion-Diff Engine — compare emotion vectors between two commits.

Answers: "How did the emotional character of a composition change between
two points in history?" An agent composing a new section uses this to detect
whether the current creative direction is drifting from the intended emotional
arc, and to decide whether to reinforce or contrast the mood.

Two sourcing strategies are supported:

1. **Explicit tags** — ``emotion:*`` tags attached via ``muse tag add``.
   When both commits carry an emotion tag, their vectors are looked up from
   the canonical :data:`EMOTION_VECTORS` table and compared directly.

2. **Inferred** — When one or both commits lack an emotion tag, the engine
   infers a vector from available musical metadata (tempo, commit metadata)
   stored in the :class:`~maestro.muse_cli.models.MuseCliCommit` row.
   Full MIDI-feature inference (mode, note density, velocity) is tracked as a
   follow-up; the current implementation uses tempo and tag-derived proxies.

Boundary rules
--------------
- Must NOT import StateStore, executor, MCP tools, or handlers.
- Must NOT import live streaming or SSE modules.
- May import ``muse_cli.{db, models}``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.models import MuseCliCommit, MuseCliTag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Emotion vector catalogue
# ---------------------------------------------------------------------------

#: Canonical 4-D emotion vectors keyed by ``emotion:<label>`` suffix.
#:
#: Dimensions:
#:   energy   — activity / rhythmic intensity (0.0 = still, 1.0 = frenetic)
#:   valence  — positivity / happiness (0.0 = dark/sad, 1.0 = bright/joyful)
#:   tension  — harmonic / rhythmic tension (0.0 = resolved, 1.0 = highly tense)
#:   darkness — heaviness / weight (0.0 = light, 1.0 = heavy/dark)
EMOTION_VECTORS: dict[str, tuple[float, float, float, float]] = {
    "joyful":      (0.80, 0.90, 0.20, 0.10),
    "melancholic": (0.30, 0.30, 0.40, 0.60),
    "anxious":     (0.60, 0.20, 0.80, 0.50),
    "cinematic":   (0.55, 0.50, 0.50, 0.40),
    "peaceful":    (0.20, 0.70, 0.10, 0.20),
    "dramatic":    (0.80, 0.30, 0.70, 0.60),
    "hopeful":     (0.60, 0.70, 0.30, 0.20),
    "tense":       (0.70, 0.20, 0.90, 0.50),
    "dark":        (0.40, 0.20, 0.50, 0.80),
    "euphoric":    (0.90, 0.90, 0.30, 0.10),
    "serene":      (0.25, 0.65, 0.15, 0.25),
    "epic":        (0.85, 0.55, 0.65, 0.45),
    "mysterious":  (0.35, 0.40, 0.60, 0.55),
    "aggressive":  (0.90, 0.25, 0.85, 0.70),
    "nostalgic":   (0.35, 0.50, 0.35, 0.50),
}

#: Ordered tuple of dimension names (index-stable for vector arithmetic).
EMOTION_DIMENSIONS: tuple[str, ...] = ("energy", "valence", "tension", "darkness")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionVector:
    """4-dimensional emotion representation in [0.0, 1.0] per dimension.

    Attributes:
        energy:   Activity / rhythmic intensity.
        valence:  Positivity / happiness.
        tension:  Harmonic / rhythmic tension.
        darkness: Heaviness / weight.
    """

    energy: float
    valence: float
    tension: float
    darkness: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return dimensions in :data:`EMOTION_DIMENSIONS` order."""
        return (self.energy, self.valence, self.tension, self.darkness)

    def drift_from(self, other: EmotionVector) -> float:
        """Euclidean distance between *self* and *other* in emotion space.

        Range: [0.0, 2.0] (maximum when all four dimensions flip from 0 to 1).
        A drift > 0.5 is considered a significant emotional shift.

        Args:
            other: The reference vector (commit A).

        Returns:
            Euclidean distance rounded to 4 decimal places.
        """
        return round(
            math.sqrt(sum((a - b) ** 2 for a, b in zip(self.as_tuple(), other.as_tuple()))),
            4,
        )


@dataclass(frozen=True)
class EmotionDimDelta:
    """Delta for a single emotion dimension between two commits.

    Attributes:
        dimension: Dimension name (one of :data:`EMOTION_DIMENSIONS`).
        value_a:   Value at commit A.
        value_b:   Value at commit B.
        delta:     ``value_b - value_a``; positive = increased, negative = decreased.
    """

    dimension: str
    value_a: float
    value_b: float
    delta: float


@dataclass(frozen=True)
class EmotionDiffResult:
    """Full emotion-diff report between two Muse commits.

    Attributes:
        commit_a:       Short (8-char) ref of the first commit.
        commit_b:       Short (8-char) ref of the second commit.
        source:         ``"explicit_tags"`` | ``"inferred"`` | ``"mixed"`` —
                        how the emotion vectors were obtained.
        label_a:        Emotion label for commit A (e.g. ``"melancholic"``),
                        or ``None`` when inferred without a known label.
        label_b:        Emotion label for commit B, or ``None``.
        vector_a:       Emotion vector at commit A, or ``None`` if unavailable.
        vector_b:       Emotion vector at commit B, or ``None`` if unavailable.
        dimensions:     Per-dimension deltas between the two vectors.
        drift:          Euclidean distance in emotion space.
        narrative:      Human-readable summary of the emotional shift.
        track:          Track filter applied (or ``None``).
        section:        Section filter applied (or ``None``).
    """

    commit_a: str
    commit_b: str
    source: str
    label_a: str | None
    label_b: str | None
    vector_a: EmotionVector | None
    vector_b: EmotionVector | None
    dimensions: tuple[EmotionDimDelta, ...]
    drift: float
    narrative: str
    track: str | None
    section: str | None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def vector_from_label(label: str) -> EmotionVector | None:
    """Look up the canonical :class:`EmotionVector` for an emotion label.

    The *label* should be the suffix of an ``emotion:*`` tag (e.g. ``"melancholic"``).
    Returns ``None`` for unknown labels so callers can fall back to inference.

    Args:
        label: Lowercase emotion label string.

    Returns:
        :class:`EmotionVector` if known, ``None`` otherwise.
    """
    entry = EMOTION_VECTORS.get(label.lower())
    if entry is None:
        return None
    energy, valence, tension, darkness = entry
    return EmotionVector(energy=energy, valence=valence, tension=tension, darkness=darkness)


def infer_vector_from_metadata(commit_metadata: dict[str, object] | None) -> EmotionVector:
    """Infer an emotion vector from available commit metadata.

    Uses ``tempo_bpm`` (from ``muse tempo --set``) as the primary signal:
    - Higher tempo → higher energy, lower darkness.
    - Absent metadata → returns a neutral midpoint vector.

    Full MIDI-feature inference (mode detection, note density, velocity
    analysis) is tracked as a follow-up and will supersede this stub when
    MIDI content is queryable at commit time.

    Args:
        commit_metadata: The ``commit_metadata`` JSON blob from
            :class:`~maestro.muse_cli.models.MuseCliCommit`, or ``None``.

    Returns:
        An :class:`EmotionVector` inferred from available signals.
    """
    if not commit_metadata:
        # Neutral midpoint — no musical signal available
        return EmotionVector(energy=0.50, valence=0.50, tension=0.50, darkness=0.50)

    tempo_bpm = commit_metadata.get("tempo_bpm")
    if tempo_bpm is None or not isinstance(tempo_bpm, (int, float)):
        return EmotionVector(energy=0.50, valence=0.50, tension=0.50, darkness=0.50)

    # Normalize tempo: 60 BPM = 0.0 energy, 180 BPM = 1.0 energy
    tempo_f = float(tempo_bpm)
    energy = min(1.0, max(0.0, (tempo_f - 60.0) / 120.0))
    # Fast tempo correlates slightly with higher valence (major-feel dance music)
    valence = min(1.0, max(0.0, 0.3 + energy * 0.4))
    # Fast tempo can increase rhythmic tension up to a point
    tension = min(1.0, max(0.0, 0.2 + energy * 0.5))
    # Darkness inversely correlates with energy at moderate tempos
    darkness = min(1.0, max(0.0, 0.7 - energy * 0.6))

    return EmotionVector(
        energy=round(energy, 4),
        valence=round(valence, 4),
        tension=round(tension, 4),
        darkness=round(darkness, 4),
    )


def compute_dimension_deltas(
    vec_a: EmotionVector,
    vec_b: EmotionVector,
) -> tuple[EmotionDimDelta, ...]:
    """Compute per-dimension deltas between two emotion vectors.

    Args:
        vec_a: Vector at commit A (baseline).
        vec_b: Vector at commit B (target).

    Returns:
        Tuple of :class:`EmotionDimDelta` in :data:`EMOTION_DIMENSIONS` order.
    """
    dims = zip(EMOTION_DIMENSIONS, vec_a.as_tuple(), vec_b.as_tuple())
    return tuple(
        EmotionDimDelta(
            dimension=dim,
            value_a=round(a, 4),
            value_b=round(b, 4),
            delta=round(b - a, 4),
        )
        for dim, a, b in dims
    )


def build_narrative(
    label_a: str | None,
    label_b: str | None,
    dimensions: tuple[EmotionDimDelta, ...],
    drift: float,
    source: str,
) -> str:
    """Produce a human-readable narrative of the emotional shift.

    The narrative describes the direction and magnitude of change using
    production-vocabulary language.  Agents use this to decide whether a
    compositional decision is reinforcing or subverting the intended arc.

    Args:
        label_a:    Emotion label at commit A (or ``None``).
        label_b:    Emotion label at commit B (or ``None``).
        dimensions: Per-dimension deltas from :func:`compute_dimension_deltas`.
        drift:      Euclidean drift distance.
        source:     Sourcing strategy (``"explicit_tags"`` | ``"inferred"`` | ``"mixed"``).

    Returns:
        Human-readable narrative string.
    """
    if drift < 0.05:
        magnitude = "minimal"
        verdict = "Emotional character unchanged."
    elif drift < 0.25:
        magnitude = "subtle"
        verdict = "Slight emotional shift."
    elif drift < 0.50:
        magnitude = "moderate"
        verdict = "Noticeable emotional change."
    elif drift < 0.80:
        magnitude = "significant"
        verdict = "Strong emotional shift — compositional direction changed."
    else:
        magnitude = "major"
        verdict = "Dramatic emotional departure — a fundamentally different mood."

    # Build label transition string
    if label_a and label_b:
        transition = f"{label_a} → {label_b}"
    elif label_a:
        transition = f"{label_a} → (inferred)"
    elif label_b:
        transition = f"(inferred) → {label_b}"
    else:
        transition = "(inferred) → (inferred)"

    # Dominant dimension change
    biggest = max(dimensions, key=lambda d: abs(d.delta))
    sign = "+" if biggest.delta > 0 else ""
    dim_note = f"+{biggest.dimension}" if biggest.delta > 0 else f"-{biggest.dimension}"
    if abs(biggest.delta) < 0.02:
        dim_note = "no dominant shift"

    source_note = " [inferred from metadata]" if source != "explicit_tags" else ""

    return (
        f"{verdict} {transition} (drift={drift:.3f}, {magnitude}, "
        f"dominant: {dim_note}){source_note}"
    )


# ---------------------------------------------------------------------------
# Async DB helpers
# ---------------------------------------------------------------------------


async def get_emotion_tag(
    session: AsyncSession,
    repo_id: str,
    commit_id: str,
) -> str | None:
    """Return the first ``emotion:*`` tag for *commit_id*, or ``None``.

    Args:
        session:   Open async DB session.
        repo_id:   Repository identifier.
        commit_id: Full 64-char commit hash.

    Returns:
        The label portion of the ``emotion:<label>`` tag (e.g. ``"melancholic"``),
        or ``None`` if no emotion tag is attached.
    """
    result = await session.execute(
        select(MuseCliTag.tag)
        .where(
            MuseCliTag.repo_id == repo_id,
            MuseCliTag.commit_id == commit_id,
            MuseCliTag.tag.like("emotion:%"),
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    # Strip "emotion:" prefix
    return row[len("emotion:"):]


async def resolve_commit_id(
    session: AsyncSession,
    repo_id: str,
    ref: str,
    branch: str,
) -> str | None:
    """Resolve a commit ref to a full commit ID.

    Supported refs:
    - Full 64-char hash — returned as-is (after existence check).
    - ``HEAD`` — resolves to the most recent commit on *branch*.
    - ``HEAD~N`` — walks N parents back from HEAD (e.g. ``HEAD~1``).
    - 8-char abbreviated hash — matches any commit ID with that prefix.

    Args:
        session:  Open async DB session.
        repo_id:  Repository identifier.
        ref:      Commit reference string.
        branch:   Current branch name (used for HEAD resolution).

    Returns:
        Full 64-char commit ID, or ``None`` if the ref cannot be resolved.
    """
    # ── HEAD~N shorthand ─────────────────────────────────────────────────
    head_tilde_n = 0
    lookup_ref = ref
    if ref.upper() == "HEAD" or ref.upper().startswith("HEAD~"):
        if ref.upper() == "HEAD":
            head_tilde_n = 0
        else:
            try:
                head_tilde_n = int(ref[5:])  # strip "HEAD~"
            except ValueError:
                return None
        lookup_ref = "HEAD"

    if lookup_ref.upper() == "HEAD":
        # Find HEAD commit for branch
        result = await session.execute(
            select(MuseCliCommit)
            .where(
                MuseCliCommit.repo_id == repo_id,
                MuseCliCommit.branch == branch,
            )
            .order_by(MuseCliCommit.committed_at.desc())
            .limit(1)
        )
        commit = result.scalar_one_or_none()
        if commit is None:
            return None
        # Walk N parents back
        for _ in range(head_tilde_n):
            if commit.parent_commit_id is None:
                return None
            parent = await session.get(MuseCliCommit, commit.parent_commit_id)
            if parent is None:
                return None
            commit = parent
        return commit.commit_id

    # ── Full 64-char hash ─────────────────────────────────────────────────
    if len(ref) == 64:
        commit = await session.get(MuseCliCommit, ref)
        return ref if commit is not None else None

    # ── Abbreviated hash (prefix match) ──────────────────────────────────
    result = await session.execute(
        select(MuseCliCommit.commit_id)
        .where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.commit_id.like(f"{ref}%"),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()  # type: ignore[return-value]  # SQLAlchemy scalar() -> Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_emotion_diff(
    session: AsyncSession,
    *,
    repo_id: str,
    commit_a: str,
    commit_b: str,
    branch: str,
    track: str | None = None,
    section: str | None = None,
) -> EmotionDiffResult:
    """Compute an emotion-diff between two Muse commits.

    Sourcing strategy (in priority order):
    1. Both commits have ``emotion:*`` tags → ``"explicit_tags"``.
    2. One has a tag, the other is inferred → ``"mixed"``.
    3. Neither has a tag → ``"inferred"`` from commit metadata.

    Args:
        session:  Open async DB session.
        repo_id:  Repository identifier (from ``.muse/repo.json``).
        commit_a: Commit reference for the baseline (e.g. ``"HEAD~1"``).
        commit_b: Commit reference for the target (e.g. ``"HEAD"``).
        branch:   Current branch name (used for HEAD resolution).
        track:    Optional track name filter (noted in result; full filtering
                  requires MIDI content access — tracked as follow-up).
        section:  Optional section name filter (same stub note as *track*).

    Returns:
        :class:`EmotionDiffResult` with vectors, per-dimension deltas,
        drift distance, and a human-readable narrative.

    Raises:
        ValueError: If *commit_a* or *commit_b* cannot be resolved to a
                    commit that exists in the database.
    """
    # ── Resolve commit refs ───────────────────────────────────────────────
    # Read branch from HEAD file if needed — callers should pass branch
    resolved_a = await resolve_commit_id(session, repo_id, commit_a, branch)
    if resolved_a is None:
        raise ValueError(
            f"Cannot resolve commit ref '{commit_a}' in repo '{repo_id}' "
            f"on branch '{branch}'."
        )
    resolved_b = await resolve_commit_id(session, repo_id, commit_b, branch)
    if resolved_b is None:
        raise ValueError(
            f"Cannot resolve commit ref '{commit_b}' in repo '{repo_id}' "
            f"on branch '{branch}'."
        )

    short_a = resolved_a[:8]
    short_b = resolved_b[:8]

    # ── Load commit rows for metadata ────────────────────────────────────
    row_a = await session.get(MuseCliCommit, resolved_a)
    row_b = await session.get(MuseCliCommit, resolved_b)

    # Both rows are guaranteed to exist because resolve_commit_id checked them
    meta_a: dict[str, object] | None = row_a.commit_metadata if row_a else None
    meta_b: dict[str, object] | None = row_b.commit_metadata if row_b else None

    # ── Read explicit emotion tags ────────────────────────────────────────
    label_a = await get_emotion_tag(session, repo_id, resolved_a)
    label_b = await get_emotion_tag(session, repo_id, resolved_b)

    # ── Resolve vectors ───────────────────────────────────────────────────
    vec_a: EmotionVector | None = None
    vec_b: EmotionVector | None = None

    if label_a:
        vec_a = vector_from_label(label_a)
    if label_b:
        vec_b = vector_from_label(label_b)

    # Fall back to inference for commits without explicit tags
    if vec_a is None:
        vec_a = infer_vector_from_metadata(meta_a)
    if vec_b is None:
        vec_b = infer_vector_from_metadata(meta_b)

    # ── Determine sourcing label ─────────────────────────────────────────
    if label_a and label_b:
        source = "explicit_tags"
    elif label_a or label_b:
        source = "mixed"
    else:
        source = "inferred"

    # ── Compute deltas and drift ─────────────────────────────────────────
    dimensions = compute_dimension_deltas(vec_a, vec_b)
    drift = vec_b.drift_from(vec_a)
    narrative = build_narrative(label_a, label_b, dimensions, drift, source)

    if track:
        logger.info("⚠️  --track %r: per-track emotion scoping not yet implemented", track)
    if section:
        logger.info(
            "⚠️  --section %r: section-scoped emotion analysis not yet implemented", section
        )

    logger.info(
        "✅ muse emotion-diff: %s → %s drift=%.4f source=%s",
        short_a,
        short_b,
        drift,
        source,
    )

    return EmotionDiffResult(
        commit_a=short_a,
        commit_b=short_b,
        source=source,
        label_a=label_a,
        label_b=label_b,
        vector_a=vec_a,
        vector_b=vec_b,
        dimensions=dimensions,
        drift=drift,
        narrative=narrative,
        track=track,
        section=section,
    )
