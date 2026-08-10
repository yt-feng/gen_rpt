from __future__ import annotations

import math
import os
import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

import requests

from .web_fetch import SourceDocument


OPENALEX_BASE_URL = "https://api.openalex.org"
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+]{1,}", re.IGNORECASE)
_QUERY_NOISE = {
    "across",
    "and",
    "annual",
    "arabia",
    "china",
    "company",
    "current",
    "east",
    "economics",
    "evidence",
    "filetype",
    "for",
    "from",
    "global",
    "government",
    "gulf",
    "historical",
    "in",
    "investment",
    "market",
    "markets",
    "middle",
    "of",
    "official",
    "on",
    "opportunities",
    "opportunity",
    "outlook",
    "pdf",
    "regional",
    "regulator",
    "report",
    "saudi",
    "statistics",
    "the",
    "to",
    "uae",
    "with",
}
_GEOGRAPHY_TERMS = ("China", "Gulf", "Middle East", "UAE", "Saudi Arabia")
_ANCHOR_GROUPS = (
    ("data centre", "data center"),
    ("brain computer interface", "brain machine interface"),
    ("large language model", "foundation model"),
    ("capital market", "equity market"),
    ("commercial space", "space launch"),
    ("digital asset", "crypto asset", "cryptocurrency"),
    ("merger acquisition", "mergers acquisitions"),
    ("optical module", "optical interconnect"),
    ("real estate", "property market"),
    ("semiconductor", "integrated circuit"),
    ("sovereign wealth fund", "sovereign investment fund"),
)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _anchor_group(value: str) -> tuple[str, ...]:
    normalized = _normalized_text(value)
    return next((group for group in _ANCHOR_GROUPS if any(phrase in normalized for phrase in group)), ())


def reconstruct_abstract(inverted_index: Mapping[str, Any] | None) -> str:
    """Rebuild the abstract text returned by OpenAlex's inverted index."""

    if not isinstance(inverted_index, Mapping) or not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for token, raw_positions in inverted_index.items():
        if not isinstance(raw_positions, list):
            continue
        for raw_position in raw_positions:
            try:
                positioned.append((int(raw_position), str(token)))
            except (TypeError, ValueError):
                continue
    positioned.sort(key=lambda item: item[0])
    return re.sub(r"\s+", " ", " ".join(token for _, token in positioned)).strip()


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _query_terms(value: str) -> set[str]:
    value = re.sub(r"[-_/]", " ", str(value or ""))
    return {
        token.lower()
        for token in _WORD_RE.findall(value)
        if token.lower() not in _QUERY_NOISE
    }


def _ordered_query_terms(value: str) -> list[str]:
    terms: list[str] = []
    normalized = re.sub(r"[-_/]", " ", str(value or ""))
    for token in _WORD_RE.findall(normalized):
        token = token.lower()
        if token in _QUERY_NOISE or token in terms:
            continue
        terms.append(token)
    return terms


