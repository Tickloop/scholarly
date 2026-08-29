from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_map_backend.app import create_app
from research_map_backend.chat import (
    ChatCoordinator,
    _chat_context,
    _chat_prompt,
    submit_chat_message,
)
from research_map_backend.db import Database
from research_map_backend.models import (
    AgentRun,
    Canvas,
    CanvasPaper,
    Conversation,
    Job,
    Message,
    Paper,
)
from research_map_backend.runs import RunCoordinator
from research_map_backend.settings import Settings


class _NoopChatCoordinator:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.jobs.append(job_id)


def test_chat_routes_persist_and_route_canvas_paper_and_comparison_messages(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'chat.sqlite3'}",
        openai_api_key="test",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        noop = _NoopChatCoordinator()
        app.state.chat_coordinator = noop
        with app.state.database.session_context() as session:
            canvas = Canvas(name="Chat", research_goal="Map chat")
            other = Canvas(name="Other", research_goal="Other")
            first = Paper(
                title="First", normalized_title="first", authors=[], year=2020,
                month=1, summary="", url="https://example.test/first",
            )
            second = Paper(
                title="Second", normalized_title="second", authors=[], year=2021,
                month=1, summary="", url="https://example.test/second",
            )
            session.add_all([canvas, other, first, second])
            session.flush()
            session.add_all([
                CanvasPaper(canvas_id=canvas.id, paper_id=first.id),
                CanvasPaper(canvas_id=canvas.id, paper_id=second.id),
            ])
            session.commit()
            canvas_id, other_id = canvas.id, other.id
            first_id, second_id = first.id, second.id

        main = client.post(
            f"/api/v1/canvases/{canvas_id}/messages",
            json={"content": "Canvas question", "paper_ids": []},
        )
        reviewer = client.post(
            f"/api/v1/canvases/{canvas_id}/messages",
            json={"content": "Paper question", "paper_ids": [first_id]},
        )
        comparison = client.post(
            f"/api/v1/canvases/{canvas_id}/messages",
            json={"content": "Compare", "paper_ids": [first_id, second_id]},
        )

        assert main.status_code == reviewer.status_code == comparison.status_code == 200
        assert main.json()["message"]["agent_name"] == "research-map-main"
        assert reviewer.json()["message"]["agent_name"] == "research-map-reviewer"
        assert reviewer.json()["message"]["paper_id"] == first_id
        assert comparison.json()["message"]["agent_name"] == "research-map-main"
        assert comparison.json()["message"]["paper_id"] is None
        assert comparison.json()["queued"] is True
        assert len(noop.jobs) == 3

        messages = client.get(f"/api/v1/canvases/{canvas_id}/messages")
        assert messages.status_code == 200 and len(messages.json()) == 3
        assert client.get(f"/api/v1/canvases/{other_id}/messages").json() == []
        invalid = client.post(
            f"/api/v1/canvases/{other_id}/messages",
            json={"content": "Leak", "paper_ids": [first_id]},
        )
        assert invalid.status_code == 422

        single_context = _chat_context(app.state.database, canvas_id, [first_id])
        single_prompt = _chat_prompt("Explain", [first_id], single_context)
        assert "Map chat" in single_prompt and "First" in single_prompt
        assert "Second" not in single_prompt
        multi_context = _chat_context(
            app.state.database, canvas_id, [first_id, second_id]
        )
        multi_prompt = _chat_prompt("Compare", [first_id, second_id], multi_context)
        assert "Map chat" in multi_prompt
        assert "First" in multi_prompt and "Second" in multi_prompt


class _RunEvents:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.events: list[tuple[str, str, dict]] = []

    async def _append_event(self, run_id: str, event_type: str, payload: dict) -> None:
        self.events.append((run_id, event_type, payload))

    async def _finish_run(
        self,
        run_id: str,
        status: str,
        event_type: str,
        payload: dict,
        *,
        update_canvas: bool,
    ) -> None:
        assert update_canvas is False
        self.events.append((run_id, event_type, payload))
        with self.database.session_context() as session:
            run = session.get(AgentRun, run_id)
            assert run is not None
            run.status = status
            session.commit()


@pytest.mark.anyio
async def test_chat_coordinator_persists_assistant_output_and_terminal_events(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'chat-worker.sqlite3'}",
        openai_api_key="test",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Chat", research_goal="Goal")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    message, run, job, _ = submit_chat_message(database, canvas_id, "Hello", [])
    run_events = _RunEvents(database)
    coordinator = ChatCoordinator(database, settings, run_events)

    async def fake_turn(run_id, agent_name, prompt, session_id):
        assert agent_name == "research-map-main" and "Hello" in prompt
        assert '"research_goal":"Goal"' in prompt
        await run_events._append_event(
            run_id, "agent.message.delta", {"agent": agent_name, "content": "Answer"}
        )
        return "Answer", "session-1"

    coordinator._run_turn = fake_turn  # type: ignore[method-assign]
    await coordinator._execute(job.id)

    with database.session_context() as session:
        stored_job = session.get(Job, job.id)
        messages = list(session.query(Message).order_by(Message.created_at))
        stored_run = session.get(AgentRun, run.id)
    database.dispose()

    assert stored_job is not None and stored_job.status == "completed"
    assert stored_run is not None and stored_run.status == "completed"
    assert [(item.role, item.content, item.status) for item in messages] == [
        ("user", "Hello", "completed"),
        ("assistant", "Answer", "completed"),
    ]
    assert [event[1] for event in run_events.events] == [
        "run.started",
        "agent.message.delta",
        "run.completed",
    ]


