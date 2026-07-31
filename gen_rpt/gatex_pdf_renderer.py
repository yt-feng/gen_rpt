from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import fitz
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas


PDF_MIME_TYPE = "application/pdf"
PDF_SCHEMA = "gatex-pdf-release/v1"
MAX_PDF_BYTES = 50 * 1024 * 1024


class GatexPdfError(RuntimeError):
    pass


def release_pdf_filename(payload: Mapping[str, Any]) -> str:
    content_key = str(payload.get("contentKey") or payload.get("outputSlug") or payload.get("title") or "report")
    content_key = re.sub(r"^generated-", "", content_key, flags=re.IGNORECASE)
    # Keep this limit aligned with GateX's worker-side slugify() so the
    # renderer and the verified upload endpoint always agree on the name.
    slug = re.sub(r"[^a-z0-9]+", "-", content_key.lower()).strip("-")[:64] or "report"
    language = "zh" if str(payload.get("language") or "").lower().startswith("zh") else "en"
    try:
        version = max(1, min(999, int(payload.get("versionNo") or 1)))
    except (TypeError, ValueError):
        version = 1
    return f"gatex-{slug}-{language}-v{version:02d}.pdf"


def release_classification(payload: Mapping[str, Any]) -> str:
    explicit = _clean(payload.get("classification"), 120)
    if explicit:
        return explicit.upper()
    scope = str(payload.get("accessScope") or "member").strip().lower()
    return {
        "staff": "GATEX RESTRICTED",
        "advanced": "PRIVATE OFFICE CONFIDENTIAL",
        "public": "GATEX MEMBER EDITION",
    }.get(scope, "MEMBER CONFIDENTIAL")


def render_gatex_release_pdf(
    payload: Mapping[str, Any],
    output_dir: Path,
    *,
    output_name: str | None = None,
    browser_executable: str | None = None,
) -> Dict[str, Any]:
    report = _validated_payload(payload)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = output_name or release_pdf_filename(report)
    if Path(file_name).name != file_name or not file_name.lower().endswith(".pdf"):
        raise GatexPdfError("PDF output_name must be a plain .pdf file name.")
    pdf_path = output_dir / file_name

    with tempfile.TemporaryDirectory(prefix="gatex-pdf-", dir=output_dir) as work_name:
        work_dir = Path(work_name)
        cover_html = work_dir / "cover.html"
        body_html = work_dir / "body.html"
        cover_pdf = work_dir / "cover.pdf"
        body_pdf = work_dir / "body.pdf"
        cover_html.write_text(_cover_html(report), encoding="utf-8")
        body_html.write_text(_body_html(report), encoding="utf-8")
        _render_html(cover_html, cover_pdf, browser_executable=browser_executable)
        _render_html(body_html, body_pdf, browser_executable=browser_executable)
        _assemble_pdf(cover_pdf, body_pdf, pdf_path, report)

    qa = validate_gatex_pdf(pdf_path, expected_title=str(report["title"]))
    byte_size = pdf_path.stat().st_size
    if byte_size > MAX_PDF_BYTES:
        pdf_path.unlink(missing_ok=True)
        raise GatexPdfError("Rendered PDF exceeds the 50 MB GateX delivery limit.")
    return {
        "schema": PDF_SCHEMA,
        "path": str(pdf_path),
        "fileName": file_name,
        "contentType": PDF_MIME_TYPE,
        "byteSize": byte_size,
        "sha256": _sha256(pdf_path),
        "pageCount": qa["pageCount"],
        "classification": release_classification(report),
        "title": report["title"],
        "versionId": report.get("versionId"),
        "versionNo": report.get("versionNo"),
        "qa": qa,
    }


