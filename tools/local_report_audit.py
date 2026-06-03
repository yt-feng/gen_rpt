from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROCESS_LANGUAGE_RE = re.compile(
    r"\b(?:this|the)\s+(?:chapter|section)\s+(?:therefore\s+)?"
    r"(?:concludes|finds|shows|argues|frames|explains|demonstrates|sets\s+out|assesses|analyzes|translates|will|is\s+about)\b",
    re.I,
)

META_LABELS = (
    "future action agenda",
    "what to watch",
    "signals to watch",
    "decision implications",
    "management action plan",
    "risk register",
    "ceo decision scenario",
    "management implication:",
    "evidence:",
    "evidence boundary",
    "internal framework",
    "stress test",
    "mckinsey",
    "mck",
)

GENERIC_CAPTIONS = (
    "directional index used where public evidence supports relative comparison",
    "this exhibit is a directional management view",
    "actual percentages should be replaced",
    "weakly supported areas should remain",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast local quality audit for generated gen_rpt reports.")
    parser.add_argument("report_dir", type=Path, help="Report output directory containing report_payload.json and report_latex.tex.")
    parser.add_argument("--benchmark-pdf", type=Path, default=None, help="Optional benchmark PDF for page/text-density metrics.")
    parser.add_argument("--min-section-chars", type=int, default=1900)
    parser.add_argument("--min-section-paragraphs", type=int, default=6)
    parser.add_argument("--min-charts", type=int, default=14)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    report_dir = args.report_dir
    payload_path = report_dir / "report_payload.json"
    tex_path = report_dir / "report_latex.tex"
    html_path = report_dir / "report.html"
    md_path = report_dir / "report.md"

    issues: List[str] = []
    metrics: Dict[str, Any] = {"report_dir": str(report_dir)}

    if not payload_path.exists():
        issues.append(f"missing payload: {payload_path}")
        return emit(issues, metrics, args.warn_only)

    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"cannot read payload JSON: {exc}")
        return emit(issues, metrics, args.warn_only)

    sections = [x for x in as_list(payload.get("sections")) if isinstance(x, dict)]
    charts = [x for x in as_list(payload.get("charts")) if isinstance(x, dict)]
    metrics["sections"] = len(sections)
    metrics["charts"] = len(charts)
    metrics["chart_types"] = sorted({str(x.get("type") or "").lower() for x in charts})

    if not (7 <= len(sections) <= 10):
        issues.append(f"section count should be 7-10, got {len(sections)}")
    for idx, section in enumerate(sections, start=1):
        title = reader_text(section.get("title"))
        lead = reader_text(section.get("lead"))
        paragraphs = [reader_text(x) for x in as_list(section.get("paragraphs")) if reader_text(x)]
        section_chars = len(lead) + sum(len(x) for x in paragraphs)
        if len(paragraphs) < args.min_section_paragraphs:
            issues.append(f"section {idx} has only {len(paragraphs)} paragraphs: {title[:90]}")
        if section_chars < args.min_section_chars:
            issues.append(f"section {idx} is too thin ({section_chars} chars): {title[:90]}")
        for para in paragraphs:
            if PROCESS_LANGUAGE_RE.search(para):
                issues.append(f"section {idx} leaks process language: {para[:120]}")

    duplicate_keys = duplicates(long_paragraph_keys(sections))
    if duplicate_keys:
        issues.append(f"duplicate long paragraphs survived: {len(duplicate_keys)}")

    if len(charts) < args.min_charts:
        issues.append(f"chart count should be at least {args.min_charts}, got {len(charts)}")
    if len(set(metrics["chart_types"]) & {"stacked_bar", "line", "matrix", "bubble"}) < 2:
        issues.append("chart mix is too narrow; need several native exhibit types")
    for idx, chart in enumerate(charts, start=1):
        chart_type = str(chart.get("type") or "").lower()
        if chart_type in {"pie", "donut"}:
            issues.append(f"chart {idx} uses forbidden type {chart_type}: {chart.get('title')}")
        if chart_type in {"bar", "stacked_bar", "line"} and weak_series_chart(chart):
            issues.append(f"chart {idx} is weak/single-bar/single-point: {chart.get('title')}")
        if chart_type == "bubble" and len(as_list(chart.get("points"))) < 3:
            issues.append(f"chart {idx} has too few bubble points: {chart.get('title')}")
        chart_reader = " ".join(reader_text(chart.get(key)) for key in ("subtitle", "caption", "source_note")).lower()
        if any(marker in chart_reader for marker in GENERIC_CAPTIONS):
            issues.append(f"chart {idx} has generic caption/source wording: {chart.get('title')}")

    rendered_text = collect_rendered_text(tex_path, html_path, md_path)
    metrics["tex_exists"] = tex_path.exists()
    metrics["html_exists"] = html_path.exists()
    metrics["markdown_exists"] = md_path.exists()
    metrics["tex_exhibits"] = len(re.findall(r"\bEXHIBIT\s+\d+", rendered_text, flags=re.I))
    if tex_path.exists():
        tex_text = tex_path.read_text(encoding="utf-8", errors="ignore")
        exhibit_pages = [len(re.findall(r"\bEXHIBIT\s+\d+", page, flags=re.I)) for page in tex_text.split("\\clearpage")]
        metrics["tex_exhibit_pages"] = sum(1 for count in exhibit_pages if count > 0)
        metrics["tex_single_exhibit_pages"] = sum(1 for count in exhibit_pages if count == 1)
    if tex_path.exists() and metrics["tex_exhibits"] < len(charts):
        issues.append(f"TeX exhibit count is below chart count ({metrics['tex_exhibits']} < {len(charts)})")
    if len(charts) >= args.min_charts and metrics.get("tex_single_exhibit_pages", 0) > max(1, metrics.get("tex_exhibit_pages", 0) // 3):
        issues.append(
            f"TeX exhibit pages are too sparse: {metrics.get('tex_single_exhibit_pages')} single-exhibit pages across "
            f"{metrics.get('tex_exhibit_pages')} exhibit pages"
        )
    lower_rendered = rendered_text.lower()
    leaked_labels = [label for label in META_LABELS if label in lower_rendered]
    if leaked_labels:
        issues.append("rendered report leaks internal/process labels: " + ", ".join(sorted(set(leaked_labels))[:10]))
    process_hits = PROCESS_LANGUAGE_RE.findall(rendered_text)
    if process_hits:
        issues.append(f"rendered report leaks process language ({len(process_hits)} hits)")

    if args.benchmark_pdf:
        metrics["benchmark_pdf"] = pdf_metrics(args.benchmark_pdf)
        report_pdf = report_dir / "report_latex.pdf"
        if report_pdf.exists():
            metrics["report_pdf"] = pdf_metrics(report_pdf)
            benchmark = metrics["benchmark_pdf"]
            report = metrics["report_pdf"]
            if benchmark.get("available") and report.get("available"):
                min_median = int(benchmark.get("median_chars_per_page", 0) * 0.88)
                if min_median and report.get("median_chars_per_page", 0) < min_median:
                    issues.append(
                        "PDF text density is below benchmark threshold: "
                        f"{report.get('median_chars_per_page')} median chars/page < {min_median}"
                    )

    return emit(issues, metrics, args.warn_only)


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def reader_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(reader_text(x) for x in value.values())
    if isinstance(value, list):
        return " ".join(reader_text(x) for x in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def long_paragraph_keys(sections: Iterable[Dict[str, Any]]) -> List[str]:
    keys: List[str] = []
    for section in sections:
        for para in as_list(section.get("paragraphs")):
            text = reader_text(para)
            if len(text) >= 120:
                keys.append(re.sub(r"\W+", "", text.lower())[:220])
    return keys


def duplicates(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    dupes: List[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            dupes.append(value)
        seen.add(value)
    return dupes


def weak_series_chart(chart: Dict[str, Any]) -> bool:
    categories = [reader_text(x) for x in as_list(chart.get("categories") or chart.get("labels")) if reader_text(x)]
    if not categories and isinstance(chart.get("data"), dict):
        categories = [reader_text(x) for x in as_list(chart["data"].get("labels") or chart["data"].get("categories")) if reader_text(x)]
    series = series_list(chart.get("series")) or series_from_data(chart.get("data")) or series_from_top_level(chart)
    if len(categories) < 3 or not series:
        return True
    values_by_category = [0.0 for _ in categories]
    value_count = 0
    nonzero_count = 0
    for item in series:
        values = [to_number(x) for x in as_list(item.get("values"))]
        for idx, value in enumerate(values[: len(categories)]):
            value_count += 1
            values_by_category[idx] += abs(value)
            if abs(value) > 1e-6:
                nonzero_count += 1
    active_categories = sum(1 for value in values_by_category if abs(value) > 1e-6)
    return value_count < 3 or nonzero_count <= 1 or active_categories < 3


def series_list(value: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in as_list(value):
        if not isinstance(item, dict):
            continue
        values = [to_number(x) for x in as_list(item.get("values"))]
        if values:
            out.append({"values": values})
    return out


def series_from_data(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    datasets = [x for x in as_list(value.get("datasets") or value.get("series")) if isinstance(x, dict)]
    out: List[Dict[str, Any]] = []
    for dataset in datasets:
        raw = dataset.get("values")
        if raw is None:
            raw = dataset.get("data")
        values = [to_number(x.get("y", x.get("value", 0)) if isinstance(x, dict) else x) for x in as_list(raw)]
        if values:
            out.append({"values": values})
    return out


def series_from_top_level(chart: Dict[str, Any]) -> List[Dict[str, Any]]:
    return series_from_data({"datasets": chart.get("datasets")}) if isinstance(chart, dict) else []


def to_number(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").replace("$", "").replace(",", "").strip())
    except Exception:
        return 0.0


def collect_rendered_text(*paths: Path) -> str:
    parts: List[str] = []
    for path in paths:
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def pdf_metrics(path: Path) -> Dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception:
        return {"path": str(path), "available": False, "error": "PyMuPDF is not installed"}
    try:
        doc = fitz.open(str(path))
        page_lengths = [len(page.get_text("text")) for page in doc]
        return {
            "path": str(path),
            "available": True,
            "pages": len(doc),
            "median_chars_per_page": median(page_lengths),
            "total_chars": sum(page_lengths),
        }
    except Exception as exc:
        return {"path": str(path), "available": False, "error": str(exc)}


def median(values: List[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return int((ordered[middle - 1] + ordered[middle]) / 2)


def emit(issues: List[str], metrics: Dict[str, Any], warn_only: bool) -> int:
    result = {"ok": not issues, "issue_count": len(issues), "issues": issues[:80], "metrics": metrics}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if issues and not warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
