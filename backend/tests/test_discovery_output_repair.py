import asyncio
import json
import re
from pathlib import Path

import pytest

from research_map_backend.pipeline import (
    DISCOVERY_PROMPT_MAX_CHARACTERS,
    SECTION_KEYS,
    AutonomousPipelineError,
    _discovery_prompt,
    _lexically_relevant_candidates,
    _run_validated_discovery,
    _validate_discovery_output,
    run_autonomous_pipeline,
)
from research_map_backend.db import Database
from research_map_backend.models import AgentRun, Canvas
from research_map_backend.research_search import ResearchSearchError
from research_map_backend.settings import Settings


def _candidates(count: int = 10) -> list[dict]:
    return [
        {
            "candidate_id": f"candidate-{index}",
            "title": f"Paper {index}",
            "authors": ["A. Author"],
            "year": 2020 + index,
            "abstract": f"Verified abstract {index}.",
            "url": f"https://example.test/{index}",
            "pdf_url": f"https://example.test/{index}.pdf",
        }
        for index in range(count)
    ]


def _valid_output(count: int = 10) -> str:
    return json.dumps(
        {
            "decisions": [
                {
                    "candidate_id": f"candidate-{index}",
                    "score": {
                        "relevance": 25,
                        "source_credibility": 12,
                        "age_adjusted_impact": 10,
                        "full_text_availability": 12,
                        "novelty": 8,
                        "relationship_potential": 8,
                    },
                    "accepted": True,
                    "reason": "Relevant verified primary research.",
                }
                for index in range(count)
            ]
        }
    )


@pytest.mark.parametrize("candidate_count", [3, 5, 10])
def test_discovery_output_cardinality_matches_actual_batch(
    candidate_count: int,
) -> None:
    candidates = _candidates(candidate_count)
    result = _validate_discovery_output(_valid_output(candidate_count), candidates)
    assert len(result) == candidate_count
    with pytest.raises(AutonomousPipelineError, match="every candidate"):
        _validate_discovery_output(_valid_output(candidate_count - 1), candidates)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'repair.sqlite3'}",
        upstream_max_attempts=3,
        retry_base_seconds=0,
    )


def test_exact_live_greeting_gets_fresh_full_output_repair_turn(
    tmp_path: Path,
) -> None:
    requests: list[str] = []
    events: list[tuple[str, dict]] = []
    outputs = [
        "How can I help with the next step?",
        f"Here is the complete replacement.\n```json\n{_valid_output()}\n```",
    ]

    async def source(_settings: Settings, agent_name: str, prompt: str):
        assert agent_name == "research-map-discovery"
        requests.append(prompt)
        yield {"type": "session.created", "session_id": f"session-{len(requests)}"}
        yield {"type": "turn.created", "turn_id": f"turn-{len(requests)}"}
        yield {"type": "model.message", "content": outputs[len(requests) - 1]}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    result = asyncio.run(
        _run_validated_discovery(
            _settings(tmp_path),
            "discovery:2",
            "Evaluate the exact ten supplied candidates and return decisions JSON.",
            _candidates(),
            emit,
            source,
        )
    )

    assert len(result) == 10
    assert len(requests) == 2
    assert "How can I help with the next step?" in requests[1]
    assert "complete replacement response" in requests[1]
    assert "Original task:" in requests[1]
    retry = [payload for event, payload in events if event == "agent.output.retry"]
    assert retry == [
        {
            "agent": "research-map-discovery",
            "thread_id": "discovery:2",
            "attempt": 1,
            "next_attempt": 2,
            "code": "invalid_agent_output",
            "message": "Discovery agent returned invalid JSON.",
        }
    ]
    started_threads = [
        payload["thread_id"]
        for event, payload in events
        if event == "agent.thread.started"
        and "session_id" not in payload
        and "turn_id" not in payload
    ]
    assert started_threads == ["discovery:2", "discovery:2:repair:2"]


