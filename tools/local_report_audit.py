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
    "source backup",
    "supporting sources",
    "fact pack",
    "fact-pack",
    "source pack",
    "next useful work",
    "evidence ledger",
    "validation gap",
    "validation gaps",
    "open diligence items",
    "test the chapter",
    "the report should",
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

GENERIC_CHART_TERMS = (
    "blueocean synthesis",
    "blueocean synthesis from public evidence",
    "blueocean public-source synthesis",
    "blueocean scenario synthesis",
    "blueocean risk screen",
    "blueocean qualitative assessment",
    "blueocean management screen",
    "blueocean option synthesis",
    "blueocean customer option synthesis",
    "blueocean capital staging view",
    "blueocean uncertainty-resolution model",
    "priority index",
    "readiness index",
    "management conviction",
    "evidence maturity",
    "capital exposure",
    "decision readiness",
    "diligence workload",
    "management attention",
    "commitment posture",
    "generated narrative text",
    "retrieval order",
    "chapter text",
    "fetched public",
    "search context",
    "page body could not be fully extracted",
    "lower-confidence public signal",
    "scenario choices should",
    "key risks should be ranked",
    "validation effort shifts",
    "use-case priorities separate",
)

PROCESS_PHRASES = (
    "decision readiness still depends",
    "the decision lens is",
    "a practical reading is",
    "retained signal",
    "retained source signal",
    "the sourced record",
    "search context:",
    "the page body could not be fully extracted",
    "lower-confidence public signal",
    "this chapter concludes",
    "this section concludes",
    "this chapter shows",
    "this section finds",
)

