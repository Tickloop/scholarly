from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from research_map_backend.db import Database
from research_map_backend.models import (
    AgentRun,
    Canvas,
    CanvasPaper,
    DiscoveryDecision,
    Evidence,
    Job,
    Paper,
    PaperSource,
    Relationship,
    Review,
)
from research_map_backend.schemas import (
    normalize_arxiv_identifier,
    CanvasCreate,
    CanvasSnapshot,
    CanvasSummary,
    DiscoveryCandidateInput,
    PaperRead,
    PaperSourceInput,
    RelationshipInput,
    RelationshipRead,
    ReviewInput,
)

REVIEW_SNAPSHOT_KEYS = {
    "core_idea": "coreIdea",
    "problem_space": "problemSpace",
    "approach": "approach",
    "data": "data",
    "novel_contribution": "novelContribution",
    "results": "results",
    "benchmarks": "benchmarks",
    "statistical_evidence": "statisticalEvidence",
    "limitations": "limitations",
    "cited_ideas_and_differences": "citedIdeasAndDifferences",
}
JOB_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "retrying"}
ACTIVE_DISCOVERY_RUN_STATUSES = {"queued", "running"}
ACTIVE_DISCOVERY_JOB_STATUSES = {"queued", "running", "retrying"}
DISCOVERY_CHECKPOINT_MAX_CALLS = 700


def _queue_canvas_build_record(session: Session, canvas: Canvas) -> AgentRun:
    run = AgentRun(canvas_id=canvas.id, status="queued")
    canvas.build_status = "queued"
    session.add(run)
    session.flush()
    session.add(
        Job(
            canvas_id=canvas.id,
            run_id=run.id,
            kind="canvas_build",
            payload={},
        )
    )
    return run


def queue_canvas_build(database: Database, canvas_id: str) -> AgentRun:
    """Create the durable run and job used by every autonomous canvas build."""
    with database.session_context() as session:
        canvas = _require_canvas(session, canvas_id)
        run = _queue_canvas_build_record(session, canvas)
        session.commit()
        return run


def create_canvas_and_queue_build(
    database: Database, payload: CanvasCreate | dict[str, Any]
) -> tuple[Canvas, AgentRun]:
    """Atomically create one canvas and exactly one autonomous build record."""
    parsed = (
        payload
        if isinstance(payload, CanvasCreate)
        else CanvasCreate.model_validate(payload)
    )
    with database.session_context() as session:
        canvas = Canvas(name=parsed.name, research_goal=parsed.research_goal)
        session.add(canvas)
        session.flush()
        run = _queue_canvas_build_record(session, canvas)
        session.commit()
        return canvas, run


def _normalized_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def _normalized_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi.casefold().removeprefix("https://doi.org/").removeprefix("doi:").strip()


def _normalized_arxiv_id(arxiv_id: str | None) -> str | None:
    return normalize_arxiv_identifier(arxiv_id)


def _require_canvas(session: Session, canvas_id: str) -> Canvas:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise ValueError(f"Canvas {canvas_id} does not exist")
    return canvas


def store_research_brief(
    database: Database, canvas_id: str, brief: dict[str, Any]
) -> None:
    if not brief:
        raise ValueError("Research brief cannot be empty")
    with database.session_context() as session:
        canvas = _require_canvas(session, canvas_id)
        stored = dict(brief)
        existing_summary = (
            canvas.research_brief.get("pipeline_summary")
            if isinstance(canvas.research_brief, dict)
            else None
        )
        if isinstance(existing_summary, dict):
            stored["pipeline_summary"] = existing_summary
        canvas.research_brief = stored
        session.commit()


def store_pipeline_summary(
    database: Database, canvas_id: str, summary: dict[str, Any]
) -> None:
    with database.session_context() as session:
        canvas = _require_canvas(session, canvas_id)
        brief = dict(canvas.research_brief or {})
        brief["pipeline_summary"] = summary
        canvas.research_brief = brief
        session.commit()


