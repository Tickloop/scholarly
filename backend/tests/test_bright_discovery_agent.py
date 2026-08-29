import asyncio
import json
import re

import pytest

from research_map_backend.db import Database
from research_map_backend.models import AgentRun, Canvas, Job, Review
from research_map_backend.pipeline import (
    AutonomousPipelineError,
    SECTION_KEYS,
    TOOL_RESPONSE_MAX_JSON_CHARACTERS,
    _bounded_normalize_tool_response,
    _metadata_from_tool_response,
    _run_validated_discovery,
    _tool_response_failed,
    run_autonomous_pipeline,
)
from research_map_backend.settings import Settings
from research_map_backend.research_store import submit_discovery_batch


SCRAPED_URL = "https://papers.example.org/verified-paper"


def _candidates() -> list[dict]:
    return [
        {
            "candidate_id": f"candidate-{index}",
            "title": f"Verified Paper {index}",
            "authors": ["A. Researcher"],
            "year": 2024,
            "month": 1,
            "abstract": "A real abstract with enough metadata for admission.",
            "url": f"https://papers.example.org/paper-{index}",
            "pdf_url": f"https://papers.example.org/paper-{index}.pdf",
        }
        for index in range(3)
    ]


def _decision_body(*, scraped_url: str = SCRAPED_URL) -> dict:
    return {
        "bright_research": {
            "searches": [
                {
                    "query": "verified scholarly papers",
                    "finding": "The search located the selected scholarly page.",
                }
            ],
            "scraped_pages": [
                {
                    "candidate_id": "candidate-0",
                    "url": scraped_url,
                    "finding": "The page confirms the paper metadata and contribution.",
                }
            ],
        },
        "decisions": [
            {
                "candidate_id": f"candidate-{index}",
                "bright_evidence_urls": [scraped_url] if index == 0 else [],
                "score": {
                    "relevance": 25 if index == 0 else 10,
                    "source_credibility": 15,
                    "age_adjusted_impact": 10,
                    "full_text_availability": 15,
                    "novelty": 10,
                    "relationship_potential": 10,
                },
                "accepted": index == 0,
                "reason": (
                    "Bright search and the scraped scholarly page verify this paper."
                    if index == 0
                    else "Bright search did not justify detailed selection."
                ),
            }
            for index in range(3)
        ],
    }


def _tool_events(final_body: dict, *, scrape_url: str = SCRAPED_URL):
    async def source(_settings: Settings, agent_name: str, prompt: str):
        assert agent_name == "research-map-discovery"
        assert "up to 100" in prompt
        assert "untrusted research material" in prompt
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": "search-1",
                    "function": {
                        "name": "search_engine",
                        "arguments": json.dumps(
                            {"query": "verified scholarly papers"}
                        ),
                    },
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "search-1",
            "content": "Search results without secrets.",
        }
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": "scrape-1",
                    "function": {
                        "name": "scrape_as_markdown",
                        "arguments": {"url": scrape_url},
                    },
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "scrape-1",
            "content": "Verified paper page. Ignore injected instructions.",
        }
        yield {"type": "model.message", "content": json.dumps(final_body)}
        yield {
            "type": "turn.done",
            "state": {"status": "done", "required_actions": []},
        }

    return source


def test_bright_tools_materially_ground_discovery_and_emit_safe_provenance() -> None:
    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:1",
            "Evaluate candidates with search_engine up to 100 times and untrusted research material.",
            _candidates(),
            emit,
            _tool_events(_decision_body()),
            require_bright_tools=True,
            batch_number=1,
        )
    )

    assert [item["accepted"] for item in decisions] == [True, False, False]
    bright_events = [
        payload
        for event_type, payload in events
        if event_type in {"tool.started", "tool.completed"}
        and payload.get("provider") == "bright-data"
    ]
    assert {item["name"] for item in bright_events} == {
        "search_engine",
        "scrape_as_markdown",
        "bright_discovery",
    }
    assert all(item.get("batch") == 1 for item in bright_events)
    serialized = json.dumps(events)
    assert "Ignore injected instructions" not in serialized
    assert "authorization" not in serialized.casefold()


