import asyncio
import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from research_map_backend.app import create_app
from research_map_backend.db import Database
from research_map_backend.link_pipeline import run_link_pipeline
from research_map_backend.models import (
    AgentRun,
    Canvas,
    CanvasPaper,
    Job,
    Paper,
    Review,
    ToolInvocation,
)
from research_map_backend.pipeline import SECTION_KEYS
from research_map_backend.paper_links import PaperLinkError
from research_map_backend.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        openai_api_key="test-openai-key",
    )


def test_streamable_http_mcp_initializes_and_lists_specialist_tools(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(
        app, base_url="http://127.0.0.1:8000", follow_redirects=False
    ) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "MCP", "research_goal": "Map agent research."},
        ).json()["canvas"]["id"]
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "trueforge-smoke", "version": "0.1.4"},
                },
            },
        )
        listed = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-03-26"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        called = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-03-26"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_canvas", "arguments": {"canvas_id": canvas_id}},
            },
        )
        failed = client.post(
            "/mcp",
            headers={**headers, "MCP-Protocol-Version": "2025-03-26"},
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_canvas",
                    "arguments": {"canvas_id": "missing-canvas"},
                },
            },
        )
        with app.state.database.session_context() as session:
            invocations = list(
                session.scalars(
                    select(ToolInvocation).order_by(ToolInvocation.started_at)
                )
            )

    assert initialized.status_code == 200
    assert initialized.headers.get("location") is None
    assert initialized.json()["result"]["protocolVersion"] == "2025-03-26"
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    for tool in listed.json()["result"]["tools"]:
        # TrueForge 0.1.4 rejects scalar/array output schemas while loading an
        # MCP server. Its tool adapter requires an object result envelope.
        assert tool["outputSchema"]["type"] == "object"
        assert tool["outputSchema"]["additionalProperties"] is False
    assert {
        "get_canvas",
        "get_paper_text",
        "resolve_paper_metadata",
        "upsert_canvas_paper",
        "search_semantic_scholar",
        "search_arxiv",
        "record_discovery_decision",
        "record_paper_source",
        "record_review",
        "record_relationship",
        "record_progress",
        "create_canvas_and_start_build",
    } <= names
    assert called.status_code == 200
    assert called.json()["result"]["isError"] is False
    assert failed.status_code == 200
    assert failed.json()["result"]["isError"] is True
    assert [invocation.status for invocation in invocations] == [
        "completed",
        "failed",
    ]
    assert invocations[0].canvas_id == canvas_id
    assert invocations[0].arguments == {"canvas_id": canvas_id}
    assert invocations[0].result["canvas"]["id"] == canvas_id
    assert invocations[0].completed_at is not None
    assert invocations[1].canvas_id is None
    assert invocations[1].error == "Canvas does not exist"


def test_main_mcp_creates_one_canvas_and_enqueues_one_build_with_events(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    enqueued: list[str] = []
    ready = threading.Event()

    async def capture_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    async def receive_two_events(broadcaster) -> list[dict[str, str]]:
        async with broadcaster.subscribe() as queue:
            ready.set()
            return [
                await asyncio.wait_for(queue.get(), timeout=2),
                await asyncio.wait_for(queue.get(), timeout=2),
            ]

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        app.state.run_coordinator.enqueue = capture_enqueue
        assert client.portal is not None
        pending_events = client.portal.start_task_soon(
            receive_two_events, app.state.canvas_events
        )
        assert ready.wait(timeout=1)
        called = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-03-26",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_canvas_and_start_build",
                    "arguments": {
                        "research_goal": "Map autonomous literature review systems.",
                        "name": "Autonomous reviews",
                    },
                },
            },
        )
        events = pending_events.result(timeout=2)
        with app.state.database.session_context() as session:
            canvases = list(session.scalars(select(Canvas)))
            runs = list(session.scalars(select(AgentRun)))
            jobs = list(session.scalars(select(Job)))

    assert called.status_code == 200
    result = called.json()["result"]
    assert result["isError"] is False
    payload = result["structuredContent"]
    assert payload == {
        "canvas_id": canvases[0].id,
        "run_id": runs[0].id,
        "status": "queued",
        "name": "Autonomous reviews",
    }
    assert len(canvases) == len(runs) == len(jobs) == 1
    assert canvases[0].build_status == "queued"
    assert runs[0].status == "queued"
    assert jobs[0].kind == "canvas_build" and jobs[0].run_id == runs[0].id
    assert enqueued == [runs[0].id]
    assert [event["change_type"] for event in events] == [
        "canvas.created",
        "run.queued",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"research_goal": "   "},
        {"research_goal": "Valid goal", "name": "x" * 201},
    ],
)
def test_main_mcp_rejects_invalid_canvas_input_without_persisting(
    tmp_path: Path, arguments: dict[str, str]
) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        failed = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-03-26",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_canvas_and_start_build",
                    "arguments": arguments,
                },
            },
        )
        with app.state.database.session_context() as session:
            counts = (
                session.scalar(select(func.count()).select_from(Canvas)),
                session.scalar(select(func.count()).select_from(AgentRun)),
                session.scalar(select(func.count()).select_from(Job)),
            )

    assert failed.status_code == 200
    assert failed.json()["result"]["isError"] is True
    assert counts == (0, 0, 0)


