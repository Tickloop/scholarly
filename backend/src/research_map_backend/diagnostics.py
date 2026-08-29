from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from research_map_backend.settings import Settings


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|api[_-]?key|password|secret|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token|reasoning(?:[_-]?content)?)\b"
    r"(\s*[:=]\s*)(bearer\s+[^\s,;]+|\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_PROVIDER_KEY = re.compile(r"\bsk-[a-z0-9_-]{12,}\b", re.IGNORECASE)


async def dependency_health(settings: Settings) -> dict[str, Any]:
    brightdata_key = bool(
        settings.brightdata_api_key
        and settings.brightdata_api_key.get_secret_value().strip()
    )
    results = {"brightdata_mcp": (
        "configured_active" if brightdata_key else "missing_api_key"
    )}
    storage = local_storage_health(settings.data_dir)
    return {
        "status": "ok"
        if brightdata_key and storage["status"] == "ok"
        else "degraded",
        "academic_apis": results,
        "local_storage": storage,
    }


def local_storage_health(data_dir: Path) -> dict[str, int | str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(data_dir)
    used_bytes = sum(
        path.stat().st_size
        for path in data_dir.rglob("*")
        if path.is_file()
    )
    status = "ok" if usage.free >= 100 * 1024 * 1024 else "low_space"
    return {
        "status": status,
        "data_bytes": used_bytes,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    }


def summarize_usage(events: list[Any]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for event in events:
        usage = event.payload.get("usage") if isinstance(event.payload, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost"):
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals


def public_diagnostic_value(value: Any) -> Any:
    """Defensively remove model-private reasoning and credential-shaped fields."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if (
                "reasoning" in normalized
                or normalized in {"api_key", "authorization", "password", "secret"}
                or normalized.endswith("_token")
            ):
                result[key] = "[redacted]"
            else:
                result[key] = public_diagnostic_value(item)
        return result
    if isinstance(value, list):
        return [public_diagnostic_value(item) for item in value]
    if isinstance(value, str):
        return public_diagnostic_text(value)
    return value


def public_diagnostic_text(value: str) -> str:
    """Redact secret and private-reasoning values without hiding useful errors."""
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            structured = json.loads(stripped)
        except ValueError:
            pass
        else:
            if isinstance(structured, (dict, list)):
                return json.dumps(
                    public_diagnostic_value(structured),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]", value
    )
    redacted = _BEARER_VALUE.sub("Bearer [redacted]", redacted)
    return _PROVIDER_KEY.sub("[redacted]", redacted)


def public_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return public_diagnostic_value(payload)
