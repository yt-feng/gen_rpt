from __future__ import annotations

import math
import textwrap
from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

from .theme import load_theme


THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
REPORT_LABEL = THEME.get("report_label", "Deep Research Report")
ACCENT = PALETTE["accent"]
ACCENT_DARK = PALETTE["accent_dark"]
INK = PALETTE["ink"]
SUBTLE = PALETTE["subtle"]
GRID = PALETTE["grid"]
PAPER = PALETTE["paper"]
PANEL = PALETTE["panel"]
LINE = PALETTE["line"]
SERIES_COLORS = THEME.get("series_colors", [ACCENT, "#233645", "#7E96A8", "#C4D0D8"])


def configure_matplotlib_fonts() -> None:
    preferred_fonts = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Sans SC",
        "Microsoft YaHei",
        "PingFang SC",
        "Heiti SC",
        "SimHei",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = [name for name in preferred_fonts if name in available]
    plt.rcParams["font.sans-serif"] = (chosen + ["DejaVu Sans"]) if chosen else ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_fonts()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _wrap_text(value: str, width: int) -> str:
    return textwrap.fill(str(value).strip(), width=max(8, width))


def _wrapped_lines(value: str, width: int) -> int:
    wrapped = textwrap.wrap(str(value).strip(), width=max(8, width))
    return max(1, len(wrapped))


def _truncate_text(value: str, max_chars: int) -> str:
    text = str(value).strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _fit_card_layout(title: str, subtitle: str, bullets: List[str], highlight_label: str) -> Dict[str, float]:
    title_lines = _wrapped_lines(title, 26)
    subtitle_lines = _wrapped_lines(subtitle, 46)
    bullet_lines = sum(_wrapped_lines(b, 36) for b in bullets)
    highlight_lines = _wrapped_lines(highlight_label, 12)
    score = title_lines * 1.5 + subtitle_lines * 1.0 + bullet_lines * 1.15 + highlight_lines * 0.7

    if score <= 15:
        return {"title": 24, "subtitle": 12.2, "bullet": 13.3, "y": 0.60}
    if score <= 18:
        return {"title": 22, "subtitle": 11.5, "bullet": 12.5, "y": 0.59}
    if score <= 21:
        return {"title": 20, "subtitle": 11.0, "bullet": 11.8, "y": 0.57}
    return {"title": 18, "subtitle": 10.4, "bullet": 11.0, "y": 0.55}


