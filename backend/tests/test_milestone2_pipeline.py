import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from research_map_backend.app import create_app
from research_map_backend.db import Database
from research_map_backend.models import (
    ActiveReviewMessage,
    AgentRun,
    AgentRunEvent,
    Canvas,
    CanvasPaper,
    DiscoveryDecision,
    Job,
    Review,
)
from research_map_backend.pipeline import SECTION_KEYS, _retry_call, run_autonomous_pipeline
from research_map_backend.research_search import ResearchSearchError, search_academic_papers
from research_map_backend.runs import RunCoordinator
from research_map_backend.review_queue import recover_leased_review_messages
from research_map_backend.settings import Settings


def _candidate(batch: int, index: int) -> dict[str, Any]:
    return {
        "source_provider": "mock",
        "candidate_id": f"batch-{batch}-paper-{index}",
        "semantic_scholar_id": f"s2-{batch}-{index}",
        "doi": f"10.1000/{batch}-{index}",
        "arxiv_id": None,
        "title": f"Batch {batch} Paper {index}",
        "authors": ["Researcher"],
        "year": 2000 + batch,
        "month": 1,
        "venue": "Test",
        "abstract": "Verified evidence.",
        "url": f"https://example.test/{batch}/{index}",
        "pdf_url": f"https://example.test/{batch}/{index}.pdf",
        "citation_count": 1,
    }


async def _source(_: Settings, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": candidate["url"],
        "source_type": "abstract",
        "retrieval_status": "abstract_fallback",
        "pages": [{"page": None, "text": candidate["abstract"]}],
    }


def test_complete_five_batch_loop_caps_at_fifty_and_reviewers_at_two(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'five.sqlite3'}",
        openai_api_key="test",
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Five", research_goal="Map five batches.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    batch_calls = 0
    active_reviewers = 0
    max_reviewers = 0

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        nonlocal batch_calls
        assert count == 10
        batch_calls += 1
        return [_candidate(batch_calls, index) for index in range(10)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        nonlocal active_reviewers, max_reviewers
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 25,
                            "source_credibility": 12,
                            "age_adjusted_impact": 10,
                            "full_text_availability": 12,
                            "novelty": 8,
                            "relationship_potential": 8,
                        },
                        "accepted": True,
                        "reason": "Worthwhile.",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            active_reviewers += 1
            max_reviewers = max(max_reviewers, active_reviewers)
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            await asyncio.sleep(0.001)
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
            active_reviewers -= 1
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map five batches.",
            research_brief="Five batch brief.",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    with database.session_context() as session:
        decisions = session.scalar(select(func.count()).select_from(DiscoveryDecision))
        reviews = session.scalar(select(func.count()).select_from(Review))
        canvas = session.get(Canvas, canvas_id)
    database.dispose()

    assert batch_calls == 5
    assert decisions == 50
    assert reviews == 50
    assert max_reviewers == 2
    assert summary["batches_started"] == 5
    assert summary["accepted_papers"] == 50
    assert summary["stop_reason"] == "autonomous_paper_limit_reached"
    assert summary["status"] == "completed"
    assert canvas.research_brief["pipeline_summary"] == summary


def test_duplicate_second_batch_stops_before_starting_it(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'duplicate.sqlite3'}",
        openai_api_key="test",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Duplicates", research_goal="Map duplicates.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    async def candidates(_: str, __: str, ___: int) -> list[dict[str, Any]]:
        return [_candidate(1, index) for index in range(10)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 25,
                            "source_credibility": 12,
                            "age_adjusted_impact": 10,
                            "full_text_availability": 12,
                            "novelty": 8,
                            "relationship_potential": 8,
                        },
                        "accepted": True,
                        "reason": "Worthwhile.",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map duplicates.",
            research_brief="Duplicates.",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    database.dispose()

    assert summary["batches_started"] == 1
    assert summary["stop_reason"] == "insufficient_unique_candidates"
    assert len(summary["coverage"]) == 2
    assert summary["coverage"][1]["started"] is False


