from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from .theme import load_theme
from .web_publication_contract import clean_client_text, clean_client_value, is_internal_workbench_exhibit


THEME = load_theme()
BRAND_NAME = THEME.get("brand_name", "BlueOcean")


CSS = """
:root {
  --forest: #0C2B15;
  --green: #197A56;
  --lime: #96F878;
  --blue: #0A6B8A;
  --amber: #B45F06;
  --ink: #212427;
  --muted: #696969;
  --line: #D4D4D4;
  --paper: #FFFFFF;
  --sand: #F1EEEA;
  --sand-2: #FAF8F4;
  --charcoal: #232326;
  --max: 1180px;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: blueocean-sans, "Helvetica Neue", Arial, sans-serif;
  font-size: 18px;
  line-height: 1.58;
}
a { color: inherit; text-decoration-color: var(--green); text-underline-offset: 0.18em; }
.site-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255,255,255,.94);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}
.nav-inner {
  max-width: var(--max);
  margin: 0 auto;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.brand {
  font-weight: 700;
  letter-spacing: .01em;
  color: var(--forest);
}
.nav-links {
  display: flex;
  gap: 18px;
  align-items: center;
  font-size: 14px;
  color: var(--muted);
}
.hero {
  background: var(--sand);
  border-bottom: 1px solid var(--line);
}
.hero-grid {
  max-width: var(--max);
  min-height: 620px;
  margin: 0 auto;
  padding: 58px 24px 44px;
  display: grid;
  grid-template-columns: minmax(0, 0.96fr) minmax(360px, 0.72fr);
  gap: 48px;
  align-items: end;
}
.hero-topic {
  color: var(--green);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
  margin-bottom: 18px;
}
.hero h1 {
  color: var(--forest);
  font-family: blueocean-serif, Georgia, "Times New Roman", serif;
  font-weight: 400;
  font-size: 66px;
  line-height: .98;
  letter-spacing: 0;
  margin: 0 0 24px;
}
.dek {
  max-width: 760px;
  color: #323232;
  font-size: 24px;
  line-height: 1.28;
  margin: 0 0 28px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  color: var(--muted);
  font-size: 14px;
}
.hero-media {
  align-self: stretch;
  min-height: 460px;
  background: var(--forest);
  overflow: hidden;
  position: relative;
}
.hero-media img {
  width: 100%;
  height: 100%;
  min-height: 460px;
  object-fit: cover;
  display: block;
}
.hero-media::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(12,43,21,0) 45%, rgba(12,43,21,.35));
}
.takeaways {
  max-width: var(--max);
  margin: 0 auto;
  padding: 32px 24px 42px;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 40px;
}
.takeaways h2 {
  margin: 0;
  color: var(--forest);
  font-family: blueocean-serif, Georgia, "Times New Roman", serif;
  font-size: 34px;
  line-height: 1.05;
  font-weight: 400;
}
.takeaway-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.takeaway-list li {
  border-top: 4px solid var(--lime);
  padding-top: 16px;
  font-size: 17px;
  line-height: 1.42;
}
.article-shell {
  max-width: var(--max);
  margin: 0 auto;
  padding: 52px 24px 80px;
  display: grid;
  grid-template-columns: 210px minmax(0, 860px);
  gap: 38px;
  align-items: start;
}
.toc {
  position: sticky;
  top: 76px;
  font-size: 14px;
  line-height: 1.32;
  color: var(--muted);
}
.toc-title {
  color: var(--forest);
  font-weight: 700;
  margin-bottom: 12px;
}
.toc a {
  display: block;
  padding: 8px 0;
  border-top: 1px solid var(--line);
  text-decoration: none;
}
.article-main {
  min-width: 0;
}
.lead-block {
  font-size: 22px;
  line-height: 1.42;
  color: #323232;
  margin-bottom: 54px;
}
.section-block {
  margin: 0 0 66px;
  scroll-margin-top: 96px;
}
.section-kicker,
.exhibit-kicker {
  color: var(--green);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
  margin-bottom: 14px;
}
.section-block h2 {
  margin: 0 0 18px;
  color: var(--forest);
  font-family: blueocean-serif, Georgia, "Times New Roman", serif;
  font-size: 42px;
  line-height: 1.08;
  letter-spacing: 0;
  font-weight: 400;
}
.section-lead {
  color: #323232;
  font-size: 21px;
  line-height: 1.38;
  margin: 0 0 24px;
}
.section-media {
  margin: 30px 0 30px;
  background: var(--sand);
  overflow: hidden;
}
.section-media img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  display: block;
}
.section-block p {
  margin: 0 0 20px;
}
.evidence-list {
  margin: 28px 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}
.evidence-list li {
  padding: 13px 0 13px 18px;
  border-left: 4px solid var(--lime);
  background: linear-gradient(90deg, rgba(150,248,120,.14), rgba(150,248,120,0));
  font-size: 16px;
  line-height: 1.42;
}
.so-what {
  margin: 30px 0 0;
  padding: 22px 24px;
  background: var(--sand-2);
  border-top: 4px solid var(--forest);
  font-size: 17px;
}
.action-list {
  margin: 26px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 18px;
}
.action-list li {
  padding: 18px 0;
  border-top: 1px solid var(--line);
}
.action-horizon {
  color: var(--green);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.action-list strong {
  display: block;
  color: var(--forest);
  font-size: 21px;
  line-height: 1.25;
  margin-bottom: 6px;
}
.action-list span {
  color: #323232;
  font-size: 16px;
  line-height: 1.42;
}
.exhibit {
  margin: 42px 0 62px;
  padding: 28px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.exhibit h3 {
  color: var(--forest);
  font-family: blueocean-serif, Georgia, "Times New Roman", serif;
  font-size: 32px;
  line-height: 1.12;
  font-weight: 400;
  margin: 0 0 10px;
}
.exhibit-subtitle {
  color: var(--muted);
  font-size: 16px;
  margin: 0 0 20px;
}
.source-note {
  margin-top: 14px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.35;
}
.data-basis {
  margin-top: 14px;
  border-top: 1px solid var(--line);
  padding-top: 12px;
  color: #323232;
  font-size: 13px;
  line-height: 1.36;
}
.data-basis summary {
  color: var(--forest);
  cursor: pointer;
  font-weight: 700;
}
.data-basis ul {
  margin: 10px 0 0;
  padding-left: 18px;
}
.data-basis li {
  margin-bottom: 8px;
}
.basis-id {
  color: var(--green);
  font-weight: 700;
}
.metric-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.metric-card {
  border-top: 4px solid var(--green);
  background: var(--sand-2);
  padding: 18px;
  min-height: 136px;
}
.metric-card .metric {
  color: var(--forest);
  font-family: Georgia, "Times New Roman", serif;
  font-size: 38px;
  line-height: 1;
  margin-bottom: 10px;
}
.metric-card .label {
  color: #323232;
  font-size: 15px;
  line-height: 1.34;
}
.svg-chart {
  width: 100%;
  height: auto;
  display: block;
  background: #fff;
}
.chart-axis { stroke: #A8A8A8; stroke-width: 1; }
.chart-grid { stroke: #E4E1DC; stroke-width: 1; }
.chart-label {
  fill: #323232;
  font-family: Arial, sans-serif;
  font-size: 13px;
}
.chart-small {
  fill: #696969;
  font-family: Arial, sans-serif;
  font-size: 12px;
}
.bubble-label-box {
  fill: rgba(255,255,255,.96);
  stroke: #D4D4D4;
  stroke-width: 1;
}
.bubble-leader {
  stroke: #7B817D;
  stroke-width: 1;
}
.bar-primary { fill: var(--green); }
.bar-secondary { fill: var(--blue); }
.bar-tertiary { fill: var(--amber); }
.line-primary { fill: none; stroke: var(--green); stroke-width: 3; }
.line-secondary { fill: none; stroke: var(--blue); stroke-width: 3; }
.line-tertiary { fill: none; stroke: var(--amber); stroke-width: 3; }
.matrix {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 14px;
  line-height: 1.34;
  border-top: 4px solid var(--green);
  box-shadow: inset 0 0 0 1px var(--line);
}
.matrix th,
.matrix td {
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 13px 14px;
  text-align: left;
  vertical-align: top;
}
.matrix th {
  color: var(--forest);
  background: #F5F2EC;
  font-weight: 700;
}
.matrix tbody tr:nth-child(even) td,
.matrix tbody tr:nth-child(even) th {
  background: var(--sand-2);
}
.matrix td:last-child,
.matrix th:last-child {
  border-right: 0;
}
.matrix tr:last-child td,
.matrix tr:last-child th {
  border-bottom: 0;
}
.matrix-score {
  color: var(--forest);
  font-weight: 600;
}
.matrix-badge {
  display: inline-block;
  padding: 4px 8px;
  background: #EAF4E4;
  color: var(--forest);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.1;
  text-transform: uppercase;
}
.matrix-badge.medium {
  background: #F4EEDB;
}
.matrix-badge.low {
  background: #F1E4DD;
}
.matrix-text {
  color: #323232;
}
.timeline {
  position: relative;
  display: grid;
  gap: 16px;
  padding: 10px 0 4px;
}
.timeline::before {
  content: "";
  position: absolute;
  left: 28px;
  top: 18px;
  bottom: 18px;
  width: 2px;
  background: var(--line);
}
.timeline-item {
  position: relative;
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.timeline-year {
  position: relative;
  z-index: 1;
  width: 58px;
  min-height: 58px;
  border-radius: 50%;
  background: var(--forest);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 17px;
  line-height: 1;
}
.timeline-card {
  background: var(--sand-2);
  border-top: 4px solid var(--green);
  padding: 14px 16px;
  min-height: 76px;
}
.timeline-card strong {
  color: var(--forest);
  display: block;
  margin-bottom: 4px;
}
.process {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.process-step {
  background: var(--sand-2);
  border-top: 4px solid var(--lime);
  padding: 16px;
  min-height: 142px;
}
.process-step span {
  color: var(--green);
  font-weight: 700;
  font-size: 13px;
}
.process-step strong {
  color: var(--forest);
  display: block;
  margin: 8px 0;
}
.methodology {
  max-width: var(--max);
  margin: 0 auto;
  padding: 26px 24px 34px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
  border-top: 1px solid var(--line);
}
.footer {
  padding: 32px 24px;
  color: #fff;
  background: var(--forest);
  font-size: 14px;
}
.footer-inner {
  max-width: var(--max);
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  gap: 24px;
}
@media (max-width: 980px) {
  .hero-grid,
  .takeaways,
  .article-shell {
    grid-template-columns: 1fr;
  }
  .hero h1 { font-size: 46px; }
  .dek { font-size: 21px; }
  .hero-media,
  .hero-media img { min-height: 300px; }
  .toc,
  .methodology {
    position: static;
    border-left: 0;
    padding-left: 0;
  }
  .takeaway-list,
  .metric-row,
  .process {
    grid-template-columns: 1fr;
  }
}
@media print {
  .site-nav,
  .toc { display: none; }
  .article-shell { grid-template-columns: 1fr; max-width: 820px; }
  .hero-grid { min-height: 0; }
  .section-block,
  .exhibit { break-inside: avoid; }
}
"""


