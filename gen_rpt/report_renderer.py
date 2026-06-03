from __future__ import annotations

import html
import os
import re
import unicodedata
from datetime import date
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
PAD_X, PAD_TOP, PAD_BOTTOM = (0.46, 0.42, 0.42) if PAGE_FORMAT == "A4" else (0.34, 0.34, 0.34)

CSS = f"""
@page {{ size:{PAGE_W}in {PAGE_H}in; margin:0; }}
:root {{ --accent:#00A651; --accent2:#0E6B72; --navy:#1B2A34; --ink:#24323A; --muted:#7C878E; --line:#D9E1E6; --paper:#FFFFFF; --panel:#F2F6F4; --brand:{PALETTE.get('accent', '#003087')}; }}
* {{ box-sizing:border-box; }}
html, body {{ width:{PAGE_W}in; margin:0; padding:0; background:#fff; }}
body {{ font-family:{FONT_FAMILY}; color:var(--ink); font-size:10.2pt; line-height:1.36; }}
.page {{ width:{PAGE_W}in; height:{PAGE_H}in; margin:0; background:var(--paper); position:relative; padding:{PAD_TOP}in {PAD_X}in {PAD_BOTTOM}in {PAD_X}in; page-break-after:always; overflow:hidden; }}
.cover {{ padding:0; background-size:cover; background-position:center; }}
.cover::after {{ content:""; position:absolute; inset:0; background:rgba(27,42,52,.10); }}
.cover-panel {{ position:absolute; left:.63in; top:.72in; width:4.95in; min-height:3.20in; background:rgba(255,255,255,.96); padding:.28in .32in .26in; z-index:2; border-top:.06in solid var(--accent); }}
.cover-panel .eyebrow {{ font-size:7pt; color:var(--muted); font-weight:bold; letter-spacing:.08em; text-transform:uppercase; }}
.cover-panel h1 {{ font-size:23pt; line-height:1.10; font-weight:400; color:var(--navy); margin:.18in 0 .20in; }}
.cover-date {{ font-size:7.8pt; line-height:1.35; color:var(--ink); font-weight:bold; }}
.cover-brand {{ position:absolute; right:.45in; bottom:.34in; z-index:2; color:#fff; font-size:18pt; font-weight:bold; letter-spacing:.01em; }}
.logo-fixed {{ position:absolute; top:.14in; right:.28in; width:.48in; z-index:10; }}
.page-header {{ position:absolute; top:.14in; left:{PAD_X}in; right:.92in; color:#9BA4AA; font-size:7pt; text-transform:uppercase; letter-spacing:.06em; }}
.page-footer {{ position:absolute; bottom:.10in; left:{PAD_X}in; right:{PAD_X}in; display:flex; justify-content:space-between; color:#A9B0B5; font-size:7pt; }}
.kicker {{ color:var(--accent2); font-size:6.5pt; font-weight:bold; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.045in; }}
h1, h2, h3 {{ margin:0; letter-spacing:0; }}
h2 {{ font-size:20pt; line-height:1.12; font-weight:400; color:var(--navy); margin-bottom:.10in; }}
h3 {{ font-size:12.2pt; line-height:1.18; font-weight:bold; color:var(--navy); margin-bottom:.055in; }}
p {{ margin:0 0 .068in; }}
ul, ol {{ margin:.02in 0 .05in .17in; padding:0; }}
li {{ margin-bottom:.034in; }}
.contents-table {{ width:100%; border-collapse:collapse; margin-top:.10in; }}
.contents-table td {{ vertical-align:top; padding:.030in 0; }}
.contents-page {{ width:.52in; color:var(--accent); font-weight:bold; font-size:9.5pt; }}
.contents-title {{ color:var(--ink); font-size:10.5pt; line-height:1.22; }}
.contents-sub {{ color:var(--muted); font-size:7.1pt; line-height:1.18; padding-top:.010in; }}
.opening-visual, .chapter-visual {{ width:100%; height:1.72in; object-fit:cover; display:block; margin-bottom:.18in; }}
.opening-title {{ font-size:22pt; line-height:1.12; color:var(--navy); font-weight:400; margin-bottom:.05in; }}
.lead {{ font-size:12.1pt; line-height:1.22; color:var(--accent2); font-weight:400; margin:.045in 0 .12in; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:.28in; align-items:start; }}
.two-col p {{ font-size:9.4pt; line-height:1.34; }}
.side-note {{ border-left:.035in solid var(--accent); padding-left:.10in; color:var(--ink); font-size:8.0pt; line-height:1.24; }}
.side-note b {{ color:var(--accent2); display:block; font-size:6.6pt; letter-spacing:.07em; text-transform:uppercase; margin-bottom:.03in; }}
.chapter-grid {{ display:grid; grid-template-columns:.54fr .46fr; gap:.25in; align-items:start; }}
.chapter-grid.reverse {{ grid-template-columns:.48fr .52fr; }}
.chapter-grid.text-grid {{ grid-template-columns:1fr 1fr; }}
.body-copy p {{ font-size:9.15pt; line-height:1.34; }}
.section-visual {{ width:100%; height:3.55in; object-fit:cover; display:block; }}
.takeaway {{ border-left:.035in solid var(--accent); background:var(--panel); padding:.075in .095in; margin:.085in 0 .065in; page-break-inside:avoid; font-size:7.4pt; line-height:1.22; }}
.takeaway strong {{ display:block; color:var(--accent2); font-size:6.4pt; letter-spacing:.06em; text-transform:uppercase; margin-bottom:.025in; }}
.exhibit-img {{ width:100%; height:6.82in; object-fit:contain; display:block; margin:.12in 0 .08in; }}
.figure-note {{ color:var(--muted); font-size:7.4pt; line-height:1.28; }}
.scenario-box {{ border-top:.055in solid var(--accent); background:#fff; box-shadow:0 0 0 1px var(--line); padding:.18in .20in; min-height:3.9in; }}
.scenario-box p {{ font-size:9.2pt; line-height:1.35; }}
.scenario-label {{ color:var(--accent2); font-size:6.5pt; font-weight:bold; letter-spacing:.07em; text-transform:uppercase; margin:.10in 0 .03in; }}
.agenda-cols {{ display:grid; grid-template-columns:1fr 1fr; gap:.28in; margin-top:.12in; }}
.agenda-list {{ border-top:.035in solid var(--accent); padding-top:.09in; }}
.agenda-list h3 {{ font-size:10.5pt; color:var(--accent2); text-transform:uppercase; letter-spacing:.05em; }}
.agenda-list li {{ font-size:8.55pt; line-height:1.27; margin-bottom:.055in; }}
.about-text {{ color:var(--muted); font-size:8.45pt; line-height:1.42; column-count:2; column-gap:.32in; }}
.reference-note {{ color:var(--muted); font-size:7.6pt; line-height:1.35; border-top:1px solid var(--line); padding-top:.08in; margin-top:.16in; }}
.placeholder {{ width:100%; height:3.72in; background:linear-gradient(135deg,#F5F9FC,#E7F1EA); border:1px solid var(--line); position:relative; }}
.placeholder::before {{ content:""; position:absolute; left:.30in; right:.30in; top:1.58in; height:.035in; background:var(--accent); transform:rotate(-14deg); }}
.placeholder::after {{ content:"Strategic visual"; position:absolute; left:.30in; bottom:.26in; color:var(--muted); font-size:7pt; }}
.back-cover {{ padding:0; background-size:cover; background-position:center; }}
.back-cover::after {{ content:""; position:absolute; inset:0; background:rgba(0,0,0,.45); }}
.back-cover .cover-brand {{ color:#fff; }}
@media print {{ html, body {{ width:{PAGE_W}in; }} .page {{ margin:0; box-shadow:none; }} }}
"""

