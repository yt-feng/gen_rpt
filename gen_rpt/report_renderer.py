from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
REPORT_LABEL = THEME.get("report_label", "Deep Research Report")
FONT_FAMILY = THEME.get("font_family", "Trebuchet MS, Aptos, Arial, sans-serif")

PAGE_FORMAT = os.getenv("REPORT_PAGE_FORMAT", "B5").upper()
if PAGE_FORMAT == "A4":
    PAGE_W, PAGE_H = 8.27, 11.69
    PAD_X, PAD_TOP, PAD_BOTTOM = 0.46, 0.44, 0.34
    BASE_FONT = 12.0
    H2_SIZE = 17.8
    LEAD_SIZE = 13.8
    CHART_MAX = 4.10
else:
    PAGE_W, PAGE_H = 6.93, 9.84
    PAD_X, PAD_TOP, PAD_BOTTOM = 0.30, 0.30, 0.24
    BASE_FONT = 10.4
    H2_SIZE = 14.9
    LEAD_SIZE = 11.5
    CHART_MAX = 3.34

CONTENT_W = PAGE_W - 2 * PAD_X
COVER_PANEL_W = min(4.75, CONTENT_W - 0.15)

CSS = f"""
@page {{ size:{PAGE_W}in {PAGE_H}in; margin:0; }}
:root {{ --accent:{PALETTE['accent']}; --accent2:{PALETTE.get('bright_blue', PALETTE['accent'])}; --ink:{PALETTE['ink']}; --muted:{PALETTE['subtle']}; --line:{PALETTE['line']}; --paper:{PALETTE['paper']}; --bg:{PALETTE['panel']}; --lightblue:{PALETTE.get('light_blue_fill', '#EBF5FF')}; }}
* {{ box-sizing:border-box; }}
html, body {{ width:{PAGE_W}in; margin:0; padding:0; background:#fff; }}
body {{ font-family:{FONT_FAMILY}; color:var(--ink); font-size:{BASE_FONT}px; line-height:1.36; }}
.page {{ width:{PAGE_W}in; height:{PAGE_H}in; margin:0; background:var(--paper); position:relative; padding:{PAD_TOP}in {PAD_X}in {PAD_BOTTOM}in {PAD_X}in; page-break-after:always; overflow:hidden; }}
.content-page {{ padding-top:{PAD_TOP}in; }}
.cover {{ padding:0; background-size:cover; background-position:center; color:white; }}
.cover::after {{ content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(5,28,44,.90) 0%, rgba(5,28,44,.62) 46%, rgba(5,28,44,.04) 100%); }}
.cover-panel {{ position:absolute; left:.36in; top:.48in; width:{COVER_PANEL_W:.2f}in; min-height:2.58in; background:rgba(255,255,255,.96); color:var(--ink); padding:.22in .25in; z-index:2; border-top:.05in solid var(--accent2); }}
.cover-panel .eyebrow {{ font-size:7.2pt; color:var(--accent); font-weight:bold; letter-spacing:.04em; text-transform:uppercase; }}
.cover-panel h1 {{ font-size:{20 if PAGE_FORMAT != 'A4' else 23}pt; line-height:1.08; font-weight:400; margin:.14in 0 .12in; }}
.cover-date {{ font-size:7.3pt; color:#555; font-weight:bold; }}
.logo-fixed {{ position:absolute; top:.12in; right:.28in; width:.50in; z-index:10; }}
.page-header {{ position:absolute; top:.12in; left:{PAD_X}in; right:.92in; height:.15in; color:#9aa0a6; font-size:5.3pt; text-transform:uppercase; letter-spacing:.04em; }}
.page-footer {{ position:absolute; bottom:.09in; left:{PAD_X}in; right:{PAD_X}in; display:flex; justify-content:space-between; color:#a7adb3; font-size:5.2pt; white-space:nowrap; }}
.kicker {{ color:var(--accent); font-size:6.4pt; font-weight:bold; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.045in; }}
h1, h2, h3 {{ margin:0; }}
h2 {{ font-size:{H2_SIZE}pt; line-height:1.12; font-weight:400; color:var(--ink); margin-bottom:.10in; }}
h3 {{ font-size:10.2pt; line-height:1.14; color:var(--accent); margin:.035in 0 .05in; }}
.lead {{ font-size:{LEAD_SIZE}pt; line-height:1.18; color:var(--accent); font-weight:400; margin:.055in 0 .10in; max-width:{CONTENT_W:.2f}in; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:.18in; }}
.text-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:.16in; }}
.opening-grid {{ display:grid; grid-template-columns:.86fr 1.14fr; gap:.18in; align-items:start; }}
p {{ margin:0 0 .055in; }}
ul {{ margin:.01in 0 .035in .12in; padding:0; }}
li {{ margin-bottom:.025in; }}
.contents-list {{ margin-top:.13in; font-size:8.7pt; line-height:1.32; }}
.contents-list li {{ margin-bottom:.048in; }}
.highlight-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:.075in .10in; margin-top:.10in; }}
.highlight-card {{ border-left:3px solid var(--accent); background:#fff; padding:.050in .065in; min-height:.47in; box-shadow:0 0 0 1px var(--line); }}
.highlight-card .num {{ color:var(--accent); font-size:7.5pt; font-weight:bold; margin-bottom:.015in; }}
.highlight-card .text {{ color:var(--ink); font-size:7.0pt; line-height:1.18; }}
.hero-image {{ width:100%; height:2.70in; object-fit:cover; display:block; margin:.02in 0 .08in; }}
.opening-image {{ width:100%; height:4.95in; object-fit:cover; display:block; }}
.chart-inline {{ width:100%; max-height:{CHART_MAX}in; object-fit:contain; border:none; margin:.045in 0 .055in; page-break-inside:avoid; }}
.takeaway {{ border-left:3px solid var(--accent); background:#f7fbfd; padding:.055in .075in; margin:.070in 0 .06in; page-break-inside:avoid; font-size:7.0pt; line-height:1.18; }}
.takeaway strong {{ color:var(--ink); display:block; margin-bottom:.018in; }}
.reference-note {{ color:var(--muted); font-size:6.0pt; border-top:1px solid var(--line); padding-top:.060in; margin-top:.09in; }}
.disclaimer-text, .small-note {{ color:var(--muted); font-size:7.8pt; line-height:1.34; max-width:{CONTENT_W:.2f}in; }}
.section-note {{ color:var(--muted); font-size:6.8pt; margin:.018in 0 .05in; }}
@media print {{ html, body {{ width:{PAGE_W}in; }} .page {{ margin:0; box-shadow:none; }} }}
"""