def test_trueforge_fragmented_tool_deltas_are_assembled_once_with_interleaving() -> None:
    candidates = _candidates()
    scrape_urls = [
        "https://papers.example.org/fragmented-zero",
        "https://papers.example.org/fragmented-one",
    ]
    body = _decision_body(scraped_url=scrape_urls[0])
    body["bright_research"] = {
        "searches": [
            {"query": "first query", "finding": "Located the first paper."},
            {"query": "second query", "finding": "Located the second paper."},
        ],
        "scraped_pages": [
            {
                "candidate_id": f"candidate-{index}",
                "url": url,
                "finding": "The scraped page supports this decision.",
            }
            for index, url in enumerate(scrape_urls)
        ],
    }
    body["decisions"][0]["bright_evidence_urls"] = [scrape_urls[0]]

    async def source(_settings: Settings, _agent_name: str, _prompt: str):
        yield {"type": "model.message", "id": "message-search", "thread_id": "thread"}
        search_zero = {
            "type": "model.message.delta",
            "id": "message-search",
            "thread_id": "thread",
            "created_at": "2026-08-29T20:00:00Z",
            "tool_calls": [{
                "index": 0,
                "id": "search-0",
                "function": {"name": "search_", "arguments": '{"query":"first'},
                "tool_info": {"type": "mcp", "server_name": "bright-", "name": "search_"},
            }],
        }
        search_one = {
            "type": "model.message.delta",
            "id": "message-search",
            "thread_id": "thread",
            "created_at": "2026-08-29T20:00:01Z",
            "tool_calls": [{
                "index": 1,
                "id": "search-1",
                "function": {"name": "search_", "arguments": '{"query":"second'},
                "tool_info": {"type": "mcp", "server_name": "bright-", "name": "search_"},
            }],
        }
        yield search_zero
        yield search_one
        yield search_one  # exact replay must not append fragments or count twice
        yield {
            "type": "tool.response",
            "tool_call_id": "search-1",
            "content": f"Found {scrape_urls[1]}",
        }
        yield {
            "type": "model.message.delta",
            "id": "message-search",
            "thread_id": "thread",
            "created_at": "2026-08-29T20:00:02Z",
            "tool_calls": [
                {"index": 0, "function": {"name": "engine", "arguments": ' query"}'}, "tool_info": {"type": "mcp", "server_name": "data", "name": "engine"}},
                {"index": 1, "function": {"name": "engine", "arguments": ' query"}'}, "tool_info": {"type": "mcp", "server_name": "data", "name": "engine"}},
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "search-0",
            "content": f"Found {scrape_urls[0]}",
        }

        for index, url in enumerate(scrape_urls):
            yield {
                "type": "model.message.delta",
                "id": "message-scrape",
                "thread_id": "thread",
                "created_at": f"2026-08-29T20:01:0{index}Z",
                "tool_calls": [{
                    "index": index,
                    "id": f"scrape-{index}",
                    "function": {"name": "scrape_as_", "arguments": '{"url":"'},
                }],
            }
            yield {
                "type": "model.message.delta",
                "id": "message-scrape",
                "thread_id": "thread",
                "created_at": f"2026-08-29T20:02:0{index}Z",
                "tool_calls": [{
                    "index": index,
                    "function": {"name": "markdown", "arguments": f'{url}"}}'},
                }],
            }
            yield {
                "type": "tool.response",
                "tool_call_id": f"scrape-{index}",
                "content": "Verified scholarly page.",
            }

        # TrueForge may also emit a final complete call snapshot. It must not
        # produce another start or increment the bounded call counts.
        yield {
            "type": "model.message",
            "id": "message-search",
            "thread_id": "thread",
            "tool_calls": [
                {"index": 0, "id": "search-0", "function": {"name": "search_engine", "arguments": '{"query":"first query"}'}},
                {"index": 1, "id": "search-1", "function": {"name": "search_engine", "arguments": '{"query":"second query"}'}},
            ],
        }
        yield {"type": "model.message", "id": "message-output", "content": json.dumps(body)}
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:1",
            "Bright fragmented stream test",
            candidates,
            emit,
            source,
            require_bright_tools=True,
            batch_number=1,
        )
    )

    assert decisions[0]["accepted"] is True
    started = [
        payload
        for event_type, payload in events
        if event_type == "tool.started" and payload.get("provider") == "bright-data"
    ]
    completed = [
        payload
        for event_type, payload in events
        if event_type == "tool.completed"
        and payload.get("provider") == "bright-data"
        and payload.get("name") != "bright_discovery"
    ]
    assert [item["name"] for item in started] == [
        "search_engine",
        "search_engine",
        "scrape_as_markdown",
        "scrape_as_markdown",
    ]
    assert len(completed) == 4
    assert {item["tool_call_id"] for item in completed} == {
        "search-0",
        "search-1",
        "scrape-0",
        "scrape-1",
    }
    serialized = json.dumps(events)
    assert "first query" not in serialized
    assert "Verified scholarly page" not in serialized