def validate_gatex_pdf(pdf_path: Path, *, expected_title: str = "") -> Dict[str, Any]:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists() or pdf_path.stat().st_size < 8_000:
        raise GatexPdfError("Rendered PDF is missing or unexpectedly small.")
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:
        raise GatexPdfError(f"Rendered PDF cannot be opened: {exc}") from exc
    try:
        if document.page_count < 2:
            raise GatexPdfError("GateX release PDF must contain a cover and at least one body page.")
        page_text = [document.load_page(index).get_text("text").strip() for index in range(document.page_count)]
        if "GATEX" not in page_text[0].upper():
            raise GatexPdfError("GateX cover mark is missing from the rendered PDF.")
        if expected_title and _comparison_key(expected_title) not in _comparison_key(" ".join(page_text[:2])):
            raise GatexPdfError("The PDF cover does not contain the approved report title.")
        sparse_pages = [index + 1 for index, value in enumerate(page_text[1:], start=1) if len(value) < 20]
        if sparse_pages:
            raise GatexPdfError(f"Rendered PDF contains empty body pages: {sparse_pages}")
        for index in range(document.page_count):
            rect = document.load_page(index).rect
            ratio = rect.width / max(rect.height, 1)
            if not 0.69 <= ratio <= 0.72:
                raise GatexPdfError(f"Page {index + 1} is not A4 portrait.")
        return {
            "passed": True,
            "pageCount": document.page_count,
            "textCharacters": sum(len(value) for value in page_text),
            "a4Portrait": True,
        }
    finally:
        document.close()


def _validated_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise GatexPdfError("GateX release payload must be an object.")
    title = _clean(payload.get("title"), 300)
    if not title:
        raise GatexPdfError("GateX release payload requires an approved title.")
    raw_sections = payload.get("contentSections")
    if not isinstance(raw_sections, Sequence) or isinstance(raw_sections, (str, bytes)):
        raise GatexPdfError("GateX release payload requires approved contentSections.")
    sections = [_normalized_section(value, index) for index, value in enumerate(raw_sections[:40])]
    sections = [section for section in sections if section]
    if not sections:
        raise GatexPdfError("GateX release payload has no approved report content.")
    report = dict(payload)
    report.update(
        {
            "schema": PDF_SCHEMA,
            "title": title,
            "subtitle": _clean(payload.get("subtitle"), 1_000),
            "summary": _clean(payload.get("summary"), 8_000),
            "reportType": _clean(payload.get("reportType"), 160) or "GateX Decision Intelligence",
            "language": "zh" if str(payload.get("language") or "").lower().startswith("zh") else "en",
            "contentSections": sections,
        }
    )
    return report


def _normalized_section(value: Any, index: int) -> Dict[str, Any]:
    item = value if isinstance(value, Mapping) else {}
    kind = _clean(item.get("kind"), 40).lower() or "section"
    heading = _clean(item.get("heading") or item.get("title"), 300) or f"Section {index + 1}"
    paragraphs = [_clean(entry, 12_000) for entry in _list(item.get("paragraphs"))[:24]]
    evidence = [_clean(entry, 4_000) for entry in _list(item.get("evidence"))[:20]]
    normalized: Dict[str, Any] = {
        "id": _clean(item.get("id"), 120) or f"section-{index + 1}",
        "kind": kind,
        "heading": heading,
        "body": _clean(item.get("body"), 20_000),
        "lead": _clean(item.get("lead"), 4_000),
        "paragraphs": [entry for entry in paragraphs if entry],
        "evidence": [entry for entry in evidence if entry],
        "so_what": _clean(item.get("so_what") or item.get("soWhat"), 5_000),
        "items": list(item.get("items") or [])[:20] if isinstance(item.get("items"), list) else [],
        "exhibit": dict(item.get("exhibit") or {}) if isinstance(item.get("exhibit"), Mapping) else {},
    }
    return normalized


