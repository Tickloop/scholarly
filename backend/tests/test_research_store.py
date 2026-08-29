from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from research_map_backend.app import create_app
from research_map_backend.models import DiscoveryDecision, Evidence, Job, Relationship, Review
from research_map_backend.research_store import (
    create_job,
    get_pipeline_papers,
    record_discovery_batch,
    store_paper_source,
    store_relationship,
    store_research_brief,
    store_review,
    update_job,
)
from research_map_backend.settings import Settings


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
    )
    return TestClient(create_app(settings))


def candidate(candidate_id: str, title: str, year: int, *, accepted: bool) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": title,
        "authors": ["Researcher One", "Researcher Two"],
        "year": year,
        "abstract": f"Verified abstract for {title}.",
        "venue": "Test Conference",
        "url": f"https://example.org/{candidate_id}",
        "pdf_url": f"https://example.org/{candidate_id}.pdf",
        "semantic_scholar_id": candidate_id,
        "doi": None,
        "arxiv_id": None,
        "citation_count": 42,
        "score": {
            "relevance": 25,
            "credibility": 12,
            "impact": 10,
            "full_text": 15,
            "novelty": 10,
            "relationship": 8,
        },
        "total_score": 80 if accepted else 55,
        "accepted": accepted,
        "reason": "Relevant and usable" if accepted else "Not relevant enough",
    }


def review(evidence_id: str) -> dict:
    keys = [
        "core_idea",
        "problem_space",
        "approach",
        "data",
        "novel_contribution",
        "results",
        "benchmarks",
        "statistical_evidence",
        "limitations",
        "cited_ideas_and_differences",
        "plain_language_summary",
    ]
    return {
        "sections": {
            key: {
                "text": f"Supported {key.replace('_', ' ')}.",
                "evidence_ids": [evidence_id],
                "confidence": "medium",
            }
            for key in keys
        },
        "confidence": "medium",
    }


def test_pipeline_records_serialize_into_canvas_snapshot(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Agents", "research_goal": "Map agent research."},
        ).json()["canvas"]["id"]
        store_research_brief(
            database,
            canvas_id,
            {"question": "Which evaluations predict reliable agents?"},
        )
        accepted = record_discovery_batch(
            database,
            canvas_id,
            None,
            1,
            [
                candidate("paper-a", "Paper A", 2023, accepted=True),
                candidate("paper-b", "Paper B", 2024, accepted=True),
                candidate("paper-c", "Paper C", 2022, accepted=False),
            ],
        )
        first_source = store_paper_source(
            database,
            accepted["paper-a"],
            {
                "url": "https://example.org/paper-a.pdf",
                "pages": [{"page": 1, "text": "Evidence for paper A."}],
            },
        )
        second_source = store_paper_source(
            database,
            accepted["paper-b"],
            {
                "url": "https://example.org/paper-b.pdf",
                "pages": [{"page": 2, "text": "Evidence for paper B."}],
            },
        )
        store_review(
            database,
            canvas_id,
            accepted["paper-a"],
            review(first_source["evidence_ids"][0]),
        )
        store_review(
            database,
            canvas_id,
            accepted["paper-b"],
            review(second_source["evidence_ids"][0]),
        )
        store_relationship(
            database,
            canvas_id,
            {
                "source_paper_id": accepted["paper-a"],
                "target_paper_id": accepted["paper-b"],
                "type": "extends",
                "label": "extends",
                "explanation": "Paper B extends Paper A's evaluation method.",
                "evidence_ids": [
                    first_source["evidence_ids"][0],
                    second_source["evidence_ids"][0],
                ],
                "confidence": "high",
            },
        )
        job_id = create_job(database, canvas_id, None, "review", {})
        update_job(database, job_id, "running")
        update_job(database, job_id, "completed")

        snapshot = client.get(f"/api/v1/canvases/{canvas_id}")
        pipeline_papers = get_pipeline_papers(database, canvas_id)
        with database.session_context() as session:
            decisions = list(session.scalars(select(DiscoveryDecision)))
            stored_job = session.get(Job, job_id)

    assert set(accepted) == {"paper-a", "paper-b"}
    assert snapshot.status_code == 200
    assert snapshot.json()["canvas"]["research_brief"]["question"].startswith("Which")
    assert [paper["title"] for paper in snapshot.json()["papers"]] == [
        "Paper A",
        "Paper B",
    ]
    assert snapshot.json()["papers"][0]["review"]["coreIdea"].startswith("Supported")
    assert snapshot.json()["papers"][0]["summary"] == "Verified abstract for Paper A."
    assert snapshot.json()["papers"][0]["plain_language_summary"] == (
        "Supported plain language summary."
    )
    assert snapshot.json()["relationships"][0]["source"] == accepted["paper-a"]
    assert len(pipeline_papers) == 2
    assert pipeline_papers[0]["sources"][0]["pages"][0]["page"] == 1
    assert len(decisions) == 3
    assert stored_job is not None
    assert stored_job.status == "completed"
    assert stored_job.attempts == 1


