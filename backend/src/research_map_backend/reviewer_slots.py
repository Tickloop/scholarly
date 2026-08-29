from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

ReviewerSlotEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


@asynccontextmanager
async def reviewer_slot(
    semaphore: asyncio.Semaphore,
    emit: ReviewerSlotEventSink,
    *,
    canvas_id: str,
    run_id: str,
    paper_id: str | None,
    purpose: str,
) -> AsyncIterator[None]:
    """Hold one process-local reviewer slot and emit an exact durable interval."""
    slot_id = str(uuid4())
    payload = {
        "agent": "research-map-reviewer",
        "slot_id": slot_id,
        "canvas_id": canvas_id,
        "run_id": run_id,
        "paper_id": paper_id,
        "purpose": purpose,
    }
    await semaphore.acquire()
    try:
        await emit("reviewer.slot.acquired", payload)
    except BaseException:
        semaphore.release()
        raise
    try:
        yield
    finally:
        semaphore.release()
        await emit("reviewer.slot.released", payload)