LABELS = {
    "en": {
        "lang": "en",
        "opening": "Opening view",
        "toc": "Contents",
        "chapter": "Chapter",
        "figure": "Figure",
        "leader_changes": "What changes for leaders",
        "what_to_watch": "What to watch",
        "decision_question": "Decision question",
        "recommended_move": "Recommended move",
        "watchout": "Watchout",
        "agenda": "Future action agenda",
        "priorities": "Priorities",
        "signals": "Signals to watch",
        "about": "About this research",
        "reference_note": "This report was informed by public research and data from:",
        "formal_note": "Detailed supporting sources are retained in the backup folder.",
        "disclaimer_text": "This report has been prepared for strategy discussion and executive decision support. It is not investment, legal, tax, audit or valuation advice. Market estimates, forecasts and scenarios are directional and should be independently validated before they are used for investment, financing, transaction, regulatory or operational decisions. Forward-looking views may change as technology, policy, financing, regulation, competition, supply chains and macro conditions evolve. Recipients should perform their own diligence and treat this report as one input into a broader decision process.",
    },
    "zh": {
        "lang": "zh-CN",
        "opening": "开篇观点",
        "toc": "目录",
        "chapter": "章节",
        "figure": "图",
        "leader_changes": "对管理层意味着什么",
        "what_to_watch": "需要持续观察",
        "decision_question": "决策问题",
        "recommended_move": "建议动作",
        "watchout": "需要注意",
        "agenda": "未来行动议程",
        "priorities": "优先事项",
        "signals": "需要观察的信号",
        "about": "关于本研究",
        "reference_note": "参考机构：",
        "formal_note": "详细支持来源保留在 backup 文件夹。",
        "disclaimer_text": "本报告用于战略讨论与高管决策支持，不构成投资、法律、税务、审计或估值建议。市场估计、预测和情景判断均为方向性内容，在用于投资、融资、交易、监管或运营决策前应独立核验。前瞻性观点会随技术、政策、融资、监管、竞争、供应链和宏观环境变化而调整。读者应进行独立尽调，并将本报告作为更广泛决策流程中的一个输入。",
    },
}


