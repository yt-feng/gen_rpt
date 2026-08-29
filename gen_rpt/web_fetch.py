from __future__ import annotations

import os
import base64
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import parse_qs, quote, unquote, urlparse

import fitz
import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_GDELT_LAST_REQUEST = 0.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str
    provider: str = ""


@dataclass
class SourceDocument:
    title: str
    url: str
    query: str
    snippet: str
    content: str
    source_type: str = "html"
    content_type: str = ""
    domain: str = ""
    confidence: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedPage:
    title: str
    url: str
    content: str
    source_type: str
    content_type: str


_FALLBACK_QUERY_STOPWORDS = {
    "about", "addressable", "analysis", "data", "future", "global", "industry",
    "latest", "linked", "market", "official", "report", "research", "scan",
    "source", "study", "total", "website", "with",
}


def _fallback_result_relevant(query: str, result: SearchResult) -> bool:
    """Apply a conservative lexical floor to noisy HTML-search fallbacks."""

    query_text = re.sub(r"\b(?:site|filetype):\S+", " ", str(query or ""), flags=re.I)
    query_terms = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", query_text.lower())
        if token not in _FALLBACK_QUERY_STOPWORDS
    }
    for run in re.findall(r"[\u3400-\u9fff]+", query_text):
        query_terms.update(run[index:index + 2] for index in range(len(run) - 1))
    if not query_terms:
        return True

    result_text = " ".join((result.title, result.snippet, result.url)).lower()
    result_terms = set(re.findall(r"[a-z][a-z0-9-]{2,}", result_text))
    for run in re.findall(r"[\u3400-\u9fff]+", result_text):
        result_terms.update(run[index:index + 2] for index in range(len(run) - 1))
    overlap = query_terms & result_terms
    if len(overlap) >= (2 if len(query_terms) >= 4 else 1):
        return True

    result_domain = _domain(result.url).split(".", 1)[0]
    return bool(result_domain and result_domain in query_terms)


