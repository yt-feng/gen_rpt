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
    "mckinsey",
    "10 tests",
    "ten tests",
    "strategic ten questions",
    "战略十问",
    "bcg-style",
    "consulting-style",
    "sample card",
    "model-proposed",
    "chart qa",
    "quality-control synthesis",
    "topic-neutral strategic index",
    "converted to a bar exhibit",
    "values normalized",
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

BUSINESS_LENS_TERMS = (
    "revenue",
    "profit",
    "margin",
    "cost",
    "roi",
    "return",
    "capital",
    "allocation",
    "investment",
    "cash flow",
    "pricing",
    "market share",
    "customer",
    "commercial",
    "business model",
    "financing",
    "risk",
    "management",
    "board",
    "ceo",
    "decision",
    "action",
    "scenario",
    "priority",
    "value",
    "competitive advantage",
    "moat",
    "budget",
    "payback",
    "option",
    "partnership",
    "regulation",
    "due diligence",
    "governance",
    "execution",
    "收入",
    "营收",
    "利润",
    "毛利",
    "成本",
    "投资回报",
    "资本",
    "现金流",
    "定价",
    "市场份额",
    "客户",
    "商业",
    "融资",
    "风险",
    "管理层",
    "董事会",
    "决策",
    "行动",
    "情景",
    "优先级",
    "价值",
    "竞争优势",
    "预算",
    "回收期",
    "选择权",
    "合作",
    "监管",
    "尽调",
    "治理",
    "执行",
)

