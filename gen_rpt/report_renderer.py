from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List


CSS = """
:root {
  --accent: #0b6e6e;
  --ink: #1f2937;
  --muted: #6b7280;
  --line: #dbe4ea;
  --paper: #ffffff;
  --bg: #f4f7f9;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.72;
}
.container {
  width: min(1100px, calc(100% - 48px));
  margin: 32px auto 56px;
}
.hero, .block {
  background: var(--paper);
  border-radius: 20px;
  padding: 32px;
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.06);
  margin-bottom: 22px;
}
.kicker {
  color: var(--accent);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  font-size: 40px;
  line-height: 1.15;
  margin: 12px 0;
}
h2 {
  font-size: 28px;
  margin: 0 0 16px;
}
h3 {
  font-size: 22px;
  margin-top: 0;
}
.subtitle, .meta, .caption, .source-note {
  color: var(--muted);
}
.grid {
  display: grid;
  gap: 18px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.summary-list li, .takeaways li, .refs li {
  margin-bottom: 10px;
}
img.visual {
  width: 100%;
  border-radius: 16px;
  border: 1px solid var(--line);
  margin: 18px 0 8px;
}
.callout {
  border-left: 4px solid var(--accent);
  background: #f6fbfb;
  padding: 18px 18px 18px 20px;
  border-radius: 12px;
  margin: 18px 0;
}
a { color: var(--accent); }
@media (max-width: 860px) {
  .grid { grid-template-columns: 1fr; }
  .container { width: min(100%, calc(100% - 24px)); }
  h1 { font-size: 32px; }
}
"""


def render_report_html(report: Dict, assets: Dict[str, str], output_file: Path, topic: str) -> Path:
    sections = report.get("sections", [])
    refs = report.get("references", [])

    parts: List[str] = [
        "<!DOCTYPE html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8' />",
        "<meta name='viewport' content='width=device-width, initial-scale=1' />",
        f"<title>{html.escape(report.get('report_title', topic))}</title>",
        f"<style>{CSS}</style>",
        "</head>",
        "<body><div class='container'>",
        "<section class='hero'>",
        "<div class='kicker'>Deep Research Report</div>",
        f"<h1>{html.escape(report.get('report_title', topic))}</h1>",
        f"<p class='subtitle'>{html.escape(report.get('report_subtitle', ''))}</p>",
        f"<p class='meta'>选题：{html.escape(topic)}</p>",
        "</section>",
    ]

    summary = report.get("executive_summary", [])
    if summary:
        parts.append("<section class='block'><h2>执行摘要</h2><ul class='summary-list'>")
        for item in summary:
            parts.append(f"<li>{html.escape(item)}</li>")
        parts.append("</ul></section>")

    overview_cards = []
    for key, rel_path in assets.items():
        if key.startswith("card-"):
            overview_cards.append(rel_path)
    if overview_cards:
        parts.append("<section class='block'><h2>关键洞察图卡</h2>")
        for rel_path in overview_cards[:2]:
            parts.append(f"<img class='visual' src='{html.escape(rel_path)}' alt='insight card' />")
        parts.append("</section>")

    for section in sections:
        parts.append("<section class='block'>")
        parts.append(f"<h2>{html.escape(section.get('title', '章节'))}</h2>")
        lead = section.get("lead", "")
        if lead:
            parts.append(f"<p class='subtitle'>{html.escape(lead)}</p>")

        visual_key = section.get("visual_hint", "")
        paragraphs = section.get("paragraphs", [])
        inserted = False
        for idx, paragraph in enumerate(paragraphs):
            parts.append(f"<p>{html.escape(paragraph)}</p>")
            if not inserted and idx >= 1 and visual_key in assets:
                parts.append(f"<img class='visual' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
                inserted = True

        takeaways = section.get("key_takeaways", [])
        if takeaways:
            parts.append("<div class='callout'><strong>本节要点</strong><ul class='takeaways'>")
            for item in takeaways:
                parts.append(f"<li>{html.escape(item)}</li>")
            parts.append("</ul></div>")

        if not inserted and visual_key in assets:
            parts.append(f"<img class='visual' src='{html.escape(assets[visual_key])}' alt='{html.escape(visual_key)}' />")
        parts.append("</section>")

    charts = [path for key, path in assets.items() if key.startswith("chart-")]
    if charts:
        parts.append("<section class='block'><h2>数据图表</h2>")
        for rel_path in charts:
            parts.append(f"<img class='visual' src='{html.escape(rel_path)}' alt='chart' />")
        parts.append("</section>")

    if refs:
        parts.append("<section class='block'><h2>参考资料</h2><ol class='refs'>")
        for ref in refs:
            title = html.escape(ref.get("title", ref.get("url", "来源")))
            url = html.escape(ref.get("url", "#"))
            note = html.escape(ref.get("note", ""))
            parts.append(f"<li><a href='{url}' target='_blank' rel='noreferrer'>{title}</a><div class='source-note'>{note}</div></li>")
        parts.append("</ol></section>")

    parts.append("</div></body></html>")

    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file
