import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from research_map_backend.app import create_app
from research_map_backend.db import Database
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    Canvas,
    CanvasPaper,
    Job,
    Paper,
)
from research_map_backend.migrations import upgrade_database
from research_map_backend.pipeline import AutonomousPipelineError
from research_map_backend.runs import RunCoordinator, _parse_research_brief_output
from research_map_backend.settings import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'jobs.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=3,
        job_heartbeat_seconds=1,
        job_lease_seconds=3,
    )
    values.update(overrides)
    return Settings(**values)


async def _main_events(_: Settings, __: str):
    yield {
        "type": "model.message.delta",
        "content": json.dumps(
            {
                "brief_markdown": "Recovered brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
            }
        ),
    }
    yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}


def test_restart_recovers_running_job_and_completes_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path, worker_concurrency=1)
    upgrade_database(settings)
    database = Database(settings)
    with database.session_context() as session:
        canvas = Canvas(name="Restart", research_goal="Recover this run.", build_status="running")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.flush()
        job = Job(
            canvas_id=canvas.id,
            run_id=run.id,
            kind="canvas_build",
            status="running",
            attempts=1,
            lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        session.add(job)
        session.commit()
        run_id, job_id = run.id, job.id
    database.dispose()

    app = create_app(settings, agent_event_source=_main_events, pipeline_runner=None)
    with TestClient(app) as client:
        events = client.get(f"/api/v1/runs/{run_id}/events")
        with app.state.database.session_context() as session:
            recovered_run = session.get(AgentRun, run_id)
            recovered_job = session.get(Job, job_id)

    assert events.status_code == 200
    assert "event: run.completed" in events.text
    assert recovered_run is not None and recovered_run.status == "completed"
    assert recovered_job is not None and recovered_job.status == "completed"
    assert recovered_job.attempts == 2
    assert recovered_job.lease_until is None


def test_terminal_job_failure_cannot_leave_canvas_run_running(tmp_path: Path) -> None:
    settings = _settings(tmp_path, worker_concurrency=1)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(
            name="Terminal safety",
            research_goal="Never leave failed builds running.",
            build_status="running",
        )
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        job = Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build")
        session.add(job)
        session.commit()
        canvas_id, run_id, job_id = canvas.id, run.id, job.id

    coordinator = RunCoordinator(database, settings)

    async def incomplete_execute(_run_id: str) -> None:
        with database.session_context() as session:
            current_run = session.get(AgentRun, run_id)
            current_canvas = session.get(Canvas, canvas_id)
            assert current_run is not None and current_canvas is not None
            current_run.status = "running"
            current_canvas.build_status = "running"
            session.commit()

    coordinator._execute = incomplete_execute  # type: ignore[method-assign]
    asyncio.run(coordinator._execute_leased(run_id))

    with database.session_context() as session:
        stored_run = session.get(AgentRun, run_id)
        stored_canvas = session.get(Canvas, canvas_id)
        stored_job = session.get(Job, job_id)
        failure = session.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.type == "run.failed",
            )
        )
        assert stored_run is not None and stored_run.status == "failed"
        assert stored_canvas is not None and stored_canvas.build_status == "failed"
        assert stored_job is not None and stored_job.status == "failed"
        assert failure is not None
        assert failure.payload["code"] == "run_did_not_complete"
    database.dispose()


