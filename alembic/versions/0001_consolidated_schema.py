"""Consolidated schema — all tables, single migration.

Revision ID: 0001
Revises:
Create Date: 2026-02-27 00:00:00.000000

THIS IS THE ONLY MIGRATION. All new tables are folded in here during
development. Do NOT create new migration files — add tables directly to
upgrade() and their drops (in reverse order) to the top of downgrade().

Single source-of-truth migration for Maestro. Creates:

  Auth & usage
  - maestro_users, maestro_usage_logs, maestro_access_tokens

  Conversations
  - maestro_conversations, maestro_conversation_messages, maestro_message_actions

  Muse — DAW-level variation history
  - muse_variations, muse_phrases, muse_note_changes

  Muse — filesystem commit history
  - muse_objects, muse_snapshots, muse_commits
    (includes parent2_commit_id for merge commits; metadata JSON blob for
    commit-level annotations e.g. tempo_bpm set via ``muse tempo --set``)
  - muse_tags (music-semantic tags attached to commits)

  Muse Hub — remote collaboration backend
  - musehub_repos, musehub_branches, musehub_commits, musehub_issues
  - musehub_issue_milestones (many-to-many join: issues ↔ milestones)
  - musehub_pull_requests (PR workflow; merged_at records exact merge timestamp)
  - musehub_pr_comments (inline review comments on musical diffs within PRs)
  - musehub_objects (content-addressed binary artifact storage)
  - musehub_stars (per-user repo starring for the explore/discover page)
  - musehub_profiles (public user profile pages — bio, avatar, pinned repos)
  - musehub_sessions (recording session metadata — participants, intent, commits)
  - musehub_releases (published version releases with download packages)
  - musehub_release_assets (downloadable file attachments per release with download counts)
  - musehub_webhooks (registered event-driven webhook subscriptions)
  - musehub_webhook_deliveries (delivery log per dispatch attempt; payload column stores JSON body for retry)
  - musehub_render_jobs (async audio render pipeline)
  - musehub_comments, musehub_reactions, musehub_follows, musehub_watches
  - musehub_notifications, musehub_forks, musehub_view_events, musehub_download_events
  - musehub_events (activity event stream)
  - musehub_labels, musehub_issue_labels, musehub_pr_labels (label tagging)
  - musehub_collaborators (repo access control beyond owner)
  - musehub_stash, musehub_stash_entries (git-stash-style temporary shelving)
  - musehub_pr_reviews (reviewer assignment and approval tracking per PR)
  - musehub_repos.settings (nullable JSON column for feature-flag settings)

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
        "maestro_users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("budget_cents", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("budget_limit_cents", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "maestro_usage_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maestro_usage_logs_user_id", "maestro_usage_logs", ["user_id"])
    op.create_index("ix_maestro_usage_logs_created_at", "maestro_usage_logs", ["created_at"])

    op.create_table(
        "maestro_access_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maestro_access_tokens_user_id", "maestro_access_tokens", ["user_id"])
    op.create_index("ix_maestro_access_tokens_token_hash", "maestro_access_tokens", ["token_hash"], unique=True)

    # ── Conversations ─────────────────────────────────────────────────────
    op.create_table(
        "maestro_conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(255), nullable=False, server_default="New Conversation"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("project_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maestro_conversations_user_id", "maestro_conversations", ["user_id"])
    op.create_index("ix_maestro_conversations_project_id", "maestro_conversations", ["project_id"])
    op.create_index("ix_maestro_conversations_is_archived", "maestro_conversations", ["is_archived"])
    op.create_index("ix_maestro_conversations_updated_at", "maestro_conversations", ["updated_at"])

    op.create_table(
        "maestro_conversation_messages",
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
        sa.ForeignKeyConstraint(["conversation_id"], ["maestro_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maestro_conversation_messages_conversation_id", "maestro_conversation_messages", ["conversation_id"])
    op.create_index("ix_maestro_conversation_messages_timestamp", "maestro_conversation_messages", ["timestamp"])

    op.create_table(
        "maestro_message_actions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extra_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["message_id"], ["maestro_conversation_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maestro_message_actions_message_id", "maestro_message_actions", ["message_id"])

    # ── Muse VCS — DAW-level variation history ────────────────────────────
    op.create_table(
        "muse_variations",
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
        sa.Column("parent_variation_id", sa.String(36), sa.ForeignKey("muse_variations.variation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent2_variation_id", sa.String(36), sa.ForeignKey("muse_variations.variation_id", ondelete="SET NULL"), nullable=True),
        sa.Column("commit_state_id", sa.String(36), nullable=True),
        sa.Column("is_head", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("variation_id"),
    )
    op.create_index("ix_muse_variations_project_id", "muse_variations", ["project_id"])
    op.create_index("ix_muse_variations_parent_variation_id", "muse_variations", ["parent_variation_id"])

    op.create_table(
        "muse_phrases",
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
        sa.ForeignKeyConstraint(["variation_id"], ["muse_variations.variation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("phrase_id"),
    )
    op.create_index("ix_muse_phrases_variation_id", "muse_phrases", ["variation_id"])

    op.create_table(
        "muse_note_changes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("phrase_id", sa.String(36), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["phrase_id"], ["muse_phrases.phrase_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_muse_note_changes_phrase_id", "muse_note_changes", ["phrase_id"])

    # ── Muse CLI — filesystem commit history ──────────────────────────────
    op.create_table(
        "muse_objects",
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("object_id"),
    )

    op.create_table(
        "muse_snapshots",
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )

    op.create_table(
        "muse_commits",
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
        sa.ForeignKeyConstraint(["snapshot_id"], ["muse_snapshots.snapshot_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("commit_id"),
    )
    op.create_index("ix_muse_commits_repo_id", "muse_commits", ["repo_id"])
    op.create_index("ix_muse_commits_parent_commit_id", "muse_commits", ["parent_commit_id"])
    op.create_index("ix_muse_commits_parent2_commit_id", "muse_commits", ["parent2_commit_id"])

    op.create_table(
        "muse_tags",
        sa.Column("tag_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["commit_id"], ["muse_commits.commit_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id"),
    )
    op.create_index("ix_muse_tags_repo_id", "muse_tags", ["repo_id"])
    op.create_index("ix_muse_tags_commit_id", "muse_tags", ["commit_id"])
    op.create_index("ix_muse_tags_tag", "muse_tags", ["tag"])

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
        # Soft-delete timestamp; non-null means the repo is logically deleted
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("repo_id"),
        sa.UniqueConstraint("owner", "slug", name="uq_musehub_repos_owner_slug"),
    )
    op.create_index("ix_musehub_repos_owner", "musehub_repos", ["owner"])
    op.create_index("ix_musehub_repos_slug", "musehub_repos", ["slug"])
    op.create_index("ix_musehub_repos_owner_user_id", "musehub_repos", ["owner_user_id"])

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

    # ── Muse Hub — issue-milestone join table ─────────────────────────────
    op.create_table(
        "musehub_issue_milestones",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("milestone_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["musehub_issues.issue_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["musehub_milestones.milestone_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "milestone_id"),
    )
    op.create_index(
        "ix_musehub_issue_milestones_milestone_id",
        "musehub_issue_milestones",
        ["milestone_id"],
    )
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
        # Set by merge_pr() at the exact moment of merge; NULL for open/unmerged PRs.
        # Used by the timeline overlay to position PR markers at merge time, not open time.
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pr_id"),
    )
    op.create_index("ix_musehub_pull_requests_repo_id", "musehub_pull_requests", ["repo_id"])
    op.create_index("ix_musehub_pull_requests_state", "musehub_pull_requests", ["state"])

    # ── Muse Hub — PR review comments ────────────────────────────────────
    op.create_table(
        "musehub_pr_comments",
        sa.Column("comment_id", sa.String(36), nullable=False),
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False, server_default="general"),
        sa.Column("target_track", sa.String(255), nullable=True),
        sa.Column("target_beat_start", sa.Float(), nullable=True),
        sa.Column("target_beat_end", sa.Float(), nullable=True),
        sa.Column("target_note_pitch", sa.Integer(), nullable=True),
        sa.Column("parent_comment_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["pr_id"], ["musehub_pull_requests.pr_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index("ix_musehub_pr_comments_pr_id", "musehub_pr_comments", ["pr_id"])
    op.create_index("ix_musehub_pr_comments_repo_id", "musehub_pr_comments", ["repo_id"])
    op.create_index("ix_musehub_pr_comments_parent_comment_id", "musehub_pr_comments", ["parent_comment_id"])
    op.create_index("ix_musehub_pr_comments_created_at", "musehub_pr_comments", ["created_at"])

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
        # Human-readable display name shown in profile header (e.g. "Johann Sebastian Bach")
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        # Physical or virtual location shown on profile card
        sa.Column("location", sa.String(255), nullable=True),
        # Personal website or project homepage URL
        sa.Column("website_url", sa.String(2048), nullable=True),
        # Twitter/X handle without the leading @
        sa.Column("twitter_handle", sa.String(64), nullable=True),
        # True for Public Domain and Creative Commons licensed archive artists
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        # CC attribution string, e.g. "Public Domain", "CC BY 4.0"; null = all rights reserved
        sa.Column("cc_license", sa.String(50), nullable=True),
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
    op.create_index("ix_musehub_profiles_is_verified", "musehub_profiles", ["is_verified"])

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
        # JSON-encoded payload stored so failed deliveries can be retried via redeliver endpoint
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
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
        sa.Column("is_prerelease", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("gpg_signature", sa.Text(), nullable=True),
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

    # ── Muse Hub — release assets ─────────────────────────────────────────
    op.create_table(
        "musehub_release_assets",
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("download_url", sa.String(2048), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"], ["musehub_releases.release_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    op.create_index(
        "ix_musehub_release_assets_release_id", "musehub_release_assets", ["release_id"]
    )
    op.create_index(
        "ix_musehub_release_assets_repo_id", "musehub_release_assets", ["repo_id"]
    )

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
    op.create_index("ix_musehub_comments_author", "musehub_comments", ["author"])
    op.create_index("ix_musehub_comments_created_at", "musehub_comments", ["created_at"])
    op.create_index("ix_musehub_comments_target_type", "musehub_comments", ["target_type"])
    op.create_index("ix_musehub_comments_target_id", "musehub_comments", ["target_id"])

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
    op.create_index("ix_musehub_reactions_repo_id", "musehub_reactions", ["repo_id"])
    op.create_index("ix_musehub_reactions_target_type", "musehub_reactions", ["target_type"])
    op.create_index("ix_musehub_reactions_target_id", "musehub_reactions", ["target_id"])
    op.create_index("ix_musehub_reactions_user_id", "musehub_reactions", ["user_id"])

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
    op.create_index("ix_musehub_notifications_created_at", "musehub_notifications", ["created_at"])

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
    op.create_index("ix_musehub_forks_fork_repo_id", "musehub_forks", ["fork_repo_id"])

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
    op.create_index("ix_musehub_download_events_created_at", "musehub_download_events", ["created_at"])

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

    # Muse Hub — labels (folded from 0003_labels)
    op.create_table(
        "musehub_labels",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "name", name="uq_musehub_labels_repo_name"),
    )
    op.create_index("ix_musehub_labels_repo_id", "musehub_labels", ["repo_id"])

    op.create_table(
        "musehub_issue_labels",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("label_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["musehub_issues.issue_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["musehub_labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id", "label_id"),
    )
    op.create_index("ix_musehub_issue_labels_label_id", "musehub_issue_labels", ["label_id"])

    op.create_table(
        "musehub_pr_labels",
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("label_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["musehub_pull_requests.pr_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["musehub_labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pr_id", "label_id"),
    )
    op.create_index("ix_musehub_pr_labels_label_id", "musehub_pr_labels", ["label_id"])

    # Muse Hub — collaborators (folded from 0004_collaborators)
    op.create_table(
        "musehub_collaborators",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("permission", sa.String(20), nullable=False, server_default="write"),
        sa.Column("invited_by", sa.String(36), nullable=True),
        sa.Column(
            "invited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["maestro_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "user_id", name="uq_musehub_collaborators_repo_user"),
    )
    op.create_index("ix_musehub_collaborators_repo_id", "musehub_collaborators", ["repo_id"])
    op.create_index("ix_musehub_collaborators_user_id", "musehub_collaborators", ["user_id"])

    # Muse Hub — stash (folded from 0005_stash)
    op.create_table(
        "musehub_stash",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("is_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_stash_repo_id", "musehub_stash", ["repo_id"])
    op.create_index("ix_musehub_stash_user_id", "musehub_stash", ["user_id"])

    op.create_table(
        "musehub_stash_entries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("stash_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["stash_id"], ["musehub_stash.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_stash_entries_stash_id", "musehub_stash_entries", ["stash_id"])

    # Muse Hub — PR reviews (folded from 0006_pr_reviews)
    op.create_table(
        "musehub_pr_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("reviewer_username", sa.String(255), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["pr_id"],
            ["musehub_pull_requests.pr_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_pr_reviews_pr_id", "musehub_pr_reviews", ["pr_id"])
    op.create_index(
        "ix_musehub_pr_reviews_reviewer_username",
        "musehub_pr_reviews",
        ["reviewer_username"],
    )
    op.create_index("ix_musehub_pr_reviews_state", "musehub_pr_reviews", ["state"])

    # Muse Hub — repo settings (folded from 0006_repo_settings)
    op.add_column(
        "musehub_repos",
        sa.Column("settings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # Drop in reverse creation order, respecting foreign-key dependencies.

    # Muse Hub — PR reviews (folded from 0006_pr_reviews)
    op.drop_index("ix_musehub_pr_reviews_state", table_name="musehub_pr_reviews")
    op.drop_index(
        "ix_musehub_pr_reviews_reviewer_username", table_name="musehub_pr_reviews"
    )
    op.drop_index("ix_musehub_pr_reviews_pr_id", table_name="musehub_pr_reviews")
    op.drop_table("musehub_pr_reviews")

    # Muse Hub — repo settings (folded from 0006_repo_settings)
    op.drop_column("musehub_repos", "settings")

    # Muse Hub — stash (folded from 0005_stash)
    op.drop_index("ix_musehub_stash_entries_stash_id", table_name="musehub_stash_entries")
    op.drop_table("musehub_stash_entries")
    op.drop_index("ix_musehub_stash_user_id", table_name="musehub_stash")
    op.drop_index("ix_musehub_stash_repo_id", table_name="musehub_stash")
    op.drop_table("musehub_stash")

    # Muse Hub — collaborators (folded from 0004_collaborators)
    op.drop_index("ix_musehub_collaborators_user_id", table_name="musehub_collaborators")
    op.drop_index("ix_musehub_collaborators_repo_id", table_name="musehub_collaborators")
    op.drop_table("musehub_collaborators")

    # Muse Hub — labels (folded from 0003_labels)
    op.drop_index("ix_musehub_pr_labels_label_id", table_name="musehub_pr_labels")
    op.drop_table("musehub_pr_labels")
    op.drop_index("ix_musehub_issue_labels_label_id", table_name="musehub_issue_labels")
    op.drop_table("musehub_issue_labels")
    op.drop_index("ix_musehub_labels_repo_id", table_name="musehub_labels")
    op.drop_table("musehub_labels")

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
    op.drop_index("ix_musehub_download_events_created_at", table_name="musehub_download_events")
    op.drop_index("ix_musehub_download_events_repo_id", table_name="musehub_download_events")
    op.drop_table("musehub_download_events")
    op.drop_index("ix_musehub_view_events_repo_id", table_name="musehub_view_events")
    op.drop_table("musehub_view_events")
    op.drop_index("ix_musehub_forks_fork_repo_id", table_name="musehub_forks")
    op.drop_index("ix_musehub_forks_source_repo_id", table_name="musehub_forks")
    op.drop_table("musehub_forks")
    op.drop_index("ix_musehub_notifications_created_at", table_name="musehub_notifications")
    op.drop_index("ix_musehub_notifications_is_read", table_name="musehub_notifications")
    op.drop_index("ix_musehub_notifications_recipient_id", table_name="musehub_notifications")
    op.drop_table("musehub_notifications")
    op.drop_index("ix_musehub_watches_repo_id", table_name="musehub_watches")
    op.drop_index("ix_musehub_watches_user_id", table_name="musehub_watches")
    op.drop_table("musehub_watches")
    op.drop_index("ix_musehub_follows_followee_id", table_name="musehub_follows")
    op.drop_index("ix_musehub_follows_follower_id", table_name="musehub_follows")
    op.drop_table("musehub_follows")
    op.drop_index("ix_musehub_reactions_user_id", table_name="musehub_reactions")
    op.drop_index("ix_musehub_reactions_target_id", table_name="musehub_reactions")
    op.drop_index("ix_musehub_reactions_target_type", table_name="musehub_reactions")
    op.drop_index("ix_musehub_reactions_repo_id", table_name="musehub_reactions")
    op.drop_table("musehub_reactions")
    op.drop_index("ix_musehub_comments_target_id", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_target_type", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_created_at", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_author", table_name="musehub_comments")
    op.drop_index("ix_musehub_comments_repo_id", table_name="musehub_comments")
    op.drop_table("musehub_comments")

    # Muse Hub — profiles (no FK deps from other tables)
    op.drop_index("ix_musehub_profiles_is_verified", table_name="musehub_profiles")
    op.drop_index("ix_musehub_profiles_username", table_name="musehub_profiles")
    op.drop_table("musehub_profiles")

    # Muse Hub — webhook deliveries (depends on webhooks)
    op.drop_index("ix_musehub_webhook_deliveries_event_type", table_name="musehub_webhook_deliveries")
    op.drop_index("ix_musehub_webhook_deliveries_webhook_id", table_name="musehub_webhook_deliveries")
    op.drop_table("musehub_webhook_deliveries")

    # Muse Hub — webhooks (depends on repos)
    op.drop_index("ix_musehub_webhooks_repo_id", table_name="musehub_webhooks")
    op.drop_table("musehub_webhooks")

    # Muse Hub — release assets (depends on musehub_releases)
    op.drop_index(
        "ix_musehub_release_assets_repo_id", table_name="musehub_release_assets"
    )
    op.drop_index(
        "ix_musehub_release_assets_release_id", table_name="musehub_release_assets"
    )
    op.drop_table("musehub_release_assets")

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

    # Muse Hub — PR review comments (depends on pull_requests)
    op.drop_index("ix_musehub_pr_comments_created_at", table_name="musehub_pr_comments")
    op.drop_index("ix_musehub_pr_comments_parent_comment_id", table_name="musehub_pr_comments")
    op.drop_index("ix_musehub_pr_comments_repo_id", table_name="musehub_pr_comments")
    op.drop_index("ix_musehub_pr_comments_pr_id", table_name="musehub_pr_comments")
    op.drop_table("musehub_pr_comments")

    # Muse Hub — pull requests (depends on repos; merged_at included in table creation)
    op.drop_index("ix_musehub_pull_requests_state", table_name="musehub_pull_requests")
    op.drop_index("ix_musehub_pull_requests_repo_id", table_name="musehub_pull_requests")
    op.drop_table("musehub_pull_requests")

    # Muse Hub — issue comments (depends on issues and repos)
    op.drop_index("ix_musehub_issue_comments_created_at", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_parent_id", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_repo_id", table_name="musehub_issue_comments")
    op.drop_index("ix_musehub_issue_comments_issue_id", table_name="musehub_issue_comments")
    op.drop_table("musehub_issue_comments")

    # Muse Hub — issue-milestone join table (depends on issues and milestones)
    op.drop_index(
        "ix_musehub_issue_milestones_milestone_id",
        table_name="musehub_issue_milestones",
    )
    op.drop_table("musehub_issue_milestones")

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
    op.drop_index("ix_musehub_repos_owner_user_id", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_slug", table_name="musehub_repos")
    op.drop_index("ix_musehub_repos_owner", table_name="musehub_repos")
    op.drop_table("musehub_repos")

    # Muse CLI — tags (depends on commits)
    op.drop_index("ix_muse_tags_tag", table_name="muse_tags")
    op.drop_index("ix_muse_tags_commit_id", table_name="muse_tags")
    op.drop_index("ix_muse_tags_repo_id", table_name="muse_tags")
    op.drop_table("muse_tags")

    # Muse CLI — commits (depends on snapshots)
    op.drop_index("ix_muse_commits_parent2_commit_id", table_name="muse_commits")
    op.drop_index("ix_muse_commits_parent_commit_id", table_name="muse_commits")
    op.drop_index("ix_muse_commits_repo_id", table_name="muse_commits")
    op.drop_table("muse_commits")

    op.drop_table("muse_snapshots")
    op.drop_table("muse_objects")

    # Muse VCS
    op.drop_index("ix_muse_note_changes_phrase_id", table_name="muse_note_changes")
    op.drop_table("muse_note_changes")
    op.drop_index("ix_muse_phrases_variation_id", table_name="muse_phrases")
    op.drop_table("muse_phrases")
    op.drop_index("ix_muse_variations_parent_variation_id", table_name="muse_variations")
    op.drop_index("ix_muse_variations_project_id", table_name="muse_variations")
    op.drop_table("muse_variations")

    # Conversations
    op.drop_index("ix_maestro_message_actions_message_id", table_name="maestro_message_actions")
    op.drop_table("maestro_message_actions")
    op.drop_index("ix_maestro_conversation_messages_timestamp", table_name="maestro_conversation_messages")
    op.drop_index("ix_maestro_conversation_messages_conversation_id", table_name="maestro_conversation_messages")
    op.drop_table("maestro_conversation_messages")
    op.drop_index("ix_maestro_conversations_updated_at", table_name="maestro_conversations")
    op.drop_index("ix_maestro_conversations_is_archived", table_name="maestro_conversations")
    op.drop_index("ix_maestro_conversations_project_id", table_name="maestro_conversations")
    op.drop_index("ix_maestro_conversations_user_id", table_name="maestro_conversations")
    op.drop_table("maestro_conversations")

    # Auth & usage
    op.drop_index("ix_maestro_access_tokens_token_hash", table_name="maestro_access_tokens")
    op.drop_index("ix_maestro_access_tokens_user_id", table_name="maestro_access_tokens")
    op.drop_table("maestro_access_tokens")
    op.drop_index("ix_maestro_usage_logs_created_at", table_name="maestro_usage_logs")
    op.drop_index("ix_maestro_usage_logs_user_id", table_name="maestro_usage_logs")
    op.drop_table("maestro_usage_logs")
    op.drop_table("maestro_users")
