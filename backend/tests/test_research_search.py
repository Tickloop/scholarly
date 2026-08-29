import asyncio

import httpx
import pytest

from research_map_backend.research_search import (
    _arxiv_query,
    ResearchSearchError,
    _openalex_params,
    _query,
    _search_seed_titles,
    _seed_titles,
    meaningful_research_terms,
    search_academic_papers,
    search_arxiv,
    search_openalex,
)


def _openalex_rows() -> list[dict]:
    return [
        {
            "id": f"https://openalex.org/W{index}",
            "doi": f"https://doi.org/10.1000/{index}",
            "display_name": f"Verified paper {index}",
            "publication_year": 2020 + index,
            "publication_date": f"{2020 + index}-01-01",
            "primary_location": {
                "landing_page_url": f"https://example.test/{index}",
                "source": {"display_name": "Verified venue"},
            },
            "best_oa_location": {
                "pdf_url": f"https://example.test/{index}.pdf"
            },
            "authorships": [
                {"author": {"display_name": f"Verified author {index}"}}
            ],
            "cited_by_count": index,
            "abstract_inverted_index": {
                "Verified": [0],
                "abstract": [1],
                str(index): [2],
            },
            "ids": {"arxiv": f"https://arxiv.org/abs/2401.{index:05d}"},
            "type": "article",
            "is_retracted": False,
        }
        for index in range(10)
    ]


def _arxiv_feed(start: int, count: int, *, total: int | None = None) -> str:
    entries = "".join(
        f"""
        <entry>
          <id>http://arxiv.org/abs/2401.{index:05d}v2</id>
          <updated>2024-01-02T00:00:00Z</updated>
          <published>2024-01-01T00:00:00Z</published>
          <title>Verified arXiv paper {index}</title>
          <summary>Verified abstract evidence {index}.</summary>
          <author><name>Verified Author {index}</name></author>
          <arxiv:doi>10.48550/arXiv.2401.{index:05d}</arxiv:doi>
          <link title="pdf" href="http://arxiv.org/pdf/2401.{index:05d}v2" type="application/pdf"/>
        </entry>
        """
        for index in range(start, start + count)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>{total if total is not None else count}</opensearch:totalResults>
      {entries}
    </feed>"""


def _arxiv_feed_titles(rows: list[tuple[str, str]]) -> str:
    entries = "".join(
        f"""
        <entry>
          <id>http://arxiv.org/abs/{paper_id}v1</id>
          <updated>2024-01-02T00:00:00Z</updated>
          <published>2024-01-01T00:00:00Z</published>
          <title>{title}</title>
          <summary>Verified abstract evidence for {title}.</summary>
          <author><name>Verified Author</name></author>
          <link title="pdf" href="http://arxiv.org/pdf/{paper_id}v1" type="application/pdf"/>
        </entry>
        """
        for paper_id, title in rows
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      {entries}
    </feed>"""


EMPTY_ARXIV_FEED = _arxiv_feed(0, 0)


def test_semantic_scholar_429_falls_back_to_verified_openalex_candidates() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.host or "")
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429, json={"message": "rate limited"})
        assert request.url.params["search"] == (
            "reliable agent evaluation without human approval"
        )
        return httpx.Response(200, json={"results": _openalex_rows()})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await search_academic_papers(
                "Map reliable agent evaluation without human approval.",
                "**Goal**\nMap reliable agent evaluation without human approval.",
                10,
                client=client,
            )

    candidates = asyncio.run(run())

    assert requests == ["api.semanticscholar.org", "api.openalex.org"]
    assert len(candidates) == 10
    assert {candidate["source_provider"] for candidate in candidates} == {
        "openalex"
    }
    assert candidates[0]["candidate_id"] == "openalex:W0"
    assert candidates[0]["abstract"] == "Verified abstract 0"
    assert candidates[0]["pdf_url"] == "https://example.test/0.pdf"
    assert candidates[0]["arxiv_id"] == "2401.00000"


