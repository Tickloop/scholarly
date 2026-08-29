from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from research_map_backend.diagnostics import public_diagnostic_text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": public_diagnostic_text(record.getMessage()),
        }
        for field in (
            "canvas_id",
            "paper_id",
            "job_id",
            "run_id",
            "trueforge_session_id",
            "trueforge_turn_id",
            "tool_name",
            "event_type",
        ):
            field_value = getattr(record, field, None)
            if field_value is not None:
                value[field] = field_value
        if record.exc_info:
            value["exception"] = public_diagnostic_text(
                self.formatException(record.exc_info)
            )
        return json.dumps(value, ensure_ascii=False, default=str)


def configure_structured_logging() -> None:
    logger = logging.getLogger("research_map")
    if any(getattr(handler, "_research_map_json", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._research_map_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def context(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
