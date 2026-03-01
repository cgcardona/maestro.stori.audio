"""Tests for Muse Hub label management endpoints.

Covers all acceptance criteria from issue #409:
- GET  /musehub/repos/{repo_id}/labels            — list labels (public)
- POST /musehub/repos/{repo_id}/labels            — create label (auth required)
- PATCH /musehub/repos/{repo_id}/labels/{label_id} — update label (auth required)
- DELETE /musehub/repos/{repo_id}/labels/{label_id} — delete label (auth required)
- POST .../issues/{number}/labels                  — assign labels to issue (auth required)
- DELETE .../issues/{number}/labels/{label_id}     — remove label from issue (auth required)
- POST .../pull-requests/{pr_id}/labels            — assign labels to PR (auth required)
- DELETE .../pull-requests/{pr_id}/labels/{label_id} — remove label from PR (auth required)

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "label-test-repo") -> str:
    """Create a repo and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _create_label(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    name: str = "bug",
    color: str = "#d73a4a",
    description: str | None = "Something isn't working",
) -> dict[str, object]:
    """Create a label and return the response body."""
    payload: dict[str, object] = {"name": name, "color": color}
    if description is not None:
        payload["description"] = description
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/labels",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    label: dict[str, object] = response.json()
    return label


async def _create_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    title: str = "Test issue",
) -> dict[str, object]:
    """Create an issue and return the response body."""
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues",
        json={"title": title, "body": "", "labels": []},
        headers=auth_headers,
    )
    assert response.status_code == 201
    issue: dict[str, object] = response.json()
    return issue


async def _push_branch(db: AsyncSession, repo_id: str, branch_name: str) -> str:
    """Insert a branch with one commit so the branch exists (required before creating a PR)."""
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch_name,
        parent_ids=[],
        message=f"Initial commit on {branch_name}",
        author="testuser",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name=branch_name,
        head_commit_id=commit_id,
    )
    db.add(commit)
    db.add(branch)
    await db.commit()
    return commit_id