def test_openalex_returns_ninety_five_verified_results_when_one_hundred_requested() -> None:
    rows = []
    for index in range(95):
        row = {
            **_openalex_rows()[0],
            "id": f"https://openalex.org/PARTIAL{index}",
            "display_name": f"Verified partial paper {index}",
            "doi": f"https://doi.org/10.2000/{index}",
            "ids": {"arxiv": f"https://arxiv.org/abs/2501.{index:05d}"},
        }
        rows.append(row)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429, json={"message": "rate limited"})
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=EMPTY_ARXIV_FEED)
        assert request.url.params["per-page"] == "100"
        return httpx.Response(200, json={"results": rows})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_academic_papers(
                "partial academic results", "", 100, client=client
            )

    candidates = asyncio.run(run())
    assert len(candidates) == 95
    assert all(item["source_provider"] == "openalex" for item in candidates)


def test_low_nonzero_semantic_result_falls_back_and_merges_openalex() -> None:
    semantic_rows = [
        {
            "paperId": "S1",
            "title": "Semantic partial",
            "year": 2024,
            "abstract": "Verified",
            "authors": [],
            "url": "https://example.test/semantic",
            "externalIds": {},
            "openAccessPdf": {"url": "https://example.test/semantic.pdf"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json={"data": semantic_rows})
        return httpx.Response(200, json={"results": _openalex_rows()})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_academic_papers("fallback topic", "", 10, client=client)

    candidates = asyncio.run(run())
    assert len(candidates) == 10
    assert candidates[0]["candidate_id"] == "semantic-scholar:S1"
    assert any(item["source_provider"] == "openalex" for item in candidates)


def test_both_temporary_provider_failures_return_one_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status_code = 429 if request.url.host == "api.semanticscholar.org" else 503
        return httpx.Response(
            status_code,
            headers={"Retry-After": "7" if status_code == 429 else "9"},
            json={"message": "unavailable"},
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            await search_academic_papers(
                "agent research", "## Research Brief", 10, client=client
            )

    with pytest.raises(ResearchSearchError) as caught:
        asyncio.run(run())

    message = str(caught.value)
    assert "Academic search providers are unavailable" in message
    assert "Semantic Scholar" in message
    assert "OpenAlex" in message
    assert "arXiv" in message
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 9


def test_both_successful_zero_result_providers_are_not_classified_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=EMPTY_ARXIV_FEED)
        return httpx.Response(
            200,
            json={"data": []}
            if request.url.host == "api.semanticscholar.org"
            else {"results": []},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await search_academic_papers("no matching work", "", 10, client=client)

    with pytest.raises(ResearchSearchError) as caught:
        asyncio.run(run())
    assert caught.value.no_results is True
    assert caught.value.retryable is False
    assert "no verified candidates" in str(caught.value)
    assert "unavailable" not in str(caught.value)


def test_both_throttled_metadata_providers_fall_back_to_official_arxiv() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.host or "")
        if request.url.host in {"api.semanticscholar.org", "api.openalex.org"}:
            return httpx.Response(
                429, headers={"Retry-After": "39701"}, json={"message": "limited"}
            )
        assert request.url.host == "export.arxiv.org"
        assert request.url.params["search_query"] == (
            "all:diffusion image generation"
        )
        assert '"' not in request.url.params["search_query"]
        return httpx.Response(200, text=_arxiv_feed(0, 10))

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_academic_papers(
                "foundational diffusion image generation", "", 10, client=client
            )

    candidates = asyncio.run(run())

    assert requests == [
        "api.semanticscholar.org",
        "api.openalex.org",
        "export.arxiv.org",
    ]
    assert len(candidates) == 10
    assert all(item["source_provider"] == "arxiv" for item in candidates)
    assert candidates[0]["arxiv_id"] == "2401.00000"
    assert candidates[0]["url"] == "https://arxiv.org/abs/2401.00000"
    assert candidates[0]["pdf_url"] == "https://arxiv.org/pdf/2401.00000v2"


def test_arxiv_partial_results_are_returned_and_not_provider_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {"api.semanticscholar.org", "api.openalex.org"}:
            return httpx.Response(429, json={"message": "limited"})
        return httpx.Response(200, text=_arxiv_feed(0, 4))

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_academic_papers("language model alignment", "", 10, client=client)

    candidates = asyncio.run(run())

    assert len(candidates) == 4
    assert all(item["source_provider"] == "arxiv" for item in candidates)


def test_arxiv_paginates_twice_and_deduplicates_verified_atom_entries() -> None:
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        starts.append(start)
        return httpx.Response(200, text=_arxiv_feed(start, 50, total=60))

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_arxiv("retrieval augmented generation", "", 60, client=client)

    candidates = asyncio.run(run())

    assert starts == [0, 50]
    assert len(candidates) == 60
    assert len({item["candidate_id"] for item in candidates}) == 60


def test_arxiv_query_is_unquoted_sanitized_and_parses_live_shaped_atom() -> None:
    requests: list[str] = []
    goal = (
        'Foundational retrieval-augmented generation OR all:"injected" '
        "and dense retrieval for open-domain QA"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["search_query"]
        requests.append(query)
        assert query == (
            "all:retrieval augmented generation injected dense open domain"
        )
        assert '"' not in query
        assert ":" not in query.removeprefix("all:")
        assert " OR " not in query
        return httpx.Response(200, text=_arxiv_feed(0, 2))

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_arxiv(goal, "", 2, client=client)

    candidates = asyncio.run(run())

    assert requests == [f"all:{_arxiv_query(goal)}"]
    assert len(candidates) == 2
    assert candidates[0]["url"].startswith("https://arxiv.org/abs/")
    assert candidates[0]["pdf_url"].startswith("https://arxiv.org/pdf/")
    assert candidates[0]["abstract"] == "Verified abstract evidence 0."


def test_research_terms_and_arxiv_query_remove_generic_goal_language() -> None:
    goal = "Can you research how attention mechanisms work in AI and build a map of how it evolved?"

    assert meaningful_research_terms(goal) == ["attention", "mechanism"]
    assert _arxiv_query(goal) == "attention mechanism"


def test_research_terms_normalize_plural_and_hyphen_forms() -> None:
    assert meaningful_research_terms(
        "Self-attention mechanisms for transformer models and neural translations"
    ) == [
        "self",
        "attention",
        "mechanism",
        "transformer",
        "model",
        "neural",
        "translation",
    ]


def test_arxiv_resolves_canonical_seeds_first_and_deduplicates_topic_fill() -> None:
    dpr = "Dense Passage Retrieval for Open-Domain Question Answering"
    rag = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    brief = f"""## Canonical seed papers
- **{dpr}**
- **{rag}**
"""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["search_query"]
        queries.append(query)
        if query == 'ti:"Dense Passage Retrieval for Open-Domain Question Answering"':
            return httpx.Response(200, text=_arxiv_feed_titles([("2004.04906", dpr)]))
        if query == 'ti:"Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"':
            return httpx.Response(200, text=_arxiv_feed_titles([("2005.11401", rag)]))
        assert query.startswith("all:")
        return httpx.Response(
            200,
            text=_arxiv_feed_titles(
                [
                    ("2005.11401", rag),
                    ("2002.08909", "REALM Retrieval Augmented Language Model Pre Training"),
                ]
            ),
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_arxiv(
                "Map foundational retrieval augmented generation.", brief, 4, client=client
            )

    candidates = asyncio.run(run())

    assert set(queries[:2]) == {
        'ti:"Dense Passage Retrieval for Open-Domain Question Answering"',
        'ti:"Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"',
    }
    assert [candidate["arxiv_id"] for candidate in candidates] == [
        "2004.04906",
        "2005.11401",
        "2002.08909",
    ]
    assert all(candidate["url"].startswith("https://") for candidate in candidates)
    assert all(candidate["pdf_url"].startswith("https://") for candidate in candidates)


def test_arxiv_keeps_topic_partial_when_seeds_fail_or_match_weakly() -> None:
    seed_one = "Canonical Methods for Reliable Evidence Grounded Generation"
    seed_two = "Foundational Retrieval Systems for Open Domain Question Answering"
    brief = f"""## Canonical seed papers
- **{seed_one}**
- **{seed_two}**
"""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["search_query"]
        if query.startswith('ti:"Canonical Methods'):
            return httpx.Response(
                200,
                text=_arxiv_feed_titles(
                    [("2401.00001", "Unrelated Survey of Distributed Databases")]
                ),
            )
        if query.startswith('ti:"Foundational Retrieval'):
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            text=_arxiv_feed_titles(
                [("2401.00003", "Evidence Grounded Generation with Neural Retrieval")]
            ),
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_arxiv(
                "evidence grounded generation", brief, 5, client=client
            )

    candidates = asyncio.run(run())

    assert [candidate["arxiv_id"] for candidate in candidates] == ["2401.00003"]


def test_arxiv_seed_lookups_and_topic_fill_run_concurrently() -> None:
    dpr = "Dense Passage Retrieval for Open-Domain Question Answering"
    rag = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    brief = f"## Canonical seed papers\n- **{dpr}**\n- **{rag}**"

    class ConcurrentTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0.01)
                query = request.url.params["search_query"]
                if query.startswith('ti:"Dense Passage'):
                    rows = [("2004.04906", dpr)]
                elif query.startswith('ti:"Retrieval-Augmented'):
                    rows = [("2005.11401", rag)]
                else:
                    rows = [("2002.08909", "REALM Retrieval Augmented Language Model Pre Training")]
                return httpx.Response(200, text=_arxiv_feed_titles(rows), request=request)
            finally:
                self.active -= 1

    async def run(transport: ConcurrentTransport) -> list[dict]:
        async with httpx.AsyncClient(transport=transport) as client:
            return await search_arxiv("foundational RAG", brief, 5, client=client)

    transport = ConcurrentTransport()
    candidates = asyncio.run(run(transport))

    assert transport.maximum == 3
    assert [candidate["arxiv_id"] for candidate in candidates[:2]] == [
        "2004.04906",
        "2005.11401",
    ]


def test_query_prefers_concise_research_brief_title_for_foundational_search() -> None:
    brief = """## Research Brief: Retrieval-Augmented Generation for Open-Domain QA

**Objective**
Identify and compare foundational papers that established retrieval-augmented
generation approaches for open-domain question answering, emphasizing how systems
retrieve external evidence and integrate it into answer generation.
"""

    assert _query(brief) == "Retrieval-Augmented Generation for Open-Domain QA"
    assert "sort" not in _openalex_params(brief, 10)


def test_generic_research_brief_heading_uses_explicit_goal_topic() -> None:
    brief = """## Research Brief

**Goal:** Identify foundational *primary research* papers that established
retrieval-augmented generation (RAG) and dense passage retrieval (DPR) for
open-domain question answering.

**Core topics**
- Dense neural retrieval of passages for open-domain QA

## Assumptions
- The original RAG and DPR papers are in scope.
"""

    query = _query(brief)

    assert query != "Research"
    assert "retrieval-augmented generation" in query
    assert "dense passage retrieval" in query
    assert "open-domain question answering" in query


def test_label_heading_ending_in_colon_is_never_the_topic_query() -> None:
    assert _query(
        "## Topic:\n\n**Objective:** Dense retrieval for open-domain question answering."
    ) == "Dense retrieval for open-domain question answering"


def test_openalex_deduplicates_same_titled_works_before_forming_batch() -> None:
    rows = _openalex_rows()
    duplicate = {**rows[0], "id": "https://openalex.org/W-duplicate"}
    extra = {
        **rows[0],
        "id": "https://openalex.org/W10",
        "display_name": "Verified paper 10",
        "publication_year": 2030,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [rows[0], duplicate, *rows[1:], extra]})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await search_openalex(
                "verified papers", "## Research Brief", 10, client=client
            )

    candidates = asyncio.run(run())

    assert len(candidates) == 10
    assert len({candidate["title"].casefold() for candidate in candidates}) == 10
    assert "openalex:W-duplicate" not in {
        candidate["candidate_id"] for candidate in candidates
    }


def test_openalex_keeps_verified_work_when_publication_year_is_unavailable() -> None:
    row = {**_openalex_rows()[0], "publication_year": None, "publication_date": None}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [row]})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_openalex(
                "undated verified paper", "## Research Brief", 1, client=client
            )

    candidates = asyncio.run(run())

    assert len(candidates) == 1
    assert candidates[0]["year"] is None


