from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import httpx

from research_map_backend.agents import load_agent_manifests
from research_map_backend.db import Database
from research_map_backend.integrations.trueforge import TrueForgeClient, TrueForgeError, run_agent
from research_map_backend.models import Canvas
from research_map_backend.research_store import get_pipeline_papers
from research_map_backend.runs import normalize_trueforge_event
from research_map_backend.settings import Settings, get_settings

AgentSource = Callable[[Settings, str, str], AsyncIterator[dict[str, Any]]]
AgentLister = Callable[[Settings], Awaitable[list[dict[str, Any]]]]
AGENT_NAMES = {
    "main": "research-map-main",
    "discovery": "research-map-discovery",
    "reviewer": "research-map-reviewer",
    "connection": "research-map-connection",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-map-agent",
        description="Debug saved research-map agents through TrueForge 0.1.4.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List agents currently saved in TrueForge.")
    run = commands.add_parser("run", help="Run one saved agent and stream JSON events.")
    run.add_argument("agent", choices=tuple(AGENT_NAMES))
    run.add_argument("--prompt", required=True, help="Direct debugging prompt.")
    run.add_argument(
        "--canvas-id",
        help="Append the current local canvas goal, brief, papers, and sources.",
    )
    return parser


async def list_trueforge_agents(settings: Settings) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await TrueForgeClient(settings.trueforge_url, client).list_agents()


async def run_cli(
    argv: Sequence[str],
    *,
    settings: Settings | None = None,
    agent_source: AgentSource = run_agent,
    agent_lister: AgentLister = list_trueforge_agents,
) -> int:
    args = build_parser().parse_args(argv)
    app_settings = settings or get_settings()
    if args.command == "list":
        for agent in await agent_lister(app_settings):
            _print({"type": "agent", "payload": agent})
        return 0

    secret = app_settings.openai_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise CliError("OPENAI_API_KEY is required to run an agent.")
    agent_name = AGENT_NAMES[args.agent]
    manifests = {item["name"]: item["manifest"] for item in load_agent_manifests()}
    manifest = manifests.get(agent_name)
    model_name = manifest.get("model", {}).get("name") if isinstance(manifest, dict) else None
    if not isinstance(model_name, str) or not model_name.startswith("openai/"):
        raise CliError(f"{agent_name} is not configured to use OpenAI.")

    prompt = args.prompt
    if args.canvas_id:
        prompt = f"{prompt}\n\nCanvas context:\n{_canvas_context(app_settings, args.canvas_id)}"
    async for event in agent_source(app_settings, agent_name, prompt):
        event_type = event.get("type")
        if event_type == "session.created":
            _print(
                {
                    "type": "agent.session.created",
                    "payload": {"agent": agent_name, "session_id": event.get("session_id")},
                }
            )
            continue
        if event_type == "model.message" and isinstance(event.get("content"), str):
            _print(
                {
                    "type": "agent.message",
                    "payload": {"agent": agent_name, "content": event["content"]},
                }
            )
        for normalized_type, payload in normalize_trueforge_event(event):
            _print({"type": normalized_type, "payload": {"agent": agent_name, **payload}})
    return 0


def _canvas_context(settings: Settings, canvas_id: str) -> str:
    database = Database(settings)
    try:
        with database.session_context() as session:
            canvas = session.get(Canvas, canvas_id)
            if canvas is None:
                raise CliError(f"Canvas {canvas_id} does not exist.")
            context = {
                "canvas": {
                    "id": canvas.id,
                    "name": canvas.name,
                    "research_goal": canvas.research_goal,
                    "research_brief": canvas.research_brief,
                    "build_status": canvas.build_status,
                },
                "papers": get_pipeline_papers(database, canvas_id),
            }
        return json.dumps(context, ensure_ascii=False)
    finally:
        database.dispose()


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


class CliError(RuntimeError):
    pass


def main(argv: Sequence[str] | None = None) -> None:
    try:
        code = asyncio.run(run_cli(sys.argv[1:] if argv is None else argv))
    except (CliError, TrueForgeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
