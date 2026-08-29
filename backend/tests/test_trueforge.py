import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from research_map_backend.agents import load_agent_manifests
from research_map_backend.integrations.trueforge import (
    TrueForgeClient,
    TrueForgeConfigurationError,
    TrueForgeError,
    bootstrap_trueforge,
    openai_provider_manifest,
)
from research_map_backend.settings import Settings


def make_settings(**overrides: object) -> Settings:
    values = {
        "openai_api_key": "test-openai-secret",
        "openai_research_model": "gpt-5.6-terra",
        "openai_fast_model": "gpt-5.6-terra",
        "trueforge_url": "http://trueforge.test",
        "research_map_mcp_url": "http://api.test/mcp",
    }
    values.update(overrides)
    return Settings(**values)


def test_agent_manifests_have_fixed_roles_and_autonomous_limits() -> None:
    manifests = load_agent_manifests()

    assert [item["name"] for item in manifests] == [
        "research-map-main",
        "research-map-discovery",
        "research-map-reviewer",
        "research-map-connection",
    ]
    assert [item["manifest"]["model"]["name"] for item in manifests] == [
        "openai/gpt-5-6-terra",
        "openai/gpt-5-6-terra",
        "openai/gpt-5-6-terra",
        "openai/gpt-5-6-terra",
    ]

    for item in manifests:
        manifest = item["manifest"]
        config = manifest["config"]
        assert config["dynamic_sub_agents"]["enabled"] is False
        assert config["ask_user_questions"]["enabled"] is False
        assert 1 <= config["iteration_limit"] <= 16
        for mcp_server in manifest["mcp_servers"]:
            assert mcp_server["require_approval_for_tools"] == []

    assert manifests[0]["manifest"]["mcp_servers"][0]["preload"] is True
    assert manifests[0]["manifest"]["mcp_servers"][0]["enable_tools"] == [
        "get_canvas",
        "create_canvas_and_start_build",
    ]
    for manifest in manifests[1:]:
        assert "create_canvas_and_start_build" not in (
            manifest["manifest"]["mcp_servers"][0]["enable_tools"]
        )
    reviewer_tools = manifests[2]["manifest"]["mcp_servers"][0]["enable_tools"]
    assert reviewer_tools == ["get_paper_text", "record_review"]
    discovery = manifests[1]["manifest"]
    assert discovery["mcp_servers"][0]["enable_tools"] == [
        "get_canvas",
        "resolve_paper_metadata",
        "submit_discovery_batch",
    ]
    assert discovery["mcp_servers"][1]["enable_tools"] == [
        "search_engine",
        "scrape_as_markdown",
    ]


def test_provider_manifest_maps_configured_openai_model() -> None:
    manifest = openai_provider_manifest(make_settings())

    assert manifest == {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "auth": {"api_key": "test-openai-secret"},
        "models": [
            {
                "name": "gpt-5-6-terra",
                "model_id": "gpt-5.6-terra",
                "properties": {"reasoning_efforts": ["low", "medium", "high"]},
            },
        ],
    }


def test_provider_manifest_requires_openai_key() -> None:
    with pytest.raises(TrueForgeConfigurationError, match="OPENAI_API_KEY"):
        openai_provider_manifest(make_settings(openai_api_key=None))