LABELS = {
    "en": {
        "contents": "Contents",
        "takeaways": "Key Takeaways",
        "evidence": "Evidence",
        "so_what": "",
        "article": "Article",
        "methodology": "Methodology and source boundary",
        "actions": "How leaders should move next",
        "prepared": "Prepared by",
        "read_time": "min read",
        "exhibit": "Exhibit",
        "data_basis": "Sources",
        "where_start": "Where to Start",
    },
    "zh": {
        "contents": "目录",
        "takeaways": "关键结论",
        "evidence": "证据",
        "so_what": "管理含义",
        "article": "正文",
        "methodology": "方法与证据边界",
        "actions": "管理层下一步",
        "prepared": "出品",
        "read_time": "分钟阅读",
        "exhibit": "图表",
        "data_basis": "来源",
        "where_start": "从哪里开始",
    },
}


def render_web_report_html(
    report: Dict[str, Any],
    assets: Dict[str, str],
    output_file: Path,
    topic: str,
    language: str = "en",
) -> Path:
    normalized = normalize_web_report(report, topic=topic, language=language)
    labels = _labels(language)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    title = normalized["title"]
    dek = normalized["dek"]
    hero = _asset(assets, "cover-background") or _asset(assets, "cover-ai")
    published = normalized.get("published_date") or date.today().isoformat()
    authors = ", ".join(normalized.get("authors", [])) or BRAND_NAME
    read_time = str(normalized.get("read_time_minutes") or _estimate_read_time(normalized))
    sections = normalized["sections"]
    exhibits = normalized["exhibits"]

    parts: List[str] = [
        "<!DOCTYPE html>",
        f"<html lang='{_lang_attr(language)}'>",
        "<head>",
        "<meta charset='utf-8' />",
        "<meta name='viewport' content='width=device-width, initial-scale=1' />",
        f"<title>{_e(title)}</title>",
        f"<meta name='description' content='{_e(dek)}' />",
        f"<style>{CSS}</style>",
        "</head>",
        "<body>",
        "<nav class='site-nav'><div class='nav-inner'>",
        f"<div class='brand'>{_e(BRAND_NAME)}</div>",
        "<div class='nav-links'>",
        f"<a href='#article'>{_e(labels['article'])}</a>",
        "</div></div></nav>",
        "<header class='hero'>",
        "<div class='hero-grid'>",
        "<div>",
        f"<div class='hero-topic'>{_e(normalized.get('category') or 'Deep research')}</div>",
        f"<h1>{_e(title)}</h1>",
        f"<p class='dek'>{_e(dek)}</p>",
        "<div class='meta'>",
        f"<span>{_e(labels['prepared'])}: {_e(authors)}</span>",
        f"<span>{_e(published)}</span>",
        f"<span>{_e(read_time)} {_e(labels['read_time'])}</span>",
        "</div>",
        "</div>",
        "<div class='hero-media'>",
    ]
    if hero:
        parts.append(f"<img src='{_e(hero)}' alt='' />")
    parts.extend(["</div>", "</div>"])
    _render_takeaways(parts, normalized["key_takeaways"], labels)
    parts.append("</header>")

    parts.append("<main id='article' class='article-shell'>")
    _render_toc(parts, sections, labels)
    parts.append("<article class='article-main'>")
    if normalized.get("intro"):
        parts.append(f"<div class='lead-block'>{_paragraphs(list(normalized['intro']))}</div>")
    exhibit_by_after = _exhibits_by_anchor(exhibits)
    for idx, section in enumerate(sections, start=1):
        _render_section(parts, section, idx, labels, assets)
        for exhibit in exhibit_by_after.get(section.get("id") or f"section-{idx}", []):
            _render_exhibit(parts, exhibit, labels)
    for exhibit in exhibit_by_after.get("", []):
        _render_exhibit(parts, exhibit, labels)
    _render_actions(parts, normalized.get("action_steps", []), labels, language)
    parts.append("</article>")
    parts.append("</main>")

    _render_methodology(parts, normalized, labels)
    parts.append(
        "<footer class='footer'><div class='footer-inner'>"
        f"<span>{_e(BRAND_NAME)}</span>"
        f"<span>{_e(normalized.get('disclaimer') or 'Prepared for strategy discussion. Validate source data before investment or operational use.')}</span>"
        "</div></footer>"
    )
    parts.append("</body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def render_web_report_markdown(
    report: Dict[str, Any],
    output_file: Path,
    topic: str,
    language: str = "en",
) -> Path:
    normalized = normalize_web_report(report, topic=topic, language=language)
    lines = [f"# {normalized['title']}", "", normalized["dek"], ""]
    lines.extend(["## Key Takeaways", ""])
    for item in normalized["key_takeaways"]:
        lines.append(f"- {item}")
    lines.append("")
    for section in normalized["sections"]:
        lines.extend([f"## {section['title']}", "", section.get("lead", ""), ""])
        for paragraph in section.get("paragraphs", []):
            lines.extend([paragraph, ""])
        if section.get("evidence"):
            lines.extend(["Evidence:", ""])
            for item in section["evidence"]:
                lines.append(f"- {item}")
            lines.append("")
    action_summary = _action_summary(normalized.get("action_steps", []), language)
    if action_summary:
        lines.extend(["", action_summary, ""])
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_file


