from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME.get("brand_name", "BlueOcean")

CSS = f"""
:root {{ --navy:{PALETTE.get('navy_dark', '#051C2C')}; --accent:{PALETTE.get('accent', '#003087')}; --blue:{PALETTE.get('bright_blue', '#3273F6')}; --ink:{PALETTE.get('ink', '#333333')}; --muted:{PALETTE.get('subtle', '#64696E')}; --line:{PALETTE.get('line', '#C8C8C8')}; --panel:{PALETTE.get('panel', '#F5F7FA')}; --lightblue:{PALETTE.get('light_blue_fill', '#EBF5FF')}; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; padding:0; background:#111; font-family: Trebuchet MS, Aptos, Arial, sans-serif; color:var(--ink); }}
.deck {{ width:100vw; height:100vh; overflow:hidden; }}
.slide {{ display:none; width:100vw; height:100vh; background:#fff; padding:5.5vh 6vw 5vh; position:relative; overflow:hidden; }}
.slide.active {{ display:block; }}
.brand {{ position:absolute; top:2.4vh; right:4.5vw; color:var(--accent); font-size:1.05vw; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
.footer {{ position:absolute; left:4.5vw; right:4.5vw; bottom:2vh; display:flex; justify-content:space-between; color:#a0a6ad; font-size:.82vw; }}
h1 {{ font-size:4.0vw; line-height:1.05; font-weight:400; margin:0; color:var(--navy); max-width:62vw; }}
h2 {{ font-size:2.15vw; line-height:1.12; font-weight:400; margin:0 0 2.6vh; color:var(--ink); max-width:74vw; }}
.subtitle {{ color:var(--muted); font-size:1.18vw; margin-top:2vh; max-width:55vw; }}
.kicker {{ color:var(--accent); font-size:.95vw; font-weight:700; letter-spacing:.08em; text-transform:uppercase; margin-bottom:1vh; }}
.cover {{ background:linear-gradient(105deg, var(--navy) 0%, var(--navy) 43%, #fff 43%, #fff 100%); color:#fff; }}
.cover h1 {{ color:#fff; max-width:36vw; margin-top:14vh; }}
.cover .subtitle {{ color:#d9e6f2; max-width:34vw; }}
.cover .right-note {{ position:absolute; left:50vw; top:19vh; width:38vw; color:var(--ink); font-size:2vw; line-height:1.25; }}
.grid-3 {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:2vw; margin-top:4vh; }}
.card {{ border-top:.42vh solid var(--accent); background:#fff; padding:1.4vw; min-height:20vh; box-shadow:0 0 0 1px var(--line); }}
.card .num {{ color:var(--accent); font-size:2.3vw; font-weight:700; margin-bottom:1vh; }}
.card p {{ font-size:1.15vw; line-height:1.28; margin:0; }}
.two-col {{ display:grid; grid-template-columns: 0.95fr 1.05fr; gap:4vw; margin-top:3vh; }}
p, li {{ font-size:1.14vw; line-height:1.42; }}
ul {{ margin-top:0; }}
.lead {{ font-size:2.05vw; line-height:1.18; color:var(--accent); max-width:42vw; margin-bottom:3vh; }}
.visual {{ max-width:100%; max-height:58vh; object-fit:contain; }}
.process {{ display:grid; grid-template-columns: repeat(7, 1fr); gap:.8vw; margin-top:6vh; }}
.step {{ background:var(--panel); border:1px solid var(--line); padding:.85vw; min-height:25vh; }}
.step b {{ color:var(--accent); display:block; margin-bottom:1vh; font-size:1vw; }}
.step span {{ font-size:.9vw; line-height:1.25; }}
.controls {{ position:fixed; right:1.5vw; bottom:1.5vh; color:#777; font-size:.9vw; z-index:99; }}
@media print {{ .slide {{ display:block; width:13.333in; height:7.5in; page-break-after:always; }} .controls {{ display:none; }} }}
"""

JS = """
let current = 0;
const slides = [...document.querySelectorAll('.slide')];
function show(i){ current = Math.max(0, Math.min(slides.length-1, i)); slides.forEach((s,idx)=>s.classList.toggle('active', idx===current)); document.querySelector('.controls').textContent = `${current+1}/${slides.length}  ← → / F`; }
document.addEventListener('keydown', e => { if(['ArrowRight','PageDown',' '].includes(e.key)) show(current+1); if(['ArrowLeft','PageUp'].includes(e.key)) show(current-1); if(e.key.toLowerCase()==='f') document.documentElement.requestFullscreen?.(); });
show(0);
"""


def render_presentation_html(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    title = report.get("report_title", topic)
    parts: List[str] = ["<!DOCTYPE html><html><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/>", f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main class='deck'>"]

    _slide(parts, "cover", f"<div class='kicker'>{html.escape(BRAND_NAME)}</div><h1>{html.escape(title)}</h1><div class='subtitle'>{html.escape(report.get('report_subtitle',''))}</div><div class='right-note'>{html.escape(topic)}</div>", "Cover", 1)

    summary = report.get("executive_summary", [])[:6]
    cards = []
    for idx, item in enumerate(summary, start=1):
        cards.append(f"<div class='card'><div class='num'>{idx}</div><p>{html.escape(item)}</p></div>")
    _slide(parts, "", f"<div class='kicker'>Key highlights</div><h2>The research narrows the agenda to a few high-conviction issues</h2><div class='grid-3'>{''.join(cards)}</div>", "Highlights", 2)

    actions = []
    for idx, item in enumerate(report.get("action_plan", [])[:6], start=1):
        actions.append(f"<div class='step'><b>{html.escape(item.get('horizon', f'Phase {idx}'))}</b><span>{html.escape(item.get('action', 'Translate evidence into a management decision.'))}</span></div>")
    if actions:
        _slide(parts, "", f"<div class='kicker'>Management agenda</div><h2>Actions are sequenced by evidence gates and decision readiness</h2><div class='process'>{''.join(actions)}</div>", "Management agenda", 3)

    slide_no = 4
    for section in report.get("sections", [])[:6]:
        visual = assets.get(section.get("visual_hint", ""), "")
        paras = "".join(f"<p>{html.escape(p)}</p>" for p in section.get("paragraphs", [])[:3])
        image = f"<img class='visual' src='{html.escape(visual)}'/>" if visual else ""
        body = f"<h2>{html.escape(section.get('title','Section'))}</h2><div class='lead'>{html.escape(section.get('lead',''))}</div><div class='two-col'><div>{paras}</div><div>{image}</div></div>"
        _slide(parts, "", body, section.get("title", "Section"), slide_no)
        slide_no += 1

    for key, path in [(k, v) for k, v in assets.items() if k.startswith("chart-")][:4]:
        _slide(parts, "", f"<div class='kicker'>Exhibit</div><h2>Evidence page</h2><img class='visual' src='{html.escape(path)}'/>", "Exhibit", slide_no)
        slide_no += 1

    parts.append("</main><div class='controls'></div><script>" + JS + "</script></body></html>")
    output_file.write_text("\n".join(parts), encoding="utf-8")
    return output_file


def _slide(parts: List[str], cls: str, body: str, footer: str, num: int) -> None:
    parts.append(f"<section class='slide {cls}'><div class='brand'>{html.escape(BRAND_NAME)}</div>{body}<div class='footer'><span>{html.escape(footer)}</span><span>{num}</span></div></section>")
