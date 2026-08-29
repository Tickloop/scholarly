import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from research_map_backend.app import create_app
from research_map_backend.cli import build_cli
from research_map_backend.models import (
    AgentRun,
    AgentRunEvent,
    Canvas,
    CanvasPaper,
    Job,
    Paper,
)
from research_map_backend.research_store import (
    record_discovery_batch,
    store_paper_source,
    store_relationship,
    store_review,
)
from research_map_backend.settings import Settings


runner = CliRunner()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'cli.sqlite3'}",
        openai_api_key="test-key",
        retry_base_seconds=0,
    )


def _cli(client: TestClient):
    return build_cli(client_factory=lambda _api_url: client)


def _candidate(key: str, title: str, year: int) -> dict:
    return {
        "candidate_id": key,
        "title": title,
        "authors": ["Researcher"],
        "year": year,
        "month": 1,
        "abstract": f"Evidence for {title}.",
        "url": f"https://example.test/{key}",
        "score": {"relevance": 25},
        "total_score": 80,
        "accepted": True,
        "reason": "Relevant verified paper.",
    }


def _review(evidence_id: str) -> dict:
    keys = {
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
    }
    return {
        "sections": {
            key: {
                "text": f"Supported {key}.",
                "evidence_ids": [evidence_id],
                "confidence": "medium",
            }
            for key in keys
        },
        "confidence": "medium",
    }


def test_help_exposes_complete_command_groups() -> None:
    result = runner.invoke(build_cli(), ["--help"])

    assert result.exit_code == 0
    for name in ["canvas", "build", "paper", "agent", "run", "layout", "shell"]:
        assert name in result.stdout

    expected = {
        "canvas": ["create", "list", "show", "rename", "delete"],
        "build": ["start", "watch", "cancel", "restart", "status"],
        "paper": ["add-url", "status"],
        "agent": ["main", "discovery", "reviewer", "connection"],
        "run": ["list", "inspect", "retry", "cancel", "events"],
        "layout": ["reset"],
    }
    for group, names in expected.items():
        group_help = runner.invoke(build_cli(), [group, "--help"])
        assert group_help.exit_code == 0
        assert all(name in group_help.stdout for name in names)


def test_json_canvas_commands_and_api_failure(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), pipeline_runner=None)
    with TestClient(app) as client:
        cli = _cli(client)
        created = runner.invoke(
            cli,
            ["--json", "canvas", "create", "--name", "CLI", "--goal", "Map RAG."],
        )
        canvas_id = json.loads(created.stdout)["canvas"]["id"]
        listed = runner.invoke(cli, ["--json", "canvas", "list"])
        missing = runner.invoke(cli, ["canvas", "show", "missing"])
        renamed = runner.invoke(cli, ["--json", "canvas", "rename", canvas_id, "Renamed"])
        deleted = runner.invoke(cli, ["--json", "canvas", "delete", canvas_id])
        after_delete = runner.invoke(cli, ["--json", "canvas", "list"])

    assert created.exit_code == 0
    assert json.loads(listed.stdout)[0]["id"] == canvas_id
    assert json.loads(renamed.stdout)["name"] == "Renamed"
    assert json.loads(deleted.stdout)["status"] == "deleted"
    assert json.loads(after_delete.stdout) == []
    assert missing.exit_code == 1
    assert "HTTP 404" in missing.stderr


def test_stream_cancel_and_raw_event_replay(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), pipeline_runner=None)
    with TestClient(app) as client:
        database = app.state.database
        with database.session_context() as session:
            canvas = Canvas(name="Cancel", research_goal="Cancel this.")
            session.add(canvas)
            session.flush()
            paper = Paper(
                title="Direct paper",
                normalized_title="direct paper",
                authors=[],
                year=2024,
                month=1,
                summary="",
                url="https://example.test/direct-paper",
            )
            session.add(paper)
            session.flush()
            session.add(
                CanvasPaper(
                    canvas_id=canvas.id,
                    paper_id=paper.id,
                    origin="user",
                    processing_status="completed",
                )
            )
            run = AgentRun(canvas_id=canvas.id, status="queued")
            session.add(run)
            session.flush()
            session.add(
                Job(
                    canvas_id=canvas.id,
                    run_id=run.id,
                    paper_id=paper.id,
                    kind="direct_agent",
                    payload={"prompt": "Inspect", "paper_ids": [paper.id]},
                )
            )
            session.add(
                AgentRunEvent(
                    run_id=run.id,
                    canvas_id=canvas.id,
                    type="run.started",
                    payload={"agent": "main"},
                )
            )
            session.commit()
            run_id, paper_id = run.id, paper.id
        cli = _cli(client)
        cancelled = runner.invoke(cli, ["--json", "run", "cancel", run_id])
        replayed = runner.invoke(cli, ["--json", "run", "events", run_id])
        watched = runner.invoke(cli, ["build", "watch", run_id])

    assert cancelled.exit_code == 0
    assert json.loads(cancelled.stdout)["status"] == "cancelled"
    assert json.loads(cancelled.stdout)["paper_id"] == paper_id
    event_types = [json.loads(line)["type"] for line in replayed.stdout.splitlines()]
    assert event_types == ["run.started", "run.cancelled"]
    assert watched.exit_code == 0
    assert "[run.cancelled] Cancelled by user." in watched.stdout


