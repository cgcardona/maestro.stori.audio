"""Pydantic v2 request/response models for the Muse Hub API.

All wire-format fields use camelCase via CamelModel.  Python code uses
snake_case throughout; only serialisation to JSON uses camelCase.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from maestro.models.base import CamelModel


# ── Request models ────────────────────────────────────────────────────────────


class CreateRepoRequest(CamelModel):
    """Body for POST /musehub/repos."""

    name: str = Field(..., min_length=1, max_length=255, description="Repo name")
    visibility: str = Field("private", pattern="^(public|private)$")


# ── Response models ───────────────────────────────────────────────────────────


class RepoResponse(CamelModel):
    """Wire representation of a Muse Hub repo."""

    repo_id: str
    name: str
    visibility: str
    owner_user_id: str
    clone_url: str
    created_at: datetime


class BranchResponse(CamelModel):
    """Wire representation of a branch pointer."""

    branch_id: str
    name: str
    head_commit_id: str | None = None


class CommitResponse(CamelModel):
    """Wire representation of a pushed commit."""

    commit_id: str
    branch: str
    parent_ids: list[str]
    message: str
    author: str
    timestamp: datetime
    snapshot_id: str | None = None


class BranchListResponse(CamelModel):
    """Paginated list of branches."""

    branches: list[BranchResponse]


class CommitListResponse(CamelModel):
    """Paginated list of commits (newest first)."""

    commits: list[CommitResponse]
    total: int


# ── Issue models ───────────────────────────────────────────────────────────────


class IssueCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/issues."""

    title: str = Field(..., min_length=1, max_length=500, description="Issue title")
    body: str = Field("", description="Issue description (Markdown)")
    labels: list[str] = Field(default_factory=list, description="Free-form label strings")


class IssueResponse(CamelModel):
    """Wire representation of a Muse Hub issue."""

    issue_id: str
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    created_at: datetime


class IssueListResponse(CamelModel):
    """List of issues for a repo."""

    issues: list[IssueResponse]


# ── Pull request models ────────────────────────────────────────────────────────


class PRCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests."""

    title: str = Field(..., min_length=1, max_length=500, description="PR title")
    from_branch: str = Field(..., min_length=1, max_length=255, description="Source branch name")
    to_branch: str = Field(..., min_length=1, max_length=255, description="Target branch name")
    body: str = Field("", description="PR description (Markdown)")


class PRResponse(CamelModel):
    """Wire representation of a Muse Hub pull request."""

    pr_id: str
    title: str
    body: str
    state: str
    from_branch: str
    to_branch: str
    merge_commit_id: str | None = None
    created_at: datetime


class PRListResponse(CamelModel):
    """List of pull requests for a repo."""

    pull_requests: list[PRResponse]


class PRMergeRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge."""

    merge_strategy: str = Field(
        "merge_commit",
        pattern="^(merge_commit)$",
        description="Merge strategy — only 'merge_commit' is supported at MVP",
    )


class PRMergeResponse(CamelModel):
    """Confirmation that a PR was merged."""

    merged: bool
    merge_commit_id: str
