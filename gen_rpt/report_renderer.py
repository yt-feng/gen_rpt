from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
REPORT_LABEL = THEME.get("report_label", "Deep Research Report")
FONT_FAMILY = THEME.get("font_family", "Trebuchet MS, Aptos, Arial, sans-serif")

CSS = f"""
:root {{ --accent:{PALETTE['accent']}; --accent2:{PALETTE.get('bright_blue', PALETTE['accent'])}; --ink:{PALETTE['ink']}; --muted:{PALETTE['subtle']}; --line:{PALETTE['line']}; --paper:{PALETTE['paper']}; --bg:{PALETTE['panel']}; --lightblue:{PALETTE.get('light_blue_fill', '#EBF5FF')}; }}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; }}
body {{ font-family:{FONT_FAMILY}; background:#fff; color:var(--ink); font-size:12.4px; line-height:1.44; }}
.page {{ width:8.27in; min-height:11.69in; margin:0 auto; background:var(--paper); position:relative; padding:0.56in 0.58in 0.56in 0.58in; page-break-after:always; overflow:hidden; }}
.content-page {{ padding-top:0.58in; }}
.cover {{ height:11.69in; padding:0; background-size:cover; background-position:center; color:white; }}
.cover::after {{ content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(5,28,44,.90) 0%, rgba(5,28,44,.68) 42%, rgba(5,28,44,.10) 100%); }}
.cover-panel {{ position:absolute; left:.55in; top:.62in; width:5.45in; min-height:3.25in; background:rgba(255,255,255,.96); color:var(--ink); padding:.32in .36in; z-index:2; border-top:.07in solid var(--accent2); }}
.cover-panel .eyebrow {{ font-size:9pt; color:var(--accent); font-weight:bold; letter-spacing:.04em; text-transform:uppercase; }}
.cover-panel h1 {{ font-size:25pt; line-height:1.10; font-weight:400; margin:.22in 0 .2in; }}
.cover-date {{ font-size:9pt; color:#555; font-weight:bold; }}
.logo-fixed {{ position:absolute; top:.22in; right:.42in; width:.72in; z-index:10; }}
.page-header {{ position:absolute; top:.22in; left:.58in; right:1.3in; height:.22in; color:#9aa0a6; font-size:6.6pt; text-transform:uppercase; letter-spacing:.04em; }}
.page-footer {{ position:absolute; bottom:.17in; left:.58in; right:.58in; display:flex; justify-content:space-between; color:#a7adb3; font-size:6.4pt; white-space:nowrap; }}
.kicker {{ color:var(--accent); font-size:8pt; font-weight:bold; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.08in; }}
h1, h2, h3 {{ margin:0; }}
h1 {{ font-size:29pt; line-height:1.08; font-weight:400; color:var(--ink); }}
h2 {{ font-size:18.4pt; line-height:1.16; font-weight:400; color:var(--ink); margin-bottom:.18in; }}
h3 {{ font-size:13pt; line-height:1.18; color:var(--accent); margin:.06in 0 .08in; }}
.lead {{ font-size:14.6pt; line-height:1.25; color:var(--accent); font-weight:400; margin:.10in 0 .18in; max-width:6.65in; }}
.two-col {{ display:grid; grid-template-columns:1.08fr .92fr; gap:.30in; }}
p {{ margin:0 0 .10in; }}
ul {{ margin:.03in 0 .06in .18in; padding:0; }}
li {{ margin-bottom:.045in; }}
.contents-list {{ margin-top:.22in; font-size:11.3pt; line-height:1.48; }}
.contents-list li {{ margin-bottom:.10in; }}
.highlight-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:.14in .18in; margin-top:.16in; }}
.highlight-card {{ border-left:3px solid var(--accent); background:#fff; padding:.08in .10in; min-height:.72in; box-shadow:0 0 0 1px var(--line); }}
.highlight-card .num {{ color:var(--accent); font-size:10pt; font-weight:bold; margin-bottom:.03in; }}
.highlight-card .text {{ color:var(--ink); font-size:9.2pt; line-height:1.25; }}
.visual {{ width:100%; max-height:7.85in; object-fit:contain; border:none; margin:.04in 0 .10in; page-break-inside:avoid; }}
.ai-visual {{ width:100%; height:2.85in; object-fit:cover; border:none; margin:.02in 0 .08in; }}
.takeaway {{ border-left:3px solid var(--accent); background:#f7fbfd; padding:.09in .12in; margin:.12in 0 0; page-break-inside:avoid; font-size:9.1pt; line-height:1.25; }}
.takeaway strong {{ color:var(--ink); display:block; margin-bottom:.03in; }}
.reference-note {{ color:var(--muted); font-size:6.5pt; border-top:1px solid var(--line); padding-top:.08in; margin-top:.12in; }}
.disclaimer-text, .small-note {{ color:var(--muted); font-size:9.5pt; line-height:1.45; max-width:6.2in; }}
.section-note {{ color:var(--muted); font-size:8.2pt; margin-top:.04in; }}
@media print {{ body {{ background:#fff; }} .page {{ margin:0; box-shadow:none; }} }}
"""

