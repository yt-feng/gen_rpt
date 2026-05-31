from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

from .web_fetch import SourceDocument

AUTHORITY_DOMAIN_HINTS = (
    ".gov",
    ".edu",
    "sec.gov",
    "europa.eu",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "iea.org",
    "un.org",
    "who.int",
    "wto.org",
    "federalreserve.gov",
    "bis.org",
    "nasdaq.com",
    "nyse.com",
    "londonstockexchange.com",
    "hkexnews.hk",
    "sse.com.cn",
    "szse.cn",
    "cninfo.com.cn",
    "csrc.gov.cn",
    "stats.gov.cn",
)

META_LABEL_PATTERNS = (
    "mckinsey-style",
    "bcg-style",
    "consulting-style",
    "sample card",
    "制作说明",
    "样例图卡",
)

GENERIC_SECTION_TITLES = (
    "overview",
    "introduction",
    "background",
    "market overview",
    "market dynamics",
    "key trends",
    "analysis",
    "conclusion",
    "摘要",
    "背景",
    "市场概览",
    "行业趋势",
    "分析",
    "结论",
)

GENERIC_CHART_TERMS = (
    "policy",
    "platforms",
    "technology",
    "market",
    "growth",
    "impact",
    "行业",
    "市场",
    "技术",
    "政策",
    "趋势",
)

