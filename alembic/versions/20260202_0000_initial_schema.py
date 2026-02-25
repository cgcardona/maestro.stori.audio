"""Consolidated initial schema — all tables.

Revision ID: 20260202_0000
Revises:
Create Date: 2026-02-02 00:00:00.000000

Canonical initial migration for Stori Maestro. Creates:
  - users, usage_logs, access_tokens
  - conversations, conversation_messages, message_actions
  - variations, phrases, note_changes (Muse persistent history)

Fresh install: drop the database (or delete SQLite file), then run:
  alembic upgrade head
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260202_0000'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users & auth ─────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('budget_cents', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('budget_limit_cents', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'usage_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_logs_user_id', 'usage_logs', ['user_id'], unique=False)
    op.create_index('ix_usage_logs_created_at', 'usage_logs', ['created_at'], unique=False)

    op.create_table(
        'access_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_access_tokens_user_id', 'access_tokens', ['user_id'], unique=False)
    op.create_index('ix_access_tokens_token_hash', 'access_tokens', ['token_hash'], unique=True)

    # ── Conversations ────────────────────────────────────────────────
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False, server_default='New Conversation'),
        sa.Column('is_archived', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('project_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'], unique=False)
    op.create_index('ix_conversations_project_id', 'conversations', ['project_id'], unique=False)
    op.create_index('ix_conversations_is_archived', 'conversations', ['is_archived'], unique=False)
    op.create_index('ix_conversations_updated_at', 'conversations', ['updated_at'], unique=False)

    op.create_table(
        'conversation_messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('model_used', sa.String(length=100), nullable=True),
        sa.Column('tokens_used', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('cost_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('sse_events', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('extra_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversation_messages_conversation_id', 'conversation_messages', ['conversation_id'], unique=False)
    op.create_index('ix_conversation_messages_timestamp', 'conversation_messages', ['timestamp'], unique=False)

    op.create_table(
        'message_actions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('message_id', sa.String(length=36), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('extra_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['message_id'], ['conversation_messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_message_actions_message_id', 'message_actions', ['message_id'], unique=False)

    # ── Muse persistent variation history ────────────────────────────
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
        sa.Column("controller_changes", sa.JSON(), nullable=True),
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


def downgrade() -> None:
    op.drop_table('note_changes')
    op.drop_table('phrases')
    op.drop_table('variations')
    op.drop_table('message_actions')
    op.drop_table('conversation_messages')
    op.drop_table('conversations')
    op.drop_table('access_tokens')
    op.drop_table('usage_logs')
    op.drop_table('users')