def render_report_html(report: Dict[str, Any], assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    sections = _safe_sections(report.get("sections", []))
    summary = _summary_items(report.get("executive_summary", []))
    charts = _safe_charts(report.get("charts", []))
    institutions = [_reader_text(x) for x in (report.get("reference_institutions", []) or []) if _reader_text(x)]
    logo_path = assets.get("brand-logo", "")
    cover_path = assets.get("cover-background", "")
    title = _reader_text(report.get("report_title") or topic)
    topic_text = _reader_text(topic)
    page_no = 1
    parts: List[str] = [
        "<!DOCTYPE html>",
        f"<html lang='{labels['lang']}'>",
        "<head>",
        "<meta charset='utf-8' />",
        f"<title>{html.escape(title)}</title>",
        f"<style>{CSS}</style>",
        "</head>",
        "<body>",
    ]

    _render_cover(parts, title, topic_text, cover_path)
    page_no += 1
    _render_contents(parts, sections, summary, charts, logo_path, page_no, labels)
    page_no += 1
    _render_opening(parts, report, summary, sections, assets, logo_path, page_no, labels, topic_text)
    page_no += 1

    for idx, section in enumerate(sections, start=1):
        _render_chapter(parts, section, assets, logo_path, page_no, labels, idx)
        page_no += 1
        if idx <= len(charts):
            _render_exhibit(parts, charts[idx - 1], assets, logo_path, page_no, labels, idx)
            page_no += 1
        if idx == 2:
            _render_decision_story(parts, report, logo_path, page_no, labels)
            page_no += 1

    _render_leadership_agenda(parts, report, sections, logo_path, page_no, labels)
    page_no += 1
    _render_about(parts, institutions, logo_path, page_no, labels)
    page_no += 1
    _render_back_cover(parts, cover_path)
    parts.append("</body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_report_markdown(report: Dict[str, Any], assets: Dict[str, str], output_file: Path, topic: str, language: str = "en") -> Path:
    labels = _labels(language)
    sections = _safe_sections(report.get("sections", []))
    summary = _summary_items(report.get("executive_summary", []))
    title = _reader_text(report.get("report_title") or topic)
    topic_text = _reader_text(topic)
    lines: List[str] = [
        f"# {title}",
        "",
        f"**Prepared by**: {BRAND_NAME}",
        f"**Topic**: {topic_text}",
        f"**Date**: {date.today().isoformat()}",
        "",
        f"## {labels['opening']}",
        "",
    ]
    opening = _reader_text(report.get("executive_summary_text") or " ".join(summary[:3]))
    if opening:
        lines.extend([opening, ""])
    for item in summary[:5]:
        lines.append(f"- {_shorten(item, 230)}")

    lines.extend(["", f"## {labels['toc']}", ""])
    for idx, section in enumerate(sections, start=1):
        lines.append(f"{idx}. {_strip_number_prefix(section.get('title', 'Section'))}")

    for idx, section in enumerate(sections, start=1):
        default_title = f"{labels['chapter']} {idx}"
        lines.extend(["", f"## {_strip_number_prefix(section.get('title', default_title))}", ""])
        lead = _reader_text(section.get("lead", ""))
        if lead:
            lines.extend([lead, ""])
        for paragraph in _section_paragraphs(section)[:5]:
            lines.extend([paragraph, ""])
        hints = _section_content_hints(section)[:3]
        if hints:
            lines.append(f"**{labels['what_to_watch']}**")
            for item in hints:
                lines.append(f"- {_shorten(item, 170)}")

    scenario = _scenario_payload(report)
    if scenario:
        lines.extend(["", "## " + scenario["title"], "", scenario["situation"], ""])
        if scenario["question"]:
            lines.extend([f"**{labels['decision_question']}**: {scenario['question']}", ""])
        if scenario["move"]:
            lines.extend([f"**{labels['recommended_move']}**: {scenario['move']}", ""])
        if scenario["watchouts"]:
            lines.extend([f"**{labels['watchout']}**: {scenario['watchouts']}", ""])

    actions, risks = _agenda_payload(report, sections)
    lines.extend(["", f"## {labels['agenda']}", "", f"**{labels['priorities']}**"])
    for item in actions:
        lines.append(f"- {_shorten(item, 210)}")
    lines.extend(["", f"**{labels['signals']}**"])
    for item in risks:
        lines.append(f"- {_shorten(item, 210)}")

    lines.extend(["", f"## {labels['about']}", "", labels["disclaimer_text"], ""])
    institutions = [_reader_text(x) for x in (report.get("reference_institutions", []) or []) if _reader_text(x)]
    if institutions:
        lines.extend([f"{labels['reference_note']} {', '.join(institutions)}.", ""])
    lines.append(labels["formal_note"])
    output_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_file


def _render_cover(parts: List[str], title: str, topic: str, cover_path: str) -> None:
    bg = f"background-image:url('{html.escape(cover_path)}');" if cover_path else "background:#1B2A34;"
    parts.append(
        f"<section class='page cover' style=\"{bg}\">"
        "<div class='cover-panel'>"
        f"<div class='eyebrow'>{html.escape(BRAND_NAME)} Research</div>"
        f"<div class='eyebrow'>{html.escape(REPORT_LABEL)}</div>"
        f"<h1>{html.escape(title)}</h1>"
        f"<div class='cover-date'>{html.escape(_shorten(topic, 180))}<br>{date.today().isoformat()}</div>"
        "</div>"
        f"<div class='cover-brand'>{html.escape(BRAND_NAME)}</div>"
        "</section>"
    )


def _render_contents(parts: List[str], sections: List[Dict[str, Any]], summary: List[str], charts: List[Dict[str, Any]], logo_path: str, page_no: int, labels: Dict[str, str]) -> None:
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<h2>{html.escape(labels['toc'])}</h2><table class='contents-table'>")
    for page, title, subs in _content_page_rows(summary, sections, charts, labels):
        sub_html = "".join(f"<div class='contents-sub'>{html.escape(_display_bullet(x, 110))}</div>" for x in subs)
        parts.append(f"<tr><td class='contents-page'>{html.escape(page)}</td><td><div class='contents-title'>{html.escape(_shorten(title, 115))}</div>{sub_html}</td></tr>")
    parts.append("</table></section>")


def _content_page_rows(summary: List[str], sections: List[Dict[str, Any]], charts: List[Dict[str, Any]], labels: Dict[str, str]) -> List[tuple[str, str, List[str]]]:
    rows: List[tuple[str, str, List[str]]] = [("03", _agenda_heading(summary, sections), [])]
    page_no = 4
    chart_count = len(charts)
    for idx, section in enumerate(sections, start=1):
        rows.append((f"{page_no:02d}", _strip_number_prefix(section.get("title", "Section")), _section_content_hints(section)[:2]))
        page_no += 1
        if idx <= chart_count:
            page_no += 1
        if idx == 2:
            page_no += 1
    rows.append((f"{page_no:02d}", labels["agenda"], []))
    rows.append((f"{page_no + 1:02d}", labels["about"], []))
    return rows


def _render_opening(parts: List[str], report: Dict[str, Any], summary: List[str], sections: List[Dict[str, Any]], assets: Dict[str, str], logo_path: str, page_no: int, labels: Dict[str, str], topic: str) -> None:
    visual = _asset_for_key(assets, "image-1") or _asset_for_key(assets, "cover-background")
    heading = _agenda_heading(summary, sections)
    narrative = _reader_text(report.get("executive_summary_text") or " ".join(summary[:3]))
    bullets = summary[:4] or [_strip_number_prefix(x.get("title", "")) for x in sections[:4]]
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    if visual:
        parts.append(f"<img class='opening-visual' src='{html.escape(visual)}' alt='' />")
    parts.append(f"<div class='kicker'>{html.escape(labels['opening'])}</div>")
    parts.append(f"<div class='opening-title'>{html.escape(_shorten(heading, 105))}</div>")
    parts.append(f"<div class='lead'>{html.escape(_shorten(topic, 165))}</div>")
    parts.append("<div class='two-col'><div>")
    if narrative:
        parts.append(f"<p>{html.escape(_shorten(narrative, 1180))}</p>")
    parts.append("</div><div class='side-note'>")
    parts.append(f"<b>{html.escape(labels['leader_changes'])}</b><ul>")
    for item in bullets[:4]:
        parts.append(f"<li>{html.escape(_display_bullet(item, 165))}</li>")
    parts.append("</ul></div></div></section>")


def _render_chapter(parts: List[str], section: Dict[str, Any], assets: Dict[str, str], logo_path: str, page_no: int, labels: Dict[str, str], idx: int) -> None:
    title = _strip_number_prefix(section.get("title", f"{labels['chapter']} {idx}"))
    lead = _reader_text(section.get("lead", ""))
    paragraphs = _section_paragraphs(section)
    visual = _resolve_visual(section, idx, assets)
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    if idx % 2 == 1 and visual:
        parts.append(f"<img class='chapter-visual' src='{html.escape(visual)}' alt='' />")
    parts.append(f"<div class='kicker'>{html.escape(labels['chapter'])} {idx}</div>")
    parts.append(f"<h2>{html.escape(_shorten(title, 118))}</h2>")
    if lead and _normalize_punctuation(lead).lower() != _normalize_punctuation(title).lower():
        parts.append(f"<div class='lead'>{html.escape(_shorten(lead, 290))}</div>")

    if idx % 2 == 0:
        parts.append("<div class='chapter-grid reverse'>")
        parts.append("<div>")
        _append_visual(parts, visual)
        parts.append("</div><div class='body-copy'>")
        for paragraph in paragraphs[:3]:
            parts.append(f"<p>{html.escape(_shorten(paragraph, 620))}</p>")
        _append_takeaways(parts, _section_content_hints(section), labels)
        parts.append("</div></div>")
    else:
        parts.append("<div class='chapter-grid text-grid'>")
        parts.append("<div class='body-copy'>")
        for paragraph in paragraphs[:2]:
            parts.append(f"<p>{html.escape(_shorten(paragraph, 620))}</p>")
        parts.append("</div><div class='body-copy'>")
        for paragraph in paragraphs[2:4]:
            parts.append(f"<p>{html.escape(_shorten(paragraph, 620))}</p>")
        _append_takeaways(parts, _section_content_hints(section), labels)
        parts.append("</div></div>")
    parts.append("</section>")


def _render_exhibit(parts: List[str], chart: Dict[str, Any], assets: Dict[str, str], logo_path: str, page_no: int, labels: Dict[str, str], idx: int) -> None:
    title = _reader_text(chart.get("title") or f"{labels['figure']} {idx}")
    subtitle = _reader_text(chart.get("subtitle") or chart.get("caption") or "")
    caption = _reader_text(chart.get("caption") or "")
    source = _reader_text(chart.get("source_note") or f"Source: public sources and {BRAND_NAME} synthesis.")
    path = _asset_for_key(assets, str(chart.get("id") or f"chart-{idx}")) or _asset_for_key(assets, f"chart-{idx}")
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['figure'])} {idx}</div>")
    parts.append(f"<h2>{html.escape(_shorten(title, 125))}</h2>")
    if subtitle:
        parts.append(f"<p class='figure-note'>{html.escape(_shorten(subtitle, 230))}</p>")
    if path:
        parts.append(f"<img class='exhibit-img' src='{html.escape(path)}' alt='' />")
    else:
        parts.append("<div class='placeholder'></div>")
    if caption:
        parts.append(f"<p>{html.escape(_shorten(caption, 300))}</p>")
    parts.append(f"<p class='figure-note'>{html.escape(_shorten(source, 210))}</p>")
    parts.append("</section>")


