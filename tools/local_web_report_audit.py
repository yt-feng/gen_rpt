from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit HTML-first thought leadership report output.")
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    report_dir = args.report_dir
    html_path = report_dir / "index.html"
    payload_path = report_dir / "web_report_payload.json"
    fact_pack_path = report_dir / "research_fact_pack.json"
    sources_path = report_dir / "sources.json"

    issues: List[str] = []
    metrics: Dict[str, Any] = {"report_dir": str(report_dir)}

    for path in [html_path, payload_path, fact_pack_path, sources_path]:
        if not path.exists():
            issues.append(f"missing required file: {path.name}")

    if issues:
        return emit(issues, metrics, args.warn_only)

    payload = read_json(payload_path, issues)
    sources = read_json(sources_path, issues)
    fact_pack = read_json(fact_pack_path, issues)
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    html_text = strip_tags(html)

    sections = [x for x in as_list(payload.get("sections")) if isinstance(x, dict)]
    exhibits = [x for x in as_list(payload.get("exhibits")) if isinstance(x, dict)]
    takeaways = [text(x) for x in as_list(payload.get("key_takeaways")) if text(x)]
    action_steps = [x for x in as_list(payload.get("action_steps")) if isinstance(x, dict)]
    references = [x for x in as_list(payload.get("references")) if isinstance(x, dict)]

    metrics.update(
        {
            "sections": len(sections),
            "exhibits": len(exhibits),
            "takeaways": len(takeaways),
            "actions": len(action_steps),
            "references": len(references),
            "sources": len(sources if isinstance(sources, list) else []),
            "fact_pack_sources": fact_pack.get("source_count") if isinstance(fact_pack, dict) else None,
            "html_chars": len(html_text),
        }
    )

    if not (4 <= len(sections) <= 6):
        issues.append(f"expected 4-6 substantial sections, got {len(sections)}")
    if len(takeaways) != 3:
        issues.append(f"expected exactly 3 key takeaways, got {len(takeaways)}")
    if not (3 <= len(exhibits) <= 5):
        issues.append(f"expected 3-5 exhibits, got {len(exhibits)}")
    if len(action_steps) < 3:
        issues.append(f"expected at least 3 action steps, got {len(action_steps)}")
    if len(references) < 4:
        issues.append(f"expected at least 4 retained references, got {len(references)}")
    if len(html_text) < 12000:
        issues.append(f"HTML article appears too thin ({len(html_text)} text chars)")

    for idx, section in enumerate(sections, start=1):
        title = text(section.get("title"))
        lead = text(section.get("lead"))
        paragraphs = [text(x) for x in as_list(section.get("paragraphs")) if text(x)]
        evidence = [text(x) for x in as_list(section.get("evidence")) if text(x)]
        body = " ".join([lead] + paragraphs + evidence)
        if title.lower().strip(" .:-") in BAD_HEADINGS:
            issues.append(f"section {idx} uses generic label heading: {title}")
        if len(title) < 24:
            issues.append(f"section {idx} title is too thin: {title}")
        if len(paragraphs) < 5:
            issues.append(f"section {idx} has too few paragraphs: {len(paragraphs)}")
        if len(body) < 1400:
            issues.append(f"section {idx} lacks depth ({len(body)} chars): {title[:90]}")
        if not evidence:
            issues.append(f"section {idx} has no explicit evidence bullets: {title[:90]}")
        if not re.search(r"\b(19|20)\d{2}\b|\b\d+(?:\.\d+)?%|\b\$\d+|\b\d+(?:\.\d+)?\s*(?:billion|million|trillion|GW|MW|kg|years?|months?)\b", body, re.I):
            issues.append(f"section {idx} lacks dates or numeric evidence cues: {title[:90]}")

    exhibit_types = {text(x.get("type")).lower() for x in exhibits}
    metrics["exhibit_types"] = sorted(exhibit_types)
    if len(exhibit_types) < 2:
        issues.append("exhibit mix is too narrow")
    for idx, exhibit in enumerate(exhibits, start=1):
        title = text(exhibit.get("title"))
        source_note = text(exhibit.get("source_note") or exhibit.get("caption"))
        if len(title) < 24:
            issues.append(f"exhibit {idx} title is too thin: {title}")
        if not source_note:
            issues.append(f"exhibit {idx} lacks caption/source note: {title}")

    lower = html_text.lower()
    for pattern in PROCESS_PATTERNS:
        if re.search(pattern, lower, re.I):
            issues.append(f"HTML leaks process language matching: {pattern}")
    for required in ["Key Takeaways", "Contents", "How leaders should move next", "Methodology", "Sources"]:
        if required.lower() not in lower:
            issues.append(f"HTML missing expected BCG-style module: {required}")

    return emit(issues, metrics, args.warn_only)


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


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "title", "finding", "action", "description", "summary"):
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