def _find_paper(session: Session, candidate: DiscoveryCandidateInput) -> Paper | None:
    doi = _normalized_doi(candidate.doi)
    arxiv_id = _normalized_arxiv_id(candidate.arxiv_id)
    identifiers = []
    if doi:
        identifiers.append(Paper.doi == doi)
    if arxiv_id:
        identifiers.append(Paper.arxiv_id == arxiv_id)
    if candidate.semantic_scholar_id:
        identifiers.append(Paper.semantic_scholar_id == candidate.semantic_scholar_id)
    if identifiers:
        paper = session.scalar(select(Paper).where(or_(*identifiers)).limit(1))
        if paper is not None:
            return paper
    return session.scalar(
        select(Paper)
        .where(
            Paper.normalized_title == _normalized_title(candidate.title),
            Paper.year == candidate.year,
        )
        .limit(1)
    )


def _upsert_paper(session: Session, candidate: DiscoveryCandidateInput) -> Paper:
    paper = _find_paper(session, candidate)
    if paper is None:
        paper = Paper(
            title=candidate.title,
            normalized_title=_normalized_title(candidate.title),
            authors=candidate.authors,
            year=candidate.year,
            month=candidate.month,
            summary=candidate.abstract,
            url=candidate.url,
            doi=_normalized_doi(candidate.doi),
            arxiv_id=_normalized_arxiv_id(candidate.arxiv_id),
            semantic_scholar_id=candidate.semantic_scholar_id,
        )
        session.add(paper)
        session.flush()
        return paper

    if not paper.summary and candidate.abstract:
        paper.summary = candidate.abstract
    if not paper.authors and candidate.authors:
        paper.authors = candidate.authors
    if not paper.url and candidate.url:
        paper.url = candidate.url
    paper.doi = paper.doi or _normalized_doi(candidate.doi)
    paper.arxiv_id = paper.arxiv_id or _normalized_arxiv_id(candidate.arxiv_id)
    paper.semantic_scholar_id = (
        paper.semantic_scholar_id or candidate.semantic_scholar_id
    )
    return paper


def record_discovery_batch(
    database: Database,
    canvas_id: str,
    run_id: str | None,
    batch_number: int,
    candidates: list[DiscoveryCandidateInput | dict[str, Any]],
    *,
    complete_batch: bool = True,
) -> dict[str, str]:
    if batch_number < 1:
        raise ValueError("Batch number must be positive")
    parsed = [
        item
        if isinstance(item, DiscoveryCandidateInput)
        else DiscoveryCandidateInput.model_validate(item)
        for item in candidates
    ]
    with database.session_context() as session:
        accepted = _record_discovery_batch_in_session(
            session,
            canvas_id,
            run_id,
            batch_number,
            parsed,
            complete_batch=complete_batch,
        )
        session.commit()
    return accepted


def _record_discovery_batch_in_session(
    session: Session,
    canvas_id: str,
    run_id: str | None,
    batch_number: int,
    parsed: list[DiscoveryCandidateInput],
    *,
    complete_batch: bool,
) -> dict[str, str]:
    canvas = _require_canvas(session, canvas_id)
    if run_id is not None:
        run = session.get(AgentRun, run_id)
        if run is None or run.canvas_id != canvas_id:
            raise ValueError("Run does not belong to the canvas")
    accepted: dict[str, str] = {}
    for candidate in parsed:
        paper = _upsert_paper(session, candidate) if candidate.accepted else None
        existing = session.scalar(
            select(DiscoveryDecision).where(
                DiscoveryDecision.canvas_id == canvas_id,
                DiscoveryDecision.batch_number == batch_number,
                DiscoveryDecision.candidate_key == candidate.candidate_id,
            )
        )
        metadata = candidate.model_dump(exclude={"score", "total_score", "accepted", "reason"})
        if existing is None:
            existing = DiscoveryDecision(
                canvas_id=canvas_id,
                run_id=run_id,
                batch_number=batch_number,
                candidate_key=candidate.candidate_id,
                candidate_metadata=metadata,
                scores=candidate.score,
                total_score=candidate.total_score,
                accepted=candidate.accepted,
                reason=candidate.reason,
            )
            session.add(existing)
        else:
            existing.run_id = run_id
            existing.candidate_metadata = metadata
            existing.scores = candidate.score
            existing.total_score = candidate.total_score
            existing.accepted = candidate.accepted
            existing.reason = candidate.reason
        existing.paper_id = paper.id if paper else None
        if paper is None:
            continue
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.paper_id == paper.id,
            )
        )
        if membership is None:
            session.add(CanvasPaper(canvas_id=canvas_id, paper_id=paper.id))
        accepted[candidate.candidate_id] = paper.id
    if complete_batch:
        canvas.batch_count = max(canvas.batch_count, batch_number)
    return accepted