def test_bootstrap_creates_missing_agents_and_updates_existing_agent() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, payload))

        if request.method == "GET" and request.url.path == "/api/v1/agents":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "agent-reviewer",
                            "name": "research-map-reviewer",
                            "manifest": {},
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == "/api/v1/agents":
            return httpx.Response(
                201,
                json={"data": {"id": f"agent-{payload['name']}", **payload}},
            )
        if request.method == "PUT" and request.url.path.startswith(
            "/api/v1/agents/"
        ):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": request.url.path.rsplit("/", 1)[-1],
                        "name": "research-map-reviewer",
                        "manifest": payload["manifest"],
                    }
                },
            )
        return httpx.Response(200, json={"data": {}})

    async def run() -> object:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await bootstrap_trueforge(make_settings(), client=client)

    result = asyncio.run(run())

    assert result.provider_name == "openai"
    assert result.mcp_server_name == "research-map"
    assert set(result.agent_ids) == {
        "research-map-main",
        "research-map-discovery",
        "research-map-reviewer",
        "research-map-connection",
    }
    assert ("research-map-reviewer", "agent-reviewer") in result.agent_ids.items()

    provider_call = calls[0]
    assert provider_call[0:2] == (
        "PUT",
        "/api/v1/settings/model-providers",
    )
    assert provider_call[2]["manifest"]["auth"]["api_key"] == (
        "test-openai-secret"
    )
    assert calls[1][0:2] == ("PUT", "/api/v1/settings/mcp-servers")
    assert calls[1][2]["manifest"]["url"] == "http://api.test/mcp"
    assert sum(call[0] == "POST" for call in calls) == 3
    assert (
        "PUT",
        "/api/v1/agents/agent-reviewer",
    ) in [call[0:2] for call in calls]


def test_bootstrap_errors_never_include_openai_secret() -> None:
    secret = "never-print-this-secret"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": secret})

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            await bootstrap_trueforge(
                make_settings(openai_api_key=secret), client=client
            )

    with pytest.raises(TrueForgeError) as caught:
        asyncio.run(run())

    assert secret not in str(caught.value)


def test_bootstrap_is_repeatable_without_duplicate_agents() -> None:
    saved_agents: dict[str, dict] = {}
    methods: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        methods.append((request.method, request.url.path))

        if request.method == "GET" and request.url.path == "/api/v1/agents":
            return httpx.Response(200, json={"data": list(saved_agents.values())})
        if request.method == "POST" and request.url.path == "/api/v1/agents":
            saved = {"id": f"id-{payload['name']}", **payload}
            saved_agents[payload["name"]] = saved
            return httpx.Response(201, json={"data": saved})
        if request.url.path.startswith("/api/v1/agents/"):
            agent_id = request.url.path.rsplit("/", 1)[-1]
            name = next(
                name
                for name, agent in saved_agents.items()
                if agent["id"] == agent_id
            )
            saved_agents[name]["manifest"] = payload["manifest"]
            return httpx.Response(200, json={"data": saved_agents[name]})
        return httpx.Response(200, json={"data": {}})

    async def run_twice() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            await bootstrap_trueforge(make_settings(), client=client)
            await bootstrap_trueforge(make_settings(), client=client)

    asyncio.run(run_twice())

    assert len(saved_agents) == 4
    assert methods.count(("POST", "/api/v1/agents")) == 4
    assert sum(
        method == "PUT" and path.startswith("/api/v1/agents/")
        for method, path in methods
    ) == 4


def test_manifest_loader_rejects_invalid_file(tmp_path: Path) -> None:
    for filename in ("main.json", "discovery.json", "reviewer.json"):
        (tmp_path / filename).write_text(
            json.dumps({"name": filename, "manifest": {}}), encoding="utf-8"
        )
    (tmp_path / "connection.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid agent manifest"):
        load_agent_manifests(tmp_path)


def test_session_and_turn_stream_match_trueforge_014_contract() -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.url.path == "/api/v1/sessions":
            return httpx.Response(201, json={"data": {"id": "session-1"}})
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "turn-1",
                        "session_id": "session-1",
                        "state": {"status": "running"},
                    }
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'id: 1\ndata: {"type":"turn.created","turn_id":"turn-1"}\n\n'
                'id: 2\ndata: {"type":"turn.done","state":{"status":"done"}}\n\n'
            ),
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TrueForgeClient("http://trueforge.test", http_client)
            session_id = await client.create_session("research-map-main")
            return [event async for event in client.stream_turn(session_id, "Goal")]

    events = asyncio.run(run())

    assert [event["type"] for event in events] == ["turn.created", "turn.done"]
    assert requests == [
        (
            "POST",
            "/api/v1/sessions",
            {"agent": {"name": "research-map-main"}},
        ),
        (
            "POST",
            "/api/v1/sessions/session-1/turns",
            {
                "input": [{"type": "user.message", "content": "Goal"}],
                "stream": False,
            },
        ),
        (
            "GET",
            "/api/v1/sessions/session-1/turns/turn-1/subscribe",
            None,
        ),
    ]


