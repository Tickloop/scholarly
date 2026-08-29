"""Create agent runs and replayable run events.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trueforge_session_id", sa.String(length=100), nullable=True),
        sa.Column("trueforge_turn_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_canvas_id", "agent_runs", ["canvas_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_run_events_canvas_id", "agent_run_events", ["canvas_id"]
    )
    op.create_index("ix_agent_run_events_run_id", "agent_run_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_run_events_run_id", table_name="agent_run_events")
    op.drop_index("ix_agent_run_events_canvas_id", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_canvas_id", table_name="agent_runs")
    op.drop_table("agent_runs")
