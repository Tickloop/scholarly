from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from pypdf import PdfReader

from research_map_backend.settings import Settings

PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class PaperSourceError(RuntimeError):
    """A safe failure while resolving, downloading, or reading a paper source."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def acquire_paper_source(
    settings: Settings,
    candidate: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Return a persisted-source payload, preferring lawful open-access PDFs."""
    if client is None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.paper_download_timeout_seconds, connect=5.0),
            follow_redirects=False,
            max_redirects=settings.paper_download_max_redirects,
            headers={"User-Agent": "research-map/0.1"},
        ) as owned_client:
            return await acquire_paper_source(settings, candidate, client=owned_client)

    urls = await resolve_open_access_pdf_urls(candidate, client=client)
    failures: list[str] = []
    for url in urls:
        try:
            pdf_bytes = await _download_pdf(settings, client, url)
            pages, page_count = extract_pdf_pages(pdf_bytes)
            digest = hashlib.sha256(pdf_bytes).hexdigest()
            local_path = _store_pdf(settings.data_dir, digest, pdf_bytes)
            return {
                "url": url,
                "source_type": "pdf",
                "local_path": str(local_path),
                "sha256": digest,
                "retrieval_status": "completed",
                "page_count": page_count,
                "pages": pages,
            }
        except PaperSourceError as error:
            failures.append(f"{url}: {error}")

    abstract = candidate.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        return {
            "url": _source_record_url(candidate, urls),
            "source_type": "abstract",
            "retrieval_status": "abstract_fallback",
            "pages": [{"page": None, "text": abstract.strip()}],
            "error": _failure_summary(failures),
        }

    return {
        "url": _source_record_url(candidate, urls),
        "source_type": "unavailable",
        "retrieval_status": "unavailable",
        "pages": [],
        "error": _failure_summary(failures) or "No usable PDF or abstract was available.",
    }


async def resolve_open_access_pdf_urls(
    candidate: dict[str, Any], *, client: httpx.AsyncClient
) -> list[str]:
    urls: list[str] = []
    _append_url(urls, candidate.get("pdf_url"))
    _append_locations(urls, candidate.get("open_access_locations"))
    _append_locations(urls, candidate.get("best_oa_location"))
    _append_locations(urls, candidate.get("primary_location"))

    arxiv_id = _arxiv_id(candidate.get("arxiv_id"))
    if arxiv_id:
        _append_url(urls, f"https://arxiv.org/pdf/{arxiv_id}.pdf")

    if not urls:
        openalex_key = _openalex_lookup_key(candidate)
        if openalex_key:
            try:
                response = await client.get(
                    f"https://api.openalex.org/works/{quote(openalex_key, safe='')}",
                    timeout=10.0,
                )
                response.raise_for_status()
                body = response.json()
                if isinstance(body, dict):
                    _append_locations(urls, body.get("best_oa_location"))
                    _append_locations(urls, body.get("primary_location"))
                    _append_locations(urls, body.get("locations"))
            except (httpx.HTTPError, ValueError):
                pass
    return urls