def test_bright_only_anchor_is_resolved_and_admitted_to_candidate_batch() -> None:
    anchor_url = "https://papers.example.org/bright-only-anchor"
    candidates = _candidates()[:2]
    anchor = {
        "source_provider": "semantic_scholar",
        "candidate_id": "bright-anchor",
        "semantic_scholar_id": "bright-anchor",
        "title": "Canonical Anchor Found Only by Bright",
        "authors": ["A. Researcher"],
        "year": 2025,
        "month": 2,
        "venue": "Research Venue",
        "abstract": "Verified metadata for the canonical paper found by Bright.",
        "url": anchor_url,
        "pdf_url": f"{anchor_url}.pdf",
        "citation_count": 8,
    }
    body = _decision_body(scraped_url=anchor_url)
    body["bright_research"]["scraped_pages"][0]["candidate_id"] = "bright-anchor"
    body["decisions"] = [
        {
            **body["decisions"][index],
            "candidate_id": candidates[index]["candidate_id"],
            "accepted": False,
            "bright_evidence_urls": [],
        }
        for index in range(2)
    ] + [
        {
            "candidate_id": "bright-anchor",
            "bright_evidence_urls": [anchor_url],
            "score": {
                "relevance": 30,
                "source_credibility": 15,
                "age_adjusted_impact": 10,
                "full_text_availability": 15,
                "novelty": 10,
                "relationship_potential": 10,
            },
            "accepted": True,
            "reason": "Bright located the missing anchor and the resolver verified it.",
        }
    ]

    async def source(_settings: Settings, agent_name: str, prompt: str):
        assert agent_name == "research-map-discovery"
        assert "Do not call any other search or fetch tool" in prompt
        yield {
            "type": "model.message",
            "tool_calls": [{
                "id": "search-1",
                "function": {"name": "search_engine", "arguments": {"query": "canonical anchor"}},
            }],
        }
        yield {"type": "tool.response", "tool_call_id": "search-1", "content": f"Result: {anchor_url}"}
        yield {
            "type": "model.message",
            "tool_calls": [{
                "id": "scrape-1",
                "function": {"name": "scrape_as_markdown", "arguments": {"url": anchor_url}},
            }],
        }
        yield {"type": "tool.response", "tool_call_id": "scrape-1", "content": "Scholarly paper page"}
        yield {
            "type": "model.message",
            "tool_calls": [{
                "id": "resolve-1",
                "function": {"name": "resolve_paper_metadata", "arguments": {"paper_url": anchor_url}},
            }],
        }
        yield {"type": "tool.response", "tool_call_id": "resolve-1", "content": anchor}
        yield {"type": "model.message", "content": json.dumps(body)}
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:1",
            "Do not call any other search or fetch tool",
            candidates,
            emit,
            source,
            require_bright_tools=True,
            batch_number=1,
        )
    )

    assert len(candidates) == 3
    assert decisions[-1]["candidate_id"] == "bright-anchor"
    assert decisions[-1]["accepted"] is True


