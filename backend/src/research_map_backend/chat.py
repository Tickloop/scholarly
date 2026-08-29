from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy import select

from research_map_backend.db import Database
from research_map_backend.integrations.trueforge import TrueForgeClient, TrueForgeError
from research_map_backend.models import (
    AgentRun,
    Canvas,
    CanvasPaper,
    Conversation,
    Job,
    Message,
)
from research_map_backend.settings import Settings
from research_map_backend.reviewer_slots import reviewer_slot

ChatClientFactory = Callable[[Settings], Any]


class ChatCoordinator:
    """Durable single-worker dispatcher for canvas and reviewer conversations."""

    def __init__(self, database: Database, settings: Settings, run_coordinator: Any) -> None:
        self._database = database
        self._settings = settings
        self._runs = run_coordinator
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        with self._database.session_context() as session:
            jobs = list(
                session.scalars(
                    select(Job).where(
                        Job.kind == "chat_message", Job.status.in_(("queued", "running"))
                    )
                )
            )
            for job in jobs:
                job.status = "queued"
                run = session.get(AgentRun, job.run_id) if job.run_id else None
                if run is not None and run.status == "running":
                    run.status = "queued"
            session.commit()
        for job in jobs:
            await self._queue.put(job.id)
        self._worker = asyncio.create_task(self._work(), name="chat-message-worker")

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def _work(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._execute(job_id)
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str) -> None:
        with self._database.session_context() as session:
            job = session.get(Job, job_id)
            if job is None or job.status == "cancelled":
                return
            conversation = session.get(Conversation, job.payload.get("conversation_id"))
            user_message = session.get(Message, job.payload.get("message_id"))
            run = session.get(AgentRun, job.run_id) if job.run_id else None
            if conversation is None or user_message is None or run is None:
                if job is not None:
                    job.status = "failed"
                    job.error = "Chat job references are missing."
                    session.commit()
                return
            job.status = "running"
            job.attempts += 1
            conversation.busy = True
            run.status = "running"
            session.commit()
            conversation_id = conversation.id
            run_id = run.id
            canvas_id = job.canvas_id
            agent_name = conversation.agent_name
            paper_id = conversation.paper_id
            existing_session_id = conversation.trueforge_session_id
            paper_ids = job.payload.get("paper_ids", [])
            prompt = _chat_prompt(
                user_message.content,
                paper_ids,
                _chat_context(self._database, job.canvas_id, paper_ids),
            )

        await self._runs._append_event(
            run_id, "run.started", {"agent": agent_name, "conversation_id": conversation_id}
        )
        try:
            if agent_name == "research-map-reviewer":
                async with reviewer_slot(
                    self._runs._reviewer_semaphore,
                    lambda event_type, payload: self._runs._append_event(
                        run_id, event_type, payload
                    ),
                    canvas_id=canvas_id,
                    run_id=run_id,
                    paper_id=paper_id,
                    purpose="reviewer_chat",
                ):
                    content, session_id = await self._run_turn(
                        run_id, agent_name, prompt, existing_session_id
                    )
            else:
                content, session_id = await self._run_turn(
                    run_id, agent_name, prompt, existing_session_id
                )
            if not content.strip():
                raise TrueForgeError("TrueForge returned an empty chat response")
            with self._database.session_context() as session:
                job = session.get(Job, job_id)
                conversation = session.get(Conversation, conversation_id)
                user_message = session.get(Message, job.payload["message_id"]) if job else None
                run = session.get(AgentRun, job.run_id) if job and job.run_id else None
                if job is None or conversation is None or run is None:
                    return
                if job.status == "cancelled" or run.status == "cancelled":
                    conversation.busy = False
                    session.commit()
                    return
                conversation.trueforge_session_id = session_id
                conversation.busy = False
                if user_message is not None:
                    user_message.status = "completed"
                assistant = Message(
                        canvas_id=job.canvas_id,
                        conversation_id=conversation.id,
                        paper_id=conversation.paper_id,
                        role="assistant",
                        agent_name=agent_name,
                        content=content.strip(),
                        status="completed",
                    )
                session.add(assistant)
                job.status = "completed"
                job.error = None
                session.commit()
                assistant_id = assistant.id
            await self._runs._finish_run(
                run_id,
                "completed",
                "run.completed",
                {"message_id": assistant_id, "agent": agent_name},
                update_canvas=False,
            )
        except Exception as error:
            with self._database.session_context() as session:
                job = session.get(Job, job_id)
                conversation = session.get(Conversation, conversation_id)
                run = session.get(AgentRun, job.run_id) if job and job.run_id else None
                user_message = session.get(Message, job.payload.get("message_id")) if job else None
                if job is not None:
                    job.status = "failed"
                    job.error = str(error) or "Chat agent failed."
                if conversation is not None:
                    conversation.busy = False
                if user_message is not None:
                    user_message.status = "failed"
                session.commit()
            await self._runs._finish_run(
                run_id,
                "failed",
                "run.failed",
                {"message": str(error) or "Chat agent failed.", "agent": agent_name},
                update_canvas=False,
            )

    async def _run_turn(
        self, run_id: str, agent_name: str, prompt: str, session_id: str | None
    ) -> tuple[str, str]:
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
            trueforge = TrueForgeClient(self._settings.trueforge_url, client)
            active_session = session_id or await trueforge.create_session(agent_name)
            store_ids = getattr(self._runs, "_store_trueforge_ids", None)
            if store_ids is not None:
                await store_ids(run_id, session_id=active_session)
            with self._database.session_context() as session:
                run = session.get(AgentRun, run_id)
                job = session.scalar(select(Job).where(Job.run_id == run_id))
                conversation = (
                    session.get(Conversation, job.payload.get("conversation_id"))
                    if job is not None
                    else None
                )
                if run is not None:
                    run.trueforge_session_id = active_session
                if conversation is not None:
                    conversation.trueforge_session_id = active_session
                session.commit()
            await self._runs._append_event(
                run_id,
                "agent.thread.started",
                {"agent": agent_name, "session_id": active_session},
            )
            parts: list[str] = []
            final_content: str | None = None
            try:
                async for event in trueforge.stream_turn(active_session, prompt):
                    if event.get("type") == "turn.created":
                        turn_id = event.get("turn_id")
                        if store_ids is not None and isinstance(turn_id, str):
                            await store_ids(run_id, turn_id=turn_id)
                    event_type = event.get("type")
                    content = event.get("content")
                    if event_type == "model.message" and isinstance(content, str):
                        final_content = content
                    elif event_type == "model.message.delta" and isinstance(content, str):
                        parts.append(content)
                        await self._runs._append_event(
                            run_id,
                            "agent.message.delta",
                            {"agent": agent_name, "content": content},
                        )
                    elif event_type == "transport.error":
                        await self._runs._append_event(
                            run_id,
                            "agent.transport.retry",
                            {
                                key: value
                                for key, value in event.items()
                                if key not in {"type", "content"}
                            },
                        )
            except TrueForgeError as error:
                await self._runs._append_event(
                    run_id,
                    "agent.transport.failed",
                    {
                        "agent": agent_name,
                        "session_id": active_session,
                        **error.transport_payload(status="failed"),
                    },
                )
                raise
            return final_content or "".join(parts), active_session