def _render_decision_story(parts: List[str], report: Dict[str, Any], logo_path: str, page_no: int, labels: Dict[str, str]) -> None:
    scenario = _scenario_payload(report)
    if not scenario:
        return
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<h2>{html.escape(_shorten(scenario['title'], 110))}</h2>")
    parts.append("<div class='two-col'><div>")
    if scenario["situation"]:
        parts.append(f"<p>{html.escape(_shorten(scenario['situation'], 620))}</p>")
    if scenario["move"]:
        parts.append(f"<p>{html.escape(_shorten(scenario['move'], 540))}</p>")
    parts.append("</div><div class='scenario-box'>")
    if scenario["question"]:
        parts.append(f"<div class='scenario-label'>{html.escape(labels['decision_question'])}</div><h3>{html.escape(_shorten(scenario['question'], 260))}</h3>")
    if scenario["watchouts"]:
        parts.append(f"<div class='scenario-label'>{html.escape(labels['watchout'])}</div><p>{html.escape(_shorten(scenario['watchouts'], 360))}</p>")
    parts.append("</div></div></section>")


def _render_leadership_agenda(parts: List[str], report: Dict[str, Any], sections: List[Dict[str, Any]], logo_path: str, page_no: int, labels: Dict[str, str]) -> None:
    actions, risks = _agenda_payload(report, sections)
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<h2>{html.escape(labels['agenda'])}</h2>")
    parts.append("<div class='agenda-cols'>")
    for heading, items in [(labels["priorities"], actions), (labels["signals"], risks)]:
        parts.append("<div class='agenda-list'>")
        parts.append(f"<h3>{html.escape(heading)}</h3><ul>")
        for item in items[:6]:
            parts.append(f"<li>{html.escape(_shorten(item, 210))}</li>")
        parts.append("</ul></div>")
    parts.append("</div></section>")