@pytest.mark.anyio
async def test_chat_active_ids_cancel_propagation_and_terminal_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research_map_backend.chat as chat_module
    import research_map_backend.runs as runs_module

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'chat-cancel.sqlite3'}",
        openai_api_key="test",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Cancel", research_goal="Scoped cancellation")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    message, run, job, _ = submit_chat_message(database, canvas_id, "Wait", [])
    turn_started = __import__("asyncio").Event()
    release = __import__("asyncio").Event()
    cancelled: list[tuple[str, str]] = []

    class FakeTrueForge:
        def __init__(self, *_):
            pass

        async def create_session(self, agent_name: str) -> str:
            assert agent_name == "research-map-main"
            return "chat-session"

        async def stream_turn(self, session_id: str, prompt: str):
            assert session_id == "chat-session" and "Scoped cancellation" in prompt
            yield {"type": "turn.created", "turn_id": "chat-turn"}
            turn_started.set()
            await release.wait()
            yield {"type": "model.message.delta", "content": "late answer"}

    async def fake_cancel(_: Settings, session_id: str, turn_id: str) -> bool:
        cancelled.append((session_id, turn_id))
        return True

    monkeypatch.setattr(chat_module, "TrueForgeClient", FakeTrueForge)
    monkeypatch.setattr(runs_module, "cancel_trueforge_turn", fake_cancel)
    run_coordinator = RunCoordinator(database, settings, pipeline_runner=None)
    chat = ChatCoordinator(database, settings, run_coordinator)
    task = __import__("asyncio").create_task(chat._execute(job.id))
    await turn_started.wait()
    with database.session_context() as session:
        stored_run = session.get(AgentRun, run.id)
        conversation = session.get(Conversation, message.conversation_id)
        assert stored_run is not None
        assert stored_run.trueforge_session_id == "chat-session"
        assert stored_run.trueforge_turn_id == "chat-turn"
        assert conversation is not None
        assert conversation.trueforge_session_id == "chat-session"
        assert conversation.busy is True
    await run_coordinator.cancel(run.id)
    release.set()
    await task
    with database.session_context() as session:
        stored_run = session.get(AgentRun, run.id)
        stored_job = session.get(Job, job.id)
        stored_message = session.get(Message, message.id)
        conversation = session.get(Conversation, message.conversation_id)
        assistants = list(
            session.query(Message).filter(Message.role == "assistant")
        )
        assert stored_run is not None and stored_run.status == "cancelled"
        assert stored_job is not None and stored_job.status == "cancelled"
        assert stored_message is not None and stored_message.status == "failed"
        assert conversation is not None and conversation.busy is False
        assert assistants == []
    database.dispose()
    assert cancelled == [("chat-session", "chat-turn")]


@pytest.mark.anyio
async def test_chat_restart_requeues_running_message_and_reuses_session(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'chat-restart.sqlite3'}",
        openai_api_key="test",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Restart", research_goal="Resume context")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    message, run, job, _ = submit_chat_message(database, canvas_id, "Resume", [])
    with database.session_context() as session:
        stored_job = session.get(Job, job.id)
        stored_run = session.get(AgentRun, run.id)
        conversation = session.get(Conversation, message.conversation_id)
        assert stored_job is not None and stored_run is not None and conversation is not None
        stored_job.status = "running"
        stored_run.status = "running"
        conversation.busy = True
        conversation.trueforge_session_id = "existing-session"
        session.commit()
    events = _RunEvents(database)
    chat = ChatCoordinator(database, settings, events)

    async def fake_turn(run_id, agent_name, prompt, session_id):
        assert run_id == run.id and agent_name == "research-map-main"
        assert session_id == "existing-session" and "Resume context" in prompt
        return "Recovered", session_id

    chat._run_turn = fake_turn  # type: ignore[method-assign]
    await chat.start()
    await chat._queue.join()
    await chat.stop()
    with database.session_context() as session:
        stored_job = session.get(Job, job.id)
        stored_run = session.get(AgentRun, run.id)
        stored_message = session.get(Message, message.id)
        conversation = session.get(Conversation, message.conversation_id)
        assert stored_job is not None and stored_job.status == "completed"
        assert stored_run is not None and stored_run.status == "completed"
        assert stored_message is not None and stored_message.status == "completed"
        assert conversation is not None and conversation.trueforge_session_id == "existing-session"
        assert conversation.busy is False
    database.dispose()
