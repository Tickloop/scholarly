import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from research_map_backend.db import Database
from research_map_backend.models import AgentRun, Canvas, CanvasPaper, Job, Paper, Relationship
from research_map_backend.paper_links import PaperLinkError
from research_map_backend.paper_links import (
    attach_canvas_paper,
    create_canvas_paper_placeholder,
    resolve_paper_link,
)
from research_map_backend.paper_sources import resolve_open_access_pdf_urls
from research_map_backend.research_store import recover_interrupted_jobs
from research_map_backend.settings import Settings


ARXIV_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2005.11401v4</id>
    <published>2020-05-22T17:32:46Z</published>
    <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
    <summary>We explore retrieval-augmented generation for knowledge-intensive tasks.</summary>
    <author><name>Patrick Lewis</name></author>
    <author><name>Ethan Perez</name></author>
    <arxiv:doi>10.48550/arXiv.2005.11401</arxiv:doi>
    <link href="http://arxiv.org/abs/2005.11401v4" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2005.11401v4" rel="related" type="application/pdf"/>
  </entry>
</feed>
"""

LEGACY_ARXIV_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/math/0309136v1</id>
    <published>2003-09-07T21:36:15Z</published>
    <title>Regular points in affine Springer fibers</title>
    <summary>Verified legacy arXiv metadata.</summary>
    <author><name>A. Researcher</name></author>
    <link href="http://arxiv.org/abs/math/0309136v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/math/0309136v1" rel="related" type="application/pdf"/>
  </entry>
</feed>
"""