LABELS = {
    "zh": {"lang": "zh-CN", "hero": REPORT_LABEL, "topic": "选题", "prepared_by": "出品方", "summary": "执行摘要", "toc": "目录", "disclaimer": "免责声明", "takeaways": "本页要点", "charts": "关键图表", "reference_note": "本报告参考了以下机构或平台的公开研究与数据资料：", "formal_note": "完整底稿与来源备份已归档于 backup 文件夹。", "disclaimer_text": "本文件为管理咨询与研究分析材料，仅供战略讨论、行业研判与管理决策参考，不构成投资建议、证券建议、法律意见、税务意见或审计意见。"},
    "en": {"lang": "en", "hero": REPORT_LABEL, "topic": "Topic", "prepared_by": "Prepared by", "summary": "Key Highlights", "toc": "Contents", "disclaimer": "Disclaimer", "takeaways": "Takeaways", "charts": "Exhibits", "reference_note": "This report was informed by public research and data from:", "formal_note": "The full source backup is archived in the backup folder.", "disclaimer_text": "This document is a management consulting and research analysis deliverable for strategy discussion only and does not constitute investment, legal, tax, or audit advice."},
}


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def _page_header(parts: List[str], logo_path: str, page_title: str, page_no: int) -> None:
    if logo_path:
        parts.append(f"<img class='logo-fixed' src='{html.escape(logo_path)}' alt='brand logo' />")
    parts.append(f"<div class='page-header'>{html.escape(BRAND_NAME)} | CONFIDENTIAL</div>")
    parts.append(f"<div class='page-footer'><span>{html.escape(page_title)}</span><span>{page_no}</span></div>")


def _clean_summary_item(item: str) -> str:
    item = str(item or "").strip()
    if "：" in item:
        head, rest = item.split("：", 1)
        if rest.strip().startswith(head.strip()):
            item = head + "：" + rest.strip()[len(head.strip()):].lstrip("：: ，,。")
    return item


