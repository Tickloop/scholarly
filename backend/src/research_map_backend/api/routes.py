import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx

from research_map_backend.db import Database
from research_map_backend.chat import list_chat_messages, submit_chat_message
from research_map_backend.diagnostics import (
    dependency_health,
    public_diagnostic_value,
    public_event_payload,
    summarize_usage,
)
from research_map_backend.layout_store import reset_canvas_layout as reset_layout_state
from research_map_backend.layout_store import update_canvas_layout
from research_map_backend.manual_controls import (
    delete_paper,
    delete_relationship,
    edit_paper,
    edit_relationship,
    edit_review,
    undo,
)
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    Canvas,
    CanvasPaper,
    Job,
    Paper,
    ToolInvocation,
)
from research_map_backend.paper_links import PaperLinkError, create_canvas_paper_placeholder
from research_map_backend.research_store import (
    create_job,
    get_canvas_snapshot,
    queue_canvas_build,
)
from research_map_backend.runs import RunCoordinator, TERMINAL_RUN_STATUSES
from research_map_backend.schemas import (
    AgentRunEventRead,
    AgentRunDetail,
    AgentRunRead,
    CanvasLayoutUpdate,
    CanvasCreate,
    CanvasSnapshot,
    CanvasSummary,
    CanvasUpdate,
    ChatMessageCreate,
    ChatMessageRead,
    ChatSubmission,
    DirectAgentCreate,
    DependencyHealthRead,
    HealthRead,
    PaperLinkCreate,
    PaperUpdate,
    RelationshipEdit,
    ReviewEdit,
    RunTimelineRead,
    ToolInvocationRead,
    UndoRead,
    UndoRequest,
)
from research_map_backend.settings import Settings

router = APIRouter(prefix="/api/v1")


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_session(
    database: Annotated[Database, Depends(get_database)],
):
    yield from database.session()


SessionDependency = Annotated[Session, Depends(get_session)]


async def _publish_canvas_change(
    request: Request, canvas_id: str, change_type: str
) -> None:
    await request.app.state.canvas_events.publish(canvas_id, change_type)


@router.get("/health", response_model=HealthRead)
async def health(request: Request) -> HealthRead:
    database: Database = request.app.state.database
    settings: Settings = request.app.state.settings
    database.check()
    openai_configured = bool(
        settings.openai_api_key
        and settings.openai_api_key.get_secret_value().strip()
    )
    brightdata_api_key_configured = bool(
        settings.brightdata_api_key
        and settings.brightdata_api_key.get_secret_value().strip()
    )
    brightdata_mcp_configured = brightdata_api_key_configured
    trueforge_status = "unavailable"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.trueforge_url.rstrip('/')}/api/v1/agents")
            response.raise_for_status()
        trueforge_status = "ok"
    except httpx.HTTPError:
        pass
    if not openai_configured:
        configuration = "missing_openai_api_key"
    elif not brightdata_mcp_configured:
        configuration = "missing_brightdata_api_key"
    else:
        configuration = "ok"
    return HealthRead(
        status=(
            "ok"
            if trueforge_status == "ok" and configuration == "ok"
            else "degraded"
        ),
        api="ok",
        database="ok",
        trueforge=trueforge_status,
        trueforge_url=settings.trueforge_url,
        openai_configured=openai_configured,
        brightdata_api_key_configured=brightdata_api_key_configured,
        brightdata_mcp_configured=brightdata_mcp_configured,
        required_configuration=configuration,
        research_providers={
            "academic_metadata_apis": "enabled",
            "brightdata_mcp": (
                "configured_active"
                if brightdata_mcp_configured
                else "missing_api_key"
            ),
            "web_search": "brightdata_mcp_only",
            "web_scrape": "brightdata_mcp_only",
            "model_inference": "openai_via_trueforge",
        },
    )


@router.get("/health/dependencies", response_model=DependencyHealthRead)
async def health_dependencies(request: Request) -> DependencyHealthRead:
    return DependencyHealthRead.model_validate(
        await dependency_health(request.app.state.settings)
    )


