"""Add soft deletion, protected user revisions, and canvas chat records.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_column("canvases", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("canvas_papers", sa.Column("metadata_override", sa.JSON(), nullable=True))
    _add_column("canvas_papers", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    _add_column(
        "reviews", sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    _add_column(
        "relationships",
        sa.Column("author", sa.String(length=32), nullable=False, server_default="connection"),
    )
    _add_column(
        "relationships",
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column(
        "relationships", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )

    if not _has_table("user_revisions"):
        op.create_table(
        "user_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_revisions_canvas_id", "user_revisions", ["canvas_id"])
        op.create_index("ix_user_revisions_entity_type", "user_revisions", ["entity_type"])
        op.create_index("ix_user_revisions_entity_id", "user_revisions", ["entity_id"])

    if not _has_table("conversations"):
        op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("trueforge_session_id", sa.String(length=100), nullable=True),
        sa.Column("busy", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_conversations_canvas_id", "conversations", ["canvas_id"])
        op.create_index("ix_conversations_paper_id", "conversations", ["paper_id"])

    if not _has_table("messages"):
        op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_messages_canvas_id", "messages", ["canvas_id"])
        op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
        op.create_index("ix_messages_paper_id", "messages", ["paper_id"])


def _add_column(table: str, column: sa.Column) -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)
    }
    if column.name not in columns:
        op.add_column(table, column)


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("user_revisions")
    op.drop_column("relationships", "deleted_at")
    op.drop_column("relationships", "protected")
    op.drop_column("relationships", "author")
    op.drop_column("reviews", "protected")
    op.drop_column("canvas_papers", "deleted_at")
    op.drop_column("canvas_papers", "metadata_override")
    op.drop_column("canvases", "deleted_at")
