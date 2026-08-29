from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from research_map_backend.agents import load_agent_manifests
from research_map_backend.settings import Settings

OPENAI_PROVIDER_NAME = "openai"
OPENAI_MODEL_NAME = "gpt-5-6-terra"
MCP_SERVER_NAME = "research-map"


class TrueForgeError(RuntimeError):
    """A safe error from the local TrueForge API."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        error_class: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.error_class = error_class
        self.detail = detail

    def transport_payload(self, *, status: str = "retrying") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "stage": self.stage or "trueforge",
            "error_class": self.error_class or type(self).__name__,
            "message": str(self),
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        if self.detail:
            payload["detail"] = self.detail
        return payload


class TrueForgeConfigurationError(TrueForgeError):
    """Required local configuration is missing."""


@dataclass(frozen=True)
class BootstrapResult:
    provider_name: str
    mcp_server_name: str
    agent_ids: dict[str, str]


class TrueForgeClient:
    """Small client for the TrueForge 0.1.4 bootstrap endpoints."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def put_provider(self, manifest: dict[str, Any]) -> None:
        await self._request(
            "PUT",
            "/api/v1/settings/model-providers",
            json={"manifest": manifest},
        )

    async def put_mcp_server(self, manifest: dict[str, Any]) -> None:
        await self._request(
            "PUT",
            "/api/v1/settings/mcp-servers",
            json={"manifest": manifest},
        )

    async def list_agents(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v1/agents")
        data = body.get("data")
        if not isinstance(data, list):
            raise TrueForgeError("TrueForge returned an invalid agent list")
        return data

    async def create_agent(
        self, name: str, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        body = await self._request(
            "POST",
            "/api/v1/agents",
            json={"name": name, "manifest": manifest},
        )
        return self._response_data(body, "created agent")

    async def update_agent(
        self, agent_id: str, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        body = await self._request(
            "PUT",
            f"/api/v1/agents/{agent_id}",
            json={"manifest": manifest},
        )
        return self._response_data(body, "updated agent")

    async def create_session(self, agent_name: str) -> str:
        body = await self._request(
            "POST",
            "/api/v1/sessions",
            json={"agent": {"name": agent_name}},
        )
        data = self._response_data(body, "created session")
        session_id = data.get("id")
        if not isinstance(session_id, str):
            raise TrueForgeError("TrueForge returned no id for created session")
        return session_id

    async def stream_turn(
        self, session_id: str, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Create one durable turn, then follow that same turn until terminal.

        TrueForge 0.1.4 deliberately lets execution outlive the create-turn HTTP
        response. Creating without streaming gives us the durable turn ID before
        model work, and the dedicated subscribe endpoint can resume after a broken
        SSE connection without starting or billing another turn.
        """
        turn = await self.create_turn(session_id, message)
        turn_id = turn["id"]
        yield {
            "type": "turn.created",
            "turn_id": turn_id,
            "state": turn.get("state", {"status": "running"}),
        }
        state = turn.get("state")
        if isinstance(state, dict) and state.get("status") != "running":
            async for event in _terminal_turn_events(turn_id, state, recovered=True):
                yield event
            return

        sequence_number: int | None = None
        recovery_deadline = time.monotonic() + 660.0
        while True:
            try:
                async for sequence, event in self.subscribe_turn(
                    session_id,
                    turn_id,
                    after_sequence_number=sequence_number,
                ):
                    if sequence is not None:
                        sequence_number = sequence
                    # create_turn already made this ID durable and the caller has
                    # persisted it. Do not emit the replayed lifecycle event twice.
                    if event.get("type") == "turn.created":
                        continue
                    yield event
                    if event.get("type") == "turn.done":
                        return
            except TrueForgeError as error:
                yield {
                    "type": "transport.error",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **error.transport_payload(),
                }

            # A subscription can close cleanly on its server-side timeout, or fail
            # after TrueForge has detached execution. The durable turn is the source
            # of truth in both cases.
            try:
                turn = await self.get_turn(session_id, turn_id)
            except TrueForgeError as error:
                yield {
                    "type": "transport.error",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **error.transport_payload(),
                }
                if time.monotonic() >= recovery_deadline:
                    raise TrueForgeError(
                        "TrueForge turn recovery timed out.",
                        stage="turn_recovery",
                        error_class=type(error).__name__,
                        detail=error.detail or str(error),
                    ) from error
                await _short_recovery_delay(error.retry_after_seconds)
                continue

            state = turn.get("state")
            if not isinstance(state, dict):
                raise TrueForgeError(
                    "TrueForge returned an invalid durable turn state.",
                    stage="get_turn",
                    error_class="InvalidTurnState",
                )
            if state.get("status") != "running":
                async for event in _terminal_turn_events(
                    turn_id, state, recovered=True
                ):
                    yield event
                return
            if time.monotonic() >= recovery_deadline:
                raise TrueForgeError(
                    "TrueForge turn remained running past the recovery deadline.",
                    stage="turn_recovery",
                    error_class="TurnRecoveryTimeout",
                )
            await _short_recovery_delay(None)

    async def create_turn(self, session_id: str, message: str) -> dict[str, Any]:
        path = f"/api/v1/sessions/{session_id}/turns"
        body = await self._request(
            "POST",
            path,
            json={
                "input": [{"type": "user.message", "content": message}],
                "stream": False,
            },
            stage="create_turn",
        )
        data = self._response_data(body, "created turn")
        turn_id = data.get("id")
        state = data.get("state")
        if not isinstance(turn_id, str) or not isinstance(state, dict):
            raise TrueForgeError(
                "TrueForge returned invalid data for created turn",
                stage="create_turn",
                error_class="InvalidTurnData",
            )
        return data

    async def subscribe_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        after_sequence_number: int | None = None,
    ) -> AsyncIterator[tuple[int | None, dict[str, Any]]]:
        path = f"/api/v1/sessions/{session_id}/turns/{turn_id}/subscribe"
        params = (
            {"after_sequence_number": after_sequence_number}
            if after_sequence_number is not None
            else None
        )
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}{path}",
                params=params,
            ) as response:
                response.raise_for_status()
                async for item in _read_sse_records(response.aiter_lines()):
                    yield item
        except httpx.HTTPStatusError as error:
            raise _http_status_error(error, "subscribe_turn", path) from error
        except httpx.HTTPError as error:
            raise _http_transport_error(error, "subscribe_turn", path) from error

    async def get_turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        path = f"/api/v1/sessions/{session_id}/turns/{turn_id}"
        body = await self._request("GET", path, stage="get_turn")
        data = self._response_data(body, "fetched turn")
        if not isinstance(data.get("id"), str) or not isinstance(
            data.get("state"), dict
        ):
            raise TrueForgeError(
                "TrueForge returned invalid data for fetched turn",
                stage="get_turn",
                error_class="InvalidTurnData",
            )
        return data

    async def cancel_turn(self, session_id: str, turn_id: str) -> bool:
        """Cancel an active turn when the installed TrueForge exposes cancellation."""
        path = f"/api/v1/sessions/{session_id}/turns/{turn_id}"
        try:
            response = await self._client.request("DELETE", f"{self._base_url}{path}")
        except httpx.HTTPError:
            return False
        if response.status_code in {404, 405, 501}:
            return False
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def _request(
        self,
        method: str,
        path: str,
        stage: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                **kwargs,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise _http_status_error(error, stage or method.casefold(), path) from error
        except httpx.HTTPError as error:
            raise _http_transport_error(error, stage or method.casefold(), path) from error

        try:
            body = response.json()
        except ValueError as error:
            raise TrueForgeError(
                f"TrueForge {method} {path} returned invalid JSON"
            ) from error
        if not isinstance(body, dict):
            raise TrueForgeError(f"TrueForge {method} {path} returned invalid data")
        return body

    @staticmethod
    def _response_data(body: dict[str, Any], operation: str) -> dict[str, Any]:
        data = body.get("data")
        if not isinstance(data, dict):
            raise TrueForgeError(f"TrueForge returned invalid data for {operation}")
        return data


async def _read_sse_records(
    lines: AsyncIterator[str],
) -> AsyncIterator[tuple[int | None, dict[str, Any]]]:
    data_lines: list[str] = []
    sequence_number: int | None = None
    async for line in lines:
        if line == "":
            if data_lines:
                yield sequence_number, _decode_sse_data(data_lines)
                data_lines = []
                sequence_number = None
            continue
        if line.startswith("id:"):
            raw_sequence = line[3:].strip()
            try:
                sequence_number = int(raw_sequence)
            except ValueError:
                sequence_number = None
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield sequence_number, _decode_sse_data(data_lines)


async def _read_sse(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Backward-compatible event-only SSE reader used by focused tests."""
    async for _, event in _read_sse_records(lines):
        yield event


def _decode_sse_data(data_lines: list[str]) -> dict[str, Any]:
    try:
        event = json.loads("\n".join(data_lines))
    except ValueError as error:
        raise TrueForgeError("TrueForge returned invalid SSE data") from error
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise TrueForgeError("TrueForge returned an invalid SSE event")
    return event


async def _terminal_turn_events(
    turn_id: str, state: dict[str, Any], *, recovered: bool
) -> AsyncIterator[dict[str, Any]]:
    output = state.get("output")
    if isinstance(output, dict) and output.get("type") == "model.message":
        yield {**output, "recovered": recovered}
    yield {
        "type": "turn.done",
        "turn_id": turn_id,
        "state": state,
        "recovered": recovered,
    }


async def _short_recovery_delay(retry_after_seconds: float | None) -> None:
    delay = (
        min(max(retry_after_seconds, 0.0), 1.0)
        if isinstance(retry_after_seconds, (int, float))
        else 0.05
    )
    await asyncio.sleep(delay)


def _http_status_error(
    error: httpx.HTTPStatusError, stage: str, path: str
) -> TrueForgeError:
    response = error.response
    status = response.status_code
    detail = _safe_response_detail(response)
    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    return TrueForgeError(
        f"TrueForge {stage} failed with status {status}.",
        stage=stage,
        status_code=status,
        retry_after_seconds=retry_after,
        error_class=type(error).__name__,
        detail=detail or f"Local TrueForge endpoint {path} rejected the request.",
    )


def _http_transport_error(
    error: httpx.HTTPError, stage: str, path: str
) -> TrueForgeError:
    detail = _sanitize_detail(str(error))
    return TrueForgeError(
        f"TrueForge {stage} transport failed.",
        stage=stage,
        error_class=type(error).__name__,
        detail=detail or f"Local TrueForge endpoint {path} disconnected.",
    )


def _safe_response_detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return _sanitize_detail(message) if isinstance(message, str) else None


def _sanitize_detail(value: str) -> str:
    cleaned = " ".join(value.split())[:500]
    cleaned = re.sub(
        r"(?i)(authorization|api[_-]?key|bearer)\s*[:=]?\s*[^\s,;]+",
        r"\1=[redacted]",
        cleaned,
    )
    return cleaned


def _retry_after_seconds(value: str | None) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


async def run_agent(
    settings: Settings, agent_name: str, prompt: str
) -> AsyncIterator[dict[str, Any]]:
    timeout = httpx.Timeout(300.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        client = TrueForgeClient(settings.trueforge_url, http_client)
        session_id = await client.create_session(agent_name)
        yield {
            "type": "session.created",
            "session_id": session_id,
            "agent_name": agent_name,
        }
        try:
            async for event in client.stream_turn(session_id, prompt):
                yield event
        except TrueForgeError as error:
            yield {
                "type": "transport.error",
                "session_id": session_id,
                **error.transport_payload(status="failed"),
            }
            raise


async def run_main_agent(
    settings: Settings, research_goal: str
) -> AsyncIterator[dict[str, Any]]:
    prompt = (
        "Create a concise research brief and identify canonical anchor papers. "
        "Return one JSON object only with exactly this shape: "
        '{"brief_markdown":"...","canonical_seed_titles":["Exact full paper title",'
        '"Another exact full paper title"]}. Include 2–8 plausible full canonical '
        "paper titles, never topic labels or author names. Do not use markdown fences, "
        "ask questions, search, review papers, or create relationships. Research goal:\n\n"
        f"{research_goal}"
    )
    async for event in run_agent(settings, "research-map-main", prompt):
        yield event


async def cancel_trueforge_turn(
    settings: Settings, session_id: str, turn_id: str
) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        return await TrueForgeClient(settings.trueforge_url, http_client).cancel_turn(
            session_id, turn_id
        )


def openai_provider_manifest(settings: Settings) -> dict[str, Any]:
    secret = settings.openai_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise TrueForgeConfigurationError("OPENAI_API_KEY is required")

    return {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "auth": {"api_key": secret.get_secret_value()},
        "models": [
            {
                "name": OPENAI_MODEL_NAME,
                "model_id": settings.openai_research_model,
                "properties": {"reasoning_efforts": ["low", "medium", "high"]},
            },
        ],
    }


def mcp_server_manifest(settings: Settings) -> dict[str, Any]:
    return {
        "type": "remote",
        "name": MCP_SERVER_NAME,
        "url": settings.research_map_mcp_url,
        "description": "Research map application tools",
    }


async def bootstrap_trueforge(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    manifest_dir: Path | None = None,
) -> BootstrapResult:
    """Create or replace the provider, connector, and four saved agents."""
    if client is not None:
        return await _bootstrap(
            TrueForgeClient(settings.trueforge_url, client), settings, manifest_dir
        )

    async with httpx.AsyncClient(timeout=10.0) as owned_client:
        return await _bootstrap(
            TrueForgeClient(settings.trueforge_url, owned_client),
            settings,
            manifest_dir,
        )


async def _bootstrap(
    client: TrueForgeClient,
    settings: Settings,
    manifest_dir: Path | None,
) -> BootstrapResult:
    await client.put_provider(openai_provider_manifest(settings))
    await client.put_mcp_server(mcp_server_manifest(settings))

    existing_agents = await client.list_agents()
    existing_ids = {
        agent["name"]: agent["id"]
        for agent in existing_agents
        if isinstance(agent, dict)
        and isinstance(agent.get("name"), str)
        and isinstance(agent.get("id"), str)
    }

    agent_ids: dict[str, str] = {}
    for request_body in load_agent_manifests(manifest_dir):
        name = request_body["name"]
        manifest = request_body["manifest"]
        existing_id = existing_ids.get(name)
        if existing_id is None:
            saved = await client.create_agent(name, manifest)
        else:
            saved = await client.update_agent(existing_id, manifest)

        saved_id = saved.get("id")
        if not isinstance(saved_id, str):
            raise TrueForgeError(f"TrueForge returned no id for agent {name}")
        agent_ids[name] = saved_id

    return BootstrapResult(
        provider_name=OPENAI_PROVIDER_NAME,
        mcp_server_name=MCP_SERVER_NAME,
        agent_ids=agent_ids,
    )
