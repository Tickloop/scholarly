from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from research_map_backend.db import Database
from research_map_backend.integrations.trueforge import run_agent
from research_map_backend.paper_links import attach_canvas_paper, resolve_paper_link
from research_map_backend.paper_sources import acquire_paper_source
from research_map_backend.pipeline import (
    AgentSource,
    EventSink,
    _connection_prompt,
    _pair_prompt,
    _review_prompt,
    _run_specialist,
    _validate_pair_output,
    _validate_relationship_output,
    _validate_review_output,
)
from research_map_backend.research_store import (
    get_pipeline_papers,
    mark_paper_failed,
    store_paper_source,
    store_relationship,
    store_review,
)
from research_map_backend.reviewer_slots import reviewer_slot
from research_map_backend.settings import Settings

MetadataResolver = Callable[[str], Awaitable[dict[str, Any]]]
SourceAcquirer = Callable[[Settings, dict[str, Any]], Awaitable[dict[str, Any]]]


async def run_link_pipeline(
    *,
    database: Database,
    settings: Settings,
    canvas_id: str,
    run_id: str,
    paper_url: str,
    research_goal: str,
    research_brief: str,
    placeholder_paper_id: str | None = None,
    replace_protected_review: bool = False,
    emit: EventSink,
    agent_source: AgentSource = run_agent,
    metadata_resolver: MetadataResolver = resolve_paper_link,
    source_acquirer: SourceAcquirer = acquire_paper_source,
    reviewer_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Resolve and autonomously process one user-submitted academic link."""
    await emit("tool.started", {"agent": "discovery", "name": "resolve_paper_metadata"})
    candidate = await metadata_resolver(paper_url)
    candidate["submitted_url"] = paper_url
    paper_id = attach_canvas_paper(
        database,
        canvas_id,
        candidate,
        placeholder_paper_id=placeholder_paper_id,
    )
    await emit(
        "paper.added",
        {"agent": "discovery", "paper_id": paper_id, "title": candidate.get("title")},
    )
    await emit(
        "tool.completed",
        {"agent": "discovery", "name": "resolve_paper_metadata", "paper_id": paper_id},
    )

    source = await source_acquirer(settings, candidate)
    source_result = store_paper_source(database, paper_id, source, canvas_id=canvas_id)
    evidence = source_result.get("evidence", [])
    evidence_ids = [
        item for item in source_result.get("evidence_ids", []) if isinstance(item, str)
    ]
    if source_result.get("retrieval_status") == "unavailable":
        await emit(
            "tool.completed",
            {
                "agent": "reviewer",
                "name": "acquire_paper_source",
                "status": "failed",
                "message": source.get("error") or "No usable paper text was available.",
            },
        )
    else:
        await emit(
            "tool.completed",
            {
                "agent": "reviewer",
                "name": "acquire_paper_source",
                "source_type": source_result.get("source_type"),
            },
        )

    try:
        reviewer_limit = reviewer_semaphore or asyncio.Semaphore(
            settings.reviewer_concurrency
        )
        async with reviewer_slot(
            reviewer_limit,
            emit,
            canvas_id=canvas_id,
            run_id=run_id,
            paper_id=paper_id,
            purpose="link_review",
        ):
            review_text = await _run_specialist(
                settings,
                "research-map-reviewer",
                f"reviewer:{paper_id}",
                _review_prompt({**candidate, "paper_id": paper_id}, evidence),
                emit,
                agent_source,
            )
        review = _validate_review_output(review_text, evidence_ids)
        store_review(
            database,
            canvas_id,
            paper_id,
            review,
            replace_protected=replace_protected_review,
        )
    except Exception as error:
        message = str(error) or "Review could not be validated or stored."
        mark_paper_failed(database, canvas_id, paper_id, message)
        raise
    await emit("review.completed", {"agent": "reviewer", "paper_id": paper_id})

    all_papers = get_pipeline_papers(database, canvas_id)
    review_records = []
    for paper in all_papers:
        sections = paper.get("review")
        if not isinstance(sections, dict):
            continue
        stored_ids = []
        for section in sections.values():
            if isinstance(section, dict) and isinstance(section.get("evidence_ids"), list):
                stored_ids.extend(
                    value for value in section["evidence_ids"] if isinstance(value, str)
                )
        review_records.append(
            {
                "paper_id": paper["id"],
                "title": paper["title"],
                "year": paper["year"],
                "month": paper.get("month", 1),
                "sections": sections,
                "evidence_ids": list(dict.fromkeys(stored_ids)),
            }
        )

    relationships_added = 0
    if len(review_records) >= 2:
        pair_text = await _run_specialist(
            settings,
            "research-map-discovery",
            "discovery:submitted-paper-connections",
            _pair_prompt(research_brief or research_goal, review_records)
            + f"\nOnly shortlist pairs that include submitted paper {paper_id}.",
            emit,
            agent_source,
        )
        pairs = [
            pair
            for pair in _validate_pair_output(pair_text, review_records)
            if paper_id in {pair["source_paper_id"], pair["target_paper_id"]}
        ]
        if pairs:
            relationship_text = await _run_specialist(
                settings,
                "research-map-connection",
                f"connection:{paper_id}",
                _connection_prompt(pairs, review_records),
                emit,
                agent_source,
            )
            relationships = _validate_relationship_output(
                relationship_text, pairs, review_records
            )
            for relationship in relationships:
                relationship_id = store_relationship(database, canvas_id, relationship)
                await emit(
                    "relationship.added",
                    {
                        "agent": "connection",
                        "relationship_id": relationship_id,
                        "source": relationship["source_paper_id"],
                        "target": relationship["target_paper_id"],
                        "relationship_type": relationship["type"],
                    },
                )
                relationships_added += 1

    return {
        "paper_id": paper_id,
        "completed_reviews": 1,
        "relationships": relationships_added,
    }
