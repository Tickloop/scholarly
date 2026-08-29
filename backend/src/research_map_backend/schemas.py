from datetime import datetime
import re
from urllib.parse import urlparse

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ARXIV_ID_PATTERN = (
    r"(?:[a-z][a-z0-9.-]*/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?"
)

_ABSENCE_PAST_VERBS = (
    r"(?:reported|provided|presented|stated|specified|mentioned|described|"
    r"disclosed|included|documented|given)"
)
_ABSENCE_BASE_VERBS = (
    r"(?:report|provide|present|state|specify|mention|describe|disclose|"
    r"include|document|give)"
)
_ABSENCE_TOPIC = re.compile(
    r"\b(?:evidence|information|details?|data|statistics?|statistical\s+tests?|"
    r"tests?|metrics?|measurements?|values?|intervals?|analys(?:is|es)|"
    r"experiments?|benchmarks?|comparisons?|ablations?|limitations?|methods?|"
    r"approaches?|ideas?|contributions?|citations?|references?|datasets?|"
    r"sample\s+sizes?|implementation|discussion|description|statement|results?|"
    r"findings?)\b"
)
_CURRENT_WORK = (
    r"(?:(?:the|this|current)\s+(?:paper|study|article|manuscript|work)|"
    r"(?:the\s+)?authors?)"
)
_PRIOR_WORK_REFERENCE = re.compile(
    r"(?:^|\b(?:by|from|in)\s+)(?:the\s+)?"
    r"(?:prior|previous|earlier|other)\s+(?:work|studies|research|papers?)\b"
)
_NULL_RESULT_SUBJECT = re.compile(
    r"\b(?:statistically\s+)?significant\b|"
    r"\b(?:detectable|meaningful)\s+(?:effect|difference|improvement|change)s?\b|"
    r"\bevidence\s+(?:of|for|that)\b"
)
_CLAUSE_BOUNDARY = re.compile(
    r"(?:[.;!?]\s*|\n+|,\s*(?:but|although|though|while|whereas)\s+)"
)


def states_information_not_reported(text: str) -> bool:
    """Recognize a narrow, explicit statement that section information is absent."""
    normalized = " ".join(text.replace("’", "'").casefold().split())
    if not normalized:
        return False
    for raw_clause in _CLAUSE_BOUNDARY.split(normalized):
        clause = re.sub(
            r"^(?:however|but|and|also|additionally|moreover)\s*,?\s+",
            "",
            raw_clause.strip(),
        )
        if not clause:
            continue
        terse = re.match(
            rf"^not\s+(?:explicitly\s+)?{_ABSENCE_PAST_VERBS}\b",
            clause,
        )
        if terse and not _PRIOR_WORK_REFERENCE.search(clause[terse.end() :]):
            return True
        active = re.search(
            rf"\b{_CURRENT_WORK}\s+"
            rf"(?:(?:does|do|did)\s+not|(?:doesn't|don't|didn't))\s+"
            rf"(?:explicitly\s+)?{_ABSENCE_BASE_VERBS}\b",
            clause,
        )
        if active:
            return True
        passive = re.match(
            rf"^(?P<subject>.{{1,160}}?)\s+"
            rf"(?:is|are|was|were|has\s+been|have\s+been|had\s+been)\s+"
            rf"not\s+(?:explicitly\s+)?{_ABSENCE_PAST_VERBS}\b",
            clause,
        )
        if passive:
            subject = passive.group("subject")
            generic_subject = subject.strip(" ,:") in {
                "it",
                "this",
                "that",
                "these",
                "those",
            }
            if (
                (generic_subject or _ABSENCE_TOPIC.search(subject))
                and not _NULL_RESULT_SUBJECT.search(subject)
                and not _PRIOR_WORK_REFERENCE.search(subject)
                and not _PRIOR_WORK_REFERENCE.search(clause[passive.end() :])
            ):
                return True
        no_information = re.match(
            rf"^no\s+(?P<subject>.{{1,120}}?)\s+"
            rf"(?:is|are|was|were|has\s+been|have\s+been|had\s+been)\s+"
            rf"(?:explicitly\s+)?{_ABSENCE_PAST_VERBS}\b",
            clause,
        )
        if no_information:
            subject = no_information.group("subject")
            if (
                _ABSENCE_TOPIC.search(subject)
                and not _NULL_RESULT_SUBJECT.search(subject)
                and not _PRIOR_WORK_REFERENCE.search(subject)
                and not _PRIOR_WORK_REFERENCE.search(clause[no_information.end() :])
            ):
                return True
    return False