def _cover_html(report: Mapping[str, Any]) -> str:
    title = _e(report["title"])
    subtitle = _e(report.get("subtitle") or report.get("summary") or "Private decision intelligence")
    report_type = _e(report.get("reportType") or "GateX Decision Intelligence")
    classification = _e(release_classification(report))
    language = "ZH" if report.get("language") == "zh" else "EN"
    version = int(report.get("versionNo") or 1)
    issued = _display_date(report.get("versionSubmittedAt") or report.get("releaseDate"))
    title_size = 42 if len(str(report["title"])) < 74 else 35 if len(str(report["title"])) < 130 else 30
    return f"""<!doctype html>
<html lang="{report.get('language', 'en')}">
<head><meta charset="utf-8"><style>
@page {{ size: A4 portrait; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ width: 210mm; height: 297mm; margin: 0; }}
body {{
  position: relative; overflow: hidden; color: #f7fbff;
  font-family: "Noto Sans CJK SC", "Noto Sans CJK TC", "Source Han Sans SC", Arial, "Helvetica Neue", sans-serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
  background:
    radial-gradient(circle at 83% 11%, rgba(65,154,255,.32), transparent 27%),
    radial-gradient(circle at 12% 88%, rgba(28,102,222,.24), transparent 31%),
    linear-gradient(145deg, #02091d 0%, #041d50 47%, #071438 100%);
}}
.grid {{ position: absolute; inset: 0; opacity: .18; background-image: linear-gradient(rgba(126,183,255,.16) 1px, transparent 1px), linear-gradient(90deg, rgba(126,183,255,.16) 1px, transparent 1px); background-size: 14mm 14mm; }}
.ring, .ring:before, .ring:after {{ position: absolute; border: .35mm solid rgba(153,203,255,.17); border-radius: 50%; content: ""; }}
.ring {{ width: 120mm; height: 120mm; right: 0; top: 0; }}
.ring:before {{ width: 90mm; height: 90mm; left: 14mm; top: 14mm; }}
.ring:after {{ width: 60mm; height: 60mm; left: 29mm; top: 29mm; }}
.frame {{ position: absolute; inset: 13mm; border: .3mm solid rgba(188,219,255,.2); }}
.rail {{ position: absolute; left: 13mm; top: 13mm; bottom: 13mm; width: 2.2mm; background: linear-gradient(#46a8ff, #176ce0 55%, rgba(23,108,224,.15)); }}
.content {{ position: relative; z-index: 2; height: 100%; padding: 20mm 23mm 18mm 28mm; display: flex; flex-direction: column; }}
.mast {{ display: flex; justify-content: space-between; align-items: flex-start; }}
.brand {{ font-size: 22pt; line-height: 1; font-weight: 800; letter-spacing: .16em; }}
.brand small {{ display: block; margin-top: 3.2mm; color: #7fc2ff; font-size: 6.5pt; font-weight: 700; letter-spacing: .26em; }}
.class {{ padding: 2.4mm 3.4mm; color: #cbe5ff; border: .3mm solid rgba(147,202,255,.36); border-radius: 20mm; font-size: 6.5pt; font-weight: 700; letter-spacing: .14em; }}
.copy {{ margin-top: 56mm; width: 156mm; }}
.kicker {{ margin: 0 0 7mm; color: #70baff; font-size: 8pt; font-weight: 800; letter-spacing: .22em; text-transform: uppercase; }}
h1 {{ margin: 0; max-width: 156mm; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: {title_size}pt; font-weight: 400; line-height: 1.04; letter-spacing: -.025em; }}
.rule {{ width: 36mm; height: 1.1mm; margin: 9mm 0 7mm; background: #4daeff; }}
.subtitle {{ max-width: 130mm; margin: 0; color: rgba(230,241,255,.76); font-size: 12pt; line-height: 1.45; }}
.foot {{ display: grid; margin-top: auto; padding-top: 8mm; grid-template-columns: 1fr 1fr 1fr; border-top: .3mm solid rgba(194,222,255,.25); color: rgba(222,237,255,.68); font-size: 7pt; letter-spacing: .08em; }}
.foot span:nth-child(2) {{ text-align: center; }} .foot span:last-child {{ text-align: right; }}
</style></head>
<body><div class="grid"></div><div class="ring"></div><div class="frame"></div><div class="rail"></div>
<main class="content">
  <div class="mast"><div class="brand">GATEX<small>PRIVATE DECISION INTELLIGENCE</small></div><div class="class">{classification}</div></div>
  <section class="copy"><p class="kicker">{report_type}</p><h1>{title}</h1><div class="rule"></div><p class="subtitle">{subtitle}</p></section>
  <footer class="foot"><span>EDITION {issued}</span><span>{language} / VERSION {version:02d}</span><span>GATEX.FUND</span></footer>
</main></body></html>"""


