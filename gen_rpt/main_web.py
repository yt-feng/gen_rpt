from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path

from .deepseek_client import DeepSeekClient
from .web_report_pipeline import WebReportPipeline


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "web-research-topic"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an HTML-first deep research web report.")
    parser.add_argument("--topic", required=True, help="Research topic or prompt.")
    parser.add_argument("--slug", default="", help="Optional output directory slug.")
    parser.add_argument("--language", default="en", help="Report language: en or zh.")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek model name.")
    parser.add_argument("--out-root", default="reports_web", help="Output root directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    language = "zh" if str(args.language).lower().startswith("zh") else "en"
    client = DeepSeekClient(model=args.model)
    pipeline = WebReportPipeline(client=client, language=language)

    date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
    slug = args.slug.strip() or slugify(args.topic)
    output_dir = Path(args.out_root) / f"{date_prefix}-{slug}"

    result = pipeline.build_report(topic=args.topic, output_dir=output_dir)
    print(f"HTML web report generated at: {result['html_path']}")
    print(f"Markdown generated at: {result['markdown_path']}")
    print(f"Payload generated at: {output_dir / 'web_report_payload.json'}")
    print(f"Research fact pack generated at: {output_dir / 'research_fact_pack.json'}")
    print(f"Sources generated at: {output_dir / 'sources.json'}")

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## HTML-first deep research report generated\n")
            f.write(f"- Topic: {args.topic}\n")
            f.write(f"- Language: {language}\n")
            f.write(f"- HTML: `{result['html_path']}`\n")
            f.write(f"- Markdown: `{result['markdown_path']}`\n")
            f.write(f"- Payload: `{output_dir / 'web_report_payload.json'}`\n")
            f.write(f"- Sources: `{output_dir / 'sources.json'}`\n")


if __name__ == "__main__":
    main()
