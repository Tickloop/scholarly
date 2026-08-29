from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from research_map_backend.db import Database
from research_map_backend.canvas_events import CanvasEventBroadcaster
from research_map_backend.diagnostics import public_diagnostic_value
from research_map_backend.integrations.trueforge import (
    TrueForgeError,
    cancel_trueforge_turn,
    run_agent,
    run_main_agent,
)
from research_map_backend.link_pipeline import run_link_pipeline
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    ActiveReviewMessage,
    Canvas,
    CanvasPaper,
    Conversation,
    Job,
    Message,
)
from research_map_backend.paper_links import PaperLinkError
from research_map_backend.pipeline import AutonomousPipelineError, run_autonomous_pipeline
from research_map_backend.schemas import ResearchBriefOutput
from research_map_backend.reviewer_slots import reviewer_slot
from research_map_backend.settings import Settings
from research_map_backend.structured_logging import context

AgentEventSource = Callable[[Settings, str], AsyncIterator[dict[str, Any]]]
DirectAgentSource = Callable[[Settings, str, str], AsyncIterator[dict[str, Any]]]
PipelineRunner = Callable[..., Awaitable[dict[str, Any]]]
LinkPipelineRunner = Callable[..., Awaitable[dict[str, Any]]]
TERMINAL_RUN_STATUSES = {"completed", "completed_with_errors", "failed", "cancelled"}
COMPLETED_CANVAS_BUILD_RETRY_STOPS = {
    "insufficient_relevant_candidates",
    "insufficient_unique_candidates",
    "academic_search_unavailable_after_checkpoint",
}
NORMALIZABLE_CANVAS_BUILD_RETRY_STOPS = {
    *COMPLETED_CANVAS_BUILD_RETRY_STOPS,
    # Compatibility with checkpoints written before the clean-stop policy.
    "academic_search_unavailable",
}
logger = logging.getLogger("research_map.runs")


async def _empty_event_source() -> AsyncIterator[dict[str, Any]]:
    if False:
        yield {}


