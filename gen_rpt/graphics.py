from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

from .theme import load_theme

THEME = load_theme()
PALETTE = THEME["palette"]
BRAND_NAME = THEME["brand_name"]
ACCENT = PALETTE["accent"]
ACCENT_DARK = PALETTE.get("accent_dark", ACCENT)
INK = PALETTE["ink"]
SUBTLE = PALETTE["subtle"]
GRID = PALETTE["grid"]
PAPER = PALETTE["paper"]
LINE = PALETTE["line"]
AXIS = PALETTE.get("axis", "#808080")
SERIES_COLORS = THEME.get("series_colors", [ACCENT, "#3273F6", "#A3B1BE", "#DCE0E4", "#7C8A99"])
FONT_FAMILY = THEME.get("font_family", "Trebuchet MS, Aptos, Arial, sans-serif")


def configure_matplotlib_fonts() -> None:
    preferred_fonts = [x.strip() for x in FONT_FAMILY.split(",")] + ["Trebuchet MS", "Aptos", "Arial", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "SimHei", "DejaVu Sans"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = [name for name in preferred_fonts if name in available]
    plt.rcParams["font.sans-serif"] = chosen if chosen else ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


configure_matplotlib_fonts()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _wrap_text(value: Any, width: int) -> str:
    return textwrap.fill(str(value or "").strip(), width=max(8, width))


def _wrapped_lines(value: Any, width: int) -> int:
    return max(1, len(textwrap.wrap(str(value or "").strip(), width=max(8, width))))


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip()


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def create_insight_card(card: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    fig = plt.figure(figsize=(11.4, 4.5), dpi=180, facecolor=PAPER)
    ax = plt.axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    panel = FancyBboxPatch((0.02, 0.06), 0.96, 0.88, boxstyle="round,pad=0.004,rounding_size=0.012", linewidth=0.8, edgecolor=LINE, facecolor=PAPER)
    bar = FancyBboxPatch((0.035, 0.12), 0.008, 0.74, boxstyle="round,pad=0.0,rounding_size=0.008", linewidth=0, facecolor=ACCENT)
    metric = FancyBboxPatch((0.71, 0.24), 0.20, 0.46, boxstyle="round,pad=0.006,rounding_size=0.012", linewidth=0.8, edgecolor=GRID, facecolor="#F7FAFD")
    ax.add_patch(panel)
    ax.add_patch(bar)
    ax.add_patch(metric)

    exhibit = _truncate_text(card.get("exhibit_label", "Key point"), 42)
    title = _truncate_text(card.get("title", "Insight"), 74)
    subtitle = _truncate_text(card.get("subtitle", ""), 110)
    bullets = [_truncate_text(x, 62) for x in _safe_list(card.get("bullets", []))[:4]]
    highlight_number = str(card.get("highlight_number", "3"))
    highlight_label = _truncate_text(card.get("highlight_label", "Key finding"), 24)

    title_size = 15.5 if _wrapped_lines(title, 30) <= 2 else 13.5
    subtitle_size = 8.8 if _wrapped_lines(subtitle, 46) <= 2 else 8.0
    ax.text(0.075, 0.84, BRAND_NAME.upper(), fontsize=7.2, fontweight="bold", color=ACCENT, va="top")
    ax.text(0.075, 0.79, exhibit, fontsize=7.4, color=SUBTLE, va="top")
    ax.text(0.075, 0.725, _wrap_text(title, 30), fontsize=title_size, fontweight="bold", color=INK, va="top", linespacing=1.08)
    ax.text(0.075, 0.61, _wrap_text(subtitle, 46), fontsize=subtitle_size, color=SUBTLE, va="top", linespacing=1.22)

    y = 0.50
    for bullet in bullets:
        lines = _wrapped_lines(bullet, 34)
        ax.text(0.082, y, f"- {_wrap_text(bullet, 34)}", fontsize=8.5, color=INK, va="top", linespacing=1.25)
        y -= 0.07 + max(0, lines - 1) * 0.036

    ax.text(0.81, 0.50, highlight_number, fontsize=22, fontweight="bold", color=ACCENT_DARK, ha="center", va="center")
    ax.text(0.81, 0.38, _wrap_text(highlight_label, 11), fontsize=8.8, color=INK, ha="center", va="center", linespacing=1.1)

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor(), pad_inches=0.02)
    plt.close(fig)
    return output_path


def _base_chart_frame(fig, title: str, subtitle: str, exhibit_no: str | None = None):
    label = f"EXHIBIT {exhibit_no}" if exhibit_no else "EXHIBIT"
    fig.text(0.055, 0.955, label, fontsize=8.0, fontweight="bold", color=ACCENT)
    fig.text(0.055, 0.915, _wrap_text(title, 72), fontsize=15.2, color=INK)
    if subtitle:
        fig.text(0.055, 0.875, _wrap_text(subtitle, 95), fontsize=8.6, color=SUBTLE)
    ax = plt.axes([0.09, 0.22, 0.82, 0.55])
    ax.set_facecolor(PAPER)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="x", labelsize=7.8, colors=AXIS)
    ax.tick_params(axis="y", labelsize=8.0, colors=INK)
    ax.grid(True, axis="x", color=GRID, linewidth=0.55, alpha=0.55)
    ax.grid(False, axis="y")
    ax.set_axisbelow(True)
    return ax


