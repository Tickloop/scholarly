from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from research_map_backend.db import Database
from research_map_backend.models import (
    ActiveReviewMessage,
    Canvas,
    CanvasPaper,
    Conversation,
    DiscoveryDecision,
    Evidence,
    Job,
    Message,
    Paper,
    PaperSource,
    Relationship,
    Review,
    UserRevision,
)
from research_map_backend.schemas import ARXIV_ID_PATTERN, normalize_arxiv_identifier


class PaperLinkError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def resolve_paper_link(
    link: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Resolve a DOI/arXiv/provider paper link through verified metadata APIs."""
    identifier, doi, arxiv_id, openalex_id = _identifier(link)
    if client is None:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as owned:
            return await resolve_paper_link(link, client=owned)

    fields = (
        "paperId,title,abstract,authors,year,venue,url,citationCount,"
        "externalIds,openAccessPdf,publicationDate"
    )
    try:
        response = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/{quote(identifier, safe='')}",
            params={"fields": fields},
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            return _semantic_candidate(body, link)
    except (httpx.HTTPError, ValueError, PaperLinkError):
        pass

    arxiv_error: PaperLinkError | None = None
    if arxiv_id:
        try:
            return await _resolve_arxiv(arxiv_id, client=client)
        except PaperLinkError as error:
            arxiv_error = error

    openalex_key = openalex_id or (f"https://doi.org/{doi}" if doi else None)
    if openalex_key:
        try:
            response = await client.get(
                f"https://api.openalex.org/works/{quote(openalex_key, safe='')}",
            )
            response.raise_for_status()
            body = response.json()
            if isinstance(body, dict):
                return _openalex_candidate(body, link)
        except (httpx.HTTPError, ValueError, PaperLinkError):
            pass

    if arxiv_error is not None:
        raise arxiv_error
    suffix = f" ({arxiv_id})" if arxiv_id else ""
    raise PaperLinkError(
        "paper_metadata_unresolved",
        f"No verified academic metadata was found for the submitted link{suffix}.",
    )


async def _resolve_arxiv(
    arxiv_id: str, *, client: httpx.AsyncClient
) -> dict[str, Any]:
    canonical_lookup = normalize_arxiv_identifier(arxiv_id, keep_version=True)
    canonical_id = normalize_arxiv_identifier(arxiv_id)
    if canonical_lookup is None or canonical_id is None:
        raise PaperLinkError("invalid_paper_link", "The arXiv identifier is invalid.")
    lookup_ids = [arxiv_id]
    if canonical_lookup.casefold() != arxiv_id.casefold():
        lookup_ids.append(canonical_lookup)
    atom = "{http://www.w3.org/2005/Atom}"
    arxiv = "{http://arxiv.org/schemas/atom}"
    entry = None
    for index, lookup_id in enumerate(lookup_ids):
        try:
            response = await client.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": lookup_id, "max_results": 1},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if index + 1 < len(lookup_ids) and error.response.status_code in {400, 404}:
                continue
            raise PaperLinkError(
                "arxiv_metadata_unavailable",
                "The official arXiv metadata API is unavailable.",
            ) from error
        except httpx.HTTPError as error:
            raise PaperLinkError(
                "arxiv_metadata_unavailable",
                "The official arXiv metadata API is unavailable.",
            ) from error
        if len(response.content) > 1_000_000:
            raise PaperLinkError(
                "arxiv_metadata_invalid",
                "The official arXiv metadata response was too large.",
            )
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            raise PaperLinkError(
                "arxiv_metadata_invalid",
                "The official arXiv metadata response was invalid.",
            ) from error
        entry = root.find(f"{atom}entry")
        if entry is not None:
            break
    if entry is None:
        raise PaperLinkError(
            "arxiv_metadata_not_found", "The official arXiv API returned no matching paper."
        )
    returned_url = _element_text(entry, f"{atom}id")
    returned_path = urlparse(returned_url).path.rstrip("/")
    returned_id = (
        returned_path.removeprefix("/abs/")
        if returned_path.startswith("/abs/")
        else returned_path.rsplit("/", 1)[-1]
    )
    normalized_returned = normalize_arxiv_identifier(returned_id)
    if normalized_returned != canonical_id:
        raise PaperLinkError(
            "arxiv_metadata_mismatch", "The official arXiv API returned a different paper."
        )
    title = " ".join(_element_text(entry, f"{atom}title").split())
    abstract = " ".join(_element_text(entry, f"{atom}summary").split())
    published = _element_text(entry, f"{atom}published")
    if not title or not abstract or not re.match(r"^\d{4}-\d{2}", published):
        raise PaperLinkError(
            "arxiv_metadata_invalid", "The official arXiv metadata was incomplete."
        )
    authors = []
    for author in entry.findall(f"{atom}author"):
        name = _element_text(author, f"{atom}name")
        if name:
            authors.append(" ".join(name.split()))
    if not authors:
        raise PaperLinkError(
            "arxiv_metadata_invalid", "The official arXiv metadata had no authors."
        )
    pdf_url = None
    for link in entry.findall(f"{atom}link"):
        href = link.attrib.get("href")
        if not isinstance(href, str):
            continue
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = href.replace("http://", "https://", 1)
            break
    return {
        "source_provider": "arxiv",
        "candidate_id": f"arxiv:{canonical_id}",
        "semantic_scholar_id": None,
        "doi": _element_text(entry, f"{arxiv}doi") or None,
        "arxiv_id": canonical_id,
        "title": title,
        "authors": authors,
        "year": int(published[:4]),
        "month": int(published[5:7]),
        "publication_date": published[:10],
        "abstract": abstract,
        "url": f"https://arxiv.org/abs/{canonical_id}",
        "pdf_url": pdf_url or f"https://arxiv.org/pdf/{canonical_id}",
        "venue": "arXiv",
        "citation_count": 0,
    }


def _element_text(element: ElementTree.Element, path: str) -> str:
    child = element.find(path)
    return child.text.strip() if child is not None and isinstance(child.text, str) else ""


def attach_canvas_paper(
    database: Database,
    canvas_id: str,
    candidate: dict[str, Any],
    *,
    placeholder_paper_id: str | None = None,
) -> str:
    """Upsert verified metadata and attach the user-submitted paper to a canvas."""
    title = candidate.get("title")
    year = _optional_year(candidate.get("year"))
    month = _optional_month(candidate.get("month")) if year is not None else None
    url = candidate.get("url")
    if not isinstance(title, str) or not title.strip():
        raise PaperLinkError("invalid_paper_metadata", "Resolved paper metadata is incomplete.")
    if not isinstance(url, str) or not url.strip():
        raise PaperLinkError("invalid_paper_metadata", "Resolved paper URL is missing.")
    normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    doi = _normalized_doi(candidate.get("doi"))
    arxiv_id = _normalized_arxiv(candidate.get("arxiv_id"))
    semantic_id = candidate.get("semantic_scholar_id")
    submitted_url = candidate.get("submitted_url")
    for attempt in range(2):
        try:
            return _attach_canvas_paper_once(
                database,
                canvas_id,
                candidate,
                title=title.strip(),
                year=year,
                month=month,
                url=url.strip(),
                normalized_title=normalized_title,
                doi=doi,
                arxiv_id=arxiv_id,
                semantic_id=semantic_id if isinstance(semantic_id, str) else None,
                submitted_url=(
                    submitted_url.strip()
                    if isinstance(submitted_url, str) and submitted_url.strip()
                    else None
                ),
                placeholder_paper_id=placeholder_paper_id,
            )
        except IntegrityError as error:
            if attempt == 0:
                continue
            raise _paper_identity_conflict() from error
        except SQLAlchemyError as error:
            raise _paper_identity_conflict() from error
    raise _paper_identity_conflict()


def _attach_canvas_paper_once(
    database: Database,
    canvas_id: str,
    candidate: dict[str, Any],
    *,
    title: str,
    year: int | None,
    month: int | None,
    url: str,
    normalized_title: str,
    doi: str | None,
    arxiv_id: str | None,
    semantic_id: str | None,
    submitted_url: str | None,
    placeholder_paper_id: str | None,
) -> str:
    with database.session_context() as session:
        if session.get(Canvas, canvas_id) is None:
            raise PaperLinkError("canvas_not_found", "Canvas does not exist.")
        placeholder = (
            session.get(Paper, placeholder_paper_id)
            if placeholder_paper_id is not None
            else None
        )
        if placeholder is None and submitted_url is not None:
            placeholder = session.scalar(
                select(Paper).where(Paper.url == submitted_url).limit(1)
            )
        identities = []
        if doi:
            identities.append(Paper.doi == doi)
        if arxiv_id:
            identities.append(Paper.arxiv_id == arxiv_id)
        if semantic_id:
            identities.append(Paper.semantic_scholar_id == semantic_id)
        identity_matches = (
            list(session.scalars(select(Paper).where(or_(*identities))))
            if identities
            else []
        )
        if len({paper.id for paper in identity_matches}) > 1:
            raise _paper_identity_conflict()
        paper = identity_matches[0] if identity_matches else None
        if paper is not None and placeholder is not None and paper.id != placeholder.id:
            _merge_queued_placeholder(session, placeholder, paper)
        elif paper is None:
            paper = placeholder
        if paper is None:
            paper = session.scalar(
                select(Paper).where(Paper.normalized_title == normalized_title, Paper.year == year)
            )
        if paper is None:
            paper = Paper(
                title=title,
                normalized_title=normalized_title,
                authors=[item for item in candidate.get("authors", []) if isinstance(item, str)],
                year=year,
                month=month,
                summary=candidate.get("abstract") or "",
                url=url,
                doi=doi,
                arxiv_id=arxiv_id,
                semantic_scholar_id=semantic_id if isinstance(semantic_id, str) else None,
            )
            session.add(paper)
            session.flush()
        else:
            paper.title = title
            paper.normalized_title = normalized_title
            paper.authors = [
                item for item in candidate.get("authors", []) if isinstance(item, str)
            ]
            paper.year = year
            paper.month = month
            paper.summary = candidate.get("abstract") or ""
            paper.url = url
            paper.doi = doi or paper.doi
            paper.arxiv_id = arxiv_id or paper.arxiv_id
            if isinstance(semantic_id, str) and semantic_id:
                paper.semantic_scholar_id = semantic_id
            session.flush()
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id, CanvasPaper.paper_id == paper.id
            )
        )
        if membership is None:
            session.add(
                CanvasPaper(
                    canvas_id=canvas_id,
                    paper_id=paper.id,
                    origin="user",
                    processing_status="queued",
                )
            )
        elif membership.processing_status == "failed":
            membership.processing_status = "queued"
            membership.error = None
        if membership is not None:
            membership.deleted_at = None
        session.commit()
        return paper.id


def _merge_queued_placeholder(session: Any, placeholder: Paper, paper: Paper) -> None:
    """Move safe queued references to the canonical paper in one transaction."""
    if placeholder.id == paper.id:
        return
    for model in (PaperSource, Review, Evidence):
        if session.scalar(select(model.id).where(model.paper_id == placeholder.id).limit(1)):
            raise PaperLinkError(
                "paper_identity_conflict",
                "The queued paper already has dependent evidence and cannot be reconciled safely.",
            )

    memberships = list(
        session.scalars(select(CanvasPaper).where(CanvasPaper.paper_id == placeholder.id))
    )
    memberships_to_move: list[CanvasPaper] = []
    for membership in memberships:
        existing = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == membership.canvas_id,
                CanvasPaper.paper_id == paper.id,
            )
        )
        if existing is None:
            memberships_to_move.append(membership)
            continue
        existing.deleted_at = None
        existing.processing_status = "queued"
        existing.error = None
        session.delete(membership)
    session.flush()
    for membership in memberships_to_move:
        membership.paper_id = paper.id

    relationships = list(
        session.scalars(
            select(Relationship).where(
                or_(
                    Relationship.source_paper_id == placeholder.id,
                    Relationship.target_paper_id == placeholder.id,
                )
            )
        )
    )
    for relationship in relationships:
        if relationship.source_paper_id == placeholder.id:
            relationship.source_paper_id = paper.id
        if relationship.target_paper_id == placeholder.id:
            relationship.target_paper_id = paper.id
        if relationship.source_paper_id == relationship.target_paper_id:
            relationship.deleted_at = datetime.now(UTC)

    for row in session.scalars(
        select(DiscoveryDecision).where(DiscoveryDecision.paper_id == placeholder.id)
    ):
        row.paper_id = paper.id
    for row in session.scalars(select(Job).where(Job.paper_id == placeholder.id)):
        row.paper_id = paper.id
    for row in session.scalars(
        select(ActiveReviewMessage).where(ActiveReviewMessage.paper_id == placeholder.id)
    ):
        row.paper_id = paper.id
    for row in session.scalars(
        select(Conversation).where(Conversation.paper_id == placeholder.id)
    ):
        row.paper_id = paper.id
    for row in session.scalars(select(Message).where(Message.paper_id == placeholder.id)):
        row.paper_id = paper.id
    for row in session.scalars(
        select(UserRevision).where(
            UserRevision.entity_type == "paper", UserRevision.entity_id == placeholder.id
        )
    ):
        row.entity_id = paper.id
    session.flush()
    session.delete(placeholder)
    session.flush()


def _paper_identity_conflict() -> PaperLinkError:
    return PaperLinkError(
        "paper_identity_conflict",
        "The paper identity could not be reconciled safely. Retry the paper after refreshing the canvas.",
    )


def create_canvas_paper_placeholder(
    database: Database,
    canvas_id: str,
    link: str,
    *,
    x: float | None = None,
    y: float | None = None,
) -> str:
    """Persist the stable queued node before asynchronous metadata resolution."""
    identifier, doi, arxiv_id, openalex_id = _identifier(link)
    canonical_arxiv_id = _normalized_arxiv(arxiv_id)
    semantic_id = identifier if re.fullmatch(r"[0-9a-fA-F]{20,64}", identifier) else None
    label = doi or canonical_arxiv_id or openalex_id or urlparse(link).netloc
    title = f"Queued paper: {label}"
    normalized_title = f"queued-paper-{abs(hash(link.strip()))}"
    for attempt in range(2):
        try:
            return _create_canvas_paper_placeholder_once(
                database,
                canvas_id,
                link.strip(),
                doi=doi,
                arxiv_id=canonical_arxiv_id,
                semantic_id=semantic_id,
                title=title,
                normalized_title=normalized_title,
                x=x,
                y=y,
            )
        except IntegrityError as error:
            if attempt == 0:
                continue
            raise _paper_identity_conflict() from error
        except SQLAlchemyError as error:
            raise _paper_identity_conflict() from error
    raise _paper_identity_conflict()


def _create_canvas_paper_placeholder_once(
    database: Database,
    canvas_id: str,
    link: str,
    *,
    doi: str | None,
    arxiv_id: str | None,
    semantic_id: str | None,
    title: str,
    normalized_title: str,
    x: float | None,
    y: float | None,
) -> str:
    with database.session_context() as session:
        if session.get(Canvas, canvas_id) is None:
            raise PaperLinkError("canvas_not_found", "Canvas does not exist.")
        identities = [Paper.url == link]
        if doi:
            identities.append(Paper.doi == doi)
        if arxiv_id:
            identities.append(Paper.arxiv_id == arxiv_id)
        if semantic_id:
            identities.append(Paper.semantic_scholar_id == semantic_id)
        paper = session.scalar(select(Paper).where(or_(*identities)).limit(1))
        if paper is None:
            paper = Paper(
                title=title,
                normalized_title=normalized_title,
                authors=[],
                year=None,
                month=None,
                summary="",
                url=link,
                doi=doi,
                arxiv_id=arxiv_id,
                semantic_scholar_id=semantic_id,
            )
            session.add(paper)
            session.flush()
        membership = session.scalar(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.paper_id == paper.id,
            )
        )
        if membership is None:
            session.add(
                CanvasPaper(
                    canvas_id=canvas_id,
                    paper_id=paper.id,
                    x=x if x is not None else 0,
                    y=y if y is not None else 0,
                    origin="user",
                    processing_status="queued",
                )
            )
        else:
            membership.processing_status = "queued"
            membership.error = None
            membership.deleted_at = None
        session.commit()
        return paper.id


def _identifier(link: str) -> tuple[str, str | None, str | None, str | None]:
    value = link.strip()
    if not value:
        raise PaperLinkError("invalid_paper_link", "Paper link cannot be empty.")
    decoded = unquote(value)
    doi_match = re.search(r"10\.\d{4,9}/[^\s?#]+", decoded, re.IGNORECASE)
    doi = doi_match.group(0).rstrip(".,;)]") if doi_match else None
    arxiv_match = re.search(
        rf"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)({ARXIV_ID_PATTERN})",
        decoded,
        re.IGNORECASE,
    )
    if arxiv_match is None:
        arxiv_match = re.fullmatch(
            rf"({ARXIV_ID_PATTERN})",
            decoded,
            re.IGNORECASE,
        )
    arxiv_id = arxiv_match.group(1) if arxiv_match else None
    openalex_match = re.search(r"openalex\.org/(W\d+)", decoded, re.IGNORECASE)
    openalex_id = openalex_match.group(1).upper() if openalex_match else None
    semantic_match = re.search(r"semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{20,64})", decoded, re.IGNORECASE)
    if doi:
        return f"DOI:{doi}", doi.casefold(), arxiv_id, openalex_id
    if arxiv_id:
        return f"ARXIV:{arxiv_id}", None, arxiv_id, openalex_id
    if semantic_match:
        return semantic_match.group(1), None, None, openalex_id
    if openalex_id:
        return openalex_id, None, None, openalex_id
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"URL:{value}", None, None, None
    raise PaperLinkError(
        "invalid_paper_link", "Submit a DOI, arXiv, OpenAlex, or Semantic Scholar paper link."
    )


def _semantic_candidate(row: dict[str, Any], submitted: str) -> dict[str, Any]:
    title = row.get("title")
    year = _optional_year(row.get("year"))
    if not isinstance(title, str) or not title.strip():
        raise PaperLinkError("invalid_paper_metadata", "Semantic Scholar metadata is incomplete.")
    external = row.get("externalIds") if isinstance(row.get("externalIds"), dict) else {}
    oa = row.get("openAccessPdf") if isinstance(row.get("openAccessPdf"), dict) else {}
    authors = row.get("authors") if isinstance(row.get("authors"), list) else []
    paper_id = row.get("paperId")
    return {
        "source_provider": "semantic_scholar",
        "candidate_id": f"semantic-scholar:{paper_id}",
        "semantic_scholar_id": paper_id,
        "doi": external.get("DOI"),
        "arxiv_id": external.get("ArXiv"),
        "title": title.strip(),
        "authors": [item["name"] for item in authors if isinstance(item, dict) and isinstance(item.get("name"), str)],
        "year": year,
        "month": _month(row.get("publicationDate")) if year is not None else None,
        "abstract": row.get("abstract") or "",
        "url": row.get("url") or submitted,
        "pdf_url": oa.get("url") if isinstance(oa.get("url"), str) else None,
        "venue": row.get("venue") or "",
        "citation_count": row.get("citationCount") or 0,
    }


def _openalex_candidate(row: dict[str, Any], submitted: str) -> dict[str, Any]:
    title = row.get("display_name") or row.get("title")
    year = _optional_year(row.get("publication_year"))
    if not isinstance(title, str) or not title.strip():
        raise PaperLinkError("invalid_paper_metadata", "OpenAlex metadata is incomplete.")
    primary = row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
    best = row.get("best_oa_location") if isinstance(row.get("best_oa_location"), dict) else {}
    raw_id = row.get("id") if isinstance(row.get("id"), str) else submitted
    authorships = row.get("authorships") if isinstance(row.get("authorships"), list) else []
    authors = []
    for item in authorships:
        author = item.get("author") if isinstance(item, dict) else None
        if isinstance(author, dict) and isinstance(author.get("display_name"), str):
            authors.append(author["display_name"])
    ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
    arxiv = normalize_arxiv_identifier(ids.get("arxiv"))
    return {
        "source_provider": "openalex",
        "candidate_id": f"openalex:{raw_id.rstrip('/').rsplit('/', 1)[-1]}",
        "semantic_scholar_id": None,
        "doi": row.get("doi"),
        "arxiv_id": arxiv,
        "title": title.strip(),
        "authors": authors,
        "year": year,
        "month": _month(row.get("publication_date")) if year is not None else None,
        "abstract": _openalex_abstract(row.get("abstract_inverted_index")),
        "url": primary.get("landing_page_url") or raw_id,
        "pdf_url": best.get("pdf_url") or primary.get("pdf_url"),
        "best_oa_location": best,
        "primary_location": primary,
        "citation_count": row.get("cited_by_count") or 0,
    }


def _month(value: object) -> int | None:
    if isinstance(value, str) and re.match(r"^\d{4}-(\d{2})", value):
        return _optional_month(int(value[5:7]))
    return None


def _optional_year(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1600 <= value <= 2200:
        raise PaperLinkError(
            "invalid_paper_metadata", "Paper publication year is invalid."
        )
    return value


def _optional_month(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 12:
        raise PaperLinkError(
            "invalid_paper_metadata", "Paper publication month is invalid."
        )
    return value


def _openalex_abstract(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    words = []
    for word, positions in value.items():
        if isinstance(word, str) and isinstance(positions, list):
            words.extend((position, word) for position in positions if isinstance(position, int))
    return " ".join(word for _, word in sorted(words))


def _normalized_doi(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.casefold().removeprefix("https://doi.org/").removeprefix("doi:").strip()


def _normalized_arxiv(value: object) -> str | None:
    return normalize_arxiv_identifier(value)
