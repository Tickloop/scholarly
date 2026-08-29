from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Canvas(Base):
    __tablename__ = "canvases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(200))
    research_goal: Mapped[str] = mapped_column(Text)
    research_brief: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    build_status: Mapped[str] = mapped_column(String(32), default="idle")
    batch_count: Mapped[int] = mapped_column(Integer, default=0)
    viewport: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(100), default="research-map-main")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    trueforge_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trueforge_turn_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CanvasPaper(Base):
    __tablename__ = "canvas_papers"
    __table_args__ = (UniqueConstraint("canvas_id", "paper_id"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(32), default="discovery")
    processing_status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PaperSource(Base):
    __tablename__ = "paper_sources"
    __table_args__ = (UniqueConstraint("paper_id", "url"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default="pdf")
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieval_status: Mapped[str] = mapped_column(String(32), default="pending")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages: Mapped[list[dict]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("canvas_id", "paper_id", "revision"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    sections: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    author: Mapped[str] = mapped_column(String(32), default="reviewer")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    owner_type: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    source_paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    target_paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(120))
    explanation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    author: Mapped[str] = mapped_column(String(32), default="connection")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveryDecision(Base):
    __tablename__ = "discovery_decisions"
    __table_args__ = (
        UniqueConstraint("canvas_id", "batch_number", "candidate_key"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True
    )
    batch_number: Mapped[int] = mapped_column(Integer)
    candidate_key: Mapped[str] = mapped_column(Text)
    candidate_metadata: Mapped[dict] = mapped_column("metadata", JSON)
    scores: Mapped[dict] = mapped_column(JSON)
    total_score: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ActiveReviewMessage(Base):
    """A leased, disposable queue message for one durable paper-review job."""

    __tablename__ = "active_review_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    batch_number: Mapped[int] = mapped_column(Integer, index=True)
    processing_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ToolInvocation(Base):
    """Observable MCP tool activity; model reasoning is intentionally excluded."""

    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str | None] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[object | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserRevision(Base):
    __tablename__ = "user_revisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(32))
    before_state: Mapped[dict] = mapped_column(JSON)
    after_state: Mapped[dict] = mapped_column(JSON)
    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(100))
    trueforge_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    agent_name: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
