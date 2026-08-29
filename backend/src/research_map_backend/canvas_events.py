from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

from fastapi import Request


class CanvasEventBroadcaster:
    """Best-effort process-local notifications that tell clients to refetch."""

    def __init__(self, *, keepalive_seconds: float = 15.0) -> None:
        self.keepalive_seconds = keepalive_seconds
        self._subscribers: set[asyncio.Queue[dict[str, str]]] = set()

    async def publish(self, canvas_id: str, change_type: str) -> None:
        event = {
            "canvas_id": canvas_id,
            "change_type": change_type,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - defensive race guard
                    pass
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, str]]]:
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def stream(self, request: Request) -> AsyncIterator[str]:
        async with self.subscribe() as queue:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self.keepalive_seconds
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield (
                    "event: canvas.changed\n"
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                )
