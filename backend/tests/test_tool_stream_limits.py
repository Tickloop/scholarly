from __future__ import annotations

import json

import pytest

from research_map_backend.pipeline import (
    AutonomousPipelineError,
    TOOL_STREAM_MAX_ARGUMENT_CHARACTERS,
    TOOL_STREAM_MAX_CALLS,
    TOOL_STREAM_MAX_DELTAS,
    _ToolCallAccumulator,
)


def test_live_sized_fragmented_discovery_turn_reaches_large_submit() -> None:
    accumulator = _ToolCallAccumulator()
    names = [
        *(["search_engine"] * 2),
        *(["scrape_as_markdown"] * 5),
        *(["resolve_paper_metadata"] * 5),
    ]
    completed = []
    for index, name in enumerate(names):
        completed.extend(
            accumulator.ingest(
                {
                    "type": "model.message.delta",
                    "id": f"message-{index}",
                    "created_at": f"tool-{index}",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call-{index}",
                            "function": {
                                "name": name,
                                "arguments": json.dumps({"value": index}),
                            },
                        }
                    ],
                }
            )
        )

    target_length = 5_259
    prefix, suffix = '{"payload":"', '"}'
    arguments = prefix + ("x" * (target_length - len(prefix) - len(suffix))) + suffix
    assert len(arguments) == target_length
    chunks = [arguments[index : index + 4] for index in range(0, len(arguments), 4)]
    for index, chunk in enumerate(chunks):
        fragment = {"index": 0, "function": {"arguments": chunk}}
        if index == 0:
            fragment["id"] = "submit-call"
            fragment["function"]["name"] = "submit_discovery_batch"
        completed.extend(
            accumulator.ingest(
                {
                    "type": "model.message.delta",
                    "id": "submit-message",
                    "created_at": f"submit-{index}",
                    "tool_calls": [fragment],
                }
            )
        )

    assert len(completed) == 13
    assert completed[-1]["name"] == "submit_discovery_batch"
    assert completed[-1]["arguments"]["payload"] == "x" * (
        target_length - len(prefix) - len(suffix)
    )


def test_exact_tool_fragment_boundary_and_unrelated_deltas_do_not_count() -> None:
    accumulator = _ToolCallAccumulator()
    unrelated = {"type": "model.message.delta", "content": "reasoning"}
    for _ in range(TOOL_STREAM_MAX_DELTAS + 1):
        assert accumulator.ingest(unrelated) == []

    for index in range(TOOL_STREAM_MAX_DELTAS):
        assert accumulator.ingest(
            {
                "type": "model.message.delta",
                "id": "one-message",
                "created_at": str(index),
                "tool_calls": [{"index": 0}],
            }
        ) == []

    with pytest.raises(AutonomousPipelineError, match="too many tool-call deltas"):
        accumulator.ingest(
            {
                "type": "model.message.delta",
                "id": "one-message",
                "created_at": str(TOOL_STREAM_MAX_DELTAS),
                "tool_calls": [{"index": 0}],
            }
        )


def test_replayed_fragments_are_deduplicated_before_budget_counting() -> None:
    accumulator = _ToolCallAccumulator()
    event = {
        "type": "model.message.delta",
        "id": "replayed-message",
        "created_at": "stable-time",
        "tool_calls": [{"index": 0}],
    }
    for _ in range(TOOL_STREAM_MAX_DELTAS + 1):
        assert accumulator.ingest(event) == []


def test_exact_logical_call_boundary_rejects_call_1025() -> None:
    accumulator = _ToolCallAccumulator()
    for index in range(TOOL_STREAM_MAX_CALLS):
        assert accumulator.ingest(
            {
                "type": "model.message.delta",
                "id": f"message-{index}",
                "created_at": str(index),
                "tool_calls": [{"index": 0}],
            }
        ) == []

    with pytest.raises(AutonomousPipelineError, match="too many tool calls"):
        accumulator.ingest(
            {
                "type": "model.message.delta",
                "id": "message-over-limit",
                "created_at": "over-limit",
                "tool_calls": [{"index": 0}],
            }
        )


def test_exact_argument_boundary_rejects_one_extra_character() -> None:
    accumulator = _ToolCallAccumulator()
    accumulator.ingest(
        {
            "type": "model.message.delta",
            "id": "argument-message",
            "created_at": "first",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "argument-call",
                    "function": {
                        "name": "submit_discovery_batch",
                        "arguments": "x" * TOOL_STREAM_MAX_ARGUMENT_CHARACTERS,
                    },
                }
            ],
        }
    )

    with pytest.raises(AutonomousPipelineError, match="bounded field size"):
        accumulator.ingest(
            {
                "type": "model.message.delta",
                "id": "argument-message",
                "created_at": "second",
                "tool_calls": [
                    {"index": 0, "function": {"arguments": "y"}}
                ],
            }
        )