def _discovery_submission_key(batch_number: int, attempt: int) -> str:
    return f"{batch_number}:{attempt}"


def _canvas_build_job(session: Session, canvas_id: str, run_id: str) -> Job:
    _require_canvas(session, canvas_id)
    run = session.get(AgentRun, run_id)
    if run is None or run.canvas_id != canvas_id:
        raise ValueError("Run does not belong to the canvas")
    job = session.scalar(
        select(Job).where(
            Job.canvas_id == canvas_id,
            Job.run_id == run_id,
            Job.kind == "canvas_build",
        )
    )
    if job is None:
        raise ValueError("Canvas build job does not exist")
    if (
        run.status not in ACTIVE_DISCOVERY_RUN_STATUSES
        or job.status not in ACTIVE_DISCOVERY_JOB_STATUSES
    ):
        raise ValueError("Canvas build is no longer active")
    return job


def clear_pending_discovery_submissions_from_job(job: Job) -> int:
    payload = dict(job.payload or {})
    submissions = payload.get("discovery_submissions")
    if not isinstance(submissions, dict):
        return 0
    retained = {
        key: value
        for key, value in submissions.items()
        if not isinstance(value, dict) or value.get("status") != "pending"
    }
    removed = len(submissions) - len(retained)
    if not removed:
        return 0
    if retained:
        payload["discovery_submissions"] = retained
    else:
        payload.pop("discovery_submissions", None)
    job.payload = payload
    return removed


def cleanup_terminal_discovery_submissions(
    database: Database, run_id: str | None = None
) -> int:
    """Remove only pending shaped handoffs owned by terminal canvas builds."""
    with database.session_context() as session:
        statement = (
            select(Job)
            .join(AgentRun, AgentRun.id == Job.run_id)
            .where(
                Job.kind == "canvas_build",
                or_(
                    AgentRun.status.not_in(ACTIVE_DISCOVERY_RUN_STATUSES),
                    Job.status.not_in(ACTIVE_DISCOVERY_JOB_STATUSES),
                ),
            )
        )
        if run_id is not None:
            statement = statement.where(Job.run_id == run_id)
        removed = sum(
            clear_pending_discovery_submissions_from_job(job)
            for job in session.scalars(statement)
        )
        session.commit()
        return removed


def load_discovery_tool_checkpoint(
    database: Database, canvas_id: str, run_id: str, batch_number: int
) -> list[dict[str, Any]]:
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        checkpoint = (job.payload or {}).get("discovery_tool_checkpoints", {}).get(
            str(batch_number), {}
        )
        calls = checkpoint.get("calls") if isinstance(checkpoint, dict) else None
        if not isinstance(calls, dict):
            return []
        return [dict(item) for item in calls.values() if isinstance(item, dict)]


def record_discovery_tool_checkpoint(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    tool_call_id: str,
    entry: dict[str, Any],
) -> None:
    """Checkpoint bounded, nonsecret provenance and deduplicated call counters."""
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        payload = dict(job.payload or {})
        checkpoints = dict(payload.get("discovery_tool_checkpoints") or {})
        checkpoint = dict(checkpoints.get(str(batch_number)) or {})
        calls = dict(checkpoint.get("calls") or {})
        if tool_call_id not in calls:
            if len(calls) >= DISCOVERY_CHECKPOINT_MAX_CALLS:
                raise ValueError("Discovery tool checkpoint exceeded its call limit")
            calls[tool_call_id] = entry
        checkpoint["calls"] = calls
        checkpoints[str(batch_number)] = checkpoint
        payload["discovery_tool_checkpoints"] = checkpoints
        job.payload = payload
        session.commit()