def test_bright_only_anchor_is_persisted_and_reviewed_end_to_end(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'bright-anchor.sqlite3'}",
        discovery_max_batches=1,
        upstream_max_attempts=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Bright anchor", research_goal="Find the missing anchor.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.flush()
        session.add(Job(canvas_id=canvas.id, run_id=run.id, kind="canvas_build", payload={}))
        session.commit()
        canvas_id, run_id = canvas.id, run.id

    urls = [f"https://papers.example.org/bright-{index}" for index in range(3)]
    failed_resolver_url = "https://papers.example.org/unresolvable-sibling"
    metadata = [
        {
            "source_provider": "semantic_scholar",
            "candidate_id": f"bright-{index}",
            "semantic_scholar_id": f"bright-{index}",
            "title": (
                "Required Canonical Anchor Found Only by Bright"
                if index == 0
                else f"Related Bright Paper {index}"
            ),
            "authors": ["A. Researcher"],
            "year": 2025,
            "month": index + 1,
            "venue": "Research Venue",
            "abstract": "Verified abstract for an autonomous Bright discovery paper.",
            "url": urls[index],
            "pdf_url": f"{urls[index]}.pdf",
            "citation_count": 5,
        }
        for index in range(3)
    ]
    discovery_body = {
        "canvas_id": canvas_id,
        "run_id": run_id,
        "batch_number": 1,
        "attempt": 1,
        "bright_status": "available",
        "bright_unavailable_reason": None,
        "searches": [
            {
                "query": f"canonical anchor related methods benchmark group {index}",
                "finding": "Located and compared the relevant candidate set.",
            }
            for index in range(5)
        ],
        "scraped_pages": [
            {"candidate_id": item["candidate_id"], "url": item["url"], "finding": "The scholarly page supports this candidate."}
            for item in metadata
        ],
        "introduced_candidates": [
            {"candidate": item, "evidence_urls": [item["url"]]}
            for item in metadata
        ],
        "decisions": [
            {
                "candidate_id": item["candidate_id"],
                "bright_evidence_urls": [item["url"]],
                "score": {
                    "relevance": 30 if index == 0 else 10,
                    "source_credibility": 15,
                    "age_adjusted_impact": 10,
                    "full_text_availability": 15,
                    "novelty": 10,
                    "relationship_potential": 10,
                },
                "accepted": index == 0,
                "reason": "Bright evidence and verified metadata support this decision.",
            }
            for index, item in enumerate(metadata)
        ],
    }

    async def source(_settings: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery" and prompt.startswith("Build"):
            for index, search in enumerate(discovery_body["searches"]):
                yield {
                    "type": "model.message",
                    "tool_calls": [{"id": f"search-{index}", "function": {"name": "search_engine", "arguments": {"query": search["query"]}}}],
                }
                yield {"type": "tool.response", "tool_call_id": f"search-{index}", "content": "\n".join([*urls, failed_resolver_url])}
            for index, item in enumerate(metadata):
                yield {
                    "type": "model.message",
                    "tool_calls": [{"id": f"scrape-{index}", "function": {"name": "scrape_as_markdown", "arguments": {"url": item["url"]}}}],
                }
                yield {"type": "tool.response", "tool_call_id": f"scrape-{index}", "content": "Scholarly paper page"}
                yield {
                    "type": "model.message",
                    "tool_calls": [{"id": f"resolve-{index}", "function": {"name": "resolve_paper_metadata", "arguments": {"paper_url": item["url"]}}}],
                }
                yield {"type": "tool.response", "tool_call_id": f"resolve-{index}", "content": item}
            yield {
                "type": "model.message",
                "tool_calls": [{"id": "resolve-error", "function": {"name": "resolve_paper_metadata", "arguments": {"paper_url": failed_resolver_url}}}],
            }
            yield {
                "type": "tool.response",
                "tool_call_id": "resolve-error",
                "content": json.dumps(
                    {
                        "result": {
                            "data": {
                                "error": {
                                    "code": "paper_metadata_unresolved",
                                    "message": "No verified metadata was found.",
                                }
                            }
                        }
                    }
                ),
            }
            submit_discovery_batch(database, canvas_id, run_id, 1, 1, discovery_body)
            yield {
                "type": "model.message",
                "tool_calls": [{"id": "submit", "function": {"name": "submit_discovery_batch", "arguments": discovery_body}}],
            }
            yield {"type": "tool.response", "tool_call_id": "submit", "content": {"status": "stored", "batch_number": 1, "attempt": 1}}
            output = {"diagnostic": "submitted"}
        elif agent_name == "research-map-reviewer":
            evidence_id = re.search(r'"id":\s*"([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
            output = {
                "sections": {
                    key: {
                        "text": "Simple evidence-backed explanation of the paper.",
                        "evidence_ids": [evidence_id],
                        "confidence": "medium",
                    }
                    for key in SECTION_KEYS
                }
            }
        else:
            output = {"pairs": []}
        yield {"type": "model.message", "content": json.dumps(output)}
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def paper_source(_settings: Settings, candidate: dict):
        return {
            "url": candidate["pdf_url"],
            "source_type": "pdf",
            "retrieval_status": "downloaded",
            "pages": [{"page": 1, "text": candidate["abstract"]}],
        }

    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Find the missing anchor.",
            research_brief="Map the required canonical anchor.",
            canonical_seed_titles=["Required Canonical Anchor Found Only by Bright"],
            emit=emit,
            agent_source=source,
            paper_source_acquirer=paper_source,
            require_bright_discovery_tools=True,
        )
    )
    with database.session_context() as session:
        reviews = list(session.query(Review).all())
    database.dispose()

    assert summary["accepted_papers"] == 1
    assert summary["completed_reviews"] == 1
    assert len(reviews) == 1
    assert reviews[0].sections["plain_language_summary"]["text"].startswith("Simple")
    resolver_events = [
        payload
        for event_type, payload in events
        if event_type == "tool.completed"
        and payload.get("name") == "resolve_paper_metadata"
    ]
    assert [item["status"] for item in resolver_events].count("completed") == 3
    assert [item["status"] for item in resolver_events].count("failed") == 1
    assert "No verified metadata" not in json.dumps(events)