def _render_about(parts: List[str], institutions: List[str], logo_path: str, page_no: int, labels: Dict[str, str]) -> None:
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<h2>{html.escape(labels['about'])}</h2>")
    parts.append(f"<div class='about-text'>{html.escape(labels['disclaimer_text'])}</div>")
    if institutions:
        parts.append(f"<div class='reference-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}. {html.escape(labels['formal_note'])}</div>")
    else:
        parts.append(f"<div class='reference-note'>{html.escape(labels['formal_note'])}</div>")
    parts.append("</section>")


def _render_back_cover(parts: List[str], cover_path: str) -> None:
    bg = f"background-image:url('{html.escape(cover_path)}');" if cover_path else "background:#1B2A34;"
    parts.append(f"<section class='page back-cover' style=\"{bg}\"><div class='cover-brand'>{html.escape(BRAND_NAME)}</div></section>")


def _append_visual(parts: List[str], visual: str) -> None:
    if visual:
        parts.append(f"<img class='section-visual' src='{html.escape(visual)}' alt='' />")
    else:
        parts.append("<div class='placeholder'></div>")


def _append_takeaways(parts: List[str], items: List[str], labels: Dict[str, str]) -> None:
    if not items:
        return
    parts.append(f"<div class='takeaway'><strong>{html.escape(labels['what_to_watch'])}</strong><ul>")
    for item in items[:3]:
        parts.append(f"<li>{html.escape(_display_bullet(item, 150))}</li>")
    parts.append("</ul></div>")