class RunCoordinator:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        event_source: AgentEventSource = run_main_agent,
        direct_agent_source: DirectAgentSource = run_agent,
        pipeline_runner: PipelineRunner | None = run_autonomous_pipeline,
        link_pipeline_runner: LinkPipelineRunner = run_link_pipeline,
        canvas_events: CanvasEventBroadcaster | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._event_source = event_source
        self._direct_agent_source = direct_agent_source
        self._pipeline_runner = pipeline_runner
        self._link_pipeline_runner = link_pipeline_runner
        self._canvas_events = canvas_events
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._active_runs: dict[str, asyncio.Task[None]] = {}
        self._conditions: defaultdict[str, asyncio.Condition] = defaultdict(
            asyncio.Condition
        )
        self._reviewer_semaphore = asyncio.Semaphore(settings.reviewer_concurrency)
        self._versions: defaultdict[str, int] = defaultdict(int)
        self._stopping = False

    async def start(self) -> None:
        from research_map_backend.research_store import (
            cleanup_terminal_discovery_submissions,
            recover_interrupted_jobs,
        )

        self._stopping = False
        cleanup_terminal_discovery_submissions(self._database)
        recovered = set(recover_interrupted_jobs(self._database))
        with self._database.session_context() as session:
            runs = list(
                session.scalars(
                    select(AgentRun)
                    .join(Job, Job.run_id == AgentRun.id)
                    .where(
                        AgentRun.status == "queued",
                        Job.kind.in_(("canvas_build", "paper_link", "direct_agent")),
                    )
                    .distinct()
                )
            )
        for run in runs:
            recovered.add(run.id)
        for run_id in recovered:
            await self._queue.put(run_id)
        self._workers = [
            asyncio.create_task(self._work(), name=f"agent-run-worker-{index + 1}")
            for index in range(self._settings.worker_concurrency)
        ]

    async def stop(self) -> None:
        self._stopping = True
        for run_id in set(self._conditions) | set(self._active_runs):
            await self._notify(run_id)
        for task in self._active_runs.values():
            task.cancel()
        if self._active_runs:
            await asyncio.gather(*self._active_runs.values(), return_exceptions=True)
        self._active_runs.clear()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    @property
    def stopping(self) -> bool:
        return self._stopping

    async def enqueue(self, run_id: str) -> None:
        await self._queue.put(run_id)

    async def cancel(self, run_id: str) -> str:
        from research_map_backend.research_store import (
            clear_pending_discovery_submissions_from_job,
        )

        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"Run {run_id} does not exist")
            if run.status in TERMINAL_RUN_STATUSES:
                return run.id
            run.status = "cancelled"
            run.error = "Cancelled by user."
            canvas = session.get(Canvas, run.canvas_id)
            jobs = list(session.scalars(select(Job).where(Job.run_id == run_id)))
            if canvas is not None and any(job.kind == "canvas_build" for job in jobs):
                canvas.build_status = "cancelled"
            paper_id = next(
                (job.paper_id for job in jobs if job.paper_id is not None), None
            )
            for job in jobs:
                job.status = "cancelled"
                job.error = "Cancelled by user."
                job.lease_until = None
                if job.kind == "canvas_build":
                    clear_pending_discovery_submissions_from_job(job)
                if job.kind == "paper_link" and job.paper_id is not None:
                    membership = session.scalar(
                        select(CanvasPaper).where(
                            CanvasPaper.canvas_id == run.canvas_id,
                            CanvasPaper.paper_id == job.paper_id,
                            CanvasPaper.deleted_at.is_(None),
                        )
                    )
                    if membership is not None:
                        # PaperRead already represents retryable terminal failures.
                        membership.processing_status = "failed"
                        membership.error = "Cancelled by user."
                if job.kind == "chat_message":
                    message = session.get(Message, job.payload.get("message_id"))
                    conversation = session.get(
                        Conversation, job.payload.get("conversation_id")
                    )
                    if message is not None:
                        message.status = "failed"
                    if conversation is not None:
                        conversation.busy = False
                if job.kind == "paper_review":
                    message = session.scalar(
                        select(ActiveReviewMessage).where(
                            ActiveReviewMessage.job_id == job.id
                        )
                    )
                    if message is not None:
                        session.delete(message)
            event_payload: dict[str, Any] = {
                "code": "cancelled",
                "message": "Cancelled by user.",
            }
            if paper_id is not None:
                event_payload["paper_id"] = paper_id
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=run.canvas_id,
                    type="run.cancelled",
                    payload=event_payload,
                )
            )
            session_id = run.trueforge_session_id
            turn_id = run.trueforge_turn_id
            session.commit()
        active = self._active_runs.get(run_id)
        if active is not None:
            active.cancel()
        turn_targets = self._trueforge_turn_targets(
            run_id, fallback_session_id=session_id, fallback_turn_id=turn_id
        )
        cancellation_results = await asyncio.gather(
            *(
                self._cancel_specialist_turn(run_id, target)
                for target in turn_targets
            )
        )
        for payload in cancellation_results:
            await self._append_event(run_id, "agent.turn.cancelled", payload)
        if self._canvas_events is not None:
            await self._canvas_events.publish(run.canvas_id, "run.cancelled")
        await self._notify(run_id)
        return run_id

    def _trueforge_turn_targets(
        self,
        run_id: str,
        *,
        fallback_session_id: str | None,
        fallback_turn_id: str | None,
    ) -> list[dict[str, str]]:
        """Rebuild every persisted specialist session/turn pair after restart."""
        sessions: dict[tuple[str, str], str] = {}
        targets: list[dict[str, str]] = []
        with self._database.session_context() as session:
            events = list(
                session.scalars(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.type == "agent.thread.started",
                    )
                    .order_by(AgentRunEvent.id)
                )
            )
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            agent = payload.get("agent")
            thread_id = payload.get("thread_id")
            if not isinstance(agent, str):
                agent = "research-map-main"
            if not isinstance(thread_id, str):
                thread_id = agent
            key = (agent, thread_id)
            session_value = payload.get("session_id")
            if isinstance(session_value, str) and session_value:
                sessions[key] = session_value
            turn_value = payload.get("turn_id")
            active_session = sessions.get(key)
            if (
                isinstance(turn_value, str)
                and turn_value
                and isinstance(active_session, str)
            ):
                # Keep every observed pair, including prior retry attempts. The
                # remote cancellation endpoint safely tolerates terminal turns.
                targets.append(
                    {
                        "agent": agent,
                        "thread_id": thread_id,
                        "session_id": active_session,
                        "turn_id": turn_value,
                    }
                )
        if fallback_session_id and fallback_turn_id:
            targets.append(
                {
                    "agent": "research-map-main",
                    "thread_id": "main",
                    "session_id": fallback_session_id,
                    "turn_id": fallback_turn_id,
                }
            )
        unique: dict[tuple[str, str], dict[str, str]] = {}
        for target in targets:
            unique[(target["session_id"], target["turn_id"])] = target
        return list(unique.values())

    async def _cancel_specialist_turn(
        self, run_id: str, target: dict[str, str]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {**target, "cancelled": False}
        try:
            payload["cancelled"] = await cancel_trueforge_turn(
                self._settings, target["session_id"], target["turn_id"]
            )
        except Exception as error:
            payload["error"] = str(error) or "TrueForge cancellation failed."
        return payload

    async def retry(self, run_id: str) -> str:
        from research_map_backend.models import CanvasPaper

        with self._database.session_context() as session:
            original = session.get(AgentRun, run_id)
            if original is None:
                raise ValueError(f"Run {run_id} does not exist")
            original_job = session.scalar(
                select(Job)
                .where(Job.run_id == run_id, Job.kind != "paper_review")
                .order_by(Job.created_at.desc())
            )
            canvas = session.get(Canvas, original.canvas_id)
            ordinary_retry = original.status in {
                "failed",
                "cancelled",
                "completed_with_errors",
            }
            completed_checkpoint_retry = (
                original.status == "completed"
                and original_job is not None
                and original_job.kind == "canvas_build"
                and canvas is not None
                and _completed_canvas_build_retryable(canvas)
            )
            if not ordinary_retry and not completed_checkpoint_retry:
                raise ValueError(
                    "Only failed, cancelled, completed-with-errors, or explicitly "
                    "retryable completed canvas-build runs can retry"
                )
            retried = AgentRun(
                canvas_id=original.canvas_id,
                agent_name=original.agent_name,
                status="queued",
            )
            session.add(retried)
            session.flush()
            if original_job is not None:
                session.add(
                    Job(
                        canvas_id=original_job.canvas_id,
                        run_id=retried.id,
                        paper_id=original_job.paper_id,
                        kind=original_job.kind,
                        status="queued",
                        payload=original_job.payload,
                        max_attempts=original_job.max_attempts,
                    )
                )
                if original_job.paper_id:
                    membership = session.scalar(
                        select(CanvasPaper).where(
                            CanvasPaper.canvas_id == original.canvas_id,
                            CanvasPaper.paper_id == original_job.paper_id,
                        )
                    )
                    if membership is not None:
                        membership.processing_status = "queued"
                        membership.error = None
            if (
                canvas is not None
                and original_job is not None
                and original_job.kind == "canvas_build"
            ):
                _prepare_canvas_build_retry(canvas)
                canvas.build_status = "queued"
            session.commit()
            retried_id = retried.id
        if original_job is None or original_job.kind in {
            "canvas_build",
            "paper_link",
            "direct_agent",
        }:
            await self.enqueue(retried_id)
        if self._canvas_events is not None:
            await self._canvas_events.publish(original.canvas_id, "run.queued")
        return retried_id

    async def record_progress(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        allowed = {
            "agent.thread.started",
            "agent.message.delta",
            "tool.started",
            "tool.completed",
            "paper.added",
            "review.completed",
            "relationship.added",
        }
        if event_type not in allowed:
            raise ValueError(f"Unsupported progress event type: {event_type}")
        await self._append_event(run_id, event_type, payload)

    def version(self, run_id: str) -> int:
        return self._versions[run_id]

    async def wait_for_change(self, run_id: str, version: int) -> None:
        condition = self._conditions[run_id]
        async with condition:
            await condition.wait_for(lambda: self._versions[run_id] != version)

    async def _notify(self, run_id: str) -> None:
        condition = self._conditions[run_id]
        async with condition:
            self._versions[run_id] += 1
            condition.notify_all()

    async def _work(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                task = asyncio.create_task(
                    self._execute_leased(run_id), name=f"agent-run:{run_id}"
                )
                self._active_runs[run_id] = task
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._active_runs.pop(run_id, None)
            finally:
                self._queue.task_done()

    async def _execute_leased(self, run_id: str) -> None:
        from research_map_backend.research_store import (
            heartbeat_job,
            lease_job,
            update_job,
        )

        with self._database.session_context() as session:
            job = session.scalar(
                select(Job).where(Job.run_id == run_id, Job.kind != "paper_review")
            )
            job_id = job.id if job is not None else None
            canvas_id = job.canvas_id if job is not None else None
            paper_id = job.paper_id if job is not None else None
        logger.info(
            "job execution starting",
            extra=context(
                canvas_id=canvas_id,
                paper_id=paper_id,
                job_id=job_id,
                run_id=run_id,
            ),
        )
        if job_id is not None and not lease_job(
            self._database, job_id, self._settings.job_lease_seconds
        ):
            return

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self._settings.job_heartbeat_seconds)
                if job_id is None or not heartbeat_job(
                    self._database, job_id, self._settings.job_lease_seconds
                ):
                    return

        heartbeat_task = (
            asyncio.create_task(heartbeat(), name=f"job-heartbeat:{job_id}")
            if job_id is not None
            else None
        )
        try:
            await self._execute(run_id)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if job_id is not None:
                if self._stopping:
                    return
                with self._database.session_context() as session:
                    run = session.get(AgentRun, run_id)
                    job = session.get(Job, job_id)
                    final_status = run.status if run is not None else "failed"
                    job_status = job.status if job is not None else None
                    job_kind = job.kind if job is not None else None
                    last_failure = session.scalar(
                        select(AgentRunEvent)
                        .where(
                            AgentRunEvent.run_id == run_id,
                            AgentRunEvent.type == "run.failed",
                        )
                        .order_by(AgentRunEvent.id.desc())
                    )
                    failure_code = (
                        last_failure.payload.get("code")
                        if last_failure is not None
                        else None
                    )
                    attempts = job.attempts if job is not None else 0
                    max_attempts = job.max_attempts if job is not None else 0
                if (
                    job_status == "running"
                    and final_status == "failed"
                    and failure_code in {"trueforge_unavailable"}
                    and attempts < max_attempts
                ):
                    with self._database.session_context() as session:
                        run = session.get(AgentRun, run_id)
                        job = session.get(Job, job_id)
                        if run is not None and job is not None:
                            run.status = "queued"
                            job.status = "retrying"
                            job.error = f"Transient failure: {failure_code}"
                            job.lease_until = None
                            session.commit()
                    await asyncio.sleep(
                        self._settings.retry_base_seconds * (2 ** max(0, attempts - 1))
                    )
                    await self.enqueue(run_id)
                    return
                if job_status not in {"cancelled", "completed", "failed"}:
                    successful = final_status in {"completed", "completed_with_errors"}
                    update_job(
                        self._database,
                        job_id,
                        "completed" if successful else "failed",
                        error=None if successful else "Run did not complete.",
                    )
                    if (
                        not successful
                        and job_kind == "canvas_build"
                        and final_status not in {
                            "failed",
                            "cancelled",
                            "completed",
                            "completed_with_errors",
                        }
                    ):
                        await self._fail_run(
                            run_id,
                            "run_did_not_complete",
                            "The canvas build ended without a terminal run result.",
                        )

    async def _execute(self, run_id: str) -> None:
        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status == "cancelled":
                return
            canvas = session.get(Canvas, run.canvas_id)
            if canvas is None:
                return
            research_goal = canvas.research_goal
            stored_brief = canvas.research_brief
            stored_brief_output = _stored_research_brief_output(stored_brief)
            job = session.scalar(
                select(Job)
                .where(Job.run_id == run_id, Job.kind != "paper_review")
                .order_by(Job.created_at.desc())
            )
            job_kind = job.kind if job is not None else "canvas_build"
            run.status = "running"
            if job_kind == "canvas_build":
                canvas.build_status = "running"
            session.commit()

        if job_kind == "paper_link":
            await self._execute_link(run_id, canvas.id, research_goal)
            return
        if job_kind == "direct_agent":
            await self._execute_direct(run_id, canvas.id)
            return

        await self._append_event(run_id, "run.started", {"agent": "main"})

        try:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value().strip():
                raise RunConfigurationError(
                    "openai_not_configured",
                    "OPENAI_API_KEY is not configured.",
                )

            main_completion_payload: dict[str, Any] = {}
            brief_output = stored_brief_output
            raw_invalid_output = (
                json.dumps(stored_brief, ensure_ascii=False, default=str)
                if stored_brief is not None and brief_output is None
                else None
            )
            validation_error = (
                "The stored research brief does not contain 2–8 valid canonical seed titles."
                if raw_invalid_output is not None
                else None
            )
            if brief_output is None and raw_invalid_output is None:
                raw_output, main_completion_payload = await self._collect_main_turn(
                    run_id, research_goal
                )
                try:
                    brief_output = _parse_research_brief_output(raw_output)
                except AutonomousPipelineError as error:
                    raw_invalid_output = raw_output
                    validation_error = str(error)

            if brief_output is None:
                await self._append_event(
                    run_id,
                    "tool.completed",
                    {
                        "agent": "main",
                        "name": "validate_research_brief",
                        "status": "repairing",
                        "code": "invalid_research_brief",
                        "message": validation_error,
                    },
                )
                repair_request = _main_brief_repair_request(
                    research_goal,
                    raw_invalid_output or "",
                    validation_error or "The research brief is invalid.",
                )
                repaired_output, main_completion_payload = await self._collect_main_turn(
                    run_id, repair_request
                )
                try:
                    brief_output = _parse_research_brief_output(repaired_output)
                except AutonomousPipelineError as error:
                    raise AutonomousPipelineError(
                        "invalid_research_brief",
                        f"Main failed to produce a valid structured research brief after one repair: {error}",
                    ) from error

            research_brief = brief_output.brief_markdown
            canonical_seed_titles = brief_output.canonical_seed_titles

            from research_map_backend.research_store import store_research_brief

            if stored_brief_output is None:
                store_research_brief(
                    self._database,
                    canvas.id,
                    {
                        "text": research_brief,
                        "canonical_seed_titles": canonical_seed_titles,
                    },
                )
                await self._append_event(
                    run_id,
                    "tool.completed",
                    {"agent": "main", "name": "store_research_brief"},
                )
            if self._pipeline_runner is None:
                await self._finish_run(
                    run_id,
                    "completed",
                    "run.completed",
                    {"trueforge_status": "done", **main_completion_payload},
                )
                return
            summary = await self._pipeline_runner(
                database=self._database,
                settings=self._settings,
                canvas_id=canvas.id,
                run_id=run_id,
                research_goal=research_goal,
                research_brief=research_brief,
                canonical_seed_titles=canonical_seed_titles,
                emit=lambda event_type, payload: self._append_event(
                    run_id, event_type, payload
                ),
                reviewer_semaphore=self._reviewer_semaphore,
            )
            terminal_status = summary.get("status", "completed")
            if terminal_status not in {"completed", "completed_with_errors"}:
                terminal_status = "completed"
            await self._finish_run(
                run_id,
                terminal_status,
                "run.completed",
                {"trueforge_status": "done", **main_completion_payload, **summary},
            )
        except RunConfigurationError as error:
            await self._fail_run(run_id, error.code, str(error))
        except AutonomousPipelineError as error:
            await self._fail_run(run_id, error.code, str(error))
        except TrueForgeError as error:
            await self._fail_run(
                run_id,
                "trueforge_unavailable",
                "TrueForge could not run the main agent.",
                detail=str(error),
            )
        except Exception:
            await self._fail_run(
                run_id,
                "run_execution_failed",
                "The main agent run failed unexpectedly.",
            )

    async def _collect_main_turn(
        self, run_id: str, request: str
    ) -> tuple[str, dict[str, Any]]:
        completed = False
        completion_payload: dict[str, Any] = {}
        parts: list[str] = []
        final_content: str | None = None
        async for source_event in self._event_source(self._settings, request):
            source_type = source_event.get("type")
            if source_type == "session.created":
                await self._store_trueforge_ids(
                    run_id, session_id=source_event.get("session_id")
                )
                continue
            if source_type == "turn.created":
                await self._store_trueforge_ids(
                    run_id, turn_id=source_event.get("turn_id")
                )
            if source_type == "model.message.delta":
                content = source_event.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
            elif source_type == "model.message":
                content = source_event.get("content")
                if isinstance(content, str) and content:
                    final_content = content

            for event_type, payload in normalize_trueforge_event(source_event):
                if event_type == "run.completed":
                    completed = True
                    completion_payload = payload
                elif event_type == "run.failed":
                    raise TrueForgeError(
                        payload.get("message") or "TrueForge main turn failed."
                    )
                else:
                    await self._append_event(run_id, event_type, payload)
        if not completed:
            raise TrueForgeError("TrueForge turn stream ended before turn.done")
        return (final_content or "".join(parts)).strip(), completion_payload

    async def _execute_direct(self, run_id: str, canvas_id: str) -> None:
        from research_map_backend.research_store import get_pipeline_papers

        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            canvas = session.get(Canvas, canvas_id)
            job = session.scalar(
                select(Job).where(Job.run_id == run_id, Job.kind == "direct_agent")
            )
            if run is None or canvas is None or job is None:
                await self._fail_run(
                    run_id,
                    "direct_agent_job_missing",
                    "The direct-agent job or canvas could not be found.",
                    update_canvas=False,
                )
                return
            agent_name = run.agent_name
            direct_paper_id = job.paper_id
            prompt = job.payload.get("prompt")
            raw_paper_ids = job.payload.get("paper_ids", [])
            paper_ids = [item for item in raw_paper_ids if isinstance(item, str)] if isinstance(raw_paper_ids, list) else []
            canvas_context = {
                "canvas": {
                    "id": canvas.id,
                    "name": canvas.name,
                    "research_goal": canvas.research_goal,
                    "research_brief": canvas.research_brief,
                },
                "papers": get_pipeline_papers(self._database, canvas_id, paper_ids or None),
            }
        if not isinstance(prompt, str) or not prompt.strip():
            await self._fail_run(
                run_id,
                "invalid_direct_agent_prompt",
                "The direct-agent prompt is empty.",
                update_canvas=False,
            )
            return
        scoped_prompt = (
            f"{prompt.strip()}\n\n"
            "This is a scoped debugging turn. Use the preloaded application MCP tools "
            "for all reads and writes. Do not ask for human approval. Do not bypass "
            "canvas membership or evidence validation.\n\n"
            f"Canvas context:\n{canvas_context}"
        )
        await self._append_event(run_id, "run.started", {"agent": agent_name})
        try:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value().strip():
                raise RunConfigurationError(
                    "openai_not_configured", "OPENAI_API_KEY is not configured."
                )
            completed = False
            completion_payload: dict[str, Any] = {}
            async def consume_direct_turn() -> None:
                nonlocal completed, completion_payload
                async for source_event in self._direct_agent_source(
                    self._settings, agent_name, scoped_prompt
                ):
                    source_type = source_event.get("type")
                    if source_type == "session.created":
                        await self._store_trueforge_ids(
                            run_id, session_id=source_event.get("session_id")
                        )
                        continue
                    if source_type == "turn.created":
                        await self._store_trueforge_ids(
                            run_id, turn_id=source_event.get("turn_id")
                        )
                    for event_type, payload in normalize_trueforge_event(source_event):
                        if event_type == "run.completed":
                            completed = True
                            completion_payload = payload
                        elif event_type == "run.failed":
                            await self._finish_run(
                                run_id, "failed", event_type, payload, update_canvas=False
                            )
                            return
                        else:
                            await self._append_event(run_id, event_type, payload)

            if agent_name == "research-map-reviewer":
                async with reviewer_slot(
                    self._reviewer_semaphore,
                    lambda event_type, payload: self._append_event(
                        run_id, event_type, payload
                    ),
                    canvas_id=canvas_id,
                    run_id=run_id,
                    paper_id=direct_paper_id,
                    purpose="direct_reviewer",
                ):
                    await consume_direct_turn()
            else:
                await consume_direct_turn()
            if not completed:
                raise TrueForgeError("TrueForge turn stream ended before turn.done")
            await self._finish_run(
                run_id,
                "completed",
                "run.completed",
                {"trueforge_status": "done", **completion_payload, "agent": agent_name},
                update_canvas=False,
            )
        except RunConfigurationError as error:
            await self._fail_run(
                run_id, error.code, str(error), update_canvas=False
            )
        except TrueForgeError as error:
            await self._fail_run(
                run_id,
                "trueforge_unavailable",
                f"TrueForge could not run {agent_name}.",
                detail=str(error),
                update_canvas=False,
            )
        except Exception:
            await self._fail_run(
                run_id,
                "direct_agent_execution_failed",
                f"The {agent_name} debugging turn failed unexpectedly.",
                update_canvas=False,
            )

    async def _execute_link(
        self, run_id: str, canvas_id: str, research_goal: str
    ) -> None:
        await self._append_event(
            run_id, "run.started", {"agent": "research-map-link-ingestion"}
        )
        with self._database.session_context() as session:
            job = session.scalar(
                select(Job).where(Job.run_id == run_id, Job.kind == "paper_link")
            )
            if job is None:
                await self._fail_run(
                    run_id,
                    "paper_link_job_missing",
                    "The queued paper-link job could not be found.",
                    update_canvas=False,
                )
                return
            job_id = job.id
            placeholder_paper_id = job.paper_id
            paper_url = job.payload.get("url")
            canvas = session.get(Canvas, canvas_id)
            research_brief_value = canvas.research_brief if canvas is not None else None
        from research_map_backend.research_store import update_job

        if not isinstance(paper_url, str) or not paper_url.strip():
            update_job(self._database, job_id, "failed", error="Paper link is missing.")
            await self._fail_run(
                run_id,
                "invalid_paper_link_job",
                "The queued paper-link job has no link.",
                update_canvas=False,
            )
            return
        update_job(self._database, job_id, "running")
        brief = ""
        if isinstance(research_brief_value, dict):
            value = research_brief_value.get("text")
            brief = value if isinstance(value, str) else ""
        try:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value().strip():
                raise RunConfigurationError(
                    "openai_not_configured", "OPENAI_API_KEY is not configured."
                )
            summary = await self._link_pipeline_runner(
                database=self._database,
                settings=self._settings,
                canvas_id=canvas_id,
                run_id=run_id,
                paper_url=paper_url,
                research_goal=research_goal,
                research_brief=brief,
                placeholder_paper_id=placeholder_paper_id,
                replace_protected_review=bool(
                    job.payload.get("replace_protected_review")
                ),
                reviewer_semaphore=self._reviewer_semaphore,
                emit=lambda event_type, payload: self._append_event(
                    run_id, event_type, payload
                ),
            )
            update_job(self._database, job_id, "completed")
            await self._finish_run(
                run_id,
                "completed",
                "run.completed",
                summary,
                update_canvas=False,
            )
        except (PaperLinkError, AutonomousPipelineError, RunConfigurationError) as error:
            code = getattr(error, "code", "paper_link_processing_failed")
            message = str(error)
            with self._database.session_context() as session:
                current_job = session.get(Job, job_id)
                failed_paper_id = current_job.paper_id if current_job is not None else None
            if failed_paper_id is not None:
                from research_map_backend.research_store import mark_paper_failed

                mark_paper_failed(
                    self._database, canvas_id, failed_paper_id, message
                )
            update_job(self._database, job_id, "failed", error=message)
            await self._fail_run(run_id, code, message, update_canvas=False)
        except Exception as error:
            logger.exception(
                "unexpected paper link processing failure",
                extra=context(canvas_id=canvas_id, run_id=run_id, job_id=job_id),
            )
            message = "The submitted paper could not be processed."
            with self._database.session_context() as session:
                current_job = session.get(Job, job_id)
                failed_paper_id = current_job.paper_id if current_job is not None else None
            if failed_paper_id is not None:
                from research_map_backend.research_store import mark_paper_failed

                mark_paper_failed(
                    self._database, canvas_id, failed_paper_id, message
                )
            update_job(self._database, job_id, "failed", error=message)
            await self._fail_run(
                run_id,
                "paper_link_processing_failed",
                message,
                update_canvas=False,
            )

    async def _store_trueforge_ids(
        self,
        run_id: str,
        *,
        session_id: object = None,
        turn_id: object = None,
    ) -> None:
        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            if isinstance(session_id, str):
                run.trueforge_session_id = session_id
            if isinstance(turn_id, str):
                run.trueforge_turn_id = turn_id
            session.commit()
            values = context(
                canvas_id=run.canvas_id,
                run_id=run.id,
                trueforge_session_id=run.trueforge_session_id,
                trueforge_turn_id=run.trueforge_turn_id,
            )
        logger.info("TrueForge identifiers stored", extra=values)

    async def _append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=run.canvas_id,
                    type=event_type,
                    payload=public_diagnostic_value(payload),
                )
            )
            session.commit()
            log_values = context(
                canvas_id=run.canvas_id,
                run_id=run.id,
                trueforge_session_id=run.trueforge_session_id,
                trueforge_turn_id=run.trueforge_turn_id,
                event_type=event_type,
                paper_id=payload.get("paper_id"),
            )
        logger.info("run event stored", extra=log_values)
        if self._canvas_events is not None and event_type in {
            "paper.added",
            "review.completed",
            "relationship.added",
            "run.started",
            "run.completed",
            "run.failed",
            "run.cancelled",
        }:
            await self._canvas_events.publish(run.canvas_id, event_type)
        await self._notify(run_id)

    async def _finish_run(
        self,
        run_id: str,
        status: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        update_canvas: bool = True,
    ) -> None:
        from research_map_backend.research_store import (
            clear_pending_discovery_submissions_from_job,
        )

        with self._database.session_context() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            canvas = session.get(Canvas, run.canvas_id)
            now = datetime.now(UTC)
            if run.created_at.tzinfo is None:
                now = now.replace(tzinfo=None)
            duration_ms = max(
                0, int((now - run.created_at).total_seconds() * 1000)
            )
            terminal_payload = public_diagnostic_value(
                {**payload, "duration_ms": duration_ms}
            )
            run.status = status
            run.error = (
                public_diagnostic_value(payload.get("message"))
                if status == "failed" and isinstance(payload.get("message"), str)
                else None
            )
            if canvas is not None and update_canvas:
                canvas.build_status = status
            canvas_build = session.scalar(
                select(Job).where(
                    Job.run_id == run_id,
                    Job.kind == "canvas_build",
                )
            )
            if canvas_build is not None:
                clear_pending_discovery_submissions_from_job(canvas_build)
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=run.canvas_id,
                    type=event_type,
                    payload=terminal_payload,
                )
            )
            session.commit()
            log_values = context(
                canvas_id=run.canvas_id,
                run_id=run.id,
                trueforge_session_id=run.trueforge_session_id,
                trueforge_turn_id=run.trueforge_turn_id,
                event_type=event_type,
            )
        logger.info("run finished", extra=log_values)
        if self._canvas_events is not None:
            await self._canvas_events.publish(run.canvas_id, event_type)
        await self._notify(run_id)

    async def _fail_run(
        self,
        run_id: str,
        code: str,
        message: str,
        *,
        detail: str | None = None,
        update_canvas: bool = True,
    ) -> None:
        payload: dict[str, Any] = {"code": code, "message": message}
        if detail:
            payload["detail"] = detail
        await self._finish_run(
            run_id,
            "failed",
            "run.failed",
            payload,
            update_canvas=update_canvas,
        )


class RunConfigurationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canvas_build_stop_reason(canvas: Canvas) -> str | None:
    brief = canvas.research_brief
    if not isinstance(brief, dict):
        return None
    summary = brief.get("pipeline_summary")
    if not isinstance(summary, dict):
        return None
    value = summary.get("stop_reason")
    return value if isinstance(value, str) else None


def _completed_canvas_build_retryable(canvas: Canvas) -> bool:
    reason = _canvas_build_stop_reason(canvas)
    if reason in COMPLETED_CANVAS_BUILD_RETRY_STOPS:
        return True
    if reason != "academic_search_unavailable":
        return False
    summary = canvas.research_brief.get("pipeline_summary", {})
    accepted = summary.get("accepted_papers")
    reviewed = summary.get("completed_reviews")
    return (
        isinstance(accepted, int)
        and not isinstance(accepted, bool)
        and accepted > 0
        and isinstance(reviewed, int)
        and not isinstance(reviewed, bool)
        and reviewed > 0
    )


def _prepare_canvas_build_retry(canvas: Canvas) -> None:
    """Resume only an explicitly retried terminal checkpoint.

    Startup recovery never calls this helper. Fully started batches and their durable
    papers, reviews, and relationships remain intact; a non-started terminal search
    marker is removed so the next batch can perform real work.
    """
    if _canvas_build_stop_reason(canvas) not in NORMALIZABLE_CANVAS_BUILD_RETRY_STOPS:
        return
    brief = dict(canvas.research_brief or {})
    previous = brief.get("pipeline_summary")
    if not isinstance(previous, dict):
        return
    summary = dict(previous)
    coverage = [
        dict(item)
        for item in summary.get("coverage", [])
        if isinstance(item, dict) and item.get("started") is True
    ]
    failures = [
        dict(item)
        for item in summary.get("paper_failures", [])
        if isinstance(item, dict)
        and isinstance(item.get("paper_id"), str)
        and bool(item["paper_id"])
    ]
    summary.update(
        {
            "status": "queued",
            "stop_reason": "in_progress",
            "coverage": coverage,
            "paper_failures": failures,
            "batches_started": len(coverage),
        }
    )
    brief["pipeline_summary"] = summary
    canvas.research_brief = brief


