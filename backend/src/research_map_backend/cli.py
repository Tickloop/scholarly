from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx
import typer
from rich.console import Console
from rich.table import Table


class HttpClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...
    def stream(self, method: str, url: str, **kwargs: Any) -> Any: ...


ClientFactory = Callable[[str], HttpClient]


def _default_client(api_url: str) -> httpx.Client:
    return httpx.Client(base_url=api_url, timeout=httpx.Timeout(900.0, connect=5.0))


@dataclass
class CliState:
    api_url: str
    json_output: bool
    client: HttpClient
    console: Console

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            self.fail(f"API request failed: {error}")
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = response.text or f"HTTP {response.status_code}"
            self.fail(f"HTTP {response.status_code}: {detail}")
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError:
            self.fail("The API returned invalid JSON.")

    def emit(self, value: Any, *, title: str | None = None) -> None:
        if self.json_output:
            typer.echo(json.dumps(value, ensure_ascii=False, default=str))
            return
        if isinstance(value, list):
            if not value:
                self.console.print(f"{title or 'Result'}: none")
                return
            if all(isinstance(item, dict) for item in value):
                keys = _table_keys(value)
                table = Table(title=title, show_header=True)
                for key in keys:
                    table.add_column(key)
                for item in value:
                    table.add_row(*[_display_value(item.get(key)) for key in keys])
                self.console.print(table)
                return
        if isinstance(value, dict):
            if title:
                self.console.print(f"[bold]{title}[/bold]")
            for key, item in value.items():
                self.console.print(f"{key}: {_display_value(item)}")
            return
        self.console.print(value)

    def watch(self, run_id: str) -> str | None:
        terminal: str | None = None
        try:
            with self.client.stream("GET", f"/api/v1/runs/{run_id}/events") as response:
                if response.status_code >= 400:
                    detail = response.text or f"HTTP {response.status_code}"
                    self.fail(detail)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except ValueError:
                        self.fail("The API returned an invalid event stream.")
                    if self.json_output:
                        typer.echo(json.dumps(event, ensure_ascii=False, default=str))
                    else:
                        event_type = event.get("type", "event")
                        payload = event.get("payload", {})
                        message = (
                            payload.get("message")
                            or payload.get("content")
                            or payload.get("name")
                            or payload.get("status")
                            or ""
                        )
                        self.console.print(f"[{event_type}] {message}", markup=False)
                    if event.get("type") in {
                        "run.completed",
                        "run.failed",
                        "run.cancelled",
                    }:
                        terminal = event["type"]
        except httpx.HTTPError as error:
            self.fail(f"Event stream failed: {error}")
        return terminal

    def fail(self, message: str) -> None:
        typer.echo(f"error: {message}", err=True)
        raise typer.Exit(code=1)


def _display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if value is None:
        return "-"
    return str(value)


def _table_keys(rows: list[dict[str, Any]]) -> list[str]:
    preferred = ["id", "name", "title", "agent_name", "status", "build_status"]
    available = {key for row in rows for key in row}
    keys = [key for key in preferred if key in available]
    return keys or list(rows[0])[:5]


def _state(ctx: typer.Context) -> CliState:
    return ctx.ensure_object(CliState)


