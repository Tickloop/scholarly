import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from research_map_backend.chat import ChatCoordinator, submit_chat_message
from research_map_backend.db import Database
from research_map_backend.link_pipeline import run_link_pipeline
from research_map_backend.models import AgentRun, Canvas, CanvasPaper, Job, Paper
from research_map_backend.pipeline import SECTION_KEYS, run_autonomous_pipeline
from research_map_backend.runs import RunCoordinator
from research_map_backend.settings import Settings


@pytest.mark.anyio
async def test_all_four_reviewer_sources_share_one_global_limit_of_two(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'mixed-reviewers.sqlite3'}",
        openai_api_key="test",
        reviewer_concurrency=2,
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        autonomous_canvas = Canvas(name="Autonomous", research_goal="Auto goal")
        link_canvas = Canvas(name="Link", research_goal="Link goal")
        chat_canvas = Canvas(name="Chat", research_goal="Chat goal")
        session.add_all([autonomous_canvas, link_canvas, chat_canvas])
        session.flush()
        autonomous_run = AgentRun(canvas_id=autonomous_canvas.id)
        link_run = AgentRun(canvas_id=link_canvas.id)
        chat_paper = Paper(
            title="Chat paper",
            normalized_title="chat paper",
            authors=[],
            year=2024,
            month=1,
            summary="Chat context",
            url="https://example.test/chat",
        )
        session.add_all([autonomous_run, link_run, chat_paper])
        session.flush()
        session.add(CanvasPaper(canvas_id=chat_canvas.id, paper_id=chat_paper.id))
        direct_run = AgentRun(
            canvas_id=chat_canvas.id,
            agent_name="research-map-reviewer",
            status="queued",
        )
        session.add(direct_run)
        session.flush()
        session.add(
            Job(
                canvas_id=chat_canvas.id,
                run_id=direct_run.id,
                paper_id=chat_paper.id,
                kind="direct_agent",
                payload={"prompt": "Direct review debug", "paper_ids": [chat_paper.id]},
            )
        )
        session.commit()
        autonomous_ids = autonomous_canvas.id, autonomous_run.id
        link_ids = link_canvas.id, link_run.id
        chat_canvas_id, chat_paper_id = chat_canvas.id, chat_paper.id
        direct_run_id = direct_run.id

    _, chat_run, chat_job, _ = submit_chat_message(
        database, chat_canvas_id, "Explain this paper", [chat_paper_id]
    )
    coordinator = RunCoordinator(database, settings)
    chat = ChatCoordinator(database, settings, coordinator)
    active = 0
    maximum = 0
    seen: set[str] = set()

    async def tracked(label: str) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        seen.add(label)
        await asyncio.sleep(0.01)
        active -= 1

    async def candidates(_: str, __: str, count: int) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": f"auto-{index}",
                "semantic_scholar_id": f"auto-s2-{index}",
                "title": f"Auto Paper {index}",
                "authors": ["Researcher"],
                "year": 2020 + index,
                "month": 1,
                "abstract": "Verified abstract",
                "url": f"https://example.test/auto/{index}",
                "pdf_url": f"https://example.test/auto/{index}.pdf",
            }
            for index in range(count)
        ]

    async def source(_: Settings, candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": candidate["url"],
            "source_type": "abstract",
            "retrieval_status": "abstract_fallback",
            "pages": [{"page": None, "text": candidate["abstract"]}],
        }

    async def specialists(_: Settings, agent_name: str, prompt: str):
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
                        "reason": "Worthwhile",
                    }
                    for item in supplied
                ]
            }
        elif agent_name == "research-map-reviewer":
            await tracked("link" if "Link Paper" in prompt else "autonomous")
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

    async def link_metadata(_: str) -> dict[str, Any]:
        return {
            "candidate_id": "link-paper",
            "title": "Link Paper",
            "authors": ["Link Author"],
            "year": 2024,
            "month": 1,
            "abstract": "Verified link abstract",
            "url": "https://example.test/link",
            "pdf_url": "https://example.test/link.pdf",
        }

    async def chat_turn(*_: Any) -> tuple[str, str]:
        await tracked("chat")
        return "Chat answer", "chat-session"

    async def direct_source(_: Settings, agent_name: str, __: str):
        assert agent_name == "research-map-reviewer"
        await tracked("direct")
        yield {"type": "session.created", "session_id": "direct-session"}
        yield {"type": "turn.created", "turn_id": "direct-turn"}
        yield {"type": "model.message.delta", "content": "Direct answer"}
        yield {"type": "turn.done", "state": {"status": "done"}}

    coordinator._direct_agent_source = direct_source
    chat._run_turn = chat_turn  # type: ignore[method-assign]

    async def emit(_: str, __: dict[str, Any]) -> None:
        return None

    await asyncio.gather(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=autonomous_ids[0],
            run_id=autonomous_ids[1],
            research_goal="Auto goal",
            research_brief="Auto brief",
            emit=emit,
            agent_source=specialists,
            candidate_source=candidates,
            paper_source_acquirer=source,
            reviewer_semaphore=coordinator._reviewer_semaphore,
        ),
        run_link_pipeline(
            database=database,
            settings=settings,
            canvas_id=link_ids[0],
            run_id=link_ids[1],
            paper_url="https://example.test/link",
            research_goal="Link goal",
            research_brief="Link brief",
            emit=emit,
            agent_source=specialists,
            metadata_resolver=link_metadata,
            source_acquirer=source,
            reviewer_semaphore=coordinator._reviewer_semaphore,
        ),
        chat._execute(chat_job.id),
        coordinator._execute_direct(direct_run_id, chat_canvas_id),
    )
    database.dispose()

    assert seen == {"autonomous", "link", "chat", "direct"}
    assert maximum == 2