def _page_header(parts: List[str], logo_path: str, page_no: int) -> None:
    if logo_path and not logo_path.lower().endswith(".svg"):
        parts.append(f"<img class='logo-fixed' src='{html.escape(logo_path)}' alt='' />")
    parts.append(f"<div class='page-header'>{html.escape(BRAND_NAME)}</div>")
    parts.append(f"<div class='page-footer'><span>{html.escape(BRAND_NAME)} | {html.escape(REPORT_LABEL)}</span><span>{page_no}</span></div>")


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def _scenario_payload(report: Dict[str, Any]) -> Dict[str, str]:
    scenarios = _as_list(report.get("scenario_vignettes"))[:1]
    if scenarios and isinstance(scenarios[0], dict):
        item = scenarios[0]
        title = _field(item, "title") or "A concrete executive choice"
        if _looks_like_internal_label(title):
            title = "A concrete executive choice"
        return {
            "title": title,
            "situation": _field(item, "situation"),
            "question": _field(item, "ceo_question"),
            "move": _field(item, "recommended_move"),
            "watchouts": _field(item, "watchouts"),
        }
    return {
        "title": "A concrete executive choice",
        "situation": "Leadership must decide which moves can be made now and which should wait for stronger evidence.",
        "question": "What should be done before the next major commitment?",
        "move": "Keep learning options open while reserving larger commitments for verified proof points.",
        "watchouts": "Avoid treating market enthusiasm as a substitute for validated economics.",
    }


def _agenda_payload(report: Dict[str, Any], sections: List[Dict[str, Any]]) -> tuple[List[str], List[str]]:
    actions = []
    for item in _as_list(report.get("action_plan"))[:6]:
        action = _field(item, "action") or _item_to_text(item)
        horizon = _field(item, "horizon")
        decision = _field(item, "decision_gate")
        actions.append(" - ".join(x for x in [horizon, action, decision] if x))
    if not actions:
        actions = [_strip_number_prefix(section.get("lead") or section.get("title") or "") for section in sections[:4]]
    risks = []
    for item in _as_list(report.get("risk_register"))[:6]:
        risk = _field(item, "risk")
        trigger = _field(item, "trigger")
        action = _field(item, "management_action")
        risks.append("; ".join(x for x in [risk, trigger, action] if x))
    if not risks:
        risks = ["Revisit the thesis when the public evidence base, economics or regulatory path changes."]
    return [_reader_text(x) for x in actions if _reader_text(x)], [_reader_text(x) for x in risks if _reader_text(x)]