def _body_html(report: Mapping[str, Any]) -> str:
    sections = list(report.get("contentSections") or [])
    takeaways = next((item for item in sections if item.get("kind") == "key_takeaways"), None)
    introduction = next((item for item in sections if item.get("kind") == "intro"), None)
    substantive = [
        item for item in sections
        if item.get("kind") not in {"key_takeaways", "intro", "methodology", "disclaimer"}
    ]
    notes = [item for item in sections if item.get("kind") in {"methodology", "disclaimer"}]
    parts = [
        "<!doctype html>",
        f"<html lang='{_e(report.get('language') or 'en')}'><head><meta charset='utf-8'><style>{_body_css()}</style></head><body>",
        "<section class='opening'>",
        "<div class='eyebrow'>PRIVATE DECISION BRIEF</div>",
        f"<h1>{_e(report['title'])}</h1>",
        f"<p class='opening-summary'>{_e(report.get('summary') or report.get('subtitle') or '')}</p>",
    ]
    intro_text = _section_text(introduction) if introduction else ""
    if intro_text:
        parts.append(f"<p class='opening-intro'>{_e(intro_text)}</p>")
    takeaway_items = _action_items(takeaways.get("items") if takeaways else [])
    if takeaway_items:
        parts.append("<div class='takeaway-grid'>")
        for index, item in enumerate(takeaway_items[:5], start=1):
            parts.append(
                f"<article><span>{index:02d}</span><p>{_e(item['action'])}</p></article>"
            )
        parts.append("</div>")
    parts.append("</section>")

    if substantive:
        parts.extend(["<section class='contents page'>", "<div class='eyebrow'>REPORT MAP</div>", "<h2>Decision sequence</h2>", "<ol>"])
        for item in substantive:
            parts.append(f"<li><span>{_e(_kind_label(item.get('kind')))}</span><strong>{_e(item.get('heading'))}</strong></li>")
        parts.extend(["</ol>", "</section>"])

    exhibit_number = 0
    section_number = 0
    for section in substantive:
        kind = str(section.get("kind") or "section")
        if kind == "exhibit":
            exhibit_number += 1
            parts.append(_render_exhibit(section, exhibit_number))
        elif kind == "actions":
            parts.append(_render_actions(section))
        else:
            section_number += 1
            parts.append(_render_section(section, section_number))

    if notes:
        parts.append("<section class='notes page'><div class='eyebrow'>EDITORIAL NOTES</div><h2>Methodology and use</h2>")
        for note in notes:
            parts.append(f"<article><h3>{_e(note.get('heading'))}</h3><p>{_e(_section_text(note))}</p></article>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def _body_css() -> str:
    return """
@page { size: A4 portrait; margin: 21mm 17mm 20mm 17mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #18233a; background: #fff; font-family: "Noto Sans CJK SC", "Noto Sans CJK TC", "Source Han Sans SC", Arial, "Helvetica Neue", sans-serif; font-size: 9.5pt; line-height: 1.52; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3, p { margin-top: 0; }
h1, h2, h3 { break-after: avoid-page; }
.page, .analysis, .exhibit, .actions, .notes { break-before: page; }
.opening { min-height: 244mm; display: flex; flex-direction: column; }
.eyebrow { margin-bottom: 7mm; color: #176ddc; font-size: 7pt; font-weight: 800; letter-spacing: .2em; }
.opening h1 { max-width: 162mm; margin: 0 0 7mm; color: #061b46; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 28pt; font-weight: 400; line-height: 1.08; letter-spacing: -.02em; }
.opening-summary { max-width: 150mm; margin-bottom: 8mm; color: #3b4c68; font-size: 13pt; line-height: 1.44; }
.opening-intro { max-width: 154mm; padding: 5mm 0 5mm 6mm; border-left: 1.2mm solid #2d91f2; color: #35435a; font-size: 10.5pt; }
.takeaway-grid { display: grid; margin-top: auto; gap: 4mm; grid-template-columns: 1fr; }
.takeaway-grid article { display: grid; gap: 5mm; padding: 4.5mm 0; grid-template-columns: 12mm 1fr; border-top: .35mm solid #bdd2ed; break-inside: avoid; }
.takeaway-grid span { color: #1b75de; font-size: 8pt; font-weight: 800; letter-spacing: .12em; }
.takeaway-grid p { margin: 0; color: #172643; font-size: 11pt; line-height: 1.45; }
.contents h2, .notes h2 { margin: 0 0 11mm; color: #061b46; font-family: Georgia, "Times New Roman", serif; font-size: 25pt; font-weight: 400; }
.contents ol { margin: 0; padding: 0; list-style: none; border-top: 1.2mm solid #176ddc; }
.contents li { display: grid; gap: 5mm; padding: 4mm 0; grid-template-columns: 35mm 1fr; border-bottom: .3mm solid #d5e0ed; break-inside: avoid; }
.contents li span { color: #59708f; font-size: 7pt; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.contents li strong { color: #102343; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 11pt; font-weight: 400; line-height: 1.35; }
.section-head { margin-bottom: 8mm; padding-top: 4mm; border-top: 1.2mm solid #176ddc; }
.section-no { color: #1c73da; font-size: 8pt; font-weight: 800; letter-spacing: .14em; }
.analysis h2, .exhibit h2, .actions h2 { max-width: 158mm; margin: 3mm 0 4mm; color: #061b46; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 23pt; font-weight: 400; line-height: 1.12; }
.lead { margin: 0; color: #37618e; font-size: 12pt; line-height: 1.45; }
.analysis > p { margin-bottom: 4.3mm; text-align: left; orphans: 3; widows: 3; }
.evidence { margin: 7mm 0; padding: 5mm 6mm; background: #f2f7fd; border-top: 1mm solid #72b8ff; break-inside: avoid-page; }
.evidence strong { display: block; margin-bottom: 3mm; color: #0b3f83; font-size: 7pt; letter-spacing: .14em; text-transform: uppercase; }
.evidence ul { margin: 0; padding-left: 5mm; }
.evidence li { margin: 0 0 2.2mm; }
.so-what { margin-top: 7mm; padding: 5mm 6mm; color: #eff7ff; background: #082b63; break-inside: avoid-page; }
.so-what span { display: block; margin-bottom: 2mm; color: #75bdff; font-size: 7pt; font-weight: 800; letter-spacing: .15em; }
.so-what p { margin: 0; font-size: 10.5pt; line-height: 1.48; }
.exhibit { break-inside: auto; }
.exhibit-caption { color: #465a76; font-size: 10.5pt; }
.metrics { display: grid; gap: 4mm; margin: 8mm 0; grid-template-columns: repeat(3, 1fr); }
.metric { min-height: 32mm; padding: 5mm; background: #f1f6fc; border-top: 1.2mm solid #257de2; break-inside: avoid; }
.metric strong { display: block; margin-bottom: 3mm; color: #06265b; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 21pt; font-weight: 400; }
.metric span { color: #50657f; font-size: 8pt; }
.bars { margin: 7mm 0; padding: 5mm 0; border-top: .3mm solid #cbd8e8; border-bottom: .3mm solid #cbd8e8; }
.bar { display: grid; align-items: center; gap: 3mm; margin: 3mm 0; grid-template-columns: 35mm 1fr 19mm; break-inside: avoid; }
.bar label { color: #334965; font-size: 8pt; }
.track { height: 5mm; background: #e9f0f8; }
.track i { display: block; height: 100%; background: linear-gradient(90deg, #0a55ba, #3ba2f6); }
.bar b { color: #0b3f83; font-size: 8pt; text-align: right; }
.source-note { margin-top: 5mm; color: #697b91; font-size: 7.5pt; }
.actions-grid { display: grid; gap: 4mm; margin-top: 7mm; grid-template-columns: 1fr 1fr; }
.action { min-height: 42mm; padding: 5mm; background: #f3f7fc; border-top: 1mm solid #2d8fe9; break-inside: avoid; }
.action span { color: #1a6dd0; font-size: 7pt; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.action h3 { margin: 3mm 0 2mm; color: #10284d; font-size: 11pt; line-height: 1.35; }
.action p { margin: 0; color: #52657f; font-size: 8.5pt; }
.notes article { margin: 0 0 8mm; padding-top: 4mm; border-top: .3mm solid #cedbea; }
.notes h3 { margin-bottom: 3mm; color: #153665; font-size: 11pt; }
.notes p { color: #52657c; font-size: 8.5pt; }
"""


def _render_section(section: Mapping[str, Any], number: int) -> str:
    parts = [
        "<section class='analysis'>",
        "<header class='section-head'>",
        f"<span class='section-no'>SECTION {number:02d}</span>",
        f"<h2>{_e(section.get('heading'))}</h2>",
    ]
    if section.get("lead"):
        parts.append(f"<p class='lead'>{_e(section.get('lead'))}</p>")
    parts.append("</header>")
    if section.get("body"):
        parts.append(f"<p>{_e(section.get('body'))}</p>")
    for paragraph in section.get("paragraphs") or []:
        parts.append(f"<p>{_e(paragraph)}</p>")
    evidence = list(section.get("evidence") or [])
    if evidence:
        parts.append("<aside class='evidence'><strong>Evidence retained</strong><ul>")
        for entry in evidence:
            parts.append(f"<li>{_e(entry)}</li>")
        parts.append("</ul></aside>")
    if section.get("so_what"):
        parts.append(f"<aside class='so-what'><span>DECISION IMPLICATION</span><p>{_e(section.get('so_what'))}</p></aside>")
    parts.append("</section>")
    return "".join(parts)


def _render_exhibit(section: Mapping[str, Any], number: int) -> str:
    exhibit = section.get("exhibit") if isinstance(section.get("exhibit"), Mapping) else {}
    parts = [
        "<section class='exhibit'>",
        "<header class='section-head'>",
        f"<span class='section-no'>EXHIBIT {number:02d}</span>",
        f"<h2>{_e(section.get('heading'))}</h2>",
        "</header>",
    ]
    caption = _clean(exhibit.get("caption") or exhibit.get("subtitle") or section.get("body"), 6_000)
    if caption:
        parts.append(f"<p class='exhibit-caption'>{_e(caption)}</p>")
    metrics = exhibit.get("metrics") if isinstance(exhibit.get("metrics"), list) else []
    if metrics:
        parts.append("<div class='metrics'>")
        for raw in metrics[:6]:
            metric = raw if isinstance(raw, Mapping) else {}
            parts.append(f"<article class='metric'><strong>{_e(metric.get('value') or '-')}</strong><span>{_e(metric.get('label'))}</span></article>")
        parts.append("</div>")
    categories = [str(value) for value in _list(exhibit.get("categories"))[:12]]
    series = exhibit.get("series") if isinstance(exhibit.get("series"), list) else []
    first = series[0] if series and isinstance(series[0], Mapping) else {}
    values = [_number(value) for value in _list(first.get("values"))[: len(categories)]]
    if categories and values:
        maximum = max([abs(value) for value in values] + [1.0])
        parts.append("<div class='bars'>")
        for label, value in zip(categories, values):
            width = max(2.0, min(100.0, abs(value) / maximum * 100.0))
            parts.append(
                f"<div class='bar'><label>{_e(label)}</label><div class='track'><i style='width:{width:.2f}%'></i></div><b>{_e(_format_number(value))}</b></div>"
            )
        parts.append("</div>")
    source = _clean(exhibit.get("source_note") or exhibit.get("sourceNote"), 1_500)
    if source:
        parts.append(f"<p class='source-note'>Source: {_e(source)}</p>")
    parts.append("</section>")
    return "".join(parts)


def _render_actions(section: Mapping[str, Any]) -> str:
    parts = [
        "<section class='actions'>",
        "<header class='section-head'><span class='section-no'>MANAGEMENT AGENDA</span>",
        f"<h2>{_e(section.get('heading') or 'Action agenda')}</h2></header>",
        "<div class='actions-grid'>",
    ]
    for index, item in enumerate(_action_items(section.get("items")), start=1):
        parts.append(
            "<article class='action'>"
            f"<span>{_e(item.get('horizon') or f'Priority {index}')}</span>"
            f"<h3>{_e(item.get('action'))}</h3>"
            + (f"<p><strong>Decision gate:</strong> {_e(item.get('successMetric'))}</p>" if item.get("successMetric") else "")
            + "</article>"
        )
    parts.append("</div></section>")
    return "".join(parts)


def _render_html(html_path: Path, pdf_path: Path, *, browser_executable: str | None = None) -> None:
    try:
        with sync_playwright() as playwright:
            launch_args: Dict[str, Any] = {"headless": True, "args": ["--font-render-hinting=none"]}
            if browser_executable:
                launch_args["executable_path"] = browser_executable
            browser = playwright.chromium.launch(**launch_args)
            try:
                page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1)
                page.goto(html_path.as_uri(), wait_until="load", timeout=60_000)
                page.emulate_media(media="print")
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
    except Exception as exc:
        raise GatexPdfError(
            "Chromium PDF rendering failed. Install it with `python -m playwright install --with-deps chromium`. "
            f"Details: {exc}"
        ) from exc
    if not pdf_path.exists() or pdf_path.stat().st_size < 2_048:
        raise GatexPdfError("Chromium did not produce a usable PDF.")


