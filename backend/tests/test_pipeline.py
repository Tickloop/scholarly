import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from research_map_backend.app import create_app
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    Canvas,
    CanvasPaper,
    DiscoveryDecision,
    Paper,
    Relationship,
    Review,
)
from research_map_backend.pipeline import (
    SECTION_KEYS,
    AutonomousPipelineError,
    _validate_pair_output,
    _validate_relationship_output,
    run_autonomous_pipeline,
)
from research_map_backend.research_search import ResearchSearchError
from research_map_backend.research_store import get_canvas_snapshot
from research_map_backend.settings import Settings


def test_relationship_contract_canonicalizes_pairs_and_rejects_reverse_edges() -> None:
    reviews = [
        {
            "paper_id": "old",
            "title": "Foundational paper",
            "year": 2020,
            "month": 5,
            "evidence_ids": ["e-old"],
        },
        {
            "paper_id": "new",
            "title": "Extension paper",
            "year": 2023,
            "month": 1,
            "evidence_ids": ["e-new"],
        },
    ]
    pairs = _validate_pair_output(
        json.dumps(
            {
                "pairs": [
                    {
                        "source_paper_id": "new",
                        "target_paper_id": "old",
                        "reason": "The new paper builds on the old paper.",
                    }
                ]
            }
        ),
        reviews,
    )
    assert pairs == [
        {
            "source_paper_id": "old",
            "target_paper_id": "new",
            "reason": "The new paper builds on the old paper.",
        }
    ]

    reverse = json.dumps(
        {
            "relationships": [
                {
                    "source_paper_id": "new",
                    "target_paper_id": "old",
                    "relationship_type": "extends",
                    "label": "extends the foundation",
                    "explanation": "The new paper extends the old paper.",
                    "confidence": "high",
                    "source_evidence_ids": ["e-new"],
                    "target_evidence_ids": ["e-old"],
                }
            ]
        }
    )
    with pytest.raises(AutonomousPipelineError, match="unsupported"):
        _validate_relationship_output(reverse, pairs, reviews)


def _candidates(research_goal: str, _: str, count: int) -> Any:
    async def result() -> list[dict[str, Any]]:
        assert research_goal == "Map test research."
        assert count == 10
        return [
            {
                "candidate_id": f"candidate-{index}",
                "semantic_scholar_id": f"s2-{index}",
                "doi": None,
                "arxiv_id": None,
                "title": f"Paper {index}",
                "authors": [f"Author {index}"],
                "year": 2020 + index,
                "venue": "Test Venue",
                "abstract": f"Verified abstract evidence for paper {index}.",
                "url": f"https://example.test/paper-{index}",
                "pdf_url": f"https://example.test/paper-{index}.pdf",
                "citation_count": index,
            }
            for index in range(10)
        ]

    return result()


