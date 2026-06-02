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