def _assemble_pdf(cover_pdf: Path, body_pdf: Path, output_pdf: Path, report: Mapping[str, Any]) -> None:
    cover_reader = PdfReader(str(cover_pdf))
    body_reader = PdfReader(str(body_pdf))
    if len(cover_reader.pages) != 1:
        raise GatexPdfError("GateX cover must render as exactly one page.")
    if not body_reader.pages:
        raise GatexPdfError("GateX report body did not render any pages.")
    total_pages = 1 + len(body_reader.pages)
    writer = PdfWriter()
    writer.add_page(cover_reader.pages[0])
    for index, page in enumerate(body_reader.pages, start=2):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay = PdfReader(
            io.BytesIO(
                _page_furniture(
                    width,
                    height,
                    page_number=index,
                    total_pages=total_pages,
                    classification=release_classification(report),
                )
            )
        ).pages[0]
        page.merge_page(overlay)
        writer.add_page(page)
    writer.add_metadata(
        {
            "/Title": str(report.get("title") or "GateX Report"),
            "/Author": "GateX / Blue Ocean",
            "/Subject": str(report.get("reportType") or "Private decision intelligence"),
            "/Keywords": "GateX, Blue Ocean, private decision intelligence, board report",
            "/Creator": "GateX PDF Release Pipeline",
            "/Producer": "GateX / Chromium / pypdf",
        }
    )
    with output_pdf.open("wb") as stream:
        writer.write(stream)