def _academic_queries(topic: str, search_queries: Sequence[str]) -> list[str]:
    maximum = _integer("GATEX_OPENALEX_MAX_QUERIES", 3, 1, 5)
    core_terms = _ordered_query_terms(topic)[:9]
    candidates = [" ".join(core_terms)] if core_terms else [topic]
    geographies = [name for name in _GEOGRAPHY_TERMS if name.lower() in topic.lower()]
    candidates.extend(f"{' '.join(core_terms[:7])} {geography}" for geography in geographies)
    candidates.extend(" ".join(_ordered_query_terms(query)[:9]) for query in search_queries)
    selected: list[str] = []
    seen: set[str] = set()
    topic_terms = set(core_terms)
    for candidate in candidates:
        cleaned = re.sub(r"\b20\d{2}\b", "", str(candidate or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")[:280]
        key = re.sub(r"\W+", "", cleaned.lower())
        if len(cleaned) < 12 or key in seen:
            continue
        terms = _query_terms(cleaned)
        if selected and topic_terms and len(terms & topic_terms) < 2:
            continue
        seen.add(key)
        selected.append(cleaned)
        if len(selected) >= maximum:
            break
    return selected


def _venue(work: Mapping[str, Any]) -> str:
    for location_key in ("primary_location", "best_oa_location"):
        location = work.get(location_key)
        if not isinstance(location, Mapping):
            continue
        source = location.get("source")
        if isinstance(source, Mapping) and source.get("display_name"):
            return str(source["display_name"])
    return ""


def _authors(work: Mapping[str, Any], limit: int = 6) -> list[str]:
    names: list[str] = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, Mapping):
            continue
        author = authorship.get("author")
        name = str(author.get("display_name") or "").strip() if isinstance(author, Mapping) else ""
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _work_score(work: Mapping[str, Any], query: str) -> float:
    title = str(work.get("display_name") or "")
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    query_terms = _query_terms(query)
    work_terms = _query_terms(f"{title} {abstract}")
    overlap = len(query_terms & work_terms) / max(1, len(query_terms))
    citations = max(0, int(work.get("cited_by_count") or 0))
    year = int(work.get("publication_year") or 0)
    recency = max(0, year - (date.today().year - 8))
    open_access = work.get("open_access") if isinstance(work.get("open_access"), Mapping) else {}
    anchors = _anchor_group(query)
    title_text = _normalized_text(title)
    abstract_text = _normalized_text(abstract)
    anchor_bonus = 0.0
    if anchors and any(anchor in title_text for anchor in anchors):
        anchor_bonus = 24.0
    elif anchors and any(anchor in abstract_text for anchor in anchors):
        anchor_bonus = 8.0
    return (
        overlap * 60.0
        + min(16.0, math.log1p(citations) * 2.5)
        + min(8.0, float(recency))
        + (4.0 if work.get("doi") else 0.0)
        + (3.0 if open_access.get("is_oa") else 0.0)
        + anchor_bonus
    )


def _work_is_relevant(work: Mapping[str, Any], query: str) -> bool:
    title = str(work.get("display_name") or "")
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    query_terms = _query_terms(query)
    work_text = f"{title} {abstract}"
    overlap = query_terms & _query_terms(work_text)
    minimum = 3 if len(query_terms) >= 6 else 2
    anchors = _anchor_group(query)
    if anchors and not any(anchor in _normalized_text(work_text) for anchor in anchors):
        return False
    return len(overlap) >= minimum


def _work_to_source(work: Mapping[str, Any], query: str) -> SourceDocument | None:
    if work.get("is_retracted") is True:
        return None
    title = re.sub(r"\s+", " ", str(work.get("display_name") or "")).strip()
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    if not title or len(abstract) < 180:
        return None
    doi = str(work.get("doi") or "").strip()
    openalex_id = str(work.get("id") or "").strip()
    url = doi if doi.startswith("https://") else openalex_id
    if not url.startswith("https://"):
        return None
    year = int(work.get("publication_year") or 0)
    publication_date = str(work.get("publication_date") or "")
    citations = max(0, int(work.get("cited_by_count") or 0))
    authors = _authors(work)
    venue = _venue(work)
    open_access = work.get("open_access") if isinstance(work.get("open_access"), Mapping) else {}
    details = [
        f"Title: {title}",
        f"Publication year: {year}" if year else "",
        f"Authors: {', '.join(authors)}" if authors else "",
        f"Venue: {venue}" if venue else "",
        f"Abstract: {abstract}",
    ]
    return SourceDocument(
        title=title,
        url=url,
        query=query,
        snippet=abstract[:700],
        content="\n".join(item for item in details if item),
        source_type="academic",
        content_type="application/json",
        domain=urlparse(url).netloc.lower(),
        confidence=min(1.0, _work_score(work, query) / 90.0),
        metadata={
            "search_provider": "openalex",
            "academic": True,
            "openalex_id": openalex_id,
            "doi": doi,
            "publication_year": year,
            "publication_date": publication_date,
            "cited_by_count": citations,
            "is_open_access": bool(open_access.get("is_oa")),
            "venue": venue,
            "authors": authors,
        },
    )


def _dedupe_ranked(rows: Iterable[tuple[float, SourceDocument]], maximum: int) -> list[SourceDocument]:
    output: list[SourceDocument] = []
    seen: set[str] = set()
    for _, source in sorted(rows, key=lambda item: item[0], reverse=True):
        doi = str(source.metadata.get("doi") or "").lower()
        key = doi or re.sub(r"\W+", "", source.title.lower())[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(source)
        if len(output) >= maximum:
            break
    return output


def collect_openalex_sources(topic: str, search_queries: Sequence[str]) -> list[SourceDocument]:
    """Fetch a small, ranked academic supplement without blocking core research."""

    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        print("[gatex.openalex] disabled | OPENALEX_API_KEY is not configured", flush=True)
        return []
    base_url = os.getenv("OPENALEX_BASE_URL", OPENALEX_BASE_URL).rstrip("/")
    per_query = _integer("GATEX_OPENALEX_PER_QUERY", 8, 3, 20)
    maximum = _integer("GATEX_OPENALEX_MAX_SOURCES", 6, 1, 10)
    timeout = _integer("GATEX_OPENALEX_TIMEOUT", 30, 5, 90)
    min_year = _integer("GATEX_OPENALEX_MIN_YEAR", date.today().year - 7, 1900, date.today().year)
    ranked: list[tuple[float, SourceDocument]] = []
    queries = _academic_queries(topic, search_queries)
    for query in queries:
        try:
            response = requests.get(
                f"{base_url}/works",
                params={
                    "api_key": api_key,
                    "search": query,
                    "filter": f"from_publication_date:{min_year}-01-01,has_abstract:true,is_retracted:false",
                    "sort": "relevance_score:desc",
                    "per_page": per_query,
                    "select": (
                        "id,doi,display_name,publication_year,publication_date,cited_by_count,"
                        "abstract_inverted_index,authorships,primary_location,best_oa_location,"
                        "open_access,is_retracted"
                    ),
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            for work in payload.get("results") or []:
                if not isinstance(work, Mapping):
                    continue
                if not _work_is_relevant(work, query):
                    continue
                source = _work_to_source(work, query)
                if source is not None:
                    ranked.append((_work_score(work, query), source))
        except Exception as exc:
            message = str(exc).replace(api_key, "[redacted]")
            message = re.sub(r"([?&]api_key=)[^&\s]+", r"\1[redacted]", message, flags=re.IGNORECASE)
            print(f"[gatex.openalex] query failed | reason={type(exc).__name__}: {message[:180]}", flush=True)
    selected = _dedupe_ranked(ranked, maximum)
    print(
        f"[gatex.openalex] completed | queries={len(queries)} | candidates={len(ranked)} | selected={len(selected)}",
        flush=True,
    )
    return selected