def _parse_research_brief_output(content: str) -> ResearchBriefOutput:
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        last_fence = stripped.rfind("```")
        if first_newline >= 0 and last_fence > first_newline:
            stripped = stripped[first_newline + 1:last_fence].strip()
    try:
        return ResearchBriefOutput.model_validate_json(stripped)
    except Exception as error:
        raise AutonomousPipelineError(
            "invalid_research_brief",
            f"Main returned an invalid structured research brief: {error}",
        ) from error


def _stored_research_brief_output(value: object) -> ResearchBriefOutput | None:
    if not isinstance(value, dict):
        return None
    try:
        return ResearchBriefOutput.model_validate(
            {
                "brief_markdown": value.get("text"),
                "canonical_seed_titles": value.get("canonical_seed_titles"),
            }
        )
    except Exception:
        return None


def _main_brief_repair_request(
    research_goal: str, invalid_output: str, validation_error: str
) -> str:
    return (
        "Repair the prior Main research-brief output autonomously. Return JSON only "
        "with exactly this schema: "
        '{"brief_markdown":"concise markdown brief",'
        '"canonical_seed_titles":["Exact full paper title",'
        '"Another exact full paper title"]}. '
        "canonical_seed_titles must contain 2–8 unique, plausible full titles of "
        "canonical anchor papers, not topics, headings, authors, or commentary. Do not "
        "ask the user or use markdown fences.\n\n"
        f"Research goal:\n{research_goal}\n\n"
        f"Validation error:\n{validation_error}\n\n"
        f"Invalid output:\n{invalid_output[:12_000]}"
    )