def test_worker_pool_is_bounded_at_three(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    active = 0
    maximum = 0

    async def slow_main(_: Settings, __: str):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        yield {
            "type": "model.message.delta",
            "content": json.dumps(
                {
                    "brief_markdown": "Brief.",
                    "canonical_seed_titles": [
                        "Reliable Recovery of Durable Background Agent Workflows",
                        "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                    ],
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}
        active -= 1

    with database.session_context() as session:
        canvas = Canvas(name="Pool", research_goal="Pool.")
        session.add(canvas)
        session.flush()
        for _ in range(5):
            run = AgentRun(canvas_id=canvas.id, status="queued")
            session.add(run)
            session.flush()
            session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()

    async def exercise() -> None:
        coordinator = RunCoordinator(
            database, settings, event_source=slow_main, pipeline_runner=None
        )
        await coordinator.start()
        await coordinator._queue.join()
        await coordinator.stop()

    asyncio.run(exercise())
    database.dispose()

    assert maximum == 3


def test_cancel_marks_run_and_job_and_retry_clones_durable_job(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, worker_concurrency=1)
    database = Database(settings)
    database.create_schema()
    started = asyncio.Event()
    release = asyncio.Event()
    propagated = []

    async def link_runner(**_kwargs):
        started.set()
        await release.wait()
        return {"paper_id": None, "completed_reviews": 0, "relationships": 0}

    async def cancel_remote(_: Settings, session_id: str, turn_id: str) -> bool:
        propagated.append((session_id, turn_id))
        return True

    monkeypatch.setattr("research_map_backend.runs.cancel_trueforge_turn", cancel_remote)
    with database.session_context() as session:
        canvas = Canvas(name="Cancel", research_goal="Cancel.")
        session.add(canvas)
        session.flush()
        paper = Paper(
            title="Queued paper",
            normalized_title="queued paper",
            authors=[],
            year=2024,
            month=1,
            summary="",
            url="https://example.test/paper",
        )
        session.add(paper)
        session.flush()
        session.add(
            CanvasPaper(
                canvas_id=canvas.id,
                paper_id=paper.id,
                origin="user",
                processing_status="queued",
            )
        )
        run = AgentRun(
            canvas_id=canvas.id,
            agent_name="research-map-link-ingestion",
            status="queued",
            trueforge_session_id="session",
            trueforge_turn_id="turn",
        )
        session.add(run)
        session.flush()
        job = Job(
            canvas_id=canvas.id,
            run_id=run.id,
            paper_id=paper.id,
            kind="paper_link",
            payload={"url": "https://example.test/paper"},
        )
        session.add(job)
        session.commit()
        run_id, job_id, paper_id, canvas_id = run.id, job.id, paper.id, canvas.id

    async def exercise() -> str:
        coordinator = RunCoordinator(database, settings, link_pipeline_runner=link_runner)
        await coordinator.start()
        await started.wait()
        assert await coordinator.cancel(run_id) == run_id
        await coordinator._queue.join()
        with database.session_context() as session:
            cancelled_membership = session.scalar(
                select(CanvasPaper).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id == paper_id,
                )
            )
            assert cancelled_membership is not None
            assert cancelled_membership.processing_status == "failed"
            assert cancelled_membership.error == "Cancelled by user."
        retried_id = await coordinator.retry(run_id)
        await coordinator.stop()
        return retried_id

    retried_id = asyncio.run(exercise())
    with database.session_context() as session:
        cancelled_run = session.get(AgentRun, run_id)
        cancelled_job = session.get(Job, job_id)
        retried_run = session.get(AgentRun, retried_id)
        retried_job = session.scalar(select(Job).where(Job.run_id == retried_id))
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.paper_id == paper_id,
            )
        )
        cancelled_event = session.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.type == "run.cancelled",
            )
        )
    database.dispose()

    assert cancelled_run is not None and cancelled_run.status == "cancelled"
    assert cancelled_job is not None and cancelled_job.status == "cancelled"
    assert retried_run is not None and retried_run.status == "queued"
    assert retried_job is not None and retried_job.payload == cancelled_job.payload
    assert retried_job.paper_id == paper_id
    assert membership is not None and membership.processing_status == "queued"
    assert membership.error is None
    assert cancelled_event is not None
    assert cancelled_event.payload["paper_id"] == paper_id
    assert propagated == [("session", "turn")]


