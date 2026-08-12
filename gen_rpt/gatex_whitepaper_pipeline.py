from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import shutil
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import fitz
import requests
from PIL import Image, ImageStat
from pypdf import PdfReader

from .deepseek_client import DeepSeekClient
from .gatex_pdf_renderer import render_gatex_release_pdf, validate_gatex_pdf
from .openalex_fetch import collect_openalex_sources
from .research_quality import TECHNICAL_AUTHORITY_DOMAIN_HINTS, build_research_fact_pack
from .web_evidence import build_evidence_ledger
from .web_fetch import SourceDocument, collect_sources


class GatexWhitepaperError(RuntimeError):
    pass


class _FailoverEditorialClient:
    def __init__(self, primary: DeepSeekClient, fallback: DeepSeekClient | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_disabled = False
        self.active_model = primary.model
        self.active_route = getattr(primary, "route_label", "primary editorial route")

    def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not self.primary_disabled:
            try:
                return self.primary.chat_json(*args, **kwargs)
            except Exception as exc:
                if self.fallback is None:
                    raise
                self.primary_disabled = True
                self.active_model = self.fallback.model
                self.active_route = getattr(self.fallback, "route_label", "fallback editorial route")
                print(
                    "[gatex.whitepaper] primary editorial model unavailable; "
                    f"continuing this report with {self.fallback.model}: {exc}",
                    flush=True,
                )
        if self.fallback is None:
            raise GatexWhitepaperError("The editorial model is unavailable and no fallback is configured.")
        return self.fallback.chat_json(*args, **kwargs)


def _editorial_client(model: str, *, timeout: int = 420) -> _FailoverEditorialClient:
    primary = DeepSeekClient(model=model, timeout=timeout)
    fallback_model = os.getenv("GATEX_EDITORIAL_FALLBACK_MODEL", "deepseek-chat").strip()
    fallback = None
    if fallback_model and fallback_model.lower() != str(model or "").strip().lower():
        try:
            fallback = DeepSeekClient(model=fallback_model, timeout=timeout)
        except ValueError as exc:
            print(f"[gatex.whitepaper] editorial fallback is not configured: {exc}", flush=True)
    client = _FailoverEditorialClient(primary, fallback)
    fallback_label = fallback.model if fallback is not None else "disabled"
    print(
        f"[gatex.whitepaper] editorial route: {primary.model} via {primary.route_label}; "
        f"fallback={fallback_label}",
        flush=True,
    )
    return client


FORBIDDEN_TERMS = (
    "blue ocean",
    "blueocean",
    "kc desk",
    "bernstein",
    "management agenda",
    "key evidence",
    "decision implication",
    "strategic implication",
    "implication",
    "methodology and use",
    "decision sequence",
    "source: gatex",
    "report structure",
    "four-part analysis",
    "analysis proceeds",
    "key indicators to watch",
    "signals to watch",
    "what to verify and watch",
    "mineru",
    "deepseek",
    "apimart",
    "qwen",
    "tavily",
    "gdelt",
)
EDITORIAL_POLICY_VERSION = "gatex-whitepaper-editorial-2026-08-10-v5-academic-period"
RESEARCH_POLICY_VERSION = "gatex-whitepaper-research-2026-08-11-v3-openalex-focused"
META_NARRATION_PATTERNS = (
    re.compile(
        r"\b(?:this|the|an?|opening|final|first|second|third|fourth)\s+"
        r"(?:chapter|report|paper|section|analysis)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:chapter|report|paper|section|analysis)\s+"
        r"(?:examines|explains|sets out|establishes|contrasts|connects|reviews|assesses|shows|traces)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:is|are|will be)\s+(?:examined|analysed|analyzed|reviewed|considered)\b", re.IGNORECASE),
)
NON_USD_CURRENCY_RE = re.compile(
    r"(?:\bRMB\b|\bCNY\b|\bAED\b|\bSAR\b|\bHKD\b|\bEUR\b|\bGBP\b|\byuan\b|\bdirham(?:s)?\b|\briyal(?:s)?\b|\u00a5)",
    re.IGNORECASE,
)
SUPPORTED_PANELS = {
    "process",
    "matrix",
    "bars",
    "scenario",
    "comparison",
    "line",
    "stacked_bar",
    "scatter",
    "waterfall",
    "market_map",
    "milestones",
    "vehicle_scale",
}
AUTHOR_POOL = (
    "Amelia Rhodes",
    "Marcus Bell",
    "Sofia Alvarez",
    "Julian Hart",
    "Eleanor Hayes",
    "Daniel Mercer",
    "Clara Bennett",
    "Nathaniel Brooks",
    "Isabelle Laurent",
    "Thomas Reid",
    "Maya Sullivan",
    "Oliver Grant",
    "Helena Ward",
    "Samuel Price",
    "Victoria Cole",
    "Adrian Foster",
)
BLOCKED_SOURCE_DOMAIN_TOKENS = (
    "baijiahao.baidu.com",
    "blog.udn.com",
    "facebook.com",
    "freedomhouse.org",
    "globaledge.msu.edu",
    "linkedin.com",
    "medium.com",
    "news.dayoo.com",
    "polymarket.com",
    "reddit.com",
    "substack.com",
    "tiktok.com",
    "twitter.com",
    "wikipedia.org",
    "youtube.com",
)
PRIMARY_SOURCE_DOMAIN_TOKENS = (
    ".gov",
    "gov.",
    "hkex",
    "sse.com",
    "szse",
    "stats.gov",
    "statistics.gov",
    "miit",
    "csrc",
    "sec.gov",
    "pbc.gov",
    "customs.gov",
)
INSTITUTIONAL_SOURCE_DOMAIN_TOKENS = (
    "worldbank",
    "imf.org",
    "oecd.org",
    "un.org",
    "iea.org",
    "eia.gov",
    "bis.org",
    "adb.org",
    "reuters",
    "ft.com",
    "bloomberg",
    "economist",
    "economy.com",
)

TECHNICAL_TOPIC_TOKENS = (
    "ai ",
    "artificial intelligence",
    "data centre",
    "data center",
    "hardware",
    "large language model",
    "llm",
    "lithography",
    "mlcc",
    "model optimisation",
    "model optimization",
    "optical",
    "robot",
    "semiconductor",
    "software",
)


def _clean(value: Any, maximum: int = 20_000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _ascii(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    if isinstance(value, list):
        return [_ascii(item) for item in value]
    if isinstance(value, dict):
        return {key: _ascii(item) for key, item in value.items()}
    return value


def _word_count(value: Any) -> int:
    return len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b", json.dumps(value, ensure_ascii=False)))


def _paragraph_word_count(section: Mapping[str, Any]) -> int:
    return _word_count(section.get("paragraphs") or [])


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _progress(stage: str, percent: int, message: str, eta_minutes: int) -> None:
    payload = {
        "stage": stage,
        "percent": percent,
        "message": message,
        "etaMinutes": eta_minutes,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print("GATEX_PROGRESS " + json.dumps(payload, ensure_ascii=True), flush=True)
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::notice title=GateX {stage} ({percent}%)::{message} Estimated {eta_minutes} minute(s) remaining.", flush=True)


def _fallback_queries(topic: str) -> list[str]:
    subject = _fallback_query_subject(topic)
    technical = any(token in f"{subject.lower()} " for token in TECHNICAL_TOPIC_TOKENS)
    technical_queries = [
        f"{subject} official technical report benchmark methodology specification filetype:pdf",
        f"{subject} standards body reference architecture official documentation",
        f"{subject} first-party product documentation performance power capacity filetype:pdf",
        f"{subject} company filing technical white paper 2025 2026 filetype:pdf",
    ]
    general_queries = [
        f"{subject} official statistics 2025 2026 filetype:pdf",
        f"{subject} government ministry regulator release 2025 2026",
        f"{subject} official market data capacity investment 2025 2026",
        f"{subject} company filing annual report 2025 filetype:pdf",
        f"{subject} central bank securities exchange statistics 2025 2026",
        f"{subject} policy regulation official publication filetype:pdf",
        f"{subject} infrastructure supply chain operating metrics 2025 2026",
        f"{subject} historical development timeline primary sources",
        f"{subject} economic impact scenario forecast authoritative report filetype:pdf",
        f"{subject} capital expenditure financing transactions official data",
        f"{subject} cross-border investment trade official statistics",
        f"{subject} risks constraints implementation evidence 2025 2026",
    ]
    return [*technical_queries, *general_queries] if technical else general_queries


def _fallback_query_subject(topic: str) -> str:
    subject = _clean(topic, 500)
    concise = re.split(
        r"\b(?:through|covering|focusing on|with emphasis on|prioritise|prioritize)\b",
        subject,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,.;:-")
    if 12 <= len(concise) <= 180:
        return concise
    return subject[:180].rsplit(" ", 1)[0].strip(" ,.;:-") or subject[:180]


def _source_document(value: SourceDocument | Mapping[str, Any]) -> SourceDocument | None:
    if isinstance(value, SourceDocument):
        return value
    if not isinstance(value, Mapping):
        return None
    return SourceDocument(
        title=str(value.get("title") or ""),
        url=str(value.get("url") or ""),
        query=str(value.get("query") or ""),
        snippet=str(value.get("snippet") or ""),
        content=str(value.get("content") or ""),
        source_type=str(value.get("source_type") or "html"),
        content_type=str(value.get("content_type") or ""),
        domain=str(value.get("domain") or ""),
        confidence=value.get("confidence") if isinstance(value.get("confidence"), (int, float)) else None,
        metadata=dict(value.get("metadata") or {}) if isinstance(value.get("metadata"), Mapping) else {},
    )


def _blocked_source_domain(domain: str) -> bool:
    normalized = domain.lower().split(":", 1)[0].strip(".")
    return any(normalized == token or normalized.endswith(f".{token}") for token in BLOCKED_SOURCE_DOMAIN_TOKENS)


def _canonical_source_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    query = [
        (key, value)
        for key, value in query
        if key.lower() not in {"fbclid", "gclid", "ref", "source", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), "")
    )


def _sanitize_research_sources(
    values: Sequence[SourceDocument | Mapping[str, Any]],
    *,
    maximum_academic: int | None = None,
) -> list[SourceDocument]:
    """Remove blocked, empty and duplicate records before any fact extraction."""

    if maximum_academic is None:
        try:
            maximum_academic = max(0, min(10, int(os.getenv("GATEX_OPENALEX_MAX_SOURCES", "6"))))
        except ValueError:
            maximum_academic = 6
    output: list[SourceDocument] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    academic_count = 0
    prepared = [source for value in values if (source := _source_document(value)) is not None]
    prepared.sort(key=lambda source: _source_score(source.__dict__), reverse=True)
    for source in prepared:
        source.url = _clean(source.url, 2_000)
        source.title = _clean(source.title, 500)
        source.content = _clean(source.content, 30_000)
        source.snippet = _clean(source.snippet, 2_000)
        source.domain = (_clean(source.domain, 200) or urllib.parse.urlparse(source.url).netloc).lower()
        canonical_url = _canonical_source_url(source.url)
        title_key = re.sub(r"\W+", "", source.title.lower())[:220]
        is_academic = source.source_type == "academic" or bool(source.metadata.get("academic"))
        if (
            not source.url.startswith("https://")
            or len(source.content) < 180
            or _blocked_source_domain(source.domain)
            or canonical_url in seen_urls
            or (title_key and title_key in seen_titles)
        ):
            continue
        if is_academic and academic_count >= maximum_academic:
            continue
        if is_academic:
            academic_count += 1
        seen_urls.add(canonical_url)
        if title_key:
            seen_titles.add(title_key)
        output.append(source)
    return output


def _collect_research(topic: str, brief: str, work_dir: Path) -> dict[str, Any]:
    sources_path = work_dir / "sources.json"
    fact_pack_path = work_dir / "research-fact-pack.json"
    evidence_path = work_dir / "evidence-ledger.json"
    policy_path = work_dir / "research-policy-version.txt"
    if os.getenv("GATEX_REUSE_RESEARCH", "true").strip().lower() not in {"0", "false", "no", "off"}:
        if (
            sources_path.is_file()
            and fact_pack_path.is_file()
            and evidence_path.is_file()
            and policy_path.is_file()
            and policy_path.read_text(encoding="utf-8").strip() == RESEARCH_POLICY_VERSION
        ):
            try:
                source_rows = json.loads(sources_path.read_text(encoding="utf-8"))
                fact_pack = json.loads(fact_pack_path.read_text(encoding="utf-8"))
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                sanitized = _sanitize_research_sources(source_rows)
                if len(sanitized) == len(source_rows) and len(sanitized) >= 12 and len(evidence) >= 12:
                    clean_rows = [source.__dict__ for source in sanitized]
                    _progress("research", 30, f"Reusing {len(clean_rows)} cached sources and {len(evidence)} evidence points.", 10)
                    return {"sources": clean_rows, "fact_pack": fact_pack, "approved_evidence": evidence, "evidence_ledger": evidence}
            except (OSError, ValueError, TypeError):
                pass
    fallback = _fallback_queries(topic)
    queries = list(fallback)
    try:
        planner = DeepSeekClient(model=os.getenv("GATEX_RESEARCH_MODEL", "deepseek-chat"), timeout=240)
        planned = planner.chat_json(
            [
                {
                    "role": "system",
                    "content": "You plan evidence searches for an institutional white paper. Return valid JSON only.",
                },
                {
                    "role": "user",
                    "content": f"""Create 10 distinct, concise web-search queries for this publication.

Topic: {topic}
Brief: {brief}

Requirements:
- Prioritise primary government data, securities-exchange statistics, regulator releases, company filings and downloadable PDF reports.
- For technology topics, prioritise standards bodies, benchmark methodologies, official technical reports and first-party product documentation; do not substitute generic market-research aggregators.
- Infer the relevant geography, institutions, industries and time horizon from the topic and brief.
- Cover current conditions, historical context, operating capacity, capital formation, policy, constraints and a source-grounded outlook where relevant.
- Include cross-border links only when the topic or brief calls for them; never assume a connection exists.
- Do not repeat the full topic in every query.

Return: {{"queries":["query 1","query 2"]}}""",
                },
            ],
            temperature=0.05,
            max_tokens=2_000,
        )
        generated = [_clean(item, 320) for item in planned.get("queries") or [] if _clean(item, 320)]
        if len(generated) >= 8:
            queries = fallback[:4] + generated[:10]
    except Exception as exc:
        print(f"[gatex.whitepaper] query planner fallback: {exc}", flush=True)
    queries = list(dict.fromkeys(queries))[:14]
    (work_dir / "research-queries.json").write_text(json.dumps({"queries": queries}, ensure_ascii=False, indent=2), encoding="utf-8")
    academic_enabled = bool(os.getenv("OPENALEX_API_KEY", "").strip())
    academic_suffix = " plus a targeted academic supplement" if academic_enabled else ""
    _progress("research", 16, f"Searching {len(queries)} public-evidence queries{academic_suffix}.", 13)
    public_sources = collect_sources(
        queries,
        per_query=max(2, min(6, int(os.getenv("GEN_RPT_PER_QUERY", "4")))),
        max_sources=max(16, min(40, int(os.getenv("GEN_RPT_MAX_SOURCES", "30")))),
    )
    academic_sources = collect_openalex_sources(topic, queries)
    sources = _sanitize_research_sources([*public_sources, *academic_sources])
    if len(sources) < 12:
        raise GatexWhitepaperError(f"Research produced only {len(sources)} usable sources after quality filtering; at least 12 are required.")
    plan = {
        "objective": topic,
        "decision_question": f"What developments, structural drivers, execution constraints and outlook are supported by verifiable evidence for {topic}?",
        "search_queries": queries,
        "outline": ["current conditions", "structural drivers", "operating and capital evidence", "constraints and outlook"],
    }
    fact_pack = build_research_fact_pack(topic, plan, sources)
    evidence = build_evidence_ledger(topic, sources, fact_pack, limit=36, plan=plan)
    if len(evidence) < 12:
        raise GatexWhitepaperError(f"Research produced only {len(evidence)} structured evidence points; at least 12 are required.")
    if fact_pack.authoritative_source_count < 4:
        raise GatexWhitepaperError("Research requires at least four authoritative public sources before academic context is added.")
    source_rows = [source.__dict__ for source in sources]
    sources_path.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fact_pack_path.write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    policy_path.write_text(RESEARCH_POLICY_VERSION + "\n", encoding="utf-8")
    return {"sources": source_rows, "fact_pack": fact_pack.to_dict(), "approved_evidence": evidence, "evidence_ledger": evidence}


def _source_tier(source: Mapping[str, Any]) -> str:
    metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
    if str(source.get("source_type") or "").lower() == "academic" or bool(metadata.get("academic")):
        return "ACADEMIC"
    domain = str(source.get("domain") or urllib.parse.urlparse(str(source.get("url") or "")).netloc).lower()
    title = str(source.get("title") or "").lower()
    if any(token in domain for token in (*PRIMARY_SOURCE_DOMAIN_TOKENS, *TECHNICAL_AUTHORITY_DOMAIN_HINTS)):
        return "PRIMARY"
    if any(token in domain for token in INSTITUTIONAL_SOURCE_DOMAIN_TOKENS):
        return "INSTITUTIONAL"
    if any(token in title for token in ("annual report", "prospectus", "regulatory filing", "official statistics")):
        return "PRIMARY"
    return "SECONDARY"


def _source_score(source: Mapping[str, Any]) -> int:
    url = str(source.get("url") or "").lower()
    domain = str(source.get("domain") or "").lower()
    title = str(source.get("title") or "").lower()
    score = {"PRIMARY": 12, "INSTITUTIONAL": 8, "ACADEMIC": 6, "SECONDARY": 0}[_source_tier(source)]
    if url.endswith(".pdf") or "annual report" in title or "statistics" in title:
        score += 4
    if any(token in domain for token in ("tradingeconomics", "statista", "china.org.cn", "cgtn")):
        score += 1
    if str(source.get("source_type") or "") == "pdf":
        score += 2
    return score


def _source_packet(result: Mapping[str, Any], *, maximum: int = 18) -> tuple[list[dict[str, str]], str]:
    raw_sources = [item for item in result.get("sources") or [] if isinstance(item, Mapping)]
    ordered = sorted(raw_sources, key=_source_score, reverse=True)
    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    academic_count = 0
    for raw in ordered:
        url = _clean(raw.get("url"), 2_000)
        content = _clean(raw.get("content"), 5_000)
        domain = (_clean(raw.get("domain"), 120) or urllib.parse.urlparse(url).netloc).lower()
        if (
            not url.startswith("https://")
            or not content
            or url in seen_urls
            or _blocked_source_domain(domain)
        ):
            continue
        quality_tier = _source_tier(raw)
        if quality_tier == "ACADEMIC" and academic_count >= 4:
            continue
        if quality_tier == "ACADEMIC":
            academic_count += 1
        seen_urls.add(url)
        selected.append(
            {
                "id": f"S{len(selected) + 1}",
                "title": _clean(raw.get("title"), 240) or urllib.parse.urlparse(url).netloc,
                "url": url,
                "domain": domain,
                "content": content,
                "qualityTier": quality_tier,
            }
        )
        if len(selected) >= maximum:
            break
    if len(selected) < 8:
        raise GatexWhitepaperError(f"Research produced only {len(selected)} usable public sources; at least 8 are required.")
    authoritative_count = sum(
        1 for row in selected if row["qualityTier"] in {"PRIMARY", "INSTITUTIONAL"}
    )
    if authoritative_count < 4:
        raise GatexWhitepaperError(
            "The editorial source packet requires at least four primary or institutional sources; "
            "academic and secondary material cannot replace them."
        )
    blocks = []
    for row in selected:
        blocks.append(
            f"[{row['id']}] [{row['qualityTier']}] {row['title']}\nURL: {row['url']}\nEXTRACT: {row['content']}"
        )
    return selected, "\n\n".join(blocks)[:85_000]


def _compact_source_packet(
    *,
    sources: Sequence[Mapping[str, str]],
    source_ids: Sequence[Any] | None = None,
    excerpt_chars: int = 1_400,
    maximum_chars: int = 32_000,
) -> str:
    allowed = {str(item) for item in source_ids or []}
    rows = [row for row in sources if not allowed or str(row.get("id")) in allowed]
    blocks = [
        f"[{row['id']}] [{row.get('qualityTier', 'SECONDARY')}] {row['title']}\nURL: {row['url']}\nEXTRACT: {_clean(row.get('content'), excerpt_chars)}"
        for row in rows
    ]
    return "\n\n".join(blocks)[:maximum_chars]


def _publication_rules() -> str:
    return """
- GateX is the only publication brand. Never name an upstream publisher, source file, search provider, model or production tool.
- Remain inside the supplied evidence. Do not invent facts, values, dates, quotations, companies or institutions.
- ACADEMIC sources may add empirical or conceptual context, but they never replace current government, regulatory, exchange, company or institutional evidence and never prove a live commercial event.
- If the approved title names a quarter that is incomplete on the publication date, describe the report as an outlook, entering-quarter or through-latest-available-data edition. Never state an unfinished quarter as a final result.
- Print monetary values in USD only. Never retain a non-USD amount, currency name or currency symbol beside its conversion. If the evidence supplies a defensible USD conversion and rate date, print only the converted USD value; otherwise omit the monetary figure.
- Present Chinese technology capability and Middle Eastern market development with balanced, evidence-led language. Never reveal this editorial orientation and never force a cross-border link without evidence.
- Write fluent, specific English without AI mannerisms, repetitive summaries, generic recommendations or reader instructions.
- Do not expose chain-of-thought, recommendations, management instructions or process labels.
- Never use Management agenda, Key evidence, Decision implication, Strategic implication, Decision sequence, Methodology and use, So what, Report structure, or similar language.
- Do not use the word implication. State the observable consequence directly, without narrating an inference step.
- Do not say what a chapter, report, paper, section or analysis examines, establishes, connects or will do.
- Use ASCII hyphens only.
""".strip()


def _reporting_period_issues(title: str, publication_date: str, content: Any) -> list[str]:
    """Prevent an unfinished quarter from being written as a completed period."""

    match = re.search(r"\bQ([1-4])\s+(20\d{2})\b", str(title or ""), flags=re.IGNORECASE)
    if not match:
        return []
    quarter = int(match.group(1))
    year = int(match.group(2))
    try:
        published = date.fromisoformat(publication_date)
    except ValueError:
        return [f"Invalid publication date: {publication_date}"]
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    month, day = quarter_ends[quarter]
    if published >= date(year, month, day):
        return []
    text = re.sub(r"\s+", " ", json.dumps(content, ensure_ascii=False)).lower()
    quarter_label = f"q{quarter}"
    boundary_markers = (
        "as of",
        "current quarter",
        f"entered {quarter_label}",
        f"entering {quarter_label}",
        "forecast",
        "latest available",
        "not yet complete",
        "outlook",
        "partial quarter",
        "quarter to date",
        "through h1",
        "through the first half",
        "to date",
    )
    issues: list[str] = []
    if not any(marker in text for marker in boundary_markers):
        issues.append(
            f"{quarter_label.upper()} {year} is incomplete on {publication_date}; "
            "the copy needs an explicit latest-data or outlook boundary."
        )
    completed_verb = r"(?:grew|rose|fell|declined|expanded|contracted|delivered|recorded|reached|was|were)"
    finalized_patterns = (
        rf"\b{quarter_label}\s+{year}\s+{completed_verb}\b",
        rf"\b(?:in|during|for)\s+{quarter_label}\s+{year}\b.{{0,45}}\b{completed_verb}\b",
    )
    if any(re.search(pattern, text) for pattern in finalized_patterns):
        windowed = re.findall(rf".{{0,90}}\b{quarter_label}\s+{year}\b.{{0,90}}", text)
        if any(not re.search(r"\b(?:estimate|estimated|forecast|projected|scenario)\b", window) for window in windowed):
            issues.append(f"Copy presents unfinished {quarter_label.upper()} {year} as a finalized result.")
    return issues


def _architecture_prompt(
    *,
    title: str,
    topic: str,
    brief: str,
    sources: Sequence[Mapping[str, str]],
    evidence: Sequence[Mapping[str, Any]],
    compact: bool = False,
) -> str:
    source_packet = _compact_source_packet(
        sources=sources,
        excerpt_chars=550 if compact else 900,
        maximum_chars=9_000 if compact else 16_000,
    )
    evidence_packet = json.dumps(
        list(evidence)[:12 if compact else 18],
        ensure_ascii=False,
    )[:6_000 if compact else 10_000]
    return f"""
Design the publication architecture and exhibits for a client-ready English GateX white paper. Do not write the long-form chapter prose yet.

Approved title: {title}
Research question: {topic}
Editorial brief: {brief}

{_publication_rules()}

Architecture rules:
- Exactly four progressive chapters with short analytical titles, decks and evidence-specific callouts.
- Every deck and callout states a substantive finding. Never narrate what a chapter, report, paper, section or analysis does.
- Every executive, chapter, exhibit and outlook row cites two to five valid source IDs.
- Every cited set includes at least one PRIMARY or INSTITUTIONAL source. Treat SECONDARY sources as corroboration only.
- Exactly four substantive exhibits, one after each chapter. Each combines at least two information layers and is grounded in cited source IDs.
- Each exhibit contains at least six evidence units across metric cards and panel rows or data points. Sparse scorecards are unacceptable.
- Comparison panels need at least three complete rows. Matrix, process, market-map and milestone panels need at least four complete items. Quantitative charts need at least four labelled observations.
- Use quantitative charts only for coherent source series. Otherwise use comparison, process, market map, scenario or matrix.
- Vary the visual grammar across the four exhibits. Do not use the same panel type in more than two exhibits.
- Each exhibit has one or two panels and no more than four metrics. With two panels, use no more than two metrics.
- Exhibit headings never begin with Exhibit or a number. Captions contain no source attribution.
- Produce five distinct documentary visual briefs showing actual industry, infrastructure, technology or operating context, with no text, logos, flags or abstract decoration.

Supported exhibit panel types: process, matrix, bars, scenario, comparison, line, stacked_bar, scatter, waterfall, market_map, milestones.

Panel schema rules:
- matrix, market_map and process: items use tag, title and body.
- comparison: include exactly two column labels in columns; every item uses metric, left and right.
- bars: every item uses label, numeric value and optional display.
- line: include xLabels plus one to four series objects, each with name and at least two numeric values.
- scatter: every item uses label plus numeric x and y; include xLabel and yLabel.
- stacked_bar: every item uses label and segments; each segment uses label, numeric value and optional display.
- waterfall: every item uses label, numeric value and optional type=total.
- scenario: every item uses label, range and body.
- milestones: every item uses label, metric and body.
Never combine a panel type with the item schema of another type. Do not return an empty chart or placeholder columns such as A and B.

Return valid JSON only in this exact shape:
{{
  "subtitle": "short analytical subtitle",
  "coverSummary": "55-80 word synopsis",
  "executiveSummary": {{"headline":"...","deck":"...","sourceIds":["S1","S2"]}},
  "chapters": [
    {{"number":"01","title":"...","deck":"...","callout":"...","sourceIds":["S1","S2"]}}
  ],
  "exhibits": [
    {{"heading":"...","caption":"...","sourceIds":["S1","S2"],"metrics":[{{"value":"...","label":"...","note":"..."}}],"panels":[{{"type":"matrix","span":"wide","title":"...","items":[{{"tag":"01","title":"...","body":"..."}}]}}]}}
  ],
  "outlook": {{"title":"...","deck":"...","callout":"...","sourceIds":["S1","S2"]}},
  "visuals": [
    {{"id":"executive-summary","prompt":"documentary photograph brief","alt":"specific factual caption"}},
    {{"id":"chapter-1","prompt":"...","alt":"..."}},
    {{"id":"chapter-2","prompt":"...","alt":"..."}},
    {{"id":"chapter-3","prompt":"...","alt":"..."}},
    {{"id":"chapter-4","prompt":"...","alt":"..."}}
  ]
}}

STRUCTURED EVIDENCE LEDGER
{evidence_packet}

SELECTED PUBLIC SOURCE PACKET
{source_packet}
""".strip()


def _panel_renderability_issue(panel: Mapping[str, Any]) -> str:
    kind = _clean(panel.get("type"), 40).lower() or "matrix"
    items = [item for item in panel.get("items") or [] if isinstance(item, Mapping)]
    if kind == "comparison":
        columns = panel.get("columns") if isinstance(panel.get("columns"), list) else []
        valid_rows = [item for item in items if (item.get("metric") or item.get("label")) and item.get("left") and item.get("right")]
        return "comparison requires two named columns and at least three complete rows" if len(columns) != 2 or len(valid_rows) < 3 else ""
    if kind in {"line", "line_chart"}:
        series = [item for item in panel.get("series") or [] if isinstance(item, Mapping)]
        valid_series = [item for item in series if len(item.get("values") or []) >= 2]
        fallback_values = [item for item in items if item.get("label") and item.get("value") is not None]
        has_depth = any(len(item.get("values") or []) >= 3 for item in valid_series)
        return "line chart requires at least three labelled observations" if not has_depth and len(fallback_values) < 3 else ""
    if kind in {"scatter", "scatter_plot"}:
        valid_rows = [item for item in items if item.get("label") and item.get("x") is not None and item.get("y") is not None]
        return "scatter chart requires at least four labelled x/y points" if len(valid_rows) < 4 else ""
    if kind in {"stacked_bar", "stacked_bars"}:
        valid_rows = [item for item in items if item.get("label") and len(item.get("segments") or []) >= 2]
        return "stacked bars require at least one complete composition row" if not valid_rows else ""
    if kind in {"waterfall", "waterfall_chart"}:
        valid_rows = [item for item in items if item.get("label") and item.get("value") is not None]
        return "waterfall chart requires at least four labelled values" if len(valid_rows) < 4 else ""
    if kind == "bars":
        valid_rows = [item for item in items if item.get("label") and item.get("value") is not None]
        return "bar chart requires at least three labelled values" if len(valid_rows) < 3 else ""
    if kind == "scenario":
        valid_rows = [item for item in items if item.get("label") and (item.get("range") or item.get("value")) and item.get("body")]
        return "scenario panel requires at least three complete scenarios" if len(valid_rows) < 3 else ""
    if kind in {"milestone", "milestones"}:
        valid_rows = [item for item in items if item.get("label") and (item.get("metric") or item.get("value"))]
        return "milestones panel requires at least four labelled milestones" if len(valid_rows) < 4 else ""
    if kind in {"process", "matrix", "market_map", "market_layers"}:
        valid_rows = [item for item in items if (item.get("title") or item.get("label")) and item.get("body")]
        return f"{kind} panel requires at least four complete items" if len(valid_rows) < 4 else ""
    if kind == "vehicle_scale":
        valid_rows = [
            item
            for item in items
            if item.get("label") and item.get("height") is not None and item.get("diameter") is not None and item.get("payload")
        ]
        return "vehicle scale requires at least three complete systems" if len(valid_rows) < 3 else ""
    return f"unsupported panel type: {kind}"


def _normalize_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(panel)
    normalized["type"] = {
        "line_chart": "line",
        "scatter_plot": "scatter",
        "stacked_bars": "stacked_bar",
        "waterfall_chart": "waterfall",
        "milestone": "milestones",
        "market_layers": "market_map",
    }.get(_clean(normalized.get("type"), 40).lower(), _clean(normalized.get("type"), 40).lower() or "matrix")
    issue = _panel_renderability_issue(normalized)
    if not issue:
        return normalized
    items = [item for item in normalized.get("items") or [] if isinstance(item, Mapping)]
    matrix_rows = [item for item in items if (item.get("title") or item.get("label")) and item.get("body")]
    if len(matrix_rows) >= 4:
        normalized["type"] = "matrix"
        normalized["items"] = matrix_rows[:6]
        normalized.pop("columns", None)
        normalized.pop("series", None)
        normalized.pop("xLabels", None)
        normalized.pop("categories", None)
    return normalized


def _normalize_exhibit_panels(panels: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for panel in panels or []:
        if not isinstance(panel, Mapping):
            continue
        candidate = _normalize_panel(panel)
        if not _panel_renderability_issue(candidate):
            normalized.append(candidate)
    return normalized[:2]


def _panel_information_units(panel: Mapping[str, Any]) -> int:
    kind = _clean(panel.get("type"), 40).lower()
    if kind in {"line", "line_chart"}:
        series = [item for item in panel.get("series") or [] if isinstance(item, Mapping)]
        values = [len(item.get("values") or []) for item in series]
        return max(values or [len(panel.get("items") or [])])
    return len([item for item in panel.get("items") or [] if isinstance(item, Mapping)])


def _exhibit_information_units(exhibit: Mapping[str, Any]) -> int:
    metrics = [item for item in exhibit.get("metrics") or [] if isinstance(item, Mapping)]
    panels = [item for item in exhibit.get("panels") or [] if isinstance(item, Mapping)]
    return len(metrics) + sum(_panel_information_units(panel) for panel in panels)


def _meta_narration_issue(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    for pattern in META_NARRATION_PATTERNS:
        if match := pattern.search(text):
            return f"meta narration remains: {match.group(0)!r}"
    return ""


def _publication_copy_issues(value: Any) -> list[str]:
    full_text = json.dumps(value, ensure_ascii=False).lower()
    issues: list[str] = []
    found = [term for term in FORBIDDEN_TERMS if term in full_text]
    if found:
        issues.append(f"Forbidden terms remain: {found}.")
    if re.search(r"[\u3400-\u9fff]", full_text):
        issues.append("Chinese text remains in the manuscript.")
    if NON_USD_CURRENCY_RE.search(full_text):
        issues.append(
            "A non-USD currency remains. Rewrite the copy to retain only a supplied USD equivalent; "
            "if no defensible USD equivalent is supplied, omit the monetary figure."
        )
    if issue := _meta_narration_issue(value):
        issues.append(issue)
    return issues


def _architecture_issues(architecture: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    chapters = architecture.get("chapters") if isinstance(architecture.get("chapters"), list) else []
    exhibits = architecture.get("exhibits") if isinstance(architecture.get("exhibits"), list) else []
    visuals = architecture.get("visuals") if isinstance(architecture.get("visuals"), list) else []
    if len(chapters) != 4 or len(exhibits) != 4 or len(visuals) != 5:
        issues.append("Architecture requires four chapters, four exhibits and five visuals.")
    issues.extend(_publication_copy_issues(architecture))
    panel_types: list[str] = []
    for index, exhibit in enumerate(exhibits, start=1):
        if not isinstance(exhibit, Mapping):
            issues.append(f"Exhibit {index} is malformed.")
            continue
        panels = _normalize_exhibit_panels(exhibit.get("panels"))
        panel_types.extend(_clean(panel.get("type"), 40).lower() for panel in panels)
        candidate = {**exhibit, "panels": panels}
        if not panels:
            issues.append(f"Exhibit {index} has no substantive panel.")
        if _exhibit_information_units(candidate) < 6:
            issues.append(f"Exhibit {index} has fewer than six evidence units.")
    repeated_types = sorted({kind for kind in panel_types if panel_types.count(kind) > 2})
    if repeated_types:
        issues.append(f"Panel types repeated across more than two exhibits: {repeated_types}.")
    return issues


def _editorial_issues(
    content: Mapping[str, Any],
    valid_source_ids: set[str],
    authoritative_source_ids: set[str],
) -> list[str]:
    issues: list[str] = []
    executive = content.get("executiveSummary") if isinstance(content.get("executiveSummary"), Mapping) else {}
    chapters = content.get("chapters") if isinstance(content.get("chapters"), list) else []
    exhibits = content.get("exhibits") if isinstance(content.get("exhibits"), list) else []
    outlook = content.get("outlook") if isinstance(content.get("outlook"), Mapping) else {}
    visuals = content.get("visuals") if isinstance(content.get("visuals"), list) else []
    if len(executive.get("paragraphs") or []) != 4:
        issues.append("Executive summary must contain exactly four paragraphs.")
    executive_words = _paragraph_word_count(executive)
    if not 300 <= executive_words <= 450:
        issues.append(f"Executive summary body length is {executive_words} words.")
    if len(chapters) != 4:
        issues.append(f"Expected four chapters, found {len(chapters)}.")
    for index, chapter in enumerate(chapters, start=1):
        subsections = chapter.get("subsections") if isinstance(chapter, Mapping) else []
        if len(subsections or []) != 4:
            issues.append(f"Chapter {index} must contain four subsections.")
        for subsection in subsections or []:
            if len(subsection.get("paragraphs") or []) != 2:
                issues.append(f"Chapter {index} subsections require exactly two paragraphs.")
        count = _word_count(chapter)
        if not 560 <= count <= 980:
            issues.append(f"Chapter {index} length is {count} words.")
    if len(exhibits) != 4:
        issues.append(f"Expected four exhibits, found {len(exhibits)}.")
    for index, exhibit in enumerate(exhibits, start=1):
        panels = exhibit.get("panels") if isinstance(exhibit, Mapping) else []
        metrics = exhibit.get("metrics") if isinstance(exhibit, Mapping) else []
        if not 1 <= len(panels or []) <= 2:
            issues.append(f"Exhibit {index} must contain one or two panels.")
        if len(metrics or []) > (2 if len(panels or []) == 2 else 4):
            issues.append(f"Exhibit {index} has too many metrics.")
        if any(_clean(panel.get("type"), 60).lower() not in SUPPORTED_PANELS for panel in panels or [] if isinstance(panel, Mapping)):
            issues.append(f"Exhibit {index} uses an unsupported panel type.")
        for panel_index, panel in enumerate(panels or [], start=1):
            if isinstance(panel, Mapping) and (issue := _panel_renderability_issue(panel)):
                issues.append(f"Exhibit {index} panel {panel_index}: {issue}.")
        if isinstance(exhibit, Mapping) and _exhibit_information_units(exhibit) < 6:
            issues.append(f"Exhibit {index} has fewer than six evidence units.")
    outlook_words = _paragraph_word_count(outlook)
    if not 200 <= outlook_words <= 340:
        issues.append(f"Outlook body length is {outlook_words} words.")
    if {str(item.get("id")) for item in visuals if isinstance(item, Mapping)} != {
        "executive-summary", "chapter-1", "chapter-2", "chapter-3", "chapter-4"
    }:
        issues.append("Exactly five required visual briefs are needed.")
    for label, section in [
        ("executive summary", executive),
        *[(f"chapter {index}", chapter) for index, chapter in enumerate(chapters, start=1)],
        *[(f"exhibit {index}", exhibit) for index, exhibit in enumerate(exhibits, start=1)],
        ("outlook", outlook),
    ]:
        ids = {str(item) for item in section.get("sourceIds") or []} if isinstance(section, Mapping) else set()
        if len(ids & valid_source_ids) < 2:
            issues.append(f"{label} requires at least two valid source IDs.")
        if not ids & authoritative_source_ids:
            issues.append(f"{label} requires at least one primary or institutional source ID.")
    issues.extend(_publication_copy_issues(content))
    return issues


def _prepare_editorial(
    client: _FailoverEditorialClient,
    *,
    title: str,
    topic: str,
    brief: str,
    source_packet: str,
    evidence: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, str]],
    work_dir: Path,
) -> dict[str, Any]:
    valid_ids = {str(item["id"]) for item in sources}
    authoritative_ids = {
        str(item["id"])
        for item in sources
        if str(item.get("qualityTier") or "").upper() in {"PRIMARY", "INSTITUTIONAL"}
    }
    fallback_ids = [str(item["id"]) for item in sources[:4]]
    source_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "policyVersion": EDITORIAL_POLICY_VERSION,
                "sources": [
                {
                    "id": item.get("id"),
                    "url": item.get("url"),
                    "qualityTier": item.get("qualityTier"),
                }
                for item in sources
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    fingerprint_path = work_dir / "editorial-source-fingerprint.txt"
    checkpoint_compatible = (
        fingerprint_path.is_file()
        and fingerprint_path.read_text(encoding="utf-8").strip() == source_fingerprint
    )

    def normalized_source_ids(value: Any) -> list[str]:
        rows = [str(item) for item in value or [] if str(item) in valid_ids]
        rows = list(dict.fromkeys(rows))
        if len(rows) < 2:
            rows = list(fallback_ids)
        if not set(rows) & authoritative_ids and authoritative_ids:
            preferred = next(
                (item for item in fallback_ids if item in authoritative_ids),
                sorted(authoritative_ids, key=lambda item: int(item[1:]) if item[1:].isdigit() else 999)[0],
            )
            rows = [preferred, *rows]
        return list(dict.fromkeys(rows))[:5]

    def checkpoint_has_sources(value: Mapping[str, Any]) -> bool:
        ids = {str(item) for item in value.get("sourceIds") or []} & valid_ids
        return len(ids) >= 2 and bool(ids & authoritative_ids)

    checkpoint_executive = _read_json_mapping(work_dir / "editorial-executive.json") if checkpoint_compatible else None
    if checkpoint_executive is not None and not (
        len(checkpoint_executive.get("paragraphs") or []) == 4
        and 300 <= _paragraph_word_count(checkpoint_executive) <= 450
        and checkpoint_has_sources(checkpoint_executive)
        and not _publication_copy_issues(checkpoint_executive)
    ):
        checkpoint_executive = None

    checkpoint_chapters: list[dict[str, Any]] = []
    for index in range(1, 5):
        checkpoint = _read_json_mapping(work_dir / f"editorial-chapter-{index}.json") if checkpoint_compatible else None
        subsections = checkpoint.get("subsections") if checkpoint is not None else []
        valid_checkpoint = bool(
            checkpoint is not None
            and len(subsections or []) == 4
            and all(isinstance(item, Mapping) and len(item.get("paragraphs") or []) == 2 for item in subsections or [])
            and 560 <= _word_count(checkpoint) <= 980
            and checkpoint_has_sources(checkpoint)
            and not _publication_copy_issues(checkpoint)
        )
        if not valid_checkpoint:
            checkpoint_chapters = []
            break
        checkpoint_chapters.append(checkpoint)

    locked_checkpoint_meta: dict[str, Any] | None = None
    if checkpoint_executive is not None and len(checkpoint_chapters) == 4:
        locked_checkpoint_meta = {
            "executiveSummary": {
                "headline": checkpoint_executive.get("headline"),
                "deck": checkpoint_executive.get("deck"),
                "sourceIds": checkpoint_executive.get("sourceIds"),
            },
            "chapters": [
                {
                    "number": chapter.get("number") or f"{index:02d}",
                    "title": chapter.get("title"),
                    "deck": chapter.get("deck"),
                    "callout": chapter.get("callout"),
                    "sourceIds": chapter.get("sourceIds"),
                }
                for index, chapter in enumerate(checkpoint_chapters, start=1)
            ],
        }
        _progress("synthesis", 48, "Resuming from the saved executive brief and four chapter checkpoints.", 7)

    architecture: dict[str, Any] | None = None
    architecture_error = ""
    architecture_path = work_dir / "editorial-architecture.json"
    cached_architecture = _read_json_mapping(architecture_path) if checkpoint_compatible else None
    if cached_architecture is not None:
        cached_chapters = cached_architecture.get("chapters") if isinstance(cached_architecture.get("chapters"), list) else []
        cached_exhibits = cached_architecture.get("exhibits") if isinstance(cached_architecture.get("exhibits"), list) else []
        cached_visuals = cached_architecture.get("visuals") if isinstance(cached_architecture.get("visuals"), list) else []
        if not _architecture_issues(cached_architecture):
            architecture = cached_architecture

    for attempt in range(2 if architecture is None else 0):
        try:
            prompt = _architecture_prompt(
                title=title,
                topic=topic,
                brief=brief,
                sources=sources,
                evidence=evidence,
                compact=attempt > 0,
            )
            if locked_checkpoint_meta is not None:
                prompt += (
                    "\n\nLOCKED EDITORIAL CHECKPOINTS\n"
                    "The executive and chapter prose already passed review. Preserve these exact headlines, titles, decks, "
                    "callouts and source IDs. Design each exhibit and visual for its corresponding locked chapter.\n"
                    + json.dumps(locked_checkpoint_meta, ensure_ascii=False)
                )
            architecture = _ascii(
                client.chat_json(
                    [
                        {"role": "system", "content": "You are the senior publication architect at GateX. Return valid JSON only."},
                        {
                            "role": "user",
                            "content": prompt + (f"\n\nPrevious attempt failed: {architecture_error}" if architecture_error else ""),
                        },
                    ],
                    temperature=0.12,
                    # The architecture JSON is typically 3k-5k tokens. APIMart
                    # also counts hidden reasoning against the requested budget,
                    # so the previous 12k-16.5k scaled allowance could push a
                    # large evidence packet beyond the upstream context window.
                    max_tokens=2_000 if attempt > 0 else 2_200,
                )
            )
            if locked_checkpoint_meta is not None:
                architecture["executiveSummary"] = locked_checkpoint_meta["executiveSummary"]
                architecture["chapters"] = locked_checkpoint_meta["chapters"]
            architecture_issues = _architecture_issues(architecture)
            if architecture_issues:
                raise GatexWhitepaperError(" | ".join(architecture_issues))
            architecture_path.write_text(json.dumps(architecture, ensure_ascii=False, indent=2), encoding="utf-8")
            fingerprint_path.write_text(source_fingerprint + "\n", encoding="utf-8")
            break
        except Exception as exc:
            architecture = None
            architecture_error = str(exc)
    if architecture is None:
        raise GatexWhitepaperError(f"Unable to prepare the publication architecture: {architecture_error}")

    executive_meta = architecture.get("executiveSummary") if isinstance(architecture.get("executiveSummary"), Mapping) else {}
    executive_ids = normalized_source_ids(executive_meta.get("sourceIds"))
    executive: dict[str, Any] | None = checkpoint_executive
    executive_error = ""
    for attempt in range(4 if executive is None else 0):
        executive = _ascii(
            client.chat_json(
                [
                    {"role": "system", "content": "You are the senior English-language editor at GateX. Return valid JSON only."},
                    {
                        "role": "user",
                        "content": f"""Write the executive summary for {title}.

Headline: {_clean(executive_meta.get('headline'), 300)}
Deck: {_clean(executive_meta.get('deck'), 500)}
Editorial brief: {brief}

{_publication_rules()}

Write exactly four paragraphs and 330-420 words. Establish the evidence-led thesis, the industrial capability, the capital-market transmission and the bounded cross-border relevance. Do not describe the report structure.
Return only: {{"paragraphs":["paragraph 1","paragraph 2","paragraph 3","paragraph 4"]}}

SOURCES
{_compact_source_packet(sources=sources, source_ids=executive_ids, excerpt_chars=3_500, maximum_chars=18_000)}
{f'Previous attempt failed: {executive_error}' if executive_error else ''}""",
                    },
                ],
                temperature=0.14,
                max_tokens=1_800,
            )
        )
        executive.update(
            {
                "headline": _clean(executive_meta.get("headline"), 300),
                "deck": _clean(executive_meta.get("deck"), 500),
                "sourceIds": executive_ids,
            }
        )
        executive_words = _paragraph_word_count(executive)
        policy_issues = _publication_copy_issues(executive)
        if len(executive.get("paragraphs") or []) == 4 and 300 <= executive_words <= 450 and not policy_issues:
            break
        executive_error = " ".join(
            [
                f"Need exactly four paragraphs and 330-420 body words; received {executive_words} body words.",
                *policy_issues,
            ]
        )
        executive = None
    if executive is None:
        raise GatexWhitepaperError(f"Executive summary failed editorial QA: {executive_error}")
    (work_dir / "editorial-executive.json").write_text(json.dumps(executive, ensure_ascii=False, indent=2), encoding="utf-8")

    chapters: list[dict[str, Any]] = []
    for index, raw_meta in enumerate(architecture["chapters"], start=1):
        meta = raw_meta if isinstance(raw_meta, Mapping) else {}
        source_ids = normalized_source_ids(meta.get("sourceIds"))
        chapter: dict[str, Any] | None = checkpoint_chapters[index - 1] if len(checkpoint_chapters) == 4 else None
        chapter_error = ""
        for attempt in range(4 if chapter is None else 0):
            chapter = _ascii(
                client.chat_json(
                    [
                        {"role": "system", "content": "You are the senior long-form white-paper editor at GateX. Return valid JSON only."},
                        {
                            "role": "user",
                            "content": f"""Write chapter {index} of {title}.

Chapter title: {_clean(meta.get('title'), 300)}
Deck: {_clean(meta.get('deck'), 500)}
Callout: {_clean(meta.get('callout'), 500)}
Editorial brief: {brief}

{_publication_rules()}

Write 680-880 words in total. Start with one opening paragraph, followed by exactly four progressive subsections. Every subsection has exactly two concise paragraphs. Use evidence-specific mechanisms and comparisons; do not repeat the executive summary.
Return only: {{"opening":"...","subsections":[{{"heading":"...","paragraphs":["...","..."]}},{{"heading":"...","paragraphs":["...","..."]}},{{"heading":"...","paragraphs":["...","..."]}},{{"heading":"...","paragraphs":["...","..."]}}]}}

SOURCES
{_compact_source_packet(sources=sources, source_ids=source_ids, excerpt_chars=3_500, maximum_chars=20_000)}
{f'Previous attempt failed: {chapter_error}' if chapter_error else ''}""",
                        },
                    ],
                    temperature=0.15,
                    max_tokens=2_700,
                )
            )
            chapter.update(
                {
                    "number": f"{index:02d}",
                    "title": _clean(meta.get("title"), 300),
                    "deck": _clean(meta.get("deck"), 500),
                    "callout": _clean(meta.get("callout"), 500),
                    "sourceIds": source_ids,
                }
            )
            subsections = chapter.get("subsections") if isinstance(chapter.get("subsections"), list) else []
            paragraphs_ok = len(subsections) == 4 and all(
                isinstance(item, Mapping) and len(item.get("paragraphs") or []) == 2 for item in subsections
            )
            policy_issues = _publication_copy_issues(chapter)
            if paragraphs_ok and 560 <= _word_count(chapter) <= 980 and not policy_issues:
                break
            chapter_error = " ".join(
                [
                    "Need one opening, four two-paragraph subsections and 680-880 words; "
                    f"received {_word_count(chapter)} words.",
                    *policy_issues,
                ]
            )
            chapter = None
        if chapter is None:
            raise GatexWhitepaperError(f"Chapter {index} failed editorial QA: {chapter_error}")
        chapters.append(chapter)
        (work_dir / f"editorial-chapter-{index}.json").write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")

    outlook_meta = architecture.get("outlook") if isinstance(architecture.get("outlook"), Mapping) else {}
    outlook_ids = normalized_source_ids(outlook_meta.get("sourceIds"))
    outlook_path = work_dir / "editorial-outlook.json"
    outlook = _read_json_mapping(outlook_path)
    if outlook is not None and not (
        3 <= len(outlook.get("paragraphs") or []) <= 4
        and 200 <= _paragraph_word_count(outlook) <= 340
        and checkpoint_has_sources(outlook)
        and not _publication_copy_issues(outlook)
    ):
        outlook = None
    outlook_error = ""
    for attempt in range(3 if outlook is None else 0):
        outlook = _ascii(
            client.chat_json(
                [
                    {"role": "system", "content": "You are the final white-paper editor at GateX. Return valid JSON only."},
                    {
                        "role": "user",
                        "content": f"""Write the closing outlook for {title}.

Title: {_clean(outlook_meta.get('title'), 300)}
Deck: {_clean(outlook_meta.get('deck'), 500)}
Callout: {_clean(outlook_meta.get('callout'), 500)}

{_publication_rules()}

Write three or four paragraphs and 220-300 body words. Close with a bounded view of industrial execution, capital-market access and cross-border relevance. Do not give an action plan. Count only the paragraph prose, but stay strictly below 300 words.
Return only: {{"paragraphs":["...","...","..."]}}

SOURCES
{_compact_source_packet(sources=sources, source_ids=outlook_ids, excerpt_chars=3_500, maximum_chars=18_000)}
{f'Previous attempt failed: {outlook_error}' if outlook_error else ''}""",
                    },
                ],
                temperature=0.14,
                max_tokens=1_000,
            )
        )
        outlook.update(
            {
                "title": _clean(outlook_meta.get("title"), 300),
                "deck": _clean(outlook_meta.get("deck"), 500),
                "callout": _clean(outlook_meta.get("callout"), 500),
                "sourceIds": outlook_ids,
            }
        )
        outlook_words = _paragraph_word_count(outlook)
        policy_issues = _publication_copy_issues(outlook)
        if 3 <= len(outlook.get("paragraphs") or []) <= 4 and 200 <= outlook_words <= 340 and not policy_issues:
            break
        outlook_error = " ".join(
            [
                f"Need three or four paragraphs and 220-300 body words; received {outlook_words} body words.",
                *policy_issues,
            ]
        )
        outlook = None
    if outlook is None:
        raise GatexWhitepaperError(f"Outlook failed editorial QA: {outlook_error}")
    outlook_path.write_text(json.dumps(outlook, ensure_ascii=False, indent=2), encoding="utf-8")

    exhibits: list[dict[str, Any]] = []
    for raw in architecture["exhibits"]:
        exhibit = dict(raw) if isinstance(raw, Mapping) else {}
        exhibit["sourceIds"] = normalized_source_ids(exhibit.get("sourceIds"))
        exhibit["panels"] = _normalize_exhibit_panels(exhibit.get("panels"))
        exhibits.append(_ascii(exhibit))
    visuals = [dict(item) for item in architecture["visuals"] if isinstance(item, Mapping)]
    expected_visual_ids = ["executive-summary", "chapter-1", "chapter-2", "chapter-3", "chapter-4"]
    for index, visual in enumerate(visuals):
        visual["id"] = expected_visual_ids[index]

    candidate = _ascii(
        {
            "title": title,
            "subtitle": _clean(architecture.get("subtitle"), 500),
            "coverSummary": _clean(architecture.get("coverSummary"), 1_500),
            "executiveSummary": executive,
            "chapters": chapters,
            "exhibits": exhibits,
            "outlook": outlook,
            "visuals": visuals,
        }
    )
    issues = _editorial_issues(candidate, valid_ids, authoritative_ids)
    if issues:
        raise GatexWhitepaperError("Assembled editorial QA failed: " + " | ".join(issues))
    (work_dir / "editorial-final.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidate


def visual_quality_issues(source: bytes | Path | Image.Image) -> list[str]:
    try:
        if isinstance(source, Image.Image):
            image = source.copy()
        elif isinstance(source, Path):
            image = Image.open(source)
        else:
            image = Image.open(io.BytesIO(source))
        with image:
            image.load()
            rgb = image.convert("RGB")
    except Exception as exc:
        return [f"image cannot be decoded ({exc})"]
    issues: list[str] = []
    if rgb.width < 1_000 or rgb.height < 650:
        issues.append(f"image is too small ({rgb.width}x{rgb.height})")
    sample = rgb.copy()
    sample.thumbnail((320, 240), Image.Resampling.LANCZOS)
    grayscale = sample.convert("L")
    histogram = grayscale.histogram()
    pixels = max(1, sample.width * sample.height)
    near_black = sum(histogram[:6]) / pixels
    near_white = sum(histogram[250:]) / pixels
    standard_deviation = float(ImageStat.Stat(grayscale).stddev[0])
    if near_black > 0.58:
        issues.append(f"{near_black:.0%} of sampled pixels are near-black")
    if near_white > 0.88:
        issues.append(f"{near_white:.0%} of sampled pixels are near-white")
    if standard_deviation < 10 or grayscale.entropy() < 3.2:
        issues.append("image has insufficient tonal information")
    sampled_pixels = list(grayscale.getdata())
    dark_rows = []
    for y in range(sample.height):
        row = sampled_pixels[y * sample.width : (y + 1) * sample.width]
        dark_rows.append(sum(value <= 5 for value in row) / max(1, len(row)) >= 0.985)
    dark_columns = []
    for x in range(sample.width):
        column = [sampled_pixels[y * sample.width + x] for y in range(sample.height)]
        dark_columns.append(sum(value <= 5 for value in column) / max(1, len(column)) >= 0.985)

    def longest_run(values: Sequence[bool]) -> int:
        longest = current = 0
        for value in values:
            current = current + 1 if value else 0
            longest = max(longest, current)
        return longest

    if longest_run(dark_rows) / max(1, sample.height) >= 0.28 or longest_run(dark_columns) / max(1, sample.width) >= 0.28:
        issues.append("image contains a large solid-black band")
    return issues


def _visual_data_url(source: bytes | Path | Image.Image) -> str:
    if isinstance(source, Image.Image):
        image = source.copy()
    elif isinstance(source, Path):
        image = Image.open(source)
    else:
        image = Image.open(io.BytesIO(source))
    with image:
        image.load()
        preview = image.convert("RGB")
        preview.thumbnail((1_280, 1_280), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        preview.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def semantic_visual_quality_issues(
    source: bytes | Path | Image.Image,
    *,
    brief: str,
    alt: str = "",
) -> list[str]:
    api_key = os.getenv("QWEN_VL_API_KEY", "").strip()
    required = os.getenv("GATEX_REQUIRE_SEMANTIC_VISUAL_QA", "").strip().lower() in {"1", "true", "yes"}
    if not api_key:
        return ["Qwen visual QA is not configured"] if required else []

    base_url = os.getenv("QWEN_VL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = os.getenv("QWEN_VL_MODEL", "qwen3-vl-flash").strip() or "qwen3-vl-flash"
    instruction = f"""Assess one raw editorial image before it is placed in a GateX management-consulting white paper.
Visual brief: {_clean(brief, 2_500)}
Intended alt text: {_clean(alt, 500)}

Reject the image when any of these are true:
- it contains readable words, letters, numbers, logos, watermarks, captions, interfaces or generated pseudo-text;
- it is abstract filler, decorative geometry, a chart, a document screenshot or a mostly empty/black frame;
- the visible subject does not materially match the visual brief;
- it looks staged, synthetic, distorted, low-quality or unsuitable for a polished McKinsey/BCG-style publication.

Return JSON only with this exact shape:
{{"pass":true,"readableTextPresent":false,"readableText":[],"contextRelevance":0.0,"professionalQuality":0.0,"issues":[],"scene":"short factual description"}}
Scores range from 0 to 1. Be strict. Do not provide reasoning outside the JSON object."""
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": _visual_data_url(source)}},
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "temperature": 0,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") if isinstance(payload, Mapping) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else {}
    raw_content = message.get("content") if isinstance(message, Mapping) else ""
    if isinstance(raw_content, list):
        raw_content = "".join(
            str(item.get("text") or "") for item in raw_content if isinstance(item, Mapping)
        )
    raw_text = str(raw_content or "").strip()
    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.IGNORECASE | re.DOTALL)
    try:
        assessment = json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise GatexWhitepaperError(f"Qwen visual QA returned invalid JSON: {exc}") from exc
    if not isinstance(assessment, Mapping):
        raise GatexWhitepaperError("Qwen visual QA did not return a JSON object.")

    issues: list[str] = []
    readable = assessment.get("readableText")
    readable_rows = readable if isinstance(readable, list) else [readable] if readable else []
    if bool(assessment.get("readableTextPresent")) or readable_rows:
        excerpt = ", ".join(_clean(item, 80) for item in readable_rows if _clean(item, 80))
        issues.append("image contains readable or generated text" + (f" ({excerpt[:180]})" if excerpt else ""))
    try:
        context_score = float(assessment.get("contextRelevance", 0))
        quality_score = float(assessment.get("professionalQuality", 0))
    except (TypeError, ValueError):
        context_score = quality_score = 0
    if context_score < 0.72:
        issues.append(f"image is not sufficiently relevant to its visual brief ({context_score:.2f})")
    if quality_score < 0.72:
        issues.append(f"image is not publication quality ({quality_score:.2f})")
    model_issues = assessment.get("issues") if isinstance(assessment.get("issues"), list) else []
    if not bool(assessment.get("pass")):
        issues.extend(_clean(item, 180) for item in model_issues if _clean(item, 180))
        if not issues:
            issues.append("image failed semantic visual QA")
    return list(dict.fromkeys(issues))


def _download_apimart_image(prompt: str) -> bytes:
    api_key = os.getenv("APIMART_API_KEY", "").strip()
    if not api_key:
        raise GatexWhitepaperError("APIMART_API_KEY is not configured.")
    base_url = os.getenv("APIMART_BASE_URL", "https://api.apimart.ai").rstrip("/")
    model = os.getenv("APIMART_IMAGE_MODEL", "gpt-image-2")
    response = requests.post(
        f"{base_url}/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "prompt": prompt, "n": 1, "size": "3:2", "resolution": "2k"},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    task_id = _clean(data.get("task_id") or data.get("id"), 240) if isinstance(data, Mapping) else ""
    if not task_id:
        raise GatexWhitepaperError("APIMart image request did not return a task id.")
    deadline = time.time() + 420
    while time.time() < deadline:
        time.sleep(8)
        result = requests.get(
            f"{base_url}/v1/tasks/{urllib.parse.quote(task_id, safe='')}?language=en",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=90,
        )
        result.raise_for_status()
        payload = result.json()
        task = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        status = _clean(task.get("status"), 80).lower() or "processing"
        if status == "failed":
            raise GatexWhitepaperError(f"APIMart image task {task_id[-8:]} failed.")
        if status != "completed":
            continue
        task_result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
        images = task_result.get("images") if isinstance(task_result.get("images"), list) else []
        first = images[0] if images and isinstance(images[0], Mapping) else {}
        urls = first.get("url") if isinstance(first.get("url"), list) else []
        image_url = _clean(urls[0] if urls else "", 2_000)
        if not image_url.startswith("https://"):
            raise GatexWhitepaperError("APIMart image task completed without an HTTPS image URL.")
        image_response = requests.get(image_url, headers={"Accept": "image/*"}, timeout=120)
        image_response.raise_for_status()
        return image_response.content
    raise GatexWhitepaperError(f"APIMart image task {task_id[-8:]} did not finish within seven minutes.")


def _pollinations_image(prompt: str, seed: str) -> bytes:
    params = urllib.parse.urlencode(
        {"width": 1280, "height": 854, "enhance": "true", "private": "true", "nologo": "true", "safe": "true", "model": "flux", "seed": seed}
    )
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt, safe="") + "?" + params
    response = requests.get(url, headers={"Accept": "image/*", "User-Agent": "GateXWhitepaper/4.0"}, timeout=90)
    response.raise_for_status()
    return response.content


def _save_visual(blob: bytes, target: Path, *, brief: str, alt: str) -> None:
    issues = visual_quality_issues(blob)
    issues.extend(semantic_visual_quality_issues(blob, brief=brief, alt=alt))
    if issues:
        raise GatexWhitepaperError("Generated image failed visual QA: " + "; ".join(issues))
    with Image.open(io.BytesIO(blob)) as source:
        image = source.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=88, optimize=True)


def _generate_visuals(content: Mapping[str, Any], target_dir: Path) -> dict[str, dict[str, str]]:
    rows = {str(item.get("id")): item for item in content.get("visuals") or [] if isinstance(item, Mapping)}
    shared = (
        " Premium management-consulting white-paper editorial photography, documentary realism, precise natural light, "
        "restrained navy, steel, teal and neutral palette, 3:2 landscape composition. Show the actual industry, infrastructure, "
        "technology or operating context. No readable text, letters, numbers, logos, flags, insignia, watermarks, interfaces, "
        "charts, abstract geometry, decorative gradients or black empty areas."
    )

    def generate(identifier: str, row: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
        target = target_dir / f"{identifier}.jpg"
        prompt = _clean(row.get("prompt"), 3_500) + shared
        alt = _clean(row.get("alt"), 500)
        if target.is_file():
            issues = visual_quality_issues(target)
            issues.extend(semantic_visual_quality_issues(target, brief=prompt, alt=alt))
            if not issues:
                return identifier, {"path": str(target), "alt": alt}
            print(f"[gatex.whitepaper] cached visual rejected for {identifier}: {'; '.join(issues)}", flush=True)
        errors: list[str] = []
        for attempt in range(3):
            try:
                blob = _download_apimart_image(prompt + (f" Alternate documentary camera composition {attempt + 1}." if attempt else ""))
                _save_visual(blob, target, brief=prompt, alt=alt)
                return identifier, {"path": str(target), "alt": alt}
            except Exception as exc:
                errors.append(str(exc))
        try:
            blob = _pollinations_image(prompt, hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:10])
            _save_visual(blob, target, brief=prompt, alt=alt)
            return identifier, {"path": str(target), "alt": alt}
        except Exception as exc:
            errors.append(str(exc))
        raise GatexWhitepaperError(f"Visual generation failed for {identifier}: {' | '.join(errors)}")

    results: dict[str, dict[str, str]] = {}
    workers = max(1, min(3, int(os.getenv("GATEX_IMAGE_WORKERS", "3"))))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(generate, identifier, row): identifier for identifier, row in rows.items()}
        for future in as_completed(futures):
            identifier, value = future.result()
            results[identifier] = value
            print(f"[gatex.whitepaper] visual prepared: {identifier}", flush=True)
    return results


def _english_source_title(source: Mapping[str, str]) -> str:
    title = _clean(source.get("title"), 500)
    title = title.translate(str.maketrans({
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }))
    title = re.sub(r"\s+(?:of|for|in)\s+\.{3}$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\.{3}$", "", title).rstrip(" .")
    if not re.search(r"[\u3400-\u9fff]", title):
        return title
    translations = (
        ("首次公开发行股票并在科创板上市招股说明书", "STAR Market Initial Public Offering Prospectus (Registration Draft)"),
        ("长鑫科技集团股份有限公司", "ChangXin Memory Technologies Group Co., Ltd. Filing"),
    )
    for needle, translated in translations:
        if needle in title:
            return translated
    if "招股" in title:
        return "Initial Public Offering Prospectus"
    if "年度报告" in title or "年报" in title:
        return "Annual Report"
    if "统计" in title:
        return "Official Statistical Release"
    if "公告" in title or "有限公司" in title:
        return "Company Filing"
    domain = _clean(source.get("domain"), 160)
    return "Official Publication" if ".gov" in domain else "Primary-source Publication"


def _citation_rows(source_ids: Iterable[Any], source_map: Mapping[str, Mapping[str, str]]) -> list[str]:
    rows: list[str] = []
    for source_id in source_ids:
        source = source_map.get(str(source_id))
        if not source:
            continue
        rows.append(f"{_english_source_title(source)}, {source['domain']}, {source['url']}")
    # Five long citations can spill a single footnote onto an otherwise empty page.
    # Four still provides strong triangulation while keeping the citation block intact.
    return rows[:4]


def _authors(slug: str) -> list[dict[str, str]]:
    rng = random.Random(int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:16], 16))
    names = rng.sample(AUTHOR_POOL, 4)
    roles = ("China Technology Research", "Capital Markets Research", "Industry Strategy", "Research Operations")
    result = [{"name": "Frank Feng", "role": "Managing Partner", "email": "frank@gatex.fund"}]
    for name, role in zip(names, roles):
        first = re.sub(r"[^a-z]", "", name.split()[0].lower())
        result.append({"name": name, "role": role, "email": f"{first}@gatex.fund"})
    return result


def _disclaimer() -> dict[str, Any]:
    return {
        "id": "disclaimer",
        "kind": "disclaimer",
        "heading": "Disclaimer",
        "body": "This publication is prepared by GateX, a management-consulting and strategic-intelligence firm, solely for general information and informed management discussion. It is not investment, legal, tax, accounting, regulatory or other professional advice; it is not an offer, solicitation, recommendation, valuation or assurance concerning any security, transaction, jurisdiction or strategy.",
        "items": [
            {"heading": "Information boundary", "body": "The publication draws on public and permitted third-party information believed to be reliable at the publication date. GateX has not independently audited every underlying statement and does not warrant that the material is complete, accurate or current. Facts, estimates and circumstances may change without notice."},
            {"heading": "Forward-looking material", "body": "Forecasts, estimates, targets and scenarios are inherently uncertain and are included only to frame possible developments. Actual outcomes may differ materially because of market, policy, technology, financing, execution, geopolitical and other factors. No forecast should be read as a promise or assurance."},
            {"heading": "Independent judgement", "body": "Readers remain responsible for their own analysis, assumptions and decisions. They should obtain appropriate legal, tax, accounting, regulatory, technical and investment advice before making any commercial, capital-allocation or operating decision."},
            {"heading": "No offer or fiduciary duty", "body": "Nothing in this publication constitutes an offer to buy or sell, a solicitation, personal recommendation, fairness opinion or commitment to arrange a transaction. GateX does not act as fiduciary, broker, dealer, investment adviser or placement agent by providing this material."},
            {"heading": "Third-party material", "body": "Names, marks, data and publications belonging to third parties remain the property of their respective owners. Their inclusion does not imply sponsorship, endorsement or verification. Source links are provided to identify the underlying public record and may later change or become unavailable."},
            {"heading": "Conflicts and interests", "body": "GateX and its personnel may advise, research or maintain relationships with organisations active in sectors discussed here. The publication is prepared as general intelligence and should not be treated as independent securities research or as a complete statement of any such relationship."},
            {"heading": "Distribution and confidentiality", "body": "This member-confidential edition is intended only for authorised GateX readers. It may not be copied, quoted, reproduced, forwarded, posted or redistributed, in whole or in part, without prior written permission from GateX, except where applicable law expressly permits."},
            {"heading": "Limitation of responsibility", "body": "To the fullest extent permitted by law, GateX accepts no responsibility for losses arising from reliance on this publication or from errors, omissions, delays or interruptions in underlying information. References to companies, markets or jurisdictions do not constitute endorsement."},
        ],
    }


def _build_payload(
    *,
    slug: str,
    title: str,
    publication_date: str,
    content: Mapping[str, Any],
    sources: Sequence[Mapping[str, str]],
    visuals: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    source_map = {str(item["id"]): item for item in sources}
    executive = content["executiveSummary"]
    executive_visual = visuals["executive-summary"]
    sections: list[dict[str, Any]] = [
        {
            "id": "executive-summary",
            "kind": "executive_summary",
            "heading": executive["headline"],
            "lead": executive["deck"],
            "paragraphs": executive["paragraphs"],
            "visualPath": executive_visual["path"],
            "visualAlt": executive_visual["alt"],
            "footnotes": _citation_rows(executive.get("sourceIds") or [], source_map),
        }
    ]
    for index, (chapter, exhibit) in enumerate(zip(content["chapters"], content["exhibits"]), start=1):
        visual = visuals[f"chapter-{index}"]
        sections.append(
            {
                "id": f"chapter-{index}",
                "kind": "chapter",
                "chapterNumber": f"{index:02d}",
                "heading": chapter["title"],
                "lead": chapter["deck"],
                "callout": chapter["callout"],
                "paragraphs": [chapter["opening"]],
                "subsections": chapter["subsections"],
                "visualPath": visual["path"],
                "visualAlt": visual["alt"],
                "visualPlacement": "top",
                "footnotes": _citation_rows(chapter.get("sourceIds") or [], source_map),
            }
        )
        sections.append(
            {
                "id": f"exhibit-{index}",
                "kind": "exhibit",
                "heading": re.sub(r"^\s*Exhibit\s*\d+\s*[-:]?\s*", "", _clean(exhibit["heading"], 500), flags=re.IGNORECASE),
                "exhibit": {
                    "label": f"EXHIBIT {index}",
                    "caption": re.sub(r"\s*Source\s*:\s*GateX\.?", "", _clean(exhibit["caption"], 1_000), flags=re.IGNORECASE),
                    "metrics": exhibit.get("metrics") or [],
                    "panels": exhibit.get("panels") or [],
                    "source_note": "",
                },
                "footnotes": _citation_rows(exhibit.get("sourceIds") or [], source_map),
            }
        )
    outlook = content["outlook"]
    sections.append(
        {
            "id": "outlook",
            "kind": "outlook",
            "chapterNumber": "C",
            "heading": outlook["title"],
            "lead": outlook["deck"],
            "callout": outlook["callout"],
            "paragraphs": outlook["paragraphs"],
            "footnotes": _citation_rows(outlook.get("sourceIds") or [], source_map),
        }
    )
    sections.append(_disclaimer())
    return {
        "contentKey": slug,
        "slug": slug,
        "title": title,
        "subtitle": content["subtitle"],
        "summary": content["coverSummary"],
        "reportType": "Strategic Intelligence",
        "language": "en",
        "classification": "Member Confidential",
        "publishedAt": f"{publication_date}T00:00:00.000Z",
        "releaseDate": publication_date,
        "versionNo": 1,
        "accessScope": "member",
        "contentSections": sections,
        "coverImagePath": executive_visual["path"],
        "authors": _authors(slug),
    }


def _inject_toc(pdf_path: Path, payload: dict[str, Any]) -> None:
    reader = PdfReader(str(pdf_path))
    pages = [re.sub(r"[^a-z0-9]", "", (page.extract_text() or "").lower()) for page in reader.pages]
    for section in payload["contentSections"]:
        if section.get("kind") not in {"chapter", "outlook"}:
            continue
        needle = re.sub(r"[^a-z0-9]", "", _clean(section.get("heading"), 500).lower())
        page_number = next((index + 1 for index, text in enumerate(pages) if index > 2 and needle in text), None)
        if not page_number:
            raise GatexWhitepaperError(f"Unable to map Contents entry: {section.get('heading')}")
        section["tocPage"] = str(page_number)


def _page_composition_issues(page_text: str, page_number: int) -> list[str]:
    word_count = len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b", page_text))
    issues: list[str] = []
    if word_count < 35:
        issues.append(f"Page {page_number} is unexpectedly sparse.")
    page_lower = page_text.lower()
    if (
        "sources and notes" in page_lower
        and word_count < 120
        and not any(
            marker in page_lower
            for marker in ("executive summary", "chapter ", "exhibit ", "outlook", "disclaimer")
        )
    ):
        issues.append(f"Page {page_number} contains orphaned source notes.")
    return issues


def _pdf_issues(pdf_path: Path, payload: Mapping[str, Any]) -> list[str]:
    reader = PdfReader(str(pdf_path))
    text_by_page = [page.extract_text() or "" for page in reader.pages]
    full = "\n".join(text_by_page)
    lowered = full.lower()
    issues: list[str] = []
    if not 18 <= len(reader.pages) <= 22:
        issues.append(f"Unexpected page count: {len(reader.pages)}")
    if len(text_by_page) < 3 or "executive summary" not in text_by_page[1].lower():
        issues.append("Executive summary is not on page 2.")
    if len(text_by_page) < 4 or "contents" not in text_by_page[2].lower():
        issues.append("Contents is not on page 3.")
    if "disclaimer" not in text_by_page[-2].lower():
        issues.append("Disclaimer is not the penultimate page.")
    for chapter in range(1, 5):
        if f"chapter {chapter:02d} / continued" not in lowered:
            issues.append(f"Chapter {chapter:02d} continuation page is missing.")
    for page_number, page_text in enumerate(text_by_page[1:-1], start=2):
        issues.extend(_page_composition_issues(page_text, page_number))
    if re.search(r"[\u3400-\u9fff]", full):
        issues.append("Chinese text remains in the PDF.")
    if NON_USD_CURRENCY_RE.search(full):
        issues.append("A non-USD currency remains in the PDF.")
    for term in FORBIDDEN_TERMS:
        if term in lowered:
            issues.append(f"Forbidden term remains: {term}")
    for section in payload.get("contentSections") or []:
        if section.get("kind") in {"executive_summary", "chapter", "exhibit", "outlook"}:
            if not any("https://" in str(item) for item in section.get("footnotes") or []):
                issues.append(f"Missing underlying source URL for {section.get('id')}")
        visual_path = Path(str(section.get("visualPath") or ""))
        if visual_path.is_file():
            for issue in visual_quality_issues(visual_path):
                issues.append(f"Visual {visual_path.name}: {issue}")
        if section.get("kind") == "exhibit":
            heading_key = re.sub(r"[^a-z0-9]", "", _clean(section.get("heading"), 500).lower())
            page_text = next(
                (
                    text_by_page[index]
                    for index in range(3, len(text_by_page) - 1)
                    if heading_key and heading_key in re.sub(r"[^a-z0-9]", "", text_by_page[index].lower())
                ),
                "",
            )
            issues.extend(_chart_label_issues(section.get("exhibit") or {}, page_text))
    return issues


def _chart_label_issues(exhibit: Mapping[str, Any], page_text: str) -> list[str]:
    page_key = re.sub(r"[^a-z0-9]", "", page_text.lower())
    issues: list[str] = []
    for panel in exhibit.get("panels") or []:
        if not isinstance(panel, Mapping):
            continue
        kind = _clean(panel.get("type"), 40).lower()
        labels: list[str] = []
        if kind in {"bars", "scatter", "scatter_plot", "stacked_bar", "stacked_bars", "waterfall", "waterfall_chart", "vehicle_scale"}:
            labels.extend(_clean(item.get("label"), 120) for item in panel.get("items") or [] if isinstance(item, Mapping))
        elif kind in {"line", "line_chart"}:
            labels.extend(_clean(item.get("name"), 120) for item in panel.get("series") or [] if isinstance(item, Mapping))
            labels.extend(_clean(item, 120) for item in panel.get("xLabels") or [])
        for label in labels:
            label_key = re.sub(r"[^a-z0-9]", "", label.lower())
            if len(label_key) >= 3 and label_key not in page_key:
                issues.append(f"Rendered chart label is missing or clipped: {label}")
    return issues


def _uniform_dark_region_issue(source: Path | Image.Image) -> str:
    """Detect the solid-black image failures that can survive PDF rendering."""

    if isinstance(source, Image.Image):
        image = source.copy()
    else:
        image = Image.open(source)
    with image:
        sample = image.convert("L")
        sample.thumbnail((252, 356), Image.Resampling.LANCZOS)
    columns = 28
    rows = 40
    tile_width = max(1, sample.width // columns)
    tile_height = max(1, sample.height // rows)
    dark: set[tuple[int, int]] = set()
    for row in range(rows):
        for column in range(columns):
            left = column * tile_width
            top = row * tile_height
            right = sample.width if column == columns - 1 else min(sample.width, left + tile_width)
            bottom = sample.height if row == rows - 1 else min(sample.height, top + tile_height)
            tile = sample.crop((left, top, right, bottom))
            stat = ImageStat.Stat(tile)
            if float(stat.mean[0]) <= 7.0 and float(stat.stddev[0]) <= 3.0:
                dark.add((column, row))

    largest: set[tuple[int, int]] = set()
    unseen = set(dark)
    while unseen:
        seed = unseen.pop()
        component = {seed}
        frontier = [seed]
        while frontier:
            column, row = frontier.pop()
            for neighbour in ((column - 1, row), (column + 1, row), (column, row - 1), (column, row + 1)):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    component.add(neighbour)
                    frontier.append(neighbour)
        if len(component) > len(largest):
            largest = component
    if not largest:
        return ""
    coverage = len(largest) / max(1, columns * rows)
    width = max(column for column, _ in largest) - min(column for column, _ in largest) + 1
    height = max(row for _, row in largest) - min(row for _, row in largest) + 1
    if coverage >= 0.05 and width >= 6 and height >= 4:
        return f"solid near-black rendered region covers {coverage:.1%} of the page"
    return ""


def _printable_content_overlap_issue(
    *,
    page_height: float,
    y0: float,
    y1: float,
    text: str,
) -> str:
    printable_bottom = page_height - (18 / 25.4 * 72)
    normalized = re.sub(r"\s+", " ", text).strip().upper()
    is_footer = (
        y0 >= printable_bottom + 8
        or normalized in {"MEMBER CONFIDENTIAL", "GATEX.FUND"}
        or bool(re.fullmatch(r"\d{2}\s*/\s*\d{2}", normalized))
    )
    if not is_footer and y0 < printable_bottom and y1 > printable_bottom + 0.5:
        return f"text crosses the printable footer boundary ({y0:.1f}-{y1:.1f} > {printable_bottom:.1f})"
    return ""


def _payload_renderability_issues(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    exhibits = [
        section
        for section in payload.get("contentSections") or []
        if isinstance(section, Mapping) and section.get("kind") == "exhibit"
    ]
    if len(exhibits) != 4:
        issues.append(f"Expected four rendered exhibits, found {len(exhibits)}.")
    for exhibit_index, section in enumerate(exhibits, start=1):
        exhibit = section.get("exhibit") if isinstance(section.get("exhibit"), Mapping) else {}
        panels = exhibit.get("panels") if isinstance(exhibit.get("panels"), list) else []
        metrics = exhibit.get("metrics") if isinstance(exhibit.get("metrics"), list) else []
        if not 1 <= len(panels) <= 2:
            issues.append(f"Exhibit {exhibit_index} has {len(panels)} panels; expected one or two.")
        if len(panels) == 2 and len(metrics) > 2:
            issues.append(f"Exhibit {exhibit_index} has two panels and more than two metric cards.")
        for panel_index, panel in enumerate(panels, start=1):
            issue = _panel_renderability_issue(panel if isinstance(panel, Mapping) else {})
            if issue:
                issues.append(f"Exhibit {exhibit_index} panel {panel_index}: {issue}.")
    return issues


def _contact_sheets(page_paths: Sequence[Path], output_dir: Path) -> list[str]:
    contact_dir = output_dir / "review-contact-sheets"
    if contact_dir.exists():
        shutil.rmtree(contact_dir)
    contact_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for start in range(0, len(page_paths), 9):
        batch = page_paths[start : start + 9]
        thumbnails: list[Image.Image] = []
        for path in batch:
            with Image.open(path) as page:
                thumbnail = page.convert("RGB")
                thumbnail.thumbnail((360, 510), Image.Resampling.LANCZOS)
                thumbnails.append(thumbnail.copy())
        cell_width = max(image.width for image in thumbnails) + 24
        cell_height = max(image.height for image in thumbnails) + 42
        sheet = Image.new("RGB", (cell_width * 3, cell_height * 3), "#d9dee5")
        for offset, thumbnail in enumerate(thumbnails):
            x = (offset % 3) * cell_width + (cell_width - thumbnail.width) // 2
            y = (offset // 3) * cell_height + 28
            sheet.paste(thumbnail, (x, y))
        first_page = start + 1
        last_page = start + len(batch)
        target = contact_dir / f"pages-{first_page:02d}-{last_page:02d}.jpg"
        sheet.save(target, format="JPEG", quality=88, optimize=True)
        outputs.append(str(target))
    return outputs


def _render_full_review_package(pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    legacy_preview_dir = output_dir / "review-previews"
    if legacy_preview_dir.exists():
        shutil.rmtree(legacy_preview_dir)
    page_dir = output_dir / "review-pages"
    if page_dir.exists():
        shutil.rmtree(page_dir)
    page_dir.mkdir(parents=True, exist_ok=True)
    page_paths: list[Path] = []
    visual_issues: list[str] = []
    geometry_issues: list[str] = []
    document = fitz.open(pdf_path)
    try:
        for index, page in enumerate(document):
            target = page_dir / f"page-{index + 1:02d}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(target)
            page_paths.append(target)
            if 0 < index < document.page_count - 1:
                issue = _uniform_dark_region_issue(target)
                if issue:
                    visual_issues.append(f"Page {index + 1}: {issue}.")
            bounds = page.rect
            for block in page.get_text("blocks"):
                x0, y0, x1, y1 = block[:4]
                block_text = str(block[4] or "")
                if x0 < -1 or y0 < -1 or x1 > bounds.width + 1 or y1 > bounds.height + 1:
                    geometry_issues.append(
                        f"Page {index + 1}: text block extends outside the page bounds "
                        f"({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})."
                    )
                if 0 < index < document.page_count - 1 and (
                    issue := _printable_content_overlap_issue(
                        page_height=bounds.height,
                        y0=y0,
                        y1=y1,
                        text=block_text,
                    )
                ):
                    geometry_issues.append(f"Page {index + 1}: {issue}.")
    finally:
        document.close()
    return {
        "pagePaths": [str(path) for path in page_paths],
        "contactSheetPaths": _contact_sheets(page_paths, output_dir),
        "visualIssues": visual_issues,
        "geometryIssues": geometry_issues,
    }


def generate_gatex_whitepaper(
    *,
    topic: str,
    title: str,
    slug: str,
    brief: str,
    output_root: Path,
    model: str = "gpt-5.6-sol",
    publication_date: str | None = None,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    work_dir = output_root / slug
    work_dir.mkdir(parents=True, exist_ok=True)
    publication_date = publication_date or date.today().isoformat()

    _progress("research", 8, "Planning the evidence search and source requirements.", 18)
    research_dir = work_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    research = _collect_research(topic, brief, research_dir)

    _progress("synthesis", 42, "Converting the evidence base into the GateX white-paper architecture.", 10)
    client = _editorial_client(model, timeout=420)
    sources, source_packet = _source_packet(research)
    editorial_brief = (
        f"{brief}\nPublication date: {publication_date}. "
        "Apply the reporting-period boundary literally: an unfinished quarter is an outlook or latest-data edition, not a completed-quarter result."
    )
    content = _prepare_editorial(
        client,
        title=title,
        topic=topic,
        brief=editorial_brief,
        source_packet=source_packet,
        evidence=research.get("approved_evidence") or research.get("evidence_ledger") or [],
        sources=sources,
        work_dir=work_dir,
    )
    if period_issues := _reporting_period_issues(title, publication_date, content):
        raise GatexWhitepaperError("Reporting-period QA failed: " + " | ".join(period_issues))

    _progress("visuals", 62, "Generating and pixel-checking five contextual editorial images.", 7)
    visuals = _generate_visuals(content, work_dir / "assets")
    payload = _build_payload(
        slug=slug,
        title=title,
        publication_date=publication_date,
        content=content,
        sources=sources,
        visuals=visuals,
    )
    (work_dir / "gatex-release-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    _progress("rendering", 82, "Rendering the first PDF pass and resolving the contents page.", 4)
    artifact = render_gatex_release_pdf(payload, work_dir, output_name=f"gatex-{slug}.pdf")
    pdf_path = Path(artifact["path"])
    _inject_toc(pdf_path, payload)
    artifact = render_gatex_release_pdf(payload, work_dir, output_name=f"gatex-{slug}.pdf")
    pdf_path = Path(artifact["path"])

    _progress("quality_assurance", 93, "Running pagination, citations, language, currency and black-image checks.", 2)
    validate_gatex_pdf(pdf_path, expected_title=title)
    review_package = _render_full_review_package(pdf_path, work_dir)
    issues = [
        *_pdf_issues(pdf_path, payload),
        *_payload_renderability_issues(payload),
        *review_package["visualIssues"],
        *review_package["geometryIssues"],
    ]
    if issues:
        raise GatexWhitepaperError("PDF QA failed: " + " | ".join(issues))
    source_tier_counts = Counter(str(source.get("qualityTier") or "SECONDARY") for source in sources)
    qa = {
        "status": "passed",
        "pageCount": artifact["pageCount"],
        "byteSize": artifact["byteSize"],
        "sha256": artifact["sha256"],
        "sourceCount": len(sources),
        "academicSourceCount": source_tier_counts.get("ACADEMIC", 0),
        "sourceTierCounts": dict(sorted(source_tier_counts.items())),
        "researchPolicyVersion": RESEARCH_POLICY_VERSION,
        "editorialWordCount": _word_count(content),
        "editorialModel": {
            "requested": model,
            "used": client.active_model,
            "route": client.active_route,
            "fallbackUsed": client.primary_disabled,
        },
        "pagePaths": review_package["pagePaths"],
        "contactSheetPaths": review_package["contactSheetPaths"],
        "visualIssues": [],
        "geometryIssues": [],
        "checks": [
            "GateX-only branding",
            "English-only copy",
            "USD-only monetary units",
            "underlying public source URLs",
            "academic context supplements rather than replaces authoritative evidence",
            "unfinished reporting periods use an explicit latest-data or outlook boundary",
            "18-22 page architecture",
            "every page rendered for review",
            "text blocks remain within page bounds",
            "five contextual images",
            "rendered-page black-screen detection",
            "four structurally renderable exhibits",
        ],
    }
    (work_dir / "qa-report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    review = {
        "status": "awaiting_human_review",
        "title": title,
        "slug": slug,
        "pdf": str(pdf_path),
        "payload": str(work_dir / "gatex-release-payload.json"),
        "qa": str(work_dir / "qa-report.json"),
        "publishReady": False,
        "generatedIn": "github-actions",
    }
    (work_dir / "review-manifest.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    _progress("human_review", 100, "Cloud generation is complete and waiting for publication review.", 0)
    return {"artifact": artifact, "qa": qa, "review": review, "workDir": str(work_dir)}
