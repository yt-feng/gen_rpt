from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
REPORT_LABEL = THEME.get("report_label", "Deep Research Report")
FONT_FAMILY = THEME.get("font_family", "Trebuchet MS, Aptos, Arial, sans-serif")
PAGE_FORMAT = os.getenv("REPORT_PAGE_FORMAT", "A4").upper()
PAGE_W, PAGE_H = (8.27, 11.69) if PAGE_FORMAT == "A4" else (6.93, 9.84)
PAD_X, PAD_TOP, PAD_BOTTOM = (0.42, 0.38, 0.30) if PAGE_FORMAT == "A4" else (0.30, 0.30, 0.24)

CSS = f"""
@page {{ size:{PAGE_W}in {PAGE_H}in; margin:0; }}
:root {{ --accent:{PALETTE['accent']}; --accent2:{PALETTE.get('bright_blue', PALETTE['accent'])}; --ink:{PALETTE['ink']}; --muted:{PALETTE['subtle']}; --line:{PALETTE['line']}; --paper:{PALETTE['paper']}; --panel:{PALETTE['panel']}; }}
* {{ box-sizing:border-box; }}
html, body {{ width:{PAGE_W}in; margin:0; padding:0; background:#fff; }}
body {{ font-family:{FONT_FAMILY}; color:var(--ink); font-size:10.6pt; line-height:1.34; }}
.page {{ width:{PAGE_W}in; height:{PAGE_H}in; margin:0; background:var(--paper); position:relative; padding:{PAD_TOP}in {PAD_X}in {PAD_BOTTOM}in {PAD_X}in; page-break-after:always; overflow:hidden; }}
.cover {{ padding:0; background-size:cover; background-position:center; }}
.cover::after {{ content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(5,28,44,.88), rgba(5,28,44,.52), rgba(5,28,44,.06)); }}
.cover-panel {{ position:absolute; left:.48in; top:.62in; width:5.25in; background:rgba(255,255,255,.95); color:var(--ink); padding:.24in .28in; z-index:2; border-top:.055in solid var(--accent2); }}
.cover-panel .eyebrow {{ font-size:7pt; color:var(--accent); font-weight:bold; letter-spacing:.06em; text-transform:uppercase; }}
.cover-panel h1 {{ font-size:22pt; line-height:1.07; font-weight:400; margin:.14in 0 .13in; }}
.cover-date {{ font-size:7.6pt; color:#5f666e; font-weight:bold; }}
.logo-fixed {{ position:absolute; top:.12in; right:.28in; width:.50in; z-index:10; }}
.page-header {{ position:absolute; top:.12in; left:{PAD_X}in; right:.92in; color:#98A1AA; font-size:5.4pt; text-transform:uppercase; letter-spacing:.05em; }}
.page-footer {{ position:absolute; bottom:.09in; left:{PAD_X}in; right:{PAD_X}in; display:flex; justify-content:space-between; color:#A8B0B8; font-size:5.4pt; }}
.kicker {{ color:var(--accent); font-size:6.6pt; font-weight:bold; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.05in; }}
h1, h2, h3 {{ margin:0; }} h2 {{ font-size:17pt; line-height:1.12; font-weight:400; color:var(--ink); margin-bottom:.10in; }}
.lead {{ font-size:12pt; line-height:1.20; color:var(--accent); font-weight:400; margin:.045in 0 .10in; }}
p {{ margin:0 0 .062in; }} ul, ol {{ margin:.02in 0 .04in .16in; padding:0; }} li {{ margin-bottom:.03in; }}
.contents-list {{ margin-top:.15in; font-size:9.2pt; line-height:1.35; }} .contents-list li {{ margin-bottom:.055in; }}
.highlight-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:.08in .11in; margin-top:.10in; }}
.highlight-card {{ border-left:3px solid var(--accent); background:#fff; padding:.06in .075in; min-height:.55in; box-shadow:0 0 0 1px var(--line); }}
.highlight-card .num {{ color:var(--accent); font-size:7.8pt; font-weight:bold; margin-bottom:.016in; }} .highlight-card .text {{ color:var(--ink); font-size:7.5pt; line-height:1.20; }}
.section-grid {{ display:grid; grid-template-columns:1.02fr .98fr; gap:.20in; align-items:start; }}
.section-visual {{ width:100%; height:3.95in; object-fit:cover; display:block; border:0; }} .chart-inline {{ width:100%; max-height:3.85in; object-fit:contain; border:none; margin:.06in 0; }}
.placeholder {{ height:3.95in; background:linear-gradient(135deg,#F5F9FC,#E6F1FA); border:1px solid var(--line); position:relative; }}
.placeholder::before {{ content:""; position:absolute; left:.25in; right:.25in; top:1.6in; height:.035in; background:var(--accent2); transform:rotate(-14deg); }}
.placeholder::after {{ content:"Strategic visual"; position:absolute; left:.25in; bottom:.25in; color:var(--muted); font-size:7pt; }}
.takeaway {{ border-left:3px solid var(--accent); background:#F6FAFD; padding:.065in .085in; margin:.075in 0 .06in; page-break-inside:avoid; font-size:7.3pt; line-height:1.20; }} .takeaway strong {{ display:block; margin-bottom:.02in; }}
.reference-note, .disclaimer-text, .small-note {{ color:var(--muted); font-size:7.8pt; line-height:1.36; }} .reference-note {{ border-top:1px solid var(--line); padding-top:.07in; }}
@media print {{ html, body {{ width:{PAGE_W}in; }} .page {{ margin:0; box-shadow:none; }} }}
"""

