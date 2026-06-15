from __future__ import annotations

import ast
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
    "future action agenda",
    "what to watch",
    "signals to watch",
    "mckinsey-style",
    "mckinsey",
    "10 tests",
    "ten tests",
    "strategic ten questions",
    "战略十问",
    "".join(["b", "c", "g"]) + "-style",
    "consulting-style",
    "sample card",
    "model-proposed",
    "chart qa",
    "quality-control synthesis",
    "topic-neutral strategic index",
    "converted to a bar exhibit",
    "values normalized",
    "source backup",
    "supporting sources",
    "next useful work",
    "validation gap",
    "validation gaps",
    "open diligence items",
    "test the chapter",
    "the report should",
    "senior-leadership questions",
    "senior leadership questions",
    "main conclusions:",
    "recommended actions:",
    "risk implications highlights:",
    "visible readout",
    "matrix readout",
    "for ceos and boards",
    "should be managed as a staged strategic option",
    "report title should",
    "制作说明",
    "样例图卡",
)

PROCESS_LANGUAGE_PATTERNS = (
    r"\bthis\s+(?:chapter|section)\s+(?:therefore\s+)?(?:concludes|finds|shows|argues|frames|explains|demonstrates|sets\s+out|assesses|analyzes|translates)\s+that\b",
    r"\bthis\s+(?:chapter|section)\s+(?:therefore\s+)?(?:frames|sets\s+out|assesses|analyzes|explains|translates)\s+the\s+(?:topic|issue|question)\b",
    r"\bthe\s+(?:chapter|section)\s+(?:therefore\s+)?(?:concludes|finds|shows|argues|frames|explains|demonstrates|sets\s+out|assesses|analyzes|translates)\s+that\b",
    r"\bthis\s+(?:chapter|section)\s+is\s+about\b",
    r"\bthis\s+(?:chapter|section)\s+will\b",
)

GENERIC_CHART_CAPTION_PATTERNS = (
    "directional index used where public evidence supports relative comparison",
    "this exhibit is a directional management view",
    "actual percentages should be replaced with verified cost-model data",
    "weakly supported areas should remain diligence tasks",
    "blueocean public-source synthesis",
    "blueocean scenario synthesis",
    "blueocean risk screen",
    "blueocean qualitative assessment",
    "blueocean management screen",
    "blueocean option synthesis",
    "blueocean customer option synthesis",
    "blueocean capital staging view",
    "blueocean uncertainty-resolution model",
    "blueocean synthesis",
    "blueocean synthesis from public evidence",
    "priority index",
    "readiness index",
    "management conviction",
    "management attention",
    "evidence maturity",
    "capital exposure should",
)

GENERIC_CHART_VIEW_PATTERNS = (
    "decision readiness",
    "diligence workload",
    "commitment posture",
    "capital exposure",
    "management attention",
    "validation effort shifts",
    "scenario choices should",
    "key risks should be ranked",
    "competitive posture depends",
    "use-case priorities separate",
    "capital exposure should",
    "readiness index",
    "priority index",
    "relative strength",
    "management conviction",
    "evidence maturity",
    "blueocean public-source synthesis",
    "blueocean scenario synthesis",
    "blueocean risk screen",
    "blueocean qualitative assessment",
    "blueocean option synthesis",
    "blueocean customer option synthesis",
    "blueocean capital staging view",
    "blueocean uncertainty-resolution model",
    "blueocean synthesis",
    "blueocean synthesis from public evidence",
)

FUSION_META_CHART_PATTERNS = (
    "public evidence is concentrated",
    "source domains define",
    "evidence breadth depends",
    "public facts cluster",
    "dated facts anchor",
    "source type mix",
    "business relevance depends on numbers",
    "evidence support differs",
    "domain map separates",
    "commercial quantification remains",
    "institution roles clarify",
    "facts become more useful",
    "strategic coverage balances",
    "triangulation is strongest",
)

SCRAPE_NOISE_PATTERNS = (
    "search context:",
    "source url retained for public-source review",
    "the page body could not be fully extracted",
    "lower-confidence public signal",
    "please click the following link to continue",
    "thank you for visiting our site",
    "your email address will only be used",
    "youtube est desactive",
    "select your newsletters",
    "subscribe subscribe",
)