def test_named_seed_papers_precede_deduplicated_topic_results_after_fallback() -> None:
    research_goal = (
        "Find and compare the original Dense Passage Retrieval for Open-Domain "
        "Question Answering and Retrieval-Augmented Generation for Knowledge-Intensive "
        "NLP Tasks papers, plus only closely related foundational primary papers."
    )
    brief = """## Research Brief

**Objective**
Map and compare the two original methods.

**Required anchor papers**
- *Dense Passage Retrieval for Open-Domain Question Answering* — Karpukhin et al.
- *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks* — Lewis et al.

**Related-paper inclusion criteria**
- Primary research rather than surveys.
"""
    dpr = {
        **_openalex_rows()[0],
        "id": "https://openalex.org/DPR",
        "display_name": "Dense Passage Retrieval for Open-Domain Question Answering",
        "publication_year": 2020,
    }
    rag = {
        **_openalex_rows()[1],
        "id": "https://openalex.org/RAG",
        "display_name": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "publication_year": 2020,
    }
    realm = {
        **_openalex_rows()[2],
        "id": "https://openalex.org/REALM",
        "display_name": "REALM: Retrieval-Augmented Language Model Pre-Training",
        "publication_year": 2020,
    }
    orqa = {
        **_openalex_rows()[3],
        "id": "https://openalex.org/ORQA",
        "display_name": "Latent Retrieval for Weakly Supervised Open Domain Question Answering",
        "publication_year": 2019,
    }
    fillers = [
        {
            **row,
            "id": f"https://openalex.org/FILL{index}",
            "display_name": f"Relevant primary paper {index}",
        }
        for index, row in enumerate(_openalex_rows()[4:10], 1)
    ]
    searches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429, json={"message": "rate limited"})
        query = request.url.params["search"]
        searches.append(query)
        is_seed_lookup = request.url.params["per-page"] == "5"
        if is_seed_lookup and query.startswith("Dense Passage Retrieval"):
            return httpx.Response(200, json={"results": [dpr]})
        if is_seed_lookup and query.startswith("Retrieval-Augmented Generation for Knowledge"):
            return httpx.Response(200, json={"results": [rag]})
        return httpx.Response(
            200,
            json={"results": [rag, dpr, realm, orqa, *fillers]},
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await search_academic_papers(
                research_goal, brief, 10, client=client
            )

    candidates = asyncio.run(run())

    assert _seed_titles(brief) == [
        "Dense Passage Retrieval for Open-Domain Question Answering",
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    ]
    assert searches[:2] == _seed_titles(brief)
    assert searches[-1] == _query(research_goal)
    assert "Dense Passage Retrieval" in searches[-1]
    assert "Retrieval-Augmented Generation" in searches[-1]
    assert "sort" not in _openalex_params(brief, 10)
    assert [candidate["candidate_id"] for candidate in candidates[:4]] == [
        "openalex:DPR",
        "openalex:RAG",
        "openalex:REALM",
        "openalex:ORQA",
    ]
    assert len(candidates) == 10
    assert len({candidate["title"].casefold() for candidate in candidates}) == 10


def test_goal_named_seeds_merge_with_generalized_brief_seed_sections() -> None:
    goal = (
        "Compare the original Dense Passage Retrieval for Open-Domain Question "
        "Answering paper and the original Retrieval-Augmented Generation for "
        "Knowledge-Intensive NLP Tasks paper."
    )
    brief_without_seeds = """## Research Brief

**Goal:** Compare DPR and RAG using their reported evidence.

### Comparison Focus
- Architecture and training
"""
    assert _search_seed_titles(goal, brief_without_seeds) == [
        "Dense Passage Retrieval for Open-Domain Question Answering",
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    ]

    generalized_brief = """### Foundational papers
- “REALM: Retrieval-Augmented Language Model Pre-Training”
- Latent Retrieval for Weakly Supervised Open Domain Question Answering

### Evaluation
- Do not treat this as a seed section.
"""
    assert _seed_titles(generalized_brief) == [
        "REALM: Retrieval-Augmented Language Model Pre-Training",
        "Latent Retrieval for Weakly Supervised Open Domain Question Answering",
    ]