async def _create_pr(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    title: str = "Test PR",
) -> dict[str, object]:
    """Create a pull request and return the response body."""
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={"title": title, "body": "", "fromBranch": "feature", "toBranch": "main"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    pr: dict[str, object] = response.json()
    return pr


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/labels
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_label_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels creates a label and returns 201 with the label data."""
    repo_id = await _create_repo(client, auth_headers, "create-label-repo")
    label = await _create_label(client, auth_headers, repo_id)

    assert label["name"] == "bug"
    assert label["color"] == "#d73a4a"
    assert label["description"] == "Something isn't working"
    assert "labelId" in label or "label_id" in label
    assert label.get("repoId") == repo_id or label.get("repo_id") == repo_id


@pytest.mark.anyio
async def test_create_label_requires_auth(
    client: AsyncClient,
) -> None:
    """POST /labels without auth returns 401."""
    response = await client.post(
        "/api/v1/musehub/repos/nonexistent/labels",
        json={"name": "bug", "color": "#d73a4a"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_label_unknown_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels for a non-existent repo returns 404."""
    response = await client.post(
        "/api/v1/musehub/repos/does-not-exist/labels",
        json={"name": "bug", "color": "#d73a4a"},
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_label_duplicate_name_returns_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels with a duplicate name returns 409 Conflict."""
    repo_id = await _create_repo(client, auth_headers, "dupe-label-repo")
    await _create_label(client, auth_headers, repo_id, name="bug")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/labels",
        json={"name": "bug", "color": "#aabbcc"},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_create_label_invalid_color_returns_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /labels with an invalid colour format returns 422."""
    repo_id = await _create_repo(client, auth_headers, "color-invalid-repo")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/labels",
        json={"name": "bug", "color": "red"},
        headers=auth_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/labels
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_labels_public_access(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /labels is publicly accessible and returns all repo labels."""
    repo_id = await _create_repo(client, auth_headers, "list-labels-repo")
    await _create_label(client, auth_headers, repo_id, name="bug", color="#d73a4a")
    await _create_label(client, auth_headers, repo_id, name="enhancement", color="#a2eeef")

    # No auth headers — public endpoint.
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/labels")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert body["total"] == 2
    names = [item["name"] for item in body["items"]]
    assert "bug" in names
    assert "enhancement" in names


@pytest.mark.anyio
async def test_list_labels_unknown_repo_returns_404(
    client: AsyncClient,
) -> None:
    """GET /labels for a non-existent repo returns 404."""
    response = await client.get("/api/v1/musehub/repos/no-such-repo/labels")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_list_labels_empty_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /labels for a repo with no labels returns an empty list."""
    repo_id = await _create_repo(client, auth_headers, "empty-labels-repo")
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/labels")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# PATCH /musehub/repos/{repo_id}/labels/{label_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_label_name(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH /labels/{id} updates the label name."""
    repo_id = await _create_repo(client, auth_headers, "update-label-repo")
    label = await _create_label(client, auth_headers, repo_id, name="old-name", color="#aabbcc")
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.patch(
        f"/api/v1/musehub/repos/{repo_id}/labels/{label_id}",
        json={"name": "new-name"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "new-name"
    assert response.json()["color"] == "#aabbcc"


@pytest.mark.anyio
async def test_update_label_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH /labels/{id} without auth returns 401."""
    repo_id = await _create_repo(client, auth_headers, "update-auth-label-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.patch(
        f"/api/v1/musehub/repos/{repo_id}/labels/{label_id}",
        json={"name": "hacked"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_update_label_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH /labels/{id} with an unknown label_id returns 404."""
    repo_id = await _create_repo(client, auth_headers, "update-404-repo")
    response = await client.patch(
        f"/api/v1/musehub/repos/{repo_id}/labels/00000000-0000-0000-0000-000000000000",
        json={"name": "ghost"},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /musehub/repos/{repo_id}/labels/{label_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_label_returns_204(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /labels/{id} removes the label and returns 204."""
    repo_id = await _create_repo(client, auth_headers, "delete-label-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/labels/{label_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    # Confirm the label is gone.
    list_resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/labels")
    assert list_resp.json()["total"] == 0


@pytest.mark.anyio
async def test_delete_label_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /labels/{id} without auth returns 401."""
    repo_id = await _create_repo(client, auth_headers, "delete-auth-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/labels/{label_id}",
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Issue label assignments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_assign_labels_to_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST .../issues/{number}/labels assigns labels and returns them."""
    repo_id = await _create_repo(client, auth_headers, "issue-label-assign-repo")
    label = await _create_label(client, auth_headers, repo_id, name="bug", color="#d73a4a")
    label_id = label.get("label_id") or label.get("labelId")
    issue = await _create_issue(client, auth_headers, repo_id)
    issue_number = issue["number"]

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue_number}/labels",
        json={"label_ids": [label_id]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assigned = response.json()
    assert len(assigned) == 1
    assert assigned[0]["name"] == "bug"


@pytest.mark.anyio
async def test_assign_labels_to_issue_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Assigning the same label twice does not raise an error."""
    repo_id = await _create_repo(client, auth_headers, "issue-label-idem-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")
    issue = await _create_issue(client, auth_headers, repo_id)
    issue_number = issue["number"]

    for _ in range(2):
        response = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/issues/{issue_number}/labels",
            json={"label_ids": [label_id]},
            headers=auth_headers,
        )
        assert response.status_code == 200


@pytest.mark.anyio
async def test_remove_label_from_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE .../issues/{number}/labels/{label_id} removes the association."""
    repo_id = await _create_repo(client, auth_headers, "issue-label-remove-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")
    issue = await _create_issue(client, auth_headers, repo_id)
    issue_number = issue["number"]

    # Assign first.
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue_number}/labels",
        json={"label_ids": [label_id]},
        headers=auth_headers,
    )

    # Then remove.
    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue_number}/labels/{label_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_remove_label_from_issue_unknown_issue_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE .../issues/{number}/labels/{label_id} for an unknown issue returns 404."""
    repo_id = await _create_repo(client, auth_headers, "issue-label-404-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/issues/9999/labels/{label_id}",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PR label assignments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_assign_labels_to_pr(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST .../pull-requests/{pr_id}/labels assigns labels and returns them."""
    repo_id = await _create_repo(client, auth_headers, "pr-label-assign-repo")
    await _push_branch(db_session, repo_id, "main")
    await _push_branch(db_session, repo_id, "feature")
    label = await _create_label(client, auth_headers, repo_id, name="enhancement", color="#a2eeef")
    label_id = label.get("label_id") or label.get("labelId")
    pr = await _create_pr(client, auth_headers, repo_id)
    pr_id = pr.get("prId") or pr.get("pr_id")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/labels",
        json={"label_ids": [label_id]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assigned = response.json()
    assert len(assigned) == 1
    assert assigned[0]["name"] == "enhancement"


@pytest.mark.anyio
async def test_remove_label_from_pr(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DELETE .../pull-requests/{pr_id}/labels/{label_id} removes the association."""
    repo_id = await _create_repo(client, auth_headers, "pr-label-remove-repo")
    await _push_branch(db_session, repo_id, "main")
    await _push_branch(db_session, repo_id, "feature")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")
    pr = await _create_pr(client, auth_headers, repo_id)
    pr_id = pr.get("prId") or pr.get("pr_id")

    # Assign first.
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/labels",
        json={"label_ids": [label_id]},
        headers=auth_headers,
    )

    # Then remove — should be idempotent too.
    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/labels/{label_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_remove_label_from_pr_unknown_pr_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE .../pull-requests/{pr_id}/labels/{label_id} for an unknown PR returns 404."""
    repo_id = await _create_repo(client, auth_headers, "pr-label-404-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/00000000-0000-0000-0000-000000000000/labels/{label_id}",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_label_cascades_to_issue_associations(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Deleting a label removes it from all issue associations (cascade)."""
    repo_id = await _create_repo(client, auth_headers, "cascade-delete-repo")
    label = await _create_label(client, auth_headers, repo_id)
    label_id = label.get("label_id") or label.get("labelId")
    issue = await _create_issue(client, auth_headers, repo_id)
    issue_number = issue["number"]

    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/issues/{issue_number}/labels",
        json={"label_ids": [label_id]},
        headers=auth_headers,
    )

    delete_resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/labels/{label_id}",
        headers=auth_headers,
    )
    assert delete_resp.status_code == 204

    # The label should no longer appear in the repo's label list.
    list_resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/labels")
    assert list_resp.json()["total"] == 0
