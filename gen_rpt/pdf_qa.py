from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz

META_LABEL_PATTERNS = [
    "mckinsey-style",
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
]


def run_pdf_qa(pdf_path: Path, html_path: Path, qa_dir: Path, *, render_pages: int = 10) -> Dict[str, Any]:
    qa_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {
        "passed": True,
        "pdf_path": str(pdf_path),
        "html_path": str(html_path),
        "checks": [],
        "issues": [],
        "recommendations": [],
        "page_count": 0,
        "screenshots": [],
    }

    if not pdf_path.exists() or pdf_path.stat().st_size < 2048:
        _fail(result, "missing_or_tiny_pdf", "PDF is missing or suspiciously small.", "rerender_pdf")
        _write(result, qa_dir)
        return result

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        _fail(result, "pdf_open_error", f"Unable to open PDF: {exc}", "rerender_pdf")
        _write(result, qa_dir)
        return result

    result["page_count"] = doc.page_count
    if doc.page_count < 6:
        _fail(result, "short_report", "PDF has fewer than six pages; report may be too thin.", "expand_report_or_check_renderer")
    else:
        _pass(result, "page_count", f"PDF has {doc.page_count} pages.")

    full_text = ""
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        full_text += "\n" + page.get_text("text")
        for issue in _inspect_page_text_layout(page, page_index + 1):
            _fail(result, issue["code"], issue["message"], issue["recommendation"])

        if page_index < render_pages:
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            out = qa_dir / f"page_{page_index + 1:03d}.png"
            pix.save(str(out))
            result["screenshots"].append(str(out))

    if len(full_text.strip()) < 500:
        _fail(result, "low_text_extractability", "Extracted PDF text is very short; text may be rendered incorrectly.", "check_pdf_text")
    else:
        _pass(result, "text_extractability", "PDF text extraction looks healthy.")

    if "..." in full_text or "…" in full_text:
        _fail(result, "visible_truncation", "Visible ellipses found in PDF text; content may have been truncated.", "remove_text_truncation")

    lower = full_text.lower()
    for pattern in META_LABEL_PATTERNS:
        if pattern.lower() in lower:
            _fail(result, "meta_label_visible", f"Visible meta label found: {pattern}", "strip_meta_labels")

    result["passed"] = not result["issues"]
    _write(result, qa_dir)
    return result


def apply_pdf_qa_fixes(report: Dict[str, Any], qa_result: Dict[str, Any]) -> Dict[str, Any]:
    """Clean unsafe/meta text without truncating client-ready prose.

    Previous versions shortened content aggressively, which created visible ellipses
    in titles and paragraphs. The renderer now handles overflow by adding pages, so
    the QA fix should preserve the report substance.
    """
    fixed = deepcopy(report)
    fixed["_layout_profile"] = "client_ready"
    fixed["_qa_autofixed"] = True
    fixed["_qa_recommendations"] = qa_result.get("recommendations", [])

    fixed["report_title"] = _clean_text(fixed.get("report_title", ""))
    fixed["report_subtitle"] = _clean_text(fixed.get("report_subtitle", ""))
    fixed["executive_summary_text"] = _clean_text(fixed.get("executive_summary_text", ""))
    fixed["methodology_note"] = _clean_text(fixed.get("methodology_note", ""))
    fixed["executive_summary"] = [_clean_text(x) for x in fixed.get("executive_summary", [])]
    for key in ["key_findings", "action_plan", "risk_register", "scenario_vignettes", "author_credentials"]:
        fixed[key] = _clean_nested(fixed.get(key, []))

    for step in fixed.get("method_steps", []):
        step["name"] = _clean_text(step.get("name", ""))
        step["description"] = _clean_text(step.get("description", ""))

    for section in fixed.get("sections", []):
        section["title"] = _clean_text(section.get("title", ""))
        section["lead"] = _clean_text(section.get("lead", ""))
        section["paragraphs"] = [_clean_text(p) for p in section.get("paragraphs", [])]
        section["key_takeaways"] = [_clean_text(x) for x in section.get("key_takeaways", [])]

    for card in fixed.get("insight_cards", []):
        card["title"] = _clean_text(card.get("title", ""))
        card["subtitle"] = _clean_text(card.get("subtitle", ""))
        card["bullets"] = [_clean_text(x) for x in card.get("bullets", [])]
        card["highlight_label"] = _clean_text(card.get("highlight_label", ""))
        card["exhibit_label"] = _clean_text(card.get("exhibit_label", ""))

    for chart in fixed.get("charts", []):
        chart["title"] = _clean_text(chart.get("title", ""))
        chart["subtitle"] = _clean_text(chart.get("subtitle", ""))
        chart["categories"] = [_clean_text(str(x)) for x in chart.get("categories", [])]
        chart["caption"] = _clean_text(chart.get("caption", ""))
        chart["source_note"] = _clean_text(chart.get("source_note", ""))
        for item in chart.get("series", []):
            item["name"] = _clean_text(item.get("name", ""))
    return fixed


def _inspect_page_text_layout(page: fitz.Page, page_no: int) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    blocks = []
    for block in page.get_text("blocks"):
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        text = str(text).strip()
        if len(text) < 4:
            continue
        blocks.append((x0, y0, x1, y1, text))

    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a = blocks[i]
            b = blocks[j]
            overlap = _overlap_ratio(a[:4], b[:4])
            if overlap > 0.16 and not _same_line_artifact(a, b):
                issues.append({
                    "code": "text_block_overlap",
                    "message": f"Page {page_no}: possible text overlap between '{a[4][:28]}' and '{b[4][:28]}'.",
                    "recommendation": "increase_page_count_or_adjust_layout",
                })
                return issues

    text_dict = page.get_text("dict")
    sizes = []
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                size = float(span.get("size", 0))
                if txt and size:
                    sizes.append(size)
    if sizes:
        if min(sizes) < 4.2:
            issues.append({"code": "font_too_small", "message": f"Page {page_no}: text below 4.2pt detected.", "recommendation": "increase_min_font_or_add_pages"})
        if max(sizes) > 56:
            issues.append({"code": "font_too_large", "message": f"Page {page_no}: unusually large text detected.", "recommendation": "reduce_cover_or_title_font"})
    return issues


def _clean_text(value: Any) -> str:
    text = str(value or "")
    for pattern in META_LABEL_PATTERNS:
        text = re.sub(re.escape(pattern), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _clean_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_nested(item) for item in value]
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _overlap_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def _same_line_artifact(a: Tuple, b: Tuple) -> bool:
    return abs(a[1] - b[1]) < 2.0 and abs(a[3] - b[3]) < 2.0


def _fail(result: Dict[str, Any], code: str, message: str, recommendation: str) -> None:
    result["issues"].append({"code": code, "message": message, "recommendation": recommendation})
    if recommendation not in result["recommendations"]:
        result["recommendations"].append(recommendation)


def _pass(result: Dict[str, Any], code: str, message: str) -> None:
    result["checks"].append({"code": code, "message": message})


def _write(result: Dict[str, Any], qa_dir: Path) -> None:
    (qa_dir / "pdf_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