def submit_discovery_batch(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    attempt: int,
    submission: dict[str, Any],
) -> None:
    key = _discovery_submission_key(batch_number, attempt)
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        payload = dict(job.payload or {})
        submissions = dict(payload.get("discovery_submissions") or {})
        if key in submissions:
            raise ValueError("Discovery batch was already submitted for this attempt")
        submissions[key] = {
            "status": "pending",
            "submission": submission,
            "created_at": datetime.now(UTC).isoformat(),
        }
        payload["discovery_submissions"] = submissions
        job.payload = payload
        session.commit()


def load_discovery_submission(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    attempt: int,
) -> dict[str, Any] | None:
    key = _discovery_submission_key(batch_number, attempt)
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        entry = (job.payload or {}).get("discovery_submissions", {}).get(key)
        if not isinstance(entry, dict) or entry.get("status") != "pending":
            return None
        submission = entry.get("submission")
        return dict(submission) if isinstance(submission, dict) else None


def invalidate_discovery_submission(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    attempt: int,
) -> None:
    _finish_discovery_submission(
        database, canvas_id, run_id, batch_number, attempt, status="invalid"
    )


def _finish_discovery_submission(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    attempt: int,
    *,
    status: str,
) -> None:
    key = _discovery_submission_key(batch_number, attempt)
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        payload = dict(job.payload or {})
        submissions = dict(payload.get("discovery_submissions") or {})
        entry = submissions.get(key)
        if not isinstance(entry, dict) or entry.get("status") != "pending":
            return
        submissions[key] = {
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        payload["discovery_submissions"] = submissions
        job.payload = payload
        session.commit()


def consume_discovery_submission(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    attempt: int,
    candidates: list[dict[str, Any]],
) -> dict[str, str]:
    parsed = [DiscoveryCandidateInput.model_validate(item) for item in candidates]
    key = _discovery_submission_key(batch_number, attempt)
    with database.session_context() as session:
        job = _canvas_build_job(session, canvas_id, run_id)
        payload = dict(job.payload or {})
        submissions = dict(payload.get("discovery_submissions") or {})
        entry = submissions.get(key)
        if not isinstance(entry, dict) or entry.get("status") != "pending":
            raise ValueError("Discovery submission is no longer pending")
        accepted = _record_discovery_batch_in_session(
            session,
            canvas_id,
            run_id,
            batch_number,
            parsed,
            complete_batch=False,
        )
        submissions[key] = {
            "status": "consumed",
            "finished_at": datetime.now(UTC).isoformat(),
        }
        payload["discovery_submissions"] = submissions
        job.payload = payload
        session.commit()
        return accepted


def complete_pipeline_batch(
    database: Database,
    canvas_id: str,
    batch_number: int,
    summary: dict[str, Any],
) -> None:
    """Atomically publish a terminal batch checkpoint and its completed watermark."""
    with database.session_context() as session:
        canvas = _require_canvas(session, canvas_id)
        value = dict(canvas.research_brief or {})
        value["pipeline_summary"] = summary
        canvas.research_brief = value
        canvas.batch_count = max(canvas.batch_count, batch_number)
        session.commit()


def store_paper_source(
    database: Database,
    paper_id: str,
    source: PaperSourceInput | dict[str, Any],
    *,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    parsed = source if isinstance(source, PaperSourceInput) else PaperSourceInput.model_validate(source)
    with database.session_context() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise ValueError(f"Paper {paper_id} does not exist")
        stored = session.scalar(
            select(PaperSource).where(
                PaperSource.paper_id == paper_id, PaperSource.url == parsed.url
            )
        )
        if stored is None:
            stored = PaperSource(paper_id=paper_id, url=parsed.url)
            session.add(stored)
            session.flush()
        stored.source_type = parsed.source_type
        stored.local_path = parsed.local_path
        stored.sha256 = parsed.sha256
        stored.retrieval_status = parsed.retrieval_status
        stored.page_count = parsed.page_count
        stored.pages = [page.model_dump() for page in parsed.pages]
        stored.error = parsed.error
        stored.retrieved_at = parsed.retrieved_at or datetime.now(UTC)

        if canvas_id is not None:
            membership = session.scalar(
                select(CanvasPaper.id).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id == paper_id,
                )
            )
            if membership is None:
                raise ValueError("Paper does not belong to the canvas")
            canvas_ids = [canvas_id]
        else:
            canvas_ids = list(
                session.scalars(
                    select(CanvasPaper.canvas_id).where(CanvasPaper.paper_id == paper_id)
                )
            )
        evidence_ids: list[str] = []
        for canvas_id in canvas_ids:
            for page in parsed.pages:
                evidence = session.scalar(
                    select(Evidence).where(
                        Evidence.canvas_id == canvas_id,
                        Evidence.paper_id == paper_id,
                        Evidence.owner_type
                        == ("web_source" if parsed.source_type == "web" else "source"),
                        Evidence.owner_id == stored.id,
                        Evidence.page == page.page,
                        Evidence.excerpt == page.text,
                    )
                )
                if evidence is None:
                    evidence = Evidence(
                        canvas_id=canvas_id,
                        paper_id=paper_id,
                        owner_type=(
                            "web_source" if parsed.source_type == "web" else "source"
                        ),
                        owner_id=stored.id,
                        source_url=stored.url,
                        page=page.page,
                        excerpt=page.text,
                    )
                    session.add(evidence)
                    session.flush()
                evidence_ids.append(evidence.id)
        session.commit()
        evidence_rows = list(
            session.scalars(
                select(Evidence).where(Evidence.id.in_(evidence_ids))
            )
        ) if evidence_ids else []
        return {
            "source_id": stored.id,
            "source_type": stored.source_type,
            "retrieval_status": stored.retrieval_status,
            "evidence_ids": evidence_ids,
            "evidence": [
                {
                    "id": item.id,
                    "page": item.page,
                    "text": item.excerpt,
                    "source_url": item.source_url,
                    "source_type": stored.source_type,
                    "title": next(
                        (
                            page.title
                            for page in parsed.pages
                            if page.text == item.excerpt and page.title
                        ),
                        None,
                    ),
                    "retrieved_at": stored.retrieved_at,
                    "untrusted": stored.source_type == "web",
                }
                for item in evidence_rows
            ],
        }


def _validate_evidence(
    session: Session, canvas_id: str, paper_id: str, evidence_ids: set[str]
) -> None:
    if not evidence_ids:
        return
    valid = set(
        session.scalars(
            select(Evidence.id).where(
                Evidence.id.in_(evidence_ids),
                Evidence.canvas_id == canvas_id,
                Evidence.paper_id == paper_id,
            )
        )
    )
    missing = evidence_ids - valid
    if missing:
        raise ValueError(f"Evidence does not belong to this canvas and paper: {sorted(missing)}")


def store_review(
    database: Database,
    canvas_id: str,
    paper_id: str,
    review: ReviewInput | dict[str, Any],
    *,
    protected: bool = False,
    replace_protected: bool = False,
) -> str:
    parsed = review if isinstance(review, ReviewInput) else ReviewInput.model_validate(review)
    with database.session_context() as session:
        _require_canvas(session, canvas_id)
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id, CanvasPaper.paper_id == paper_id
            )
        )
        if membership is None:
            raise ValueError("Paper does not belong to the canvas")
        evidence_ids = {
            evidence_id
            for section in parsed.sections.values()
            for evidence_id in section.evidence_ids
        }
        _validate_evidence(session, canvas_id, paper_id, evidence_ids)
        current = list(
            session.scalars(
                select(Review).where(
                    Review.canvas_id == canvas_id,
                    Review.paper_id == paper_id,
                    Review.is_current.is_(True),
                )
            )
        )
        if any(item.protected for item in current) and not replace_protected:
            raise ValueError("Current user-protected review cannot be replaced")
        for item in current:
            item.is_current = False
        revision = (
            session.scalar(
                select(func.max(Review.revision)).where(
                    Review.canvas_id == canvas_id, Review.paper_id == paper_id
                )
            )
            or 0
        ) + 1
        stored = Review(
            canvas_id=canvas_id,
            paper_id=paper_id,
            sections={key: value.model_dump() for key, value in parsed.sections.items()},
            confidence=parsed.confidence,
            author=parsed.author,
            protected=protected,
            revision=revision,
        )
        session.add(stored)
        membership.processing_status = "reviewed"
        membership.error = None
        session.commit()
        return stored.id


