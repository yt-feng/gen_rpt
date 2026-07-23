"""
review_cli.py

Standalone CLI for the evidence-based report audit engine.

Usage:
    python -m gen_rpt.review_cli --file <path_to_report.md> [--out-dir review_output]
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from gen_rpt.review.groq_reviewer import run_groq_review_file


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Evidence-Based Report Auditor — "
            "runs a multi-step Groq AI review on any text or markdown report."
        )
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the report file to audit (e.g., report.md, report.txt)",
    )
    parser.add_argument(
        "--out-dir",
        default="review_output",
        help="Base output directory for review artifacts (default: review_output)",
    )
    return parser.parse_args()


def main():
    load_dotenv()

    args = parse_args()
    file_path   = Path(args.file)
    base_out    = Path(args.out_dir)

    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    if not file_path.is_file():
        print(f"Error: Not a file: {file_path}")
        sys.exit(1)

    stem = file_path.stem
    # If the file is generically named (like 'report.md'), use its parent folder name
    if stem.lower() in ("report", "index", "main") and file_path.parent.name and file_path.parent.name not in (".", "reports", "gen_rpt-main"):
        base_name = file_path.parent.name
    else:
        base_name = stem
        
    folder_name = f"{base_name}_review"
    output_dir  = base_out / folder_name

    print("=" * 60)
    print("  EVIDENCE-BASED REPORT AUDITOR")
    print("=" * 60)
    print(f"  File    : {file_path}")
    print(f"  Output  : {output_dir}")
    print("=" * 60)

    review_data = run_groq_review_file(file_path, output_dir)

    if review_data:
        scores = review_data.get("scores", {})
        claims = review_data.get("claims_audit", {})
        recs   = review_data.get("recommendations", {})
        tasks  = recs.get("improvement_tasks", [])

        print()
        print("=" * 60)
        print("  AUDIT COMPLETE")
        print("=" * 60)
        print(f"  Score   : {scores.get('overall_score')} / 100")
        print(f"  Grade   : {scores.get('grade')}")
        print(f"  Claims  : {claims.get('total_claims', 0)} extracted | "
              f"{claims.get('high_risk_count', 0) + claims.get('unsupported_count', 0)} flagged")
        print(f"  Tasks   : {len(tasks)} improvement tasks generated")
        print()
        print("  Artifacts saved to:")
        print(f"    {output_dir / 'review_report.md'}")
        print(f"    {output_dir / 'review_summary.txt'}")
        print(f"    {output_dir / 'review_report.json'}")
        print(f"    {output_dir / 'improvement_tasks.json'}")
        print(f"    {output_dir / 'claims.json'}")
        print("=" * 60)
    else:
        print()
        print("Review failed or was skipped.")
        print("Check that OPENROUTER_API_KEY is set in your .env file.")
        sys.exit(1)


if __name__ == "__main__":
    main()
