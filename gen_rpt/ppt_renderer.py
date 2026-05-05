from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME.get("brand_name", "BlueOcean")

NAVY = PALETTE.get("navy_dark", "#051C2C")
ACCENT = PALETTE.get("accent", "#003087")
BRIGHT_BLUE = PALETTE.get("bright_blue", "#3273F6")
INK = PALETTE.get("ink", "#333333")
MUTED = PALETTE.get("subtle", "#64696E")
LINE = PALETTE.get("line", "#C8C8C8")
LIGHT_BLUE = PALETTE.get("light_blue_fill", "#EBF5FF")
PANEL = PALETTE.get("panel", "#F5F7FA")

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
LM = Inches(0.62)
RM = Inches(0.62)
TOP = Inches(0.42)
BOTTOM = Inches(0.36)


def render_pptx(report: Dict, assets: Dict[str, str], output_file: Path, topic: str, language: str = "zh") -> Path:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    title = report.get("report_title", topic)
    subtitle = report.get("report_subtitle", "")

    _slide_cover(prs, title, subtitle, topic)
    _slide_toc(prs, report)
    _slide_highlights(prs, report)
    _slide_approach(prs, report)

    cards = [(k, v) for k, v in assets.items() if k.startswith("card-")]
    if cards:
        _slide_image_gallery(prs, "Three implications shape the management agenda", [p for _, p in cards[:3]], output_file.parent)

    for section in report.get("sections", [])[:6]:
        _slide_section(prs, section, assets, output_file.parent)

    chart_paths = [v for k, v in assets.items() if k.startswith("chart-")]
    for idx, chart_path in enumerate(chart_paths[:4], start=1):
        _slide_chart(prs, f"Exhibit {idx}: evidence clarifies the strategic trade-off", chart_path, output_file.parent)

    _slide_closing(prs, report, topic)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_file))
    return output_file


