from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
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
    parser.add_argument("--content-policy", choices=("strict", "seo_overview"), default="strict",
                        help="Explicitly allow a disclosed general overview when research evidence is unavailable.")
    parser.add_argument("--result-path", type=Path, default=None,
                        help="Machine-readable generation receipt outside the published directory.")
    parser.add_argument("--checkpoint-path", type=Path, default=None,
                        help="Optional diagnostic draft receipt outside the published report directory.")
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
    # This is an idempotent read of the same scoped package. A brief backend
    # stall must not restart generation, change source scope or lose the job.
    max_attempts = 3
    transient_statuses = {408, 429, 500, 502, 503, 504}
    for attempt in range(1, max_attempts + 1):
        resp = None
        try:
            resp = requests.get(url, headers=headers, timeout=(10, 30))
            resp.raise_for_status()
            response_payload = resp.json()
            break
        except Exception as exc:
            retryable = (
                isinstance(exc, (requests.Timeout, requests.ConnectionError))
                and not isinstance(exc, requests.exceptions.SSLError)
            ) or (
                isinstance(exc, requests.HTTPError)
                and resp is not None
                and resp.status_code in transient_statuses
            )
            if not retryable or attempt == max_attempts:
                raise RAGBridgeError(
                    f"Failed to retrieve validated context for slug '{slug}' "
                    f"after {attempt} attempt(s): {exc}"
                ) from exc
            print(f"[RAG Bridge] Temporary backend failure; retrying context read ({attempt}/{max_attempts}).")
            time.sleep(2 ** (attempt - 1))
        finally:
            if resp is not None:
                resp.close()

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
    if getattr(args, "content_policy", "strict") == "seo_overview":
        from .generation_continuity import run_continuity
        # A lower-evidence overview must never overwrite an earlier successful
        # report for the same logical slug, including another run today.
        run_id, attempt = os.getenv("GITHUB_RUN_ID", ""), os.getenv("GITHUB_RUN_ATTEMPT", "1")
        if run_id:
            if not run_id.isdigit() or not attempt.isdigit():
                raise ValueError("Invalid workflow attempt identity")
            output_dir = output_dir.with_name(f"{output_dir.name}--run{run_id}-{attempt}")
        run_continuity(args=args, output_dir=output_dir, slug=slug,
                       fetch_rag=_fetch_rag_context, client_factory=DeepSeekClient,
                       pipeline_factory=WebReportPipeline)
        return
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
        raise RAGBridgeError(
            f"RAG_REQUIRED is enabled but no validated context exists for slug '{slug}'. "
            "Retry the original backend job to refresh its original source scope before dispatch; "
            "rerunning GitHub Actions alone cannot restore expired context."
        )

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
        checkpoint_path=getattr(args, "checkpoint_path", None),
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

    if getattr(args, "result_path", None):
        # Strict mode uses the same exact artifact receipt as overview mode.
        outcome = {"status": "generated", "reason": "strict_report", "content_policy": "strict",
                   "requested_rag_required": rag_required, "evidence_verified": bool(rag_package),
                   "slug": slug, "report_id": output_dir.name,
                   "run_id": os.getenv("GITHUB_RUN_ID", "local"),
                   "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "1"),
                   "job_id": os.getenv("GENERATION_JOB_ID", ""),
                   "job_retry_count": os.getenv("GENERATION_JOB_RETRY_COUNT", "")}
        payload_path = output_dir / "web_report_payload.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["generation_outcome"] = outcome
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output_dir / "report.html").write_bytes((output_dir / "index.html").read_bytes())
        receipt = {**outcome, "report_dir": str(output_dir),
                   "payload_sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest()}
        for name in ("report.html", "report.md"):
            receipt[name + "_sha256"] = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
        args.result_path.parent.mkdir(parents=True, exist_ok=True)
        args.result_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        if os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                for key in ("report_dir", "report_id", "slug", "status", "reason"):
                    stream.write(f"{key}={receipt[key]}\n")

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
