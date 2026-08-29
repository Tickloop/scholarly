from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from research_map_backend.api.routes import router
from research_map_backend.chat import ChatCoordinator
from research_map_backend.canvas_events import CanvasEventBroadcaster
from research_map_backend.db import Database
from research_map_backend.migrations import upgrade_database
from research_map_backend.mcp_server import create_research_mcp
from research_map_backend.runs import (
    AgentEventSource,
    DirectAgentSource,
    LinkPipelineRunner,
    PipelineRunner,
    RunCoordinator,
)
from research_map_backend.settings import Settings, get_settings
from research_map_backend.structured_logging import configure_structured_logging


def create_app(
    settings: Settings | None = None,
    *,
    agent_event_source: AgentEventSource | None = None,
    direct_agent_source: DirectAgentSource | None = None,
    pipeline_runner: PipelineRunner | None = None,
    link_pipeline_runner: LinkPipelineRunner | None = None,
) -> FastAPI:
    configure_structured_logging()
    app_settings = settings or get_settings()
    services: dict[str, object] = {}
    mcp_server = create_research_mcp(
        lambda: services["database"],  # type: ignore[return-value]
        lambda run_id, event_type, payload: services["run_coordinator"].record_progress(  # type: ignore[attr-defined]
            run_id, event_type, payload
        ),
        lambda canvas_id, change_type: services["canvas_events"].publish(  # type: ignore[attr-defined]
            canvas_id, change_type
        ),
        lambda run_id: services["run_coordinator"].enqueue(run_id),  # type: ignore[attr-defined]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        upgrade_database(app_settings)
        database = Database(app_settings)
        database.create_schema()
        canvas_events = CanvasEventBroadcaster()
        run_coordinator = RunCoordinator(
            database,
            app_settings,
            canvas_events=canvas_events,
            **({"event_source": agent_event_source} if agent_event_source else {}),
            **(
                {"direct_agent_source": direct_agent_source}
                if direct_agent_source
                else {}
            ),
            **(
                {"pipeline_runner": pipeline_runner}
                if pipeline_runner is not None
                else ({"pipeline_runner": None} if agent_event_source else {})
            ),
            **(
                {"link_pipeline_runner": link_pipeline_runner}
                if link_pipeline_runner is not None
                else {}
            ),
        )
        app.state.database = database
        app.state.settings = app_settings
        app.state.run_coordinator = run_coordinator
        app.state.canvas_events = canvas_events
        chat_coordinator = ChatCoordinator(database, app_settings, run_coordinator)
        app.state.chat_coordinator = chat_coordinator
        services["database"] = database
        services["run_coordinator"] = run_coordinator
        services["canvas_events"] = canvas_events
        await run_coordinator.start()
        await chat_coordinator.start()
        async with mcp_server.session_manager.run():
            yield
        await chat_coordinator.stop()
        await run_coordinator.stop()
        database.dispose()
        services.clear()

    app = FastAPI(title="Research Map API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_ui_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    # Mount last so FastAPI routes win while MCP is available at exact /mcp.
    app.mount("/", mcp_server.streamable_http_app())
    return app


app = create_app()