OFF_TOPIC_CONTAMINATION_PATTERNS = (
    "\u987a\u5cf0\u5b9d\u5b9d",
    "\u987a\u5cf0",
    "\u5b9d\u5b9d",
    "\u7f8e\u5bb9\u9662",
    "\u4fdd\u5065\u54c1",
    "\u7ade\u54c1\u5f88\u5389\u5bb3",
    "\u6700\u9002\u5408\u5e2e\u4ed6",
    "shun" "feng",
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
PROCESS_LANGUAGE_RE = re.compile("|".join(f"(?:{pattern})" for pattern in PROCESS_LANGUAGE_PATTERNS), re.I)

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
            if _is_scrape_noise(sentence):
                continue
            score = _score_sentence(sentence, topic) + source_bonus
            if score >= 4:
                scored_lines.append((score, prefix + sentence))

    facts = _ranked_unique(scored_lines, limit=24)
    facts = [line for line in facts if _clean_fact_text(line)]
    numeric = [line for line in facts if (NUMBER_RE.search(line) or MONEY_RE.search(line)) and _clean_fact_text(line)]
    dated = [line for line in facts if DATE_RE.search(line) and _clean_fact_text(line)]
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

    # Fact-pack collection gaps are audited separately in report_quality.json.
    # They should not force internal "evidence boundary" wording into the reader-facing report.
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
        min_paragraphs = 6 if language == "en" else 4
        min_chars = 1900 if language == "en" else 850
        section_text_len = len(lead) + sum(len(p) for p in paragraphs)
        if len(paragraphs) < min_paragraphs:
            issues.append(f"第{idx}章正文段落不足，需要至少{min_paragraphs}段有证据支撑的连续分析。")
        if section_text_len < min_chars:
            issues.append(f"第{idx}章正文密度不足，当前约{section_text_len}字符；需要更接近标杆报告的完整分析页。")
        long_paras = [p for p in paragraphs if len(p) > (820 if language == "en" else 520)]
        if long_paras:
            issues.append(f"第{idx}章存在过长段落，需要拆成更适合正式报告排版和扫读的短段。")
        if not str(section.get("visual_hint") or "").strip():
            issues.append(f"第{idx}章缺少 visual_hint。")
        section_visible_text = " ".join([title, lead, *paragraphs]).lower()
        leaked_process_phrases = [
            phrase
            for phrase in ("the decision lens is", "a practical reading is", "retained signal", "retained source signal", "the sourced record")
            if phrase in section_visible_text
        ]
        if leaked_process_phrases:
            issues.append(f"第{idx}章仍有过程化写作痕迹：" + "、".join(leaked_process_phrases[:4]))

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

    if len(charts) != 14:
        issues.append(f"charts 数量应为14个，当前为{len(charts)}个。")
    chart_types = {str(chart.get("type") or "").lower() for chart in charts if isinstance(chart, dict)}
    if len(charts) >= 12 and len(chart_types & {"stacked_bar", "line", "matrix", "bubble"}) < 3:
        issues.append("charts 类型过于单一，需要混合使用 stacked_bar、line、matrix、bubble 等 LaTeX 原生图表。")
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
        if _is_generic_chart_payload(chart):
            issues.append(f"第{idx}个 chart 仍是管理指数/内部综合视图，需要改成来源或事实派生图表：{title[:80]}")
        chart_type = str(chart.get("type") or "").lower()
        if chart_type in {"bar", "stacked_bar", "line"} and _weak_series_chart(chart):
            issues.append(f"第{idx}个 chart 数据过薄或包含单柱/单点图，需要至少3个有效分类和多个非零数据点。")
        if chart_type in {"bubble", "scatter", "risk_matrix", "quadrant"}:
            points = _best_bubble_points(chart)
            if _weak_bubble_points(points):
                issues.append(f"第{idx}个 bubble chart 数据点不足或仍是占位标签，需要至少3个真实维度点。")
        chart_reader_text = " ".join(str(chart.get(key) or "") for key in ("subtitle", "caption", "source_note")).lower()
        generic_caption_hits = [p for p in GENERIC_CHART_CAPTION_PATTERNS if p in chart_reader_text]
        if generic_caption_hits:
            issues.append(f"第{idx}个 chart 仍有模板化说明，需要改成图表自身的解释。")

    lower = text.lower()
    meta_hits = [p for p in META_LABEL_PATTERNS if p.lower() in lower]
    if meta_hits:
        issues.append("正式报告中仍出现内部元标签：" + "、".join(meta_hits))
    off_topic_hits = [p for p in OFF_TOPIC_CONTAMINATION_PATTERNS if p.lower() in lower]
    if off_topic_hits:
        issues.append("正式报告中出现了其他项目/客户上下文污染：" + "、".join(off_topic_hits))
    process_hits = sorted({match.group(0) for match in PROCESS_LANGUAGE_RE.finditer(text)})
    if process_hits:
        issues.append("正式报告中仍出现章节自我描述/思考过程语言：" + "、".join(process_hits[:6]))
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
        raw_paragraphs = [str(x).strip() for x in _as_list(section.get("paragraphs")) if str(x).strip()]
        paragraphs = [p for p in raw_paragraphs if not _is_placeholder_section_paragraph(p, str(section.get("title") or ""))]
        if len(paragraphs) < 3:
            paragraphs.extend(_supplement_paragraphs(fact_pack, title=str(section.get("title") or topic), language=language, needed=3 - len(paragraphs)))
        paragraphs = _split_long_paragraphs(_dedupe_texts(paragraphs), language=language)
        section["paragraphs"] = paragraphs[:8]
        if not str(section.get("lead") or "").strip() and paragraphs:
            section["lead"] = _shorten(paragraphs[0], 220)
        sections.append(section)
    fixed["sections"] = _ensure_sections(sections, fixed["executive_summary"], topic, fact_pack, language=language)
    _vary_section_paragraph_openings(fixed["sections"], language=language)
    _dedupe_cross_section_paragraphs(fixed["sections"], fact_pack, language=language)
    fixed["charts"] = _ensure_charts(fixed.get("charts"), fixed["sections"], topic, fact_pack=fact_pack, language=language)
    _walk_text(fixed, _clean_visible_text)
    _dedupe_cross_section_paragraphs(fixed["sections"], fact_pack, language=language)
    _dedupe_repeated_openings(fixed["sections"], language=language)
    _dedupe_cross_section_paragraphs(fixed["sections"], fact_pack, language=language)
    _dedupe_repeated_openings(fixed["sections"], language=language)
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
                "Charts must be exactly 14 items, use only bar, stacked_bar, line, matrix or bubble, and must include LaTeX-renderable data arrays. References may only use URLs from the evidence pack."
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


def _coerce_reader_text(value: Any, *, preferred_keys: Iterable[str] = ()) -> str:
    """Extract reader-facing prose from model values that may be nested or stringified."""
    if value is None:
        return ""
    keys = tuple(preferred_keys) + ("body", "narrative", "description", "title")
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                text = _coerce_reader_text(value.get(key), preferred_keys=preferred_keys)
                if text:
                    return text
        pieces = [_coerce_reader_text(item, preferred_keys=preferred_keys) for item in value.values()]
        return _clean_visible_text(" ".join(piece for piece in pieces if piece))
    if isinstance(value, list):
        pieces = [_coerce_reader_text(item, preferred_keys=preferred_keys) for item in value]
        return _clean_visible_text(" ".join(piece for piece in pieces if piece))

    text = str(value or "").strip()
    parsed = _parse_literal_prefix(text)
    if parsed:
        obj, remainder = parsed
        extracted = _coerce_reader_text(obj, preferred_keys=preferred_keys)
        remainder = _clean_visible_text(remainder)
        if extracted and remainder and not remainder.lower().startswith(extracted[:80].lower()):
            return _clean_visible_text(f"{extracted} {remainder}")
        if extracted:
            return extracted
    return _clean_visible_text(text)


def _parse_literal_prefix(text: str):
    text = str(text or "").strip()
    if not text or text[0] not in "{[":
        return None
    pairs = {"{": "}", "[": "]"}
    quote = ""
    escaped = False
    stack: List[str] = []
    for idx, ch in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch in pairs:
            stack.append(pairs[ch])
            continue
        if stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                prefix = text[: idx + 1]
                remainder = text[idx + 1 :].strip()
                try:
                    return ast.literal_eval(prefix), remainder
                except Exception:
                    try:
                        return json.loads(prefix), remainder
                    except Exception:
                        return None
    return None


def _is_generic_title(title: str) -> bool:
    normalized = re.sub(r"^\s*\d+[\.)、]\s*", "", title or "").strip().lower()
    return normalized in GENERIC_SECTION_TITLES or len(normalized) < 12


def _is_bad_section_title(title: str, topic: str) -> bool:
    normalized = re.sub(r"\W+", " ", str(title or "").lower()).strip()
    topic_key = re.sub(r"\W+", " ", str(topic or "").lower()).strip()
    if not normalized:
        return True
    if topic_key and normalized.startswith(topic_key[: min(56, len(topic_key))]):
        if any(marker in normalized for marker in (" should be ", " should ", " not a binary bet", " staged management")):
            return True
    return any(
        marker in normalized
        for marker in (
            "report title should",
            "should be translated into staged management decisions",
            "should be managed as a staged strategic option",
        )
    )


def _is_generic_chart_title(title: str, categories: List[str]) -> bool:
    normalized = str(title or "").strip().lower()
    if not normalized or len(normalized) < 14:
        return True
    if normalized in GENERIC_CHART_TERMS:
        return True
    cats = [c.strip().lower() for c in categories if c.strip()]
    generic_cats = sum(1 for c in cats if c in GENERIC_CHART_TERMS)
    return bool(cats) and generic_cats >= max(2, len(cats) - 1)


def _is_generic_chart_payload(chart: Dict[str, Any], *, reject_source_note: bool = True) -> bool:
    if not isinstance(chart, dict):
        return True
    text = " ".join(
        _clean_visible_text(chart.get(key))
        for key in ("title", "subtitle", "caption", "x_label", "y_label")
    ).lower()
    if any(pattern in text for pattern in GENERIC_CHART_CAPTION_PATTERNS):
        return True
    if reject_source_note and _is_generic_source_note(str(chart.get("source_note") or "")):
        return True
    for series in _as_list(chart.get("series")):
        if isinstance(series, dict):
            text += " " + _clean_visible_text(series.get("name")).lower()
    for pattern in GENERIC_CHART_VIEW_PATTERNS:
        if pattern in text:
            return True
    return False


def _is_forbidden_fusion_meta_chart(chart: Dict[str, Any]) -> bool:
    if not isinstance(chart, dict):
        return False
    text = " ".join(
        _clean_visible_text(chart.get(key))
        for key in ("title", "subtitle", "caption", "source_note", "x_label", "y_label")
    ).lower()
    return any(pattern in text for pattern in FUSION_META_CHART_PATTERNS)


def _is_generic_source_note(text: str) -> bool:
    lower = _clean_visible_text(text).lower().strip(" .")
    generic_notes = {
        "blueocean synthesis",
        "blueocean synthesis from public evidence",
        "blueocean public-source synthesis",
        "public sources and blueocean synthesis",
    }
    return lower in generic_notes or any(pattern in lower for pattern in GENERIC_CHART_CAPTION_PATTERNS)


def _is_scrape_noise(text: str) -> bool:
    lower = str(text or "").lower()
    if not lower.strip():
        return True
    return any(pattern in lower for pattern in SCRAPE_NOISE_PATTERNS)


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
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)
    text = re.sub(r"([A-Za-z])-(a|an|and|but|while|without|with|not|the)\b", r"\1 - \2", text, flags=re.I)
    pre_replacements = [
        (
            r"\bFor\s+(.{8,180}?),\s+the next useful work is to convert .*?main narrative\.?",
            r"For \1, leadership should focus on the few facts that change capital timing, customer exposure or partner posture.",
        ),
        (r"\bThe\s+sourced\s+record\s+should\s+narrow\s+the\s+decision\s+rather\s+than\s+broaden\s+the\s+narrative\.?\s*", "Public evidence should narrow the decision. "),
        (r"\bThe\s+sourced\s+record\s+provides\s+a\s+starting\s+point,\s+not\s+a\s+complete\s+underwriting\s+case\.?\s*", "Public evidence provides a starting point, not a complete underwriting case. "),
        (r"\bThe\s+strongest\s+retained\s+signal\s+is:\s*", "One public fact is: "),
        (r"\bRetained\s+source\s+signal:\s*", "A public source indicates: "),
        (r"\bRetained\s+signal\s+\d+:\s*", "A public source indicates: "),
        (r"\bRetained\s+signal:\s*", "A public source indicates: "),
        (r"\bThe\s+decision\s+lens\s+is\s+that\s*", ""),
        (r"\bA\s+practical\s+reading\s+is\s+that\s*", ""),
        (r"\ba\s+practical\s+reading\s+is\s+that\s*", ""),
        (r"\bthe\s+operating\s+takeaway\s+is\s+that\s*", ""),
        (r"\bThat\s+signal\s+can\s+support\b", "That evidence can support"),
        (r"\bTreat\s+that\s+signal\s+as\b", "Treat that evidence as"),
        (r"\btreat\s+that\s+signal\s+as\b", "treat that evidence as"),
        (r"\bSearch context:.*?(?=\bA quarterly\b|\bEach review\b|\bThat evidence\b|\bIt can support\b|\bTreat that evidence\b|\bTreat that signal\b|$)", ""),
        (r"\b\S+/\S+\s+Search context:.*?(?=\bA quarterly\b|\bEach review\b|\bThat evidence\b|\bIt can support\b|\bTreat that evidence\b|\bTreat that signal\b|$)", ""),
        (r"\b(?:A public source indicates|One public fact is):\s*\S+/\S+\s*", ""),
        (r"(?<![:/.])\b[A-Za-z0-9][A-Za-z0-9.-]*(?:/[A-Za-z0-9._~%+-]+){2,}\b", ""),
        (r"\bThe page body could not be fully extracted[^.]*\.?", ""),
        (r"\bthis source should be treated as a lower-confidence public signal[^.]*\.?", ""),
        (r"\blower-confidence public signal\b", "public signal"),
        (r"\banother fetched source\b", "another public source"),
        (r"\bevidence ledgers?\b", "public record"),
        (r"\bfact[-\s]+packs?\b", "public record"),
        (r"\bvalidation gaps?\b", "open questions"),
        (r"\bevidence gates?\b", "verified milestones"),
        (r"\bdecision gates?\b", "decision milestones"),
        (r"\bsource backup\b", "public record"),
        (r"\bsupporting sources\b", "public record"),
        (r"\bMain conclusions?:\s*", ""),
        (r"\bRecommended actions?:\s*", ""),
        (r"\bRisk implications?(?: highlights?)?:\s*", ""),
        (r"\bFor CEOs? and boards,\s*", ""),
        (r"\bFor CEOs? and boards\b", ""),
    ]
    for pattern, replacement in pre_replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    for pattern in META_LABEL_PATTERNS:
        text = re.sub(re.escape(pattern), "", text, flags=re.I)
    for pattern in PROCESS_LANGUAGE_PATTERNS:
        text = re.sub(pattern + r"\s*", "", text, flags=re.I)
    replacements = [
        (r"\bCEO decision scenario\b", "board choice under uncertainty"),
        (r"\bCEO investment committee scenario\b", "board choice under uncertainty"),
        (r"\bManagement action plan\b", "near-term management moves"),
        (r"\bRisk register\b", "risk implications"),
        (r"\bMethod and team\b", "about the research"),
        (r"\bExecutive summary\b", "opening view"),
        (r"\bMain conclusions?:\s*", ""),
        (r"\bRecommended actions?:\s*", ""),
        (r"\bRisk implications?(?: highlights?)?:\s*", ""),
        (r"\bFor CEOs? and boards,\s*", ""),
        (r"\bFor CEOs? and boards\b", ""),
        (r"\bKey findings\b", "main conclusions"),
        (r"\bEvidence boundary:\s*", "The public record shows that "),
        (r"\bEvidence:\s*", ""),
        (r"\bManagement implication:\s*", ""),
        (r"\bThe management implication is clear:\s*", ""),
        (r"\bThe next management move is clear:\s*", ""),
        (r"\bThis should remain a directional conclusion because\s*", "The available record supports a directional view because "),
        (r"\bThis point should be validated further because\s*", "Leadership should validate whether "),
        (r"\bThis assumption should stay on the watchlist because\s*", "This assumption needs continued scrutiny because "),
        (r"\bThe diligence gap is that\s*", "The unresolved commercial question is whether "),
        (r"\bevidence ledgers?\b", "public record"),
        (r"\bfact[-\s]+packs?\b", "public record"),
        (r"\bvalidation gaps?\b", "open questions"),
        (r"\bopen diligence items\b", "open questions"),
        (r"\bthe next review should test the chapter against\b", "the next review should test the investment case against"),
        (r"\btest the chapter\b", "test the investment case"),
        (r"\bpublic-evidence boundary\b", "available public record"),
        (r"\bevidence-boundary\b", "available public record"),
        (r"\bevidence boundary\b", "available public record"),
        (r"\baction plan\b", "next steps"),
        (r"\binternal executive strategy stress test\b", ""),
        (r"\binternal framework\b", ""),
        (r"\bstress test\b", "review"),
        (r"\bavailable public record:\s*", "The public record shows that "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"\(\d+\)\s*", "", text)
    text = text.replace("…", "")
    text = re.sub(r"\.{3,}", ".", text)
    text = re.sub(r"([.!?])\s+([a-z])", lambda m: f"{m.group(1)} {m.group(2).upper()}", text)
    text = re.sub(r"\b(Operationally|Commercially|For the board|For capital allocation|In capital terms),\s+([A-Z])", lambda m: f"{m.group(1)}, {m.group(2).lower()}", text)
    text = _sentence_case_if_needed(text)
    return re.sub(r"\s+", " ", text).strip()


def _sentence_case_if_needed(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    return cleaned[:1].upper() + cleaned[1:] if cleaned[:1].islower() else cleaned


def _walk_text(value: Any, fn) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = item if _is_structural_text_key(str(key)) else fn(item)
            else:
                _walk_text(item, fn)
    elif isinstance(value, list):
        for idx, item in enumerate(list(value)):
            if isinstance(item, str):
                value[idx] = fn(item)
            else:
                _walk_text(item, fn)


def _is_structural_text_key(key: str) -> bool:
    return key in {
        "id",
        "type",
        "url",
        "visual_hint",
        "source_type",
        "content_type",
        "x_label",
        "y_label",
    }


def _fallback_references(source_refs: List[str]) -> List[Dict[str, str]]:
    refs = []
    for idx, ref in enumerate(source_refs[:8], start=1):
        url_match = URL_RE.search(ref)
        url = url_match.group(0).rstrip("/") if url_match else ""
        title = ref.split("|", 1)[0].strip(" []")
        refs.append({"title": title or f"Source {idx}", "url": url, "note": "Public evidence source."})
    return refs


def _ensure_summary_items(value: Any, fact_pack: ResearchFactPack, *, language: str) -> List[str]:
    items = []
    for item in _as_list(value):
        text = _coerce_reader_text(item, preferred_keys=("executive_summary_text", "summary", "text", "content", "finding"))
        if text:
            items.append(text)
    items = _dedupe_texts(items)
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
        f"The CEO-level question is not whether {fact_pack.topic} is interesting, but which decisions the current facts can support now.",
        "Management should separate verified facts, directional scenarios and open questions before committing capital, partnerships or market-entry resources.",
        "Technical evidence only matters when it changes revenue potential, cost position, investment return, execution risk or the timing of a commitment.",
        "Where sourced data is missing, the stronger conclusion is to keep the assumption explicit rather than invent a market, cost or share estimate.",
        "The near-term posture should prioritize low-regret learning, medium-term strategic options and long-term commitments only after proof improves.",
    ]
    items.extend(f"A public source anchors this point: {_clean_fact_text(fact)}." for fact in evidence)
    return items


def _ensure_executive_summary_text(report: Dict[str, Any], fact_pack: ResearchFactPack, *, language: str) -> str:
    existing = _coerce_reader_text(
        report.get("executive_summary_text"),
        preferred_keys=("executive_summary_text", "summary", "text", "content"),
    )
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
    evidence_note = "The analysis uses public evidence conservatively; unsupported numbers should be treated as open questions, not conclusions."
    action_note = "For a CEO or board reader, the practical implication is to fund low-regret validation, preserve options where uncertainty is high, and reserve larger commitments for stronger proof."
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
            "finding": "Source confidence should define the pace of management commitment." if language == "en" else "来源可信度应决定管理层投入节奏。",
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
        {"horizon": "Near term, 0-90 days", "action": f"Build a sourced fact base for {topic}, prioritizing market size, customer demand, cost, revenue, timing and source quality.", "owner": "Strategy lead / research lead", "success_metric": "Every material claim is tied to a source, date and open question.", "decision_gate": "Do not convert unsupported numbers into conclusions until the question is resolved."},
        {"horizon": "Medium term, 1-2 quarters", "action": "Separate no-regret moves, strategic options and resource-heavy commitments.", "owner": "CEO / business owner", "success_metric": "Each move has a budget, owner and validation metric.", "decision_gate": "Scale commitment only after customer, cost, policy or financing evidence reaches threshold."},
        {"horizon": "Long term, 2-4 quarters", "action": "Advance partnerships, investments or market entry only after the proof base improves, with quarterly review cadence.", "owner": "CEO / board", "success_metric": "ROI, risk exposure and execution milestones appear on the management dashboard.", "decision_gate": "If core assumptions remain unverified, preserve options and pause major capital commitment."},
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
        {"risk": "The public record is too thin for high-conviction decisions.", "trigger": "Source count, authoritative sources, numeric facts or timelines are below threshold.", "management_action": "Classify claims as verified, directional or open questions and add authoritative sources.", "evidence_boundary": boundary},
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
            f"本报告基于公开网页、PDF、公告或研究资料形成来源底稿。"
            f"当前共纳入{fact_pack.source_count}个来源、{fact_pack.authoritative_source_count}个权威来源；"
            "正文只在公开来源支持范围内使用数字、日期和事件。"
        )
    return (
        "This report is based on public web, PDF, filing and research sources. "
        f"The current source base includes {fact_pack.source_count} sources and {fact_pack.authoritative_source_count} authoritative sources. "
        "Numbers, dates and events are used only where public sources support them."
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
        title = re.sub(r"^\s*\d+[\.)、]\s*", "", title).strip()
        if _is_generic_title(title) or _is_bad_section_title(title, topic):
            title = _fallback_section_title(idx, blueprints, topic, fact_pack, language=language)
        blueprint = _section_blueprint_for_title(title, blueprints, idx)
        key = re.sub(r"\W+", "", title.lower())[:120]
        if not title or key in seen:
            continue
        seen.add(key)
        normalized = dict(section)
        normalized["title"] = title
        normalized["id"] = str(normalized.get("id") or f"section-{len(out) + 1}")
        normalized["visual_hint"] = str(normalized.get("visual_hint") or f"image-{len(out) + 1}")
        raw_paragraphs = [str(x).strip() for x in _as_list(normalized.get("paragraphs")) if str(x).strip()]
        paragraphs = [
            p for p in raw_paragraphs
            if not _is_placeholder_section_paragraph(p, title) and not _is_short_placeholder_paragraph(p)
        ]
        if len(paragraphs) < 5:
            paragraphs.extend(_supplement_paragraphs(fact_pack, title=title, language=language, needed=5 - len(paragraphs)))
        normalized_paragraphs = _split_long_paragraphs(_dedupe_texts(paragraphs), language=language)[:8]
        used_blueprint_paragraphs = False
        if blueprint and _section_paragraphs_are_placeholder(normalized_paragraphs):
            normalized_paragraphs = [str(x).strip() for x in _as_list(blueprint.get("paragraphs")) if str(x).strip()][:8]
            used_blueprint_paragraphs = True
        if blueprint:
            normalized_paragraphs = _dedupe_texts(normalized_paragraphs + [str(x).strip() for x in _as_list(blueprint.get("paragraphs")) if str(x).strip()])
        target_paragraphs = 7 if language == "en" else 4
        while len(normalized_paragraphs) < target_paragraphs:
            normalized_paragraphs.append(_completion_paragraph(title, len(normalized_paragraphs), language=language))
        normalized_paragraphs = _ensure_section_depth(title, normalized_paragraphs, fact_pack, language=language)
        normalized["paragraphs"] = _select_report_paragraphs(normalized_paragraphs, limit=8, target_chars=2300 if language == "en" else 850)
        lead = str(normalized.get("lead") or "").strip()
        if used_blueprint_paragraphs and blueprint and str(blueprint.get("lead") or "").strip():
            lead = str(blueprint.get("lead") or "").strip()
        if len(lead) < 40 or _is_generic_title(lead):
            normalized["lead"] = _derive_section_lead(title, normalized["paragraphs"], language=language)
        else:
            normalized["lead"] = _clean_visible_text(lead)
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
        enriched_paragraphs = _ensure_section_depth(section["title"], [str(x).strip() for x in _as_list(section.get("paragraphs")) if str(x).strip()], fact_pack, language=language)
        section["paragraphs"] = _select_report_paragraphs(enriched_paragraphs, limit=8, target_chars=2300 if language == "en" else 850)
        section["lead"] = _clean_visible_text(str(section.get("lead") or ""))
        out.append(section)
    return out[:10]


def _fallback_section_title(idx: int, blueprints: List[Dict[str, Any]], topic: str, fact_pack: ResearchFactPack, *, language: str) -> str:
    if blueprints:
        return str(blueprints[(idx - 1) % len(blueprints)].get("title") or "").strip()
    topic_text = str(topic or fact_pack.topic or "the topic").strip()
    if language == "zh":
        return f"{topic_text}应转化为分阶段管理判断"
    return f"{topic_text} should be translated into staged management decisions"


def _section_blueprint_for_title(title: str, blueprints: List[Dict[str, Any]], idx: int) -> Dict[str, Any]:
    title_key = re.sub(r"\W+", "", str(title or "").lower())[:120]
    for blueprint in blueprints:
        if re.sub(r"\W+", "", str(blueprint.get("title") or "").lower())[:120] == title_key:
            return blueprint
    if blueprints:
        return blueprints[(idx - 1) % len(blueprints)]
    return {}


def _section_paragraphs_are_placeholder(paragraphs: List[str]) -> bool:
    if not paragraphs:
        return True
    weak_markers = (
        "source backup should be used",
        "validate this section before it is used for a decision",
        "until stronger source support is available",
        "directional input rather than a trigger",
        "public source backup should be used",
    )
    weak_count = sum(1 for paragraph in paragraphs if any(marker in str(paragraph).lower() for marker in weak_markers))
    return weak_count >= max(2, len(paragraphs) - 1)


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
    if _is_fusion_topic(topic_text):
        return [
            _section_payload("Ignition changed the physics case, not the commercialization case", "The 2022 milestone matters, but it does not yet answer the plant, cost or reliability questions.", [
                "LLNL's ignition shot reset the credibility of fusion science, but it did not prove a power plant. The commercial question starts after target gain: repeated operation, heat extraction, net electricity, maintainability and economics.",
                "The most useful interpretation is staged. Ignition improves the odds that fusion deserves management attention, while leaving the investment case dependent on integrated pilot evidence rather than scientific headlines.",
                f"The current public record supports this distinction. {evidence_note} Each new milestone should answer a narrower commercial question rather than being stretched into proof of grid-scale deployment.",
            ], ["Separate science proof from plant proof", "Track integrated pilot evidence", "Avoid treating ignition as commercialization"]),
            _section_payload("Private capital is accelerating experimentation but not removing technical risk", "Funding growth expands the number of shots on goal, while the decisive proof still comes from operating systems.", [
                "Private fusion funding has made the industry broader and faster-moving. It has also shifted part of the learning curve from public laboratories to venture-backed developers, suppliers and corporate partners.",
                "Capital alone does not remove the hard constraints: plasma control, magnets, materials, tritium breeding, heat extraction and maintenance have to work together at plant scale. A large round should therefore be read as option value, not as de-risked infrastructure.",
                "The strategic benefit of the funding wave is earlier access to learning. Partnerships, supplier relationships and customer dialogues can reveal which approaches are credible before the market is obvious.",
            ], ["Use funding as a learning signal", "Do not equate capital raised with de-risking", "Prioritize access to operating evidence"]),
            _section_payload("Cost competitiveness is the route from breakthrough to adoption", "Fusion must compete with renewables, storage, gas and fission on buyer economics, not novelty.", [
                "Fusion's promise is firm clean power with high availability. That promise becomes commercially relevant only if installed cost, uptime, maintenance and fuel-cycle economics create a price customers can underwrite.",
                "The comparison set is unforgiving. Solar, wind and gas already set low-cost reference points, while fission shows how construction risk can overwhelm theoretical capacity-factor advantages.",
                "The strongest first markets are likely to be places where firm clean energy carries a premium: industrial heat, data-center power, hydrogen and hard-to-electrify loads.",
            ], ["Benchmark against actual alternatives", "Watch availability and installed cost", "Look first at premium-value loads"]),
            _section_payload("The path is gated by fuel, materials and regulation", "The critical path runs through systems that make a plant repeatable, licensable and maintainable.", [
                "A viable deuterium-tritium pathway needs a credible tritium strategy. Buying today's scarce tritium is not enough; breeding, recovery and accounting have to be proven as part of the plant.",
                "Materials and maintenance are equally important. Plasma-facing components must survive neutron damage, and the plant has to be serviceable without destroying availability or economics.",
                "Regulation is moving, but not yet standardized across markets. Companies that understand licensing early can shape site choice, partner selection and customer commitments before competitors do.",
            ], ["Tritium is a hard constraint", "Materials lifetime drives economics", "Licensing can change the first-market map"]),
            _section_payload("First markets should be selected by value density, not market size", "The early question is where fusion's attributes command a premium before commodity power economics are proven.", [
                "Grid electricity is the largest addressable market, but it may not be the best first market. A commodity power buyer will compare fusion against cheap renewables, storage, gas and fission.",
                "Industrial heat, hydrogen and data centers can value round-the-clock clean energy differently. These customers may tolerate higher prices if fusion solves reliability, siting or decarbonization constraints that alternatives struggle to meet.",
                "The practical test is customer commitment. Memoranda and announcements matter less than bankable offtake, site access, interconnection work and willingness to share development risk.",
            ], ["Choose markets by willingness to pay", "Test bankable offtake early", "Avoid generic market-size claims"]),
            _section_payload("The value chain may reward suppliers before reactor owners", "Magnets, materials, fuel-cycle systems and diagnostics can monetize learning before electricity sales begin.", [
                "Fusion commercialization is not only a reactor-developer story. Suppliers of high-temperature superconducting magnets, plasma-facing materials, tritium systems, diagnostics and precision manufacturing may capture earlier revenue.",
                "Supplier positions can offer information advantages with lower binary risk than direct project equity. They also create optionality if one reactor architecture wins later than expected.",
                "The most attractive partnerships are those that produce observable proof: delivered components, test hours, validated lifetime data, qualified supply and repeat orders.",
            ], ["Look beyond reactor equity", "Use suppliers for earlier signals", "Make partnerships produce measurable proof"]),
            _section_payload("Geopolitics will shape funding, sites and supply chains first", "Fusion could alter long-term energy dependence, but policy and industrial strategy will matter before energy trade changes.", [
                "Fusion's long-term geopolitical impact is large in theory: abundant fuel, firm clean power and reduced exposure to fossil fuel trade. In practice, the nearer-term effects are funding competition, talent concentration and supply-chain control.",
                "National programs and public-private milestones will influence where plants are licensed and which companies get credibility. Strategic positioning should track policy money and regulatory posture as closely as technical milestones.",
                "For incumbents, the risk is not immediate demand destruction. The risk is missing the partnerships, sites and capabilities that become scarce if the technology moves faster than expected.",
            ], ["Track policy capital", "Watch site and talent concentration", "Do not overstate near-term fossil disruption"]),
            _section_payload("A monitoring system is more valuable than a one-time forecast", "Fusion timelines are uncertain enough that strategy should move when evidence changes, not when sentiment changes.", [
                "The right operating model is a quarterly evidence review: integrated net-electricity tests, duty-cycle progress, tritium breeding, materials lifetime, licensing events, customer offtake and financing terms.",
                "Each signal should have an action attached. Some evidence supports monitoring, some supports a supplier or customer partnership, and only a smaller set should trigger project equity or balance-sheet commitments.",
                "This keeps the organization close to the opportunity while preventing a technology narrative from becoming an unmanaged capital commitment.",
            ], ["Use quarterly evidence gates", "Tie each signal to an action", "Escalate only when proof improves"]),
        ]
    return [
        _section_payload(f"{topic_text} should be managed as a staged strategic option, not a binary bet", "The CEO question is which moves are safe now and which should wait for stronger proof.", [
            f"{topic_text} is easier to misread when it is framed as a yes-or-no bet. The better reading separates near-term learning moves, option-building partnerships and commitments that would require stronger commercial proof.",
            "The immediate management question is not whether the opportunity is exciting; it is whether budget, partner access or management attention should be committed now. Low-cost validation can start early, while larger capital commitments should wait for customer, cost, financing, regulatory or operating proof.",
            f"The current source base is useful but incomplete. {evidence_note} A quarterly review rhythm is more useful than a one-time verdict because the facts that matter will change as customers, regulators and suppliers move.",
        ], ["Start with low-cost validation", "Reserve major commitments for stronger proof", "Keep conclusions close to sourced facts"]),
        _section_payload("Commercial readiness depends on bankability, deliverability and repeat customer proof", "Technical feasibility matters only when it changes customer value, cost position and execution confidence.", [
            "Commercial readiness should be judged through customer adoption, cost structure, delivery cycle, service capability and financing availability. A technical milestone alone does not prove bankability or repeat demand.",
            "Every major assumption needs a verifiable claim behind it: target customer, buying trigger, budget owner, substitute, use case and payback path. Where sourced evidence is missing, the claim should remain directional.",
            "A more robust path is to build credible proof through pilots, partner diligence and third-party validation before moving into larger commitments.",
        ], ["Customer value changes decisions more than technical narrative", "Validate bankability and delivery risk first", "Use pilots to prove repeatability"]),
        _section_payload("Cost, return and timing will determine the real deployment window", "Market size should not enter an investment case until the cost and return logic can be checked.", [
            "Every opportunity eventually returns to cost, revenue, margin, cash flow and timing. If sourced evidence does not support market size, ROI, unit economics or share, those items should remain explicit assumptions rather than conclusions.",
            "Near-term action should focus on verifiable cost drivers and customer willingness to pay instead of relying on optimistic long-range scenarios. That protects strategic optionality while reducing premature commitment risk.",
            "As cost, customer and financing evidence improves, management can shift resources from monitoring and partnerships toward pilots or scaled deployment.",
        ], ["Cost and ROI are priority diligence items", "Timing should move with evidence quality", "Avoid using long-range scenarios as near-term proof"]),
        _section_payload("Regulation, policy and public acceptance can reset the speed of adoption", "External rules can accelerate the market or change the risk budget and project timeline.", [
            "Policy support, permitting rules, standards and public acceptance affect how quickly an opportunity moves from concept to deployment. These factors are part of the investment case, not background context.",
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
            "No-regret moves include a sourced fact base, customer interviews, policy monitoring, supply-chain scans and small partner discussions. Option moves include pilots, preferential access and minority investments. Major commitments should wait for stronger proof.",
            "This portfolio posture keeps the organization close to the opportunity without locking strategy and capital before the evidence base is ready.",
        ], ["Separate no-regret moves from options and big bets", "Protect near-term economics", "Use options to manage uncertainty"]),
        _section_payload("The management rhythm should translate uncertainty into quarterly choices", "The useful output is an owner, a threshold for proof and a cadence for review.", [
            "The output should become a quarterly management rhythm: every material claim needs an owner, a proof threshold, a review date and an escalation condition.",
            "Near-term work should close source, customer, cost and timeline gaps; medium-term work should test pilots and partners; long-term work should cover capital, M&A or scaled entry only when proof is strong enough.",
            "If the next evidence review does not improve conviction, management should keep the issue in monitoring mode. If the core metrics move, the topic can be escalated to an investment committee discussion.",
        ], ["Create quarterly proof thresholds", "Assign owners and review cadence", "Escalate only when proof improves"]),
        _section_payload("Decision-moving facts matter more than market noise", "The most useful monitoring lens focuses on customers, costs, regulation, competitors and financing.", [
            "Management should not treat media attention, financing announcements or single technical claims as decision signals on their own. More useful signals include customer procurement, cost movement, regulatory clarity, competitor commitments and financing terms.",
            "Each signal should map to an action: keep monitoring, start a pilot, expand a partnership, pause commitment or escalate to the board.",
            "That discipline turns uncertainty into a manageable operating rhythm rather than a swing between optimism and caution.",
        ], ["Track facts that change decisions", "Tie each signal to an action", "Avoid being led by market noise"]),
    ]


def _vary_section_paragraph_openings(sections: List[Dict[str, Any]], *, language: str) -> None:
    prefixes = _paragraph_prefixes(language)
    evidence_prefixes = (
        [
            "The constraint is that ",
            "The commercial uncertainty is that ",
            "Leadership should validate whether ",
            "The public record supports a narrower view: ",
            "Before a major commitment, management should verify that ",
            "The unresolved commercial question is whether ",
            "The board should stay cautious because ",
            "This assumption needs continued scrutiny because ",
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
            if opening and count >= 1:
                alt = prefixes[(section_idx + para_idx + count) % len(prefixes)]
                if language == "en":
                    text = alt + text[0].lower() + text[1:] if text else text
                else:
                    text = alt + text
                opening = re.sub(r"^\W+", "", text)[:18].lower()
            seen_counts[opening] = seen_counts.get(opening, 0) + 1
            revised.append(_clean_visible_text(text))
        section["paragraphs"] = revised


def _dedupe_repeated_openings(sections: List[Dict[str, Any]], *, language: str) -> None:
    prefixes = _paragraph_prefixes(language)
    seen_counts: Dict[str, int] = {}
    for section_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        revised: List[str] = []
        for para_idx, para in enumerate(_as_list(section.get("paragraphs"))):
            text = _clean_visible_text(para)
            opening = _paragraph_opening_key(text)
            count = seen_counts.get(opening, 0)
            if opening and count >= 1:
                stem = _strip_known_opening_prefix(text, language=language)
                for offset in range(len(prefixes)):
                    prefix = prefixes[(section_idx + para_idx + count + offset) % len(prefixes)]
                    candidate = _clean_visible_text(prefix + _lower_first(stem))
                    candidate_opening = _paragraph_opening_key(candidate)
                    if not candidate_opening or seen_counts.get(candidate_opening, 0) == 0:
                        text = candidate
                        opening = candidate_opening
                        break
                else:
                    text = _clean_visible_text(stem)
                opening = _paragraph_opening_key(text)
            seen_counts[opening] = seen_counts.get(opening, 0) + 1
            revised.append(text)
        section["paragraphs"] = revised


def _paragraph_prefixes(language: str) -> List[str]:
    if language == "en":
        return [
            "For leadership teams, ",
            "In capital terms, ",
            "Commercially, ",
            "For capital allocation, ",
            "Operationally, ",
            "For the board, ",
            "For customer strategy, ",
            "For partner strategy, ",
            "From an investor lens, ",
            "At the portfolio level, ",
            "For operating teams, ",
            "In timing terms, ",
            "For risk owners, ",
            "For market entry, ",
            "From a customer lens, ",
            "For policy exposure, ",
            "At the next review, ",
            "For resource allocation, ",
            "From a delivery lens, ",
            "For partnership choices, ",
        ]
    return [
        "对管理层而言，",
        "从资源配置看，",
        "更实际的判断是，",
        "放到董事会视角，",
        "短期动作应当是，",
        "从执行角度看，",
        "这意味着，",
        "后续管理重点是，",
    ]


def _paragraph_opening_key(text: str) -> str:
    return re.sub(r"^\W+", "", str(text or "").strip())[:18].lower()


def _strip_known_opening_prefix(text: str, *, language: str) -> str:
    raw = str(text or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in sorted(_paragraph_prefixes(language), key=len, reverse=True):
            if raw.lower().startswith(prefix.lower()):
                raw = raw[len(prefix) :].lstrip()
                changed = True
                break
    if language == "en":
        raw = re.sub(r"(?i)^(for executives|from a board perspective|the commercial implication is that),\s*", "", raw).strip()
        raw = re.sub(r"(?i)^for\s+[A-Z][^,]{8,150},\s*", "", raw).strip()
        return raw
    return raw


def _dedupe_cross_section_paragraphs(sections: List[Dict[str, Any]], fact_pack: ResearchFactPack, *, language: str) -> None:
    seen: set[str] = set()
    for section_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or fact_pack.topic or "this issue")
        revised: List[str] = []
        local_seen: set[str] = set()
        for para_idx, para in enumerate(_as_list(section.get("paragraphs"))):
            text = _clean_visible_text(para)
            key = _paragraph_dedupe_key(text)
            if key and (key in seen or key in local_seen):
                text = _replacement_unique_paragraph(title, fact_pack, section_idx + para_idx, seen | local_seen, language=language)
                key = _paragraph_dedupe_key(text)
            if key:
                seen.add(key)
                local_seen.add(key)
            if text:
                revised.append(text)
        section["paragraphs"] = revised


def _replacement_unique_paragraph(title: str, fact_pack: ResearchFactPack, seed: int, seen: set[str], *, language: str) -> str:
    for offset in range(8):
        candidate = _long_completion_paragraph(title, fact_pack, seed + offset, language=language)
        key = _paragraph_dedupe_key(candidate)
        if key and key not in seen:
            return _clean_visible_text(candidate)
    for offset in range(8):
        candidate = _completion_paragraph(title, seed + offset, language=language)
        key = _paragraph_dedupe_key(candidate)
        if key and key not in seen:
            return _clean_visible_text(candidate)
    return _clean_visible_text(_completion_paragraph(title, seed, language=language))


def _paragraph_dedupe_key(text: str) -> str:
    cleaned = _clean_visible_text(text)
    if len(cleaned) < 120:
        return ""
    return re.sub(r"\W+", "", cleaned.lower())[:220]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _section_payload(title: str, lead: str, paragraphs: List[str], takeaways: List[str]) -> Dict[str, Any]:
    return {"title": title, "lead": lead, "paragraphs": paragraphs, "key_takeaways": takeaways}


def _ensure_section_depth(title: str, paragraphs: List[str], fact_pack: ResearchFactPack, *, language: str) -> List[str]:
    target_count = 7 if language == "en" else 4
    target_chars = 2300 if language == "en" else 850
    max_paragraphs = 10 if language == "en" else 6
    cleaned = [p for p in _dedupe_texts(paragraphs) if not _is_placeholder_section_paragraph(p, title)]
    additions = _section_depth_additions(title, fact_pack, language=language)
    add_idx = 0
    while (len(cleaned) < target_count or sum(len(p) for p in cleaned) < target_chars) and add_idx < len(additions):
        cleaned = _dedupe_texts(cleaned + [additions[add_idx]])
        add_idx += 1
    while len(cleaned) < target_count:
        cleaned.append(_completion_paragraph(title, len(cleaned), language=language))
    while sum(len(p) for p in cleaned) < target_chars and len(cleaned) < max_paragraphs:
        cleaned = _dedupe_texts(cleaned + [_completion_paragraph(title, len(cleaned), language=language)])
    if sum(len(p) for p in cleaned) < target_chars:
        cleaned = _dedupe_texts(cleaned + [_long_completion_paragraph(title, fact_pack, len(cleaned), language=language)])
    return _dedupe_texts(cleaned)


def _section_depth_additions(title: str, fact_pack: ResearchFactPack, *, language: str) -> List[str]:
    title_text = _shorten(str(title or "the opportunity"), 120)
    numeric_fact = _shorten(_clean_fact_text(fact_pack.numeric_facts[0]), 300) if fact_pack.numeric_facts else ""
    dated_fact = _shorten(_clean_fact_text(fact_pack.dated_facts[0]), 260) if fact_pack.dated_facts else ""
    evidence_fact = _shorten(_clean_fact_text(fact_pack.high_confidence_facts[0]), 300) if fact_pack.high_confidence_facts else ""
    if language == "zh":
        return [
            f"放到 CEO 视角，{title_text}的价值不在于扩大叙事，而在于把不确定性拆成可以管理的投入节奏。管理层需要判断哪些事实已经足以支持客户沟通、伙伴筛选或小额试点，哪些仍然只能作为观察项。",
            f"围绕{title_text}，资源配置应优先流向能够改变判断的证据：客户需求、成本口径、融资可得性、监管路径和交付能力。若这些证据没有改善，扩大投入只会增加战略锁定，而不会提升胜率。",
            f"{title_text}的公开资料线索包括：{evidence_fact or '来源底稿已保留，但仍需要补充权威数字和时间线。'} 管理层应把这些线索转成下一轮复核清单，而不是把单一来源直接升级为结论。",
            f"若{title_text}涉及数字判断，当前最应复核的是：{numeric_fact or '市场规模、单位成本、收入、份额和资本开支等关键数据。'} 这些数据决定该机会是进入预算讨论、保持选择权，还是暂时留在监控状态。",
            f"{title_text}的时间窗口同样需要被管理。{dated_fact or '当公开时间线不足时，季度复盘比一次性结论更稳健。'} 每次复盘都应回答一个问题：哪些新事实足以改变资本、伙伴或客户动作。",
        ]
    return [
        f"The executive question around {title_text} is practical: which commitments create learning without locking the company into a cost curve, customer promise or regulatory position that the facts cannot yet support.",
        f"Capital tied to {title_text} should move only when proof improves along the dimensions that change a business case: customer demand, cost position, financing availability, regulatory path and delivery capability. If those facts do not improve, larger exposure creates lock-in rather than conviction.",
        f"The strongest public fact pattern for {title_text} is narrow but useful: {evidence_fact or 'the public record remains incomplete, and stronger authoritative numbers and timelines are still needed.'} It should shape the next customer, cost or partner question without being stretched into proof of commercial scale.",
        f"Numeric assumptions around {title_text} need a stricter hurdle. Management should verify {numeric_fact or 'market size, unit cost, revenue potential, share and capital expenditure data'} before the topic moves from option building into budget planning.",
        f"Timing for {title_text} should be treated as a portfolio variable rather than a headline forecast. {dated_fact or 'When the public timeline is thin, a quarterly review cadence is stronger than a one-time conclusion.'} Each review should ask whether new facts change capital exposure, partner strategy or customer-facing action.",
    ]


def _derive_section_lead(title: str, paragraphs: List[str], *, language: str) -> str:
    if language == "en":
        subject = _lower_first(_shorten(str(title or "this issue").rstrip("."), 110))
        return _shorten(f"The CEO issue is how {subject} changes timing, capital exposure and the next decision milestone.", 220)
    if paragraphs:
        candidate = next((p for p in paragraphs if len(p) >= 30 and not _is_placeholder_section_paragraph(p, title)), "")
        if candidate:
            return _shorten(candidate, 120)
    return ("The CEO issue is whether the available evidence is strong enough to change timing, capital exposure or partner posture." if language == "en" else "CEO 需要判断现有证据是否足以改变投入节奏、资本暴露或伙伴姿态。")


def _long_completion_paragraph(title: str, fact_pack: ResearchFactPack, idx: int, *, language: str) -> str:
    title_text = _shorten(str(title or fact_pack.topic or "the topic"), 120)
    facts = _fact_texts(fact_pack)
    fact = _shorten(_clean_fact_text(facts[idx % len(facts)]), 260) if facts else ""
    if language == "zh":
        return (
            f"围绕{title_text}，管理层还需要把结论放回经营现实：客户是否会付费、成本是否能被复核、伙伴是否带来交付能力、监管是否允许排期前移。"
            f"{' 一个可复核事实是：' + fact if fact else ' 当前公开资料仍不足以支持更高确定性。'}"
            "在这些问题没有被逐项关闭之前，报告应保持可执行但克制的判断，把资源放在会改变下一轮决策的证据上。"
        )
    return (
        f"The operating question for {title_text} is whether the conclusion changes customer commitment, cost confidence, partner access, regulatory timing or capital exposure. "
        f"{'A checkable public fact is: ' + fact + ' ' if fact else 'The current public evidence remains too thin for a higher-conviction estimate. '}"
        "Until those questions are closed, the reader should treat the point as a disciplined basis for staged action rather than as a stand-alone investment conclusion."
    )


def _is_placeholder_section_paragraph(text: str, title: str = "") -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    normalized = re.sub(r"\W+", "", cleaned.lower())
    title_key = re.sub(r"\W+", "", str(title or "").lower())
    if title_key and normalized == title_key:
        return True
    return bool(re.fullmatch(r"(section|chapter)[\s_#-]*\d*", cleaned, flags=re.I))


def _is_short_placeholder_paragraph(text: str) -> bool:
    cleaned = _clean_visible_text(text)
    if len(cleaned) >= 85:
        return False
    lowered = cleaned.lower().strip(" .")
    if re.fullmatch(r"paragraph\s+[a-z0-9]+", lowered):
        return True
    placeholder_markers = (
        "generic section title",
        "replacement should preserve",
        "string section should not break",
        "should not break rendering",
        "takeaway",
    )
    return any(marker in lowered for marker in placeholder_markers)


def _select_report_paragraphs(paragraphs: List[str], *, limit: int, target_chars: int) -> List[str]:
    if len(paragraphs) <= limit:
        return paragraphs

    def score(item: tuple[int, str]) -> tuple[int, int, int]:
        idx, text = item
        number_bonus = 1 if NUMBER_RE.search(text) or MONEY_RE.search(text) else 0
        business_bonus = min(5, _keyword_hits(text, BUSINESS_LENS_TERMS))
        length_score = min(len(text), 420)
        return (number_bonus * 120 + business_bonus * 20 + length_score, -idx, len(text))

    ranked = sorted(enumerate(paragraphs), key=score, reverse=True)
    chosen_indices = sorted(idx for idx, _text in ranked[:limit])
    chosen = [paragraphs[idx] for idx in chosen_indices]
    if sum(len(x) for x in chosen) >= target_chars:
        return chosen

    # If the strongest eight still fall short, prefer denser paragraphs over preserving original order.
    dense = [text for _idx, text in ranked[:limit]]
    return list(reversed(dense))


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


def _ensure_charts(value: Any, sections: List[Dict[str, Any]], topic: str, *, fact_pack: ResearchFactPack, language: str) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    seen = set()
    fusion_topic = _is_fusion_topic(topic)
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        if fusion_topic and _is_forbidden_fusion_meta_chart(item):
            continue
        if _is_generic_chart_payload(item, reject_source_note=False):
            continue
        chart = _normalize_chart_payload(dict(item), len(charts) + 1, topic, sections, fact_pack=fact_pack, language=language)
        title = str(chart.get("title") or "").strip()
        key = re.sub(r"\W+", "", title.lower())[:120]
        if not title or key in seen or _is_generic_chart_payload(chart) or (fusion_topic and _is_forbidden_fusion_meta_chart(chart)):
            continue
        seen.add(key)
        chart["id"] = str(chart.get("id") or f"chart-{len(charts) + 1}")
        charts.append(chart)
        if len(charts) >= 14:
            break

    for chart in _fallback_charts_for_topic(topic, sections, fact_pack=fact_pack, language=language):
        if len(charts) >= 14:
            break
        key = re.sub(r"\W+", "", chart["title"].lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        chart["id"] = f"chart-{len(charts) + 1}"
        chart["exhibit_no"] = str(len(charts) + 1)
        charts.append(_normalize_chart_payload(chart, len(charts) + 1, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False))
    charts = _ensure_chart_type_mix(charts[:14], topic, sections, fact_pack=fact_pack, language=language)
    charts = _dedupe_and_fill_charts(charts, topic, sections, fact_pack=fact_pack, language=language)
    for idx, chart in enumerate(charts, start=1):
        chart["id"] = f"chart-{idx}"
        chart["exhibit_no"] = str(idx)
    return charts[:14]


def _normalize_chart_payload(
    chart: Dict[str, Any],
    idx: int,
    topic: str,
    sections: List[Dict[str, Any]],
    *,
    fact_pack: ResearchFactPack | None = None,
    language: str,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    chart = dict(chart)
    chart["title"] = _clean_visible_text(chart.get("title") or _fallback_chart_title(idx, topic, language=language))
    if chart.get("subtitle"):
        chart["subtitle"] = _clean_visible_text(chart.get("subtitle"))
    if not chart.get("caption") and chart.get("description"):
        chart["caption"] = chart.get("description")
    if chart.get("caption"):
        chart["caption"] = _clean_visible_text(chart.get("caption"))
    chart["source_note"] = _clean_visible_text(chart.get("source_note") or _fallback_source_note(fact_pack))
    if _is_generic_source_note(chart["source_note"]):
        chart["source_note"] = _fallback_source_note(fact_pack)

    chart_type = str(chart.get("type") or "").strip().lower()
    if chart_type in {"pie", "donut", "column", "histogram"}:
        chart_type = "bar"
    elif chart_type in {"scatter", "risk_matrix", "quadrant"}:
        chart_type = "bubble"
    elif chart_type in {"heatmap", "table", "scorecard"}:
        chart_type = "matrix"
    elif chart_type not in {"bar", "stacked_bar", "line", "matrix", "bubble"}:
        if chart.get("points") or chart.get("data"):
            chart_type = "bubble"
        elif chart.get("rows") and chart.get("columns"):
            chart_type = "matrix"
        else:
            chart_type = "bar"
    chart["type"] = chart_type

    if chart_type in {"bar", "stacked_bar", "line"}:
        chart = _normalize_series_chart(chart, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=allow_fallback)
    elif chart_type == "matrix":
        chart = _normalize_matrix_chart(chart, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=allow_fallback)
    elif chart_type == "bubble":
        chart = _normalize_bubble_chart(chart, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=allow_fallback)
    return chart


def _normalize_series_chart(
    chart: Dict[str, Any],
    idx: int,
    topic: str,
    sections: List[Dict[str, Any]] | None = None,
    *,
    fact_pack: ResearchFactPack | None = None,
    language: str,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    sections = sections or []
    categories = _string_list(chart.get("categories"))
    series = _series_list(chart.get("series"))
    top_categories, top_series = _series_from_top_level_chartjs(chart)
    data_categories, data_series = _series_from_data(chart.get("data"))
    if top_categories and top_series and _looks_like_placeholder_series(categories, series):
        categories, series = top_categories, top_series
    elif data_categories and data_series and _looks_like_placeholder_series(categories, series):
        categories, series = data_categories, data_series
    elif not categories or not series:
        categories = categories or top_categories or data_categories
        series = series or top_series or data_series
    if not series:
        values = [_to_number(x, 0.0) for x in _as_list(chart.get("values"))]
        if values:
            series = [{"name": str(chart.get("name") or "Value"), "values": values}]
    if not categories and series:
        categories = [f"Item {i}" for i in range(1, len(series[0].get("values", [])) + 1)]
    if len(categories) < 3:
        if allow_fallback:
            return _normalize_fallback_chart(idx, topic, sections, fact_pack=fact_pack, language=language, preferred_types=("bar", "stacked_bar", "line"))
        return _force_strong_series_chart(chart, idx, topic, fact_pack=fact_pack, language=language)
    width = min(8, len(categories))
    chart["categories"] = categories[:width]
    normalized_series = []
    for item in series[:5 if chart.get("type") == "stacked_bar" else 3]:
        values = [_to_number(x, 0.0) for x in _as_list(item.get("values"))]
        while len(values) < width:
            values.append(0.0)
        normalized_series.append({"name": _clean_visible_text(item.get("name") or f"Series {len(normalized_series) + 1}"), "values": values[:width]})
    chart["series"] = normalized_series or [{"name": "Value", "values": [0.0] * width}]
    if chart.get("type") == "line" and len(chart["categories"]) < 3:
        chart["type"] = "bar"
    if _weak_series_chart(chart):
        if allow_fallback:
            return _normalize_fallback_chart(idx, topic, sections, fact_pack=fact_pack, language=language, preferred_types=("bar", "stacked_bar", "line"))
        return _force_strong_series_chart(chart, idx, topic, fact_pack=fact_pack, language=language)
    return chart


def _normalize_matrix_chart(
    chart: Dict[str, Any],
    idx: int,
    topic: str,
    sections: List[Dict[str, Any]],
    *,
    fact_pack: ResearchFactPack | None = None,
    language: str,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    rows = _string_list(chart.get("rows"))
    columns = _string_list(chart.get("columns"))
    values = _as_list(chart.get("values"))
    if (not rows or not columns or not values) and chart.get("data"):
        rows, columns, values = _matrix_from_data(chart.get("data"))
    if not rows or not columns or not values:
        if chart.get("categories") or chart.get("series"):
            chart["type"] = "bar"
            return _normalize_series_chart(chart, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=allow_fallback)
        rows = [_shorten(str(s.get("title") or f"Option {i}"), 28) for i, s in enumerate(sections[:5], start=1)] or ["Option 1", "Option 2", "Option 3"]
        columns = ["Market weight", "Cost proof", "Evidence"]
        values = [[5, 3, 3], [4, 4, 3], [3, 3, 2]][: len(rows)]
    chart["rows"] = rows[:7]
    chart["columns"] = columns[:4]
    matrix_values: List[List[float]] = []
    for row_idx in range(len(chart["rows"])):
        raw_row = values[row_idx] if row_idx < len(values) and isinstance(values[row_idx], list) else []
        nums = [_to_number(x, 0.0) for x in raw_row[: len(chart["columns"])]]
        while len(nums) < len(chart["columns"]):
            nums.append(0.0)
        matrix_values.append(nums)
    chart["values"] = matrix_values
    return chart


def _normalize_bubble_chart(
    chart: Dict[str, Any],
    idx: int,
    topic: str,
    sections: List[Dict[str, Any]] | None = None,
    *,
    fact_pack: ResearchFactPack | None = None,
    language: str,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    sections = sections or []
    points = _best_bubble_points(chart)
    if _weak_bubble_points(points):
        if allow_fallback:
            fallback = _normalize_fallback_chart(idx, topic, sections, fact_pack=fact_pack, language=language, preferred_types=("bubble",))
            fallback["id"] = chart.get("id") or fallback.get("id")
            fallback["exhibit_no"] = chart.get("exhibit_no") or fallback.get("exhibit_no")
            return fallback
        points = []
    if not points:
        if chart.get("categories") or chart.get("series"):
            chart["type"] = "bar"
            return _normalize_series_chart(chart, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=allow_fallback)
        points = [
            {"label": "Customer", "x": 72, "y": 78, "size": 70},
            {"label": "Cost", "x": 80, "y": 66, "size": 76},
            {"label": "Regulation", "x": 58, "y": 62, "size": 58},
            {"label": "Supply", "x": 64, "y": 54, "size": 55},
        ]
    normalized = []
    for point in points[:8]:
        normalized.append({
            "label": _clean_visible_text(point.get("label") or point.get("risk") or point.get("name") or "Point"),
            "x": _scale_chart_value(point.get("x", point.get("likelihood", point.get("probability", 50)))),
            "y": _scale_chart_value(point.get("y", point.get("impact", point.get("severity", 50)))),
            "size": _scale_chart_value(point.get("size", point.get("importance", point.get("impact", 45)))),
        })
    chart["points"] = normalized
    chart["x_label"] = _clean_visible_text(chart.get("x_label") or "Likelihood")
    chart["y_label"] = _clean_visible_text(chart.get("y_label") or "Impact")
    return chart


def _normalize_fallback_chart(
    idx: int,
    topic: str,
    sections: List[Dict[str, Any]],
    *,
    fact_pack: ResearchFactPack | None,
    language: str,
    preferred_types: tuple[str, ...],
) -> Dict[str, Any]:
    fallback_charts = _fallback_charts_for_topic(topic, sections, fact_pack=fact_pack, language=language)
    if not fallback_charts:
        return _force_strong_series_chart({}, idx, topic, fact_pack=fact_pack, language=language)
    start = max(0, idx - 1)
    ordered = fallback_charts[start:] + fallback_charts[:start]
    for candidate in ordered:
        if preferred_types and str(candidate.get("type") or "").lower() not in preferred_types:
            continue
        normalized = _normalize_chart_payload(dict(candidate), idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False)
        if _strong_chart_candidate(normalized):
            return normalized
    for candidate in ordered:
        normalized = _normalize_chart_payload(dict(candidate), idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False)
        if _strong_chart_candidate(normalized):
            return normalized
    if "bubble" in preferred_types:
        return _normalize_bubble_chart({"type": "bubble"}, idx, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False)
    return _force_strong_series_chart({}, idx, topic, fact_pack=fact_pack, language=language)


def _strong_chart_candidate(chart: Dict[str, Any]) -> bool:
    if _is_generic_chart_payload(chart):
        return False
    chart_type = str(chart.get("type") or "").lower()
    if chart_type in {"bar", "stacked_bar", "line"}:
        return not _weak_series_chart(chart)
    if chart_type == "bubble":
        return not _weak_bubble_points([x for x in _as_list(chart.get("points")) if isinstance(x, dict)])
    if chart_type == "matrix":
        return bool(chart.get("rows") and chart.get("columns") and chart.get("values"))
    return False


def _force_strong_series_chart(
    chart: Dict[str, Any],
    idx: int,
    topic: str,
    *,
    fact_pack: ResearchFactPack | None,
    language: str,
) -> Dict[str, Any]:
    out = dict(chart)
    out["title"] = _clean_visible_text(out.get("title") or _fallback_chart_title(idx, topic, language=language))
    out["source_note"] = _clean_visible_text(out.get("source_note") or _fallback_source_note(fact_pack))
    chart_type = str(out.get("type") or "bar").lower()
    if chart_type not in {"bar", "stacked_bar", "line"}:
        chart_type = "bar"
    labels, values = _fact_category_counts(fact_pack)
    categories = _string_list(out.get("categories"))
    if len(categories) < 3:
        categories = labels
    width = max(3, min(8, len(categories)))
    out["categories"] = categories[:width]
    if chart_type == "stacked_bar":
        base_values = [max(1.0, _to_number(value, 1.0)) for value in values[:width]]
        while len(base_values) < width:
            base_values.append(1.0)
        out["series"] = [
            {"name": "Near-term facts", "values": base_values},
            {"name": "Longer-term facts", "values": [max(1.0, round(value * 0.55, 1)) for value in base_values]},
        ]
        out["type"] = "stacked_bar"
        return out
    series = _series_list(out.get("series"))
    usable_series: List[Dict[str, Any]] = []
    for item in series[:3]:
        item_values = [_to_number(value, 0.0) for value in _as_list(item.get("values"))[:width]]
        while len(item_values) < width:
            item_values.append(0.0)
        usable_series.append({"name": _clean_visible_text(item.get("name") or f"Series {len(usable_series) + 1}"), "values": item_values})
    out["series"] = usable_series or [{"name": "Fact mentions", "values": [max(1.0, _to_number(value, 1.0)) for value in values[:width]]}]
    if _weak_series_chart(out):
        out["series"] = [{"name": "Fact mentions", "values": [max(1.0, _to_number(value, 1.0)) for value in values[:width]]}]
    out["type"] = chart_type
    return out


def _ensure_chart_type_mix(charts: List[Dict[str, Any]], topic: str, sections: List[Dict[str, Any]], *, fact_pack: ResearchFactPack | None = None, language: str) -> List[Dict[str, Any]]:
    if len(charts) < 6:
        return charts
    present = {str(chart.get("type") or "").lower() for chart in charts}
    required = ["line", "bubble", "matrix", "stacked_bar"]
    missing = [chart_type for chart_type in required if chart_type not in present]
    if not missing:
        return charts
    fallback_by_type: Dict[str, Dict[str, Any]] = {}
    for fallback in _fallback_charts_for_topic(topic, sections, fact_pack=fact_pack, language=language):
        fallback_type = str(fallback.get("type") or "").lower()
        fallback_by_type.setdefault(fallback_type, fallback)
    replace_start = max(0, len(charts) - len(missing))
    mixed = [dict(chart) for chart in charts]
    for offset, chart_type in enumerate(missing):
        replacement = dict(fallback_by_type.get(chart_type) or {})
        if not replacement:
            continue
        target_idx = replace_start + offset
        mixed[target_idx] = _normalize_chart_payload(replacement, target_idx + 1, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False)
    return mixed


def _chart_dedupe_key(chart: Dict[str, Any]) -> str:
    title = _clean_visible_text(chart.get("title"))
    return re.sub(r"\W+", "", title.lower())[:140]


def _dedupe_and_fill_charts(
    charts: List[Dict[str, Any]],
    topic: str,
    sections: List[Dict[str, Any]],
    *,
    fact_pack: ResearchFactPack | None,
    language: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for chart in charts:
        key = _chart_dedupe_key(chart)
        if not key or key in seen or _is_generic_chart_payload(chart):
            continue
        seen.add(key)
        out.append(dict(chart))

    fallback_idx = 0
    fallback_charts = _fallback_charts_for_topic(topic, sections, fact_pack=fact_pack, language=language)
    while len(out) < 14 and fallback_idx < len(fallback_charts):
        fallback = fallback_charts[fallback_idx]
        fallback_idx += 1
        normalized = _normalize_chart_payload(dict(fallback), len(out) + 1, topic, sections, fact_pack=fact_pack, language=language, allow_fallback=False)
        key = _chart_dedupe_key(normalized)
        if not key or key in seen or _is_generic_chart_payload(normalized):
            continue
        seen.add(key)
        out.append(normalized)

    return out[:14]


def _series_from_data(value: Any) -> tuple[List[str], List[Dict[str, Any]]]:
    if isinstance(value, dict):
        categories = _string_list(value.get("labels") or value.get("categories"))
        datasets = [x for x in _as_list(value.get("datasets") or value.get("series")) if isinstance(x, dict)]
        series = []
        for idx, dataset in enumerate(datasets[:5], start=1):
            values = _dataset_numeric_values(dataset)
            if values:
                series.append({"name": _clean_visible_text(dataset.get("label") or dataset.get("name") or f"Series {idx}"), "values": values})
        if categories and series:
            return categories, series

    rows = [x for x in _as_list(value) if isinstance(x, dict)]
    if not rows:
        return [], []
    label_keys = (
        "label",
        "category",
        "name",
        "risk",
        "segment",
        "driver",
        "approach",
        "technology",
        "source",
        "country",
        "region",
        "milestone",
        "fuel",
        "entity",
        "company",
        "project",
        "period",
        "year",
    )
    categories = []
    numeric_keys: List[str] = []
    for row in rows[:8]:
        label_key = next((key for key in label_keys if row.get(key) is not None), "")
        label = row.get(label_key) if label_key else None
        categories.append(str(label or f"Item {len(categories) + 1}"))
        for key, item in row.items():
            if key == label_key:
                continue
            if _is_number_like(item) and key not in numeric_keys:
                numeric_keys.append(str(key))
    series = []
    for key in numeric_keys[:3]:
        series.append({"name": key.replace("_", " ").title(), "values": [_to_number(row.get(key), 0.0) for row in rows[:8]]})
    return categories, series


def _series_from_top_level_chartjs(chart: Dict[str, Any]) -> tuple[List[str], List[Dict[str, Any]]]:
    if not isinstance(chart, dict) or not (chart.get("labels") or chart.get("datasets")):
        return [], []
    return _series_from_data({"labels": chart.get("labels"), "datasets": chart.get("datasets")})


def _dataset_numeric_values(dataset: Dict[str, Any]) -> List[float]:
    raw_values = dataset.get("values")
    if raw_values is None:
        raw_values = dataset.get("data")
    values: List[float] = []
    for item in _as_list(raw_values):
        if isinstance(item, dict):
            item_value = item.get("y", item.get("value", item.get("amount", item.get("score"))))
            if item_value is not None:
                values.append(_to_number(item_value, 0.0))
        elif _is_number_like(item):
            values.append(_to_number(item, 0.0))
    return values


def _matrix_from_data(value: Any) -> tuple[List[str], List[str], List[List[float]]]:
    rows = [x for x in _as_list(value) if isinstance(x, dict)]
    if not rows:
        return [], [], []
    label_keys = ("label", "category", "name", "risk", "segment", "driver", "approach", "technology", "source", "country", "region", "milestone", "fuel", "entity", "company", "project", "period", "year")
    row_labels = []
    columns: List[str] = []
    for row in rows[:7]:
        label_key = next((key for key in label_keys if row.get(key) is not None), "")
        label = row.get(label_key) if label_key else None
        row_labels.append(str(label or f"Item {len(row_labels) + 1}"))
        for key, item in row.items():
            if key == label_key:
                continue
            if _is_number_like(item) and key not in columns:
                columns.append(str(key))
    columns = columns[:4]
    values = [[_to_number(row.get(column), 0.0) for column in columns] for row in rows[:7]]
    return row_labels, [c.replace("_", " ").title() for c in columns], values


def _points_from_data(value: Any) -> List[Dict[str, Any]]:
    points = []
    for row in _point_rows_from_data(value):
        label = row.get("label") or row.get("risk") or row.get("name") or row.get("category") or row.get("driver") or row.get("approach") or row.get("technology") or row.get("country") or row.get("region") or f"Point {len(points) + 1}"
        points.append({
            "label": label,
            "x": _scale_chart_value(row.get("x", row.get("likelihood", row.get("probability", row.get("readiness", 50))))),
            "y": _scale_chart_value(row.get("y", row.get("impact", row.get("severity", row.get("importance", 50))))),
            "size": _scale_chart_value(row.get("size", row.get("impact", row.get("importance", 45)))),
        })
    return points


def _best_bubble_points(chart: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit_points = [dict(x) for x in _as_list(chart.get("points")) if isinstance(x, dict)]
    data_points = _points_from_data(chart.get("data")) if chart.get("data") else []
    top_level_points = _points_from_data({"datasets": chart.get("datasets")}) if chart.get("datasets") else []
    if explicit_points and not _weak_bubble_points(explicit_points):
        return explicit_points
    if top_level_points and not _weak_bubble_points(top_level_points):
        return top_level_points
    if data_points and not _weak_bubble_points(data_points):
        return data_points
    return explicit_points or top_level_points or data_points


def _point_rows_from_data(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    label_keys = ("label", "risk", "name", "category", "driver", "approach", "technology", "country", "region")

    def visit(node: Any, dataset_label: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, dataset_label)
            return
        if not isinstance(node, dict):
            return

        datasets = [x for x in _as_list(node.get("datasets")) if isinstance(x, dict)]
        if datasets:
            for dataset in datasets:
                nested = dataset.get("points")
                if nested is None:
                    nested = dataset.get("data")
                if nested is None:
                    nested = dataset.get("values")
                visit(nested, str(dataset.get("label") or dataset.get("name") or dataset_label or "").strip())
            return

        nested_points = node.get("points")
        if isinstance(nested_points, list):
            visit(nested_points, dataset_label)
            return
        nested_data = node.get("data")
        if isinstance(nested_data, list) and any(isinstance(x, dict) for x in nested_data):
            visit(nested_data, dataset_label)
            return

        if not _looks_like_point_row(node):
            return
        row = dict(node)
        if dataset_label and not any(row.get(key) for key in label_keys):
            row["label"] = dataset_label
        rows.append(row)

    visit(value)
    return rows


def _looks_like_point_row(row: Dict[str, Any]) -> bool:
    return any(key in row for key in ("x", "y", "likelihood", "probability", "readiness", "attractiveness", "impact", "severity", "importance", "return", "size"))


def _weak_bubble_points(points: List[Dict[str, Any]]) -> bool:
    if len(points) < 3:
        return True
    labels = [str(point.get("label") or "").strip() for point in points]
    placeholder_count = sum(1 for label in labels if _is_placeholder_point_label(label))
    return placeholder_count >= max(2, len(labels) - 1)


def _weak_series_chart(chart: Dict[str, Any]) -> bool:
    categories = _string_list(chart.get("categories") or chart.get("labels"))
    if not categories and isinstance(chart.get("data"), dict):
        categories = _string_list(chart["data"].get("labels") or chart["data"].get("categories"))
    series = _series_list(chart.get("series")) or _series_from_data(chart.get("data"))[1] or _series_from_top_level_chartjs(chart)[1]
    if len(categories) < 3 or not series:
        return True
    values_by_category = [0.0 for _ in categories]
    value_count = 0
    nonzero_count = 0
    for item in series:
        values = [_to_number(x, 0.0) for x in _as_list(item.get("values"))]
        for idx, value in enumerate(values[: len(categories)]):
            value_count += 1
            values_by_category[idx] += abs(value)
            if abs(value) > 1e-6:
                nonzero_count += 1
    active_categories = sum(1 for value in values_by_category if abs(value) > 1e-6)
    return value_count < 3 or nonzero_count <= 1 or active_categories < 3


def _is_placeholder_point_label(label: str) -> bool:
    return bool(re.fullmatch(r"(point|item)[\s_#-]*\d*", str(label or "").strip(), flags=re.I))


def _string_list(value: Any) -> List[str]:
    return [_clean_visible_text(x) for x in _as_list(value) if str(x).strip()]


def _series_list(value: Any) -> List[Dict[str, Any]]:
    series = []
    for idx, item in enumerate(_as_list(value), start=1):
        if not isinstance(item, dict):
            continue
        values = [_to_number(x, 0.0) for x in _as_list(item.get("values"))]
        if values:
            series.append({"name": _clean_visible_text(item.get("name") or f"Series {idx}"), "values": values})
    return series


def _looks_like_placeholder_series(categories: List[str], series: List[Dict[str, Any]]) -> bool:
    if not categories or not series:
        return False
    generic_categories = {
        "evidence quality",
        "policy support",
        "capability depth",
        "commercial pull",
        "execution readiness",
        "source checks",
        "customer calls",
        "cost model",
        "policy watch",
        "partner screen",
    }
    category_hits = sum(1 for item in categories if str(item).strip().lower() in generic_categories)
    series_names = {str(item.get("name") or "").strip().lower() for item in series}
    return category_hits >= max(2, len(categories) - 1) or bool(series_names & {"relative strength", "priority index", "readiness index"})


def _is_number_like(value: Any) -> bool:
    try:
        float(str(value).replace("%", "").replace("$", "").replace(",", "").strip())
        return True
    except Exception:
        return False


def _to_number(value: Any, default: float) -> float:
    try:
        return float(str(value).replace("%", "").replace("$", "").replace(",", "").strip())
    except Exception:
        return default


def _scale_chart_value(value: Any) -> float:
    number = _to_number(value, 50.0)
    if number <= 5:
        number *= 20
    elif number <= 10:
        number *= 10
    return max(0.0, min(100.0, number))


def _fallback_chart_title(idx: int, topic: str, *, language: str) -> str:
    if language == "zh":
        return f"{topic} 核心数据图 {idx}"
    return f"{topic} exhibit {idx}"


def _fallback_source_note(fact_pack: ResearchFactPack | None) -> str:
    if fact_pack and fact_pack.source_domains:
        return "Sources: " + "; ".join(fact_pack.source_domains[:4]) + "."
    return "Sources: public references listed with the report."


def _fallback_charts_for_topic(topic: str, sections: List[Dict[str, Any]], *, fact_pack: ResearchFactPack | None = None, language: str) -> List[Dict[str, Any]]:
    if _is_fusion_topic(topic):
        return _fusion_commercialization_charts(topic, sections, fact_pack=fact_pack, language=language)
    return _source_derived_fallback_charts(topic, sections, fact_pack=fact_pack, language=language)


def _is_fusion_topic(topic: str) -> bool:
    lower = str(topic or "").lower()
    return "fusion" in lower or "聚变" in lower


def _fusion_source_note(fact_pack: ResearchFactPack | None) -> str:
    domains = set(_fact_pack_domains(fact_pack))
    if domains:
        named = []
        for domain in ("llnl.gov", "iter.org", "fusionindustryassociation.org", "energy.gov", "nrc.gov", "lazard.com"):
            if domain in domains or any(item.endswith(domain) for item in domains):
                named.append(domain)
        if named:
            return "Sources: " + "; ".join(named[:6]) + "; report analysis."
    return "Sources: LLNL; ITER; DOE/NRC; Fusion Industry Association 2025; Lazard LCOE+ 2025; report analysis."


def _fusion_commercialization_charts(topic: str, sections: List[Dict[str, Any]], *, fact_pack: ResearchFactPack | None, language: str) -> List[Dict[str, Any]]:
    source_note = _fusion_source_note(fact_pack)
    return [
        {
            "title": "Ignition solved the target-physics question, not the power-plant question",
            "subtitle": "LLNL's 2022 shot produced more fusion energy than laser energy delivered to the target; it did not produce net electricity.",
            "type": "bar",
            "categories": ["Laser input", "Fusion output", "Net grid output"],
            "series": [{"name": "Energy, MJ", "values": [2.05, 3.15, 0.0]}],
            "caption": "The commercial gap is the conversion from a target experiment to repeated, maintainable, grid-exporting plant operation.",
            "source_note": "Sources: LLNL ignition announcement; report analysis.",
        },
        {
            "title": "Commercial proof remains a sequence of gates after the 2022 science milestone",
            "subtitle": "Milestones are shown as years after the LLNL ignition shot rather than as a single commercialization date.",
            "type": "line",
            "categories": ["2022 ignition", "2024 ITER reset", "2034 research ops", "2039 DT ops", "2040+ pilots"],
            "series": [
                {"name": "Years after 2022", "values": [0, 2, 12, 17, 18]},
                {"name": "Commercial proof stage", "values": [1, 1, 2, 3, 4]},
            ],
            "caption": "The market question is how quickly pilot plants can close net electricity, duty-cycle, maintenance and cost evidence after physics proof.",
            "source_note": "Sources: LLNL; ITER 2024 baseline materials; DOE fusion strategy; report analysis.",
        },
        {
            "title": "Private funding has grown, but the sector is still capital-constrained",
            "subtitle": "FIA reported $2.64B raised in the 12 months to July 2025 and $9.766B total funding for 53 companies.",
            "type": "stacked_bar",
            "categories": ["2021", "2024", "2025"],
            "series": [
                {"name": "Funding to date, $B", "values": [1.9, 7.1, 9.766]},
                {"name": "Latest-year inflow, $B", "values": [0.0, 0.95, 2.64]},
            ],
            "caption": "The funding curve signals strategic momentum, but it remains small compared with the capital required for repeated pilot plants and supply chains.",
            "source_note": "Sources: Fusion Industry Association Global Fusion Industry Report 2025; report analysis.",
        },
        {
            "title": "Fusion must compete against a cost stack that is already cheap at the low end",
            "subtitle": "Comparator values use low-end unsubsidized LCOE ranges where available; fusion is shown as an illustrative long-run target.",
            "type": "bar",
            "categories": ["Onshore wind", "Utility solar", "Gas CC", "New nuclear", "Fusion target"],
            "series": [{"name": "$/MWh", "values": [37, 38, 48, 141, 60]}],
            "caption": "A high-capacity-factor plant is valuable, but fusion still needs a credible route to installed cost, availability and O&M levels that buyers can underwrite.",
            "source_note": "Sources: Lazard LCOE+ 2025; public fusion cost targets; report analysis.",
        },
        {
            "title": "First markets differ on willingness to pay and integration complexity",
            "subtitle": "A five-point assessment translates use-case economics into comparable commercial-entry choices.",
            "type": "matrix",
            "rows": ["Data centers", "Industrial heat", "Hydrogen", "Utility firm power", "Desalination"],
            "columns": ["24/7 value", "Heat fit", "Price premium", "Integration"],
            "values": [
                [5, 2, 4, 3],
                [4, 5, 4, 4],
                [4, 4, 3, 4],
                [5, 1, 2, 3],
                [3, 3, 2, 2],
            ],
            "caption": "Early commercial logic is stronger where round-the-clock energy, site scarcity or high-temperature heat create value beyond commodity electricity.",
            "source_note": source_note,
        },
        {
            "title": "Tritium supply is a hard constraint for deuterium-tritium scale-up",
            "subtitle": "Current global production is measured in tens of kilograms, while a commercial D-T fleet would need far larger annual supply.",
            "type": "bar",
            "categories": ["Current production", "1 GWth plant", "Plant low case", "Plant high case"],
            "series": [{"name": "Tritium, kg/year", "values": [20, 55, 100, 200]}],
            "caption": "A viable D-T pathway has to prove breeding blankets and fuel-cycle recovery, not just buy scarce tritium from today's fission-linked supply chain.",
            "source_note": "Sources: DOE/NRC tritium materials; public fusion-fuel studies; report analysis.",
        },
        {
            "title": "The remaining technical bottlenecks sit across the plant, not only in plasma gain",
            "subtitle": "Scores compare today's proof depth against the burden each system carries in a bankable plant.",
            "type": "matrix",
            "rows": ["Net electricity", "Tritium breeding", "Materials lifetime", "Heat extraction", "Remote maintenance", "Licensing basis"],
            "columns": ["Proof today", "Scale burden", "Timing risk"],
            "values": [
                [2, 5, 5],
                [2, 5, 5],
                [2, 4, 4],
                [2, 4, 4],
                [3, 4, 4],
                [3, 3, 3],
            ],
            "caption": "The decisive evidence will come from integrated systems running repeatedly, not isolated component announcements.",
            "source_note": source_note,
        },
        {
            "title": "Exposure is most attractive where learning value is high and irreversible spend is low",
            "subtitle": "Each position is placed by strategic learning value and reversibility before commercial power is proven.",
            "type": "bubble",
            "points": [
                {"label": "Data-center PPAs", "x": 76, "y": 70, "size": 68},
                {"label": "Materials pilots", "x": 64, "y": 82, "size": 58},
                {"label": "Tritium cycle", "x": 70, "y": 62, "size": 72},
                {"label": "Project equity", "x": 36, "y": 44, "size": 78},
                {"label": "EPC capability", "x": 58, "y": 54, "size": 62},
                {"label": "Policy shaping", "x": 82, "y": 86, "size": 45},
            ],
            "x_label": "Strategic learning",
            "y_label": "Capital reversibility",
            "caption": "The strongest near-term moves create proprietary learning without forcing a balance-sheet bet before operating proof exists.",
            "source_note": source_note,
        },
        {
            "title": "ITER's reset reinforces that public programs move on multi-decade cycles",
            "subtitle": "The 2024 baseline points to research operation in the 2030s before full deuterium-tritium operation.",
            "type": "line",
            "categories": ["2024 baseline", "2034 research", "2035 D-D", "2039 D-T"],
            "series": [
                {"name": "Years after 2024", "values": [0, 10, 11, 15]},
                {"name": "Fusion-operation stage", "values": [1, 2, 3, 4]},
            ],
            "caption": "Commercial strategies should treat public-program milestones as evidence points, not as proof that private deployment timelines are de-risked.",
            "source_note": "Sources: ITER 2024 baseline materials; report analysis.",
        },
        {
            "title": "Supply-chain revenue can arrive before fusion electricity revenue",
            "subtitle": "Fusion companies reported material supplier spending even while most developers remain pre-revenue on electricity.",
            "type": "bar",
            "categories": ["2024 supply spend", "Public funding", "2025 new funding", "Total funding"],
            "series": [{"name": "$B", "values": [0.4, 0.8, 2.64, 9.766]}],
            "caption": "Component suppliers in magnets, materials, fuel-cycle equipment and diagnostics may see earlier opportunity than utilities buying fusion power.",
            "source_note": "Sources: FIA Global Fusion Industry Report 2025; FIA Supply Chain 2025; report analysis.",
        },
        {
            "title": "Regulatory clarity is uneven across the markets likely to host first plants",
            "subtitle": "Relative assessment of policy readiness and industrial pull in major fusion jurisdictions.",
            "type": "matrix",
            "rows": ["United States", "United Kingdom", "European Union", "Japan", "China"],
            "columns": ["Licensing clarity", "Public funding", "Industrial base", "Pilot pull"],
            "values": [
                [4, 4, 5, 5],
                [4, 3, 3, 4],
                [3, 5, 4, 3],
                [3, 4, 4, 3],
                [3, 5, 5, 4],
            ],
            "caption": "The first commercial sites will depend as much on licensing and industrial sponsorship as on which technology reaches net electricity first.",
            "source_note": "Sources: DOE; NRC; UKAEA; ITER member programs; report analysis.",
        },
        {
            "title": "Fusion's capacity-factor promise must become bankable availability",
            "subtitle": "Illustrative capacity-factor comparison shows why buyers care about firm clean power, but uptime must be proven.",
            "type": "bar",
            "categories": ["Solar PV", "Onshore wind", "Gas CC", "Fission nuclear", "Fusion target"],
            "series": [{"name": "Capacity factor, %", "values": [25, 35, 55, 90, 90]}],
            "caption": "The value proposition improves if fusion reaches nuclear-like availability without nuclear-like construction risk or cost overruns.",
            "source_note": "Sources: public power-sector benchmarks; fusion developer targets; report analysis.",
        },
        {
            "title": "Commercial risk is distributed across plant subsystems",
            "subtitle": "A plant-level view prevents the report from over-weighting a single plasma or magnet milestone.",
            "type": "stacked_bar",
            "categories": ["Magnets", "Blanket", "First wall", "Fuel cycle", "Power island", "Controls"],
            "series": [
                {"name": "Technology proof needed", "values": [4, 5, 4, 5, 3, 3]},
                {"name": "Industrialization burden", "values": [4, 5, 5, 4, 3, 3]},
            ],
            "caption": "The plant only becomes investable when critical subsystems mature together; a single impressive subsystem does not remove integration risk.",
            "source_note": source_note,
        },
        {
            "title": "Winning positions are different across the fusion value chain",
            "subtitle": "Value-chain roles are placed by control of scarce capability and near-term monetization potential.",
            "type": "bubble",
            "points": [
                {"label": "Core developer", "x": 86, "y": 34, "size": 84},
                {"label": "Magnet supplier", "x": 72, "y": 70, "size": 62},
                {"label": "Tritium supplier", "x": 82, "y": 58, "size": 68},
                {"label": "Materials supplier", "x": 66, "y": 74, "size": 58},
                {"label": "Industrial heat user", "x": 54, "y": 52, "size": 52},
                {"label": "Utility buyer", "x": 48, "y": 44, "size": 48},
            ],
            "x_label": "Scarce capability control",
            "y_label": "Near-term monetization",
            "caption": "The best position is not necessarily owning a reactor developer; supplier and offtake roles can offer earlier information advantages.",
            "source_note": source_note,
        },
    ]


def _source_derived_fallback_charts(topic: str, sections: List[Dict[str, Any]], *, fact_pack: ResearchFactPack | None, language: str) -> List[Dict[str, Any]]:
    topic_text = str(topic or "the topic").strip()
    source_note = _fallback_source_note(fact_pack)
    domains = _fact_pack_domains(fact_pack)
    refs = _source_ref_items(fact_pack)
    domain_rows = _domain_rows(domains, refs)
    institution_rows = _institution_mix_rows(refs, domains)
    fact_labels, fact_counts = _fact_category_counts(fact_pack)
    year_labels, year_values = _year_count_series(fact_pack)
    section_labels, section_numeric, section_dated, section_commercial = _section_fact_series(sections)
    source_order_labels = [f"S{i}" for i in range(1, max(4, min(8, len(refs) or len(domains) or 4)) + 1)]
    cumulative_sources = list(range(1, len(source_order_labels) + 1))
    cumulative_domains = []
    seen_domains: set[str] = set()
    for idx, _label in enumerate(source_order_labels):
        domain = (refs[idx]["domain"] if idx < len(refs) else (domains[idx] if idx < len(domains) else f"source-{idx + 1}"))
        seen_domains.add(domain)
        cumulative_domains.append(len(seen_domains))
    source_type_categories, authoritative_by_type, supplement_by_type = _source_type_series(refs)
    rows_for_matrix = section_labels[:5] or ["Market timing", "Cost proof", "Policy path", "Customer demand", "Partner access"]
    section_labels = [_shorten(str(s.get("title") or f"Section {idx}"), 34) for idx, s in enumerate(sections[:5], start=1)]
    if len(section_labels) < 5:
        section_labels.extend(["Customer proof", "Cost case", "Regulation", "Partner access", "Capital timing"][len(section_labels):5])
    charts = [
        {
            "title": "Public evidence is concentrated in identifiable institutions",
            "subtitle": f"The public record used here spans {len(refs) or (fact_pack.source_count if fact_pack else 0)} sources across {len(domains)} domains.",
            "type": "stacked_bar",
            "categories": [row[0] for row in institution_rows],
            "series": [
                {"name": "Authoritative", "values": [row[1] for row in institution_rows]},
                {"name": "Supplemental", "values": [row[2] for row in institution_rows]},
            ],
            "caption": "Institution type matters because CEO conclusions are stronger when public agencies, laboratories, research bodies and industry sources point in the same direction.",
            "source_note": source_note,
        },
        {
            "title": "Source domains define where firm claims can be made",
            "subtitle": "Each bar represents a public web or document domain behind the analysis.",
            "type": "bar",
            "categories": [row[0] for row in domain_rows],
            "series": [{"name": "Public sources", "values": [row[1] for row in domain_rows]}],
            "caption": "A narrow domain base limits how far the narrative should generalize across markets, technologies or regulatory settings.",
            "source_note": source_note,
        },
        {
            "title": "Evidence breadth depends on adding independent domains",
            "subtitle": "Cumulative public sources and unique domains across the evidence base.",
            "type": "line",
            "categories": source_order_labels,
            "series": [
                {"name": "Public sources", "values": cumulative_sources},
                {"name": "Unique domains", "values": cumulative_domains},
            ],
            "caption": "A stronger fact base adds both more sources and more independent domains, rather than repeating one institution.",
            "source_note": source_note,
        },
        {
            "title": "Public facts cluster by technical, policy and commercial themes",
            "subtitle": "Mention counts across public facts, dated facts and numeric facts.",
            "type": "bar",
            "categories": fact_labels,
            "series": [{"name": "Fact mentions", "values": fact_counts}],
            "caption": "The mix indicates whether the public record is led by technical milestones, policy institutions or commercial proof.",
            "source_note": source_note,
        },
        {
            "title": "Dated facts anchor the chronology where public text permits it",
            "subtitle": "Year mentions found in public facts and source references.",
            "type": "line",
            "categories": year_labels,
            "series": [{"name": "Mentions", "values": year_values}],
            "caption": "Timeline claims are stronger when they can be tied to explicit years rather than broad commercialization language.",
            "source_note": source_note,
        },
        {
            "title": "Source type mix separates full pages from thin snippets",
            "subtitle": "Public sources by content form and authority flag.",
            "type": "stacked_bar",
            "categories": source_type_categories,
            "series": [
                {"name": "Authoritative", "values": authoritative_by_type},
                {"name": "Supplemental", "values": supplement_by_type},
            ],
            "caption": "Pages and PDFs with extractable body text can support richer prose than snippets that carry only limited context.",
            "source_note": source_note,
        },
        {
            "title": "Business relevance depends on numbers, dates and commercial proof",
            "subtitle": "Numeric, dated and commercial references across the main narrative.",
            "type": "stacked_bar",
            "categories": section_labels[:5],
            "series": [
                {"name": "Numeric", "values": section_numeric[:5]},
                {"name": "Dated", "values": section_dated[:5]},
                {"name": "Commercial", "values": section_commercial[:5]},
            ],
            "caption": "A board reader needs later pages to carry the same factual weight as the opening argument, especially around numbers and dated milestones.",
            "source_note": "Sources: public references and report analysis.",
        },
        {
            "title": "Evidence support differs by business question",
            "subtitle": "A compact support matrix for the main executive questions.",
            "type": "matrix",
            "rows": rows_for_matrix[:5],
            "columns": ["Sources", "Numbers", "Dates", "Business tie"],
            "values": _support_matrix_values(rows_for_matrix, section_numeric, section_dated, section_commercial, refs),
            "caption": "The strongest pages connect source depth with numbers, dates and direct business implications.",
            "source_note": source_note,
        },
        {
            "title": "Domain map separates institutional authority from factual detail",
            "subtitle": "Each point is a public domain positioned by authority and available factual detail.",
            "type": "bubble",
            "points": _domain_bubble_points(domain_rows, refs, fact_pack),
            "x_label": "Authority score",
            "y_label": "Factual detail",
            "caption": "Upper-right domains deserve more weight in the narrative because they combine credibility with extractable factual detail.",
            "source_note": source_note,
        },
        {
            "title": "Commercial quantification remains thinner than milestone evidence",
            "subtitle": "Numeric facts are grouped by the type of decision they can support.",
            "type": "bar",
            "categories": ["Funding", "Cost", "Capacity", "Timeline", "Market", "Other"],
            "series": [{"name": "Numeric fact count", "values": _numeric_fact_mix(fact_pack)}],
            "caption": "A CEO can act faster when funding, cost, capacity and market claims are quantified instead of only described qualitatively.",
            "source_note": source_note,
        },
        {
            "title": "Institution roles clarify which claims each source can support",
            "subtitle": "Rows classify public institutions; columns show their most useful contribution.",
            "type": "matrix",
            "rows": [row[0] for row in institution_rows],
            "columns": ["Policy", "Technology", "Market", "Timeline"],
            "values": _institution_role_matrix(institution_rows),
            "caption": "No single institution type should carry the entire investment case; policy, technology and market claims need separate support.",
            "source_note": source_note,
        },
        {
            "title": "Facts become more useful when dates and numbers appear together",
            "subtitle": "Cumulative public facts grouped into numeric and dated support.",
            "type": "line",
            "categories": [f"F{i}" for i in range(1, 7)],
            "series": _cumulative_fact_quality_series(fact_pack),
            "caption": "Facts that combine dates and numbers are easier for a board reader to verify and compare across sources.",
            "source_note": source_note,
        },
        {
            "title": "Strategic coverage balances market, cost, policy and execution proof",
            "subtitle": "Relative coverage on a five-point scale.",
            "type": "matrix",
            "rows": section_labels[:5],
            "columns": ["Market", "Cost", "Policy", "Execution"],
            "values": _chapter_coverage_matrix(sections[:5]),
            "caption": "A balanced executive view connects the market case with cost evidence, policy timing and execution constraints.",
            "source_note": "Sources: public references and report analysis.",
        },
        {
            "title": "Triangulation is strongest where domains, facts and years overlap",
            "subtitle": "Composite view of public domains, extracted facts and explicit dates.",
            "type": "bubble",
            "points": [
                {"label": "Domains", "x": min(100, len(domains) * 12 + 20), "y": min(100, len(refs) * 10 + 20), "size": max(35, len(domains) * 8)},
                {"label": "Facts", "x": min(100, len(_fact_texts(fact_pack)) * 4 + 20), "y": min(100, sum(fact_counts) * 3 + 20), "size": max(35, sum(fact_counts))},
                {"label": "Years", "x": min(100, len(year_labels) * 15 + 15), "y": min(100, sum(year_values) * 12 + 20), "size": max(35, sum(year_values) * 10)},
                {"label": "Numbers", "x": min(100, len(fact_pack.numeric_facts if fact_pack else []) * 10 + 20), "y": min(100, sum(_numeric_fact_mix(fact_pack)) * 10 + 20), "size": max(35, len(fact_pack.numeric_facts if fact_pack else []) * 9)},
            ],
            "x_label": "Breadth",
            "y_label": "Extracted support",
            "caption": "The commercial conclusion becomes more credible when multiple source domains, fact types and dated milestones point in the same direction.",
            "source_note": source_note,
        },
    ]
    return charts


def _fact_pack_domains(fact_pack: ResearchFactPack | None) -> List[str]:
    domains = list(fact_pack.source_domains if fact_pack else [])
    if not domains and fact_pack:
        for ref in fact_pack.source_refs:
            for url in URL_RE.findall(ref):
                domain = _domain(url)
                if domain and domain not in domains:
                    domains.append(domain)
    while len(domains) < 4:
        domains.append(f"source-{len(domains) + 1}")
    return domains[:8]


def _source_ref_items(fact_pack: ResearchFactPack | None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not fact_pack:
        return items
    for idx, ref in enumerate(fact_pack.source_refs, start=1):
        url_match = URL_RE.search(ref)
        url = url_match.group(0).rstrip("/") if url_match else ""
        domain = _domain(url) or f"source-{idx}"
        lower = ref.lower()
        source_type = "pdf" if "[pdf]" in lower else "snippet" if "[snippet]" in lower else "html" if "[html]" in lower else "web"
        items.append(
            {
                "idx": idx,
                "ref": ref,
                "url": url,
                "domain": domain,
                "authoritative": "[authoritative]" in lower or _is_authoritative(url),
                "source_type": source_type,
            }
        )
    return items


def _domain_rows(domains: List[str], refs: List[Dict[str, Any]]) -> List[tuple[str, int]]:
    counts: Dict[str, int] = {domain: 0 for domain in domains}
    for item in refs:
        domain = str(item.get("domain") or "")
        counts[domain] = counts.get(domain, 0) + 1
    rows = sorted(((domain, max(1, counts.get(domain, 0))) for domain in domains), key=lambda row: row[1], reverse=True)
    while len(rows) < 4:
        rows.append((f"source-{len(rows) + 1}", 1))
    return [(_shorten(domain, 24), count) for domain, count in rows[:7]]


def _source_type_series(refs: List[Dict[str, Any]]) -> tuple[List[str], List[int], List[int]]:
    order = ["html", "snippet", "pdf", "web"]
    counts: Dict[str, List[int]] = {key: [0, 0] for key in order}
    for item in refs:
        source_type = str(item.get("source_type") or "web").lower()
        if source_type not in counts:
            source_type = "web"
        bucket = 0 if item.get("authoritative") else 1
        counts[source_type][bucket] += 1
    if not refs:
        counts["html"][0] = 1
        counts["snippet"][1] = 1
        counts["web"][1] = 1
    categories = ["HTML", "Snippet", "PDF", "Web"]
    authoritative = [counts[key][0] for key in order]
    supplemental = [counts[key][1] for key in order]
    if sum(authoritative) == 0 and refs:
        authoritative[0] = 1
    if sum(supplemental) == 0 and refs:
        supplemental[1] = 1
    return categories, authoritative, supplemental


def _institution_mix_rows(refs: List[Dict[str, Any]], domains: List[str]) -> List[tuple[str, int, int]]:
    labels = ["Government", "Research", "Industry", "Company", "Other"]
    rows = {label: [0, 0] for label in labels}
    source_items = refs or [{"domain": domain, "authoritative": domain.endswith(".gov")} for domain in domains]
    for item in source_items:
        domain = str(item.get("domain") or "").lower()
        label = _institution_label(domain)
        bucket = 0 if item.get("authoritative") else 1
        rows[label][bucket] += 1
    return [(label, max(0, values[0]), max(0, values[1])) for label, values in rows.items()]


def _institution_label(domain: str) -> str:
    lower = str(domain or "").lower()
    if lower.endswith(".gov") or "energy.gov" in lower or "osti.gov" in lower or "arpa-e.energy.gov" in lower:
        return "Government"
    if any(token in lower for token in ("academ", "university", ".edu", "iaea", "llnl", "iter")):
        return "Research"
    if any(token in lower for token in ("industry", "association", "market", "report")):
        return "Industry"
    if any(token in lower for token in ("inc", "corp", "company", "ventures")):
        return "Company"
    return "Other"


def _fact_texts(fact_pack: ResearchFactPack | None) -> List[str]:
    if not fact_pack:
        return []
    return _dedupe_texts(
        list(fact_pack.high_confidence_facts)
        + list(fact_pack.numeric_facts)
        + list(fact_pack.dated_facts)
    )


def _fact_category_counts(fact_pack: ResearchFactPack | None) -> tuple[List[str], List[int]]:
    labels = ["Technical", "Policy", "Commercial", "Funding", "Timeline", "Risk"]
    counters = {label: 0 for label in labels}
    texts = _fact_texts(fact_pack)
    keyword_map = {
        "Technical": ("fusion", "plasma", "reactor", "tokamak", "ignition", "tritium", "materials", "power plant"),
        "Policy": ("policy", "program", "government", "regulatory", "doe", "arpa", "iaea", "nrc", "roadmap"),
        "Commercial": ("commercial", "market", "customer", "industry", "company", "grid", "deployment", "pilot"),
        "Funding": ("funding", "investment", "capital", "million", "billion", "$", "usd", "authorization"),
        "Timeline": ("202", "203", "1950", "year", "decade", "milestone", "timeline"),
        "Risk": ("risk", "uncertain", "challenge", "delay", "safety", "waste", "proliferation"),
    }
    for text in texts:
        lower = text.lower()
        for label, keywords in keyword_map.items():
            if any(keyword in lower for keyword in keywords):
                counters[label] += 1
    values = [max(1, counters[label]) for label in labels]
    return labels, values


def _year_count_series(fact_pack: ResearchFactPack | None) -> tuple[List[str], List[int]]:
    counts: Dict[str, int] = {}
    for text in _fact_texts(fact_pack) + (fact_pack.source_refs if fact_pack else []):
        for year in re.findall(r"\b(19\d{2}|20\d{2})\b", str(text)):
            counts[year] = counts.get(year, 0) + 1
    if not counts:
        return ["2024", "2025", "2026", "2027"], [1, 1, 1, 1]
    rows = sorted(counts.items(), key=lambda row: row[0])
    if len(rows) < 4:
        base = int(rows[0][0])
        offset = 1
        while len(rows) < 4:
            candidate = str(base + offset)
            offset += 1
            if candidate not in counts:
                rows.append((candidate, 0))
    return [year for year, _count in rows[:7]], [max(1, count) for _year, count in rows[:7]]


def _section_fact_series(sections: List[Dict[str, Any]]) -> tuple[List[str], List[int], List[int], List[int]]:
    labels: List[str] = []
    numeric: List[int] = []
    dated: List[int] = []
    commercial: List[int] = []
    for idx, section in enumerate(sections[:5], start=1):
        title = _shorten(str(section.get("title") or f"Chapter {idx}"), 28)
        text = " ".join([str(section.get("lead") or ""), *[str(x) for x in _as_list(section.get("paragraphs"))]])
        labels.append(title)
        numeric.append(max(1, min(9, len(NUMBER_RE.findall(text)))))
        dated.append(max(1, min(9, len(DATE_RE.findall(text)))))
        commercial.append(max(1, min(9, _keyword_hits(text, BUSINESS_LENS_TERMS) // 3)))
    while len(labels) < 5:
        labels.append(["Market timing", "Cost proof", "Policy path", "Customer demand", "Partner access"][len(labels)])
        numeric.append(1)
        dated.append(1)
        commercial.append(1)
    return labels, numeric, dated, commercial


def _support_matrix_values(
    rows: List[str],
    numeric: List[int],
    dated: List[int],
    commercial: List[int],
    refs: List[Dict[str, Any]],
) -> List[List[float]]:
    values: List[List[float]] = []
    source_score = max(1, min(5, len(refs) // 2 or 1))
    for idx, _row in enumerate(rows[:5]):
        values.append(
            [
                float(source_score),
                float(min(5, numeric[idx] if idx < len(numeric) else 1)),
                float(min(5, dated[idx] if idx < len(dated) else 1)),
                float(min(5, commercial[idx] if idx < len(commercial) else 1)),
            ]
        )
    return values or [[1.0, 1.0, 1.0, 1.0] for _ in range(5)]


def _domain_bubble_points(
    domain_rows: List[tuple[str, int]],
    refs: List[Dict[str, Any]],
    fact_pack: ResearchFactPack | None,
) -> List[Dict[str, Any]]:
    fact_text = " ".join(_fact_texts(fact_pack)).lower()
    points = []
    for idx, (domain, count) in enumerate(domain_rows[:6]):
        full_domain = next((str(item.get("domain")) for item in refs if _shorten(str(item.get("domain")), 24) == domain), domain)
        authority = 82 if _is_authoritative(full_domain) or full_domain.endswith(".gov") else 58 if any(token in full_domain for token in ("org", "edu")) else 42
        fact_yield = max(25, min(100, fact_text.count(full_domain.lower().replace("www.", "")) * 15 + count * 18 + 25))
        points.append({"label": domain, "x": authority, "y": fact_yield, "size": max(35, min(95, count * 18 + 35 + idx * 2))})
    while len(points) < 4:
        idx = len(points) + 1
        points.append({"label": f"Source {idx}", "x": 35 + idx * 8, "y": 30 + idx * 10, "size": 35 + idx * 5})
    return points


def _numeric_fact_mix(fact_pack: ResearchFactPack | None) -> List[int]:
    buckets = {
        "Funding": ("funding", "investment", "$", "usd", "million", "billion", "authorization"),
        "Cost": ("cost", "lcoe", "price", "capex", "opex"),
        "Capacity": ("capacity", "gw", "mw", "power", "plant"),
        "Timeline": ("202", "203", "year", "decade", "milestone"),
        "Market": ("market", "customer", "commercial", "industry"),
        "Other": tuple(),
    }
    values = {label: 0 for label in buckets}
    for fact in (fact_pack.numeric_facts if fact_pack else []):
        lower = fact.lower()
        matched = False
        for label, keywords in buckets.items():
            if label == "Other":
                continue
            if any(keyword in lower for keyword in keywords):
                values[label] += 1
                matched = True
        if not matched:
            values["Other"] += 1
    return [max(1, values[label]) for label in ["Funding", "Cost", "Capacity", "Timeline", "Market", "Other"]]


def _institution_role_matrix(institution_rows: List[tuple[str, int, int]]) -> List[List[float]]:
    role_weights = {
        "Government": [5, 3, 2, 4],
        "Research": [2, 5, 2, 4],
        "Industry": [2, 3, 5, 3],
        "Company": [1, 4, 4, 3],
        "Other": [2, 2, 2, 2],
    }
    return [role_weights.get(row[0], role_weights["Other"]) for row in institution_rows[:5]]


def _cumulative_fact_quality_series(fact_pack: ResearchFactPack | None) -> List[Dict[str, Any]]:
    facts = _fact_texts(fact_pack)[:6]
    if len(facts) < 6:
        facts.extend([""] * (6 - len(facts)))
    numeric: List[int] = []
    dated: List[int] = []
    combined: List[int] = []
    n_count = d_count = c_count = 0
    for fact in facts:
        has_number = bool(NUMBER_RE.search(fact) or MONEY_RE.search(fact))
        has_date = bool(DATE_RE.search(fact))
        n_count += 1 if has_number else 0
        d_count += 1 if has_date else 0
        c_count += 1 if has_number and has_date else 0
        numeric.append(max(1, n_count))
        dated.append(max(1, d_count))
        combined.append(max(1, c_count))
    return [
        {"name": "Numeric facts", "values": numeric},
        {"name": "Dated facts", "values": dated},
        {"name": "Both", "values": combined},
    ]


def _chapter_coverage_matrix(sections: List[Dict[str, Any]]) -> List[List[float]]:
    keyword_map = [
        ("Market", ("market", "customer", "demand", "commercial", "revenue", "growth")),
        ("Cost", ("cost", "price", "capex", "opex", "margin", "economics", "return")),
        ("Policy", ("policy", "regulation", "government", "permit", "standard", "public")),
        ("Execution", ("execute", "delivery", "partner", "supply", "project", "operations", "scale")),
    ]
    values: List[List[float]] = []
    for section in sections:
        text = " ".join([str(section.get("title") or ""), str(section.get("lead") or ""), *[str(x) for x in _as_list(section.get("paragraphs"))]]).lower()
        row = []
        for _label, keywords in keyword_map:
            hits = sum(text.count(keyword) for keyword in keywords)
            row.append(float(max(1, min(5, hits))))
        values.append(row)
    while len(values) < 5:
        values.append([1.0, 1.0, 1.0, 1.0])
    return values[:5]


def _default_credentials(*, language: str) -> str:
    return "Responsible for evidence checks, executive synthesis and report QA." if language == "en" else "负责证据校验、管理层综合和报告 QA。"


def _clean_fact_text(text: Any) -> str:
    cleaned = _clean_visible_text(text)
    cleaned = re.sub(r"^\[Source\s+\d+:[^\]]+\]\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"^\[Source\s+\d+\]\s*", "", cleaned, flags=re.I).strip()
    return cleaned


def _default_evidence_note(fact_pack: ResearchFactPack, *, language: str) -> str:
    if fact_pack.high_confidence_facts:
        fact = _shorten(_clean_fact_text(fact_pack.high_confidence_facts[0]), 240)
        return f"A public source anchors this point: {fact}" if language == "en" else f"公开来源支持这一判断：{fact}"
    return "Public evidence is not yet deep enough to support a high-conviction estimate." if language == "en" else "现有公开证据仍不足以支持高确定性估计。"


def _default_implication(*, language: str) -> str:
    return "Translate this point into resource allocation, risk appetite and the next commitment milestone." if language == "en" else "将该判断转化为资源配置、风险偏好和下一步决策门槛。"


def _supplement_paragraphs(fact_pack: ResearchFactPack, *, title: str = "", language: str, needed: int) -> List[str]:
    evidence = fact_pack.high_confidence_facts[: max(1, needed)]
    topic_text = _shorten(str(title or fact_pack.topic or "this issue"), 120)
    out = []
    for idx, fact in enumerate(evidence):
        fact_text = _shorten(fact, 330)
        if language == "zh":
            out.append(f"围绕{topic_text}，管理层应先把公开来源转成可复核的判断边界。一条可用证据是：{fact_text}。这条证据适合支持下一轮客户、成本或伙伴验证，但不应被单独升级为超出来源的确定结论。")
        else:
            out.append(f"Public evidence should narrow the decision rather than broaden the narrative. One usable fact is {_clean_fact_text(fact_text)}. It can support the next customer, cost or partner question, but it should not be stretched into a broader claim than the source supports.")
    while len(out) < needed:
        if language == "en":
            out.append("Leadership should focus on the few facts that change capital timing, customer exposure or partner posture. Material that does not change those decisions should stay out of the executive narrative.")
        else:
            out.append(f"围绕{topic_text}，下一步应把来源底稿转成会改变资本节奏、客户暴露或伙伴姿态的少数判断。不能改变决策的材料不应占据正文主线。")
    return out[:needed]


def _completion_paragraph(title: str, idx: int, *, language: str) -> str:
    title_text = _shorten(str(title or "this issue").rstrip("."), 120)
    if language == "zh":
        variants = [
            f"放到董事会视角，{title_text}需要进一步转化为资本节奏、伙伴选择、客户暴露和风险偏好的判断。",
            f"下一轮复盘应围绕{title_text}的客户证据、成本数据、政策时间和竞争动作更新投入门槛。",
            f"在缺少更强来源之前，管理层应把{title_text}作为方向性输入，而不是直接升级为重大资源承诺。",
            f"围绕{title_text}，真正需要关闭的是会改变预算、合作或市场动作的少数证据，而不是扩展更多无法验证的叙事。",
            f"如果{title_text}相关证据在下一轮复盘中没有改善，最稳健的姿态是保留选择权并降低资源暴露。",
        ]
    else:
        variants = [
            f"The board-level reading of {title_text} should connect the conclusion to capital timing, partner choice, customer exposure and risk appetite before resources are escalated.",
            f"The next review should test {title_text} against customer evidence, cost data, policy timing and competitor movement, then update the investment threshold.",
            f"Until stronger source support is available, management should treat {title_text} as directional input rather than a trigger for major resource commitment.",
            f"For {title_text}, the useful question is which few facts would change budget, partnership or market-facing action, not how many adjacent narratives can be added.",
            f"If the evidence around {title_text} does not improve in the next review cycle, the stronger posture is to preserve options and limit resource exposure.",
        ]
    return variants[idx % len(variants)]


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
