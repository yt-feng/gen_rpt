from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List
from urllib.parse import parse_qs, quote, unquote, urlparse

import fitz
import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 14 * 1024 * 1024


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str


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


@dataclass
class FetchedPage:
    title: str
    url: str
    content: str
    source_type: str
    content_type: str


def search_web(query: str, max_results: int = 5) -> List[SearchResult]:
    results: List[SearchResult] = []
    seen = set()
    for searcher in (_search_duckduckgo, _search_bing):
        try:
            for result in searcher(query, max_results=max_results):
                if not result.url or result.url in seen:
                    continue
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    return results
        except Exception:
            continue
    return results


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


def fetch_page(url: str, max_chars: int = 9000) -> FetchedPage:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8"},
        timeout=30,
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
            content=_extract_pdf_text(content, max_chars=max_chars),
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


def collect_sources(queries: List[str], per_query: int = 3, max_sources: int = 8) -> List[SourceDocument]:
    docs: List[SourceDocument] = []
    seen = set()

    for query in queries:
        try:
            search_results = search_web(query, max_results=per_query)
        except Exception:
            search_results = []
        search_results.extend(_direct_source_candidates(query))

        for result in search_results:
            if result.url in seen:
                continue
            seen.add(result.url)
            try:
                fetched = fetch_page(result.url)
            except Exception:
                fetched = FetchedPage("", result.url, "", "error", "")
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
                )
            )
            if len(docs) >= max_sources:
                return docs

    return docs


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
    if parsed.scheme not in {"http", "https"}:
        return ""
    return url


def _direct_source_candidates(query: str) -> List[SearchResult]:
    lower = str(query or "").lower()
    candidates: List[tuple[str, str, str]] = []
    if any(token in lower for token in ("fusion", "tokamak", "plasma", "tritium", "reactor")):
        candidates.extend(
            [
                ("U.S. DOE Fusion Energy Sciences", "https://science.osti.gov/fes", "Authoritative public source on U.S. fusion research programs."),
                ("DOE Milestone-Based Fusion Development Program", "https://www.energy.gov/science/fusion-energy-sciences/fusion-milestone-based-development-program", "Public program page for private fusion commercialization milestones."),
                ("IAEA Fusion Energy", "https://www.iaea.org/topics/energy/fusion", "International Atomic Energy Agency overview of fusion energy and development status."),
                ("ITER project", "https://www.iter.org/", "International fusion project source for tokamak construction, milestones and technical scope."),
                ("National Academies: Bringing Fusion to the U.S. Grid", "https://www.nationalacademies.org/our-work/bringing-fusion-to-the-us-grid", "Independent study program on fusion commercialization and grid deployment."),
                ("Fusion Industry Association reports", "https://www.fusionindustryassociation.org/reports/", "Industry report archive covering private fusion financing and company progress."),
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
        out.append(SearchResult(title=title, url=url, snippet=snippet, query=query))
    return out


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


def _extract_pdf_text(content: bytes, max_chars: int = 9000, max_pages: int = 12) -> str:
    if not content:
        return ""
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return ""
    parts: List[str] = []
    for page_index in range(min(doc.page_count, max_pages)):
        try:
            text = doc.load_page(page_index).get_text("text")
        except Exception:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            parts.append(text)
        if sum(len(x) for x in parts) >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""