def render_report_html(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    labels = _labels(language)
    sections = report.get("sections", [])
    institutions = report.get("reference_institutions", [])
    logo_path = assets.get("brand-logo", "")
    cover_path = assets.get("cover-background", "")
    title = report.get("report_title", topic)
    page_no = 1
    parts: List[str] = ["<!DOCTYPE html>", f"<html lang='{labels['lang']}'>", "<head>", "<meta charset='utf-8' />", "<meta name='viewport' content='width=device-width, initial-scale=1' />", f"<title>{html.escape(title)}</title>", f"<style>{CSS}</style>", "</head>", "<body>"]

    parts.append(f"<section class='page cover' style=\"background-image:url('{html.escape(cover_path)}');\"><div class='cover-panel'><div class='eyebrow'>{html.escape(BRAND_NAME)}</div><div class='eyebrow'>{html.escape(labels['hero'])}</div><h1>{html.escape(title)}</h1><div class='cover-date'>{html.escape(topic)}</div></div></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, title, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['summary'])}</div><h2>The analysis points to a focused set of management priorities</h2>")
    summary = [_clean_summary_item(x) for x in report.get("executive_summary", [])[:8]]
    parts.append("<div class='highlight-grid'>")
    for idx, item in enumerate(summary, start=1):
        parts.append(f"<div class='highlight-card'><div class='num'>{idx:02d}</div><div class='text'>{html.escape(item)}</div></div>")
    parts.append("</div></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, title, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['toc'])}</div><h2>{html.escape(labels['toc'])}</h2><ol class='contents-list'>")
    for section in sections:
        parts.append(f"<li>{html.escape(section.get('title', 'Section'))}</li>")
    if report.get("charts"):
        parts.append(f"<li>{html.escape(labels['charts'])}</li>")
    parts.append("</ol></section>")
    page_no += 1

    parts.append("<section class='page content-page'>")
    _page_header(parts, logo_path, title, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['disclaimer'])}</div><h2>{html.escape(labels['disclaimer'])}</h2><p class='disclaimer-text'>{html.escape(labels['disclaimer_text'])}</p>")
    if institutions:
        parts.append(f"<p class='small-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}.</p>")
    parts.append(f"<p class='small-note'>{html.escape(labels['formal_note'])}</p></section>")
    page_no += 1

    for idx, section in enumerate(sections, start=1):
        paragraphs = section.get("paragraphs", [])
        first = paragraphs[:3]
        rest = paragraphs[3:]
        parts.append("<section class='page content-page'>")
        _page_header(parts, logo_path, title, page_no)
        parts.append(f"<h2>{html.escape(section.get('title', 'Section'))}</h2>")
        lead = section.get("lead", "")
        if lead:
            parts.append(f"<div class='lead'>{html.escape(lead)}</div>")
        parts.append("<div class='two-col'><div>")
        for p in first[:2]:
            parts.append(f"<p>{html.escape(p)}</p>")
        takeaways = section.get("key_takeaways", [])[:3]
        if takeaways:
            parts.append(f"<div class='takeaway'><strong>{html.escape(labels['takeaways'])}</strong><ul>")
            for item in takeaways:
                parts.append(f"<li>{html.escape(item)}</li>")
            parts.append("</ul></div>")
        parts.append("</div><div>")
        visual_key = section.get("visual_hint", "")
        if visual_key in assets:
            css_class = "ai-visual" if visual_key.startswith("image-") else "visual"
            parts.append(f"<img class='{css_class}' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
        for p in first[2:3]:
            parts.append(f"<p>{html.escape(p)}</p>")
        parts.append("</div></div></section>")
        page_no += 1

        if rest:
            parts.append("<section class='page content-page'>")
            _page_header(parts, logo_path, title, page_no)
            parts.append(f"<h2>{html.escape(section.get('title', 'Section'))}</h2><div class='section-note'>Additional evidence and implications</div>")
            for p in rest:
                parts.append(f"<p>{html.escape(p)}</p>")
            parts.append("</section>")
            page_no += 1

    charts = [path for key, path in assets.items() if key.startswith("chart-")]
    for idx, chart_path in enumerate(charts, start=1):
        parts.append("<section class='page content-page'>")
        _page_header(parts, logo_path, title, page_no)
        parts.append(f"<div class='kicker'>{html.escape(labels['charts'])} {idx}</div><img class='visual' src='{html.escape(chart_path)}' alt='chart' />")
        parts.append("</section>")
        page_no += 1

    if institutions:
        parts.append("<section class='page content-page'>")
        _page_header(parts, logo_path, title, page_no)
        parts.append(f"<div class='reference-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}. {html.escape(labels['formal_note'])}</div>")
        parts.append("</section>")

    parts.append("</body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_report_markdown(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    labels = _labels(language)
    institutions = report.get("reference_institutions", [])
    lines: List[str] = [f"# {report.get('report_title', topic)}", "", f"**{labels['prepared_by']}**: {BRAND_NAME}", "", f"**{labels['topic']}**: {topic}", ""]
    lines.extend([f"## {labels['summary']}", ""])
    for item in report.get("executive_summary", []):
        lines.append(f"- {_clean_summary_item(item)}")
    lines.extend(["", f"## {labels['toc']}", ""])
    for section in report.get("sections", []):
        lines.append(f"- {section.get('title', 'Section')}")
    lines.extend(["", f"## {labels['disclaimer']}", "", labels['disclaimer_text'], ""])
    for section in report.get("sections", []):
        lines.extend([f"## {section.get('title', 'Section')}", ""])
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
