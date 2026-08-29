from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from research_map_backend.app import create_app
from research_map_backend.mcp_contracts import (
    CreateCanvasAndStartBuildRequest,
    CreateCanvasAndStartBuildResult,
    GetCanvasRequest,
    GetCanvasResult,
    GetPaperTextRequest,
    GetPaperTextResult,
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
    StrictToolModel,
    SubmitDiscoveryBatchRequest,
    SubmitDiscoveryBatchResult,
    UpsertCanvasPaperRequest,
    UpsertCanvasPaperResult,
)
from research_map_backend.db import Database
from research_map_backend.mcp_server import _finish_invocation, _json_value
from research_map_backend.models import ToolInvocation
from research_map_backend.pipeline import SECTION_KEYS
from research_map_backend.schemas import PaperUpdate
from research_map_backend.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        openai_api_key="test-secret",
    )


def _paper_metadata() -> dict:
    return {
        "source_provider": "openalex",
        "candidate_id": "openalex:W1",
        "semantic_scholar_id": None,
        "doi": "10.1000/example",
        "arxiv_id": None,
        "title": "An Evidence Paper",
        "authors": ["A. Author"],
        "year": None,
        "month": None,
        "publication_date": None,
        "venue": "Journal",
        "abstract": "Abstract evidence.",
        "url": "https://example.org/paper",
        "pdf_url": None,
        "citation_count": 3,
        "open_access_locations": [],
        "best_oa_location": None,
        "primary_location": None,
        "is_retracted": False,
        "work_type": "article",
        "submitted_url": None,
    }


def _review() -> dict:
    return {
        "sections": {
            key: {
                "text": "Not reported in the supplied evidence.",
                "evidence_ids": [],
                "confidence": "low",
            }
            for key in SECTION_KEYS
        },
        "confidence": "low",
        "author": "reviewer",
    }


def _paper_result() -> dict:
    return {
        "id": "paper-1",
        "title": "An Evidence Paper",
        "authors": ["A. Author"],
        "year": None,
        "month": None,
        "abstract": "Abstract evidence.",
        "url": "https://example.org/paper",
        "doi": None,
        "arxiv_id": None,
        "semantic_scholar_id": None,
        "sources": [],
        "review": None,
    }


@pytest.mark.parametrize(
    "paper_reference",
    [
        "https://arxiv.org/abs/2005.11401",
        "https://publisher.example/paper.pdf",
        "10.1000/example",
        "arXiv:2005.11401",
        "2005.11401",
    ],
)
def test_mcp_paper_input_contract_accepts_secure_links_and_identifiers(
    paper_reference: str,
) -> None:
    assert (
        ResolvePaperMetadataRequest(paper_url=paper_reference).paper_url
        == paper_reference
    )


@pytest.mark.parametrize(
    "paper_reference",
    ["https://publisher.example/paper", "doi:10.1000/updated", "arXiv:2005.11401"],
)
def test_api_paper_update_uses_shared_reference_contract(paper_reference: str) -> None:
    assert PaperUpdate(link=paper_reference).link == paper_reference