def build_cli(*, client_factory: ClientFactory = _default_client) -> typer.Typer:
    app = typer.Typer(
        name="research-map",
        help="Build, inspect, and debug autonomous research maps.",
        no_args_is_help=True,
    )
    canvas = typer.Typer(help="Create and manage canvases.", no_args_is_help=True)
    build = typer.Typer(help="Start and control autonomous builds.", no_args_is_help=True)
    paper = typer.Typer(help="Add and inspect canvas papers.", no_args_is_help=True)
    agent = typer.Typer(help="Run a fixed TrueForge specialist directly.", no_args_is_help=True)
    runs = typer.Typer(name="run", help="Inspect and control durable runs.", no_args_is_help=True)
    layout = typer.Typer(help="Reset persisted canvas layout.", no_args_is_help=True)
    app.add_typer(canvas, name="canvas")
    app.add_typer(build, name="build")
    app.add_typer(paper, name="paper")
    app.add_typer(agent, name="agent")
    app.add_typer(runs, name="run")
    app.add_typer(layout, name="layout")

    @app.callback()
    def root(
        ctx: typer.Context,
        api_url: str = typer.Option(
            os.environ.get("RESEARCH_MAP_API_URL", "http://127.0.0.1:8000"),
            "--api-url",
            help="FastAPI base URL.",
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Print raw JSON records instead of readable tables."
        ),
    ) -> None:
        ctx.obj = CliState(
            api_url=api_url.rstrip("/"),
            json_output=json_output,
            client=client_factory(api_url.rstrip("/")),
            console=Console(file=sys.stdout, force_terminal=False, color_system=None),
        )

    @canvas.command("create")
    def canvas_create(
        ctx: typer.Context,
        name: str = typer.Option(..., "--name", help="Canvas name."),
        goal: str = typer.Option(..., "--goal", help="Immutable research goal."),
    ) -> None:
        state = _state(ctx)
        state.emit(
            state.request(
                "POST", "/api/v1/canvases", json={"name": name, "research_goal": goal}
            ),
            title="Canvas created",
        )

    @canvas.command("list")
    def canvas_list(ctx: typer.Context) -> None:
        state = _state(ctx)
        state.emit(state.request("GET", "/api/v1/canvases"), title="Canvases")

    @canvas.command("show")
    def canvas_show(ctx: typer.Context, canvas_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("GET", f"/api/v1/canvases/{canvas_id}"), title="Canvas")

    @canvas.command("rename")
    def canvas_rename(ctx: typer.Context, canvas_id: str, name: str) -> None:
        state = _state(ctx)
        state.emit(
            state.request("PATCH", f"/api/v1/canvases/{canvas_id}", json={"name": name}),
            title="Canvas renamed",
        )

    @canvas.command("delete")
    def canvas_delete(ctx: typer.Context, canvas_id: str) -> None:
        state = _state(ctx)
        state.request("DELETE", f"/api/v1/canvases/{canvas_id}")
        state.emit({"id": canvas_id, "status": "deleted"})

    def start_and_maybe_watch(state: CliState, path: str, *, watch: bool) -> None:
        run = state.request("POST", path)
        state.emit(run, title="Run queued")
        if watch:
            state.watch(run["id"])

    @build.command("start")
    def build_start(
        ctx: typer.Context,
        canvas_id: str,
        watch: bool = typer.Option(False, "--watch", help="Stream until terminal."),
    ) -> None:
        start_and_maybe_watch(
            _state(ctx), f"/api/v1/canvases/{canvas_id}/builds", watch=watch
        )

    @build.command("watch")
    def build_watch(ctx: typer.Context, run_id: str) -> None:
        _state(ctx).watch(run_id)

    @build.command("cancel")
    def build_cancel(ctx: typer.Context, run_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("POST", f"/api/v1/runs/{run_id}/cancel"))

    @build.command("restart")
    def build_restart(
        ctx: typer.Context,
        run_id: str,
        watch: bool = typer.Option(False, "--watch"),
    ) -> None:
        state = _state(ctx)
        run = state.request("POST", f"/api/v1/runs/{run_id}/retry")
        state.emit(run, title="Run restarted")
        if watch:
            state.watch(run["id"])

    @build.command("status")
    def build_status(ctx: typer.Context, run_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("GET", f"/api/v1/runs/{run_id}"), title="Build")

    @paper.command("add-url")
    def paper_add_url(
        ctx: typer.Context,
        canvas_id: str,
        url: str,
        x: float | None = typer.Option(None),
        y: float | None = typer.Option(None),
        watch: bool = typer.Option(False, "--watch"),
    ) -> None:
        state = _state(ctx)
        run = state.request(
            "POST",
            f"/api/v1/canvases/{canvas_id}/papers/from-link",
            json={"url": url, "x": x, "y": y},
        )
        state.emit(run, title="Paper queued")
        if watch:
            state.watch(run["id"])

    @paper.command("status")
    def paper_status(ctx: typer.Context, canvas_id: str, paper_id: str) -> None:
        state = _state(ctx)
        snapshot = state.request("GET", f"/api/v1/canvases/{canvas_id}")
        selected = next(
            (item for item in snapshot.get("papers", []) if item.get("id") == paper_id),
            None,
        )
        if selected is None:
            state.fail("Paper does not belong to the canvas.")
        state.emit(selected, title="Paper")

    def direct_agent(
        state: CliState,
        canvas_id: str,
        agent_name: str,
        prompt: str,
        paper_ids: list[str],
    ) -> None:
        run = state.request(
            "POST",
            f"/api/v1/canvases/{canvas_id}/agents/runs",
            json={"agent": agent_name, "prompt": prompt, "paper_ids": paper_ids},
        )
        state.emit(run, title=f"{agent_name} run")
        state.watch(run["id"])

    @agent.command("main")
    def agent_main(ctx: typer.Context, canvas_id: str, prompt: str = typer.Option(...)) -> None:
        direct_agent(_state(ctx), canvas_id, "main", prompt, [])

    @agent.command("discovery")
    def agent_discovery(ctx: typer.Context, canvas_id: str, prompt: str = typer.Option(...)) -> None:
        direct_agent(_state(ctx), canvas_id, "discovery", prompt, [])

    @agent.command("reviewer")
    def agent_reviewer(
        ctx: typer.Context,
        canvas_id: str,
        paper_id: str = typer.Option(..., "--paper-id"),
        prompt: str = typer.Option(...),
    ) -> None:
        direct_agent(_state(ctx), canvas_id, "reviewer", prompt, [paper_id])

    @agent.command("connection")
    def agent_connection(
        ctx: typer.Context,
        canvas_id: str,
        paper_id: list[str] = typer.Option(..., "--paper-id"),
        prompt: str = typer.Option(...),
    ) -> None:
        direct_agent(_state(ctx), canvas_id, "connection", prompt, paper_id)

    @runs.command("list")
    def run_list(
        ctx: typer.Context,
        canvas_id: str | None = typer.Option(None, "--canvas-id"),
    ) -> None:
        state = _state(ctx)
        params = {"canvas_id": canvas_id} if canvas_id else None
        state.emit(state.request("GET", "/api/v1/runs", params=params), title="Runs")

    @runs.command("inspect")
    def run_inspect(ctx: typer.Context, run_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("GET", f"/api/v1/runs/{run_id}"), title="Run")

    @runs.command("retry")
    def run_retry(ctx: typer.Context, run_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("POST", f"/api/v1/runs/{run_id}/retry"))

    @runs.command("cancel")
    def run_cancel(ctx: typer.Context, run_id: str) -> None:
        state = _state(ctx)
        state.emit(state.request("POST", f"/api/v1/runs/{run_id}/cancel"))

    @runs.command("events")
    def run_events(
        ctx: typer.Context,
        run_id: str,
        after_id: int = typer.Option(0, min=0),
    ) -> None:
        state = _state(ctx)
        events = state.request(
            "GET", f"/api/v1/runs/{run_id}/event-log", params={"after_id": after_id}
        )
        for event in events:
            state.emit(event)

    @layout.command("reset")
    def layout_reset(ctx: typer.Context, canvas_id: str) -> None:
        state = _state(ctx)
        state.emit(
            state.request("POST", f"/api/v1/canvases/{canvas_id}/layout/reset"),
            title="Layout reset",
        )

    @app.command("shell")
    def shell(ctx: typer.Context, canvas_id: str) -> None:
        state = _state(ctx)
        state.console.print("Commands: show, build, runs, paper <url>, agent <name> <prompt>, quit")
        while True:
            try:
                line = typer.prompt(f"research-map:{canvas_id}").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            parts = shlex.split(line)
            if parts[0] in {"quit", "exit"}:
                break
            if parts[0] == "show":
                state.emit(state.request("GET", f"/api/v1/canvases/{canvas_id}"))
            elif parts[0] == "build":
                start_and_maybe_watch(
                    state, f"/api/v1/canvases/{canvas_id}/builds", watch=True
                )
            elif parts[0] == "runs":
                state.emit(
                    state.request("GET", "/api/v1/runs", params={"canvas_id": canvas_id})
                )
            elif parts[0] == "paper" and len(parts) == 2:
                state.emit(
                    state.request(
                        "POST",
                        f"/api/v1/canvases/{canvas_id}/papers/from-link",
                        json={"url": parts[1]},
                    )
                )
            elif parts[0] == "agent" and len(parts) >= 3 and parts[1] in {"main", "discovery"}:
                direct_agent(state, canvas_id, parts[1], " ".join(parts[2:]), [])
            else:
                state.console.print("Unknown or incomplete command.", style="yellow")

    return app


app = build_cli()


def main() -> None:
    app()