NUMBER_RE = re.compile(r"\d+(?:[,.]\d+)*(?:\.\d+)?\s*(?:%|％|x|倍|年|月|日|亿美元|亿元|万元|million|billion|bn|mn|GW|MW|GWh|TWh|USD|RMB|HKD)?", re.I)
DATE_RE = re.compile(r"(?:20\d{2}|19\d{2})(?:[年\-/\.]\s?\d{1,2})?(?:[月\-/\.]\s?\d{1,2})?|Q[1-4]\s?20\d{2}|20\d{2}\s?Q[1-4]", re.I)
MONEY_RE = re.compile(r"(?:US\$|USD|RMB|HK\$|CNY|人民币|美元|港元|欧元|亿元|亿美元|万元|million|billion|bn|mn)", re.I)
URL_RE = re.compile(r"https?://[^\s,;)\]]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

EVIDENCE_KEYWORDS = (
    "revenue",
    "profit",
    "income",
    "ebitda",
    "cash flow",
    "market share",
    "capacity",
    "installed",
    "shipment",
    "valuation",
    "funding",
    "acquisition",
    "merger",
    "policy",
    "regulation",
    "tariff",
    "guidance",
    "forecast",
    "收入",
    "营收",
    "利润",
    "净利润",
    "现金流",
    "市场份额",
    "装机",
    "产能",
    "出货",
    "估值",
    "融资",
    "收购",
    "并购",
    "政策",
    "监管",
    "预测",
)


@dataclass
class ResearchFactPack:
    topic: str
    objective: str
    decision_question: str
    source_count: int
    authoritative_source_count: int
    source_domains: List[str]
    source_refs: List[str]
    high_confidence_facts: List[str]
    numeric_facts: List[str]
    dated_facts: List[str]
    validation_issues: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def digest(self, max_chars: int = 9000) -> str:
        payload = {
            "source_count": self.source_count,
            "authoritative_source_count": self.authoritative_source_count,
            "source_refs": self.source_refs[:14],
            "high_confidence_facts": self.high_confidence_facts[:22],
            "numeric_facts": self.numeric_facts[:16],
            "dated_facts": self.dated_facts[:10],
            "validation_issues": self.validation_issues,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]


def build_research_fact_pack(topic: str, plan: Dict[str, Any], sources: List[SourceDocument]) -> ResearchFactPack:
    domains = _clean_list([_domain(src.url) for src in sources if src.url], limit=20)
    source_refs = _source_refs(sources)
    scored_lines: List[tuple[int, str]] = []
    for idx, source in enumerate(sources, start=1):
        prefix = f"[Source {idx}: {source.title or source.domain or source.url}] "
        source_bonus = 4 if _is_authoritative(source.url) else 0
        if source.source_type == "pdf":
            source_bonus += 2
        for sentence in _split_sentences("\n".join([source.snippet or "", source.content or ""])):
            score = _score_sentence(sentence, topic) + source_bonus
            if score >= 4:
                scored_lines.append((score, prefix + sentence))

    facts = _ranked_unique(scored_lines, limit=24)
    numeric = [line for line in facts if NUMBER_RE.search(line) or MONEY_RE.search(line)]
    dated = [line for line in facts if DATE_RE.search(line)]
    auth_count = sum(1 for src in sources if _is_authoritative(src.url))
    issues = _validate_fact_pack(source_count=len(sources), auth_count=auth_count, domains=domains, facts=facts, numeric=numeric, dated=dated)
    return ResearchFactPack(
        topic=topic,
        objective=str(plan.get("objective") or topic),
        decision_question=str(plan.get("decision_question") or ""),
        source_count=len(sources),
        authoritative_source_count=auth_count,
        source_domains=domains,
        source_refs=source_refs,
        high_confidence_facts=facts,
        numeric_facts=numeric[:16],
        dated_facts=dated[:10],
        validation_issues=issues,
    )


def validate_report(report: Dict[str, Any], fact_pack: ResearchFactPack, *, language: str = "en") -> List[str]:
    issues: List[str] = []
    text = _report_text(report)
    sections = _as_list(report.get("sections"))
    summary = _as_list(report.get("executive_summary"))
    charts = _as_list(report.get("charts"))
    references = _as_list(report.get("references"))

    if fact_pack.validation_issues and not _mentions_evidence_boundary(text, language=language):
        issues.append("资料包仍有待核验事项，报告需要显式标注证据边界并避免补充未披露事实：" + "；".join(fact_pack.validation_issues[:4]))
    if len(summary) < 5:
        issues.append("executive_summary 不足，需要至少5条结论先行、可执行的关键发现。")
    if len(sections) < 7 or len(sections) > 10:
        issues.append(f"sections 数量应为7-10个，当前为{len(sections)}个。")
    for idx, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            issues.append(f"第{idx}个 section 不是对象。")
            continue
        title = str(section.get("title") or "")
        lead = str(section.get("lead") or "")
        paragraphs = [str(x).strip() for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        if _is_generic_title(title):
            issues.append(f"第{idx}章标题过于泛化，需要改成结论型标题：{title[:80]}")
        if len(lead) < 40:
            issues.append(f"第{idx}章 lead 过短，需要一句话说明该章结论和管理含义。")
        if len(paragraphs) < 3:
            issues.append(f"第{idx}章正文段落不足，需要至少3段有证据支撑的连续分析。")
        if not str(section.get("visual_hint") or "").strip():
            issues.append(f"第{idx}章缺少 visual_hint。")

    number_count = len(NUMBER_RE.findall(text))
    date_count = len(DATE_RE.findall(text))
    if fact_pack.numeric_facts and number_count < 18:
        issues.append(f"报告中的数字密度不足，当前约{number_count}处；需要使用资料包中的交易/市场/财务/经营数字。")
    if fact_pack.dated_facts and date_count < 3:
        issues.append(f"报告中的时间线不足，当前约{date_count}处；需要写入可核验年份、季度或日期。")
    if references:
        bad_refs = _references_outside_sources(references, fact_pack.source_refs)
        if bad_refs:
            issues.append("references 只能使用已抓取资料中的真实 URL，发现资料包外链接：" + "、".join(bad_refs[:4]))
    elif fact_pack.source_refs:
        issues.append("references 为空，需要引用已抓取资料中的真实来源。")

    if len(charts) < 5 or len(charts) > 7:
        issues.append(f"charts 数量应为5-7个，当前为{len(charts)}个。")
    for idx, chart in enumerate(charts, start=1):
        if not isinstance(chart, dict):
            issues.append(f"第{idx}个 chart 不是对象。")
            continue
        if str(chart.get("type") or "").lower() in {"pie", "donut"}:
            issues.append(f"第{idx}个 chart 使用了 pie/donut，应改成 bar、stacked_bar、line、matrix 或 bubble。")
        title = str(chart.get("title") or "")
        categories = [str(x) for x in _as_list(chart.get("categories"))]
        if _is_generic_chart_title(title, categories):
            issues.append(f"第{idx}个 chart 仍是泛化图表，需要贴合选题和资料证据：{title[:80]}")

    lower = text.lower()
    meta_hits = [p for p in META_LABEL_PATTERNS if p.lower() in lower]
    if meta_hits:
        issues.append("正式报告中仍出现内部元标签：" + "、".join(meta_hits))
    if "..." in text or "…" in text:
        issues.append("正式报告中仍有可见省略号，可能来自截断，需要改写成完整句子。")
    if language == "en" and CJK_RE.search(text):
        issues.append("英文报告中仍存在中文字符，需要翻译或移除。")
    if language == "zh" and _has_cjk_alnum_space(text):
        issues.append("中文报告中不应在中文字符与英文/数字之间加入空格。")

    repeated_openings = _repeated_paragraph_openings(sections)
    if repeated_openings:
        issues.append("段落开头重复、行文模式化，需要调整章节推进方式：" + "、".join(repeated_openings[:5]))
    return issues[:40]


def apply_deterministic_report_fixes(report: Dict[str, Any], fact_pack: ResearchFactPack, *, language: str = "en") -> Dict[str, Any]:
    fixed = json.loads(json.dumps(report, ensure_ascii=False))
    _walk_text(fixed, _clean_visible_text)
    source_urls = _source_urls_from_refs(fact_pack.source_refs)
    refs = []
    for ref in _as_list(fixed.get("references")):
        if not isinstance(ref, dict):
            continue
        url = str(ref.get("url") or "")
        if url and source_urls and _normalize_url(url) not in source_urls:
            continue
        refs.append(ref)
    if not refs and fact_pack.source_refs:
        refs = _fallback_references(fact_pack.source_refs)
    fixed["references"] = refs

    sections = []
    for idx, section in enumerate(_as_list(fixed.get("sections")), start=1):
        if not isinstance(section, dict):
            section = {"title": str(section), "lead": "", "paragraphs": [str(section)]}
        section["id"] = str(section.get("id") or f"section-{idx}")
        section["visual_hint"] = str(section.get("visual_hint") or f"image-{idx}")
        paragraphs = [str(x).strip() for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        if len(paragraphs) < 3:
            paragraphs.extend(_supplement_paragraphs(fact_pack, language=language, needed=3 - len(paragraphs)))
        section["paragraphs"] = paragraphs[:8]
        if not str(section.get("lead") or "").strip() and paragraphs:
            section["lead"] = _shorten(paragraphs[0], 220)
        sections.append(section)
    fixed["sections"] = sections
    return fixed


def build_revision_messages(
    *,
    topic: str,
    raw_topic: str,
    language: str,
    fact_pack: ResearchFactPack,
    issues: List[str],
    previous_report: Dict[str, Any],
) -> List[Dict[str, str]]:
    lang_rule = "Use English only." if language == "en" else "全程使用中文输出。"
    return [
        {"role": "system", "content": "You are a strict research editor and fact-checking reviewer. Return one valid JSON object only."},
        {
            "role": "user",
            "content": (
                "Revise the report data structure. Do not browse. Do not add facts beyond the evidence pack. "
                "Preserve the required schema and improve only what is needed to resolve the listed issues.\n"
                f"{lang_rule}\n"
                f"Topic: {topic}\n"
                f"Raw user input: {raw_topic}\n"
                "Evidence pack:\n"
                f"{fact_pack.digest()}\n"
                "Validation issues to fix:\n"
                f"{json.dumps(issues, ensure_ascii=False, indent=2)}\n"
                "Previous report JSON:\n"
                f"{json.dumps(previous_report, ensure_ascii=False)[:26000]}\n"
                "Return JSON with fields: report_title, report_subtitle, executive_summary, method_steps, issue_tree, sections, insight_cards, charts, references. "
                "Sections must be 7-10 items, each with title, lead, paragraphs, key_takeaways, visual_hint. "
                "Charts must be 5-7 items and must not use pie/donut. References may only use URLs from the evidence pack."
            ),
        },
    ]


def _validate_fact_pack(*, source_count: int, auth_count: int, domains: List[str], facts: List[str], numeric: List[str], dated: List[str]) -> List[str]:
    issues: List[str] = []
    if source_count < 4:
        issues.append(f"资料来源偏少，当前仅{source_count}个。")
    if len(domains) < 3:
        issues.append(f"来源域名多样性不足，当前{len(domains)}个。")
    if auth_count < 1:
        issues.append("缺少政府、交易所、监管、公司公告、国际组织或其他权威来源。")
    if len(facts) < 8:
        issues.append("可抽取事实句不足，生成时必须显式说明证据边界。")
    if len(numeric) < 4:
        issues.append("可核验数字事实不足，需继续收集市场规模、财务、交易、产能、份额或时间数据。")
    if len(dated) < 2:
        issues.append("可核验时间线不足，需补充年份、季度、政策发布时间或交易节点。")
    return issues


def _split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；;.!?])\s*|[\r\n]+", text)
    out = []
    for part in parts:
        part = part.strip(" -•\t")
        if 35 <= len(part) <= 420:
            out.append(part)
    return out


def _score_sentence(sentence: str, topic: str) -> int:
    lower = sentence.lower()
    score = 0
    if NUMBER_RE.search(sentence):
        score += 4
    if MONEY_RE.search(sentence):
        score += 3
    if DATE_RE.search(sentence):
        score += 2
    score += sum(2 for kw in EVIDENCE_KEYWORDS if kw.lower() in lower)
    for term in _topic_terms(topic):
        if term and term.lower() in lower:
            score += 3
    if len(sentence) > 320:
        score -= 1
    return score


def _topic_terms(topic: str) -> List[str]:
    raw = re.split(r"[^\w\u4e00-\u9fff]+", topic or "")
    return [x for x in raw if len(x) >= 3][:10]


def _ranked_unique(items: List[tuple[int, str]], limit: int) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for _score, text in sorted(items, key=lambda x: x[0], reverse=True):
        normalized = re.sub(r"\s+", " ", text).strip()
        key = re.sub(r"\W+", "", normalized.lower())[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(normalized[:520])
        if len(out) >= limit:
            break
    return out


def _source_refs(sources: List[SourceDocument], limit: int = 18) -> List[str]:
    refs = []
    for idx, src in enumerate(sources, start=1):
        flag = "authoritative" if _is_authoritative(src.url) else "supplement"
        refs.append(f"[Source {idx}][{flag}][{src.source_type}] {src.title or src.domain} | {src.url}"[:420])
    return _clean_list(refs, limit=limit)


def _is_authoritative(url: str) -> bool:
    domain = _domain(url)
    if not domain:
        return False
    return any(hint in domain for hint in AUTHORITY_DOMAIN_HINTS) or domain.endswith(".gov") or ".gov." in domain


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _clean_list(values: Iterable[str], limit: int = 10) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _report_text(report: Dict[str, Any]) -> str:
    parts = [str(report.get("report_title") or ""), str(report.get("report_subtitle") or "")]
    parts.extend(str(x) for x in _as_list(report.get("executive_summary")))
    for section in _as_list(report.get("sections")):
        if not isinstance(section, dict):
            parts.append(str(section))
            continue
        parts.append(str(section.get("title") or ""))
        parts.append(str(section.get("lead") or ""))
        parts.extend(str(x) for x in _as_list(section.get("paragraphs")))
        parts.extend(str(x) for x in _as_list(section.get("key_takeaways")))
    for chart in _as_list(report.get("charts")):
        if isinstance(chart, dict):
            parts.append(str(chart.get("title") or ""))
            parts.append(str(chart.get("subtitle") or ""))
            parts.append(str(chart.get("caption") or ""))
    return "\n".join(parts)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_generic_title(title: str) -> bool:
    normalized = re.sub(r"^\s*\d+[\.)、]\s*", "", title or "").strip().lower()
    return normalized in GENERIC_SECTION_TITLES or len(normalized) < 12


def _is_generic_chart_title(title: str, categories: List[str]) -> bool:
    normalized = str(title or "").strip().lower()
    if not normalized or len(normalized) < 14:
        return True
    if normalized in GENERIC_CHART_TERMS:
        return True
    cats = [c.strip().lower() for c in categories if c.strip()]
    generic_cats = sum(1 for c in cats if c in GENERIC_CHART_TERMS)
    return bool(cats) and generic_cats >= max(2, len(cats) - 1)


def _references_outside_sources(references: List[Any], source_refs: List[str]) -> List[str]:
    source_urls = _source_urls_from_refs(source_refs)
    bad = []
    for ref in references:
        if not isinstance(ref, dict):
            continue
        url = _normalize_url(str(ref.get("url") or ""))
        if url and source_urls and url not in source_urls:
            bad.append(url)
    return bad


def _source_urls_from_refs(source_refs: List[str]) -> set[str]:
    urls = set()
    for ref in source_refs:
        for url in URL_RE.findall(ref):
            urls.add(_normalize_url(url))
    return {x for x in urls if x}


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _clean_visible_text(value: Any) -> str:
    text = str(value or "")
    for pattern in META_LABEL_PATTERNS:
        text = re.sub(re.escape(pattern), "", text, flags=re.I)
    text = text.replace("…", "")
    text = re.sub(r"\.{3,}", ".", text)
    return re.sub(r"\s+", " ", text).strip()


def _walk_text(value: Any, fn) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = fn(item)
            else:
                _walk_text(item, fn)
    elif isinstance(value, list):
        for idx, item in enumerate(list(value)):
            if isinstance(item, str):
                value[idx] = fn(item)
            else:
                _walk_text(item, fn)


def _fallback_references(source_refs: List[str]) -> List[Dict[str, str]]:
    refs = []
    for idx, ref in enumerate(source_refs[:8], start=1):
        url_match = URL_RE.search(ref)
        url = url_match.group(0).rstrip("/") if url_match else ""
        title = ref.split("|", 1)[0].strip(" []")
        refs.append({"title": title or f"Source {idx}", "url": url, "note": "Evidence source retained in source backup."})
    return refs


def _supplement_paragraphs(fact_pack: ResearchFactPack, *, language: str, needed: int) -> List[str]:
    evidence = fact_pack.high_confidence_facts[: max(1, needed)]
    out = []
    for fact in evidence:
        if language == "zh":
            out.append(f"该判断需要回到公开资料边界内复核。资料包中的可核验线索包括：{fact}。报告不应在该证据之外补充未披露事实。")
        else:
            out.append(f"This point should be read within the public-evidence boundary. The retained evidence pack includes: {fact}. The report should not add undisclosed facts beyond that source base.")
    while len(out) < needed:
        out.append("The source backup should be used to validate this section before it is used for a decision." if language == "en" else "该章节应结合来源底稿继续复核后再用于决策。")
    return out[:needed]


def _shorten(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."


def _has_cjk_alnum_space(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]\s+[A-Za-z0-9]|[A-Za-z0-9]\s+[\u4e00-\u9fff]", text or ""))


def _mentions_evidence_boundary(text: str, *, language: str) -> bool:
    lower = str(text or "").lower()
    if language == "zh":
        return any(token in text for token in ("公开资料", "证据边界", "来源底稿", "未披露", "需复核"))
    return any(token in lower for token in ("public evidence", "evidence boundary", "source backup", "not sufficient", "independently validated", "public-source"))


def _repeated_paragraph_openings(sections: List[Any]) -> List[str]:
    counts: Dict[str, int] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        for para in _as_list(section.get("paragraphs")):
            opening = re.sub(r"^\W+", "", str(para).strip())[:18].lower()
            if len(opening) >= 8:
                counts[opening] = counts.get(opening, 0) + 1
    return [opening for opening, count in counts.items() if count >= 3]
