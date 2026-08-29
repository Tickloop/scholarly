"""Allow papers with unavailable publication dates.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch:
        batch.alter_column("year", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("month", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    missing = connection.execute(
        sa.text("SELECT count(*) FROM papers WHERE year IS NULL OR month IS NULL")
    ).scalar_one()
    if missing:
        raise RuntimeError(
            "Cannot downgrade while papers with unknown publication dates exist."
        )
    with op.batch_alter_table("papers") as batch:
        batch.alter_column("year", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("month", existing_type=sa.Integer(), nullable=False)
