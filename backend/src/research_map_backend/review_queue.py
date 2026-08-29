from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from research_map_backend.db import Database
from research_map_backend.models import ActiveReviewMessage, CanvasPaper, Job

MAX_REVIEW_ATTEMPTS = 3
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def ensure_review_messages(
    database: Database,
    canvas_id: str,
    run_id: str,
    batch_number: int,
    accepted_map: dict[str, str],
    candidates_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """Create one durable job and one disposable queue message per admitted paper."""
    job_ids: list[str] = []
    with database.session_context() as session:
        for candidate_id, paper_id in accepted_map.items():
            job = session.scalar(
                select(Job).where(
                    Job.run_id == run_id,
                    Job.canvas_id == canvas_id,
                    Job.paper_id == paper_id,
                    Job.kind == "paper_review",
                    Job.payload["batch_number"].as_integer() == batch_number,
                )
            )
            if job is None:
                job = Job(
                    canvas_id=canvas_id,
                    run_id=run_id,
                    paper_id=paper_id,
                    kind="paper_review",
                    status="queued",
                    max_attempts=MAX_REVIEW_ATTEMPTS,
                    payload={
                        "batch_number": batch_number,
                        "candidate_id": candidate_id,
                        "candidate": candidates_by_id[candidate_id],
                    },
                )
                session.add(job)
                session.flush()
            job_ids.append(job.id)
            if job.status in TERMINAL_JOB_STATUSES:
                continue
            message = session.scalar(
                select(ActiveReviewMessage).where(
                    ActiveReviewMessage.job_id == job.id
                )
            )
            if message is None:
                session.add(
                    ActiveReviewMessage(
                        job_id=job.id,
                        canvas_id=canvas_id,
                        run_id=run_id,
                        paper_id=paper_id,
                        batch_number=batch_number,
                    )
                )
        session.commit()
    return job_ids


def claim_review_message(
    database: Database, message_id: str, lease_seconds: int
) -> dict[str, Any] | None:
    now = datetime.now(UTC)
    with database.session_context() as session:
        message = session.get(ActiveReviewMessage, message_id)
        if message is None:
            return None
        job = session.get(Job, message.job_id)
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            session.delete(message)
            session.commit()
            return None
        if message.processing_count >= MAX_REVIEW_ATTEMPTS:
            _fail_and_delete(session, message, job, "Review exhausted its maximum attempts.")
            session.commit()
            return None
        available_at = message.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        if message.lease_until is not None or available_at > now:
            return None
        last_error = message.last_error
        message.processing_count += 1
        message.lease_until = now + timedelta(seconds=lease_seconds)
        job.status = "running"
        job.attempts = message.processing_count
        job.lease_until = message.lease_until
        job.error = None
        session.commit()
        return {
            "message_id": message.id,
            "job_id": job.id,
            "canvas_id": message.canvas_id,
            "run_id": message.run_id,
            "paper_id": message.paper_id,
            "batch_number": message.batch_number,
            "attempt": message.processing_count,
            "last_error": last_error,
            "candidate": job.payload.get("candidate", {}),
        }


def heartbeat_review_message(
    database: Database, message_id: str, lease_seconds: int
) -> bool:
    with database.session_context() as session:
        message = session.get(ActiveReviewMessage, message_id)
        if message is None or message.lease_until is None:
            return False
        lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        message.lease_until = lease_until
        job = session.get(Job, message.job_id)
        if job is not None and job.status == "running":
            job.lease_until = lease_until
        session.commit()
        return True


def ack_review_message(database: Database, message_id: str) -> None:
    """Atomically complete the durable job and delete its active queue message."""
    with database.session_context() as session:
        message = session.get(ActiveReviewMessage, message_id)
        if message is None:
            return
        job = session.get(Job, message.job_id)
        if job is not None:
            job.status = "completed"
            job.error = None
            job.lease_until = None
        session.delete(message)
        session.commit()


def reject_review_message(
    database: Database,
    message_id: str,
    error: str,
    *,
    transient: bool,
    retry_delay_seconds: float,
) -> bool:
    """Release a retryable message, or remove a terminal poison message.

    Returns True when the message remains active for another attempt.
    """
    with database.session_context() as session:
        message = session.get(ActiveReviewMessage, message_id)
        if message is None:
            return False
        job = session.get(Job, message.job_id)
        if job is None:
            session.delete(message)
            session.commit()
            return False
        if transient and message.processing_count < MAX_REVIEW_ATTEMPTS:
            message.lease_until = None
            message.available_at = datetime.now(UTC) + timedelta(
                seconds=max(0, retry_delay_seconds)
            )
            message.last_error = error
            job.status = "retrying"
            job.error = error
            job.lease_until = None
            session.commit()
            return True
        _fail_and_delete(session, message, job, error)
        session.commit()
        return False


def _fail_and_delete(session: Any, message: ActiveReviewMessage, job: Job, error: str) -> None:
    job.status = "failed"
    job.error = error
    job.lease_until = None
    membership = session.scalar(
        select(CanvasPaper).where(
            CanvasPaper.canvas_id == message.canvas_id,
            CanvasPaper.paper_id == message.paper_id,
            CanvasPaper.deleted_at.is_(None),
        )
    )
    if membership is not None:
        membership.processing_status = "failed"
        membership.error = error
    session.delete(message)


def recover_leased_review_messages(database: Database) -> list[str]:
    """Release only active review messages that were leased by a dead process."""
    run_ids: set[str] = set()
    with database.session_context() as session:
        messages = list(
            session.scalars(
                select(ActiveReviewMessage).where(
                    ActiveReviewMessage.lease_until.is_not(None)
                )
            )
        )
        for message in messages:
            job = session.get(Job, message.job_id)
            if job is None:
                session.delete(message)
                continue
            message.lease_until = None
            message.available_at = datetime.now(UTC)
            job.lease_until = None
            if message.processing_count >= MAX_REVIEW_ATTEMPTS:
                _fail_and_delete(
                    session,
                    message,
                    job,
                    job.error or "Review exhausted its maximum attempts after restart.",
                )
            else:
                job.status = "retrying"
                job.error = "Recovered leased review message after worker restart."
                run_ids.add(message.run_id)
        session.commit()
    return sorted(run_ids)


def active_batch_message_ids(
    database: Database, canvas_id: str, run_id: str, batch_number: int
) -> list[str]:
    with database.session_context() as session:
        return list(
            session.scalars(
                select(ActiveReviewMessage.id)
                .where(
                    ActiveReviewMessage.canvas_id == canvas_id,
                    ActiveReviewMessage.run_id == run_id,
                    ActiveReviewMessage.batch_number == batch_number,
                )
                .order_by(ActiveReviewMessage.created_at, ActiveReviewMessage.id)
            )
        )


def batch_review_jobs(
    database: Database, canvas_id: str, run_id: str, batch_number: int
) -> list[dict[str, Any]]:
    with database.session_context() as session:
        jobs = list(
            session.scalars(
                select(Job).where(
                    Job.canvas_id == canvas_id,
                    Job.run_id == run_id,
                    Job.kind == "paper_review",
                    Job.payload["batch_number"].as_integer() == batch_number,
                )
            )
        )
        return [
            {
                "id": job.id,
                "paper_id": job.paper_id,
                "status": job.status,
                "error": job.error,
                "attempts": job.attempts,
            }
            for job in jobs
        ]