def _agent_source(*, fail_first_review: bool = False):
    reviewed: list[tuple[str, str]] = []
    review_attempts: dict[str, int] = {}

    async def source(_: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Evaluate"):
            payload = {
                "decisions": [
                    {
                        "candidate_id": f"candidate-{index}",
                        "score": {
                            "relevance": 25 if index < 2 else 10,
                            "source_credibility": 12 if index < 2 else 5,
                            "age_adjusted_impact": 10 if index < 2 else 2,
                            "full_text_availability": 12 if index < 2 else 2,
                            "novelty": 8 if index < 2 else 2,
                            "relationship_potential": 8 if index < 2 else 2,
                        },
                        "accepted": index < 2,
                        "reason": "Accepted by discovery." if index < 2 else "Too weak.",
                    }
                    for index in range(10)
                ]
            }
        elif agent_name == "research-map-reviewer":
            paper_id = re.search(r'"paper_id": "([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
            evidence = json.loads(prompt.split("Allowed evidence: ", 1)[1])[0]["id"]
            review_attempts[paper_id] = review_attempts.get(paper_id, 0) + 1
            should_fail = fail_first_review and '"title": "Paper 0"' in prompt
            if not should_fail:
                reviewed.append((paper_id, evidence))
            payload = {
                "sections": {
                    key: {
                        "text": "Grounded in the supplied abstract.",
                        "evidence_ids": [] if should_fail else [evidence],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        elif agent_name == "research-map-discovery":
            payload = {
                "pairs": [
                    {
                        "source_paper_id": reviewed[0][0],
                        "target_paper_id": reviewed[1][0],
                        "reason": "Their verified abstracts describe related work.",
                    }
                ]
            }
        else:
            payload = {
                "relationships": [
                    {
                        "source_paper_id": reviewed[0][0],
                        "target_paper_id": reviewed[1][0],
                        "relationship_type": "related",
                        "label": "related evidence",
                        "explanation": "Both verified abstracts support a basic relation.",
                        "confidence": "medium",
                        "source_evidence_ids": [reviewed[0][1]],
                        "target_evidence_ids": [reviewed[1][1]],
                    }
                ]
            }
        yield {"type": "session.created", "session_id": f"session-{agent_name}"}
        yield {"type": "model.message.delta", "content": json.dumps(payload)}
        yield {"type": "turn.done", "state": {"status": "done"}}

    return source


async def _paper_source(_: Settings, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": candidate["url"],
        "source_type": "abstract",
        "retrieval_status": "abstract_fallback",
        "pages": [{"page": None, "text": candidate["abstract"]}],
        "error": "PDF fixture is intentionally unavailable.",
    }


async def _main_source(_: Settings, __: str):
    yield {"type": "session.created", "session_id": "main-session"}
    yield {"type": "turn.created", "turn_id": "main-turn"}
    yield {
        "type": "model.message.delta",
        "content": json.dumps(
            {
                "brief_markdown": "Complete stored brief.",
                "canonical_seed_titles": [
                    "Dense Passage Retrieval for Open-Domain Question Answering",
                    "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                ],
            }
        ),
    }
    yield {"type": "turn.done", "state": {"status": "done"}}


def test_goal_runs_complete_autonomous_pipeline_before_terminal_sse(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'pipeline.sqlite3'}",
        openai_api_key="test-key",
    )
    specialist_source = _agent_source()

    async def pipeline(**kwargs: Any) -> dict[str, Any]:
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=specialist_source,
            candidate_source=_candidates,
            paper_source_acquirer=_paper_source,
        )

    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=pipeline,
    )
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Autonomous", "research_goal": "Map test research."},
        ).json()["canvas"]["id"]
        run_id = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()["id"]
        streamed = client.get(f"/api/v1/runs/{run_id}/events")

        with client.app.state.database.session_context() as session:
            run = session.get(AgentRun, run_id)
            canvas = session.get(Canvas, canvas_id)
            decision_count = session.scalar(
                select(func.count()).select_from(DiscoveryDecision)
            )
            review_count = session.scalar(select(func.count()).select_from(Review))
            relationship_count = session.scalar(
                select(func.count()).select_from(Relationship)
            )
            snapshot = get_canvas_snapshot(session, canvas_id)

    assert run is not None and run.status == "completed"
    assert canvas is not None
    assert canvas.research_brief["text"] == "Complete stored brief."
    assert len(canvas.research_brief["canonical_seed_titles"]) == 2
    assert canvas.research_brief["pipeline_summary"]["stop_reason"] == "insufficient_unique_candidates"
    assert decision_count == 10
    assert review_count == 2
    assert relationship_count == 1
    assert len(snapshot.papers) == 2
    assert len(snapshot.relationships) == 1
    assert streamed.text.index("event: paper.added") < streamed.text.index(
        "event: run.completed"
    )
    assert streamed.text.index("event: review.completed") < streamed.text.index(
        "event: run.completed"
    )
    assert streamed.text.index("event: relationship.added") < streamed.text.index(
        "event: run.completed"
    )


def test_academic_source_failure_is_a_clear_terminal_event(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'source-failure.sqlite3'}",
        openai_api_key="test-key",
    )

    async def unavailable_source(_: str, __: str, ___: int) -> list[dict[str, Any]]:
        raise ResearchSearchError("Semantic Scholar is unavailable.")

    async def pipeline(**kwargs: Any) -> dict[str, Any]:
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=_agent_source(),
            candidate_source=unavailable_source,
            paper_source_acquirer=_paper_source,
        )

    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=pipeline,
    )
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Failure", "research_goal": "Map test research."},
        ).json()["canvas"]["id"]
        run_id = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()["id"]
        streamed = client.get(f"/api/v1/runs/{run_id}/events")
        with client.app.state.database.session_context() as session:
            run = session.get(AgentRun, run_id)

    assert run is not None and run.status == "failed"
    assert "event: tool.started" in streamed.text
    assert "event: run.failed" in streamed.text
    assert "candidate_context_failed" in streamed.text
    assert "Semantic Scholar is unavailable" in streamed.text


