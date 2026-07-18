from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

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


def _fetch_rag_context(slug: str, backend_url: str, internal_token: str) -> Optional[str]:
    """
    Fetches private document context from the backend RAG bridge.
    Returns the pre-formatted context_text string if documents exist, else None.
    """
    import requests
    url = f"{backend_url.rstrip('/')}/api/internal/context/{slug}"
    headers = {"Authorization": f"Bearer {internal_token}"}
    try:
        print(f"[RAG Bridge] Fetching context for slug '{slug}' from backend...")
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            has_rag = data.get("has_rag_context", False)
            context_text = data.get("context_text", "")
            doc_count = data.get("document_count", 0)
            chunks = data.get("validated_chunks", [])

            if has_rag and context_text:
                print(f"[RAG Bridge] Active. Found {len(chunks)} chunks from {doc_count} document(s). Switching to RAG-grounded mode.")
                return context_text
            elif chunks and not context_text:
                # Fallback: build context_text manually from chunks (older backend versions)
                context_parts = []
                for c in chunks:
                    chunk_id = c.get("chunk_id", "")
                    conf = c.get("confidence", 0)
                    text = c.get("text", "")
                    context_parts.append(f"[Chunk {chunk_id}] (Confidence: {conf:.2f})\n{text}")
                fallback_text = "\n\n".join(context_parts)
                print(f"[RAG Bridge] Active (legacy). Loaded {len(chunks)} chunks. Switching to RAG-grounded mode.")
                return fallback_text
            else:
                print(f"[RAG Bridge] No private document context found for slug '{slug}'. Using standard research mode.")
                return None
        else:
            print(f"[RAG Bridge] Backend returned status {resp.status_code}. Using standard research mode.")
            return None
    except Exception as e:
        print(f"[RAG Bridge] Failed to retrieve context: {e}. Using standard research mode.")
        return None


def main() -> None:
    args = parse_args()
    language = "zh" if str(args.language).lower().startswith("zh") else "en"
    client = DeepSeekClient(model=args.model)
    pipeline = WebReportPipeline(client=client, language=language)

    date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
    slug = args.slug.strip() or slugify(args.topic)
    output_dir = Path(args.out_root) / f"{date_prefix}-{slug}"

    # --- PHASE 0: RAG CONTEXT BRIDGE (runs BEFORE the pipeline) ---
    # Fetch private document context early so planning, evidence, and synthesis
    # are all grounded in the real document facts — not invented public benchmarks.
    rag_context: Optional[str] = None
    backend_url = os.getenv("BACKEND_URL")
    internal_token = os.getenv("INTERNAL_TOKEN")
    if backend_url and internal_token:
        rag_context = _fetch_rag_context(slug, backend_url, internal_token)

    result = pipeline.build_report(topic=args.topic, output_dir=output_dir, rag_context=rag_context)
    print(f"HTML web report generated at: {result['html_path']}")
    print(f"Markdown generated at: {result['markdown_path']}")
    print(f"Payload generated at: {output_dir / 'web_report_payload.json'}")
    print(f"Analysis framework generated at: {output_dir / 'analysis_framework.json'}")
    print(f"Publication contract generated at: {output_dir / 'publication_contract.json'}")
    print(f"Research fact pack generated at: {output_dir / 'research_fact_pack.json'}")
    print(f"Evidence ledger generated at: {output_dir / 'evidence_ledger.json'}")
    print(f"Storyline plan generated at: {output_dir / 'storyline_plan.json'}")
    print(f"Sources generated at: {output_dir / 'sources.json'}")

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## HTML-first deep research report generated\n")
            f.write(f"- Topic: {args.topic}\n")
            f.write(f"- Language: {language}\n")
            f.write(f"- RAG mode: {'ACTIVE (document-grounded)' if rag_context else 'OFF (public research only)'}\n")
            f.write(f"- HTML: `{result['html_path']}`\n")
            f.write(f"- Markdown: `{result['markdown_path']}`\n")
            f.write(f"- Payload: `{output_dir / 'web_report_payload.json'}`\n")
            f.write(f"- Analysis framework: `{output_dir / 'analysis_framework.json'}`\n")
            f.write(f"- Publication contract: `{output_dir / 'publication_contract.json'}`\n")
            f.write(f"- Evidence ledger: `{output_dir / 'evidence_ledger.json'}`\n")
            f.write(f"- Storyline plan: `{output_dir / 'storyline_plan.json'}`\n")
            f.write(f"- Sources: `{output_dir / 'sources.json'}`\n")


if __name__ == "__main__":
    main()
