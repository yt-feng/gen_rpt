from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch


ACCENT = "#127C7E"
ACCENT_DARK = "#0F5B63"
INK = "#1F2937"
SUBTLE = "#667085"
GRID = "#D8DEE5"
PAPER = "#FFFFFF"
PANEL = "#F7F9FB"
SERIES_COLORS = ["#127C7E", "#22313F", "#8AA5B8", "#C6D2DB"]


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
    if chosen:
        plt.rcParams["font.sans-serif"] = chosen + ["DejaVu Sans"]
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_fonts()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _wrap_text(value: str, width: int) -> str:
    return textwrap.fill(str(value), width=width)


def create_insight_card(card: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    fig = plt.figure(figsize=(12, 6.75), dpi=160, facecolor=PANEL)
    ax = plt.axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    bg = FancyBboxPatch(
        (0.03, 0.05),
        0.94,
        0.90,
        boxstyle="round,pad=0.012,rounding_size=0.02",
        linewidth=0,
        facecolor=PAPER,
    )
    accent_bar = FancyBboxPatch(
        (0.03, 0.05),
        0.015,
        0.90,
        boxstyle="round,pad=0.0,rounding_size=0.02",
        linewidth=0,
        facecolor=ACCENT,
    )
    right_panel = FancyBboxPatch(
        (0.68, 0.15),
        0.23,
        0.64,
        boxstyle="round,pad=0.02,rounding_size=0.02",
        linewidth=1,
        edgecolor="#DCE6EC",
        facecolor="#F7FBFC",
    )
    ax.add_patch(bg)
    ax.add_patch(accent_bar)
    ax.add_patch(right_panel)

    title = card.get("title", "Insight")
    subtitle = card.get("subtitle", "")
    bullets: List[str] = card.get("bullets", [])[:4]
    highlight_number = card.get("highlight_number", "3")
    highlight_label = card.get("highlight_label", "Key finding")
    exhibit_label = card.get("exhibit_label", "Executive insight")

    ax.text(0.08, 0.88, exhibit_label.upper(), fontsize=11, fontweight="bold", color=ACCENT, va="top")
    ax.text(0.08, 0.82, _wrap_text(title, 30), fontsize=24, fontweight="bold", color=INK, va="top")
    ax.text(0.08, 0.73, _wrap_text(subtitle, 44), fontsize=12, color=SUBTLE, va="top", linespacing=1.5)

    y = 0.60
    for bullet in bullets:
        wrapped = _wrap_text(bullet, 38)
        ax.text(0.09, y, f"• {wrapped}", fontsize=13.5, color=INK, va="top", linespacing=1.45)
        y -= 0.13

    ax.text(0.795, 0.57, str(highlight_number), fontsize=42, fontweight="bold", color=ACCENT_DARK, ha="center")
    ax.text(0.795, 0.43, _wrap_text(highlight_label, 12), fontsize=15, color=INK, ha="center")
    ax.text(0.795, 0.24, "McKinsey-style\ninsight card", fontsize=11.5, color=SUBTLE, ha="center")

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def _apply_mckinsey_axes_style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(PAPER)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#C9D3DC")
    ax.spines["bottom"].set_color("#C9D3DC")
    ax.tick_params(axis="x", labelsize=10, colors=INK)
    ax.tick_params(axis="y", labelsize=10, colors=INK)
    ax.grid(True, axis="y", linestyle="--", linewidth=0.8, color=GRID, alpha=0.9)
    ax.set_axisbelow(True)
    ax.set_title(title, fontsize=18, fontweight="bold", color=INK, loc="left", pad=20)
    if subtitle:
        ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=10.5, color=SUBTLE, va="bottom")


def create_chart(chart: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    chart_type = str(chart.get("type", "bar")).lower()
    title = chart.get("title", "Chart")
    subtitle = chart.get("subtitle", "")
    categories = chart.get("categories", [])
    series = chart.get("series", []) or [{"name": "Value", "values": chart.get("values", [])}]

    fig = plt.figure(figsize=(12, 7), dpi=160, facecolor=PAPER)
    ax = plt.axes([0.10, 0.22, 0.84, 0.62])
    _apply_mckinsey_axes_style(ax, title=title, subtitle=subtitle)
    fig.text(0.10, 0.90, "Exhibit", fontsize=10.5, fontweight="bold", color=ACCENT)

    if chart_type == "line":
        for idx, item in enumerate(series):
            values = item.get("values", [])
            color = SERIES_COLORS[idx % len(SERIES_COLORS)]
            ax.plot(categories, values, marker="o", markersize=6, linewidth=2.6, color=color, label=item.get("name", "Series"))
            if values:
                ax.annotate(str(values[-1]), (categories[-1], values[-1]), xytext=(8, 0), textcoords="offset points", fontsize=10, color=color)
    elif chart_type == "pie":
        pie_values = series[0].get("values", []) if series else []
        colors = SERIES_COLORS[: max(1, len(categories))]
        wedges, texts, autotexts = ax.pie(pie_values, labels=categories, autopct="%1.0f%%", startangle=90, colors=colors, wedgeprops={"width": 0.48, "edgecolor": PAPER})
        for t in texts + autotexts:
            t.set_color(INK)
            t.set_fontsize(10)
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
            for bar, value in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), str(value), ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_xticks(x)
        ax.set_xticklabels([_wrap_text(c, 12) for c in categories], rotation=0, ha="center")

    if chart.get("x_label") and chart_type != "pie":
        ax.set_xlabel(chart["x_label"], fontsize=10.5, color=SUBTLE, labelpad=12)
    if chart.get("y_label") and chart_type != "pie":
        ax.set_ylabel(chart["y_label"], fontsize=10.5, color=SUBTLE, labelpad=10)
    if len(series) > 1 and chart_type != "pie":
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 1.01), ncol=min(3, len(series)))

    caption = chart.get("caption") or ""
    source_note = chart.get("source_note") or ""
    if caption:
        fig.text(0.10, 0.08, caption, fontsize=10.2, color=INK)
    if source_note:
        fig.text(0.10, 0.045, f"Source: {source_note}", fontsize=9.5, color=SUBTLE)

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path
