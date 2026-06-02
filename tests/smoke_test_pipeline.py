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
from gen_rpt.latex_renderer import render_latex_pdf
from gen_rpt.research_quality import apply_deterministic_report_fixes, build_research_fact_pack, validate_report
from gen_rpt.report_renderer import render_report_html, render_report_markdown
from gen_rpt.web_fetch import SourceDocument


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
        "executive_summary_text": "The CEO-level issue is whether the source-backed facts are strong enough to support action, investment and risk decisions.",
        "key_findings": [
            {"finding": "Evidence quality changes the pace of commitment.", "evidence": "Public source backup and fact pack.", "management_implication": "Fund validation before major allocation."}
        ],
        "action_plan": [
            {"horizon": "Near term, 0-90 days", "action": "Build an evidence ledger for management decisions.", "owner": "Strategy", "success_metric": "Claims have sources and dates.", "decision_gate": "Do not use unsupported numbers."}
        ],
        "risk_register": [
            {"risk": "Evidence remains too thin.", "trigger": "Few authoritative sources or numeric facts.", "management_action": "Add source validation before investment use.", "evidence_boundary": "Public source backup."}
        ],
        "scenario_vignettes": [
            {"title": "CEO decision scenario", "situation": "Management is deciding whether to fund a next step.", "ceo_question": "What can be done now?", "recommended_move": "Fund validation first.", "watchouts": "Do not treat narrative as ROI proof."}
        ],
        "methodology_note": "The smoke report uses public source backup, evidence-boundary checks and validation gaps to test rendering.",
        "author_credentials": [{"name": "BlueOcean Research", "role": "Research synthesis team", "credentials": "Evidence checks and report QA."}],
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
            {"title": "Bad single point market size", "type": "pie", "categories": ["Market"], "series": [{"name": "Value", "values": [100]}]},
        ],
        "references": ["BloombergNEF 2024", {"title": "IEA report", "url": "https://www.iea.org/", "note": "Source"}],
    }
    report = normalize_structured_payload(payload)
    fact_pack = build_research_fact_pack(
        "Smoke topic",
        {"objective": "Smoke topic", "decision_question": "Can the pipeline validate structure?"},
        [
            SourceDocument(
                title="Example source",
                url="https://example.gov/report",
                query="smoke query",
                snippet="In 2024, the market reached USD 12 billion with 18% growth.",
                content="In 2024, the market reached USD 12 billion with 18% growth. The source states that capacity increased to 45 GW in 2025 and should be independently validated.",
                source_type="html",
                content_type="text/html",
                domain="example.gov",
            )
        ],
    )
    validation_issues = validate_report(report, fact_pack, language="en")
    assert validation_issues
    report = apply_deterministic_report_fixes(report, fact_pack, language="en")
    for required_key in ["executive_summary_text", "key_findings", "action_plan", "risk_register", "scenario_vignettes", "methodology_note", "author_credentials"]:
        assert report.get(required_key), f"{required_key} should be present after deterministic fixes"
    report["reference_institutions"] = summarize_reference_institutions(report.get("references", []), [])

    asset_map = copy_or_generate_brand_assets(assets)
    write_reference_backup(out, report.get("references", []), [{"title": "Source", "url": "https://example.com", "content": "content"}])

    # Provide one real local image placeholder for image visual hints without external calls.
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
    latex_result = render_latex_pdf(report, asset_map, out, "Smoke topic", "en")

    assert (out / "report.html").exists()
    assert (out / "report.md").exists()
    assert (out / "report_latex.tex").exists()
    html_text = (out / "report.html").read_text(encoding="utf-8")
    markdown_text = (out / "report.md").read_text(encoding="utf-8")
    latex_text = (out / "report_latex.tex").read_text(encoding="utf-8")
    for text in [html_text, markdown_text, latex_text]:
        lowered = text.lower()
        assert "future action agenda" in lowered
        assert "signals to watch" in lowered
        assert "a concrete executive choice" in lowered
        for internal_label in [
            "Management action plan",
            "Risk register",
            "CEO decision scenario",
            "Method and team",
            "Executive summary",
            "Key findings",
            "Evidence:",
            "Management implication:",
            "internal framework",
            "stress test",
            "McKinsey",
            "Mck",
            "The issue matters because",
            "The first lens is technology readiness",
            "The second lens is economics",
            "The third lens is ecosystem readiness",
            "This chapter therefore frames the topic",
        ]:
            assert internal_label.lower() not in lowered
    assert any((assets / f"chart-{idx}.png").exists() for idx in range(1, 5))
    if not latex_result.get("pdf_path"):
        error_path = out / "latex_error.txt"
        if error_path.exists() and "xelatex not found" in error_path.read_text(encoding="utf-8").lower():
            print(json.dumps({"ok": True, "assets": len(asset_map), "cards": len(report.get("insight_cards", [])), "charts": len(report.get("charts", [])), "latex_pdf": False, "latex_note": "xelatex not found"}))
            return
        if error_path.exists():
            raise RuntimeError(error_path.read_text(encoding="utf-8")[-1200:])
        raise RuntimeError("LaTeX PDF was not generated and no latex_error.txt was written")
    print(json.dumps({"ok": True, "assets": len(asset_map), "cards": len(report.get("insight_cards", [])), "charts": len(report.get("charts", [])), "latex_pdf": latex_result.get("pdf_path")}))


if __name__ == "__main__":
    main()