def test_direct_agents_are_canvas_scoped_and_stream_normalized_events(tmp_path: Path) -> None:
    observed = []

    async def direct_source(_settings: Settings, agent_name: str, prompt: str):
        observed.append((agent_name, prompt))
        yield {"type": "session.created", "session_id": "session-1"}
        yield {"type": "turn.created", "turn_id": "turn-1"}
        yield {"type": "model.message.delta", "thread_id": "direct", "content": "done"}
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    app = create_app(
        _settings(tmp_path), pipeline_runner=None, direct_agent_source=direct_source
    )
    with TestClient(app) as client:
        first_canvas = client.post(
            "/api/v1/canvases", json={"name": "First", "research_goal": "First."}
        ).json()["canvas"]["id"]
        second_canvas = client.post(
            "/api/v1/canvases", json={"name": "Second", "research_goal": "Second."}
        ).json()["canvas"]["id"]
        accepted = record_discovery_batch(
            app.state.database,
            second_canvas,
            None,
            1,
            [_candidate("other", "Other paper", 2024)],
        )
        paper_id = accepted["other"]
        cli = _cli(client)
        rejected = runner.invoke(
            cli,
            [
                "agent",
                "reviewer",
                first_canvas,
                "--paper-id",
                paper_id,
                "--prompt",
                "Review it.",
            ],
        )
        accepted_run = runner.invoke(
            cli,
            ["agent", "discovery", first_canvas, "--prompt", "Inspect coverage."],
        )

    assert rejected.exit_code == 1
    assert "must belong to the active canvas" in rejected.stderr
    assert accepted_run.exit_code == 0
    assert "[agent.message.delta] done" in accepted_run.stdout
    assert len(observed) == 1
    assert observed[0][0] == "research-map-discovery"
    assert first_canvas in observed[0][1]
    assert "preloaded application MCP tools" in observed[0][1]


def test_paper_and_run_status_commands_use_durable_api_records(tmp_path: Path) -> None:
    async def link_runner(**kwargs):
        return {
            "paper_id": kwargs["placeholder_paper_id"],
            "completed_reviews": 0,
            "relationships": 0,
        }

    app = create_app(
        _settings(tmp_path), pipeline_runner=None, link_pipeline_runner=link_runner
    )
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Links", "research_goal": "Map linked work."},
        ).json()["canvas"]["id"]
        cli = _cli(client)
        added = runner.invoke(
            cli,
            [
                "--json",
                "paper",
                "add-url",
                canvas_id,
                "https://arxiv.org/abs/2005.11401",
            ],
        )
        run = json.loads(added.stdout)
        paper = runner.invoke(
            cli, ["--json", "paper", "status", canvas_id, run["paper_id"]]
        )
        inspected = runner.invoke(cli, ["--json", "run", "inspect", run["id"]])
        listed = runner.invoke(
            cli, ["--json", "run", "list", "--canvas-id", canvas_id]
        )
        status = runner.invoke(cli, ["--json", "build", "status", run["id"]])

    assert added.exit_code == 0
    assert json.loads(paper.stdout)["id"] == run["paper_id"]
    assert json.loads(inspected.stdout)["id"] == run["id"]
    assert json.loads(listed.stdout)[0]["id"] == run["id"]
    assert json.loads(status.stdout)["status"] in {"queued", "running", "completed"}


def test_cli_paper_add_url_inherits_api_http_rejection(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), pipeline_runner=None)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "CLI secure", "research_goal": "Validate paper links."},
        ).json()["canvas"]["id"]
        result = runner.invoke(
            _cli(client),
            ["paper", "add-url", canvas_id, "http://publisher.example/paper"],
        )

    assert result.exit_code == 1
    assert "HTTP 422" in result.stderr
    assert "must use HTTPS" in result.stderr