async def _fake_link_pipeline(**kwargs):
    assert kwargs["paper_url"] == "https://doi.org/10.1000/test-paper"
    paper_id = kwargs["placeholder_paper_id"]
    await kwargs["emit"](
        "paper.added", {"paper_id": paper_id, "title": "Resolved paper"}
    )
    return {"paper_id": paper_id, "completed_reviews": 1, "relationships": 0}


def test_paper_link_is_persisted_enqueued_processed_and_streamed(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), link_pipeline_runner=_fake_link_pipeline)
    with TestClient(app) as client:
        canvas = client.post(
            "/api/v1/canvases",
            json={"name": "RAG", "research_goal": "Map retrieval augmented generation."},
        ).json()["canvas"]
        started = client.post(
            f"/api/v1/canvases/{canvas['id']}/papers/from-link",
            json={"url": "https://doi.org/10.1000/test-paper"},
        )
        run = started.json()
        snapshot = client.get(f"/api/v1/canvases/{canvas['id']}").json()
        events = client.get(f"/api/v1/runs/{run['id']}/events")
        with app.state.database.session_context() as session:
            stored_run = session.get(AgentRun, run["id"])
            job = session.scalar(select(Job).where(Job.run_id == run["id"]))
            stored_canvas = session.get(Canvas, canvas["id"])

    assert started.status_code == 202
    assert stored_run is not None and stored_run.status == "completed"
    assert job is not None
    assert job.kind == "paper_link"
    assert job.status == "completed"
    assert job.payload == {"url": "https://doi.org/10.1000/test-paper"}
    assert len(snapshot["papers"]) == 1
    assert snapshot["papers"][0]["id"] == job.paper_id
    assert snapshot["papers"][0]["summary"] == ""
    assert snapshot["papers"][0]["link"] == "https://doi.org/10.1000/test-paper"
    assert stored_canvas is not None and stored_canvas.build_status == "idle"
    assert "event: run.started" in events.text
    assert "event: paper.added" in events.text
    assert "event: run.completed" in events.text


def test_versioned_arxiv_api_response_reuses_cross_canvas_canonical_paper_id(
    tmp_path: Path,
) -> None:
    async def no_op_link_pipeline(**kwargs):
        return {
            "paper_id": kwargs["placeholder_paper_id"],
            "completed_reviews": 0,
            "relationships": 0,
        }

    app = create_app(_settings(tmp_path), link_pipeline_runner=no_op_link_pipeline)
    with TestClient(app) as client:
        canvas_ids = [
            client.post(
                "/api/v1/canvases",
                json={"name": f"Canvas {index}", "research_goal": "Map RAG."},
            ).json()["canvas"]["id"]
            for index in range(2)
        ]
        canonical = client.post(
            f"/api/v1/canvases/{canvas_ids[0]}/papers/from-link",
            json={"url": "2005.11401"},
        ).json()
        versioned = client.post(
            f"/api/v1/canvases/{canvas_ids[1]}/papers/from-link",
            json={"url": "2005.11401v2"},
        ).json()
        second_snapshot = client.get(f"/api/v1/canvases/{canvas_ids[1]}").json()
        with app.state.database.session_context() as session:
            paper_count = len(list(session.scalars(select(Paper))))
            second_job = session.scalar(select(Job).where(Job.run_id == versioned["id"]))

    assert versioned["paper_id"] == canonical["paper_id"]
    assert second_job is not None and second_job.paper_id == canonical["paper_id"]
    assert second_snapshot["papers"][0]["id"] == canonical["paper_id"]
    assert paper_count == 1