LABELS = {
    "en": {"lang": "en", "summary": "Key highlights", "toc": "Contents", "disclaimer": "Disclaimer", "takeaways": "Takeaways", "reference_note": "This report was informed by public research and data from:", "formal_note": "The full source backup is archived in the backup folder.", "disclaimer_text": "This document is a management consulting and research analysis deliverable for strategy discussion only. It is not professional advisory guidance."},
    "zh": {"lang": "zh-CN", "summary": "Key highlights", "toc": "Contents", "disclaimer": "Disclaimer", "takeaways": "Takeaways", "reference_note": "Reference institutions:", "formal_note": "Source backup is archived in the backup folder.", "disclaimer_text": "This document is for management research and strategy discussion only."},
}


def render_report_html(report: Dict[str, Any], assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    sections = _safe_sections(report.get("sections", []))
    institutions = report.get("reference_institutions", []) or []
    logo_path = assets.get("brand-logo", "")
    cover_path = assets.get("cover-background", "")
    title = str(report.get("report_title") or topic)
    page_no = 1
    parts: List[str] = ["<!DOCTYPE html>", f"<html lang='{labels['lang']}'>", "<head>", "<meta charset='utf-8' />", f"<title>{html.escape(title)}</title>", f"<style>{CSS}</style>", "</head>", "<body>"]

    bg = f"background-image:url('{html.escape(cover_path)}');" if cover_path else "background:#051C2C;"
    parts.append(f"<section class='page cover' style=\"{bg}\"><div class='cover-panel'><div class='eyebrow'>{html.escape(BRAND_NAME)}</div><div class='eyebrow'>{html.escape(REPORT_LABEL)}</div><h1>{html.escape(title)}</h1><div class='cover-date'>{html.escape(topic)}</div></div></section>")
    page_no += 1

    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{labels['summary']}</div><h2>The report opens with decision-relevant conclusions</h2><div class='highlight-grid'>")
    summary = [_clean_summary_item(x) for x in (report.get("executive_summary", []) or [])[:8]] or ["Evidence should be translated into a focused management agenda."]
    for idx, item in enumerate(summary[:8], start=1):
        parts.append(f"<div class='highlight-card'><div class='num'>{idx:02d}</div><div class='text'>{html.escape(_shorten(item, 210))}</div></div>")
    parts.append("</div></section>")
    page_no += 1

    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{labels['toc']}</div><h2>{labels['toc']}</h2><ol class='contents-list'>")
    for section in sections:
        parts.append(f"<li>{html.escape(_strip_number_prefix(section.get('title', 'Section')))}</li>")
    parts.append("</ol></section>")
    page_no += 1

    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{labels['disclaimer']}</div><h2>{labels['disclaimer']}</h2><p class='disclaimer-text'>{html.escape(labels['disclaimer_text'])}</p>")
    if institutions:
        parts.append(f"<p class='small-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(str(x) for x in institutions))}.</p>")
    parts.append(f"<p class='small-note'>{html.escape(labels['formal_note'])}</p></section>")
    page_no += 1

    for idx, section in enumerate(sections, start=1):
        parts.append("<section class='page'>")
        _page_header(parts, logo_path, page_no)
        title_text = _strip_number_prefix(section.get("title", f"Section {idx}"))
        lead = str(section.get("lead", ""))
        paragraphs = [str(p) for p in (section.get("paragraphs", []) or [])]
        while len(paragraphs) < 3:
            paragraphs.append("Evidence should be validated against the source backup and translated into management implications.")
        takeaways = [str(x) for x in (section.get("key_takeaways", []) or [])[:3]]
        visual = _resolve_visual(section, idx, assets)
        parts.append(f"<div class='kicker'>Chapter {idx}</div><h2>{html.escape(title_text)}</h2>")
        if lead:
            parts.append(f"<div class='lead'>{html.escape(_shorten(lead, 260))}</div>")
        parts.append("<div class='section-grid'><div>")
        for p in paragraphs[:2]:
            parts.append(f"<p>{html.escape(_shorten(p, 650))}</p>")
        if takeaways:
            parts.append(f"<div class='takeaway'><strong>{html.escape(labels['takeaways'])}</strong><ul>")
            for item in takeaways:
                parts.append(f"<li>{html.escape(_shorten(item, 150))}</li>")
            parts.append("</ul></div>")
        parts.append("</div><div>")
        if visual:
            cls = "chart-inline" if visual.startswith("chart-") else "section-visual"
            parts.append(f"<img class='{cls}' src='{html.escape(assets[visual])}' alt='{html.escape(visual)}' />")
        else:
            parts.append("<div class='placeholder'></div>")
        parts.append("</div></div>")
        for p in paragraphs[2:4]:
            parts.append(f"<p>{html.escape(_shorten(p, 520))}</p>")
        parts.append("</section>")
        page_no += 1

    if institutions:
        parts.append("<section class='page'>")
        _page_header(parts, logo_path, page_no)
        parts.append(f"<div class='reference-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(str(x) for x in institutions))}. {html.escape(labels['formal_note'])}</div>")
        parts.append("</section>")
    parts.append("</body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_report_markdown(report: Dict[str, Any], assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    sections = _safe_sections(report.get("sections", []))
    lines: List[str] = [f"# {report.get('report_title', topic)}", "", f"**Prepared by**: {BRAND_NAME}", "", f"**Topic**: {topic}", "", f"## {labels['summary']}", ""]
    for item in report.get("executive_summary", []) or []:
        lines.append(f"- {_clean_summary_item(item)}")
    lines.extend(["", f"## {labels['toc']}", ""])
    for section in sections:
        lines.append(f"- {_strip_number_prefix(section.get('title', 'Section'))}")
    lines.extend(["", f"## {labels['disclaimer']}", "", labels['disclaimer_text'], ""])
    for section in sections:
        lines.extend([f"## {_strip_number_prefix(section.get('title', 'Section'))}", ""])
        if section.get("lead"):
            lines.extend([f"> {section.get('lead')}", ""])
        for paragraph in section.get("paragraphs", []):
            lines.extend([str(paragraph), ""])
    output_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_file


def _page_header(parts: List[str], logo_path: str, page_no: int) -> None:
    if logo_path and not logo_path.lower().endswith(".svg"):
        parts.append(f"<img class='logo-fixed' src='{html.escape(logo_path)}' alt='brand logo' />")
    parts.append(f"<div class='page-header'>{html.escape(BRAND_NAME)} | CONFIDENTIAL</div>")
    parts.append(f"<div class='page-footer'><span>{html.escape(BRAND_NAME)} | Confidential</span><span>{page_no}</span></div>")


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def _resolve_visual(section: Dict[str, Any], idx: int, assets: Dict[str, str]) -> str:
    for key in [f"image-{idx}", str(section.get("visual_hint", "")), f"chart-{idx}", "cover-background"]:
        if key and key in assets:
            return key
    return ""


def _safe_sections(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list) and value:
        return [x if isinstance(x, dict) else {"title": str(x), "paragraphs": [str(x)]} for x in value]
    return [{"title": "Executive priorities and implications", "lead": "The analysis should be translated into a concise management agenda.", "paragraphs": ["The available evidence should be organized around decision quality, execution risk and near-term management implications.", "The most useful output is a short list of actions that can be tested against public evidence and client constraints.", "Follow-up work should validate the assumptions against the source backup."], "key_takeaways": ["Focus on actionability."], "visual_hint": "image-1"}]


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", str(text or "")).strip()


def _clean_summary_item(item: str) -> str:
    return " ".join(str(item or "").split())


def _shorten(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."