EMPTY_ARXIV_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" />
"""


def test_arxiv_link_uses_exact_official_metadata_and_keeps_placeholder_id(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(404, request=request)
        assert request.url == "https://export.arxiv.org/api/query?id_list=2005.11401&max_results=1"
        assert parse_qs(request.url.query.decode())["id_list"] == ["2005.11401"]
        return httpx.Response(
            200,
            headers={"content-type": "application/atom+xml"},
            content=ARXIV_RESPONSE,
            request=request,
        )

    async def resolve():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            candidate = await resolve_paper_link(
                "https://arxiv.org/abs/2005.11401", client=client
            )
            urls = await resolve_open_access_pdf_urls(candidate, client=client)
            return candidate, urls

    candidate, pdf_urls = asyncio.run(resolve())
    assert candidate == {
        "source_provider": "arxiv",
        "candidate_id": "arxiv:2005.11401",
        "semantic_scholar_id": None,
        "doi": "10.48550/arXiv.2005.11401",
        "arxiv_id": "2005.11401",
        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "authors": ["Patrick Lewis", "Ethan Perez"],
        "year": 2020,
        "month": 5,
        "publication_date": "2020-05-22",
        "abstract": "We explore retrieval-augmented generation for knowledge-intensive tasks.",
        "url": "https://arxiv.org/abs/2005.11401",
        "pdf_url": "https://arxiv.org/pdf/2005.11401v4",
        "venue": "arXiv",
        "citation_count": 0,
    }
    assert pdf_urls[0] == "https://arxiv.org/pdf/2005.11401v4"

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'links.sqlite3'}",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="RAG", research_goal="Map RAG.")
        session.add(canvas)
        session.commit()
        canvas_id = canvas.id
    placeholder_id = create_canvas_paper_placeholder(
        database, canvas_id, "https://arxiv.org/abs/2005.11401"
    )
    candidate["submitted_url"] = "https://arxiv.org/abs/2005.11401"
    resolved_id = attach_canvas_paper(database, canvas_id, candidate)
    with database.session_context() as session:
        paper = session.get(Paper, placeholder_id)
    database.dispose()

    assert resolved_id == placeholder_id
    assert paper is not None
    assert paper.title == candidate["title"]
    assert paper.arxiv_id == "2005.11401"


@pytest.mark.anyio
async def test_legacy_arxiv_category_and_version_use_exact_official_metadata() -> None:
    lookups: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(404, request=request)
        assert request.url.host == "export.arxiv.org"
        lookup = parse_qs(request.url.query.decode())["id_list"][0]
        lookups.append(lookup)
        return httpx.Response(
            200,
            headers={"content-type": "application/atom+xml"},
            content=(
                LEGACY_ARXIV_RESPONSE
                if lookup == "math/0309136v1"
                else EMPTY_ARXIV_RESPONSE
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidate = await resolve_paper_link("math.GT/0309136v1", client=client)

    assert lookups == ["math.GT/0309136v1", "math/0309136v1"]
    assert candidate["candidate_id"] == "arxiv:math/0309136"
    assert candidate["arxiv_id"] == "math/0309136"
    assert candidate["url"] == "https://arxiv.org/abs/math/0309136"
    assert candidate["pdf_url"] == "https://arxiv.org/pdf/math/0309136v1"


def test_versioned_arxiv_placeholder_reuses_cross_canvas_canonical_id(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'canonical-placeholder.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        first = Canvas(name="First", research_goal="First")
        second = Canvas(name="Second", research_goal="Second")
        paper = Paper(
            title="Canonical RAG",
            normalized_title="canonical rag",
            authors=[],
            year=2020,
            month=5,
            summary="",
            url="https://arxiv.org/abs/2005.11401",
            arxiv_id="2005.11401",
        )
        session.add_all([first, second, paper])
        session.flush()
        session.add(CanvasPaper(canvas_id=first.id, paper_id=paper.id))
        session.commit()
        first_id, second_id, canonical_id = first.id, second.id, paper.id

    placeholder_id = create_canvas_paper_placeholder(
        database, second_id, "2005.11401v2", x=75, y=125
    )
    with database.session_context() as session:
        papers = list(session.scalars(select(Paper)))
        memberships = list(
            session.scalars(select(CanvasPaper).where(CanvasPaper.paper_id == canonical_id))
        )
    database.dispose()

    assert placeholder_id == canonical_id
    assert len(papers) == 1
    assert {membership.canvas_id for membership in memberships} == {first_id, second_id}
    second_membership = next(item for item in memberships if item.canvas_id == second_id)
    assert (second_membership.x, second_membership.y) == (75, 125)
    assert second_membership.processing_status == "queued"


def test_resolved_doi_merges_a_legacy_url_placeholder_into_canonical_paper(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'canonical-doi.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        first = Canvas(name="First", research_goal="Paper")
        second = Canvas(name="Second", research_goal="Paper")
        canonical = Paper(
            title="Canonical DOI paper",
            normalized_title="canonical doi paper",
            authors=[],
            year=2024,
            month=1,
            summary="",
            url="https://doi.org/10.1000/canonical",
            doi="10.1000/canonical",
        )
        placeholder = Paper(
            title="Queued publisher URL",
            normalized_title="queued-publisher-url",
            authors=[],
            year=None,
            month=None,
            summary="",
            url="https://publisher.example/paper",
        )
        session.add_all([first, second, canonical, placeholder])
        session.flush()
        session.add_all(
            [
                CanvasPaper(canvas_id=first.id, paper_id=canonical.id),
                CanvasPaper(canvas_id=second.id, paper_id=placeholder.id),
            ]
        )
        session.commit()
        second_id = second.id
        canonical_id = canonical.id
        placeholder_id = placeholder.id

    resolved_id = attach_canvas_paper(
        database,
        second_id,
        {
            "title": "Canonical DOI paper",
            "authors": ["A. Author"],
            "year": 2024,
            "month": 1,
            "abstract": "Verified metadata.",
            "url": "https://doi.org/10.1000/canonical",
            "doi": "10.1000/CANONICAL",
            "submitted_url": "https://publisher.example/paper",
        },
        placeholder_paper_id=placeholder_id,
    )
    with database.session_context() as session:
        placeholder = session.get(Paper, placeholder_id)
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == second_id,
                CanvasPaper.paper_id == canonical_id,
            )
        )
    database.dispose()

    assert resolved_id == canonical_id
    assert placeholder is None
    assert membership is not None


def test_concurrent_cross_canvas_aliases_create_one_canonical_paper(tmp_path: Path) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'concurrent-canonical.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        canvases = [Canvas(name=f"Canvas {index}", research_goal="RAG") for index in range(3)]
        session.add_all(canvases)
        session.commit()
        canvas_ids = [canvas.id for canvas in canvases]
    barrier = threading.Barrier(len(canvas_ids))
    references = [
        "2005.11401v2",
        "https://arxiv.org/abs/2005.11401v4",
        "arXiv:2005.11401",
    ]

    def create(item: tuple[str, str]) -> str:
        canvas_id, reference = item
        barrier.wait()
        return create_canvas_paper_placeholder(database, canvas_id, reference)

    with ThreadPoolExecutor(max_workers=3) as executor:
        paper_ids = list(executor.map(create, zip(canvas_ids, references, strict=True)))
    with database.session_context() as session:
        papers = list(session.scalars(select(Paper)))
        memberships = list(session.scalars(select(CanvasPaper)))
    database.dispose()

    assert len(set(paper_ids)) == 1
    assert len(papers) == 1 and papers[0].arxiv_id == "2005.11401"
    assert {membership.canvas_id for membership in memberships} == set(canvas_ids)


def test_concurrent_legacy_placeholders_resolve_to_one_cross_canvas_paper(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'concurrent-resolution.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        canvases = [Canvas(name=f"Canvas {index}", research_goal="RAG") for index in range(3)]
        canonical = Paper(
            title="Canonical RAG",
            normalized_title="canonical rag",
            authors=[],
            year=2020,
            month=5,
            summary="",
            url="https://arxiv.org/abs/2005.11401",
            arxiv_id="2005.11401",
        )
        placeholders = [
            Paper(
                title=f"Queued RAG v{version}",
                normalized_title=f"queued-rag-v{version}",
                authors=[],
                year=None,
                month=None,
                summary="",
                url=f"2005.11401v{version}",
                arxiv_id=f"2005.11401v{version}",
            )
            for version in (2, 3)
        ]
        session.add_all([*canvases, canonical, *placeholders])
        session.flush()
        session.add_all(
            [
                CanvasPaper(canvas_id=canvases[0].id, paper_id=canonical.id),
                CanvasPaper(canvas_id=canvases[1].id, paper_id=placeholders[0].id),
                CanvasPaper(canvas_id=canvases[2].id, paper_id=placeholders[1].id),
            ]
        )
        session.commit()
        canvas_ids = [canvas.id for canvas in canvases]
        canonical_id = canonical.id
        placeholder_ids = [paper.id for paper in placeholders]

    barrier = threading.Barrier(2)

    def resolve(item: tuple[str, str, str]) -> str:
        canvas_id, submitted_url, placeholder_id = item
        barrier.wait()
        return attach_canvas_paper(
            database,
            canvas_id,
            {
                "title": "Canonical RAG",
                "authors": ["A. Author"],
                "year": 2020,
                "month": 5,
                "abstract": "Verified metadata.",
                "url": "https://arxiv.org/abs/2005.11401",
                "arxiv_id": "2005.11401",
                "submitted_url": submitted_url,
            },
            placeholder_paper_id=placeholder_id,
        )

    work = [
        (canvas_ids[1], "2005.11401v2", placeholder_ids[0]),
        (canvas_ids[2], "2005.11401v3", placeholder_ids[1]),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        resolved_ids = list(executor.map(resolve, work))
    with database.session_context() as session:
        papers = list(session.scalars(select(Paper)))
        memberships = list(session.scalars(select(CanvasPaper)))
    database.dispose()

    assert resolved_ids == [canonical_id, canonical_id]
    assert len(papers) == 1 and papers[0].id == canonical_id
    assert {membership.canvas_id for membership in memberships} == set(canvas_ids)


def test_recovered_legacy_placeholder_merges_references_into_canonical_paper(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'legacy-merge.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        first = Canvas(name="First", research_goal="RAG")
        second = Canvas(name="Second", research_goal="RAG")
        canonical = Paper(
            title="Canonical RAG",
            normalized_title="canonical rag",
            authors=[],
            year=2020,
            month=5,
            summary="",
            url="https://arxiv.org/abs/2005.11401",
            arxiv_id="2005.11401",
        )
        placeholder = Paper(
            title="Queued paper: 2005.11401v2",
            normalized_title="queued-versioned-rag",
            authors=[],
            year=None,
            month=None,
            summary="",
            url="2005.11401v2",
            arxiv_id="2005.11401v2",
        )
        partner = Paper(
            title="Partner",
            normalized_title="partner",
            authors=[],
            year=2021,
            month=1,
            summary="",
            url="https://example.test/partner",
        )
        session.add_all([first, second, canonical, placeholder, partner])
        session.flush()
        session.add_all(
            [
                CanvasPaper(canvas_id=first.id, paper_id=canonical.id),
                CanvasPaper(canvas_id=second.id, paper_id=placeholder.id),
                CanvasPaper(canvas_id=second.id, paper_id=partner.id),
            ]
        )
        run = AgentRun(canvas_id=second.id, status="running")
        session.add(run)
        session.flush()
        job = Job(
            canvas_id=second.id,
            run_id=run.id,
            paper_id=placeholder.id,
            kind="paper_link",
            status="running",
            attempts=1,
            payload={"url": "2005.11401v2"},
        )
        relationship = Relationship(
            canvas_id=second.id,
            source_paper_id=placeholder.id,
            target_paper_id=partner.id,
            type="extends",
            label="Queued relation",
            explanation="Queued relation",
        )
        session.add_all([job, relationship])
        session.commit()
        canonical_id = canonical.id
        placeholder_id = placeholder.id
        second_id = second.id
        job_id = job.id
        relationship_id = relationship.id

    assert recover_interrupted_jobs(database)
    candidate = {
        "title": "Canonical RAG",
        "authors": ["A. Author"],
        "year": 2020,
        "month": 5,
        "abstract": "Verified metadata.",
        "url": "https://arxiv.org/abs/2005.11401",
        "arxiv_id": "2005.11401",
        "submitted_url": "2005.11401v2",
    }
    resolved_id = attach_canvas_paper(
        database,
        second_id,
        candidate,
        placeholder_paper_id=placeholder_id,
    )
    with database.session_context() as session:
        stored_job = session.get(Job, job_id)
        stored_relationship = session.get(Relationship, relationship_id)
        placeholder = session.get(Paper, placeholder_id)
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == second_id,
                CanvasPaper.paper_id == canonical_id,
            )
        )
    database.dispose()

    assert resolved_id == canonical_id
    assert placeholder is None
    assert membership is not None
    assert stored_job is not None and stored_job.paper_id == canonical_id
    assert stored_job.status == "retrying"
    assert stored_relationship is not None
    assert stored_relationship.source_paper_id == canonical_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "submitted",
    [
        "https://doi.org/10.1000/test-paper",
        "https://www.semanticscholar.org/paper/Title/0123456789abcdef0123456789abcdef01234567",
        "https://publisher.example.test/articles/paper",
        "https://repository.example.test/papers/paper.pdf",
    ],
)
async def test_resolves_doi_semantic_publisher_and_direct_pdf_urls(
    submitted: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            request=request,
            json={
                "paperId": "0123456789abcdef0123456789abcdef01234567",
                "title": "Resolved provider paper",
                "abstract": "Verified abstract.",
                "authors": [{"name": "A. Author"}],
                "year": 2024,
                "venue": "Venue",
                "url": submitted,
                "citationCount": 1,
                "externalIds": {"DOI": "10.1000/test-paper"},
                "openAccessPdf": {"url": "https://repository.example.test/paper.pdf"},
                "publicationDate": "2024-03-02",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidate = await resolve_paper_link(submitted, client=client)

    assert requests and requests[0].startswith(
        "https://api.semanticscholar.org/graph/v1/paper/"
    )
    assert candidate["title"] == "Resolved provider paper"
    assert candidate["pdf_url"] == "https://repository.example.test/paper.pdf"


@pytest.mark.anyio
async def test_openalex_legacy_arxiv_identity_keeps_canonical_archive_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(404, request=request)
        assert request.url.host == "api.openalex.org"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "https://openalex.org/W123",
                "display_name": "A verified legacy paper",
                "publication_year": 2003,
                "publication_date": "2003-09-07",
                "authorships": [{"author": {"display_name": "A. Author"}}],
                "ids": {"arxiv": "https://arxiv.org/abs/math/0309136v1"},
                "primary_location": {
                    "landing_page_url": "https://arxiv.org/abs/math/0309136",
                },
                "best_oa_location": {
                    "pdf_url": "https://arxiv.org/pdf/math/0309136v1",
                },
                "abstract_inverted_index": {"Verified": [0], "abstract": [1]},
                "cited_by_count": 2,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidate = await resolve_paper_link(
            "https://openalex.org/W123", client=client
        )

    assert candidate["arxiv_id"] == "math/0309136"


@pytest.mark.anyio
async def test_rejects_invalid_non_web_link() -> None:
    with pytest.raises(PaperLinkError, match="Submit a DOI"):
        await resolve_paper_link("ftp://example.test/paper.pdf")


@pytest.mark.anyio
async def test_semantic_metadata_without_publication_date_stays_unknown() -> None:
    submitted = "https://www.semanticscholar.org/paper/undated"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "paperId": "undated-paper-id",
                "title": "Verified undated paper",
                "abstract": "Verified abstract.",
                "authors": [{"name": "A. Author"}],
                "year": None,
                "url": submitted,
                "externalIds": {},
                "openAccessPdf": None,
                "publicationDate": None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidate = await resolve_paper_link(submitted, client=client)

    assert candidate["year"] is None
    assert candidate["month"] is None


def test_explicit_user_link_can_exceed_autonomous_fifty_node_limit(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'limit.sqlite3'}",
    )
    database = Database(settings)
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Large", research_goal="Map papers")
        session.add(canvas)
        session.flush()
        for index in range(50):
            paper = Paper(
                title=f"Paper {index}",
                normalized_title=f"paper {index}",
                authors=[],
                year=2000 + index % 20,
                month=1,
                summary="",
                url=f"https://example.test/{index}",
            )
            session.add(paper)
            session.flush()
            session.add(
                CanvasPaper(
                    canvas_id=canvas.id,
                    paper_id=paper.id,
                    origin="discovery",
                )
            )
        session.commit()
        canvas_id = canvas.id

    user_id = create_canvas_paper_placeholder(
        database, canvas_id, "https://doi.org/10.1000/user-paper"
    )
    with database.session_context() as session:
        count = session.scalar(
            select(func.count()).select_from(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id
            )
        )
        membership = session.scalar(
            select(CanvasPaper).where(CanvasPaper.paper_id == user_id)
        )
    database.dispose()

    assert count == 51
    assert membership is not None and membership.origin == "user"
