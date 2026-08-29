import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from research_map_backend.app import create_app
from research_map_backend.db import Database
from research_map_backend.migrations import upgrade_database
from research_map_backend.models import (
    AgentRun,
    Canvas,
    CanvasPaper,
    Conversation,
    DiscoveryDecision,
    Evidence,
    Job,
    Message,
    Paper,
    Relationship,
    Review,
)
from research_map_backend.chat import submit_chat_message
from research_map_backend.research_store import (
    get_canvas_snapshot,
    mark_paper_failed,
    record_discovery_batch,
    store_paper_source,
    store_pipeline_summary,
    store_relationship,
    store_review,
)
from research_map_backend.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'integration.sqlite3'}",
        openai_api_key="test",
        worker_concurrency=3,
        retry_base_seconds=0,
    )


def _candidate(canvas_id: str, index: int, year: int) -> dict:
    return {
        "candidate_id": f"{canvas_id}:{index}",
        "title": f"Paper {index} for {canvas_id}",
        "authors": [f"Author {index}"],
        "year": year,
        "month": 1,
        "abstract": f"Verified evidence for canvas {canvas_id}, paper {index}.",
        "url": f"https://example.test/{canvas_id}/{index}",
        "score": {"relevance": 25, "credibility": 15},
        "total_score": 80,
        "accepted": True,
        "reason": "Relevant and independently verified.",
    }


def _review(evidence_id: str) -> dict:
    keys = {
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
    return {
        "sections": {
            key: {
                "text": f"Supported {key}.",
                "evidence_ids": [evidence_id],
                "confidence": "medium",
            }
            for key in keys
        },
        "confidence": "medium",
    }


async def _main_source(_settings: Settings, goal: str):
    yield {
        "type": "model.message.delta",
        "content": json.dumps(
            {
                "brief_markdown": f"Brief for {goal}",
                "canonical_seed_titles": [
                    "Reliable Multi Canvas Research Agent Orchestration",
                    "Evidence Grounded Reviews for Autonomous Research Systems",
                ],
            }
        ),
    }
    yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}


async def _persisting_pipeline(**kwargs):
    database = kwargs["database"]
    canvas_id = kwargs["canvas_id"]
    run_id = kwargs["run_id"]
    research_goal = kwargs["research_goal"]
    has_failure = "failure" in research_goal.casefold()
    accepted = record_discovery_batch(
        database,
        canvas_id,
        run_id,
        1,
        [_candidate(canvas_id, index, 2020 + index) for index in range(3)],
    )
    evidence: dict[int, str] = {}
    for index in range(3):
        paper_id = accepted[f"{canvas_id}:{index}"]
        if has_failure and index == 2:
            mark_paper_failed(database, canvas_id, paper_id, "No usable source text.")
            continue
        stored = store_paper_source(
            database,
            paper_id,
            {
                "url": f"https://example.test/{canvas_id}/{index}",
                "source_type": "abstract",
                "retrieval_status": "abstract_fallback",
                "pages": [{"page": None, "text": f"Evidence {canvas_id} {index}."}],
            },
            canvas_id=canvas_id,
        )
        evidence[index] = stored["evidence_ids"][0]
        store_review(database, canvas_id, paper_id, _review(evidence[index]))
    store_relationship(
        database,
        canvas_id,
        {
            "source_paper_id": accepted[f"{canvas_id}:0"],
            "target_paper_id": accepted[f"{canvas_id}:1"],
            "type": "extends",
            "label": "extends",
            "explanation": "The newer canvas-scoped paper extends the older one.",
            "evidence_ids": [evidence[0], evidence[1]],
            "confidence": "high",
        },
    )
    summary = {
        "status": "completed_with_errors" if has_failure else "completed",
        "batch_count": 1,
        "accepted_papers": 3,
        "completed_reviews": 2 if has_failure else 3,
        "relationships": 1,
        "stopping_reason": "no_worthwhile_candidates",
        "coverage": [{"batch": 1, "goal": research_goal}],
        "failures": (
            [{"code": "paper_source_unavailable", "message": "No usable source text."}]
            if has_failure
            else []
        ),
    }
    store_pipeline_summary(database, canvas_id, summary)
    return summary


def _event_ids(stream: str) -> list[int]:
    return [int(value) for value in re.findall(r"^id: (\d+)$", stream, re.MULTILINE)]