def test_legacy_review_without_plain_language_summary_returns_null(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Legacy", "research_goal": "Read an older review."},
        ).json()["canvas"]["id"]
        accepted = record_discovery_batch(
            database,
            canvas_id,
            None,
            1,
            [candidate("legacy-paper", "Legacy Paper", 2020, accepted=True)],
        )
        source = store_paper_source(
            database,
            accepted["legacy-paper"],
            {
                "url": "https://example.org/legacy-paper.pdf",
                "pages": [{"page": 1, "text": "Legacy evidence."}],
            },
            canvas_id=canvas_id,
        )
        legacy_review = review(source["evidence_ids"][0])
        legacy_review["sections"].pop("plain_language_summary")
        store_review(database, canvas_id, accepted["legacy-paper"], legacy_review)

        paper = client.get(f"/api/v1/canvases/{canvas_id}").json()["papers"][0]

    assert paper["summary"] == "Verified abstract for Legacy Paper."
    assert paper["plain_language_summary"] is None


def test_review_rejects_evidence_from_another_paper(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Agents", "research_goal": "Map agent research."},
        ).json()["canvas"]["id"]
        accepted = record_discovery_batch(
            database,
            canvas_id,
            None,
            1,
            [
                candidate("paper-a", "Paper A", 2023, accepted=True),
                candidate("paper-b", "Paper B", 2024, accepted=True),
            ],
        )
        source = store_paper_source(
            database,
            accepted["paper-a"],
            {
                "url": "https://example.org/paper-a.pdf",
                "pages": [{"page": 1, "text": "Evidence for paper A."}],
            },
        )

        with pytest.raises(ValueError, match="does not belong"):
            store_review(
                database,
                canvas_id,
                accepted["paper-b"],
                review(source["evidence_ids"][0]),
            )


