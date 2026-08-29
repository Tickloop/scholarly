from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from research_map_backend.app import create_app
from research_map_backend.models import (
    Canvas,
    CanvasPaper,
    Evidence,
    Paper,
    Relationship,
    Review,
    UserRevision,
)
from research_map_backend.pipeline import SECTION_KEYS
from research_map_backend.research_store import store_relationship, store_review
from research_map_backend.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'manual.sqlite3'}",
        openai_api_key="test",
    )


def _seed(app) -> tuple[str, str, str, str]:
    with app.state.database.session_context() as session:
        canvas = Canvas(name="Manual", research_goal="Map papers")
        first = Paper(
            title="First", normalized_title="first", authors=["A"], year=2020,
            month=1, summary="One", url="https://example.test/first",
        )
        second = Paper(
            title="Second", normalized_title="second", authors=["B"], year=2022,
            month=1, summary="Two", url="https://example.test/second",
        )
        session.add_all([canvas, first, second])
        session.flush()
        session.add_all([
            CanvasPaper(canvas_id=canvas.id, paper_id=first.id, processing_status="reviewed"),
            CanvasPaper(canvas_id=canvas.id, paper_id=second.id, processing_status="reviewed"),
        ])
        review = Review(
            canvas_id=canvas.id,
            paper_id=first.id,
            sections={key: {"text": "Not reported.", "evidence_ids": [], "confidence": "low"} for key in SECTION_KEYS},
            confidence="low",
            author="reviewer",
        )
        relationship = Relationship(
            canvas_id=canvas.id,
            source_paper_id=first.id,
            target_paper_id=second.id,
            type="extends",
            label="Extends",
            explanation="Second extends First.",
        )
        session.add_all([review, relationship])
        session.commit()
        return canvas.id, first.id, second.id, relationship.id