@pytest.mark.parametrize(
    ("original_status", "stop_reason", "batch_count", "expected_batch"),
    [
        ("completed", "insufficient_relevant_candidates", 0, 1),
        ("completed", "insufficient_unique_candidates", 0, 1),
        ("completed_with_errors", "academic_search_unavailable", 2, 3),
        ("completed", "academic_search_unavailable", 2, 3),
        ("completed", "academic_search_unavailable_after_checkpoint", 2, 3),
    ],
)
def test_explicit_canvas_retry_resumes_real_work_from_valid_checkpoint(
    tmp_path: Path,
    original_status: str,
    stop_reason: str,
    batch_count: int,
    expected_batch: int,
) -> None:
    settings = _settings(tmp_path, worker_concurrency=1)
    database = Database(settings)
    database.create_schema()
    coverage = [
        {
            "batch": batch,
            "started": True,
            "unique_candidates": 10,
            "accepted": 2,
            "reviewed": 2,
            "relationships": 1,
        }
        for batch in range(1, batch_count + 1)
    ]
    coverage.append(
        {
            "batch": batch_count + 1,
            "started": False,
            "unique_candidates": 0,
            "accepted": 0,
            "reviewed": 0,
            "relationships": 0,
        }
    )
    with database.session_context() as session:
        canvas = Canvas(
            name="Explicit retry",
            research_goal="Resume a valid research checkpoint.",
            build_status=original_status,
            batch_count=batch_count,
            research_brief={
                "text": "Persisted valid brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
                "pipeline_summary": {
                    "status": original_status,
                    "accepted_papers": batch_count * 2,
                    "completed_reviews": batch_count * 2,
                    "relationships": batch_count,
                    "paper_failures": [
                        {"paper_id": "", "error": "Legacy search outage marker."}
                    ],
                    "batches_started": batch_count,
                    "stop_reason": stop_reason,
                    "coverage": coverage,
                },
            },
        )
        session.add(canvas)
        session.flush()
        original = AgentRun(canvas_id=canvas.id, status=original_status)
        session.add(original)
        session.flush()
        session.add(
            Job(
                canvas_id=canvas.id,
                run_id=original.id,
                kind="canvas_build",
                status="completed",
            )
        )
        session.commit()
        canvas_id, original_id = canvas.id, original.id

    observed: list[dict] = []

    async def pipeline_runner(**kwargs):
        with database.session_context() as session:
            current = session.get(Canvas, canvas_id)
            assert current is not None
            summary = current.research_brief["pipeline_summary"]
            observed.append(
                {
                    "batch_count": current.batch_count,
                    "stop_reason": summary["stop_reason"],
                    "coverage": summary["coverage"],
                    "paper_failures": summary["paper_failures"],
                }
            )
        await kwargs["emit"](
            "tool.started",
            {"agent": "discovery", "name": "academic_search", "batch": expected_batch},
        )
        await kwargs["emit"](
            "tool.completed",
            {
                "agent": "discovery",
                "name": "bright_discovery",
                "batch": expected_batch,
                "provider": "bright-data",
                "queries": [
                    {
                        "index": 1,
                        "type": "seed",
                        "request_status": "completed",
                        "citation_count": 5,
                    }
                ],
                "rejection_counts": {"non_primary_citation": 2},
            },
        )
        return {
            "status": "completed",
            "accepted_papers": batch_count * 2,
            "completed_reviews": batch_count * 2,
            "relationships": batch_count,
            "paper_failures": [],
            "batches_started": batch_count,
            "stop_reason": "insufficient_unique_candidates",
            "coverage": coverage[:batch_count],
        }

    async def exercise() -> str:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=_main_events,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        retried_id = await coordinator.retry(original_id)
        await coordinator._queue.join()
        await coordinator.stop()
        return retried_id

    retried_id = asyncio.run(exercise())
    with database.session_context() as session:
        retried = session.get(AgentRun, retried_id)
        events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == retried_id)
                .order_by(AgentRunEvent.id)
            )
        )
    database.dispose()

    assert retried is not None and retried.status == "completed"
    assert observed == [
        {
            "batch_count": batch_count,
            "stop_reason": "in_progress",
            "coverage": coverage[:batch_count],
            "paper_failures": [],
        }
    ]
    assert [event.type for event in events] == [
        "run.started",
        "tool.started",
        "tool.completed",
        "run.completed",
    ]
    assert events[1].payload["batch"] == expected_batch
    assert events[2].payload["name"] == "bright_discovery"
    assert events[2].payload["provider"] == "bright-data"
    assert "url" not in json.dumps(events[2].payload).casefold()


def test_successful_completed_canvas_build_is_not_retryable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(
            name="Complete map",
            research_goal="Keep completed maps terminal.",
            build_status="completed",
            research_brief={
                "text": "Complete brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
                "pipeline_summary": {
                    "status": "completed",
                    "stop_reason": "no_worthwhile_candidates",
                },
            },
        )
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="completed")
        session.add(run)
        session.flush()
        session.add(
            Job(
                canvas_id=canvas.id,
                run_id=run.id,
                kind="canvas_build",
                status="completed",
            )
        )
        session.commit()
        run_id = run.id

    coordinator = RunCoordinator(database, settings)
    with pytest.raises(ValueError, match="explicitly retryable"):
        asyncio.run(coordinator.retry(run_id))
    database.dispose()


@pytest.mark.parametrize(
    ("job_kind", "accepted", "reviewed"),
    [
        ("canvas_build", 0, 0),
        ("canvas_build", 2, 0),
        ("direct_agent", 2, 2),
    ],
)
def test_legacy_completed_search_stop_requires_a_reviewed_build_checkpoint(
    tmp_path: Path, job_kind: str, accepted: int, reviewed: int
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(
            name="Legacy stop",
            research_goal="Retry only useful build checkpoints.",
            build_status="completed",
            batch_count=2,
            research_brief={
                "text": "Stored brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
                "pipeline_summary": {
                    "status": "completed",
                    "accepted_papers": accepted,
                    "completed_reviews": reviewed,
                    "relationships": 0,
                    "paper_failures": [],
                    "batches_started": 2,
                    "stop_reason": "academic_search_unavailable",
                    "coverage": [],
                },
            },
        )
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="completed")
        session.add(run)
        session.flush()
        session.add(
            Job(
                canvas_id=canvas.id,
                run_id=run.id,
                kind=job_kind,
                status="completed",
                payload={"agent": "discovery", "prompt": "debug"}
                if job_kind == "direct_agent"
                else {},
            )
        )
        session.commit()
        run_id = run.id

    coordinator = RunCoordinator(database, settings)
    with pytest.raises(ValueError, match="explicitly retryable"):
        asyncio.run(coordinator.retry(run_id))
    database.dispose()


