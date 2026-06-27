from __future__ import annotations

import os
import re
import time
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
_GDELT_LAST_REQUEST = 0.0


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

    query_list = [str(query or "").strip() for query in queries if str(query or "").strip()]
    gdelt_query_limit = int(os.getenv("GEN_RPT_GDELT_QUERIES", "2"))
    _log(f"collect_sources started | queries={len(query_list)} | per_query={per_query} | max_sources={max_sources}")
    for qidx, query in enumerate(query_list, start=1):
        query_start = time.monotonic()
        _log(f"query {qidx}/{len(query_list)} search started | {query[:140]!r}")
        try:
            search_results = search_web(query, max_results=per_query)
        except Exception as exc:
            _log(f"query {qidx}/{len(query_list)} search failed | reason={str(exc)[:180]!r}")
            search_results = []
        search_results.extend(_direct_source_candidates(query))
        if qidx <= gdelt_query_limit:
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
                fetched = fetch_page(result.url)
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
    if parsed.scheme not in {"http", "https"}:
        return ""
    return url


def _direct_source_candidates(query: str) -> List[SearchResult]:
    lower = str(query or "").lower()
    candidates: List[tuple[str, str, str]] = []
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
        out.append(SearchResult(title=title, url=url, snippet=snippet, query=query))
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
        out.append(SearchResult(title=title, url=url, snippet=snippet, query=f"GDELT: {gdelt_query}"))
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
