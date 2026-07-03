"""
bulk_generation/dispatch_bulk.py
================================
The ONLY entry point for triggering bulk concurrent report generation.

This script does NOT touch, call, or modify:
  - generate_deep_research_v2.yml  (the existing single-report workflow)
  - generate_review_v2.yml         (the existing single-review workflow)
  - Any file under gen_rpt/

It exclusively dispatches the new _bulk workflows via `gh workflow run`.

Usage
-----
# From a JSON file:
python bulk_generation/dispatch_bulk.py --jobs bulk_generation/jobs.json

# Inline (CLI args):
python bulk_generation/dispatch_bulk.py \
  --topic "AI in Healthcare" --slug "ai-healthcare-2026" \
  --topic "Quantum Computing" --slug "quantum-computing-2026"

# Dry run (print commands without executing):
python bulk_generation/dispatch_bulk.py --jobs bulk_generation/jobs.json --dry-run

jobs.json format
----------------
[
  {"topic": "AI in Healthcare", "slug": "ai-healthcare-2026", "model": "deepseek-chat"},
  {"topic": "Quantum Computing", "slug": "quantum-computing-2026"}
]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


BULK_WORKFLOW = "generate_deep_research_bulk.yml"
DEFAULT_MODEL = "deepseek-chat"
STAGGER_SECONDS = 2  # delay between each gh workflow run call to avoid API spam


def dispatch_one(topic: str, slug: str, model: str, dry_run: bool) -> bool:
    """Fire a single bulk workflow dispatch. Returns True on success."""
    cmd = [
        "gh", "workflow", "run", BULK_WORKFLOW,
        "-f", f"topic={topic}",
        "-f", f"slug={slug}",
        "-f", f"model={model}",
    ]
    if dry_run:
        print(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return True

    print(f"Dispatching: topic='{topic}' slug='{slug}' model='{model}'")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR dispatching '{slug}': {result.stderr.strip()}", file=sys.stderr)
        return False
    print(f"  OK -> {slug}")
    return True


def load_jobs(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Dispatch multiple concurrent bulk report generation workflows."
    )
    parser.add_argument("--jobs", help="Path to a JSON file with list of {topic, slug, model?} objects.")
    parser.add_argument("--topic", action="append", dest="topics", metavar="TOPIC",
                        help="Topic string (repeat for multiple). Must pair with --slug.")
    parser.add_argument("--slug", action="append", dest="slugs", metavar="SLUG",
                        help="Slug string (repeat for multiple). Must pair with --topic.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"DeepSeek model (default: {DEFAULT_MODEL})")
    parser.add_argument("--stagger", type=float, default=STAGGER_SECONDS,
                        help=f"Seconds between each dispatch (default: {STAGGER_SECONDS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing.")
    args = parser.parse_args()

    jobs: list[dict] = []

    if args.jobs:
        jobs = load_jobs(args.jobs)
    elif args.topics and args.slugs:
        if len(args.topics) != len(args.slugs):
            print("ERROR: --topic and --slug counts must match.", file=sys.stderr)
            sys.exit(1)
        jobs = [
            {"topic": t, "slug": s, "model": args.model}
            for t, s in zip(args.topics, args.slugs)
        ]
    else:
        parser.print_help()
        sys.exit(1)

    if not jobs:
        print("No jobs to dispatch.")
        sys.exit(0)

    print(f"Dispatching {len(jobs)} report(s) via {BULK_WORKFLOW}")
    print(f"Stagger delay: {args.stagger}s between dispatches\n")

    failed = []
    for i, job in enumerate(jobs):
        topic = job.get("topic", "").strip()
        slug = job.get("slug", "").strip()
        model = job.get("model", args.model).strip() or DEFAULT_MODEL

        if not topic or not slug:
            print(f"  SKIP job #{i+1}: missing topic or slug -> {job}", file=sys.stderr)
            continue

        ok = dispatch_one(topic, slug, model, dry_run=args.dry_run)
        if not ok:
            failed.append(slug)

        # Stagger between dispatches (not after the last one)
        if i < len(jobs) - 1:
            time.sleep(args.stagger)

    print(f"\nDone. {len(jobs) - len(failed)}/{len(jobs)} dispatched successfully.")
    if failed:
        print(f"Failed slugs: {failed}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