def normalize_trueforge_event(
    event: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    event_type = event.get("type")
    thread_id = event.get("thread_id")

    if event_type == "transport.error":
        return [
            (
                "agent.transport.retry",
                {
                    key: value
                    for key, value in event.items()
                    if key not in {"type", "content"}
                },
            )
        ]

    if event_type == "turn.created":
        return [
            (
                "agent.thread.started",
                {"thread_id": "main", "turn_id": event.get("turn_id")},
            )
        ]
    if event_type == "thread.created":
        return [
            (
                "agent.thread.started",
                {"thread_id": thread_id, "title": event.get("title")},
            )
        ]
    if event_type == "model.message.delta":
        content = event.get("content")
        if not isinstance(content, str) or not content:
            return []
        return [
            (
                "agent.message.delta",
                {"thread_id": thread_id, "content": content},
            )
        ]
    if event_type == "model.message":
        normalized: list[tuple[str, dict[str, Any]]] = []
        tool_calls = event.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                normalized.append(
                    (
                        "tool.started",
                        {
                            "thread_id": thread_id,
                            "tool_call_id": tool_call.get("id"),
                            "name": function.get("name")
                            if isinstance(function, dict)
                            else None,
                        },
                    )
                )
        return normalized
    if event_type == "tool.response":
        return [
            (
                "tool.completed",
                {
                    "thread_id": thread_id,
                    "tool_call_id": event.get("tool_call_id"),
                    "content": event.get("content"),
                },
            )
        ]
    if event_type == "turn.done":
        state = event.get("state")
        if not isinstance(state, dict):
            return [
                (
                    "run.failed",
                    {"code": "invalid_turn_state", "message": "TrueForge returned an invalid turn state."},
                )
            ]
        status = state.get("status")
        if status == "done" and not state.get("required_actions"):
            payload: dict[str, Any] = {"trueforge_status": status}
            output = state.get("output")
            output_usage = output.get("usage") if isinstance(output, dict) else None
            usage = _public_usage(
                state.get("metrics")
                or state.get("usage")
                or output_usage
                or event.get("usage")
            )
            if usage:
                payload["usage"] = usage
            return [("run.completed", payload)]
        message = state.get("message")
        if not isinstance(message, str):
            message = "TrueForge did not complete the main agent turn."
        return [
            (
                "run.failed",
                {"code": f"trueforge_turn_{status or 'invalid'}", "message": message},
            )
        ]
    return []


def _public_usage(value: object) -> dict[str, int | float]:
    """Keep provider accounting only; never persist hidden reasoning content."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, int | float] = {}
    aliases = {
        "total_input_tokens": "input_tokens",
        "total_output_tokens": "output_tokens",
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "total_cost_in_usd": "cost",
        "cost": "cost",
    }
    for source, target in aliases.items():
        item = value.get(source)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[target] = item
    return result
