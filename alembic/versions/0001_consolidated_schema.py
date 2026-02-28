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
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("key_signature", sa.String(50), nullable=True),
        sa.Column("tempo_bpm", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("repo_id"),
    )
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index("ix_musehub_issues_repo_id", "musehub_issues", ["repo_id"])
    op.create_index("ix_musehub_issues_number", "musehub_issues", ["number"])
    op.create_index("ix_musehub_issues_state", "musehub_issues", ["state"])

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


def downgrade() -> None:
    # Muse Hub — webhook deliveries
    op.drop_index(
        "ix_musehub_webhook_deliveries_event_type",
        table_name="musehub_webhook_deliveries",
    )
    op.drop_index(
        "ix_musehub_webhook_deliveries_webhook_id",
        table_name="musehub_webhook_deliveries",
    )
    op.drop_table("musehub_webhook_deliveries")

    # Muse Hub — webhooks
    op.drop_index("ix_musehub_webhooks_repo_id", table_name="musehub_webhooks")
    op.drop_table("musehub_webhooks")

    # Muse Hub — repo starring
    op.drop_index("ix_musehub_stars_user_id", table_name="musehub_stars")
    op.drop_index("ix_musehub_stars_repo_id", table_name="musehub_stars")
    op.drop_table("musehub_stars")

    # Muse Hub — binary artifact storage
    op.drop_index("ix_musehub_objects_repo_id", table_name="musehub_objects")
    op.drop_table("musehub_objects")

    # Muse Hub — pull requests
    op.drop_index("ix_musehub_pull_requests_state", table_name="musehub_pull_requests")
    op.drop_index("ix_musehub_pull_requests_repo_id", table_name="musehub_pull_requests")
    op.drop_table("musehub_pull_requests")

    # Muse Hub — issues
    op.drop_index("ix_musehub_issues_state", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_number", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_repo_id", table_name="musehub_issues")
    op.drop_table("musehub_issues")

    # Muse Hub
    op.drop_index("ix_musehub_commits_timestamp", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_branch", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_repo_id", table_name="musehub_commits")
    op.drop_table("musehub_commits")
    op.drop_index("ix_musehub_branches_repo_id", table_name="musehub_branches")
    op.drop_table("musehub_branches")
    op.drop_index("ix_musehub_repos_visibility", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_owner_user_id", table_name="musehub_repos")
    op.drop_table("musehub_repos")

    # Muse CLI
    op.drop_index("ix_muse_cli_tags_tag", table_name="muse_cli_tags")
    op.drop_index("ix_muse_cli_tags_commit_id", table_name="muse_cli_tags")
    op.drop_index("ix_muse_cli_tags_repo_id", table_name="muse_cli_tags")
    op.drop_table("muse_cli_tags")
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