def test_review_validation_failure_marks_only_that_paper_failed(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'review-failure.sqlite3'}",
        openai_api_key="test-key",
    )
    specialist_source = _agent_source(fail_first_review=True)

    async def pipeline(**kwargs: Any) -> dict[str, Any]:
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=specialist_source,
            candidate_source=_candidates,
            paper_source_acquirer=_paper_source,
        )

    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=pipeline,
    )
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Review failure", "research_goal": "Map test research."},
        ).json()["canvas"]["id"]
        run_id = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()["id"]
        streamed = client.get(f"/api/v1/runs/{run_id}/events")
        with client.app.state.database.session_context() as session:
            states = {
                title: (processing_status, error)
                for title, processing_status, error in session.execute(
                    select(Paper.title, CanvasPaper.processing_status, CanvasPaper.error)
                    .join(CanvasPaper, CanvasPaper.paper_id == Paper.id)
                    .where(CanvasPaper.canvas_id == canvas_id)
                )
            }
            run = session.get(AgentRun, run_id)
            failure_events = list(
                session.scalars(
                    select(AgentRunEvent).where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.type == "tool.completed",
                    )
                )
            )

    assert run is not None and run.status == "completed_with_errors"
    assert states["Paper 0"] == (
        "failed",
        "Review section core_idea must cite evidence or say not reported.",
    )
    assert states["Paper 1"] == ("reviewed", None)
    assert any(
        event.payload.get("paper_id")
        and event.payload.get("code") == "invalid_review_output"
        and event.payload.get("status") == "failed"
        for event in failure_events
    )
    assert "event: run.completed" in streamed.text
    assert "invalid_review_output" in streamed.text


def test_unavailable_source_marks_only_that_paper_failed(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'paper-source-failure.sqlite3'}",
        openai_api_key="test-key",
    )

    async def partial_source(
        source_settings: Settings, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        if candidate["title"] == "Paper 0":
            return {
                "url": candidate["url"],
                "source_type": "unavailable",
                "retrieval_status": "unavailable",
                "pages": [],
                "error": "The PDF had no extractable text and no abstract fallback.",
            }
        return await _paper_source(source_settings, candidate)

    async def pipeline(**kwargs: Any) -> dict[str, Any]:
        return await run_autonomous_pipeline(
            **kwargs,
            agent_source=_agent_source(),
            candidate_source=_candidates,
            paper_source_acquirer=partial_source,
        )

    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=pipeline,
    )
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Source failure", "research_goal": "Map test research."},
        ).json()["canvas"]["id"]
        run_id = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()["id"]
        streamed = client.get(f"/api/v1/runs/{run_id}/events")
        with client.app.state.database.session_context() as session:
            run = session.get(AgentRun, run_id)
            states = {
                title: processing_status
                for title, processing_status in session.execute(
                    select(Paper.title, CanvasPaper.processing_status)
                    .join(CanvasPaper, CanvasPaper.paper_id == Paper.id)
                    .where(CanvasPaper.canvas_id == canvas_id)
                )
            }

    assert run is not None and run.status == "completed_with_errors"
    assert states["Paper 0"] == "failed"
    assert states["Paper 1"] == "reviewed"
    assert "paper_source_unavailable" in streamed.text
    assert "event: run.completed" in streamed.text