def normalize_web_report(report: Dict[str, Any], *, topic: str, language: str = "en") -> Dict[str, Any]:
    data = dict(report or {})
    title = _text(data.get("title") or data.get("report_title") or topic)
    dek = _text(
        data.get("dek")
        or data.get("subtitle")
        or data.get("report_subtitle")
        or data.get("executive_summary_text")
        or _first_text(data.get("executive_summary"))
        or topic
    )
    intro = _list_text(data.get("intro") or data.get("opening") or [data.get("executive_summary_text") or dek])
    takeaways = _normalize_takeaways(
        data.get("key_takeaways")
        or data.get("keyTakeaways")
        or data.get("takeaways")
        or data.get("take_aways")
        or data.get("executive_summary")
        or data.get("executiveSummary")
        or data.get("key_findings")
        or data.get("findings")
    )
    sections = _normalize_sections(data.get("sections") or data.get("chapters"), takeaways)
    takeaways = _ensure_three_takeaways(takeaways, data, sections, topic, language, dek)
    exhibits = _normalize_exhibits(data.get("exhibits") or data.get("charts") or [])
    action_steps = _normalize_actions(data.get("action_steps") or data.get("action_plan") or [])
    references = _normalize_references(data.get("references") or data.get("sources") or [])
    authors = _list_text(data.get("authors") or data.get("author_credentials") or [BRAND_NAME])
    methodology = _text(data.get("methodology") or data.get("methodology_note") or "")
    source_count = int(_number(data.get("source_count"), 0))

    if len(sections) < 4:
        sections.extend(_fallback_sections(topic, takeaways, language)[len(sections):])
    if not exhibits:
        exhibits = _fallback_exhibits(topic, language)
    if not action_steps:
        action_steps = _fallback_actions(language)

    return {
        "title": _compact(title, 135),
        "dek": _compact(dek, 320),
        "category": _text(data.get("category") or data.get("topic_label") or "Deep research"),
        "published_date": _text(data.get("published_date") or date.today().isoformat()),
        "authors": authors[:6] or [BRAND_NAME],
        "read_time_minutes": data.get("read_time_minutes") or 0,
        "intro": intro[:3],
        "key_takeaways": [_compact(x, 260) for x in takeaways[:3]],
        "sections": sections[:8],
        "exhibits": exhibits[:8],
        "action_steps": action_steps[:6],
        "methodology": methodology or _default_methodology(language),
        "references": references[:24],
        "source_count": source_count or len(references),
        "evidence_quality": _text(data.get("evidence_quality") or data.get("evidence_boundary") or ""),
        "disclaimer": _text(data.get("disclaimer") or ""),
    }


def _render_takeaways(parts: List[str], takeaways: List[str], labels: Dict[str, str]) -> None:
    parts.append("<div class='takeaways'>")
    parts.append(f"<h2>{_e(labels['takeaways'])}</h2>")
    parts.append("<ul class='takeaway-list'>")
    for item in takeaways[:3]:
        parts.append(f"<li>{_e(item)}</li>")
    parts.append("</ul></div>")


def _render_toc(parts: List[str], sections: List[Dict[str, Any]], labels: Dict[str, str]) -> None:
    parts.append("<aside class='toc'>")
    parts.append(f"<div class='toc-title'>{_e(labels['contents'])}</div>")
    for idx, section in enumerate(sections, start=1):
        parts.append(f"<a href='#{_e(section['id'])}'>{idx}. {_e(_compact(section['title'], 90))}</a>")
    parts.append("</aside>")


def _render_section(parts: List[str], section: Dict[str, Any], idx: int, labels: Dict[str, str], assets: Dict[str, str] | None = None) -> None:
    parts.append(f"<section id='{_e(section['id'])}' class='section-block'>")
    parts.append(f"<div class='section-kicker'>{_e(labels['article'])} {idx}</div>")
    parts.append(f"<h2>{_e(section['title'])}</h2>")
    if section.get("lead"):
        parts.append(f"<p class='section-lead'>{_e(section['lead'])}</p>")
    image = _section_image(assets or {}, idx)
    if image:
        parts.append(f"<figure class='section-media'><img src='{_e(image)}' alt='' /></figure>")
    for paragraph in section.get("paragraphs", [])[:7]:
        parts.append(f"<p>{_e(paragraph)}</p>")
    if section.get("evidence"):
        parts.append("<ul class='evidence-list'>")
        for item in section["evidence"][:4]:
            parts.append(f"<li>{_e(item)}</li>")
        parts.append("</ul>")
    if section.get("so_what"):
        parts.append(f"<div class='so-what'>{_e(section['so_what'])}</div>")
    parts.append("</section>")


