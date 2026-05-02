from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
REPORT_LABEL = THEME.get("report_label", "Deep Research Report")

CSS = f"""
:root {{ --accent:{PALETTE['accent']}; --ink:{PALETTE['ink']}; --muted:{PALETTE['subtle']}; --line:{PALETTE['line']}; --paper:{PALETTE['paper']}; --bg:{PALETTE['panel']}; }}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; }}
body {{ font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans CJK SC','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--ink); line-height:1.72; }}
.container {{ width:min(1040px, calc(100% - 56px)); margin:0 auto 56px; padding-top:28px; }}
.logo-fixed {{ position:fixed; top:22px; right:34px; width:118px; z-index:999; }}
.cover {{ position:relative; min-height:1020px; border-radius:28px; overflow:hidden; page-break-after:always; background-size:cover; background-position:center; }}
.cover-overlay {{ position:absolute; inset:0; background:linear-gradient(180deg, rgba(9,24,42,.18) 0%, rgba(9,24,42,.62) 100%); }}
.cover-content {{ position:absolute; left:64px; right:64px; bottom:78px; color:white; }}
.cover-brand {{ font-size:14px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; opacity:.9; margin-bottom:18px; }}
.cover-label {{ font-size:18px; font-weight:600; opacity:.9; margin-bottom:10px; }}
.cover h1 {{ font-size:46px; line-height:1.12; margin:0 0 18px; }}
.cover-subtitle {{ font-size:18px; max-width:760px; opacity:.94; }}
.cover-meta {{ margin-top:28px; font-size:14px; opacity:.86; }}
.section-block, .front-block {{ background:var(--paper); border-radius:20px; padding:32px; box-shadow:0 14px 32px rgba(15,23,42,.06); margin-bottom:22px; }}
.front-block {{ page-break-after:always; }}
.brand {{ color:var(--muted); font-size:12px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; margin-bottom:10px; }}
.kicker {{ color:var(--accent); font-size:13px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
h1 {{ font-size:40px; line-height:1.15; margin:12px 0; }}
h2 {{ font-size:26px; margin:0 0 14px; }}
.subtitle, .meta, .small-note, .disclaimer-text {{ color:var(--muted); }}
.summary-list li, .takeaways li, .toc li {{ margin-bottom:10px; }}
img.visual {{ width:100%; border-radius:16px; border:1px solid var(--line); margin:18px 0 8px; }}
.callout {{ border-left:4px solid var(--accent); background:#f6fbfb; padding:18px 18px 18px 20px; border-radius:12px; margin:18px 0; }}
.toc a {{ color:var(--ink); text-decoration:none; }}
.reference-note {{ color:var(--muted); font-size:12px; margin-top:24px; border-top:1px solid var(--line); padding-top:14px; }}
.footer-note {{ color:var(--muted); font-size:12px; margin-top:16px; }}
a {{ color:var(--accent); }}
"""