def test_restart_recovers_queued_and_running_jobs_with_replay_and_snapshot(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings)
    database = Database(settings)
    seeded: list[tuple[str, str]] = []
    with database.session_context() as session:
        for status, attempts in [("queued", 0), ("running", 1)]:
            canvas = Canvas(
                name=f"Restart {status}",
                research_goal=f"Recover {status} work.",
                build_status=status,
            )
            session.add(canvas)
            session.flush()
            run = AgentRun(canvas_id=canvas.id, status=status)
            session.add(run)
            session.flush()
            session.add(
                Job(
                    canvas_id=canvas.id,
                    run_id=run.id,
                    kind="canvas_build",
                    status=status,
                    attempts=attempts,
                )
            )
            seeded.append((canvas.id, run.id))
        session.commit()
    database.dispose()

    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=_persisting_pipeline,
    )
    with TestClient(app) as client:
        for canvas_id, run_id in seeded:
            stream = client.get(f"/api/v1/runs/{run_id}/events")
            snapshot = client.get(f"/api/v1/canvases/{canvas_id}").json()
            ids = _event_ids(stream.text)
            replay = client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={"Last-Event-ID": str(ids[-2])},
            )
            assert stream.status_code == 200
            assert "event: run.completed" in stream.text
            assert len(snapshot["papers"]) == 3
            assert all(paper["review"] for paper in snapshot["papers"])
            assert len(snapshot["relationships"]) == 1
            assert replay.text.count("data:") == 1
            assert "event: run.completed" in replay.text
        with app.state.database.session_context() as session:
            jobs = list(session.scalars(select(Job).where(Job.kind == "canvas_build")))

    assert sorted(job.attempts for job in jobs) == [1, 2]
    assert {job.status for job in jobs} == {"completed"}


class _QueuedChat:
    async def enqueue(self, _job_id: str) -> None:
        return None