def mark_paper_failed(
    database: Database, canvas_id: str, paper_id: str, error: str
) -> None:
    if not error.strip():
        raise ValueError("Paper failure reason cannot be empty")
    with database.session_context() as session:
        _require_canvas(session, canvas_id)
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.paper_id == paper_id,
            )
        )
        if membership is None:
            raise ValueError("Paper does not belong to the canvas")
        membership.processing_status = "failed"
        membership.error = error.strip()
        session.commit()


def store_relationship(
    database: Database,
    canvas_id: str,
    relationship: RelationshipInput | dict[str, Any],
    *,
    author: str = "connection",
    protected: bool = False,
    replace_protected: bool = False,
) -> str:
    parsed = (
        relationship
        if isinstance(relationship, RelationshipInput)
        else RelationshipInput.model_validate(relationship)
    )
    with database.session_context() as session:
        _require_canvas(session, canvas_id)
        member_ids = set(
            session.scalars(
                select(CanvasPaper.paper_id).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id.in_(
                        [parsed.source_paper_id, parsed.target_paper_id]
                    ),
                )
            )
        )
        if member_ids != {parsed.source_paper_id, parsed.target_paper_id}:
            raise ValueError("Both relationship papers must belong to the canvas")
        source_paper = session.get(Paper, parsed.source_paper_id)
        target_paper = session.get(Paper, parsed.target_paper_id)
        if source_paper is None or target_paper is None:
            raise ValueError("Both relationship papers must exist")
        if (
            source_paper.year is not None
            and target_paper.year is not None
            and source_paper.year > target_paper.year
        ):
            raise ValueError(
                "Relationship source must be the earlier or foundational paper; "
                "the target must be the later or dependent paper"
            )
        evidence = list(
            session.scalars(
                select(Evidence).where(
                    Evidence.id.in_(set(parsed.evidence_ids)),
                    Evidence.canvas_id == canvas_id,
                )
            )
        )
        if set(parsed.evidence_ids) != {item.id for item in evidence}:
            raise ValueError("Relationship evidence does not belong to the canvas")
        if {item.paper_id for item in evidence} != {
            parsed.source_paper_id,
            parsed.target_paper_id,
        }:
            raise ValueError("Relationship evidence must include both papers")

        current = list(
            session.scalars(
                select(Relationship).where(
                    Relationship.canvas_id == canvas_id,
                    Relationship.source_paper_id == parsed.source_paper_id,
                    Relationship.target_paper_id == parsed.target_paper_id,
                    Relationship.type == parsed.type,
                    Relationship.is_current.is_(True),
                    Relationship.deleted_at.is_(None),
                )
            )
        )
        if any(item.protected for item in current) and not replace_protected:
            raise ValueError("Current user-protected relationship cannot be replaced")
        for item in current:
            item.is_current = False
        revision = (
            session.scalar(
                select(func.max(Relationship.revision)).where(
                    Relationship.canvas_id == canvas_id,
                    Relationship.source_paper_id == parsed.source_paper_id,
                    Relationship.target_paper_id == parsed.target_paper_id,
                    Relationship.type == parsed.type,
                )
            )
            or 0
        ) + 1
        stored = Relationship(
            canvas_id=canvas_id,
            source_paper_id=parsed.source_paper_id,
            target_paper_id=parsed.target_paper_id,
            type=parsed.type,
            label=parsed.label,
            explanation=parsed.explanation,
            confidence=parsed.confidence,
            author=author,
            protected=protected,
            revision=revision,
        )
        session.add(stored)
        session.flush()
        for item in evidence:
            session.add(
                Evidence(
                    canvas_id=canvas_id,
                    paper_id=item.paper_id,
                    owner_type="relationship",
                    owner_id=stored.id,
                    source_url=item.source_url,
                    page=item.page,
                    excerpt=item.excerpt,
                )
            )
        session.commit()
        return stored.id


