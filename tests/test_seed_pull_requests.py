"""Unit tests for scripts/seed_pull_requests.py.

Tests verify:
- PR generation logic produces the correct lifecycle distribution
- Stable ID generation is deterministic and collision-free
- Cross-repo PR targeting is correct (fork → upstream)
- Conflict scenario PRs are correctly represented as closed
- Merge commit references are correctly populated for merged PRs
- Review and comment generation helpers produce valid model dicts
- Idempotency guard behavior (via direct function inspection)

These are pure unit tests — no DB I/O, no async calls. The seed function's
async DB path is exercised via integration tests in the CI pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.seed_pull_requests import (
    FORK_REPOS,
    FORK_UPSTREAM,
    PRIMARY_REPOS,
    REPO_NEO_SOUL,
    REPO_NEO_SOUL_FORK,
    REPO_AMBIENT,
    REPO_AMBIENT_FORK,
    REPO_CHANSON,
    REPO_OWNER,
    REPO_REVIEWERS,
    _make_cross_repo_prs,
    _make_pr_comment,
    _make_prs,
    _make_reviews,
    _uid,
    _sha,
    _now,
)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

def test_uid_deterministic() -> None:
    """_uid must return the same UUID for the same seed string."""
    assert _uid("test-seed") == _uid("test-seed")


def test_uid_collision_free_for_different_seeds() -> None:
    """_uid must produce different values for different inputs."""
    assert _uid("repo-a-pr-1") != _uid("repo-a-pr-2")


def test_sha_deterministic() -> None:
    """_sha must return the same hex digest for the same input."""
    assert _sha("commit-abc") == _sha("commit-abc")


def test_now_returns_utc_datetime() -> None:
    """_now must return timezone-aware datetimes in UTC."""
    dt = _now()
    assert dt.tzinfo == timezone.utc


def test_now_offset_is_in_the_past() -> None:
    """_now(days=1) must be earlier than _now()."""
    assert _now(days=1) < _now()


# ---------------------------------------------------------------------------
# _make_prs — lifecycle distribution
# ---------------------------------------------------------------------------

def _fake_merge_commits(n: int = 8) -> list[str]:
    return [_sha(f"fake-commit-{i}") for i in range(n)]


def test_make_prs_returns_ten_entries() -> None:
    """_make_prs must return exactly 10 PRs per repo."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    assert len(prs) == 10


def test_make_prs_lifecycle_open_count() -> None:
    """_make_prs must include exactly 3 open PRs."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    open_prs = [p for p in prs if p["state"] == "open"]
    assert len(open_prs) == 3


def test_make_prs_lifecycle_merged_count() -> None:
    """_make_prs must include exactly 4 merged PRs."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    merged_prs = [p for p in prs if p["state"] == "merged"]
    assert len(merged_prs) == 4


def test_make_prs_lifecycle_closed_count() -> None:
    """_make_prs must include exactly 3 closed PRs (2 rejected + 1 conflict)."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    closed_prs = [p for p in prs if p["state"] == "closed"]
    assert len(closed_prs) == 3


def test_make_prs_merged_have_merge_commit_id() -> None:
    """Every merged PR must have a non-None merge_commit_id."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    for pr in prs:
        if pr["state"] == "merged":
            assert pr.get("merge_commit_id") is not None, (
                f"Merged PR '{pr['title']}' has no merge_commit_id"
            )


def test_make_prs_merged_have_merged_at() -> None:
    """Every merged PR must have a non-None merged_at timestamp."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    for pr in prs:
        if pr["state"] == "merged":
            assert pr.get("merged_at") is not None, (
                f"Merged PR '{pr['title']}' has no merged_at"
            )
            assert isinstance(pr["merged_at"], datetime)


def test_make_prs_open_have_no_merge_commit() -> None:
    """Open PRs must not have a merge_commit_id."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    for pr in prs:
        if pr["state"] == "open":
            assert pr.get("merge_commit_id") is None, (
                f"Open PR '{pr['title']}' should not have merge_commit_id"
            )


def test_make_prs_stable_ids_are_deterministic() -> None:
    """All PR IDs must be deterministic across two calls with the same inputs."""
    mc = _fake_merge_commits()
    prs_a = _make_prs(REPO_NEO_SOUL, "gabriel", ["marcus", "sofia"], mc, 60)
    prs_b = _make_prs(REPO_NEO_SOUL, "gabriel", ["marcus", "sofia"], mc, 60)
    ids_a = {p["pr_id"] for p in prs_a}
    ids_b = {p["pr_id"] for p in prs_b}
    assert ids_a == ids_b