def _render_actions(parts: List[str], actions: Any, labels: Dict[str, str], language: str) -> None:
    items = _normalize_actions(actions)[:5]
    if not items:
        return
    zh = str(language or "").lower().startswith("zh")
    lead = (
        "The near-term objective is to turn the evidence into a few choices that preserve learning before committing scarce capital or operating capacity."
        if not zh
        else "近期重点不是把判断写得更满，而是把公开证据转成少数可执行选择，在投入稀缺资源前保留学习速度。"
    )
    parts.append("<section class='section-block action-block'>")
    parts.append(f"<div class='section-kicker'>{_e('Management agenda' if not zh else '管理议程')}</div>")
    parts.append(f"<h2>{_e(labels.get('where_start') or 'Where to Start')}</h2>")
    parts.append(f"<p class='section-lead'>{_e(lead)}</p>")
    parts.append("<ul class='action-list'>")
    for item in items:
        horizon = _compact(_text(item.get("horizon") or ""), 70)
        action = _compact(_text(item.get("action") or ""), 190)
        metric = _compact(_text(item.get("success_metric") or item.get("description") or ""), 190)
        if not action:
            continue
        if metric:
            metric_text = (
                f"Progress should be visible through {metric[0].lower() + metric[1:] if metric else metric}"
                if not zh
                else f"观察指标：{metric}"
            )
        else:
            metric_text = ""
        parts.append("<li>")
        if horizon:
            parts.append(f"<div class='action-horizon'>{_e(horizon)}</div>")
        parts.append(f"<strong>{_e(action)}</strong>")
        if metric_text:
            parts.append(f"<span>{_e(metric_text)}</span>")
        parts.append("</li>")
    parts.append("</ul></section>")


def _render_exhibit(parts: List[str], exhibit: Dict[str, Any], labels: Dict[str, str]) -> None:
    parts.append("<section class='exhibit'>")
    parts.append(f"<div class='exhibit-kicker'>{_e(labels.get('exhibit') or 'Exhibit')} {_e(str(exhibit.get('no') or ''))}</div>")
    parts.append(f"<h3>{_e(exhibit.get('title') or 'Exhibit')}</h3>")
    if exhibit.get("subtitle"):
        parts.append(f"<p class='exhibit-subtitle'>{_e(exhibit['subtitle'])}</p>")
    etype = str(exhibit.get("type") or "bar").lower().replace("-", "_")
    if etype in {"metric_row", "metrics", "big_numbers"}:
        _render_metrics(parts, exhibit)
    elif etype in {"line", "line_chart"}:
        parts.append(_svg_line(exhibit))
    elif etype in {"matrix", "heatmap", "opportunity_matrix", "decision_matrix"}:
        _render_matrix(parts, exhibit)
    elif etype in {"timeline", "milestone_timeline"}:
        _render_timeline(parts, exhibit)
    elif etype in {"process", "roadmap", "flywheel"}:
        _render_process(parts, exhibit)
    elif etype in {"bubble", "scatter", "opportunity_map", "quadrant"}:
        parts.append(_svg_bubble(exhibit))
    else:
        parts.append(_svg_bar(exhibit))
    if exhibit.get("caption"):
        parts.append(f"<p>{_e(exhibit['caption'])}</p>")
    if exhibit.get("source_note"):
        parts.append(f"<p class='source-note'>{_e(exhibit['source_note'])}</p>")
    _render_data_basis(parts, exhibit.get("data_basis") or [], labels)
    parts.append("</section>")


def _render_metrics(parts: List[str], exhibit: Dict[str, Any]) -> None:
    metrics = exhibit.get("metrics") or exhibit.get("items") or []
    if not isinstance(metrics, list):
        metrics = []
    if not metrics:
        categories, values = _chart_values(exhibit)
        metrics = [{"value": _format_value(value), "label": label} for label, value in zip(categories[:3], values[:3])]
    parts.append("<div class='metric-row'>")
    for item in metrics[:3]:
        value = _text(item.get("value") if isinstance(item, dict) else item)
        label = _text(item.get("label") if isinstance(item, dict) else "")
        parts.append(f"<div class='metric-card'><div class='metric'>{_e(value)}</div><div class='label'>{_e(label)}</div></div>")
    parts.append("</div>")


def _render_matrix(parts: List[str], exhibit: Dict[str, Any]) -> None:
    rows = _list_text(exhibit.get("rows")) or ["Capability", "Data", "Operating model"]
    columns = _list_text(exhibit.get("columns")) or ["Low", "Medium", "High"]
    values = exhibit.get("values")
    parts.append("<table class='matrix'><thead><tr><th></th>")
    for column in columns[:5]:
        parts.append(f"<th>{_e(column)}</th>")
    parts.append("</tr></thead><tbody>")
    for ridx, row in enumerate(rows[:8]):
        parts.append(f"<tr><th>{_e(row)}</th>")
        row_values = values[ridx] if isinstance(values, list) and ridx < len(values) and isinstance(values[ridx], list) else []
        for cidx, _column in enumerate(columns[:5]):
            raw = row_values[cidx] if cidx < len(row_values) else ""
            parts.append(f"<td>{_matrix_cell_html(raw)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")


def _render_timeline(parts: List[str], exhibit: Dict[str, Any]) -> None:
    items = exhibit.get("events") or exhibit.get("steps") or exhibit.get("items") or []
    if not isinstance(items, list) or not items:
        items = [
            {"year": "Now", "title": "Baseline", "description": "Establish the current evidence base."},
            {"year": "Next", "title": "Validate", "description": "Close the highest-risk assumptions."},
            {"year": "Later", "title": "Commit", "description": "Move only after proof gates are met."},
        ]
    parts.append("<div class='timeline'>")
    for idx, item in enumerate(items[:6], start=1):
        if isinstance(item, dict):
            year = _text(item.get("year") or item.get("date") or item.get("label") or str(idx))
            title = _text(item.get("title") or item.get("milestone") or f"Milestone {idx}")
            desc = _text(item.get("description") or item.get("text") or item.get("fact") or "")
        else:
            year, title, desc = str(idx), f"Milestone {idx}", _text(item)
        parts.append("<div class='timeline-item'>")
        parts.append(f"<div class='timeline-year'>{_e(_compact(year, 8))}</div>")
        parts.append(f"<div class='timeline-card'><strong>{_e(_compact(title, 72))}</strong><div>{_e(_compact(desc, 220))}</div></div>")
        parts.append("</div>")
    parts.append("</div>")


def _render_process(parts: List[str], exhibit: Dict[str, Any]) -> None:
    steps = exhibit.get("steps") or exhibit.get("items") or []
    if not isinstance(steps, list) or not steps:
        steps = [{"title": "Assess", "description": "Define the baseline."}, {"title": "Prioritize", "description": "Select the highest-value use cases."}, {"title": "Scale", "description": "Build repeatable operating routines."}, {"title": "Govern", "description": "Install decision gates and safeguards."}]
    parts.append("<div class='process'>")
    for idx, item in enumerate(steps[:4], start=1):
        if isinstance(item, dict):
            title = _text(item.get("title") or item.get("name") or f"Step {idx}")
            desc = _text(item.get("description") or item.get("text") or item.get("action") or "")
        else:
            title, desc = f"Step {idx}", _text(item)
        parts.append(f"<div class='process-step'><span>{idx:02d}</span><strong>{_e(title)}</strong><div>{_e(desc)}</div></div>")
    parts.append("</div>")


def _render_data_basis(parts: List[str], basis: Any, labels: Dict[str, str]) -> None:
    rows = [item for item in _as_list(basis) if isinstance(item, dict)]
    if not rows:
        return
    parts.append("<details class='data-basis'>")
    parts.append(f"<summary>{_e(labels.get('data_basis') or 'Sources')}</summary>")
    parts.append("<ul>")
    for item in rows[:8]:
        basis_id = _text(item.get("id") or "")
        value = _text(item.get("value") or "")
        fact = _compact(_text(item.get("fact") or item.get("text") or item.get("description") or ""), 220)
        title = _compact(_text(item.get("source_title") or item.get("title") or item.get("domain") or ""), 80)
        url = _text(item.get("url") or "")
        left = f"<span class='basis-id'>{_e(basis_id)}</span> " if basis_id else ""
        if value:
            left += f"{_e(value)} - "
        body = _e(fact or title or "Retained evidence item")
        if url:
            source = f" <a href='{_e(url)}'>{_e(title or _display_domain(url) or 'Source')}</a>"
        elif title:
            source = f" {_e(title)}"
        else:
            source = ""
        parts.append(f"<li>{left}{body}{source}</li>")
    parts.append("</ul></details>")


