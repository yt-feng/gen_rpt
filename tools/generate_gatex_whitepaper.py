#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gen_rpt.gatex_whitepaper_pipeline import generate_gatex_whitepaper


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a review-gated GateX white paper.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--brief", default="")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--publication-date", default="")
    parser.add_argument("--out-root", type=Path, default=Path("output/gatex-whitepapers"))
    args = parser.parse_args()
    result = generate_gatex_whitepaper(
        topic=args.topic,
        title=args.title,
        slug=args.slug,
        brief=args.brief,
        model=args.model,
        publication_date=args.publication_date or None,
        output_root=args.out_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