def _section_paragraphs(section: Dict[str, Any]) -> List[str]:
    paragraphs = [_reader_text(x) for x in _as_list(section.get("paragraphs")) if _reader_text(x)]
    fallback = [
        "The available record should be translated into a small number of executive choices, each tied to a clear proof point and a practical timing question.",
        "Open questions should remain visible as diligence items instead of being converted into unsupported certainty.",
        "For senior leadership, the practical test is whether the evidence changes resource allocation, partner selection, customer focus or risk appetite.",
        "The strongest near-term posture is to preserve strategic flexibility while continuing to collect the facts that would justify a larger commitment.",
    ]
    while len(paragraphs) < 4:
        paragraphs.append(fallback[len(paragraphs) % len(fallback)])
    return _dedupe(paragraphs)


def _section_content_hints(section: Dict[str, Any]) -> List[str]:
    hints = []
    for item in _as_list(section.get("key_takeaways")):
        text = _reader_text(item)
        if text:
            hints.append(text)
    for item in _as_list(section.get("paragraphs")):
        text = _reader_text(item)
        if text and len(text) > 35:
            hints.append(_title_from_sentence(text))
        if len(hints) >= 4:
            break
    return _dedupe(hints)[:4]


def _agenda_heading(summary: List[str], sections: List[Dict[str, Any]]) -> str:
    for item in summary:
        cleaned = _reader_text(item)
        if not cleaned:
            continue
        if cleaned.lower().startswith(("this report", "the report", "our analysis")):
            continue
        return _compact_headline(cleaned)
    if sections:
        return _shorten(_strip_number_prefix(sections[0].get("title", "Management agenda")), 118)
    return "Management should focus on the few moves that can change the outcome"


def _title_from_sentence(text: str) -> str:
    cleaned = _reader_text(text).strip()
    cleaned = re.sub(r"^main conclusions?:\s*", "", cleaned, flags=re.I)
    cleaned = cleaned.split(";")[0].strip()
    if len(cleaned) > 118:
        cleaned = _compact_headline(cleaned)
    return cleaned or "The management agenda should be staged around evidence quality"


def _compact_headline(text: str) -> str:
    cleaned = _reader_text(text)
    cleaned = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    for pattern in [
        r",\s+driven\s+by\b",
        r",\s+supported\s+by\b",
        r",\s+requiring\b",
        r",\s+creating\b",
        r",\s+with\b",
        r",\s+but\b",
        r",\s+while\b",
        r";",
        r"\s+because\s+",
        r"\s+but\s+",
        r"\s+while\s+",
    ]:
        parts = re.split(pattern, cleaned, maxsplit=1, flags=re.I)
        if len(parts) > 1 and 35 <= len(parts[0]) <= 145:
            return _clean_sentence_fragment(parts[0])
    return _shorten(cleaned, 110)


def _display_bullet(text: str, max_chars: int) -> str:
    cleaned = _reader_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    compact = _compact_headline(cleaned)
    if compact and len(compact) < len(cleaned):
        cleaned = compact
    return _shorten(cleaned, max_chars)


def _clean_sentence_fragment(text: str) -> str:
    return _reader_text(text).rstrip(".,;: ")


def _resolve_visual(section: Dict[str, Any], idx: int, assets: Dict[str, str]) -> str:
    for key in [f"image-{idx}", str(section.get("visual_hint", "")), f"chart-{idx}", "cover-background"]:
        path = _asset_for_key(assets, key)
        if path:
            return path
    return ""


def _asset_for_key(assets: Dict[str, str], key: str) -> str:
    if not key:
        return ""
    path = assets.get(key, "")
    return "" if str(path).lower().endswith(".svg") else str(path or "")


def _safe_sections(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list) and value:
        return [x if isinstance(x, dict) else {"title": str(x), "paragraphs": [str(x)]} for x in value]
    return [
        {
            "title": "Executive priorities and implications",
            "lead": "The analysis should be translated into a concise management agenda.",
            "paragraphs": [
                "The available record should be organized around decision quality, execution timing and leadership implications.",
                "The most useful output is a short list of actions that can be tested against public evidence and client constraints.",
                "Follow-up work should validate the assumptions against the supporting sources.",
            ],
            "key_takeaways": ["Focus on actionability."],
            "visual_hint": "image-1",
        }
    ]


def _safe_charts(value: Any) -> List[Dict[str, Any]]:
    charts = []
    for idx, item in enumerate(_as_list(value), start=1):
        if not isinstance(item, dict):
            continue
        chart = dict(item)
        chart["id"] = str(chart.get("id") or f"chart-{idx}")
        charts.append(chart)
    return charts[:8]


def _summary_items(value: Any) -> List[str]:
    raw = [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else ([str(value).strip()] if str(value).strip() else [])
    if len(raw) <= 2 and raw and len(" ".join(raw)) > 450:
        raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(raw)) if len(s.strip()) > 20]
    return [_reader_text(x) for x in raw[:8] if _reader_text(x)]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _field(item: Any, key: str, default: str = "") -> str:
    if isinstance(item, dict):
        return _reader_text(item.get(key) or default)
    return _reader_text(item or default)


