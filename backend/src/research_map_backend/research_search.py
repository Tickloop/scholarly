from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from research_map_backend.schemas import normalize_arxiv_identifier


_RESEARCH_TERM_STOP_WORDS = {
    "about",
    "agent",
    "all",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "brief",
    "build",
    "by",
    "can",
    "canvas",
    "compare",
    "could",
    "did",
    "do",
    "does",
    "evolution",
    "evolve",
    "evolved",
    "find",
    "for",
    "foundational",
    "from",
    "had",
    "has",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "map",
    "may",
    "might",
    "must",
    "need",
    "no",
    "not",
    "objective",
    "of",
    "on",
    "or",
    "our",
    "paper",
    "plus",
    "research",
    "should",
    "study",
    "that",
    "the",
    "their",
    "them",
    "these",
    "they",
    "this",
    "those",
    "to",
    "topic",
    "using",
    "via",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "work",
    "would",
    "you",
    "your",
}


def meaningful_research_terms(value: object) -> list[str]:
    """Return ordered topic terms with only small, deterministic normalization."""
    if not isinstance(value, str):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", value.casefold()):
        if len(raw) < 3:
            continue
        term = raw
        if len(term) > 4 and term.endswith("ies"):
            term = f"{term[:-3]}y"
        elif (
            len(term) > 4
            and term.endswith("s")
            and not term.endswith(("ss", "us", "is"))
        ):
            term = term[:-1]
        if len(term) < 3 or term in _RESEARCH_TERM_STOP_WORDS or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


class ResearchSearchError(RuntimeError):
    """A safe error from an academic metadata provider."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        no_results: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.no_results = no_results


async def search_academic_papers(
    research_goal: str,
    research_brief: str,
    candidate_count: int = 10,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search Semantic Scholar, falling back to OpenAlex on temporary failure."""
    if client is None:
        async with _client() as owned_client:
            return await search_academic_papers(
                research_goal, research_brief, candidate_count, client=owned_client
            )
    candidates: list[dict[str, Any]] = []
    errors: list[tuple[str, ResearchSearchError]] = []
    providers = (
        ("Semantic Scholar", search_semantic_scholar),
        ("OpenAlex", search_openalex),
        ("arXiv", search_arxiv),
    )
    for provider_name, provider in providers:
        try:
            provider_candidates = await provider(
                research_goal, research_brief, candidate_count, client=client
            )
        except ResearchSearchError as error:
            errors.append((provider_name, error))
            continue
        candidates = _merge_candidates(candidates, provider_candidates, candidate_count)
        if len(candidates) >= candidate_count:
            return candidates[:candidate_count]
    if candidates:
        return candidates
    if errors and all(error.no_results for _, error in errors):
        raise ResearchSearchError(
            "Academic search providers returned no verified candidates.",
            no_results=True,
        )
    details = " ".join(f"{name}: {error}" for name, error in errors)
    retry_after = next(
        (
            error.retry_after_seconds
            for _, error in reversed(errors)
            if error.retry_after_seconds is not None
        ),
        None,
    )
    raise ResearchSearchError(
        f"Academic search providers are unavailable. {details}",
        retryable=any(error.retryable for _, error in errors),
        retry_after_seconds=retry_after,
    )