@pytest.mark.parametrize(
    "wrapped",
    (
        lambda body: f"Result follows:\n```json\n{body}\n```\nDone.",
        lambda body: json.dumps(body),
        lambda body: json.dumps({"role": "assistant", "content": body}),
        lambda body: json.dumps(
            {"role": "assistant", "content": [{"type": "text", "text": body}]}
        ),
        lambda body: json.dumps(
            {"type": "message", "message": {"role": "assistant", "content": body}}
        ),
    ),
)
def test_legitimate_model_wrappers_preserve_strict_discovery_schema(wrapped) -> None:
    result = _validate_discovery_output(wrapped(_valid_output()), _candidates())

    assert len(result) == 10
    assert all(item["accepted"] for item in result)


@pytest.mark.parametrize(
    "invalid",
    (
        '{"decisions":[]}',
        '{"role":"assistant","content":"{\\"decisions\\":[]}"}',
        '{"decisions":[]} {"decisions":[]}',
    ),
)
def test_wrappers_do_not_allow_schema_invalid_or_ambiguous_discovery_data(
    invalid: str,
) -> None:
    with pytest.raises(AutonomousPipelineError):
        _validate_discovery_output(invalid, _candidates())


def test_discovery_output_repair_never_exceeds_configured_attempts(
    tmp_path: Path,
) -> None:
    calls = 0

    async def source(_settings: Settings, _agent_name: str, _prompt: str):
        nonlocal calls
        calls += 1
        yield {"type": "model.message", "content": "How can I help with the next step?"}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    with pytest.raises(AutonomousPipelineError) as caught:
        asyncio.run(
            _run_validated_discovery(
                _settings(tmp_path),
                "discovery:2",
                "Evaluate candidates.",
                _candidates(),
                emit,
                source,
            )
        )

    assert caught.value.code == "invalid_agent_output"
    assert calls == 3


