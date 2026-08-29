"""Label PDF, abstract fallback, and unavailable paper sources.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("paper_sources")}
    if "source_type" not in columns:
        op.add_column(
            "paper_sources",
            sa.Column(
                "source_type",
                sa.String(length=32),
                nullable=False,
                server_default="pdf",
            ),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("paper_sources")}
    if "source_type" in columns:
        op.drop_column("paper_sources", "source_type")