def test_partial_provider_pool_exhausts_cleanly_after_three_complete_batches(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'partial-pool.sqlite3'}",
        openai_api_key="test",
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Partial", research_goal="Exhaust a partial pool")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    pool = [_candidate(1 + index // 10, index) for index in range(35)]
    calls: list[int] = []

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        calls.append(count)
        return pool[: min(10 * len(calls), len(pool))]

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": index == 0,
                        "reason": "Best in this batch" if index == 0 else "Less useful",
                    }
                    for index, item in enumerate(supplied)
                ]
            }
        elif agent_name == "research-map-reviewer":
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Exhaust a partial pool",
            research_brief="Partial pool",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    database.dispose()

    assert calls == [10, 10, 10, 10, 10]
    assert summary["status"] == "completed"
    assert summary["batches_started"] == 4
    assert summary["stop_reason"] == "insufficient_unique_candidates"
    assert [item["unique_candidates"] for item in summary["coverage"]] == [
        10,
        10,
        10,
        5,
        0,
    ]
    assert summary["coverage"][3]["started"] is True
    assert summary["coverage"][3]["requested_candidates"] == 10
    assert summary["coverage"][3]["actual_candidates"] == 5
    assert summary["coverage"][-1]["started"] is False
    assert summary["paper_failures"] == []


@pytest.mark.parametrize("candidate_count", [3, 5])
def test_variable_verified_batch_runs_reviews_and_relationships(
    tmp_path: Path, candidate_count: int
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / f'variable-{candidate_count}.sqlite3'}",
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Variable", research_goal="Map a verified topic.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    async def candidates(_: str, __: str, requested: int) -> list[dict[str, Any]]:
        assert requested == 10
        return [_candidate(1, index) for index in range(candidate_count)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            assert prompt.startswith(f"Evaluate the {candidate_count} supplied")
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 25,
                            "source_credibility": 12,
                            "age_adjusted_impact": 10,
                            "full_text_availability": 12,
                            "novelty": 8,
                            "relationship_potential": 8,
                        },
                        "accepted": index < 2,
                        "reason": "Useful verified paper.",
                    }
                    for index, item in enumerate(supplied)
                ]
            }
        elif agent_name == "research-map-reviewer":
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported by the supplied evidence.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        elif agent_name == "research-map-discovery":
            reviews = json.loads(prompt.split("Reviews:\n", 1)[1])
            payload = {
                "pairs": [
                    {
                        "source_paper_id": reviews[0]["paper_id"],
                        "target_paper_id": reviews[1]["paper_id"],
                        "reason": "The papers cover related verified methods.",
                    }
                ]
            }
        else:
            pairs_text, reviews_text = prompt.split("\n\nReviews and evidence IDs:\n", 1)
            pairs = json.loads(pairs_text.split("Shortlisted pairs:\n", 1)[1])
            reviews = json.loads(reviews_text)
            evidence = {item["paper_id"]: item["evidence_ids"][0] for item in reviews}
            pair = pairs[0]
            payload = {
                "relationships": [
                    {
                        "source_paper_id": pair["source_paper_id"],
                        "target_paper_id": pair["target_paper_id"],
                        "relationship_type": "related",
                        "label": "related methods",
                        "explanation": "Both papers study related verified methods.",
                        "confidence": "medium",
                        "source_evidence_ids": [evidence[pair["source_paper_id"]]],
                        "target_evidence_ids": [evidence[pair["target_paper_id"]]],
                    }
                ]
            }
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map a verified topic.",
            research_brief="Variable verified batch.",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    database.dispose()

    assert summary["status"] == "completed"
    assert summary["accepted_papers"] == summary["completed_reviews"] == 2
    assert summary["relationships"] == 1
    assert summary["coverage"] == [
        {
            "batch": 1,
            "started": True,
            "requested_candidates": 10,
            "actual_candidates": candidate_count,
            "unique_candidates": candidate_count,
            "accepted": 2,
            "reviewed": 2,
            "relationships": 1,
        }
    ]


@pytest.mark.parametrize("candidate_count", [1, 2])
def test_tiny_verified_pool_stops_before_discovery(
    tmp_path: Path, candidate_count: int
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / f'tiny-{candidate_count}.sqlite3'}",
        discovery_max_batches=1,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Tiny", research_goal="Map a verified topic.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    model_called = False

    async def candidates(_: str, __: str, ___: int) -> list[dict[str, Any]]:
        return [_candidate(1, index) for index in range(candidate_count)]

    async def agents(_: Settings, __: str, ___: str):
        nonlocal model_called
        model_called = True
        if False:
            yield {}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map a verified topic.",
            research_brief="Tiny pool.",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
        )
    )
    database.dispose()

    assert model_called is False
    assert summary["batches_started"] == 0
    assert summary["stop_reason"] == "insufficient_unique_candidates"
    assert summary["coverage"][0]["requested_candidates"] == 10
    assert summary["coverage"][0]["actual_candidates"] == candidate_count


def test_retry_uses_retry_after_and_stops_after_third_attempt(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, upstream_max_attempts=3, retry_base_seconds=99)
    attempts = 0

    class TemporaryError(RuntimeError):
        retryable = True
        retry_after_seconds = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TemporaryError("rate limited")
        return "ok"

    assert asyncio.run(_retry_call(operation, settings)) == "ok"
    assert attempts == 3