LABELS = {
    "zh": {"lang": "zh-CN", "hero": REPORT_LABEL, "topic": "选题", "prepared_by": "出品方", "summary": "执行摘要", "toc": "目录", "disclaimer": "免责声明", "cards": "关键洞察图卡", "takeaways": "本节要点", "charts": "数据图表", "reference_note": "本报告参考了以下机构或平台的公开研究与数据资料：", "formal_note": "完整底稿与来源备份已归档于 backup 文件夹，本正式文件不构成逐条引文展示。", "disclaimer_text": "本文件为管理咨询与研究分析材料，仅供战略讨论、行业研判与管理决策参考，不构成投资建议、证券建议、法律意见、税务意见或审计意见。"},
    "en": {"lang": "en", "hero": REPORT_LABEL, "topic": "Topic", "prepared_by": "Prepared by", "summary": "Executive Summary", "toc": "Contents", "disclaimer": "Disclaimer", "cards": "Key Insight Cards", "takeaways": "Section Takeaways", "charts": "Charts", "reference_note": "This report was informed by public research and data from:", "formal_note": "The full source backup is archived in the backup folder; the formal report intentionally avoids line-by-line citation display.", "disclaimer_text": "This document is a management consulting and research analysis deliverable for strategy discussion only and does not constitute investment, legal, tax, or audit advice."},
}


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def render_report_html(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    labels = _labels(language)
    sections = report.get("sections", [])
    institutions = report.get("reference_institutions", [])
    logo_path = assets.get("brand-logo", "")
    cover_path = assets.get("cover-background", "")
    parts: List[str] = ["<!DOCTYPE html>", f"<html lang='{labels['lang']}'>", "<head>", "<meta charset='utf-8' />", "<meta name='viewport' content='width=device-width, initial-scale=1' />", f"<title>{html.escape(report.get('report_title', topic))}</title>", f"<style>{CSS}</style>", "</head>", "<body>"]
    if logo_path:
        parts.append(f"<img class='logo-fixed' src='{html.escape(logo_path)}' alt='brand logo' />")
    parts.extend(["<div class='container'>", f"<section class='cover' style=\"background-image:url('{html.escape(cover_path)}');\">", "<div class='cover-overlay'></div>", "<div class='cover-content'>", f"<div class='cover-brand'>{html.escape(BRAND_NAME)}</div>", f"<div class='cover-label'>{html.escape(labels['hero'])}</div>", f"<h1>{html.escape(report.get('report_title', topic))}</h1>", f"<div class='cover-subtitle'>{html.escape(report.get('report_subtitle', ''))}</div>", f"<div class='cover-meta'>{html.escape(labels['topic'])}: {html.escape(topic)}<br/>{html.escape(labels['prepared_by'])}: {html.escape(BRAND_NAME)}</div>", "</div>", "</section>"])
    parts.append(f"<section class='front-block'><div class='brand'>{html.escape(BRAND_NAME)}</div><div class='kicker'>{html.escape(labels['summary'])}</div><h2>{html.escape(labels['summary'])}</h2><ul class='summary-list'>")
    for item in report.get("executive_summary", []):
        parts.append(f"<li>{html.escape(item)}</li>")
    parts.append("</ul></section>")
    parts.append(f"<section class='front-block'><div class='kicker'>{html.escape(labels['toc'])}</div><h2>{html.escape(labels['toc'])}</h2><ol class='toc'>")
    for index, section in enumerate(sections, start=1):
        sid = html.escape(section.get('id', f'section-{index}'))
        stitle = html.escape(section.get('title', f'Section {index}'))
        parts.append(f"<li><a href='#{sid}'>{stitle}</a></li>")
    parts.append("</ol></section>")
    parts.append(f"<section class='front-block'><div class='kicker'>{html.escape(labels['disclaimer'])}</div><h2>{html.escape(labels['disclaimer'])}</h2><p class='disclaimer-text'>{html.escape(labels['disclaimer_text'])}</p>")
    if institutions:
        parts.append(f"<p class='small-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}.</p>")
    parts.append(f"<p class='small-note'>{html.escape(labels['formal_note'])}</p></section>")
    overview_cards = [rel_path for key, rel_path in assets.items() if key.startswith("card-")]
    if overview_cards:
        parts.append(f"<section class='section-block'><div class='brand'>{html.escape(BRAND_NAME)}</div><div class='kicker'>{html.escape(labels['cards'])}</div><h2>{html.escape(labels['cards'])}</h2>")
        for rel_path in overview_cards[:2]:
            parts.append(f"<img class='visual' src='{html.escape(rel_path)}' alt='insight card' />")
        parts.append("</section>")
    for index, section in enumerate(sections, start=1):
        sid = html.escape(section.get('id', f'section-{index}'))
        parts.append(f"<section class='section-block' id='{sid}'><div class='brand'>{html.escape(BRAND_NAME)}</div><h2>{html.escape(section.get('title', 'Section'))}</h2>")
        lead = section.get("lead", "")
        if lead:
            parts.append(f"<p class='subtitle'>{html.escape(lead)}</p>")
        visual_key = section.get("visual_hint", "")
        inserted = False
        for idx, paragraph in enumerate(section.get("paragraphs", [])):
            parts.append(f"<p>{html.escape(paragraph)}</p>")
            if not inserted and idx >= 1 and visual_key in assets:
                parts.append(f"<img class='visual' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
                inserted = True
        takeaways = section.get("key_takeaways", [])
        if takeaways:
            parts.append(f"<div class='callout'><strong>{html.escape(labels['takeaways'])}</strong><ul class='takeaways'>")
            for item in takeaways:
                parts.append(f"<li>{html.escape(item)}</li>")
            parts.append("</ul></div>")
        if not inserted and visual_key in assets:
            parts.append(f"<img class='visual' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
        parts.append("</section>")
    charts = [path for key, path in assets.items() if key.startswith("chart-")]
    if charts:
        parts.append(f"<section class='section-block'><div class='brand'>{html.escape(BRAND_NAME)}</div><div class='kicker'>{html.escape(labels['charts'])}</div><h2>{html.escape(labels['charts'])}</h2>")
        for rel_path in charts:
            parts.append(f"<img class='visual' src='{html.escape(rel_path)}' alt='chart' />")
        parts.append("</section>")
    if institutions:
        parts.append(f"<div class='reference-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(institutions))}.</div>")
    parts.append(f"<div class='footer-note'>{html.escape(BRAND_NAME)}</div></div></body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_report_markdown(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    labels = _labels(language)
    sections = report.get("sections", [])
    institutions = report.get("reference_institutions", [])
    lines: List[str] = [f"# {report.get('report_title', topic)}", "", f"**{labels['prepared_by']}**: {BRAND_NAME}"]
    subtitle = report.get("report_subtitle", "")
    if subtitle:
        lines.extend(["", f"> {subtitle}"])
    lines.extend(["", f"**{labels['topic']}**: {topic}", "", f"## {labels['summary']}", ""])
    for item in report.get("executive_summary", []):
        lines.append(f"- {item}")
    lines.extend(["", f"## {labels['toc']}", ""])
    for section in sections:
        lines.append(f"- {section.get('title', 'Section')}")
    lines.extend(["", f"## {labels['disclaimer']}", "", labels['disclaimer_text'], ""])
    cards = [(key, path) for key, path in assets.items() if key.startswith("card-")]
    if cards:
        lines.extend([f"## {labels['cards']}", ""])
        for _, rel_path in cards[:2]:
            lines.extend([f"![]({rel_path})", ""])
    for section in sections:
        lines.extend([f"## {section.get('title', 'Section')}", ""])
        lead = section.get("lead", "")
        if lead:
            lines.extend([f"> {lead}", ""])
        visual_key = section.get("visual_hint", "")
        inserted = False
        for idx, paragraph in enumerate(section.get("paragraphs", [])):
            lines.extend([paragraph, ""])
            if not inserted and idx >= 1 and visual_key in assets:
                lines.extend([f"![]({assets[visual_key]})", ""])
                inserted = True
        takeaways = section.get("key_takeaways", [])
        if takeaways:
            lines.extend([f"**{labels['takeaways']}**", ""])
            for item in takeaways:
                lines.append(f"- {item}")
            lines.append("")
        if not inserted and visual_key in assets:
            lines.extend([f"![]({assets[visual_key]})", ""])
    charts = [path for key, path in assets.items() if key.startswith("chart-")]
    if charts:
        lines.extend([f"## {labels['charts']}", ""])
        for rel_path in charts:
            lines.extend([f"![]({rel_path})", ""])
    if institutions:
        lines.extend([f"> {labels['reference_note']} {', '.join(institutions)}.", "", f"> {labels['formal_note']}", ""])
    output_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_file
