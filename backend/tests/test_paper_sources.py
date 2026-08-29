from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from research_map_backend.paper_sources import (
    acquire_paper_source,
    extract_pdf_pages,
    resolve_open_access_pdf_urls,
)
from research_map_backend.pipeline import _review_prompt
from research_map_backend.settings import Settings


def _text_pdf(*texts: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.anyio
async def test_downloads_hashes_and_extracts_pdf_by_page(tmp_path: Path) -> None:
    pdf = _text_pdf("Evidence on page one", "Result on page two")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://papers.example.test/open.pdf"
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": str(len(pdf))},
            content=pdf,
        )

    settings = Settings(data_dir=tmp_path, paper_download_max_bytes=len(pdf) + 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            settings,
            {"pdf_url": "https://papers.example.test/open.pdf", "abstract": "Fallback"},
            client=client,
        )

    assert source["source_type"] == "pdf"
    assert source["retrieval_status"] == "completed"
    assert source["page_count"] == 2
    assert [(page["page"], page["text"]) for page in source["pages"]] == [
        (1, "Evidence on page one"),
        (2, "Result on page two"),
    ]
    assert Path(source["local_path"]).read_bytes() == pdf
    assert Path(source["local_path"]).name == f"{source['sha256']}.pdf"


@pytest.mark.anyio
async def test_invalid_or_oversized_pdf_uses_labeled_abstract_fallback(
    tmp_path: Path,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "200"},
            content=b"not a pdf",
        )

    settings = Settings(data_dir=tmp_path, paper_download_max_bytes=100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            settings,
            {
                "pdf_url": "https://papers.example.test/not-pdf",
                "url": "https://papers.example.test/paper",
                "abstract": "Only verified abstract content.",
            },
            client=client,
        )

    assert source["source_type"] == "abstract"
    assert source["retrieval_status"] == "abstract_fallback"
    assert source["pages"] == [{"page": None, "text": "Only verified abstract content."}]
    assert "Unsupported PDF content type" in source["error"]
    assert not (tmp_path / "papers").exists()


@pytest.mark.anyio
async def test_oversized_pdf_is_not_buffered_or_saved(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": "101"},
            content=b"%PDF-" + b"x" * 96,
        )

    settings = Settings(data_dir=tmp_path, paper_download_max_bytes=100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            settings,
            {
                "pdf_url": "https://papers.example.test/too-large.pdf",
                "abstract": "Safe fallback.",
            },
            client=client,
        )

    assert source["retrieval_status"] == "abstract_fallback"
    assert "exceeds the download limit" in source["error"]
    assert not (tmp_path / "papers").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://papers.example.test/work.pdf",
        "https://127.0.0.1/work.pdf",
        "https://10.0.0.8/work.pdf",
        "https://169.254.169.254/latest/meta-data",
        "https://192.0.2.10/work.pdf",
    ],
)
async def test_rejects_non_https_and_non_public_pdf_destinations(
    tmp_path: Path, url: str
) -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"%PDF-never")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            Settings(data_dir=tmp_path), {"pdf_url": url}, client=client
        )

    assert source["retrieval_status"] == "unavailable"
    assert "public" in source["error"]
    assert called is False


@pytest.mark.anyio
async def test_revalidates_redirect_destination_before_following(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "https://127.0.0.1/private.pdf"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            Settings(data_dir=tmp_path),
            {"pdf_url": "https://papers.example.test/start.pdf"},
            client=client,
        )

    assert requests == ["https://papers.example.test/start.pdf"]
    assert source["retrieval_status"] == "unavailable"
    assert "not a public address" in source["error"]


@pytest.mark.anyio
async def test_enforces_redirect_limit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        step = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(302, headers={"location": f"/redirect/{step + 1}"})

    settings = Settings(data_dir=tmp_path, paper_download_max_redirects=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            settings,
            {"pdf_url": "https://papers.example.test/redirect/0"},
            client=client,
        )

    assert source["retrieval_status"] == "unavailable"
    assert "redirect limit" in source["error"]


@pytest.mark.anyio
async def test_scanned_or_blank_pdf_is_unsupported_without_ocr(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=pdf
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await acquire_paper_source(
            Settings(data_dir=tmp_path),
            {"pdf_url": "https://papers.example.test/scanned.pdf"},
            client=client,
        )

    assert source["source_type"] == "unavailable"
    assert source["retrieval_status"] == "unavailable"
    assert "no extractable text" in source["error"]
    assert not (tmp_path / "papers").exists()


@pytest.mark.anyio
async def test_resolves_doi_through_openalex_open_access_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openalex.org"
        return httpx.Response(
            200,
            json={
                "best_oa_location": {
                    "is_oa": True,
                    "pdf_url": "https://repository.example.test/work.pdf",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await resolve_open_access_pdf_urls(
            {"doi": "https://doi.org/10.1234/example"}, client=client
        )

    assert urls == ["https://repository.example.test/work.pdf"]


def test_extract_pdf_pages_keeps_real_page_numbers() -> None:
    pages, page_count = extract_pdf_pages(_text_pdf("First", "Second"))

    assert page_count == 2
    assert [page["page"] for page in pages] == [1, 2]


def test_reviewer_context_identifies_pdf_page_evidence() -> None:
    prompt = _review_prompt(
        {"paper_id": "paper-1", "title": "Page-aware work"},
        [
            {
                "id": "evidence-1",
                "page": 7,
                "source_type": "pdf",
                "source_url": "https://papers.example.test/work.pdf",
                "text": "The reported result appears here.",
            }
        ],
    )

    assert "Prefer PDF evidence with real page numbers" in prompt
    assert "Write every section in simple language" in prompt
    assert '"page": 7' in prompt
    assert '"id": "evidence-1"' in prompt