def create_insight_card(card: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    fig = plt.figure(figsize=(12, 6.75), dpi=160, facecolor=PANEL)
    ax = plt.axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    bg = FancyBboxPatch((0.03, 0.05), 0.94, 0.90, boxstyle="round,pad=0.012,rounding_size=0.02", linewidth=0, facecolor=PAPER)
    accent_bar = FancyBboxPatch((0.03, 0.05), 0.015, 0.90, boxstyle="round,pad=0.0,rounding_size=0.02", linewidth=0, facecolor=ACCENT)
    right_panel = FancyBboxPatch((0.69, 0.15), 0.22, 0.64, boxstyle="round,pad=0.02,rounding_size=0.02", linewidth=1, edgecolor=LINE, facecolor="#F8FBFC")
    ax.add_patch(bg)
    ax.add_patch(accent_bar)
    ax.add_patch(right_panel)

    exhibit_label = _truncate_text(card.get("exhibit_label", "Strategic insight"), 48)
    title = _truncate_text(card.get("title", "Insight"), 120)
    subtitle = _truncate_text(card.get("subtitle", ""), 180)
    bullets: List[str] = [_truncate_text(item, 100) for item in card.get("bullets", [])[:4]]
    highlight_number = str(card.get("highlight_number", "3"))
    highlight_label = _truncate_text(card.get("highlight_label", "Key finding"), 36)

    layout = _fit_card_layout(title, subtitle, bullets, highlight_label)
    ax.text(0.08, 0.90, BRAND_NAME.upper(), fontsize=10.5, fontweight="bold", color=ACCENT, va="top")
    ax.text(0.08, 0.855, _wrap_text(exhibit_label, 40), fontsize=10.5, fontweight="bold", color=SUBTLE, va="top")
    ax.text(0.08, 0.805, _wrap_text(title, 28), fontsize=layout["title"], fontweight="bold", color=INK, va="top", linespacing=1.15)
    ax.text(0.08, 0.705, _wrap_text(subtitle, 46), fontsize=layout["subtitle"], color=SUBTLE, va="top", linespacing=1.45)

    y = layout["y"]
    for bullet in bullets:
        wrapped = _wrap_text(bullet, 36)
        line_count = _wrapped_lines(bullet, 36)
        ax.text(0.09, y, f"• {wrapped}", fontsize=layout["bullet"], color=INK, va="top", linespacing=1.42)
        y -= 0.046 * line_count + 0.026

    ax.text(0.80, 0.57, highlight_number, fontsize=42, fontweight="bold", color=ACCENT_DARK, ha="center")
    ax.text(0.80, 0.43, _wrap_text(highlight_label, 11), fontsize=14.5, color=INK, ha="center", linespacing=1.25)
    ax.text(0.80, 0.22, BRAND_NAME, fontsize=10.0, color=SUBTLE, ha="center")

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def _apply_axes_style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(PAPER)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#C9D3DC")
    ax.spines["bottom"].set_color("#C9D3DC")
    ax.tick_params(axis="x", labelsize=10, colors=INK)
    ax.tick_params(axis="y", labelsize=10, colors=INK)
    ax.grid(True, axis="y", linestyle="--", linewidth=0.8, color=GRID, alpha=0.9)
    ax.set_axisbelow(True)
    ax.set_title(_wrap_text(title, 60), fontsize=18, fontweight="bold", color=INK, loc="left", pad=20)
    if subtitle:
        ax.text(0.0, 1.04, _wrap_text(subtitle, 90), transform=ax.transAxes, fontsize=10.3, color=SUBTLE, va="bottom")


def _figure_height(categories: List[str], chart_type: str) -> float:
    longest = max((len(str(x)) for x in categories), default=0)
    count = len(categories)
    height = 7.0
    if chart_type == "bar":
        height += min(2.2, max(0, count - 6) * 0.18)
    if longest > 16:
        height += 0.6
    return height


def create_chart(chart: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    chart_type = str(chart.get("type", "bar")).lower()
    title = chart.get("title", "Chart")
    subtitle = chart.get("subtitle", "")
    categories = [str(c) for c in chart.get("categories", [])]
    series = chart.get("series", []) or [{"name": "Value", "values": chart.get("values", [])}]
    fig_height = _figure_height(categories, chart_type)

    fig = plt.figure(figsize=(12, fig_height), dpi=160, facecolor=PAPER)
    ax = plt.axes([0.10, 0.20, 0.84, 0.64])
    _apply_axes_style(ax, title=title, subtitle=subtitle)
    fig.text(0.10, 0.90, BRAND_NAME.upper(), fontsize=10.0, fontweight="bold", color=ACCENT)
    fig.text(0.10, 0.875, REPORT_LABEL, fontsize=10.0, color=SUBTLE)

    should_show_values = sum(len(item.get("values", [])) for item in series) <= 12

    if chart_type == "line":
        for idx, item in enumerate(series):
            values = item.get("values", [])
            color = SERIES_COLORS[idx % len(SERIES_COLORS)]
            ax.plot(categories, values, marker="o", markersize=5.5, linewidth=2.5, color=color, label=item.get("name", "Series"))
            if values and len(values) <= 10:
                for x, y in zip(categories, values):
                    ax.annotate(str(y), (x, y), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=9, color=color)
    elif chart_type == "pie":
        pie_values = series[0].get("values", []) if series else []
        colors = SERIES_COLORS[: max(1, len(categories))]
        if len(categories) <= 6:
            wedges, texts, autotexts = ax.pie(
                pie_values,
                labels=[_wrap_text(c, 16) for c in categories],
                autopct="%1.0f%%",
                startangle=90,
                colors=colors,
                wedgeprops={"width": 0.48, "edgecolor": PAPER},
            )
            for t in texts + autotexts:
                t.set_color(INK)
                t.set_fontsize(10)
        else:
            wedges, _ = ax.pie(
                pie_values,
                startangle=90,
                colors=colors,
                wedgeprops={"width": 0.48, "edgecolor": PAPER},
            )
            ax.legend(wedges, [_wrap_text(c, 22) for c in categories], frameon=False, loc="center left", bbox_to_anchor=(1.02, 0.5))
        ax.axis("equal")
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
    else:
        total_series = max(1, len(series))
        x = list(range(len(categories)))
        width = 0.72 / total_series
        offset = -((total_series - 1) * width) / 2
        for idx, item in enumerate(series):
            positions = [i + offset + idx * width for i in x]
            values = item.get("values", [])
            color = SERIES_COLORS[idx % len(SERIES_COLORS)]
            bars = ax.bar(positions, values, width=width, label=item.get("name", "Series"), color=color)
            if should_show_values:
                for bar, value in zip(bars, values):
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), str(value), ha="center", va="bottom", fontsize=8.8, color=INK)
        rotate = 25 if len(categories) > 7 or max((len(c) for c in categories), default=0) > 14 else 0
        ax.set_xticks(x)
        ax.set_xticklabels([_wrap_text(c, 12 if rotate else 14) for c in categories], rotation=rotate, ha="right" if rotate else "center")

    if chart.get("x_label") and chart_type != "pie":
        ax.set_xlabel(chart["x_label"], fontsize=10.4, color=SUBTLE, labelpad=12)
    if chart.get("y_label") and chart_type != "pie":
        ax.set_ylabel(chart["y_label"], fontsize=10.4, color=SUBTLE, labelpad=10)
    if len(series) > 1 and chart_type != "pie":
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 1.01), ncol=min(3, len(series)))

    caption = chart.get("caption") or ""
    source_note = chart.get("source_note") or ""
    if caption:
        fig.text(0.10, 0.08, _wrap_text(caption, 120), fontsize=10.0, color=INK)
    if source_note:
        fig.text(0.10, 0.045, _wrap_text(f"Source: {source_note}", 140), fontsize=9.3, color=SUBTLE)

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path