def test_three_canvas_isolation_failure_continuation_and_control_interactions(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(
        settings,
        agent_event_source=_main_source,
        pipeline_runner=_persisting_pipeline,
    )
    goals = ["Alpha goal.", "Beta failure goal.", "Gamma goal."]
    with TestClient(app) as client:
        canvas_ids = [
            client.post(
                "/api/v1/canvases",
                json={"name": f"Canvas {index}", "research_goal": goal},
            ).json()["canvas"]["id"]
            for index, goal in enumerate(goals)
        ]
        run_ids = []
        for canvas_id in canvas_ids:
            run_id = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()["id"]
            run_ids.append(run_id)
            assert "event: run.completed" in client.get(
                f"/api/v1/runs/{run_id}/events"
            ).text

        snapshots = {
            canvas_id: client.get(f"/api/v1/canvases/{canvas_id}").json()
            for canvas_id in canvas_ids
        }
        for index, canvas_id in enumerate(canvas_ids):
            positions = [
                {
                    "paper_id": paper["id"],
                    "x": float(index * 1000 + offset * 100),
                    "y": float(index * 100),
                    "pinned": True,
                }
                for offset, paper in enumerate(snapshots[canvas_id]["papers"])
            ]
            response = client.put(
                f"/api/v1/canvases/{canvas_id}/layout",
                json={"positions": positions},
            )
            assert response.status_code == 200

        # Keep chat jobs durably queued so the second message proves same-session queuing.
        app.state.chat_coordinator = _QueuedChat()
        first_chat = client.post(
            f"/api/v1/canvases/{canvas_ids[0]}/messages",
            json={"content": "First alpha question", "paper_ids": []},
        ).json()
        second_chat = client.post(
            f"/api/v1/canvases/{canvas_ids[0]}/messages",
            json={"content": "Second alpha question", "paper_ids": []},
        ).json()
        for index, canvas_id in enumerate(canvas_ids[1:], 1):
            isolated_chat = client.post(
                f"/api/v1/canvases/{canvas_id}/messages",
                json={"content": f"Question only for {goals[index]}", "paper_ids": []},
            )
            assert isolated_chat.status_code == 200
            assert isolated_chat.json()["queued"] is False
        assert first_chat["queued"] is False
        assert second_chat["queued"] is True

        # Exercise cancel and retry against a durable queued build without racing a worker.
        with app.state.database.session_context() as session:
            cancelled_run = AgentRun(canvas_id=canvas_ids[2], status="queued")
            session.add(cancelled_run)
            session.flush()
            session.add(
                Job(
                    canvas_id=canvas_ids[2],
                    run_id=cancelled_run.id,
                    kind="canvas_build",
                    status="queued",
                )
            )
            session.commit()
            cancelled_id = cancelled_run.id
        cancelled = client.post(f"/api/v1/runs/{cancelled_id}/cancel")
        retried = client.post(f"/api/v1/runs/{cancelled_id}/retry")
        assert cancelled.json()["status"] == "cancelled"
        retried_id = retried.json()["id"]
        assert "event: run.completed" in client.get(
            f"/api/v1/runs/{retried_id}/events"
        ).text

        with app.state.database.session_context() as session:
            canvases = {item.id: item for item in session.scalars(select(Canvas))}
            memberships = list(session.scalars(select(CanvasPaper)))
            reviews = list(session.scalars(select(Review)))
            relationships = list(session.scalars(select(Relationship)))
            conversations = list(session.scalars(select(Conversation)))
            messages = list(session.scalars(select(Message)))
            jobs = list(session.scalars(select(Job)))
            decisions = list(session.scalars(select(DiscoveryDecision)))

    paper_ids_by_canvas = {
        canvas_id: {
            membership.paper_id
            for membership in memberships
            if membership.canvas_id == canvas_id
        }
        for canvas_id in canvas_ids
    }
    assert all(
        paper_ids_by_canvas[left].isdisjoint(paper_ids_by_canvas[right])
        for index, left in enumerate(canvas_ids)
        for right in canvas_ids[index + 1 :]
    )
    for index, canvas_id in enumerate(canvas_ids):
        canvas = canvases[canvas_id]
        assert canvas.research_goal == goals[index]
        assert canvas.batch_count == 1
        assert canvas.research_brief["pipeline_summary"]["coverage"][0]["goal"] == goals[index]
        scoped_papers = paper_ids_by_canvas[canvas_id]
        assert len(scoped_papers) == 3
        assert all(
            relationship.source_paper_id in scoped_papers
            and relationship.target_paper_id in scoped_papers
            for relationship in relationships
            if relationship.canvas_id == canvas_id
        )
        assert all(review.paper_id in scoped_papers for review in reviews if review.canvas_id == canvas_id)
        assert len([item for item in decisions if item.canvas_id == canvas_id]) == 3
        assert all(
            item.candidate_key.startswith(f"{canvas_id}:")
            for item in decisions
            if item.canvas_id == canvas_id
        )

    beta_memberships = [item for item in memberships if item.canvas_id == canvas_ids[1]]
    assert sorted(item.processing_status for item in beta_memberships) == [
        "failed",
        "reviewed",
        "reviewed",
    ]
    assert len([item for item in reviews if item.canvas_id == canvas_ids[1]]) == 2
    assert len([item for item in relationships if item.canvas_id == canvas_ids[1]]) == 1
    assert {conversation.canvas_id for conversation in conversations} == set(canvas_ids)
    assert {message.canvas_id for message in messages} == set(canvas_ids)
    conversation_by_id = {item.id: item for item in conversations}
    assert all(
        conversation_by_id[message.conversation_id].canvas_id == message.canvas_id
        for message in messages
    )
    assert all(
        job.paper_id is None or job.paper_id in paper_ids_by_canvas[job.canvas_id]
        for job in jobs
    )
    assert all(
        job.payload.get("conversation_id") is None
        or conversation_by_id[job.payload["conversation_id"]].canvas_id == job.canvas_id
        for job in jobs
    )
    alpha_layout = {
        (item.x, item.y, item.pinned)
        for item in memberships
        if item.canvas_id == canvas_ids[0]
    }
    gamma_layout = {
        (item.x, item.y, item.pinned)
        for item in memberships
        if item.canvas_id == canvas_ids[2]
    }
    assert alpha_layout.isdisjoint(gamma_layout)


def test_shared_canonical_paper_keeps_all_canvas_owned_state_isolated(
    tmp_path: Path,
) -> None:
    database = Database(_settings(tmp_path))
    database.create_schema()
    with database.session_context() as session:
        canvases = [
            Canvas(name=f"Shared {index}", research_goal=f"Shared goal {index}")
            for index in range(3)
        ]
        session.add_all(canvases)
        session.commit()
        canvas_ids = [canvas.id for canvas in canvases]

    shared_paper_ids: list[str] = []
    partner_ids: list[str] = []
    evidence_by_canvas: dict[str, set[str]] = {}
    conversation_ids: list[str] = []
    for index, canvas_id in enumerate(canvas_ids):
        shared = {
            **_candidate("shared", 0, 2020),
            "candidate_id": "shared-canonical",
            "title": "One Canonical Paper",
            "doi": "10.1000/shared-canonical",
            "url": "https://example.test/shared-canonical",
        }
        partner = {
            **_candidate(canvas_id, 1, 2021 + index),
            "candidate_id": f"partner-{index}",
            "doi": f"10.1000/partner-{index}",
        }
        accepted = record_discovery_batch(
            database, canvas_id, None, 1, [shared, partner]
        )
        shared_id = accepted["shared-canonical"]
        partner_id = accepted[f"partner-{index}"]
        shared_paper_ids.append(shared_id)
        partner_ids.append(partner_id)
        shared_source = store_paper_source(
            database,
            shared_id,
            {
                "url": "https://example.test/shared-canonical",
                "source_type": "abstract",
                "retrieval_status": "abstract_fallback",
                "pages": [{"page": None, "text": "Shared source evidence."}],
            },
            canvas_id=canvas_id,
        )
        partner_source = store_paper_source(
            database,
            partner_id,
            {
                "url": f"https://example.test/partner-{index}",
                "source_type": "abstract",
                "retrieval_status": "abstract_fallback",
                "pages": [{"page": None, "text": f"Partner evidence {index}."}],
            },
            canvas_id=canvas_id,
        )
        shared_evidence = shared_source["evidence_ids"][0]
        partner_evidence = partner_source["evidence_ids"][0]
        evidence_by_canvas[canvas_id] = {shared_evidence, partner_evidence}
        store_review(database, canvas_id, shared_id, _review(shared_evidence))
        store_review(database, canvas_id, partner_id, _review(partner_evidence))
        store_relationship(
            database,
            canvas_id,
            {
                "source_paper_id": shared_id,
                "target_paper_id": partner_id,
                "type": "extends",
                "label": f"Canvas {index} relation",
                "explanation": f"Only canvas {index} owns this relationship.",
                "evidence_ids": [shared_evidence, partner_evidence],
                "confidence": "high",
            },
        )
        _, _, _, queued = submit_chat_message(
            database, canvas_id, f"Canvas {index} paper question", [shared_id]
        )
        assert queued is False
        with database.session_context() as session:
            membership = session.scalar(
                select(CanvasPaper).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id == shared_id,
                )
            )
            assert membership is not None
            membership.x = float(index * 1000)
            membership.y = float(index * 100)
            membership.pinned = index % 2 == 0
            membership.metadata_override = {"title": f"Canvas {index} Shared Title"}
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.canvas_id == canvas_id,
                    Conversation.paper_id == shared_id,
                )
            )
            assert conversation is not None
            conversation.trueforge_session_id = f"session-{index}"
            conversation_ids.append(conversation.id)
            session.commit()

    with database.session_context() as session:
        shared_papers = list(
            session.scalars(
                select(Paper).where(Paper.doi == "10.1000/shared-canonical")
            )
        )
        memberships = list(
            session.scalars(
                select(CanvasPaper).where(
                    CanvasPaper.paper_id == shared_paper_ids[0]
                )
            )
        )
        reviews = list(
            session.scalars(select(Review).where(Review.paper_id == shared_paper_ids[0]))
        )
        evidence = list(
            session.scalars(select(Evidence).where(Evidence.paper_id == shared_paper_ids[0]))
        )
        relationships = list(session.scalars(select(Relationship)))
        conversations = list(
            session.scalars(select(Conversation).where(Conversation.id.in_(conversation_ids)))
        )
        messages = list(
            session.scalars(select(Message).where(Message.conversation_id.in_(conversation_ids)))
        )
        jobs = list(
            session.scalars(select(Job).where(Job.payload["conversation_id"].as_string().in_(conversation_ids)))
        )
        snapshots = {
            canvas_id: get_canvas_snapshot(session, canvas_id).model_dump()
            for canvas_id in canvas_ids
        }
    database.dispose()

    assert len(shared_papers) == 1
    assert len(set(shared_paper_ids)) == 1
    assert {item.canvas_id for item in memberships} == set(canvas_ids)
    assert {(item.x, item.y, item.pinned) for item in memberships} == {
        (0.0, 0.0, True),
        (1000.0, 100.0, False),
        (2000.0, 200.0, True),
    }
    assert {item.canvas_id for item in reviews} == set(canvas_ids)
    assert {item.canvas_id for item in evidence} == set(canvas_ids)
    assert all(
        {
            evidence_id
            for section in review.sections.values()
            for evidence_id in section["evidence_ids"]
        }.issubset(evidence_by_canvas[review.canvas_id])
        for review in reviews
    )
    assert len(relationships) == 3
    assert all(
        relationship.target_paper_id == partner_ids[canvas_ids.index(relationship.canvas_id)]
        for relationship in relationships
    )
    assert {item.trueforge_session_id for item in conversations} == {
        "session-0",
        "session-1",
        "session-2",
    }
    conversation_by_id = {item.id: item for item in conversations}
    assert all(
        message.canvas_id == conversation_by_id[message.conversation_id].canvas_id
        for message in messages
    )
    assert all(
        job.canvas_id == conversation_by_id[job.payload["conversation_id"]].canvas_id
        and job.paper_id == shared_paper_ids[0]
        for job in jobs
    )
    for index, canvas_id in enumerate(canvas_ids):
        shared_snapshot = next(
            paper for paper in snapshots[canvas_id]["papers"] if paper["id"] == shared_paper_ids[0]
        )
        assert shared_snapshot["title"] == f"Canvas {index} Shared Title"
        assert snapshots[canvas_id]["relationships"][0]["label"] == f"Canvas {index} relation"
