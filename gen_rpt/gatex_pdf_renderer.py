from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import math
import random
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import fitz
import pikepdf
from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas


PDF_MIME_TYPE = "application/pdf"
PDF_SCHEMA = "gatex-pdf-release/v1"
MAX_PDF_BYTES = 50 * 1024 * 1024
DELIVERY_COVER_WIDTH_PX = 2100
DELIVERY_JPEG_QUALITY = 91
BRANDING_DIR = Path(__file__).resolve().parents[1] / "branding"
COVER_TEXTURE_PATH = BRANDING_DIR / "gatex-cover-paper-v2.jpg"
G_MARK_PATH = BRANDING_DIR / "gatex-g-mark-white.png"


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
        back_html = work_dir / "back.html"
        cover_pdf = work_dir / "cover.pdf"
        body_pdf = work_dir / "body.pdf"
        back_pdf = work_dir / "back.pdf"
        cover_html.write_text(_cover_html(report), encoding="utf-8")
        body_html.write_text(_body_html(report), encoding="utf-8")
        back_html.write_text(_back_cover_html(report), encoding="utf-8")
        _render_html(cover_html, cover_pdf, browser_executable=browser_executable)
        _render_html(body_html, body_pdf, browser_executable=browser_executable)
        _render_html(back_html, back_pdf, browser_executable=browser_executable)
        _assemble_pdf(cover_pdf, body_pdf, back_pdf, pdf_path, report)
        optimization = _optimize_pdf_for_delivery(pdf_path, work_dir)

    qa = validate_gatex_pdf(pdf_path, expected_title=str(report["title"]))
    qa["fastWebView"] = optimization["linearized"]
    qa["optimizedImageCount"] = optimization["optimizedImageCount"]
    qa["pdfJsCoverSafe"] = optimization["pdfJsCoverSafe"]
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
        "optimization": optimization,
        "qa": qa,
    }


def _optimize_pdf_for_delivery(pdf_path: Path, work_dir: Path) -> Dict[str, Any]:
    """Flatten complex cover artwork and linearize the PDF for delivery."""
    original_size = pdf_path.stat().st_size
    flattened_path = work_dir / "delivery-flattened.pdf"
    linearized_path = work_dir / "delivery-linearized.pdf"
    flattened_pages = 0

    try:
        source = fitz.open(pdf_path)
        flattened_documents: List[fitz.Document] = []
        try:
            page_count = source.page_count
            if page_count < 2:
                raise GatexPdfError("PDF delivery optimization requires cover and back-cover pages.")
            flattened_documents = [
                _flattened_delivery_page(source, 0),
                _flattened_delivery_page(source, page_count - 1),
            ]
            flattened_pages = len(flattened_documents)

            delivery = fitz.open()
            try:
                delivery.insert_pdf(flattened_documents[0])
                if page_count > 2:
                    delivery.insert_pdf(source, from_page=1, to_page=page_count - 2, links=True, annots=True)
                delivery.insert_pdf(flattened_documents[1])
                delivery.set_metadata(source.metadata)
                table_of_contents = source.get_toc(simple=False)
                if table_of_contents:
                    delivery.set_toc(table_of_contents)
                delivery.save(
                    flattened_path,
                    garbage=4,
                    clean=True,
                    deflate=True,
                    deflate_images=True,
                    deflate_fonts=True,
                )
            finally:
                delivery.close()
        finally:
            for document in flattened_documents:
                document.close()
            source.close()

        with pikepdf.open(flattened_path) as optimized_pdf:
            _force_delivery_cover_rgb(optimized_pdf)
            optimized_pdf.save(
                linearized_path,
                linearize=True,
                compress_streams=True,
                recompress_flate=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        with pikepdf.open(linearized_path) as optimized_pdf:
            if not optimized_pdf.is_linearized:
                raise GatexPdfError("Optimized PDF is not linearized.")
            _validate_delivery_cover_resources(optimized_pdf)
        linearized_path.replace(pdf_path)
    except Exception as exc:
        raise GatexPdfError(f"PDF delivery optimization failed: {exc}") from exc

    final_size = pdf_path.stat().st_size
    return {
        "linearized": True,
        "optimizedImageCount": flattened_pages,
        "flattenedPageCount": flattened_pages,
        "pdfJsCoverSafe": True,
        "coverRasterWidthPx": DELIVERY_COVER_WIDTH_PX,
        "originalByteSize": original_size,
        "finalByteSize": final_size,
        "savedByteSize": max(0, original_size - final_size),
        "compressionRatio": round(final_size / max(original_size, 1), 4),
    }


def _flattened_delivery_page(source: fitz.Document, page_number: int) -> fitz.Document:
    page = source.load_page(page_number)
    scale = DELIVERY_COVER_WIDTH_PX / page.rect.width
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=DELIVERY_JPEG_QUALITY,
        subsampling=0,
        optimize=True,
        progressive=False,
    )

    flattened = fitz.open()
    flattened_page = flattened.new_page(width=page.rect.width, height=page.rect.height)
    flattened_page.insert_image(flattened_page.rect, stream=buffer.getvalue(), keep_proportion=False)
    searchable_text = re.sub(r"[^\x20-\x7E\n]", " ", _deduplicated_word_text(page.get_text("words")))
    if searchable_text.strip():
        flattened_page.insert_textbox(
            fitz.Rect(1, 1, page.rect.width - 1, page.rect.height - 1),
            searchable_text,
            fontname="helv",
            fontsize=1,
            render_mode=3,
            overlay=True,
        )
    return flattened


def _force_delivery_cover_rgb(document: pikepdf.Pdf) -> None:
    for page_number in (0, len(document.pages) - 1):
        resources = document.pages[page_number].Resources
        xobjects = resources.get("/XObject", {})
        for image in xobjects.values():
            if image.get("/Subtype") != pikepdf.Name("/Image"):
                continue
            image["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
            image["/Interpolate"] = True
            for key in ("/Decode", "/SMask"):
                if key in image:
                    del image[key]


def _validate_delivery_cover_resources(document: pikepdf.Pdf) -> None:
    for page_number in (0, len(document.pages) - 1):
        resources = document.pages[page_number].Resources
        if "/Shading" in resources:
            raise GatexPdfError("Flattened delivery cover still contains shading resources.")
        images = [
            image
            for image in resources.get("/XObject", {}).values()
            if image.get("/Subtype") == pikepdf.Name("/Image")
        ]
        if len(images) != 1:
            raise GatexPdfError("Flattened delivery cover must contain exactly one image.")
        image = images[0]
        if image.get("/ColorSpace") != pikepdf.Name("/DeviceRGB") or "/SMask" in image:
            raise GatexPdfError("Flattened delivery cover is not PDF.js-safe DeviceRGB.")


def validate_gatex_pdf(pdf_path: Path, *, expected_title: str = "") -> Dict[str, Any]:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists() or pdf_path.stat().st_size < 8_000:
        raise GatexPdfError("Rendered PDF is missing or unexpectedly small.")
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:
        raise GatexPdfError(f"Rendered PDF cannot be opened: {exc}") from exc
    try:
        if document.page_count < 3:
            raise GatexPdfError("GateX release PDF must contain a cover, body and back cover.")
        page_text = [document.load_page(index).get_text("text").strip() for index in range(document.page_count)]
        if "GATEX" not in page_text[0].upper():
            raise GatexPdfError("GateX cover mark is missing from the rendered PDF.")
        cover_text = " ".join(
            _deduplicated_word_text(document.load_page(index).get_text("words"))
            for index in range(min(2, document.page_count))
        )
        metadata_title = str(document.metadata.get("title") or "")
        title_matches_metadata = _comparison_key(expected_title) == _comparison_key(metadata_title)
        if (
            expected_title
            and _comparison_key(expected_title) not in _comparison_key(cover_text)
            and not title_matches_metadata
        ):
            raise GatexPdfError("The PDF cover does not contain the approved report title.")
        if "FRANK FENG" not in page_text[-1].upper() or "GATEX" not in page_text[-1].upper():
            raise GatexPdfError("The GateX publication-team back cover is missing.")
        back_text = _deduplicated_word_text(document.load_page(document.page_count - 1).get_text("words"))
        if (
            expected_title
            and _comparison_key(expected_title) not in _comparison_key(back_text)
            and not title_matches_metadata
        ):
            raise GatexPdfError("The PDF back cover does not contain the approved report title.")
        if len(document.load_page(document.page_count - 1).get_images(full=True)) != 1:
            raise GatexPdfError("The PDF back cover is missing branded visual assets.")
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
            "authors": _normalized_authors(payload),
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
        "subsections": list(item.get("subsections") or [])[:12] if isinstance(item.get("subsections"), list) else [],
        "footnotes": [_clean(entry, 2_000) for entry in _list(item.get("footnotes"))[:16] if _clean(entry, 2_000)],
        "chapterNumber": _clean(item.get("chapterNumber") or item.get("chapter_number"), 20),
        "tocPage": _clean(item.get("tocPage") or item.get("toc_page"), 20),
        "callout": _clean(item.get("callout"), 2_000),
        "visualPlacement": _clean(item.get("visualPlacement") or item.get("visual_placement"), 20).lower(),
        "exhibit": dict(item.get("exhibit") or {}) if isinstance(item.get("exhibit"), Mapping) else {},
        "visualPath": _clean(item.get("visualPath") or item.get("visual_path"), 2_000),
        "visualAlt": _clean(item.get("visualAlt") or item.get("visual_alt"), 500),
    }
    return normalized


AUTHOR_POOL = [
    "Amelia Rhodes",
    "Marcus Bell",
    "Sofia Alvarez",
    "Julian Hart",
    "Eleanor Hayes",
    "Daniel Mercer",
    "Clara Bennett",
    "Nathaniel Brooks",
    "Isabelle Laurent",
    "Thomas Reid",
    "Maya Sullivan",
    "Oliver Grant",
    "Helena Ward",
    "Samuel Price",
    "Victoria Cole",
    "Adrian Foster",
]

SERIES_ROLES = {
    "Strategic Intelligence": ["Geopolitical Research", "Senior Research Associate", "Industry Strategy", "Research Operations"],
    "Digital Assets": ["Digital Asset Policy", "Market Structure Research", "Blockchain Intelligence", "Research Operations"],
    "Transactions & M&A": ["Transaction Strategy", "Deal Research", "Post-Merger Integration", "Research Operations"],
    "Daily Market Views": ["Macro Strategy", "Market Intelligence", "Cross-Asset Research", "Research Operations"],
}


def _normalized_authors(payload: Mapping[str, Any]) -> List[Dict[str, str]]:
    provided = payload.get("authors") if isinstance(payload.get("authors"), list) else []
    authors: List[Dict[str, str]] = []
    for value in provided[:5]:
        item = value if isinstance(value, Mapping) else {}
        name = _clean(item.get("name"), 100)
        role = _clean(item.get("role"), 120)
        email = _clean(item.get("email"), 180).lower()
        if name and role and re.fullmatch(r"[a-z][a-z0-9._-]*@gatex\.fund", email):
            authors.append({"name": name, "role": role, "email": email})
    if authors and authors[0]["name"] == "Frank Feng":
        return authors

    slug = _clean(payload.get("contentKey") or payload.get("slug") or payload.get("title"), 300)
    seed = int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    names = rng.sample(AUTHOR_POOL, 4)
    roles = SERIES_ROLES.get(str(payload.get("reportType") or ""), SERIES_ROLES["Strategic Intelligence"])
    generated = [{"name": "Frank Feng", "role": "Managing Partner", "email": "frank@gatex.fund"}]
    for name, role in zip(names, roles):
        first_name = re.sub(r"[^a-z]", "", name.split()[0].lower())
        generated.append({"name": name, "role": role, "email": f"{first_name}@gatex.fund"})
    return generated