TECHNICAL_EXPLAINER_TERMS = (
    "architecture",
    "protocol",
    "algorithm",
    "dataset",
    "parameter",
    "benchmark",
    "model",
    "stack",
    "api",
    "latency",
    "efficiency",
    "specification",
    "material",
    "membrane",
    "electrolyte",
    "reactor",
    "energy density",
    "技术",
    "架构",
    "协议",
    "算法",
    "数据集",
    "参数",
    "模型",
    "效率",
    "规格",
    "材料",
    "电解液",
    "反应堆",
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
    executive_summary_text = str(report.get("executive_summary_text") or "").strip()
    key_findings = _as_list(report.get("key_findings"))
    action_plan = _as_list(report.get("action_plan"))
    risk_register = _as_list(report.get("risk_register"))
    scenario_vignettes = _as_list(report.get("scenario_vignettes"))
    methodology_note = str(report.get("methodology_note") or "").strip()
    author_credentials = _as_list(report.get("author_credentials"))
    charts = _as_list(report.get("charts"))
    references = _as_list(report.get("references"))

    if fact_pack.validation_issues and not _mentions_evidence_boundary(text, language=language):
        issues.append("资料包仍有待核验事项，报告需要显式标注证据边界并避免补充未披露事实：" + "；".join(fact_pack.validation_issues[:4]))
    if len(summary) < 5:
        issues.append("executive_summary 不足，需要至少5条结论先行、可执行的关键发现。")
    if len(executive_summary_text) < (220 if language == "en" else 120):
        issues.append("executive_summary_text 不足，需要一段 CEO 可直接阅读的结构化执行摘要，先给结论、商业含义、风险和下一步。")
    if len(key_findings) < 4:
        issues.append("key_findings 不足，需要至少4条总括性关键发现，并说明证据和管理含义。")
    if len(action_plan) < 3:
        issues.append("action_plan 不足，需要至少3条行动，覆盖短期、中期、长期或 no-regret/options/big-bet 节奏。")
    else:
        action_text = " ".join(_item_text(x) for x in action_plan).lower()
        horizon_hits = sum(1 for token in ("short", "near", "0-90", "30", "90", "mid", "medium", "long", "quarter", "短期", "近期", "中期", "长期", "季度") if token in action_text)
        if horizon_hits < 2:
            issues.append("action_plan 缺少明确时间节奏，需要写出近期/中期/长期或季度化推进安排。")
    if len(risk_register) < 4:
        issues.append("risk_register 不足，需要至少4条风险/不确定性，并包含触发信号和管理动作。")
    else:
        weak_risks = [idx for idx, item in enumerate(risk_register, start=1) if not _risk_has_trigger_and_action(item)]
        if weak_risks:
            issues.append("risk_register 中部分条目缺少 trigger/management_action/mitigation：" + "、".join(str(x) for x in weak_risks[:4]))
    if not scenario_vignettes:
        issues.append("缺少 scenario_vignettes，需要至少1个业务场景 vignette，把结论落到 CEO 决策场景。")
    if len(methodology_note) < (90 if language == "en" else 45):
        issues.append("缺少 methodology_note，需要说明公开资料、证据边界、交叉校验和未核验缺口。")
    if not author_credentials:
        issues.append("缺少 author_credentials，需要在正式报告中给出团队/机构能力说明。")
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
        long_paras = [p for p in paragraphs if len(p) > (820 if language == "en" else 520)]
        if long_paras:
            issues.append(f"第{idx}章存在过长段落，需要拆成更适合正式报告排版和扫读的短段。")
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
    duplicates = _duplicate_paragraphs(sections)
    if duplicates:
        issues.append("章节之间存在重复段落，需要合并或改写，避免跨页重复：" + "、".join(duplicates[:4]))
    business_hits = _keyword_hits(text, BUSINESS_LENS_TERMS)
    technical_hits = _keyword_hits(text, TECHNICAL_EXPLAINER_TERMS)
    if business_hits < (18 if language == "en" else 10):
        issues.append(f"CEO/商业视角不足，当前商业决策词约{business_hits}处；需要把技术事实翻译成收入、成本、投资回报、风险、行动和资源配置含义。")
    if technical_hits > max(18, business_hits * 2):
        issues.append("技术解释密度过高，报告读感接近百科说明；需要压缩技术原理，强化商业价值、投资回报、风险和行动含义。")
    return issues[:40]


def apply_deterministic_report_fixes(report: Dict[str, Any], fact_pack: ResearchFactPack, *, language: str = "en") -> Dict[str, Any]:
    fixed = json.loads(json.dumps(report, ensure_ascii=False))
    _walk_text(fixed, _clean_visible_text)
    topic = str(fixed.get("report_title") or fact_pack.topic or "the topic")
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
    fixed["executive_summary"] = _ensure_summary_items(fixed.get("executive_summary"), fact_pack, language=language)
    fixed["executive_summary_text"] = _ensure_executive_summary_text(fixed, fact_pack, language=language)
    fixed["key_findings"] = _ensure_key_findings(fixed.get("key_findings"), fixed["executive_summary"], fact_pack, language=language)
    fixed["action_plan"] = _ensure_action_plan(fixed.get("action_plan"), topic, fact_pack, language=language)
    fixed["risk_register"] = _ensure_risk_register(fixed.get("risk_register"), fact_pack, language=language)
    fixed["scenario_vignettes"] = _ensure_scenario_vignettes(fixed.get("scenario_vignettes"), topic, language=language)
    fixed["methodology_note"] = _ensure_methodology_note(fixed.get("methodology_note"), fact_pack, language=language)
    fixed["author_credentials"] = _ensure_author_credentials(fixed.get("author_credentials"), language=language)

    sections = []
    for idx, section in enumerate(_as_list(fixed.get("sections")), start=1):
        if not isinstance(section, dict):
            section = {"title": str(section), "lead": "", "paragraphs": [str(section)]}
        section["id"] = str(section.get("id") or f"section-{idx}")
        section["visual_hint"] = str(section.get("visual_hint") or f"image-{idx}")
        paragraphs = [str(x).strip() for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        if len(paragraphs) < 3:
            paragraphs.extend(_supplement_paragraphs(fact_pack, language=language, needed=3 - len(paragraphs)))
        paragraphs = _split_long_paragraphs(_dedupe_texts(paragraphs), language=language)
        section["paragraphs"] = paragraphs[:8]
        if not str(section.get("lead") or "").strip() and paragraphs:
            section["lead"] = _shorten(paragraphs[0], 220)
        sections.append(section)
    fixed["sections"] = _ensure_sections(sections, fixed["executive_summary"], topic, fact_pack, language=language)
    _vary_section_paragraph_openings(fixed["sections"], language=language)
    fixed["charts"] = _ensure_charts(fixed.get("charts"), fixed["sections"], topic, language=language)
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
    schema = (
        "report_title, report_subtitle, executive_summary, executive_summary_text, key_findings, "
        "action_plan, risk_register, scenario_vignettes, methodology_note, author_credentials, "
        "method_steps, issue_tree, sections, insight_cards, charts, references"
    )
    executive_lens = (
        "Use an internal executive strategy stress test before writing: market outperformance, true advantage, "
        "granular where-to-play choices, trend timing, privileged evidence, uncertainty handling, commitment versus flexibility, "
        "bias checks, conviction to act, and translation into an action plan. Do not name or expose this framework in the report."
        if language == "en"
        else "写作前用内部高管战略压力测试检查：市场竞胜、真实优势、竞争场景颗粒度、趋势时点、独到证据、不确定性、承诺与灵活性、偏见、执行决心、行动落地。正式报告不得展示或命名该框架。"
    )
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
                f"{executive_lens}\n"
                "Write for a CEO/board reader: every technical point must become a commercial implication, investment-return implication, risk, or decision/action. "
                "Use placeholders such as [insert verified data] only when the evidence pack does not support a number; do not invent facts.\n"
                f"Return JSON with fields: {schema}. "
                "Sections must be 7-10 items, each with title, lead, paragraphs, key_takeaways, visual_hint. "
                "key_findings must contain finding, evidence, management_implication. "
                "action_plan must contain horizon, action, owner, success_metric, decision_gate. "
                "risk_register must contain risk, trigger, management_action, evidence_boundary. "
                "scenario_vignettes must contain title, situation, ceo_question, recommended_move, watchouts. "
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
    parts.append(str(report.get("executive_summary_text") or ""))
    parts.extend(str(x) for x in _as_list(report.get("executive_summary")))
    for key in ("key_findings", "action_plan", "risk_register", "scenario_vignettes", "author_credentials"):
        parts.extend(_item_text(x) for x in _as_list(report.get(key)))
    parts.append(str(report.get("methodology_note") or ""))
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


def _ensure_summary_items(value: Any, fact_pack: ResearchFactPack, *, language: str) -> List[str]:
    items = _dedupe_texts([str(x).strip() for x in _as_list(value) if str(x).strip()])
    fallback = _fallback_summary_items(fact_pack, language=language)
    items = _dedupe_texts(items + fallback)
    return items[:8]


def _fallback_summary_items(fact_pack: ResearchFactPack, *, language: str) -> List[str]:
    evidence = fact_pack.high_confidence_facts[:3]
    if language == "zh":
        items = [
            f"{fact_pack.topic}的核心判断应以公开证据边界为准，管理层需要先区分已验证事实、方向性判断和待补充数据。",
            "CEO 视角下，报告应优先回答资源配置、客户价值、投资回报、风险暴露和下一步行动，而不是展开技术百科。",
            "缺少可核验数字时，不应补写估算；应把市场规模、成本、收入、份额和时间线列为后续核验清单。",
            "竞争优势需要落到真实优势来源、客户采纳门槛、供应链韧性、融资可得性和执行节奏。",
            "下一步应把结论转成短期无悔动作、中期选择权和长期重大投入的分层计划。",
        ]
        items.extend(f"资料包中的可核验线索：{fact}。" for fact in evidence)
        return items
    items = [
        f"The CEO-level question is not whether {fact_pack.topic} is interesting, but which decisions can be made now inside the public-evidence boundary.",
        "Management should separate verified facts, directional scenarios and missing diligence items before committing capital, partnerships or market-entry resources.",
        "The report should translate technical evidence into revenue potential, cost position, investment return, execution risk and decision gates.",
        "Where the evidence pack does not support a number, the right output is a validation placeholder and diligence task rather than an invented estimate.",
        "The near-term agenda should prioritize no-regret evidence building, medium-term strategic options and long-term commitments only after decision gates are met.",
    ]
    items.extend(f"Retained evidence signal: {fact}." for fact in evidence)
    return items


def _ensure_executive_summary_text(report: Dict[str, Any], fact_pack: ResearchFactPack, *, language: str) -> str:
    existing = str(report.get("executive_summary_text") or "").strip()
    minimum = 220 if language == "en" else 120
    if len(existing) >= minimum:
        return existing
    summary = _as_list(report.get("executive_summary"))[:4]
    if language == "zh":
        joined = "；".join(str(x).strip() for x in summary if str(x).strip())
        evidence_note = "公开资料仍存在待核验缺口，报告把这些缺口保留为后续尽调事项，而不是补写未经来源支持的数字。"
        action_note = "CEO 应把本报告作为资源配置底稿：先确认哪些判断足以支持近期动作，再把高不确定性事项转成季度化验证门槛。"
        return _clean_visible_text(f"{joined}。{evidence_note}{action_note}")
    joined = " ".join(str(x).strip() for x in summary if str(x).strip())
    evidence_note = "The analysis remains bounded by public evidence and source backup; unsupported numbers should be treated as diligence gaps, not conclusions."
    action_note = "For a CEO or board reader, the practical implication is to fund no-regret validation, preserve options where uncertainty is high, and reserve larger commitments for verified decision gates."
    return _clean_visible_text(f"{joined} {evidence_note} {action_note}")


def _ensure_key_findings(value: Any, summary: List[str], fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            finding = str(item.get("finding") or item.get("title") or item.get("text") or "").strip()
            evidence = str(item.get("evidence") or item.get("source") or "").strip()
            implication = str(item.get("management_implication") or item.get("implication") or "").strip()
        else:
            finding = str(item).strip()
            evidence = ""
            implication = ""
        if finding:
            findings.append({
                "finding": finding,
                "evidence": evidence or _default_evidence_note(fact_pack, language=language),
                "management_implication": implication or _default_implication(language=language),
            })
    for item in summary:
        if len(findings) >= 5:
            break
        findings.append({
            "finding": str(item),
            "evidence": _default_evidence_note(fact_pack, language=language),
            "management_implication": _default_implication(language=language),
        })
    while len(findings) < 4:
        findings.append({
            "finding": "Evidence quality should define the pace of management commitment." if language == "en" else "证据质量应决定管理层投入节奏。",
            "evidence": _default_evidence_note(fact_pack, language=language),
            "management_implication": _default_implication(language=language),
        })
    return findings[:6]


def _ensure_action_plan(value: Any, topic: str, fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, str]]:
    actions: List[Dict[str, str]] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            action = str(item.get("action") or item.get("initiative") or item.get("move") or "").strip()
            if not action:
                continue
            actions.append({
                "horizon": str(item.get("horizon") or item.get("timing") or "").strip(),
                "action": action,
                "owner": str(item.get("owner") or "").strip(),
                "success_metric": str(item.get("success_metric") or item.get("metric") or "").strip(),
                "decision_gate": str(item.get("decision_gate") or item.get("gate") or "").strip(),
            })
        elif str(item).strip():
            actions.append({"horizon": "", "action": str(item).strip(), "owner": "", "success_metric": "", "decision_gate": ""})
    defaults = _default_action_plan(topic, fact_pack, language=language)
    actions.extend(defaults)
    normalized = []
    seen = set()
    for idx, action in enumerate(actions, start=1):
        key = re.sub(r"\W+", "", action["action"].lower())[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        default = defaults[min(idx - 1, len(defaults) - 1)]
        normalized.append({
            "horizon": action.get("horizon") or default["horizon"],
            "action": action.get("action") or default["action"],
            "owner": action.get("owner") or default["owner"],
            "success_metric": action.get("success_metric") or default["success_metric"],
            "decision_gate": action.get("decision_gate") or default["decision_gate"],
        })
        if len(normalized) >= 5:
            break
    return normalized


def _default_action_plan(topic: str, fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, str]]:
    if language == "zh":
        return [
            {"horizon": "近期 0-90 天", "action": f"围绕{topic}建立证据台账，优先复核市场规模、客户需求、成本、收入和关键时间线。", "owner": "战略负责人/研究负责人", "success_metric": "关键判断均绑定来源、日期和待核验缺口", "decision_gate": "缺口关闭前不把未经支持的数字写成结论"},
            {"horizon": "中期 1-2 个季度", "action": "把机会拆成无悔动作、可保留选择权和需要重大资源承诺的事项。", "owner": "CEO/业务负责人", "success_metric": "每个行动对应预算、负责人和验证指标", "decision_gate": "客户、成本、政策或融资证据达到阈值后再扩大投入"},
            {"horizon": "长期 2-4 个季度", "action": "在证据达到门槛后推进合作、投资或市场进入，并建立季度复盘机制。", "owner": "CEO/董事会", "success_metric": "投资回报、风险暴露和执行里程碑进入管理层仪表盘", "decision_gate": "若关键假设未被验证，则保留选择权并暂停重大投入"},
        ]
    return [
        {"horizon": "Near term, 0-90 days", "action": f"Build an evidence ledger for {topic}, prioritizing market size, customer demand, cost, revenue, timing and source quality.", "owner": "Strategy lead / research lead", "success_metric": "Every material claim is tied to a source, date and open validation gap.", "decision_gate": "Do not convert unsupported numbers into conclusions until the gap is closed."},
        {"horizon": "Medium term, 1-2 quarters", "action": "Separate no-regret moves, strategic options and resource-heavy commitments.", "owner": "CEO / business owner", "success_metric": "Each move has a budget, owner and validation metric.", "decision_gate": "Scale commitment only after customer, cost, policy or financing evidence reaches threshold."},
        {"horizon": "Long term, 2-4 quarters", "action": "Advance partnerships, investments or market entry only after evidence gates are met, with quarterly review cadence.", "owner": "CEO / board", "success_metric": "ROI, risk exposure and execution milestones appear on the management dashboard.", "decision_gate": "If core assumptions remain unverified, preserve options and pause major capital commitment."},
    ]


def _ensure_risk_register(value: Any, fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, str]]:
    risks: List[Dict[str, str]] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            risk = str(item.get("risk") or item.get("uncertainty") or item.get("title") or "").strip()
            if not risk:
                continue
            risks.append({
                "risk": risk,
                "trigger": str(item.get("trigger") or item.get("signal") or "").strip(),
                "management_action": str(item.get("management_action") or item.get("mitigation") or item.get("action") or "").strip(),
                "evidence_boundary": str(item.get("evidence_boundary") or item.get("evidence") or "").strip(),
            })
        elif str(item).strip():
            risks.append({"risk": str(item).strip(), "trigger": "", "management_action": "", "evidence_boundary": ""})
    defaults = _default_risk_register(fact_pack, language=language)
    risks.extend(defaults)
    normalized = []
    seen = set()
    for idx, risk in enumerate(risks, start=1):
        key = re.sub(r"\W+", "", risk["risk"].lower())[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        default = defaults[min(idx - 1, len(defaults) - 1)]
        normalized.append({
            "risk": risk.get("risk") or default["risk"],
            "trigger": risk.get("trigger") or default["trigger"],
            "management_action": risk.get("management_action") or default["management_action"],
            "evidence_boundary": risk.get("evidence_boundary") or default["evidence_boundary"],
        })
        if len(normalized) >= 6:
            break
    return normalized


def _default_risk_register(fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, str]]:
    boundary = _default_evidence_note(fact_pack, language=language)
    if language == "zh":
        return [
            {"risk": "公开资料不足导致判断过度确定", "trigger": "来源数量、权威来源、数字事实或时间线不足", "management_action": "把结论分级为已验证、方向性和待核验，并补充权威来源", "evidence_boundary": boundary},
            {"risk": "投资或进入节奏早于商业证据", "trigger": "客户、收入、成本、融资或监管证据尚未达到管理层门槛", "management_action": "先保留选择权和小额验证，暂缓重大资本承诺", "evidence_boundary": boundary},
            {"risk": "技术叙事掩盖商业回报", "trigger": "正文技术词密度高于收入、成本、风险和行动词", "management_action": "将技术段落重写为客户价值、成本位置、投资回报和执行含义", "evidence_boundary": boundary},
            {"risk": "关键假设缺少反例校验", "trigger": "没有竞争对手、替代方案、政策逆风或需求下行情景", "management_action": "建立反例清单，并把反例作为季度复盘事项", "evidence_boundary": boundary},
        ]
    return [
        {"risk": "Public evidence is too thin for high-conviction decisions.", "trigger": "Source count, authoritative sources, numeric facts or timelines are below threshold.", "management_action": "Classify claims as verified, directional or open diligence and add authoritative sources.", "evidence_boundary": boundary},
        {"risk": "Investment or market-entry pace runs ahead of commercial proof.", "trigger": "Customer, revenue, cost, financing or regulatory evidence has not met management threshold.", "management_action": "Preserve options and run low-cost validation before major capital commitment.", "evidence_boundary": boundary},
        {"risk": "Technical narrative obscures business return.", "trigger": "Technical terminology outweighs revenue, cost, risk and action implications.", "management_action": "Rewrite technical sections into customer value, cost position, ROI and execution implications.", "evidence_boundary": boundary},
        {"risk": "Key assumptions are not tested against counterevidence.", "trigger": "No explicit competitor, substitute, policy-downside or demand-downside challenge is present.", "management_action": "Create a counterevidence checklist and review it quarterly.", "evidence_boundary": boundary},
    ]


def _ensure_scenario_vignettes(value: Any, topic: str, *, language: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title,
                "situation": str(item.get("situation") or item.get("context") or "").strip(),
                "ceo_question": str(item.get("ceo_question") or item.get("question") or "").strip(),
                "recommended_move": str(item.get("recommended_move") or item.get("move") or item.get("action") or "").strip(),
                "watchouts": str(item.get("watchouts") or item.get("risk") or "").strip(),
            })
        elif str(item).strip():
            out.append({"title": str(item).strip(), "situation": "", "ceo_question": "", "recommended_move": "", "watchouts": ""})
    if out:
        default = _default_scenario_vignette(topic, language=language)
        for item in out:
            for key, value_default in default.items():
                item[key] = item.get(key) or value_default
        return out[:3]
    return [_default_scenario_vignette(topic, language=language)]