@pytest.mark.parametrize(
    "paper_reference",
    [
        "https://arxiv.org/abs/2005.11401",
        "10.1000/test-paper",
        "2005.11401",
        "cs.AI/0301001v2",
        "math.GT/0309136v1",
        "https://www.semanticscholar.org/paper/title/0123456789abcdef0123456789abcdef01234567",
        "https://publisher.example/article/verified",
        "https://publisher.example/article/verified.pdf",
    ],
)
def test_paper_link_api_accepts_secure_links_and_bare_identifiers(
    tmp_path: Path, paper_reference: str
) -> None:
    seen: list[str] = []

    async def link_pipeline(**kwargs):
        seen.append(kwargs["paper_url"])
        return {
            "paper_id": kwargs["placeholder_paper_id"],
            "completed_reviews": 0,
            "relationships": 0,
        }

    app = create_app(_settings(tmp_path), link_pipeline_runner=link_pipeline)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Secure", "research_goal": "Validate paper inputs."},
        ).json()["canvas"]["id"]
        response = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": paper_reference},
        )

    assert response.status_code == 202
    assert seen == [paper_reference]


def test_paper_link_api_rejects_insecure_remote_http_before_enqueue(
    tmp_path: Path,
) -> None:
    called = False

    async def link_pipeline(**_kwargs):
        nonlocal called
        called = True
        return {}

    app = create_app(_settings(tmp_path), link_pipeline_runner=link_pipeline)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Secure", "research_goal": "Validate paper inputs."},
        ).json()["canvas"]["id"]
        response = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": " http://publisher.example/paper.pdf "},
        )

    assert response.status_code == 422
    assert "must use HTTPS" in response.text
    assert called is False


@pytest.mark.parametrize(
    "paper_reference",
    ["ftp://publisher.example/paper.pdf", "publisher.example/paper", "not a paper"],
)
def test_paper_link_api_rejects_unsupported_reference_forms(
    tmp_path: Path, paper_reference: str
) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Strict", "research_goal": "Validate paper inputs."},
        ).json()["canvas"]["id"]
        response = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": paper_reference},
        )
    assert response.status_code == 422
    assert "HTTPS URL, DOI, or arXiv ID" in response.text


def test_paper_link_api_forbids_extra_input_fields(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Strict", "research_goal": "Reject extra input."},
        ).json()["canvas"]["id"]
        response = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": "10.1000/example", "unexpected": True},
        )
    assert response.status_code == 422
    assert "extra_forbidden" in response.text


async def _failed_link_pipeline(**_kwargs):
    raise PaperLinkError("paper_metadata_unresolved", "No verified metadata was found.")


def test_failed_link_processing_leaves_terminal_placeholder_reason(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), link_pipeline_runner=_failed_link_pipeline)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Links", "research_goal": "Map papers."},
        ).json()["canvas"]["id"]
        run = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": "https://example.test/paper"},
        ).json()
        events = client.get(f"/api/v1/runs/{run['id']}/events")
        paper = client.get(f"/api/v1/canvases/{canvas_id}").json()["papers"][0]

    assert "event: run.failed" in events.text
    assert "paper_metadata_unresolved" in events.text
    assert paper["processing_status"] == "failed"
    assert paper["error"] == "No verified metadata was found."


def test_unexpected_link_failure_does_not_expose_database_details(tmp_path: Path) -> None:
    async def unsafe_failure(**_kwargs):
        raise RuntimeError(
            "(sqlite3.IntegrityError) UNIQUE constraint failed: papers.arxiv_id "
            "[SQL: UPDATE papers SET arxiv_id=?] [parameters: ('secret-id',)]"
        )

    app = create_app(_settings(tmp_path), link_pipeline_runner=unsafe_failure)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Safe errors", "research_goal": "Map papers."},
        ).json()["canvas"]["id"]
        run = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": "2005.11401v2"},
        ).json()
        events = client.get(f"/api/v1/runs/{run['id']}/events")
        snapshot = client.get(f"/api/v1/canvases/{canvas_id}").json()
        with app.state.database.session_context() as session:
            job = session.scalar(select(Job).where(Job.run_id == run["id"]))

    public_text = events.text + json.dumps(snapshot) + (job.error if job else "")
    assert "event: run.failed" in events.text
    assert "The submitted paper could not be processed." in public_text
    assert "UNIQUE constraint" not in public_text
    assert "papers.arxiv_id" not in public_text
    assert "secret-id" not in public_text
    assert snapshot["papers"][0]["processing_status"] == "failed"
    assert job is not None and job.status == "failed"


