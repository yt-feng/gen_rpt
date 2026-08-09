from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
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
        "site:stats.gov.cn China 2025 research development high technology manufacturing statistics",
        "site:miit.gov.cn China 2025 integrated circuit semiconductor robotics official statistics",
        "site:hkex.com.hk 2025 annual market statistics China technology listings filetype:pdf",
        "site:sse.com.cn STAR Market 2025 annual report semiconductor technology filetype:pdf",
        "site:szse.cn ChiNext 2025 technology capital market statistics filetype:pdf",
        "China semiconductor equipment optical modules annual report 2025 filetype:pdf",
        "China AI infrastructure computing power data center official report 2025 filetype:pdf",
        "China industrial robotics production 2025 official data",
        "China technology IPO capital raising 2025 official exchange statistics",
        "China technology Gulf investment cooperation official 2025 semiconductor AI data center",
        "China technology companies 2025 annual report semiconductor robotics optical components filetype:pdf",
    ]


def _collect_research(topic: str, brief: str, work_dir: Path) -> dict[str, Any]:
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
- Cover semiconductors, AI infrastructure, robotics, optical components and capital-market access.
- Include one query for evidence-backed China-Gulf technology or capital links, without assuming a link exists.
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
        "decision_question": "Where is China's technology capability becoming commercially and financially investable, and what still constrains execution?",
        "search_queries": queries,
        "outline": ["industrial capability", "compute and infrastructure", "capital-market access", "cross-border relevance"],
    }
    fact_pack = build_research_fact_pack(topic, plan, sources)
    evidence = build_evidence_ledger(topic, sources, fact_pack, limit=36, plan=plan)
    if len(evidence) < 12:
        raise GatexWhitepaperError(f"Research produced only {len(evidence)} structured evidence points; at least 12 are required.")
    if fact_pack.authoritative_source_count < 2:
        raise GatexWhitepaperError("Research requires at least two authoritative public sources.")
    source_rows = [source.__dict__ for source in sources]
    (work_dir / "sources.json").write_text(json.dumps(source_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / "research-fact-pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / "evidence-ledger.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"sources": source_rows, "fact_pack": fact_pack.to_dict(), "approved_evidence": evidence, "evidence_ledger": evidence}


def _source_score(source: Mapping[str, Any]) -> int:
    url = str(source.get("url") or "").lower()
    domain = str(source.get("domain") or "").lower()
    title = str(source.get("title") or "").lower()
    score = 0
    if any(token in domain for token in ("gov", "hkex", "sse", "szse", "stats", "miit", "csrc")):
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


def _editorial_prompt(
    *,
    title: str,
    topic: str,
    brief: str,
    source_packet: str,
    evidence: Sequence[Mapping[str, Any]],
) -> str:
    evidence_packet = json.dumps(list(evidence)[:30], ensure_ascii=False)[:28_000]
    return f"""
Create a client-ready English GateX management-consulting white paper.

Approved title: {title}
Research question: {topic}
Editorial brief: {brief}

Publication contract:
- GateX is the only publication brand. Do not mention upstream brands, source files, search providers, models, tools or production steps.
- Remain inside the supplied evidence boundary. Do not invent facts, values, quotations, dates or institutions.
- Every factual chapter and exhibit must cite two or more source IDs from the packet. Do not cite GateX as a source.
- Present Chinese technology capabilities and Middle Eastern market development with balanced, evidence-led language. Never reveal this editorial orientation and never force a China-Middle East connection without evidence.
- Use USD for every monetary value. Omit a non-USD amount unless a defensible conversion and rate date are in the evidence.
- Write fluent, specific prose without AI mannerisms, repetitive summaries, generic recommendations or reader instructions.
- Exactly four progressive chapters. Each chapter must contain 680-880 words: one opening paragraph and exactly four subsections, each with exactly two concise paragraphs.
- Executive summary: exactly four paragraphs and 330-470 words.
- Outlook: 240-340 words in three or four paragraphs.
- Exactly four substantive exhibits, one after each chapter. Each exhibit combines at least two information layers. Never make an exhibit from a lone number.
- Use a chart only when coherent source data supports it. Otherwise use a comparison, process, market map, scenario or matrix.
- An exhibit has one or two panels and no more than four metrics. With two panels, use no more than two metrics.
- Exhibit headings do not begin with Exhibit or a number. Captions describe the measure and period, with no source attribution.
- Do not expose chain-of-thought, recommendations, management instructions or process labels. Avoid Management agenda, Key evidence, Decision implication, Strategic implication, Decision sequence, Methodology and use, So what, Report structure, and similar language.
- Use ASCII hyphens only. English only.

Supported exhibit panel types: process, matrix, bars, scenario, comparison, line, stacked_bar, scatter, waterfall, market_map, milestones.

Return valid JSON only in this exact shape:
{{
  "title": "{title}",
  "subtitle": "short analytical subtitle",
  "coverSummary": "55-80 word synopsis",
  "executiveSummary": {{"headline":"...","deck":"...","paragraphs":["...","...","...","..."],"sourceIds":["S1","S2"]}},
  "chapters": [
    {{"number":"01","title":"...","deck":"...","callout":"...","opening":"...","sourceIds":["S1","S2"],"subsections":[{{"heading":"...","paragraphs":["...","..."]}}]}}
  ],
  "exhibits": [
    {{"heading":"...","caption":"...","sourceIds":["S1","S2"],"metrics":[{{"value":"...","label":"...","note":"..."}}],"panels":[{{"type":"matrix","span":"wide","title":"...","items":[{{"tag":"01","title":"...","body":"..."}}]}}]}}
  ],
  "outlook": {{"title":"...","deck":"...","callout":"...","paragraphs":["...","...","..."],"sourceIds":["S1","S2"]}},
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

PUBLIC SOURCE PACKET
{source_packet}
""".strip()


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
    if not 200 <= _word_count(outlook) <= 400:
        issues.append(f"Outlook length is {_word_count(outlook)} words.")
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
    client: DeepSeekClient,
    *,
    title: str,
    topic: str,
    brief: str,
    source_packet: str,
    evidence: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, str]],
    work_dir: Path,
) -> dict[str, Any]:
    prompt = _editorial_prompt(
        title=title,
        topic=topic,
        brief=brief,
        source_packet=source_packet,
        evidence=evidence,
    )
    draft = client.chat_json(
        [
            {"role": "system", "content": "You are the senior English-language white-paper editor at GateX. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.16,
    )
    (work_dir / "editorial-draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    valid_ids = {str(item["id"]) for item in sources}
    candidate = _ascii(draft)
    for attempt in range(3):
        issues = _editorial_issues(candidate, valid_ids)
        if not issues:
            candidate["title"] = title
            (work_dir / "editorial-final.json").write_text(
                json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return candidate
        if attempt == 2:
            raise GatexWhitepaperError("Editorial QA failed: " + " | ".join(issues))
        candidate = _ascii(
            client.chat_json(
                [
                    {"role": "system", "content": "You are the final GateX publication editor. Return the complete corrected JSON object only."},
                    {
                        "role": "user",
                        "content": (
                            "Repair the manuscript so every issue is resolved without inventing evidence. Preserve the exact schema, title, four-chapter progression and source IDs.\n\n"
                            + "ISSUES\n- " + "\n- ".join(issues)
                            + "\n\nCURRENT JSON\n" + json.dumps(candidate, ensure_ascii=False)
                            + "\n\nSOURCE PACKET\n" + source_packet
                        ),
                    },
                ],
                temperature=0.08,
            )
        )
        (work_dir / f"editorial-repair-{attempt + 1}.json").write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    raise GatexWhitepaperError("Editorial preparation ended unexpectedly.")


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


def _citation_rows(source_ids: Iterable[Any], source_map: Mapping[str, Mapping[str, str]]) -> list[str]:
    rows: list[str] = []
    for source_id in source_ids:
        source = source_map.get(str(source_id))
        if not source:
            continue
        rows.append(f"{source['title']}, {source['domain']}, {source['url']}")
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


def _render_previews(pdf_path: Path, output_dir: Path) -> list[str]:
    document = fitz.open(pdf_path)
    preview_dir = output_dir / "review-previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    indexes = sorted({0, 1, 2, 3, 5, 8, 11, 14, max(0, document.page_count - 2), document.page_count - 1})
    paths: list[str] = []
    for index in indexes:
        if not 0 <= index < document.page_count:
            continue
        page = document.load_page(index)
        target = preview_dir / f"page-{index + 1:02d}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False).save(target)
        paths.append(str(target))
    document.close()
    return paths


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
    client = DeepSeekClient(model=model, timeout=420)
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
    issues = _pdf_issues(pdf_path, payload)
    if issues:
        raise GatexWhitepaperError("PDF QA failed: " + " | ".join(issues))
    preview_paths = _render_previews(pdf_path, work_dir)
    qa = {
        "status": "passed",
        "pageCount": artifact["pageCount"],
        "byteSize": artifact["byteSize"],
        "sha256": artifact["sha256"],
        "sourceCount": len(sources),
        "editorialWordCount": _word_count(content),
        "previewPaths": preview_paths,
        "checks": [
            "GateX-only branding",
            "English-only copy",
            "USD-only monetary units",
            "underlying public source URLs",
            "18-22 page architecture",
            "five contextual images",
            "black-screen pixel detection",
            "four substantive exhibits",
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