def create_chart(chart: Dict, output_path: Path) -> Path:
    ensure_dir(output_path.parent)
    if not isinstance(chart, dict):
        chart = {"title": "Chart", "series": [chart]}
    chart_type = str(chart.get("type", "bar") or "bar").lower().replace("-", "_")
    title = _truncate_text(chart.get("title", "Chart"), 82)
    subtitle = _truncate_text(chart.get("subtitle", ""), 130)

    fig = plt.figure(figsize=(10.8, 6.0), dpi=180, facecolor=PAPER)
    ax = _base_chart_frame(fig, title, subtitle, str(chart.get("exhibit_no", "")) or None)

    try:
        if chart_type == "line":
            categories, series = _normalize_chart_data(chart)
            _draw_line_chart(ax, categories, series)
        elif chart_type in {"pie", "donut"}:
            categories, series = _normalize_chart_data(chart)
            _draw_pie_chart(fig, ax, categories, series)
        elif chart_type == "stacked_bar":
            categories, series = _normalize_chart_data(chart)
            _draw_stacked_bar_chart(ax, categories, series)
        elif chart_type == "matrix":
            _draw_matrix_chart(fig, ax, chart)
        elif chart_type == "bubble":
            _draw_bubble_chart(ax, chart)
        else:
            categories, series = _normalize_chart_data(chart)
            _draw_bar_chart(ax, categories, series)
    except Exception:
        ax.clear()
        categories, series = _normalize_chart_data(chart)
        if not series:
            categories, series = ["Value"], [{"name": "Value", "values": [0.0]}]
        _draw_bar_chart(ax, categories, series[:1])

    if chart.get("x_label") and chart_type not in {"pie", "donut", "matrix"}:
        ax.set_xlabel(str(chart["x_label"]), fontsize=7.8, color=SUBTLE)
    if chart.get("y_label") and chart_type not in {"pie", "donut", "bar", "stacked_bar", "matrix"}:
        ax.set_ylabel(str(chart["y_label"]), fontsize=7.8, color=SUBTLE)

    caption = chart.get("caption") or ""
    source_note = chart.get("source_note") or ""
    if caption:
        fig.text(0.055, 0.118, _wrap_text(caption, 135), fontsize=7.6, color=INK)
    if source_note:
        fig.text(0.055, 0.075, _wrap_text(f"Sources: {source_note}", 140), fontsize=6.0, color=SUBTLE)

    plt.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor(), pad_inches=0.02)
    plt.close(fig)
    return output_path


def _draw_line_chart(ax, categories: List[str], series: List[Dict[str, Any]]) -> None:
    ax.grid(True, axis="both", color=GRID, linewidth=0.55, alpha=0.55)
    for idx, item in enumerate(series):
        values = item.get("values", [])
        color = SERIES_COLORS[idx % len(SERIES_COLORS)]
        x = list(range(len(categories)))
        ax.plot(x, values, marker="o", markersize=4.0, linewidth=1.9, color=color, label=item.get("name", "Series"))
    ax.set_xticks(list(range(len(categories))))
    ax.set_xticklabels([_wrap_text(c, 11) for c in categories], fontsize=7.6)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=7.8, loc="upper left")