def test_active_link_cancel_preserves_paper_and_terminal_placeholder(tmp_path: Path) -> None:
    started = threading.Event()

    async def slow_link_pipeline(**_kwargs):
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("cancel should stop the active link pipeline")

    app = create_app(_settings(tmp_path), link_pipeline_runner=slow_link_pipeline)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Cancel link", "research_goal": "Map papers."},
        ).json()["canvas"]["id"]
        queued = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": "https://doi.org/10.1000/cancel-me"},
        ).json()
        assert started.wait(timeout=2)
        cancelled = client.post(f"/api/v1/runs/{queued['id']}/cancel")
        snapshot = client.get(f"/api/v1/canvases/{canvas_id}").json()
        events = client.get(f"/api/v1/runs/{queued['id']}/events")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["paper_id"] == queued["paper_id"]
    assert snapshot["papers"][0]["id"] == queued["paper_id"]
    assert snapshot["papers"][0]["processing_status"] == "failed"
    assert snapshot["papers"][0]["error"] == "Cancelled by user."
    assert "event: run.cancelled" in events.text
    assert f'"paper_id": "{queued["paper_id"]}"' in events.text


async def _resolved_metadata(_: str) -> dict:
    return {
        "source_provider": "semantic_scholar",
        "candidate_id": "semantic-scholar:verified-1",
        "semantic_scholar_id": "verified-1",
        "title": "A Verified Paper",
        "authors": ["A. Researcher"],
        "year": 2024,
        "month": 2,
        "abstract": "The paper reports a verified method and result.",
        "url": "https://example.test/paper",
        "pdf_url": None,
    }


async def _abstract_source(_: Settings, candidate: dict) -> dict:
    return {
        "url": candidate["url"],
        "source_type": "abstract",
        "retrieval_status": "abstract_fallback",
        "pages": [{"page": None, "text": candidate["abstract"]}],
    }


async def _reviewer_events(_: Settings, agent_name: str, prompt: str):
    assert agent_name == "research-map-reviewer"
    evidence_id = re.search(r'"id":\s*"([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
    body = {
        "sections": {
            key: {
                "text": "Supported by the stored abstract.",
                "evidence_ids": [evidence_id],
                "confidence": "medium",
            }
            for key in SECTION_KEYS
        }
    }
    yield {"type": "model.message", "content": json.dumps(body)}
    yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}


def test_default_link_pipeline_persists_source_and_reviewer_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Links", research_goal="Map verified work.")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    from research_map_backend.paper_links import create_canvas_paper_placeholder

    placeholder_id = create_canvas_paper_placeholder(
        database, canvas_id, "https://doi.org/10.1000/verified"
    )

    events = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    summary = asyncio.run(
        run_link_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id="unused-by-link-pipeline",
            paper_url="https://doi.org/10.1000/verified",
            research_goal="Map verified work.",
            research_brief="",
            placeholder_paper_id=placeholder_id,
            emit=emit,
            agent_source=_reviewer_events,
            metadata_resolver=_resolved_metadata,
            source_acquirer=_abstract_source,
        )
    )
    with database.session_context() as session:
        membership = session.scalar(
            select(CanvasPaper).where(CanvasPaper.canvas_id == canvas_id)
        )
        review = session.scalar(select(Review).where(Review.canvas_id == canvas_id))
    database.dispose()

    assert summary["completed_reviews"] == 1
    assert summary["paper_id"] == placeholder_id
    assert membership is not None and membership.paper_id == placeholder_id
    assert membership is not None and membership.processing_status == "reviewed"
    assert review is not None and set(review.sections) == set(SECTION_KEYS)
    assert any(event_type == "review.completed" for event_type, _ in events)