def test_schema_error_also_gets_one_full_replacement_chance(tmp_path: Path) -> None:
    calls = 0

    async def source(_settings: Settings, _agent_name: str, prompt: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            content = '{"decisions":[]}'
        else:
            assert "one decision for every candidate" in prompt
            content = _valid_output()
        yield {"type": "model.message", "content": content}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    result = asyncio.run(
        _run_validated_discovery(
            _settings(tmp_path),
            "discovery:2",
            "Evaluate candidates.",
            _candidates(),
            emit,
            source,
        )
    )

    assert calls == 2
    assert len(result) == 10


def test_discovery_prompt_compacts_exact_oversized_author_shape() -> None:
    candidates = _candidates()
    for index, candidate in enumerate(candidates):
        candidate.update(
            {
                "source_provider": "arxiv",
                "authors": [f"Author {index}-{author}-" + "x" * 180 for author in range(180)],
                "abstract": "attention mechanism transformer " + "evidence " * 2_000,
                "open_access_locations": [{"large": "x" * 20_000}],
            }
        )

    prompt = _discovery_prompt("attention mechanism " * 2_000, candidates)
    serialized = json.loads(prompt.split("Candidates:\n", 1)[1])

    assert len(prompt) <= DISCOVERY_PROMPT_MAX_CHARACTERS
    assert len(serialized) == 10
    assert len(serialized[0]["authors"]) == 5
    assert serialized[0]["authors_omitted_count"] == 175
    assert len(serialized[0]["abstract"]) <= 1_800
    assert "open_access_locations" not in serialized[0]


def test_lexical_prefilter_drops_obviously_unrelated_arxiv_fallback_records() -> None:
    candidates = [
        {
            "candidate_id": f"arxiv:gr-{index}",
            "title": f"KAGRA observation of gravitational wave event {index}",
            "abstract": "Interferometer calibration and compact binary strain analysis.",
            "source_provider": "arxiv",
        }
        for index in range(10)
    ]
    candidates.append(
        {
            "candidate_id": "arxiv:attention",
            "title": "Attention Is All You Need",
            "abstract": "Transformer self-attention replaces recurrent sequence models.",
            "source_provider": "arxiv",
        }
    )

    relevant = _lexically_relevant_candidates(
        candidates,
        "How did attention mechanisms in AI evolve?",
        ["Attention Is All You Need"],
    )

    assert [item["candidate_id"] for item in relevant] == ["arxiv:attention"]


def test_lexical_prefilter_rejects_exact_live_gravity_wave_batch() -> None:
    live_rows = [
        (
            "2403.03004",
            "Ultralight vector dark matter search using data from the KAGRA O3GK run",
            "The detector can search at scale and with calibrated observations.",
        ),
        (
            "2607.19293",
            "GWTC-5.0: Tests of General Relativity",
            "Tests for gravitational-wave generation and compact binaries.",
        ),
        (
            "2407.12867",
            "Swift-BAT GUANO follow-up of gravitational-wave triggers in the third LIGO-Virgo-KAGRA observing run",
            "Follow-up observations for all alerts and compact mergers.",
        ),
        (
            "2605.27225",
            "GWTC-5.0: Observations from the Second Part of the Fourth LIGO-Virgo-KAGRA Observing Run and Updates to the Gravitational-Wave Transient Catalog",
            "A catalog for all gravitational-wave observations and sources.",
        ),
        (
            "2501.01495",
            "Search for continuous gravitational waves from known pulsars in the first part of the fourth LIGO-Virgo-KAGRA observing run",
            "The analysis can constrain pulsars with calibrated strain.",
        ),
        (
            "2605.27223",
            "GWTC-5.0: An Introduction to Version 5.0 of the Gravitational-Wave Transient Catalog",
            "An introduction supporting understanding of the source catalog.",
        ),
        (
            "2410.16565",
            "Search for gravitational waves emitted from SN 2023ixf",
            "A supernova emission mechanism is constrained with detector data.",
        ),
        (
            "2605.27227",
            "GWTC-5.0: Constraints on the Cosmic Expansion Rate and Modified Gravitational-wave Propagation",
            "Cosmic expansion constraints from compact binary events.",
        ),
        (
            "2603.25938",
            "Narrowband searches for continuous gravitational waves from known pulsars in the first two parts of the fourth LIGO--Virgo--KAGRA observing run",
            "The search can constrain continuous signals with detector strain.",
        ),
        (
            "2508.18083",
            "GWTC-4.0: Population Properties of Merging Compact Binaries",
            "Population inference for merging compact binaries with observations.",
        ),
    ]
    candidates = [
        {
            "candidate_id": f"arxiv:{paper_id}",
            "title": title,
            "abstract": abstract,
            "source_provider": "arxiv",
        }
        for paper_id, title, abstract in live_rows
    ]
    seeds = [
        "Attention Is All You Need",
        "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "Neural Machine Translation by Jointly Learning to Align and Translate",
        "Show and Tell: A Neural Image Caption Generator",
        "Words Worth: Generalized Scale-Invariant Language-Image Retrieval",
    ]

    assert _lexically_relevant_candidates(
        candidates,
        "Can you research how attention mechanism works in AI and build a map of how it evolved?",
        seeds,
    ) == []


def test_lexical_prefilter_keeps_exact_seeds_and_two_term_topic_matches() -> None:
    candidates = [
        {
            "candidate_id": "exact-attention",
            "title": "Attention Is All You Need",
            "abstract": "",
        },
        {
            "candidate_id": "exact-bert",
            "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
            "abstract": "",
        },
        {
            "candidate_id": "attention-transformer",
            "title": "Efficient self-attention for transformer models",
            "abstract": "Sequence modeling.",
        },
        {
            "candidate_id": "neural-translation",
            "title": "Neural machine-translation alignment",
            "abstract": "Sequence generation.",
        },
        {
            "candidate_id": "plural-hyphen",
            "title": "Self-Attention Mechanisms for Transformers",
            "abstract": "Architecture analysis.",
        },
        {
            "candidate_id": "generic-mechanism",
            "title": "A detector mechanism for supernova signals",
            "abstract": "Unrelated astrophysics.",
        },
        {
            "candidate_id": "generic-generation",
            "title": "Gravitational-wave generation from binary mergers",
            "abstract": "Unrelated astrophysics.",
        },
    ]
    seeds = [
        "Attention Is All You Need",
        "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "Neural Machine Translation by Jointly Learning to Align and Translate",
    ]

    relevant = _lexically_relevant_candidates(
        candidates,
        "attention mechanisms, BERT, and neural translation",
        seeds,
    )

    assert [candidate["candidate_id"] for candidate in relevant] == [
        "exact-attention",
        "exact-bert",
        "attention-transformer",
        "neural-translation",
        "plural-hyphen",
    ]


def test_off_topic_provider_fallback_stops_before_discovery_model(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Attention", research_goal="Map attention mechanisms.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    model_called = False

    async def candidate_source(_goal: str, _brief: str, _count: int):
        return [
            {
                "candidate_id": f"arxiv:gr-{index}",
                "title": f"LIGO and KAGRA gravitational wave observation {index}",
                "authors": ["A. Physicist"],
                "year": 2020,
                "abstract": "Detector strain calibration for compact binary mergers.",
                "url": f"https://arxiv.org/abs/gr-{index}",
                "pdf_url": f"https://arxiv.org/pdf/gr-{index}",
                "source_provider": "arxiv",
            }
            for index in range(10)
        ]

    async def source(_settings: Settings, _agent_name: str, _prompt: str):
        nonlocal model_called
        model_called = True
        if False:
            yield {}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="How did attention mechanisms in AI evolve?",
            research_brief="Trace attention and Transformer architectures.",
            canonical_seed_titles=["Attention Is All You Need"],
            emit=emit,
            agent_source=source,
            candidate_source=candidate_source,
        )
    )
    database.dispose()

    assert model_called is False
    assert summary["status"] == "completed"
    assert summary["stop_reason"] == "insufficient_relevant_candidates"
    assert summary["coverage"] == [
        {
            "batch": 1,
            "started": False,
            "requested_candidates": 10,
            "actual_candidates": 0,
            "unique_candidates": 0,
            "accepted": 0,
            "reviewed": 0,
            "relationships": 0,
        }
    ]


