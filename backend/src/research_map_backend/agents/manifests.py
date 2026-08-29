from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_map_backend.settings import PROJECT_ROOT

AGENT_FILENAMES = (
    "main.json",
    "discovery.json",
    "reviewer.json",
    "connection.json",
)


def load_agent_manifests(
    manifest_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the four explicit saved-agent request bodies."""
    directory = manifest_dir or PROJECT_ROOT / "agents"
    manifests: list[dict[str, Any]] = []
    names: set[str] = set()

    for filename in AGENT_FILENAMES:
        path = directory / filename
        with path.open(encoding="utf-8") as file:
            request_body = json.load(file)

        name = request_body.get("name")
        manifest = request_body.get("manifest")
        if not isinstance(name, str) or not isinstance(manifest, dict):
            raise ValueError(f"Invalid agent manifest: {path}")
        if name in names:
            raise ValueError(f"Duplicate agent name: {name}")

        names.add(name)
        manifests.append(request_body)

    return manifests