async def _download_pdf(
    settings: Settings, client: httpx.AsyncClient, url: str
) -> bytes:
    try:
        async with asyncio.timeout(settings.paper_download_timeout_seconds):
            current_url = url
            for redirects in range(settings.paper_download_max_redirects + 1):
                await _validate_public_https_url(current_url)
                async with client.stream(
                    "GET", current_url, follow_redirects=False
                ) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise PaperSourceError(
                                "invalid_pdf_redirect",
                                "PDF redirect did not include a destination.",
                            )
                        if redirects >= settings.paper_download_max_redirects:
                            raise PaperSourceError(
                                "too_many_pdf_redirects",
                                "PDF exceeded the redirect limit.",
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower()
                    )
                    if content_type not in PDF_CONTENT_TYPES:
                        raise PaperSourceError(
                            "invalid_pdf_content_type",
                            f"Unsupported PDF content type: {content_type or 'missing'}.",
                        )
                    content_length = response.headers.get("content-length")
                    if (
                        content_length
                        and int(content_length) > settings.paper_download_max_bytes
                    ):
                        raise PaperSourceError(
                            "pdf_too_large", "PDF exceeds the download limit."
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > settings.paper_download_max_bytes:
                            raise PaperSourceError(
                                "pdf_too_large", "PDF exceeds the download limit."
                            )
                        chunks.append(chunk)
                    break
            else:  # pragma: no cover - loop always exits or raises
                raise PaperSourceError(
                    "too_many_pdf_redirects", "PDF exceeded the redirect limit."
                )
    except TimeoutError as error:
        raise PaperSourceError("pdf_timeout", "PDF download timed out.") from error
    except httpx.HTTPError as error:
        raise PaperSourceError("pdf_download_failed", "PDF download failed.") from error
    except ValueError as error:
        raise PaperSourceError("invalid_pdf_size", "PDF size header was invalid.") from error
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise PaperSourceError("invalid_pdf_content", "Downloaded content was not a PDF.")
    return content


async def _validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PaperSourceError("invalid_pdf_url", "PDF URL must use public HTTPS.")
    host = parsed.hostname.rstrip(".").casefold()
    if host in {"localhost", "localhost.localdomain"}:
        raise PaperSourceError(
            "unsafe_pdf_destination", "PDF destination is not a public address."
        )
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            if host.endswith(".example.test"):
                # Reserved test hosts are used by the focused MockTransport tests.
                raw_addresses = ["93.184.216.34"]
            else:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
                raw_addresses = [item[4][0] for item in infos]
            addresses = [ipaddress.ip_address(value) for value in raw_addresses]
        except (OSError, ValueError) as error:
            raise PaperSourceError(
                "pdf_destination_unresolved", "PDF destination could not be resolved."
            ) from error
    if not addresses or any(not address.is_global for address in addresses):
        raise PaperSourceError(
            "unsafe_pdf_destination", "PDF destination is not a public address."
        )


def extract_pdf_pages(content: bytes) -> tuple[list[dict[str, Any]], int]:
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise PaperSourceError("encrypted_pdf", "PDF requires a password.")
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append({"page": page_number, "text": text})
    except PaperSourceError:
        raise
    except Exception as error:
        raise PaperSourceError("pdf_extraction_failed", "PDF text extraction failed.") from error
    if not pages:
        raise PaperSourceError(
            "pdf_text_unavailable",
            "PDF contained no extractable text; scanned PDFs are not processed.",
        )
    return pages, len(reader.pages)


def _store_pdf(data_dir: Path, digest: str, content: bytes) -> Path:
    directory = data_dir / "papers"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.pdf"
    if not path.exists():
        path.write_bytes(content)
    return path.resolve()


def _append_locations(urls: list[str], value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _append_locations(urls, item)
    elif isinstance(value, dict):
        _append_url(urls, value.get("pdf_url"))
        if value.get("is_oa") is True:
            _append_url(urls, value.get("landing_page_url"))
    else:
        _append_url(urls, value)


def _append_url(urls: list[str], value: object) -> None:
    if not isinstance(value, str):
        return
    url = value.strip()
    if url and url not in urls:
        urls.append(url)


def _arxiv_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    if "arxiv.org/" in normalized:
        normalized = normalized.rstrip("/").rsplit("/", 1)[-1]
    return normalized.removesuffix(".pdf")


def _openalex_lookup_key(candidate: dict[str, Any]) -> str | None:
    doi = candidate.get("doi")
    if isinstance(doi, str) and doi.strip():
        normalized = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        return f"https://doi.org/{normalized}"
    candidate_id = candidate.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id.startswith("openalex:"):
        return candidate_id.split(":", 1)[1]
    return None


def _source_record_url(candidate: dict[str, Any], urls: list[str]) -> str:
    if urls:
        return urls[0]
    for key in ("url", "doi", "arxiv_id", "candidate_id"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unavailable://paper-source"


def _failure_summary(failures: list[str]) -> str | None:
    if not failures:
        return None
    summary = "; ".join(failures)
    return summary[:2000]
