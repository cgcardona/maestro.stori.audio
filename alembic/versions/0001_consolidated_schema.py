"""Consolidated schema — all tables, single migration.

Revision ID: 0001
Revises:
Create Date: 2026-02-27 00:00:00.000000

Single source-of-truth migration for Stori Maestro. Creates:

  Auth & usage
  - users, usage_logs, access_tokens

  Conversations
  - conversations, conversation_messages, message_actions

  Muse VCS — DAW-level variation history
  - variations, phrases, note_changes

  Muse CLI — filesystem commit history
  - muse_cli_objects, muse_cli_snapshots, muse_cli_commits
    (includes parent2_commit_id for merge commits; metadata JSON blob for
    commit-level annotations e.g. tempo_bpm set via ``muse tempo --set``)
  - muse_cli_tags (music-semantic tags attached to commits)

  Muse Hub — remote collaboration backend
  - musehub_repos, musehub_branches, musehub_commits, musehub_issues
  - musehub_pull_requests (PR workflow between branches)
  - musehub_objects (content-addressed binary artifact storage)
  - musehub_stars (per-user repo starring for the explore/discover page)
  - musehub_profiles (public user profile pages — bio, avatar, pinned repos)
  - musehub_sessions (recording session metadata — participants, intent, commits)
  - musehub_releases (published version releases with download packages)
  - musehub_webhooks (registered event-driven webhook subscriptions)
  - musehub_webhook_deliveries (delivery log per dispatch attempt)

Fresh install:
  docker compose exec maestro alembic upgrade head
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users & auth ──────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("budget_cents", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("budget_limit_cents", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "usage_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_logs_user_id", "usage_logs", ["user_id"])
    op.create_index("ix_usage_logs_created_at", "usage_logs", ["created_at"])

    op.create_table(
        "access_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"])
    op.create_index("ix_access_tokens_token_hash", "access_tokens", ["token_hash"], unique=True)

    # ── Conversations ─────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(255), nullable=False, server_default="New Conversation"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("project_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    op.create_index("ix_conversations_is_archived", "conversations", ["is_archived"])
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("tokens_used", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sse_events", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extra_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])
    op.create_index("ix_conversation_messages_timestamp", "conversation_messages", ["timestamp"])

    op.create_table(
        "message_actions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extra_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_actions_message_id", "message_actions", ["message_id"])

    # ── Muse VCS — DAW-level variation history ────────────────────────────
    op.create_table(
        "variations",
        sa.Column("variation_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("base_state_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("affected_tracks", sa.JSON(), nullable=True),
        sa.Column("affected_regions", sa.JSON(), nullable=True),
        sa.Column("beat_range_start", sa.Float(), nullable=False, server_default="0"),
        sa.Column("beat_range_end", sa.Float(), nullable=False, server_default="0"),
        sa.Column("parent_variation_id", sa.String(36), sa.ForeignKey("variations.variation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent2_variation_id", sa.String(36), sa.ForeignKey("variations.variation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("commit_state_id", sa.String(36), nullable=True),
        sa.Column("is_head", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("variation_id"),
    )
    op.create_index("ix_variations_project_id", "variations", ["project_id"])
    op.create_index("ix_variations_status", "variations", ["status"])
    op.create_index("ix_variations_parent_variation_id", "variations", ["parent_variation_id"])

    op.create_table(
        "phrases",
        sa.Column("phrase_id", sa.String(36), nullable=False),
        sa.Column("variation_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.String(36), nullable=False),
        sa.Column("region_id", sa.String(36), nullable=False),
        sa.Column("start_beat", sa.Float(), nullable=False),
        sa.Column("end_beat", sa.Float(), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("cc_events", sa.JSON(), nullable=True),
        sa.Column("pitch_bends", sa.JSON(), nullable=True),
        sa.Column("aftertouch", sa.JSON(), nullable=True),
        sa.Column("region_start_beat", sa.Float(), nullable=True),
        sa.Column("region_duration_beats", sa.Float(), nullable=True),
        sa.Column("region_name", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["variation_id"], ["variations.variation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("phrase_id"),
    )
    op.create_index("ix_phrases_variation_id", "phrases", ["variation_id"])

    op.create_table(
        "note_changes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("phrase_id", sa.String(36), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["phrase_id"], ["phrases.phrase_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_changes_phrase_id", "note_changes", ["phrase_id"])

    # ── Muse CLI — filesystem commit history ──────────────────────────────
    op.create_table(
        "muse_cli_objects",
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("object_id"),
    )

    op.create_table(
        "muse_cli_snapshots",
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )

    op.create_table(
        "muse_cli_commits",
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("parent_commit_id", sa.String(64), nullable=True),
        sa.Column("parent2_commit_id", sa.String(64), nullable=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["muse_cli_snapshots.snapshot_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("commit_id"),
    )
    op.create_index("ix_muse_cli_commits_repo_id", "muse_cli_commits", ["repo_id"])
    op.create_index("ix_muse_cli_commits_parent_commit_id", "muse_cli_commits", ["parent_commit_id"])
    op.create_index("ix_muse_cli_commits_parent2_commit_id", "muse_cli_commits", ["parent2_commit_id"])

    op.create_table(
        "muse_cli_tags",
        sa.Column("tag_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["commit_id"], ["muse_cli_commits.commit_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id"),
    )
    op.create_index("ix_muse_cli_tags_repo_id", "muse_cli_tags", ["repo_id"])
    op.create_index("ix_muse_cli_tags_commit_id", "muse_cli_tags", ["commit_id"])
    op.create_index("ix_muse_cli_tags_tag", "muse_cli_tags", ["tag"])

    # ── Muse Hub — remote collaboration backend ───────────────────────────
    op.create_table(
        "musehub_repos",
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # URL-visible owner username (e.g. "gabriel") — forms the /{owner}/{slug} path
        sa.Column("owner", sa.String(64), nullable=False),
        # URL-safe slug auto-generated from name (e.g. "neo-soul-experiment")
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("key_signature", sa.String(50), nullable=True),
        sa.Column("tempo_bpm", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("repo_id"),
        sa.UniqueConstraint("owner", "slug", name="uq_musehub_repos_owner_slug"),
    )
    op.create_index("ix_musehub_repos_owner", "musehub_repos", ["owner"])
    op.create_index("ix_musehub_repos_slug", "musehub_repos", ["slug"])
    op.create_index("ix_musehub_repos_owner_user_id", "musehub_repos", ["owner_user_id"])
    op.create_index("ix_musehub_repos_visibility", "musehub_repos", ["visibility"])

    op.create_table(
        "musehub_branches",
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("head_commit_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("branch_id"),
    )
    op.create_index("ix_musehub_branches_repo_id", "musehub_branches", ["repo_id"])

    op.create_table(
        "musehub_commits",
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("parent_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("commit_id"),
    )
    op.create_index("ix_musehub_commits_repo_id", "musehub_commits", ["repo_id"])
    op.create_index("ix_musehub_commits_branch", "musehub_commits", ["branch"])
    op.create_index("ix_musehub_commits_timestamp", "musehub_commits", ["timestamp"])

    # ── Muse Hub — milestones ─────────────────────────────────────────────
    op.create_table(
        "musehub_milestones",
        sa.Column("milestone_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("due_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("milestone_id"),
    )
    op.create_index("ix_musehub_milestones_repo_id", "musehub_milestones", ["repo_id"])
    op.create_index("ix_musehub_milestones_number", "musehub_milestones", ["number"])
    op.create_index("ix_musehub_milestones_state", "musehub_milestones", ["state"])

    # ── Muse Hub — issue tracking ─────────────────────────────────────────
    op.create_table(
        "musehub_issues",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("assignee", sa.String(255), nullable=True),
        sa.Column("milestone_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["musehub_milestones.milestone_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index("ix_musehub_issues_repo_id", "musehub_issues", ["repo_id"])
    op.create_index("ix_musehub_issues_number", "musehub_issues", ["number"])
    op.create_index("ix_musehub_issues_state", "musehub_issues", ["state"])
    op.create_index("ix_musehub_issues_milestone_id", "musehub_issues", ["milestone_id"])

    # ── Muse Hub — issue comments ─────────────────────────────────────────
    op.create_table(
        "musehub_issue_comments",
        sa.Column("comment_id", sa.String(36), nullable=False),
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("musical_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["musehub_issues.issue_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index("ix_musehub_issue_comments_issue_id", "musehub_issue_comments", ["issue_id"])
    op.create_index("ix_musehub_issue_comments_repo_id", "musehub_issue_comments", ["repo_id"])
    op.create_index("ix_musehub_issue_comments_parent_id", "musehub_issue_comments", ["parent_id"])
    op.create_index("ix_musehub_issue_comments_created_at", "musehub_issue_comments", ["created_at"])

    # ── Muse Hub — pull requests ──────────────────────────────────────────
    op.create_table(
        "musehub_pull_requests",
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("from_branch", sa.String(255), nullable=False),
        sa.Column("to_branch", sa.String(255), nullable=False),
        sa.Column("merge_commit_id", sa.String(64), nullable=True),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pr_id"),
    )
    op.create_index("ix_musehub_pull_requests_repo_id", "musehub_pull_requests", ["repo_id"])
    op.create_index("ix_musehub_pull_requests_state", "musehub_pull_requests", ["state"])

    # ── Muse Hub — binary artifact storage ───────────────────────────────
    op.create_table(
        "musehub_objects",
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disk_path", sa.String(2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
    )
    op.create_index("ix_musehub_objects_repo_id", "musehub_objects", ["repo_id"])

    # ── Muse Hub — repo starring (explore/discover page) ─────────────────
    op.create_table(
        "musehub_stars",
        sa.Column("star_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("star_id"),
        sa.UniqueConstraint("repo_id", "user_id", name="uq_musehub_stars_repo_user"),
    )
    op.create_index("ix_musehub_stars_repo_id", "musehub_stars", ["repo_id"])
    op.create_index("ix_musehub_stars_user_id", "musehub_stars", ["user_id"])

    # ── Muse Hub — recording sessions ─────────────────────────────────────
    op.create_table(
        "musehub_sessions",
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(10), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participants", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("location", sa.String(500), nullable=False, server_default=""),
        sa.Column("intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("commits", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        # True while the session is still active; False after muse session end / stop
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_musehub_sessions_repo_id", "musehub_sessions", ["repo_id"])
    op.create_index("ix_musehub_sessions_started_at", "musehub_sessions", ["started_at"])
    op.create_index("ix_musehub_sessions_is_active", "musehub_sessions", ["is_active"])

    # ── Muse Hub — public user profiles ───────────────────────────────────
    op.create_table(
        "musehub_profiles",
        # PK is the JWT sub claim — same value used in musehub_repos.owner_user_id
        sa.Column("user_id", sa.String(36), nullable=False),
        # URL-friendly handle, e.g. "gabriel" → /musehub/ui/users/gabriel
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        # JSON list of repo_ids (up to 6) pinned by the user on their profile page
        sa.Column("pinned_repo_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username", name="uq_musehub_profiles_username"),
    )
    op.create_index("ix_musehub_profiles_username", "musehub_profiles", ["username"])

    # ── Muse Hub — webhook subscriptions ─────────────────────────────────
    op.create_table(
        "musehub_webhooks",
        sa.Column("webhook_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("webhook_id"),
    )
    op.create_index("ix_musehub_webhooks_repo_id", "musehub_webhooks", ["repo_id"])

    op.create_table(
        "musehub_webhook_deliveries",
        sa.Column("delivery_id", sa.String(36), nullable=False),
        sa.Column("webhook_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("response_status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_body", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"], ["musehub_webhooks.webhook_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_musehub_webhook_deliveries_webhook_id",
        "musehub_webhook_deliveries",
        ["webhook_id"],
    )
    op.create_index(
        "ix_musehub_webhook_deliveries_event_type",
        "musehub_webhook_deliveries",
        ["event_type"],
    )


    # ── Muse Hub — releases ───────────────────────────────────────────────
    op.create_table(
        "musehub_releases",
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("commit_id", sa.String(64), nullable=True),
        sa.Column("download_urls", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("release_id"),
        sa.UniqueConstraint("repo_id", "tag", name="uq_musehub_releases_repo_tag"),
    )
    op.create_index("ix_musehub_releases_repo_id", "musehub_releases", ["repo_id"])
    op.create_index("ix_musehub_releases_tag", "musehub_releases", ["tag"])


    # ── Muse Hub — social layer (Phase 4) ────────────────────────────────

    op.create_table(
        "musehub_comments",
        sa.Column("comment_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index("ix_musehub_comments_repo_id", "musehub_comments", ["repo_id"])
    op.create_index("ix_musehub_comments_target", "musehub_comments", ["target_type", "target_id"])
    op.create_index("ix_musehub_comments_author", "musehub_comments", ["author"])

    op.create_table(
        "musehub_reactions",
        sa.Column("reaction_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("emoji", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("reaction_id"),
        sa.UniqueConstraint("user_id", "target_type", "target_id", "emoji", name="uq_musehub_reactions"),
    )
    op.create_index("ix_musehub_reactions_target", "musehub_reactions", ["target_type", "target_id"])

    op.create_table(
        "musehub_follows",
        sa.Column("follow_id", sa.String(36), nullable=False),
        sa.Column("follower_id", sa.String(255), nullable=False),
        sa.Column("followee_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("follow_id"),
        sa.UniqueConstraint("follower_id", "followee_id", name="uq_musehub_follows"),
    )
    op.create_index("ix_musehub_follows_follower_id", "musehub_follows", ["follower_id"])
    op.create_index("ix_musehub_follows_followee_id", "musehub_follows", ["followee_id"])

    op.create_table(
        "musehub_watches",
        sa.Column("watch_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("watch_id"),
        sa.UniqueConstraint("user_id", "repo_id", name="uq_musehub_watches"),
    )
    op.create_index("ix_musehub_watches_user_id", "musehub_watches", ["user_id"])
    op.create_index("ix_musehub_watches_repo_id", "musehub_watches", ["repo_id"])

    op.create_table(
        "musehub_notifications",
        sa.Column("notif_id", sa.String(36), nullable=False),
        sa.Column("recipient_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("notif_id"),
    )
    op.create_index("ix_musehub_notifications_recipient_id", "musehub_notifications", ["recipient_id"])
    op.create_index("ix_musehub_notifications_is_read", "musehub_notifications", ["is_read"])

    op.create_table(
        "musehub_forks",
        sa.Column("fork_id", sa.String(36), nullable=False),
        sa.Column("source_repo_id", sa.String(36), nullable=False),
        sa.Column("fork_repo_id", sa.String(36), nullable=False),
        sa.Column("forked_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["source_repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fork_repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fork_id"),
        sa.UniqueConstraint("source_repo_id", "fork_repo_id", name="uq_musehub_forks"),
    )
    op.create_index("ix_musehub_forks_source_repo_id", "musehub_forks", ["source_repo_id"])

    op.create_table(
        "musehub_view_events",
        sa.Column("view_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("viewer_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_date", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("view_id"),
        sa.UniqueConstraint("repo_id", "viewer_fingerprint", "event_date", name="uq_musehub_view_events"),
    )
    op.create_index("ix_musehub_view_events_repo_id", "musehub_view_events", ["repo_id"])

    op.create_table(
        "musehub_download_events",
        sa.Column("dl_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("downloader_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dl_id"),
    )
    op.create_index("ix_musehub_download_events_repo_id", "musehub_download_events", ["repo_id"])

    # ── MuseHub — render pipeline (Phase 5) ──────────────────────────────
    op.create_table(
        "musehub_render_jobs",
        sa.Column("render_job_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("midi_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mp3_object_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("image_object_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("render_job_id"),
        sa.UniqueConstraint("repo_id", "commit_id", name="uq_musehub_render_jobs_repo_commit"),
    )
    op.create_index("ix_musehub_render_jobs_repo_id", "musehub_render_jobs", ["repo_id"])
    op.create_index("ix_musehub_render_jobs_commit_id", "musehub_render_jobs", ["commit_id"])
    op.create_index("ix_musehub_render_jobs_status", "musehub_render_jobs", ["status"])

    # ── MuseHub — activity event stream (Phase 6) ─────────────────────────
    op.create_table(
        "musehub_events",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("event_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_musehub_events_repo_id", "musehub_events", ["repo_id"])
    op.create_index("ix_musehub_events_event_type", "musehub_events", ["event_type"])
    op.create_index("ix_musehub_events_created_at", "musehub_events", ["created_at"])


def downgrade() -> None:
    # Drop in reverse creation order, respecting foreign-key dependencies.

    # MuseHub — activity event stream (Phase 6)
    op.drop_index("ix_musehub_events_created_at", table_name="musehub_events")
    op.drop_index("ix_musehub_events_event_type", table_name="musehub_events")
    op.drop_index("ix_musehub_events_repo_id", table_name="musehub_events")
    op.drop_table("musehub_events")

    # MuseHub — render pipeline (Phase 5)
    op.drop_index("ix_musehub_render_jobs_status", table_name="musehub_render_jobs")
    op.drop_index("ix_musehub_render_jobs_commit_id", table_name="musehub_render_jobs")
    op.drop_index("ix_musehub_render_jobs_repo_id", table_name="musehub_render_jobs")
    op.drop_table("musehub_render_jobs")

    # Muse Hub — social layer (Phase 4)
    op.drop_index("ix_musehub_download_events_repo_id", table_name="musehub_download_events")
    op.drop_table("musehub_download_events")
    op.drop_index("ix_musehub_view_events_repo_id", table_name="musehub_view_events")
    op.drop_table("musehub_view_events")
    op.drop_index("ix_musehub_forks_source_repo_id", table_name="musehub_forks")
    op.drop_table("musehub_forks")
    op.drop_index("ix_musehub_notifications_is_read", table_name="musehub_notifications")
    op.drop_index("ix_musehub_notifications_recipient_id", table_name="musehub_notifications")
    op.drop_table("musehub_notifications")
    op.drop_index("ix_musehub_watches_repo_id", table_name="musehub_watches")
    op.drop_index("ix_musehub_watches_user_id", table_name="musehub_watches")
    op.drop_table("musehub_watches")
    op.drop_index("ix_musehub_follows_followee_id", table_name="musehub_follows")
    op.drop_index("ix_musehub_follows_follower_id", table_name="musehub_follows")
    op.drop_table("musehub_follows")
    op.drop_index("ix_musehub_reactions_target", table_name="musehub_reactions")
    op.drop_table("musehub_reactions")
    op.drop_index("ix_musehub_comments_author", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_target", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_repo_id", table_name="musehub_comments")
    op.drop_table("musehub_comments")

    # Muse Hub — profiles (no FK deps from other tables)
    op.drop_index("ix_musehub_profiles_username", table_name="musehub_profiles")
    op.drop_table("musehub_profiles")

    # Muse Hub — webhook deliveries (depends on webhooks)
    op.drop_index("ix_musehub_webhook_deliveries_event_type", table_name="musehub_webhook_deliveries")
    op.drop_index("ix_musehub_webhook_deliveries_webhook_id", table_name="musehub_webhook_deliveries")
    op.drop_table("musehub_webhook_deliveries")

    # Muse Hub — webhooks (depends on repos)
    op.drop_index("ix_musehub_webhooks_repo_id", table_name="musehub_webhooks")
    op.drop_table("musehub_webhooks")

    # Muse Hub — releases (depends on repos)
    op.drop_index("ix_musehub_releases_tag", table_name="musehub_releases")
    op.drop_index("ix_musehub_releases_repo_id", table_name="musehub_releases")
    op.drop_table("musehub_releases")

    # Muse Hub — sessions (depends on repos)
    op.drop_index("ix_musehub_sessions_is_active", table_name="musehub_sessions")
    op.drop_index("ix_musehub_sessions_started_at", table_name="musehub_sessions")
    op.drop_index("ix_musehub_sessions_repo_id", table_name="musehub_sessions")
    op.drop_table("musehub_sessions")

    # Muse Hub — stars (depends on repos)
    op.drop_index("ix_musehub_stars_user_id", table_name="musehub_stars")
    op.drop_index("ix_musehub_stars_repo_id", table_name="musehub_stars")
    op.drop_table("musehub_stars")

    # Muse Hub — binary artifact storage (depends on repos)
    op.drop_index("ix_musehub_objects_repo_id", table_name="musehub_objects")
    op.drop_table("musehub_objects")

    # Muse Hub — pull requests (depends on repos)
    op.drop_index("ix_musehub_pull_requests_state", table_name="musehub_pull_requests")
    op.drop_index("ix_musehub_pull_requests_repo_id", table_name="musehub_pull_requests")
    op.drop_table("musehub_pull_requests")

    # Muse Hub — issue comments (depends on issues and repos)
    op.drop_index("ix_musehub_issue_comments_created_at", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_parent_id", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_repo_id", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_issue_id", table_name="musehub_issue_comments")
    op.drop_table("musehub_issue_comments")

    # Muse Hub — issues (depends on repos and milestones)
    op.drop_index("ix_musehub_issues_milestone_id", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_state", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_number", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_repo_id", table_name="musehub_issues")
    op.drop_table("musehub_issues")

    # Muse Hub — milestones (depends on repos)
    op.drop_index("ix_musehub_milestones_state", table_name="musehub_milestones")
    op.drop_index("ix_musehub_milestones_number", table_name="musehub_milestones")
    op.drop_index("ix_musehub_milestones_repo_id", table_name="musehub_milestones")
    op.drop_table("musehub_milestones")

    # Muse Hub — commits (depends on repos)
    op.drop_index("ix_musehub_commits_timestamp", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_branch", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_repo_id", table_name="musehub_commits")
    op.drop_table("musehub_commits")

    # Muse Hub — branches (depends on repos)
    op.drop_index("ix_musehub_branches_repo_id", table_name="musehub_branches")
    op.drop_table("musehub_branches")

    # Muse Hub — repos (root)
    op.drop_index("ix_musehub_repos_visibility", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_owner_user_id", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_slug", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_owner", table_name="musehub_repos")
    op.drop_table("musehub_repos")

    # Muse CLI — tags (depends on commits)
    op.drop_index("ix_muse_cli_tags_tag", table_name="muse_cli_tags")
    op.drop_index("ix_muse_cli_tags_commit_id", table_name="muse_cli_tags")
    op.drop_index("ix_muse_cli_tags_repo_id", table_name="muse_cli_tags")
    op.drop_table("muse_cli_tags")

    # Muse CLI — commits (depends on snapshots)
    op.drop_index("ix_muse_cli_commits_parent2_commit_id", table_name="muse_cli_commits")
    op.drop_index("ix_muse_cli_commits_parent_commit_id", table_name="muse_cli_commits")
    op.drop_index("ix_muse_cli_commits_repo_id", table_name="muse_cli_commits")
    op.drop_table("muse_cli_commits")

    op.drop_table("muse_cli_snapshots")
    op.drop_table("muse_cli_objects")

    # Muse VCS
    op.drop_index("ix_note_changes_phrase_id", table_name="note_changes")
    op.drop_table("note_changes")
    op.drop_index("ix_phrases_variation_id", table_name="phrases")
    op.drop_table("phrases")
    op.drop_index("ix_variations_parent_variation_id", table_name="variations")
    op.drop_index("ix_variations_status", table_name="variations")
    op.drop_index("ix_variations_project_id", table_name="variations")
    op.drop_table("variations")

    # Conversations
    op.drop_index("ix_message_actions_message_id", table_name="message_actions")
    op.drop_table("message_actions")
    op.drop_index("ix_conversation_messages_timestamp", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_conversation_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_index("ix_conversations_is_archived", table_name="conversations")
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")

    # Auth & usage
    op.drop_index("ix_access_tokens_token_hash", table_name="access_tokens")
    op.drop_index("ix_access_tokens_user_id", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_index("ix_usage_logs_created_at", table_name="usage_logs")
    op.drop_index("ix_usage_logs_user_id", table_name="usage_logs")
    op.drop_table("usage_logs")
    op.drop_table("users")
