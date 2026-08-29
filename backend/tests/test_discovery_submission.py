from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from sqlalchemy import func, select

from research_map_backend.db import Database
from research_map_backend.mcp_contracts import SubmitDiscoveryBatchRequest
from research_map_backend.mcp_server import create_research_mcp
from research_map_backend.models import AgentRun, DiscoveryDecision, Job
from research_map_backend.pipeline import (
    AutonomousPipelineError,
    _run_validated_discovery,
    _validate_shaped_discovery_submission,
)
from research_map_backend.research_store import (
    cleanup_terminal_discovery_submissions,
    consume_discovery_submission,
    create_canvas_and_queue_build,
    invalidate_discovery_submission,
    load_discovery_tool_checkpoint,
    load_discovery_submission,
    record_discovery_tool_checkpoint,
    submit_discovery_batch,
)
from research_map_backend.settings import Settings


def _database(tmp_path: Path) -> tuple[Database, str, str]:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'submission.sqlite3'}",
        )
    )
    database.create_schema()
    canvas, run = create_canvas_and_queue_build(
        database, {"name": "Discovery", "research_goal": "Map robust research agents."}
    )
    return database, canvas.id, run.id


def _metadata(index: int, *, url: str | None = None) -> dict:
    paper_url = url or f"https://papers.example.org/paper-{index}"
    return {
        "source_provider": "bright_resolver",
        "candidate_id": f"candidate-{index}",
        "semantic_scholar_id": f"candidate-{index}",
        "title": f"Research Agent Paper {index}",
        "authors": ["A. Researcher"],
        "year": 2025,
        "month": index + 1,
        "venue": "Research Venue",
        "abstract": "Evidence-backed metadata for a relevant research paper.",
        "url": paper_url,
        "pdf_url": f"{paper_url}.pdf",
        "citation_count": 7,
    }


def _decision(candidate_id: str, url: str, *, accepted: bool = True) -> dict:
    return {
        "candidate_id": candidate_id,
        "bright_evidence_urls": [url],
        "score": {
            "relevance": 30,
            "source_credibility": 15,
            "age_adjusted_impact": 10,
            "full_text_availability": 15,
            "novelty": 10,
            "relationship_potential": 10,
        },
        "accepted": accepted,
        "reason": "The exact Bright evidence and resolved metadata support this decision.",
    }


def _submission(
    canvas_id: str,
    run_id: str,
    metadata: list[dict],
    *,
    attempt: int = 1,
    evidence_urls: list[str] | None = None,
) -> dict:
    urls = evidence_urls or [item["url"] for item in metadata]
    return {
        "canvas_id": canvas_id,
        "run_id": run_id,
        "batch_number": 1,
        "attempt": attempt,
        "bright_status": "available",
        "bright_unavailable_reason": None,
        "searches": [
            {
                "query": f"research agents retrieval evaluation anchor group {index}",
                "finding": "Located and compared several scholarly candidates.",
            }
            for index in range(5)
        ],
        "scraped_pages": [
            {
                "candidate_id": item["candidate_id"],
                "url": url,
                "finding": "The scholarly page supports this candidate.",
            }
            for item, url in zip(metadata, urls, strict=True)
        ],
        "introduced_candidates": [
            {"candidate": item, "evidence_urls": [url]}
            for item, url in zip(metadata, urls, strict=True)
        ],
        "decisions": [
            _decision(item["candidate_id"], url)
            for item, url in zip(metadata, urls, strict=True)
        ],
    }


