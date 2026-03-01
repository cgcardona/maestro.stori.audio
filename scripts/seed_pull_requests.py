"""Seed pull requests with full lifecycle coverage.

Inserts 8+ pull requests per existing seeded repo covering:
  - Open PRs (active, awaiting review)
  - Merged PRs (with merge commit references and timestamps)
  - Closed/rejected PRs (declined without merge)
  - Conflict scenarios (two branches editing the same measure range)
  - Cross-repo PRs (fork → upstream)

Lifecycle states:
  open    — PR submitted, awaiting review or merge
  merged  — branch merged; merge_commit_id and merged_at populated
  closed  — PR closed without merge (rejected, withdrawn, superseded)

Conflict scenarios are documented inline: two branches that edit overlapping
measure ranges produce a conflicted state that a human must resolve before
either can merge cleanly into main.

Prerequisites:
  - seed_musehub.py must have run first (repos and commits must exist)
  - Issue #452 (seed_commits) must have seeded commit history

Idempotent: checks existing PR count before inserting; pass --force to wipe
and re-insert all PR seed data.

Run inside the container:
  docker compose exec maestro python3 /app/scripts/seed_pull_requests.py
  docker compose exec maestro python3 /app/scripts/seed_pull_requests.py --force
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from maestro.config import settings
from maestro.db.musehub_models import (
    MusehubCommit,
    MusehubPRComment,
    MusehubPRReview,
    MusehubPullRequest,
    MusehubRepo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _now(days: int = 0, hours: int = 0) -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=days, hours=hours)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _uid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest()))


# ---------------------------------------------------------------------------
# Stable repo IDs — must match seed_musehub.py
# ---------------------------------------------------------------------------

REPO_NEO_SOUL = "repo-neo-soul-00000001"
REPO_MODAL_JAZZ = "repo-modal-jazz-000001"
REPO_AMBIENT = "repo-ambient-textures-1"
REPO_AFROBEAT = "repo-afrobeat-grooves-1"
REPO_MICROTONAL = "repo-microtonal-etudes1"
REPO_DRUM_MACHINE = "repo-drum-machine-00001"
REPO_CHANSON = "repo-chanson-minimale-1"
REPO_GRANULAR = "repo-granular-studies-1"
REPO_FUNK_SUITE = "repo-funk-suite-0000001"
REPO_JAZZ_TRIO = "repo-jazz-trio-0000001"
# Fork repos (cross-repo PRs — fork → upstream)
REPO_NEO_SOUL_FORK = "repo-neo-soul-fork-0001"
REPO_AMBIENT_FORK = "repo-ambient-fork-0001"

# Owner map — needed to set PR author correctly
REPO_OWNER: dict[str, str] = {
    REPO_NEO_SOUL: "gabriel",
    REPO_MODAL_JAZZ: "gabriel",
    REPO_AMBIENT: "sofia",
    REPO_AFROBEAT: "aaliya",
    REPO_MICROTONAL: "chen",
    REPO_DRUM_MACHINE: "fatou",
    REPO_CHANSON: "pierre",
    REPO_GRANULAR: "yuki",
    REPO_FUNK_SUITE: "marcus",
    REPO_JAZZ_TRIO: "marcus",
    REPO_NEO_SOUL_FORK: "marcus",
    REPO_AMBIENT_FORK: "yuki",
}

# Collaborator pairs: repo → list of reviewers who are not the owner
REPO_REVIEWERS: dict[str, list[str]] = {
    REPO_NEO_SOUL: ["marcus", "sofia", "aaliya"],
    REPO_MODAL_JAZZ: ["marcus", "sofia"],
    REPO_AMBIENT: ["yuki", "pierre", "gabriel"],
    REPO_AFROBEAT: ["fatou", "marcus", "gabriel"],
    REPO_MICROTONAL: ["sofia", "yuki"],
    REPO_DRUM_MACHINE: ["aaliya", "marcus"],
    REPO_CHANSON: ["sofia", "gabriel"],
    REPO_GRANULAR: ["sofia", "chen"],
    REPO_FUNK_SUITE: ["gabriel", "aaliya"],
    REPO_JAZZ_TRIO: ["gabriel", "sofia"],
    REPO_NEO_SOUL_FORK: ["gabriel"],
    REPO_AMBIENT_FORK: ["sofia"],
}

# All primary repos (non-fork) — full PR suite
PRIMARY_REPOS = [
    REPO_NEO_SOUL,
    REPO_MODAL_JAZZ,
    REPO_AMBIENT,
    REPO_AFROBEAT,
    REPO_MICROTONAL,
    REPO_DRUM_MACHINE,
    REPO_CHANSON,
    REPO_GRANULAR,
    REPO_FUNK_SUITE,
    REPO_JAZZ_TRIO,
]

# Fork repos — cross-repo PRs only
FORK_REPOS = [REPO_NEO_SOUL_FORK, REPO_AMBIENT_FORK]

# Upstream repo for each fork (cross-repo PR target)
FORK_UPSTREAM: dict[str, str] = {
    REPO_NEO_SOUL_FORK: REPO_NEO_SOUL,
    REPO_AMBIENT_FORK: REPO_AMBIENT,
}


# ---------------------------------------------------------------------------
# PR templates per repo — 8+ entries covering the full lifecycle
# ---------------------------------------------------------------------------
#
# Lifecycle distribution per primary repo (10 PRs each):
#   3 open    — actively being worked on or awaiting review
#   4 merged  — branch fully integrated into main
#   2 closed  — rejected, withdrawn, or superseded by another PR
#   1 conflict — two concurrent branches editing overlapping measure ranges
#                (represented as a closed PR noting the conflict)
#
# Cross-repo PRs (fork → upstream): 2 additional PRs per fork repo.
# ---------------------------------------------------------------------------

def _make_prs(
    repo_id: str,
    owner: str,
    reviewers: list[str],
    merge_commit_ids: list[str],
    days_base: int,
) -> list[dict[str, Any]]:
    """Build the full lifecycle PR list for a single repo.

    merge_commit_ids is taken from the repo's existing commit history so that
    merged PRs reference real commit SHAs — the model constraint allows any
    string, so we use seeded commit IDs from seed_musehub.py.

    days_base controls the temporal spread relative to now.
    """
    reviewer_a = reviewers[0] if reviewers else owner
    reviewer_b = reviewers[1] if len(reviewers) > 1 else reviewer_a
    mc = merge_commit_ids

    # Stable merge commit references — fall back to generated SHAs if commits
    # are fewer than expected.
    def _mc(idx: int) -> str | None:
        return mc[idx] if idx < len(mc) else None

    return [
        # ── OPEN PRs (3) ──────────────────────────────────────────────────
        dict(
            pr_id=_uid(f"pr2-{repo_id}-open-1"),
            repo_id=repo_id,
            title="Feat: add counter-melody layer to verse sections",
            body=(
                "## Changes\n"
                "Adds a secondary melodic voice in the upper register (measures 1–8, 17–24).\n\n"
                "## Musical Analysis\n"
                "The counter-melody creates harmonic tension against the main theme, "
                "raising the Tension metric by +0.08 and Complexity by +0.05.\n\n"
                "## Review notes\n"
                "Please check voice-leading in bars 5–6 — the major 7th leap may be too angular."
            ),
            state="open",
            from_branch="feat/counter-melody-verse",
            to_branch="main",
            author=owner,
            created_at=_now(days=days_base + 6),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-open-2"),
            repo_id=repo_id,
            title="Experiment: alternate bridge harmony (tritone substitution)",
            body=(
                "## Summary\n"
                "Replaces the ii-V-I cadence in the bridge with a tritone substitution "
                "(bII7 → I). Adds chromatic colour without disrupting the groove.\n\n"
                "## A/B comparison\n"
                "Original: Dm7 → G7 → Cmaj7\n"
                "Proposed: Db7 → Cmaj7\n\n"
                "## Status\n"
                "Awaiting feedback from collaborators before committing to the change."
            ),
            state="open",
            from_branch="experiment/bridge-tritone-sub",
            to_branch="main",
            author=reviewer_a,
            created_at=_now(days=days_base + 3),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-open-3"),
            repo_id=repo_id,
            title="Feat: dynamic automation — swell into final chorus",
            body=(
                "## Overview\n"
                "Adds volume and filter automation curves across the 4-bar pre-chorus "
                "(measures 29–32) to build energy into the final chorus.\n\n"
                "## Details\n"
                "- Main pad: +6dB over 4 bars (linear ramp)\n"
                "- Hi-pass filter: 200Hz → 20Hz over same range\n"
                "- Reverb send: +3dB on downbeat of bar 33\n\n"
                "## Test\n"
                "Exported test render attached. The swell is audible but not aggressive."
            ),
            state="open",
            from_branch="feat/dynamic-swell-final-chorus",
            to_branch="main",
            author=owner,
            created_at=_now(days=days_base + 1),
        ),
        # ── MERGED PRs (4) ────────────────────────────────────────────────
        dict(
            pr_id=_uid(f"pr2-{repo_id}-merged-1"),
            repo_id=repo_id,
            title="Refactor: humanize all MIDI timing (±12ms variance)",
            body=(
                "## Changes\n"
                "Applied `muse humanize --natural` to all tracks. "
                "Groove score improved from 0.71 to 0.83.\n\n"
                "## Tracks affected\n"
                "- drums: ±12ms on hits, ±8ms on hats\n"
                "- bass: ±6ms per note\n"
                "- keys: ±4ms per chord\n\n"
                "## Verified\n"
                "Full export A/B comparison confirmed improvement in perceived groove."
            ),
            state="merged",
            from_branch="fix/humanize-midi-timing",
            to_branch="main",
            merge_commit_id=_mc(0),
            merged_at=_now(days=days_base + 14),
            author=owner,
            created_at=_now(days=days_base + 16),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-merged-2"),
            repo_id=repo_id,
            title="Fix: resolve voice-leading errors (parallel 5ths bars 7–8)",
            body=(
                "## Problem\n"
                "Parallel 5ths between soprano and bass in bars 7–8 violate classical "
                "voice-leading rules and produce a hollow, unintentional sound.\n\n"
                "## Fix\n"
                "Moved soprano from G4 to B4 on beat 3 of bar 7, introducing a 3rd "
                "above the alto line and eliminating the parallel motion.\n\n"
                "## Validation\n"
                "Voice-leading analysis report: 0 parallel 5ths, 0 parallel octaves."
            ),
            state="merged",
            from_branch="fix/voice-leading-parallel-fifths",
            to_branch="main",
            merge_commit_id=_mc(1),
            merged_at=_now(days=days_base + 20),
            author=reviewer_a,
            created_at=_now(days=days_base + 22),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-merged-3"),
            repo_id=repo_id,
            title="Feat: add breakdown section (bass + drums only, 4 bars)",
            body=(
                "## Motivation\n"
                "The arrangement transitions directly from the second chorus into the "
                "outro without a moment of release. A minimal breakdown restores energy "
                "contrast before the final section.\n\n"
                "## Implementation\n"
                "Added 4-bar section after measure 48:\n"
                "- All instruments muted except bass and kick\n"
                "- Bass plays root + 5th alternating pattern\n"
                "- Kick on all four beats (half-time feel)\n\n"
                "## Energy curve\n"
                "Peak energy drops from 0.91 to 0.42 in the breakdown, then rises to "
                "0.95 at the final chorus downbeat."
            ),
            state="merged",
            from_branch="feat/breakdown-section-pre-outro",
            to_branch="main",
            merge_commit_id=_mc(2),
            merged_at=_now(days=days_base + 30),
            author=owner,
            created_at=_now(days=days_base + 32),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-merged-4"),
            repo_id=repo_id,
            title="Fix: remove accidental octave doubling in chorus voicing",
            body=(
                "## Issue\n"
                "Rhodes and strings both play the root in octave unison during the "
                "chorus (bars 33–40). The doubling thins out the mid-range and causes "
                "phase cancellation on mono playback.\n\n"
                "## Fix\n"
                "Transposed strings up a major 3rd — now playing a 10th above bass, "
                "creating a richer spread voicing.\n\n"
                "## Result\n"
                "Stereo width score: 0.68 → 0.74. Mono compatibility confirmed."
            ),
            state="merged",
            from_branch="fix/chorus-octave-doubling",
            to_branch="main",
            merge_commit_id=_mc(3) if len(mc) > 3 else _mc(0),
            merged_at=_now(days=days_base + 40),
            author=reviewer_b if reviewer_b != owner else reviewer_a,
            created_at=_now(days=days_base + 42),
        ),
        # ── CLOSED/REJECTED PRs (2) ───────────────────────────────────────
        dict(
            pr_id=_uid(f"pr2-{repo_id}-closed-1"),
            repo_id=repo_id,
            title="Experiment: half-time feel for entire track",
            body=(
                "## Concept\n"
                "Proposed converting the entire arrangement to a half-time feel "
                "(kick on beats 1 and 3 only, snare on beat 3).\n\n"
                "## Outcome\n"
                "After review, the consensus was that the half-time feel loses the "
                "rhythmic momentum that defines the track's character. "
                "The change is too drastic.\n\n"
                "## Decision\n"
                "Closing without merge. A more surgical application (breakdown only) "
                "will be explored in a separate PR."
            ),
            state="closed",
            from_branch="experiment/half-time-full-track",
            to_branch="main",
            author=reviewer_a,
            created_at=_now(days=days_base + 50),
        ),
        dict(
            pr_id=_uid(f"pr2-{repo_id}-closed-2"),
            repo_id=repo_id,
            title="Feat: add rap vocal layer (rejected — genre scope)",
            body=(
                "## Proposal\n"
                "Adds a spoken-word / rap vocal layer over the second verse. "
                "Sample rhythm: 16th-note triplet flow.\n\n"
                "## Review feedback\n"
                "The addition of a rap layer changes the core genre identity of the "
                "composition. The project description and tags do not include hip-hop "
                "or spoken word. This feature would need a fork or a separate project.\n\n"
                "## Decision\n"
                "Rejected by owner. Closing. Contributor encouraged to fork and "
                "explore in their own namespace."
            ),
            state="closed",
            from_branch="feat/rap-vocal-overlay",
            to_branch="main",
            author=reviewer_b if reviewer_b != owner else reviewer_a,
            created_at=_now(days=days_base + 55),
        ),
        # ── CONFLICT SCENARIO ─────────────────────────────────────────────
        #
        # Two branches both edit measures 25–32 (the bridge/transition zone).
        # Branch A adds a string countermelody; Branch B rewrites the chord
        # changes. Both modify the same measure range — merging one will
        # require manual conflict resolution before the other can land.
        #
        # Here we model the conflicted branch as a separate closed PR with a
        # clear conflict explanation. The "winning" change was already merged
        # (merged-3 above). The conflicting PR is closed with a note to rebase.
        dict(
            pr_id=_uid(f"pr2-{repo_id}-conflict-1"),
            repo_id=repo_id,
            title="Feat: rewrite bridge chord changes (CONFLICT — bars 25–32)",
            body=(
                "## Overview\n"
                "Proposed complete reharmonisation of the bridge (bars 25–32):\n"
                "- Original: I → IV → V → I\n"
                "- Proposed: I → bVII → IV → bVII → I (Mixolydian cadence)\n\n"
                "## Conflict\n"
                "This PR conflicts with `feat/breakdown-section-pre-outro` which also "
                "modifies bars 25–32 to insert the bass+drums breakdown. Both branches "
                "modify the same measure range and cannot be auto-merged.\n\n"
                "## Status\n"
                "Closed — the breakdown PR merged first (#merged-3). "
                "This PR must be rebased on the updated main and the chord rewrite "
                "adjusted to target bars 33–40 instead. Reopening as a follow-up."
            ),
            state="closed",
            from_branch="feat/bridge-mixolydian-reharmony",
            to_branch="main",
            author=reviewer_a,
            created_at=_now(days=days_base + 28),
        ),
    ]


def _make_cross_repo_prs(
    fork_repo_id: str,
    upstream_repo_id: str,
    fork_owner: str,
    merge_commit_ids: list[str],
) -> list[dict[str, Any]]:
    """Build cross-repo PRs from a fork toward its upstream.

    These represent the canonical 'fork → upstream' contribution flow:
    a contributor forks the repo, adds their own changes, and opens a PR
    targeting the upstream main branch.
    """

    def _mc(idx: int) -> str | None:
        return merge_commit_ids[idx] if idx < len(merge_commit_ids) else None

    return [
        dict(
            pr_id=_uid(f"pr2-cross-{fork_repo_id}-open"),
            repo_id=upstream_repo_id,
            title=f"Feat (fork/{fork_owner}): add extended intro variation",
            body=(
                "## Fork contribution\n"
                f"This PR comes from the fork `{fork_owner}/{upstream_repo_id}`. "
                "It proposes adding a 16-bar intro variation that establishes the "
                "harmonic language before the main theme enters.\n\n"
                "## Changes\n"
                "- New intro: 16 bars of sparse pad and bass drone\n"
                "- Main theme entry delayed to bar 17\n"
                "- Cross-fade from intro texture to full arrangement\n\n"
                "## Cross-repo note\n"
                f"Opened from fork `{fork_owner}` — all commits come from the fork's "
                "main branch at HEAD."
            ),
            state="open",
            from_branch=f"forks/{fork_owner}/feat/extended-intro",
            to_branch="main",
            author=fork_owner,
            created_at=_now(days=8),
        ),
        dict(
            pr_id=_uid(f"pr2-cross-{fork_repo_id}-merged"),
            repo_id=upstream_repo_id,
            title=f"Fix (fork/{fork_owner}): correct tempo marking in metadata",
            body=(
                "## Fork contribution\n"
                f"Fix contributed from fork `{fork_owner}/{upstream_repo_id}`.\n\n"
                "## Problem\n"
                "The `tempo_bpm` metadata field was set to 96 but the actual MIDI "
                "content runs at 92 BPM. This mismatch confuses playback sync.\n\n"
                "## Fix\n"
                "Updated tempo_bpm to 92 in repo metadata. "
                "Verified against MIDI clock ticks.\n\n"
                "## Cross-repo note\n"
                f"Merged from fork `{fork_owner}` into upstream main."
            ),
            state="merged",
            from_branch=f"forks/{fork_owner}/fix/tempo-metadata-correction",
            to_branch="main",
            merge_commit_id=_mc(0),
            merged_at=_now(days=12),
            author=fork_owner,
            created_at=_now(days=14),
        ),
    ]


# ---------------------------------------------------------------------------
# Review seeds — adds realistic review submissions to key PRs
# ---------------------------------------------------------------------------

def _make_reviews(pr_id: str, reviewer: str, state: str, body: str) -> dict[str, Any]:
    return dict(
        id=_uid(f"review-{pr_id}-{reviewer}"),
        pr_id=pr_id,
        reviewer_username=reviewer,
        state=state,
        body=body,
        submitted_at=_now(days=1) if state != "pending" else None,
        created_at=_now(days=2),
    )


# ---------------------------------------------------------------------------
# PR comment seeds — inline review comments on specific tracks/regions
# ---------------------------------------------------------------------------

def _make_pr_comment(
    pr_id: str,
    repo_id: str,
    author: str,
    body: str,
    target_type: str = "general",
    target_track: str | None = None,
    target_beat_start: float | None = None,
    target_beat_end: float | None = None,
    parent_comment_id: str | None = None,
) -> dict[str, Any]:
    return dict(
        comment_id=_uid(f"prcomment-{pr_id}-{author}-{body[:20]}"),
        pr_id=pr_id,
        repo_id=repo_id,
        author=author,
        body=body,
        target_type=target_type,
        target_track=target_track,
        target_beat_start=target_beat_start,
        target_beat_end=target_beat_end,
        target_note_pitch=None,
        parent_comment_id=parent_comment_id,
        created_at=_now(days=1),
    )


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------

async def seed(db: AsyncSession, force: bool = False) -> None:
    """Seed pull requests with full lifecycle coverage.

    Idempotent: skips if PRs already exist, unless --force is passed.
    Depends on seed_musehub.py having already populated repos and commits.
    """
    print("🌱 Seeding pull requests — full lifecycle dataset…")

    # Guard: check that repos exist (seed_musehub.py must have run first)
    result = await db.execute(text("SELECT COUNT(*) FROM musehub_repos"))
    repo_count = result.scalar() or 0
    if repo_count == 0:
        print("  ❌ No repos found — run seed_musehub.py first.")
        return

    # Idempotency check — count PRs created by this script (use stable prefix)
    result = await db.execute(
        text("SELECT COUNT(*) FROM musehub_pull_requests WHERE pr_id LIKE 'pr2-%'")
    )
    existing_pr_count = result.scalar() or 0

    if existing_pr_count > 0 and not force:
        print(
            f"  ⚠️  {existing_pr_count} seeded PR(s) already exist — skipping. "
            "Pass --force to wipe and reseed."
        )
        return

    if existing_pr_count > 0 and force:
        print("  🗑  --force: clearing previously seeded PRs (pr2-* prefix)…")
        await db.execute(
            text("DELETE FROM musehub_pull_requests WHERE pr_id LIKE 'pr2-%'")
        )
        await db.flush()

    # Fetch the first few commit IDs per repo to use as merge commit references.
    # We take early commits (not the latest HEAD) to simulate realistic merge
    # points that predate the current branch tip.
    commit_ids_by_repo: dict[str, list[str]] = {}
    for repo_id in PRIMARY_REPOS + FORK_REPOS:
        res = await db.execute(
            select(MusehubCommit.commit_id)
            .where(MusehubCommit.repo_id == repo_id)
            .order_by(MusehubCommit.timestamp)
            .limit(8)
        )
        rows = res.scalars().all()
        commit_ids_by_repo[repo_id] = list(rows)

    # Fetch days_ago per repo (used to spread PR timestamps)
    repo_days: dict[str, int] = {}
    res = await db.execute(select(MusehubRepo.repo_id, MusehubRepo.created_at))
    for row in res.all():
        rid, created_at = row
        delta = datetime.now(tz=UTC) - (
            created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        )
        repo_days[rid] = max(0, delta.days)

    # ── Primary repos: 10 PRs each ────────────────────────────────────────
    total_prs = 0
    all_reviews: list[dict[str, Any]] = []
    all_comments: list[dict[str, Any]] = []

    for repo_id in PRIMARY_REPOS:
        owner = REPO_OWNER.get(repo_id, "gabriel")
        reviewers = REPO_REVIEWERS.get(repo_id, ["sofia"])
        mc = commit_ids_by_repo.get(repo_id, [])
        days_base = repo_days.get(repo_id, 60)

        prs = _make_prs(repo_id, owner, reviewers, mc, days_base)

        for pr in prs:
            db.add(MusehubPullRequest(**pr))
            total_prs += 1

        await db.flush()

        # Add reviews to the first open PR and the first merged PR
        open_pr_id = _uid(f"pr2-{repo_id}-open-1")
        merged_pr_id = _uid(f"pr2-{repo_id}-merged-1")
        reviewer_a = reviewers[0] if reviewers else owner
        reviewer_b = reviewers[1] if len(reviewers) > 1 else reviewer_a

        all_reviews.extend([
            _make_reviews(
                open_pr_id, reviewer_a, "changes_requested",
                "The counter-melody leap in bar 5 is too wide for the register. "
                "Consider stepping by 3rds instead of a 7th. Otherwise this is lovely."
            ),
            _make_reviews(
                open_pr_id, reviewer_b, "approved",
                "Approved from a harmonic standpoint. Voicing tension is intentional "
                "per the discussion — the resolution in bar 8 justifies the leap."
            ),
            _make_reviews(
                merged_pr_id, reviewer_a, "approved",
                "Humanization is well-calibrated. The ±12ms on drums is right on "
                "the edge of perceptible — just enough to breathe without dragging. ✅"
            ),
        ])

        # Inline PR comments on the open counter-melody PR
        comment_id = _uid(f"prcomment-{open_pr_id}-{reviewer_a}-The counter-melody")
        all_comments.extend([
            _make_pr_comment(
                open_pr_id, repo_id, reviewer_a,
                "The counter-melody leap in bar 5 (major 7th) feels too angular. "
                "The voice jumps up a major 7th where the harmonic context suggests "
                "a 3rd or 5th would feel more idiomatic.",
                target_type="region",
                target_track="counter_melody",
                target_beat_start=17.0,
                target_beat_end=21.0,
            ),
            _make_pr_comment(
                open_pr_id, repo_id, owner,
                "Intentional — the 7th creates a moment of suspension before the "
                "resolution in bar 8. I'll add a passing tone to soften it if needed.",
                target_type="region",
                target_track="counter_melody",
                target_beat_start=17.0,
                target_beat_end=21.0,
                parent_comment_id=comment_id,
            ),
            _make_pr_comment(
                open_pr_id, repo_id, reviewer_b,
                "Overall structure looks solid. The counter-melody enters at the "
                "right harmonic moment — bar 2 of the verse.",
                target_type="general",
            ),
        ])

    print(f"  ✅ Primary repo PRs: {total_prs} across {len(PRIMARY_REPOS)} repos")

    # ── Fork repos: cross-repo PRs ────────────────────────────────────────
    cross_pr_count = 0
    for fork_repo_id in FORK_REPOS:
        upstream_id = FORK_UPSTREAM[fork_repo_id]
        fork_owner = REPO_OWNER.get(fork_repo_id, "marcus")
        mc = commit_ids_by_repo.get(fork_repo_id, [])

        cross_prs = _make_cross_repo_prs(fork_repo_id, upstream_id, fork_owner, mc)
        for pr in cross_prs:
            db.add(MusehubPullRequest(**pr))
            cross_pr_count += 1

    await db.flush()
    print(f"  ✅ Cross-repo (fork → upstream) PRs: {cross_pr_count}")

    # ── Reviews ───────────────────────────────────────────────────────────
    for review in all_reviews:
        db.add(MusehubPRReview(**review))
    await db.flush()
    print(f"  ✅ PR reviews: {len(all_reviews)}")

    # ── Inline PR comments ────────────────────────────────────────────────
    for comment in all_comments:
        db.add(MusehubPRComment(**comment))
    await db.flush()
    print(f"  ✅ PR inline comments: {len(all_comments)}")

    await db.commit()

    # Summary
    print()
    _print_summary(total_prs, cross_pr_count, len(all_reviews), len(all_comments))


def _print_summary(prs: int, cross_prs: int, reviews: int, comments: int) -> None:
    BASE = "http://localhost:10001/musehub/ui"
    print("=" * 72)
    print("🎵  SEED PULL REQUESTS — COMPLETE")
    print("=" * 72)
    print(f"  PRs seeded   : {prs} primary + {cross_prs} cross-repo = {prs + cross_prs} total")
    print(f"  Reviews      : {reviews}")
    print(f"  PR comments  : {comments}")
    print()
    print("  Lifecycle coverage:")
    print("    open    — 3 per primary repo (awaiting review or merge)")
    print("    merged  — 4 per primary repo (with merge commit + merged_at)")
    print("    closed  — 2 per primary repo (rejected / withdrawn)")
    print("    conflict— 1 per primary repo (overlapping measure range, closed)")
    print()
    print("  Cross-repo PRs (fork → upstream):")
    print("    marcus/neo-soul-fork → gabriel/neo-soul-experiment")
    print("    yuki/ambient-fork   → sofia/ambient-textures-vol-1")
    print()
    print("  Sample PR URLs:")
    print(f"    {BASE}/gabriel/neo-soul-experiment/pulls")
    print(f"    {BASE}/sofia/ambient-textures-vol-1/pulls")
    print(f"    {BASE}/marcus/funk-suite-no-1/pulls")
    print("=" * 72)
    print("✅  Seed complete.")
    print("=" * 72)


async def main() -> None:
    """Entry point — connects to the DB and runs the seed."""
    force = "--force" in sys.argv
    db_url: str = settings.database_url or ""
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(  # type: ignore[call-overload]  # SQLAlchemy 2.x async stubs don't type class_= kwarg correctly
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as db:
        await seed(db, force=force)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