@pytest.mark.parametrize(
    ("paper_reference", "message"),
    [
        ("http://publisher.example/paper", "must use HTTPS"),
        ("ftp://publisher.example/paper", "HTTPS URL, DOI, or arXiv ID"),
        ("not a paper", "HTTPS URL, DOI, or arXiv ID"),
    ],
)
def test_mcp_paper_input_contract_rejects_unsupported_references(
    paper_reference: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ResolvePaperMetadataRequest(paper_url=paper_reference)


CONTRACTS = [
    (
        "get_canvas",
        GetCanvasRequest,
        {"canvas_id": "canvas-1"},
        GetCanvasResult,
        {
            "canvas": {
                "id": "canvas-1",
                "name": "Canvas",
                "research_goal": "Goal",
                "research_brief": {"text": "Brief"},
                "build_status": "completed",
            },
            "papers": [],
        },
    ),
    (
        "get_paper_text",
        GetPaperTextRequest,
        {"canvas_id": "canvas-1", "paper_id": "paper-1"},
        GetPaperTextResult,
        _paper_result(),
    ),
    (
        "resolve_paper_metadata",
        ResolvePaperMetadataRequest,
        {"paper_url": "https://doi.org/10.1000/example"},
        ResolvePaperMetadataResult,
        _paper_metadata(),
    ),
    (
        "upsert_canvas_paper",
        UpsertCanvasPaperRequest,
        {"canvas_id": "canvas-1", "candidate": _paper_metadata()},
        UpsertCanvasPaperResult,
        {"paper_id": "paper-1"},
    ),
    (
        "search_semantic_scholar",
        SearchPapersRequest,
        {"research_goal": "Goal", "research_brief": "Brief", "candidate_count": 5},
        SearchPapersResult,
        {"papers": [_paper_metadata()]},
    ),
    (
        "search_arxiv",
        SearchPapersRequest,
        {"research_goal": "Goal", "research_brief": "Brief", "candidate_count": 5},
        SearchPapersResult,
        {"papers": [_paper_metadata()]},
    ),
    (
        "submit_discovery_batch",
        SubmitDiscoveryBatchRequest,
        {
            "canvas_id": "canvas-1",
            "run_id": "run-1",
            "batch_number": 1,
            "attempt": 1,
            "bright_status": "available",
            "bright_unavailable_reason": None,
            "searches": [
                {
                    "query": f"anchor methods benchmarks related work group {index}",
                    "finding": "Found and compared scholarly candidates.",
                }
                for index in range(5)
            ],
            "scraped_pages": [
                {
                    "candidate_id": f"candidate-{index}",
                    "url": f"https://example.org/paper-{index}",
                    "finding": "Scholarly page supports the candidate.",
                }
                for index in range(3)
            ],
            "introduced_candidates": [],
            "decisions": [
                {
                    "candidate_id": f"candidate-{index}",
                    "bright_evidence_urls": [f"https://example.org/paper-{index}"],
                    "score": {
                        "relevance": 30,
                        "source_credibility": 15,
                        "age_adjusted_impact": 10,
                        "full_text_availability": 15,
                        "novelty": 10,
                        "relationship_potential": 10,
                    },
                    "accepted": True,
                    "reason": "Relevant and supported.",
                }
                for index in range(3)
            ],
        },
        SubmitDiscoveryBatchResult,
        {"status": "stored", "batch_number": 1, "attempt": 1},
    ),
    (
        "record_discovery_decision",
        RecordDiscoveryDecisionRequest,
        {
            "canvas_id": "canvas-1",
            "run_id": "run-1",
            "batch_number": 1,
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "title": "Paper",
                    "authors": [],
                    "year": None,
                    "month": None,
                    "abstract": "",
                    "url": "https://example.org/paper",
                    "score": {},
                    "total_score": 50,
                    "accepted": True,
                    "reason": "Relevant.",
                }
            ],
        },
        RecordDiscoveryDecisionResult,
        {"accepted_papers": {"candidate-1": "paper-1"}},
    ),
    (
        "record_paper_source",
        RecordPaperSourceRequest,
        {
            "canvas_id": "canvas-1",
            "paper_id": "paper-1",
            "source": {
                "url": "https://example.org/paper.pdf",
                "source_type": "pdf",
                "retrieval_status": "completed",
                "page_count": 1,
                "pages": [{"page": 1, "text": "Evidence."}],
            },
        },
        RecordPaperSourceResult,
        {
            "source_id": "source-1",
            "source_type": "pdf",
            "retrieval_status": "completed",
            "evidence_ids": ["evidence-1"],
            "evidence": [
                {
                    "id": "evidence-1",
                    "page": 1,
                    "text": "Evidence.",
                    "source_url": "https://example.org/paper.pdf",
                    "source_type": "pdf",
                }
            ],
        },
    ),
    (
        "record_review",
        RecordReviewRequest,
        {"canvas_id": "canvas-1", "paper_id": "paper-1", "review": _review()},
        RecordReviewResult,
        {"review_id": "review-1"},
    ),
    (
        "record_relationship",
        RecordRelationshipRequest,
        {
            "canvas_id": "canvas-1",
            "relationship": {
                "source_paper_id": "paper-1",
                "target_paper_id": "paper-2",
                "type": "extends",
                "label": "extends",
                "explanation": "The later work extends the method.",
                "evidence_ids": ["evidence-1", "evidence-2"],
                "confidence": "medium",
            },
        },
        RecordRelationshipResult,
        {"relationship_id": "relationship-1"},
    ),
    (
        "record_progress",
        RecordProgressRequest,
        {
            "run_id": "run-1",
            "event_type": "agent.message.delta",
            "payload": {"agent": "reviewer", "content": "Working."},
        },
        RecordProgressResult,
        {"run_id": "run-1", "event_type": "agent.message.delta"},
    ),
    (
        "create_canvas_and_start_build",
        CreateCanvasAndStartBuildRequest,
        {"research_goal": "Map reliable research agents.", "name": "Agents"},
        CreateCanvasAndStartBuildResult,
        {
            "canvas_id": "canvas-1",
            "run_id": "run-1",
            "status": "queued",
            "name": "Agents",
        },
    ),
]