def _trace(payload: dict, resolved_metadata: list[dict]) -> list[dict]:
    observed_urls = [item["url"] for item in resolved_metadata]
    trace: list[dict] = []
    for search in payload["searches"]:
        trace.append(
            {
                "name": "search_engine",
                "arguments": {"query": search["query"]},
                "status": "completed",
                "response": "\n".join(observed_urls),
            }
        )
    for item in resolved_metadata:
        trace.extend(
            [
                {
                    "name": "scrape_as_markdown",
                    "arguments": {"url": item["url"]},
                    "status": "completed",
                    "response": "Bounded scholarly page finding.",
                },
                {
                    "name": "resolve_paper_metadata",
                    "arguments": {"paper_url": item["url"]},
                    "status": "completed",
                    "response": item,
                },
            ]
        )
    trace.append(
        {
            "name": "submit_discovery_batch",
            "arguments": payload,
            "status": "completed",
            "response": {"status": "stored"},
        }
    )
    return trace


def _persistable_decisions(metadata: list[dict]) -> list[dict]:
    return [
        {
            **item,
            "score": _decision(item["candidate_id"], item["url"])["score"],
            "total_score": 90,
            "accepted": True,
            "reason": "Supported.",
        }
        for item in metadata
    ]


def test_submission_schema_rejects_malformed_before_storage(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    payload = _submission(canvas_id, run_id, [_metadata(index) for index in range(3)])
    payload["scraped_pages"] = payload["scraped_pages"][:2]

    with pytest.raises(ValidationError, match="3–300 scraped pages"):
        SubmitDiscoveryBatchRequest.model_validate(payload)
    assert load_discovery_submission(database, canvas_id, run_id, 1, 1) is None
    database.dispose()


def test_submission_is_attempt_keyed_and_duplicate_is_rejected(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    payload = _submission(canvas_id, run_id, [_metadata(index) for index in range(3)])
    parsed = SubmitDiscoveryBatchRequest.model_validate(payload).model_dump(mode="json")

    submit_discovery_batch(database, canvas_id, run_id, 1, 1, parsed)
    assert load_discovery_submission(database, canvas_id, run_id, 1, 2) is None
    assert load_discovery_submission(database, canvas_id, run_id, 1, 1) == parsed
    with pytest.raises(ValueError, match="already submitted"):
        submit_discovery_batch(database, canvas_id, run_id, 1, 1, parsed)

    invalidate_discovery_submission(database, canvas_id, run_id, 1, 1)
    assert load_discovery_submission(database, canvas_id, run_id, 1, 1) is None
    second = deepcopy(parsed)
    second["attempt"] = 2
    submit_discovery_batch(database, canvas_id, run_id, 1, 2, second)
    assert load_discovery_submission(database, canvas_id, run_id, 1, 2) == second
    database.dispose()


def test_mcp_tool_validates_and_stores_one_pending_submission(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    payload = _submission(canvas_id, run_id, [_metadata(index) for index in range(3)])
    server = create_research_mcp(lambda: database, lambda *_args: None)
    tool = server._tool_manager.get_tool("submit_discovery_batch")  # noqa: SLF001
    assert tool is not None

    result = asyncio.run(tool.run(payload, convert_result=False))

    assert result.model_dump() == {"status": "stored", "batch_number": 1, "attempt": 1}
    assert load_discovery_submission(database, canvas_id, run_id, 1, 1) == (
        SubmitDiscoveryBatchRequest.model_validate(payload).model_dump(mode="json")
    )
    with pytest.raises(ToolError, match="already submitted"):
        asyncio.run(tool.run(payload, convert_result=False))
    malformed = deepcopy(payload)
    malformed["attempt"] = 2
    malformed["scraped_pages"] = malformed["scraped_pages"][:2]
    with pytest.raises(ToolError, match="3–300 scraped pages"):
        asyncio.run(tool.run(malformed, convert_result=False))
    assert load_discovery_submission(database, canvas_id, run_id, 1, 2) is None
    database.dispose()


def test_terminal_run_rejects_late_submit_and_cleanup_removes_only_pending(
    tmp_path: Path,
) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    payload = _submission(canvas_id, run_id, [_metadata(index) for index in range(3)])
    submit_discovery_batch(database, canvas_id, run_id, 1, 1, payload)
    checkpoint = {
        "tool_call_id": "search-1",
        "name": "search_engine",
        "arguments": {"query": "combined anchors"},
        "status": "completed",
        "response": {"observed_urls": ["https://papers.example.org/paper-0"]},
    }
    record_discovery_tool_checkpoint(
        database, canvas_id, run_id, 1, "search-1", checkpoint
    )
    record_discovery_tool_checkpoint(
        database, canvas_id, run_id, 1, "search-1", checkpoint
    )
    assert load_discovery_tool_checkpoint(database, canvas_id, run_id, 1) == [
        checkpoint
    ]
    with database.session_context() as session:
        run = session.get(AgentRun, run_id)
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert run is not None and job is not None
        job_payload = dict(job.payload)
        submissions = dict(job_payload["discovery_submissions"])
        submissions["0:1"] = {"status": "invalid", "finished_at": "earlier"}
        job_payload["discovery_submissions"] = submissions
        job.payload = job_payload
        run.status = "failed"
        job.status = "failed"
        session.commit()

    server = create_research_mcp(lambda: database, lambda *_args: None)
    tool = server._tool_manager.get_tool("submit_discovery_batch")  # noqa: SLF001
    assert tool is not None
    late = deepcopy(payload)
    late["attempt"] = 2
    with pytest.raises(ToolError, match="no longer active"):
        asyncio.run(tool.run(late, convert_result=False))

    assert cleanup_terminal_discovery_submissions(database, run_id) == 1
    with database.session_context() as session:
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert job is not None
        assert job.payload["discovery_submissions"] == {
            "0:1": {"status": "invalid", "finished_at": "earlier"}
        }
        assert job.payload["discovery_tool_checkpoints"]["1"]["calls"] == {
            "search-1": checkpoint
        }
        assert session.scalar(select(func.count()).select_from(DiscoveryDecision)) == 0
    database.dispose()


def test_consume_is_atomic_and_redelivery_cannot_duplicate_decisions(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    metadata = [_metadata(index) for index in range(3)]
    payload = _submission(canvas_id, run_id, metadata)
    submit_discovery_batch(database, canvas_id, run_id, 1, 1, payload)

    accepted = consume_discovery_submission(
        database, canvas_id, run_id, 1, 1, _persistable_decisions(metadata)
    )
    assert set(accepted) == {item["candidate_id"] for item in metadata}
    with pytest.raises(ValueError, match="no longer pending"):
        consume_discovery_submission(
            database, canvas_id, run_id, 1, 1, _persistable_decisions(metadata)
        )
    with database.session_context() as session:
        assert session.scalar(select(func.count()).select_from(DiscoveryDecision)) == 3
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert job is not None
        assert job.payload["discovery_submissions"]["1:1"] == pytest.approx(
            {"status": "consumed", "finished_at": job.payload["discovery_submissions"]["1:1"]["finished_at"]}
        )
    database.dispose()


def test_three_valid_siblings_survive_one_malformed_exact_url() -> None:
    metadata = [_metadata(index) for index in range(4)]
    malformed = "https://papers.example.org/paper3"
    payload = _submission(
        "canvas", "run", metadata, evidence_urls=[*(item["url"] for item in metadata[:3]), malformed]
    )
    candidates: list[dict] = []

    decisions = _validate_shaped_discovery_submission(
        payload,
        candidates,
        _trace(payload, metadata),
        canvas_id="canvas",
        run_id="run",
        batch_number=1,
        attempt=1,
        bright_unavailable=False,
    )

    assert [item["candidate_id"] for item in decisions] == [
        "candidate-0",
        "candidate-1",
        "candidate-2",
    ]
    assert [item["candidate_id"] for item in candidates] == [
        "candidate-0",
        "candidate-1",
        "candidate-2",
    ]


@pytest.mark.parametrize(
    "variant",
    [
        "https://papers.example.org/paper-2/",
        "https://papers.example.org/paper-2?source=bright",
    ],
)
def test_two_valid_and_one_hand_typed_url_requires_repair(variant: str) -> None:
    metadata = [_metadata(index) for index in range(3)]
    payload = _submission(
        "canvas", "run", metadata, evidence_urls=[metadata[0]["url"], metadata[1]["url"], variant]
    )

    with pytest.raises(AutonomousPipelineError, match="Fewer than three") as raised:
        _validate_shaped_discovery_submission(
            payload,
            [],
            _trace(payload, metadata),
            canvas_id="canvas",
            run_id="run",
            batch_number=1,
            attempt=1,
            bright_unavailable=False,
        )
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    "exact_url",
    [
        "https://papers.example.org/paper-2/",
        "https://papers.example.org/paper-2?source=bright",
    ],
)
def test_url_variant_is_valid_only_when_observed_byte_exact(exact_url: str) -> None:
    metadata = [_metadata(0), _metadata(1), _metadata(2, url=exact_url)]
    payload = _submission("canvas", "run", metadata)

    decisions = _validate_shaped_discovery_submission(
        payload,
        [],
        _trace(payload, metadata),
        canvas_id="canvas",
        run_id="run",
        batch_number=1,
        attempt=1,
        bright_unavailable=False,
    )

    assert len(decisions) == 3


def test_cross_trace_mismatch_is_invalid() -> None:
    metadata = [_metadata(index) for index in range(3)]
    payload = _submission("canvas", "run", metadata)
    trace = _trace(payload, metadata)
    trace[0]["arguments"]["query"] = "different observed query"

    with pytest.raises(AutonomousPipelineError, match="search must match"):
        _validate_shaped_discovery_submission(
            payload,
            [],
            trace,
            canvas_id="canvas",
            run_id="run",
            batch_number=1,
            attempt=1,
            bright_unavailable=False,
        )


def test_durable_submission_survives_missing_submit_stream_and_search_subset(
    tmp_path: Path,
) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    metadata = [_metadata(index) for index in range(3)]
    payload = _submission(canvas_id, run_id, metadata)

    async def source(_settings: Settings, _agent: str, _prompt: str):
        observed = _trace(payload, metadata)[:-1]
        observed.insert(
            len(payload["searches"]),
            {
                "name": "search_engine",
                "arguments": {"query": "additional successful checkpoint search"},
                "status": "completed",
                "response": "\n".join(item["url"] for item in metadata),
            },
        )
        for index, item in enumerate(observed):
            call_id = f"observed-{index}"
            yield {
                "type": "model.message",
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    }
                ],
            }
            yield {
                "type": "tool.response",
                "tool_call_id": call_id,
                "content": item["response"],
            }
        # The successful MCP handler persisted the shaped handoff, but TrueForge
        # omitted its submit tool call from the streamed events.
        submit_discovery_batch(database, canvas_id, run_id, 1, 1, payload)
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:1",
            "Build a shaped batch",
            [],
            emit,
            source,
            require_bright_tools=True,
            batch_number=1,
            database=database,
            canvas_id=canvas_id,
            run_id=run_id,
            remaining_capacity=50,
            load_submission=load_discovery_submission,
            invalidate_submission=invalidate_discovery_submission,
            consume_submission=consume_discovery_submission,
        )
    )

    assert len(decisions) == 3
    with database.session_context() as session:
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert job is not None
        assert job.payload["discovery_submissions"]["1:1"]["status"] == "consumed"
    database.dispose()


