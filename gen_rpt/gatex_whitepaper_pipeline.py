from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import shutil
import time
import urllib.parse
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
from .research_quality import build_research_fact_pack
from .web_evidence import build_evidence_ledger
from .web_fetch import collect_sources


class GatexWhitepaperError(RuntimeError):
    pass


class _FailoverEditorialClient:
    def __init__(self, primary: DeepSeekClient, fallback: DeepSeekClient | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_disabled = False

    def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not self.primary_disabled:
            try:
                return self.primary.chat_json(*args, **kwargs)
            except Exception as exc:
                if self.fallback is None:
                    raise
                self.primary_disabled = True
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
    return _FailoverEditorialClient(primary, fallback)


FORBIDDEN_TERMS = (
    "blue ocean",
    "blueocean",
    "kc desk",
    "bernstein",
    "management agenda",
    "key evidence",
    "decision implication",
    "strategic implication",
    "methodology and use",
    "decision sequence",
    "source: gatex",
    "report structure",
    "four-part analysis",
    "analysis proceeds",
    "mineru",
    "deepseek",
    "apimart",
    "qwen",
    "tavily",
    "gdelt",
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
    subject = _clean(topic, 500)
    return [
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


def _collect_research(topic: str, brief: str, work_dir: Path) -> dict[str, Any]:
    sources_path = work_dir / "sources.json"
    fact_pack_path = work_dir / "research-fact-pack.json"
    evidence_path = work_dir / "evidence-ledger.json"
    if os.getenv("GATEX_REUSE_RESEARCH", "true").strip().lower() not in {"0", "false", "no", "off"}:
        if sources_path.is_file() and fact_pack_path.is_file() and evidence_path.is_file():
            try:
                source_rows = json.loads(sources_path.read_text(encoding="utf-8"))
                fact_pack = json.loads(fact_pack_path.read_text(encoding="utf-8"))
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                if len(source_rows) >= 12 and len(evidence) >= 12:
                    _progress("research", 30, f"Reusing {len(source_rows)} cached sources and {len(evidence)} evidence points.", 10)
                    return {"sources": source_rows, "fact_pack": fact_pack, "approved_evidence": evidence, "evidence_ledger": evidence}
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
- Infer the relevant geography, institutions, industries and time horizon from the topic and brief.
- Cover current conditions, historical context, operating capacity, capital formation, policy, constraints and a source-grounded outlook where relevant.
- Include cross-border links only when the topic or brief calls for them; never assume a connection exists.
- Do not repeat the full topic in every query.

Return: {{"queries":["query 1","query 2"]}}""",
                },
            ],
            temperature=0.05,
        )
        generated = [_clean(item, 320) for item in planned.get("queries") or [] if _clean(item, 320)]
        if len(generated) >= 8:
            queries = generated[:10] + fallback[:4]
    except Exception as exc:
        print(f"[gatex.whitepaper] query planner fallback: {exc}", flush=True)
    queries = list(dict.fromkeys(queries))[:14]
    (work_dir / "research-queries.json").write_text(json.dumps({"queries": queries}, ensure_ascii=False, indent=2), encoding="utf-8")
    _progress("research", 16, f"Searching {len(queries)} evidence queries with Tavily and GDELT.", 13)
    sources = collect_sources(
        queries,
        per_query=max(2, min(6, int(os.getenv("GEN_RPT_PER_QUERY", "4")))),
        max_sources=max(16, min(40, int(os.getenv("GEN_RPT_MAX_SOURCES", "30")))),
    )
    if len(sources) < 12:
        raise GatexWhitepaperError(f"Public research produced only {len(sources)} usable sources; at least 12 are required.")
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
    if fact_pack.authoritative_source_count < 2:
        raise GatexWhitepaperError("Research requires at least two authoritative public sources.")
    source_rows = [source.__dict__ for source in sources]
    sources_path.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fact_pack_path.write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"sources": source_rows, "fact_pack": fact_pack.to_dict(), "approved_evidence": evidence, "evidence_ledger": evidence}


def _source_score(source: Mapping[str, Any]) -> int:
    url = str(source.get("url") or "").lower()
    domain = str(source.get("domain") or "").lower()
    title = str(source.get("title") or "").lower()
    score = 0
    if any(
        token in domain
        for token in (
            ".gov",
            "gov.",
            "hkex",
            "sse",
            "szse",
            "stats",
            "miit",
            "csrc",
            "sec.gov",
            "worldbank",
            "imf.org",
            "oecd.org",
            "un.org",
            "iea.org",
            "eia.gov",
            "centralbank",
        )
    ):
        score += 8
    if url.endswith(".pdf") or "annual report" in title or "statistics" in title:
        score += 4
    if any(token in domain for token in ("reuters", "ft.com", "bloomberg", "economist")):
        score += 2
    if str(source.get("source_type") or "") == "pdf":
        score += 2
    return score


def _source_packet(result: Mapping[str, Any], *, maximum: int = 18) -> tuple[list[dict[str, str]], str]:
    raw_sources = [item for item in result.get("sources") or [] if isinstance(item, Mapping)]
    ordered = sorted(raw_sources, key=_source_score, reverse=True)
    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for raw in ordered:
        url = _clean(raw.get("url"), 2_000)
        content = _clean(raw.get("content"), 5_000)
        if not url.startswith("https://") or not content or url in seen_urls:
            continue
        seen_urls.add(url)
        selected.append(
            {
                "id": f"S{len(selected) + 1}",
                "title": _clean(raw.get("title"), 240) or urllib.parse.urlparse(url).netloc,
                "url": url,
                "domain": _clean(raw.get("domain"), 120) or urllib.parse.urlparse(url).netloc,
                "content": content,
            }
        )
        if len(selected) >= maximum:
            break
    if len(selected) < 8:
        raise GatexWhitepaperError(f"Research produced only {len(selected)} usable public sources; at least 8 are required.")
    blocks = []
    for row in selected:
        blocks.append(
            f"[{row['id']}] {row['title']}\nURL: {row['url']}\nEXTRACT: {row['content']}"
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
        f"[{row['id']}] {row['title']}\nURL: {row['url']}\nEXTRACT: {_clean(row.get('content'), excerpt_chars)}"
        for row in rows
    ]
    return "\n\n".join(blocks)[:maximum_chars]


def _publication_rules() -> str:
    return """
- GateX is the only publication brand. Never name an upstream publisher, source file, search provider, model or production tool.
- Remain inside the supplied evidence. Do not invent facts, values, dates, quotations, companies or institutions.
- Use USD for every monetary value. Omit other currencies unless the evidence supplies a defensible conversion and rate date.
- Present Chinese technology capability and Middle Eastern market development with balanced, evidence-led language. Never reveal this editorial orientation and never force a cross-border link without evidence.
- Write fluent, specific English without AI mannerisms, repetitive summaries, generic recommendations or reader instructions.
- Do not expose chain-of-thought, recommendations, management instructions or process labels.
- Never use Management agenda, Key evidence, Decision implication, Strategic implication, Decision sequence, Methodology and use, So what, Report structure, or similar language.
- Use ASCII hyphens only.
""".strip()


def _architecture_prompt(
    *,
    title: str,
    topic: str,
    brief: str,
    sources: Sequence[Mapping[str, str]],
    evidence: Sequence[Mapping[str, Any]],
) -> str:
    source_packet = _compact_source_packet(sources=sources, excerpt_chars=1_200, maximum_chars=24_000)
    evidence_packet = json.dumps(list(evidence)[:20], ensure_ascii=False)[:14_000]
    return f"""
Design the publication architecture and exhibits for a client-ready English GateX white paper. Do not write the long-form chapter prose yet.

Approved title: {title}
Research question: {topic}
Editorial brief: {brief}

{_publication_rules()}

Architecture rules:
- Exactly four progressive chapters with short analytical titles, decks and evidence-specific callouts.
- Every executive, chapter, exhibit and outlook row cites two to five valid source IDs.
- Exactly four substantive exhibits, one after each chapter. Each combines at least two information layers and is grounded in cited source IDs.
- Use quantitative charts only for coherent source series. Otherwise use comparison, process, market map, scenario or matrix.
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
        return "comparison requires two named columns and at least two complete rows" if len(columns) != 2 or len(valid_rows) < 2 else ""
    if kind in {"line", "line_chart"}:
        series = [item for item in panel.get("series") or [] if isinstance(item, Mapping)]
        valid_series = [item for item in series if len(item.get("values") or []) >= 2]
        fallback_values = [item for item in items if item.get("label") and item.get("value") is not None]
        return "line chart requires a series with at least two values" if not valid_series and len(fallback_values) < 2 else ""
    if kind in {"scatter", "scatter_plot"}:
        valid_rows = [item for item in items if item.get("label") and item.get("x") is not None and item.get("y") is not None]
        return "scatter chart requires at least two labelled x/y points" if len(valid_rows) < 2 else ""
    if kind in {"stacked_bar", "stacked_bars"}:
        valid_rows = [item for item in items if item.get("label") and len(item.get("segments") or []) >= 2]
        return "stacked bars require at least two rows with two or more segments" if len(valid_rows) < 2 else ""
    if kind in {"waterfall", "waterfall_chart"}:
        valid_rows = [item for item in items if item.get("label") and item.get("value") is not None]
        return "waterfall chart requires at least two labelled values" if len(valid_rows) < 2 else ""
    if kind == "bars":
        valid_rows = [item for item in items if item.get("label") and item.get("value") is not None]
        return "bar chart requires at least two labelled values" if len(valid_rows) < 2 else ""
    if kind == "scenario":
        valid_rows = [item for item in items if item.get("label") and (item.get("range") or item.get("value")) and item.get("body")]
        return "scenario panel requires at least three complete scenarios" if len(valid_rows) < 3 else ""
    if kind in {"milestone", "milestones"}:
        valid_rows = [item for item in items if item.get("label") and (item.get("metric") or item.get("value"))]
        return "milestones panel requires at least three labelled milestones" if len(valid_rows) < 3 else ""
    if kind in {"process", "matrix", "market_map", "market_layers"}:
        valid_rows = [item for item in items if (item.get("title") or item.get("label")) and item.get("body")]
        return f"{kind} panel requires at least three complete items" if len(valid_rows) < 3 else ""
    return f"unsupported panel type: {kind}"


def _normalize_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(panel)
    issue = _panel_renderability_issue(normalized)
    if not issue:
        return normalized
    items = [item for item in normalized.get("items") or [] if isinstance(item, Mapping)]
    matrix_rows = [item for item in items if (item.get("title") or item.get("label")) and item.get("body")]
    if len(matrix_rows) >= 3:
        normalized["type"] = "matrix"
        normalized["items"] = matrix_rows[:6]
        normalized.pop("columns", None)
        normalized.pop("series", None)
        normalized.pop("xLabels", None)
        normalized.pop("categories", None)
    return normalized


def _editorial_issues(content: Mapping[str, Any], valid_source_ids: set[str]) -> list[str]:
    issues: list[str] = []
    executive = content.get("executiveSummary") if isinstance(content.get("executiveSummary"), Mapping) else {}
    chapters = content.get("chapters") if isinstance(content.get("chapters"), list) else []
    exhibits = content.get("exhibits") if isinstance(content.get("exhibits"), list) else []
    outlook = content.get("outlook") if isinstance(content.get("outlook"), Mapping) else {}
    visuals = content.get("visuals") if isinstance(content.get("visuals"), list) else []
    if len(executive.get("paragraphs") or []) != 4:
        issues.append("Executive summary must contain exactly four paragraphs.")
    if not 300 <= _word_count(executive) <= 520:
        issues.append(f"Executive summary length is {_word_count(executive)} words.")
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
    full_text = json.dumps(content, ensure_ascii=False).lower()
    found = [term for term in FORBIDDEN_TERMS if term in full_text]
    if found:
        issues.append(f"Forbidden terms remain: {found}.")
    if re.search(r"[\u3400-\u9fff]", full_text):
        issues.append("Chinese text remains in the manuscript.")
    if NON_USD_CURRENCY_RE.search(full_text):
        issues.append("A non-USD currency remains in the manuscript.")
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
    fallback_ids = [str(item["id"]) for item in sources[:4]]

    def normalized_source_ids(value: Any) -> list[str]:
        rows = [str(item) for item in value or [] if str(item) in valid_ids]
        rows = list(dict.fromkeys(rows))
        return (rows if len(rows) >= 2 else fallback_ids)[:5]

    def checkpoint_has_sources(value: Mapping[str, Any]) -> bool:
        return len({str(item) for item in value.get("sourceIds") or []} & valid_ids) >= 2

    checkpoint_executive = _read_json_mapping(work_dir / "editorial-executive.json")
    if checkpoint_executive is not None and not (
        len(checkpoint_executive.get("paragraphs") or []) == 4
        and 300 <= _word_count(checkpoint_executive) <= 520
        and checkpoint_has_sources(checkpoint_executive)
    ):
        checkpoint_executive = None

    checkpoint_chapters: list[dict[str, Any]] = []
    for index in range(1, 5):
        checkpoint = _read_json_mapping(work_dir / f"editorial-chapter-{index}.json")
        subsections = checkpoint.get("subsections") if checkpoint is not None else []
        valid_checkpoint = bool(
            checkpoint is not None
            and len(subsections or []) == 4
            and all(isinstance(item, Mapping) and len(item.get("paragraphs") or []) == 2 for item in subsections or [])
            and 560 <= _word_count(checkpoint) <= 980
            and checkpoint_has_sources(checkpoint)
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
    cached_architecture = _read_json_mapping(architecture_path)
    if cached_architecture is not None:
        cached_chapters = cached_architecture.get("chapters") if isinstance(cached_architecture.get("chapters"), list) else []
        cached_exhibits = cached_architecture.get("exhibits") if isinstance(cached_architecture.get("exhibits"), list) else []
        cached_visuals = cached_architecture.get("visuals") if isinstance(cached_architecture.get("visuals"), list) else []
        if len(cached_chapters) == 4 and len(cached_exhibits) == 4 and len(cached_visuals) == 5:
            architecture = cached_architecture

    for attempt in range(2 if architecture is None else 0):
        try:
            prompt = _architecture_prompt(
                title=title,
                topic=topic,
                brief=brief,
                sources=sources,
                evidence=evidence,
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
                    max_tokens=5_500,
                )
            )
            chapters = architecture.get("chapters") if isinstance(architecture.get("chapters"), list) else []
            exhibits = architecture.get("exhibits") if isinstance(architecture.get("exhibits"), list) else []
            visuals = architecture.get("visuals") if isinstance(architecture.get("visuals"), list) else []
            if len(chapters) != 4 or len(exhibits) != 4 or len(visuals) != 5:
                raise GatexWhitepaperError("Architecture requires four chapters, four exhibits and five visuals.")
            if locked_checkpoint_meta is not None:
                architecture["executiveSummary"] = locked_checkpoint_meta["executiveSummary"]
                architecture["chapters"] = locked_checkpoint_meta["chapters"]
            architecture_path.write_text(json.dumps(architecture, ensure_ascii=False, indent=2), encoding="utf-8")
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
    for attempt in range(2 if executive is None else 0):
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

Write exactly four paragraphs and 330-470 words. Establish the evidence-led thesis, the industrial capability, the capital-market transmission and the bounded cross-border relevance. Do not describe the report structure.
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
        if len(executive.get("paragraphs") or []) == 4 and 300 <= _word_count(executive) <= 520:
            break
        executive_error = f"Need exactly four paragraphs and 330-470 words; received {_word_count(executive)} words."
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
        for attempt in range(2 if chapter is None else 0):
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
            if paragraphs_ok and 560 <= _word_count(chapter) <= 980:
                break
            chapter_error = f"Need one opening, four two-paragraph subsections and 680-880 words; received {_word_count(chapter)} words."
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
        if 3 <= len(outlook.get("paragraphs") or []) <= 4 and 200 <= outlook_words <= 340:
            break
        outlook_error = f"Need three or four paragraphs and 220-300 body words; received {outlook_words} body words."
        outlook = None
    if outlook is None:
        raise GatexWhitepaperError(f"Outlook failed editorial QA: {outlook_error}")
    outlook_path.write_text(json.dumps(outlook, ensure_ascii=False, indent=2), encoding="utf-8")

    exhibits: list[dict[str, Any]] = []
    for raw in architecture["exhibits"]:
        exhibit = dict(raw) if isinstance(raw, Mapping) else {}
        exhibit["sourceIds"] = normalized_source_ids(exhibit.get("sourceIds"))
        exhibit["panels"] = [_normalize_panel(panel) for panel in exhibit.get("panels") or [] if isinstance(panel, Mapping)]
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
    issues = _editorial_issues(candidate, valid_ids)
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


def _save_visual(blob: bytes, target: Path) -> None:
    issues = visual_quality_issues(blob)
    if issues:
        raise GatexWhitepaperError("Generated image failed pixel QA: " + "; ".join(issues))
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
        if target.is_file():
            issues = visual_quality_issues(target)
            if not issues:
                return identifier, {"path": str(target), "alt": _clean(row.get("alt"), 500)}
            print(f"[gatex.whitepaper] cached visual rejected for {identifier}: {'; '.join(issues)}", flush=True)
        errors: list[str] = []
        for attempt in range(3):
            try:
                blob = _download_apimart_image(prompt + (f" Alternate documentary camera composition {attempt + 1}." if attempt else ""))
                _save_visual(blob, target)
                return identifier, {"path": str(target), "alt": _clean(row.get("alt"), 500)}
            except Exception as exc:
                errors.append(str(exc))
        try:
            blob = _pollinations_image(prompt, hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:10])
            _save_visual(blob, target)
            return identifier, {"path": str(target), "alt": _clean(row.get("alt"), 500)}
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
    return rows[:5]


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
        "body": "This publication is prepared by GateX for general information and management discussion. It is not investment, legal, tax, accounting or other professional advice, and it is not an offer, solicitation or recommendation concerning any security, transaction or strategy.",
        "items": [
            {"heading": "Information boundary", "body": "The publication draws on public information believed to be reliable at the publication date. GateX does not warrant completeness or accuracy, and facts may change without notice."},
            {"heading": "Forward-looking material", "body": "Forecasts, estimates and scenarios are inherently uncertain. Actual outcomes may differ materially because of market, policy, technology, financing and execution factors."},
            {"heading": "Independent judgement", "body": "Readers should conduct their own analysis and obtain appropriate professional advice before making any commercial, investment or operating decision."},
            {"heading": "Distribution", "body": "This member-confidential edition is intended only for authorised GateX readers. It may not be reproduced or redistributed without prior written permission."},
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
        if len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b", page_text)) < 35:
            issues.append(f"Page {page_number} is unexpectedly sparse.")
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
                if x0 < -1 or y0 < -1 or x1 > bounds.width + 1 or y1 > bounds.height + 1:
                    geometry_issues.append(
                        f"Page {index + 1}: text block extends outside the page bounds "
                        f"({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})."
                    )
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
    content = _prepare_editorial(
        client,
        title=title,
        topic=topic,
        brief=brief,
        source_packet=source_packet,
        evidence=research.get("approved_evidence") or research.get("evidence_ledger") or [],
        sources=sources,
        work_dir=work_dir,
    )

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
    qa = {
        "status": "passed",
        "pageCount": artifact["pageCount"],
        "byteSize": artifact["byteSize"],
        "sha256": artifact["sha256"],
        "sourceCount": len(sources),
        "editorialWordCount": _word_count(content),
        "pagePaths": review_package["pagePaths"],
        "contactSheetPaths": review_package["contactSheetPaths"],
        "visualIssues": [],
        "geometryIssues": [],
        "checks": [
            "GateX-only branding",
            "English-only copy",
            "USD-only monetary units",
            "underlying public source URLs",
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
