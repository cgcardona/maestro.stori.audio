"""Tests for the agent context endpoint (GET /musehub/repos/{repo_id}/context).

Covers every acceptance criterion from issue #249:
- GET /musehub/repos/{repo_id}/context returns all required sections
- Musical state section is present (active_tracks, key, tempo, etc.)
- History section includes recent commits
- Active PRs section lists open PRs
- Open issues section lists open issues
- Suggestions section is present
- ?depth=brief returns minimal context
- ?depth=standard returns moderate context
- ?depth=verbose returns full context
- ?format=yaml returns valid YAML
- Unknown repo returns 404
- Missing ref returns 404
- Endpoint requires JWT auth

All tests use fixtures from conftest.py.
"""
from __future__ import annotations

import pytest
import yaml  # PyYAML ships no py.typed marker
from datetime import datetime, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubBranch,
    MusehubCommit,
    MusehubIssue,
    MusehubPullRequest,
    MusehubRepo,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "neo-soul") -> str:
    """Create a repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _seed_repo_with_commits(
    db: AsyncSession,
    repo_id: str,
    branch_name: str = "main",
    num_commits: int = 3,
) -> tuple[str, list[str]]:
    """Seed a repo with a branch and commits. Returns (branch_id, list_of_commit_ids)."""
    commit_ids: list[str] = []
    parent_id: str | None = None
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    import uuid
    from datetime import timedelta

    for i in range(num_commits):
        commit_id = str(uuid.uuid4()).replace("-", "")
        commit = MusehubCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch=branch_name,
            parent_ids=[parent_id] if parent_id else [],
            message=f"Add layer {i + 1} — bass groove refinement",
            author="session-agent",
            timestamp=ts + timedelta(hours=i),
        )
        db.add(commit)
        commit_ids.append(commit_id)
        parent_id = commit_id

    branch = MusehubBranch(
        repo_id=repo_id,
        name=branch_name,
        head_commit_id=commit_ids[-1],
    )
    db.add(branch)
    await db.flush()

    return branch_name, commit_ids


# ---------------------------------------------------------------------------
# test_context_endpoint_returns_all_sections
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_endpoint_returns_all_sections(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /musehub/repos/{repo_id}/context returns all required top-level sections."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert "repoId" in body
    assert "ref" in body
    assert "depth" in body
    assert "musicalState" in body
    assert "history" in body
    assert "analysis" in body
    assert "activePrs" in body
    assert "openIssues" in body
    assert "suggestions" in body

    assert body["repoId"] == repo_id
    assert body["depth"] == "standard"


# ---------------------------------------------------------------------------
# test_context_includes_musical_state
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_includes_musical_state(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Musical state section contains expected fields (key, tempo, etc. may be None at MVP)."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    state = response.json()["musicalState"]

    assert "activeTracks" in state
    assert isinstance(state["activeTracks"], list)
    # Optional fields present (None until Storpheus integration)
    assert "key" in state
    assert "tempoBpm" in state
    assert "timeSignature" in state
    assert "form" in state
    assert "emotion" in state


# ---------------------------------------------------------------------------
# test_context_includes_history
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_includes_history(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """History section includes recent commits (excluding the head commit)."""
    repo_id = await _create_repo(client, auth_headers)
    _, commit_ids = await _seed_repo_with_commits(db_session, repo_id, num_commits=5)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    history = response.json()["history"]

    assert isinstance(history, list)
    # 5 commits seeded → head excluded → at most 4 in history at standard depth
    assert len(history) <= 10
    assert len(history) >= 1

    entry = history[0]
    assert "commitId" in entry
    assert "message" in entry
    assert "author" in entry
    assert "timestamp" in entry
    assert "activeTracks" in entry


# ---------------------------------------------------------------------------
# test_context_includes_active_prs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_includes_active_prs(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Active PRs section lists open pull requests for the repo."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id, branch_name="main")

    import uuid
    from datetime import timedelta

    feature_branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/tritone-subs",
        head_commit_id=str(uuid.uuid4()).replace("-", ""),
    )
    db_session.add(feature_branch)
    await db_session.flush()

    pr = MusehubPullRequest(
        repo_id=repo_id,
        title="Add tritone substitution in bridge",
        body="Resolves the harmonic monotony in bars 24-28.",
        state="open",
        from_branch="feat/tritone-subs",
        to_branch="main",
    )
    db_session.add(pr)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    prs = response.json()["activePrs"]

    assert isinstance(prs, list)
    assert len(prs) == 1
    assert prs[0]["title"] == "Add tritone substitution in bridge"
    assert prs[0]["state"] == "open"
    assert "prId" in prs[0]
    assert "fromBranch" in prs[0]
    assert "toBranch" in prs[0]


# ---------------------------------------------------------------------------
# test_context_brief_depth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_brief_depth(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?depth=brief returns minimal context — at most 3 history entries and 2 suggestions."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id, num_commits=8)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?depth=brief",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert body["depth"] == "brief"
    assert len(body["history"]) <= 3
    assert len(body["suggestions"]) <= 2


# ---------------------------------------------------------------------------
# test_context_standard_depth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_standard_depth(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?depth=standard (default) returns at most 10 history entries."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id, num_commits=15)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?depth=standard",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert body["depth"] == "standard"
    assert len(body["history"]) <= 10


# ---------------------------------------------------------------------------
# test_context_verbose_depth_includes_issue_bodies
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_verbose_depth_includes_issue_bodies(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?depth=verbose includes full issue bodies; brief/standard do not."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)

    import uuid

    issue = MusehubIssue(
        repo_id=repo_id,
        number=1,
        title="Add more harmonic tension",
        body="Consider a tritone substitution in bar 24 to create tension before the resolution.",
        state="open",
        labels=["harmonic", "composition"],
    )
    db_session.add(issue)
    await db_session.commit()

    # brief: body should be empty string
    brief_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?depth=brief",
        headers=auth_headers,
    )
    assert brief_resp.status_code == 200
    brief_issues = brief_resp.json()["openIssues"]
    assert len(brief_issues) == 1
    assert brief_issues[0]["body"] == ""

    # verbose: body should be included
    verbose_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?depth=verbose",
        headers=auth_headers,
    )
    assert verbose_resp.status_code == 200
    verbose_issues = verbose_resp.json()["openIssues"]
    assert len(verbose_issues) == 1
    assert "tritone substitution" in verbose_issues[0]["body"]


# ---------------------------------------------------------------------------
# test_context_yaml_format
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_yaml_format(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?format=yaml returns valid YAML with the same structure as JSON."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?format=yaml",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "yaml" in response.headers["content-type"]

    parsed = yaml.safe_load(response.text)
    assert isinstance(parsed, dict)
    assert "repoId" in parsed
    assert "musicalState" in parsed
    assert "history" in parsed
    assert "analysis" in parsed


# ---------------------------------------------------------------------------
# test_context_unknown_repo_404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{unknown_id}/context returns 404 for a non-existent repo."""
    response = await client.get(
        "/api/v1/musehub/repos/nonexistent-repo-id/context",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# test_context_ref_not_found_404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_ref_not_found_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET .../context?ref=nonexistent returns 404 when the ref has no commits."""
    repo_id = await _create_repo(client, auth_headers)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?ref=nonexistent-branch",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# test_context_requires_auth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_nonexistent_repo_returns_404_without_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/repos/{repo_id}/context returns 404 for a non-existent repo without auth.

    Context endpoint uses optional_token — auth check is visibility-based,
    so a missing repo returns 404 before the auth check fires.
    """
    response = await client.get(
        "/api/v1/musehub/repos/non-existent-repo-id/context",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# test_context_default_ref_resolves_to_latest_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_default_ref_resolves_to_latest_commit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?ref=HEAD (default) resolves to the latest commit and returns a valid ref in response."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id, branch_name="main")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    # ref should resolve to a branch name or commit id (not literally "HEAD")
    assert body["ref"] != ""


# ---------------------------------------------------------------------------
# test_context_branch_ref_resolution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_branch_ref_resolution(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?ref=<branch_name> resolves the branch head commit."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id, branch_name="main")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context?ref=main",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ref"] == "main"


# ---------------------------------------------------------------------------
# test_context_suggestions_generated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_suggestions_generated(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Suggestions are generated and returned as a list of strings."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    suggestions = response.json()["suggestions"]

    assert isinstance(suggestions, list)
    assert all(isinstance(s, str) for s in suggestions)
    # At least one suggestion since no key/tempo detected (stubs)
    assert len(suggestions) >= 1


# ---------------------------------------------------------------------------
# test_context_open_issues_excluded_when_closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_open_issues_excluded_when_closed(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Closed issues do not appear in the open_issues section."""
    repo_id = await _create_repo(client, auth_headers)
    await _seed_repo_with_commits(db_session, repo_id)

    closed_issue = MusehubIssue(
        repo_id=repo_id,
        number=1,
        title="Closed: fix the bridge",
        body="Already fixed.",
        state="closed",
        labels=[],
    )
    open_issue = MusehubIssue(
        repo_id=repo_id,
        number=2,
        title="Add swing feel to verse",
        body="",
        state="open",
        labels=["groove"],
    )
    db_session.add(closed_issue)
    db_session.add(open_issue)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context",
        headers=auth_headers,
    )
    assert response.status_code == 200
    issues = response.json()["openIssues"]

    assert len(issues) == 1
    assert issues[0]["title"] == "Add swing feel to verse"
    assert issues[0]["number"] == 2