def test_invalid_attempt_is_consumed_and_next_shaped_attempt_succeeds(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    metadata = [_metadata(index) for index in range(3)]
    malformed = "https://papers.example.org/paper2"
    first = _submission(
        canvas_id,
        run_id,
        metadata,
        evidence_urls=[metadata[0]["url"], metadata[1]["url"], malformed],
    )
    second = _submission(canvas_id, run_id, metadata, attempt=2)

    async def source(_settings: Settings, _agent: str, prompt: str):
        is_repair = "attempt=2" in prompt
        current = second if is_repair else first
        if not is_repair:
            for item in _trace(first, metadata)[:-1]:
                call_id = f"call-{len(prompt)}-{id(item)}"
                yield {
                    "type": "model.message",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "function": {
                                "name": item["name"],
                                "arguments": item["arguments"],
                            },
                        }
                    ],
                }
                yield {
                    "type": "tool.response",
                    "tool_call_id": call_id,
                    "content": item["response"],
                }
        submit_discovery_batch(database, canvas_id, run_id, 1, current["attempt"], current)
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": f"submit-{current['attempt']}",
                    "function": {"name": "submit_discovery_batch", "arguments": current},
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": f"submit-{current['attempt']}",
            "content": {"status": "stored"},
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=2, retry_base_seconds=0),
            "discovery:1",
            "Build batch with attempt=1",
            [],
            emit,
            source,
            require_bright_tools=True,
            batch_number=1,
            database=database,
            canvas_id=canvas_id,
            run_id=run_id,
            remaining_capacity=50,
            load_submission=load_discovery_submission,
            invalidate_submission=invalidate_discovery_submission,
            consume_submission=consume_discovery_submission,
        )
    )

    assert len(decisions) == 3
    assert set(decisions.accepted_map or {}) == {item["candidate_id"] for item in metadata}
    with database.session_context() as session:
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert job is not None
        entries = job.payload["discovery_submissions"]
        assert entries["1:1"]["status"] == "invalid"
        assert entries["1:2"]["status"] == "consumed"
    assert any(event == "agent.output.retry" for event, _ in events)
    database.dispose()