@router.post(
    "/canvases", response_model=CanvasSnapshot, status_code=status.HTTP_201_CREATED
)
async def create_canvas(
    payload: CanvasCreate, request: Request, session: SessionDependency
) -> CanvasSnapshot:
    canvas = Canvas(name=payload.name, research_goal=payload.research_goal)
    session.add(canvas)
    session.commit()
    session.refresh(canvas)
    await _publish_canvas_change(request, canvas.id, "canvas.created")
    return get_canvas_snapshot(session, canvas.id)


@router.get("/canvases", response_model=list[CanvasSummary])
def list_canvases(session: SessionDependency) -> list[Canvas]:
    return list(
        session.scalars(
            select(Canvas)
            .where(Canvas.deleted_at.is_(None))
            .order_by(Canvas.created_at.desc())
        )
    )


@router.patch("/canvases/{canvas_id}", response_model=CanvasSummary)
async def rename_canvas(
    canvas_id: str, payload: CanvasUpdate, request: Request, session: SessionDependency
) -> Canvas:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    canvas.name = payload.name
    session.commit()
    session.refresh(canvas)
    await _publish_canvas_change(request, canvas.id, "canvas.updated")
    return canvas


@router.delete("/canvases/{canvas_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canvas(
    canvas_id: str, request: Request, session: SessionDependency
) -> Response:
    from datetime import UTC, datetime

    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    canvas.deleted_at = datetime.now(UTC)
    session.commit()
    await _publish_canvas_change(request, canvas.id, "canvas.deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/canvases/events")
async def stream_canvas_events(request: Request) -> StreamingResponse:
    return StreamingResponse(
        request.app.state.canvas_events.stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/canvases/{canvas_id}", response_model=CanvasSnapshot)
def get_canvas(canvas_id: str, session: SessionDependency) -> CanvasSnapshot:
    return get_canvas_snapshot(session, canvas_id)


@router.post("/canvases/{canvas_id}/layout/reset", response_model=CanvasSnapshot)
async def reset_canvas_layout(
    canvas_id: str, request: Request, session: SessionDependency
) -> CanvasSnapshot:
    _manual_call(reset_layout_state, request.app.state.database, canvas_id)
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "layout.reset")
    return get_canvas_snapshot(session, canvas_id)


@router.put("/canvases/{canvas_id}/layout", response_model=CanvasSnapshot)
async def persist_canvas_layout(
    canvas_id: str,
    payload: CanvasLayoutUpdate,
    request: Request,
    session: SessionDependency,
) -> CanvasSnapshot:
    _manual_call(update_canvas_layout, request.app.state.database, canvas_id, payload)
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "layout.updated")
    return get_canvas_snapshot(session, canvas_id)


@router.post(
    "/canvases/{canvas_id}/builds",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_canvas_build(
    canvas_id: str, request: Request, session: SessionDependency
) -> AgentRunRead:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")

    run = queue_canvas_build(request.app.state.database, canvas.id)
    session.expire_all()

    coordinator: RunCoordinator = request.app.state.run_coordinator
    await coordinator.enqueue(run.id)
    await _publish_canvas_change(request, canvas.id, "run.queued")
    return AgentRunRead.model_validate(run)


@router.post(
    "/canvases/{canvas_id}/agents/runs",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_direct_agent_run(
    canvas_id: str,
    payload: DirectAgentCreate,
    request: Request,
    session: SessionDependency,
) -> AgentRunRead:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    unique_paper_ids = list(dict.fromkeys(payload.paper_ids))
    required_count = {"reviewer": 1, "connection": 2}.get(payload.agent)
    if required_count is not None and len(unique_paper_ids) != required_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The {payload.agent} command requires exactly {required_count} canvas paper(s)",
        )
    if payload.agent in {"main", "discovery"} and unique_paper_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The {payload.agent} command does not accept paper scope",
        )
    if unique_paper_ids:
        scoped_ids = set(
            session.scalars(
                select(CanvasPaper.paper_id).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.paper_id.in_(unique_paper_ids),
                    CanvasPaper.deleted_at.is_(None),
                )
            )
        )
        if scoped_ids != set(unique_paper_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Every direct-agent paper must belong to the active canvas",
            )
    agent_name = {
        "main": "research-map-main",
        "discovery": "research-map-discovery",
        "reviewer": "research-map-reviewer",
        "connection": "research-map-connection",
    }[payload.agent]
    run = AgentRun(canvas_id=canvas_id, agent_name=agent_name, status="queued")
    session.add(run)
    session.commit()
    session.refresh(run)
    create_job(
        request.app.state.database,
        canvas_id,
        run.id,
        "direct_agent",
        {"prompt": payload.prompt, "paper_ids": unique_paper_ids},
        paper_id=unique_paper_ids[0] if len(unique_paper_ids) == 1 else None,
    )
    await request.app.state.run_coordinator.enqueue(run.id)
    await _publish_canvas_change(request, canvas_id, "run.queued")
    return AgentRunRead.model_validate(run)