def _render_methodology(parts: List[str], report: Dict[str, Any], labels: Dict[str, str]) -> None:
    parts.append("<section class='methodology'>")
    zh = labels.get("contents") == "目录"
    text_value = _client_source_note(report.get("references", []), report.get("source_count") or 0, zh=zh)
    parts.append(f"<p>{_e(text_value)}</p>")
    parts.append("</section>")


def _action_summary(actions: Any, language: str) -> str:
    items = _normalize_actions(actions)[:3]
    if not items:
        return ""
    if str(language or "").lower().startswith("zh"):
        phrases = []
        for item in items:
            horizon = item.get("horizon") or ""
            action = item.get("action") or ""
            metric = item.get("success_metric") or ""
            phrase = f"{horizon}：{action}" if horizon else action
            if metric:
                phrase += f"（成功标准：{metric}）"
            phrases.append(phrase)
        return "管理层下一步应整合进执行节奏：" + "；".join(phrases) + "。"
    phrases = []
    for item in items:
        horizon = item.get("horizon") or ""
        action = item.get("action") or ""
        metric = item.get("success_metric") or ""
        phrase = f"{horizon}: {action}" if horizon else action
        if metric:
            phrase += f", with success measured by {metric[0].lower() + metric[1:] if metric else metric}"
        phrases.append(phrase.rstrip("."))
    if not phrases:
        return ""
    if len(phrases) == 1:
        joined = phrases[0]
    else:
        joined = "; ".join(phrases[:-1]) + "; and " + phrases[-1]
    return "Near-term leadership should focus on " + joined + "."


def _client_source_note(references: Any, source_count: int, *, zh: bool = False) -> str:
    refs = _normalize_references(references)
    domains = []
    for ref in refs:
        domain = ref.get("domain") or _domain(ref.get("url") or "")
        if domain and domain not in domains:
            domains.append(domain)
    count = max(int(source_count or 0), len(refs))
    if not count and not domains:
        return _default_methodology("zh" if zh else "en")
    if domains:
        shown = ", ".join(domains[:5])
        if zh:
            tail = "等公开来源" if len(domains) > 5 else "公开来源"
            return f"本文基于 {count} 个保留公开来源形成，覆盖 {shown}{tail}。图表中的数字尽量保留到原始 URL；用于投资、交易或运营决策前仍需独立核验。"
        tail = " and other public sources" if len(domains) > 5 else " public sources"
        return f"This article draws on {count} retained public sources across {shown}{tail}. Charted figures preserve URL traceability where available; numeric claims, timelines and scenarios should be independently validated before investment or operating use."
    if zh:
        return f"本文基于 {count} 个保留公开来源形成。用于投资、交易或运营决策前，数字、时间线和情景判断仍需独立核验。"
    return f"This article draws on {count} retained public sources. Numeric claims, timelines and scenarios should be independently validated before investment or operating use."


def _svg_bar(exhibit: Dict[str, Any]) -> str:
    categories, values = _chart_values(exhibit)
    categories = categories[:8]
    values = values[: len(categories)]
    if not categories:
        categories, values = ["A", "B", "C"], [60, 45, 30]
    max_value = max([abs(v) for v in values] + [1])
    width, height = 760, max(270, 54 * len(categories) + 70)
    left, right, top, row_h = 190, 50, 26, 42
    bar_w = width - left - right
    out = [f"<svg class='svg-chart' viewBox='0 0 {width} {height}' role='img'>"]
    for idx, (label, value) in enumerate(zip(categories, values)):
        y = top + idx * row_h
        w = max(2, (abs(value) / max_value) * bar_w)
        klass = ["bar-primary", "bar-secondary", "bar-tertiary"][idx % 3]
        out.append(f"<text class='chart-label' x='0' y='{y + 21}'>{_e(_compact(label, 34))}</text>")
        out.append(f"<rect class='{klass}' x='{left}' y='{y}' width='{w:.1f}' height='26'></rect>")
        out.append(f"<text class='chart-small' x='{left + w + 8:.1f}' y='{y + 18}'>{_e(_format_value(value))}</text>")
    out.append("</svg>")
    return "\n".join(out)


def _svg_line(exhibit: Dict[str, Any]) -> str:
    categories = _list_text(exhibit.get("categories") or exhibit.get("x_labels") or exhibit.get("labels"))
    series = _series(exhibit)
    if not categories or not series:
        categories = ["2024", "2025", "2026", "2027"]
        series = [{"name": "Index", "values": [40, 52, 68, 82]}]
    width, height = 760, 340
    left, right, top, bottom = 58, 28, 28, 58
    all_values = [v for item in series for v in item.get("values", [])]
    min_v = min(all_values + [0])
    max_v = max(all_values + [1])
    if abs(max_v - min_v) < 1e-6:
        max_v += 1
    plot_w = width - left - right
    plot_h = height - top - bottom
    out = [f"<svg class='svg-chart' viewBox='0 0 {width} {height}' role='img'>"]
    for i in range(5):
        y = top + i * plot_h / 4
        out.append(f"<line class='chart-grid' x1='{left}' x2='{width - right}' y1='{y:.1f}' y2='{y:.1f}' />")
    for sidx, item in enumerate(series[:3]):
        values = item.get("values", [])[: len(categories)]
        points = []
        for idx, value in enumerate(values):
            x = left + (idx / max(1, len(categories) - 1)) * plot_w
            y = top + (1 - ((value - min_v) / (max_v - min_v))) * plot_h
            points.append((x, y))
        d = " ".join(("M" if idx == 0 else "L") + f"{x:.1f},{y:.1f}" for idx, (x, y) in enumerate(points))
        klass = ["line-primary", "line-secondary", "line-tertiary"][sidx % 3]
        out.append(f"<path class='{klass}' d='{d}' />")
        for x, y in points:
            out.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='white' stroke='currentColor'></circle>")
        out.append(f"<text class='chart-small' x='{left + sidx * 170}' y='{height - 12}'>{_e(item.get('name') or f'Series {sidx + 1}')}</text>")
    for idx, label in enumerate(categories):
        x = left + (idx / max(1, len(categories) - 1)) * plot_w
        out.append(f"<text class='chart-small' x='{x - 18:.1f}' y='{height - 34}'>{_e(_compact(label, 12))}</text>")
    out.append("</svg>")
    return "\n".join(out)