LABELS = {
    "zh": {"lang": "zh-CN", "hero": REPORT_LABEL, "topic": "选题", "prepared_by": "出品方", "summary": "执行摘要", "toc": "目录", "disclaimer": "免责声明", "takeaways": "本页要点", "charts": "Exhibit", "reference_note": "本报告参考了以下机构或平台的公开研究与数据资料：", "formal_note": "完整底稿与来源备份已归档于 backup 文件夹。", "disclaimer_text": "本文件为管理咨询与研究分析材料，仅供战略讨论、行业研判与管理决策参考，不构成投资建议、证券建议、法律意见、税务意见或审计意见。"},
    "en": {"lang": "en", "hero": REPORT_LABEL, "topic": "Topic", "prepared_by": "Prepared by", "summary": "Key Highlights", "toc": "Contents", "disclaimer": "Disclaimer", "takeaways": "Takeaways", "charts": "Exhibit", "reference_note": "This report was informed by public research and data from:", "formal_note": "The full source backup is archived in the backup folder.", "disclaimer_text": "This document is a management consulting and research analysis deliverable for strategy discussion only and does not constitute investment, legal, tax, or audit advice."},
}


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def _page_header(parts: List[str], logo_path: str, page_no: int) -> None:
    if logo_path:
        parts.append(f"<img class='logo-fixed' src='{html.escape(logo_path)}' alt='brand logo' />")
    parts.append(f"<div class='page-header'>{html.escape(BRAND_NAME)} | CONFIDENTIAL</div>")
    parts.append(f"<div class='page-footer'><span>{html.escape(BRAND_NAME)} | Confidential</span><span>{page_no}</span></div>")


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", str(text or "")).strip()


def _clean_summary_item(item: str) -> str:
    item = str(item or "").strip()
    if "：" in item:
        head, rest = item.split("：", 1)
        if rest.strip().startswith(head.strip()):
            item = head + "：" + rest.strip()[len(head.strip()):].lstrip("：: ，,。")
    return item


def _chart_keys(assets: Dict[str, str]) -> List[str]:
    def sort_key(k: str) -> Tuple[int, str]:
        try:
            return int(k.split("-", 1)[1]), k
        except Exception:
            return 999, k
    return sorted([k for k in assets if k.startswith("chart-")], key=sort_key)


def _resolve_visual(section: Dict, section_idx: int, assets: Dict[str, str], chart_ids: List[str], used_charts: set[str]) -> str:
    hint = str(section.get("visual_hint", "") or "")
    if hint in assets:
        return hint
    image_key = f"image-{section_idx}"
    if image_key in assets:
        return image_key
    for chart_key in chart_ids:
        if chart_key not in used_charts:
            return chart_key
    return ""