def normalize_arxiv_identifier(
    value: object, *, keep_version: bool = False
) -> str | None:
    """Return one canonical arXiv identity, including legacy category aliases."""
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
        if parsed.hostname.casefold() not in {"arxiv.org", "www.arxiv.org"}:
            return None
        path = parsed.path.rstrip("/")
        if path.startswith("/abs/"):
            cleaned = path.removeprefix("/abs/")
        elif path.startswith("/pdf/"):
            cleaned = path.removeprefix("/pdf/").removesuffix(".pdf")
        else:
            return None
    cleaned = cleaned.removeprefix("arXiv:").removeprefix("arxiv:").strip()
    match = re.fullmatch(ARXIV_ID_PATTERN, cleaned, re.IGNORECASE)
    if match is None:
        return None
    version_match = re.search(r"v\d+$", cleaned, re.IGNORECASE)
    version = version_match.group(0).casefold() if version_match else ""
    base = re.sub(r"v\d+$", "", cleaned, flags=re.IGNORECASE)
    if "/" in base:
        archive, serial = base.split("/", 1)
        # Old dotted subject classes are aliases; the export API uses the
        # archive before the first dot (math.GT/... -> math/...).
        archive = archive.split(".", 1)[0]
        base = f"{archive}/{serial}"
    return base.casefold() + (version if keep_version else "")


def validate_paper_reference(value: str) -> str:
    """Accept an HTTPS paper URL or a bare DOI/arXiv identifier."""
    cleaned = value.strip()
    if cleaned.casefold().startswith("http://"):
        raise ValueError("Remote paper links must use HTTPS.")
    parsed = urlparse(cleaned)
    if parsed.scheme.casefold() == "https":
        if parsed.hostname and parsed.username is None and parsed.password is None:
            return cleaned
        raise ValueError("Paper HTTPS URLs must include a public hostname and no credentials.")
    if parsed.scheme and parsed.scheme.casefold() not in {"doi", "arxiv"}:
        raise ValueError("Paper references must be an HTTPS URL, DOI, or arXiv ID.")

    identifier = cleaned
    if identifier.casefold().startswith("doi:"):
        identifier = identifier[4:].strip()
    if re.fullmatch(r"10\.\d{4,9}/[^\s?#]+", identifier, re.IGNORECASE):
        return cleaned

    if identifier.casefold().startswith("arxiv:"):
        identifier = identifier[6:].strip()
    if re.fullmatch(ARXIV_ID_PATTERN, identifier, re.IGNORECASE):
        return cleaned
    raise ValueError("Paper references must be an HTTPS URL, DOI, or arXiv ID.")


class ResearchBriefOutput(BaseModel):
    """Strict autonomous handoff from Main into academic discovery."""

    model_config = ConfigDict(extra="forbid")

    brief_markdown: str = Field(min_length=1)
    canonical_seed_titles: list[str] = Field(min_length=2, max_length=8)

    @field_validator("brief_markdown")
    @classmethod
    def normalize_brief(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Research brief markdown cannot be empty")
        return cleaned

    @field_validator("canonical_seed_titles", mode="before")
    @classmethod
    def normalize_seed_titles(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("Canonical seed titles must be a list")
        generic = {
            "anchor papers",
            "canonical papers",
            "foundational papers",
            "key papers",
            "priority papers",
            "seed papers",
        }
        titles: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("Each canonical seed title must be a string")
            title = " ".join(item.strip().split())
            key = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
            if (
                not title
                or "\n" in item
                or title.endswith(":")
                or len(title) > 300
                or len(key.split()) < 4
                or key in generic
            ):
                raise ValueError(f"Canonical seed title is not a plausible full title: {title}")
            if key in seen:
                continue
            seen.add(key)
            titles.append(title)
        return titles


class CanvasCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    research_goal: str = Field(min_length=1)

    @field_validator("name", "research_goal")
    @classmethod
    def canvas_text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Canvas name and research goal cannot be blank")
        return cleaned


class CanvasUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def canvas_name_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Canvas name cannot be blank")
        return cleaned


class CanvasSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    research_goal: str
    research_brief: dict | None = None
    build_status: str
    created_at: datetime
    updated_at: datetime


class PaperRead(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    month: int | None
    summary: str
    plain_language_summary: str | None = None
    link: str
    processing_status: str
    error: str | None = None
    review: dict[str, str] | None = None
    x: float = 0
    y: float = 0
    pinned: bool = False


class RelationshipRead(BaseModel):
    id: str
    source: str
    target: str
    type: Literal[
        "extends", "contradicts", "same_benchmark", "uses_method", "cites", "related"
    ]
    label: str
    explanation: str


class CanvasSnapshot(BaseModel):
    canvas: CanvasSummary
    papers: list[PaperRead] = Field(default_factory=list)
    relationships: list[RelationshipRead] = Field(default_factory=list)


class HealthRead(BaseModel):
    status: str
    api: str
    database: str
    trueforge: str
    trueforge_url: str
    openai_configured: bool
    brightdata_api_key_configured: bool
    brightdata_mcp_configured: bool
    required_configuration: str
    research_providers: dict[str, str]


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    canvas_id: str
    status: str
    paper_id: str | None = None


class AgentRunDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    canvas_id: str
    agent_name: str
    status: str
    trueforge_session_id: str | None = None
    trueforge_turn_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class DirectAgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["main", "discovery", "reviewer", "connection"]
    prompt: str = Field(min_length=1)
    paper_ids: list[str] = Field(default_factory=list, max_length=2)


class PaperLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    x: float | None = None
    y: float | None = None

    @field_validator("url")
    @classmethod
    def paper_url_must_use_https(cls, value: str) -> str:
        return validate_paper_reference(value)


class PaperUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    authors: list[str] | None = None
    year: int | None = Field(default=None, ge=1600, le=2200)
    month: int | None = Field(default=None, ge=1, le=12)
    summary: str | None = None
    link: str | None = Field(default=None, min_length=1, max_length=2048)
    x: float | None = None
    y: float | None = None
    pinned: bool | None = None

    @field_validator("link")
    @classmethod
    def paper_link_must_use_https(cls, value: str | None) -> str | None:
        return validate_paper_reference(value) if value is not None else None


class PaperLayoutPosition(BaseModel):
    paper_id: str = Field(min_length=1)
    x: float
    y: float
    pinned: bool


class CanvasLayoutUpdate(BaseModel):
    positions: list[PaperLayoutPosition] = Field(max_length=1000)


class ReviewEdit(BaseModel):
    sections: dict[str, str] = Field(min_length=1)


class RelationshipEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=120)
    explanation: str | None = Field(default=None, min_length=1)


class UndoRead(BaseModel):
    undo_token: str


class UndoRequest(BaseModel):
    undo_token: str = Field(min_length=1)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1)
    paper_ids: list[str] = Field(default_factory=list)


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    canvas_id: str
    paper_id: str | None = None
    role: Literal["user", "assistant"]
    agent_name: str
    content: str
    status: str
    created_at: datetime


class ChatSubmission(BaseModel):
    message: ChatMessageRead
    run: AgentRunRead | None = None
    queued: bool


class AgentRunEventRead(BaseModel):
    id: str
    run_id: str
    canvas_id: str
    type: str
    payload: dict
    created_at: datetime


class ToolInvocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    arguments: dict
    result: object | None = None
    status: str
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class RunTimelineRead(BaseModel):
    run: AgentRunDetail
    events: list[AgentRunEventRead]
    tool_invocations: list[ToolInvocationRead]
    duration_ms: int | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)


