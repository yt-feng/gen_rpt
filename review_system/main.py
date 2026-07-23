"""
review_system/main.py

CLI entry point for the isolated AI Review System.

Usage:
    python review_system/main.py \\
        --report path/to/report.md \\
        --output review_outputs/BO-CN-00001

The review system:
  - Reads report artifact from --report path
  - Never writes to gen_rpt_original or any generation directory
  - Writes all review outputs to --output directory
  - Maintains its own logs in review_system/logs/
"""
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime

# Allow running as `python review_system/main.py` from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env from project root if present
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv is optional; rely on shell environment

from review_system.reviewers.openrouter_review_engine import OpenRouterReviewEngine
from review_system.reviewers.review_orchestrator import run_pipeline
from review_system.reviewers.review_builder import assemble
from review_system.extractors.report_loader import load_report
from review_system.extractors.section_parser import parse_report
from review_system.outputs.json_writer import write_all_json
from review_system.outputs.markdown_writer import write_markdown
from review_system.outputs.html_writer import write_html
from review_system.utils.file_utils import resolve_report_path, safe_mkdir
from review_system.utils.logging_utils import get_run_logger, get_error_logger

log = get_run_logger()
err = get_error_logger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="review_system",
        description=(
            "Evidence-Based Report Auditor — "
            "Independently reviews report artifacts produced by gen_rpt_original."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python review_system/main.py --report reports/2026-01-01-fusion/report.md
  python review_system/main.py --report reports/2026-01-01-fusion/report.md \\
      --output review_outputs/BO-CN-00001
        """,
    )
    parser.add_argument(
        "--report",
        required=True,
        metavar="PATH",
        help="Path to the report file to audit (.md, .html, .json, .txt)",
    )
    parser.add_argument(
        "--output",
        default="",
        metavar="DIR",
        help=(
            "Output directory for review artifacts. "
            "Default: review_outputs/<report_stem>_<timestamp>"
        ),
    )
    parser.add_argument(
        "--model",
        default="",
        metavar="MODEL",
        help="OpenRouter model name to use (default: meta-llama/llama-3.3-70b-instruct)",
    )
    parser.add_argument(
        "--full-analysis",
        action="store_true",
        default=False,
        help=(
            "Run all 6 analyzers as separate API calls (9 total calls). "
            "Use only if you have a paid OpenRouter plan or sufficient limits. "
            "Default: lean mode (3 API calls)."
        ),
    )
    return parser.parse_args()


def _resolve_output_dir(args: argparse.Namespace, report_path: Path) -> Path:
    if args.output.strip():
        return Path(args.output)
    
    stem = report_path.stem
    # If the file is generically named (like 'report.md'), use its parent folder name
    if stem.lower() in ("report", "index", "main") and report_path.parent.name and report_path.parent.name not in (".", "reports", "gen_rpt-main"):
        base_name = report_path.parent.name
    else:
        base_name = stem
        
    folder = f"{base_name}_review"
    return Path("review_outputs") / folder


def _print_banner(report_path: Path, output_dir: Path, lean_mode: bool) -> None:
    mode = "Lean (3 API calls)" if lean_mode else "Full (9 API calls)"
    print("=" * 62)
    print("  AI REVIEW SYSTEM — Evidence-Based Report Auditor")
    print("=" * 62)
    print(f"  Report : {report_path}")
    print(f"  Output : {output_dir}")
    print(f"  Mode   : {mode}")
    print("=" * 62)


def _print_result(review_data: dict) -> None:
    scores = review_data.get("scores", {})
    claims = review_data.get("claims_audit", {})
    recs   = review_data.get("recommendations", {})
    tasks  = recs.get("improvement_tasks", [])
    ec     = recs.get("executive_communication", {})
    bad    = claims.get("unsupported_count", 0) + claims.get("high_risk_count", 0)

    print()
    print("=" * 62)
    print("  AUDIT COMPLETE")
    print("=" * 62)
    print(f"  Score         : {scores.get('overall_score')} / 100")
    print(f"  Grade         : {scores.get('grade')}")
    print(f"  Claims Audited: {claims.get('total_claims', 0)}"
          f"  |  Flagged: {bad}")
    print(f"  Tasks         : {len(tasks)} improvement tasks generated")
    print()
    print("  Audience Readiness:")
    print(f"    Minister : {'YES' if ec.get('minister_ready') else 'NO'}")
    print(f"    Board    : {'YES' if ec.get('board_ready') else 'NO'}")
    print(f"    SWF      : {'YES' if ec.get('swf_ready') else 'NO'}")
    print("=" * 62)


def main() -> None:
    args = parse_args()

    # ── Resolve report path ─────────────────────────────────────────────────
    report_path = resolve_report_path(args.report)
    if report_path is None:
        print(f"Error: Report file not found or not a file: {args.report}")
        sys.exit(1)

    # ── Resolve output dir ──────────────────────────────────────────────────
    output_dir = _resolve_output_dir(args, report_path)
    safe_mkdir(output_dir)

    lean_mode = not args.full_analysis
    _print_banner(report_path, output_dir, lean_mode)
    log.info("Review started | report=%s | output=%s", report_path, output_dir)

    # ── Check OPENROUTER_API_KEY ──────────────────────────────────────────────────
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print()
        print("Error: OPENROUTER_API_KEY is not set.")
        print("Set it in your .env file or shell environment and retry.")
        log.error("OPENROUTER_API_KEY not set. Aborting.")
        sys.exit(1)

    # ── Load and parse report ───────────────────────────────────────────────
    try:
        raw_text, report_title = load_report(report_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading report: {e}")
        err.error("load_report failed: %s", e)
        sys.exit(1)

    parsed = parse_report(raw_text, title=report_title)
    print(f"  Parsed : {len(parsed.sections)} sections, {parsed.total_words} words")
    print()

    # ── OpenRouter engine ─────────────────────────────────────────────────────────
    from review_system.config.review_config import OPENROUTER_DEFAULT_MODEL
    model = args.model.strip() or OPENROUTER_DEFAULT_MODEL
    engine = OpenRouterReviewEngine(api_key=api_key, model=model)

    # ── Run review pipeline ─────────────────────────────────────────────────
    try:
        pipeline_results = run_pipeline(engine, parsed, output_dir, lean_mode=lean_mode)
    except Exception as e:
        print(f"Review pipeline failed: {e}")
        err.exception("Pipeline failed")
        sys.exit(1)

    # ── Assemble final ReviewData ────────────────────────────────────────────
    try:
        review_data = assemble(
            engine,
            pipeline_results,
            report_title=report_title,
            report_path=str(report_path),
        )
    except Exception as e:
        print(f"Review assembly failed: {e}")
        err.exception("Assembly failed")
        sys.exit(1)

    # ── Write outputs ────────────────────────────────────────────────────────
    write_all_json(output_dir, review_data)
    write_markdown(output_dir, review_data)
    write_html(output_dir, review_data)

    _print_result(review_data)

    print()
    print("  Artifacts:")
    print(f"    {output_dir / 'review.md'}")
    print(f"    {output_dir / 'review.html'}")
    print(f"    {output_dir / 'review.json'}")
    print(f"    {output_dir / 'claims.json'}")
    print(f"    {output_dir / 'findings.json'}")
    print(f"    {output_dir / 'scores.json'}")
    print(f"    {output_dir / 'audit_manifest.json'}")
    print("=" * 62)

    log.info("Review complete | score=%.1f | grade=%s",
             review_data.get("scores", {}).get("overall_score", 0),
             review_data.get("scores", {}).get("grade", "?"))


if __name__ == "__main__":
    main()