def _svg_bubble(exhibit: Dict[str, Any]) -> str:
    points = exhibit.get("points") or []
    if not isinstance(points, list) or not points:
        points = [{"label": "Now", "x": 72, "y": 78, "size": 70}, {"label": "Next", "x": 55, "y": 62, "size": 54}, {"label": "Later", "x": 38, "y": 42, "size": 40}]
    width, height = 760, 420
    left, right, top, bottom = 76, 126, 36, 68
    plot_w = width - left - right
    plot_h = height - top - bottom
    x_label = _text(exhibit.get("x_label") or "Proof available")
    y_label = _text(exhibit.get("y_label") or "Actionability")
    out = [f"<svg class='svg-chart' viewBox='0 0 {width} {height}' role='img'>"]
    mid_x = left + plot_w / 2
    mid_y = top + plot_h / 2
    out.append(f"<line class='chart-grid' x1='{mid_x:.1f}' x2='{mid_x:.1f}' y1='{top}' y2='{height - bottom}' />")
    out.append(f"<line class='chart-grid' x1='{left}' x2='{width - right}' y1='{mid_y:.1f}' y2='{mid_y:.1f}' />")
    out.append(f"<line class='chart-axis' x1='{left}' x2='{width - right}' y1='{height - bottom}' y2='{height - bottom}' />")
    out.append(f"<line class='chart-axis' x1='{left}' x2='{left}' y1='{top}' y2='{height - bottom}' />")
    out.append(f"<text class='chart-small' x='{width - right - 140}' y='{height - 14}'>{_e(_compact(x_label, 36))}</text>")
    out.append(f"<text class='chart-small' x='{left}' y='{top - 10}'>{_e(_compact(y_label, 36))}</text>")
    out.append(f"<text class='chart-small' x='{left + 8}' y='{height - bottom - 10}'>Lower</text>")
    out.append(f"<text class='chart-small' x='{width - right - 45}' y='{height - bottom - 10}'>Higher</text>")
    colors = ["var(--green)", "var(--blue)", "var(--amber)", "var(--forest)"]
    placed_labels: List[Tuple[float, float, float, float]] = []
    plotted = []
    for idx, point in enumerate(points[:8]):
        x = left + (_number(point.get("x"), 50) / 100.0) * plot_w
        y = top + (1 - _number(point.get("y"), 50) / 100.0) * plot_h
        r = max(8, min(34, _number(point.get("size"), 40) / 3.2))
        label = _text(point.get("label") or f"Point {idx + 1}")
        plotted.append((idx, x, y, r, label))
    for idx, x, y, r, label in sorted(plotted, key=lambda item: item[3], reverse=True):
        out.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' fill='{colors[idx % len(colors)]}' opacity='.78'></circle>")
    for idx, x, y, r, label in plotted:
        label_text = _compact(label, 28)
        box_w = min(184.0, max(74.0, len(label_text) * 6.7 + 18.0))
        box_h = 24.0
        prefer_right = x < left + plot_w * 0.62
        if prefer_right:
            box_x = x + r + 12.0
            if box_x + box_w > width - 10:
                box_x = x - r - 12.0 - box_w
        else:
            box_x = x - r - 12.0 - box_w
            if box_x < 10:
                box_x = x + r + 12.0
        box_x = max(8.0, min(width - box_w - 8.0, box_x))
        box_y = max(top + 4.0, min(height - bottom - box_h - 4.0, y - box_h / 2))
        direction = 1 if idx % 2 == 0 else -1
        step = box_h + 5.0
        attempts = 0
        while any(_rects_overlap((box_x, box_y, box_w, box_h), prior) for prior in placed_labels) and attempts < 12:
            attempts += 1
            candidate_y = box_y + direction * step
            if candidate_y < top + 4.0 or candidate_y + box_h > height - bottom - 4.0:
                direction *= -1
                candidate_y = box_y + direction * step
            box_y = max(top + 4.0, min(height - bottom - box_h - 4.0, candidate_y))
        placed_labels.append((box_x, box_y, box_w, box_h))
        edge_x = box_x if box_x > x else box_x + box_w
        out.append(f"<line class='bubble-leader' x1='{x:.1f}' y1='{y:.1f}' x2='{edge_x:.1f}' y2='{box_y + box_h / 2:.1f}' />")
        out.append(f"<rect class='bubble-label-box' x='{box_x:.1f}' y='{box_y:.1f}' width='{box_w:.1f}' height='{box_h:.1f}' rx='3'></rect>")
        out.append(f"<text class='chart-label' x='{box_x + 9:.1f}' y='{box_y + 16.5:.1f}'>{_e(label_text)}</text>")
    out.append("</svg>")
    return "\n".join(out)