def _item_to_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_reader_text(v) for v in value.values() if isinstance(v, (str, int, float)) and _reader_text(v))
    if isinstance(value, list):
        return " ".join(_item_to_text(x) for x in value)
    return _reader_text(value)


def _strip_number_prefix(text: str) -> str:
    return re.sub(r"^\s*\d+[\.)、]\s*", "", _reader_text(text)).strip()


def _shorten(value: Any, max_chars: int) -> str:
    text = _reader_text(value)
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    sentence_cut = max(window.rfind("."), window.rfind("?"), window.rfind("!"))
    if sentence_cut >= max(35, int(max_chars * 0.45)):
        return window[: sentence_cut + 1].strip()
    shortened = window.rsplit(" ", 1)[0].strip()
    return (shortened or window.strip()).rstrip(".,;:")


def _dedupe(values: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        normalized = _reader_text(value)
        if not normalized:
            continue
        key = re.sub(r"\W+", "", normalized.lower())[:180]
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _looks_like_internal_label(text: str) -> bool:
    cleaned = _normalize_punctuation(text).strip().lower()
    labels = {
        "executive summary",
        "key findings",
        "management action plan",
        "risk register",
        "ceo decision scenario",
        "method and team",
        "methodology",
        "执行摘要",
        "管理层行动计划",
        "风险台账",
        "方法与团队",
    }
    return cleaned in labels or cleaned.startswith(("ceo decision", "management action", "risk register"))


def _reader_text(value: Any) -> str:
    text = str(value or "").replace("\u00ad", "").replace("\ufffe", "").replace("\ufeff", "")
    text = _reader_clean(" ".join(text.replace("\n", " ").split()))
    return text


def _reader_clean(text: str) -> str:
    text = _normalize_punctuation(text)
    replacements = [
        (r"\bCEO decision scenario\b", "A concrete executive choice"),
        (r"\bCEO investment committee scenario\b", "A concrete executive choice"),
        (r"\bManagement action plan\b", "Future action agenda"),
        (r"\bRisk register\b", "Signals to watch"),
        (r"\bMethod and team\b", "About this research"),
        (r"\bKey findings\b", "Main conclusions"),
        (r"\bExecutive summary\b", "Opening view"),
        (r"\bEvidence:\s*", ""),
        (r"\bManagement implication:\s*", ""),
        (r"\bManagement implications\b", "Leadership implications"),
        (r"\bCEO question:\s*", "Decision question: "),
        (r"\bRecommended move:\s*", "Recommended move: "),
        (r"\bpublic-evidence boundary\b", "available public record"),
        (r"\bevidence-boundary\b", "available public record"),
        (r"\bevidence boundary\b", "available public record"),
        (r"\bsource backup\b", "supporting sources"),
        (r"\bevidence gates\b", "verified milestones"),
        (r"\bdecision gates\b", "decision milestones"),
        (r"\bvalidation gaps\b", "open questions"),
        (r"\bmodel-assisted synthesis\b", "research synthesis"),
        (r"\binternal executive strategy stress test\b", ""),
        (r"\binternal framework\b", ""),
        (r"\bstress test\b", "review"),
        (r"执行摘要", "开篇观点"),
        (r"管理层行动计划", "未来行动议程"),
        (r"风险台账", "需要观察的信号"),
        (r"CEO\s*决策场景", "一个具体的高管选择"),
        (r"方法与团队", "关于本研究"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_punctuation(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    translation = {
        0x2018: "'",
        0x2019: "'",
        0x201A: "'",
        0x201B: "'",
        0x2032: "'",
        0xFF07: "'",
        0x201C: '"',
        0x201D: '"',
        0x201E: '"',
        0x201F: '"',
        0x2033: '"',
        0xFF02: '"',
        0x2010: "-",
        0x2011: "-",
        0x2012: "-",
        0x2013: "-",
        0x2014: "-",
        0x2212: "-",
        0x00A0: " ",
        0x202F: " ",
        0x3000: " ",
        0xFF0C: ",",
        0xFF0E: ".",
        0xFF1A: ":",
        0xFF1B: ";",
        0xFF08: "(",
        0xFF09: ")",
    }
    text = "".join(translation.get(ord(ch), ch) for ch in text)
    text = re.sub(r"([A-Za-z])\s+'\s+s\b", r"\1's", text)
    text = re.sub(r"\b([A-Za-z]+n)\s+'\s+t\b", r"\1't", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