OFF_TOPIC_CONTAMINATION = (
    "\u987a\u5cf0\u5b9d\u5b9d",
    "\u987a\u5cf0",
    "\u5b9d\u5b9d",
    "\u7f8e\u5bb9\u9662",
    "\u4fdd\u5065\u54c1",
    "\u7ade\u54c1\u5f88\u5389\u5bb3",
    "\u6700\u9002\u5408\u5e2e\u4ed6",
    "shun" "feng",
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
    duplicate_chart_titles = duplicates(chart_title_keys(charts))
    if duplicate_chart_titles:
        issues.append(f"duplicate chart titles survived: {len(duplicate_chart_titles)}")
    for idx, chart in enumerate(charts, start=1):
        chart_type = str(chart.get("type") or "").lower()
        if chart_type in {"pie", "donut"}:
            issues.append(f"chart {idx} uses forbidden type {chart_type}: {chart.get('title')}")
        if chart_type in {"bar", "stacked_bar", "line"} and weak_series_chart(chart):
            issues.append(f"chart {idx} is weak/single-bar/single-point: {chart.get('title')}")
        if chart_type == "bubble" and len(as_list(chart.get("points"))) < 3:
            issues.append(f"chart {idx} has too few bubble points: {chart.get('title')}")
        chart_reader = " ".join(reader_text(chart.get(key)) for key in ("title", "subtitle", "caption", "source_note")).lower()
        if any(marker in chart_reader for marker in GENERIC_CAPTIONS):
            issues.append(f"chart {idx} has generic caption/source wording: {chart.get('title')}")
        leaked_chart_terms = [marker for marker in GENERIC_CHART_TERMS if marker in chart_reader]
        if leaked_chart_terms:
            issues.append(f"chart {idx} has generic management-screen terms: {', '.join(leaked_chart_terms[:4])}: {chart.get('title')}")

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
        chapter_blocks = re.findall(r"\\label\{chap:(\d+)\}(.*?)\\label\{chap:\1:end\}", tex_text, flags=re.S)
        chapter_paragraph_counts = [len(re.findall(r"\{\\small\s+", block)) for _idx, block in chapter_blocks]
        metrics["tex_chapter_paragraphs"] = chapter_paragraph_counts
        if chapter_paragraph_counts and min(chapter_paragraph_counts) < args.min_section_paragraphs:
            issues.append(
                "LaTeX output thins at least one chapter below the paragraph target: "
                f"min {min(chapter_paragraph_counts)} < {args.min_section_paragraphs}"
            )
    if tex_path.exists() and metrics["tex_exhibits"] < len(charts):
        issues.append(f"TeX exhibit count is below chart count ({metrics['tex_exhibits']} < {len(charts)})")
    if len(charts) >= args.min_charts and metrics.get("tex_single_exhibit_pages", 0) > max(1, metrics.get("tex_exhibit_pages", 0) // 3):
        issues.append(
            f"TeX exhibit pages are too sparse: {metrics.get('tex_single_exhibit_pages')} single-exhibit pages across "
            f"{metrics.get('tex_exhibit_pages')} exhibit pages"
        )
    image_stats = section_image_metrics(report_dir)
    if image_stats:
        metrics["section_images"] = image_stats
        if image_stats.get("count", 0) >= 4 and image_stats.get("avg_stddev", 0) < 12:
            issues.append(f"section images are too flat/abstract; avg RGB stddev {image_stats.get('avg_stddev')}")
        if image_stats.get("count", 0) >= 6 and image_stats.get("unique_hashes", 0) < image_stats.get("count", 0) - 2:
            issues.append(
                f"section images are not diverse enough; {image_stats.get('unique_hashes')} unique hashes across "
                f"{image_stats.get('count')} images"
            )
        if image_stats.get("near_duplicate_pairs", 0) >= max(2, image_stats.get("count", 0) // 2):
            issues.append(f"section images are too repetitive; near-duplicate adjacent pairs {image_stats.get('near_duplicate_pairs')}")
    cover_stats = cover_image_metrics(report_dir)
    if cover_stats:
        metrics["cover_image"] = cover_stats
        if cover_stats.get("looks_document_like"):
            issues.append("cover image looks document-like or too pale for a benchmark-style cover")
    lower_rendered = rendered_text.lower()
    leaked_labels = [label for label in META_LABELS if label in lower_rendered]
    if leaked_labels:
        issues.append("rendered report leaks internal/process labels: " + ", ".join(sorted(set(leaked_labels))[:10]))
    leaked_phrases = [phrase for phrase in PROCESS_PHRASES if phrase in lower_rendered]
    if leaked_phrases:
        issues.append("rendered report leaks process prose: " + ", ".join(sorted(set(leaked_phrases))[:10]))
    off_topic_hits = [phrase for phrase in OFF_TOPIC_CONTAMINATION if phrase.lower() in lower_rendered]
    if off_topic_hits:
        issues.append("rendered report leaks off-topic repo/customer context: " + ", ".join(sorted(set(off_topic_hits))[:10]))
    rendered_template_terms = [
        phrase
        for phrase in (
            "readiness index",
            "priority index",
            "blueocean synthesis",
            "blueocean synthesis from public evidence",
            "fact pack",
            "fact-pack",
            "source pack",
            "generated narrative text",
            "retrieval order",
            "chapter text",
            "fetched public",
            "search context:",
            "the page body could not be fully extracted",
            "lower-confidence public signal",
        )
        if phrase in lower_rendered
    ]
    if rendered_template_terms:
        issues.append("rendered report leaks generic chart/source terms: " + ", ".join(sorted(set(rendered_template_terms))[:10]))
    rendered_without_urls = re.sub(r"https?://\S+", "", rendered_text, flags=re.I)
    bare_source_paths = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9.-]*(?:/[A-Za-z0-9._~%+-]+){2,}\b", rendered_without_urls)
    if bare_source_paths:
        issues.append("rendered report leaks bare source/path fragments: " + ", ".join(sorted(set(bare_source_paths))[:6]))
    process_hits = PROCESS_LANGUAGE_RE.findall(rendered_text)
    if process_hits:
        issues.append(f"rendered report leaks process language ({len(process_hits)} hits)")

    report_pdf = report_dir / "report_latex.pdf"
    if report_pdf.exists():
        metrics["report_pdf"] = pdf_metrics(report_pdf)
        visual_metrics = pdf_visual_metrics(report_pdf)
        if visual_metrics:
            metrics["report_pdf_visual"] = visual_metrics
            sparse_pages = visual_metrics.get("sparse_exhibit_pages", [])
            if sparse_pages:
                issues.append(
                    "PDF exhibit pages look visually sparse compared with the benchmark style: "
                    + ", ".join(f"p{page}" for page in sparse_pages[:8])
                )
            weak_cover_pages = visual_metrics.get("weak_full_bleed_pages", [])
            if weak_cover_pages:
                issues.append(
                    "PDF cover/back-cover pages are not rendering as full-bleed visual pages: "
                    + ", ".join(f"p{page}" for page in weak_cover_pages[:4])
                )
            sparse_text_pages = visual_metrics.get("sparse_text_pages", [])
            if sparse_text_pages:
                issues.append(
                    "PDF has sparse non-exhibit pages that look like layout overflow or thin back matter: "
                    + ", ".join(f"p{page}" for page in sparse_text_pages[:8])
                )

    if args.benchmark_pdf:
        metrics["benchmark_pdf"] = pdf_metrics(args.benchmark_pdf)
        if report_pdf.exists():
            benchmark = metrics["benchmark_pdf"]
            report = metrics["report_pdf"]
            if benchmark.get("available") and report.get("available"):
                min_median = int(benchmark.get("median_chars_per_page", 0) * 0.88)
                if min_median and report.get("median_chars_per_page", 0) < min_median:
                    issues.append(
                        "PDF text density is below benchmark threshold: "
                        f"{report.get('median_chars_per_page')} median chars/page < {min_median}"
                    )
                min_pages = int(benchmark.get("pages", 0) * 0.80)
                if min_pages and report.get("pages", 0) < min_pages:
                    issues.append(
                        "PDF page count is below benchmark rhythm threshold: "
                        f"{report.get('pages')} pages < {min_pages}"
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


def chart_title_keys(charts: Iterable[Dict[str, Any]]) -> List[str]:
    keys: List[str] = []
    for chart in charts:
        title = reader_text(chart.get("title"))
        if title:
            keys.append(re.sub(r"\W+", "", title.lower())[:140])
    return keys


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


def section_image_metrics(report_dir: Path) -> Dict[str, Any]:
    image_paths = sorted((report_dir / "assets").glob("image-*.png"))
    if not image_paths:
        return {}
    try:
        from PIL import Image, ImageStat  # type: ignore
    except Exception:
        return {"count": len(image_paths), "available": False, "error": "Pillow is not installed"}
    hashes: List[str] = []
    stddevs: List[float] = []
    for path in image_paths:
        try:
            image = Image.open(path).convert("RGB")
            stat = ImageStat.Stat(image)
            stddevs.append(round(sum(float(x) for x in stat.stddev) / 3, 2))
            gray = image.convert("L").resize((16, 16))
            pixels = list(gray.getdata())
            avg = sum(pixels) / max(1, len(pixels))
            hashes.append("".join("1" if value > avg else "0" for value in pixels))
        except Exception:
            continue
    near_duplicate_pairs = 0
    for left, right in zip(hashes, hashes[1:]):
        if hamming(left, right) <= 3:
            near_duplicate_pairs += 1
    return {
        "count": len(image_paths),
        "available": True,
        "avg_stddev": round(sum(stddevs) / len(stddevs), 2) if stddevs else 0,
        "min_stddev": min(stddevs) if stddevs else 0,
        "unique_hashes": len(set(hashes)),
        "near_duplicate_pairs": near_duplicate_pairs,
    }


def cover_image_metrics(report_dir: Path) -> Dict[str, Any]:
    path = report_dir / "assets" / "cover-ai.png"
    if not path.exists():
        return {}
    try:
        from PIL import Image, ImageStat  # type: ignore
    except Exception:
        return {"available": False, "error": "Pillow is not installed"}
    try:
        image = Image.open(path).convert("RGB")
        stat = ImageStat.Stat(image)
        mean = sum(float(x) for x in stat.mean) / 3
        stddev = sum(float(x) for x in stat.stddev) / 3
        gray = image.convert("L").resize((160, 112))
        pixels = list(gray.getdata())
        near_white = sum(1 for pixel in pixels if pixel >= 236) / max(1, len(pixels))
        looks_document_like = mean > 218 and stddev < 42 and near_white > 0.48
        return {
            "available": True,
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
            "near_white": round(near_white, 3),
            "looks_document_like": looks_document_like,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def hamming(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right)) + abs(len(left) - len(right))


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


def pdf_visual_metrics(path: Path) -> Dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception:
        return {}
    try:
        doc = fitz.open(str(path))
        page_metrics = []
        sparse_exhibit_pages: List[int] = []
        sparse_text_pages: List[int] = []
        weak_full_bleed_pages: List[int] = []
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text")
            text_chars = len(text.strip())
            pix = page.get_pixmap(matrix=fitz.Matrix(0.15, 0.15), alpha=False)
            samples = pix.samples
            pixel_count = max(1, pix.width * pix.height)
            nonwhite = 0
            color = 0
            for offset in range(0, len(samples), 3):
                red, green, blue = samples[offset], samples[offset + 1], samples[offset + 2]
                if min(red, green, blue) < 245:
                    nonwhite += 1
                if max(red, green, blue) - min(red, green, blue) > 28 and min(red, green, blue) < 245:
                    color += 1
            nonwhite_ratio = nonwhite / pixel_count
            color_ratio = color / pixel_count
            is_exhibit = bool(re.search(r"\bEXHIBIT\s+\d+", text, flags=re.I))
            page_metrics.append(
                {
                    "page": page_index,
                    "nonwhite": round(nonwhite_ratio, 3),
                    "color": round(color_ratio, 3),
                    "chars": text_chars,
                    "exhibit": is_exhibit,
                }
            )
            if is_exhibit and nonwhite_ratio < 0.14:
                sparse_exhibit_pages.append(page_index)
            if page_index in {1, len(doc)} and nonwhite_ratio < 0.22:
                weak_full_bleed_pages.append(page_index)
            elif not is_exhibit and (
                (text_chars < 420 and nonwhite_ratio < 0.12)
                or (text_chars < 900 and nonwhite_ratio < 0.075)
            ):
                sparse_text_pages.append(page_index)
        return {
            "available": True,
            "page_count": len(doc),
            "sparse_exhibit_pages": sparse_exhibit_pages,
            "sparse_text_pages": sparse_text_pages,
            "weak_full_bleed_pages": weak_full_bleed_pages,
            "pages": page_metrics,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


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