def test_injected_candidate_context_filters_irrelevant_crispr_result(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'crispr-overfetch.sqlite3'}",
        discovery_max_batches=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="CRISPR", research_goal="Map CRISPR gene editing.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    requested: list[int] = []
    discovery_ids: list[str] = []

    async def recovered_candidates(
        _goal: str,
        _brief: str,
        candidate_limit: int,
    ):
        requested.append(candidate_limit)
        rows = [
            {
                "candidate_id": "arxiv:1602.01876",
                "title": "Primer on the Gene Ontology",
                "authors": ["A. Author"],
                "year": 2016,
                "abstract": "Ontology annotation reference.",
                "url": "https://arxiv.org/abs/1602.01876",
                "pdf_url": "https://arxiv.org/pdf/1602.01876",
                "source_provider": "arxiv",
            }
        ]
        rows.extend(
            {
                "candidate_id": f"arxiv:2501.{index:05d}",
                "title": f"CRISPR gene editing method {index}",
                "authors": ["A. Author"],
                "year": 2025,
                "abstract": "Verified CRISPR gene editing evidence.",
                "url": f"https://arxiv.org/abs/2501.{index:05d}",
                "pdf_url": f"https://arxiv.org/pdf/2501.{index:05d}",
                "source_provider": "arxiv",
            }
            for index in range(1, candidate_limit)
        )
        return rows

    async def source(_settings: Settings, agent_name: str, prompt: str):
        assert agent_name == "research-map-discovery"
        candidates = json.loads(prompt.split("Candidates:\n", 1)[1])
        discovery_ids.extend(item["candidate_id"] for item in candidates)
        yield {
            "type": "model.message",
            "content": json.dumps(
                {
                    "decisions": [
                        {
                            "candidate_id": item["candidate_id"],
                            "score": {
                                "relevance": 10,
                                "source_credibility": 10,
                                "age_adjusted_impact": 5,
                                "full_text_availability": 10,
                                "novelty": 5,
                                "relationship_potential": 5,
                            },
                            "accepted": False,
                            "reason": "Not worthwhile for this focused canvas.",
                        }
                        for item in candidates
                    ]
                }
            ),
        }
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map the evolution of CRISPR gene editing from bacterial adaptive immunity through base editing and prime editing.",
            research_brief="Trace verified CRISPR gene editing research.",
            canonical_seed_titles=[
                "A Programmable Dual-RNA-Guided DNA Endonuclease in Adaptive Bacterial Immunity"
            ],
            emit=emit,
            agent_source=source,
            candidate_source=recovered_candidates,
        )
    )
    database.dispose()

    assert requested == [10]
    assert len(discovery_ids) == 9
    assert "arxiv:1602.01876" not in discovery_ids
    assert summary["coverage"][0]["unique_candidates"] == 9
    assert summary["coverage"][0]["started"] is True