@router.post(
    "/canvases/{canvas_id}/papers/from-link",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_paper_link(
    canvas_id: str,
    payload: PaperLinkCreate,
    request: Request,
    session: SessionDependency,
) -> AgentRunRead:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    paper_url = payload.url.strip()
    if not paper_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Paper link cannot be empty",
        )
    try:
        paper_id = create_canvas_paper_placeholder(
            request.app.state.database,
            canvas.id,
            paper_url,
            x=payload.x,
            y=payload.y,
        )
    except PaperLinkError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    run = AgentRun(
        canvas_id=canvas.id,
        agent_name="research-map-link-ingestion",
        status="queued",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    create_job(
        request.app.state.database,
        canvas.id,
        run.id,
        "paper_link",
        {"url": paper_url},
        paper_id=paper_id,
    )
    coordinator: RunCoordinator = request.app.state.run_coordinator
    await coordinator.enqueue(run.id)
    await _publish_canvas_change(request, canvas.id, "paper.queued")
    return AgentRunRead.model_validate(run).model_copy(update={"paper_id": paper_id})


@router.post(
    "/canvases/{canvas_id}/papers/{paper_id}/retry",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_paper(
    canvas_id: str, paper_id: str, request: Request, session: SessionDependency
) -> AgentRunRead:
    return await _enqueue_existing_paper(
        canvas_id, paper_id, request, session, replace_protected_review=False
    )


@router.post(
    "/canvases/{canvas_id}/papers/{paper_id}/review/regenerate",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_review(
    canvas_id: str, paper_id: str, request: Request, session: SessionDependency
) -> AgentRunRead:
    return await _enqueue_existing_paper(
        canvas_id, paper_id, request, session, replace_protected_review=True
    )


@router.patch("/canvases/{canvas_id}/papers/{paper_id}", response_model=CanvasSnapshot)
async def update_paper(
    canvas_id: str,
    paper_id: str,
    payload: PaperUpdate,
    request: Request,
    session: SessionDependency,
) -> CanvasSnapshot:
    _manual_call(edit_paper, request.app.state.database, canvas_id, paper_id, payload)
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "paper.updated")
    return get_canvas_snapshot(session, canvas_id)


@router.patch(
    "/canvases/{canvas_id}/papers/{paper_id}/review", response_model=CanvasSnapshot
)
async def update_review(
    canvas_id: str,
    paper_id: str,
    payload: ReviewEdit,
    request: Request,
    session: SessionDependency,
) -> CanvasSnapshot:
    _manual_call(edit_review, request.app.state.database, canvas_id, paper_id, payload)
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "review.updated")
    return get_canvas_snapshot(session, canvas_id)


@router.delete(
    "/canvases/{canvas_id}/papers/{paper_id}", response_model=UndoRead
)
async def remove_paper(
    canvas_id: str, paper_id: str, request: Request
) -> UndoRead:
    token = _manual_call(delete_paper, request.app.state.database, canvas_id, paper_id)
    await _publish_canvas_change(request, canvas_id, "paper.deleted")
    return UndoRead(undo_token=token)


@router.patch(
    "/canvases/{canvas_id}/relationships/{relationship_id}",
    response_model=CanvasSnapshot,
)
async def update_relationship(
    canvas_id: str,
    relationship_id: str,
    payload: RelationshipEdit,
    request: Request,
    session: SessionDependency,
) -> CanvasSnapshot:
    _manual_call(
        edit_relationship,
        request.app.state.database,
        canvas_id,
        relationship_id,
        payload,
    )
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "relationship.updated")
    return get_canvas_snapshot(session, canvas_id)