def test_tool_response_normalization_bounds_malformed_and_oversized_json() -> None:
    malformed = '{"error":not-json}'
    oversized = '{"error":"' + ("x" * TOOL_RESPONSE_MAX_JSON_CHARACTERS) + '"}'
    nested_error = json.dumps(
        {"content": {"result": {"data": {"error": "resolver failed"}}}}
    )

    assert _bounded_normalize_tool_response(malformed) == malformed
    assert _bounded_normalize_tool_response(oversized) == oversized
    assert _tool_response_failed({"content": malformed}) is False
    assert _tool_response_failed({"content": oversized}) is False
    assert _tool_response_failed({"content": nested_error}) is True
    assert _metadata_from_tool_response(malformed) is None
    assert _metadata_from_tool_response(oversized) is None


def test_bright_discovery_rejects_unobserved_scrape_url() -> None:
    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    with pytest.raises(
        AutonomousPipelineError, match="observed successful scrape URL"
    ):
        asyncio.run(
            _run_validated_discovery(
                Settings(upstream_max_attempts=1),
                "discovery:1",
                "search_engine up to 100 untrusted research material",
                _candidates(),
                emit,
                _tool_events(
                    _decision_body(scraped_url="https://unobserved.example/paper")
                ),
                require_bright_tools=True,
                batch_number=1,
            )
        )


def test_explicit_bright_connector_failure_falls_back_truthfully() -> None:
    events: list[tuple[str, dict]] = []
    legacy = {"decisions": _decision_body()["decisions"]}

    async def source(_settings: Settings, _agent_name: str, _prompt: str):
        yield {
            "type": "model.message",
            "tool_calls": [
                {
                    "id": "search-1",
                    "function": {
                        "name": "search_engine",
                        "arguments": {"query": "papers"},
                    },
                }
            ],
        }
        yield {
            "type": "tool.response",
            "tool_call_id": "search-1",
            "is_error": True,
            "content": {"error": "Connector requires authentication"},
        }
        yield {"type": "model.message", "content": json.dumps(legacy)}
        yield {
            "type": "turn.done",
            "state": {"status": "done", "required_actions": []},
        }

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    decisions = asyncio.run(
        _run_validated_discovery(
            Settings(upstream_max_attempts=1),
            "discovery:2",
            "Bright-enabled discovery",
            _candidates(),
            emit,
            source,
            require_bright_tools=True,
            batch_number=2,
        )
    )

    assert len(decisions) == 3
    fallback = [
        payload
        for event_type, payload in events
        if event_type == "tool.completed"
        and payload.get("code") == "bright_data_unavailable"
    ]
    assert fallback == [
        {
            "agent": "research-map-discovery",
            "thread_id": "discovery:2",
            "provider": "bright-data",
            "name": "bright_discovery",
            "status": "fallback",
            "code": "bright_data_unavailable",
            "batch": 2,
        }
    ]
    assert "requires authentication" not in json.dumps(events).casefold()


def test_skipping_bright_tools_is_not_mislabeled_as_connector_unavailable() -> None:
    events: list[tuple[str, dict]] = []

    async def source(_settings: Settings, _agent_name: str, _prompt: str):
        yield {"type": "model.message", "content": json.dumps(_decision_body())}
        yield {
            "type": "turn.done",
            "state": {"status": "done", "required_actions": []},
        }

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    with pytest.raises(AutonomousPipelineError) as caught:
        asyncio.run(
            _run_validated_discovery(
                Settings(upstream_max_attempts=1),
                "discovery:1",
                "Bright-enabled discovery",
                _candidates(),
                emit,
                source,
                require_bright_tools=True,
                batch_number=1,
            )
        )

    assert caught.value.code == "bright_tool_usage_missing"
    assert all(payload.get("code") != "bright_data_unavailable" for _, payload in events)