def test_startup_does_not_reinterpret_terminal_canvas_checkpoint(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(
            name="Terminal checkpoint",
            research_goal="Do not resume without an explicit retry.",
            build_status="completed",
            batch_count=2,
            research_brief={
                "text": "Stored brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
                "pipeline_summary": {
                    "status": "completed",
                    "stop_reason": "academic_search_unavailable_after_checkpoint",
                    "coverage": [],
                },
            },
        )
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id

    async def exercise() -> None:
        coordinator = RunCoordinator(database, settings)
        await coordinator.start()
        await coordinator.stop()

    asyncio.run(exercise())
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        assert canvas is not None
        assert canvas.research_brief["pipeline_summary"]["stop_reason"] == (
            "academic_search_unavailable_after_checkpoint"
        )
        assert canvas.batch_count == 2
    database.dispose()


def test_cancel_after_restart_fans_out_to_every_persisted_specialist_turn(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    targets = [
        ("research-map-discovery", "discovery:1", "session-discovery", "turn-discovery"),
        ("research-map-reviewer", "reviewer:p1", "session-review-1", "turn-review-1"),
        ("research-map-reviewer", "reviewer:p2", "session-review-2", "turn-review-2"),
        ("research-map-connection", "connection:1", "session-connection", "turn-connection"),
    ]
    with database.session_context() as session:
        canvas = Canvas(name="Restart cancel", research_goal="Cancel every specialist")
        session.add(canvas)
        session.flush()
        run = AgentRun(
            canvas_id=canvas.id,
            status="running",
            trueforge_session_id="session-main",
            trueforge_turn_id="turn-main",
        )
        session.add(run)
        session.flush()
        session.add(
            Job(
                canvas_id=canvas.id,
                run_id=run.id,
                kind="canvas_build",
                status="running",
            )
        )
        for agent, thread_id, session_id, turn_id in targets:
            session.add_all(
                [
                    AgentRunEvent(
                        run_id=run.id,
                        canvas_id=canvas.id,
                        type="agent.thread.started",
                        payload={
                            "agent": agent,
                            "thread_id": thread_id,
                            "session_id": session_id,
                        },
                    ),
                    AgentRunEvent(
                        run_id=run.id,
                        canvas_id=canvas.id,
                        type="agent.thread.started",
                        payload={
                            "agent": agent,
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                        },
                    ),
                ]
            )
        session.add_all(
            [
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=canvas.id,
                    type="agent.thread.started",
                    payload={
                        "agent": "research-map-reviewer",
                        "thread_id": "reviewer:p1",
                        "session_id": "session-review-1-retry",
                    },
                ),
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=canvas.id,
                    type="agent.thread.started",
                    payload={
                        "agent": "research-map-reviewer",
                        "thread_id": "reviewer:p1",
                        "turn_id": "turn-review-1-retry",
                    },
                ),
            ]
        )
        session.commit()
        run_id = run.id

    calls: list[tuple[str, str]] = []

    async def cancel_remote(_: Settings, session_id: str, turn_id: str) -> bool:
        calls.append((session_id, turn_id))
        if turn_id == "turn-review-2":
            raise RuntimeError("already terminal")
        return True

    monkeypatch.setattr("research_map_backend.runs.cancel_trueforge_turn", cancel_remote)
    # A new coordinator instance proves cancellation reconstruction does not rely
    # on process-local task state.
    coordinator = RunCoordinator(database, settings)
    assert asyncio.run(coordinator.cancel(run_id)) == run_id
    expected = {
        ("session-main", "turn-main"),
        *((session_id, turn_id) for _, _, session_id, turn_id in targets),
        ("session-review-1-retry", "turn-review-1-retry"),
    }
    with database.session_context() as session:
        outcomes = list(
            session.scalars(
                select(AgentRunEvent).where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.type == "agent.turn.cancelled",
                )
            )
        )
    database.dispose()

    assert set(calls) == expected
    assert len(outcomes) == 6
    failed = [item for item in outcomes if item.payload["cancelled"] is False]
    assert len(failed) == 1
    assert failed[0].payload["turn_id"] == "turn-review-2"
    assert failed[0].payload["error"] == "already terminal"