def test_huge_retry_after_is_capped_and_reported_without_long_worker_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        upstream_max_attempts=3,
        retry_base_seconds=99,
        upstream_max_retry_delay_seconds=0.05,
    )
    attempts = 0
    sleeps: list[float] = []
    retry_events: list[dict[str, Any]] = []

    class Throttled(RuntimeError):
        retryable = True
        retry_after_seconds = 39_701
        code = "academic_search_throttled"

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Throttled("OpenAlex asked us to wait eleven hours")
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def on_retry(payload: dict[str, Any]) -> None:
        retry_events.append(payload)

    monkeypatch.setattr("research_map_backend.pipeline.asyncio.sleep", fake_sleep)
    result = asyncio.run(
        _retry_call(operation, settings, on_retry=on_retry)
    )

    assert result == "ok"
    assert attempts == 3
    assert sleeps == [0.05, 0.05]
    assert [event["delay_seconds"] for event in retry_events] == [0.05, 0.05]
    assert [event["requested_delay_seconds"] for event in retry_events] == [39_701, 39_701]
    assert [event["next_attempt"] for event in retry_events] == [2, 3]


def test_restart_during_capped_academic_retry_wait_recovers_build(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'retry-wait-restart.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=1,
        discovery_max_batches=1,
        upstream_max_retry_delay_seconds=10,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Retry restart", research_goal="Recover academic search")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()
        run_id = run.id

    recovered = False
    search_calls = 0

    class Throttled(RuntimeError):
        retryable = True
        retry_after_seconds = 39_701
        code = "academic_search_throttled"

    async def main_source(_: Settings, __: str):
        yield {
            "type": "model.message.delta",
            "content": json.dumps(
                {
                    "brief_markdown": "Persisted retry brief",
                    "canonical_seed_titles": [
                        "Reliable Academic Search Recovery for Autonomous Agents",
                        "Durable Retry Scheduling for Background Research Workflows",
                    ],
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        nonlocal search_calls
        search_calls += 1
        if not recovered:
            raise Throttled("Provider requested an eleven-hour wait")
        return [_candidate(1, index) for index in range(count)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 0,
                            "source_credibility": 0,
                            "age_adjusted_impact": 0,
                            "full_text_availability": 0,
                            "novelty": 0,
                            "relationship_potential": 0,
                        },
                        "accepted": False,
                        "reason": "Not useful for this recovered test.",
                    }
                    for item in supplied
                ]
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def pipeline_runner(**kwargs):
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )

    async def interrupt_wait() -> None:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=main_source,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        for _ in range(200):
            with database.session_context() as session:
                retry_event = next(
                    (
                        event
                        for event in session.scalars(
                            select(AgentRunEvent).where(
                                AgentRunEvent.run_id == run_id,
                                AgentRunEvent.type == "tool.completed",
                            )
                        )
                        if event.payload.get("status") == "retrying"
                    ),
                    None,
                )
            if retry_event is not None:
                assert retry_event.payload["delay_seconds"] == 10
                assert retry_event.payload["requested_delay_seconds"] == 39_701
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("Academic retry event was not persisted")
        await coordinator.stop()

    asyncio.run(interrupt_wait())
    recovered = True

    async def resume() -> None:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=main_source,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        await coordinator._queue.join()
        await coordinator.stop()

    asyncio.run(resume())
    with database.session_context() as session:
        run = session.get(AgentRun, run_id)
        job = session.scalar(select(Job).where(Job.run_id == run_id))
    database.dispose()

    assert search_calls == 2
    assert run is not None and run.status == "completed"
    assert job is not None and job.status == "completed"
    assert job.attempts == 2


def test_all_three_academic_providers_fail_clearly_after_two_bounded_retries(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        upstream_max_attempts=3,
        upstream_max_retry_delay_seconds=0,
    )
    hosts: list[str] = []
    retry_events: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host or "")
        return httpx.Response(
            429,
            headers={"Retry-After": "39701"},
            json={"message": "provider throttled"},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def operation() -> list[dict[str, Any]]:
                return await search_academic_papers(
                    "retrieval augmented generation", "", 10, client=client
                )

            async def on_retry(payload: dict[str, Any]) -> None:
                retry_events.append(payload)

            await _retry_call(operation, settings, on_retry=on_retry)

    with pytest.raises(ResearchSearchError) as caught:
        asyncio.run(run())

    assert hosts == [
        provider
        for _ in range(3)
        for provider in (
            "api.semanticscholar.org",
            "api.openalex.org",
            "export.arxiv.org",
        )
    ]
    assert len(retry_events) == 2
    assert all(event["delay_seconds"] == 0 for event in retry_events)
    assert all(event["requested_delay_seconds"] == 39_701 for event in retry_events)
    assert "Semantic Scholar" in str(caught.value)
    assert "OpenAlex" in str(caught.value)
    assert "arXiv" in str(caught.value)


def test_five_concurrent_canvas_pipelines_pair_retry_slots_and_never_exceed_two(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'global-reviewers.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=3,
        reviewer_concurrency=2,
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    canvas_ids: list[str] = []
    with database.session_context() as session:
        for index in range(5):
            canvas = Canvas(name=f"Canvas {index}", research_goal=f"goal-{index}")
            session.add(canvas)
            session.flush()
            canvas_ids.append(canvas.id)
            run = AgentRun(canvas_id=canvas.id, status="queued")
            session.add(run)
            session.flush()
            session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()

    active_reviewers = 0
    max_reviewers = 0
    semaphore_ids: set[int] = set()
    review_attempts: dict[str, int] = {}

    async def main_source(_: Settings, goal: str):
        yield {
            "type": "model.message.delta",
            "content": json.dumps(
                {
                    "brief_markdown": f"Brief for {goal}.",
                    "canonical_seed_titles": [
                        "Reliable Concurrent Review Processing for Research Agents",
                        "Bounded Parallel Inference for Autonomous Agent Workflows",
                    ],
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def candidates(goal: str, _: str, count: int) -> list[dict[str, Any]]:
        assert count == 10
        return [
            {
                **_candidate(1, index),
                "candidate_id": f"{goal}-{index}",
                "semantic_scholar_id": f"s2-{goal}-{index}",
                "doi": f"10.1000/{goal}-{index}",
                "title": f"{goal} Paper {index}",
                "url": f"https://example.test/{goal}/{index}",
                "pdf_url": f"https://example.test/{goal}/{index}.pdf",
            }
            for index in range(10)
        ]

    async def agents(_: Settings, agent_name: str, prompt: str):
        nonlocal active_reviewers, max_reviewers
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": True,
                        "reason": "Worthwhile.",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            active_reviewers += 1
            max_reviewers = max(max_reviewers, active_reviewers)
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            review_attempts[evidence_id] = review_attempts.get(evidence_id, 0) + 1
            await asyncio.sleep(0.005)
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [
                            evidence_id
                            if review_attempts[evidence_id] > 1
                            else "invalid-evidence"
                        ],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
            active_reviewers -= 1
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def pipeline_runner(**kwargs):
        semaphore_ids.add(id(kwargs["reviewer_semaphore"]))
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )

    async def exercise() -> None:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=main_source,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        await coordinator._queue.join()
        await coordinator.stop()

    asyncio.run(exercise())

    with database.session_context() as session:
        slot_events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.type.in_(
                        ("reviewer.slot.acquired", "reviewer.slot.released")
                    )
                )
                .order_by(AgentRunEvent.id)
            )
        )

    assert len(semaphore_ids) == 1
    assert max_reviewers == 2
    assert len(review_attempts) == 50
    assert set(review_attempts.values()) == {2}

    active_slots: dict[str, dict[str, Any]] = {}
    maximum_event_occupancy = 0
    acquired_canvas_ids: set[str] = set()
    acquired_count = 0
    released_count = 0
    for event in slot_events:
        payload = event.payload
        slot_id = payload["slot_id"]
        assert payload["run_id"] == event.run_id
        assert payload["canvas_id"] == event.canvas_id
        assert payload["paper_id"]
        assert payload["purpose"] == "autonomous_review"
        if event.type == "reviewer.slot.acquired":
            assert slot_id not in active_slots
            active_slots[slot_id] = payload
            acquired_canvas_ids.add(event.canvas_id)
            acquired_count += 1
            maximum_event_occupancy = max(
                maximum_event_occupancy, len(active_slots)
            )
        else:
            assert active_slots.pop(slot_id) == payload
            released_count += 1

    database.dispose()
    assert acquired_canvas_ids == set(canvas_ids)
    assert acquired_count == released_count == 100
    assert active_slots == {}
    assert maximum_event_occupancy == 2


def test_restart_after_two_batches_resumes_three_through_five_only(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'resume.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=1,
        reviewer_concurrency=2,
        discovery_max_batches=5,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(
            name="Resume",
            research_goal="Resume five batches.",
            research_brief={
                "text": "Persisted brief.",
                "canonical_seed_titles": [
                    "Reliable Recovery of Durable Background Agent Workflows",
                    "Fault Tolerant Queue Processing for Autonomous Agent Systems",
                ],
            },
            build_status="running",
        )
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.flush()
        session.add(
            Job(
                canvas_id=canvas.id,
                run_id=run.id,
                kind="canvas_build",
                status="running",
                attempts=1,
                lease_until=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": True,
                        "reason": "Worthwhile.",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    initial_calls = 0

    async def initial_candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        nonlocal initial_calls
        initial_calls += 1
        if initial_calls == 3:
            raise asyncio.CancelledError()
        return [_candidate(initial_calls, index) for index in range(count)]

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_autonomous_pipeline(
                database=database,
                settings=settings,
                canvas_id=canvas_id,
                run_id=run_id,
                research_goal="Resume five batches.",
                research_brief="Persisted brief.",
                emit=emit,
                agent_source=agents,
                candidate_source=initial_candidates,
                paper_source_acquirer=_source,
            )
        )
    database.dispose()

    recovery_batches: list[int] = []

    async def recovery_candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        batch = 3 + len(recovery_batches)
        recovery_batches.append(batch)
        return [_candidate(batch, index) for index in range(count)]

    async def fail_if_main_repeats(_: Settings, __: str):
        raise AssertionError("Recovered build must reuse its persisted brief")
        yield {}

    async def recovery_pipeline(**kwargs):
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=agents,
            candidate_source=recovery_candidates,
            paper_source_acquirer=_source,
        )

    app = create_app(
        settings,
        agent_event_source=fail_if_main_repeats,
        pipeline_runner=recovery_pipeline,
    )
    with TestClient(app) as client:
        stream = client.get(f"/api/v1/runs/{run_id}/events")
        snapshot = client.get(f"/api/v1/canvases/{canvas_id}").json()
        with app.state.database.session_context() as session:
            canvas = session.get(Canvas, canvas_id)
            job = session.scalar(select(Job).where(Job.run_id == run_id))
            decisions = list(
                session.scalars(
                    select(DiscoveryDecision).where(
                        DiscoveryDecision.canvas_id == canvas_id
                    )
                )
            )

    assert stream.status_code == 200 and "event: run.completed" in stream.text
    assert recovery_batches == [3, 4, 5]
    assert len(snapshot["papers"]) == 50
    assert canvas is not None and canvas.batch_count == 5
    assert job is not None and job.attempts == 2 and job.status == "completed"
    assert sorted({item.batch_number for item in decisions}) == [1, 2, 3, 4, 5]
    assert len(decisions) == 50
    summary = canvas.research_brief["pipeline_summary"]
    assert [item["batch"] for item in summary["coverage"]] == [1, 2, 3, 4, 5]
    assert summary["stop_reason"] == "autonomous_paper_limit_reached"


def test_crash_during_five_candidate_review_resumes_same_batch_without_rediscovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'mid-review.sqlite3'}",
        openai_api_key="test",
        reviewer_concurrency=2,
        discovery_max_batches=5,
        autonomous_paper_limit=50,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Mid review", research_goal="Recover every paper")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    search_batches = 0

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        nonlocal search_batches
        assert count == 10
        search_batches += 1
        batch_size = 5 if search_batches == 1 else 10
        return [_candidate(search_batches, index) for index in range(batch_size)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": True,
                        "reason": "Worthwhile.",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    import research_map_backend.review_queue as queue_module

    real_ack = queue_module.ack_review_message
    committed = 0

    def crash_after_fourth_commit(db: Database, message_id: str) -> None:
        nonlocal committed
        committed += 1
        if committed == 4:
            raise asyncio.CancelledError()
        real_ack(db, message_id)

    monkeypatch.setattr(queue_module, "ack_review_message", crash_after_fourth_commit)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_autonomous_pipeline(
                database=database,
                settings=settings,
                canvas_id=canvas_id,
                run_id=run_id,
                research_goal="Recover every paper",
                research_brief="Persisted brief",
                emit=emit,
                agent_source=agents,
                candidate_source=candidates,
                paper_source_acquirer=_source,
            )
        )
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        assert canvas is not None and canvas.batch_count == 0
        assert session.scalar(select(func.count()).select_from(DiscoveryDecision)) == 5
        assert session.scalar(select(func.count()).select_from(ActiveReviewMessage)) > 0

    recovered = recover_leased_review_messages(database)
    # Cooperative cancellation releases the message immediately. A hard
    # process crash leaves a lease and is covered by test_review_queue.
    assert recovered == []
    monkeypatch.setattr(queue_module, "ack_review_message", real_ack)
    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Recover every paper",
            research_brief="Persisted brief",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        decisions = list(session.scalars(select(DiscoveryDecision)))
        review_jobs = list(session.scalars(select(Job).where(Job.kind == "paper_review")))
        reviews = list(session.scalars(select(Review)))
        active_count = session.scalar(select(func.count()).select_from(ActiveReviewMessage))
        current_reviews = sum(item.is_current for item in reviews)
    database.dispose()

    assert search_batches == 5
    assert canvas is not None and canvas.batch_count == 5
    assert len(decisions) == 45 and {item.batch_number for item in decisions} == {1, 2, 3, 4, 5}
    assert len(review_jobs) == 45 and all(item.status == "completed" for item in review_jobs)
    assert active_count == 0 and current_reviews == 45
    assert 45 <= len(reviews) <= 46
    assert summary["stop_reason"] == "maximum_batches_reached"
    assert summary["accepted_papers"] == 45
    assert summary["coverage"][0]["actual_candidates"] == 5


def test_graceful_shutdown_releases_active_review_and_restart_clears_transient_state(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'shutdown-review.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=1,
        reviewer_concurrency=1,
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Shutdown", research_goal="Resume active review")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="queued")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build"))
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    reviewer_started = asyncio.Event()
    hold_reviewer = asyncio.Event()
    recovered = False
    search_calls = 0
    reviewer_calls = 0

    async def main_source(_: Settings, __: str):
        yield {
            "type": "model.message.delta",
            "content": json.dumps(
                {
                    "brief_markdown": "Stored brief",
                    "canonical_seed_titles": [
                        "Reliable Reviewer Recovery for Autonomous Research Agents",
                        "Durable Evidence Validation in Background Review Workflows",
                    ],
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        nonlocal search_calls
        search_calls += 1
        return [_candidate(1, index) for index in range(count)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        nonlocal reviewer_calls
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": index == 0,
                        "reason": "Accept one" if index == 0 else "Reject",
                    }
                    for index, item in enumerate(supplied)
                ]
            }
        elif agent_name == "research-map-reviewer":
            reviewer_calls += 1
            reviewer_started.set()
            if not recovered:
                await hold_reviewer.wait()
            elif reviewer_calls == 2:
                yield {
                    "type": "turn.done",
                    "state": {
                        "status": "failed",
                        "message": "research-map-reviewer did not complete its turn",
                    },
                }
                return
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def pipeline_runner(**kwargs):
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )

    async def interrupt() -> None:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=main_source,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        await reviewer_started.wait()
        await coordinator.stop()

    asyncio.run(interrupt())
    with database.session_context() as session:
        build_job = session.scalar(
            select(Job).where(Job.run_id == run_id, Job.kind == "canvas_build")
        )
        review_job = session.scalar(
            select(Job).where(Job.run_id == run_id, Job.kind == "paper_review")
        )
        membership = session.scalar(
            select(CanvasPaper).where(CanvasPaper.canvas_id == canvas_id)
        )
        message = session.scalar(select(ActiveReviewMessage))
        assert build_job is not None and build_job.status == "running"
        assert review_job is not None and review_job.status == "retrying"
        assert review_job.attempts == 1
        assert membership is not None and membership.processing_status == "queued"
        assert membership.error is None
        assert message is not None and message.lease_until is None

    recovered = True
    reviewer_started.clear()

    async def resume() -> None:
        coordinator = RunCoordinator(
            database,
            settings,
            event_source=main_source,
            pipeline_runner=pipeline_runner,
        )
        await coordinator.start()
        await coordinator._queue.join()
        await coordinator.stop()

    asyncio.run(resume())
    with database.session_context() as session:
        stored_run = session.get(AgentRun, run_id)
        canvas = session.get(Canvas, canvas_id)
        review_job = session.scalar(
            select(Job).where(Job.run_id == run_id, Job.kind == "paper_review")
        )
        membership = session.scalar(
            select(CanvasPaper).where(CanvasPaper.canvas_id == canvas_id)
        )
        active_count = session.scalar(
            select(func.count()).select_from(ActiveReviewMessage)
        )
    database.dispose()

    assert search_calls == 1
    assert reviewer_calls == 3
    assert stored_run is not None and stored_run.status == "completed"
    assert canvas is not None
    assert canvas.research_brief["pipeline_summary"]["status"] == "completed"
    assert canvas.research_brief["pipeline_summary"]["paper_failures"] == []
    assert review_job is not None and review_job.status == "completed"
    assert review_job.attempts == 3
    assert membership is not None and membership.processing_status == "reviewed"
    assert membership.error is None
    assert active_count == 0


