import asyncio
import json
import threading
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from research_map_backend.app import create_app
from research_map_backend.api.routes import stream_canvas_events
from research_map_backend.canvas_events import CanvasEventBroadcaster
from research_map_backend.settings import Settings


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_canvas_event_stream_delivers_public_refetch_shape_and_reconnects() -> None:
    async def scenario() -> None:
        broadcaster = CanvasEventBroadcaster(keepalive_seconds=1)

        first_stream = broadcaster.stream(ConnectedRequest())  # type: ignore[arg-type]
        first_item = asyncio.create_task(anext(first_stream))
        await asyncio.sleep(0)
        await broadcaster.publish("canvas-1", "review.completed")
        frame = await first_item
        await first_stream.aclose()

        assert frame.startswith("event: canvas.changed\n")
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["canvas_id"] == "canvas-1"
        assert payload["change_type"] == "review.completed"
        datetime.fromisoformat(payload["updated_at"])
        assert set(payload) == {"canvas_id", "change_type", "updated_at"}

        # A reconnect has no durable replay, but receives subsequent changes.
        second_stream = broadcaster.stream(ConnectedRequest())  # type: ignore[arg-type]
        second_item = asyncio.create_task(anext(second_stream))
        await asyncio.sleep(0)
        await broadcaster.publish("canvas-2", "paper.added")
        second_frame = await second_item
        await second_stream.aclose()
        assert '"canvas_id":"canvas-2"' in second_frame

    asyncio.run(scenario())


def test_canvas_event_stream_sends_keepalive_and_endpoint_headers() -> None:
    async def scenario() -> None:
        broadcaster = CanvasEventBroadcaster(keepalive_seconds=0.001)
        stream = broadcaster.stream(ConnectedRequest())  # type: ignore[arg-type]
        assert await anext(stream) == ": keep-alive\n\n"
        await stream.aclose()

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(canvas_events=broadcaster))
        )
        response = await stream_canvas_events(request)  # type: ignore[arg-type]
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

    asyncio.run(scenario())


def test_canvas_api_mutation_publishes_change_to_live_subscriber(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'events.sqlite3'}",
        trueforge_url="http://127.0.0.1:1",
    )
    ready = threading.Event()

    async def receive_one(broadcaster: CanvasEventBroadcaster) -> dict[str, str]:
        async with broadcaster.subscribe() as queue:
            ready.set()
            return await asyncio.wait_for(queue.get(), timeout=2)

    with TestClient(create_app(settings)) as client:
        assert client.portal is not None
        pending = client.portal.start_task_soon(
            receive_one, client.app.state.canvas_events
        )
        assert ready.wait(timeout=1)
        created = client.post(
            "/api/v1/canvases",
            json={"name": "Live", "research_goal": "Observe canvas changes."},
        )
        event = pending.result(timeout=2)

    assert created.status_code == 201
    assert event["canvas_id"] == created.json()["canvas"]["id"]
    assert event["change_type"] == "canvas.created"