def _exhibits_by_anchor(exhibits: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for idx, exhibit in enumerate(exhibits, start=1):
        anchor = _text(exhibit.get("after_section_id") or exhibit.get("section_id") or "")
        if not anchor:
            anchor = f"section-{min(idx, 8)}"
        grouped.setdefault(anchor, []).append(exhibit)
    return grouped


def _rects_overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def _normalize_sections(value: Any, takeaways: List[str]) -> List[Dict[str, Any]]:
    sections = []
    for idx, item in enumerate(_as_list(value), start=1):
        section = dict(item) if isinstance(item, dict) else {"title": str(item), "paragraphs": [str(item)]}
        title = _text(section.get("title") or section.get("headline") or f"Section {idx}")
        lead = _text(section.get("lead") or section.get("summary") or section.get("dek") or "")
        paragraphs = _list_text(section.get("paragraphs") or section.get("body") or section.get("content"))
        evidence = _list_text(section.get("evidence") or section.get("proof_points") or section.get("facts"))
        so_what = _text(section.get("so_what") or section.get("management_implication") or section.get("implication") or "")
        if not paragraphs and lead:
            paragraphs = [lead]
        sections.append(
            {
                "id": _slug(section.get("id") or f"section-{idx}"),
                "title": _compact(title, 120),
                "lead": _compact(lead, 260),
                "paragraphs": [_compact(x, 900) for x in paragraphs[:8]],
                "evidence": [_compact(x, 260) for x in evidence[:5]],
                "so_what": _compact(so_what, 420),
            }
        )
    return [s for s in sections if s["title"] or s["paragraphs"]]


def _normalize_takeaways(value: Any) -> List[str]:
    out = []
    if isinstance(value, dict):
        for key in ("items", "bullets", "points", "takeaways", "key_takeaways", "keyTakeaways", "findings", "messages"):
            nested = value.get(key)
            if nested:
                out.extend(_normalize_takeaways(nested))
        if out:
            return _dedupe(out)
    for item in _as_list(value):
        if isinstance(item, dict):
            claim = _text(item.get("takeaway") or item.get("claim") or item.get("finding") or item.get("title") or item.get("headline") or item.get("summary") or item.get("text"))
            implication = _text(item.get("implication") or item.get("management_implication") or item.get("so_what") or item.get("why_it_matters"))
            text = f"{claim} {implication}".strip() if implication and implication not in claim else claim
        else:
            text = item
        text = _text(text)
        if text:
            out.append(text)
    return _dedupe(out)


def _ensure_three_takeaways(
    takeaways: List[str],
    data: Dict[str, Any],
    sections: List[Dict[str, Any]],
    topic: str,
    language: str,
    dek: str,
) -> List[str]:
    candidates = list(takeaways)
    candidates.extend(_normalize_takeaways(data.get("management_implications") or data.get("implications")))
    for section in sections:
        candidates.append(section.get("so_what", ""))
        if section.get("title"):
            candidates.append(section["title"])
    defaults = (
        [
            _compact(dek, 220),
            _compact(topic, 180),
            "The source boundary should be kept visible before major decisions are made.",
            "Leadership should move through explicit validation gates before committing capital or operating resources.",
        ]
        if not str(language or "").lower().startswith("zh")
        else [
            _compact(dek, 220),
            _compact(topic, 180),
            "重大决策前必须保留清晰的证据边界和核验任务。",
            "管理层应通过明确核验门槛推进，而不是基于未证实假设投入资源。",
        ]
    )
    candidates.extend(defaults)
    return _dedupe([x for x in candidates if _text(x)])[:3]


def _normalize_exhibits(value: Any) -> List[Dict[str, Any]]:
    exhibits = []
    for idx, item in enumerate(_as_list(value), start=1):
        if not isinstance(item, dict):
            continue
        exhibit = dict(item)
        if is_internal_workbench_exhibit(exhibit):
            continue
        exhibit["id"] = _slug(exhibit.get("id") or f"exhibit-{idx}")
        exhibit["no"] = str(exhibit.get("no") or exhibit.get("exhibit_no") or idx)
        exhibit["title"] = _compact(_text(exhibit.get("title") or f"Exhibit {idx}"), 130)
        exhibit["subtitle"] = _compact(_text(exhibit.get("subtitle") or ""), 220)
        exhibit["type"] = _text(exhibit.get("type") or "bar").lower().replace("-", "_")
        exhibit["caption"] = _compact(_text(exhibit.get("caption") or exhibit.get("summary") or ""), 360)
        exhibit["source_note"] = _compact(_text(exhibit.get("source_note") or exhibit.get("source") or ""), 260)
        exhibit["data_basis"] = _normalize_data_basis(exhibit.get("data_basis") or exhibit.get("basis") or [])
        exhibit["evidence_quality"] = _compact(_text(exhibit.get("evidence_quality") or ""), 120)
        for key in ("metrics", "items", "events", "steps", "categories", "labels", "x_labels", "rows", "columns", "values", "series", "points"):
            if key in exhibit:
                exhibit[key] = clean_client_value(exhibit[key])
        if not exhibit.get("after_section_id"):
            exhibit["after_section_id"] = f"section-{min(idx, 8)}"
        exhibits.append(exhibit)
    for idx, exhibit in enumerate(exhibits, start=1):
        exhibit["no"] = str(idx)
        exhibit["id"] = _slug(exhibit.get("id") or f"exhibit-{idx}")
        if not _text(exhibit.get("after_section_id")):
            exhibit["after_section_id"] = f"section-{min(idx, 8)}"
    return exhibits


def _normalize_data_basis(value: Any) -> List[Dict[str, str]]:
    basis = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            basis.append(
                {
                    "id": _compact(_text(item.get("id") or f"E{idx}"), 24),
                    "value": _compact(_text(item.get("value") or item.get("display_value") or ""), 40),
                    "fact": _compact(_text(item.get("fact") or item.get("text") or item.get("description") or ""), 300),
                    "source_title": _compact(_text(item.get("source_title") or item.get("title") or ""), 120),
                    "url": _text(item.get("url") or item.get("source_url") or ""),
                    "domain": _compact(_text(item.get("domain") or ""), 80),
                }
            )
        else:
            text_value = _text(item)
            if text_value:
                basis.append({"id": f"E{idx}", "value": "", "fact": _compact(text_value, 300), "source_title": "", "url": "", "domain": ""})
    return [item for item in basis if item.get("fact") or item.get("url") or item.get("source_title")][:12]


def _normalize_actions(value: Any) -> List[Dict[str, str]]:
    actions = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            actions.append(
                {
                    "horizon": _compact(_text(item.get("horizon") or item.get("timing") or f"Step {idx}"), 70),
                    "action": _compact(_text(item.get("action") or item.get("title") or item.get("name") or ""), 180),
                    "success_metric": _compact(_text(item.get("success_metric") or item.get("metric") or item.get("decision_gate") or ""), 180),
                    "description": _compact(_text(item.get("description") or ""), 180),
                }
            )
        else:
            actions.append({"horizon": f"Step {idx}", "action": _compact(_text(item), 180), "success_metric": "", "description": ""})
    return [x for x in actions if x.get("action")]


def _normalize_references(value: Any) -> List[Dict[str, str]]:
    refs = []
    for idx, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            title = _text(item.get("title") or item.get("name") or item.get("url") or f"Source {idx}")
            url = _text(item.get("url") or "")
            note = _text(item.get("note") or item.get("description") or "")
        else:
            text = _text(item)
            title = text or f"Source {idx}"
            url = _extract_url(text)
            note = text
        if title or url:
            refs.append({"title": _compact(title, 180), "url": url, "note": _compact(note, 220), "domain": _domain(url)})
    return refs


def _chart_values(exhibit: Dict[str, Any]) -> Tuple[List[str], List[float]]:
    categories = _list_text(exhibit.get("categories") or exhibit.get("labels") or exhibit.get("x_labels"))
    series = _series(exhibit)
    if series and not categories:
        categories = [str(i + 1) for i in range(len(series[0].get("values", [])))]
    values = series[0].get("values", []) if series else _values(exhibit.get("values"))
    values = values[: len(categories)] if categories else values
    if not categories and values:
        categories = [f"Item {idx}" for idx in range(1, len(values) + 1)]
    return categories, values


def _series(exhibit: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = exhibit.get("series")
    if raw is None and isinstance(exhibit.get("data"), dict):
        raw = exhibit["data"].get("series") or exhibit["data"].get("datasets")
    out = []
    for idx, item in enumerate(_as_list(raw), start=1):
        if isinstance(item, dict):
            vals = _values(item.get("values") if item.get("values") is not None else item.get("data"))
            if vals:
                out.append({"name": _text(item.get("name") or item.get("label") or f"Series {idx}"), "values": vals})
    if not out and exhibit.get("values") is not None:
        out.append({"name": "Value", "values": _values(exhibit.get("values"))})
    return out


def _fallback_sections(topic: str, takeaways: List[str], language: str) -> List[Dict[str, Any]]:
    if str(language).lower().startswith("zh"):
        base = [
            ("先把议题定义为一个管理层决策，而不是资料综述", "网页报告需要回答资源配置、风险承受和行动节奏，而不只是复述公开信息。"),
            ("证据密度决定报告可信度", "关键判断必须绑定来源、时间、数字和待核验边界。"),
            ("强图表应服务于一个判断", "每个图表都应该解释一个管理问题，而不是填充版面。"),
            ("行动路径需要分阶段", "好的报告会把近期验证、中期扩展和长期承诺拆开。"),
        ]
    else:
        base = [
            ("The topic should be framed as a management decision, not a literature review", "The report should connect evidence to capital allocation, risk appetite and timing."),
            ("Evidence density determines credibility", "Material claims need dates, numbers, source boundaries and open validation tasks."),
            ("Exhibits should carry judgment", "Every exhibit should answer one executive question rather than decorate the page."),
            ("The action path should be staged", "Near-term validation, medium-term scaling and long-term commitments should sit behind different proof gates."),
        ]
    sections = []
    for idx, (title, lead) in enumerate(base, start=1):
        sections.append(
            {
                "id": f"section-{idx}",
                "title": title,
                "lead": lead,
                "paragraphs": [
                    lead,
                    takeaways[(idx - 1) % len(takeaways)] if takeaways else topic,
                    "The public evidence base should be treated as the factual boundary until additional primary research closes open questions.",
                ],
                "evidence": [],
                "so_what": "Use the section to separate decisions that can be made now from decisions that require further validation.",
            }
        )
    return sections


def _fallback_exhibits(topic: str, language: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": "exhibit-1",
            "no": "1",
            "type": "metric_row",
            "title": "Decision readiness should be measured across evidence, economics and execution",
            "metrics": [
                {"value": "3", "label": "proof domains to close before major commitment"},
                {"value": "90d", "label": "practical window for a first evidence sprint"},
                {"value": "1", "label": "integrated leadership agenda"},
            ],
            "source_note": f"{BRAND_NAME} synthesis.",
        },
        {
            "id": "exhibit-2",
            "no": "2",
            "type": "bar",
            "title": "Management should rank proof points by ability to change the decision",
            "categories": ["Customer pull", "Cost position", "Execution capacity", "Partner access", "Policy timing"],
            "series": [{"name": "Priority", "values": [88, 82, 74, 68, 61]}],
            "source_note": f"{BRAND_NAME} synthesis.",
        },
    ]


def _fallback_actions(language: str) -> List[Dict[str, str]]:
    if str(language).lower().startswith("zh"):
        return [
            {"horizon": "0-30 天", "action": "锁定最关键的公开证据和缺口", "success_metric": "关键判断均有来源、日期和待核验项"},
            {"horizon": "30-90 天", "action": "完成客户、成本和伙伴验证", "success_metric": "形成可执行的资源配置门槛"},
            {"horizon": "90 天后", "action": "按证据门槛扩大投入", "success_metric": "重大承诺绑定明确触发条件"},
        ]
    return [
        {"horizon": "0-30 days", "action": "Lock down the few public facts that matter most", "success_metric": "Material claims have sources, dates and open proof points."},
        {"horizon": "30-90 days", "action": "Validate customers, cost and partners", "success_metric": "Resource gates are tied to proof points."},
        {"horizon": "After 90 days", "action": "Scale behind evidence gates", "success_metric": "Major commitments have explicit triggers."},
    ]


def _default_methodology(language: str) -> str:
    if str(language).lower().startswith("zh"):
        return "本报告基于公开资料检索和来源摘录形成。所有数字、时间和情景判断在用于投资、交易或运营决策前仍需独立核验。"
    return "This report is based on public-source collection and excerpt review. Numeric claims, timelines and scenarios should be independently validated before investment, transaction or operating use."


def _estimate_read_time(report: Dict[str, Any]) -> int:
    text = json.dumps(report, ensure_ascii=False)
    words = max(1, len(re.findall(r"\w+|[\u4e00-\u9fff]", text)))
    return max(4, min(35, round(words / 260)))


def _labels(language: str) -> Dict[str, str]:
    return LABELS["zh"] if str(language).lower().startswith("zh") else LABELS["en"]


def _lang_attr(language: str) -> str:
    return "zh-CN" if str(language).lower().startswith("zh") else "en"


def _asset(assets: Dict[str, str], key: str) -> str:
    value = str((assets or {}).get(key) or "")
    return "" if value.lower().endswith(".svg") else value


def _section_image(assets: Dict[str, str], idx: int) -> str:
    for key in (f"image-{idx}", f"section-{idx}", f"article-{idx}"):
        value = _asset(assets, key)
        if value:
            return value
    return ""


def _paragraphs(items: List[str]) -> str:
    return "".join(f"<p>{_e(item)}</p>" for item in items if item)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _list_text(value: Any) -> List[str]:
    out = []
    for item in _as_list(value):
        if isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("title")
                or item.get("description")
                or item.get("finding")
                or item.get("claim")
                or item.get("message")
                or item.get("point")
                or item.get("action")
            )
        else:
            text = item
        text = _text(text)
        if text:
            out.append(text)
    return _dedupe(out)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in (
            "text",
            "title",
            "summary",
            "description",
            "finding",
            "claim",
            "takeaway",
            "implication",
            "management_implication",
            "so_what",
            "why_it_matters",
            "message",
            "point",
            "action",
        ):
            if value.get(key):
                return _text(value.get(key))
        return ""
    if isinstance(value, list):
        return " ".join(_text(x) for x in value if _text(x))
    return _clean_visible_text(re.sub(r"\s+", " ", str(value).strip()))