def search_web(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search the web using the configured provider chain.

    Provider order:
      1. SearXNG  — used when SEARXNG_URL env-var is set (preferred, self-hosted).
      2. DuckDuckGo HTML — fallback when SearXNG is not configured or returns nothing.
      3. Bing HTML — final fallback.

    Each provider is tried in sequence. If the current provider yields enough results
    (>= max_results) the remaining providers are skipped. If a provider raises an
    exception it is logged and skipped without stopping the chain.
    """
    results: List[SearchResult] = []
    seen: set = set()
    site_domains = {
        domain.lower().strip(".")
        for domain in re.findall(
            r"(?:^|\s)site:([a-z0-9.-]+)",
            str(query or ""),
            flags=re.I,
        )
        if domain.strip(".")
    }

    # Build provider chain: Tavily and SearXNG first when configured, then HTML fallbacks.
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    searxng_url = os.getenv("SEARXNG_URL", "").strip()
    searchers = (
        ([_search_tavily] if tavily_key else [])
        + ([_search_searxng] if searxng_url else [])
        + [_search_duckduckgo, _search_bing]
    )

    configured = [name for name, enabled in (("tavily", tavily_key), ("searxng", searxng_url)) if enabled]
    _log(
        "search provider chain | order="
        + ",".join([*configured, "duckduckgo", "bing"])
        + f" | tavily_set={bool(tavily_key)} | searxng_set={bool(searxng_url)}"
    )

    for searcher in searchers:
        provider_name = searcher.__name__.removeprefix("_search_")
        before = len(results)
        try:
            _log(f"search provider attempt | provider={provider_name} | query={query[:120]!r}")
            for result in searcher(query, max_results=max_results):
                if not result.url or result.url in seen:
                    continue
                result_domain = _domain(result.url)
                if site_domains and not any(
                    result_domain == domain or result_domain.endswith(f".{domain}")
                    for domain in site_domains
                ):
                    continue
                if (
                    not site_domains
                    and provider_name in {"duckduckgo", "bing"}
                    and not _fallback_result_relevant(query, result)
                ):
                    continue
                result.provider = result.provider or provider_name
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    _log(f"search provider result | provider={provider_name} | found={len(results) - before} | total={len(results)} | quota_reached=true")
                    return results
        except Exception as exc:
            _log(f"search provider failed | provider={provider_name} | reason={str(exc)[:180]!r}")
            continue
        found = len(results) - before
        _log(f"search provider result | provider={provider_name} | found={found} | total={len(results)}")
        # If SearXNG already found results, skip the HTML-scraping fallbacks
        # (DDG/Bing runner IPs are often blocked; wasting time on them degrades latency).
        if provider_name in {"tavily", "searxng"} and results:
            _log(f"search provider chain | skipping_remaining_fallbacks=true | reason={provider_name}_succeeded")
            return results

    return results


def _search_tavily(query: str, max_results: int = 5) -> List[SearchResult]:
    api_key = os.environ["TAVILY_API_KEY"].strip()
    response = requests.post(
        os.getenv("TAVILY_SEARCH_URL", "https://api.tavily.com/search"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=float(os.getenv("TAVILY_SEARCH_TIMEOUT", "35")),
    )
    response.raise_for_status()
    payload = response.json()
    output: List[SearchResult] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            continue
        output.append(
            SearchResult(
                title=str(item.get("title") or url).strip(),
                url=url,
                snippet=str(item.get("content") or "").strip(),
                query=query,
                provider="tavily",
            )
        )
        if len(output) >= max_results:
            break
    return output


def _search_searxng(query: str, max_results: int = 5) -> List[SearchResult]:
    endpoint = os.environ["SEARXNG_URL"].rstrip("/")
    if not endpoint.endswith("/search"):
        endpoint += "/search"
        
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    api_key = os.getenv("SEARXNG_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
        headers["x-api-key"] = api_key
        
    response = requests.get(
        endpoint,
        params={"q": query, "format": "json", "safesearch": 1},
        headers=headers,
        timeout=float(os.getenv("GEN_RPT_SEARCH_TIMEOUT", "20")),
    )
    response.raise_for_status()
    results: List[SearchResult] = []
    for item in response.json().get("results") or []:
        url = _normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        snippet = BeautifulSoup(str(item.get("content") or ""), "html.parser").get_text(" ", strip=True)
        results.append(
            SearchResult(
                title=str(item.get("title") or url),
                url=url,
                snippet=re.sub(r"\s+", " ", snippet).strip(),
                query=query,
                provider="searxng",
            )
        )
    return results[:max_results]


def _search_duckduckgo(query: str, max_results: int = 5) -> List[SearchResult]:
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results: List[SearchResult] = []
    seen = set()

    for node in soup.select(".result"):
        anchor = node.select_one(".result__title a") or node.select_one("a.result__a") or node.find("a")
        if not anchor:
            continue
        href = anchor.get("href", "").strip()
        title = anchor.get_text(" ", strip=True)
        snippet_node = node.select_one(".result__snippet") or node.select_one(".snippet")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        clean_url = _normalize_url(href)
        if not clean_url or clean_url in seen:
            continue
        seen.add(clean_url)
        results.append(SearchResult(title=title, url=clean_url, snippet=snippet, query=query))
        if len(results) >= max_results:
            break

    return results


def _search_bing(query: str, max_results: int = 5) -> List[SearchResult]:
    url = f"https://www.bing.com/search?q={quote(query)}"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results: List[SearchResult] = []
    seen = set()
    for node in soup.select("li.b_algo"):
        anchor = node.select_one("h2 a") or node.find("a")
        if not anchor:
            continue
        clean_url = _normalize_url(anchor.get("href", "").strip())
        if not clean_url or clean_url in seen:
            continue
        seen.add(clean_url)
        snippet_node = node.select_one(".b_caption p") or node.find("p")
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                url=clean_url,
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
                query=query,
            )
        )
        if len(results) >= max_results:
            break
    return results


def fetch_page(url: str, max_chars: int = 18000, *, query: str = "") -> FetchedPage:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8"},
        timeout=float(os.getenv("GEN_RPT_FETCH_TIMEOUT", "18")),
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    content = _read_limited_content(response)
    final_url = response.url or url

    if _is_pdf(final_url, content_type, content):
        return FetchedPage(
            title="",
            url=final_url,
            content=_extract_pdf_text(content, max_chars=max_chars, query=query),
            source_type="pdf",
            content_type=content_type,
        )

    if "text/html" not in content_type and "xml" not in content_type and b"<html" not in content[:2048].lower():
        return FetchedPage("", final_url, "", "other", content_type)

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    lines = []
    for tag_name in ["h1", "h2", "h3", "p", "li"]:
        for tag in soup.find_all(tag_name):
            text = tag.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            if len(text) >= 40:
                lines.append(text)

    merged = "\n".join(lines)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    merged = merged[:max_chars]
    return FetchedPage(title=title, url=final_url, content=f"{title}\n\n{merged}".strip(), source_type="html", content_type=content_type)


def fetch_page_text(url: str, max_chars: int = 7000) -> str:
    return fetch_page(url, max_chars=max_chars).content


def sources_from_validated_context(payload: Dict[str, Any], query: str) -> List[SourceDocument]:
    """Convert the backend's validated chunks into traceable report sources."""
    document_names = {
        str(reference.get("document_id") or ""): str(reference.get("file_name") or "").strip()
        for reference in payload.get("document_references", []) or []
        if isinstance(reference, dict)
    }
    sources: List[SourceDocument] = []
    seen_chunk_ids = set()
    for chunk in payload.get("validated_chunks", []) or []:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        document_id = str(chunk.get("document_id") or "").strip()
        text = str(chunk.get("text") or "").strip()
        if not chunk_id or not document_id or not text or chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        chunk_metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        file_name = (
            document_names.get(document_id)
            or str(chunk_metadata.get("file_name") or "").strip()
            or str(chunk.get("file_name") or chunk.get("document_title") or "").strip()
            or "Internal Document"
        )
        confidence_value = chunk.get("confidence")
        try:
            confidence = float(confidence_value) if confidence_value is not None else None
        except (TypeError, ValueError):
            confidence = None
        sources.append(
            SourceDocument(
                title=f"{file_name} (Fragment {chunk_id[:8]})",
                url=f"internal://documents/{document_id}#chunk={chunk_id}",
                query=query or "Enterprise Query",
                snippet=text[:300],
                content=text,
                source_type="internal",
                content_type="text/plain",
                domain="internal.enterprise",
                confidence=confidence,
                metadata={
                    **chunk_metadata,
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "file_name": file_name,
                    "authority": chunk.get("authority"),
                    "validation_status": chunk.get("validation_status"),
                    "conflicts_with": chunk.get("conflicts_with") or [],
                },
            )
        )
    return sources


def merge_sources(
    internal_sources: List[SourceDocument], public_sources: List[SourceDocument]
) -> List[SourceDocument]:
    merged: List[SourceDocument] = []
    seen = set()
    for source in [*internal_sources, *public_sources]:
        key = source.url or f"{source.title}\n{source.content[:240]}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged


def build_rag_manifest(
    context_text: str | None,
    rag_sources: List[SourceDocument],
    evidence_ledger: List[Dict[str, Any]],
    *,
    required: bool,
    public_sources: List[SourceDocument] | None = None,
    conflicts: List[Dict[str, Any]] | None = None,
    web_required: bool = False,
    web_query_count: int = 0,
    source_mode: str = "web_only",
) -> Dict[str, Any]:
    chunk_ids = [
        str(source.metadata.get("chunk_id") or "")
        for source in rag_sources
        if source.metadata.get("chunk_id")
    ]
    document_ids = sorted(
        {
            str(source.metadata.get("document_id") or "")
            for source in rag_sources
            if source.metadata.get("document_id")
        }
    )
    internal_urls = {source.url for source in rag_sources}
    evidence_points = sum(
        1 for item in evidence_ledger if str(item.get("source_url") or "") in internal_urls
    )
    rag_evidence_points = sum(
        1
        for item in evidence_ledger
        if item.get("origin") == "rag" or str(item.get("source_url") or "") in internal_urls
    )
    web_evidence_points = sum(1 for item in evidence_ledger if item.get("origin") == "web")
    web_sources = public_sources or []
    web_providers = sorted(
        {
            str(source.metadata.get("search_provider") or "unknown")
            for source in web_sources
        }
    )
    return {
        "status": "active" if rag_sources else "off",
        "required": bool(required),
        "source_mode": source_mode,
        "context_characters": len(context_text or ""),
        "validated_chunk_count": len(chunk_ids),
        "document_count": len(document_ids),
        "chunk_ids": chunk_ids,
        "document_ids": document_ids,
        "internal_evidence_points": evidence_points,
        "rag_source_count": len(rag_sources),
        "web_source_count": len(web_sources),
        "rag_evidence_points": rag_evidence_points,
        "web_evidence_points": web_evidence_points,
        "conflict_count": len(conflicts or []),
        "web_search_status": "success" if web_sources else "no_usable_sources" if rag_sources and web_query_count else "not_run",
        "web_search_required": bool(web_required),
        "web_search_query_count": int(web_query_count),
        "web_search_providers": web_providers,
    }



def collect_sources(queries: List[str], per_query: int = 3, max_sources: int = 8) -> List[SourceDocument]:

    docs: List[SourceDocument] = []
    seen = set()


    query_list = [str(query or "").strip() for query in queries if str(query or "").strip()]
    gdelt_query_limit = int(os.getenv("GEN_RPT_GDELT_QUERIES", "2"))
    _log(f"collect_sources started | queries={len(query_list)} | per_query={per_query} | max_sources={max_sources}")
    for qidx, query in enumerate(query_list, start=1):
        query_start = time.monotonic()
        _log(f"query {qidx}/{len(query_list)} search started | {query[:140]!r}")
        # Curated first-party anchors are deterministic evidence, so evaluate
        # them before noisy search-result pages can fill the per-query quota.
        direct_results = _direct_source_candidates(query)
        try:
            discovered_results = search_web(query, max_results=per_query)
        except Exception as exc:
            _log(f"query {qidx}/{len(query_list)} search failed | reason={str(exc)[:180]!r}")
            discovered_results = []
        search_results = [*direct_results, *discovered_results]
        if qidx <= gdelt_query_limit and not direct_results:
            gdelt_doc = _gdelt_timeline_document(query)
            if gdelt_doc and gdelt_doc.url not in seen and len(docs) < max_sources:
                seen.add(gdelt_doc.url)
                docs.append(gdelt_doc)
                _log(f"source accepted | count={len(docs)}/{max_sources} | domain={gdelt_doc.domain} | type={gdelt_doc.source_type} | reason=gdelt_timeline")
            search_results.extend(_search_gdelt_articles(query, max_results=min(3, per_query)))
        _log(
            f"query {qidx}/{len(query_list)} search completed "
            f"| elapsed={_elapsed(query_start)} | candidates={len(search_results)}"
        )

        for result in search_results:
            if result.url in seen:
                continue
            seen.add(result.url)
            fetch_start = time.monotonic()
            try:
                fetched = fetch_page(result.url, query=result.query)
            except Exception as exc:
                _log(f"fetch failed | domain={_domain(result.url)} | reason={str(exc)[:180]!r}")
                fetched = FetchedPage("", result.url, "", "error", "")
            if len(fetched.content) < 200:
                fallback_content = _snippet_content(result)
                if fallback_content:
                    fetched = FetchedPage(result.title, result.url, fallback_content, "snippet", "text/plain")
            if len(fetched.content) < 200:
                continue
            source_url = fetched.url or result.url
            docs.append(
                SourceDocument(
                    title=result.title or fetched.title,
                    url=source_url,
                    query=result.query,
                    snippet=result.snippet,
                    content=fetched.content,
                    source_type=fetched.source_type,
                    content_type=fetched.content_type,
                    domain=_domain(source_url),
                    metadata={"search_provider": result.provider or "unknown"},
                )
            )
            _log(
                f"source accepted | count={len(docs)}/{max_sources} | domain={docs[-1].domain} "
                f"| type={docs[-1].source_type} | elapsed={_elapsed(fetch_start)}"
            )
            if len(docs) >= max_sources:
                _log(f"collect_sources completed | accepted={len(docs)} | reason=max_sources")
                return docs

    _log(f"collect_sources completed | accepted={len(docs)} | reason=queries_exhausted")
    return docs


def _log(message: str) -> None:
    print(f"[gen_rpt.fetch] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


def _elapsed(start: float) -> str:
    seconds = max(0, int(time.monotonic() - start))
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m{remainder:02d}s"
    return f"{remainder}s"


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = "https://duckduckgo.com" + url
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return _normalize_url(unquote(uddg[0]))
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        encoded = (parse_qs(parsed.query).get("u") or [""])[0]
        if encoded.startswith("a1"):
            payload = encoded[2:]
            try:
                decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                decoded = ""
            if decoded:
                return _normalize_url(decoded)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return url


def _direct_source_candidates(query: str) -> List[SearchResult]:
    lower = str(query or "").lower()
    candidates: List[tuple[str, str, str]] = []
    if any(token in lower for token in ("china", "chinese", "prc")) and any(
        token in lower
        for token in (
            "semiconductor",
            "lithography",
            "etch",
            "deposition",
            "wafer equipment",
        )
    ):
        candidates.extend(
            [
                (
                    "NAURA Technology 2025 Annual Report",
                    "https://static.cninfo.com.cn/finalpage/2026-04-18/1225122918.PDF",
                    "Shenzhen exchange-hosted annual report covering semiconductor deposition, etch, cleaning and related process-equipment operations and research investment.",
                ),
                (
                    "AMEC 2025 Interim Report",
                    "https://star.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-29/688012_20250829_BW75.pdf",
                    "Shanghai STAR Market filing covering etch, thin-film deposition and other semiconductor-equipment operations for the first half of 2025.",
                ),
                (
                    "AMEC Shanghai Stock Exchange Company Profile",
                    "https://star.sse.com.cn/star/en/marketdata/snapshot/c/5542484.shtml",
                    "Official Shanghai Stock Exchange company profile for Advanced Micro-Fabrication Equipment Inc. China.",
                ),
                (
                    "ASML 2025 Annual Report",
                    "https://ourbrand.asml.com/m/71076aaad607de4d/original/asml-2025-annual-report-based-on-us-gaap.pdf",
                    "ASML annual report providing the global reference architecture for EUV, DUV, metrology and inspection systems, including 2025 system shipments and research expenditure.",
                ),
                (
                    "SEMI 2025 Global Semiconductor Equipment Billings",
                    "https://www.semi.org/en/SEMI-Reports-Global-Semiconductor-Equipment-Billings-Reached-135-Billion-in-2025",
                    "SEMI market statistics for 2025 semiconductor-manufacturing equipment billings by region, including China and the global total.",
                ),
                (
                    "SEMI Equipment and Materials Market Outlook",
                    "https://www.semi.org/sites/semi.org/files/2025-09/5%20Clark%20Tseng_Building%20the%20Future-AI%20Investment%2C%20Equipment%20%26%20Materials%20Market%20Outlook.pdf",
                    "SEMI market-outlook presentation covering AI investment, fab equipment and semiconductor-material demand.",
                ),
            ]
        )
    if any(token in lower for token in ("china", "chinese", "prc")) and any(
        token in lower for token in ("mlcc", "multilayer ceramic", "passive component", "capacitor")
    ):
        candidates.extend(
            [
                (
                    "Fenghua Advanced Technology 2025 Interim Report",
                    "https://static.cninfo.com.cn/finalpage/2025-08-22/1224535376.PDF",
                    "Shenzhen exchange-hosted filing covering MLCC and passive-component operations, production, research and financial performance for the first half of 2025.",
                ),
                (
                    "Fenghua Advanced Technology Investor Q&A",
                    "https://static.cninfo.com.cn/finalpage/2025-08-26/1224574201.PDF",
                    "Exchange-hosted investor record describing high-capacitance MLCC development, layer counts and application progress.",
                ),
                (
                    "Fenghua Advanced Technology Clarification Announcement",
                    "https://static.cninfo.com.cn/finalpage/2026-06-30/1225397295.PDF",
                    "Exchange-hosted clarification that constrains unsupported customer-certification claims and quantifies the current contribution from emerging applications.",
                ),
                (
                    "Chaozhou Three-Circle Group Company Profile",
                    "https://static.cninfo.com.cn/finalpage/enpage/300408_2.pdf",
                    "Exchange-hosted English company profile for a Chinese electronic-ceramics and components manufacturer.",
                ),
                (
                    "TDK Integrated Report 2025",
                    "https://www.tdk.com/system/files/integrated_report_pdf_2025_en.pdf",
                    "Global passive-components benchmark covering capacitors, sensors, energy applications and financial performance.",
                ),
                (
                    "Murata Value Report 2025",
                    "https://corporate.murata.com/-/media/corporate/ir/library/murata-value-report/2025_e/murata-value-report-2025-all-for-viewing-e.ashx?cvid=20251023011830000000&la=en",
                    "Murata integrated report providing a global benchmark for ceramic passive components, manufacturing capability and end-market exposure.",
                ),
            ]
        )
    if any(token in lower for token in ("gulf", "gcc", "uae", "saudi", "qatar")) and any(
        token in lower
        for token in (
            "semiconductor",
            "lithography",
            "wafer equipment",
            "mlcc",
            "passive component",
            "capacitor",
            "electronics",
        )
    ):
        candidates.extend(
            [
                (
                    "Saudi National Semiconductor Hub",
                    "https://rdia.gov.sa/en/programs/infrastructure/national-semiconductor-hub-1/",
                    "Official Saudi government program describing the Kingdom's semiconductor design, manufacturing, talent and startup-development objectives.",
                ),
                (
                    "UAE Operation 300Bn Industrial Strategy",
                    "https://www.moiat.gov.ae/en/about-us/about-the-strategy",
                    "Official UAE industrial strategy covering advanced technology, Industry 4.0, local manufacturing and the 2031 industrial-contribution target.",
                ),
                (
                    "Saudi National Industrial Strategy",
                    "https://www.vision2030.gov.sa/media/t0uiiudv/nsd_en.pdf",
                    "Official Saudi industrial strategy covering advanced manufacturing, supply-chain development, industrial infrastructure and international partnerships.",
                ),
            ]
        )
    if any(token in lower for token in ("china", "chinese", "prc")) and any(
        token in lower for token in ("optical", "fibre", "fiber", "connectivity")
    ):
        candidates.extend(
            [
                (
                    "YOFC 2025 Annual Results",
                    "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0327/2026032703026.pdf",
                    "Hong Kong exchange filing for Yangtze Optical Fibre and Cable covering 2025 operations, optical-fibre preforms and related products.",
                ),
                (
                    "YOFC 2025 Sustainability Report",
                    "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0429/2026042903573.pdf",
                    "Exchange-hosted YOFC report documenting 400G, 800G and 1.6T optical transceivers, hollow-core and multi-core fibre, overseas production bases and optical-network applications.",
                ),
                (
                    "YOFC 2024 Sustainability Report",
                    "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0429/2025042904268.pdf",
                    "Exchange-hosted YOFC report covering optical-fibre manufacturing, technical development and international operations.",
                ),
                (
                    "DSBJ 2025 Annual Report",
                    "https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-05-23/dfb085f2-b654-4e3e-a4df-2d53fb360ea4.PDF",
                    "Shenzhen exchange-hosted annual report covering high-speed optical-module product architecture, market estimates and manufacturing investment.",
                ),
                (
                    "Linktel Technologies Hong Kong Listing Application",
                    "https://www1.hkexnews.hk/app/sehk/2026/108694/documents/sehk26062902260.pdf",
                    "Hong Kong exchange-hosted listing application with company-specific 800G-and-above optical-transceiver capacity, shipments and financial disclosure.",
                ),
            ]
        )
    if any(token in lower for token in ("gulf", "gcc", "uae", "saudi", "qatar")) and any(
        token in lower for token in ("optical", "fibre", "fiber", "connectivity", "data centre", "data center")
    ):
        candidates.extend(
            [
                (
                    "Ooredoo launches 100 Gbps SDN connectivity in Qatar",
                    "https://www.ooredoo.qa/web/en/press-release/ooredoo-launches-sdn-connect-the-future-of-high-speed-connectivity-in-qatar/",
                    "Ooredoo Qatar operator release documenting 1, 10, 40 and 100 Gbps service tiers, data-centre interconnect and service availability.",
                ),
                (
                    "Ooredoo forms an international fibre and submarine-cable infrastructure entity",
                    "https://www.ooredoo.com/en/media/news_view/ooredoo-announces-formation-of-new-international-connectivity-infrastructure-entity-and-appoints-khalid-hassan-al-hamadi-as-ceo/",
                    "Ooredoo Group release documenting a dedicated vehicle for high-capacity terrestrial fibre and submarine-cable investment.",
                ),
                (
                    "Ooredoo and DE-CIX launch Doha IX",
                    "https://www.ooredoo.qa/web/en/press-release/ooredoo-launches-doha-ix-qatars-first-commercial-internet-exchange-point-in-partnership-with-de-cix/",
                    "Operator release on Qatar's commercial internet exchange, data-centre hosting and regional low-latency interconnection.",
                ),
                (
                    "Saudi Internet Report 2025",
                    "https://www.cst.gov.sa/en/knowledge-center/reports/saudi-internet-report-25-dashboard",
                    "Saudi regulator statistics covering internet traffic, fixed and mobile network performance, infrastructure and AI-tool adoption.",
                ),
                (
                    "center3 Reference Offer",
                    "https://mutasilbus.cst.gov.sa/regulations/attachments/DownloadFile/858ksZZAzEhcSJ.LLKKFMyZc0mNfRNHJ2t.JLJVK0g.rsEUrj3XtzWnQemjomqQNKbCNhS0SU7wqXqz4Y1gP0uWKLhtaz9v6KwlPcSkGy00-",
                    "Saudi regulatory-hosted reference offer covering DWDM, internet exchange points, data centres and high-capacity connectivity services.",
                ),
            ]
        )
    if any(token in lower for token in ("fusion", "tokamak", "plasma", "tritium", "reactor")):
        candidates.extend(
            [
                ("U.S. DOE Fusion Energy", "https://www.energy.gov/fusion/fusion-energy", "Authoritative public source on U.S. fusion strategy, roadmap and public-private fusion programs."),
                ("U.S. DOE Fusion Energy Sciences", "https://science.osti.gov/fes", "Authoritative public source on U.S. fusion research programs."),
                ("DOE Fusion Innovation Research Engine selectees", "https://www.energy.gov/articles/us-department-energy-announces-selectees-107-million-fusion-innovation-research-engine", "DOE source on fusion innovation funding, milestone program authorization and selected public-private projects."),
                ("IAEA Fusion Energy", "https://www.iaea.org/topics/energy/fusion", "International Atomic Energy Agency source describing fusion's long-term low-carbon energy potential, technical status and international coordination."),
                ("ITER project", "https://www.iter.org/", "International fusion project source for tokamak construction, the experimental step between research machines and future power plants."),
                ("National Academies: Bringing Fusion to the U.S. Grid", "https://www.nationalacademies.org/projects/DEPS-BPA-20-03/publication/25991", "Independent study program on the scientific, engineering, regulatory and market issues required to bring fusion-generated electricity to the U.S. grid."),
                ("National Academies fusion pilot plant strategy", "https://www.nationalacademies.org/read/25991/chapter/7", "National Academies chapter on strategy and roadmap for a U.S. fusion pilot plant."),
                ("Fusion Industry Association reports", "https://www.fusionindustryassociation.org/fusion-industry-reports/", "Industry report archive covering private fusion financing, company progress and commercialization milestones."),
                ("FIA 2024 Global Fusion Industry Report launch", "https://www.fusionindustryassociation.org/fia-launches-2024-global-fusion-industry-report/", "Fusion Industry Association release with investment, company-count and government funding metrics."),
                ("FIA 2025 global fusion industry coverage", "https://www.fusionindustryassociation.org/in-the-news-the-global-fusion-industry-in-2025/", "Fusion Industry Association coverage summary with 2025 investment momentum and cited media reactions."),
                ("Lawrence Livermore fusion ignition", "https://www.llnl.gov/article/49306/llnl-achieves-fusion-ignition", "National-lab source on fusion ignition, a key scientific milestone that raised investor and policy attention."),
                ("ARPA-E BETHE fusion program", "https://arpa-e.energy.gov/programs-and-initiatives/view-all-programs/bethe", "U.S. innovation-program source focused on enabling commercially viable fusion energy technology paths."),
                ("DOE Fusion Science and Technology Roadmap", "https://www.energy.gov/articles/energy-department-announces-fusion-science-and-technology-roadmap-accelerate-commercial", "DOE source on the Fusion Science and Technology Roadmap and mid-2030s commercialization ambition."),
                ("DOE FY 2027 Fusion Energy Sciences budget request", "https://www.energy.gov/documents/fy-2027-fusion-energy-sciences-budget-request", "DOE budget request source with public-private fusion program funding and infrastructure priorities."),
                ("NRC fusion energy regulation", "https://www.nrc.gov/materials/fusion-energy.html", "U.S. Nuclear Regulatory Commission source on the regulatory treatment and licensing path for fusion energy systems."),
                ("UK STEP fusion programme", "https://step.ukaea.uk/", "UK public fusion programme source for prototype plant objectives, timeline and commercialization milestones."),
                ("EUROfusion roadmap", "https://euro-fusion.org/eurofusion/roadmap/", "European fusion roadmap source covering scientific and engineering steps toward grid electricity from fusion."),
                ("Commonwealth Fusion Systems SPARC", "https://cfs.energy/technology/sparc/", "Company project page for SPARC, a high-field compact tokamak demonstration path."),
                ("Helion Polaris", "https://www.helionenergy.com/polaris/", "Company project page for Polaris, Helion's planned electricity-demonstration fusion machine."),
                ("Zap Energy how it works", "https://legacy.zapenergy.com/how-it-works", "Company technology page for sheared-flow-stabilized Z-pinch fusion development."),
                ("TAE Technologies fusion power", "https://tae.com/fusion-power/", "Company technology page for field-reversed configuration fusion development."),
                ("General Fusion demonstration program", "https://generalfusion.com/technology/", "Company technology page for magnetized target fusion development."),
            ]
        )
    if any(token in lower for token in ("battery", "storage", "grid", "hydrogen", "power")):
        candidates.extend(
            [
                ("IEA energy storage", "https://www.iea.org/energy-system/electricity/grid-scale-storage", "International Energy Agency source on grid-scale storage."),
                ("U.S. DOE Office of Electricity", "https://www.energy.gov/oe/office-electricity", "Public source on grid modernization and storage programs."),
            ]
        )
    out = []
    for title, url, snippet in candidates:
        out.append(SearchResult(title=title, url=url, snippet=snippet, query=query, provider="direct"))
    return out


def _search_gdelt_articles(query: str, max_results: int = 3) -> List[SearchResult]:
    gdelt_query = _gdelt_query(query)
    if not gdelt_query:
        return []
    data = _gdelt_request(
        {
            "query": gdelt_query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max(1, min(10, max_results))),
            "timespan": os.getenv("GEN_RPT_GDELT_TIMESPAN", "36months"),
            "sort": "HybridRel",
        }
    )
    articles = data.get("articles") if isinstance(data, dict) else []
    if not isinstance(articles, list):
        return []
    out: List[SearchResult] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        title = str(article.get("title") or article.get("seendate") or "").strip()
        if not url or not title:
            continue
        seendate = str(article.get("seendate") or "").strip()
        domain = str(article.get("domain") or _domain(url)).strip()
        language = str(article.get("language") or "").strip()
        country = str(article.get("sourcecountry") or "").strip()
        snippet = " | ".join(
            part
            for part in [
                "GDELT DOC 2.0 article result",
                f"seen {seendate}" if seendate else "",
                domain,
                language,
                country,
            ]
            if part
        )
        out.append(SearchResult(title=title, url=url, snippet=snippet, query=f"GDELT: {gdelt_query}", provider="gdelt"))
    return out[:max_results]


def _gdelt_timeline_document(query: str) -> SourceDocument | None:
    gdelt_query = _gdelt_query(query)
    if not gdelt_query:
        return None
    params = {
        "query": gdelt_query,
        "mode": "TimelineVolRaw",
        "format": "json",
        "timespan": os.getenv("GEN_RPT_GDELT_TIMESPAN", "36months"),
    }
    data = _gdelt_request(params)
    points = _gdelt_timeline_points(data)
    if len(points) < 3:
        return None
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + "&".join(f"{key}={quote(str(value))}" for key, value in params.items())
    top_points = sorted(points, key=lambda item: item[1], reverse=True)[:6]
    recent_points = points[-12:]
    total_articles = int(sum(value for _date, value in points))
    lines = [
        f"GDELT DOC 2.0 TimelineVolRaw returned {total_articles} articles for query '{gdelt_query}' across {len(points)} observed periods.",
    ]
    for date_label, value in recent_points:
        lines.append(f"In {date_label}, GDELT DOC 2.0 returned {int(value)} articles for query '{gdelt_query}'.")
    for date_label, value in top_points:
        lines.append(f"The highest observed GDELT coverage point was {int(value)} articles in {date_label} for query '{gdelt_query}'.")
    return SourceDocument(
        title=f"GDELT news coverage timeline: {gdelt_query}",
        url=url,
        query=f"GDELT TimelineVolRaw: {gdelt_query}",
        snippet=f"Raw GDELT news-volume timeline for {gdelt_query}.",
        content="\n".join(lines),
        source_type="gdelt_timeline",
        content_type="application/json",
        domain="api.gdeltproject.org",
        metadata={"search_provider": "gdelt"},
    )


def _gdelt_request(params: dict[str, str]) -> dict:
    global _GDELT_LAST_REQUEST
    min_interval = float(os.getenv("GEN_RPT_GDELT_MIN_INTERVAL", "5.5"))
    elapsed = time.monotonic() - _GDELT_LAST_REQUEST
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _GDELT_LAST_REQUEST = time.monotonic()
    try:
        response = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=float(os.getenv("GEN_RPT_GDELT_TIMEOUT", "14")),
        )
        if response.status_code == 429:
            _log("gdelt request rate-limited | " + str(params.get("mode") or ""))
            return {}
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        _log(f"gdelt request failed | mode={params.get('mode')} | reason={str(exc)[:180]!r}")
        return {}