def test_stream_turn_waits_for_delayed_terminal_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": {"id": "turn-delayed", "state": {"status": "running"}}},
            )
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'id: 1\ndata: {"type":"turn.created","turn_id":"turn-delayed"}\n\n'
                'id: 2\ndata: {"type":"model.message","content":"complete JSON"}\n\n'
                'id: 3\ndata: {"type":"turn.done","state":{"status":"done",'
                '"required_actions":[],"metrics":{"total_input_tokens":12}}}\n\n'
            ),
        )

    async def run() -> tuple[list[dict], float]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = TrueForgeClient("http://trueforge.test", http_client)
            started = time.monotonic()
            events = [event async for event in client.stream_turn("session", "Review")]
            return events, time.monotonic() - started

    events, elapsed = asyncio.run(run())

    assert elapsed >= 0.045
    assert [event["type"] for event in events] == [
        "turn.created",
        "model.message",
        "turn.done",
    ]
    assert events[1]["content"] == "complete JSON"


def test_stream_disconnect_reconnects_same_turn_with_sequence_cursor() -> None:
    calls: list[tuple[str, str, str | None]] = []
    subscribe_count = 0

    class DisconnectAfterCreated(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'id: 1\ndata: {"type":"turn.created","turn_id":"turn-1"}\n\n'
            raise httpx.ReadError("peer closed the SSE stream")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal subscribe_count
        calls.append(
            (
                request.method,
                request.url.path,
                request.url.params.get("after_sequence_number"),
            )
        )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": {"id": "turn-1", "state": {"status": "running"}}},
            )
        if request.url.path.endswith("/subscribe"):
            subscribe_count += 1
            if subscribe_count == 1:
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    stream=DisconnectAfterCreated(),
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    'id: 2\ndata: {"type":"model.message","content":"recovered"}\n\n'
                    'id: 3\ndata: {"type":"turn.done","state":{"status":"done",'
                    '"required_actions":[]}}\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={"data": {"id": "turn-1", "state": {"status": "running"}}},
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return [
                event
                async for event in TrueForgeClient(
                    "http://trueforge.test", http_client
                ).stream_turn("session-1", "Review")
            ]

    events = asyncio.run(run())

    assert sum(method == "POST" for method, _, _ in calls) == 1
    subscribe_calls = [item for item in calls if item[1].endswith("/subscribe")]
    assert subscribe_calls == [
        ("GET", "/api/v1/sessions/session-1/turns/turn-1/subscribe", None),
        ("GET", "/api/v1/sessions/session-1/turns/turn-1/subscribe", "1"),
    ]
    assert [event["type"] for event in events] == [
        "turn.created",
        "transport.error",
        "model.message",
        "turn.done",
    ]
    assert events[1]["error_class"] == "ReadError"
    assert events[2]["content"] == "recovered"