def test_make_prs_all_ids_unique() -> None:
    """All 10 PR IDs within a repo must be distinct."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    ids = [p["pr_id"] for p in prs]
    assert len(ids) == len(set(ids)), "Duplicate PR IDs detected"


def test_make_prs_conflict_pr_is_closed() -> None:
    """The conflict scenario PR must have state=closed."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    conflict_prs = [p for p in prs if "CONFLICT" in p["title"]]
    assert len(conflict_prs) == 1
    assert conflict_prs[0]["state"] == "closed"


def test_make_prs_conflict_pr_body_mentions_conflict() -> None:
    """Conflict PR body must explain the measure-range overlap."""
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    conflict_pr = next(p for p in prs if "CONFLICT" in p["title"])
    assert "conflict" in conflict_pr["body"].lower()
    assert "25" in conflict_pr["body"]  # measure range 25-32 mentioned


def test_make_prs_all_have_required_fields() -> None:
    """Every PR dict must have all fields required by MusehubPullRequest."""
    required_fields = {
        "pr_id", "repo_id", "title", "body", "state",
        "from_branch", "to_branch", "author", "created_at",
    }
    prs = _make_prs(
        REPO_NEO_SOUL,
        owner="gabriel",
        reviewers=["marcus", "sofia"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=60,
    )
    for pr in prs:
        missing = required_fields - set(pr.keys())
        assert not missing, f"PR '{pr.get('title')}' missing fields: {missing}"


def test_make_prs_works_with_single_reviewer() -> None:
    """_make_prs must not crash when only one reviewer is provided."""
    prs = _make_prs(
        REPO_AMBIENT,
        owner="sofia",
        reviewers=["yuki"],
        merge_commit_ids=_fake_merge_commits(),
        days_base=45,
    )
    assert len(prs) == 10


def test_make_prs_works_with_empty_merge_commit_list() -> None:
    """_make_prs must not crash when no merge commit IDs are available.

    This handles the case where the repo was seeded without commits (e.g.
    seed_musehub.py ran but issue #452 seed_commits has not yet run).
    """
    prs = _make_prs(
        REPO_CHANSON,
        owner="pierre",
        reviewers=["sofia"],
        merge_commit_ids=[],
        days_base=15,
    )
    assert len(prs) == 10
    # Merged PRs should fall back gracefully (merge_commit_id may be None)
    merged = [p for p in prs if p["state"] == "merged"]
    assert len(merged) == 4


# ---------------------------------------------------------------------------
# _make_cross_repo_prs
# ---------------------------------------------------------------------------

def test_make_cross_repo_prs_returns_two_entries() -> None:
    """Cross-repo PR generator must return exactly 2 PRs per fork."""
    cross_prs = _make_cross_repo_prs(
        REPO_NEO_SOUL_FORK,
        REPO_NEO_SOUL,
        "marcus",
        _fake_merge_commits(),
    )
    assert len(cross_prs) == 2


def test_make_cross_repo_prs_target_upstream_repo() -> None:
    """Cross-repo PRs must target the upstream (not the fork) repo_id."""
    cross_prs = _make_cross_repo_prs(
        REPO_NEO_SOUL_FORK,
        REPO_NEO_SOUL,
        "marcus",
        _fake_merge_commits(),
    )
    for pr in cross_prs:
        assert pr["repo_id"] == REPO_NEO_SOUL, (
            "Cross-repo PR must target the upstream repo, not the fork"
        )


def test_make_cross_repo_prs_lifecycle_coverage() -> None:
    """Cross-repo PRs must include one open and one merged PR."""
    cross_prs = _make_cross_repo_prs(
        REPO_NEO_SOUL_FORK,
        REPO_NEO_SOUL,
        "marcus",
        _fake_merge_commits(),
    )
    states = {p["state"] for p in cross_prs}
    assert "open" in states
    assert "merged" in states


def test_make_cross_repo_prs_from_branch_contains_fork_owner() -> None:
    """Cross-repo PR from_branch must reference the fork owner namespace."""
    cross_prs = _make_cross_repo_prs(
        REPO_AMBIENT_FORK,
        REPO_AMBIENT,
        "yuki",
        _fake_merge_commits(),
    )
    for pr in cross_prs:
        assert "yuki" in pr["from_branch"], (
            "from_branch must include the fork owner namespace"
        )


def test_make_cross_repo_prs_stable_ids() -> None:
    """Cross-repo PR IDs must be deterministic."""
    args = (REPO_NEO_SOUL_FORK, REPO_NEO_SOUL, "marcus", _fake_merge_commits())
    ids_a = {p["pr_id"] for p in _make_cross_repo_prs(*args)}
    ids_b = {p["pr_id"] for p in _make_cross_repo_prs(*args)}
    assert ids_a == ids_b


# ---------------------------------------------------------------------------
# _make_reviews
# ---------------------------------------------------------------------------

def test_make_reviews_has_required_fields() -> None:
    """Review dict must have all fields required by MusehubPRReview."""
    required = {"id", "pr_id", "reviewer_username", "state", "created_at"}
    review = _make_reviews("pr-123", "marcus", "approved", "LGTM.")
    missing = required - set(review.keys())
    assert not missing


def test_make_reviews_pending_has_no_submitted_at() -> None:
    """A pending review must have submitted_at=None."""
    review = _make_reviews("pr-123", "marcus", "pending", "")
    assert review["submitted_at"] is None


def test_make_reviews_approved_has_submitted_at() -> None:
    """An approved review must have a non-None submitted_at timestamp."""
    review = _make_reviews("pr-123", "marcus", "approved", "Looks good.")
    assert review["submitted_at"] is not None


# ---------------------------------------------------------------------------
# _make_pr_comment
# ---------------------------------------------------------------------------

def test_make_pr_comment_general_target() -> None:
    """A general comment must have target_type='general' and no track/region."""
    comment = _make_pr_comment("pr-1", "repo-1", "sofia", "Nice work overall.")
    assert comment["target_type"] == "general"
    assert comment["target_track"] is None
    assert comment["target_beat_start"] is None


def test_make_pr_comment_region_target() -> None:
    """A region comment must carry track name and beat range."""
    comment = _make_pr_comment(
        "pr-1", "repo-1", "sofia", "The bass riff here is busy.",
        target_type="region",
        target_track="bass",
        target_beat_start=5.0,
        target_beat_end=9.0,
    )
    assert comment["target_type"] == "region"
    assert comment["target_track"] == "bass"
    assert comment["target_beat_start"] == 5.0
    assert comment["target_beat_end"] == 9.0


def test_make_pr_comment_reply_carries_parent_id() -> None:
    """A reply comment must carry its parent_comment_id."""
    parent_id = _uid("parent-comment-1")
    reply = _make_pr_comment(
        "pr-1", "repo-1", "gabriel", "Good point, will adjust.",
        parent_comment_id=parent_id,
    )
    assert reply["parent_comment_id"] == parent_id


def test_make_pr_comment_has_required_fields() -> None:
    """Comment dict must have all fields required by MusehubPRComment."""
    required = {
        "comment_id", "pr_id", "repo_id", "author", "body",
        "target_type", "created_at",
    }
    comment = _make_pr_comment("pr-1", "repo-1", "gabriel", "LGTM.")
    missing = required - set(comment.keys())
    assert not missing


# ---------------------------------------------------------------------------
# Repo constants
# ---------------------------------------------------------------------------

def test_all_primary_repos_have_owners() -> None:
    """Every primary repo must have an entry in REPO_OWNER."""
    for repo_id in PRIMARY_REPOS:
        assert repo_id in REPO_OWNER, f"{repo_id} missing from REPO_OWNER"


def test_all_primary_repos_have_reviewers() -> None:
    """Every primary repo must have at least one reviewer defined."""
    for repo_id in PRIMARY_REPOS:
        reviewers = REPO_REVIEWERS.get(repo_id, [])
        assert len(reviewers) >= 1, f"{repo_id} has no reviewers"


def test_fork_repos_have_upstream_mapping() -> None:
    """Every fork repo must have an upstream entry in FORK_UPSTREAM."""
    for fork_id in FORK_REPOS:
        assert fork_id in FORK_UPSTREAM, f"{fork_id} missing from FORK_UPSTREAM"


def test_fork_upstream_targets_primary_repo() -> None:
    """Each fork's upstream must be in PRIMARY_REPOS."""
    for fork_id, upstream_id in FORK_UPSTREAM.items():
        assert upstream_id in PRIMARY_REPOS, (
            f"Fork {fork_id} → {upstream_id} is not a primary repo"
        )


def test_ids_across_repos_are_collision_free() -> None:
    """PR IDs across all primary repos must be globally unique."""
    all_ids: list[str] = []
    commits = _fake_merge_commits()
    for repo_id in PRIMARY_REPOS:
        owner = REPO_OWNER[repo_id]
        reviewers = REPO_REVIEWERS.get(repo_id, ["sofia"])
        prs = _make_prs(repo_id, owner, reviewers, commits, days_base=30)
        all_ids.extend(p["pr_id"] for p in prs)

    assert len(all_ids) == len(set(all_ids)), (
        "Duplicate PR IDs detected across primary repos"
    )