def render_report_html(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    sections = report.get("sections", [])
    institutions = report.get("reference_institutions", [])
    logo_path = assets.get("brand-logo", "")
    cover_path = assets.get("cover-background", "")
    title = report.get("report_title", topic)
    page_no = 1
    chart_ids = _chart_keys(assets)
    used_charts: set[str] = set()

    parts: List[str] = ["<!DOCTYPE html>", f"<html lang='{labels['lang']}'>", "<head>", "<meta charset='utf-8' />", "<meta name='viewport' content='width=device-width, initial-scale=1' />", f"<title>{html.escape(title)}</title>", f"<style>{CSS}</style>", "</head>", "<body>"]

    parts.append(f"<section class='page cover' style=\"background-image:url('{html.escape(cover_path)}');\"><div class='cover-panel'><div class='eyebrow'>{html.escape(BRAND_NAME)}</div><div class='eyebrow'>{html.escape(labels['hero'])}</div><h1>{html.escape(title)}</h1><div class='cover-date'>{html.escape(topic)}</div></div></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['summary'])}</div><h2>The analysis points to a focused set of management priorities</h2>")
    summary = [_clean_summary_item(x) for x in report.get("executive_summary", [])[:8]]
    parts.append("<div class='highlight-grid'>")
    for idx, item in enumerate(summary, start=1):
        parts.append(f"<div class='highlight-card'><div class='num'>{idx:02d}</div><div class='text'>{html.escape(item)}</div></div>")
    parts.append("</div></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['toc'])}</div><h2>{html.escape(labels['toc'])}</h2><ol class='contents-list'>")
    for section in sections:
        parts.append(f"<li>{html.escape(_strip_number_prefix(section.get('title', 'Section')))}</li>")
    parts.append("</ol></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['disclaimer'])}</div><h2>{html.escape(labels['disclaimer'])}</h2><p class='disclaimer-text'>{html.escape(labels['disclaimer_text'])}</p>")
    if institutions:
        parts.append(f"<p class='small-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}.</p>")
    parts.append(f"<p class='small-note'>{html.escape(labels['formal_note'])}</p></section>")
    page_no += 1

    for idx, section in enumerate(sections, start=1):
        paragraphs = list(section.get("paragraphs", []) or [])
        visual_key = _resolve_visual(section, idx, assets, chart_ids, used_charts)
        is_chart = visual_key.startswith("chart-")
        if is_chart:
            used_charts.add(visual_key)
        title_text = _strip_number_prefix(section.get("title", "Section"))
        lead = section.get("lead", "")
        takeaways = section.get("key_takeaways", [])[:3]

        parts.append("<section class='page content-page'>")
        _page_header(parts, logo_path, page_no)
        if visual_key.startswith("image-") and visual_key in assets:
            parts.append(f"<div class='opening-grid'><div><h2>{html.escape(title_text)}</h2>")
            if lead:
                parts.append(f"<div class='lead'>{html.escape(lead)}</div>")
            for p in paragraphs[:2]:
                parts.append(f"<p>{html.escape(p)}</p>")
            if takeaways:
                parts.append(f"<div class='takeaway'><strong>{html.escape(labels['takeaways'])}</strong><ul>")
                for item in takeaways:
                    parts.append(f"<li>{html.escape(item)}</li>")
                parts.append("</ul></div>")
            parts.append(f"</div><img class='opening-image' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' /></div>")
            for p in paragraphs[2:4]:
                parts.append(f"<p>{html.escape(p)}</p>")
        else:
            parts.append(f"<h2>{html.escape(title_text)}</h2>")
            if lead:
                parts.append(f"<div class='lead'>{html.escape(lead)}</div>")
            parts.append("<div class='text-grid'><div>")
            for p in paragraphs[:2]:
                parts.append(f"<p>{html.escape(p)}</p>")
            if takeaways:
                parts.append(f"<div class='takeaway'><strong>{html.escape(labels['takeaways'])}</strong><ul>")
                for item in takeaways:
                    parts.append(f"<li>{html.escape(item)}</li>")
                parts.append("</ul></div>")
            parts.append("</div><div>")
            for p in paragraphs[2:4]:
                parts.append(f"<p>{html.escape(p)}</p>")
            parts.append("</div></div>")
            if is_chart and visual_key in assets:
                parts.append(f"<img class='chart-inline' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
        parts.append("</section>")
        page_no += 1

    if institutions:
        parts.append("<section class='page content-page'>")
        _page_header(parts, logo_path, page_no)
        parts.append(f"<div class='reference-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}. {html.escape(labels['formal_note'])}</div>")
        parts.append("</section>")

    parts.append("</body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_report_markdown(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    institutions = report.get("reference_institutions", [])
    lines: List[str] = [f"# {report.get('report_title', topic)}", "", f"**{labels['prepared_by']}**: {BRAND_NAME}", "", f"**{labels['topic']}**: {topic}", ""]
    lines.extend([f"## {labels['summary']}", ""])
    for item in report.get("executive_summary", []):
        lines.append(f"- {_clean_summary_item(item)}")
    lines.extend(["", f"## {labels['toc']}", ""])
    for section in report.get("sections", []):
        lines.append(f"- {_strip_number_prefix(section.get('title', 'Section'))}")
    lines.extend(["", f"## {labels['disclaimer']}", "", labels['disclaimer_text'], ""])
    for section in report.get("sections", []):
        lines.extend([f"## {_strip_number_prefix(section.get('title', 'Section'))}", ""])
        if section.get("lead"):
            lines.extend([f"> {section.get('lead')}", ""])
        for paragraph in section.get("paragraphs", []):
            lines.extend([paragraph, ""])
        if section.get("key_takeaways"):
            lines.extend([f"**{labels['takeaways']}**", ""])
            for item in section.get("key_takeaways", []):
                lines.append(f"- {item}")
            lines.append("")
    if institutions:
        lines.extend([f"> {labels['reference_note']} {', '.join(institutions)}.", "", f"> {labels['formal_note']}", ""])
    output_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_file