def test_manual_edits_are_protected_revisioned_and_undoable(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        canvas_id, first_id, _, relationship_id = _seed(app)
        paper_edit = client.patch(
            f"/api/v1/canvases/{canvas_id}/papers/{first_id}",
            json={"title": "User title", "x": 42, "y": -7, "pinned": True},
        )
        assert paper_edit.status_code == 200
        paper = paper_edit.json()["papers"][0]
        assert (paper["title"], paper["x"], paper["y"], paper["pinned"]) == (
            "User title", 42, -7, True
        )

        review_edit = client.patch(
            f"/api/v1/canvases/{canvas_id}/papers/{first_id}/review",
            json={"sections": {"coreIdea": "User-protected core idea."}},
        )
        assert review_edit.status_code == 200
        assert review_edit.json()["papers"][0]["review"]["coreIdea"] == "User-protected core idea."
        assert review_edit.json()["papers"][0]["plain_language_summary"] == "Not reported."
        with app.state.database.session_context() as session:
            current = session.scalar(select(Review).where(Review.is_current.is_(True)))
            assert current is not None and current.author == "user" and current.protected
        with pytest.raises(ValueError, match="protected"):
            store_review(
                app.state.database,
                canvas_id,
                first_id,
                {"sections": {key: {"text": "Not reported.", "evidence_ids": [], "confidence": "low"} for key in SECTION_KEYS}},
            )

        relationship_edit = client.patch(
            f"/api/v1/canvases/{canvas_id}/relationships/{relationship_id}",
            json={"label": "User label"},
        )
        assert relationship_edit.status_code == 200
        edited_relationship = relationship_edit.json()["relationships"][0]
        assert edited_relationship["label"] == "User label"
        with app.state.database.session_context() as session:
            current_relationship = session.scalar(
                select(Relationship).where(Relationship.is_current.is_(True))
            )
            assert (
                current_relationship is not None
                and current_relationship.author == "user"
                and current_relationship.protected
            )
            source_evidence = Evidence(
                canvas_id=canvas_id,
                paper_id=first_id,
                owner_type="source",
                owner_id="source-first",
                source_url="https://example.test/first.pdf",
                page=1,
                excerpt="First evidence",
            )
            second_id = current_relationship.target_paper_id
            target_evidence = Evidence(
                canvas_id=canvas_id,
                paper_id=second_id,
                owner_type="source",
                owner_id="source-second",
                source_url="https://example.test/second.pdf",
                page=1,
                excerpt="Second evidence",
            )
            session.add_all([source_evidence, target_evidence])
            session.commit()
            evidence_ids = [source_evidence.id, target_evidence.id]
        with pytest.raises(ValueError, match="protected"):
            store_relationship(
                app.state.database,
                canvas_id,
                {
                    "source_paper_id": first_id,
                    "target_paper_id": second_id,
                    "type": "extends",
                    "label": "Agent replacement",
                    "explanation": "Should not replace the user revision.",
                    "evidence_ids": evidence_ids,
                    "confidence": "high",
                },
            )

        deleted = client.delete(
            f"/api/v1/canvases/{canvas_id}/papers/{first_id}"
        )
        assert deleted.status_code == 200
        hidden = client.get(f"/api/v1/canvases/{canvas_id}").json()
        assert {paper["id"] for paper in hidden["papers"]} == {hidden["papers"][0]["id"]}
        assert first_id not in {paper["id"] for paper in hidden["papers"]}
        assert hidden["relationships"] == []
        restored = client.post(
            f"/api/v1/canvases/{canvas_id}/undo",
            json={"undo_token": deleted.json()["undo_token"]},
        )
        assert restored.status_code == 200
        assert first_id in {paper["id"] for paper in restored.json()["papers"]}
        assert len(restored.json()["relationships"]) == 1

        with app.state.database.session_context() as session:
            revisions = list(session.scalars(select(UserRevision)))
        assert {item.action for item in revisions} == {"update", "delete"}


def test_canvas_rename_and_soft_delete(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        canvas = client.post(
            "/api/v1/canvases", json={"name": "Before", "research_goal": "Goal"}
        ).json()["canvas"]
        renamed = client.patch(
            f"/api/v1/canvases/{canvas['id']}", json={"name": "After"}
        )
        assert renamed.status_code == 200 and renamed.json()["name"] == "After"
        removed = client.delete(f"/api/v1/canvases/{canvas['id']}")
        assert removed.status_code == 204
        assert client.get(f"/api/v1/canvases/{canvas['id']}").status_code == 404
        assert client.get("/api/v1/canvases").json() == []


def test_failed_paper_retry_and_explicit_review_regeneration_reuse_stable_node(
    tmp_path: Path,
) -> None:
    observed: list[bool] = []

    async def fake_link_pipeline(**kwargs):
        observed.append(kwargs["replace_protected_review"])
        return {
            "paper_id": kwargs["placeholder_paper_id"],
            "completed_reviews": 1,
            "relationships": 0,
        }

    app = create_app(_settings(tmp_path), link_pipeline_runner=fake_link_pipeline)
    with TestClient(app) as client:
        canvas_id, first_id, _, _ = _seed(app)
        with app.state.database.session_context() as session:
            membership = session.scalar(
                select(CanvasPaper).where(CanvasPaper.paper_id == first_id)
            )
            assert membership is not None
            membership.processing_status = "failed"
            membership.error = "Previous failure"
            session.commit()

        retried = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/{first_id}/retry"
        )
        regenerated = client.post(
            f"/api/v1/canvases/{canvas_id}/papers/{first_id}/review/regenerate"
        )
        assert retried.status_code == regenerated.status_code == 202
        assert retried.json()["paper_id"] == regenerated.json()["paper_id"] == first_id
        client.get(f"/api/v1/runs/{retried.json()['id']}/events")
        client.get(f"/api/v1/runs/{regenerated.json()['id']}/events")

    assert observed == [False, True]