def test_immediate_subscribe_exit_recovers_same_durable_turn() -> None:
    post_count = 0
    subscribe_count = 0
    turn_paths: list[str] = []

    class ImmediateDisconnect(httpx.AsyncByteStream):
        async def __aiter__(self):
            if False:
                yield b""
            raise httpx.ReadError("stream exited before the first event")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count, subscribe_count
        turn_paths.append(request.url.path)
        if request.method == "POST":
            post_count += 1
            return httpx.Response(
                200,
                json={"data": {"id": "durable-turn", "state": {"status": "running"}}},
            )
        if request.url.path.endswith("/subscribe"):
            subscribe_count += 1
            if subscribe_count == 1:
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    stream=ImmediateDisconnect(),
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    'id: 1\ndata: {"type":"turn.created","turn_id":"durable-turn"}\n\n'
                    'id: 2\ndata: {"type":"model.message","content":"late full JSON"}\n\n'
                    'id: 3\ndata: {"type":"turn.done","state":{"status":"done",'
                    '"required_actions":[]}}\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={"data": {"id": "durable-turn", "state": {"status": "running"}}},
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return [
                event
                async for event in TrueForgeClient(
                    "http://trueforge.test", http_client
                ).stream_turn("session", "large PDF review")
            ]

    events = asyncio.run(run())

    assert post_count == 1
    assert subscribe_count == 2
    assert set(turn_paths) == {
        "/api/v1/sessions/session/turns",
        "/api/v1/sessions/session/turns/durable-turn",
        "/api/v1/sessions/session/turns/durable-turn/subscribe",
    }
    assert [event["type"] for event in events] == [
        "turn.created",
        "transport.error",
        "model.message",
        "turn.done",
    ]
    assert events[2]["content"] == "late full JSON"


def test_stream_transport_failure_uses_durable_terminal_get_output() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": {"id": "turn-1", "state": {"status": "running"}}},
            )
        if request.url.path.endswith("/subscribe"):
            return httpx.Response(
                412,
                headers={"Retry-After": "7"},
                json={"error": {"message": "resumable stream expired"}},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "turn-1",
                    "state": {
                        "status": "done",
                        "output": {
                            "type": "model.message",
                            "content": "durable complete output",
                            "usage": {"input_tokens": 42, "output_tokens": 7},
                        },
                        "required_actions": [],
                        "metrics": {"total_input_tokens": 42, "total_output_tokens": 7},
                    },
                }
            },
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return [
                event
                async for event in TrueForgeClient(
                    "http://trueforge.test", http_client
                ).stream_turn("session-1", "Review")
            ]

    events = asyncio.run(run())

    assert calls == [
        ("POST", "/api/v1/sessions/session-1/turns"),
        ("GET", "/api/v1/sessions/session-1/turns/turn-1/subscribe"),
        ("GET", "/api/v1/sessions/session-1/turns/turn-1"),
    ]
    assert events[1]["type"] == "transport.error"
    assert events[1]["status_code"] == 412
    assert events[1]["retry_after_seconds"] == 7
    assert events[1]["detail"] == "resumable stream expired"
    assert events[2]["type"] == "model.message"
    assert events[2]["content"] == "durable complete output"
    assert events[2]["recovered"] is True
    assert events[3]["type"] == "turn.done" and events[3]["recovered"] is True


def test_subscribed_turn_can_be_cancelled_by_its_durable_id() -> None:
    cancelled = asyncio.Event()
    delete_calls: list[str] = []

    class WaitForCancellation(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'id: 1\ndata: {"type":"turn.created","turn_id":"turn-1"}\n\n'
            await cancelled.wait()
            yield (
                b'id: 2\ndata: {"type":"turn.done","state":{"status":"cancelled",'
                b'"reason":"client-cancelled"}}\n\n'
            )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": {"id": "turn-1", "state": {"status": "running"}}},
            )
        if request.method == "DELETE":
            delete_calls.append(request.url.path)
            cancelled.set()
            return httpx.Response(204)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=WaitForCancellation(),
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = TrueForgeClient("http://trueforge.test", http_client)
            stream = client.stream_turn("session-1", "Review")
            first = await anext(stream)
            terminal_task = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            assert await client.cancel_turn("session-1", first["turn_id"]) is True
            terminal = await terminal_task
            await stream.aclose()
            return [first, terminal]

    events = asyncio.run(run())

    assert events[0] == {
        "type": "turn.created",
        "turn_id": "turn-1",
        "state": {"status": "running"},
    }
    assert events[1]["type"] == "turn.done"
    assert events[1]["state"]["status"] == "cancelled"
    assert delete_calls == [
        "/api/v1/sessions/session-1/turns/turn-1"
    ]