def _gdelt_query(query: str) -> str:
    stopwords = {
        "and",
        "or",
        "the",
        "a",
        "an",
        "for",
        "with",
        "from",
        "data",
        "report",
        "official",
        "market",
        "size",
    }
    terms = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", str(query or "")):
        lower = token.lower()
        if lower in stopwords or lower in terms:
            continue
        terms.append(lower)
        if len(terms) >= 7:
            break
    return " ".join(terms)


def _gdelt_timeline_points(data: dict) -> List[tuple[str, float]]:
    timeline = data.get("timeline") if isinstance(data, dict) else None
    if not isinstance(timeline, list):
        return []
    points: List[tuple[str, float]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        date_value = str(item.get("date") or item.get("datetime") or item.get("timestamp") or "").strip()
        value = item.get("value")
        if value is None and isinstance(item.get("series"), list) and item["series"]:
            first = item["series"][0]
            if isinstance(first, dict):
                value = first.get("value") or first.get("count")
        if value is None:
            value = item.get("count") or item.get("articles")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        label = _gdelt_date_label(date_value)
        if label and numeric >= 0:
            points.append((label, numeric))
    return points


def _gdelt_date_label(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) >= 6:
        return f"{digits[:4]}-{digits[4:6]}"
    if len(digits) >= 4:
        return digits[:4]
    return value[:10]


def _snippet_content(result: SearchResult) -> str:
    title = re.sub(r"\s+", " ", str(result.title or "")).strip()
    snippet = re.sub(r"\s+", " ", str(result.snippet or "")).strip()
    if len(snippet) < 40:
        return ""
    query = re.sub(r"\s+", " ", str(result.query or "")).strip()
    parts = [
        title,
        snippet,
        f"Source URL retained for public-source review: {result.url}",
    ]
    if query:
        parts.append(f"Search context: {query}")
    parts.append(
        "The page body could not be fully extracted, so this source should be treated as a lower-confidence "
        "public signal unless another fetched source confirms the same claim."
    )
    return "\n\n".join(part for part in parts if part)


def _read_limited_content(response: requests.Response, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    chunks: List[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _is_pdf(url: str, content_type: str, content: bytes) -> bool:
    clean_url = url.lower().split("?", 1)[0]
    return clean_url.endswith(".pdf") or "pdf" in content_type.lower() or content.startswith(b"%PDF")


def _pdf_query_terms(query: str) -> List[str]:
    stop = {
        "and", "company", "filetype", "for", "from", "official", "report",
        "the", "with", "2024", "2025", "2026",
    }
    terms = [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", query or "")
        if token.lower() not in stop
    ]
    return list(dict.fromkeys(terms))[:18]


def _pdf_page_relevance(text: str, query_terms: List[str]) -> float:
    lower = text.lower()
    score = sum(min(4, lower.count(term)) for term in query_terms)
    evidence_phrases = (
        "annual results", "business overview", "capacity utilisation", "capacity utilization",
        "designed capacity", "market share", "operating income", "production capacity",
        "research and development", "revenue", "sales volume", "shipment", "sustainability",
    )
    score += sum(4 for phrase in evidence_phrases if phrase in lower)
    score += min(8, len(re.findall(r"\b\d[\d,.]*\s*(?:%|gbps|mbps|mw|gw|million|billion|units?)\b", lower)))
    boilerplate = (
        "take no responsibility for the contents", "application proof is in draft form",
        "should obtain independent professional advice", "table of contents",
    )
    score -= sum(8 for phrase in boilerplate if phrase in lower)
    return float(score)


def _extract_pdf_text(content: bytes, max_chars: int = 18000, max_pages: int = 24, query: str = "") -> str:
    if not content:
        return ""
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return ""
    query_terms = _pdf_query_terms(query)
    ranked: List[tuple[float, int, str]] = []
    identity_pages: List[tuple[int, str]] = []
    scan_limit = min(doc.page_count, int(os.getenv("GEN_RPT_PDF_SCAN_PAGES", "650")))
    for page_index in range(scan_limit):
        try:
            text = doc.load_page(page_index).get_text("text")
        except Exception:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if page_index == 0:
            identity_pages.append((page_index, text[:800]))
        ranked.append((_pdf_page_relevance(text, query_terms), page_index, text))

    # Long filings often place operating data hundreds of pages after the cover.
    # Keep a compact identity excerpt, then put the most query-relevant evidence
    # pages first so downstream prompts do not receive pages of legal boilerplate.
    selected: List[tuple[int, str]] = list(identity_pages)
    selected_indexes = {index for index, _ in selected}
    for _, page_index, text in sorted(ranked, key=lambda row: (-row[0], row[1])):
        if page_index in selected_indexes:
            continue
        selected.append((page_index, text))
        selected_indexes.add(page_index)
        if len(selected) >= max_pages:
            break
    parts: List[str] = []
    used = 0
    for page_index, text in selected:
        remaining = max_chars - used
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        parts.append(f"[PDF page {page_index + 1}] {excerpt}")
        used += len(excerpt) + 20
    return "\n".join(parts)[:max_chars]


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""