def create_job(
    database: Database,
    canvas_id: str,
    run_id: str | None,
    kind: str,
    payload: dict[str, Any],
    *,
    paper_id: str | None = None,
) -> str:
    with database.session_context() as session:
        _require_canvas(session, canvas_id)
        if run_id is not None:
            run = session.get(AgentRun, run_id)
            if run is None or run.canvas_id != canvas_id:
                raise ValueError("Run does not belong to the canvas")
        if paper_id is not None:
            membership = session.scalar(
                select(CanvasPaper.id).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id == paper_id,
                )
            )
            if membership is None:
                raise ValueError("Paper does not belong to the canvas")
        job = Job(
            canvas_id=canvas_id,
            run_id=run_id,
            paper_id=paper_id,
            kind=kind,
            payload=payload,
        )
        session.add(job)
        session.commit()
        return job.id


def update_job(
    database: Database, job_id: str, status_value: str, *, error: str | None = None
) -> None:
    if status_value not in JOB_STATUSES:
        raise ValueError(f"Unsupported job status: {status_value}")
    with database.session_context() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} does not exist")
        previous_status = job.status
        job.status = status_value
        job.error = error
        if status_value == "running" and previous_status != "running":
            job.attempts += 1
        if status_value in {"completed", "failed", "cancelled"}:
            job.lease_until = None
            if job.kind == "canvas_build":
                clear_pending_discovery_submissions_from_job(job)
        session.commit()