def test_malformed_review_and_relationship_updates_are_atomic(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Atomic", "research_goal": "Map agent research."},
        ).json()["canvas"]["id"]
        accepted = record_discovery_batch(
            database,
            canvas_id,
            None,
            1,
            [
                candidate("paper-a", "Paper A", 2023, accepted=True),
                candidate("paper-b", "Paper B", 2024, accepted=True),
            ],
        )
        first = store_paper_source(
            database,
            accepted["paper-a"],
            {"url": "https://example.org/a", "pages": [{"page": 1, "text": "A"}]},
            canvas_id=canvas_id,
        )
        second = store_paper_source(
            database,
            accepted["paper-b"],
            {"url": "https://example.org/b", "pages": [{"page": 1, "text": "B"}]},
            canvas_id=canvas_id,
        )
        review_id = store_review(
            database, canvas_id, accepted["paper-a"], review(first["evidence_ids"][0])
        )
        relationship_id = store_relationship(
            database,
            canvas_id,
            {
                "source_paper_id": accepted["paper-a"],
                "target_paper_id": accepted["paper-b"],
                "type": "extends",
                "label": "extends",
                "explanation": "Paper B extends Paper A.",
                "evidence_ids": [
                    first["evidence_ids"][0],
                    second["evidence_ids"][0],
                ],
                "confidence": "high",
            },
        )

        unsupported = review(first["evidence_ids"][0])
        unsupported["sections"]["core_idea"] = {
            "text": "Unsupported claim.",
            "evidence_ids": [],
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="not reported"):
            store_review(database, canvas_id, accepted["paper-a"], unsupported)
        with pytest.raises(ValueError, match="include both papers"):
            store_relationship(
                database,
                canvas_id,
                {
                    "source_paper_id": accepted["paper-a"],
                    "target_paper_id": accepted["paper-b"],
                    "type": "extends",
                    "label": "replacement",
                    "explanation": "Malformed replacement.",
                    "evidence_ids": [first["evidence_ids"][0], first["evidence_ids"][0]],
                    "confidence": "low",
                },
            )
        with database.session_context() as session:
            current_reviews = list(
                session.scalars(
                    select(Review).where(Review.is_current.is_(True))
                )
            )
            current_relationships = list(
                session.scalars(
                    select(Relationship).where(Relationship.is_current.is_(True))
                )
            )

    assert [item.id for item in current_reviews] == [review_id]
    assert [item.id for item in current_relationships] == [relationship_id]


def test_relationship_rejects_newer_to_older_direction(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Direction", "research_goal": "Map paper influence."},
        ).json()["canvas"]["id"]
        accepted = record_discovery_batch(
            database,
            canvas_id,
            None,
            1,
            [
                candidate("older", "Older", 2020, accepted=True),
                candidate("newer", "Newer", 2023, accepted=True),
            ],
        )
        older_source = store_paper_source(
            database,
            accepted["older"],
            {"url": "https://example.org/older", "pages": [{"page": 1, "text": "Old."}]},
            canvas_id=canvas_id,
        )
        newer_source = store_paper_source(
            database,
            accepted["newer"],
            {"url": "https://example.org/newer", "pages": [{"page": 1, "text": "New."}]},
            canvas_id=canvas_id,
        )

        with pytest.raises(ValueError, match="earlier or foundational"):
            store_relationship(
                database,
                canvas_id,
                {
                    "source_paper_id": accepted["newer"],
                    "target_paper_id": accepted["older"],
                    "type": "extends",
                    "label": "extends",
                    "explanation": "The newer paper extends the older paper.",
                    "evidence_ids": [
                        newer_source["evidence_ids"][0],
                        older_source["evidence_ids"][0],
                    ],
                    "confidence": "high",
                },
            )


def test_source_returns_evidence_only_for_requested_canvas(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        database = client.app.state.database
        first_canvas = client.post(
            "/api/v1/canvases",
            json={"name": "First", "research_goal": "First research map."},
        ).json()["canvas"]["id"]
        second_canvas = client.post(
            "/api/v1/canvases",
            json={"name": "Second", "research_goal": "Second research map."},
        ).json()["canvas"]["id"]
        first = record_discovery_batch(
            database,
            first_canvas,
            None,
            1,
            [candidate("shared", "Shared Paper", 2024, accepted=True)],
        )
        second = record_discovery_batch(
            database,
            second_canvas,
            None,
            1,
            [candidate("shared", "Shared Paper", 2024, accepted=True)],
        )
        assert first["shared"] == second["shared"]

        stored = store_paper_source(
            database,
            first["shared"],
            {
                "url": "https://example.org/shared.pdf",
                "source_type": "pdf",
                "pages": [{"page": 3, "text": "Page-scoped evidence."}],
            },
            canvas_id=first_canvas,
        )
        with database.session_context() as session:
            evidence = session.get(Evidence, stored["evidence_ids"][0])

    assert len(stored["evidence_ids"]) == 1
    assert evidence is not None and evidence.canvas_id == first_canvas