def test_structured_research_brief_accepts_fence_and_rejects_seedless_output() -> None:
    parsed = _parse_research_brief_output(
        """```json
        {"brief_markdown":"Map foundational RAG work.",
         "canonical_seed_titles":[
           "Dense Passage Retrieval for Open-Domain Question Answering",
           "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"]}
        ```"""
    )

    assert parsed.canonical_seed_titles == [
        "Dense Passage Retrieval for Open-Domain Question Answering",
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    ]
    with pytest.raises(AutonomousPipelineError) as caught:
        _parse_research_brief_output(
            '{"brief_markdown":"Map RAG.","canonical_seed_titles":[]}'
        )
    assert caught.value.code == "invalid_research_brief"


def test_invalid_main_brief_gets_exactly_one_repair_and_persists_seeds(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Repair", research_goal="Map foundational RAG papers.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    requests: list[str] = []

    async def main_source(_: Settings, request: str):
        requests.append(request)
        output = (
            "A prose brief without structured canonical seeds."
            if len(requests) == 1
            else json.dumps(
                {
                    "brief_markdown": "Map the foundational retrieval line.",
                    "canonical_seed_titles": [
                        "Dense Passage Retrieval for Open-Domain Question Answering",
                        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                    ],
                }
            )
        )
        yield {"type": "model.message.delta", "content": output}
        yield {"type": "turn.done", "state": {"status": "done"}}

    received: dict = {}

    async def pipeline(**kwargs):
        received.update(kwargs)
        return {"status": "completed", "stop_reason": "coverage_complete"}

    coordinator = RunCoordinator(
        database, settings, event_source=main_source, pipeline_runner=pipeline
    )
    asyncio.run(coordinator._execute(run_id))

    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        run = session.get(AgentRun, run_id)
        repair_events = list(
            session.scalars(
                select(AgentRunEvent).where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.type == "tool.completed",
                )
            )
        )
    database.dispose()

    assert len(requests) == 2
    assert "Validation error:" in requests[1]
    assert "A prose brief without structured canonical seeds." in requests[1]
    assert run is not None and run.status == "completed"
    assert canvas is not None and canvas.research_brief == {
        "text": "Map the foundational retrieval line.",
        "canonical_seed_titles": [
            "Dense Passage Retrieval for Open-Domain Question Answering",
            "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        ],
    }
    assert received["canonical_seed_titles"] == canvas.research_brief[
        "canonical_seed_titles"
    ]
    assert any(event.payload.get("status") == "repairing" for event in repair_events)


def test_second_invalid_main_brief_fails_without_entering_pipeline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Invalid", research_goal="Map an obscure field.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()
        run_id = run.id

    calls = 0

    async def invalid_source(_: Settings, __: str):
        nonlocal calls
        calls += 1
        yield {"type": "model.message.delta", "content": "still not JSON"}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def forbidden_pipeline(**_kwargs):
        raise AssertionError("Invalid briefs must not reach discovery")

    coordinator = RunCoordinator(
        database,
        settings,
        event_source=invalid_source,
        pipeline_runner=forbidden_pipeline,
    )
    asyncio.run(coordinator._execute(run_id))
    with database.session_context() as session:
        run = session.get(AgentRun, run_id)
        failed = session.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.type == "run.failed",
            )
        )
    database.dispose()

    assert calls == 2
    assert run is not None and run.status == "failed"
    assert failed is not None and failed.payload["code"] == "invalid_research_brief"


def test_recovery_reuses_valid_stored_brief_and_seed_titles(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    stored = {
        "text": "Persisted RAG brief.",
        "canonical_seed_titles": [
            "Dense Passage Retrieval for Open-Domain Question Answering",
            "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        ],
    }
    with database.session_context() as session:
        canvas = Canvas(
            name="Recover seeds",
            research_goal="Map foundational RAG papers.",
            research_brief=stored,
        )
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()
        run_id = run.id

    async def forbidden_main(_: Settings, __: str):
        raise AssertionError("Recovery must reuse its validated stored brief")
        yield

    received: dict = {}

    async def pipeline(**kwargs):
        received.update(kwargs)
        return {"status": "completed", "stop_reason": "coverage_complete"}

    coordinator = RunCoordinator(
        database, settings, event_source=forbidden_main, pipeline_runner=pipeline
    )
    asyncio.run(coordinator._execute(run_id))
    database.dispose()

    assert received["research_brief"] == stored["text"]
    assert received["canonical_seed_titles"] == stored["canonical_seed_titles"]