def lease_job(database: Database, job_id: str, lease_seconds: int) -> bool:
    now = datetime.now(UTC)
    with database.session_context() as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in {"queued", "retrying"}:
            return False
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error = "Job exhausted its maximum attempts."
            if job.kind == "canvas_build":
                clear_pending_discovery_submissions_from_job(job)
            session.commit()
            return False
        job.status = "running"
        job.attempts += 1
        job.lease_until = now + timedelta(seconds=lease_seconds)
        session.commit()
        return True


def heartbeat_job(database: Database, job_id: str, lease_seconds: int) -> bool:
    with database.session_context() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "running":
            return False
        job.lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        session.commit()
        return True


def recover_interrupted_jobs(database: Database) -> list[str]:
    """Return interrupted run IDs after making their durable jobs runnable again."""
    from research_map_backend.review_queue import recover_leased_review_messages

    run_ids: set[str] = set(recover_leased_review_messages(database))
    with database.session_context() as session:
        jobs = list(
            session.scalars(
                select(Job).where(
                    Job.status == "running",
                    Job.kind.in_(("canvas_build", "paper_link", "direct_agent")),
                )
            )
        )
        for job in jobs:
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error = "Job exhausted its maximum attempts after restart."
                job.lease_until = None
                if job.kind == "canvas_build":
                    clear_pending_discovery_submissions_from_job(job)
                continue
            job.status = "retrying"
            job.error = "Recovered after worker restart."
            job.lease_until = None
            if job.run_id:
                run = session.get(AgentRun, job.run_id)
                if run is not None and run.status == "running":
                    run.status = "queued"
                    run_ids.add(run.id)
        session.commit()
    cleanup_terminal_discovery_submissions(database)
    return sorted(run_ids)


