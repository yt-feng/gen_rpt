from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .deepseek_client import DeepSeekClient
from .private_sources import SOURCE_MODES, load_private_sources
from .web_fetch import SourceDocument, sources_from_validated_context
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
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional local directory containing PDF, DOCX, PPTX, MD, TXT or HTML sources.",
    )
    parser.add_argument(
        "--source-mode",
        choices=SOURCE_MODES,
        default=None,
        help=(
            "Source selection: web_only, collection_only or web_and_collection. "
            "Defaults to web_and_collection when --source-dir is provided, otherwise web_only."
        ),
    )
    return parser.parse_args()


class RAGBridgeError(RuntimeError):
    pass


@dataclass
class RAGContextPackage:
    context_text: str
    sources: List[SourceDocument]
    document_count: int
    metadata: Dict[str, Any]


def _fetch_rag_context(slug: str, backend_url: str, internal_token: str, topic: str = "") -> Optional[RAGContextPackage]:
    """
    Fetches private document context from the backend RAG bridge.
    Returns context text and the same validated chunks as structured sources.
    """
    import requests
    url = f"{backend_url.rstrip('/')}/api/internal/context/{slug}"
    headers = {"Authorization": f"Bearer {internal_token}"}
    print(f"[RAG Bridge] Fetching context for slug '{slug}' from backend...")
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        response_payload = resp.json()
    except Exception as exc:
        raise RAGBridgeError(f"Failed to retrieve validated context for slug '{slug}': {exc}") from exc

    data = response_payload.get("data", {}) if isinstance(response_payload, dict) else {}
    if not isinstance(data, dict):
        raise RAGBridgeError(f"Backend returned an invalid context package for slug '{slug}'")
    chunks = data.get("validated_chunks", []) or []
    if not chunks:
        print(f"[RAG Bridge] No private document context found for slug '{slug}'.")
        return None

    sources = sources_from_validated_context(data, topic)
    context_text = "\n\n".join(
        f"[Chunk: {source.metadata['chunk_id']} | Document: {source.metadata['file_name']}]\n{source.content}"
        for source in sources
    )
    if not context_text or not sources:
        raise RAGBridgeError(
            f"Context package for slug '{slug}' contained chunks but no usable text-backed sources"
        )
    document_count = int(data.get("document_count") or len({source.metadata.get("document_id") for source in sources}))
    print(
        f"[RAG Bridge] Active. Preserved {len(sources)} validated chunks "
        f"from {document_count} document(s)."
    )
    return RAGContextPackage(
        context_text=context_text,
        sources=sources,
        document_count=document_count,
        metadata={
            "validation_report_reference": data.get("validation_report_reference"),
            "context_metadata": data.get("context_metadata") or {},
            "knowledge_snapshot": data.get("knowledge_snapshot") or {},
        },
    )


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    args = parse_args()
    language = "zh" if str(args.language).lower().startswith("zh") else "en"
    date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
    slug = args.slug.strip() or slugify(args.topic)
    output_dir = Path(args.out_root) / f"{date_prefix}-{slug}"
    source_mode = args.source_mode or ("web_and_collection" if args.source_dir else "web_only")
    if source_mode != "web_only" and args.source_dir is None:
        raise SystemExit(f"--source-dir is required when --source-mode={source_mode}")
    private_sources = load_private_sources(args.source_dir) if source_mode != "web_only" else []
    if source_mode != "web_only" and not private_sources:
        raise SystemExit(f"No supported private source documents found under: {args.source_dir}")

    # --- PHASE 0: RAG CONTEXT BRIDGE (runs BEFORE the pipeline) ---
    # Fetch private document context early so planning, evidence, and synthesis
    # are all grounded in the real document facts — not invented public benchmarks.
    rag_package: Optional[RAGContextPackage] = None
    rag_required = _env_flag("RAG_REQUIRED")
    backend_url = os.getenv("BACKEND_URL")
    internal_token = os.getenv("INTERNAL_TOKEN")
    if rag_required and (not backend_url or not internal_token):
        raise RAGBridgeError("RAG_REQUIRED is enabled but BACKEND_URL or INTERNAL_TOKEN is missing")
    if backend_url and internal_token:
        try:
            rag_package = _fetch_rag_context(slug, backend_url, internal_token, args.topic)
        except RAGBridgeError:
            if rag_required:
                raise
            print("[RAG Bridge] Retrieval failed for an optional RAG run; continuing in public-research mode.")
    if rag_required and rag_package is None:
        raise RAGBridgeError(f"RAG_REQUIRED is enabled but no validated context exists for slug '{slug}'")

    client = DeepSeekClient(model=args.model)
    pipeline = WebReportPipeline(client=client, language=language)

    result = pipeline.build_report(
        topic=args.topic,
        output_dir=output_dir,
        rag_context=rag_package.context_text if rag_package else None,
        rag_sources=rag_package.sources if rag_package else None,
        rag_required=rag_required,
        private_sources=private_sources,
        source_mode=source_mode,
    )
    print(f"HTML web report generated at: {result['html_path']}")
    print(f"Markdown generated at: {result['markdown_path']}")
    print(f"Payload generated at: {output_dir / 'web_report_payload.json'}")
    print(f"Analysis framework generated at: {output_dir / 'analysis_framework.json'}")
    print(f"Publication contract generated at: {output_dir / 'publication_contract.json'}")
    print(f"Research fact pack generated at: {output_dir / 'research_fact_pack.json'}")
    print(f"Evidence ledger generated at: {output_dir / 'evidence_ledger.json'}")
    print(f"Storyline plan generated at: {output_dir / 'storyline_plan.json'}")
    print(f"Sources generated at: {output_dir / 'sources.json'}")
    print(
        "Source selection: "
        f"mode={result['source_mode']} "
        f"web={result['web_source_count']} "
        f"private={result['private_source_count']}"
    )

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## HTML-first deep research report generated\n")
            f.write(f"- Topic: {args.topic}\n")
            f.write(f"- Language: {language}\n")
            f.write(f"- RAG mode: {'ACTIVE (document-grounded)' if rag_package else 'OFF (public research only)'}\n")
            if rag_package:
                f.write(f"- RAG evidence: {len(rag_package.sources)} validated chunks from {rag_package.document_count} documents\n")
            f.write(f"- Source mode: {result['source_mode']}\n")
            f.write(f"- Web sources: {result['web_source_count']}\n")
            f.write(f"- Private sources: {result['private_source_count']}\n")
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
