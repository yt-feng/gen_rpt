from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path

from .deepseek_client import DeepSeekClient
from .latex_renderer import render_latex_pdf
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
    parser.add_argument("--language", default="en", help="Report language. Default: en")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek model name.")
    parser.add_argument("--target-length", type=int, default=0, help="Optional legacy target length. Leave empty for natural report length.")
    parser.add_argument("--out-root", default="reports", help="Output root directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    normalized_language = "zh" if str(args.language).lower().startswith("zh") else "en"
    target_length = args.target_length or 0

    client = DeepSeekClient(model=args.model)
    pipeline = ResearchPipeline(client=client, language=normalized_language, target_length=target_length)

    date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
    slug = args.slug.strip() or slugify(args.topic)
    output_dir = Path(args.out_root) / f"{date_prefix}-{slug}"

    result = pipeline.build_report(topic=args.topic, output_dir=output_dir)
    report_path = output_dir / "report.html"
    markdown_path = output_dir / "report.md"
    pdf_path = output_dir / "report.pdf"
    pptx_path = output_dir / "report.pptx"
    presentation_path = output_dir / "presentation.html"
    qa_path = output_dir / "qa_result.json"

    latex_result = render_latex_pdf(
        report=result.get("report", {}),
        assets=result.get("asset_map", {}),
        output_dir=output_dir,
        topic=result.get("report", {}).get("_display_topic") or args.topic,
        language=normalized_language,
    )
    latex_tex_path = Path(latex_result.get("tex_path", output_dir / "report_latex.tex"))
    latex_pdf_path = Path(latex_result.get("pdf_path")) if latex_result.get("pdf_path") else output_dir / "report_latex.pdf"

    print(f"Report generated at: {report_path}")
    print(f"Markdown generated at: {markdown_path}")
    print(f"PDF generated at: {pdf_path}")
    print(f"LaTeX source generated at: {latex_tex_path}")
    if latex_pdf_path.exists():
        print(f"LaTeX PDF generated at: {latex_pdf_path}")
    else:
        print(f"LaTeX PDF not generated; see {output_dir / 'latex_error.txt'} if present")
    print(f"PPTX generated at: {pptx_path}")
    print(f"HTML presentation generated at: {presentation_path}")
    print(f"QA result generated at: {qa_path}")

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        qa_result = result.get("qa_result", {})
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## Deep Research report generated\n")
            f.write(f"- Topic: {args.topic}\n")
            f.write(f"- Language: {normalized_language}\n")
            f.write(f"- HTML: `{report_path}`\n")
            f.write(f"- Markdown: `{markdown_path}`\n")
            f.write(f"- PDF: `{pdf_path}`\n")
            f.write(f"- LaTeX source: `{latex_tex_path}`\n")
            f.write(f"- LaTeX PDF: `{latex_pdf_path}`\n")
            f.write(f"- PPTX: `{pptx_path}`\n")
            f.write(f"- HTML Presentation: `{presentation_path}`\n")
            f.write(f"- QA passed: `{qa_result.get('passed')}`\n")
            f.write(f"- QA issues: `{len(qa_result.get('issues', []))}`\n")
            f.write(f"- Assets: {len(result['asset_map'])}\n")


if __name__ == "__main__":
    main()