@router.delete(
    "/canvases/{canvas_id}/relationships/{relationship_id}", response_model=UndoRead
)
async def remove_relationship(
    canvas_id: str, relationship_id: str, request: Request
) -> UndoRead:
    token = _manual_call(
        delete_relationship, request.app.state.database, canvas_id, relationship_id
    )
    await _publish_canvas_change(request, canvas_id, "relationship.deleted")
    return UndoRead(undo_token=token)


@router.post("/canvases/{canvas_id}/undo", response_model=CanvasSnapshot)
async def undo_change(
    canvas_id: str,
    payload: UndoRequest,
    request: Request,
    session: SessionDependency,
) -> CanvasSnapshot:
    _manual_call(undo, request.app.state.database, canvas_id, payload.undo_token)
    session.expire_all()
    await _publish_canvas_change(request, canvas_id, "canvas.undo")
    return get_canvas_snapshot(session, canvas_id)


@router.get("/canvases/{canvas_id}/messages", response_model=list[ChatMessageRead])
def get_messages(canvas_id: str, request: Request) -> list[ChatMessageRead]:
    rows = _manual_call(list_chat_messages, request.app.state.database, canvas_id)
    return [ChatMessageRead.model_validate(item) for item in rows]


@router.post("/canvases/{canvas_id}/messages", response_model=ChatSubmission)
async def post_message(
    canvas_id: str, payload: ChatMessageCreate, request: Request
) -> ChatSubmission:
    message, run, job, queued = _manual_call(
        submit_chat_message,
        request.app.state.database,
        canvas_id,
        payload.content,
        payload.paper_ids,
    )
    await request.app.state.chat_coordinator.enqueue(job.id)
    return ChatSubmission(
        message=ChatMessageRead.model_validate(message),
        run=AgentRunRead.model_validate(run).model_copy(update={"paper_id": job.paper_id}),
        queued=queued,
    )


