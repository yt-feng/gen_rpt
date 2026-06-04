from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from gen_rpt.brand_assets import copy_or_generate_brand_assets, summarize_reference_institutions, write_reference_backup
from gen_rpt.deepseek_client import normalize_structured_payload
from gen_rpt.graphics import create_chart, create_insight_card
from gen_rpt.image_generator import _fallback_image
from gen_rpt.latex_renderer import render_latex_pdf
from gen_rpt.research_quality import ResearchFactPack, apply_deterministic_report_fixes, validate_report
from gen_rpt.report_renderer import render_report_html, render_report_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-render an existing report directory using current deterministic fixes.")
    parser.add_argument("report_dir", type=Path, help="Existing report directory with report_payload.json and research_fact_pack.json.")
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".tmp_real_rerender", help="Output directory for the re-rendered report.")
    parser.add_argument("--language", default="en")
    parser.add_argument("--compile-latex", action="store_true", help="Run xelatex instead of the fast TeX-only path.")
    parser.add_argument("--force", action="store_true", help="Overwrite the output directory if it exists.")
    args = parser.parse_args()

    report_dir = args.report_dir
    payload_path = report_dir / "report_payload.json"
    fact_pack_path = report_dir / "research_fact_pack.json"
    if not payload_path.exists():
        raise FileNotFoundError(f"Missing {payload_path}")
    if not fact_pack_path.exists():
        raise FileNotFoundError(f"Missing {fact_pack_path}")

    out_dir = args.out_dir
    if out_dir.exists():
        if not args.force:
            raise FileExistsError(f"{out_dir} already exists; pass --force to overwrite it")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"

    report = normalize_structured_payload(json.loads(payload_path.read_text(encoding="utf-8")))
    fact_pack = _load_fact_pack(fact_pack_path)
    language = "zh" if str(args.language).lower().startswith("zh") else "en"
    fixed = apply_deterministic_report_fixes(report, fact_pack, language=language)
    fixed["reference_institutions"] = summarize_reference_institutions(fixed.get("references", []), [])
    topic = str(fixed.get("_display_topic") or fixed.get("report_title") or fact_pack.topic)

    asset_map = _copy_existing_assets(report_dir / "assets", assets_dir)
    asset_map.update(copy_or_generate_brand_assets(assets_dir))
    _materialize_section_visual_assets(fixed, asset_map, assets_dir, topic)
    _materialize_chart_assets(fixed, asset_map, assets_dir)
    _materialize_card_assets(fixed, asset_map, assets_dir)
    write_reference_backup(out_dir, fixed.get("references", []), [])

    render_report_html(fixed, asset_map, out_dir / "report.html", topic, language)
    render_report_markdown(fixed, asset_map, out_dir / "report.md", topic, language)

    old_skip = os.environ.get("GEN_RPT_SKIP_LATEX_COMPILE")
    if not args.compile_latex:
        os.environ["GEN_RPT_SKIP_LATEX_COMPILE"] = "true"
    try:
        latex_result = render_latex_pdf(fixed, asset_map, out_dir, topic, language)
    finally:
        if old_skip is None:
            os.environ.pop("GEN_RPT_SKIP_LATEX_COMPILE", None)
        else:
            os.environ["GEN_RPT_SKIP_LATEX_COMPILE"] = old_skip

    (out_dir / "report_payload.json").write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "rerender_quality.json").write_text(
        json.dumps({"content_issues": validate_report(fixed, fact_pack, language=language)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "out_dir": str(out_dir), "charts": len(fixed.get("charts", [])), "sections": len(fixed.get("sections", [])), "latex": latex_result}, ensure_ascii=False))
    return 0


def _load_fact_pack(path: Path) -> ResearchFactPack:
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {field.name for field in fields(ResearchFactPack)}
    payload: Dict[str, Any] = {key: data.get(key) for key in allowed}
    for key in ("source_domains", "source_refs", "high_confidence_facts", "numeric_facts", "dated_facts", "validation_issues"):
        payload[key] = payload.get(key) if isinstance(payload.get(key), list) else []
    for key in ("topic", "objective", "decision_question"):
        payload[key] = str(payload.get(key) or "")
    payload["source_count"] = int(payload.get("source_count") or len(payload["source_refs"]))
    payload["authoritative_source_count"] = int(payload.get("authoritative_source_count") or 0)
    return ResearchFactPack(**payload)


def _copy_existing_assets(src: Path, dst: Path) -> Dict[str, str]:
    dst.mkdir(parents=True, exist_ok=True)
    asset_map: Dict[str, str] = {}
    if not src.exists():
        return asset_map
    for path in src.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("image-") and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        target = dst / path.name
        shutil.copyfile(path, target)
        asset_map[path.stem] = f"assets/{target.name}"
    return asset_map


def _materialize_section_visual_assets(report: Dict[str, Any], asset_map: Dict[str, str], assets_dir: Path, topic: str) -> None:
    for idx, section in enumerate(report.get("sections", []) or [], start=1):
        if not isinstance(section, dict):
            continue
        image_id = f"image-{idx}"
        target = assets_dir / f"{image_id}.png"
        title = str(section.get("title") or "").strip()
        lead = str(section.get("lead") or "").strip()
        prompt = (
            f"{title}; {lead}; {topic}; premium strategy report section visual; "
            "topic-specific business, industrial, policy or executive setting; no readable text"
        )
        _fallback_image(target, kind="section", prompt=prompt)
        asset_map[image_id] = f"assets/{target.name}"


def _materialize_chart_assets(report: Dict[str, Any], asset_map: Dict[str, str], assets_dir: Path) -> None:
    for chart in report.get("charts", []) or []:
        if not isinstance(chart, dict):
            continue
        chart_id = str(chart.get("id") or "")
        if not chart_id:
            continue
        target = assets_dir / f"{chart_id}.png"
        create_chart(chart, target)
        asset_map[chart_id] = f"assets/{target.name}"


def _materialize_card_assets(report: Dict[str, Any], asset_map: Dict[str, str], assets_dir: Path) -> None:
    for card in report.get("insight_cards", []) or []:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id") or "")
        if not card_id:
            continue
        target = assets_dir / f"{card_id}.png"
        create_insight_card(card, target)
        asset_map[card_id] = f"assets/{target.name}"


if __name__ == "__main__":
    raise SystemExit(main())
