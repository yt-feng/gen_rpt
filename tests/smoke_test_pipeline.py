from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.brand_assets import copy_or_generate_brand_assets, summarize_reference_institutions, write_reference_backup
from gen_rpt.deepseek_client import normalize_structured_payload
from gen_rpt.graphics import create_chart, create_insight_card
from gen_rpt.report_renderer import render_report_html, render_report_markdown


def main() -> None:
    out = ROOT / ".tmp_smoke"
    if out.exists():
        shutil.rmtree(out)
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    payload = {
        "report_title": "Smoke Test Report",
        "report_subtitle": "Testing malformed model payload normalization.",
        "executive_summary": ["Summary one", "Summary two"],
        "sections": [
            {"title": "1. Numbered title should be cleaned", "lead": "Lead", "paragraphs": ["Paragraph A", "Paragraph B"], "key_takeaways": ["Takeaway"], "visual_hint": "image-1"},
            "A string section should not break rendering",
        ],
        "insight_cards": [
            {"title": "Card without id", "subtitle": "Subtitle", "bullets": ["A", "B"]},
            "String card",
        ],
        "charts": [
            {"title": "Stacked chart without id", "type": "stacked_bar", "categories": ["2024", "2025"], "series": [{"name": "A", "values": [1, 2]}, {"name": "B", "values": [2, 3]}]},
            {"title": "Matrix chart", "type": "matrix", "rows": ["Cost", "Supply"], "columns": ["A", "B"], "values": [[5, 3], [4, 2]]},
            {"title": "Bubble chart", "type": "bubble", "points": [{"label": "A", "x": 40, "y": 70, "size": 50}]},
            123,
        ],
        "references": ["BloombergNEF 2024", {"title": "IEA report", "url": "https://www.iea.org/", "note": "Source"}],
    }
    report = normalize_structured_payload(payload)
    report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), [])

    asset_map = copy_or_generate_brand_assets(assets)
    write_reference_backup(out, report.get("references", []), [{"title": "Source", "url": "https://example.com", "content": "content"}])

    # Provide AI image placeholders so image visual hints render without external calls.
    asset_map["image-1"] = asset_map["cover-background"]
    for card in report.get("insight_cards", []):
        target = assets / f"{card['id']}.png"
        create_insight_card(card, target)
        asset_map[card["id"]] = f"assets/{target.name}"
    for chart in report.get("charts", []):
        target = assets / f"{chart['id']}.png"
        create_chart(chart, target)
        asset_map[chart["id"]] = f"assets/{target.name}"

    render_report_html(report, asset_map, out / "report.html", "Smoke topic", "en")
    render_report_markdown(report, asset_map, out / "report.md", "Smoke topic", "en")

    assert (out / "report.html").exists()
    assert (out / "report.md").exists()
    assert any((assets / f"chart-{idx}.png").exists() for idx in range(1, 5))
    print(json.dumps({"ok": True, "assets": len(asset_map), "cards": len(report.get("insight_cards", [])), "charts": len(report.get("charts", []))}))


if __name__ == "__main__":
    main()