async def _enqueue_existing_paper(
    canvas_id: str,
    paper_id: str,
    request: Request,
    session: Session,
    *,
    replace_protected_review: bool,
) -> AgentRunRead:
    membership = session.scalar(
        select(CanvasPaper).where(
            CanvasPaper.canvas_id == canvas_id, CanvasPaper.paper_id == paper_id
        )
    )
    paper = session.get(Paper, paper_id)
    if membership is None or paper is None or membership.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    membership.processing_status = "queued"
    membership.error = None
    run = AgentRun(
        canvas_id=canvas_id, agent_name="research-map-link-ingestion", status="queued"
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    create_job(
        request.app.state.database,
        canvas_id,
        run.id,
        "paper_link",
        {
            "url": paper.url,
            "replace_protected_review": replace_protected_review,
        },
        paper_id=paper_id,
    )
    await request.app.state.run_coordinator.enqueue(run.id)
    await _publish_canvas_change(request, canvas_id, "paper.queued")
    return AgentRunRead.model_validate(run).model_copy(update={"paper_id": paper_id})


def _manual_call(call, *args):
    try:
        return call(*args)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error


@router.get("/runs", response_model=list[AgentRunDetail])
def list_runs(
    session: SessionDependency,
    canvas_id: Annotated[str | None, Query()] = None,
) -> list[AgentRun]:
    query = select(AgentRun).order_by(AgentRun.created_at.desc())
    if canvas_id is not None:
        query = query.where(AgentRun.canvas_id == canvas_id)
    return list(session.scalars(query))


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
def inspect_run(run_id: str, session: SessionDependency) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/runs/{run_id}/event-log", response_model=list[AgentRunEventRead])
def replay_run_events(
    run_id: str,
    session: SessionDependency,
    after_id: Annotated[int, Query(ge=0)] = 0,
) -> list[AgentRunEventRead]:
    if session.get(AgentRun, run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    events = list(
        session.scalars(
            select(AgentRunEvent)
            .where(AgentRunEvent.run_id == run_id, AgentRunEvent.id > after_id)
            .order_by(AgentRunEvent.id)
        )
    )
    return [
        AgentRunEventRead(
            id=str(event.id),
            run_id=event.run_id,
            canvas_id=event.canvas_id,
            type=event.type,
            payload=public_event_payload(event.payload),
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    session: SessionDependency,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    try:
        after_id = int(last_event_id) if last_event_id else 0
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be an integer",
        ) from error
    if after_id < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must not be negative",
        )

    database: Database = request.app.state.database
    coordinator: RunCoordinator = request.app.state.run_coordinator

    async def events():
        cursor = after_id
        while True:
            if coordinator.stopping or await request.is_disconnected():
                break
            observed_version = coordinator.version(run_id)
            with database.session_context() as stream_session:
                stored_events = list(
                    stream_session.scalars(
                        select(AgentRunEvent)
                        .where(
                            AgentRunEvent.run_id == run_id,
                            AgentRunEvent.id > cursor,
                        )
                        .order_by(AgentRunEvent.id)
                    )
                )
                current_run = stream_session.get(AgentRun, run_id)
                is_terminal = (
                    current_run is None
                    or current_run.status in TERMINAL_RUN_STATUSES
                )

            for stored_event in stored_events:
                cursor = stored_event.id
                event = AgentRunEventRead(
                    id=str(stored_event.id),
                    run_id=stored_event.run_id,
                    canvas_id=stored_event.canvas_id,
                    type=stored_event.type,
                    payload=public_event_payload(stored_event.payload),
                    created_at=stored_event.created_at,
                )
                yield (
                    f"id: {stored_event.id}\n"
                    f"event: {stored_event.type}\n"
                    f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
                )

            if is_terminal and not stored_events:
                break
            if stored_events:
                continue
            try:
                await asyncio.wait_for(
                    coordinator.wait_for_change(run_id, observed_version),
                    timeout=15,
                )
            except TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/timeline", response_model=RunTimelineRead)
def get_run_timeline(run_id: str, session: SessionDependency) -> RunTimelineRead:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    events = list(
        session.scalars(
            select(AgentRunEvent)
            .where(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.id)
        )
    )
    invocations = list(
        session.scalars(
            select(ToolInvocation)
            .where(ToolInvocation.run_id == run_id)
            .order_by(ToolInvocation.started_at, ToolInvocation.id)
        )
    )
    duration_ms = None
    if run.status in TERMINAL_RUN_STATUSES:
        duration_ms = max(
            0, int((run.updated_at - run.created_at).total_seconds() * 1000)
        )
    return RunTimelineRead(
        run=AgentRunDetail.model_validate(run),
        events=[
            AgentRunEventRead(
                id=str(event.id),
                run_id=event.run_id,
                canvas_id=event.canvas_id,
                type=event.type,
                payload=public_event_payload(event.payload),
                created_at=event.created_at,
            )
            for event in events
        ],
        tool_invocations=[
            ToolInvocationRead(
                id=invocation.id,
                tool_name=invocation.tool_name,
                arguments=public_diagnostic_value(invocation.arguments),
                result=public_diagnostic_value(invocation.result),
                status=invocation.status,
                error=(
                    public_diagnostic_value(invocation.error)
                    if invocation.error is not None
                    else None
                ),
                started_at=invocation.started_at,
                completed_at=invocation.completed_at,
            )
            for invocation in invocations
        ],
        duration_ms=duration_ms,
        usage=summarize_usage(events),
    )


@router.post("/runs/{run_id}/cancel", response_model=AgentRunRead)
async def cancel_run(
    run_id: str, request: Request, session: SessionDependency
) -> AgentRunRead:
    coordinator: RunCoordinator = request.app.state.run_coordinator
    try:
        updated_id = await coordinator.cancel(run_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    session.expire_all()
    run = session.get(AgentRun, updated_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    job = session.scalar(
        select(Job).where(Job.run_id == updated_id).order_by(Job.created_at.desc())
    )
    return AgentRunRead.model_validate(run).model_copy(
        update={"paper_id": job.paper_id if job is not None else None}
    )


@router.post("/runs/{run_id}/retry", response_model=AgentRunRead)
async def retry_run(
    run_id: str, request: Request, session: SessionDependency
) -> AgentRunRead:
    coordinator: RunCoordinator = request.app.state.run_coordinator
    try:
        retried_id = await coordinator.retry(run_id)
    except ValueError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "does not exist" in str(error)
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(error)) from error
    session.expire_all()
    run = session.get(AgentRun, retried_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    job = session.scalar(select(Job).where(Job.run_id == retried_id))
    if job is not None and job.kind == "chat_message":
        await request.app.state.chat_coordinator.enqueue(job.id)
    return AgentRunRead.model_validate(run).model_copy(
        update={"paper_id": job.paper_id if job is not None else None}
    )
