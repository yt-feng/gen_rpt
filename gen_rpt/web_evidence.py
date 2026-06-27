from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse

from .research_quality import ResearchFactPack
from .web_fetch import SourceDocument


AUTHORITY_HINTS = (
    ".gov",
    ".edu",
    "sec.gov",
    "energy.gov",
    "science.osti.gov",
    "iaea.org",
    "iter.org",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "iea.org",
    "un.org",
    "nrc.gov",
    "arpa-e.energy.gov",
    "llnl.gov",
    "nationalacademies.org",
    "investor.",
    "ir.",
)

MIN_OBSERVED_LINE_POINTS = 4
MAX_IMPLIED_PATH_POINTS = 8
ENDPOINT_IMPLIED_FAMILIES = {"funding", "market", "capacity", "cost", "media", "adoption"}
ENDPOINT_IMPLIED_UNITS = {"$M", "MW", "MWh", "articles", "mentions"}

VALUE_RE = re.compile(
    r"(?P<prefix>US\$|\$|USD|RMB|CNY|EUR|HK\$)?\s*"
    r"(?P<number>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage points?|trillion|billion|million|bn|mn|GW|MW|GWh|TWh|MWh|MJ|megajoules?|kg|kilograms?|years?|months?|days?|articles?|mentions?|companies|plants|projects|components?|parts?|USD|dollars?)?",
    re.I,
)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
URL_RE = re.compile(r"https?://[^\s,;)\]]+")


@dataclass
class EvidencePoint:
    id: str
    fact: str
    value: float | None
    unit: str
    display_value: str
    year: int | None
    metric_family: str
    source_title: str
    source_url: str
    domain: str
    source_type: str
    authoritative: bool
    score: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_evidence_ledger(
    topic: str,
    sources: List[SourceDocument],
    fact_pack: ResearchFactPack,
    *,
    limit: int = 36,
) -> List[Dict[str, Any]]:
    """Extract source-backed numeric/date evidence for web exhibits.

    The web report should not ask the LLM to invent chart values. This ledger
    keeps every chartable data point tied to a source sentence and URL.
    """

    candidates: List[EvidencePoint] = []
    for source_idx, source in enumerate(sources, start=1):
        source_text = "\n".join([source.title or "", source.snippet or "", source.content or ""])
        for sentence in _split_sentences(source_text):
            candidates.extend(_points_from_sentence(sentence, source, source_idx, topic))

    if len(candidates) < 8:
        source_lookup = {idx + 1: source for idx, source in enumerate(sources)}
        for fact in fact_pack.numeric_facts + fact_pack.dated_facts + fact_pack.high_confidence_facts:
            source = _source_for_fact(fact, source_lookup)
            if source:
                candidates.extend(_points_from_sentence(_strip_source_prefix(fact), source, 0, topic))

    ranked = _dedupe_points(sorted(candidates, key=lambda item: item.score, reverse=True), limit=limit)
    for idx, point in enumerate(ranked, start=1):
        point.id = f"E{idx}"
    return [point.to_dict() for point in ranked]


def build_storyline_plan(
    topic: str,
    plan: Dict[str, Any],
    fact_pack: ResearchFactPack,
    evidence_ledger: List[Dict[str, Any]],
    *,
    language: str = "en",
) -> Dict[str, Any]:
    families = [str(item.get("metric_family") or "") for item in evidence_ledger]
    family_counts = Counter(x for x in families if x)
    strongest = [item for item, _count in family_counts.most_common(4)]
    facts = [str(item.get("fact") or "") for item in evidence_ledger[:6]]
    decision_question = str(plan.get("decision_question") or fact_pack.decision_question or topic)
    hypotheses = _plan_hypotheses(plan)
    sizing_methods = _plan_sizing_methods(plan)
    if str(language or "").lower().startswith("zh"):
        return {
            "core_question": decision_question,
            "central_thesis": "先由公开证据决定报告主线，再把数据、时间线和管理含义串成可执行判断。",
            "narrative_focus": "围绕证据最密集的主题推进：" + "、".join(strongest[:4]),
            "structure_logic": "先说明管理层问题，再呈现源自事实包的数据图表，随后讨论商业含义、风险和行动门槛。",
            "evidence_must_cover": facts,
            "hypotheses_to_test": hypotheses,
            "market_sizing_logic": sizing_methods,
            "charting_rule": "图表只能使用 evidence_ledger 中可回溯到 URL 的数字、年份、来源计数或同单位可比数据。",
            "exhibit_narrative_rule": "每张图必须嵌入章节论证：图前有管理问题或判断铺垫，图后有客户可读解释；不得连续堆放图表。",
        }
    return {
        "core_question": decision_question,
        "central_thesis": "The storyline should be led by the strongest public evidence, then translated into management choices.",
        "narrative_focus": "Evidence is densest around: " + ", ".join(strongest[:4] or ["source quality", "timing", "commercial proof"]),
        "structure_logic": "Open with the executive decision, show source-backed data exhibits, then move through business implications, risks and action gates.",
        "evidence_must_cover": facts,
        "hypotheses_to_test": hypotheses,
        "market_sizing_logic": sizing_methods,
        "charting_rule": "Charts may use only evidence_ledger values, dates, source counts or same-unit comparable values tied to public URLs.",
        "exhibit_narrative_rule": "Every exhibit must be embedded in the section argument: prose sets up the management question before the exhibit and interprets the implication after it; never stack exhibits without prose.",
    }


def build_evidence_exhibits(
    topic: str,
    evidence_ledger: List[Dict[str, Any]],
    fact_pack: ResearchFactPack,
    *,
    plan: Dict[str, Any] | None = None,
    chart_data_needs: List[Dict[str, Any]] | None = None,
    language: str = "en",
) -> List[Dict[str, Any]]:
    if not evidence_ledger:
        return _fact_pack_exhibits(topic, fact_pack, plan=plan, chart_data_needs=chart_data_needs, language=language)

    exhibits: List[Dict[str, Any]] = []
    exhibits.append(_metric_row_exhibit(evidence_ledger, fact_pack))
    family_order = _chart_need_family_order(chart_data_needs or [])

    for time_series in _time_series_value_exhibits(evidence_ledger, limit=2, family_order=family_order):
        exhibits.append(time_series)

    funding_bubble = _funding_bubble_exhibit(evidence_ledger)
    if funding_bubble:
        exhibits.append(funding_bubble)

    for comparable in _comparable_value_exhibits(evidence_ledger, limit=2, family_order=family_order):
        exhibits.append(comparable)

    timeline = _milestone_timeline_exhibit(evidence_ledger, fact_pack)
    if timeline:
        exhibits.append(timeline)

    if len(exhibits) < 6:
        for comparable in _comparable_value_exhibits(evidence_ledger, limit=3, family_order=family_order):
            exhibits.append(comparable)

    matrix = _opportunity_matrix_exhibit(evidence_ledger, fact_pack)
    if matrix and len(exhibits) < 3:
        exhibits.append(matrix)

    opportunity = _opportunity_map_exhibit(evidence_ledger, fact_pack)
    if opportunity and len(exhibits) < 3:
        exhibits.append(opportunity)

    if len(exhibits) < 3:
        fallback = [_year_exhibit(evidence_ledger), _support_matrix_exhibit(evidence_ledger, fact_pack)]
        exhibits.extend([item for item in fallback if item][: 3 - len(exhibits)])
    if len(exhibits) < 3:
        exhibits.extend(_fact_pack_exhibits(topic, fact_pack)[: 3 - len(exhibits)])

    exhibits = _dedupe_exhibits(exhibits)
    for idx, exhibit in enumerate(exhibits[:6], start=1):
        exhibit["id"] = f"evidence-exhibit-{idx}"
        exhibit["no"] = str(idx)
        exhibit.setdefault("after_section_id", f"section-{min(idx, 5)}")
    return exhibits[:6]


