from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.web_publication_contract import (
    client_visible_internal_hits,
    is_internal_workbench_exhibit,
    report_content_quality_issues,
    source_channel_report_quality_issues,
)


BAD_HEADINGS = {
    "overview",
    "background",
    "market overview",
    "market dynamics",
    "trends",
    "analysis",
    "conclusion",
    "introduction",
}

PROCESS_PATTERNS = (
    r"\bthis\s+(?:section|chapter|report)\s+(?:argues|finds|shows|explains|will|is about)\b",
    r"\bthe\s+report\s+should\b",
    r"\binternal\s+framework\b",
    r"\bstress\s+test\b",
)

UNVERIFIED_SOURCE_PATTERNS = (
    r"\bnot in fact pack\b",
    r"\bwidely cited\b",
)

FORBIDDEN_VISIBLE_TEXT = (
    "".join(["b", "c", "g"]),
    "why " + "it matters",
    "blueocean-style",
)

UNSUPPORTED_CHART_PATTERNS = (
    r"\bdirectional\s+(?:priority|assessment|score|index|view)\b",
    r"\bpriority\s+index\b",
    r"\breadiness\s+index\b",
    r"\brelative\s+strength\b",
    r"\bmanagement\s+(?:conviction|attention)\b",
    r"\bevidence\s+maturity\b",
    r"\bblueocean\s+(?:synthesis|assessment|scenario|risk screen|management screen|option synthesis)\b",
    r"\barbitrary\s+(?:score|index)\b",
)

META_CHART_PATTERNS = (
    r"\bpublic record is densest\b",
    r"\bevidence famil(?:y|ies)\b",
    r"\bevidence points with explicit years\b",
    r"\bchronology is strongest\b",
    r"\bfact extraction determines\b",
    r"\bsource support is strongest where sources, numbers and dates overlap\b",
)


def required_reference_count(sources: Any) -> int:
    """Require every available source up to the four-source publication target."""

    source_items = sources if isinstance(sources, list) else []
    source_urls = {
        str(item.get("url") or "").strip()
        for item in source_items
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    }
    source_count = len(source_urls)
    return min(4, source_count) if source_count else 4


