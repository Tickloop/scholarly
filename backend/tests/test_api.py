import json
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select
import httpx
import pytest
from pydantic import ValidationError

from research_map_backend.app import create_app
from research_map_backend.models import AgentRun, AgentRunEvent
from research_map_backend.runs import AgentEventSource
from research_map_backend.settings import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_concurrency", 4),
        ("reviewer_concurrency", 3),
        ("discovery_max_batches", 6),
        ("autonomous_paper_limit", 51),
        ("upstream_max_attempts", 4),
        ("upstream_max_retry_delay_seconds", 30.01),
    ],
)
def test_settings_reject_values_above_hard_orchestration_ceilings(
    field: str, value: int | float
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def make_client(
    tmp_path: Path,
    *,
    openai_api_key: str | None = None,
    brightdata_api_key: str | None = None,
    agent_event_source: AgentEventSource | None = None,
) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        openai_api_key=openai_api_key,
        brightdata_api_key=brightdata_api_key,
        trueforge_url="http://127.0.0.1:1",
    )
    return TestClient(
        create_app(settings, agent_event_source=agent_event_source)
    )


def test_health_reports_database_status(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with client:
        response = client.get("/api/v1/health")
        with client.app.state.database.engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["api"] == "ok"
    assert response.json()["database"] == "ok"
    assert response.json()["trueforge"] == "unavailable"
    assert response.json()["required_configuration"] == "missing_openai_api_key"
    assert response.json()["openai_configured"] is False
    assert response.json()["brightdata_api_key_configured"] is False
    assert response.json()["brightdata_mcp_configured"] is False
    assert response.json()["research_providers"] == {
        "academic_metadata_apis": "enabled",
        "brightdata_mcp": "missing_api_key",
        "web_search": "brightdata_mcp_only",
        "web_scrape": "brightdata_mcp_only",
        "model_inference": "openai_via_trueforge",
    }
    assert journal_mode == "wal"


def test_health_reports_openai_presence_without_secret_value(tmp_path: Path) -> None:
    secret = "openai-live-secret-must-not-leak"
    with make_client(tmp_path, openai_api_key=secret) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["openai_configured"] is True
    assert response.json()["required_configuration"] == "missing_brightdata_api_key"
    assert secret not in response.text


def test_health_reports_brightdata_mcp_readiness_without_secret(
    tmp_path: Path,
) -> None:
    secret = "bright-live-secret-must-not-leak"
    with make_client(
        tmp_path,
        openai_api_key="configured",
        brightdata_api_key=secret,
    ) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["brightdata_api_key_configured"] is True
    assert body["brightdata_mcp_configured"] is True
    assert body["research_providers"]["brightdata_mcp"] == "configured_active"
    assert secret not in response.text


def test_cors_tracks_overridden_local_ui_port(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'cors.sqlite3'}",
        trueforge_url="http://127.0.0.1:1",
        ui_port=15273,
    )
    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://127.0.0.1:15273",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:15273"


def test_health_reports_ready_trueforge_and_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    class ReadyClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            return httpx.Response(200, request=httpx.Request("GET", url))

    with make_client(
        tmp_path,
        openai_api_key="configured",
        brightdata_api_key="configured",
    ) as client:
        monkeypatch.setattr(
            "research_map_backend.api.routes.httpx.AsyncClient", ReadyClient
        )
        response = client.get("/api/v1/health")

    assert response.json()["status"] == "ok"
    assert response.json()["trueforge"] == "ok"
    assert response.json()["required_configuration"] == "ok"


def test_create_list_and_get_canvas(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/canvases",
            json={
                "name": "Language model agents",
                "research_goal": "Map reliable agent evaluation research.",
            },
        )
        canvas_id = created.json()["canvas"]["id"]

        listed = client.get("/api/v1/canvases")
        fetched = client.get(f"/api/v1/canvases/{canvas_id}")
        missing = client.get("/api/v1/canvases/missing")

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [canvas["id"] for canvas in listed.json()] == [canvas_id]
    assert fetched.status_code == 200
    assert fetched.json()["canvas"]["research_goal"] == "Map reliable agent evaluation research."
    assert fetched.json()["papers"] == []
    assert fetched.json()["relationships"] == []
    assert missing.status_code == 404


async def fake_agent_events(_: Settings, research_goal: str):
    assert research_goal == "Map reliable agent evaluation research."
    yield {"type": "session.created", "session_id": "tf-session"}
    yield {
        "type": "turn.created",
        "turn_id": "tf-turn",
        "thread_id": None,
    }
    yield {
        "type": "model.message.delta",
        "thread_id": "main",
        "content": json.dumps(
            {
                "brief_markdown": "Research brief",
                "canonical_seed_titles": [
                    "Reliable Agent Evaluation with Verified Research Evidence",
                    "Benchmarking Autonomous Research Agents for Complex Workflows",
                ],
            }
        ),
        "reasoning_content": "must not be persisted",
    }
    yield {
        "type": "turn.done",
        "state": {"status": "done", "required_actions": []},
    }


def test_build_persists_and_replays_normalized_sse(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        openai_api_key="test-key",
        agent_event_source=fake_agent_events,
    ) as client:
        canvas = client.post(
            "/api/v1/canvases",
            json={
                "name": "Agents",
                "research_goal": "Map reliable agent evaluation research.",
            },
        ).json()["canvas"]
        started = client.post(f"/api/v1/canvases/{canvas['id']}/builds")
        run = started.json()
        streamed = client.get(f"/api/v1/runs/{run['id']}/events")

        with client.app.state.database.session_context() as session:
            stored_run = session.get(AgentRun, run["id"])
            stored_events = list(
                session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == run["id"])
                    .order_by(AgentRunEvent.id)
                )
            )

        replayed = client.get(
            f"/api/v1/runs/{run['id']}/events",
            headers={"Last-Event-ID": str(stored_events[1].id)},
        )

    assert started.status_code == 202
    assert run["canvas_id"] == canvas["id"]
    assert streamed.status_code == 200
    assert "event: run.started" in streamed.text
    assert "event: agent.message.delta" in streamed.text
    assert "event: run.completed" in streamed.text
    assert "must not be persisted" not in streamed.text
    assert stored_run is not None
    assert stored_run.status == "completed"
    assert stored_run.trueforge_session_id == "tf-session"
    assert stored_run.trueforge_turn_id == "tf-turn"
    assert "event: run.started" not in replayed.text
    assert "event: run.completed" in replayed.text


def test_build_streams_clear_missing_openai_failure(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Agents", "research_goal": "Map agent research."},
        ).json()["canvas"]["id"]
        run = client.post(f"/api/v1/canvases/{canvas_id}/builds").json()
        streamed = client.get(f"/api/v1/runs/{run['id']}/events")

    assert "event: run.failed" in streamed.text
    assert "openai_not_configured" in streamed.text
    assert "OPENAI_API_KEY is not configured" in streamed.text