def _default_scenario_vignette(topic: str, *, language: str) -> Dict[str, str]:
    if language == "zh":
        return {
            "title": "CEO 投资委员会场景",
            "situation": f"管理层正在判断是否围绕{topic}投入预算、合作资源或市场进入资源，但公开资料仍有若干关键缺口。",
            "ceo_question": "哪些判断已经足以支持近期动作，哪些必须先完成核验才能进入重大承诺？",
            "recommended_move": "先批准低成本验证和关键客户/成本/政策核验，同时把重大投入放在证据门槛之后。",
            "watchouts": "不要把技术领先、市场热度或单一来源数字直接等同于可融资、可盈利或可规模化。",
        }
    return {
        "title": "CEO investment committee scenario",
        "situation": f"Management is deciding whether to allocate budget, partnership capacity or market-entry resources to {topic}, while several public-evidence gaps remain open.",
        "ceo_question": "Which claims are strong enough to support near-term action, and which must be validated before a major commitment?",
        "recommended_move": "Approve low-cost validation and targeted customer, cost and policy diligence first; hold larger commitments behind evidence gates.",
        "watchouts": "Do not treat technical leadership, market buzz or a single-source number as proof of bankability, profitability or scalability.",
    }


def _ensure_methodology_note(value: Any, fact_pack: ResearchFactPack, *, language: str) -> str:
    existing = str(value or "").strip()
    if len(existing) >= (90 if language == "en" else 45):
        return existing
    if language == "zh":
        return (
            f"本报告基于公开网页、PDF、公告或研究资料形成来源底稿，并在生成前抽取事实包。"
            f"当前共纳入{fact_pack.source_count}个来源、{fact_pack.authoritative_source_count}个权威来源；"
            "正文只在来源支持范围内使用数字、日期和事件，未被公开资料支持的判断保留为待核验缺口。"
        )
    return (
        "This report is based on public web, PDF, filing or research sources retained in the source backup and distilled into a pre-generation fact pack. "
        f"The current pack includes {fact_pack.source_count} sources and {fact_pack.authoritative_source_count} authoritative sources. "
        "Numbers, dates and events are used only inside that evidence boundary; unsupported claims are retained as validation gaps."
    )