def test_later_academic_search_failure_completes_clean_checkpoint(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'academic-checkpoint.sqlite3'}",
        discovery_max_batches=2,
        upstream_max_attempts=1,
        retry_base_seconds=0,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Causal", research_goal="Map causal inference.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    search_calls = 0
    events: list[tuple[str, dict]] = []

    async def candidate_source(_goal: str, _brief: str, _count: int):
        nonlocal search_calls
        search_calls += 1
        if search_calls > 1:
            raise ResearchSearchError(
                "All academic providers are throttled.", retryable=True
            )
        return _candidates()

    async def source(_settings: Settings, agent_name: str, prompt: str):
        if agent_name == "research-map-discovery":
            content = json.loads(_valid_output())
            for index, decision in enumerate(content["decisions"]):
                decision["accepted"] = index == 0
            output = json.dumps(content)
        else:
            evidence_id = re.search(r'"id":\s*"([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
            output = json.dumps(
                {
                    "sections": {
                        key: {
                            "text": "Supported by supplied evidence.",
                            "evidence_ids": [evidence_id],
                            "confidence": "medium",
                        }
                        for key in SECTION_KEYS
                    }
                }
            )
        yield {"type": "model.message", "content": output}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def paper_source(_settings: Settings, candidate: dict):
        return {
            "url": candidate["url"],
            "source_type": "abstract",
            "retrieval_status": "abstract_fallback",
            "pages": [{"page": None, "text": candidate["abstract"]}],
        }

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map causal inference.",
            research_brief="Trace causal inference.",
            canonical_seed_titles=["A Complete Canonical Causal Inference Paper"],
            emit=emit,
            agent_source=source,
            candidate_source=candidate_source,
            paper_source_acquirer=paper_source,
        )
    )
    with database.session_context() as session:
        stored = session.get(Canvas, canvas_id).research_brief["pipeline_summary"]
    database.dispose()

    assert search_calls == 2
    assert summary["status"] == "completed"
    assert summary["accepted_papers"] == summary["completed_reviews"] == 1
    assert summary["paper_failures"] == []
    assert summary["stop_reason"] == "candidate_context_unavailable_after_checkpoint"
    assert summary["coverage"][-1] == {
        "batch": 2,
        "started": False,
        "requested_candidates": 10,
        "actual_candidates": 0,
        "unique_candidates": 0,
        "accepted": 0,
        "reviewed": 0,
        "relationships": 0,
    }
    assert stored == summary
    assert any(
        event == "tool.completed"
        and payload.get("reason") == "candidate_context_unavailable_after_checkpoint"
        for event, payload in events
    )


