import asyncio
import json
from pathlib import Path

from research_map_backend.agent_cli import build_parser, run_cli
from research_map_backend.db import Database
from research_map_backend.models import Canvas
from research_map_backend.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'cli.sqlite3'}",
        openai_api_key="test-key",
        trueforge_url="http://trueforge.test",
    )


def test_cli_help_describes_list_and_direct_run() -> None:
    help_text = build_parser().format_help()

    assert "list" in help_text
    assert "run" in help_text
    assert "TrueForge 0.1.4" in help_text


def test_cli_lists_trueforge_agents_as_json(tmp_path: Path, capsys) -> None:
    async def fake_lister(_: Settings):
        return [{"id": "agent-1", "name": "research-map-discovery"}]

    code = asyncio.run(
        run_cli(["list"], settings=_settings(tmp_path), agent_lister=fake_lister)
    )
    line = json.loads(capsys.readouterr().out)

    assert code == 0
    assert line == {
        "type": "agent",
        "payload": {"id": "agent-1", "name": "research-map-discovery"},
    }


def test_cli_runs_selected_agent_with_canvas_context_and_normalized_stream(
    tmp_path: Path, capsys
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Debug", research_goal="Map RAG papers.")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    database.dispose()
    observed = {}

    async def fake_agent_source(
        _: Settings, agent_name: str, prompt: str
    ):
        observed.update(agent_name=agent_name, prompt=prompt)
        yield {"type": "session.created", "session_id": "session-1"}
        yield {
            "type": "model.message.delta",
            "thread_id": "debug",
            "content": "result",
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    code = asyncio.run(
        run_cli(
            ["run", "discovery", "--prompt", "Inspect this.", "--canvas-id", canvas_id],
            settings=settings,
            agent_source=fake_agent_source,
        )
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert code == 0
    assert observed["agent_name"] == "research-map-discovery"
    assert "Inspect this." in observed["prompt"]
    assert "Map RAG papers." in observed["prompt"]
    assert [event["type"] for event in events] == [
        "agent.session.created",
        "agent.message.delta",
        "run.completed",
    ]