def section_quality_issues(
    section: Dict[str, Any],
    index: int,
    *,
    source_channel_profile: bool,
    simplified_profile: bool = False,
) -> List[str]:
    """Return legacy presentation checks without duplicating profile contracts."""

    issues: List[str] = []
    title = text(section.get("title"))
    lead = text(section.get("lead"))
    paragraphs = [text(x) for x in as_list(section.get("paragraphs")) if text(x)]
    evidence = [text(x) for x in as_list(section.get("evidence")) if text(x)]
    body = " ".join([lead] + paragraphs + evidence)
    if title.lower().strip(" .:-") in BAD_HEADINGS:
        issues.append(f"section {index} uses generic label heading: {title}")
    if not source_channel_profile and not simplified_profile and len(title) < 24:
        issues.append(f"section {index} title is too thin: {title}")
    if not source_channel_profile and not simplified_profile and len(paragraphs) < 5:
        issues.append(f"section {index} has too few paragraphs: {len(paragraphs)}")
    if not source_channel_profile and not simplified_profile and len(body) < 1400:
        issues.append(f"section {index} lacks depth ({len(body)} chars): {title[:90]}")
    if not evidence:
        issues.append(f"section {index} has no explicit evidence bullets: {title[:90]}")
    if not source_channel_profile and not simplified_profile and not re.search(
        r"\b(19|20)\d{2}\b|\b\d+(?:\.\d+)?%|\b\$\d+|\b\d+(?:\.\d+)?\s*(?:billion|million|trillion|GW|MW|kg|years?|months?)\b",
        body,
        re.I,
    ):
        issues.append(f"section {index} lacks dates or numeric evidence cues: {title[:90]}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit HTML-first thought leadership report output.")
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    report_dir = args.report_dir
    html_path = report_dir / "index.html"
    payload_path = report_dir / "web_report_payload.json"
    publication_contract_path = report_dir / "publication_contract.json"
    fact_pack_path = report_dir / "research_fact_pack.json"
    evidence_ledger_path = report_dir / "evidence_ledger.json"
    storyline_plan_path = report_dir / "storyline_plan.json"
    chart_data_needs_path = report_dir / "chart_data_needs.json"
    sources_path = report_dir / "sources.json"

    issues: List[str] = []
    metrics: Dict[str, Any] = {"report_dir": str(report_dir)}

    for path in [html_path, payload_path, publication_contract_path, fact_pack_path, evidence_ledger_path, storyline_plan_path, chart_data_needs_path, sources_path]:
        if not path.exists():
            issues.append(f"missing required file: {path.name}")

    if issues:
        return emit(issues, metrics, args.warn_only)

    payload = read_json(payload_path, issues)
    publication_contract = read_json(publication_contract_path, issues)
    sources = read_json(sources_path, issues)
    fact_pack = read_json(fact_pack_path, issues)
    evidence_ledger = read_json(evidence_ledger_path, issues)
    storyline_plan = read_json(storyline_plan_path, issues)
    chart_data_needs = read_json(chart_data_needs_path, issues)
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    html_text = strip_tags(html)

    sections = [x for x in as_list(payload.get("sections")) if isinstance(x, dict)]
    exhibits = [x for x in as_list(payload.get("exhibits")) if isinstance(x, dict)]
    takeaways = [text(x) for x in as_list(payload.get("key_takeaways")) if text(x)]
    takeaway_candidates = {
        key: len(takeaway_texts(payload.get(key)))
        for key in ["key_takeaways", "keyTakeaways", "takeaways", "take_aways", "executive_summary", "key_findings", "findings"]
        if isinstance(payload, dict) and key in payload
    }
    action_steps = [x for x in as_list(payload.get("action_steps")) if isinstance(x, dict)]
    references = [x for x in as_list(payload.get("references")) if isinstance(x, dict)]
    minimum_references = required_reference_count(sources)
    ledger_items = [x for x in as_list(evidence_ledger) if isinstance(x, dict)]
    chart_needs = [x for x in as_list(chart_data_needs) if isinstance(x, dict)]
    data_backed_exhibits = [x for x in exhibits if any(isinstance(item, dict) for item in as_list(x.get("data_basis")))]
    grounding_text = "\n".join(
        [text(item.get("fact")) for item in ledger_items]
        + [text(item.get("content")) for item in as_list(sources) if isinstance(item, dict)]
    )
    evidence_audit = payload.get("evidenceAudit") if isinstance(payload, dict) else {}
    evidence_audit = evidence_audit if isinstance(evidence_audit, dict) else {}
    generation_manifest = evidence_audit.get("manifest")
    generation_manifest = generation_manifest if isinstance(generation_manifest, dict) else {}
    source_channel_profile = generation_manifest.get("generation_profile") == "source_channel"
    presentation_format = text(generation_manifest.get("presentation_format") or payload.get("presentation_format"))
    simplified_profile = presentation_format == "gatex_simplified_v1"
    issues.extend(
        (
            source_channel_report_quality_issues(
                payload,
                topic="",
                context_text=grounding_text,
                source_count=len(as_list(sources)),
            )
            if source_channel_profile
            else report_content_quality_issues(
                payload,
                topic="",
                context_text=grounding_text,
                source_count=len(as_list(sources)),
            )
        )
    )

    metrics.update(
        {
            "sections": len(sections),
            "exhibits": len(exhibits),
            "takeaways": len(takeaways),
            "actions": len(action_steps),
            "references": len(references),
            "required_references": minimum_references,
            "evidence_ledger_points": len(ledger_items),
            "chart_data_needs": len(chart_needs),
            "data_backed_exhibits": len(data_backed_exhibits),
            "sources": len(sources if isinstance(sources, list) else []),
            "fact_pack_sources": fact_pack.get("source_count") if isinstance(fact_pack, dict) else None,
            "storyline_keys": sorted(storyline_plan.keys()) if isinstance(storyline_plan, dict) else [],
            "publication_contract_keys": sorted(publication_contract.keys()) if isinstance(publication_contract, dict) else [],
            "html_chars": len(html_text),
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
            "takeaway_candidate_counts": takeaway_candidates,
            "generation_profile": "source_channel" if source_channel_profile else "generic",
            "presentation_format": presentation_format or "standard_v1",
        }
    )

    if simplified_profile:
        if exhibits:
            issues.append(f"simplified GateX format requires zero exhibits, got {len(exhibits)}")
        image_path = report_dir / "assets" / "image-1.png"
        if not image_path.is_file() or image_path.stat().st_size <= 0:
            issues.append("simplified GateX format is missing assets/image-1.png")
        extra_images = sorted(
            path
            for path in (report_dir / "assets").glob("image-*")
            if path.is_file() and path.name != "image-1.png"
        )
        if extra_images:
            issues.append(
                "simplified GateX format generated extra section images: "
                + ", ".join(path.name for path in extra_images[:8])
            )
        image_prompt_path = report_dir / "backup" / "image_prompts.json"
        image_prompts = read_json(image_prompt_path, issues) if image_prompt_path.is_file() else []
        image_prompt_rows = image_prompts if isinstance(image_prompts, list) else []
        image_prompt = image_prompt_rows[0] if len(image_prompt_rows) == 1 and isinstance(image_prompt_rows[0], dict) else {}
        if text(image_prompt.get("status")) != "pollinations":
            issues.append("simplified GateX format requires one verified AI editorial image")
    elif not (3 <= len(exhibits) <= 6):
        issues.append(f"expected 3-6 exhibits, got {len(exhibits)}")
    if len(references) < minimum_references:
        issues.append(
            f"expected at least {minimum_references} retained references, got {len(references)}"
        )
    if not simplified_profile and len(html_text) < 12000:
        issues.append(f"HTML article appears too thin ({len(html_text)} text chars)")
    if len(ledger_items) < 3:
        issues.append(f"expected at least 3 chartable evidence ledger points, got {len(ledger_items)}")
    if not simplified_profile and len(chart_needs) < 3:
        issues.append(f"expected at least 3 chart data needs, got {len(chart_needs)}")
    if not isinstance(storyline_plan, dict) or not text(storyline_plan.get("core_question")):
        issues.append("storyline_plan.json lacks a core_question")
    if not isinstance(storyline_plan, dict) or not text(storyline_plan.get("exhibit_narrative_rule")):
        issues.append("storyline_plan.json lacks an exhibit_narrative_rule")
    narrative_ready_needs = [
        need
        for need in chart_needs
        if text(need.get("narrative_role"))
        and text(need.get("pre_exhibit_context"))
        and text(need.get("post_exhibit_takeaway"))
    ]
    metrics["narrative_ready_chart_needs"] = len(narrative_ready_needs)
    if not simplified_profile and len(narrative_ready_needs) < min(3, len(chart_needs)):
        issues.append(
            f"expected at least {min(3, len(chart_needs))} chart data needs with narrative role/setup/takeaway, "
            f"got {len(narrative_ready_needs)}"
        )
    if not simplified_profile and len(data_backed_exhibits) < min(3, len(exhibits)):
        issues.append(f"expected at least {min(3, len(exhibits))} exhibits with data_basis, got {len(data_backed_exhibits)}")

    for idx, section in enumerate(sections, start=1):
        issues.extend(
            section_quality_issues(
                section,
                idx,
                source_channel_profile=source_channel_profile,
                simplified_profile=simplified_profile,
            )
        )

    exhibit_types = {text(x.get("type")).lower() for x in exhibits}
    metrics["exhibit_types"] = sorted(exhibit_types)
    non_metric_exhibits = [x for x in exhibits if text(x.get("type")).lower() not in {"metric_row", "metrics", "big_numbers"}]
    metrics["non_metric_exhibits"] = len(non_metric_exhibits)
    if not simplified_profile and len(exhibit_types) < 3:
        issues.append("exhibit mix is too narrow; expected at least 3 chart/exhibit types")
    if not simplified_profile and len(non_metric_exhibits) < 3:
        issues.append(f"expected at least 3 non-metric analytical charts/exhibits, got {len(non_metric_exhibits)}")
    if not simplified_profile and not any(text(x.get("type")).lower() in {"bar", "line", "bubble", "scatter", "opportunity_map"} for x in exhibits):
        issues.append("expected at least one data chart rendered as bar, line or bubble")
    if not simplified_profile and not any(text(x.get("type")).lower() in {"line", "bubble", "scatter", "timeline"} for x in exhibits):
        issues.append("expected at least one non-bar analytical exhibit such as line, bubble/scatter or timeline")
    for idx, exhibit in enumerate(exhibits, start=1):
        title = text(exhibit.get("title"))
        source_note = text(exhibit.get("source_note") or exhibit.get("caption"))
        data_basis = [x for x in as_list(exhibit.get("data_basis")) if isinstance(x, dict)]
        exhibit_text = " ".join(
            [
                title,
                text(exhibit.get("subtitle")),
                text(exhibit.get("caption")),
                text(exhibit.get("source_note")),
                text(exhibit.get("series")),
                text(exhibit.get("categories")),
                text(exhibit.get("rows")),
                text(exhibit.get("columns")),
                text(exhibit.get("values")),
            ]
        ).lower()
        if is_internal_workbench_exhibit(exhibit):
            issues.append(f"exhibit {idx} is an internal workbench exhibit: {title}")
        if len(title) < 24:
            issues.append(f"exhibit {idx} title is too thin: {title}")
        if not source_note:
            issues.append(f"exhibit {idx} lacks caption/source note: {title}")
        if text(exhibit.get("type")).lower() in {"metric_row", "bar", "line", "matrix", "bubble"} and not data_basis:
            issues.append(f"exhibit {idx} lacks source-backed data_basis: {title}")
        if text(exhibit.get("type")).lower() == "line":
            categories = [x for x in as_list(exhibit.get("categories") or exhibit.get("labels") or exhibit.get("x_labels")) if text(x)]
            series = [x for x in as_list(exhibit.get("series")) if isinstance(x, dict)]
            first_values = as_list(series[0].get("values") if series else exhibit.get("values"))
            if len(categories) < 4 or len(first_values) < 4:
                issues.append(f"line exhibit {idx} is too sparse for a client-facing trend: {title}")
            if not text(exhibit.get("y_label") or exhibit.get("unit")):
                issues.append(f"line exhibit {idx} lacks y_label/unit: {title}")
            if len(as_list(exhibit.get("point_labels") or exhibit.get("value_labels"))) < min(len(categories), len(first_values)):
                issues.append(f"line exhibit {idx} lacks visible point-value labels: {title}")
            if text(exhibit.get("evidence_quality")) == "endpoint_implied_cagr":
                footnote = text(exhibit.get("footnote"))
                estimated = [truthy_flag(x) for x in as_list(exhibit.get("estimated_points"))]
                if not any(estimated):
                    issues.append(f"endpoint-implied line exhibit {idx} lacks estimated point flags: {title}")
                if not re.search(r"\b(formula-derived|implied\s+CAGR|GDP|demand-driver|estimate)", footnote, re.I):
                    issues.append(f"endpoint-implied line exhibit {idx} lacks transparent derivation footnote: {title}")
        for pattern in UNSUPPORTED_CHART_PATTERNS:
            if re.search(pattern, exhibit_text, re.I):
                issues.append(f"exhibit {idx} appears to use unsupported synthetic chart metric ({pattern}): {title}")
                break
        for pattern in META_CHART_PATTERNS:
            if re.search(pattern, exhibit_text, re.I):
                issues.append(f"exhibit {idx} is still a research-process/meta chart ({pattern}): {title}")
                break
        if data_basis and not any(text(item.get("url")) or text(item.get("domain")) for item in data_basis):
            issues.append(f"exhibit {idx} data_basis lacks source URL/domain: {title}")

    lower = html_text.lower()
    for pattern in PROCESS_PATTERNS:
        if re.search(pattern, lower, re.I):
            issues.append(f"HTML leaks process language matching: {pattern}")
    for pattern in UNVERIFIED_SOURCE_PATTERNS:
        if re.search(pattern, lower, re.I):
            issues.append(f"HTML contains unsupported source/evidence language matching: {pattern}")
    for pattern in client_visible_internal_hits(html_text):
        issues.append(f"HTML leaks internal analysis language matching: {pattern}")
    for required in ["Key Takeaways", "Contents"]:
        if required.lower() not in lower:
            issues.append(f"HTML missing expected BlueOcean module: {required}")
    for forbidden in FORBIDDEN_VISIBLE_TEXT:
        if forbidden in lower:
            issues.append(f"HTML contains forbidden visible/source text: {forbidden}")
    raw_lower = html.lower()
    if "<summary>sources</summary>" not in raw_lower and "<summary>来源</summary>" not in raw_lower:
        issues.append("HTML missing visible exhibit source drilldown")
    if "retained public sources" not in lower and "public-source collection" not in lower and "公开来源" not in lower:
        issues.append("HTML missing subtle public-source methodology text")
    consecutive_exhibits = find_consecutive_exhibit_pairs(html)
    metrics["consecutive_exhibit_pairs"] = len(consecutive_exhibits)
    if consecutive_exhibits:
        issues.append(
            "HTML has "
            f"{len(consecutive_exhibits)} consecutive exhibit pair(s) without interpretive prose: "
            + "; ".join(consecutive_exhibits)
        )
    for forbidden in ["How leaders should move next", "Management agenda", "Where to Start", "Source base", "Methodology and source boundary"]:
        if forbidden.lower() in lower:
            issues.append(f"HTML contains removed standalone module label: {forbidden}")
    if re.search(r"<h[1-6][^>]*>\s*Sources\s*</h[1-6]>", html, re.I):
        issues.append("HTML contains standalone Sources heading")

    return emit(issues, metrics, args.warn_only)


