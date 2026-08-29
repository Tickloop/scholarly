from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx


def stub_main() -> None:
    """Run the deterministic goal-to-paper-review-edge test."""
    import pytest

    test = Path(__file__).resolve().parents[2] / "tests" / "test_pipeline.py"
    raise SystemExit(
        pytest.main(
            [
                "-q",
                f"{test}::test_goal_runs_complete_autonomous_pipeline_before_terminal_sse",
            ]
        )
    )


def live_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="research-map-live-smoke",
        description="Opt-in goal-to-edge smoke through the running API, TrueForge, and OpenAI.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--goal",
        default="Map foundational and recent retrieval-augmented generation research.",
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    if os.environ.get("RUN_RESEARCH_MAP_LIVE_SMOKE") != "1":
        parser.error("set RUN_RESEARCH_MAP_LIVE_SMOKE=1 to allow live inference")
    base = args.api_url.rstrip("/")
    timeout = httpx.Timeout(args.timeout, connect=5.0)
    with httpx.Client(timeout=timeout) as client:
        canvas_response = client.post(
            f"{base}/api/v1/canvases",
            json={
                "name": f"Live smoke {datetime.now(UTC).isoformat()}",
                "research_goal": args.goal,
            },
        )
        canvas_response.raise_for_status()
        canvas_id = canvas_response.json()["canvas"]["id"]
        run_response = client.post(f"{base}/api/v1/canvases/{canvas_id}/builds")
        run_response.raise_for_status()
        run_id = run_response.json()["id"]
        terminal = None
        with client.stream("GET", f"{base}/api/v1/runs/{run_id}/events") as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                print(json.dumps(event, ensure_ascii=False), flush=True)
                if event.get("type") in {"run.completed", "run.failed", "run.cancelled"}:
                    terminal = event
        if terminal is None or terminal.get("type") != "run.completed":
            print("live smoke did not complete", file=sys.stderr)
            raise SystemExit(1)
        snapshot = client.get(f"{base}/api/v1/canvases/{canvas_id}")
        snapshot.raise_for_status()
        body = snapshot.json()
        if not body.get("papers") or not body.get("relationships"):
            print("live smoke completed without papers and an edge", file=sys.stderr)
            raise SystemExit(1)
    raise SystemExit(0)
