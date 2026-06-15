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

VALUE_RE = re.compile(
    r"(?P<prefix>US\$|\$|USD|RMB|CNY|EUR|HK\$)?\s*"
    r"(?P<number>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage points?|trillion|billion|million|bn|mn|GW|MW|GWh|TWh|MWh|MJ|megajoules?|kg|kilograms?|years?|months?|days?|companies|plants|projects|components?|parts?|USD|dollars?)?",
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
            point = _point_from_sentence(sentence, source, source_idx, topic)
            if point:
                candidates.append(point)

    if len(candidates) < 8:
        source_lookup = {idx + 1: source for idx, source in enumerate(sources)}
        for fact in fact_pack.numeric_facts + fact_pack.dated_facts + fact_pack.high_confidence_facts:
            source = _source_for_fact(fact, source_lookup)
            if source:
                point = _point_from_sentence(_strip_source_prefix(fact), source, 0, topic)
                if point:
                    candidates.append(point)

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
    if str(language or "").lower().startswith("zh"):
        return {
            "core_question": decision_question,
            "central_thesis": "先由公开证据决定报告主线，再把数据、时间线和管理含义串成可执行判断。",
            "narrative_focus": "围绕证据最密集的主题推进：" + "、".join(strongest[:4]),
            "structure_logic": "先说明管理层问题，再呈现源自事实包的数据图表，随后讨论商业含义、风险和行动门槛。",
            "evidence_must_cover": facts,
            "charting_rule": "图表只能使用 evidence_ledger 中可回溯到 URL 的数字、年份、来源计数或同单位可比数据。",
        }
    return {
        "core_question": decision_question,
        "central_thesis": "The storyline should be led by the strongest public evidence, then translated into management choices.",
        "narrative_focus": "Evidence is densest around: " + ", ".join(strongest[:4] or ["source quality", "timing", "commercial proof"]),
        "structure_logic": "Open with the executive decision, show source-backed data exhibits, then move through business implications, risks and action gates.",
        "evidence_must_cover": facts,
        "charting_rule": "Charts may use only evidence_ledger values, dates, source counts or same-unit comparable values tied to public URLs.",
    }


def build_evidence_exhibits(
    topic: str,
    evidence_ledger: List[Dict[str, Any]],
    fact_pack: ResearchFactPack,
    *,
    language: str = "en",
) -> List[Dict[str, Any]]:
    if not evidence_ledger:
        return _fact_pack_exhibits(topic, fact_pack)

    exhibits: List[Dict[str, Any]] = []
    exhibits.append(_metric_row_exhibit(evidence_ledger, fact_pack))

    timeline = _milestone_timeline_exhibit(evidence_ledger, fact_pack)
    if timeline:
        exhibits.append(timeline)

    comparable = _comparable_value_exhibit(evidence_ledger)
    if comparable:
        exhibits.append(comparable)

    matrix = _opportunity_matrix_exhibit(evidence_ledger, fact_pack)
    if matrix:
        exhibits.append(matrix)

    exhibits.append(_stage_gate_process_exhibit(topic, evidence_ledger, fact_pack))
    opportunity = _opportunity_map_exhibit(evidence_ledger, fact_pack)
    if opportunity:
        exhibits.append(opportunity)

    if len(exhibits) < 3:
        fallback = [_year_exhibit(evidence_ledger), _evidence_family_exhibit(evidence_ledger), _support_matrix_exhibit(evidence_ledger, fact_pack)]
        exhibits.extend([item for item in fallback if item][: 3 - len(exhibits)])
    if len(exhibits) < 3:
        exhibits.extend(_fact_pack_exhibits(topic, fact_pack)[: 3 - len(exhibits)])

    for idx, exhibit in enumerate(exhibits[:5], start=1):
        exhibit["id"] = f"evidence-exhibit-{idx}"
        exhibit["no"] = str(idx)
        if idx > 1:
            exhibit.setdefault("after_section_id", f"section-{min(idx, 5)}")
    return exhibits[:5]


def merge_evidence_exhibits(report: Dict[str, Any], evidence_exhibits: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not evidence_exhibits:
        return report
    report["exhibits"] = evidence_exhibits
    return report


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
        and str(item.get("unit") or "") not in {"", "year"}
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
        "subtitle": "A BCG-style article should show the sequence of proof points before recommending commitment.",
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
        "subtitle": "Rows translate the source base into decisions a CEO or board can actually make.",
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


def _fact_pack_exhibits(topic: str, fact_pack: ResearchFactPack) -> List[Dict[str, Any]]:
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
        if idx > 1:
            exhibit["after_section_id"] = f"section-{idx}"
    return exhibits


def _comparable_value_exhibit(ledger: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for item in ledger:
        value = item.get("value")
        unit = str(item.get("unit") or "")
        family = str(item.get("metric_family") or "")
        if value is None or not unit or unit in {"year", "month"}:
            continue
        groups[(family, unit)].append(item)
    usable = [(key, items) for key, items in groups.items() if len(items) >= 3]
    if not usable:
        return None
    (family, unit), items = sorted(usable, key=lambda pair: (len(pair[1]), _unit_priority(pair[0][1])), reverse=True)[0]
    items = sorted(items, key=lambda item: float(item.get("value") or 0), reverse=True)[:6]
    return {
        "type": "bar",
        "title": f"Comparable source-backed values use one unit: {unit}",
        "subtitle": f"Metric family: {family}. Mixed units are intentionally excluded.",
        "categories": [_short_label(_chart_label(item), 28) for item in items],
        "series": [{"name": unit, "values": [float(item.get("value") or 0) for item in items]}],
        "caption": "The bars compare only values with the same parsed unit, avoiding synthetic rankings.",
        "source_note": _source_note(items),
        "data_basis": [_basis_item(item) for item in items],
        "evidence_quality": "same_unit_source_extracted",
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
    cleaned = _clean_sentence(sentence)
    if len(cleaned) < 35 or _is_noise(cleaned):
        return None
    parsed = _parse_best_value(cleaned)
    years = [int(x) for x in YEAR_RE.findall(cleaned)]
    if not parsed and not years:
        return None
    value, unit, display_value = parsed if parsed else (None, "year", str(years[0]))
    family = _metric_family(cleaned, unit, topic)
    domain = source.domain or _domain(source.url)
    authoritative = _is_authoritative(domain, source.source_type)
    score = 1
    score += 5 if authoritative else 0
    score += 3 if value is not None and unit not in {"", "year"} else 0
    score += 2 if years else 0
    score += 2 if family != "other" else 0
    score += min(3, len(cleaned) // 120)
    return EvidencePoint(
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


def _parse_best_value(text: str) -> tuple[float | None, str, str] | None:
    best: tuple[int, float | None, str, str] | None = None
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
        priority = _unit_priority(unit)
        if best is None or priority > best[0]:
            best = (priority, value, unit, display)
    if not best:
        return None
    _priority, value, unit, display = best
    return value, unit, display


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
    if any(token in lower for token in ("funding", "raised", "investment", "capital", "venture", "financing")) or unit in {"$B", "$M"}:
        return "funding"
    if any(token in lower for token in ("cost", "lcoe", "$/mwh", "price", "capex", "opex")):
        return "cost"
    if any(token in lower for token in ("ignition", "breakeven", "energy output", "megajoule", "scientific proof", "experiment")) or unit in {"MJ"}:
        return "technology"
    if any(token in lower for token in ("capacity", "gw", "mw", "mwh", "gwh", "twh", "plant", "reactor")) or unit in {"GW", "MW", "MWh", "GWh", "TWh"}:
        return "capacity"
    if any(token in lower for token in ("market", "demand", "customer", "revenue", "sales", "companies")):
        return "market"
    if any(token in lower for token in ("year", "timeline", "target", "baseline", "operation", "commercial", "202")) or unit in {"years", "months", "year"}:
        return "timeline"
    if any(token in lower for token in ("policy", "regulation", "license", "nrc", "government")):
        return "policy"
    if any(token in topic_l for token in ("market", "commercial", "customer")):
        return "market"
    return "other"


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
        key = re.sub(r"\W+", "", f"{point.display_value} {point.fact}".lower())[:180]
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
        if len(out) >= limit:
            break
    return out


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
    title = str(item.get("source_title") or item.get("domain") or item.get("metric_family") or "Source")
    if year:
        return f"{title} ({year})"
    return title


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
        "plants": 3,
        "projects": 3,
        "components": 3,
        "component": 3,
        "parts": 3,
        "part": 3,
    }.get(str(unit), 1)
