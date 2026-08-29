from __future__ import annotations

from datetime import datetime
from typing import Literal

from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from research_map_backend.schemas import (
    CanvasCreate,
    DiscoveryCandidateInput,
    PaperSourceInput,
    RelationshipInput,
    ReviewInput,
    ReviewSectionInput,
    SourcePageInput,
    validate_paper_reference,
)


class StrictToolModel(ArgModelBase):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class GetCanvasRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)


class CreateCanvasAndStartBuildRequest(StrictToolModel):
    research_goal: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def use_canvas_create_contract(self) -> "CreateCanvasAndStartBuildRequest":
        fallback_name = self.research_goal.strip()[:80]
        parsed = CanvasCreate(
            name=self.name if self.name is not None else fallback_name,
            research_goal=self.research_goal,
        )
        self.name = parsed.name
        self.research_goal = parsed.research_goal
        return self


class GetPaperTextRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)


class ResolvePaperMetadataRequest(StrictToolModel):
    paper_url: str = Field(min_length=1, max_length=2048)

    @field_validator("paper_url")
    @classmethod
    def paper_url_must_use_https(cls, value: str) -> str:
        return validate_paper_reference(value)


class PaperMetadata(StrictToolModel):
    source_provider: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    semantic_scholar_id: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1600, le=2200)
    month: int | None = Field(default=None, ge=1, le=12)
    publication_date: str | None = None
    venue: str = ""
    abstract: str = ""
    url: str = Field(min_length=1, max_length=2048)
    pdf_url: str | None = Field(default=None, max_length=2048)
    citation_count: int = Field(default=0, ge=0)
    open_access_locations: list[dict[str, JsonValue]] = Field(default_factory=list)
    best_oa_location: dict[str, JsonValue] | None = None
    primary_location: dict[str, JsonValue] | None = None
    is_retracted: bool = False
    work_type: str | None = None
    submitted_url: str | None = Field(default=None, max_length=2048)


class UpsertCanvasPaperRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    candidate: PaperMetadata


class SearchPapersRequest(StrictToolModel):
    research_goal: str = Field(min_length=1)
    research_brief: str = ""
    candidate_count: int = Field(default=10, ge=1, le=10)


class StrictDiscoveryCandidate(DiscoveryCandidateInput):
    model_config = ConfigDict(extra="forbid")


def _exact_https_url(value: str) -> str:
    if value != value.strip() or not value.startswith("https://"):
        raise ValueError("URL must be an exact HTTPS URL without surrounding whitespace")
    return value


class DiscoverySearchFinding(StrictToolModel):
    query: str = Field(min_length=1, max_length=1_000)
    finding: str = Field(min_length=1, max_length=2_000)


class DiscoveryScrapedPage(StrictToolModel):
    candidate_id: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    finding: str = Field(min_length=1, max_length=2_000)

    _url = field_validator("url")(_exact_https_url)


class DiscoveryResolvedCandidate(StrictToolModel):
    candidate: PaperMetadata
    evidence_urls: list[str] = Field(min_length=1, max_length=5)

    @field_validator("evidence_urls")
    @classmethod
    def evidence_urls_are_exact_https(cls, values: list[str]) -> list[str]:
        parsed = [_exact_https_url(value) for value in values]
        if len(set(parsed)) != len(parsed):
            raise ValueError("evidence_urls must be unique")
        return parsed


class DiscoveryScore(StrictToolModel):
    relevance: int = Field(ge=0, le=30)
    source_credibility: int = Field(ge=0, le=15)
    age_adjusted_impact: int = Field(ge=0, le=15)
    full_text_availability: int = Field(ge=0, le=15)
    novelty: int = Field(ge=0, le=15)
    relationship_potential: int = Field(ge=0, le=10)


class DiscoverySubmittedDecision(StrictToolModel):
    candidate_id: str = Field(min_length=1, max_length=500)
    bright_evidence_urls: list[str] = Field(default_factory=list, max_length=5)
    score: DiscoveryScore
    accepted: bool
    reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("bright_evidence_urls")
    @classmethod
    def decision_urls_are_exact_https(cls, values: list[str]) -> list[str]:
        parsed = [_exact_https_url(value) for value in values]
        if len(set(parsed)) != len(parsed):
            raise ValueError("bright_evidence_urls must be unique")
        return parsed


class SubmitDiscoveryBatchRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    batch_number: int = Field(ge=1, le=5)
    attempt: int = Field(ge=1, le=3)
    bright_status: Literal["available", "unavailable"] = "available"
    bright_unavailable_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    searches: list[DiscoverySearchFinding] = Field(default_factory=list, max_length=100)
    scraped_pages: list[DiscoveryScrapedPage] = Field(default_factory=list, max_length=300)
    introduced_candidates: list[DiscoveryResolvedCandidate] = Field(default_factory=list, max_length=300)
    decisions: list[DiscoverySubmittedDecision] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_available_or_fallback_shape(self) -> "SubmitDiscoveryBatchRequest":
        if self.bright_status == "available":
            if not 1 <= len(self.searches) <= 100:
                raise ValueError("available discovery requires 1–100 Bright searches")
            if not 3 <= len(self.scraped_pages) <= 300:
                raise ValueError("available discovery requires 3–300 scraped pages")
            if not 3 <= len(self.decisions) <= 10:
                raise ValueError("available discovery requires 3–10 decisions")
            if self.bright_unavailable_reason is not None:
                raise ValueError("available discovery cannot include an unavailable reason")
        else:
            if not self.bright_unavailable_reason:
                raise ValueError("unavailable discovery requires a reason")
            if self.searches or self.scraped_pages or self.introduced_candidates:
                raise ValueError("unavailable discovery cannot claim Bright research")
            if self.decisions and not 3 <= len(self.decisions) <= 10:
                raise ValueError("fallback decisions must be empty or contain 3–10 items")
        candidate_ids = [item.candidate.candidate_id for item in self.introduced_candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("introduced candidate IDs must be unique")
        search_queries = [item.query for item in self.searches]
        if len(set(search_queries)) != len(search_queries):
            raise ValueError("Bright search queries must be unique")
        decision_ids = [item.candidate_id for item in self.decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise ValueError("decision candidate IDs must be unique")
        return self


class RecordDiscoveryDecisionRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    run_id: str | None = None
    batch_number: int = Field(ge=1)
    candidates: list[StrictDiscoveryCandidate] = Field(min_length=1, max_length=10)


class RecordPaperSourceRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    source: PaperSourceInput


class RecordReviewRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    review: ReviewInput


class RecordRelationshipRequest(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    relationship: RelationshipInput


ProgressEventType = Literal[
    "agent.thread.started",
    "agent.message.delta",
    "tool.started",
    "tool.completed",
    "paper.added",
    "review.completed",
    "relationship.added",
]


class ProgressPayload(StrictToolModel):
    agent: str = Field(min_length=1)
    thread_id: str | None = None
    session_id: str | None = None
    name: str | None = None
    paper_id: str | None = None
    title: str | None = None
    batch: int | None = Field(default=None, ge=1)
    status: str | None = None
    source_type: str | None = None
    page_count: int | None = Field(default=None, ge=0)
    message: str | None = None
    code: str | None = None
    relationship_id: str | None = None
    source: str | None = None
    target: str | None = None
    relationship_type: str | None = None
    content: str | None = None
    tool_call_id: str | None = None


class RecordProgressRequest(StrictToolModel):
    run_id: str = Field(min_length=1)
    event_type: ProgressEventType
    payload: ProgressPayload

    @model_validator(mode="after")
    def event_fields_match(self) -> "RecordProgressRequest":
        if self.event_type == "agent.message.delta" and not self.payload.content:
            raise ValueError("agent.message.delta requires payload.content")
        if self.event_type == "paper.added" and not self.payload.paper_id:
            raise ValueError("paper.added requires payload.paper_id")
        if self.event_type == "review.completed" and not self.payload.paper_id:
            raise ValueError("review.completed requires payload.paper_id")
        if self.event_type == "relationship.added" and not self.payload.relationship_id:
            raise ValueError("relationship.added requires payload.relationship_id")
        return self


class CanvasToolData(StrictToolModel):
    id: str
    name: str
    research_goal: str
    research_brief: dict[str, JsonValue] | None = None
    build_status: str


class StoredSourceResult(StrictToolModel):
    id: str
    url: str
    source_type: str
    status: str
    pages: list[SourcePageInput]


class PaperToolResult(StrictToolModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    month: int | None
    abstract: str
    url: str
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    sources: list[StoredSourceResult]
    review: dict[str, ReviewSectionInput] | None = None


class GetCanvasResult(StrictToolModel):
    canvas: CanvasToolData
    papers: list[PaperToolResult]


class GetPaperTextResult(PaperToolResult):
    pass


class ResolvePaperMetadataResult(PaperMetadata):
    pass


class UpsertCanvasPaperResult(StrictToolModel):
    paper_id: str = Field(min_length=1)


class CreateCanvasAndStartBuildResult(StrictToolModel):
    canvas_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: Literal["queued"]
    name: str = Field(min_length=1, max_length=200)


class SearchPapersResult(StrictToolModel):
    papers: list[PaperMetadata]


class RecordDiscoveryDecisionResult(StrictToolModel):
    accepted_papers: dict[str, str]


class SubmitDiscoveryBatchResult(StrictToolModel):
    status: Literal["stored"]
    batch_number: int = Field(ge=1, le=5)
    attempt: int = Field(ge=1, le=3)


class StoredEvidenceResult(StrictToolModel):
    id: str
    page: int | None
    text: str
    source_url: str | None
    source_type: str
    title: str | None = None
    retrieved_at: datetime | None = None
    untrusted: bool = False


class RecordPaperSourceResult(StrictToolModel):
    source_id: str
    source_type: str
    retrieval_status: str
    evidence_ids: list[str]
    evidence: list[StoredEvidenceResult]


class RecordReviewResult(StrictToolModel):
    review_id: str = Field(min_length=1)


class RecordRelationshipResult(StrictToolModel):
    relationship_id: str = Field(min_length=1)


class RecordProgressResult(StrictToolModel):
    run_id: str
    event_type: ProgressEventType