class DependencyHealthRead(BaseModel):
    status: Literal["ok", "degraded"]
    academic_apis: dict[str, str]
    local_storage: dict[str, int | str]


class DiscoveryCandidateInput(BaseModel):
    # Provider-specific verified metadata must survive a crash so an admitted
    # paper can resume source acquisition without another search.
    model_config = ConfigDict(extra="allow")

    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1600, le=2200)
    month: int | None = Field(default=None, ge=1, le=12)
    abstract: str = ""
    venue: str | None = None
    url: str = Field(min_length=1)
    pdf_url: str | None = None
    semantic_scholar_id: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    citation_count: int | None = Field(default=None, ge=0)
    score: dict[str, int] = Field(default_factory=dict)
    total_score: int = Field(ge=0, le=100)
    accepted: bool
    reason: str = Field(min_length=1)


class SourcePageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    retrieved_at: datetime | None = None
    untrusted: bool = False


class PaperSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    source_type: Literal["pdf", "abstract", "web", "unavailable"] = "pdf"
    local_path: str | None = None
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    retrieval_status: str = "completed"
    page_count: int | None = Field(default=None, ge=1)
    pages: list[SourcePageInput] = Field(default_factory=list)
    error: str | None = None
    retrieved_at: datetime | None = None


class ReviewSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


REVIEW_SECTION_KEYS = {
    "plain_language_summary",
    "core_idea",
    "problem_space",
    "approach",
    "data",
    "novel_contribution",
    "results",
    "benchmarks",
    "statistical_evidence",
    "limitations",
    "cited_ideas_and_differences",
}


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: dict[str, ReviewSectionInput]
    confidence: Literal["low", "medium", "high"] = "medium"
    author: str = "reviewer"

    @model_validator(mode="after")
    def validate_sections(self) -> "ReviewInput":
        keys = set(self.sections)
        # The summary was added after the first stored-review contract. Continue
        # accepting those legacy/manual payloads while autonomous reviewer output
        # validation requires the new evidence-backed section.
        legacy_keys = REVIEW_SECTION_KEYS - {"plain_language_summary"}
        if not legacy_keys.issubset(keys) or not keys.issubset(REVIEW_SECTION_KEYS):
            missing = sorted(legacy_keys - keys)
            extra = sorted(keys - REVIEW_SECTION_KEYS)
            raise ValueError(f"Review sections mismatch; missing={missing}, extra={extra}")
        for section in self.sections.values():
            if (
                not section.evidence_ids
                and not states_information_not_reported(section.text)
            ):
                raise ValueError(
                    "A review section without evidence must explicitly state that the "
                    "information was not reported"
                )
        return self


class RelationshipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_paper_id: str = Field(
        description="Earlier or foundational paper; the UI arrow starts here."
    )
    target_paper_id: str = Field(
        description="Later or dependent paper; the UI arrow ends here."
    )
    type: Literal[
        "extends", "contradicts", "same_benchmark", "uses_method", "cites", "related"
    ]
    label: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=2)
    confidence: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def different_papers(self) -> "RelationshipInput":
        if self.source_paper_id == self.target_paper_id:
            raise ValueError("A relationship requires two different papers")
        return self