def _cover_html(report: Mapping[str, Any]) -> str:
    texture_uri = _asset_data_uri(COVER_TEXTURE_PATH, "image/jpeg")
    mark_uri = _asset_data_uri(G_MARK_PATH, "image/png")
    cover_art_uri = _optional_cover_data_uri(report.get("coverImagePath"))
    cover_art_html = (
        f'<img class="cover-art" alt="" src="{cover_art_uri}"><div class="cover-art-shade"></div>'
        if cover_art_uri
        else ""
    )
    texture_opacity = ".48" if cover_art_uri else ".92"
    title = _e(report["title"])
    subtitle = _e(report.get("summary") or report.get("subtitle") or "Executive intelligence for consequential decisions")
    report_type = _e(report.get("reportType") or "GateX Decision Intelligence")
    classification = _e(release_classification(report))
    language = "ZH" if report.get("language") == "zh" else "EN"
    version = int(report.get("versionNo") or 1)
    issued = _display_date(report.get("versionSubmittedAt") or report.get("releaseDate"))
    title_size = 39 if len(str(report["title"])) < 74 else 34 if len(str(report["title"])) < 130 else 29
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
    radial-gradient(circle at 18% 12%, rgba(100,145,193,.13), transparent 36%),
    linear-gradient(148deg, #071426 0%, #0a203d 49%, #061426 100%);
}}
.cover-art {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .88; filter: saturate(.62) contrast(1.1) brightness(.74); }}
.cover-art-shade {{ position: absolute; inset: 0; background: linear-gradient(104deg, rgba(2,9,18,.93) 0%, rgba(4,15,29,.78) 52%, rgba(3,12,24,.46) 100%), linear-gradient(0deg, rgba(2,8,16,.82), transparent 48%, rgba(2,8,16,.22)); }}
.texture {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: {texture_opacity}; filter: saturate(.76) contrast(1.05) brightness(.75); mix-blend-mode: overlay; }}
.light {{ position: absolute; inset: 0; background: linear-gradient(118deg, rgba(181,210,237,.09) 0%, transparent 36%, rgba(0,7,18,.21) 100%); }}
.frame {{ position: absolute; inset: 12.5mm; border: .25mm solid rgba(215,227,239,.20); box-shadow: inset 0 0 0 .35mm rgba(0,0,0,.22); }}
.rail {{ position: absolute; left: 12.5mm; top: 12.5mm; bottom: 12.5mm; width: 2.2mm; background: linear-gradient(90deg, rgba(0,0,0,.30), rgba(177,202,228,.18) 52%, rgba(0,0,0,.28)); border-right: .2mm solid rgba(220,233,246,.13); }}
.content {{ position: relative; z-index: 2; height: 100%; padding: 19mm 22mm 17mm 26mm; display: flex; flex-direction: column; }}
.mast {{ display: flex; justify-content: space-between; align-items: flex-start; }}
.brand {{ display: flex; align-items: center; gap: 3.6mm; }}
.brand img {{ display: block; width: 15.5mm; height: 15.5mm; object-fit: contain; }}
.brand-word {{ color: #f7f9fb; font-size: 17pt; line-height: 1; font-weight: 750; letter-spacing: .105em; text-shadow: 0 .35mm .4mm rgba(0,0,0,.38); }}
.brand-word small {{ display: block; margin-top: 3mm; color: rgba(205,224,242,.72); font-size: 5.8pt; font-weight: 650; letter-spacing: .255em; }}
.class {{ margin-top: 1.5mm; padding: 2.3mm 0 2mm 6mm; min-width: 49mm; color: rgba(220,234,247,.82); border-top: .25mm solid rgba(205,222,238,.40); border-bottom: .25mm solid rgba(205,222,238,.23); font-size: 6pt; font-weight: 700; letter-spacing: .14em; text-align: right; }}
.copy {{ margin-top: 49mm; width: 156mm; }}
.kicker {{ margin: 0 0 7mm; color: #83b9e7; font-size: 7.5pt; font-weight: 750; letter-spacing: .22em; text-transform: uppercase; }}
h1 {{ margin: 0; max-width: 156mm; color: #f7f9fb; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: {title_size}pt; font-weight: 400; line-height: 1.06; letter-spacing: -.022em; text-shadow: 0 .45mm .55mm rgba(0,0,0,.40); }}
.rule {{ width: 39mm; height: .55mm; margin: 9mm 0 7mm; background: linear-gradient(90deg, #80b7e8 0%, rgba(206,226,244,.82) 48%, rgba(128,183,232,.05) 100%); }}
.subtitle {{ max-width: 133mm; margin: 0; color: rgba(229,238,247,.74); font-size: 11.5pt; line-height: 1.5; }}
.foot {{ display: grid; margin-top: auto; padding-top: 7mm; grid-template-columns: 1fr 1fr 1fr; border-top: .25mm solid rgba(205,222,238,.23); color: rgba(216,230,243,.66); font-size: 6.5pt; letter-spacing: .095em; }}
.foot span:nth-child(2) {{ text-align: center; }} .foot span:last-child {{ text-align: right; }}
</style></head>
<body>{cover_art_html}<img class="texture" alt="" src="{texture_uri}"><div class="light"></div><div class="frame"></div><div class="rail"></div>
<main class="content">
  <div class="mast"><div class="brand"><img alt="GateX G mark" src="{mark_uri}"><div class="brand-word">GATEX<small>EXECUTIVE INTELLIGENCE</small></div></div><div class="class">{classification}</div></div>
  <section class="copy"><p class="kicker">{report_type}</p><h1>{title}</h1><div class="rule"></div><p class="subtitle">{subtitle}</p></section>
  <footer class="foot"><span>EDITION {issued}</span><span>{language} / VERSION {version:02d}</span><span>GATEX.FUND</span></footer>
</main></body></html>"""


def _back_cover_html(report: Mapping[str, Any]) -> str:
    texture_uri = _asset_data_uri(COVER_TEXTURE_PATH, "image/jpeg")
    mark_uri = _asset_data_uri(G_MARK_PATH, "image/png")
    cover_art_uri = _optional_cover_data_uri(report.get("coverImagePath"))
    cover_art_html = f'<img class="cover-art" alt="" src="{cover_art_uri}">' if cover_art_uri else ""
    title = _e(report.get("title") or "GateX Intelligence")
    report_type = _e(report.get("reportType") or "Strategic Intelligence")
    classification = _e(release_classification(report))
    language = "ZH" if report.get("language") == "zh" else "EN"
    version = int(report.get("versionNo") or 1)
    issued = _display_date(report.get("versionSubmittedAt") or report.get("releaseDate"))
    author_rows = []
    for author in report.get("authors") or []:
        author_rows.append(
            "<article>"
            f"<strong>{_e(author.get('name'))}</strong>"
            f"<span>{_e(author.get('role'))}</span>"
            f"<small>{_e(author.get('email'))}</small>"
            "</article>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>
@page {{ size: A4 portrait; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ width: 210mm; height: 297mm; margin: 0; }}
body {{ position: relative; overflow: hidden; color: #f5f8fb; font-family: Arial, "Helvetica Neue", sans-serif; background: #061426; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
.cover-art {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .68; filter: saturate(.56) contrast(1.08) brightness(.62); }}
.texture {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .42; filter: saturate(.62) contrast(1.1) brightness(.54); mix-blend-mode: overlay; }}
.wash {{ position: absolute; inset: 0; background: linear-gradient(102deg, rgba(3,12,24,.97) 0%, rgba(5,23,45,.91) 58%, rgba(4,18,35,.72) 100%), linear-gradient(0deg, rgba(2,9,18,.94), transparent 54%, rgba(2,8,16,.36)); }}
.grid {{ position: absolute; inset: 0; opacity: .10; background-image: linear-gradient(rgba(126,174,219,.28) .25mm, transparent .25mm), linear-gradient(90deg, rgba(126,174,219,.28) .25mm, transparent .25mm); background-size: 22mm 22mm; }}
.frame {{ position: absolute; inset: 12.5mm; border: .25mm solid rgba(215,227,239,.22); }}
.rail {{ position: absolute; left: 12.5mm; top: 12.5mm; bottom: 12.5mm; width: 2.2mm; background: linear-gradient(90deg, rgba(0,0,0,.30), rgba(177,202,228,.18) 52%, rgba(0,0,0,.28)); border-right: .2mm solid rgba(220,233,246,.13); }}
main {{ position: relative; z-index: 2; height: 100%; padding: 19mm 22mm 17mm 26mm; display: flex; flex-direction: column; }}
header {{ display: flex; align-items: center; justify-content: space-between; padding-bottom: 7mm; border-bottom: .25mm solid rgba(213,229,244,.26); }}
.brand {{ display: flex; align-items: center; gap: 4mm; }}
.brand img {{ width: 14mm; height: 14mm; object-fit: contain; }}
.brand strong {{ font-size: 17pt; letter-spacing: .1em; }}
header > span {{ color: rgba(210,227,243,.72); font-size: 6.5pt; font-weight: 700; letter-spacing: .15em; }}
.publication {{ margin-top: 25mm; width: 155mm; }}
.eyebrow {{ margin: 0 0 6mm; color: #7fc1f2; font-size: 7pt; font-weight: 800; letter-spacing: .21em; }}
h1 {{ margin: 0; max-width: 151mm; font-family: Georgia, "Times New Roman", serif; font-size: 27pt; font-weight: 400; line-height: 1.08; }}
.statement {{ max-width: 126mm; margin: 7mm 0 0; color: rgba(228,238,247,.75); font-family: Georgia, "Times New Roman", serif; font-size: 12pt; line-height: 1.48; }}
.rule {{ width: 42mm; height: .55mm; margin: 8mm 0 6mm; background: linear-gradient(90deg, #7fc1f2, rgba(211,232,248,.78) 48%, transparent); }}
.meta {{ display: grid; width: 151mm; margin-top: 12mm; grid-template-columns: 1.2fr 1fr 1.35fr 1fr; border-top: .25mm solid rgba(211,229,245,.28); border-bottom: .25mm solid rgba(211,229,245,.20); }}
.meta div {{ min-height: 18mm; padding: 4mm 4mm 3.5mm 0; border-right: .25mm solid rgba(211,229,245,.17); }}
.meta div + div {{ padding-left: 4mm; }} .meta div:last-child {{ border-right: 0; }}
.meta span {{ display: block; margin-bottom: 2mm; color: rgba(174,205,232,.64); font-size: 5.8pt; font-weight: 700; letter-spacing: .15em; }}
.meta strong {{ color: #f2f7fb; font-size: 8.3pt; font-weight: 650; }}
.desk {{ margin-top: auto; }}
.desk-head {{ display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 4mm; padding-top: 4mm; border-top: .75mm solid #57a7e9; }}
.desk-head strong {{ color: #eaf3fa; font-size: 7pt; letter-spacing: .17em; text-transform: uppercase; }}
.desk-head span {{ max-width: 77mm; color: rgba(213,229,243,.60); font-size: 6.8pt; line-height: 1.4; text-align: right; }}
.team article {{ display: grid; align-items: baseline; grid-template-columns: 49mm 1fr 50mm; gap: 5mm; padding: 3.65mm 0; border-bottom: .25mm solid rgba(209,227,244,.18); }}
.team strong {{ font-size: 8.8pt; }}
.team span, .team small {{ color: rgba(220,233,245,.65); font-size: 7.4pt; }}
.team small {{ text-align: right; }}
footer {{ display: flex; justify-content: space-between; margin-top: 7mm; color: rgba(213,229,243,.58); font-size: 6.2pt; letter-spacing: .1em; }}
</style></head><body>{cover_art_html}<img class="texture" alt="" src="{texture_uri}"><div class="wash"></div><div class="grid"></div><div class="frame"></div><div class="rail"></div>
<main><header><div class="brand"><img alt="GateX G mark" src="{mark_uri}"><strong>GATEX</strong></div><span>MEMBER CONFIDENTIAL</span></header>
<section class="publication"><p class="eyebrow">GATEX / PUBLICATION RECORD</p><h1>{title}</h1><div class="rule"></div><p class="statement">Independent, evidence-led intelligence prepared for consequential decisions.</p>
<div class="meta"><div><span>EDITION</span><strong>{issued}</strong></div><div><span>LANGUAGE</span><strong>{language}</strong></div><div><span>PUBLICATION</span><strong>{report_type}</strong></div><div><span>VERSION</span><strong>{version:02d}</strong></div></div></section>
<section class="desk"><div class="desk-head"><strong>Authors and editorial desk</strong><span>Regional context, sector research and editorial review for authorised GateX readers.</span></div><div class="team">{''.join(author_rows)}</div></section>
<footer><span>{classification}</span><span>GATEX.FUND</span></footer></main></body></html>"""


def _body_html(report: Mapping[str, Any]) -> str:
    sections = list(report.get("contentSections") or [])
    if any(item.get("kind") in {"executive_summary", "chapter", "outlook"} for item in sections):
        return _whitepaper_body_html(report)
    return _legacy_body_html(report)


def _legacy_body_html(report: Mapping[str, Any]) -> str:
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
        "<div class='eyebrow'>EXECUTIVE BRIEF</div>",
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
        parts.append("<section class='notes page'><div class='eyebrow'>REPORT NOTES</div><h2>Methodology and use</h2>")
        for note in notes:
            parts.append(f"<article><h3>{_e(note.get('heading'))}</h3><p>{_e(_section_text(note))}</p></article>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def _whitepaper_body_html(report: Mapping[str, Any]) -> str:
    sections = list(report.get("contentSections") or [])
    executive = next((item for item in sections if item.get("kind") == "executive_summary"), None)
    chapters = [item for item in sections if item.get("kind") == "chapter"]
    outlooks = [item for item in sections if item.get("kind") == "outlook"]
    disclaimer = next((item for item in sections if item.get("kind") == "disclaimer"), None)

    parts = [
        "<!doctype html>",
        f"<html lang='{_e(report.get('language') or 'en')}'><head><meta charset='utf-8'><style>{_whitepaper_css()}</style></head><body>",
    ]
    if executive:
        parts.append(_render_executive_summary(executive, report))

    contents_entries = chapters + outlooks
    if contents_entries:
        parts.extend(
            [
                "<section class='whitepaper-contents fixed-page'>",
                "<div class='eyebrow'>CONTENTS</div>",
                "<h2>Contents</h2>",
                "<ol>",
            ]
        )
        for index, section in enumerate(contents_entries, start=1):
            number = _clean(section.get("chapterNumber"), 20) or (f"{index:02d}" if section.get("kind") == "chapter" else "C")
            page = _clean(section.get("tocPage"), 20) or "-"
            parts.append(
                "<li>"
                f"<span class='contents-number'>{_e(number)}</span>"
                "<div>"
                f"<strong>{_e(section.get('heading'))}</strong>"
                + (f"<p>{_e(section.get('lead'))}</p>" if section.get("lead") else "")
                + "</div>"
                f"<span class='contents-page'>{_e(page)}</span>"
                "</li>"
            )
        parts.extend(["</ol>", "</section>"])

    for section in sections:
        kind = str(section.get("kind") or "")
        if kind == "chapter":
            parts.append(_render_whitepaper_chapter(section))
        elif kind == "exhibit":
            parts.append(_render_whitepaper_exhibit(section))
        elif kind == "outlook":
            parts.append(_render_whitepaper_outlook(section))

    if disclaimer:
        parts.append(_render_whitepaper_disclaimer(disclaimer))
    parts.append("</body></html>")
    return "".join(parts)


def _render_executive_summary(section: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    visual_uri = _optional_visual_data_uri(section.get("visualPath"))
    paragraphs = [entry for entry in section.get("paragraphs") or [] if entry]
    parts = ["<section class='executive-summary fixed-page'>"]
    if visual_uri:
        parts.append(
            "<figure class='executive-visual'>"
            f"<img alt='{_e(section.get('visualAlt') or report.get('title'))}' src='{visual_uri}'>"
            f"<figcaption>{_e(section.get('visualAlt') or '')}</figcaption>"
            "</figure>"
        )
    parts.extend(
        [
            "<div class='eyebrow'>EXECUTIVE SUMMARY</div>",
            f"<h1>{_e(section.get('heading') or report.get('title'))}</h1>",
        ]
    )
    if section.get("lead"):
        parts.append(f"<p class='executive-deck'>{_e(section.get('lead'))}</p>")
    if section.get("body"):
        paragraphs.insert(0, str(section.get("body")))
    if paragraphs:
        parts.append("<div class='executive-copy'>")
        for paragraph in paragraphs:
            parts.append(f"<p>{_e(paragraph)}</p>")
        parts.append("</div>")
    parts.append(_render_whitepaper_footnotes(section))
    parts.append("</section>")
    return "".join(parts)


def _render_whitepaper_chapter(section: Mapping[str, Any]) -> str:
    number = _clean(section.get("chapterNumber"), 20) or "01"
    subsections = [raw if isinstance(raw, Mapping) else {} for raw in section.get("subsections") or []]
    opening_subsections = subsections[:1]
    continuation_subsections = subsections[1:]
    visual_uri = _optional_visual_data_uri(section.get("visualPath"))
    visual = ""
    if visual_uri:
        visual = (
            "<figure class='chapter-visual'>"
            f"<img alt='{_e(section.get('visualAlt') or section.get('heading'))}' src='{visual_uri}'>"
            f"<figcaption><strong>GATEX EDITORIAL IMAGE</strong><span>{_e(section.get('visualAlt') or '')}</span></figcaption>"
            "</figure>"
        )
    def append_subsections(parts: list[str], rows: list[Mapping[str, Any]]) -> None:
        for subsection in rows:
            heading = _clean(subsection.get("heading") or subsection.get("title"), 300)
            if heading:
                parts.append(f"<h3>{_e(heading)}</h3>")
            if subsection.get("body"):
                parts.append(f"<p>{_e(subsection.get('body'))}</p>")
            for paragraph in _list(subsection.get("paragraphs")):
                if _clean(paragraph, 12_000):
                    parts.append(f"<p>{_e(paragraph)}</p>")

    parts = [
        f"<section class='whitepaper-chapter chapter-opening-page fixed-page' data-chapter='{_e(number)}'>",
        "<header class='chapter-head'>",
        f"<span class='chapter-number'>{_e(number)}</span>",
        "<div>",
        f"<h2>{_e(section.get('heading'))}</h2>",
        (f"<p>{_e(section.get('lead'))}</p>" if section.get("lead") else ""),
        "</div></header>",
    ]
    if visual and section.get("visualPlacement") == "top":
        parts.append(visual)
    if section.get("callout"):
        parts.append(f"<blockquote>{_e(section.get('callout'))}</blockquote>")
    parts.append("<div class='chapter-copy'>")
    if section.get("body"):
        parts.append(f"<p>{_e(section.get('body'))}</p>")
    for paragraph in section.get("paragraphs") or []:
        parts.append(f"<p>{_e(paragraph)}</p>")
    append_subsections(parts, opening_subsections)
    parts.append("</div>")
    if visual and section.get("visualPlacement") != "top":
        parts.append(visual)
    parts.append(_render_whitepaper_footnotes(section))
    parts.append("</section>")

    if continuation_subsections:
        parts.extend(
            [
                f"<section class='whitepaper-chapter-continuation' data-chapter='{_e(number)}'>",
                "<header class='chapter-continuation-head'>",
                f"<span>CHAPTER {_e(number)} / CONTINUED</span>",
                f"<h2>{_e(section.get('heading'))}</h2>",
                "</header>",
            ]
        )
        lead_subsection = continuation_subsections[0]
        lead_heading = _clean(lead_subsection.get("heading") or lead_subsection.get("title"), 300)
        parts.append("<article class='chapter-continuation-lead'>")
        if lead_heading:
            parts.append(f"<h3>{_e(lead_heading)}</h3>")
        parts.append("<div>")
        if lead_subsection.get("body"):
            parts.append(f"<p>{_e(lead_subsection.get('body'))}</p>")
        for paragraph in _list(lead_subsection.get("paragraphs")):
            if _clean(paragraph, 12_000):
                parts.append(f"<p>{_e(paragraph)}</p>")
        parts.append("</div></article>")
        if len(continuation_subsections) > 1:
            parts.append("<div class='chapter-continuation-grid'>")
            for subsection in continuation_subsections[1:]:
                heading = _clean(subsection.get("heading") or subsection.get("title"), 300)
                parts.append("<article>")
                if heading:
                    parts.append(f"<h3>{_e(heading)}</h3>")
                if subsection.get("body"):
                    parts.append(f"<p>{_e(subsection.get('body'))}</p>")
                for paragraph in _list(subsection.get("paragraphs")):
                    if _clean(paragraph, 12_000):
                        parts.append(f"<p>{_e(paragraph)}</p>")
                parts.append("</article>")
            parts.append("</div>")
        parts.append("</section>")
    return "".join(parts)


def _render_whitepaper_exhibit(section: Mapping[str, Any]) -> str:
    exhibit = section.get("exhibit") if isinstance(section.get("exhibit"), Mapping) else {}
    parts = [
        "<section class='whitepaper-exhibit'>",
        "<header class='whitepaper-exhibit-head'>",
        f"<span>{_e(exhibit.get('label') or 'EXHIBIT')}</span>",
        f"<h2>{_e(section.get('heading'))}</h2>",
        (f"<p>{_e(exhibit.get('caption'))}</p>" if exhibit.get("caption") else ""),
        "</header>",
    ]
    metrics = exhibit.get("metrics") if isinstance(exhibit.get("metrics"), list) else []
    if metrics:
        parts.append(f"<div class='whitepaper-metrics metrics-{min(len(metrics), 6)}'>")
        for raw in metrics[:6]:
            metric = raw if isinstance(raw, Mapping) else {}
            parts.append(
                "<article>"
                f"<strong>{_e(metric.get('value') or '-')}</strong>"
                f"<span>{_e(metric.get('label'))}</span>"
                + (f"<small>{_e(metric.get('note'))}</small>" if metric.get("note") else "")
                + "</article>"
            )
        parts.append("</div>")
    panels = exhibit.get("panels") if isinstance(exhibit.get("panels"), list) else []
    if panels:
        parts.append("<div class='exhibit-panels'>")
        for panel_index, raw in enumerate(panels, start=1):
            panel = raw if isinstance(raw, Mapping) else {}
            parts.append(_render_whitepaper_panel(panel, panel_index))
        parts.append("</div>")
    narrative = [_clean(value, 12_000) for value in _list(exhibit.get("narrative")) if _clean(value, 12_000)]
    if narrative and exhibit.get("show_narrative") is True:
        parts.append("<div class='exhibit-analysis'>")
        for paragraph in narrative:
            parts.append(f"<p>{_e(paragraph)}</p>")
        parts.append("</div>")
    source = _clean(exhibit.get("source_note") or exhibit.get("sourceNote"), 2_000)
    if source:
        parts.append(f"<p class='whitepaper-source'><strong>Source:</strong> {_e(source)}</p>")
    parts.append(_render_whitepaper_footnotes(section))
    parts.append("</section>")
    return "".join(parts)


_CHART_COLORS = ("#082b59", "#147f91", "#4d8fb8", "#9aabba", "#c29b57")


def _line_chart_svg(panel: Mapping[str, Any]) -> str:
    raw_series = [item for item in _list(panel.get("series")) if isinstance(item, Mapping)]
    labels = [str(value) for value in _list(panel.get("xLabels") or panel.get("categories"))]
    if not raw_series:
        items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)]
        labels = labels or [str(item.get("label") or "") for item in items]
        raw_series = [{"name": panel.get("seriesName") or "Series", "values": [item.get("value") for item in items]}]
    series = []
    for raw in raw_series[:4]:
        values = [_number(value) for value in _list(raw.get("values"))]
        if values:
            series.append({"name": _clean(raw.get("name"), 80), "values": values})
    count = max([len(item["values"]) for item in series] + [0])
    if count < 2:
        return ""
    labels = (labels + [str(index + 1) for index in range(count)])[:count]
    values = [value for item in series for value in item["values"]]
    low = min(values + [0.0])
    high = max(values + [0.0])
    if math.isclose(low, high):
        high = low + 1.0
    padding = (high - low) * 0.08
    low -= padding
    high += padding
    width, height = 760.0, 270.0
    left, right, top, bottom = 64.0, 18.0, 34.0, 48.0
    plot_w, plot_h = width - left - right, height - top - bottom

    def point(index: int, value: float) -> tuple[float, float]:
        x = left + (plot_w * index / max(1, count - 1))
        y = top + (high - value) / (high - low) * plot_h
        return x, y

    parts = [f"<svg class='whitepaper-chart-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    for tick in range(5):
        value = high - (high - low) * tick / 4
        y = top + plot_h * tick / 4
        parts.append(f"<line class='chart-grid' x1='{left}' y1='{y:.2f}' x2='{width-right}' y2='{y:.2f}'/>")
        parts.append(f"<text class='chart-tick' x='{left-8}' y='{y+3:.2f}' text-anchor='end'>{_e(_format_number(value))}</text>")
    for index, label in enumerate(labels):
        x, _ = point(index, 0)
        parts.append(f"<text class='chart-tick' x='{x:.2f}' y='{height-20}' text-anchor='middle'>{_e(label)}</text>")
    for series_index, item in enumerate(series):
        color = _CHART_COLORS[series_index % len(_CHART_COLORS)]
        coords = [point(index, value) for index, value in enumerate(item["values"][:count])]
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
        parts.append(f"<polyline fill='none' stroke='{color}' stroke-width='3.2' points='{points}'/>")
        for x, y in coords:
            parts.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='4.2' fill='{color}' stroke='#ffffff' stroke-width='1.8'/>")
        legend_x = left + series_index * 170
        parts.append(f"<line x1='{legend_x}' y1='15' x2='{legend_x+22}' y2='15' stroke='{color}' stroke-width='3.2'/>")
        parts.append(f"<text class='chart-legend' x='{legend_x+28}' y='18'>{_e(item['name'] or f'Series {series_index + 1}')}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _scatter_chart_svg(panel: Mapping[str, Any]) -> str:
    items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)][:10]
    if len(items) < 2:
        return ""
    xs = [_number(item.get("x")) for item in items]
    ys = [_number(item.get("y")) for item in items]
    low_x, high_x = min(xs), max(xs)
    low_y, high_y = min(ys), max(ys)
    if math.isclose(low_x, high_x):
        high_x = low_x + 1.0
    if math.isclose(low_y, high_y):
        high_y = low_y + 1.0
    pad_x = (high_x - low_x) * 0.1
    pad_y = (high_y - low_y) * 0.1
    low_x, high_x = low_x - pad_x, high_x + pad_x
    low_y, high_y = low_y - pad_y, high_y + pad_y
    width, height = 760.0, 285.0
    left, right, top, bottom = 66.0, 24.0, 22.0, 54.0
    plot_w, plot_h = width - left - right, height - top - bottom

    def point(x_value: float, y_value: float) -> tuple[float, float]:
        x = left + (x_value - low_x) / (high_x - low_x) * plot_w
        y = top + (high_y - y_value) / (high_y - low_y) * plot_h
        return x, y

    parts = [f"<svg class='whitepaper-chart-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    for tick in range(5):
        x_value = low_x + (high_x - low_x) * tick / 4
        y_value = high_y - (high_y - low_y) * tick / 4
        x = left + plot_w * tick / 4
        y = top + plot_h * tick / 4
        parts.append(f"<line class='chart-grid' x1='{x:.2f}' y1='{top}' x2='{x:.2f}' y2='{height-bottom}'/>")
        parts.append(f"<line class='chart-grid' x1='{left}' y1='{y:.2f}' x2='{width-right}' y2='{y:.2f}'/>")
        parts.append(f"<text class='chart-tick' x='{x:.2f}' y='{height-27}' text-anchor='middle'>{_e(_format_number(x_value))}</text>")
        parts.append(f"<text class='chart-tick' x='{left-8}' y='{y+3:.2f}' text-anchor='end'>{_e(_format_number(y_value))}</text>")
    median_x = sorted(xs)[len(xs) // 2]
    median_y = sorted(ys)[len(ys) // 2]
    median_plot_x, median_plot_y = point(median_x, median_y)
    parts.append(f"<line class='chart-reference' x1='{median_plot_x:.2f}' y1='{top}' x2='{median_plot_x:.2f}' y2='{height-bottom}'/>")
    parts.append(f"<line class='chart-reference' x1='{left}' y1='{median_plot_y:.2f}' x2='{width-right}' y2='{median_plot_y:.2f}'/>")
    highest_y = max(range(len(items)), key=lambda index: ys[index])
    highest_x = max(range(len(items)), key=lambda index: xs[index])
    for index, item in enumerate(items):
        x, y = point(xs[index], ys[index])
        color = "#082b59" if index == highest_y else "#147f91" if index == highest_x else "#7894a8"
        radius = max(5.0, min(10.0, _number(item.get("size")) or 6.5))
        label_on_left = x > left + plot_w * 0.72
        default_dx = -radius - 5 if label_on_left else radius + 5
        label_dx = _number(item.get("labelDx")) if item.get("labelDx") is not None else default_dx
        label_dy = _number(item.get("labelDy")) if item.get("labelDy") is not None else -8.0
        label_x = x + label_dx
        label_y = y + label_dy
        anchor = _clean(item.get("labelAnchor"), 12) or ("end" if label_dx < 0 else "start")
        parts.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='{radius:.2f}' fill='{color}' fill-opacity='.94' stroke='#ffffff' stroke-width='2'/>")
        parts.append(f"<line x1='{x:.2f}' y1='{y:.2f}' x2='{label_x:.2f}' y2='{label_y+3:.2f}' stroke='#8fa2b1' stroke-width='.8'/>")
        parts.append(f"<text class='chart-point-label' x='{label_x:.2f}' y='{label_y:.2f}' text-anchor='{anchor}'>{_e(item.get('label'))}</text>")
        parts.append(f"<text class='chart-point-value' x='{label_x:.2f}' y='{label_y+11:.2f}' text-anchor='{anchor}'>{_e(_format_number(xs[index]))} / {_e(_format_number(ys[index]))}</text>")
    parts.append(f"<text class='chart-axis-label' x='{left + plot_w/2:.2f}' y='{height-5}' text-anchor='middle'>{_e(panel.get('xLabel'))}</text>")
    parts.append(f"<text class='chart-axis-label' transform='translate(13 {top + plot_h/2:.2f}) rotate(-90)' text-anchor='middle'>{_e(panel.get('yLabel'))}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _waterfall_chart_svg(panel: Mapping[str, Any]) -> str:
    items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)][:9]
    if len(items) < 2:
        return ""
    cumulative = 0.0
    bars: list[tuple[float, float, Mapping[str, Any]]] = []
    for item in items:
        if str(item.get("type") or "").lower() == "total":
            start, end = 0.0, cumulative
        else:
            value = _number(item.get("value"))
            start, end = cumulative, cumulative + value
            cumulative = end
        bars.append((start, end, item))
    low = min([0.0] + [min(start, end) for start, end, _ in bars])
    high = max([0.0] + [max(start, end) for start, end, _ in bars])
    if math.isclose(low, high):
        high = low + 1.0
    padding = (high - low) * 0.1
    low -= padding
    high += padding
    width, height = 760.0, 285.0
    left, right, top, bottom = 58.0, 18.0, 20.0, 58.0
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / len(bars)

    def y(value: float) -> float:
        return top + (high - value) / (high - low) * plot_h

    parts = [f"<svg class='whitepaper-chart-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    for tick in range(5):
        value = high - (high - low) * tick / 4
        line_y = top + plot_h * tick / 4
        parts.append(f"<line class='chart-grid' x1='{left}' y1='{line_y:.2f}' x2='{width-right}' y2='{line_y:.2f}'/>")
        parts.append(f"<text class='chart-tick' x='{left-8}' y='{line_y+3:.2f}' text-anchor='end'>{_e(_format_number(value))}</text>")
    for index, (start, end, item) in enumerate(bars):
        x = left + slot * index + slot * 0.16
        bar_w = slot * 0.68
        top_y, bottom_y = min(y(start), y(end)), max(y(start), y(end))
        bar_h = max(2.0, bottom_y - top_y)
        is_total = str(item.get("type") or "").lower() == "total"
        color = "#0b579d" if is_total else "#1593a9" if end >= start else "#b96864"
        parts.append(f"<rect x='{x:.2f}' y='{top_y:.2f}' width='{bar_w:.2f}' height='{bar_h:.2f}' fill='{color}'/>")
        display = item.get("display") or _format_number(end if is_total else end - start)
        parts.append(f"<text class='chart-value' x='{x + bar_w/2:.2f}' y='{top_y-5:.2f}' text-anchor='middle'>{_e(display)}</text>")
        parts.append(f"<text class='chart-tick' x='{x + bar_w/2:.2f}' y='{height-27}' text-anchor='middle'>{_e(item.get('label'))}</text>")
        if index < len(bars) - 1:
            connector_y = y(end)
            next_x = left + slot * (index + 1) + slot * 0.16
            parts.append(f"<line x1='{x+bar_w:.2f}' y1='{connector_y:.2f}' x2='{next_x:.2f}' y2='{connector_y:.2f}' stroke='#9eacba' stroke-width='1.1' stroke-dasharray='3 3'/>")
    parts.append("</svg>")
    return "".join(parts)


def _bar_chart_svg(panel: Mapping[str, Any]) -> str:
    items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)][:9]
    if not items:
        return ""
    values = [_number(item.get("value")) for item in items]
    low = min([0.0] + values)
    high = max([0.0] + values)
    if math.isclose(low, high):
        high = low + 1.0
    span = high - low
    low -= span * 0.04 if low < 0 else 0.0
    high += span * 0.12
    width = 760.0
    height = max(220.0, 84.0 + len(items) * 38.0)
    longest_label = max(len(_clean(item.get("label"), 42)) for item in items)
    # SVG text is clipped at the viewBox edge even when CSS overflow is visible.
    # Reserve enough room for long industry/category labels before plotting bars.
    left = min(270.0, max(172.0, 28.0 + longest_label * 5.5))
    right, top, bottom = 54.0, 34.0, 42.0
    plot_w, plot_h = width - left - right, height - top - bottom
    row_h = plot_h / len(items)

    def x(value: float) -> float:
        return left + (value - low) / (high - low) * plot_w

    zero_x = x(0.0)
    highlight = max(range(len(values)), key=lambda index: abs(values[index]))
    parts = [f"<svg class='whitepaper-chart-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    for tick in range(5):
        value = low + (high - low) * tick / 4
        line_x = x(value)
        parts.append(f"<line class='chart-grid' x1='{line_x:.2f}' y1='{top}' x2='{line_x:.2f}' y2='{height-bottom}'/>")
        parts.append(f"<text class='chart-tick' x='{line_x:.2f}' y='{height-19}' text-anchor='middle'>{_e(_format_number(value))}</text>")
    parts.append(f"<line class='chart-baseline' x1='{zero_x:.2f}' y1='{top}' x2='{zero_x:.2f}' y2='{height-bottom}'/>")
    for index, item in enumerate(items):
        value = values[index]
        center_y = top + row_h * (index + 0.5)
        bar_y = center_y - min(10.0, row_h * 0.28)
        value_x = x(value)
        start_x = min(zero_x, value_x)
        bar_w = max(2.0, abs(value_x - zero_x))
        color = "#147f91" if index == highlight else "#315f86"
        parts.append(f"<text class='chart-category' x='{left-12}' y='{center_y+4:.2f}' text-anchor='end'>{_e(_clean(item.get('label'), 42))}</text>")
        parts.append(f"<rect x='{start_x:.2f}' y='{bar_y:.2f}' width='{bar_w:.2f}' height='{min(20.0, row_h * 0.56):.2f}' fill='{color}'/>")
        display = item.get("display") or _format_number(value)
        anchor = "start" if value >= 0 else "end"
        label_x = value_x + (8 if value >= 0 else -8)
        parts.append(f"<text class='chart-value chart-value-strong' x='{label_x:.2f}' y='{center_y+4:.2f}' text-anchor='{anchor}'>{_e(display)}</text>")
    if panel.get("axisLabel"):
        parts.append(f"<text class='chart-axis-label' x='{left + plot_w/2:.2f}' y='{height-3}' text-anchor='middle'>{_e(panel.get('axisLabel'))}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _stacked_bar_chart_svg(panel: Mapping[str, Any]) -> str:
    items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)][:7]
    if not items:
        return ""
    legend: list[str] = []
    for item in items:
        for segment in _list(item.get("segments")):
            if isinstance(segment, Mapping):
                label = _clean(segment.get("label"), 80)
                if label and label not in legend:
                    legend.append(label)
    width = 760.0
    height = max(176.0, 112.0 + len(items) * 48.0)
    left, right, top, bottom = 152.0, 64.0, 55.0, 42.0
    plot_w, plot_h = width - left - right, height - top - bottom
    row_h = plot_h / len(items)
    parts = [f"<svg class='whitepaper-chart-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    legend_x = left
    for index, label in enumerate(legend):
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        parts.append(f"<rect x='{legend_x:.2f}' y='12' width='11' height='11' fill='{color}'/>")
        parts.append(f"<text class='chart-legend' x='{legend_x+17:.2f}' y='21'>{_e(label)}</text>")
        legend_x += max(92.0, 17.0 + len(label) * 6.0)
    for tick in range(5):
        value = tick * 25
        line_x = left + plot_w * tick / 4
        parts.append(f"<line class='chart-grid' x1='{line_x:.2f}' y1='{top}' x2='{line_x:.2f}' y2='{height-bottom}'/>")
        parts.append(f"<text class='chart-tick' x='{line_x:.2f}' y='{height-18}' text-anchor='middle'>{value}%</text>")
    for row_index, item in enumerate(items):
        segments = [segment for segment in _list(item.get("segments")) if isinstance(segment, Mapping)]
        total = sum(max(0.0, _number(segment.get("value"))) for segment in segments) or 1.0
        center_y = top + row_h * (row_index + 0.5)
        bar_y = center_y - min(12.0, row_h * 0.28)
        cursor = left
        parts.append(f"<text class='chart-category' x='{left-12}' y='{center_y+4:.2f}' text-anchor='end'>{_e(_clean(item.get('label'), 40))}</text>")
        for index, segment in enumerate(segments):
            value = max(0.0, _number(segment.get("value")))
            segment_w = value / total * plot_w
            color = _CHART_COLORS[index % len(_CHART_COLORS)]
            parts.append(f"<rect x='{cursor:.2f}' y='{bar_y:.2f}' width='{segment_w:.2f}' height='{min(24.0, row_h * 0.56):.2f}' fill='{color}'/>")
            display = _clean(segment.get("display"), 40)
            if display and segment_w >= 46:
                parts.append(f"<text class='chart-segment-value' x='{cursor + segment_w/2:.2f}' y='{center_y+4:.2f}' text-anchor='middle'>{_e(display)}</text>")
            cursor += segment_w
        if item.get("display"):
            parts.append(f"<text class='chart-value chart-value-strong' x='{width-right+10:.2f}' y='{center_y+4:.2f}'>{_e(item.get('display'))}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _vehicle_scale_chart_svg(panel: Mapping[str, Any]) -> str:
    items = [item for item in _list(panel.get("items")) if isinstance(item, Mapping)][:3]
    if len(items) < 2:
        return ""
    heights = [max(1.0, _number(item.get("height"))) for item in items]
    diameters = [max(1.0, _number(item.get("diameter"))) for item in items]
    chart_max = max(150.0, math.ceil(max(heights) / 25.0) * 25.0)
    width, height = 760.0, 350.0
    left, right, top, bottom = 64.0, 24.0, 24.0, 88.0
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / len(items)

    def y(value: float) -> float:
        return top + (chart_max - value) / chart_max * plot_h

    parts = [f"<svg class='whitepaper-chart-svg vehicle-scale-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img'>"]
    for value in range(0, int(chart_max) + 1, 25):
        line_y = y(float(value))
        parts.append(f"<line class='chart-grid' x1='{left}' y1='{line_y:.2f}' x2='{width-right}' y2='{line_y:.2f}'/>")
        parts.append(f"<text class='chart-tick' x='{left-9}' y='{line_y+3:.2f}' text-anchor='end'>{value} m</text>")
    base_y = y(0.0)
    parts.append(f"<line class='chart-baseline' x1='{left}' y1='{base_y:.2f}' x2='{width-right}' y2='{base_y:.2f}'/>")
    for index, item in enumerate(items):
        center_x = left + slot * (index + 0.5)
        vehicle_height = base_y - y(heights[index])
        body_width = max(14.0, min(58.0, diameters[index] * 4.5))
        nose_height = min(31.0, vehicle_height * 0.14)
        body_top = base_y - vehicle_height + nose_height
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        parts.append(
            f"<path d='M {center_x:.2f} {base_y-vehicle_height:.2f} "
            f"L {center_x+body_width/2:.2f} {body_top:.2f} L {center_x+body_width/2:.2f} {base_y-8:.2f} "
            f"L {center_x+body_width*.72:.2f} {base_y:.2f} L {center_x-body_width*.72:.2f} {base_y:.2f} "
            f"L {center_x-body_width/2:.2f} {base_y-8:.2f} L {center_x-body_width/2:.2f} {body_top:.2f} Z' "
            f"fill='{color}' fill-opacity='.94'/>")
        parts.append(f"<line x1='{center_x-body_width/2:.2f}' y1='{body_top+vehicle_height*.34:.2f}' x2='{center_x+body_width/2:.2f}' y2='{body_top+vehicle_height*.34:.2f}' stroke='#ffffff' stroke-opacity='.5' stroke-width='1.1'/>")
        parts.append(f"<line x1='{center_x-body_width/2:.2f}' y1='{body_top+vehicle_height*.68:.2f}' x2='{center_x+body_width/2:.2f}' y2='{body_top+vehicle_height*.68:.2f}' stroke='#ffffff' stroke-opacity='.5' stroke-width='1.1'/>")
        parts.append(f"<text class='vehicle-label' x='{center_x:.2f}' y='{base_y+24:.2f}' text-anchor='middle'>{_e(item.get('label'))}</text>")
        parts.append(f"<text class='vehicle-dimension' x='{center_x:.2f}' y='{base_y+40:.2f}' text-anchor='middle'>{_e(_format_number(heights[index]))} m high / {_e(_format_number(diameters[index]))} m dia.</text>")
        if item.get("payload"):
            parts.append(f"<text class='vehicle-payload' x='{center_x:.2f}' y='{base_y+57:.2f}' text-anchor='middle'>{_e(item.get('payload'))}</text>")
        if item.get("status"):
            parts.append(f"<text class='vehicle-status' x='{center_x:.2f}' y='{base_y+72:.2f}' text-anchor='middle'>{_e(item.get('status'))}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _render_whitepaper_panel(panel: Mapping[str, Any], panel_index: int) -> str:
    kind = _clean(panel.get("type"), 40).lower() or "matrix"
    span = " panel-wide" if str(panel.get("span") or "").lower() == "wide" else ""
    parts = [f"<article class='exhibit-panel panel-{_e(kind)}{span}'>"]
    if panel.get("eyebrow"):
        parts.append(f"<span class='panel-eyebrow'>{_e(panel.get('eyebrow'))}</span>")
    if panel.get("title"):
        parts.append(f"<h3>{_e(panel.get('title'))}</h3>")
    if panel.get("deck"):
        parts.append(f"<p class='panel-deck'>{_e(panel.get('deck'))}</p>")
    items = [value for value in _list(panel.get("items")) if isinstance(value, Mapping)]
    if kind in {"line", "line_chart"}:
        parts.append(_line_chart_svg(panel))
    elif kind in {"scatter", "scatter_plot"}:
        parts.append(_scatter_chart_svg(panel))
    elif kind in {"waterfall", "waterfall_chart"}:
        parts.append(_waterfall_chart_svg(panel))
    elif kind in {"stacked_bar", "stacked_bars"}:
        parts.append(_stacked_bar_chart_svg(panel))
    elif kind == "vehicle_scale":
        parts.append(_vehicle_scale_chart_svg(panel))
    elif kind == "bars":
        parts.append(_bar_chart_svg(panel))
    elif kind in {"milestone", "milestones"}:
        parts.append("<div class='milestone-track'>")
        for index, item in enumerate(items, start=1):
            parts.append(
                "<div class='milestone-item'>"
                f"<span>{index:02d}</span>"
                f"<small>{_e(item.get('label'))}</small>"
                f"<strong>{_e(item.get('metric') or item.get('value'))}</strong>"
                + (f"<p>{_e(item.get('body'))}</p>" if item.get("body") else "")
                + "</div>"
            )
        parts.append("</div>")
    elif kind == "process":
        parts.append("<div class='process-flow'>")
        for index, item in enumerate(items, start=1):
            parts.append(
                "<div class='process-step'>"
                f"<span>{index:02d}</span>"
                f"<strong>{_e(item.get('title') or item.get('label'))}</strong>"
                + (f"<b>{_e(item.get('metric'))}</b>" if item.get("metric") else "")
                + (f"<p>{_e(item.get('body'))}</p>" if item.get("body") else "")
                + "</div>"
            )
        parts.append("</div>")
    elif kind == "comparison":
        columns = [str(value) for value in _list(panel.get("columns"))[:2]]
        left = columns[0] if columns else "A"
        right = columns[1] if len(columns) > 1 else "B"
        parts.append(f"<div class='comparison-table'><header><span>Measure</span><strong>{_e(left)}</strong><strong>{_e(right)}</strong></header>")
        for item in items:
            parts.append(
                "<div>"
                f"<span>{_e(item.get('metric') or item.get('label'))}</span>"
                f"<b>{_e(item.get('left'))}</b>"
                f"<b>{_e(item.get('right'))}</b>"
                "</div>"
            )
        parts.append("</div>")
    elif kind == "scenario":
        parts.append("<div class='scenario-grid'>")
        for item in items:
            parts.append(
                "<div>"
                f"<span>{_e(item.get('label'))}</span>"
                f"<strong>{_e(item.get('range') or item.get('value'))}</strong>"
                + (f"<p>{_e(item.get('body'))}</p>" if item.get("body") else "")
                + "</div>"
            )
        parts.append("</div>")
    elif kind in {"market_map", "market_layers"}:
        parts.append("<div class='market-layer-map'>")
        parts.append("<div class='market-layer-spine' aria-hidden='true'></div>")
        for index, item in enumerate(items, start=1):
            parts.append(
                "<div class='market-layer'>"
                f"<span>{index:02d}</span>"
                f"<small>{_e(item.get('tag'))}</small>"
                f"<strong>{_e(item.get('title') or item.get('label'))}</strong>"
                + (f"<p>{_e(item.get('body'))}</p>" if item.get("body") else "")
                + "</div>"
            )
        parts.append("</div>")
    else:
        parts.append("<div class='matrix-grid'>")
        for item in items:
            parts.append(
                "<div>"
                + (f"<span>{_e(item.get('tag'))}</span>" if item.get("tag") else "")
                + f"<strong>{_e(item.get('title') or item.get('label'))}</strong>"
                + (f"<p>{_e(item.get('body'))}</p>" if item.get("body") else "")
                + "</div>"
            )
        parts.append("</div>")
    if panel.get("note"):
        parts.append(f"<p class='panel-note'>{_e(panel.get('note'))}</p>")
    parts.append("</article>")
    return "".join(parts)


def _render_whitepaper_outlook(section: Mapping[str, Any]) -> str:
    parts = [
        "<section class='whitepaper-outlook fixed-page'>",
        "<div class='eyebrow'>OUTLOOK</div>",
        f"<h2>{_e(section.get('heading'))}</h2>",
        (f"<p class='outlook-deck'>{_e(section.get('lead'))}</p>" if section.get("lead") else ""),
    ]
    if section.get("callout"):
        parts.append(f"<blockquote>{_e(section.get('callout'))}</blockquote>")
    parts.append("<div class='outlook-copy'>")
    if section.get("body"):
        parts.append(f"<p>{_e(section.get('body'))}</p>")
    for paragraph in section.get("paragraphs") or []:
        parts.append(f"<p>{_e(paragraph)}</p>")
    parts.append("</div>")
    parts.append(_render_whitepaper_footnotes(section))
    parts.append("</section>")
    return "".join(parts)


def _render_whitepaper_disclaimer(section: Mapping[str, Any]) -> str:
    parts = [
        "<section class='whitepaper-disclaimer fixed-page'>",
        "<div class='eyebrow'>IMPORTANT INFORMATION</div>",
        f"<h2>{_e(section.get('heading') or 'Disclaimer')}</h2>",
    ]
    if section.get("body"):
        parts.append(f"<p class='disclaimer-lead'>{_e(section.get('body'))}</p>")
    parts.append("<div class='disclaimer-grid'>")
    for raw in section.get("items") or []:
        item = raw if isinstance(raw, Mapping) else {}
        parts.append(
            "<article>"
            f"<h3>{_e(item.get('heading') or item.get('title'))}</h3>"
            f"<p>{_e(item.get('body'))}</p>"
            "</article>"
        )
    parts.append("</div><p class='disclaimer-foot'>Receipt and review of this report constitutes acknowledgement of the limitations and conditions stated on this page.</p></section>")
    return "".join(parts)


def _render_whitepaper_footnotes(section: Mapping[str, Any]) -> str:
    footnotes = [entry for entry in section.get("footnotes") or [] if entry]
    if not footnotes:
        return ""
    parts = ["<aside class='whitepaper-footnotes'><strong>Sources and notes</strong><ol>"]
    for entry in footnotes:
        text = str(entry)
        match = re.search(r",\s*(https://\S+)\s*$", text)
        if match:
            url = match.group(1)
            prefix = text[: match.start()].rstrip(" ,")
            host = re.sub(r"^www\.", "", url.split("/", 3)[2], flags=re.IGNORECASE)
            parts.append(f"<li>{_e(prefix)}. <a href='{_e(url)}'>{_e(host)}</a></li>")
        else:
            parts.append(f"<li>{_e(text)}</li>")
    parts.append("</ol></aside>")
    return "".join(parts)


def _whitepaper_css() -> str:
    return """
@page { size: A4 portrait; margin: 18mm 16mm 18mm 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #172238; background: #fff; font-family: Arial, "Helvetica Neue", sans-serif; font-size: 9.1pt; line-height: 1.5; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3, p, blockquote { margin-top: 0; }
h1, h2, h3 { break-after: avoid-page; }
p { orphans: 3; widows: 3; }
.fixed-page { min-height: 261mm; break-after: page; overflow: hidden; }
.eyebrow { margin-bottom: 4mm; color: #176ddc; font-size: 7pt; font-weight: 800; letter-spacing: .2em; }
.executive-summary { display: flex; height: 261mm; flex-direction: column; overflow: hidden; }
.executive-visual { margin: 0 0 5mm; }
.executive-visual img { display: block; width: 100%; height: 54mm; object-fit: cover; }
.executive-visual figcaption { margin-top: 1.5mm; color: #7b8796; font-size: 6.4pt; }
.executive-summary h1 { margin: 0 0 2.5mm; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 25pt; font-weight: 400; line-height: 1.05; }
.executive-deck { max-width: 158mm; margin-bottom: 4mm; color: #2777c9; font-size: 12.5pt; line-height: 1.34; }
.executive-copy { column-count: 2; column-gap: 8mm; column-rule: .2mm solid #dfe6ee; color: #334155; font-size: 7.75pt; line-height: 1.37; }
.executive-copy p { margin: 0 0 3.2mm; }
.executive-summary .whitepaper-footnotes { margin-top: 2.5mm; padding-top: 1.8mm; font-size: 5.6pt; line-height: 1.25; }
.executive-summary .whitepaper-footnotes ol { margin-top: 1mm; }
.whitepaper-contents { padding-top: 8mm; }
.whitepaper-contents h2 { margin: 0 0 4mm; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 31pt; font-weight: 400; }
.contents-deck { max-width: 132mm; margin-bottom: 13mm; color: #5c6b7c; font-size: 11pt; line-height: 1.5; }
.whitepaper-contents ol { margin: 0; padding: 0; list-style: none; border-top: .8mm solid #0a4e91; }
.whitepaper-contents li { display: grid; align-items: start; gap: 6mm; padding: 6mm 0; grid-template-columns: 18mm 1fr 12mm; border-bottom: .3mm solid #d2dce7; }
.contents-number { color: #1587a6; font-family: Georgia, "Times New Roman", serif; font-size: 21pt; line-height: 1; }
.whitepaper-contents strong { color: #10264a; font-family: Georgia, "Times New Roman", serif; font-size: 15pt; font-weight: 400; }
.whitepaper-contents li p { max-width: 120mm; margin: 2mm 0 0; color: #68778a; font-size: 8.5pt; }
.contents-page { color: #176ddc; font-size: 9pt; font-weight: 700; text-align: right; }
.whitepaper-chapter, .whitepaper-chapter-continuation, .whitepaper-exhibit, .whitepaper-outlook, .whitepaper-disclaimer { break-before: page; }
.chapter-opening-page { display: flex; flex-direction: column; }
.whitepaper-chapter-continuation { break-inside: avoid-page; }
.chapter-head { display: grid; gap: 5mm; margin-bottom: 4mm; padding-top: 1.5mm; grid-template-columns: 22mm 1fr; border-top: .9mm solid #0b579d; }
.chapter-number { padding-top: 4mm; color: #1587a6; font-family: Georgia, "Times New Roman", serif; font-size: 28pt; line-height: 1; }
.chapter-head h2 { max-width: 147mm; margin: 2.5mm 0 2mm; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 27pt; font-weight: 400; line-height: 1.04; }
.chapter-head p { max-width: 140mm; margin: 0; color: #2873bc; font-size: 11pt; line-height: 1.32; }
.whitepaper-chapter blockquote, .whitepaper-outlook blockquote { margin: 0 0 4mm; padding: 3mm 0 3mm 5mm; color: #0b579d; border-left: 1.2mm solid #21a0b7; font-family: Georgia, "Times New Roman", serif; font-size: 13.5pt; line-height: 1.28; }
.chapter-copy { column-count: 2; column-gap: 8mm; column-rule: .2mm solid #dfe6ee; font-size: 9.35pt; line-height: 1.52; }
.chapter-copy h3 { margin: 3.5mm 0 2mm; color: #0c4d83; font-size: 11.2pt; line-height: 1.25; break-before: avoid-page; }
.chapter-copy h3:first-child { margin-top: 0; }
.chapter-copy p { margin: 0 0 3mm; color: #344256; }
.chapter-visual { margin: 3mm 0 3mm; break-inside: avoid-page; }
.chapter-visual img { display: block; width: 100%; height: 48mm; object-fit: cover; }
.chapter-visual figcaption { display: flex; justify-content: space-between; gap: 8mm; margin-top: 1.2mm; color: #7b8796; font-size: 6pt; }
.chapter-visual figcaption strong { color: #176ddc; letter-spacing: .12em; }
.chapter-opening-page > .whitepaper-footnotes, .whitepaper-chapter-continuation > .whitepaper-footnotes { margin-top: auto; }
.chapter-continuation-head { margin-bottom: 6mm; padding-top: 3mm; border-top: .9mm solid #0b579d; }
.chapter-continuation-head span { display: block; margin-bottom: 3mm; color: #1587a6; font-size: 7pt; font-weight: 800; letter-spacing: .17em; }
.chapter-continuation-head h2 { max-width: 155mm; margin: 0; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 20pt; font-weight: 400; line-height: 1.08; }
.chapter-continuation-lead { padding-bottom: 6mm; border-bottom: .3mm solid #cfd9e3; }
.chapter-continuation-lead h3, .chapter-continuation-grid h3 { margin: 0 0 2.5mm; color: #0c4d83; font-size: 11.2pt; line-height: 1.25; }
.chapter-continuation-lead > div { display: grid; gap: 8mm; grid-template-columns: repeat(2, 1fr); }
.chapter-continuation-lead p, .chapter-continuation-grid p { margin: 0 0 3mm; color: #344256; font-size: 9.2pt; line-height: 1.48; }
.chapter-continuation-grid { display: grid; gap: 8mm; margin-top: 7mm; grid-template-columns: repeat(2, 1fr); }
.chapter-continuation-grid article { break-inside: avoid; }
.whitepaper-exhibit { padding-top: 2mm; }
.whitepaper-exhibit-head { margin-bottom: 5mm; padding-top: 3mm; border-top: .9mm solid #0b579d; }
.whitepaper-exhibit-head > span { color: #1587a6; font-size: 7pt; font-weight: 800; letter-spacing: .17em; }
.whitepaper-exhibit-head h2 { max-width: 165mm; margin: 1.5mm 0 2mm; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 22pt; font-weight: 400; line-height: 1.08; }
.whitepaper-exhibit-head p { max-width: 150mm; margin: 0; color: #5d6b7d; font-size: 10.5pt; }
.whitepaper-metrics { display: grid; gap: 0; margin: 5mm 0 6mm; border-top: .35mm solid #9eb0bf; border-bottom: .25mm solid #d3dce4; }
.whitepaper-metrics.metrics-1 { grid-template-columns: 1fr; }
.whitepaper-metrics.metrics-2 { grid-template-columns: repeat(2, 1fr); }
.whitepaper-metrics.metrics-3 { grid-template-columns: repeat(3, 1fr); }
.whitepaper-metrics.metrics-4, .whitepaper-metrics.metrics-5, .whitepaper-metrics.metrics-6 { grid-template-columns: repeat(3, 1fr); }
.whitepaper-metrics article { min-height: 23mm; padding: 3.5mm 4mm 3mm 0; background: #fff; border-right: .25mm solid #d3dce4; }
.whitepaper-metrics article + article { padding-left: 4mm; }
.whitepaper-metrics article:nth-child(3n), .whitepaper-metrics article:last-child { border-right: 0; }
.whitepaper-metrics strong { display: block; color: #0b3763; font-family: Georgia, "Times New Roman", serif; font-size: 17pt; font-weight: 400; }
.whitepaper-metrics span { display: block; margin-top: 1.5mm; color: #44566c; font-size: 8pt; }
.whitepaper-metrics small { display: block; margin-top: 2mm; color: #7b8796; font-size: 6.6pt; }
.exhibit-panels { display: grid; gap: 5mm; margin: 4mm 0; grid-template-columns: 1fr 1fr; }
.exhibit-panel { padding: 4mm 0 3mm; background: #fff; border-top: .45mm solid #a8b7c4; border-bottom: .25mm solid #dbe2e8; break-inside: avoid-page; }
.panel-wide { grid-column: 1 / -1; }
.panel-eyebrow { color: #1587a6; font-size: 6.5pt; font-weight: 800; letter-spacing: .14em; }
.exhibit-panel h3 { max-width: 148mm; margin: 1.8mm 0 2mm; color: #17395e; font-size: 11pt; line-height: 1.25; }
.panel-deck { color: #68778a; font-size: 7.5pt; }
.whitepaper-chart-svg { display: block; width: 100%; height: auto; margin-top: 3mm; overflow: visible; background: #fff; }
.chart-grid { stroke: #e0e6eb; stroke-width: .8; }
.chart-baseline { stroke: #718394; stroke-width: 1.2; }
.chart-reference { stroke: #9fb0be; stroke-width: .9; stroke-dasharray: 4 4; }
.chart-tick { fill: #6b7a89; font-family: Arial, sans-serif; font-size: 9px; }
.chart-legend { fill: #364a62; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; }
.chart-axis-label { fill: #465a72; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; }
.chart-category { fill: #30455c; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; }
.chart-point-label { fill: #213d5a; font-family: Arial, sans-serif; font-size: 9.5px; font-weight: 700; }
.chart-point-value { fill: #738494; font-family: Arial, sans-serif; font-size: 8px; }
.chart-value { fill: #17395e; font-family: Arial, sans-serif; font-size: 9px; font-weight: 700; }
.chart-value-strong { fill: #0a365e; font-size: 10px; }
.chart-segment-value { fill: #fff; font-family: Arial, sans-serif; font-size: 9px; font-weight: 800; }
.vehicle-label { fill: #102f54; font-family: Georgia, "Times New Roman", serif; font-size: 17px; }
.vehicle-dimension { fill: #40566e; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; }
.vehicle-payload { fill: #0c7790; font-family: Arial, sans-serif; font-size: 10px; font-weight: 800; }
.vehicle-status { fill: #7c8997; font-family: Arial, sans-serif; font-size: 8.5px; }
.stacked-legend { display: flex; flex-wrap: wrap; gap: 2mm 5mm; margin: 3mm 0; color: #536276; font-size: 6.6pt; }
.stacked-legend span { display: inline-flex; align-items: center; gap: 1.5mm; }
.stacked-legend i { width: 3mm; height: 3mm; }
.stacked-chart { display: grid; gap: 3.2mm; margin-top: 3mm; }
.stacked-row { display: grid; align-items: center; gap: 3mm; grid-template-columns: 31mm 1fr 16mm; }
.stacked-row label { color: #43546a; font-size: 7.2pt; }
.stacked-row > div { display: flex; height: 7mm; overflow: hidden; background: #dfe6ec; }
.stacked-row i { display: flex; align-items: center; justify-content: center; height: 100%; color: #fff; font-size: 5.8pt; font-style: normal; font-weight: 800; }
.stacked-row b { color: #0b4e7a; font-size: 7pt; text-align: right; }
.whitepaper-bars { margin-top: 4mm; }
.whitepaper-bar { display: grid; align-items: center; gap: 3mm; margin: 3mm 0; grid-template-columns: 34mm 1fr 19mm; }
.whitepaper-bar label { color: #43546a; font-size: 7.5pt; }
.whitepaper-bar > div { height: 5mm; background: #e4e9ee; }
.whitepaper-bar i { display: block; height: 100%; background: #147f91; }
.whitepaper-bar b { color: #0b4e7a; font-size: 8pt; text-align: right; }
.process-flow { display: grid; gap: 0; margin-top: 4mm; grid-template-columns: repeat(4, 1fr); }
.process-step { position: relative; min-height: 39mm; padding: 3mm; border-left: .3mm solid #c8d4df; }
.process-step:first-child { border-left: 0; }
.process-step > span { color: #1593a9; font-size: 7pt; font-weight: 800; }
.process-step strong { display: block; margin: 2mm 0; color: #17395e; font-size: 9pt; }
.process-step b { display: block; margin-bottom: 2mm; color: #b45d20; font-family: Georgia, "Times New Roman", serif; font-size: 13pt; font-weight: 400; }
.process-step p { margin: 0; color: #5d6a7b; font-size: 6.5pt; line-height: 1.35; }
.milestone-track { position: relative; display: grid; gap: 0; margin-top: 7mm; padding-top: 8mm; grid-template-columns: repeat(4, 1fr); }
.milestone-track::before { position: absolute; top: 3.6mm; right: 18mm; left: 18mm; height: .5mm; content: ""; background: linear-gradient(90deg, #0b579d, #1593a9); }
.milestone-item { position: relative; min-height: 47mm; padding: 7mm 4mm 3mm; border-left: .25mm solid #d1dbe4; }
.milestone-item:first-child { border-left: 0; }
.milestone-item > span { position: absolute; top: -8mm; left: 50%; display: flex; align-items: center; justify-content: center; width: 8mm; height: 8mm; color: #fff; background: #0b3763; border: 1.1mm solid #fff; border-radius: 50%; box-shadow: 0 0 0 .45mm #1593a9; font-size: 6.5pt; font-weight: 800; transform: translateX(-50%); }
.milestone-item small { display: block; color: #147f91; font-size: 6.3pt; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.milestone-item strong { display: block; margin: 2mm 0 3mm; color: #113c66; font-family: Georgia, "Times New Roman", serif; font-size: 17pt; font-weight: 400; }
.milestone-item p { margin: 0; color: #647386; font-size: 6.9pt; line-height: 1.4; }
.market-layer-map { position: relative; display: grid; margin-top: 5mm; padding: 10mm 0 5mm; grid-template-columns: repeat(4, 1fr); border-bottom: .35mm solid #9fb0bf; }
.market-layer-spine { position: absolute; z-index: 0; top: 5mm; right: 18mm; left: 18mm; height: .45mm; background: #9bb0c0; }
.market-layer { position: relative; z-index: 1; min-height: 57mm; padding: 0 4mm 3mm; border-left: .25mm solid #c9d4de; }
.market-layer:nth-child(2) { border-left: 0; }
.market-layer > span { display: flex; align-items: center; justify-content: center; width: 8mm; height: 8mm; margin: -9mm 0 6mm; color: #fff; background: #0c365f; border: 1.2mm solid #fff; border-radius: 50%; box-shadow: 0 0 0 .45mm #147f91; font-size: 6.8pt; font-weight: 800; }
.market-layer small { display: block; margin-bottom: 3mm; color: #147f91; font-size: 6.4pt; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.market-layer strong { display: block; min-height: 18mm; color: #12385f; font-family: Georgia, "Times New Roman", serif; font-size: 13pt; font-weight: 400; line-height: 1.16; }
.market-layer p { margin: 3mm 0 0; color: #5d6d7e; font-size: 7.1pt; line-height: 1.42; }
.comparison-table { margin-top: 4mm; border-top: .5mm solid #aab8c6; }
.comparison-table header, .comparison-table > div { display: grid; gap: 3mm; padding: 3mm 0; grid-template-columns: 1.25fr 1fr 1fr; border-bottom: .25mm solid #cbd5df; }
.comparison-table header { color: #0b4e7a; font-size: 7pt; }
.comparison-table span { color: #536276; }
.comparison-table b { color: #17395e; font-weight: 700; }
.scenario-grid, .matrix-grid { display: grid; gap: 3mm; margin-top: 4mm; grid-template-columns: repeat(2, 1fr); }
.scenario-grid > div, .matrix-grid > div { min-height: 27mm; padding: 3.5mm; background: #f4f7f9; border-left: .8mm solid #147f91; }
.scenario-grid span, .matrix-grid span { color: #68778a; font-size: 6.5pt; font-weight: 800; letter-spacing: .1em; }
.scenario-grid strong, .matrix-grid strong { display: block; margin: 2mm 0; color: #17395e; font-family: Georgia, "Times New Roman", serif; font-size: 12pt; font-weight: 400; }
.scenario-grid p, .matrix-grid p { margin: 0; color: #657487; font-size: 7pt; }
.panel-note { margin: 3mm 0 0; color: #7b8796; font-size: 6.5pt; }
.exhibit-analysis { margin-top: 6mm; column-count: 2; column-gap: 8mm; column-rule: .2mm solid #dfe6ee; }
.exhibit-analysis p { margin: 0 0 3mm; color: #3b4a5e; font-size: 8pt; }
.whitepaper-source { margin: 5mm 0 0; padding-top: 3mm; color: #778596; border-top: .25mm solid #cfd8e1; font-size: 6.7pt; }
.whitepaper-footnotes { margin-top: 2.5mm; padding-top: 1.7mm; color: #7a8795; border-top: .25mm solid #d5dde5; font-size: 5.9pt; line-height: 1.3; break-inside: auto; }
.whitepaper-footnotes strong { color: #536276; font-size: 6.3pt; letter-spacing: .08em; text-transform: uppercase; }
.whitepaper-footnotes ol { margin: 1.5mm 0 0; padding-left: 5mm; }
.whitepaper-footnotes li { margin-bottom: 1mm; }
.whitepaper-footnotes a { color: #4f718f; text-decoration: none; border-bottom: .15mm solid #9eb2c4; }
.whitepaper-exhibit > .whitepaper-footnotes { font-size: 5.4pt; line-height: 1.2; break-inside: avoid-page; }
.whitepaper-exhibit > .whitepaper-footnotes ol { margin-top: 1mm; column-count: 2; column-gap: 7mm; column-fill: balance; }
.whitepaper-exhibit > .whitepaper-footnotes li { margin-bottom: .6mm; break-inside: avoid; }
.whitepaper-outlook { padding-top: 8mm; }
.whitepaper-outlook h2 { max-width: 158mm; margin: 0 0 4mm; color: #071d43; font-family: Georgia, "Times New Roman", serif; font-size: 31pt; font-weight: 400; line-height: 1.05; }
.outlook-deck { max-width: 145mm; margin-bottom: 8mm; color: #2777c9; font-size: 12.5pt; line-height: 1.38; }
.outlook-copy { column-count: 2; column-gap: 8mm; column-rule: .2mm solid #dfe6ee; }
.outlook-copy p { margin: 0 0 3.5mm; color: #3b4a5e; }
.whitepaper-disclaimer { padding-top: 7mm; color: #687382; }
.whitepaper-disclaimer h2 { margin: 0 0 4mm; color: #26384d; font-family: Georgia, "Times New Roman", serif; font-size: 29pt; font-weight: 400; }
.disclaimer-lead { max-width: 158mm; margin-bottom: 6mm; color: #596675; font-size: 8.6pt; line-height: 1.5; }
.disclaimer-grid { display: grid; gap: 0 9mm; grid-template-columns: repeat(2, 1fr); border-top: .25mm solid #d6dde4; }
.disclaimer-grid article { min-height: 39mm; margin: 0; padding: 5mm 0 4mm; border-bottom: .25mm solid #dfe3e8; break-inside: avoid; }
.disclaimer-grid article:nth-child(odd) { padding-right: 4mm; border-right: .2mm solid #dfe3e8; }
.disclaimer-grid h3 { margin: 0 0 1.5mm; color: #435268; font-size: 8pt; text-transform: uppercase; letter-spacing: .06em; }
.disclaimer-grid p { margin: 0; color: #747f8c; font-size: 7.35pt; line-height: 1.52; }
.disclaimer-foot { margin-top: 6mm; padding-top: 3mm; color: #85909b; border-top: .25mm solid #d6dde4; font-size: 6.7pt; }
"""


def _body_css() -> str:
    return """
@page { size: A4 portrait; margin: 21mm 17mm 20mm 17mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #18233a; background: #fff; font-family: "Noto Sans CJK SC", "Noto Sans CJK TC", "Source Han Sans SC", Arial, "Helvetica Neue", sans-serif; font-size: 9.5pt; line-height: 1.52; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3, p { margin-top: 0; }
h1, h2, h3 { break-after: avoid-page; }
.page, .analysis, .actions, .notes { break-before: page; }
.opening { min-height: 244mm; display: flex; flex-direction: column; }
.eyebrow { margin-bottom: 7mm; color: #176ddc; font-size: 7pt; font-weight: 800; letter-spacing: .2em; }
.opening h1 { max-width: 162mm; margin: 0 0 7mm; color: #061b46; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 28pt; font-weight: 400; line-height: 1.08; letter-spacing: -.02em; }
.opening-summary { max-width: 150mm; margin-bottom: 8mm; color: #3b4c68; font-size: 13pt; line-height: 1.44; }
.opening-intro { max-width: 154mm; padding: 5mm 0 5mm 6mm; border-left: 1.2mm solid #2d91f2; color: #35435a; font-size: 10.5pt; }
.takeaway-grid { display: grid; margin-top: auto; gap: 0 7mm; grid-template-columns: 1fr 1fr; }
.takeaway-grid article { display: grid; gap: 3.5mm; padding: 3.5mm 0; grid-template-columns: 9mm 1fr; border-top: .35mm solid #bdd2ed; break-inside: avoid; }
.takeaway-grid article:last-child:nth-child(odd) { grid-column: 1 / -1; }
.takeaway-grid span { color: #1b75de; font-size: 8pt; font-weight: 800; letter-spacing: .12em; }
.takeaway-grid p { margin: 0; color: #172643; font-size: 9.2pt; line-height: 1.38; }
.contents h2, .notes h2 { margin: 0 0 11mm; color: #061b46; font-family: Georgia, "Times New Roman", serif; font-size: 25pt; font-weight: 400; }
.contents ol { margin: 0; padding: 0; list-style: none; border-top: 1.2mm solid #176ddc; }
.contents li { display: grid; gap: 5mm; padding: 4mm 0; grid-template-columns: 35mm 1fr; border-bottom: .3mm solid #d5e0ed; break-inside: avoid; }
.contents li span { color: #59708f; font-size: 7pt; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.contents li strong { color: #102343; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 11pt; font-weight: 400; line-height: 1.35; }
.section-head { margin-bottom: 8mm; padding-top: 4mm; border-top: 1.2mm solid #176ddc; }
.section-no { color: #1c73da; font-size: 8pt; font-weight: 800; letter-spacing: .14em; }
.analysis h2, .actions h2 { max-width: 158mm; margin: 3mm 0 4mm; color: #061b46; font-family: "Noto Serif CJK SC", "Noto Serif CJK TC", "Source Han Serif SC", Georgia, "Times New Roman", serif; font-size: 23pt; font-weight: 400; line-height: 1.12; }
.lead { margin: 0; color: #37618e; font-size: 12pt; line-height: 1.45; }
.analysis > p { margin-bottom: 4.3mm; text-align: left; orphans: 3; widows: 3; }
.evidence { margin: 7mm 0; padding: 5mm 6mm; background: #f2f7fd; border-top: 1mm solid #72b8ff; break-inside: avoid-page; }
.evidence strong { display: block; margin-bottom: 3mm; color: #0b3f83; font-size: 7pt; letter-spacing: .14em; text-transform: uppercase; }
.evidence ul { margin: 0; padding-left: 5mm; }
.evidence li { margin: 0 0 2.2mm; }
.so-what { margin-top: 7mm; padding: 5mm 6mm; color: #eff7ff; background: #082b63; break-inside: avoid-page; }
.so-what span { display: block; margin-bottom: 2mm; color: #75bdff; font-size: 7pt; font-weight: 800; letter-spacing: .15em; }
.so-what p { margin: 0; font-size: 10.5pt; line-height: 1.48; }
.exhibit { margin: 9mm 0; padding: 7mm; background: #f5f8fc; border-top: 1.2mm solid #176ddc; break-inside: avoid-page; }
.exhibit + .analysis { margin-top: 12mm; break-before: auto; }
.exhibit .section-head { margin-bottom: 5mm; padding-top: 0; border-top: 0; }
.exhibit h2 { max-width: 148mm; margin: 2.5mm 0 3mm; color: #061b46; font-family: Georgia, "Times New Roman", serif; font-size: 18pt; font-weight: 400; line-height: 1.15; }
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
.section-visual { margin: 7mm 0; break-inside: avoid-page; }
.section-visual img { display: block; width: 100%; max-height: 89mm; object-fit: cover; border: .3mm solid #cad9e9; }
.section-visual figcaption { display: flex; justify-content: space-between; gap: 8mm; margin-top: 2.5mm; color: #687b91; font-size: 7pt; }
.section-visual figcaption strong { color: #1c6fcf; font-size: 6.8pt; letter-spacing: .12em; }
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
    paragraphs = list(section.get("paragraphs") or [])
    split_at = max(1, min(2, len(paragraphs) // 2)) if paragraphs else 0
    for paragraph in paragraphs[:split_at]:
        parts.append(f"<p>{_e(paragraph)}</p>")
    visual_uri = _optional_visual_data_uri(section.get("visualPath"))
    if visual_uri:
        parts.append(
            "<figure class='section-visual'>"
            f"<img alt='{_e(section.get('visualAlt') or section.get('heading'))}' src='{visual_uri}'>"
            f"<figcaption><strong>GATEX VISUAL SYNTHESIS</strong><span>{_e(section.get('visualAlt') or section.get('heading'))}</span></figcaption>"
            "</figure>"
        )
    for paragraph in paragraphs[split_at:]:
        parts.append(f"<p>{_e(paragraph)}</p>")
    evidence = list(section.get("evidence") or [])
    if evidence:
        parts.append("<aside class='evidence'><strong>Key evidence</strong><ul>")
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
            + (f"<p><strong>Rationale:</strong> {_e(item.get('rationale'))}</p>" if item.get("rationale") else "")
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


def _assemble_pdf(cover_pdf: Path, body_pdf: Path, back_pdf: Path, output_pdf: Path, report: Mapping[str, Any]) -> None:
    cover_reader = PdfReader(str(cover_pdf))
    body_reader = PdfReader(str(body_pdf))
    back_reader = PdfReader(str(back_pdf))
    if len(cover_reader.pages) != 1:
        raise GatexPdfError("GateX cover must render as exactly one page.")
    if len(back_reader.pages) != 1:
        raise GatexPdfError("GateX back cover must render as exactly one page.")
    if not body_reader.pages:
        raise GatexPdfError("GateX report body did not render any pages.")
    total_pages = 2 + len(body_reader.pages)
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
    writer.add_page(back_reader.pages[0])
    writer.add_metadata(
        {
            "/Title": str(report.get("title") or "GateX Report"),
            "/Author": "GateX",
            "/Subject": str(report.get("reportType") or "Executive decision intelligence"),
            "/Keywords": "GateX, executive intelligence, strategic research, decision brief",
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
    surface.drawString(48, height - 29, "GATEX  |  EXECUTIVE INTELLIGENCE")
    surface.setFillColor(blue)
    surface.drawRightString(width - 48, height - 29, "CONFIDENTIAL")
    surface.setFillColor(muted)
    surface.setFont("Helvetica", 6.8)
    surface.drawString(48, 25, _ascii(classification)[:52])
    surface.drawCentredString(width / 2, 25, "GATEX.FUND")
    surface.drawRightString(width - 48, 25, f"{page_number:02d} / {total_pages:02d}")
    surface.save()
    return buffer.getvalue()


def _asset_data_uri(path: Path, media_type: str) -> str:
    if not path.is_file() or path.stat().st_size < 1_024:
        raise GatexPdfError(f"Required GateX branding asset is missing: {path.name}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _optional_cover_data_uri(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_file():
        raise GatexPdfError("The approved report cover image is missing.")
    byte_size = path.stat().st_size
    if byte_size < 1_024 or byte_size > 20 * 1024 * 1024:
        raise GatexPdfError("The approved report cover image has an invalid size.")
    suffix = path.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix)
    if not media_type:
        raise GatexPdfError("The approved report cover image uses an unsupported format.")
    try:
        pixmap = fitz.Pixmap(str(path))
    except Exception as exc:
        raise GatexPdfError("The approved report cover image cannot be decoded.") from exc
    if pixmap.width < 640 or pixmap.height < 640:
        raise GatexPdfError("The approved report cover image is too small for publication.")
    return _asset_data_uri(path, media_type)


def _optional_visual_data_uri(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_file() or not 1_024 <= path.stat().st_size <= 20 * 1024 * 1024:
        raise GatexPdfError("A report context visual is missing or has an invalid size.")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if not media_type:
        raise GatexPdfError("A report context visual uses an unsupported format.")
    try:
        pixmap = fitz.Pixmap(str(path))
    except Exception as exc:
        raise GatexPdfError("A report context visual cannot be decoded.") from exc
    if pixmap.width < 800 or pixmap.height < 500:
        raise GatexPdfError("A report context visual is too small for publication.")
    return _asset_data_uri(path, media_type)


def _action_items(value: Any) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for raw in _list(value)[:20]:
        if isinstance(raw, Mapping):
            action = _clean(raw.get("action") or raw.get("description"), 4_000)
            horizon = _clean(raw.get("horizon"), 160)
            success = _clean(raw.get("success_metric") or raw.get("successMetric"), 1_000)
            rationale = _clean(raw.get("rationale"), 2_000)
        else:
            action = _clean(raw, 4_000)
            horizon = ""
            success = ""
            rationale = ""
        if action:
            items.append({"action": action, "horizon": horizon, "successMetric": success, "rationale": rationale})
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


def _deduplicated_word_text(words: Sequence[Any]) -> str:
    accepted: List[tuple[float, float, str]] = []
    output: List[str] = []
    for raw in words:
        if not isinstance(raw, Sequence) or len(raw) < 5:
            continue
        token = _clean(raw[4], 300)
        if not token:
            continue
        try:
            x0 = float(raw[0])
            y0 = float(raw[1])
        except (TypeError, ValueError):
            output.append(token)
            continue
        key = _comparison_key(token)
        is_shadow = any(
            key == prior_key and abs(x0 - prior_x) <= 1.8 and abs(y0 - prior_y) <= 2.2
            for prior_x, prior_y, prior_key in accepted[-60:]
        )
        if is_shadow:
            continue
        accepted.append((x0, y0, key))
        output.append(token)
    return " ".join(output)


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
