from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from research_map_backend.db import Database
from research_map_backend.diagnostics import public_diagnostic_value
from research_map_backend.mcp_contracts import (
    CreateCanvasAndStartBuildRequest,
    CreateCanvasAndStartBuildResult,
    DiscoveryResolvedCandidate,
    DiscoveryScrapedPage,
    DiscoverySearchFinding,
    DiscoverySubmittedDecision,
    GetCanvasRequest,
    GetCanvasResult,
    GetPaperTextRequest,
    GetPaperTextResult,
    PaperMetadata,
    ProgressPayload,
    ProgressEventType,
    RecordDiscoveryDecisionRequest,
    RecordDiscoveryDecisionResult,
    RecordPaperSourceRequest,
    RecordPaperSourceResult,
    RecordProgressRequest,
    RecordProgressResult,
    RecordRelationshipRequest,
    RecordRelationshipResult,
    RecordReviewRequest,
    RecordReviewResult,
    ResolvePaperMetadataRequest,
    ResolvePaperMetadataResult,
    SearchPapersRequest,
    SearchPapersResult,
    SubmitDiscoveryBatchRequest,
    SubmitDiscoveryBatchResult,
    StrictToolModel,
    UpsertCanvasPaperRequest,
    UpsertCanvasPaperResult,
)
from research_map_backend.models import AgentRun, Canvas, ToolInvocation
from research_map_backend.paper_links import attach_canvas_paper, resolve_paper_link
from research_map_backend.research_search import (
    search_arxiv as search_arxiv_provider,
    search_semantic_scholar as search_semantic_scholar_provider,
)
from research_map_backend.research_store import (
    create_canvas_and_queue_build,
    get_pipeline_papers,
    record_discovery_batch,
    store_paper_source,
    store_relationship,
    store_review,
    submit_discovery_batch as store_discovery_submission,
)
from research_map_backend.schemas import (
    DiscoveryCandidateInput,
    PaperSourceInput,
    RelationshipInput,
    ReviewInput,
)
from research_map_backend.structured_logging import context

DatabaseGetter = Callable[[], Database]
ProgressRecorder = Callable[[str, str, dict[str, Any]], Any]
CanvasChangePublisher = Callable[[str, str], Any]
BuildStarter = Callable[[str], Any]
logger = logging.getLogger("research_map.tools")