def _page_furniture(
    width: float,
    height: float,
    *,
    page_number: int,
    total_pages: int,
    classification: str,
) -> bytes:
    buffer = io.BytesIO()
    surface = canvas.Canvas(buffer, pagesize=(width, height), pageCompression=1)
    navy = HexColor("#0A2A5E")
    muted = HexColor("#60728B")
    line = HexColor("#CFDCEB")
    blue = HexColor("#1B77DD")
    surface.setStrokeColor(line)
    surface.setLineWidth(0.55)
    surface.line(48, height - 38, width - 48, height - 38)
    surface.line(48, 39, width - 48, 39)
    surface.setFillColor(navy)
    surface.setFont("Helvetica-Bold", 7.2)
    surface.drawString(48, height - 29, "GATEX  |  PRIVATE OFFICE RESEARCH")
    surface.setFillColor(blue)
    surface.drawRightString(width - 48, height - 29, "BOARD EDITION")
    surface.setFillColor(muted)
    surface.setFont("Helvetica", 6.8)
    surface.drawString(48, 25, _ascii(classification)[:52])
    surface.drawCentredString(width / 2, 25, "GATEX.FUND")
    surface.drawRightString(width - 48, 25, f"{page_number:02d} / {total_pages:02d}")
    surface.save()
    return buffer.getvalue()


