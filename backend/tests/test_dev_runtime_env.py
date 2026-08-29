from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPT = PROJECT_ROOT / "scripts" / "dev.sh"


def _resolved_frontend_urls(**environment: str) -> str:
    setup = "api_host=" + DEV_SCRIPT.read_text().split(
        "api_host=", maxsplit=1
    )[1].split("\ncleanup()", maxsplit=1)[0]
    runtime_environment = {**os.environ, **environment}
    if "VITE_API_BASE_URL" not in environment:
        runtime_environment.pop("VITE_API_BASE_URL", None)
    result = subprocess.run(
        ["zsh"],
        input=(
            setup
            + '\nprint -r -- "$VITE_API_BASE_URL|$VITE_API_PROXY_TARGET"\n'
        ),
        cwd=PROJECT_ROOT,
        env=runtime_environment,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def test_dev_script_derives_browser_safe_api_origin() -> None:
    environment = {
        "API_HOST": "0.0.0.0",
        "API_PORT": "18100",
    }
    environment.pop("VITE_API_BASE_URL", None)

    assert _resolved_frontend_urls(**environment) == (
        "http://127.0.0.1:18100|http://0.0.0.0:18100"
    )


def test_dev_script_preserves_explicit_browser_api_origin() -> None:
    assert _resolved_frontend_urls(
        API_HOST="0.0.0.0",
        API_PORT="18100",
        VITE_API_BASE_URL="https://api.example.test",
    ) == "https://api.example.test|http://0.0.0.0:18100"