def _json_value(value: Any) -> Any:
    """Convert public tool arguments/results to values safe for a JSON column."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.loads(json.dumps(value, default=str))
    return public_diagnostic_value(encoded)


def _start_invocation(
    database: Database,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    canvas_id: str | None = None,
    run_id: str | None = None,
) -> str:
    with database.session_context() as session:
        # Keep failed, out-of-scope calls observable without violating foreign keys.
        scoped_canvas_id = canvas_id if canvas_id and session.get(Canvas, canvas_id) else None
        scoped_run_id = run_id if run_id and session.get(AgentRun, run_id) else None
        invocation = ToolInvocation(
            canvas_id=scoped_canvas_id,
            run_id=scoped_run_id,
            tool_name=tool_name,
            arguments=_json_value(arguments),
        )
        session.add(invocation)
        session.commit()
        logger.info(
            "tool invocation started",
            extra=context(
                canvas_id=scoped_canvas_id,
                run_id=scoped_run_id,
                tool_name=tool_name,
            ),
        )
        return invocation.id


def _finish_invocation(
    database: Database,
    invocation_id: str,
    *,
    result: Any = None,
    error: Exception | None = None,
) -> None:
    with database.session_context() as session:
        invocation = session.get(ToolInvocation, invocation_id)
        if invocation is None:
            return
        invocation.status = "failed" if error else "completed"
        invocation.result = None if error else _json_value(result)
        invocation.error = (
            public_diagnostic_value(str(error)) if error else None
        )
        invocation.completed_at = datetime.now(UTC)
        session.commit()
        logger.log(
            logging.ERROR if error else logging.INFO,
            "tool invocation failed" if error else "tool invocation completed",
            extra=context(
                canvas_id=invocation.canvas_id,
                run_id=invocation.run_id,
                tool_name=invocation.tool_name,
            ),
        )


def _record_sync(
    database: Database,
    tool_name: str,
    arguments: dict[str, Any],
    operation: Callable[[], Any],
    *,
    canvas_id: str | None = None,
    run_id: str | None = None,
) -> Any:
    invocation_id = _start_invocation(
        database, tool_name, arguments, canvas_id=canvas_id, run_id=run_id
    )
    try:
        result = operation()
    except Exception as error:
        _finish_invocation(database, invocation_id, error=error)
        raise
    _finish_invocation(database, invocation_id, result=result)
    return result


async def _record_async(
    database: Database,
    tool_name: str,
    arguments: dict[str, Any],
    operation: Callable[[], Any],
    *,
    canvas_id: str | None = None,
    run_id: str | None = None,
) -> Any:
    invocation_id = _start_invocation(
        database, tool_name, arguments, canvas_id=canvas_id, run_id=run_id
    )
    try:
        result = await operation()
    except Exception as error:
        _finish_invocation(database, invocation_id, error=error)
        raise
    _finish_invocation(database, invocation_id, result=result)
    return result


def create_research_mcp(
    get_database: DatabaseGetter,
    progress_recorder: ProgressRecorder,
    canvas_change_publisher: CanvasChangePublisher | None = None,
    build_starter: BuildStarter | None = None,
) -> FastMCP:
    """Create the stateless Streamable HTTP server used by TrueForge agents."""
    server = FastMCP(
        "research-map",
        instructions="Tools for the autonomous research-map agents.",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
    )

    @server.tool()
    def get_canvas(canvas_id: str) -> GetCanvasResult:
        """Inspect papers, stored sources, and reviews for one canvas."""
        request = GetCanvasRequest(canvas_id=canvas_id)
        database = get_database()

        def operation() -> GetCanvasResult:
            with database.session_context() as session:
                canvas = session.get(Canvas, request.canvas_id)
                if canvas is None:
                    raise ValueError("Canvas does not exist")
                canvas_data = {
                    "id": canvas.id,
                    "name": canvas.name,
                    "research_goal": canvas.research_goal,
                    "research_brief": canvas.research_brief,
                    "build_status": canvas.build_status,
                }
            return GetCanvasResult.model_validate(
                {
                    "canvas": canvas_data,
                    "papers": get_pipeline_papers(database, request.canvas_id),
                }
            )

        return _record_sync(
            database, "get_canvas", request.model_dump(), operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    def get_paper_text(canvas_id: str, paper_id: str) -> GetPaperTextResult:
        """Get stored, page-aware source text for a paper on a canvas."""
        request = GetPaperTextRequest(canvas_id=canvas_id, paper_id=paper_id)
        database = get_database()

        def operation() -> GetPaperTextResult:
            rows = get_pipeline_papers(
                database, request.canvas_id, [request.paper_id]
            )
            if not rows:
                raise ValueError("Paper does not belong to the canvas")
            return GetPaperTextResult.model_validate(rows[0])

        return _record_sync(
            database,
            "get_paper_text",
            request.model_dump(),
            operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    async def resolve_paper_metadata(paper_url: str) -> ResolvePaperMetadataResult:
        """Resolve one DOI/arXiv/provider link through verified metadata APIs."""
        request = ResolvePaperMetadataRequest(paper_url=paper_url)
        database = get_database()
        return await _record_async(
            database,
            "resolve_paper_metadata",
            request.model_dump(),
            lambda: _resolve_metadata_result(request.paper_url),
        )

    async def publish_change(canvas_id: str, change_type: str) -> None:
        if canvas_change_publisher is None:
            return
        result = canvas_change_publisher(canvas_id, change_type)
        if hasattr(result, "__await__"):
            await result

    @server.tool()
    async def upsert_canvas_paper(
        canvas_id: str, candidate: PaperMetadata
    ) -> UpsertCanvasPaperResult:
        """Attach externally verified paper metadata as a queued user-origin paper."""
        request = UpsertCanvasPaperRequest(canvas_id=canvas_id, candidate=candidate)
        database = get_database()
        async def operation() -> UpsertCanvasPaperResult:
            result = UpsertCanvasPaperResult(
                paper_id=attach_canvas_paper(
                    database,
                    request.canvas_id,
                    request.candidate.model_dump(exclude_none=True),
                )
            )
            await publish_change(request.canvas_id, "paper.added")
            return result

        return await _record_async(
            database,
            "upsert_canvas_paper",
            request.model_dump(),
            operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    async def search_semantic_scholar(
        research_goal: str, research_brief: str = "", candidate_count: int = 10
    ) -> SearchPapersResult:
        """Search verified Semantic Scholar metadata for real academic papers."""
        request = SearchPapersRequest(
            research_goal=research_goal,
            research_brief=research_brief,
            candidate_count=candidate_count,
        )
        database = get_database()
        return await _record_async(
            database,
            "search_semantic_scholar",
            request.model_dump(),
            lambda: _search_result(
                search_semantic_scholar_provider,
                request.research_goal,
                request.research_brief,
                request.candidate_count,
            ),
        )

    @server.tool()
    async def search_arxiv(
        research_goal: str, research_brief: str = "", candidate_count: int = 10
    ) -> SearchPapersResult:
        """Search verified metadata from the official arXiv Atom API."""
        request = SearchPapersRequest(
            research_goal=research_goal,
            research_brief=research_brief,
            candidate_count=candidate_count,
        )
        database = get_database()
        return await _record_async(
            database,
            "search_arxiv",
            request.model_dump(),
            lambda: _search_result(
                search_arxiv_provider,
                request.research_goal,
                request.research_brief,
                request.candidate_count,
            ),
        )

    @server.tool()
    def submit_discovery_batch(
        canvas_id: str,
        run_id: str,
        batch_number: int,
        attempt: int,
        bright_status: str,
        bright_unavailable_reason: str | None,
        searches: list[DiscoverySearchFinding],
        scraped_pages: list[DiscoveryScrapedPage],
        introduced_candidates: list[DiscoveryResolvedCandidate],
        decisions: list[DiscoverySubmittedDecision],
    ) -> SubmitDiscoveryBatchResult:
        """Store one shaped discovery handoff without publishing any papers."""
        request = SubmitDiscoveryBatchRequest(
            canvas_id=canvas_id,
            run_id=run_id,
            batch_number=batch_number,
            attempt=attempt,
            bright_status=bright_status,
            bright_unavailable_reason=bright_unavailable_reason,
            searches=searches,
            scraped_pages=scraped_pages,
            introduced_candidates=introduced_candidates,
            decisions=decisions,
        )
        database = get_database()
        payload = public_diagnostic_value(request.model_dump(mode="json"))

        def operation() -> SubmitDiscoveryBatchResult:
            store_discovery_submission(
                database,
                request.canvas_id,
                request.run_id,
                request.batch_number,
                request.attempt,
                payload,
            )
            return SubmitDiscoveryBatchResult(
                status="stored",
                batch_number=request.batch_number,
                attempt=request.attempt,
            )

        return _record_sync(
            database,
            "submit_discovery_batch",
            payload,
            operation,
            canvas_id=request.canvas_id,
            run_id=request.run_id,
        )

    @server.tool()
    async def record_discovery_decision(
        canvas_id: str,
        run_id: str | None,
        batch_number: int,
        candidates: list[DiscoveryCandidateInput],
    ) -> RecordDiscoveryDecisionResult:
        """Persist every accept/reject decision and attach accepted papers."""
        request = RecordDiscoveryDecisionRequest(
            canvas_id=canvas_id,
            run_id=run_id,
            batch_number=batch_number,
            candidates=candidates,
        )
        database = get_database()
        payload = [candidate.model_dump() for candidate in request.candidates]
        async def operation() -> RecordDiscoveryDecisionResult:
            result = RecordDiscoveryDecisionResult(
                accepted_papers=record_discovery_batch(
                    database,
                    request.canvas_id,
                    request.run_id,
                    request.batch_number,
                    payload,
                )
            )
            await publish_change(request.canvas_id, "paper.added")
            return result

        return await _record_async(
            database,
            "record_discovery_decision",
            {
                "canvas_id": request.canvas_id,
                "run_id": request.run_id,
                "batch_number": request.batch_number,
                "candidates": payload,
            },
            operation,
            canvas_id=request.canvas_id,
            run_id=request.run_id,
        )

    @server.tool()
    async def record_paper_source(
        canvas_id: str, paper_id: str, source: PaperSourceInput
    ) -> RecordPaperSourceResult:
        """Persist verified source text and create evidence records."""
        request = RecordPaperSourceRequest(
            canvas_id=canvas_id, paper_id=paper_id, source=source
        )
        database = get_database()
        async def operation() -> RecordPaperSourceResult:
            result = RecordPaperSourceResult.model_validate(
                store_paper_source(
                    database,
                    request.paper_id,
                    request.source,
                    canvas_id=request.canvas_id,
                )
            )
            await publish_change(request.canvas_id, "paper.source.updated")
            return result

        return await _record_async(
            database,
            "record_paper_source",
            request.model_dump(),
            operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    async def record_review(
        canvas_id: str, paper_id: str, review: ReviewInput
    ) -> RecordReviewResult:
        """Persist one evidence-grounded review for one paper."""
        request = RecordReviewRequest(
            canvas_id=canvas_id, paper_id=paper_id, review=review
        )
        database = get_database()
        async def operation() -> RecordReviewResult:
            result = RecordReviewResult(
                review_id=store_review(
                    database, request.canvas_id, request.paper_id, request.review
                )
            )
            await publish_change(request.canvas_id, "review.completed")
            return result

        return await _record_async(
            database,
            "record_review",
            request.model_dump(),
            operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    async def record_relationship(
        canvas_id: str, relationship: RelationshipInput
    ) -> RecordRelationshipResult:
        """Persist one evidence-grounded relationship between canvas papers."""
        request = RecordRelationshipRequest(
            canvas_id=canvas_id, relationship=relationship
        )
        database = get_database()
        async def operation() -> RecordRelationshipResult:
            result = RecordRelationshipResult(
                relationship_id=store_relationship(
                    database, request.canvas_id, request.relationship
                )
            )
            await publish_change(request.canvas_id, "relationship.added")
            return result

        return await _record_async(
            database,
            "record_relationship",
            request.model_dump(),
            operation,
            canvas_id=request.canvas_id,
        )

    @server.tool()
    async def record_progress(
        run_id: str, event_type: ProgressEventType, payload: ProgressPayload
    ) -> RecordProgressResult:
        """Append a normalized progress event to an existing autonomous run."""
        request = RecordProgressRequest(
            run_id=run_id, event_type=event_type, payload=payload
        )
        database = get_database()

        async def operation() -> RecordProgressResult:
            result = progress_recorder(
                request.run_id,
                request.event_type,
                request.payload.model_dump(exclude_none=True),
            )
            if hasattr(result, "__await__"):
                await result
            return RecordProgressResult(
                run_id=request.run_id, event_type=request.event_type
            )

        return await _record_async(
            database,
            "record_progress",
            request.model_dump(exclude_none=True),
            operation,
            run_id=request.run_id,
        )

    @server.tool()
    async def create_canvas_and_start_build(
        research_goal: str, name: str | None = None
    ) -> CreateCanvasAndStartBuildResult:
        """Create one canvas and enqueue its autonomous research build exactly once."""
        request = CreateCanvasAndStartBuildRequest(
            research_goal=research_goal,
            name=name,
        )
        database = get_database()
        if build_starter is None:
            raise RuntimeError("Canvas build starter is not configured")

        async def operation() -> CreateCanvasAndStartBuildResult:
            canvas, run = create_canvas_and_queue_build(
                database,
                {
                    "name": request.name,
                    "research_goal": request.research_goal,
                },
            )
            started = build_starter(run.id)
            if hasattr(started, "__await__"):
                await started
            await publish_change(canvas.id, "canvas.created")
            await publish_change(canvas.id, "run.queued")
            return CreateCanvasAndStartBuildResult(
                canvas_id=canvas.id,
                run_id=run.id,
                status="queued",
                name=canvas.name,
            )

        return await _record_async(
            database,
            "create_canvas_and_start_build",
            request.model_dump(),
            operation,
        )

    _bind_request_contracts(
        server,
        {
            "get_canvas": GetCanvasRequest,
            "get_paper_text": GetPaperTextRequest,
            "resolve_paper_metadata": ResolvePaperMetadataRequest,
            "upsert_canvas_paper": UpsertCanvasPaperRequest,
            "search_semantic_scholar": SearchPapersRequest,
            "search_arxiv": SearchPapersRequest,
            "record_discovery_decision": RecordDiscoveryDecisionRequest,
            "submit_discovery_batch": SubmitDiscoveryBatchRequest,
            "record_paper_source": RecordPaperSourceRequest,
            "record_review": RecordReviewRequest,
            "record_relationship": RecordRelationshipRequest,
            "record_progress": RecordProgressRequest,
            "create_canvas_and_start_build": CreateCanvasAndStartBuildRequest,
        },
    )
    return server


def _bind_request_contracts(
    server: FastMCP, contracts: dict[str, type[StrictToolModel]]
) -> None:
    """Make the public MCP schema and runtime use the same strict request models."""
    for name, contract in contracts.items():
        tool = server._tool_manager.get_tool(name)  # noqa: SLF001
        if tool is None:  # pragma: no cover - registration above is deterministic
            raise RuntimeError(f"MCP tool was not registered: {name}")
        tool.fn_metadata.arg_model = contract
        tool.parameters = contract.model_json_schema(by_alias=True)


async def _resolve_metadata_result(paper_url: str) -> ResolvePaperMetadataResult:
    return ResolvePaperMetadataResult.model_validate(
        await resolve_paper_link(paper_url)
    )


async def _search_result(
    provider: Callable[..., Any],
    research_goal: str,
    research_brief: str,
    candidate_count: int,
) -> SearchPapersResult:
    return SearchPapersResult(
        papers=await provider(research_goal, research_brief, candidate_count)
    )