def submit_chat_message(
    database: Database,
    canvas_id: str,
    content: str,
    paper_ids: list[str],
) -> tuple[Message, AgentRun, Job, bool]:
    unique_ids = list(dict.fromkeys(paper_ids))
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        if canvas is None or canvas.deleted_at is not None:
            raise ValueError("Canvas does not exist")
        if unique_ids:
            valid = set(
                session.scalars(
                    select(CanvasPaper.paper_id).where(
                        CanvasPaper.canvas_id == canvas_id,
                        CanvasPaper.paper_id.in_(unique_ids),
                        CanvasPaper.deleted_at.is_(None),
                    )
                )
            )
            if valid != set(unique_ids):
                raise ValueError("Chat paper context must belong to the canvas")
        paper_id = unique_ids[0] if len(unique_ids) == 1 else None
        agent_name = "research-map-reviewer" if paper_id else "research-map-main"
        statement = select(Conversation).where(
            Conversation.canvas_id == canvas_id,
            Conversation.agent_name == agent_name,
        )
        statement = (
            statement.where(Conversation.paper_id == paper_id)
            if paper_id
            else statement.where(Conversation.paper_id.is_(None))
        )
        conversation = session.scalar(statement)
        if conversation is None:
            conversation = Conversation(
                canvas_id=canvas_id, paper_id=paper_id, agent_name=agent_name
            )
            session.add(conversation)
            session.flush()
        queued = conversation.busy or session.scalar(
            select(Job.id).where(
                Job.kind == "chat_message",
                Job.status.in_(("queued", "running", "retrying")),
                Job.payload["conversation_id"].as_string() == conversation.id,
            ).limit(1)
        ) is not None
        message = Message(
            canvas_id=canvas_id,
            conversation_id=conversation.id,
            paper_id=paper_id,
            role="user",
            agent_name=agent_name,
            content=content.strip(),
            status="queued",
        )
        run = AgentRun(canvas_id=canvas_id, agent_name=agent_name, status="queued")
        session.add_all([message, run])
        session.flush()
        job = Job(
            canvas_id=canvas_id,
            run_id=run.id,
            paper_id=paper_id,
            kind="chat_message",
            status="queued",
            payload={
                "conversation_id": conversation.id,
                "message_id": message.id,
                "content": message.content,
                "paper_ids": unique_ids,
            },
        )
        session.add(job)
        session.commit()
        return message, run, job, queued


def list_chat_messages(database: Database, canvas_id: str) -> list[Message]:
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        if canvas is None or canvas.deleted_at is not None:
            raise ValueError("Canvas does not exist")
        return list(
            session.scalars(
                select(Message)
                .where(Message.canvas_id == canvas_id)
                .order_by(Message.created_at, Message.id)
            )
        )


def _chat_context(
    database: Database, canvas_id: str, paper_ids: list[str]
) -> dict[str, Any]:
    from research_map_backend.research_store import get_pipeline_papers

    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        if canvas is None:
            raise ValueError("Canvas does not exist")
        canvas_context = {
            "id": canvas.id,
            "name": canvas.name,
            "research_goal": canvas.research_goal,
            "research_brief": canvas.research_brief,
        }
    papers = get_pipeline_papers(
        database, canvas_id, paper_ids if paper_ids else None
    )
    compact = [
        {
            key: paper.get(key)
            for key in ("id", "title", "authors", "year", "month", "summary", "url", "review")
        }
        for paper in papers
    ]
    return {"canvas": canvas_context, "papers": compact}


def _chat_prompt(content: str, paper_ids: list[str], context: dict[str, Any]) -> str:
    serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if len(paper_ids) == 1:
        instruction = "Answer about the selected paper using only its scoped canvas context."
    if len(paper_ids) > 1:
        instruction = "Compare only the selected papers in the scoped canvas context."
    if not paper_ids:
        instruction = "Answer for the active research canvas using its scoped context."
    return f"{instruction}\nCanvas context:\n{serialized}\nUser message:\n{content}"