def test_all_application_mcp_tools_publish_input_and_output_schemas(
    tmp_path: Path,
) -> None:
    with TestClient(
        create_app(_settings(tmp_path)), base_url="http://127.0.0.1:8000"
    ) as client:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-03-26",
        }
        listed = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = listed.json()["result"]["tools"]

    assert [tool["name"] for tool in tools] == [item[0] for item in CONTRACTS]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
        assert tool["outputSchema"]
        assert tool["outputSchema"]["type"] == "object"
        assert tool["outputSchema"]["additionalProperties"] is False
    for _, _, _, result_model, _ in CONTRACTS:
        assert issubclass(result_model, StrictToolModel)


@pytest.mark.parametrize(
    ("tool_name", "request_model", "valid_request", "result_model", "valid_result"),
    CONTRACTS,
    ids=[item[0] for item in CONTRACTS],
)
def test_each_mcp_tool_has_strict_validated_request_and_result(
    tool_name, request_model, valid_request, result_model, valid_result
) -> None:
    assert request_model.model_validate(valid_request) is not None
    assert result_model.model_validate(valid_result) is not None

    invalid_request = deepcopy(valid_request)
    invalid_request["unexpected"] = "must fail"
    with pytest.raises(ValidationError):
        request_model.model_validate(invalid_request)

    if isinstance(valid_result, dict):
        invalid_result = deepcopy(valid_result)
        invalid_result["unexpected"] = "must fail"
    elif isinstance(valid_result, list):
        invalid_result = "must fail"
    else:
        invalid_result = {"unexpected": valid_result}
    with pytest.raises(ValidationError):
        result_model.model_validate(invalid_result)
    if result_model is RecordDiscoveryDecisionResult:
        with pytest.raises(ValidationError):
            result_model.model_validate({"accepted_papers": {"candidate-1": 7}})


def test_create_canvas_tool_derives_a_name_when_omitted() -> None:
    request = CreateCanvasAndStartBuildRequest(
        research_goal="  Map trustworthy scientific agents.  "
    )

    assert request.research_goal == "Map trustworthy scientific agents."
    assert request.name == "Map trustworthy scientific agents."


@pytest.mark.parametrize(
    ("model", "payload", "path"),
    [
        (
            UpsertCanvasPaperRequest,
            {"canvas_id": "c", "candidate": _paper_metadata()},
            ("candidate",),
        ),
        (
            RecordDiscoveryDecisionRequest,
            CONTRACTS[7][2],
            ("candidates", 0),
        ),
        (RecordPaperSourceRequest, CONTRACTS[8][2], ("source", "pages", 0)),
        (
            RecordReviewRequest,
            CONTRACTS[9][2],
            ("review", "sections", SECTION_KEYS[0]),
        ),
        (
            RecordRelationshipRequest,
            CONTRACTS[10][2],
            ("relationship",),
        ),
        (RecordProgressRequest, CONTRACTS[11][2], ("payload",)),
    ],
)
def test_nested_mcp_contracts_reject_extra_data(model, payload, path) -> None:
    invalid = deepcopy(payload)
    target = invalid
    for part in path:
        target = target[part]
    target["unexpected"] = "must fail"
    with pytest.raises(ValidationError):
        model.model_validate(invalid)


def test_tool_audit_redacts_secret_and_reasoning_shaped_fields() -> None:
    assert _json_value(
        {
            "canvas_id": "canvas-1",
            "api_key": "secret",
            "nested": {"reasoning_content": "private", "value": "public"},
        }
    ) == {
        "canvas_id": "canvas-1",
        "api_key": "[redacted]",
        "nested": {"reasoning_content": "[redacted]", "value": "public"},
    }


def test_failed_tool_audit_redacts_string_content_before_persistence(
    tmp_path: Path,
) -> None:
    database = Database(_settings(tmp_path))
    database.create_schema()
    with database.session_context() as session:
        invocation = ToolInvocation(tool_name="get_canvas", arguments={})
        session.add(invocation)
        session.commit()
        invocation_id = invocation.id

    _finish_invocation(
        database,
        invocation_id,
        error=RuntimeError(
            "provider HTTP 429; Authorization: Bearer sk-or-v1-live; "
            "reasoning_content=private; retry_after=3"
        ),
    )
    with database.session_context() as session:
        stored = session.get(ToolInvocation, invocation_id)
        assert stored is not None
        assert "provider HTTP 429" in stored.error
        assert "retry_after=3" in stored.error
        assert "sk-or-v1-live" not in stored.error
        assert "private" not in stored.error
        assert stored.error.count("[redacted]") >= 2
    database.dispose()