@pytest.mark.parametrize("paper_reference", ["10.1000/cli-paper", "arXiv:2005.11401"])
def test_cli_paper_add_url_inherits_api_identifier_acceptance(
    tmp_path: Path, paper_reference: str
) -> None:
    async def link_pipeline(**kwargs):
        return {
            "paper_id": kwargs["placeholder_paper_id"],
            "completed_reviews": 0,
            "relationships": 0,
        }

    app = create_app(_settings(tmp_path), link_pipeline_runner=link_pipeline)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "CLI identifiers", "research_goal": "Validate identifiers."},
        ).json()["canvas"]["id"]
        result = runner.invoke(
            _cli(client),
            ["--json", "paper", "add-url", canvas_id, paper_reference],
        )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["paper_id"]


def test_terminal_completes_stubbed_goal_to_review_and_edge_workflow(tmp_path: Path) -> None:
    async def main_source(_settings: Settings, _goal: str):
        yield {
            "type": "model.message.delta",
            "content": json.dumps(
                {
                    "brief_markdown": "A concise brief.",
                    "canonical_seed_titles": [
                        "Dense Passage Retrieval for Open-Domain Question Answering",
                        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                    ],
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done", "required_actions": []}}

    async def pipeline_runner(**kwargs):
        database = kwargs["database"]
        canvas_id = kwargs["canvas_id"]
        run_id = kwargs["run_id"]
        accepted = record_discovery_batch(
            database,
            canvas_id,
            run_id,
            1,
            [
                _candidate("old", "Older paper", 2020),
                _candidate("new", "Newer paper", 2024),
            ],
        )
        evidence = {}
        for key, paper_id in accepted.items():
            stored = store_paper_source(
                database,
                paper_id,
                {
                    "url": f"https://example.test/{key}",
                    "source_type": "abstract",
                    "retrieval_status": "abstract_fallback",
                    "pages": [{"page": None, "text": f"Evidence {key}."}],
                },
                canvas_id=canvas_id,
            )
            evidence[key] = stored["evidence_ids"][0]
            store_review(database, canvas_id, paper_id, _review(evidence[key]))
        store_relationship(
            database,
            canvas_id,
            {
                "source_paper_id": accepted["old"],
                "target_paper_id": accepted["new"],
                "type": "extends",
                "label": "extends",
                "explanation": "The newer paper extends the older paper.",
                "evidence_ids": [evidence["old"], evidence["new"]],
                "confidence": "high",
            },
        )
        return {"status": "completed", "accepted_papers": 2, "relationships": 1}

    app = create_app(
        _settings(tmp_path),
        agent_event_source=main_source,
        pipeline_runner=pipeline_runner,
    )
    with TestClient(app) as client:
        cli = _cli(client)
        created = runner.invoke(
            cli,
            ["--json", "canvas", "create", "--name", "Flow", "--goal", "Map papers."],
        )
        canvas_id = json.loads(created.stdout)["canvas"]["id"]
        built = runner.invoke(cli, ["build", "start", canvas_id, "--watch"])
        shown = runner.invoke(cli, ["--json", "canvas", "show", canvas_id])
        snapshot = json.loads(shown.stdout)

    assert built.exit_code == 0
    assert "[run.completed]" in built.stdout
    assert len(snapshot["papers"]) == 2
    assert all(paper["review"] for paper in snapshot["papers"])
    assert len(snapshot["relationships"]) == 1


def test_layout_reset_and_interactive_shell_use_api_state(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), pipeline_runner=None)
    with TestClient(app) as client:
        canvas_id = client.post(
            "/api/v1/canvases",
            json={"name": "Layout", "research_goal": "Map a timeline."},
        ).json()["canvas"]["id"]
        record_discovery_batch(
            app.state.database,
            canvas_id,
            None,
            1,
            [
                _candidate("older", "Older", 2020),
                _candidate("newer", "Newer", 2024),
            ],
        )
        cli = _cli(client)
        reset = runner.invoke(cli, ["--json", "layout", "reset", canvas_id])
        shell = runner.invoke(cli, ["shell", canvas_id], input="show\nquit\n")
        positions = {
            paper["title"]: (paper["x"], paper["y"])
            for paper in json.loads(reset.stdout)["papers"]
        }

    assert reset.exit_code == 0
    assert positions == {"Older": (0.0, 0.0), "Newer": (0.0, 0.0)}
    assert shell.exit_code == 0
    assert "research-map:" in shell.stdout
    assert "Layout" in shell.stdout
