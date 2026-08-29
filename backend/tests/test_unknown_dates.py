from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from research_map_backend.app import create_app
from research_map_backend.db import Database
from research_map_backend.models import Canvas, CanvasPaper, Paper
from research_map_backend.paper_links import (
    attach_canvas_paper,
    create_canvas_paper_placeholder,
)
from research_map_backend.research_store import (
    get_canvas_snapshot,
    record_discovery_batch,
)
from research_map_backend.settings import Settings


def _settings(tmp_path: Path, name: str) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / name}",
        openai_api_key="test",
        trueforge_url="http://127.0.0.1:1",
    )


def test_placeholder_and_unknown_date_dedupe_stay_nullable(tmp_path: Path) -> None:
    database = Database(_settings(tmp_path, "unknown-store.sqlite3"))
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Unknown dates", research_goal="Map undated papers")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id

    placeholder_id = create_canvas_paper_placeholder(
        database,
        canvas_id,
        "https://doi.org/10.1000/unknown-date",
        x=42,
        y=73,
    )
    with database.session_context() as session:
        placeholder = session.get(Paper, placeholder_id)
        snapshot = get_canvas_snapshot(session, canvas_id)
    assert placeholder is not None
    assert (placeholder.year, placeholder.month) == (None, None)
    assert (snapshot.papers[0].year, snapshot.papers[0].month) == (None, None)

    candidate = {
        "title": "Verified but undated paper",
        "authors": ["A. Researcher"],
        "year": None,
        "month": None,
        "abstract": "Verified metadata without a publication date.",
        "url": "https://example.test/undated-one",
    }
    first_id = attach_canvas_paper(database, canvas_id, candidate)
    second_id = attach_canvas_paper(
        database,
        canvas_id,
        {**candidate, "url": "https://example.test/undated-two"},
    )
    database.dispose()

    assert first_id == second_id


def test_discovery_store_deduplicates_two_unknown_year_candidates(tmp_path: Path) -> None:
    database = Database(_settings(tmp_path, "unknown-discovery.sqlite3"))
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Unknown discovery", research_goal="Map undated work")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id

    candidate = {
        "candidate_id": "candidate-one",
        "title": "Undated discovery candidate",
        "authors": [],
        "year": None,
        "month": None,
        "abstract": "",
        "url": "https://example.test/discovery-one",
        "score": {},
        "total_score": 80,
        "accepted": True,
        "reason": "Relevant verified work.",
    }
    first = record_discovery_batch(database, canvas_id, None, 1, [candidate])
    second = record_discovery_batch(
        database,
        canvas_id,
        None,
        2,
        [{**candidate, "candidate_id": "candidate-two", "url": "https://example.test/discovery-two"}],
    )
    with database.session_context() as session:
        paper_count = session.scalar(select(func.count()).select_from(Paper))
        stored = session.scalar(select(Paper))
    database.dispose()

    assert first["candidate-one"] == second["candidate-two"]
    assert paper_count == 1
    assert stored is not None and (stored.year, stored.month) == (None, None)


def test_snapshot_api_and_manual_edit_round_trip_null_dates(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, "unknown-api.sqlite3"))
    with TestClient(app) as client:
        with app.state.database.session_context() as session:
            canvas = Canvas(name="Nullable API", research_goal="Map dates")
            paper = Paper(
                title="Known date paper",
                normalized_title="known date paper",
                authors=[],
                year=2020,
                month=5,
                summary="",
                url="https://example.test/known",
            )
            session.add_all([canvas, paper])
            session.flush()
            session.add(
                CanvasPaper(
                    canvas_id=canvas.id,
                    paper_id=paper.id,
                    processing_status="reviewed",
                )
            )
            session.commit()
            canvas_id, paper_id = canvas.id, paper.id

        response = client.patch(
            f"/api/v1/canvases/{canvas_id}/papers/{paper_id}",
            json={"year": None, "month": None},
        )
        assert response.status_code == 200
        assert response.json()["papers"][0]["year"] is None
        assert response.json()["papers"][0]["month"] is None

        reloaded = client.get(f"/api/v1/canvases/{canvas_id}")
        assert reloaded.status_code == 200
        assert reloaded.json()["papers"][0]["year"] is None
        assert reloaded.json()["papers"][0]["month"] is None
