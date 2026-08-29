"""Add observable MCP tool invocation records.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("tool_invocations"):
        return
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_invocations_canvas_id", "tool_invocations", ["canvas_id"])
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])
    op.create_index("ix_tool_invocations_tool_name", "tool_invocations", ["tool_name"])
    op.create_index("ix_tool_invocations_status", "tool_invocations", ["status"])


def downgrade() -> None:
    op.drop_table("tool_invocations")