def _clean_visible_text(text: str) -> str:
    return clean_client_text(text)


def _first_text(value: Any) -> str:
    values = _list_text(value)
    return values[0] if values else ""


def _compact(text: Any, max_chars: int) -> str:
    cleaned = _text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[: max_chars - 1].rstrip()
    truncated = re.sub(r"\s+\S*$", "", truncated).rstrip(" ,;:")
    return truncated + "."


def _dedupe(items: List[str]) -> List[str]:
    out = []
    seen = set()
    for item in items:
        key = re.sub(r"\W+", "", item.lower())[:180]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _values(raw: Any) -> List[float]:
    if isinstance(raw, dict):
        raw = list(raw.values())
    if isinstance(raw, (int, float)):
        raw = [raw]
    values = []
    for item in _as_list(raw):
        try:
            values.append(float(str(item).replace(",", "").replace("%", "").strip()))
        except ValueError:
            continue
    return values


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number - round(number)) < 1e-6:
        return f"{number:.0f}"
    return f"{number:.1f}"


def _matrix_cell_html(value: Any) -> str:
    text_value = _format_value(value).strip()
    if not text_value:
        return "<span class='matrix-text'>n/a</span>"
    lower = text_value.lower()
    if any(token in lower for token in ("strong", "high", "ready", "clear")):
        return f"<span class='matrix-badge'>{_e(text_value)}</span>"
    if any(token in lower for token in ("some", "medium", "partial", "watch")):
        return f"<span class='matrix-badge medium'>{_e(text_value)}</span>"
    if any(token in lower for token in ("single", "open", "low", "gap", "limited")):
        return f"<span class='matrix-badge low'>{_e(text_value)}</span>"
    if len(text_value) <= 18:
        return f"<span class='matrix-score'>{_e(text_value)}</span>"
    return f"<span class='matrix-text'>{_e(text_value)}</span>"


def _slug(value: Any) -> str:
    text = _text(value).lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return text[:64] or "section"


def _extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s,;)\]]+", text or "")
    return match.group(0) if match else ""


def _domain(url: str) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().replace("www.", "")


def _display_domain(url: str) -> str:
    return _brand_safe_text(_domain(url))


def _brand_safe_text(value: Any) -> str:
    text = str(value or "")
    legacy = "".join(["b", "c", "g"])
    text = re.sub(rf"\bwww\.{legacy}\.com\b", BRAND_NAME, text, flags=re.I)
    text = re.sub(rf"\b{legacy}\.com\b", BRAND_NAME, text, flags=re.I)
    text = re.sub(r"\bBoston\s+Consulting\s+Group\b", BRAND_NAME, text, flags=re.I)
    text = re.sub(rf"\b{legacy}\b", BRAND_NAME, text, flags=re.I)
    return text


def _is_exact_url_or_asset(value: str) -> bool:
    return bool(re.match(r"^\s*(?:https?://|assets/|#)[^\s]*\s*$", value or ""))


def _e(value: Any) -> str:
    raw = str(value or "")
    safe = raw if _is_exact_url_or_asset(raw) else _brand_safe_text(raw)
    return html.escape(safe, quote=True)
