from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import io
import sqlite3
import sys
import tarfile

from fastapi.testclient import TestClient

from research_map_backend.app import create_app
from research_map_backend.data_cli import DataOperationError, backup_data, restore_data
from research_map_backend.diagnostics import public_diagnostic_value
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    CanvasPaper,
    Paper,
    ToolInvocation,
)
from research_map_backend.settings import Settings
from research_map_backend.structured_logging import JsonFormatter
import logging


def settings(tmp_path: Path, *, openai: bool = False) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'research_map.sqlite3'}",
        openai_api_key="test" if openai else None,
    )


def add_paper(client: TestClient, canvas_id: str, title: str, *, pinned: bool = True) -> str:
    with client.app.state.database.session_context() as session:
        paper = Paper(
            title=title,
            normalized_title=title.lower(),
            authors=[],
            year=2024,
            month=1,
            summary="",
            url=f"https://example.test/{title}",
        )
        session.add(paper)
        session.flush()
        session.add(
            CanvasPaper(
                canvas_id=canvas_id,
                paper_id=paper.id,
                x=10,
                y=20,
                pinned=pinned,
                origin="user",
                processing_status="completed",
            )
        )
        session.commit()
        return paper.id


def test_bulk_layout_is_atomic_canvas_scoped_and_reset_only_unpins(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post(
            "/api/v1/canvases", json={"name": "One", "research_goal": "Goal one"}
        ).json()["canvas"]["id"]
        second = client.post(
            "/api/v1/canvases", json={"name": "Two", "research_goal": "Goal two"}
        ).json()["canvas"]["id"]
        first_paper = add_paper(client, first, "First")
        second_paper = add_paper(client, second, "Second")

        updated = client.put(
            f"/api/v1/canvases/{first}/layout",
            json={
                "positions": [
                    {"paper_id": first_paper, "x": 110, "y": 220, "pinned": True}
                ]
            },
        )
        rejected = client.put(
            f"/api/v1/canvases/{first}/layout",
            json={
                "positions": [
                    {"paper_id": first_paper, "x": 999, "y": 999, "pinned": False},
                    {"paper_id": second_paper, "x": 0, "y": 0, "pinned": False},
                ]
            },
        )
        after_rejection = client.get(f"/api/v1/canvases/{first}").json()
        reset = client.post(f"/api/v1/canvases/{first}/layout/reset")
        other = client.get(f"/api/v1/canvases/{second}").json()

    assert updated.status_code == 200
    updated_paper = updated.json()["papers"][0]
    assert updated_paper["id"] == first_paper
    assert updated_paper["x"] == 110.0
    assert updated_paper["y"] == 220.0
    assert updated_paper["pinned"] is True
    assert rejected.status_code == 422
    assert after_rejection["papers"][0]["x"] == 110.0
    assert reset.json()["papers"][0]["pinned"] is False
    assert reset.json()["papers"][0]["x"] == 110.0
    assert reset.json()["papers"][0]["y"] == 220.0
    assert other["papers"][0]["pinned"] is True


def test_run_timeline_combines_events_tools_duration_and_public_usage(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        canvas_id = client.post(
            "/api/v1/canvases", json={"name": "Timeline", "research_goal": "Goal"}
        ).json()["canvas"]["id"]
        with client.app.state.database.session_context() as session:
            run = AgentRun(
                canvas_id=canvas_id,
                status="completed",
                created_at=datetime.now(UTC) - timedelta(seconds=2),
                updated_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=canvas_id,
                    type="run.completed",
                    payload={
                        "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
                        "reasoning_content": "must not be exposed",
                    },
                )
            )
            session.add(
                ToolInvocation(
                    canvas_id=canvas_id,
                    run_id=run.id,
                    tool_name="get_canvas",
                    arguments={
                        "canvas_id": canvas_id,
                        "nested": {"api_key": "must-not-leak"},
                    },
                    result={"ok": True, "reasoning_content": "must-not-be-exposed"},
                    error=(
                        "provider HTTP 429 authorization=Bearer sk-or-v1-live "
                        "reasoning_content=private retry_after=3"
                    ),
                    status="completed",
                    completed_at=datetime.now(UTC),
                )
            )
            session.commit()
            run_id = run.id
        response = client.get(f"/api/v1/runs/{run_id}/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["duration_ms"] >= 1900
    assert body["usage"] == {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}
    assert body["tool_invocations"][0]["tool_name"] == "get_canvas"
    assert body["tool_invocations"][0]["arguments"]["nested"]["api_key"] == "[redacted]"
    assert body["tool_invocations"][0]["result"]["reasoning_content"] == "[redacted]"
    assert "must not be exposed" not in response.text
    assert "must-not-leak" not in response.text
    assert "must-not-be-exposed" not in response.text
    assert "sk-or-v1-live" not in response.text
    assert "private" not in response.text
    assert "provider HTTP 429" in body["tool_invocations"][0]["error"]
    assert "retry_after=3" in body["tool_invocations"][0]["error"]


def test_diagnostic_string_and_log_traceback_redaction_keeps_useful_context() -> None:
    value = public_diagnostic_value(
        {
            "error": "HTTP 429 api_key=sk-or-v1-live reasoning_content=private retry_after=3",
            "serialized": '{"authorization":"Bearer live-secret","status":503}',
        }
    )
    assert value["error"] == (
        "HTTP 429 api_key=[redacted] reasoning_content=[redacted] retry_after=3"
    )
    assert value["serialized"] == '{"authorization":"[redacted]","status":503}'

    try:
        raise RuntimeError(
            "provider failed Authorization: Bearer sk-or-v1-live retry_after=3"
        )
    except RuntimeError:
        record = logging.LogRecord(
            "research_map.runs",
            logging.ERROR,
            __file__,
            1,
            "HTTP 429 api_key=sk-or-v1-live retry_after=3",
            (),
            sys.exc_info(),
        )
    output = JsonFormatter().format(record)
    assert "sk-or-v1-live" not in output
    assert "HTTP 429" in output
    assert "retry_after=3" in output


def test_legacy_event_payload_is_redacted_in_event_log_and_sse(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        canvas_id = client.post(
            "/api/v1/canvases", json={"name": "Legacy", "research_goal": "Goal"}
        ).json()["canvas"]["id"]
        with client.app.state.database.session_context() as session:
            run = AgentRun(canvas_id=canvas_id, status="completed")
            session.add(run)
            session.flush()
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=canvas_id,
                    type="run.completed",
                    payload={
                        "provider": "openalex",
                        "retry_after": 3,
                        "nested": {
                            "api_key": "sk-or-v1-legacy-secret",
                            "reasoning_content": "private chain",
                        },
                        "detail": (
                            "provider HTTP 429 authorization=Bearer legacy-secret "
                            "retry_after=3"
                        ),
                    },
                )
            )
            session.commit()
            run_id = run.id

        event_log = client.get(f"/api/v1/runs/{run_id}/event-log")
        stream = client.get(f"/api/v1/runs/{run_id}/events")

    assert event_log.status_code == 200
    payload = event_log.json()[0]["payload"]
    assert payload["provider"] == "openalex"
    assert payload["retry_after"] == 3
    assert payload["nested"] == {
        "api_key": "[redacted]",
        "reasoning_content": "[redacted]",
    }
    assert "legacy-secret" not in event_log.text
    assert "private chain" not in event_log.text
    assert stream.status_code == 200
    assert "openalex" in stream.text
    assert "retry_after" in stream.text
    assert "legacy-secret" not in stream.text
    assert "private chain" not in stream.text


def test_dependency_health_contract(tmp_path: Path, monkeypatch) -> None:
    async def healthy(_settings):
        return {
            "status": "degraded",
            "academic_apis": {
                "arxiv": "ok",
                "acl_anthology": "throttled",
            },
            "local_storage": {
                "status": "ok",
                "data_bytes": 100,
                "free_bytes": 200,
                "total_bytes": 300,
            },
        }

    monkeypatch.setattr("research_map_backend.api.routes.dependency_health", healthy)
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/health/dependencies")

    assert response.status_code == 200
    assert response.json()["academic_apis"]["acl_anthology"] == "throttled"
    assert response.json()["local_storage"]["free_bytes"] == 200


def test_backup_restore_round_trip_and_requires_force(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    app_settings.data_dir.mkdir(parents=True)
    database_path = app_settings.data_dir / "research_map.sqlite3"
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        database.execute("INSERT INTO marker VALUES ('before')")
    source_file = app_settings.data_dir / "papers" / "paper.txt"
    source_file.parent.mkdir()
    source_file.write_text("stored source", encoding="utf-8")
    archive = backup_data(app_settings, tmp_path / "backup.tar.gz")
    source_file.write_text("changed", encoding="utf-8")
    with sqlite3.connect(database_path) as database:
        database.execute("UPDATE marker SET value='after'")

    try:
        restore_data(app_settings, archive, force=False)
    except DataOperationError as error:
        assert "--force" in str(error)
    else:
        raise AssertionError("restore should require --force")
    safety = restore_data(app_settings, archive, force=True)

    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT value FROM marker").fetchone() == ("before",)
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert source_file.read_text(encoding="utf-8") == "stored source"
    assert safety.is_dir()


def test_restore_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("../outside")
        content = b"unsafe"
        member.size = len(content)
        output.addfile(member, io.BytesIO(content))
    try:
        restore_data(settings(tmp_path), archive, force=True)
    except DataOperationError as error:
        assert "unsafe path" in str(error)
    else:
        raise AssertionError("unsafe archive should be rejected")


def test_direct_agent_run_uses_durable_api_and_replayable_events(tmp_path: Path) -> None:
    async def direct_source(_settings, agent_name: str, prompt: str):
        assert agent_name == "research-map-discovery"
        assert "scoped debugging turn" in prompt
        yield {"type": "session.created", "session_id": "session-7"}
        yield {"type": "turn.created", "turn_id": "turn-7", "thread_id": None}
        yield {"type": "model.message.delta", "content": "done", "thread_id": "main"}
        yield {
            "type": "turn.done",
            "state": {
                "status": "done",
                "required_actions": [],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
                "reasoning_content": "private",
            },
        }

    app = create_app(settings(tmp_path, openai=True), direct_agent_source=direct_source)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases", json={"name": "Direct", "research_goal": "Goal"}
        ).json()["canvas"]["id"]
        started = client.post(
            f"/api/v1/canvases/{canvas_id}/agents/runs",
            json={"agent": "discovery", "prompt": "Find papers", "paper_ids": []},
        )
        run_id = started.json()["id"]
        stream = client.get(f"/api/v1/runs/{run_id}/events")
        timeline = client.get(f"/api/v1/runs/{run_id}/timeline")

    assert started.status_code == 202
    assert "event: run.started" in stream.text
    assert "event: run.completed" in stream.text
    assert timeline.json()["run"]["trueforge_session_id"] == "session-7"
    assert timeline.json()["run"]["trueforge_turn_id"] == "turn-7"
    assert timeline.json()["usage"] == {
        "input_tokens": 8,
        "output_tokens": 2,
        "total_tokens": 10,
    }
    assert "private" not in timeline.text


def test_structured_log_context_and_graceful_app_shutdown(tmp_path: Path) -> None:
    record = logging.LogRecord(
        "research_map.runs", logging.INFO, __file__, 1, "stored", (), None
    )
    record.canvas_id = "canvas-7"
    record.run_id = "run-7"
    record.trueforge_turn_id = "turn-7"
    output = JsonFormatter().format(record)
    assert '"canvas_id": "canvas-7"' in output
    assert '"run_id": "run-7"' in output
    assert '"trueforge_turn_id": "turn-7"' in output

    app = create_app(settings(tmp_path))
    with TestClient(app):
        coordinator = app.state.run_coordinator
        assert coordinator.stopping is False
    assert coordinator.stopping is True
    assert coordinator._workers == []
    assert coordinator._active_runs == {}