def test_connector_unavailable_requires_and_consumes_shaped_fallback(tmp_path: Path) -> None:
    database, canvas_id, run_id = _database(tmp_path)
    candidates = [_metadata(index) for index in range(3)]
    fallback = {
        "canvas_id": canvas_id,
        "run_id": run_id,
        "batch_number": 1,
        "attempt": 1,
        "bright_status": "unavailable",
        "bright_unavailable_reason": "Bright connector unavailable.",
        "searches": [],
        "scraped_pages": [],
        "introduced_candidates": [],
        "decisions": [
            {**_decision(item["candidate_id"], item["url"]), "bright_evidence_urls": []}
            for item in candidates
        ],
    }

    async def source(_settings: Settings, _agent: str, _prompt: str):
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": "search-failed",
                    "function": {
                        "name": "search_engine",
                        "arguments": {"query": "combined anchors"},
                    },
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "search-failed",
            "is_error": True,
            "content": {"error": "Connector requires authentication"},
        }
        submit_discovery_batch(database, canvas_id, run_id, 1, 1, fallback)
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": "submit-fallback",
                    "function": {
                        "name": "submit_discovery_batch",
                        "arguments": fallback,
                    },
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "submit-fallback",
            "content": {"status": "stored"},
        }
        yield {
            "type": "model.message.delta",
            "content": "UNTRUSTED_RAW_BRIGHT_OUTPUT_MUST_NOT_PERSIST",
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:1",
            "Build shaped fallback",
            candidates,
            emit,
            source,
            require_bright_tools=True,
            batch_number=1,
            database=database,
            canvas_id=canvas_id,
            run_id=run_id,
            remaining_capacity=50,
            load_submission=load_discovery_submission,
            invalidate_submission=invalidate_discovery_submission,
            consume_submission=consume_discovery_submission,
        )
    )

    assert len(decisions) == 3
    assert any(
        event_type == "tool.completed"
        and payload.get("code") == "bright_data_unavailable"
        for event_type, payload in events
    )
    assert "requires authentication" not in str(events).casefold()
    assert "UNTRUSTED_RAW_BRIGHT_OUTPUT_MUST_NOT_PERSIST" not in str(events)
    database.dispose()