def _action_items(value: Any) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for raw in _list(value)[:20]:
        if isinstance(raw, Mapping):
            action = _clean(raw.get("action") or raw.get("description"), 4_000)
            horizon = _clean(raw.get("horizon"), 160)
            success = _clean(raw.get("success_metric") or raw.get("successMetric"), 1_000)
        else:
            action = _clean(raw, 4_000)
            horizon = ""
            success = ""
        if action:
            items.append({"action": action, "horizon": horizon, "successMetric": success})
    return items


def _section_text(section: Mapping[str, Any] | None) -> str:
    if not section:
        return ""
    values = [section.get("body"), section.get("lead"), *(section.get("paragraphs") or [])]
    return " ".join(_clean(value, 20_000) for value in values if _clean(value, 20_000))


def _kind_label(value: Any) -> str:
    return {
        "exhibit": "Evidence exhibit",
        "actions": "Management agenda",
        "section": "Analysis",
    }.get(str(value or "section"), "Analysis")


def _display_date(value: Any) -> str:
    raw = _clean(value, 40)
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    today = date.today()
    return f"{today.year:04d}.{today.month:02d}.{today.day:02d}"


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 0.00001:
        return f"{int(round(value)):,}"
    return f"{value:,.1f}"


def _clean(value: Any, maximum: int) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _ascii(value: Any) -> str:
    return str(value or "").encode("ascii", "ignore").decode("ascii")


def _comparison_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_checksum(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GatexPdfError",
    "PDF_MIME_TYPE",
    "PDF_SCHEMA",
    "release_classification",
    "release_pdf_filename",
    "render_gatex_release_pdf",
    "stable_json_checksum",
    "validate_gatex_pdf",
]
