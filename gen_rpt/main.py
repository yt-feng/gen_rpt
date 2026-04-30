from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path

from .deepseek_client import DeepSeekClient
from .research_pipeline import ResearchPipeline


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "deep-research-topic"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a rich deep research report.")
    parser.add_argument("--topic", required=True, help="Research topic or the sentence you typed into GitHub Action.")
    parser.add_argument("--slug", default="", help="Optional custom output slug.")
    parser.add_argument("--language", default="zh", help="Report language. Default: zh")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek model name.")
    parser.add_argument("--target-length", type=int, default=0, help="Target character count for zh or word count for en.")
    parser.add_argument("--out-root", default="reports", help="Output root directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    normalized_language = "en" if str(args.language).lower().startswith("en") else "zh"
    target_length = args.target_length or (1500 if normalized_language == "en" else 3000)

    client = DeepSeekClient(model=args.model)
    pipeline = ResearchPipeline(client=client, language=normalized_language, target_length=target_length)

    date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
    slug = args.slug.strip() or slugify(args.topic)
    output_dir = Path(args.out_root) / f"{date_prefix}-{slug}"

    result = pipeline.build_report(topic=args.topic, output_dir=output_dir)
    report_path = output_dir / "report.html"
    markdown_path = output_dir / "report.md"

    print(f"Report generated at: {report_path}")
    print(f"Markdown generated at: {markdown_path}")
    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## Deep Research report generated\n")
            f.write(f"- Topic: {args.topic}\n")
            f.write(f"- Language: {normalized_language}\n")
            f.write(f"- Target length: {target_length}\n")
            f.write(f"- HTML: `{report_path}`\n")
            f.write(f"- Markdown: `{markdown_path}`\n")
            f.write(f"- Assets: {len(result['asset_map'])}\n")


if __name__ == "__main__":
    main()