def test_later_discovery_exhaustion_finishes_from_valid_checkpoint(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'checkpoint.sqlite3'}",
        upstream_max_attempts=3,
        retry_base_seconds=0,
        discovery_max_batches=2,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Checkpoint", research_goal="Map attention mechanisms.")
        session.add(canvas)
        session.flush()
        run = AgentRun(canvas_id=canvas.id, status="running")
        session.add(run)
        session.commit()
        canvas_id, run_id = canvas.id, run.id
    search_batch = 0
    discovery_calls = 0
    events: list[tuple[str, dict]] = []

    async def candidate_source(_goal: str, _brief: str, _count: int):
        nonlocal search_batch
        search_batch += 1
        return [
            {
                "candidate_id": f"candidate-b{search_batch}-{index}",
                "title": f"Attention paper batch {search_batch} item {index}",
                "authors": ["A. Author"],
                "year": 2020 + index,
                "abstract": "Verified attention mechanism evidence.",
                "url": f"https://example.test/b{search_batch}/{index}",
                "pdf_url": f"https://example.test/b{search_batch}/{index}.pdf",
                "source_provider": "mock",
            }
            for index in range(10)
        ]

    async def source(_settings: Settings, agent_name: str, prompt: str):
        nonlocal discovery_calls
        if agent_name == "research-map-discovery":
            discovery_calls += 1
            if "candidate-b1-0" in prompt:
                content = json.dumps(
                    {
                        "decisions": [
                            {
                                "candidate_id": f"candidate-b1-{index}",
                                "score": {
                                    "relevance": 25,
                                    "source_credibility": 12,
                                    "age_adjusted_impact": 10,
                                    "full_text_availability": 12,
                                    "novelty": 8,
                                    "relationship_potential": 8,
                                },
                                "accepted": index == 0,
                                "reason": "One useful anchor; remaining records are redundant.",
                            }
                            for index in range(10)
                        ]
                    }
                )
            else:
                content = "How can I help with the next step?"
        else:
            evidence_id = re.search(r'"id":\s*"([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
            content = json.dumps(
                {
                    "sections": {
                        key: {
                            "text": "Supported by the supplied evidence.",
                            "evidence_ids": [evidence_id],
                            "confidence": "medium",
                        }
                        for key in SECTION_KEYS
                    }
                }
            )
        yield {"type": "model.message", "content": content}
        yield {"type": "turn.done", "state": {"status": "done"}}

    async def paper_source(_settings: Settings, candidate: dict):
        return {
            "url": candidate["url"],
            "source_type": "abstract",
            "retrieval_status": "abstract_fallback",
            "pages": [{"page": None, "text": candidate["abstract"]}],
        }

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    summary = asyncio.run(
        run_autonomous_pipeline(
            database=database,
            settings=settings,
            canvas_id=canvas_id,
            run_id=run_id,
            research_goal="Map attention mechanisms.",
            research_brief="Trace attention mechanisms.",
            canonical_seed_titles=["Attention Is All You Need"],
            emit=emit,
            agent_source=source,
            candidate_source=candidate_source,
            paper_source_acquirer=paper_source,
        )
    )
    with database.session_context() as session:
        stored_summary = session.get(Canvas, canvas_id).research_brief["pipeline_summary"]
    database.dispose()

    assert discovery_calls == 4
    assert summary["status"] == "completed"
    assert summary["accepted_papers"] == 1
    assert summary["completed_reviews"] == 1
    assert summary["stop_reason"] == "discovery_output_exhausted_after_checkpoint"
    assert stored_summary == summary
    assert summary["coverage"][-1]["batch"] == 2
    assert summary["coverage"][-1]["started"] is False
    assert any(
        event == "tool.completed"
        and payload.get("reason") == "discovery_output_exhausted_after_checkpoint"
        for event, payload in events
    )
