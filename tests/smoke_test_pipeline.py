from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.brand_assets import copy_or_generate_brand_assets, summarize_reference_institutions, write_reference_backup
from gen_rpt.deepseek_client import normalize_structured_payload
from gen_rpt.graphics import create_chart, create_insight_card
from gen_rpt.latex_renderer import _chart_title as latex_chart_title
from gen_rpt.latex_renderer import _compact_headline as latex_compact_headline
from gen_rpt.latex_renderer import _display_bullet as latex_display_bullet
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
        "executive_summary": [
            "{'title': 'Executive Summary', 'executive_summary_text': 'Structured summary values should become plain reader prose before rendering.'}",
            "Summary two",
        ],
        "executive_summary_text": "{'title': 'Executive Summary', 'executive_summary_text': 'The CEO-level issue is whether the source-backed facts are strong enough to support action, investment and risk decisions. This malformed value intentionally mimics a model response that wrapped the narrative in a dict-like string, and the deterministic cleanup must extract only the prose for a board reader before rendering.'}",
        "key_findings": [
            {"finding": "Evidence quality changes the pace of commitment.", "evidence": "Public record and fact pack.", "management_implication": "Fund validation before major allocation."}
        ],
        "action_plan": [
            {"horizon": "Near term, 0-90 days", "action": "Build an evidence ledger for management decisions.", "owner": "Strategy", "success_metric": "Claims have sources and dates.", "decision_gate": "Do not use unsupported numbers."}
        ],
        "risk_register": [
            {"risk": "Evidence remains too thin.", "trigger": "Few authoritative sources or numeric facts.", "management_action": "Add source validation before investment use.", "evidence_boundary": "Public record."}
        ],
        "scenario_vignettes": [
            {"title": "CEO decision scenario", "situation": "Management is deciding whether to fund a next step.", "ceo_question": "What can be done now?", "recommended_move": "Fund validation first.", "watchouts": "Do not treat narrative as ROI proof."}
        ],
        "methodology_note": "The smoke report uses public records, evidence-boundary checks and open questions to test rendering.",
        "author_credentials": [{"name": "BlueOcean Research", "role": "Research synthesis team", "credentials": "Evidence checks and report QA."}],
        "sections": [
            {
                "title": "Section 1",
                "lead": "Section 1",
                "paragraphs": [
                    "Generic section title should be replaced.",
                    "The replacement should preserve useful section prose.",
                    "Fusion's timetable should not render with extra apostrophe spacing when a formal paragraph is long enough to survive production cleanup, because the LaTeX renderer must preserve reader-facing apostrophes while still removing weak placeholder fragments from the final report.",
                ],
                "key_takeaways": ["Takeaway"],
                "visual_hint": "image-1",
            },
            {"title": "1. Numbered title should be cleaned", "lead": "Lead", "paragraphs": ["Paragraph A", "Paragraph B"], "key_takeaways": ["Takeaway"], "visual_hint": "image-1"},
            "A string section should not break rendering",
        ],
        "insight_cards": [
            {"title": "Card without id", "subtitle": "Subtitle", "bullets": ["A", "B"]},
            "String card",
        ],
        "charts": [
            {"title": "Stacked chart without id", "type": "stacked_bar", "categories": ["2024", "2025"], "series": [{"name": "A", "values": [1, 2]}, {"name": "B", "values": [2, 3]}]},
            {"title": "Chart.js bar payload should keep model data", "type": "bar", "data": {"labels": ["2024", "2025", "2026"], "datasets": [{"label": "Validated spend", "values": [12, 18, 27]}]}},
            {"title": "Top-level Chart.js bar payload should keep labels", "type": "bar", "labels": ["Solar", "Wind", "Fusion"], "datasets": [{"label": "LCOE", "data": [30, 40, 95]}]},
            {"title": "Matrix chart", "type": "matrix", "rows": ["Cost", "Supply"], "columns": ["A", "B"], "values": [[5, 3], [4, 2]]},
            {"title": "Nested bubble payload should keep model points", "type": "bubble", "data": {"datasets": [{"label": "Private ventures", "points": [{"x": 8, "y": 9, "label": "CFS"}, {"x": 7, "y": 8, "label": "Helion"}, {"x": 6, "y": 7, "label": "TAE"}]}, {"label": "Public projects", "points": [{"x": 4, "y": 5, "label": "ITER"}]}]}},
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
    (out / "report_payload.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for required_key in ["executive_summary_text", "key_findings", "action_plan", "risk_register", "scenario_vignettes", "methodology_note", "author_credentials"]:
        assert report.get(required_key), f"{required_key} should be present after deterministic fixes"
    assert len(report.get("sections", [])) >= 7
    assert len(report.get("charts", [])) >= 14
    chart_types = {chart.get("type") for chart in report.get("charts", [])}
    assert {"line", "bubble", "matrix", "stacked_bar"} & chart_types
    assert len(chart_types) >= 3
    chart_title_keys = set()
    for chart in report.get("charts", []):
        chart_key = re.sub(r"\W+", "", str(chart.get("title") or "").lower())[:140]
        assert chart_key not in chart_title_keys, f"duplicate chart title survived: {chart.get('title')}"
        chart_title_keys.add(chart_key)
    chartjs_bar = next(chart for chart in report.get("charts", []) if chart.get("title") == "Chart.js bar payload should keep model data")
    assert chartjs_bar.get("categories") == ["2024", "2025", "2026"]
    assert chartjs_bar.get("series", [])[0].get("values") == [12.0, 18.0, 27.0]
    top_level_bar = next(chart for chart in report.get("charts", []) if chart.get("title") == "Top-level Chart.js bar payload should keep labels")
    assert top_level_bar.get("categories") == ["Solar", "Wind", "Fusion"]
    assert top_level_bar.get("series", [])[0].get("values") == [30.0, 40.0, 95.0]
    nested_bubble = next(chart for chart in report.get("charts", []) if chart.get("title") == "Nested bubble payload should keep model points")
    nested_labels = {point.get("label") for point in nested_bubble.get("points", [])}
    assert {"CFS", "Helion", "TAE", "ITER"}.issubset(nested_labels)
    for chart in report.get("charts", []):
        chart_type = str(chart.get("type") or "").lower()
        if chart_type in {"bar", "stacked_bar", "line"}:
            categories = [str(x) for x in chart.get("categories", [])]
            values_by_category = [0.0 for _ in categories]
            nonzero_count = 0
            for series in chart.get("series", []):
                for idx, value in enumerate(series.get("values", [])[: len(categories)]):
                    numeric = float(value or 0)
                    values_by_category[idx] += abs(numeric)
                    if abs(numeric) > 1e-6:
                        nonzero_count += 1
            assert len(categories) >= 3, f"weak series chart survived with too few categories: {chart.get('title')}"
            assert nonzero_count > 1, f"single-point series chart survived: {chart.get('title')}"
            assert sum(1 for value in values_by_category if abs(value) > 1e-6) >= 3, f"single-bar chart survived: {chart.get('title')}"
        chart_reader_text = " ".join(str(chart.get(key) or "") for key in ("subtitle", "caption", "source_note")).lower()
        assert "directional index used" not in chart_reader_text
        assert "actual percentages should be replaced" not in chart_reader_text
        assert "weakly supported areas should remain" not in chart_reader_text
        if chart.get("type") == "bubble":
            labels = [str(point.get("label") or "") for point in chart.get("points", [])]
            assert len(labels) >= 3
            assert not all(re.match(r"^(point|item|a)([\s_#-]*\d*)?$", label, re.I) for label in labels)
    generic_title = re.compile(r"^(section|chapter)\s*\d*$", re.I)
    long_para_keys = set()
    for section in report.get("sections", []):
        title = str(section.get("title") or "").strip()
        lead = str(section.get("lead") or "").strip()
        assert not generic_title.match(title), f"generic section title survived: {title}"
        assert len(title) >= 12, f"section title is too thin: {title}"
        assert len(lead) >= 40 and not generic_title.match(lead), f"weak section lead survived: {lead}"
        paragraphs = [str(x or "").strip() for x in section.get("paragraphs", []) if str(x or "").strip()]
        assert len(paragraphs) >= 6, f"section is too short: {title}"
        assert len(lead) + sum(len(x) for x in paragraphs) >= 1900, f"section lacks benchmark-like depth: {title}"
        for text in paragraphs:
            assert not generic_title.match(text), f"placeholder paragraph survived: {text}"
            assert not re.search(r"\bthis\s+(chapter|section)\s+(concludes|finds|shows|frames|explains|argues|will|is about)", text, re.I)
            if len(text) >= 120:
                key = re.sub(r"\W+", "", text.lower())[:220]
                assert key not in long_para_keys, f"duplicate long paragraph survived: {text[:90]}"
                long_para_keys.add(key)
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
    assert r"Fusion\BOApos{}s timetable" in latex_text
    section_count = len(report.get("sections", []))
    chart_pages = (len(report.get("charts", [])) + 1) // 2
    front_chart_pages = min(2, chart_pages)
    later_chart_pages = chart_pages - front_chart_pages
    latex_about_page = 5 + front_chart_pages + section_count + later_chart_pages
    html_about_page = 4 + section_count + chart_pages
    assert f"\\bfseries {latex_about_page:02d}" in latex_text and "About the research" in latex_text
    assert f"<td class='contents-page'>{html_about_page:02d}</td><td><div class='contents-title'>About the research</div>" in html_text
    assert "EXHIBIT 1" in latex_text
    assert "\\draw[BOLine]" in latex_text
    assert "\\AddToShipoutPictureBG*" in latex_text
    assert "remember picture,overlay" not in latex_text
    assert "SENIOR-LEADERSHIP QUESTIONS" in latex_text
    assert "Research basis and limitations" in latex_text
    problem_headline = "Fusion commercialization is advancing faster than expected, driven by private startups that have raised over $5 billion in investment."
    assert latex_compact_headline(problem_headline) == "Fusion commercialization is advancing faster than expected"
    weak_tail = "Management should separate verified facts, directional scenarios and missing diligence items before"
    assert not latex_display_bullet(weak_tail, 140).lower().endswith(" before")
    long_chart_title = "Key risks for Nuclear Fusion Commercialization: Strategic Implications for Energy Markets and Investment should be ranked by severity and manageability"
    assert latex_chart_title(long_chart_title, 125) == "Key risks should be ranked by severity and manageability"
    for chart in report.get("charts", []):
        assert not re.search(r"\b(before|after|with|and|or|of|for|to|in|at|by|from|as|but|while|because|requiring|including|than)$", str(chart.get("title") or ""), re.I)
    for text in [html_text, markdown_text, latex_text]:
        lowered = text.lower()
        for removed_label in [
            "future action agenda",
            "signals to watch",
            "what to watch",
            "a concrete executive choice",
        ]:
            assert removed_label not in lowered
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
            "This chapter concludes",
            "This section concludes",
            "This chapter shows",
            "This section finds",
            "Directional index used",
            "DECISION IMPLICATIONS",
            "Decision readiness still depends",
            "Readiness index",
            "Priority index",
            "BlueOcean synthesis",
            "fact pack",
            "evidence ledger",
            "source pack",
            "generated narrative text",
            "retrieval order",
            "search context",
            "page body could not be fully extracted",
            "lower-confidence public signal",
            "\u987a\u5cf0\u5b9d\u5b9d",
            "\u987a\u5cf0",
            "\u5b9d\u5b9d",
            "\u7f8e\u5bb9\u9662",
            "\u4fdd\u5065\u54c1",
            "\u7ade\u54c1\u5f88\u5389\u5bb3",
            "\u6700\u9002\u5408\u5e2e\u4ed6",
            "shun" "feng",
        ]:
            assert internal_label.lower() not in lowered
        for structured_artifact in ["executive_summary_text", "{'title'", r"\boapos{}title"]:
            assert structured_artifact not in lowered
    assert any((assets / f"chart-{idx}.png").exists() for idx in range(1, 5))
    if not latex_result.get("pdf_path"):
        error_path = out / "latex_error.txt"
        if error_path.exists() and ("xelatex not found" in error_path.read_text(encoding="utf-8").lower() or "compile skipped" in error_path.read_text(encoding="utf-8").lower()):
            print(json.dumps({"ok": True, "assets": len(asset_map), "cards": len(report.get("insight_cards", [])), "charts": len(report.get("charts", [])), "latex_pdf": False, "latex_note": error_path.read_text(encoding="utf-8").strip()}))
            return
        if error_path.exists():
            raise RuntimeError(error_path.read_text(encoding="utf-8")[-1200:])
        raise RuntimeError("LaTeX PDF was not generated and no latex_error.txt was written")
    print(json.dumps({"ok": True, "assets": len(asset_map), "cards": len(report.get("insight_cards", [])), "charts": len(report.get("charts", [])), "latex_pdf": latex_result.get("pdf_path")}))


if __name__ == "__main__":
    main()