def merge_evidence_exhibits(report: Dict[str, Any], evidence_exhibits: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not evidence_exhibits:
        return report
    report["exhibits"] = evidence_exhibits
    return report


def _chart_need_family_order(chart_data_needs: List[Dict[str, Any]]) -> List[str]:
    order: List[str] = []
    for need in chart_data_needs:
        if not isinstance(need, dict):
            continue
        text = " ".join(
            str(need.get(key) or "")
            for key in ("title", "executive_question", "required_metrics", "comparison_set", "sizing_role")
        ).lower()
        for family, tokens in (
            ("funding", ("funding", "investment", "capital", "finance", "financing")),
            ("technology", ("technology", "ignition", "breakeven", "energy", "plasma", "technical")),
            ("capacity", ("capacity", "plant", "reactor", "supply", "project")),
            ("market", ("market", "demand", "customer", "revenue", "adoption", "commercial")),
            ("cost", ("cost", "lcoe", "capex", "opex", "roi", "economics")),
            ("policy", ("policy", "regulation", "license", "approval")),
            ("media", ("media", "news", "coverage", "gdelt", "attention")),
            ("timeline", ("timeline", "milestone", "date", "schedule")),
        ):
            if any(token in text for token in tokens) and family not in order:
                order.append(family)
    defaults = ["funding", "technology", "capacity", "market", "cost", "policy", "media", "timeline"]
    return order + [family for family in defaults if family not in order]


def _family_rank(family: str, family_order: List[str] | None) -> int:
    order = family_order or ["funding", "technology", "capacity", "market", "cost", "policy", "media", "timeline"]
    try:
        return order.index(str(family or ""))
    except ValueError:
        return len(order) + 1


def _dedupe_exhibits(exhibits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for exhibit in exhibits:
        key = (str(exhibit.get("type") or ""), re.sub(r"\W+", "", str(exhibit.get("title") or "").lower())[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(exhibit)
    return out


def _metric_row_exhibit(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any]:
    points = _business_metric_points(ledger)[:3]
    metrics = []
    basis = []
    for point in points:
        metrics.append(
            {
                "value": point.get("display_value") or "",
                "label": _short_label(str(point.get("fact") or ""), 86),
            }
        )
        basis.append(_basis_item(point))
    if len(metrics) < 3:
        metrics.append({"value": str(fact_pack.source_count), "label": "public sources retained for the analysis"})
        metrics.append({"value": str(fact_pack.authoritative_source_count), "label": "authority-weighted public sources"})
    return {
        "type": "metric_row",
        "title": "The executive case should start with the few numbers the public record can support",
        "subtitle": "Each headline metric is traceable to a retained public source sentence.",
        "metrics": metrics[:3],
        "caption": "These values are extracted from public source text; they are not model-created scores.",
        "source_note": _source_note(points or ledger[:3]),
        "data_basis": basis or [_basis_item(item) for item in ledger[:3]],
        "evidence_quality": "source_extracted",
    }


def _business_metric_points(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable = [
        item
        for item in ledger
        if item.get("display_value")
        and str(item.get("unit") or "") not in {"", "year", "years", "months", "days"}
        and not _low_value_numeric_fact(str(item.get("fact") or ""), str(item.get("unit") or ""))
    ]
    return sorted(usable, key=lambda item: (_unit_priority(str(item.get("unit") or "")), int(bool(item.get("authoritative")))), reverse=True)


def _milestone_timeline_exhibit(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any] | None:
    points = []
    seen_years: set[int] = set()
    for item in sorted([x for x in ledger if x.get("year")], key=lambda x: int(x.get("year") or 0)):
        year = int(item.get("year") or 0)
        if year in seen_years:
            continue
        seen_years.add(year)
        points.append(item)
    if len(points) < 2:
        for fact in fact_pack.dated_facts + fact_pack.high_confidence_facts:
            years = [int(x) for x in YEAR_RE.findall(str(fact or ""))]
            if not years:
                continue
            year = years[0]
            if year in seen_years:
                continue
            seen_years.add(year)
            points.append(
                {
                    "id": f"S{len(points) + 1}",
                    "year": year,
                    "fact": _strip_source_prefix(str(fact)),
                    "display_value": str(year),
                    "source_title": "Fact pack",
                    "source_url": "",
                    "domain": "",
                }
            )
            if len(points) >= 5:
                break
    if len(points) < 2:
        return None
    points = sorted(points, key=lambda x: int(x.get("year") or 0))[:6]
    return {
        "type": "timeline",
        "title": "Milestones, not hype cycles, set the strategic clock",
        "subtitle": "Dated proof points should set the commitment clock before leadership treats the opportunity as investable.",
        "events": [
            {
                "year": str(item.get("year") or ""),
                "title": _short_label(_timeline_title(str(item.get("fact") or "")), 72),
                "description": _short_label(str(item.get("fact") or ""), 190),
            }
            for item in points
        ],
        "caption": "The timeline uses dated public evidence and avoids turning milestone timing into a forecast.",
        "source_note": _source_note(points),
        "data_basis": [_basis_item(item) for item in points],
        "evidence_quality": "source_dated_milestones",
    }


def _opportunity_matrix_exhibit(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any]:
    basis = _mixed_basis(ledger, fact_pack, limit=8)
    timeline_count = _family_count(ledger, {"timeline", "technology", "capacity"})
    funding_count = _family_count(ledger, {"funding"})
    market_count = _family_count(ledger, {"market"})
    policy_count = _family_count(ledger, {"policy"}) + fact_pack.authoritative_source_count
    rows = [
        "Technical proof",
        "Capital and partners",
        "Customer pull",
        "Policy and permission",
    ]
    values = [
        [
            _support_label(timeline_count),
            "Build learning options; do not underwrite full commercialization until repeatable system performance is visible.",
            "Repeatable output, plant readiness, cost path and operating reliability.",
        ],
        [
            _support_label(funding_count),
            "Use partnerships and staged funding to buy exposure without locking in a single technology path.",
            "Named capital providers, milestone funding, supplier capacity and reference projects.",
        ],
        [
            _support_label(market_count),
            "Validate willingness to pay against cheaper substitutes before sizing the prize.",
            "Customer commitments, use-case economics, grid integration value and switching triggers.",
        ],
        [
            _support_label(policy_count),
            "Track licensing, safety and public-funding signals as decision gates.",
            "Regulatory path, safety case, program funding and permitting milestones.",
        ],
    ]
    return {
        "type": "opportunity_matrix",
        "title": "The opportunity matrix separates option-building from capital commitment",
        "subtitle": "Rows translate retained evidence into decisions a CEO or board can actually make.",
        "rows": rows,
        "columns": ["Public proof", "Management move", "Validation metric"],
        "values": values,
        "caption": "Public proof is derived from retained source categories and authority-weighted sources; it is a decision aid, not a valuation model.",
        "source_note": _source_note(basis),
        "data_basis": basis,
        "evidence_quality": "source_backed_opportunity_matrix",
    }


def _stage_gate_process_exhibit(topic: str, ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any]:
    basis = _mixed_basis(ledger, fact_pack, limit=8)
    return {
        "type": "process",
        "title": "A staged path keeps commitment behind proof",
        "subtitle": "The roadmap turns an uncertain technology or market into gated management work.",
        "steps": [
            {
                "title": "Baseline",
                "description": "Extract the few source-backed facts that should shape the first decision on " + _short_label(topic, 58) + ".",
            },
            {
                "title": "Options",
                "description": "Create low-regret exposure through partnerships, monitoring rights, pilots or supplier relationships.",
            },
            {
                "title": "Validation",
                "description": "Replace open assumptions with data on customers, economics, regulatory timing and execution capacity.",
            },
            {
                "title": "Commit",
                "description": "Scale capital or operating resources only when explicit proof gates are crossed.",
            },
        ],
        "caption": "This is a management process chart, anchored in the source boundary rather than a numerical forecast.",
        "source_note": _source_note(basis),
        "data_basis": basis,
        "evidence_quality": "source_backed_stage_gates",
    }


def _opportunity_map_exhibit(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any] | None:
    family_sets = {
        "Technical proof": {"timeline", "technology", "capacity"},
        "Capital formation": {"funding"},
        "Customer pull": {"market"},
        "Policy path": {"policy"},
    }
    points = []
    basis = []
    max_count = max([_family_count(ledger, families) for families in family_sets.values()] + [1])
    for idx, (label, families) in enumerate(family_sets.items(), start=1):
        items = [item for item in ledger if str(item.get("metric_family") or "") in families]
        count = len(items)
        authority = sum(1 for item in items if item.get("authoritative"))
        recent = sum(1 for item in items if int(item.get("year") or 0) >= 2020)
        x = 30 + min(55, (count / max_count) * 55)
        y = 35 + min(50, authority * 12 + recent * 8 + (15 if count else 0))
        size = 34 + min(46, count * 9 + authority * 5)
        points.append({"label": label, "x": x, "y": min(90, y), "size": size})
        basis.extend(items[:2])
    if len(points) < 3:
        return None
    if not basis:
        basis = ledger[:6]
    return {
        "type": "opportunity_map",
        "title": "The option map shows where leaders should act, watch or hold back",
        "subtitle": "Placement is rule-based from public proof, source authority and recent milestones.",
        "x_label": "Public proof available",
        "y_label": "Near-term actionability",
        "points": points,
        "caption": "The bubble map is an evidence-derived management view; it does not estimate market value or probability of success.",
        "source_note": _source_note(basis[:8]),
        "data_basis": [_basis_item(item) for item in basis[:8]],
        "evidence_quality": "source_backed_option_map",
    }


def _market_sizing_bridge_exhibit(
    topic: str,
    ledger: List[Dict[str, Any]],
    fact_pack: ResearchFactPack,
    plan: Dict[str, Any],
    chart_data_needs: List[Dict[str, Any]],
    *,
    language: str = "en",
) -> Dict[str, Any] | None:
    sizing_methods = _plan_sizing_methods(plan)
    if not sizing_methods and not chart_data_needs and not ledger:
        return None
    zh = str(language or "").lower().startswith("zh")
    rows = (
        ["Demand ceiling", "Accessible market", "Adoption ramp", "Economics and value capture", "Supply constraint"]
        if not zh
        else ["需求上限", "可触达市场", "采用爬坡", "经济性与价值捕获", "供给侧约束"]
    )
    family_map = [
        {"market", "funding"},
        {"market", "policy"},
        {"adoption", "timeline", "market"},
        {"cost", "pricing", "funding"},
        {"capacity", "technology", "timeline"},
    ]
    validation_tasks = (
        [
            "Verify category demand, segment share and forecast year.",
            "Filter by geography, use case, customer eligibility and channel access.",
            "Replace narrative adoption with customer count, pilot conversion and repeat-order evidence.",
            "Validate price, cost, margin, payback and value-capture assumptions.",
            "Check announced capacity, utilization, delivery timing and supply-chain bottlenecks.",
        ]
        if not zh
        else [
            "复核品类需求、细分占比和预测年份。",
            "按地域、场景、客户资格和渠道可触达性过滤。",
            "用客户数、试点转化和复购证据替代采用叙事。",
            "复核价格、成本、利润率、回收期和价值捕获假设。",
            "核验公告产能、利用率、交付时间和供应链瓶颈。",
        ]
    )
    basis: List[Dict[str, Any]] = []
    values = []
    for idx, row in enumerate(rows):
        items = _ledger_for_families(ledger, family_map[idx])[:3]
        if not items:
            items = _fallback_need_items(chart_data_needs, row)[:2]
        if items and isinstance(items[0], dict) and items[0].get("source_url"):
            basis.extend(items[:2])
        support = _support_count_label(len(items), zh=zh)
        signal = _short_label(_best_signal(items) or _method_signal(sizing_methods, idx), 150)
        sizing_use = _sizing_use_text(row, zh=zh)
        values.append([support, sizing_use, signal or ("Evidence gap" if not zh else "证据缺口"), validation_tasks[idx]])
    if not basis:
        basis = _mixed_basis(ledger, fact_pack, limit=8)
    return {
        "type": "matrix",
        "title": "Build the opportunity case from demand, adoption, economics and constraints" if not zh else "把机会判断拆成需求、采用、经济性和供给约束",
        "subtitle": "The goal is not a single big number; it is to show which parts of the opportunity can be supported by public evidence." if not zh else "重点不是给出一个大数字，而是说明机会的哪些部分已有公开证据支撑。",
        "rows": rows,
        "columns": ["Public data found", "Management question", "Best public signal", "What to verify next"] if not zh else ["公开数据", "管理问题", "最佳公开信号", "下一步核验"],
        "values": values,
        "caption": "The remaining open variables show where management should validate demand, economics and execution before committing capital." if not zh else "仍未闭合的变量提示管理层在投入资本前应核验需求、经济性和执行能力。",
        "source_note": _source_note(basis),
        "data_basis": [_basis_item(item) for item in basis[:8]] if basis and basis[0].get("source_url") else basis[:8],
        "evidence_quality": "opportunity_case",
    }


def _hypothesis_evidence_exhibit(
    plan: Dict[str, Any],
    ledger: List[Dict[str, Any]],
    fact_pack: ResearchFactPack,
    *,
    language: str = "en",
) -> Dict[str, Any] | None:
    hypotheses = _plan_hypotheses(plan)
    if not hypotheses:
        return None
    zh = str(language or "").lower().startswith("zh")
    rows = []
    values = []
    basis: List[Dict[str, Any]] = []
    for idx, hypothesis in enumerate(hypotheses[:6], start=1):
        item_id = str(hypothesis.get("id") or f"H{idx}")
        text = str(hypothesis.get("hypothesis") or hypothesis.get("claim") or "")
        needed = [str(x) for x in _as_list(hypothesis.get("needed_evidence")) if str(x).strip()]
        matched = _ledger_for_hypothesis(ledger, text, needed)[:3]
        basis.extend(matched[:2])
        rows.append(item_id)
        values.append(
            [
                _short_label(text, 120),
                _support_count_label(len(matched), zh=zh),
                _short_label(_best_signal(matched), 140) or ("No direct public signal retained" if not zh else "未保留直接公开信号"),
                _short_label("; ".join(needed[:3]), 140) or ("Define primary validation data" if not zh else "定义一手核验数据"),
            ]
        )
    if not basis:
        basis = _mixed_basis(ledger, fact_pack, limit=8)
    return {
        "type": "matrix",
        "title": "Hypotheses should move only as fast as the evidence does" if not zh else "假设验证的推进速度应受证据约束",
        "subtitle": "Each row links a management hypothesis to retained evidence and the next validation task." if not zh else "每行把管理层假设、已保留证据和下一步核验任务连在一起。",
        "rows": rows,
        "columns": ["Hypothesis", "Public support", "Evidence to use", "Next validation"] if not zh else ["假设", "公开支持", "可用证据", "下一步核验"],
        "values": values,
        "caption": "The table is hypothesis-driven, but support labels come from retained evidence counts rather than subjective scores." if not zh else "表格按假设组织，但支持度来自保留证据数量，不使用主观评分。",
        "source_note": _source_note(basis),
        "data_basis": [_basis_item(item) for item in basis[:8]] if basis and basis[0].get("source_url") else basis[:8],
        "evidence_quality": "hypothesis_evidence_map",
    }


def _fact_pack_exhibits(
    topic: str,
    fact_pack: ResearchFactPack,
    *,
    plan: Dict[str, Any] | None = None,
    chart_data_needs: List[Dict[str, Any]] | None = None,
    language: str = "en",
) -> List[Dict[str, Any]]:
    basis = _fact_pack_basis(fact_pack)
    exhibits = [
        {
            "type": "metric_row",
            "title": "The report starts from a retained public-source base",
            "subtitle": "When chartable market data is thin, the exhibit stays on evidence coverage rather than inventing proxies.",
            "metrics": [
                {"value": str(fact_pack.source_count), "label": "public sources retained in the fact pack"},
                {"value": str(fact_pack.authoritative_source_count), "label": "authority-weighted sources"},
                {"value": str(len(fact_pack.numeric_facts)), "label": "numeric facts available for analysis"},
            ],
            "caption": "These counts come from the public-source fact pack and define the current evidence boundary.",
            "source_note": _source_note(basis),
            "data_basis": basis,
            "evidence_quality": "fact_pack_source_count",
        },
        {
            "type": "bar",
            "title": "Fact extraction determines which charts are safe to publish",
            "subtitle": "Counts of retained sources, authority signals and extracted facts.",
            "categories": ["Sources", "Authority", "Numeric facts", "Dated facts"],
            "series": [
                {
                    "name": "Count",
                    "values": [
                        fact_pack.source_count,
                        fact_pack.authoritative_source_count,
                        len(fact_pack.numeric_facts),
                        len(fact_pack.dated_facts),
                    ],
                }
            ],
            "caption": "The bar chart reports evidence availability; it does not rank strategic options.",
            "source_note": _source_note(basis),
            "data_basis": basis,
            "evidence_quality": "fact_pack_fact_count",
        },
        {
            "type": "matrix",
            "title": "Evidence gaps should become explicit validation work",
            "subtitle": "Readiness counts are capped at five to expose whether the source base can support stronger claims.",
            "rows": ["Source breadth", "Authority", "Numeric facts", "Timeline facts"],
            "columns": ["Observed", "Readiness cap"],
            "values": [
                [fact_pack.source_count, min(5, fact_pack.source_count)],
                [fact_pack.authoritative_source_count, min(5, fact_pack.authoritative_source_count)],
                [len(fact_pack.numeric_facts), min(5, len(fact_pack.numeric_facts))],
                [len(fact_pack.dated_facts), min(5, len(fact_pack.dated_facts))],
            ],
            "caption": "If numeric or dated facts are sparse, management should treat market size, ROI and timing as validation tasks.",
            "source_note": _source_note(basis),
            "data_basis": basis,
            "evidence_quality": "fact_pack_gap_matrix",
        },
    ]
    for idx, exhibit in enumerate(exhibits, start=1):
        exhibit["id"] = f"evidence-exhibit-{idx}"
        exhibit["no"] = str(idx)
        exhibit["after_section_id"] = f"section-{idx}"
    return exhibits[:6]


def _comparable_value_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    exhibits = _comparable_value_exhibits(ledger, limit=1)
    return exhibits[0] if exhibits else None


def _comparable_value_exhibits(
    ledger: List[Dict[str, Any]],
    *,
    limit: int = 3,
    family_order: List[str] | None = None,
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for item in ledger:
        value = item.get("value")
        unit = str(item.get("unit") or "")
        family = str(item.get("metric_family") or "")
        if value is None or not unit or unit in {"year", "years", "month", "months"}:
            continue
        common_unit = _comparable_unit(unit)
        if not common_unit:
            continue
        normalized = dict(item)
        normalized["value"] = _comparable_value(float(value), unit, common_unit)
        normalized["unit"] = common_unit
        normalized["display_value"] = _display_comparable_value(float(normalized["value"]), common_unit)
        groups[(family, common_unit)].append(normalized)
    usable = [(key, items) for key, items in groups.items() if len(items) >= 2]
    if not usable:
        return []
    exhibits: List[Dict[str, Any]] = []
    seen_titles: set[str] = set()
    for (family, unit), items in sorted(usable, key=lambda pair: (_family_rank(pair[0][0], family_order), -len(pair[1]), -_unit_priority(pair[0][1]))):
        title = _comparable_exhibit_title(family, unit)
        if title in seen_titles:
            continue
        seen_titles.add(title)
        items = sorted(items, key=lambda item: float(item.get("value") or 0), reverse=True)[:6]
        exhibits.append(
            {
                "type": "bar",
                "title": title,
                "subtitle": _comparable_reader_subtitle(family, unit),
                "categories": [_short_label(_chart_label(item), 34) for item in items],
                "series": [{"name": unit, "values": [float(item.get("value") or 0) for item in items]}],
                "caption": "",
                "footnote": f"All bars use the same unit ({unit}) and source-stated values; no synthetic rankings or maturity scores are used.",
                "source_note": _source_note(items),
                "data_basis": [_basis_item(item) for item in items],
                "evidence_quality": "same_unit_source_extracted",
            }
        )
        if len(exhibits) >= limit:
            break
    return exhibits


def _time_series_value_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    exhibits = _time_series_value_exhibits(ledger, limit=1)
    return exhibits[0] if exhibits else None


def _time_series_value_exhibits(
    ledger: List[Dict[str, Any]],
    *,
    limit: int = 2,
    family_order: List[str] | None = None,
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str], Dict[int, Dict[str, Any]]] = defaultdict(dict)
    for item in ledger:
        value = item.get("value")
        year = item.get("year")
        unit = str(item.get("unit") or "")
        family = str(item.get("metric_family") or "")
        if value is None or not year or not unit or unit in {"year", "years", "months", "days"}:
            continue
        common_unit = _comparable_unit(unit)
        if not common_unit:
            continue
        normalized = dict(item)
        normalized["value"] = _comparable_value(float(value), unit, common_unit)
        normalized["unit"] = common_unit
        normalized["display_value"] = _display_comparable_value(float(normalized["value"]), common_unit)
        existing = groups[(family, common_unit)].get(int(year))
        if existing is None or float(normalized.get("value") or 0) > float(existing.get("value") or 0):
            groups[(family, common_unit)][int(year)] = normalized
    usable = [
        ((family, unit), year_map)
        for (family, unit), year_map in groups.items()
        if len(year_map) >= MIN_OBSERVED_LINE_POINTS
    ]
    endpoint_usable = [
        ((family, unit), year_map)
        for (family, unit), year_map in groups.items()
        if len(year_map) == 2 and _can_build_endpoint_implied_path(family, unit, year_map)
    ]
    if not usable:
        ordered_endpoints = sorted(
            endpoint_usable,
            key=lambda pair: (_family_rank(pair[0][0], family_order), -_unit_priority(pair[0][1])),
        )
        return [
            exhibit
            for exhibit in (
                _endpoint_implied_line_exhibit(family, unit, year_map)
                for (family, unit), year_map in ordered_endpoints[:limit]
            )
            if exhibit
        ]
    ordered = sorted(
        usable,
        key=lambda pair: (_family_rank(pair[0][0], family_order), -len(pair[1]), -_unit_priority(pair[0][1])),
    )
    exhibits: List[Dict[str, Any]] = []
    for (family, unit), year_map in ordered:
        years = sorted(year_map)[:8]
        points = [year_map[year] for year in years]
        exhibits.append(
            {
                "type": "line",
                "title": _time_series_exhibit_title(family, unit),
                "subtitle": _time_series_reader_subtitle(family, unit),
                "categories": [str(year) for year in years],
                "series": [{"name": unit, "values": [float(point.get("value") or 0) for point in points]}],
                "y_label": unit,
                "point_labels": [_display_comparable_value(float(point.get("value") or 0), unit) for point in points],
                "estimated_points": [False for _point in points],
                "caption": "",
                "footnote": f"Only dated values with the same normalized unit ({unit}) are connected; the line is not a forecast unless the cited source states a forecast year.",
                "source_note": _source_note(points),
                "data_basis": [_basis_item(item) for item in points],
                "evidence_quality": "dated_same_unit_source_extracted",
            }
        )
        if len(exhibits) >= limit:
            break
    if len(exhibits) < limit:
        ordered_endpoints = sorted(
            endpoint_usable,
            key=lambda pair: (_family_rank(pair[0][0], family_order), -_unit_priority(pair[0][1])),
        )
        for (family, unit), year_map in ordered_endpoints:
            exhibit = _endpoint_implied_line_exhibit(family, unit, year_map)
            if exhibit:
                exhibits.append(exhibit)
            if len(exhibits) >= limit:
                break
    return exhibits


def _can_build_endpoint_implied_path(family: str, unit: str, year_map: Dict[int, Dict[str, Any]]) -> bool:
    if str(family or "") not in ENDPOINT_IMPLIED_FAMILIES or str(unit or "") not in ENDPOINT_IMPLIED_UNITS:
        return False
    years = sorted(year_map)
    if len(years) != 2:
        return False
    gap = years[-1] - years[0]
    if gap < 2 or gap > MAX_IMPLIED_PATH_POINTS - 1:
        return False
    start = float(year_map[years[0]].get("value") or 0)
    end = float(year_map[years[-1]].get("value") or 0)
    return start > 0 and end > 0


def _endpoint_implied_line_exhibit(family: str, unit: str, year_map: Dict[int, Dict[str, Any]]) -> Dict[str, Any] | None:
    if not _can_build_endpoint_implied_path(family, unit, year_map):
        return None
    source_years = sorted(year_map)
    start_year, end_year = source_years[0], source_years[-1]
    start_value = float(year_map[start_year].get("value") or 0)
    end_value = float(year_map[end_year].get("value") or 0)
    gap = end_year - start_year
    if gap <= 0:
        return None
    annual_factor = (end_value / start_value) ** (1.0 / gap)
    years = list(range(start_year, end_year + 1))[:MAX_IMPLIED_PATH_POINTS]
    values = [start_value * (annual_factor ** (year - start_year)) for year in years]
    estimated = [year not in source_years for year in years]
    labels = [
        f"est. {_display_comparable_value(value, unit)}" if is_estimate else _display_comparable_value(value, unit)
        for value, is_estimate in zip(values, estimated)
    ]
    endpoints = [year_map[start_year], year_map[end_year]]
    return {
        "type": "line",
        "title": _endpoint_implied_title(family, unit),
        "subtitle": _endpoint_implied_subtitle(family, unit),
        "categories": [str(year) for year in years],
        "series": [{"name": f"{unit} implied path", "values": values}],
        "y_label": unit,
        "point_labels": labels,
        "estimated_points": estimated,
        "caption": "",
        "footnote": (
            f"Only {start_year} and {end_year} are source-stated endpoints. Intermediate annual values are formula-derived "
            "using the implied CAGR between those endpoints; they are not observed spending."
        ),
        "source_note": _source_note(endpoints),
        "data_basis": [_basis_item(item) for item in endpoints],
        "evidence_quality": "endpoint_implied_cagr",
    }


def _endpoint_implied_title(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "funding":
        return "Funding endpoints imply the annual pace capital formation would need to sustain"
    if family_value == "capacity":
        return "Capacity endpoints imply the annual build-out pace behind the public targets"
    if family_value == "media":
        return "Coverage endpoints imply the attention path between public milestones"
    return f"Source-stated endpoints imply an annual path in {unit}"


def _endpoint_implied_subtitle(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "funding":
        return "The exhibit makes the implied annual funding pace visible instead of presenting two dated figures as a trend."
    if family_value == "capacity":
        return "The exhibit separates source-stated endpoints from the annual build-out pace implied between them."
    return "The exhibit expands two source-stated endpoints into a transparent implied path, with intermediate values marked as estimates."


def _funding_bubble_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    items = []
    for item in ledger:
        if str(item.get("metric_family") or "") != "funding" or not item.get("year"):
            continue
        unit = str(item.get("unit") or "")
        value = item.get("value")
        if value is None or unit not in {"$M", "$B"}:
            continue
        normalized = dict(item)
        normalized["value"] = float(value) * (1000.0 if unit == "$B" else 1.0)
        normalized["unit"] = "$M"
        normalized["display_value"] = _display_comparable_value(float(normalized["value"]), "$M")
        items.append(normalized)
    if len(items) < 4:
        return None
    items = sorted(items, key=lambda item: (int(item.get("year") or 0), float(item.get("value") or 0)))[:8]
    years = [int(item.get("year") or 0) for item in items]
    values = [float(item.get("value") or 0) for item in items]
    min_year, max_year = min(years), max(years)
    min_value, max_value = min(values), max(values)
    points = []
    for item, year, value in zip(items, years, values):
        x = 50.0 if min_year == max_year else 12.0 + ((year - min_year) / (max_year - min_year)) * 78.0
        y = 50.0 if abs(max_value - min_value) < 1e-6 else 16.0 + ((value - min_value) / (max_value - min_value)) * 72.0
        points.append(
            {
                "label": _short_label(f"{year}: {item.get('display_value')}", 34),
                "x": x,
                "y": y,
                "size": 18.0 + min(80.0, (value / max(max_value, 1.0)) * 82.0),
            }
        )
    return {
        "type": "bubble",
        "title": "Funding signals show when public-private capital commitments become visible",
        "subtitle": "Dated dollar figures show where commitment has moved from ambition into named programs or follow-on capital.",
        "x_label": "Earlier to later date",
        "y_label": "Dollar value",
        "points": points,
        "caption": "",
        "footnote": "Bubble x-position uses cited year; y-position and bubble size use source-stated dollar values normalized to USD millions.",
        "source_note": _source_note(items),
        "data_basis": [_basis_item(item) for item in items],
        "evidence_quality": "dated_funding_bubble",
    }


def _year_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    counts = Counter(int(item["year"]) for item in ledger if item.get("year"))
    if len(counts) < 3:
        return None
    years = sorted(counts)[:8]
    basis = []
    for year in years:
        point = next((item for item in ledger if item.get("year") == year), None)
        if point:
            basis.append(_basis_item(point))
    return {
        "type": "line",
        "title": "Dated public evidence shows where the chronology is strongest",
        "subtitle": "Count of extracted evidence points with explicit years.",
        "categories": [str(year) for year in years],
        "series": [{"name": "Evidence points", "values": [counts[year] for year in years]}],
        "caption": "The line is a chronology of cited evidence density, not a market forecast.",
        "source_note": _source_note([item for item in ledger if item.get("year") in years][:6]),
        "data_basis": basis[:6],
        "evidence_quality": "source_year_count",
    }


def _comparable_unit(unit: str) -> str:
    unit_value = str(unit or "")
    if unit_value in {"$B", "$M"}:
        return "$M"
    if unit_value in {"GW", "MW"}:
        return "MW"
    if unit_value in {"TWh", "GWh", "MWh"}:
        return "MWh"
    if unit_value in {"%", "MJ", "KG", "years", "months", "days", "articles", "mentions", "companies", "plants", "projects", "components", "parts"}:
        return unit_value
    return unit_value if _unit_priority(unit_value) > 1 else ""


def _comparable_value(value: float, unit: str, common_unit: str) -> float:
    if unit == "$B" and common_unit == "$M":
        return value * 1000.0
    if unit == "GW" and common_unit == "MW":
        return value * 1000.0
    if unit == "TWh" and common_unit == "MWh":
        return value * 1_000_000.0
    if unit == "GWh" and common_unit == "MWh":
        return value * 1000.0
    return value


def _display_comparable_value(value: float, unit: str) -> str:
    if unit == "$M":
        if abs(value) >= 1000:
            return f"${value / 1000:.1f}B"
        return f"${value:.0f}M" if abs(value - round(value)) < 1e-6 else f"${value:.1f}M"
    if unit == "MW":
        if abs(value) >= 1000:
            return f"{value / 1000:.1f} GW"
        return f"{value:.0f} MW" if abs(value - round(value)) < 1e-6 else f"{value:.1f} MW"
    if unit == "MWh":
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.1f} TWh"
        if abs(value) >= 1000:
            return f"{value / 1000:.1f} GWh"
        return f"{value:.0f} MWh" if abs(value - round(value)) < 1e-6 else f"{value:.1f} MWh"
    if unit == "%":
        return f"{value:.0f}%" if abs(value - round(value)) < 1e-6 else f"{value:.1f}%"
    if unit:
        return f"{value:.0f} {unit}" if abs(value - round(value)) < 1e-6 else f"{value:.2f} {unit}"
    return str(value)


def _comparable_exhibit_title(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "technology" and unit == "MJ":
        return "The lab proof point is an energy bridge, not yet a power-plant economics case"
    if family_value == "funding":
        return "Capital commitments are visible, but still concentrated in a few public signals"
    if family_value == "market" and unit == "%":
        return "Commercial timelines still depend on confidence bands, not delivered electricity"
    if family_value == "cost" and unit == "%":
        return "Cost sharing reveals how much public programs still rely on private balance sheets"
    if family_value == "market":
        return "Demand and market signals should be compared only where the unit is consistent"
    if family_value == "media":
        return "News coverage shows when external attention concentrates around the topic"
    if family_value == "capacity":
        return "Capacity claims need same-unit comparison before they can support scale decisions"
    if family_value == "cost":
        return "Cost benchmarks must be put on the same unit before they change allocation choices"
    if unit == "%":
        return "Comparable percentages show where the public record quantifies the issue"
    return f"Comparable source-backed values use one unit: {unit}"


def _comparable_reader_subtitle(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "funding":
        return "The visible dollar figures cluster around government programs, private follow-on capital and public budget requests."
    if family_value == "technology" and unit == "MJ":
        return "The public milestone is a physics proof point; it still needs plant-level economics, repetition and systems integration."
    if family_value == "market" and unit == "%":
        return "Surveyed or reported percentages help separate confidence in target dates from proof of commercial deployment."
    if family_value == "cost" and unit == "%":
        return "Public cost-share thresholds show where commercial developers still need government leverage."
    if family_value == "capacity":
        return "Capacity claims matter only where projects state a comparable unit and milestone."
    if family_value == "media":
        return "Coverage intensity can flag when policy, financing or technical milestones change external attention."
    return "The exhibit compares only values that public sources state in the same unit."


def _time_series_exhibit_title(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "funding":
        return "Dated funding signals show whether capital formation is accelerating"
    if family_value == "market":
        return "Dated market signals show whether demand evidence is broadening"
    if family_value == "media":
        return "GDELT news coverage shows when public attention accelerates or fades"
    if family_value == "capacity":
        return "Dated capacity signals show whether project execution is moving beyond announcements"
    if family_value == "cost":
        return "Dated cost benchmarks show whether economics are improving fast enough"
    return f"Dated source-backed values show the trajectory in {unit}"


def _time_series_reader_subtitle(family: str, unit: str) -> str:
    family_value = str(family or "evidence")
    if family_value == "funding":
        return "A dated view separates near-term appropriations and program authorizations from older launch milestones."
    if family_value == "media":
        return "The pattern shows whether external attention is accelerating, fading or clustering around specific milestones."
    if family_value == "technology":
        return "Dated technical milestones show how long the path is from proof point to repeatable plant evidence."
    if family_value == "capacity":
        return "Project timing and capacity evidence reveal whether scale-up is moving beyond announcements."
    return f"The trajectory uses dated public values reported in {unit}."


def _evidence_family_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(str(item.get("metric_family") or "other") for item in ledger)
    rows = counts.most_common(6)
    return {
        "type": "bar",
        "title": "The public record is densest in a few evidence families",
        "subtitle": "Count of source-backed numeric or dated evidence points by management question.",
        "categories": [label.title() for label, _count in rows],
        "series": [{"name": "Evidence points", "values": [count for _label, count in rows]}],
        "caption": "This chart shows where the report can make firmer claims and where it should preserve evidence boundaries.",
        "source_note": _source_note(ledger[:8]),
        "data_basis": [_basis_item(item) for item in ledger[:8]],
        "evidence_quality": "source_fact_count",
    }


def _support_matrix_exhibit(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack) -> Dict[str, Any]:
    families = ["market", "funding", "cost", "capacity", "timeline"]
    rows = []
    values = []
    for family in families:
        items = [item for item in ledger if item.get("metric_family") == family]
        if not items:
            continue
        rows.append(family.title())
        values.append(
            [
                min(5, len({item.get("domain") for item in items if item.get("domain")})),
                min(5, sum(1 for item in items if item.get("value") is not None)),
                min(5, sum(1 for item in items if item.get("year"))),
                min(5, sum(1 for item in items if item.get("authoritative"))),
            ]
        )
    if not rows:
        rows = ["Sources", "Numbers", "Dates"]
        values = [[min(5, fact_pack.source_count), min(5, len(fact_pack.numeric_facts)), min(5, len(fact_pack.dated_facts)), min(5, fact_pack.authoritative_source_count)]]
    return {
        "type": "matrix",
        "title": "Evidence support is strongest where sources, numbers and dates overlap",
        "subtitle": "Five-point scores are counts capped at five, not subjective ratings.",
        "rows": rows[:5],
        "columns": ["Domains", "Numbers", "Dates", "Authority"],
        "values": values[:5],
        "caption": "The matrix exposes where management conclusions are well supported and where additional source work is needed.",
        "source_note": _source_note(ledger[:8]),
        "data_basis": [_basis_item(item) for item in ledger[:8]],
        "evidence_quality": "count_capped_source_support",
    }


def _point_from_sentence(sentence: str, source: SourceDocument, source_idx: int, topic: str) -> EvidencePoint | None:
    points = _points_from_sentence(sentence, source, source_idx, topic)
    return points[0] if points else None


def _points_from_sentence(sentence: str, source: SourceDocument, source_idx: int, topic: str) -> List[EvidencePoint]:
    cleaned = _clean_sentence(sentence)
    if len(cleaned) < 35 or _is_noise(cleaned):
        return []
    parsed_values = _parse_values(cleaned)
    years = [int(x) for x in YEAR_RE.findall(cleaned)]
    domain = source.domain or _domain(source.url)
    authoritative = _is_authoritative(domain, source.source_type)
    out: List[EvidencePoint] = []
    if parsed_values:
        for value, unit, display_value in parsed_values[:5]:
            family = _metric_family(cleaned, unit, topic)
            score = 1
            score += 5 if authoritative else 0
            score += 3 if value is not None and unit not in {"", "year"} else 0
            score += 2 if years else 0
            score += 2 if family != "other" else 0
            score += min(3, len(cleaned) // 120)
            out.append(
                EvidencePoint(
                    id="",
                    fact=cleaned[:360],
                    value=value,
                    unit=unit,
                    display_value=display_value,
                    year=years[0] if years else None,
                    metric_family=family,
                    source_title=source.title or source.domain or f"Source {source_idx}",
                    source_url=source.url,
                    domain=domain,
                    source_type=source.source_type,
                    authoritative=authoritative,
                    score=score,
                )
            )
        return out
    if not years:
        return []
    year = years[0]
    family = _metric_family(cleaned, "year", topic)
    score = 1
    score += 5 if authoritative else 0
    score += 2
    score += 2 if family != "other" else 0
    score += min(3, len(cleaned) // 120)
    return [
        EvidencePoint(
            id="",
            fact=cleaned[:360],
            value=None,
            unit="year",
            display_value=str(year),
            year=year,
            metric_family=family,
            source_title=source.title or source.domain or f"Source {source_idx}",
            source_url=source.url,
            domain=domain,
            source_type=source.source_type,
            authoritative=authoritative,
            score=score,
        )
    ]


def _parse_best_value(text: str) -> tuple[float | None, str, str] | None:
    values = _parse_values(text)
    if not values:
        return None
    return sorted(values, key=lambda item: _unit_priority(item[1]), reverse=True)[0]


def _parse_values(text: str) -> List[tuple[float | None, str, str]]:
    parsed: List[tuple[float | None, str, str]] = []
    seen: set[tuple[float | None, str, str]] = set()
    for match in VALUE_RE.finditer(text):
        raw_number = match.group("number")
        if not raw_number:
            continue
        unit = _normalize_unit(match.group("prefix") or "", match.group("unit") or "", text)
        if not unit and not match.group("prefix"):
            continue
        if not unit and len(raw_number) == 4 and raw_number.startswith(("19", "20")):
            continue
        try:
            value = float(raw_number.replace(",", ""))
        except ValueError:
            continue
        display = _display_value(raw_number, unit, match.group("prefix") or "", match.group("unit") or "")
        key = (value, unit, display)
        if key in seen:
            continue
        seen.add(key)
        parsed.append((value, unit, display))
    return parsed


def _normalize_unit(prefix: str, unit: str, text: str) -> str:
    prefix_l = prefix.lower()
    unit_l = unit.lower()
    if prefix_l in {"$", "us$", "usd"} and unit_l in {"billion", "bn"}:
        return "$B"
    if prefix_l in {"$", "us$", "usd"} and unit_l in {"million", "mn"}:
        return "$M"
    if prefix_l in {"$", "us$", "usd"}:
        return "$"
    if unit_l in {"billion", "bn"} and re.search(r"\$|usd|funding|investment|raised|capital", text, re.I):
        return "$B"
    if unit_l in {"million", "mn"} and re.search(r"\$|usd|funding|investment|raised|capital", text, re.I):
        return "$M"
    if unit_l in {"%", "percent", "percentage point", "percentage points"}:
        return "%"
    if unit_l in {"gw", "mw", "gwh", "twh", "mwh", "kg", "mj"}:
        return unit.upper()
    if unit_l in {"megajoule", "megajoules"}:
        return "MJ"
    if unit_l in {"kilogram", "kilograms"}:
        return "KG"
    if unit_l in {"year", "years"}:
        return "years"
    if unit_l in {"month", "months"}:
        return "months"
    if unit_l in {"day", "days"}:
        return "days"
    if unit_l in {"article", "articles"}:
        return "articles"
    if unit_l in {"mention", "mentions"}:
        return "mentions"
    if unit_l in {"companies", "plants", "projects", "component", "components", "part", "parts"}:
        return unit_l
    if unit_l in {"usd", "dollars"}:
        return "$"
    return ""


def _display_value(raw_number: str, normalized_unit: str, prefix: str, raw_unit: str) -> str:
    number = raw_number.replace(",", "")
    if normalized_unit in {"$B", "$M", "$"}:
        return f"${number}" if normalized_unit == "$" else f"${number}{normalized_unit[-1]}"
    if normalized_unit == "%":
        return f"{number}%"
    if normalized_unit:
        return f"{number} {normalized_unit}"
    if prefix:
        return f"{prefix}{number}"
    return f"{number} {raw_unit}".strip()


def _metric_family(text: str, unit: str, topic: str) -> str:
    lower = str(text or "").lower()
    topic_l = str(topic or "").lower()
    if any(token in lower for token in ("adoption", "penetration", "users", "customers", "subscribers", "installations", "installed base", "orders", "pilot", "conversion")):
        return "adoption"
    if any(token in lower for token in ("price", "pricing", "arpu", "asp", "average selling price")):
        return "pricing"
    if any(token in lower for token in ("cost", "lcoe", "$/mwh", "capex", "opex", "roi", "payback", "margin", "profit")):
        return "cost"
    if any(token in lower for token in ("market", "demand", "revenue", "sales", "tam", "sam", "som", "addressable", "value pool")):
        return "market"
    if any(token in lower for token in ("funding", "raised", "investment", "capital", "venture", "financing")) or unit in {"$B", "$M"}:
        return "funding"
    if any(token in lower for token in ("ignition", "breakeven", "energy output", "megajoule", "scientific proof", "experiment")) or unit in {"MJ"}:
        return "technology"
    if any(token in lower for token in ("capacity", "gw", "mw", "mwh", "gwh", "twh", "plant", "reactor")) or unit in {"GW", "MW", "MWh", "GWh", "TWh"}:
        return "capacity"
    if any(token in lower for token in ("customer", "companies")):
        return "market"
    if any(token in lower for token in ("gdelt", "article", "articles", "news coverage", "media coverage")) or unit in {"articles", "mentions"}:
        return "media"
    if any(token in lower for token in ("year", "timeline", "target", "baseline", "operation", "commercial", "202")) or unit in {"years", "months", "year"}:
        return "timeline"
    if any(token in lower for token in ("policy", "regulation", "license", "nrc", "government")):
        return "policy"
    if any(token in topic_l for token in ("market", "commercial", "customer")):
        return "market"
    return "other"


def _plan_hypotheses(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for idx, item in enumerate(_as_list((plan or {}).get("hypotheses")), start=1):
        if isinstance(item, dict):
            text = str(item.get("hypothesis") or item.get("claim") or item.get("title") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "id": str(item.get("id") or f"H{idx}").strip(),
                    "hypothesis": text,
                    "decision_relevance": str(item.get("decision_relevance") or "").strip(),
                    "needed_evidence": [str(x).strip() for x in _as_list(item.get("needed_evidence") or item.get("evidence_needed") or item.get("data_needed")) if str(x).strip()],
                }
            )
        else:
            text = str(item or "").strip()
            if text:
                out.append({"id": f"H{idx}", "hypothesis": text, "decision_relevance": "", "needed_evidence": []})
    return out[:7]


def _plan_sizing_methods(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    sizing_plan = (plan or {}).get("market_sizing_plan") or {}
    raw_methods = sizing_plan.get("methods") if isinstance(sizing_plan, dict) else sizing_plan
    methods = []
    for idx, item in enumerate(_as_list(raw_methods), start=1):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or item.get("name") or item.get("type") or f"Sizing method {idx}").strip()
        if not method:
            continue
        methods.append(
            {
                "method": method,
                "formula": str(item.get("formula") or "").strip(),
                "variables": [str(x).strip() for x in _as_list(item.get("variables") or item.get("inputs")) if str(x).strip()],
                "known_limitations": [str(x).strip() for x in _as_list(item.get("known_limitations") or item.get("limitations")) if str(x).strip()],
            }
        )
    return methods[:5]


def _ledger_for_families(ledger: List[Dict[str, Any]], families: set[str]) -> List[Dict[str, Any]]:
    return [
        item
        for item in ledger
        if str(item.get("metric_family") or "") in families
        and (item.get("display_value") or item.get("year") or item.get("fact"))
    ]


def _ledger_for_hypothesis(ledger: List[Dict[str, Any]], hypothesis: str, needed_evidence: List[str]) -> List[Dict[str, Any]]:
    keywords = _keywords(" ".join([hypothesis] + needed_evidence))
    if not keywords:
        return ledger[:3]
    scored = []
    for item in ledger:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("fact", "metric_family", "source_title", "domain", "display_value")
        ).lower()
        score = sum(1 for keyword in keywords if keyword in haystack)
        family = str(item.get("metric_family") or "")
        if family in {"market", "adoption", "pricing", "cost", "capacity", "funding", "policy"}:
            score += 1
        if score:
            scored.append((score, item))
    return [item for _score, item in sorted(scored, key=lambda pair: pair[0], reverse=True)]


def _fallback_need_items(chart_data_needs: List[Dict[str, Any]], row_label: str) -> List[Dict[str, Any]]:
    row_l = str(row_label or "").lower()
    out = []
    for idx, need in enumerate(chart_data_needs or [], start=1):
        text = " ".join(
            [
                str(need.get("title") or ""),
                str(need.get("executive_question") or ""),
                " ".join(str(x) for x in _as_list(need.get("required_metrics"))),
                str(need.get("sizing_role") or ""),
            ]
        )
        if any(token in text.lower() for token in _keywords(row_l)):
            out.append(
                {
                    "id": str(need.get("id") or f"N{idx}"),
                    "value": "Data need",
                    "fact": _short_label(text, 260),
                    "source_title": "Chart data needs",
                    "url": "",
                    "domain": "",
                }
            )
    return out


def _support_count_label(count: int, *, zh: bool = False) -> str:
    if zh:
        if count >= 5:
            return f"{count} 条公开信号"
        if count >= 2:
            return f"{count} 条可用信号"
        if count == 1:
            return "1 条公开信号"
        return "证据缺口"
    if count >= 5:
        return f"{count} public signals"
    if count >= 2:
        return f"{count} usable signals"
    if count == 1:
        return "1 public signal"
    return "Evidence gap"


def _best_signal(items: List[Dict[str, Any]]) -> str:
    for item in items or []:
        value = str(item.get("display_value") or item.get("value") or "").strip()
        fact = str(item.get("fact") or "").strip()
        if value and fact and value not in fact:
            return f"{value}: {fact}"
        if fact:
            return fact
    return ""


def _method_signal(methods: List[Dict[str, Any]], idx: int) -> str:
    if idx < len(methods):
        method = methods[idx]
        formula = method.get("formula") or ""
        variables = ", ".join(method.get("variables") or [])
        return " | ".join(part for part in [str(method.get("method") or ""), formula, variables] if part)
    return ""


def _sizing_use_text(row_label: str, *, zh: bool = False) -> str:
    lower = str(row_label or "").lower()
    if "demand" in lower or "需求" in lower:
        return "Bound the outer demand pool." if not zh else "限定外层需求池。"
    if "accessible" in lower or "market" in lower or "触达" in lower:
        return "Identify where demand is actually reachable." if not zh else "识别真实可触达的需求。"
    if "adoption" in lower or "采用" in lower:
        return "Translate demand into realistic uptake." if not zh else "把需求转成现实采用。"
    if "economics" in lower or "value" in lower or "经济性" in lower:
        return "Test whether revenue can convert into value." if not zh else "检验收入能否转成价值。"
    if "supply" in lower or "供给" in lower:
        return "Check whether supply can meet demand." if not zh else "检查供给能否支撑需求。"
    return "Convert evidence into a decision variable." if not zh else "把证据转成决策变量。"


def _keywords(text: str) -> List[str]:
    raw = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+-]{2,}", str(text or "").lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "market",
        "sizing",
        "evidence",
        "public",
        "data",
        "that",
        "this",
        "should",
        "是否",
        "数据",
        "市场",
        "证据",
        "公开",
        "需要",
    }
    out = []
    for token in raw:
        if token in stop or len(token) < 2:
            continue
        if token not in out:
            out.append(token)
    return out[:18]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _split_sentences(text: str) -> List[str]:
    chunks = re.split(r"(?<=[.!?。！？；;])\s+|\n+", str(text or ""))
    return [chunk.strip() for chunk in chunks if chunk and len(chunk.strip()) >= 20]


def _clean_sentence(text: str) -> str:
    text = _strip_source_prefix(text)
    text = re.sub(r"\s+", " ", text).strip(" -•\t")
    return text


def _strip_source_prefix(text: str) -> str:
    return re.sub(r"^\[Source\s+\d+:[^\]]+\]\s*", "", str(text or "")).strip()


def _is_noise(text: str) -> bool:
    lower = text.lower()
    nav_tokens = (
        "making it work advantages of fusion",
        "fusion glossary",
        "all news",
        "photos videos",
        "publication centre",
        "subscribe to the newsletter",
        "select your newsletters",
        "privacy policy",
        "for scientists",
        "for industry",
        "for the press",
        "jobs about",
        "source url retained for public-source review",
        "search context:",
        "page body could not be fully extracted",
    )
    if any(token in lower for token in nav_tokens):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z&+-]*", text)
    if len(words) >= 10 and not re.search(r"[.!?;:]", text):
        short_title_words = sum(1 for word in words if word[:1].isupper() and len(word) > 2)
        if short_title_words / max(1, len(words)) > 0.65:
            return True
    return any(
        token in lower
        for token in (
            "cookie",
            "subscribe",
            "newsletter",
            "privacy policy",
            "all rights reserved",
            "javascript",
            "enable cookies",
            "click here",
            "glossary",
        )
    )


def _source_for_fact(fact: str, source_lookup: Dict[int, SourceDocument]) -> SourceDocument | None:
    match = re.search(r"\[Source\s+(\d+):", str(fact or ""))
    if match:
        return source_lookup.get(int(match.group(1)))
    return next(iter(source_lookup.values()), None)


def _dedupe_points(points: List[EvidencePoint], *, limit: int) -> List[EvidencePoint]:
    out: List[EvidencePoint] = []
    seen: set[str] = set()
    for point in points:
        if _numeric_fragment_of_existing(point, out):
            continue
        key = re.sub(r"\W+", "", f"{point.display_value} {point.fact}".lower())[:180]
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
        if len(out) >= limit:
            break
    return out


def _numeric_fragment_of_existing(point: EvidencePoint, existing_points: List[EvidencePoint]) -> bool:
    if not point.display_value or not point.fact:
        return False
    for existing in existing_points:
        if point.source_url and existing.source_url and point.source_url != existing.source_url:
            continue
        if point.unit != existing.unit:
            continue
        if not existing.display_value or not existing.fact:
            continue
        if point.display_value == existing.display_value:
            continue
        if point.display_value in existing.display_value and point.fact in existing.fact:
            return True
    return False


def _domain(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_authoritative(domain: str, source_type: str) -> bool:
    domain_l = str(domain or "").lower()
    if any(hint in domain_l for hint in AUTHORITY_HINTS):
        return True
    return str(source_type or "").lower() == "pdf"


def _basis_item(point: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": point.get("id") or "",
        "value": point.get("display_value") or "",
        "fact": point.get("fact") or "",
        "source_title": point.get("source_title") or "",
        "url": point.get("source_url") or "",
        "domain": point.get("domain") or "",
    }


def _mixed_basis(ledger: List[Dict[str, Any]], fact_pack: ResearchFactPack, *, limit: int) -> List[Dict[str, Any]]:
    basis = [_basis_item(item) for item in ledger[:limit]]
    if len(basis) < min(4, limit):
        basis.extend(_fact_pack_basis(fact_pack)[: limit - len(basis)])
    return basis[:limit]


def _family_count(ledger: List[Dict[str, Any]], families: set[str]) -> int:
    return sum(1 for item in ledger if str(item.get("metric_family") or "") in families)


def _support_label(count: int) -> str:
    if count >= 5:
        return "Strong public support"
    if count >= 2:
        return "Some public support"
    if count == 1:
        return "Single public signal"
    return "Open evidence gap"


def _timeline_title(fact: str) -> str:
    text = str(fact or "")
    if "ignition" in text.lower():
        return "Scientific proof point"
    if "program" in text.lower() or "funding" in text.lower():
        return "Public program signal"
    if "commercial" in text.lower() or "pilot" in text.lower():
        return "Commercialization milestone"
    return text


def _low_value_numeric_fact(fact: str, unit: str) -> bool:
    if unit:
        return False
    lower = str(fact or "").lower()
    return any(token in lower for token in ("representative", "source ", "search context", "news & media", "all news", "photos videos"))


def _fact_pack_basis(fact_pack: ResearchFactPack) -> List[Dict[str, Any]]:
    basis = []
    for idx, ref in enumerate(fact_pack.source_refs[:8], start=1):
        item = _basis_from_source_ref(ref, idx)
        if item:
            basis.append(item)
    if basis:
        return basis
    for idx, domain in enumerate(fact_pack.source_domains[:8], start=1):
        basis.append(
            {
                "id": f"S{idx}",
                "value": "Source domain",
                "fact": f"Retained source domain: {domain}",
                "source_title": domain,
                "url": "",
                "domain": domain,
            }
        )
    return basis


def _basis_from_source_ref(ref: str, idx: int) -> Dict[str, Any] | None:
    raw = str(ref or "").strip()
    if not raw:
        return None
    url_match = URL_RE.search(raw)
    url = url_match.group(0).rstrip(".") if url_match else ""
    title_part = raw.split("|", 1)[0]
    title_part = re.sub(r"^\[Source\s+\d+\][^]]*\]\[[^]]*\]\s*", "", title_part).strip()
    domain = _domain(url) if url else ""
    return {
        "id": f"S{idx}",
        "value": "Retained source",
        "fact": raw[:360],
        "source_title": title_part or domain or f"Source {idx}",
        "url": url,
        "domain": domain,
    }


def _source_note(points: List[Dict[str, Any]]) -> str:
    ids = [str(item.get("id") or "") for item in points if item.get("id")]
    domains = []
    for item in points:
        domain = str(item.get("domain") or "")
        if domain and domain not in domains:
            domains.append(domain)
    if ids and domains:
        return f"Evidence: {', '.join(ids[:8])}. Sources: {'; '.join(domains[:6])}."
    if domains:
        return f"Sources: {'; '.join(domains[:6])}."
    return "Sources: evidence ledger retained with the report."


def _chart_label(item: Dict[str, Any]) -> str:
    year = str(item.get("year") or "")
    unit = str(item.get("unit") or "")
    fact = str(item.get("fact") or "")
    display = str(item.get("display_value") or "")
    number = display.replace("$", "").replace("%", "").split(" ")[0].replace(",", "")
    lower = fact.lower()
    if unit == "%":
        if number and re.search(rf"{re.escape(number)}\s*%\s+decline\s+in\s+workforce\s+hours", lower):
            return "Workforce hours decline"
        if number and re.search(rf"{re.escape(number)}\s*%\s+decline\s+in\s+output", lower):
            return "Output decline"
        if "greater revenue growth" in lower or "revenue growth" in lower:
            return "AI leader revenue growth uplift"
    if unit == "MJ":
        if "target" in lower and display.split(" ")[0] in lower:
            if "resulting" in lower and "output" in lower and display.startswith("3."):
                return "Fusion energy output"
            return "Energy delivered to target"
        if "output" in lower:
            return "Fusion energy output"
    local = _local_value_label(item)
    if local:
        return local
    title = str(item.get("source_title") or item.get("domain") or item.get("metric_family") or "Source")
    if year:
        return f"{title} ({year})"
    return title


def _local_value_label(item: Dict[str, Any]) -> str:
    fact = str(item.get("fact") or "")
    display = str(item.get("display_value") or "")
    if not fact or not display:
        return ""
    number = display.replace("$", "").replace("%", "").split(" ")[0].replace(",", "")
    if not number:
        return ""
    idx = fact.replace(",", "").find(number)
    if idx < 0:
        return ""
    start = max(0, idx - 48)
    end = min(len(fact), idx + len(number) + 58)
    label = fact[start:end]
    label = re.sub(r"\s+", " ", label).strip(" ,;:.")
    label = re.sub(r"^[a-z]{1,4}\s+", "", label, flags=re.I)
    return label


def _short_label(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "."


def _unit_priority(unit: str) -> int:
    return {
        "$B": 10,
        "$M": 9,
        "$": 8,
        "%": 7,
        "GW": 6,
        "MW": 6,
        "MWh": 6,
        "GWh": 6,
        "TWh": 6,
        "MJ": 6,
        "GWH": 6,
        "MWH": 6,
        "TWH": 6,
        "KG": 5,
        "years": 4,
        "months": 4,
        "days": 4,
        "companies": 3,
        "articles": 3,
        "mentions": 3,
        "plants": 3,
        "projects": 3,
        "components": 3,
        "component": 3,
        "parts": 3,
        "part": 3,
    }.get(str(unit), 1)
