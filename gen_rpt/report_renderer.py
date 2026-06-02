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
.narrative {{ font-size:10.8pt; line-height:1.34; color:var(--ink); margin:.08in 0 .11in; }}
.module-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:.10in .12in; margin-top:.08in; }}
.module-card {{ background:#fff; border-left:3px solid var(--accent); box-shadow:0 0 0 1px var(--line); padding:.075in .09in; min-height:.88in; page-break-inside:avoid; }}
.module-card .label {{ color:var(--accent); font-size:6.6pt; font-weight:bold; text-transform:uppercase; margin-bottom:.03in; }}
.module-card .title {{ font-size:8.7pt; line-height:1.18; color:var(--ink); font-weight:bold; margin-bottom:.03in; }}
.module-card .meta {{ font-size:7.1pt; line-height:1.24; color:var(--muted); }}
.table-lite {{ width:100%; border-collapse:collapse; margin-top:.08in; font-size:7.2pt; line-height:1.20; }}
.table-lite th {{ color:var(--accent); text-align:left; border-bottom:1px solid var(--line); padding:.045in; font-size:6.5pt; text-transform:uppercase; }}
.table-lite td {{ vertical-align:top; border-bottom:1px solid var(--line); padding:.045in; }}
.scenario-box {{ background:#fff; border-top:3px solid var(--accent2); box-shadow:0 0 0 1px var(--line); padding:.13in .16in; margin-top:.10in; }}
.scenario-box h3 {{ font-size:12pt; line-height:1.15; color:var(--ink); margin-bottom:.07in; }}
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
    "en": {
        "lang": "en",
        "summary": "Executive summary",
        "highlights": "Key highlights",
        "toc": "Contents",
        "findings": "Key findings",
        "actions": "Management action plan",
        "risks": "Risk register",
        "scenario": "CEO decision scenario",
        "method": "Method and team",
        "disclaimer": "Disclaimer",
        "takeaways": "Takeaways",
        "reference_note": "This report was informed by public research and data from:",
        "formal_note": "The full source backup is archived in the backup folder.",
        "disclaimer_text": "This document is a management consulting and research analysis deliverable for strategy discussion only. It is not professional advisory guidance.",
    },
    "zh": {
        "lang": "zh-CN",
        "summary": "执行摘要",
        "highlights": "关键结论",
        "toc": "目录",
        "findings": "关键发现",
        "actions": "管理层行动计划",
        "risks": "风险台账",
        "scenario": "CEO 决策场景",
        "method": "方法与团队",
        "disclaimer": "免责声明",
        "takeaways": "要点",
        "reference_note": "参考机构：",
        "formal_note": "完整来源底稿已归档在 backup 文件夹。",
        "disclaimer_text": "本文档仅用于管理研究和战略讨论，不构成专业建议。",
    },
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
    parts.append(f"<div class='kicker'>{labels['summary']}</div><h2>{html.escape(_summary_heading(language))}</h2>")
    summary_text = str(report.get("executive_summary_text") or "").strip()
    if summary_text:
        parts.append(f"<p class='narrative'>{html.escape(_shorten(summary_text, 1050))}</p>")
    parts.append("<div class='highlight-grid'>")
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

    page_no = _render_findings_page(parts, report, labels, logo_path, page_no)
    page_no = _render_action_plan_page(parts, report, labels, logo_path, page_no)
    page_no = _render_risk_page(parts, report, labels, logo_path, page_no)
    page_no = _render_scenario_page(parts, report, labels, logo_path, page_no)
    page_no = _render_method_page(parts, report, labels, logo_path, institutions, page_no)

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
    if report.get("executive_summary_text"):
        lines.extend([str(report.get("executive_summary_text")), ""])
    for item in report.get("executive_summary", []) or []:
        lines.append(f"- {_clean_summary_item(item)}")
    lines.extend(["", f"## {labels['findings']}", ""])
    for item in _as_list(report.get("key_findings")):
        lines.append(f"- **{_field(item, 'finding')}** Evidence: {_field(item, 'evidence')}. Implication: {_field(item, 'management_implication')}")
    lines.extend(["", f"## {labels['actions']}", ""])
    for item in _as_list(report.get("action_plan")):
        lines.append(f"- **{_field(item, 'horizon')}**: {_field(item, 'action')} Owner: {_field(item, 'owner')}. Gate: {_field(item, 'decision_gate')}")
    lines.extend(["", f"## {labels['risks']}", ""])
    for item in _as_list(report.get("risk_register")):
        lines.append(f"- **{_field(item, 'risk')}** Trigger: {_field(item, 'trigger')}. Management action: {_field(item, 'management_action')}")
    lines.extend(["", f"## {labels['scenario']}", ""])
    for item in _as_list(report.get("scenario_vignettes")):
        lines.append(f"- **{_field(item, 'title')}** {_field(item, 'situation')} CEO question: {_field(item, 'ceo_question')} Recommended move: {_field(item, 'recommended_move')}")
    lines.extend(["", f"## {labels['method']}", ""])
    if report.get("methodology_note"):
        lines.extend([str(report.get("methodology_note")), ""])
    for item in _as_list(report.get("author_credentials")):
        lines.append(f"- **{_field(item, 'name')}** {_field(item, 'role')}: {_field(item, 'credentials')}")
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


def _render_findings_page(parts: List[str], report: Dict[str, Any], labels: Dict[str, str], logo_path: str, page_no: int) -> int:
    findings = _as_list(report.get("key_findings"))[:6]
    if not findings:
        return page_no
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['findings'])}</div><h2>{html.escape(labels['findings'])}</h2><div class='module-grid'>")
    for idx, item in enumerate(findings, start=1):
        parts.append(
            "<div class='module-card'>"
            f"<div class='label'>{idx:02d}</div>"
            f"<div class='title'>{html.escape(_shorten(_field(item, 'finding'), 155))}</div>"
            f"<div class='meta'>{html.escape(_shorten(_field(item, 'evidence'), 175))}</div>"
            f"<div class='meta'>{html.escape(_shorten(_field(item, 'management_implication'), 175))}</div>"
            "</div>"
        )
    parts.append("</div></section>")
    return page_no + 1


def _render_action_plan_page(parts: List[str], report: Dict[str, Any], labels: Dict[str, str], logo_path: str, page_no: int) -> int:
    actions = _as_list(report.get("action_plan"))[:5]
    if not actions:
        return page_no
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['actions'])}</div><h2>{html.escape(labels['actions'])}</h2>")
    parts.append("<table class='table-lite'><thead><tr><th>Horizon</th><th>Action</th><th>Owner</th><th>Gate</th></tr></thead><tbody>")
    for item in actions:
        parts.append(
            "<tr>"
            f"<td>{html.escape(_shorten(_field(item, 'horizon'), 80))}</td>"
            f"<td>{html.escape(_shorten(_field(item, 'action'), 210))}<br><span class='small-note'>{html.escape(_shorten(_field(item, 'success_metric'), 140))}</span></td>"
            f"<td>{html.escape(_shorten(_field(item, 'owner'), 80))}</td>"
            f"<td>{html.escape(_shorten(_field(item, 'decision_gate'), 170))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></section>")
    return page_no + 1