async def search_semantic_scholar(
    research_goal: str,
    research_brief: str,
    candidate_count: int = 10,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return real, unique Semantic Scholar paper candidates."""
    if client is None:
        async with _client() as owned_client:
            return await search_semantic_scholar(
                research_goal, research_brief, candidate_count, client=owned_client
            )
    seeds = _search_seed_titles(research_goal, research_brief)
    batches = await asyncio.gather(
        *(_semantic_rows(client, seed, 5) for seed in seeds),
        _semantic_rows(
            client,
            _query(research_goal),
            min(max(candidate_count * 3, candidate_count), 100),
        ),
        return_exceptions=True,
    )
    topic_rows = batches[-1]
    if isinstance(topic_rows, BaseException):
        raise topic_rows
    rows: list[dict[str, Any]] = []
    for seed, seed_rows in zip(seeds, batches[:-1], strict=True):
        if isinstance(seed_rows, BaseException):
            continue
        best = _best_seed_row(seed, seed_rows, ("title",))
        if best is not None:
            rows.append(best)
    rows.extend(topic_rows)

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        paper_id = row.get("paperId")
        title = row.get("title")
        year = row.get("year")
        if (
            not isinstance(paper_id, str)
            or not isinstance(title, str)
            or not _valid_optional_year(year)
        ):
            continue
        normalized_title = _title_key(title)
        if paper_id in seen_ids or normalized_title in seen_titles:
            continue
        seen_ids.add(paper_id)
        seen_titles.add(normalized_title)

        external_ids = row.get("externalIds")
        if not isinstance(external_ids, dict):
            external_ids = {}
        open_access_pdf = row.get("openAccessPdf")
        pdf_url = (
            open_access_pdf.get("url")
            if isinstance(open_access_pdf, dict)
            and isinstance(open_access_pdf.get("url"), str)
            else None
        )
        authors = row.get("authors")
        author_names = (
            [
                author["name"]
                for author in authors
                if isinstance(author, dict)
                and isinstance(author.get("name"), str)
            ]
            if isinstance(authors, list)
            else []
        )
        candidates.append(
            {
                "source_provider": "semantic_scholar",
                "candidate_id": f"semantic-scholar:{paper_id}",
                "semantic_scholar_id": paper_id,
                "doi": external_ids.get("DOI"),
                "arxiv_id": external_ids.get("ArXiv"),
                "title": title.strip(),
                "authors": author_names,
                "year": year,
                "publication_date": row.get("publicationDate"),
                "venue": row.get("venue") or "",
                "abstract": row.get("abstract") or "",
                "url": row.get("url") or pdf_url,
                "pdf_url": pdf_url,
                "citation_count": row.get("citationCount") or 0,
            }
        )
        if len(candidates) == candidate_count:
            break
    return _require_nonempty("Semantic Scholar", candidates)


async def search_openalex(
    research_goal: str,
    research_brief: str,
    candidate_count: int = 10,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return real, unique OpenAlex paper candidates in the shared contract."""
    if client is None:
        async with _client() as owned_client:
            return await search_openalex(
                research_goal, research_brief, candidate_count, client=owned_client
            )
    seeds = _search_seed_titles(research_goal, research_brief)
    batches = await asyncio.gather(
        *(_openalex_rows(client, seed, 5) for seed in seeds),
        _openalex_rows(
            client,
            _query(research_goal),
            min(max(candidate_count * 3, candidate_count), 100),
        ),
        return_exceptions=True,
    )
    topic_rows = batches[-1]
    if isinstance(topic_rows, BaseException):
        raise topic_rows
    rows: list[dict[str, Any]] = []
    for seed, seed_rows in zip(seeds, batches[:-1], strict=True):
        if isinstance(seed_rows, BaseException):
            continue
        best = _best_seed_row(seed, seed_rows, ("display_name", "title"))
        if best is not None:
            rows.append(best)
    rows.extend(topic_rows)

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        title = row.get("display_name") or row.get("title")
        year = row.get("publication_year")
        if (
            not isinstance(raw_id, str)
            or not isinstance(title, str)
            or not _valid_optional_year(year)
        ):
            continue
        openalex_id = raw_id.rstrip("/").rsplit("/", 1)[-1]
        normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
        if openalex_id in seen_ids or normalized_title in seen_titles:
            continue
        seen_ids.add(openalex_id)
        seen_titles.add(normalized_title)

        primary = row.get("primary_location")
        if not isinstance(primary, dict):
            primary = {}
        best_oa = row.get("best_oa_location")
        if not isinstance(best_oa, dict):
            best_oa = {}
        source = primary.get("source")
        venue = (
            source.get("display_name")
            if isinstance(source, dict)
            and isinstance(source.get("display_name"), str)
            else ""
        )
        authorships = row.get("authorships")
        authors = []
        if isinstance(authorships, list):
            for authorship in authorships:
                author = authorship.get("author") if isinstance(authorship, dict) else None
                if isinstance(author, dict) and isinstance(author.get("display_name"), str):
                    authors.append(author["display_name"])
        ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        arxiv_id = normalize_arxiv_identifier(ids.get("arxiv"))
        pdf_url = best_oa.get("pdf_url") or primary.get("pdf_url")
        locations = row.get("locations")
        candidates.append(
            {
                "source_provider": "openalex",
                "candidate_id": f"openalex:{openalex_id}",
                "semantic_scholar_id": None,
                "doi": row.get("doi"),
                "arxiv_id": arxiv_id,
                "title": title.strip(),
                "authors": authors,
                "year": year,
                "publication_date": row.get("publication_date"),
                "venue": venue,
                "abstract": _openalex_abstract(row.get("abstract_inverted_index")),
                "url": primary.get("landing_page_url") or raw_id,
                "pdf_url": pdf_url if isinstance(pdf_url, str) else None,
                "open_access_locations": (
                    locations if isinstance(locations, list) else []
                ),
                "citation_count": row.get("cited_by_count") or 0,
                "is_retracted": row.get("is_retracted") is True,
                "work_type": row.get("type"),
            }
        )
        if len(candidates) == candidate_count:
            break
    return _require_nonempty("OpenAlex", candidates)


async def search_arxiv(
    research_goal: str,
    research_brief: str,
    candidate_count: int = 10,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Resolve canonical seeds first, then fill from official arXiv relevance search."""
    if client is None:
        async with _client() as owned_client:
            return await search_arxiv(
                research_goal, research_brief, candidate_count, client=owned_client
            )
    seeds = _search_seed_titles(research_goal, research_brief)
    results = await asyncio.gather(
        *(
            _arxiv_page(client, _arxiv_title_query(seed), 0, 5)
            for seed in seeds
        ),
        _arxiv_topic_candidates(client, research_goal, candidate_count),
        return_exceptions=True,
    )
    seed_candidates: list[dict[str, Any]] = []
    for seed, rows in zip(seeds, results[:-1], strict=True):
        if isinstance(rows, BaseException):
            continue
        matched = _best_seed_candidate(seed, rows)
        if matched is not None:
            seed_candidates.append(matched)
    topic_result = results[-1]
    if isinstance(topic_result, BaseException):
        if not seed_candidates:
            raise topic_result
        topic_candidates: list[dict[str, Any]] = []
    else:
        topic_candidates = topic_result
    candidates = _merge_candidates(
        seed_candidates, topic_candidates, min(candidate_count, 100)
    )
    return _require_nonempty("arXiv", candidates)


async def _arxiv_topic_candidates(
    client: httpx.AsyncClient, research_goal: str, candidate_count: int
) -> list[dict[str, Any]]:
    page_size = min(50, max(1, candidate_count))
    max_results = min(candidate_count, 100)
    candidates: list[dict[str, Any]] = []
    query = f"all:{_arxiv_query(research_goal)}"
    for start in range(0, max_results, page_size):
        limit = min(page_size, max_results - start)
        try:
            page = await _arxiv_page(client, query, start, limit)
        except ResearchSearchError:
            if candidates:
                break
            raise
        candidates = _merge_candidates(candidates, page, max_results)
        if len(page) < limit:
            break
    return candidates


async def _arxiv_page(
    client: httpx.AsyncClient, query: str, start: int, limit: int
) -> list[dict[str, Any]]:
    response = await _get(
        client,
        "arXiv",
        "https://export.arxiv.org/api/query",
        {
            "search_query": query,
            "start": start,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        timeout=httpx.Timeout(20.0, connect=5.0),
    )
    if len(response.content) > 10_000_000:
        raise ResearchSearchError(
            "arXiv returned an oversized metadata response.", retryable=True
        )
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as error:
        raise ResearchSearchError(
            "arXiv returned invalid Atom metadata.", retryable=True
        ) from error
    atom = "{http://www.w3.org/2005/Atom}"
    arxiv = "{http://arxiv.org/schemas/atom}"
    candidates: list[dict[str, Any]] = []
    for entry in root.findall(f"{atom}entry"):
        raw_id = _atom_text(entry, f"{atom}id")
        canonical_id = normalize_arxiv_identifier(raw_id)
        title = " ".join(_atom_text(entry, f"{atom}title").split())
        abstract = " ".join(_atom_text(entry, f"{atom}summary").split())
        published = _atom_text(entry, f"{atom}published")
        title_key = _title_key(title)
        if (
            not canonical_id
            or not title
            or not abstract
            or not re.match(r"^\d{4}-\d{2}", published)
        ):
            continue
        authors = [
            " ".join(name.split())
            for author in entry.findall(f"{atom}author")
            if (name := _atom_text(author, f"{atom}name"))
        ]
        if not authors:
            continue
        pdf_url = None
        for link in entry.findall(f"{atom}link"):
            href = link.attrib.get("href")
            if not isinstance(href, str):
                continue
            if (
                link.attrib.get("title") == "pdf"
                or link.attrib.get("type") == "application/pdf"
            ):
                pdf_url = href.replace("http://", "https://", 1)
                break
        candidates.append(
            {
                "source_provider": "arxiv",
                "candidate_id": f"arxiv:{canonical_id}",
                "semantic_scholar_id": None,
                "doi": _atom_text(entry, f"{arxiv}doi") or None,
                "arxiv_id": canonical_id,
                "title": title,
                "authors": authors,
                "year": int(published[:4]),
                "month": int(published[5:7]),
                "publication_date": published[:10],
                "venue": "arXiv",
                "abstract": abstract,
                "url": f"https://arxiv.org/abs/{canonical_id}",
                "pdf_url": pdf_url or f"https://arxiv.org/pdf/{canonical_id}",
                "citation_count": 0,
            }
        )
    return _merge_candidates([], candidates, limit)


def _atom_text(element: ElementTree.Element, path: str) -> str:
    child = element.find(path)
    return child.text.strip() if child is not None and isinstance(child.text, str) else ""


def _arxiv_query(research_goal: str) -> str:
    """Build a bounded plain relevance query without arXiv query operators."""
    plain = _query(research_goal)
    safe_tokens = meaningful_research_terms(plain)[:24]
    query = " ".join(safe_tokens)
    if len(query) > 180:
        query = query[:180].rsplit(" ", 1)[0]
    if not query:
        raise ResearchSearchError("The research goal has no safe arXiv search terms.")
    return query


def _arxiv_title_query(title: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", title)
    safe = " ".join(tokens)
    if len(safe) > 300:
        safe = safe[:300].rsplit(" ", 1)[0]
    if not safe:
        raise ResearchSearchError("Canonical seed title has no safe arXiv terms.")
    return f'ti:"{safe}"'


def _best_seed_candidate(
    seed: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    seed_terms = set(_title_key(seed).split())
    best: tuple[float, dict[str, Any]] | None = None
    if not seed_terms:
        return None
    for candidate in candidates:
        title = candidate.get("title")
        if not isinstance(title, str):
            continue
        title_terms = set(_title_key(title).split())
        if not title_terms:
            continue
        intersection = len(seed_terms & title_terms)
        coverage = intersection / len(seed_terms)
        similarity = intersection / len(seed_terms | title_terms)
        score = (coverage + similarity) / 2
        if coverage >= 0.8 and similarity >= 0.65 and (
            best is None or score > best[0]
        ):
            best = (score, candidate)
    return best[1] if best is not None else None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        headers={"User-Agent": "research-map/0.1"},
    )


def _valid_optional_year(value: object) -> bool:
    return value is None or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1600 <= value <= 2200
    )


async def _semantic_rows(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[dict[str, Any]]:
    response = await _get(
        client,
        "Semantic Scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {
            "query": query,
            "limit": limit,
            "fields": (
                "paperId,title,abstract,authors,year,venue,url,citationCount,"
                "externalIds,openAccessPdf,publicationDate"
            ),
        },
    )
    try:
        body = response.json()
    except ValueError as error:
        raise ResearchSearchError(
            "Semantic Scholar returned invalid JSON.", retryable=True
        ) from error
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ResearchSearchError(
            "Semantic Scholar returned invalid paper data.", retryable=True
        )
    return [row for row in rows if isinstance(row, dict)]


async def _openalex_rows(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[dict[str, Any]]:
    response = await _get(
        client,
        "OpenAlex",
        "https://api.openalex.org/works",
        _openalex_params(query, limit),
    )
    try:
        body = response.json()
    except ValueError as error:
        raise ResearchSearchError(
            "OpenAlex returned invalid JSON.", retryable=True
        ) from error
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ResearchSearchError(
            "OpenAlex returned invalid paper data.", retryable=True
        )
    return [row for row in rows if isinstance(row, dict)]


def _best_seed_row(
    seed: str, rows: list[dict[str, Any]], title_keys: tuple[str, ...]
) -> dict[str, Any] | None:
    seed_terms = set(_title_key(seed).split())
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        raw_title = next(
            (
                row.get(key)
                for key in title_keys
                if isinstance(row.get(key), str) and row.get(key)
            ),
            None,
        )
        if not isinstance(raw_title, str):
            continue
        title_terms = set(_title_key(raw_title).split())
        if not title_terms:
            continue
        score = len(seed_terms & title_terms) / len(seed_terms | title_terms)
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best is not None and best[0] >= 0.5 else None


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def _seed_titles(research_brief: str) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    in_seed_section = False
    for raw_line in research_brief.splitlines():
        line = raw_line.strip()
        plain = re.sub(r"[*_`#]+", "", line).strip().rstrip(":")
        is_heading = bool(re.match(r"^#{1,6}\s+", line)) or bool(
            re.match(r"^(?:\*\*|__)[^*]+(?:\*\*|__)$", line)
        )
        if is_heading:
            in_seed_section = _is_seed_heading(plain)
            continue
        if not in_seed_section or not re.match(r"^[-*+]\s+", line):
            continue
        bold_titles = re.findall(r"\*\*([^*]+)\*\*|__([^_]+)__", line)
        candidates = [left or right for left, right in bold_titles]
        if not candidates:
            italic_titles = re.findall(
                r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)", line
            )
            candidates = [left or right for left, right in italic_titles]
        if not candidates:
            candidates = re.findall(r'[“"]([^”"]+)[”"]', line)
        if not candidates:
            candidate = re.sub(r"^[-*+]\s+", "", line)
            if re.search(r"[,;]", candidate) or re.match(
                r"(?i)^(?:additional|earlier|later|other|related)\b", candidate
            ):
                continue
            candidate = re.split(r"\s+[—–]\s+", candidate, maxsplit=1)[0]
            candidate = re.sub(r"(?i)^the\s+(?:original\s+)?", "", candidate)
            candidate = re.sub(r"(?i)\s+paper\s*$", "", candidate)
            candidates = [candidate]
        for candidate in candidates:
            candidate = re.sub(r"\s+\([A-Z0-9-]{2,12}\)\s*$", "", candidate).strip()
            key = _title_key(candidate)
            if len(key.split()) < 4 or key in seen:
                continue
            seen.add(key)
            titles.append(candidate)
    return titles[:8]


def _is_seed_heading(heading: str) -> bool:
    words = set(re.findall(r"[a-z]+", heading.casefold()))
    signals = {
        "anchor",
        "anchors",
        "canonical",
        "foundational",
        "key",
        "priority",
        "required",
        "seed",
    }
    return bool(words & {"anchor", "anchors", "canonical", "key", "priority", "seed"}) or (
        bool(words & {"paper", "papers"}) and bool(words & signals)
    )


def _search_seed_titles(research_goal: str, research_brief: str) -> list[str]:
    combined = [*_goal_seed_titles(research_goal), *_seed_titles(research_brief)]
    result: list[str] = []
    seen: set[str] = set()
    for title in combined:
        key = _title_key(title)
        if key in seen:
            continue
        seen.add(key)
        result.append(title)
    return result[:8]


def _goal_seed_titles(research_goal: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(re.findall(r"\*\*([^*]+)\*\*", research_goal))
    candidates.extend(re.findall(r'[“"]([^”"]+)[”"]', research_goal))
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            r"(?i)\boriginal\s+(.+?)\s+paper(?=\s*(?:and\b|plus\b|[,.;]|$))",
            research_goal,
        )
    )
    titles: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip().rstrip(".,;:")
        key = _title_key(candidate)
        if len(key.split()) < 4 or key in seen:
            continue
        seen.add(key)
        titles.append(candidate)
    return titles[:8]


def _query(research_brief: str) -> str:
    normalized = unicodedata.normalize("NFKC", research_brief)
    normalized = "".join(
        " "
        if unicodedata.category(character).startswith("C")
        and character not in {"\n", "\r"}
        else character
        for character in normalized
    )
    source = _preferred_query_text(normalized)
    source = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", source)
    source = re.sub(r"<[^>]+>", " ", source)
    terms = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", source, flags=re.UNICODE)
    generic_labels = {
        "and",
        "assumption",
        "assumptions",
        "brief",
        "closely",
        "compare",
        "established",
        "find",
        "focus",
        "foundational",
        "goal",
        "identify",
        "map",
        "objective",
        "only",
        "original",
        "paper",
        "papers",
        "plus",
        "primary",
        "related",
        "research",
        "scope",
        "that",
        "the",
        "topic",
        "topics",
    }
    useful_terms: list[str] = []
    seen: set[str] = set()
    for term in terms:
        folded = term.casefold()
        if folded in generic_labels or folded in seen:
            continue
        seen.add(folded)
        useful_terms.append(term)
        if len(useful_terms) == 24:
            break
    query = " ".join(useful_terms)
    if len(query) > 180:
        query = query[:180].rsplit(" ", 1)[0]
    if not query.strip():
        raise ResearchSearchError("The stored research brief is empty.")
    return query.strip()


def _preferred_query_text(brief: str) -> str:
    lines = [line.strip() for line in brief.splitlines()]
    generic_headings = {
        "assumptions",
        "brief",
        "comparison dimensions",
        "core topics",
        "desired canvas structure",
        "exclusions",
        "expected output",
        "inclusion criteria",
        "outputs",
        "overview",
        "priority papers",
        "research brief",
        "scope",
        "useful assumptions",
        "workflow status",
    }
    heading_candidates: list[str] = []
    for line in lines:
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if not heading:
            continue
        text = heading.group(1).strip()
        titled = re.match(r"(?i)^research\s+brief\s*:\s*(.+)$", text)
        if titled:
            return titled.group(1).strip()
        plain_heading = re.sub(r"[*_`]+", "", text).strip()
        if (
            plain_heading
            and not plain_heading.endswith(":")
            and plain_heading.casefold() not in generic_headings
        ):
            heading_candidates.append(plain_heading)

    label = re.compile(
        r"^(?:goal|objective|research\s+topic|topic|title)\s*:?[ \t]*(.*)$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        plain_line = re.sub(r"[*_`]+", "", line).strip()
        matched = label.match(plain_line)
        if not matched:
            continue
        inline = matched.group(1).strip()
        value_parts = [inline] if inline else []
        for following in lines[index + 1:]:
            if not following:
                if value_parts:
                    break
                continue
            next_plain = re.sub(r"[*_`]+", "", following).strip()
            if following.startswith("#") or label.match(next_plain):
                break
            value_parts.append(following)
        if value_parts:
            return " ".join(value_parts)
    if heading_candidates:
        return heading_candidates[0]
    return brief


def _openalex_params(query_text: str, result_limit: int) -> dict[str, Any]:
    return {
        "search": _query(query_text),
        "per-page": min(max(result_limit, 1), 100),
        "select": (
            "id,doi,title,display_name,publication_year,publication_date,"
            "primary_location,best_oa_location,locations,authorships,cited_by_count,"
            "open_access,abstract_inverted_index,ids,type,is_retracted"
        ),
    }


async def _get(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    params: dict[str, Any],
    *,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    try:
        request_options = {"timeout": timeout} if timeout is not None else {}
        response = await client.get(url, params=params, **request_options)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as error:
        status_code = error.response.status_code
        raise ResearchSearchError(
            f"{provider} search failed with status {status_code}.",
            retryable=status_code == 429 or status_code >= 500,
            retry_after_seconds=_retry_after_seconds(
                error.response.headers.get("retry-after")
            ),
        ) from error
    except httpx.HTTPError as error:
        raise ResearchSearchError(
            f"{provider} search is unavailable.", retryable=True
        ) from error


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _require_nonempty(
    provider: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidates:
        raise ResearchSearchError(
            f"{provider} returned no verified candidates.",
            no_results=True,
        )
    return candidates


def _merge_candidates(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in [*first, *second]:
        keys = {
            f"{field}:{str(value).casefold().removeprefix('https://doi.org/')}"
            for field in ("doi", "arxiv_id", "semantic_scholar_id", "candidate_id")
            if isinstance((value := candidate.get(field)), str) and value.strip()
        }
        title_key = _title_key(str(candidate.get("title", "")))
        if title_key:
            keys.add(f"title:{title_key}")
        if not keys or keys & seen:
            continue
        seen.update(keys)
        merged.append(candidate)
        if len(merged) == limit:
            break
    return merged


def _openalex_abstract(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        positioned.extend(
            (position, word)
            for position in positions
            if isinstance(position, int) and not isinstance(position, bool)
        )
    return " ".join(word for _, word in sorted(positioned))
