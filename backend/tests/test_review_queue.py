from pathlib import Path

from sqlalchemy import func, select

from research_map_backend.db import Database
from research_map_backend.models import (
    ActiveReviewMessage,
    AgentRun,
    Canvas,
    CanvasPaper,
    Evidence,
    Job,
    Paper,
    Review,
)
from research_map_backend.pipeline import SECTION_KEYS
from research_map_backend.review_queue import (
    ack_review_message,
    claim_review_message,
    ensure_review_messages,
    recover_leased_review_messages,
    reject_review_message,
)
from research_map_backend.research_store import store_review
from research_map_backend.settings import Settings


def _seed(tmp_path: Path) -> tuple[Database, str, str, str, str]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'queue.sqlite3'}",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Queue", research_goal="Queue reviews")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        paper = Paper(
            title="Queued paper",
            normalized_title="queued paper",
            authors=[],
            year=2024,
            month=1,
            summary="",
            url="https://example.test/paper",
        )
        session.add_all([run, paper])
        session.flush()
        membership = CanvasPaper(canvas_id=canvas.id, paper_id=paper.id)
        session.add(membership)
        session.commit()
        canvas_id, run_id, paper_id = canvas.id, run.id, paper.id
    jobs = ensure_review_messages(
        database,
        canvas_id,
        run_id,
        1,
        {"candidate": paper_id},
        {"candidate": {"candidate_id": "candidate", "title": "Queued paper"}},
    )
    with database.session_context() as session:
        message_id = session.scalar(select(ActiveReviewMessage.id))
    assert message_id is not None
    return database, canvas_id, run_id, paper_id, message_id


def test_success_ack_deletes_only_active_message_and_keeps_job_audit(tmp_path: Path) -> None:
    database, _, _, _, message_id = _seed(tmp_path)
    claimed = claim_review_message(database, message_id, 60)
    assert claimed is not None and claimed["attempt"] == 1
    ack_review_message(database, message_id)
    with database.session_context() as session:
        assert session.get(ActiveReviewMessage, message_id) is None
        job = session.get(Job, claimed["job_id"])
        assert job is not None and job.status == "completed" and job.attempts == 1
    database.dispose()


def test_transient_failure_has_three_claims_then_poison_is_deleted(tmp_path: Path) -> None:
    database, canvas_id, _, paper_id, message_id = _seed(tmp_path)
    for attempt in range(1, 4):
        claimed = claim_review_message(database, message_id, 60)
        assert claimed is not None and claimed["attempt"] == attempt
        remains = reject_review_message(
            database,
            message_id,
            f"temporary {attempt}",
            transient=True,
            retry_delay_seconds=0,
        )
        assert remains is (attempt < 3)
    assert claim_review_message(database, message_id, 60) is None
    with database.session_context() as session:
        assert session.get(ActiveReviewMessage, message_id) is None
        job = session.scalar(select(Job).where(Job.paper_id == paper_id))
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id, CanvasPaper.paper_id == paper_id
            )
        )
        assert job is not None and job.status == "failed" and job.attempts == 3
        assert membership is not None and membership.processing_status == "failed"
    database.dispose()


def test_permanent_failure_is_deleted_after_first_claim_and_never_repicked(tmp_path: Path) -> None:
    database, _, _, _, message_id = _seed(tmp_path)
    claimed = claim_review_message(database, message_id, 60)
    assert claimed is not None
    assert reject_review_message(
        database,
        message_id,
        "invalid evidence",
        transient=False,
        retry_delay_seconds=0,
    ) is False
    assert claim_review_message(database, message_id, 60) is None
    with database.session_context() as session:
        assert session.scalar(select(func.count()).select_from(ActiveReviewMessage)) == 0
    database.dispose()


def test_crash_after_review_commit_redelivers_and_accepts_new_revision(tmp_path: Path) -> None:
    database, canvas_id, run_id, paper_id, message_id = _seed(tmp_path)
    first = claim_review_message(database, message_id, 60)
    assert first is not None
    with database.session_context() as session:
        evidence = Evidence(
            canvas_id=canvas_id,
            paper_id=paper_id,
            owner_type="source",
            owner_id="source-1",
            source_url="https://example.test/paper",
            excerpt="Evidence",
        )
        session.add(evidence)
        session.commit()
        evidence_id = evidence.id
    review = {
        "sections": {
            key: {"text": "Supported", "evidence_ids": [evidence_id], "confidence": "medium"}
            for key in SECTION_KEYS
        }
    }
    store_review(database, canvas_id, paper_id, review)
    assert recover_leased_review_messages(database) == [run_id]
    second = claim_review_message(database, message_id, 60)
    assert second is not None and second["attempt"] == 2
    store_review(database, canvas_id, paper_id, review)
    ack_review_message(database, message_id)
    with database.session_context() as session:
        revisions = list(
            session.scalars(
                select(Review).where(
                    Review.canvas_id == canvas_id, Review.paper_id == paper_id
                )
            )
        )
        assert [item.revision for item in revisions] == [1, 2]
        assert sum(item.is_current for item in revisions) == 1
        assert session.get(ActiveReviewMessage, message_id) is None
    database.dispose()