def _exercise_review_deliveries(
    tmp_path: Path,
    *,
    unavailable_attempts: set[int] | None = None,
    invalid_attempts: set[int] | None = None,
) -> dict[str, Any]:
    unavailable_attempts = unavailable_attempts or set()
    invalid_attempts = invalid_attempts or set()
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'review-deliveries.sqlite3'}",
        openai_api_key="test",
        discovery_max_batches=1,
        retry_base_seconds=0,
        upstream_max_attempts=3,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Review delivery", research_goal="Test review retries")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    reviewer_calls = 0
    source_calls = 0
    prompts: list[str] = []

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        return [_candidate(1, index) for index in range(count)]

    async def source(_: Settings, candidate: dict[str, Any]) -> dict[str, Any]:
        nonlocal source_calls
        source_calls += 1
        return {
            "url": candidate["url"],
            "source_type": "abstract",
            "retrieval_status": "abstract_fallback",
            "pages": [{"page": None, "text": "evidence " * 20_000}],
        }

    async def agents(_: Settings, agent_name: str, prompt: str):
        nonlocal reviewer_calls
        if agent_name == "research-map-discovery":
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": index == 0,
                        "reason": "Best candidate" if index == 0 else "Not needed",
                    }
                    for index, item in enumerate(supplied)
                ]
            }
        elif agent_name == "research-map-reviewer":
            reviewer_calls += 1
            prompts.append(prompt)
            if reviewer_calls in unavailable_attempts:
                raise RuntimeError(f"temporary specialist outage {reviewer_calls}")
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            supplied_id = "invalid-evidence-id" if reviewer_calls in invalid_attempts else evidence_id
            payload = {
                "sections": {
                    key: {
                        "text": "Supported.",
                        "evidence_ids": [supplied_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            payload = {"pairs": []}
        yield {"type": "session.created", "session_id": f"session-{reviewer_calls}"}
        yield {"type": "turn.created", "turn_id": f"turn-{reviewer_calls}"}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Test review retries",
            research_brief="Review retry brief",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=source,
        )
    )
    with database.session_context() as session:
        review_job = session.scalar(
            select(Job).where(Job.run_id == run_id, Job.kind == "paper_review")
        )
        membership = session.scalar(
            select(CanvasPaper).where(CanvasPaper.canvas_id == canvas_id)
        )
        result = {
            "summary": summary,
            "calls": reviewer_calls,
            "source_calls": source_calls,
            "prompts": prompts,
            "job_status": review_job.status,
            "job_attempts": review_job.attempts,
            "job_error": review_job.error,
            "paper_status": membership.processing_status,
            "paper_error": membership.error,
            "review_count": session.scalar(select(func.count()).select_from(Review)),
            "active_count": session.scalar(
                select(func.count()).select_from(ActiveReviewMessage)
            ),
        }
    database.dispose()
    return result


def test_invalid_review_evidence_retries_then_clears_transient_error(
    tmp_path: Path,
) -> None:
    result = _exercise_review_deliveries(tmp_path, invalid_attempts={1})

    assert result["calls"] == 2
    assert result["source_calls"] == 1
    assert result["job_attempts"] == 2
    assert result["job_status"] == "completed"
    assert result["job_error"] is None
    assert result["paper_status"] == "reviewed"
    assert result["paper_error"] is None
    assert result["review_count"] == 1
    assert result["active_count"] == 0
    assert result["summary"]["status"] == "completed"
    assert "previous output failed validation" in result["prompts"][1]
    assert "invalid evidence" in result["prompts"][1]
    assert len(result["prompts"][1]) < len(result["prompts"][0])


def test_invalid_review_evidence_three_times_is_deleted_as_poison(
    tmp_path: Path,
) -> None:
    result = _exercise_review_deliveries(tmp_path, invalid_attempts={1, 2, 3})

    assert result["calls"] == 3
    assert result["source_calls"] == 1
    assert result["job_attempts"] == 3
    assert result["job_status"] == "failed"
    assert result["paper_status"] == "failed"
    assert "invalid evidence" in result["paper_error"]
    assert result["review_count"] == 0
    assert result["active_count"] == 0
    assert result["summary"]["status"] == "completed_with_errors"
    assert len(result["prompts"][0]) > len(result["prompts"][1]) > len(result["prompts"][2])


def test_invalid_review_evidence_twice_then_valid_third_attempt_completes(
    tmp_path: Path,
) -> None:
    result = _exercise_review_deliveries(tmp_path, invalid_attempts={1, 2})

    assert result["calls"] == 3
    assert result["job_attempts"] == 3
    assert result["job_status"] == "completed"
    assert result["paper_status"] == "reviewed"
    assert result["paper_error"] is None
    assert result["review_count"] == 1
    assert result["active_count"] == 0
    assert result["summary"]["status"] == "completed"
    assert "invalid evidence" in result["prompts"][1]
    assert "invalid evidence" in result["prompts"][2]


def test_specialist_unavailable_twice_uses_fresh_delivery_then_succeeds(
    tmp_path: Path,
) -> None:
    result = _exercise_review_deliveries(tmp_path, unavailable_attempts={1, 2})

    assert result["calls"] == 3
    assert result["source_calls"] == 1
    assert result["job_attempts"] == 3
    assert result["job_status"] == "completed"
    assert result["paper_status"] == "reviewed"
    assert result["paper_error"] is None
    assert result["review_count"] == 1
    assert result["active_count"] == 0
    assert result["summary"]["status"] == "completed"
    assert "Return one JSON object only" in result["prompts"][1]
    assert "Do not call tools" in result["prompts"][2]
    assert len(result["prompts"][0]) > len(result["prompts"][1]) > len(result["prompts"][2])


def test_specialist_unavailable_three_times_stops_without_fourth_claim(
    tmp_path: Path,
) -> None:
    result = _exercise_review_deliveries(tmp_path, unavailable_attempts={1, 2, 3})

    assert result["calls"] == 3
    assert result["source_calls"] == 1
    assert result["job_attempts"] == 3
    assert result["job_status"] == "failed"
    assert result["paper_status"] == "failed"
    assert result["review_count"] == 0
    assert result["active_count"] == 0
    assert result["summary"]["status"] == "completed_with_errors"


def test_reviewer_transport_recovery_stays_within_one_queue_claim(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'review-transport.sqlite3'}",
        openai_api_key="test",
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Transport", research_goal="Recover the same reviewer turn")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id)
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    reviewer_calls = 0
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        return [_candidate(1, index) for index in range(count)]

    async def agents(_: Settings, agent_name: str, prompt: str):
        nonlocal reviewer_calls
        if agent_name == "research-map-discovery":
            supplied = json.loads(prompt.split("Candidates:\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": item["candidate_id"],
                        "score": {
                            "relevance": 30,
                            "source_credibility": 15,
                            "age_adjusted_impact": 15,
                            "full_text_availability": 15,
                            "novelty": 15,
                            "relationship_potential": 10,
                        },
                        "accepted": index == 0,
                        "reason": "Selected" if index == 0 else "Not selected",
                    }
                    for index, item in enumerate(supplied)
                ]
            }
        elif agent_name == "research-map-reviewer":
            reviewer_calls += 1
            evidence_id = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            payload = {
                "sections": {
                    key: {
                        "text": "Supported by the supplied paper evidence.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
            yield {"type": "session.created", "session_id": "same-session"}
            yield {"type": "turn.created", "turn_id": "same-turn"}
            yield {
                "type": "transport.error",
                "session_id": "same-session",
                "turn_id": "same-turn",
                "stage": "subscribe_turn",
                "status": "retrying",
                "error_class": "ReadError",
                "message": "TrueForge subscribe_turn transport failed.",
                "detail": "peer closed the SSE stream",
            }
            await asyncio.sleep(0.01)
            yield {"type": "model.message", "content": json.dumps(payload)}
            yield {
                "type": "turn.done",
                "turn_id": "same-turn",
                "state": {
                    "status": "done",
                    "required_actions": [],
                    "metrics": {"total_input_tokens": 50, "total_output_tokens": 10},
                },
                "recovered": True,
            }
            return
        else:
            payload = {"pairs": []}
        yield {"type": "model.message", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        emitted.append((event_type, payload))

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Recover the same reviewer turn",
            research_brief="Transport recovery brief",
            emit=emit,
            agent_source=agents,
            candidate_source=candidates,
            paper_source_acquirer=_source,
        )
    )
    with database.session_context() as session:
        job = session.scalar(
            select(Job).where(Job.run_id == run_id, Job.kind == "paper_review")
        )
        review_count = session.scalar(select(func.count()).select_from(Review))
    database.dispose()

    assert reviewer_calls == 1
    assert job is not None and job.attempts == 1 and job.status == "completed"
    assert review_count == 1
    assert summary["status"] == "completed"
    assert any(
        event_type == "agent.transport.retry"
        and payload["turn_id"] == "same-turn"
        and payload["error_class"] == "ReadError"
        for event_type, payload in emitted
    )
    assert any(
        event_type == "agent.turn.completed"
        and payload["recovered"] is True
        and payload["usage"]["input_tokens"] == 50
        for event_type, payload in emitted
    )