def get_pipeline_papers(
    database: Database, canvas_id: str, paper_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    with database.session_context() as session:
        _require_canvas(session, canvas_id)
        statement = (
            select(Paper, CanvasPaper)
            .join(CanvasPaper, CanvasPaper.paper_id == Paper.id)
            .where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.deleted_at.is_(None),
            )
            .order_by(Paper.year.is_(None), Paper.year, Paper.title)
        )
        if paper_ids is not None:
            statement = statement.where(Paper.id.in_(paper_ids))
        papers = list(session.execute(statement).all())
        result = []
        for paper, membership in papers:
            override = membership.metadata_override or {}
            sources = list(
                session.scalars(
                    select(PaperSource).where(PaperSource.paper_id == paper.id)
                )
            )
            review = session.scalar(
                select(Review).where(
                    Review.canvas_id == canvas_id,
                    Review.paper_id == paper.id,
                    Review.is_current.is_(True),
                )
            )
            result.append(
                {
                    "id": paper.id,
                    "title": override.get("title", paper.title),
                    "authors": override.get("authors", paper.authors),
                    "year": override.get("year", paper.year),
                    "month": override.get("month", paper.month),
                    "abstract": override.get("summary", paper.summary),
                    "url": override.get("link", paper.url),
                    "doi": paper.doi,
                    "arxiv_id": paper.arxiv_id,
                    "semantic_scholar_id": paper.semantic_scholar_id,
                    "sources": [
                        {
                            "id": source.id,
                            "url": source.url,
                            "source_type": source.source_type,
                            "status": source.retrieval_status,
                            "pages": source.pages,
                        }
                        for source in sources
                    ],
                    "review": review.sections if review else None,
                }
            )
        return result


def get_canvas_snapshot(session: Session, canvas_id: str) -> CanvasSnapshot:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    rows = session.execute(
        select(Paper, CanvasPaper)
        .join(CanvasPaper, CanvasPaper.paper_id == Paper.id)
        .where(
            CanvasPaper.canvas_id == canvas_id,
            CanvasPaper.deleted_at.is_(None),
        )
        .order_by(Paper.year.is_(None), Paper.year, Paper.title)
    ).all()
    papers: list[PaperRead] = []
    for paper, membership in rows:
        override = membership.metadata_override or {}
        review = session.scalar(
            select(Review).where(
                Review.canvas_id == canvas_id,
                Review.paper_id == paper.id,
                Review.is_current.is_(True),
            )
        )
        flat_review = None
        plain_language_summary = None
        if review is not None:
            flat_review = {
                output_key: review.sections[input_key]["text"]
                for input_key, output_key in REVIEW_SNAPSHOT_KEYS.items()
            }
            summary_section = review.sections.get("plain_language_summary")
            if isinstance(summary_section, dict):
                summary_text = summary_section.get("text")
                if isinstance(summary_text, str) and summary_text.strip():
                    plain_language_summary = summary_text.strip()
        papers.append(
            PaperRead(
                id=paper.id,
                title=override.get("title", paper.title),
                authors=override.get("authors", paper.authors),
                year=override.get("year", paper.year),
                month=override.get("month", paper.month),
                summary=override.get("summary", paper.summary),
                plain_language_summary=plain_language_summary,
                link=override.get("link", paper.url),
                processing_status=membership.processing_status,
                error=membership.error,
                review=flat_review,
                x=membership.x,
                y=membership.y,
                pinned=membership.pinned,
            )
        )
    active_paper_ids = {paper.id for paper, _ in rows}
    relationships = [
        RelationshipRead(
            id=relationship.id,
            source=relationship.source_paper_id,
            target=relationship.target_paper_id,
            type=relationship.type,
            label=relationship.label,
            explanation=relationship.explanation,
        )
        for relationship in session.scalars(
            select(Relationship).where(
                Relationship.canvas_id == canvas_id,
                Relationship.is_current.is_(True),
                Relationship.deleted_at.is_(None),
                Relationship.source_paper_id.in_(active_paper_ids),
                Relationship.target_paper_id.in_(active_paper_ids),
            )
        )
    ]
    return CanvasSnapshot(
        canvas=CanvasSummary.model_validate(canvas),
        papers=papers,
        relationships=relationships,
    )