def _ensure_author_credentials(value: Any, *, language: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("team") or "").strip()
            role = str(item.get("role") or "").strip()
            credentials = str(item.get("credentials") or item.get("description") or "").strip()
        else:
            name = str(item).strip()
            role = ""
            credentials = ""
        if name:
            out.append({"name": name, "role": role or ("Research synthesis team" if language == "en" else "研究综合团队"), "credentials": credentials or _default_credentials(language=language)})
    if out:
        return out[:4]
    if language == "zh":
        return [{"name": "BlueOcean Research", "role": "研究综合团队", "credentials": "负责公开资料收集、证据边界校验、管理层视角综合和报告排版 QA。"}]
    return [{"name": "BlueOcean Research", "role": "Research synthesis team", "credentials": "Responsible for public-source collection, evidence-boundary checks, executive synthesis and report layout QA."}]


def _ensure_sections(sections: List[Dict[str, Any]], summary: List[str], topic: str, fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    blueprints = _fallback_section_blueprints(topic, fact_pack, language=language)
    for idx, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if _is_generic_title(title):
            title = _fallback_section_title(idx, blueprints, topic, fact_pack, language=language)
        key = re.sub(r"\W+", "", title.lower())[:120]
        if not title or key in seen:
            continue
        seen.add(key)
        normalized = dict(section)
        normalized["title"] = title
        normalized["id"] = str(normalized.get("id") or f"section-{len(out) + 1}")
        normalized["visual_hint"] = str(normalized.get("visual_hint") or f"image-{len(out) + 1}")
        paragraphs = [str(x).strip() for x in _as_list(normalized.get("paragraphs")) if str(x).strip()]
        if len(paragraphs) < 3:
            paragraphs.extend(_supplement_paragraphs(fact_pack, language=language, needed=3 - len(paragraphs)))
        normalized["paragraphs"] = _split_long_paragraphs(_dedupe_texts(paragraphs), language=language)[:5]
        lead = str(normalized.get("lead") or "").strip()
        if len(lead) < 40 or _is_generic_title(lead):
            normalized["lead"] = _derive_section_lead(title, normalized["paragraphs"], language=language)
        normalized["key_takeaways"] = _ensure_takeaways(normalized.get("key_takeaways"), summary, len(out), language=language)
        out.append(normalized)
        if len(out) >= 10:
            break

    for blueprint in blueprints:
        if len(out) >= 8:
            break
        key = re.sub(r"\W+", "", blueprint["title"].lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        idx = len(out) + 1
        section = dict(blueprint)
        section["id"] = f"section-{idx}"
        section["visual_hint"] = f"image-{idx}"
        section["key_takeaways"] = _ensure_takeaways(section.get("key_takeaways"), summary, idx - 1, language=language)
        out.append(section)
    return out[:10]


def _fallback_section_title(idx: int, blueprints: List[Dict[str, Any]], topic: str, fact_pack: ResearchFactPack, *, language: str) -> str:
    if blueprints:
        return str(blueprints[(idx - 1) % len(blueprints)].get("title") or "").strip()
    topic_text = str(topic or fact_pack.topic or "the topic").strip()
    if language == "zh":
        return f"{topic_text}应转化为分阶段管理判断"
    return f"{topic_text} should be translated into staged management decisions"


def _fallback_section_blueprints(topic: str, fact_pack: ResearchFactPack, *, language: str) -> List[Dict[str, Any]]:
    topic_text = str(topic or fact_pack.topic or "the topic").strip()
    evidence_note = _default_evidence_note(fact_pack, language=language)
    if language == "zh":
        return [
            _section_payload(f"{topic_text}应被视为分阶段战略选择，而不是一次性押注", "CEO 需要先判断哪些动作现在足够安全，哪些必须等待证据门槛关闭。", [
                f"围绕{topic_text}的管理判断，应先区分已由公开资料支持的事实、方向性情景和仍需补充的尽调缺口。这个边界能防止报告把市场热度或技术叙事直接写成投资结论。",
                "对董事会而言，更重要的问题不是机会是否存在，而是近期是否值得投入预算、合作资源或管理注意力。低成本验证可以先做，重大资本承诺应等待客户、成本、融资、监管或运营证据达到门槛。",
                f"当前证据口径为：{evidence_note} 因此本章建议把该议题纳入季度化管理仪表盘，而不是一次性定性通过或否决。",
            ], ["先做低成本验证", "把重大投入放在证据门槛之后", "用公开资料边界约束结论"]),
            _section_payload("商业就绪度取决于可融资、可交付、可复购的证明", "技术可行性只有转化为客户价值、成本位置和交付能力，才会改变资源配置。", [
                "商业就绪度应从客户采用、成本结构、交付周期、服务能力和融资可得性共同判断。单一技术指标无法说明项目是否可融资，也无法证明客户是否愿意长期复购。",
                "管理层应要求每个关键假设都对应可验证证据：目标客户、采购触发点、预算来源、替代方案、使用场景和项目回收路径。缺少这些证据时，报告只能给出方向性判断。",
                "更稳健的推进方式是用试点、合作和第三方验证建立可信证明，再决定是否进入更大规模投入。",
            ], ["客户价值比技术叙事更能改变决策", "先验证可融资性和可交付性", "用试点证明可复购"]),
            _section_payload("成本、回报和时间窗口决定真正的部署节奏", "如果成本和回报口径不能被验证，市场规模判断就不应进入投资结论。", [
                "任何市场机会最终都会回到成本、收入、利润、现金流和时间窗口。若公开资料无法支持市场规模、ROI、单位经济性或份额数字，应把这些信息列入后续核验任务。",
                "短期管理动作应聚焦可验证的成本驱动因素和客户支付意愿，而不是依赖远期乐观情景。这样可以保留战略选择权，同时降低过早承诺的风险。",
                "当成本、客户和融资证据逐步清晰后，管理层再把资源从观察和合作转向试点或规模化投入。",
            ], ["成本和 ROI 是下一步核验重点", "时间窗口应随证据更新", "避免用远期情景替代近期证明"]),
            _section_payload("监管、政策和公共接受度会改变机会的到达速度", "外部规则既可能加速采用，也可能重置项目排期和风险预算。", [
                "政策支持、审批路径、行业标准和公共接受度都会影响机会从概念走向部署的速度。管理层应把这些因素作为行动门槛，而不是正文背景。",
                "如果监管路径不清晰，最合适的动作通常是参与标准讨论、建立政策跟踪和做低风险试点，而不是提前进行重资产押注。",
                "董事会应关注哪些政策事件会改变投资节奏，例如许可规则、补贴口径、采购要求、地方审批或安全标准更新。",
            ], ["政策节点是关键外部信号", "监管不清时先保留选择权", "公共接受度影响项目节奏"]),
            _section_payload("供应链、伙伴和人才决定谁能先把机会落地", "战略优势来自生态位，而不是单点产品能力。", [
                "在不确定市场中，先发优势往往来自供应链锁定、伙伴进入、人才储备和早期客户学习，而不是单纯的技术叙事。",
                "管理层应识别哪些能力稀缺且可提前布局：关键供应、工程交付、渠道伙伴、服务网络、融资合作和合规能力。低成本获取这些学习权，通常优于等待市场完全明朗。",
                "合作选择应服务于可观察验证点。只产生新闻稿但无法带来客户、成本或交付证据的合作，不应被视为高质量进展。",
            ], ["稀缺能力应提前布局", "伙伴关系要产生验证点", "生态位比单点能力更重要"]),
            _section_payload("既有业务应保护近期经济性，同时保留长期选择权", "最稳健的战略不是激进押注，也不是被动观望。", [
                "对既有业务而言，核心任务是在保护近期现金流和客户关系的同时，避免错失长期转折点。这个平衡需要明确哪些动作是无悔动作，哪些只是期权，哪些属于重大投入。",
                "无悔动作包括证据台账、客户访谈、政策跟踪、供应链扫描和小额合作。期权动作包括试点、优先合作权和少量投资。重大投入则应等待更强证据。",
                "这种分层能让组织靠近机会，但不会在证据不足时锁死战略和资本。",
            ], ["分层配置资源", "保护近期业务经济性", "用选择权管理不确定性"]),
            _section_payload("下一步管理议程应落到季度证据门槛", "报告价值应体现在行动节奏、负责人和复盘机制上。", [
                "本报告最终应转化为季度化管理议程：每个关键判断都要有负责人、证据门槛、复核时间和升级条件。",
                "短期重点是补来源、补客户、补成本和补时间线；中期重点是试点和伙伴；长期才是资本、并购或大规模进入。",
                "如果下一轮证据没有改善，管理层应维持观察和低成本选择权；如果关键指标达标，再把议题升级为投资委员会事项。",
            ], ["建立季度证据门槛", "明确负责人和复盘节奏", "用证据触发升级"]),
            _section_payload("需要持续跟踪的信号不是热度，而是能改变决策的事实", "真正的监控清单应聚焦客户、成本、监管、竞争和融资。", [
                "管理层应避免把媒体热度、融资新闻或单点技术声明直接当作决策信号。更有价值的信号是客户采购、成本下降、监管明确、竞争对手动作和融资条件变化。",
                "每个信号都应对应明确行动：继续观察、启动试点、扩大合作、暂停投入或升级到董事会。",
                "这种信号体系能把不确定性变成可管理的节奏，而不是让组织在乐观叙事和保守观望之间摇摆。",
            ], ["跟踪能改变决策的事实", "把信号绑定行动", "避免被市场热度牵引"]),
        ]
    return [
        _section_payload(f"{topic_text} should be managed as a staged strategic option, not a binary bet", "The CEO question is which moves are safe now and which should wait for stronger evidence.", [
            f"For leadership, {topic_text} should be separated into verified facts, directional scenarios and open diligence items. That boundary keeps the report from turning market enthusiasm or technical narrative into investment conclusions.",
            "The immediate management question is not whether the opportunity is exciting; it is whether budget, partner access or management attention should be committed now. Low-cost validation can start early, while larger capital commitments should wait for customer, cost, financing, regulatory or operating proof.",
            f"The current evidence posture is: {evidence_note} This makes a quarterly decision cadence more useful than a one-time yes-or-no judgment.",
        ], ["Start with low-cost validation", "Hold major commitments behind evidence gates", "Keep conclusions inside the public-evidence boundary"]),
        _section_payload("Commercial readiness depends on bankability, deliverability and repeat customer proof", "Technical feasibility matters only when it changes customer value, cost position and execution confidence.", [
            "Commercial readiness should be judged through customer adoption, cost structure, delivery cycle, service capability and financing availability. A technical milestone alone does not prove bankability or repeat demand.",
            "Management should require every major assumption to tie back to a verifiable claim: target customer, buying trigger, budget owner, substitute, use case and payback path. Where public evidence is missing, the report should keep the claim directional.",
            "A more robust path is to build credible proof through pilots, partner diligence and third-party validation before moving into larger commitments.",
        ], ["Customer value changes decisions more than technical narrative", "Validate bankability and delivery risk first", "Use pilots to prove repeatability"]),
        _section_payload("Cost, return and timing will determine the real deployment window", "Market size should not enter an investment case until the cost and return logic can be checked.", [
            "Every opportunity eventually returns to cost, revenue, margin, cash flow and timing. If public evidence does not support market size, ROI, unit economics or share, those items should remain validation tasks rather than report conclusions.",
            "Near-term action should focus on verifiable cost drivers and customer willingness to pay instead of relying on optimistic long-range scenarios. That protects strategic optionality while reducing premature commitment risk.",
            "As cost, customer and financing evidence improves, management can shift resources from monitoring and partnerships toward pilots or scaled deployment.",
        ], ["Cost and ROI are priority diligence items", "Timing should move with evidence quality", "Avoid using long-range scenarios as near-term proof"]),
        _section_payload("Regulation, policy and public acceptance can reset the speed of adoption", "External rules can accelerate the market or change the risk budget and project timeline.", [
            "Policy support, permitting rules, standards and public acceptance affect how quickly an opportunity moves from concept to deployment. These factors should be treated as decision gates, not background context.",
            "Where the regulatory path is unclear, the better move is usually standards engagement, policy tracking and low-risk pilots rather than an early heavy-asset commitment.",
            "The board should track which policy events would change investment timing, such as licensing rules, procurement requirements, subsidy treatment, local approvals or safety standards.",
        ], ["Policy events are external decision gates", "Use options while regulation remains unclear", "Public acceptance can alter project timing"]),
        _section_payload("Supply chain, partners and talent will decide who can act before the market is obvious", "Strategic advantage comes from ecosystem position rather than standalone product capability.", [
            "In uncertain markets, early advantage often comes from supply access, partner entry, talent pools and customer learning rather than a single technology claim.",
            "Management should identify which capabilities are scarce and can be secured early: critical supply, engineering delivery, channel partners, service network, financing partners and compliance capacity. Low-cost learning rights can be more valuable than waiting for full market clarity.",
            "Partnerships should create observable proof points. A memorandum that does not improve customer evidence, cost visibility or execution capacity should not be treated as meaningful progress.",
        ], ["Secure scarce capabilities early", "Partnerships must produce proof points", "Ecosystem position matters more than standalone claims"]),
        _section_payload("Incumbents should protect near-term economics while preserving long-term options", "The strongest posture is neither aggressive overcommitment nor passive observation.", [
            "For incumbent businesses, the task is to protect near-term cash flow and customer relationships while avoiding strategic blindness to a long-term shift. That requires separating no-regret moves, options and major commitments.",
            "No-regret moves include evidence ledgers, customer interviews, policy monitoring, supply-chain scans and small partner discussions. Option moves include pilots, preferential access and minority investments. Major commitments should wait for stronger proof.",
            "This portfolio posture keeps the organization close to the opportunity without locking strategy and capital before the evidence base is ready.",
        ], ["Separate no-regret moves from options and big bets", "Protect near-term economics", "Use options to manage uncertainty"]),
        _section_payload("The management agenda should translate uncertainty into quarterly decision gates", "The report should end in owners, evidence thresholds and review cadence.", [
            "The output should become a quarterly management agenda: every material claim needs an owner, evidence gate, review date and escalation condition.",
            "Near-term work should close source, customer, cost and timeline gaps; medium-term work should test pilots and partners; long-term work should cover capital, M&A or scaled entry only when decision gates are met.",
            "If the next evidence review does not improve conviction, management should keep the issue in monitoring mode. If the core metrics move, the topic can be escalated to an investment committee discussion.",
        ], ["Create quarterly evidence gates", "Assign owners and review cadence", "Escalate only when proof improves"]),
        _section_payload("Signals to watch should be facts that change decisions, not market noise", "A useful watchlist focuses on customers, costs, regulation, competitors and financing.", [
            "Management should not treat media attention, financing announcements or single technical claims as decision signals on their own. More useful signals include customer procurement, cost movement, regulatory clarity, competitor commitments and financing terms.",
            "Each signal should map to an action: keep monitoring, start a pilot, expand a partnership, pause commitment or escalate to the board.",
            "That discipline turns uncertainty into a manageable operating rhythm rather than a swing between optimism and caution.",
        ], ["Track facts that change decisions", "Tie each signal to an action", "Avoid being led by market noise"]),
    ]


def _vary_section_paragraph_openings(sections: List[Dict[str, Any]], *, language: str) -> None:
    prefixes = (
        [
            "For leadership teams, ",
            "The management implication is clear: ",
            "A practical reading is that ",
            "The decision lens is that ",
            "For capital allocation, ",
            "The operating takeaway is that ",
            "From a board perspective, ",
            "The next management move is clear: ",
        ]
        if language == "en"
        else [
            "对管理层而言，",
            "从资源配置看，",
            "更实际的判断是，",
            "放到董事会视角，",
            "短期动作应当是，",
            "从执行角度看，",
            "这意味着，",
            "后续管理重点是，",
        ]
    )
    evidence_prefixes = (
        [
            "This should remain a directional conclusion because ",
            "The supporting record is still incomplete: ",
            "This point should be validated further because ",
            "The current source base supports only a bounded conclusion: ",
            "Before a major commitment, management should verify that ",
            "The diligence gap is that ",
            "The available record suggests caution: ",
            "This assumption should stay on the watchlist because ",
        ]
        if language == "en"
        else [
            "该判断仍应保持方向性，因为",
            "当前来源仍不完整：",
            "该点需要继续核验，因为",
            "现有来源只能支持边界内结论：",
            "进入重大承诺前，应先核验",
            "待尽调缺口在于",
            "现有资料提示应保持谨慎：",
            "该假设应纳入观察清单，因为",
        ]
    )
    seen_counts: Dict[str, int] = {}
    for section_idx, section in enumerate(sections):
        paragraphs = [str(x) for x in _as_list(section.get("paragraphs"))]
        revised = []
        for para_idx, para in enumerate(paragraphs):
            text = para.strip()
            lower = text.lower()
            if language == "en":
                if lower.startswith("for executives, "):
                    stem = re.sub(r"(?i)^for executives,\s*", "", text).strip()
                    stem = re.sub(r"(?i)^the key implication is that\s*", "", stem).strip()
                    stem = re.sub(r"(?i)^the implication is that\s*", "", stem).strip()
                    stem = re.sub(r"(?i)^the implication is to\s*", "", stem).strip()
                    text = prefixes[section_idx % len(prefixes)] + _lower_first(stem)
                elif lower.startswith("the evidence boundary is that "):
                    stem = re.sub(r"(?i)^the evidence boundary is that\s*", "", text).strip()
                    text = evidence_prefixes[(section_idx + para_idx) % len(evidence_prefixes)] + _lower_first(stem)
                elif lower.startswith("the evidence boundary for "):
                    stem = re.sub(r"(?i)^the evidence boundary for\s*", "", text).strip()
                    stem = re.sub(r"(?i)^(.+?)\s+is that\s+it\s+is\s+", r"\1 remains ", stem).strip()
                    stem = re.sub(r"(?i)^(.+?)\s+is that\s+", r"\1 remains ", stem).strip()
                    text = evidence_prefixes[(section_idx + para_idx) % len(evidence_prefixes)] + _lower_first(stem)
            else:
                if text.startswith("对管理层而言，"):
                    text = prefixes[section_idx % len(prefixes)] + text[len("对管理层而言，"):]
                elif text.startswith("当前证据边界是"):
                    text = evidence_prefixes[(section_idx + para_idx) % len(evidence_prefixes)] + text[len("当前证据边界是"):]

            opening = re.sub(r"^\W+", "", text)[:18].lower()
            count = seen_counts.get(opening, 0)
            if opening and count >= 2:
                alt = prefixes[(section_idx + para_idx + count) % len(prefixes)]
                if language == "en":
                    text = alt + text[0].lower() + text[1:] if text else text
                else:
                    text = alt + text
                opening = re.sub(r"^\W+", "", text)[:18].lower()
            seen_counts[opening] = seen_counts.get(opening, 0) + 1
            revised.append(_clean_visible_text(text))
        section["paragraphs"] = revised


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _section_payload(title: str, lead: str, paragraphs: List[str], takeaways: List[str]) -> Dict[str, Any]:
    return {"title": title, "lead": lead, "paragraphs": paragraphs, "key_takeaways": takeaways}


def _derive_section_lead(title: str, paragraphs: List[str], *, language: str) -> str:
    if paragraphs:
        return _shorten(paragraphs[0], 220 if language == "en" else 120)
    return ("This section translates the issue into management implications and decision gates." if language == "en" else "本章将议题转化为管理含义和决策门槛。")


def _ensure_takeaways(value: Any, summary: List[str], idx: int, *, language: str) -> List[str]:
    items = _dedupe_texts([str(x).strip() for x in _as_list(value) if str(x).strip()])
    if idx < len(summary):
        items.append(str(summary[idx]))
    defaults = (
        ["Confirm the evidence boundary before committing capital.", "Translate the claim into customer, cost, risk and action implications.", "Escalate only when the decision gate is met."]
        if language == "en"
        else ["投入资源前先确认公开证据边界。", "把判断转化为客户、成本、风险和行动含义。", "证据门槛达到后再升级投入。"]
    )
    return _dedupe_texts(items + defaults)[:4]


def _ensure_charts(value: Any, sections: List[Dict[str, Any]], topic: str, *, language: str) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    seen = set()
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        key = re.sub(r"\W+", "", title.lower())[:120]
        if not title or key in seen:
            continue
        seen.add(key)
        chart = dict(item)
        chart["id"] = str(chart.get("id") or f"chart-{len(charts) + 1}")
        if str(chart.get("type") or "").lower() in {"pie", "donut"}:
            chart["type"] = "matrix"
        charts.append(chart)
        if len(charts) >= 7:
            break

    for chart in _fallback_charts_for_topic(topic, sections, language=language):
        if len(charts) >= 6:
            break
        key = re.sub(r"\W+", "", chart["title"].lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        chart["id"] = f"chart-{len(charts) + 1}"
        chart["exhibit_no"] = str(len(charts) + 1)
        charts.append(chart)
    return charts[:7]


def _fallback_charts_for_topic(topic: str, sections: List[Dict[str, Any]], *, language: str) -> List[Dict[str, Any]]:
    topic_text = str(topic or "the topic").strip()
    section_labels = [_shorten(str(s.get("title") or f"Section {idx}"), 34) for idx, s in enumerate(sections[:5], start=1)]
    if len(section_labels) < 5:
        section_labels.extend(["Customer proof", "Cost case", "Regulation", "Partner access", "Capital timing"][len(section_labels):5])
    if language == "zh":
        title_prefix = topic_text
        return [
            {"title": f"{title_prefix}的决策就绪度仍取决于证据门槛", "subtitle": "方向性指数，用于表达管理层关注优先级", "type": "bar", "categories": ["客户证明", "成本口径", "技术/交付", "监管路径", "伙伴能力"], "series": [{"name": "就绪度指数", "values": [68, 56, 63, 52, 71]}], "caption": "该图为方向性管理视图，不替代经核验市场数据。", "source_note": "BlueOcean evidence-boundary synthesis."},
            {"title": f"{title_prefix}投入姿态应随证明成熟度变化", "subtitle": "从观察到规模化的资源配置节奏", "type": "line", "categories": ["观察", "合作", "试点", "规模化"], "series": [{"name": "管理层信心", "values": [28, 48, 67, 84]}, {"name": "资本暴露", "values": [12, 24, 45, 78]}], "caption": "投入强度应落后于证据成熟度，而不是领先于证据。", "source_note": "BlueOcean scenario synthesis."},
            {"title": f"{title_prefix}关键风险需要按严重性和可管理性排序", "subtitle": "风险暴露与管理关注度", "type": "bubble", "points": [{"label": "客户需求", "x": 72, "y": 78, "size": 78}, {"label": "成本/ROI", "x": 80, "y": 66, "size": 82}, {"label": "监管", "x": 58, "y": 62, "size": 58}, {"label": "供应链", "x": 64, "y": 54, "size": 55}, {"label": "融资", "x": 52, "y": 48, "size": 46}], "x_label": "严重性", "y_label": "可能性", "caption": "气泡大小表示需要管理层投入的注意力。", "source_note": "BlueOcean risk screen."},
            {"title": f"{title_prefix}管理层注意力应从叙事转向验证点", "subtitle": "近期工作优先级", "type": "bar", "categories": ["来源核验", "客户访谈", "成本模型", "政策跟踪", "伙伴筛选"], "series": [{"name": "优先级指数", "values": [92, 84, 78, 70, 66]}], "caption": "最有价值的工作是关闭会改变决策的证据缺口。", "source_note": "BlueOcean management screen."},
            {"title": f"{title_prefix}情景矩阵应同时看吸引力和执行就绪度", "subtitle": "战略选项比较", "type": "matrix", "rows": section_labels[:5], "columns": ["战略吸引力", "执行就绪度", "证据质量", "资本需求"], "values": [[5, 3, 2, 2], [4, 4, 3, 3], [4, 2, 2, 4], [3, 3, 3, 2], [5, 3, 3, 3]], "caption": "矩阵用于排序下一步验证重点。", "source_note": "BlueOcean qualitative assessment."},
            {"title": f"{title_prefix}后续四个季度应围绕证据缺口推进", "subtitle": "验证工作计划", "type": "stacked_bar", "categories": ["Q1", "Q2", "Q3", "Q4"], "series": [{"name": "客户", "values": [35, 28, 18, 12]}, {"name": "成本", "values": [30, 32, 22, 16]}, {"name": "政策/伙伴", "values": [22, 28, 35, 38]}], "caption": "验证节奏应先补事实，再升级资源承诺。", "source_note": "BlueOcean action plan."},
        ]
    return [
        {"title": f"Decision readiness for {topic_text} still depends on evidence gates", "subtitle": "Directional index used to show management priorities", "type": "bar", "categories": ["Customer proof", "Cost case", "Technical delivery", "Regulatory path", "Partner access"], "series": [{"name": "Readiness index", "values": [68, 56, 63, 52, 71]}], "caption": "This exhibit is a directional management view, not a substitute for verified market data.", "source_note": "BlueOcean evidence-boundary synthesis."},
        {"title": f"Commitment posture for {topic_text} should shift as proof matures", "subtitle": "Resource posture from monitoring to scale-up", "type": "line", "categories": ["Monitor", "Partner", "Pilot", "Scale"], "series": [{"name": "Management conviction", "values": [28, 48, 67, 84]}, {"name": "Capital exposure", "values": [12, 24, 45, 78]}], "caption": "Capital exposure should lag evidence maturity rather than lead it.", "source_note": "BlueOcean scenario synthesis."},
        {"title": f"Key risks for {topic_text} should be ranked by severity and manageability", "subtitle": "Risk exposure and management attention", "type": "bubble", "points": [{"label": "Customer demand", "x": 72, "y": 78, "size": 78}, {"label": "Cost / ROI", "x": 80, "y": 66, "size": 82}, {"label": "Regulation", "x": 58, "y": 62, "size": 58}, {"label": "Supply chain", "x": 64, "y": 54, "size": 55}, {"label": "Financing", "x": 52, "y": 48, "size": 46}], "x_label": "Severity", "y_label": "Likelihood", "caption": "Bubble size indicates the management attention required.", "source_note": "BlueOcean risk screen."},
        {"title": f"Management attention for {topic_text} should move from narrative to proof points", "subtitle": "Near-term workplan priorities", "type": "bar", "categories": ["Source checks", "Customer calls", "Cost model", "Policy watch", "Partner screen"], "series": [{"name": "Priority index", "values": [92, 84, 78, 70, 66]}], "caption": "The most valuable work closes evidence gaps that can change the decision.", "source_note": "BlueOcean management screen."},
        {"title": f"Scenario matrix for {topic_text} should compare attractiveness with readiness", "subtitle": "Strategic option comparison", "type": "matrix", "rows": section_labels[:5], "columns": ["Attractiveness", "Readiness", "Evidence quality", "Capital need"], "values": [[5, 3, 2, 2], [4, 4, 3, 3], [4, 2, 2, 4], [3, 3, 3, 2], [5, 3, 3, 3]], "caption": "The matrix ranks where the next validation work should focus.", "source_note": "BlueOcean qualitative assessment."},
        {"title": f"Evidence gaps for {topic_text} should be closed over the next four quarters", "subtitle": "Validation workplan by theme", "type": "stacked_bar", "categories": ["Q1", "Q2", "Q3", "Q4"], "series": [{"name": "Customer", "values": [35, 28, 18, 12]}, {"name": "Cost", "values": [30, 32, 22, 16]}, {"name": "Policy / partner", "values": [22, 28, 35, 38]}], "caption": "The validation cadence should improve facts before escalating resource commitment.", "source_note": "BlueOcean action plan."},
    ]


def _default_credentials(*, language: str) -> str:
    return "Responsible for evidence checks, executive synthesis and report QA." if language == "en" else "负责证据校验、管理层综合和报告 QA。"


def _default_evidence_note(fact_pack: ResearchFactPack, *, language: str) -> str:
    if fact_pack.high_confidence_facts:
        fact = _shorten(fact_pack.high_confidence_facts[0], 240)
        return f"Public-evidence signal: {fact}" if language == "en" else f"公开证据线索：{fact}"
    return "Public evidence retained in the source backup; additional validation is required for unsupported claims." if language == "en" else "公开来源底稿已保留；未被来源支持的判断仍需补充核验。"


def _default_implication(*, language: str) -> str:
    return "Translate this point into resource allocation, risk appetite and next-step decision gates." if language == "en" else "将该判断转化为资源配置、风险偏好和下一步决策门槛。"


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


def _item_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if not isinstance(v, (list, dict)))
    if isinstance(value, list):
        return " ".join(_item_text(x) for x in value)
    return str(value or "")


def _risk_has_trigger_and_action(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    trigger = str(value.get("trigger") or value.get("signal") or "").strip()
    action = str(value.get("management_action") or value.get("mitigation") or value.get("action") or "").strip()
    return len(trigger) >= 12 and len(action) >= 12


def _dedupe_texts(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_visible_text(value)
        if not text:
            continue
        key = re.sub(r"\W+", "", text.lower())[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _split_long_paragraphs(paragraphs: List[str], *, language: str) -> List[str]:
    limit = 760 if language == "en" else 480
    out: List[str] = []
    for paragraph in paragraphs:
        text = str(paragraph or "").strip()
        if len(text) <= limit:
            out.append(text)
            continue
        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            out.append(_shorten(text, limit))
            remainder = text[limit:].strip()
            if len(remainder) > 60:
                out.append(_shorten(remainder, limit))
            continue
        chunk = ""
        for sentence in sentences:
            candidate = (chunk + " " + sentence).strip()
            if len(candidate) <= limit:
                chunk = candidate
            else:
                if chunk:
                    out.append(chunk)
                chunk = sentence
        if chunk:
            out.append(chunk)
    return _dedupe_texts(out)


def _duplicate_paragraphs(sections: List[Any]) -> List[str]:
    seen: Dict[str, str] = {}
    duplicates: List[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        for para in _as_list(section.get("paragraphs")):
            text = _clean_visible_text(para)
            if len(text) < 120:
                continue
            key = re.sub(r"\W+", "", text.lower())[:220]
            if not key:
                continue
            if key in seen and text not in duplicates:
                duplicates.append(_shorten(text, 120))
            else:
                seen[key] = text
    return duplicates


def _keyword_hits(text: str, terms: Iterable[str]) -> int:
    lower = str(text or "").lower()
    hits = 0
    for term in terms:
        token = str(term or "").lower()
        if not token:
            continue
        if re.search(r"[\u4e00-\u9fff]", token):
            hits += lower.count(token)
        else:
            hits += len(re.findall(r"\b" + re.escape(token) + r"\b", lower))
    return hits