def _blank(prs: Presentation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = _rgb("#FFFFFF")
    return slide


def _add_brand(slide, page_title: str = "") -> None:
    tx = slide.shapes.add_textbox(SLIDE_W - Inches(1.55), Inches(0.18), Inches(1.1), Inches(0.25))
    p = tx.text_frame.paragraphs[0]
    p.text = BRAND_NAME
    p.font.size = Pt(8)
    p.font.bold = True
    p.font.color.rgb = _rgb(ACCENT)
    p.alignment = PP_ALIGN.RIGHT

    foot = slide.shapes.add_textbox(LM, SLIDE_H - Inches(0.28), SLIDE_W - LM - RM, Inches(0.16))
    p2 = foot.text_frame.paragraphs[0]
    p2.text = page_title
    p2.font.size = Pt(6)
    p2.font.color.rgb = _rgb("#A0A6AD")


def _title(slide, title: str, subtitle: str = "") -> None:
    box = slide.shapes.add_textbox(LM, TOP, Inches(10.3), Inches(0.82))
    tf = box.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    p.text = _trim(title, 110)
    p.font.size = Pt(22)
    p.font.color.rgb = _rgb(INK)
    p.font.bold = False
    if subtitle:
        p2 = tf.add_paragraph()
        p2.text = _trim(subtitle, 145)
        p2.font.size = Pt(9)
        p2.font.color.rgb = _rgb(MUTED)
    line = slide.shapes.add_shape(1, LM, Inches(1.25), SLIDE_W - LM - RM, Inches(0.01))
    line.fill.solid(); line.fill.fore_color.rgb = _rgb(LINE)
    line.line.color.rgb = _rgb(LINE)


def _slide_cover(prs, title: str, subtitle: str, topic: str) -> None:
    slide = _blank(prs)
    left = slide.shapes.add_shape(1, 0, 0, Inches(5.15), SLIDE_H)
    left.fill.solid(); left.fill.fore_color.rgb = _rgb(NAVY)
    left.line.fill.background()
    band = slide.shapes.add_shape(1, Inches(5.15), 0, Inches(0.16), SLIDE_H)
    band.fill.solid(); band.fill.fore_color.rgb = _rgb(BRIGHT_BLUE)
    band.line.fill.background()

    box = slide.shapes.add_textbox(Inches(0.72), Inches(0.72), Inches(4.25), Inches(4.9))
    tf = box.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = BRAND_NAME.upper()
    p.font.size = Pt(10); p.font.bold = True; p.font.color.rgb = _rgb("#FFFFFF")
    p2 = tf.add_paragraph(); p2.text = _trim(title, 90); p2.font.size = Pt(28); p2.font.color.rgb = _rgb("#FFFFFF"); p2.space_before = Pt(22)
    p3 = tf.add_paragraph(); p3.text = _trim(subtitle or topic, 130); p3.font.size = Pt(11); p3.font.color.rgb = _rgb("#C9D8E6"); p3.space_before = Pt(18)

    right = slide.shapes.add_textbox(Inches(5.65), Inches(1.05), Inches(6.8), Inches(4.8))
    tf2 = right.text_frame
    tf2.word_wrap = True
    p = tf2.paragraphs[0]
    p.text = "A decision-oriented research pack"
    p.font.size = Pt(18); p.font.color.rgb = _rgb(ACCENT)
    p2 = tf2.add_paragraph(); p2.text = _trim(topic, 180); p2.font.size = Pt(13); p2.font.color.rgb = _rgb(INK); p2.space_before = Pt(14)


def _slide_toc(prs, report: Dict) -> None:
    slide = _blank(prs); _add_brand(slide, "Contents"); _title(slide, "The discussion follows the management question, evidence, and implications")
    y = Inches(1.55)
    for idx, sec in enumerate(report.get("sections", [])[:7], start=1):
        num = slide.shapes.add_textbox(LM, y, Inches(0.4), Inches(0.3))
        p = num.text_frame.paragraphs[0]; p.text = f"{idx:02d}"; p.font.size = Pt(10); p.font.bold = True; p.font.color.rgb = _rgb(ACCENT)
        text = slide.shapes.add_textbox(Inches(1.15), y, Inches(10.8), Inches(0.35))
        p2 = text.text_frame.paragraphs[0]; p2.text = _trim(sec.get("title", "Section"), 105); p2.font.size = Pt(15); p2.font.color.rgb = _rgb(INK)
        y += Inches(0.62)


def _slide_highlights(prs, report: Dict) -> None:
    slide = _blank(prs); _add_brand(slide, "Key highlights"); _title(slide, "The analysis points to a concentrated set of management priorities")
    items = report.get("executive_summary", [])[:6]
    x0, y0 = LM, Inches(1.58)
    w, h = Inches(3.95), Inches(1.45)
    for idx, item in enumerate(items):
        x = x0 + Inches(4.25) * (idx % 3)
        y = y0 + Inches(1.75) * (idx // 3)
        _card(slide, x, y, w, h, _trim(item, 115), idx + 1)


def _slide_approach(prs, report: Dict) -> None:
    slide = _blank(prs); _add_brand(slide, "Approach"); _title(slide, "Seven-step problem solving turns a broad topic into a decision agenda")
    steps = report.get("method_steps", [])[:7]
    x = LM; y = Inches(1.68)
    box_w = Inches(1.64); gap = Inches(0.13)
    for idx, step in enumerate(steps, start=1):
        box = slide.shapes.add_shape(1, x, y, box_w, Inches(2.1))
        box.fill.solid(); box.fill.fore_color.rgb = _rgb(LIGHT_BLUE if idx in {1, 5, 7} else "#F7F9FB")
        box.line.color.rgb = _rgb(LINE)
        tb = slide.shapes.add_textbox(x + Inches(0.1), y + Inches(0.12), box_w - Inches(0.2), Inches(1.75))
        tf = tb.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.text = f"{idx}. {_trim(step.get('name', ''), 28)}"; p.font.size = Pt(9); p.font.bold = True; p.font.color.rgb = _rgb(ACCENT)
        p2 = tf.add_paragraph(); p2.text = _trim(step.get("description", ""), 85); p2.font.size = Pt(7.5); p2.font.color.rgb = _rgb(INK)
        x += box_w + gap


def _slide_image_gallery(prs, title: str, image_paths: List[str], root: Path) -> None:
    slide = _blank(prs); _add_brand(slide, "Key implications"); _title(slide, title)
    y = Inches(1.45)
    for path in image_paths:
        full = root / path
        if full.exists():
            slide.shapes.add_picture(str(full), LM, y, width=Inches(11.9))
            y += Inches(1.72)


def _slide_section(prs, section: Dict, assets: Dict[str, str], root: Path) -> None:
    slide = _blank(prs); _add_brand(slide, section.get("title", "Section")); _title(slide, section.get("title", "Section"), section.get("lead", ""))
    left = slide.shapes.add_textbox(LM, Inches(1.55), Inches(5.45), Inches(4.75))
    tf = left.text_frame; tf.word_wrap = True
    for idx, paragraph in enumerate(section.get("paragraphs", [])[:3]):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.text = _trim(paragraph, 240)
        p.font.size = Pt(9)
        p.font.color.rgb = _rgb(INK)
        p.space_after = Pt(8)
    visual = assets.get(section.get("visual_hint", ""), "")
    full = root / visual
    if visual and full.exists():
        slide.shapes.add_picture(str(full), Inches(6.45), Inches(1.55), width=Inches(6.1))
    else:
        take = slide.shapes.add_textbox(Inches(6.55), Inches(1.65), Inches(5.8), Inches(4.0))
        take.text_frame.word_wrap = True
        for idx, item in enumerate(section.get("key_takeaways", [])[:3]):
            p = take.text_frame.paragraphs[0] if idx == 0 else take.text_frame.add_paragraph()
            p.text = f"- {_trim(item, 95)}"; p.font.size = Pt(11); p.font.color.rgb = _rgb(ACCENT)


def _slide_chart(prs, title: str, chart_path: str, root: Path) -> None:
    slide = _blank(prs); _add_brand(slide, title); _title(slide, title)
    full = root / chart_path
    if full.exists():
        slide.shapes.add_picture(str(full), Inches(0.85), Inches(1.32), width=Inches(11.6))


def _slide_closing(prs, report: Dict, topic: str) -> None:
    slide = _blank(prs); _add_brand(slide, "Closing")
    _title(slide, "The next step is to convert the research into a short list of decisions")
    tx = slide.shapes.add_textbox(LM, Inches(1.6), Inches(10.8), Inches(3.2))
    tf = tx.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = _trim(report.get("report_subtitle", topic), 200)
    p.font.size = Pt(16); p.font.color.rgb = _rgb(ACCENT)


def _card(slide, x, y, w, h, text: str, idx: int) -> None:
    box = slide.shapes.add_shape(1, x, y, w, h)
    box.fill.solid(); box.fill.fore_color.rgb = _rgb("#FFFFFF")
    box.line.color.rgb = _rgb(LINE)
    bar = slide.shapes.add_shape(1, x, y, Inches(0.06), h)
    bar.fill.solid(); bar.fill.fore_color.rgb = _rgb(ACCENT)
    bar.line.fill.background()
    num = slide.shapes.add_textbox(x + Inches(0.16), y + Inches(0.12), Inches(0.45), Inches(0.25))
    p = num.text_frame.paragraphs[0]; p.text = str(idx); p.font.size = Pt(11); p.font.bold = True; p.font.color.rgb = _rgb(ACCENT)
    body = slide.shapes.add_textbox(x + Inches(0.16), y + Inches(0.42), w - Inches(0.3), h - Inches(0.5))
    body.text_frame.word_wrap = True
    p2 = body.text_frame.paragraphs[0]; p2.text = text; p2.font.size = Pt(9.5); p2.font.color.rgb = _rgb(INK)


def _rgb(hex_color: str) -> RGBColor:
    color = hex_color.strip().lstrip("#")
    return RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
