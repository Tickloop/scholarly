"""Create durable research records and pipeline jobs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("canvases", sa.Column("research_brief", sa.JSON(), nullable=True))
    op.create_table(
        "papers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("arxiv_id", sa.String(length=100), nullable=True),
        sa.Column("semantic_scholar_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi"),
        sa.UniqueConstraint("arxiv_id"),
        sa.UniqueConstraint("semantic_scholar_id"),
    )
    op.create_index("ix_papers_normalized_title", "papers", ["normalized_title"])
    op.create_table(
        "canvas_papers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "paper_id"),
    )
    op.create_index("ix_canvas_papers_canvas_id", "canvas_papers", ["canvas_id"])
    op.create_index("ix_canvas_papers_paper_id", "canvas_papers", ["paper_id"])
    op.create_table(
        "paper_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("retrieval_status", sa.String(length=32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id", "url"),
    )
    op.create_index("ix_paper_sources_paper_id", "paper_sources", ["paper_id"])
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("author", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "paper_id", "revision"),
    )
    op.create_index("ix_reviews_canvas_id", "reviews", ["canvas_id"])
    op.create_index("ix_reviews_paper_id", "reviews", ["paper_id"])
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("source_paper_id", sa.String(length=36), nullable=False),
        sa.Column("target_paper_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationships_canvas_id", "relationships", ["canvas_id"])
    op.create_index("ix_relationships_source_paper_id", "relationships", ["source_paper_id"])
    op.create_index("ix_relationships_target_paper_id", "relationships", ["target_paper_id"])
    op.create_table(
        "discovery_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("batch_number", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "batch_number", "candidate_key"),
    )
    op.create_index("ix_discovery_decisions_canvas_id", "discovery_decisions", ["canvas_id"])
    op.create_index("ix_discovery_decisions_run_id", "discovery_decisions", ["run_id"])
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_canvas_id", "evidence", ["canvas_id"])
    op.create_index("ix_evidence_owner_id", "evidence", ["owner_id"])
    op.create_index("ix_evidence_owner_type", "evidence", ["owner_type"])
    op.create_index("ix_evidence_paper_id", "evidence", ["paper_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_canvas_id", "jobs", ["canvas_id"])
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_paper_id", "jobs", ["paper_id"])
    op.create_index("ix_jobs_run_id", "jobs", ["run_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("evidence")
    op.drop_table("discovery_decisions")
    op.drop_table("relationships")
    op.drop_table("reviews")
    op.drop_table("paper_sources")
    op.drop_table("canvas_papers")
    op.drop_table("papers")
    op.drop_column("canvases", "research_brief")