def _render_risk_page(parts: List[str], report: Dict[str, Any], labels: Dict[str, str], logo_path: str, page_no: int) -> int:
    risks = _as_list(report.get("risk_register"))[:6]
    if not risks:
        return page_no
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['risks'])}</div><h2>{html.escape(labels['risks'])}</h2>")
    parts.append("<table class='table-lite'><thead><tr><th>Risk</th><th>Trigger</th><th>Management action</th><th>Boundary</th></tr></thead><tbody>")
    for item in risks:
        parts.append(
            "<tr>"
            f"<td>{html.escape(_shorten(_field(item, 'risk'), 150))}</td>"
            f"<td>{html.escape(_shorten(_field(item, 'trigger'), 150))}</td>"
            f"<td>{html.escape(_shorten(_field(item, 'management_action'), 170))}</td>"
            f"<td>{html.escape(_shorten(_field(item, 'evidence_boundary'), 150))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></section>")
    return page_no + 1


def _render_scenario_page(parts: List[str], report: Dict[str, Any], labels: Dict[str, str], logo_path: str, page_no: int) -> int:
    scenarios = _as_list(report.get("scenario_vignettes"))[:2]
    if not scenarios:
        return page_no
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['scenario'])}</div><h2>{html.escape(labels['scenario'])}</h2>")
    for item in scenarios:
        parts.append(
            "<div class='scenario-box'>"
            f"<h3>{html.escape(_shorten(_field(item, 'title'), 120))}</h3>"
            f"<p>{html.escape(_shorten(_field(item, 'situation'), 360))}</p>"
            f"<p><strong>CEO question:</strong> {html.escape(_shorten(_field(item, 'ceo_question'), 220))}</p>"
            f"<p><strong>Recommended move:</strong> {html.escape(_shorten(_field(item, 'recommended_move'), 260))}</p>"
            f"<p class='small-note'>{html.escape(_shorten(_field(item, 'watchouts'), 260))}</p>"
            "</div>"
        )
    parts.append("</section>")
    return page_no + 1


def _render_method_page(parts: List[str], report: Dict[str, Any], labels: Dict[str, str], logo_path: str, institutions: List[Any], page_no: int) -> int:
    note = str(report.get("methodology_note") or "").strip()
    authors = _as_list(report.get("author_credentials"))[:4]
    if not note and not authors and not institutions:
        return page_no
    parts.append("<section class='page'>")
    _page_header(parts, logo_path, page_no)
    parts.append(f"<div class='kicker'>{html.escape(labels['method'])}</div><h2>{html.escape(labels['method'])}</h2>")
    if note:
        parts.append(f"<p class='narrative'>{html.escape(_shorten(note, 900))}</p>")
    if authors:
        parts.append("<div class='module-grid'>")
        for item in authors:
            parts.append(
                "<div class='module-card'>"
                f"<div class='title'>{html.escape(_shorten(_field(item, 'name'), 90))}</div>"
                f"<div class='meta'>{html.escape(_shorten(_field(item, 'role'), 120))}</div>"
                f"<div class='meta'>{html.escape(_shorten(_field(item, 'credentials'), 220))}</div>"
                "</div>"
            )
        parts.append("</div>")
    if institutions:
        parts.append(f"<p class='small-note'>{html.escape(labels['reference_note'])} {html.escape(', '.join(str(x) for x in institutions))}. {html.escape(labels['formal_note'])}</p>")
    parts.append("</section>")
    return page_no + 1


def _labels(language: str) -> Dict[str, str]:
    return LABELS["en"] if str(language).lower().startswith("en") else LABELS["zh"]


def _summary_heading(language: str) -> str:
    return "What the CEO should take away before reading the body" if str(language).lower().startswith("en") else "先给 CEO 的结论、证据边界与下一步"


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _field(item: Any, key: str, default: str = "") -> str:
    if isinstance(item, dict):
        return " ".join(str(item.get(key) or default).split())
    return " ".join(str(item or default).split())


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