def _draw_pie_chart(fig, ax, categories: List[str], series: List[Dict[str, Any]]) -> None:
    ax.remove()
    ax = plt.axes([0.16, 0.22, 0.45, 0.56])
    pie_values = [max(0.0, float(x)) for x in (series[0].get("values", []) if series else [])]
    if not pie_values or sum(pie_values) <= 0:
        pie_values = [1.0]
        categories = ["Value"]
    wedges, _texts, autotexts = ax.pie(pie_values, startangle=90, colors=SERIES_COLORS[: max(1, len(categories))], wedgeprops={"width": 0.42, "edgecolor": PAPER}, autopct="%1.0f%%", pctdistance=0.78)
    for t in autotexts:
        t.set_color(PAPER)
        t.set_fontsize(7.7)
    ax.legend(wedges, [_wrap_text(c, 16) for c in categories], frameon=False, fontsize=7.8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.set(aspect="equal")


def _draw_bar_chart(ax, categories: List[str], series: List[Dict[str, Any]]) -> None:
    if not series:
        series = [{"name": "Value", "values": [0.0]}]
    if len(series) == 1:
        values = series[0].get("values", [])
        y = list(range(len(categories)))
        colors = [ACCENT] + ["#A3B1BE"] * max(0, len(categories) - 1)
        ax.barh(y, values, color=colors[: len(categories)], edgecolor="none", height=0.56)
        ax.set_yticks(y)
        ax.set_yticklabels([_wrap_text(c, 22) for c in categories], fontsize=8.0, color=INK)
        ax.invert_yaxis()
        max_v = max(values) if values else 1
        max_v = max_v if max_v > 0 else 1
        ax.set_xlim(0, max_v * 1.18)
        for yi, value in zip(y, values):
            ax.text(value + max_v * 0.015, yi, _format_value(value), va="center", ha="left", fontsize=8.2, color=ACCENT if yi == 0 else INK)
    else:
        x = list(range(len(categories)))
        width = 0.70 / max(1, len(series))
        offset = -((len(series) - 1) * width) / 2
        for idx, item in enumerate(series):
            values = item.get("values", [])
            color = SERIES_COLORS[idx % len(SERIES_COLORS)]
            pos = [i + offset + idx * width for i in x]
            ax.bar(pos, values, width=width, color=color, edgecolor="none", label=item.get("name", "Series"))
        ax.set_xticks(x)
        ax.set_xticklabels([_wrap_text(c, 11) for c in categories], fontsize=7.6)
        ax.legend(frameon=False, fontsize=7.6, loc="upper left")


def _draw_stacked_bar_chart(ax, categories: List[str], series: List[Dict[str, Any]]) -> None:
    x = list(range(len(categories)))
    bottoms = [0.0] * len(categories)
    for idx, item in enumerate(series):
        values = item.get("values", [])
        color = SERIES_COLORS[idx % len(SERIES_COLORS)]
        ax.bar(x, values, bottom=bottoms, color=color, edgecolor="none", label=item.get("name", f"Series {idx+1}"), width=0.62)
        bottoms = [b + v for b, v in zip(bottoms, values)]
    ax.set_xticks(x)
    ax.set_xticklabels([_wrap_text(c, 10) for c in categories], fontsize=7.4)
    ax.grid(True, axis="y", color=GRID, linewidth=0.55, alpha=0.55)
    ax.grid(False, axis="x")
    ax.legend(frameon=False, fontsize=7.2, loc="upper left", ncol=2)


def _draw_matrix_chart(fig, ax, chart: Dict[str, Any]) -> None:
    ax.remove()
    rows = [str(x) for x in chart.get("rows", [])]
    cols = [str(x) for x in chart.get("columns", [])]
    raw_values = chart.get("values", [])
    values: List[List[float]] = []
    for row in raw_values if isinstance(raw_values, list) else []:
        values.append([_to_float(v) or 0.0 for v in (row if isinstance(row, list) else [row])])
    if not rows:
        rows = [f"Lever {i+1}" for i in range(max(1, len(values)))]
    if not cols:
        cols = [f"Option {i+1}" for i in range(max(1, len(values[0]) if values else 4))]
    while len(values) < len(rows):
        values.append([0.0] * len(cols))
    values = [(row + [0.0] * len(cols))[: len(cols)] for row in values[: len(rows)]]

    ax = plt.axes([0.15, 0.20, 0.72, 0.58])
    im = ax.imshow(values, cmap="Blues", vmin=0, vmax=max(5, max(max(r) for r in values) if values else 5), aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_yticks(range(len(rows)))
    ax.set_xticklabels([_wrap_text(c, 12) for c in cols], fontsize=7.4)
    ax.set_yticklabels([_wrap_text(r, 18) for r in rows], fontsize=7.4)
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(j, i, _format_value(value), ha="center", va="center", fontsize=7.4, color=PAPER if value >= 4 else INK)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)


def _draw_bubble_chart(ax, chart: Dict[str, Any]) -> None:
    points = chart.get("points", [])
    if not isinstance(points, list) or not points:
        points = [{"label": "Option A", "x": 70, "y": 80, "size": 50}, {"label": "Option B", "x": 50, "y": 55, "size": 35}]
    xs, ys, sizes, labels = [], [], [], []
    for point in points:
        if not isinstance(point, dict):
            continue
        xs.append(_to_float(point.get("x")) or 0.0)
        ys.append(_to_float(point.get("y")) or 0.0)
        sizes.append(max(80.0, (_to_float(point.get("size")) or 30.0) * 8.0))
        labels.append(str(point.get("label", "")))
    ax.scatter(xs, ys, s=sizes, color=ACCENT, alpha=0.50, edgecolors=ACCENT_DARK, linewidths=0.8)
    for x, y, label in zip(xs, ys, labels):
        ax.text(x, y, _wrap_text(label, 10), fontsize=7.2, color=INK, ha="center", va="center")
    ax.set_xlim(0, max(100, max(xs) * 1.12 if xs else 100))
    ax.set_ylim(0, max(100, max(ys) * 1.12 if ys else 100))
    ax.grid(True, axis="both", color=GRID, linewidth=0.55, alpha=0.55)


def _normalize_chart_data(chart: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    raw_categories = chart.get("categories", [])
    raw_series = chart.get("series", None)
    fallback_values = chart.get("values", [])
    if isinstance(raw_categories, (str, int, float)):
        categories = [str(raw_categories)]
    elif isinstance(raw_categories, list):
        categories = [str(c) for c in raw_categories]
    else:
        categories = []
    series: List[Dict[str, Any]] = []
    if isinstance(raw_series, dict):
        raw_series = [raw_series]
    if isinstance(raw_series, list) and raw_series:
        if all(isinstance(item, dict) for item in raw_series):
            for idx, item in enumerate(raw_series):
                values = _coerce_values(item.get("values", []))
                if values:
                    series.append({"name": str(item.get("name") or f"Series {idx + 1}"), "values": values})
        else:
            values = _coerce_values(raw_series)
            if values:
                series.append({"name": "Value", "values": values})
    if not series:
        values = _coerce_values(fallback_values)
        if values:
            series.append({"name": "Value", "values": values})
    if not series:
        return categories or ["Value"], []
    max_len = max(len(item["values"]) for item in series)
    if not categories:
        categories = [f"Item {idx + 1}" for idx in range(max_len)]
    elif len(categories) < max_len:
        categories = categories + [f"Item {idx + 1}" for idx in range(len(categories), max_len)]
    elif len(categories) > max_len:
        categories = categories[:max_len]
    for item in series:
        values = item["values"]
        if len(values) < len(categories):
            values = values + [0.0] * (len(categories) - len(values))
        item["values"] = values[: len(categories)]
    return categories, series


def _coerce_values(raw: Any) -> List[float]:
    if isinstance(raw, dict):
        raw = list(raw.values())
    if isinstance(raw, (int, float)):
        raw = [raw]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    values: List[float] = []
    for value in raw:
        converted = _to_float(value)
        if converted is not None:
            values.append(converted)
    return values


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _format_value(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}" if abs(value) < 1 else f"{value:.1f}"