def find_consecutive_exhibit_pairs(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article", class_="article-main")
    if not article:
        return []

    pairs: List[str] = []
    previous_exhibit_label = ""
    previous_was_exhibit = False
    for child in article.find_all(recursive=False):
        classes = child.get("class") or []
        is_exhibit = child.name == "section" and "exhibit" in classes
        if is_exhibit:
            label = _child_text(child, ".exhibit-kicker") or "unlabeled exhibit"
            if previous_was_exhibit:
                pairs.append(f"{previous_exhibit_label} -> {label}")
            previous_exhibit_label = label
            previous_was_exhibit = True
            continue
        previous_was_exhibit = False
    return pairs


def _child_text(node: Any, selector: str) -> str:
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def read_json(path: Path, issues: List[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"cannot parse {path.name}: {exc}")
        return {}


def strip_tags(html: str) -> str:
    text_value = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text_value = re.sub(r"<style[\s\S]*?</style>", " ", text_value, flags=re.I)
    text_value = re.sub(r"<[^>]+>", " ", text_value)
    return re.sub(r"\s+", " ", text_value).strip()


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "estimate", "estimated", "est"}


def takeaway_texts(value: Any) -> List[str]:
    if isinstance(value, dict):
        for key in ("items", "bullets", "points", "takeaways", "key_takeaways", "keyTakeaways", "findings"):
            if value.get(key):
                return takeaway_texts(value.get(key))
    return [text(x) for x in as_list(value) if text(x)]


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("items", "bullets", "points", "takeaways", "key_takeaways", "keyTakeaways", "findings"):
            if value.get(key):
                return text(value.get(key))
        for key in ("text", "title", "finding", "claim", "takeaway", "implication", "action", "description", "summary"):
            if value.get(key):
                return text(value.get(key))
        return ""
    if isinstance(value, list):
        return " ".join(text(x) for x in value if text(x))
    return re.sub(r"\s+", " ", str(value).strip())


def emit(issues: List[str], metrics: Dict[str, Any], warn_only: bool) -> int:
    print(json.dumps({"ok": not issues, "issues": issues, "metrics": metrics}, ensure_ascii=False, indent=2))
    if issues and not warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
