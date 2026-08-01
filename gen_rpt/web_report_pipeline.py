from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .brand_assets import copy_or_generate_brand_assets, write_reference_backup
from .deepseek_client import DeepSeekClient
from .graphics import ensure_dir
from .image_generator import generate_ai_image_assets
from .private_sources import combine_source_documents, normalize_source_mode
from .research_quality import ResearchFactPack, build_research_fact_pack
from .web_evidence import (
    build_evidence_exhibits,
    build_evidence_ledger,
    build_storyline_plan,
    merge_evidence_exhibits,
    reconcile_rag_web_evidence,
)
from .web_fetch import SourceDocument, build_rag_manifest, collect_sources, merge_sources
from .web_publication_contract import (
    client_visible_internal_hits,
    combined_evidence_quality_issues,
    ground_rag_section_evidence,
    publication_contract_prompt,
    prune_unsupported_numeric_claims,
    rag_exhibit_is_grounded,
    rag_report_quality_issues,
    report_content_quality_issues,
    rag_rendered_output_issues,
    rag_visible_numbers_supported,
)
from .web_report_renderer import normalize_web_report, render_web_report_html, render_web_report_markdown


class ReportQualityError(RuntimeError):
    """The evidence or editorial contract is too weak for publication."""


class WebReportPipeline:
    """HTML-first research report pipeline.

    The legacy ResearchPipeline treats HTML as an intermediate artifact for PDF.
    This pipeline treats the browser article as the primary product and keeps
    PDF/PPT concerns out of the content schema.
    """

    def __init__(self, client: DeepSeekClient, language: str = "en") -> None:
        self.client = client
        self.language = "zh" if str(language or "").lower().startswith("zh") else "en"
        # Set by build_report when private document context is available
        self.rag_context: str | None = None
        self.rag_sources: List[SourceDocument] = []
        self.rag_required = False

    def build_report(
        self,
        topic: str,
        output_dir: Path,
        rag_context: str | None = None,
        rag_sources: List[SourceDocument] | None = None,
        rag_required: bool = False,
        *,
        private_sources: List[SourceDocument] | None = None,
        source_mode: str = "web_only",
    ) -> Dict[str, Any]:
        run_start = time.monotonic()
        self.rag_context = rag_context
        self.rag_sources = list(rag_sources or [])
        self.rag_required = bool(rag_required)
        if self.rag_required and (not self.rag_context or not self.rag_sources):
            raise RuntimeError("RAG report generation requires both validated context text and structured sources")
        normalized_source_mode = normalize_source_mode(source_mode)
        private_source_list = list(private_sources or [])
        if normalized_source_mode != "web_only" and not private_source_list:
            raise ValueError(
                f"source_mode={normalized_source_mode!r} requires at least one private source"
            )
        ensure_dir(output_dir)
        assets_dir = output_dir / "assets"
        ensure_dir(assets_dir)
        display_topic = str(topic or "").strip()
        rag_mode_label = "RAG-GROUNDED" if self.rag_context else "PUBLIC-RESEARCH"
        self._log(
            "START web report pipeline "
            f"| topic={display_topic!r} | output_dir={output_dir} "
            f"| mode={rag_mode_label} | source_mode={normalized_source_mode} "
            f"| rag_sources={len(self.rag_sources)} | private_sources={len(private_source_list)}"
        )
        self._log("ETA planning=15-90s, chart_data_needs=10-60s, source_collection=60-300s, evidence=5-15s, synthesis=60-180s, visuals=60-360s")

        phase_start = time.monotonic()
        self._log("PHASE planning started | expected 15-90s")
        try:
            plan = self._plan_research(display_topic)
        except Exception as exc:
            (output_dir / "web_plan_error.txt").write_text(str(exc), encoding="utf-8")
            plan = self._fallback_plan(display_topic, str(exc))
            self._log(f"PHASE planning fallback used | reason={str(exc)[:240]!r}")
        plan = self._normalize_research_plan(plan, display_topic)
        self._log(
            "PHASE planning completed "
            f"| elapsed={self._elapsed(phase_start)} | queries={len(plan.get('search_queries', []) or [])} "
            f"| outline={len(plan.get('outline', []) or [])} "
            f"| hypotheses={len(plan.get('hypotheses', []) or [])} "
            f"| sizing_methods={len(plan.get('market_sizing_plan', {}).get('methods', []) or [])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE chart_data_needs started | expected 10-60s")
        try:
            chart_data_needs = self._plan_chart_data_needs(display_topic, plan)
        except Exception as exc:
            (output_dir / "chart_data_needs_error.txt").write_text(str(exc), encoding="utf-8")
            chart_data_needs = self._fallback_chart_data_needs(display_topic, plan, str(exc))
            self._log(f"PHASE chart_data_needs fallback used | reason={str(exc)[:240]!r}")
        if not chart_data_needs:
            chart_data_needs = self._fallback_chart_data_needs(display_topic, plan, "model returned no chart data needs")
            self._log("PHASE chart_data_needs fallback used | reason='model returned no chart data needs'")
        chart_queries = self._chart_need_queries(chart_data_needs)
        self._log(
            "PHASE chart_data_needs completed "
            f"| elapsed={self._elapsed(phase_start)} | needs={len(chart_data_needs)} | chart_queries={len(chart_queries)}"
        )

        per_query = int(os.getenv("GEN_RPT_PER_QUERY", "5"))
        max_sources = int(os.getenv("GEN_RPT_MAX_SOURCES", "28"))
        max_queries = int(os.getenv("GEN_RPT_MAX_QUERIES", "18"))
        search_queries = self._expanded_search_queries(plan, chart_data_needs)[:max_queries]
        phase_start = time.monotonic()
        public_sources: List[SourceDocument] = []
        web_required = normalized_source_mode != "collection_only" and self._rag_web_required()
        if normalized_source_mode == "collection_only":
            self._log(
                "PHASE source_collection web search skipped "
                f"| source_mode={normalized_source_mode} | private_sources={len(private_source_list)}"
            )
        else:
            self._log(
                "PHASE source_collection started "
                f"| expected 45-240s | queries={len(search_queries)} | per_query={per_query} | max_sources={max_sources}"
            )
            if search_queries:
                self._log("PHASE source_collection query_plan | " + " || ".join(query[:120] for query in search_queries[:10]))
            public_sources = self._collect_public_sources(
                search_queries,
                per_query=per_query,
                max_sources=max_sources,
            )
        supplemental_sources = combine_source_documents(
            public_sources,
            private_source_list,
            normalized_source_mode,
        )
        sources = merge_sources(self.rag_sources, supplemental_sources)
        if self.rag_required and not any(source.source_type == "internal" for source in sources):
            raise RuntimeError("Validated RAG sources were lost before evidence generation")
        rag_source_chunks = {
            str(source.metadata.get("chunk_id")): source.content
            for source in self.rag_sources
            if source.metadata.get("chunk_id")
        }
        source_dicts = [source.__dict__ for source in sources]
        domains = sorted({source.domain for source in sources if source.domain})
        self._log(
            "PHASE source_collection completed "
            f"| elapsed={self._elapsed(phase_start)} | sources={len(sources)} "
            f"| rag_sources={len(self.rag_sources)} | web_sources={len(public_sources)} "
            f"| private_sources={len(private_source_list) if normalized_source_mode != 'web_only' else 0} "
            f"| domains={', '.join(domains[:8]) or 'none'}"
        )

        phase_start = time.monotonic()
        self._log("PHASE fact_pack started | expected <10s")
        rag_fact_pack = build_research_fact_pack(display_topic, plan, self.rag_sources) if self.rag_context else None
        web_fact_pack = build_research_fact_pack(display_topic, plan, supplemental_sources) if self.rag_context and supplemental_sources else None
        fact_pack = rag_fact_pack or build_research_fact_pack(display_topic, plan, sources)
        self._log(
            "PHASE fact_pack completed "
            f"| elapsed={self._elapsed(phase_start)} | source_count={fact_pack.source_count} "
            f"| authoritative={fact_pack.authoritative_source_count}"
        )

        phase_start = time.monotonic()
        self._log("PHASE evidence_ledger_and_storyline started | expected 5-15s")
        try:
            if self.rag_context:
                rag_evidence_ledger = build_evidence_ledger(
                    display_topic,
                    self.rag_sources,
                    rag_fact_pack or fact_pack,
                    limit=24,
                    id_prefix="RAG-E",
                )
                web_evidence_ledger = (
                    build_evidence_ledger(
                        display_topic,
                        supplemental_sources,
                        web_fact_pack or fact_pack,
                        limit=24,
                        id_prefix="WEB-E",
                    )
                    if supplemental_sources
                    else []
                )
                reconciliation = reconcile_rag_web_evidence([*rag_evidence_ledger, *web_evidence_ledger])
                rag_evidence_ledger = reconciliation["rag"]
                web_evidence_ledger = reconciliation["web"]
                evidence_ledger = [*rag_evidence_ledger, *web_evidence_ledger]
                approved_evidence = reconciliation["approved"]
                evidence_conflicts = reconciliation["conflicts"]
            else:
                evidence_ledger = build_evidence_ledger(display_topic, sources, fact_pack)
                rag_evidence_ledger = []
                web_evidence_ledger = evidence_ledger
                approved_evidence = evidence_ledger
                evidence_conflicts = []
        except Exception as exc:
            (output_dir / "web_evidence_error.txt").write_text(str(exc), encoding="utf-8")
            evidence_ledger = []
            rag_evidence_ledger = []
            web_evidence_ledger = []
            approved_evidence = []
            evidence_conflicts = []
            self._log(f"PHASE evidence_ledger fallback used | reason={str(exc)[:240]!r}")
        if web_required and not web_evidence_ledger:
            raise RuntimeError("Combined web search returned sources but zero structured evidence points")
        evidence_base_issues = self._evidence_base_issues(
            max(
                fact_pack.authoritative_source_count,
                web_fact_pack.authoritative_source_count if web_fact_pack else 0,
            ),
            approved_evidence,
            web_evidence_ledger,
            web_required=web_required,
        )
        if evidence_base_issues:
            message = "Evidence base is not publication-ready: " + " | ".join(evidence_base_issues)
            (output_dir / "web_report_quality_error.txt").write_text(message, encoding="utf-8")
            raise ReportQualityError(message)
        grounding_text = self._combined_grounding_text(approved_evidence, sources)
        storyline_plan = build_storyline_plan(display_topic, plan, fact_pack, approved_evidence, language=self.language)
        family_counts: Dict[str, int] = {}
        for item in approved_evidence:
            family = str(item.get("metric_family") or "other")
            family_counts[family] = family_counts.get(family, 0) + 1
        family_summary = ", ".join(f"{key}:{value}" for key, value in sorted(family_counts.items(), key=lambda x: (-x[1], x[0]))[:6])
        self._log(
            "PHASE evidence_ledger_and_storyline completed "
            f"| elapsed={self._elapsed(phase_start)} | evidence_points={len(approved_evidence)} "
            f"| conflicts={len(evidence_conflicts)} "
            f"| families={family_summary or 'none'}"
        )

        phase_start = time.monotonic()
        self._log("PHASE synthesis started | expected 60-180s")
        try:
            report = self._synthesize_web_report(
                display_topic,
                plan,
                chart_data_needs,
                sources,
                fact_pack,
                approved_evidence,
                storyline_plan,
                evidence_conflicts=evidence_conflicts,
            )
            report, quality_issues = self._prepare_report_draft(
                report,
                topic=display_topic,
                grounding_text=grounding_text,
                source_count=len(sources),
                source_chunks=rag_source_chunks,
                approved_evidence=approved_evidence,
            )
            if quality_issues:
                self._log("PHASE synthesis retry | " + " | ".join(quality_issues[:8]))
                report = self._revise_report_draft(report, quality_issues, storyline_plan)
                report, quality_issues = self._prepare_report_draft(
                    report,
                    topic=display_topic,
                    grounding_text=grounding_text,
                    source_count=len(sources),
                    source_chunks=rag_source_chunks,
                    approved_evidence=approved_evidence,
                )
            if quality_issues:
                raise ReportQualityError("Report content quality gate failed: " + " | ".join(quality_issues))

            self._post_process(report, display_topic, sources, fact_pack)
            audit = self._audit_report_content(report, storyline_plan)
            if not self._editorial_audit_passed(audit):
                corrections = self._audit_corrections(audit)
                self._log("PHASE editorial revision | " + " | ".join(corrections[:8]))
                report = self._revise_report_draft(report, corrections, storyline_plan)
                report, quality_issues = self._prepare_report_draft(
                    report,
                    topic=display_topic,
                    grounding_text=grounding_text,
                    source_count=len(sources),
                    source_chunks=rag_source_chunks,
                    approved_evidence=approved_evidence,
                )
                if quality_issues:
                    raise ReportQualityError("Editorial revision failed the content gate: " + " | ".join(quality_issues))
                self._post_process(report, display_topic, sources, fact_pack)
                audit = self._audit_report_content(report, storyline_plan)
            if not self._editorial_audit_passed(audit):
                raise ReportQualityError("Editorial audit held publication: " + " | ".join(self._audit_corrections(audit)))
            report["content_quality_audit"] = audit
        except Exception as exc:
            (output_dir / "web_synthesis_error.txt").write_text(str(exc), encoding="utf-8")
            if self.rag_required or isinstance(exc, ReportQualityError):
                raise
            report = self._fallback_report(display_topic, plan, sources, fact_pack, str(exc))
            self._log(f"PHASE synthesis fallback used | reason={str(exc)[:240]!r}")
        self._log(
            "PHASE synthesis completed "
            f"| elapsed={self._elapsed(phase_start)} | raw_keys={','.join(sorted(report.keys())[:20])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE evidence_exhibits started | expected <10s")
        report["conflicts"] = evidence_conflicts
        evidence_exhibits = build_evidence_exhibits(display_topic, approved_evidence, fact_pack, plan=plan, chart_data_needs=chart_data_needs, language=self.language)
        if self.rag_context:
            self._filter_rag_exhibits(report, rag_source_chunks, approved_evidence, grounding_text)
            evidence_exhibits = [
                exhibit
                for exhibit in evidence_exhibits
                if rag_visible_numbers_supported(exhibit, grounding_text)
                and not combined_evidence_quality_issues(
                    {"exhibits": [exhibit]},
                    approved_evidence=approved_evidence,
                    conflicts=evidence_conflicts,
                    source_chunks=rag_source_chunks,
                )
            ]
        report = merge_evidence_exhibits(
            report,
            evidence_exhibits,
            preserve_existing=bool(self.rag_context),
        )
        if self.rag_context:
            self._label_exhibit_origins(report, rag_source_chunks, approved_evidence)
            self._apply_source_aware_exhibit_text(report)
        self._log(
            "PHASE evidence_exhibits completed "
            f"| elapsed={self._elapsed(phase_start)} | exhibits={len(evidence_exhibits)} "
            f"| backed_by_ledger={sum(1 for exhibit in evidence_exhibits if exhibit.get('data_basis'))}"
        )

        phase_start = time.monotonic()
        self._log("PHASE normalize_and_validate_schema started | expected <10s")
        report = normalize_web_report(
            report,
            topic=display_topic,
            language=self.language,
            allow_synthetic_fallbacks=not bool(self.rag_context),
        )
        if self.rag_context:
            final_quality_issues = rag_report_quality_issues(
                report,
                topic=display_topic,
                context_text=grounding_text,
                source_count=len(sources),
                source_chunks=rag_source_chunks,
            )
        else:
            final_quality_issues = report_content_quality_issues(
                report,
                topic=display_topic,
                context_text=grounding_text,
                source_count=len(sources),
            )
        if final_quality_issues:
            message = "Final report content quality gate failed: " + " | ".join(final_quality_issues)
            (output_dir / "web_report_quality_error.txt").write_text(message, encoding="utf-8")
            raise ReportQualityError(message)
        if self.rag_context:
            evidence_issues = combined_evidence_quality_issues(
                report,
                approved_evidence=approved_evidence,
                conflicts=evidence_conflicts,
                source_chunks=rag_source_chunks,
            )
            if evidence_issues:
                message = "Combined evidence quality gate failed: " + " | ".join(evidence_issues)
                (output_dir / "web_report_quality_error.txt").write_text(message, encoding="utf-8")
                raise RuntimeError(message)
        visible_hits = client_visible_internal_hits(self._client_visible_text(report))
        if visible_hits:
            self._log("PHASE publication_contract warning | visible_internal_language=" + ",".join(visible_hits[:6]))
        self._log(
            "PHASE normalize_and_validate_schema completed "
            f"| elapsed={self._elapsed(phase_start)} | takeaways={len(report.get('key_takeaways', []) or [])} "
            f"| sections={len(report.get('sections', []) or [])} | exhibits={len(report.get('exhibits', []) or [])} "
            f"| actions={len(report.get('action_steps', []) or [])} | references={len(report.get('references', []) or [])}"
        )

        phase_start = time.monotonic()
        self._log("PHASE assets started | expected 60-360s when AI images are enabled")
        assets = copy_or_generate_brand_assets(assets_dir)
        backup_dir = write_reference_backup(output_dir, report.get("references", []), source_dicts)
        assets.update(
            generate_ai_image_assets(
                self.client,
                display_topic,
                report,
                assets_dir,
                Path(backup_dir),
                language=self.language,
            )
        )
        self._log(
            "PHASE assets completed "
            f"| elapsed={self._elapsed(phase_start)} | asset_keys={','.join(sorted(assets.keys()))}"
        )

        phase_start = time.monotonic()
        self._log("PHASE render_and_write started | expected <10s")
        allow_synthetic_fallbacks = not bool(self.rag_context)
        web_query_count = min(
            len(search_queries),
            max(1, min(8, int(os.getenv("GEN_RPT_RAG_WEB_MAX_QUERIES", "4")))),
        ) if self.rag_context and normalized_source_mode != "collection_only" else 0
        rag_manifest = build_rag_manifest(
            self.rag_context,
            self.rag_sources,
            evidence_ledger,
            required=self.rag_required,
            public_sources=public_sources,
            conflicts=evidence_conflicts,
            web_required=web_required,
            web_query_count=web_query_count,
        )
        report["evidenceAudit"] = {
            "manifest": rag_manifest,
            "reconciliationStatus": "checked" if web_evidence_ledger else "no_structured_web_evidence" if supplemental_sources else "web_search_unavailable",
            "corroborationCount": sum(1 for item in web_evidence_ledger if item.get("status") == "corroborates_rag"),
            "evidenceLedger": evidence_ledger,
            "ragEvidenceLedger": rag_evidence_ledger,
            "webEvidenceLedger": web_evidence_ledger,
            "approvedEvidence": approved_evidence,
            "conflicts": evidence_conflicts,
        }
        html_path = render_web_report_html(
            report,
            assets,
            output_dir / "index.html",
            display_topic,
            self.language,
            allow_synthetic_fallbacks=allow_synthetic_fallbacks,
        )
        markdown_path = render_web_report_markdown(
            report,
            output_dir / "report.md",
            display_topic,
            self.language,
            allow_synthetic_fallbacks=allow_synthetic_fallbacks,
        )
        rendered_issues = rag_rendered_output_issues(
            html_path.read_text(encoding="utf-8"),
            conflict_count=len(evidence_conflicts),
        )
        if rendered_issues:
            message = "Rendered report quality gate failed: " + " | ".join(rendered_issues)
            (output_dir / "web_report_quality_error.txt").write_text(message, encoding="utf-8")
            raise ReportQualityError(message)

        (output_dir / "web_report_payload.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "chart_data_needs.json").write_text(json.dumps(chart_data_needs, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "analysis_framework.json").write_text(json.dumps(self._analysis_framework(plan, chart_data_needs, storyline_plan), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "publication_contract.json").write_text(json.dumps(self._publication_contract_metadata(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "research_fact_pack.json").write_text(json.dumps(fact_pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "evidence_ledger.json").write_text(json.dumps(evidence_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "rag_evidence_ledger.json").write_text(json.dumps(rag_evidence_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "web_evidence_ledger.json").write_text(json.dumps(web_evidence_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "approved_evidence.json").write_text(json.dumps(approved_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "evidence_conflicts.json").write_text(json.dumps(evidence_conflicts, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "storyline_plan.json").write_text(json.dumps(storyline_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "sources.json").write_text(json.dumps(source_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "rag_manifest.json").write_text(json.dumps(rag_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(
            "PHASE render_and_write completed "
            f"| elapsed={self._elapsed(phase_start)} | html={html_path} | markdown={markdown_path}"
        )
        self._log(f"END web report pipeline | total_elapsed={self._elapsed(run_start)}")

        return {
            "plan": plan,
            "chart_data_needs": chart_data_needs,
            "analysis_framework": self._analysis_framework(plan, chart_data_needs, storyline_plan),
            "publication_contract": self._publication_contract_metadata(),
            "fact_pack": fact_pack.to_dict(),
            "evidence_ledger": evidence_ledger,
            "rag_evidence_ledger": rag_evidence_ledger,
            "web_evidence_ledger": web_evidence_ledger,
            "approved_evidence": approved_evidence,
            "evidence_conflicts": evidence_conflicts,
            "storyline_plan": storyline_plan,
            "sources": source_dicts,
            "rag_manifest": rag_manifest,
            "report": report,
            "assets": assets,
            "output_dir": str(output_dir),
            "html_path": str(html_path),
            "markdown_path": str(markdown_path),
            "backup_dir": str(backup_dir),
            "source_mode": normalized_source_mode,
            "web_source_count": len(public_sources),
            "private_source_count": len(private_source_list) if normalized_source_mode != "web_only" else 0,
            "rag_source_count": len(self.rag_sources),
        }

    def _log(self, message: str) -> None:
        print(f"[gen_rpt.web] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)

    @staticmethod
    def _elapsed(start: float) -> str:
        seconds = max(0, int(time.monotonic() - start))
        minutes, remainder = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m{remainder:02d}s"
        return f"{remainder}s"

    def _collect_public_sources(
        self,
        search_queries: List[str],
        *,
        per_query: int,
        max_sources: int,
    ) -> List[SourceDocument]:
        if not self.rag_context:
            return collect_sources(search_queries, per_query=per_query, max_sources=max_sources)
        max_queries = max(1, min(8, int(os.getenv("GEN_RPT_RAG_WEB_MAX_QUERIES", "4"))))
        rag_per_query = max(1, min(3, int(os.getenv("GEN_RPT_RAG_WEB_PER_QUERY", "2"))))
        rag_max_sources = max(1, min(12, int(os.getenv("GEN_RPT_RAG_WEB_MAX_SOURCES", "8"))))
        sources = collect_sources(search_queries[:max_queries], per_query=rag_per_query, max_sources=rag_max_sources)
        if self._rag_web_required() and not sources:
            hint = " Configure SEARXNG_URL with a SearXNG instance whose JSON format is enabled." if not os.getenv("SEARXNG_URL") else ""
            raise RuntimeError(f"Combined web search returned zero usable sources from {len(search_queries[:max_queries])} planned queries.{hint}")
        return sources

    def _rag_web_required(self) -> bool:
        return bool(
            self.rag_context
            and str(os.getenv("GEN_RPT_RAG_WEB_REQUIRED", "true")).strip().lower() in {"1", "true", "yes", "on"}
        )

    def _combined_grounding_text(
        self,
        approved_evidence: List[Dict[str, Any]],
        sources: List[SourceDocument] | None = None,
    ) -> str:
        facts = [
            str(item.get("fact") or "").strip()
            for item in approved_evidence
            if str(item.get("fact") or "").strip()
        ]
        source_text = [
            "\n".join(str(value or "").strip() for value in (source.title, source.snippet, source.content) if str(value or "").strip())
            for source in sources or []
        ]
        return "\n".join([self.rag_context or "", *facts, *source_text]).strip()

    def _evidence_base_issues(
        self,
        authoritative_source_count: int,
        approved_evidence: List[Dict[str, Any]],
        web_evidence: List[Dict[str, Any]],
        *,
        web_required: bool,
    ) -> List[str]:
        issues = []
        if len(approved_evidence) < 10:
            issues.append(f"at least 10 approved evidence points are required; found {len(approved_evidence)}")
        if (not self.rag_context or web_required) and authoritative_source_count < 1:
            issues.append("at least one authority-weighted public source is required")
        if self.rag_context and web_required and len(web_evidence) < 2:
            issues.append(f"at least two supplementary public evidence points are required; found {len(web_evidence)}")
        return issues

    def _prepare_report_draft(
        self,
        report: Dict[str, Any],
        *,
        topic: str,
        grounding_text: str,
        source_count: int,
        source_chunks: Dict[str, str],
        approved_evidence: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], List[str]]:
        if self.rag_context:
            report = ground_rag_section_evidence(report, source_chunks)
            self._filter_rag_exhibits(report, source_chunks, approved_evidence, grounding_text)
        removed_numbers = prune_unsupported_numeric_claims(report, grounding_text)
        if removed_numbers:
            self._log("PHASE synthesis removed unsupported numeric claims | " + ", ".join(removed_numbers))
        if self.rag_context:
            issues = rag_report_quality_issues(
                report,
                topic=topic,
                context_text=grounding_text,
                source_count=source_count,
                source_chunks=source_chunks,
            )
        else:
            issues = report_content_quality_issues(
                report,
                topic=topic,
                context_text=grounding_text,
                source_count=source_count,
            )
        return report, issues

    def _revise_report_draft(
        self,
        report: Dict[str, Any],
        corrections: List[str],
        storyline_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        correction_text = "\n".join(f"- {item}" for item in corrections)
        prompt = f"""Revise the rejected executive report below and return the complete corrected JSON object only.

Keep the report in its existing language. Preserve its grounded facts, exact quotations, chunk identifiers, evidence items, references and supported numbers. Do not introduce outside facts, new numbers, new sources or invented citations.

Required corrections:
{correction_text}

Selected content modules:
{json.dumps(storyline_plan.get('selected_modules') or [], ensure_ascii=False)}

Rejected report:
{json.dumps(report, ensure_ascii=False)}

Revision contract:
- Return the full report with all existing top-level fields.
- Keep exactly 3 key_takeaways, 5-6 sections and 4-6 action_steps.
- Each section must contain title, lead, paragraphs, evidence and so_what.
- paragraphs must be a JSON array with 3-6 separate strings. Never put all section prose into one string.
- Each section must contain 250-450 words including lead and so_what; each paragraph must contain at least 45 words.
- Develop evidence, causal mechanism, counterpoint or risk, and management implication without filler or repetition.
- Keep at least 2 traceable evidence items per section. Private-document evidence must retain exact chunk quotations.
- Keep the full report between 2,000 and 3,000 reader-visible words.
- Every action must retain horizon, action, success_metric and a 12-word minimum evidence rationale.
"""
        return self.client.chat_json(
            [
                {
                    "role": "system",
                    "content": "You are a strict executive-report revision editor. Correct the supplied draft without adding facts. Return one valid JSON object only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.05,
        )

    def _audit_report_content(self, report: Dict[str, Any], storyline_plan: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""Audit this executive decision brief against its selected content modules. Use only the report text; do not add outside facts. Return JSON only.

Selected modules:
{json.dumps(storyline_plan.get('selected_modules') or [], ensure_ascii=False)}

Report:
{json.dumps(report, ensure_ascii=False)}

Score these dimensions: thesis_and_logic (0-25), evidence_and_citations (0-25), uncertainty_and_scenarios (0-25), strategic_usefulness (0-25).
Check for internal contradictions, unsupported high-risk claims, filler, repetition, incomplete sentences, unsupported scenario probabilities, and recommendations without an evidence rationale.

Return exactly:
{{
  "score": <integer 0-100>,
  "thesis_and_logic": <integer 0-25>,
  "evidence_and_citations": <integer 0-25>,
  "uncertainty_and_scenarios": <integer 0-25>,
  "strategic_usefulness": <integer 0-25>,
  "critical_issues": ["specific issue"],
  "revision_instructions": ["specific correction"]
}}
"""
        return self.client.chat_json(
            [
                {"role": "system", "content": "You are a strict institutional research editor. Return one valid JSON object only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )

    @staticmethod
    def _editorial_audit_passed(audit: Dict[str, Any]) -> bool:
        try:
            return bool(
                int(audit.get("score", 0)) >= 80
                and int(audit.get("evidence_and_citations", 0)) >= 20
                and int(audit.get("strategic_usefulness", 0)) >= 20
                and not [item for item in _as_list(audit.get("critical_issues")) if str(item).strip()]
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _audit_corrections(audit: Dict[str, Any]) -> List[str]:
        corrections = [
            str(item).strip()
            for key in ("critical_issues", "revision_instructions")
            for item in _as_list(audit.get(key))
            if str(item).strip()
        ]
        if corrections:
            return corrections
        return [
            "Raise the editorial score to at least 80/100, including at least 20/25 for evidence and strategic usefulness."
        ]

    def _plan_research(self, topic: str) -> Dict[str, Any]:
        system = "You are a senior research planner for a GateX executive intelligence publication. Return strict JSON only."
        if self.language == "zh":
            user = f"""
为一个 HTML-first 深度分析网页生成研究计划，输出 JSON。

主题：{topic}

必须包含字段：objective、audience、decision_question、issue_tree、hypotheses、market_sizing_plan、validation_data_needs、search_queries、source_strategy、outline、exhibit_ideas、risks。
要求：
- hypotheses 5-7 条，每条包含 id、hypothesis、decision_relevance、needed_evidence、search_queries。必须是可证伪、可找数据验证的商业假设。
- market_sizing_plan 必须包含 methods 数组，覆盖 top-down、bottom-up、adoption funnel、value pool 或 supply-side sizing 中至少 3 种；每种方法包含 formula、variables、preferred_sources、search_queries、known_limitations。
- validation_data_needs 8-12 条，列出市场规模、需求代理、客户/用户数、价格/ARPU/ASP、成本、产能/供给、融资、政策、竞争份额、案例或时间线等可检索数据。
- search_queries 12-16 条，优先能找到政府、监管、公司公告、年报、行业协会、国际组织、权威媒体、学术或咨询机构资料；其中至少 6 条直接服务于 market sizing 或假设验证。
- outline 5-6 个章节，标题必须是结论先行、洞察驱动；避免“市场概览、主要趋势、结论”等标签式标题。
- 生成紧凑的管理层决策简报，覆盖核心判断、来源支撑、因果机制、反例或风险、决策含义和行动。投资类主题还应在证据允许时包含来源比较、情景、敏感性和细分市场含义。
- exhibit_ideas 3-5 个，不要装饰图；每个图都要回答一个管理层问题。
- 明确哪些信息需要数字、案例、时间线或反例来验证。
"""
        else:
            user = f"""
Create a research plan for an HTML-first deep analysis article and return JSON only.

Topic: {topic}

Required fields: objective, audience, decision_question, issue_tree, hypotheses, market_sizing_plan, validation_data_needs, search_queries, source_strategy, outline, exhibit_ideas, risks.
Requirements:
- 5-7 hypotheses. Each must include id, hypothesis, decision_relevance, needed_evidence and search_queries. They must be falsifiable commercial hypotheses that can be tested with public data.
- market_sizing_plan must include a methods array covering at least three of: top-down, bottom-up, adoption funnel, value pool and supply-side sizing. Each method needs formula, variables, preferred_sources, search_queries and known_limitations.
- validation_data_needs: 8-12 searchable data needs covering market size, demand proxies, customers/users, price/ARPU/ASP, cost, capacity/supply, funding, policy, competitive share, cases or timeline proof.
- 12-16 public-web search queries, prioritizing government, regulators, company filings, annual reports, industry associations, international organizations, authoritative media, academic sources and consulting research. At least six queries should directly support market sizing or hypothesis testing.
- 5-6 conclusion-first outline headings. Each heading must be a specific, insight-driven statement; avoid generic headings like "Market Overview", "Key Trends" or "Conclusion".
- Build a compact executive decision brief, not an encyclopedia. The outline must cover a thesis, source-backed findings, causal drivers, counter-evidence or risk, decision implications and actions.
- Adapt the analysis to the topic: investment reports need source comparison, scenarios, sensitivities and segment implications; policy reports need stakeholder and implementation analysis; operating reports need options, trade-offs and failure modes.
- 3-5 exhibit ideas. No decorative visuals; every exhibit must answer an executive question.
- State what needs numbers, cases, timeline evidence or counter-evidence.
"""
        # RAG OVERRIDE: If private document context exists, replace with a document-grounded plan
        if self.rag_context:
            system = "You are a precise document analyst. Plan a report that is strictly grounded in the provided documents. Return strict JSON only."
            user = f"""Create a document-grounded research plan. Return JSON only.

Topic: {topic}

PRIVATE DOCUMENT CONTEXT (primary source of truth — use only these facts, do not invent):
{self.rag_context}

Required fields: objective, audience, decision_question, issue_tree, hypotheses, validation_data_needs, search_queries, source_strategy, outline, exhibit_ideas, risks.

Rules:
- Build hypotheses around the ACTUAL document facts listed above.
- search_queries: 8-12 queries to find EXTERNAL CONTEXT only (industry benchmarks, regulations, market data) that supplements the document. Do NOT search for facts already in the document.
- outline: 5-6 section headings reflecting the real document content. Each heading must be a conclusion-first insight statement, not a generic topic label.
- Build a compact decision brief around a thesis, source-backed findings, causal drivers, counter-evidence or risk, decision implications and actions. For investment topics include source comparison, scenarios, sensitivities and segment implications when the evidence supports them.
- CRITICAL: Do not invent salary figures, compensation amounts, job titles, years of experience or any other values not explicitly in the document.
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)

    def _plan_chart_data_needs(self, topic: str, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        system = "You are a strategy-report exhibit architect. Return strict JSON only."
        if self.language == "zh":
            user = f"""
为 HTML thought-leadership 报告先定义图表数据需求，输出 JSON。

主题：{topic}
研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}

返回字段：chart_data_needs，数组 7 项。
每项包含：title、chart_type、executive_question、narrative_role、pre_exhibit_context、post_exhibit_takeaway、required_metrics、comparison_set、preferred_sources、search_queries、data_quality_rule。

要求：
- chart_type 必须从 bar、line、bubble、matrix、timeline 中选择。
- 至少包含 2 个 line 图需求、1 个 bubble/scatter 图需求、2 个 bar/column 图需求、1 个 timeline 图需求；line 图需求必须寻找逐年或有明确年份的数据。
- line 图必须寻找至少 4 个同口径年度观测值；如果只能找到两个端点，必须要求一个可披露估算口径（例如 CAGR、GDP/需求驱动或公开预测基准）来生成中间年份，并把中间值标为 estimate，不能把两个端点直接画成趋势线。
- 覆盖 top-down sizing、bottom-up sizing、adoption funnel、价值池/ROI、投资/融资、产能/项目进展、成本/经济性、监管/采用门槛中的至少六类，但输出必须是要寻找的真实指标和来源，不是测算方法图。
- required_metrics 必须写成可搜索、可量化的数据项，尽量包含 year/date、amount/value、unit、entity/segment；不要写“战略评分/优先级指数/成熟度指数”。
- narrative_role 说明这张图在章节论证中承担什么角色；pre_exhibit_context 说明图前需要铺垫的管理问题或判断；post_exhibit_takeaway 说明图后必须写出的客户可读结论。它们是写作指令，不是客户可见标题。
- search_queries 每项 2-3 条，优先官方、监管、协会、公司公告、PDF 报告和权威数据源。
- 图表只能基于真实公开数据；如果找不到数据，后续报告应把它写成仍需证明的商业问题，而不是编造或展示后台验证清单。
"""
        else:
            user = f"""
Define chart data needs before source collection for an HTML thought-leadership report. Return JSON only.

Topic: {topic}
Research plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Return field: chart_data_needs, an array of 7 items.
Each item must include: title, chart_type, executive_question, narrative_role, pre_exhibit_context, post_exhibit_takeaway, required_metrics, comparison_set, preferred_sources, search_queries, data_quality_rule.

Requirements:
- chart_type must be one of bar, line, bubble, matrix, timeline.
- Include at least 2 line-chart needs, 1 bubble/scatter need, 2 bar/column needs and 1 timeline need. Line-chart needs must search for annual or explicitly dated values.
- A line chart must seek at least 4 same-basis annual observations. If only two endpoints are discoverable, require a transparent estimation rule such as CAGR, GDP/demand driver or a public forecast benchmark, mark intermediate values as estimates, and never present two endpoints as a trend line.
- Cover at least six of: top-down sizing, bottom-up sizing, adoption funnel, value pool/ROI, investment/funding, capacity/project progress, cost/economics, regulation/adoption gates, but output real metrics and sources to search for, not a sizing-method exhibit.
- required_metrics must be searchable quantitative data items and should include year/date, amount/value, unit, and entity/segment where possible. Do not request strategic scores, priority indexes, maturity indexes or other synthetic metrics.
- narrative_role states what job the exhibit plays in the section argument; pre_exhibit_context states the management question or claim that must set up the exhibit; post_exhibit_takeaway states the client-readable interpretation that must follow the exhibit. These are drafting instructions, not client-visible headings.
- search_queries: 2-3 targeted queries per chart, prioritizing official sources, regulators, industry associations, company announcements, PDF reports and authoritative datasets.
- Charts may use only real public data. If a dataset cannot be found, the report should frame it as a business question still needing proof rather than invent values or display a backstage validation checklist.
"""
        payload = self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.05)
        return self._normalize_chart_data_needs(payload.get("chart_data_needs") or payload.get("needs") or payload.get("charts") or [])

    def _fallback_chart_data_needs(self, topic: str, plan: Dict[str, Any], reason: str) -> List[Dict[str, Any]]:
        base_queries = [str(query) for query in plan.get("search_queries", []) or [] if str(query).strip()]
        if self.language == "zh":
            title_prefix = topic
        else:
            title_prefix = topic
        needs = [
            {
                "title": f"{title_prefix}: top-down market ceiling and addressable demand pool",
                "chart_type": "bar",
                "executive_question": "What is the largest credible demand pool before adoption, pricing and eligibility filters?",
                "required_metrics": ["total market size", "category spend or demand volume", "segment share", "forecast year"],
                "comparison_set": ["regions", "segments", "years"],
                "preferred_sources": ["government dataset", "industry association", "annual report", "international organization", "market report"],
                "search_queries": [f"{topic} total addressable market data", f"{topic} market size by segment forecast", f"{topic} demand volume official data"],
                "data_quality_rule": "Use source-stated market or demand values and keep forecast years and assumptions visible.",
                "sizing_role": "top_down_market_ceiling",
                "narrative_role": "Frame the largest credible ceiling before the report narrows to adoption and economics.",
                "pre_exhibit_context": "Introduce the management question: how big the prize could be before practical filters are applied.",
                "post_exhibit_takeaway": "Explain which demand pool remains credible and which filters still need proof.",
            },
            {
                "title": f"{title_prefix}: bottom-up buyer count, usage and pricing inputs",
                "chart_type": "matrix",
                "executive_question": "Which customer, unit-volume and price inputs can turn market narrative into a revenue bridge?",
                "required_metrics": ["customer count", "unit demand", "usage frequency", "price", "ARPU", "ASP"],
                "comparison_set": ["customer segments", "use cases", "regions"],
                "preferred_sources": ["company filings", "industry association", "regulator dataset", "survey", "case study"],
                "search_queries": [f"{topic} customer count price ARPU data", f"{topic} adoption rate users units sold", f"{topic} average selling price demand by customer segment"],
                "data_quality_rule": "Do not combine buyer count, usage and price unless all units are explicit and comparable.",
                "sizing_role": "bottom_up_revenue_bridge",
                "narrative_role": "Translate market narrative into unit economics that can be checked source by source.",
                "pre_exhibit_context": "Set up the need to move from market size language to customers, usage and price.",
                "post_exhibit_takeaway": "State which inputs are strong enough for a revenue bridge and which remain diligence items.",
            },
            {
                "title": f"{title_prefix}: investment and funding by year or company",
                "chart_type": "bar",
                "executive_question": "Where is capital actually flowing, and is the funding base deep enough for scale-up?",
                "required_metrics": ["funding amount", "investment year", "company or program name"],
                "comparison_set": ["companies", "programs", "years"],
                "preferred_sources": ["industry association report", "company announcement", "government program page", "PDF report"],
                "search_queries": [f"{topic} funding investment data report pdf", f"{topic} venture funding by company", f"{topic} government funding program amount"],
                "data_quality_rule": "Use named public amounts with dates and source URLs; do not convert them into priority scores.",
                "narrative_role": "Show whether capital formation is broadening or still concentrated in a few visible commitments.",
                "pre_exhibit_context": "Introduce why funding depth matters for scale-up credibility.",
                "post_exhibit_takeaway": "Interpret whether the capital base supports acceleration or remains fragile.",
            },
            {
                "title": f"{title_prefix}: market size, demand or addressable use cases",
                "chart_type": "bar",
                "executive_question": "How large is the commercial prize, and which demand pools are credible enough to size?",
                "required_metrics": ["market size", "demand volume", "revenue or value pool", "forecast year"],
                "comparison_set": ["segments", "regions", "years"],
                "preferred_sources": ["government dataset", "industry association", "annual report", "market report"],
                "search_queries": [f"{topic} market size forecast data", f"{topic} demand outlook by segment", f"{topic} addressable market report pdf"],
                "data_quality_rule": "Keep forecast assumptions visible; exclude unsourced TAM claims.",
                "sizing_role": "demand_pool_cross_check",
                "narrative_role": "Cross-check the commercial prize against demand pools that public sources actually quantify.",
                "pre_exhibit_context": "Explain why the report uses source-backed demand pools instead of broad opportunity language.",
                "post_exhibit_takeaway": "Clarify which segments deserve deeper validation and which remain too speculative.",
            },
            {
                "title": f"{title_prefix}: capacity, projects and commercialization milestones",
                "chart_type": "timeline",
                "executive_question": "Which projects have moved beyond claims into dated milestones?",
                "required_metrics": ["project name", "milestone date", "capacity or output metric", "status"],
                "comparison_set": ["projects", "technologies", "regions"],
                "preferred_sources": ["official project page", "regulatory filing", "company release", "government award page"],
                "search_queries": [f"{topic} project timeline capacity milestone", f"{topic} commercialization milestone official", f"{topic} pilot plant demonstration date"],
                "data_quality_rule": "Use dated public milestones; label claims that are company targets rather than achieved results.",
                "sizing_role": "supply_side_constraint",
                "narrative_role": "Move the reader from capital and demand signals to dated proof of execution.",
                "pre_exhibit_context": "Set up the question of whether public claims have become observable milestones.",
                "post_exhibit_takeaway": "State what the milestone pattern implies for timing, commitment and monitoring.",
            },
            {
                "title": f"{title_prefix}: cost and economics benchmark",
                "chart_type": "bar",
                "executive_question": "What cost benchmark must the new option beat before it changes resource allocation?",
                "required_metrics": ["cost", "LCOE", "capex", "opex", "price benchmark"],
                "comparison_set": ["technologies", "incumbent alternatives", "years"],
                "preferred_sources": ["IEA", "Lazard", "NREL", "government reports", "company filings"],
                "search_queries": [f"{topic} cost benchmark LCOE data", f"{topic} capex cost estimate report", f"{topic} economics comparison incumbent alternatives"],
                "data_quality_rule": "Compare like units only and keep speculative costs out of the chart.",
                "sizing_role": "unit_economics_gate",
                "narrative_role": "Put economics beside adoption potential so the reader sees the hurdle rate for resource allocation.",
                "pre_exhibit_context": "Introduce the cost benchmark the new option must beat.",
                "post_exhibit_takeaway": "Explain whether the economic hurdle is narrowing or still blocks near-term adoption.",
            },
            {
                "title": f"{title_prefix}: regulatory and adoption gate map",
                "chart_type": "matrix",
                "executive_question": "Which nontechnical gates could delay adoption even if the technology works?",
                "required_metrics": ["regulatory status", "license date", "approval stage", "adoption barrier"],
                "comparison_set": ["countries", "regulators", "use cases"],
                "preferred_sources": ["regulator", "government policy page", "international organization", "standards body"],
                "search_queries": [f"{topic} regulation licensing framework", f"{topic} regulator approval rules", f"{topic} adoption barriers policy report"],
                "data_quality_rule": "Treat missing regulation as an explicit evidence gap; do not score it subjectively.",
                "sizing_role": "adoption_gate",
                "narrative_role": "Show where nontechnical gates can slow adoption even when product or technology progress looks strong.",
                "pre_exhibit_context": "Set up regulation and adoption gates as timing constraints, not side issues.",
                "post_exhibit_takeaway": "Translate the gate map into a management monitoring priority.",
            },
        ]
        if reason:
            needs[0]["fallback_reason"] = reason[:240]
        for need, query in zip(needs, base_queries):
            need.setdefault("search_queries", []).append(query)
        return self._normalize_chart_data_needs(needs)

    def _normalize_chart_data_needs(self, value: Any) -> List[Dict[str, Any]]:
        needs: List[Dict[str, Any]] = []
        for idx, item in enumerate(_as_list(value), start=1):
            if not isinstance(item, dict):
                continue
            chart_type = str(item.get("chart_type") or item.get("type") or "bar").lower().replace("_chart", "")
            if chart_type not in {"bar", "line", "bubble", "matrix", "timeline"}:
                chart_type = "bar"
            queries = [str(query).strip() for query in _as_list(item.get("search_queries") or item.get("queries")) if str(query).strip()]
            need = {
                "id": str(item.get("id") or f"chart-need-{idx}"),
                "title": str(item.get("title") or item.get("name") or f"Chart data need {idx}").strip(),
                "chart_type": chart_type,
                "executive_question": str(item.get("executive_question") or item.get("question") or "").strip(),
                "required_metrics": [str(x).strip() for x in _as_list(item.get("required_metrics") or item.get("metrics")) if str(x).strip()],
                "comparison_set": [str(x).strip() for x in _as_list(item.get("comparison_set") or item.get("comparisons")) if str(x).strip()],
                "preferred_sources": [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()],
                "search_queries": queries[:4],
                "data_quality_rule": str(item.get("data_quality_rule") or item.get("quality_rule") or "").strip(),
                "sizing_role": str(item.get("sizing_role") or item.get("market_sizing_role") or "").strip(),
                "hypothesis_ids": [str(x).strip() for x in _as_list(item.get("hypothesis_ids") or item.get("hypotheses")) if str(x).strip()],
            }
            need["narrative_role"] = str(item.get("narrative_role") or self._default_chart_need_narrative_role(need)).strip()
            need["pre_exhibit_context"] = str(item.get("pre_exhibit_context") or self._default_chart_need_pre_context(need)).strip()
            need["post_exhibit_takeaway"] = str(item.get("post_exhibit_takeaway") or self._default_chart_need_post_takeaway(need)).strip()
            if need["title"] and need["search_queries"]:
                needs.append(need)
        return needs[:8]

    @staticmethod
    def _default_chart_need_narrative_role(need: Dict[str, Any]) -> str:
        question = str(need.get("executive_question") or "").strip()
        if question:
            return f"Use this exhibit to answer the management question: {question}"
        return "Use this exhibit to advance the section argument with source-backed quantitative evidence."

    @staticmethod
    def _default_chart_need_pre_context(need: Dict[str, Any]) -> str:
        question = str(need.get("executive_question") or "").strip()
        if question:
            return f"Set up the exhibit by stating why leaders need to answer: {question}"
        return "Set up the exhibit with the management decision or claim it will illuminate."

    @staticmethod
    def _default_chart_need_post_takeaway(need: Dict[str, Any]) -> str:
        title = str(need.get("title") or "the exhibit").strip()
        return f"After {title}, write the interpretation that changes the decision, not another chart or method note."

    def _chart_need_queries(self, chart_data_needs: List[Dict[str, Any]]) -> List[str]:
        queries: List[str] = []
        for need in chart_data_needs:
            for query in need.get("search_queries", []) or []:
                clean = str(query or "").strip()
                if clean and clean not in queries:
                    queries.append(clean)
        return queries

    def _expanded_search_queries(self, plan: Dict[str, Any], chart_data_needs: List[Dict[str, Any]]) -> List[str]:
        plan_queries = [str(query).strip() for query in plan.get("search_queries", []) or [] if str(query).strip()]
        if self.rag_context:
            return self._dedupe_texts(plan_queries)
        chart_queries = self._chart_need_queries(chart_data_needs)
        framework_queries = self._analysis_framework_queries(plan)
        combined: List[str] = []
        for query in plan_queries[:4] + chart_queries + framework_queries + plan_queries[4:]:
            if query and query not in combined:
                combined.append(query)
        return combined

    def _synthesize_web_report(
        self,
        topic: str,
        plan: Dict[str, Any],
        chart_data_needs: List[Dict[str, Any]],
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
        evidence_ledger: List[Dict[str, Any]],
        storyline_plan: Dict[str, Any],
        evidence_conflicts: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        source_blocks = []
        synthesis_sources = [] if self.rag_context else sources
        for idx, source in enumerate(synthesis_sources[:14], start=1):
            source_blocks.append(
                f"[Source {idx}]\n"
                f"Title: {source.title}\n"
                f"URL: {source.url}\n"
                f"Domain: {source.domain}\n"
                f"Type: {source.source_type}\n"
                f"Snippet: {source.snippet}\n"
                f"Excerpt:\n{source.content[:2200]}"
            )
        source_text = "\n\n".join(source_blocks) or ("No reliable source text was fetched." if self.language == "en" else "未抓取到可靠资料正文。")
        if self.rag_context:
            prompt_evidence = [item for item in evidence_ledger if item.get("origin") == "rag"][:16]
            prompt_evidence.extend(item for item in evidence_ledger if item.get("origin") == "web")
            prompt_evidence = prompt_evidence[:24]
        else:
            prompt_evidence = evidence_ledger[:24]
        evidence_text = json.dumps(prompt_evidence, ensure_ascii=False, indent=2)
        conflict_text = json.dumps((evidence_conflicts or [])[:8], ensure_ascii=False, indent=2)
        contract_text = publication_contract_prompt(self.language)
        system = "You are an elite strategy research author. Return one valid JSON object only. No markdown."
        if self.language == "zh":
            user = f"""
生成一份 HTML-first、符合 GateX executive intelligence publication 标准的深度分析网页报告数据结构，输出 JSON。

	主题：{topic}
	研究计划：{json.dumps(plan, ensure_ascii=False, indent=2)}
	图表数据需求（这些需求已用于定向检索）：
	{json.dumps(chart_data_needs, ensure_ascii=False, indent=2)}
	叙事主线计划：{json.dumps(storyline_plan, ensure_ascii=False, indent=2)}
事实包：{fact_pack.digest()}
证据台账（图表和数字判断只能来自这里或事实包）：
{evidence_text}
资料摘录：
{source_text}

客户可见合同（必须遵守）：
{contract_text}

必须包含字段：
title、dek、category、authors、intro、key_takeaways、sections、exhibits、action_steps、methodology、evidence_quality、references、disclaimer。

写作要求：
- 全程中文，面向 CEO/董事会/战略团队。
- 深度和分析严谨性是首要目标；生成一份 2,000-3,000 字、包含 5-6 个扎实章节的紧凑决策简报，不要凑字数。
- title 和章节标题必须结论先行；不要用"概览、背景、趋势、分析、结论"这类标签标题。
- 叙事必须像一篇成熟咨询 publication：前台只呈现结论、案例、数字、机制、反例和管理含义；后台思考工具不得露出。
- 可以在内部使用假设验证和市场机会测算来组织证据，但客户可见字段不得出现 hypothesis、假设验证、market sizing、sizing bridge、TAM、SAM、SOM、issue tree、fact pack、evidence ledger、storyline plan、validation task、source boundary、data basis 等方法名或工作台语言。
- exhibits 仍必须保留 JSON 键 data_basis 以便机器可追溯；但 title、subtitle、caption、source_note、paragraphs、methodology 等可见文案不得写 "data basis"。
- 每个关键判断要能被事实包、证据台账或来源支撑；缺失变量要自然写成"还需要验证的商业问题"，不要写成框架步骤。
- key_takeaways 3 条，每条必须有明确判断和管理含义。
- sections 5-6 个；每个包含 title、lead、paragraphs、evidence、so_what。paragraphs 必须是包含 3-6 个独立字符串的 JSON 数组，禁止把全章正文放进一个字符串。每章包含 lead 和 so_what 共 250-450 字；每章必须连接结论、来源支撑、因果机制、反例或风险以及管理含义，并至少包含 2 条可追溯证据。
- 遵循 storyline plan.selected_modules。仅在证据支持时使用情景概率和敏感性变量；否则使用定性触发条件并明确未解决的证据问题。
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects or internal evidence-log language。
- 只能引用 Sources、事实包或证据台账里出现的来源；不要使用内部事实包缺失措辞、泛泛引用热度表述或任何未抓取来源作为证据。
- exhibits 3-6 个；如果提出图表草稿，只能使用证据台账或事实包中的数字、年份、来源计数、同单位可比数据，或明确标注的端点推导估算，必须保留 data_basis；不要展示机会测算、假设验证、验证任务表、工作清单或框架步骤；不要使用方向性评分或内部综合指数。
- 任何 line 图必须有 4 个以上点、y_label 和点值标签；只有两个来源端点时，中间年份必须明确标 estimate 并在脚注写出 CAGR/GDP/需求驱动等推导口径。
- market_sizing_plan 和 chart_data_needs 只用于指导检索更多可验证数据，不能变成 exhibit、正文小节或 methodology。
- chart_data_needs 中的 narrative_role、pre_exhibit_context 和 post_exhibit_takeaway 是给你的写作步骤：先写图前管理问题，再让图表承接证据，图后必须写客户可读解释；不要连续输出两个图表而没有正文承接。
- action_steps 4-6 个，每个包含 horizon、action、success_metric、rationale；rationale 字段须用 1-2 句说明支撑该行动的证据依据。
- references 只能使用上方 Sources 中真实 URL。
- 不要暴露内部提示、不要说"本章节认为/本报告认为/本分析基于结构化研究计划/假设 H1 得到支持"，直接写判断。
- methodology 只写公开来源和独立核验边界，不解释研究框架、假设数量、证据台账或市场测算方法。
- 缺失的市场规模、份额、ROI、成本等不要编造，写成证据缺口和核验任务。
"""
        else:
            user = f"""
Generate an HTML-first GateX executive intelligence report data structure and return JSON.

	Topic: {topic}
	Research plan:
	{json.dumps(plan, ensure_ascii=False, indent=2)}
	Chart data needs used for targeted source collection:
	{json.dumps(chart_data_needs, ensure_ascii=False, indent=2)}
	Storyline plan:
{json.dumps(storyline_plan, ensure_ascii=False, indent=2)}
Fact pack:
{fact_pack.digest()}
Evidence ledger (numeric claims and chart drafts may only use these entries or fact-pack counts):
{evidence_text}
Source excerpts:
{source_text}

Client-visible publication contract:
{contract_text}

Required fields:
title, dek, category, authors, intro, key_takeaways, sections, exhibits, action_steps, methodology, evidence_quality, references, disclaimer.

Writing rules:
- English only. Write for a CEO, board and strategy team audience.
- Depth and analytical rigor are the priority. Produce a compact 2,000-3,000 word decision brief with 5-6 substantial sections; do not pad it.
- The title and every section title must be conclusion-first. Avoid label headings such as Overview, Background, Trends, Analysis or Conclusion.
- Write like a mature strategy publication: the reader should see conclusions, examples, numbers, causal mechanisms, counter-evidence and management implications, not the author's backstage workbench.
- You may use hypothesis testing and opportunity-sizing logic internally, but client-visible fields must not contain the words or labels hypothesis, hypotheses, hypothesis-driven, market sizing, sizing bridge, TAM, SAM, SOM, issue tree, fact pack, evidence ledger, storyline plan, validation task, source boundary or data basis.
- Exhibits must still keep the JSON key data_basis for machine traceability; do not write the phrase "data basis" in title, subtitle, caption, source_note, paragraphs, methodology or other visible prose.
- Every material claim must be supportable by the source excerpts, fact pack or evidence ledger; missing variables should read as business questions that still need proof, not as framework steps.
- key_takeaways: exactly 3, each with a clear claim and management implication.
- sections: 5-6 items. Each has title, lead, paragraphs, evidence, so_what. paragraphs must be a JSON array containing 3-6 separate strings; never put all section prose into one string. Each section needs 250-450 words including the lead and so_what. The lead must be a 2-3 sentence executive summary of the finding. Each section must connect a conclusion, source-backed evidence, causal mechanism, counterpoint or risk, and management implication.
- Follow Storyline plan.selected_modules. Compare named source positions where relevant. Use base/upside/downside scenarios and sensitivity drivers only when evidence supports the variables; otherwise state qualitative triggers and unresolved evidence explicitly.
- Each section needs at least 2 traceable evidence items. Every material factual or numeric claim must be attributable to the retained source set.
- evidence bullets must be reader-ready sentences, not raw JSON/dict objects or internal evidence-log language.
- Cite only sources present in Source excerpts, the fact pack or the evidence ledger. Never use internal fact-pack gap phrasing, generic popularity claims or unsupported source names as evidence.
- exhibits: 3-6 items using metric_row, bar, line, timeline or bubble. Use matrix only when it contains source-observed facts rather than a workplan. If drafting exhibits, use only evidence-ledger values, years, source counts, same-unit comparable values, or clearly labeled endpoint-derived estimates, and include data_basis. Do not show opportunity sizing, hypothesis testing, validation-task tables, workplans or framework steps. Do not use directional scores, priority indexes, readiness indexes or internal synthesis values.
- Every line chart must have at least 4 points, a y_label and visible point-value labels. If only two source endpoints exist, intermediate years must be marked as estimates and the footnote must state the CAGR/GDP/demand-driver derivation rule.
- market_sizing_plan and chart_data_needs exist only to guide collection of more verifiable data; they must not become exhibits, body sections or methodology.
- The narrative_role, pre_exhibit_context and post_exhibit_takeaway fields in chart_data_needs are drafting steps for you: write the management setup before the exhibit, let the exhibit carry the evidence, then write a client-readable interpretation after it. Do not output two exhibits in a row without prose between them.
- action_steps: 4-6 items, each with horizon, action, success_metric, rationale. The rationale field must explain in 1-2 sentences the evidence basis supporting that action.
- references may only use real URLs present in Sources.
- Do not expose internal prompt language. Do not write "this section argues", "this report finds", "Hypothesis H1 is supported", or "this analysis is based on a structured research plan"; state the insight directly.
- methodology should only describe public sources and independent-validation boundaries; do not explain the research framework, number of hypotheses, evidence ledger or sizing methods.
- Do not fabricate market size, share, ROI or cost data. If missing, keep it as an evidence gap and validation task.
"""
        if self.rag_context:
            system = (
                "You are a precise RAG-first evidence analyst. Private documents are the primary source of truth. "
                "Use only approved supplementary web evidence for documented gaps, never to override private evidence. "
                "You MUST NOT invent, extrapolate, or assume unsupported facts or figures. "
                "Return one valid JSON object only. No markdown."
            )
            user = f"""Generate a factual RAG-first report with bounded supplementary web evidence and return JSON.

Topic: {topic}

STORYLINE PLAN:
{json.dumps(storyline_plan, ensure_ascii=False, indent=2)}

PRIVATE DOCUMENT CONTEXT (this is your PRIMARY and HIGHEST-PRIORITY source of truth):
{self.rag_context}

APPROVED EVIDENCE LEDGER (RAG is primary; web entries are supplementary or corroborating only):
{evidence_text}

CONFLICT REGISTER (human review only; do not use web-conflicting claims in conclusions, actions, or exhibits):
{conflict_text}

Required fields:
title, dek, category, authors, intro, key_takeaways, sections, exhibits, action_steps, methodology, evidence_quality, references, disclaimer.

CRITICAL RULES (violation = failure):
1. Use private-document facts first. Use an approved web fact only when it fills a gap or corroborates RAG, and label it as supplementary web evidence.
2. If a claim appears in neither the private context nor the approved evidence ledger, write exactly: "Not stated in the validated evidence."
3. Do NOT invent salaries, compensation figures, years of experience, job titles, team sizes, budget amounts, or any other quantitative values.
4. Every numeric claim must appear in the private context or an approved evidence-ledger fact. Never use a value from the conflict register.
5. key_takeaways: exactly 3, each grounded in document facts.
6. sections: 5-6 substantial items within a 2,000-3,000 word report. Each needs a conclusion-first title, a decisive 2-3 sentence lead, at least 2 evidence items, causal explanation, a counterpoint or risk, and a management implication of at least 35 words. paragraphs must be a JSON array containing 3-6 separate strings, never one string containing all section prose. Each section needs 250-450 words including lead and so_what.
7. action_steps: 4-6 items based on what the document states, not invented recommendations. Each must include horizon, action, success_metric, and rationale (1-2 sentences explaining the evidence basis from the private documents).
8. references: only use real internal identifiers or URLs present in the approved evidence ledger.
9. Every section evidence list must include at least two items from distinct chunks formatted exactly as `[Chunk: <exact chunk id>] "<exact supporting excerpt of 20+ characters>" — <why it matters>`. Never invent a chunk id or alter the quoted text.
10. Every exhibit must use approved values and include data_basis. For RAG use `{{"id": "<exact chunk id>", "fact": "<exact supporting excerpt>"}}`; for web use the exact approved evidence ID and fact. Unsupported exhibits will be removed.
11. Numbers in action_steps, success metrics, timelines, labels, and exhibits must appear in the private context or approved evidence. Use non-numeric decision gates when no approved value exists.
12. Do not generate or resolve the human-review conflict section. The pipeline adds it deterministically after synthesis.
13. Follow Storyline plan.selected_modules. Compare named source positions where relevant. Use scenario probabilities only when they appear in approved evidence; otherwise use qualitative scenario triggers and state the unresolved evidence.
"""
        return self.client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.12)

    def _post_process(
        self,
        report: Dict[str, Any],
        topic: str,
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
    ) -> None:
        report.setdefault("title", topic)
        report.setdefault("category", "Deep research" if self.language == "en" else "深度研究")
        report["source_count"] = len(sources) if self.rag_context else fact_pack.source_count
        if not report.get("evidence_quality"):
            if self.rag_context:
                internal_count = sum(1 for source in sources if source.source_type == "internal")
                public_count = sum(1 for source in sources if source.source_type != "internal")
                report["evidence_quality"] = (
                    f"The report retained {internal_count} validated private-document fragments "
                    f"and {public_count} supplementary public sources."
                )
            elif fact_pack.validation_issues:
                report["evidence_quality"] = " ".join(fact_pack.validation_issues[:3])
            else:
                report["evidence_quality"] = (
                    f"Evidence base includes {fact_pack.source_count} public sources and {fact_pack.authoritative_source_count} authority-weighted sources."
                    if self.language == "en"
                    else f"资料底座包含 {fact_pack.source_count} 个公开来源，其中 {fact_pack.authoritative_source_count} 个具有权威来源特征。"
                )
        source_by_url = {source.url: source for source in sources if source.url}
        real_urls = set(source_by_url)
        refs = []
        seen_ref_urls = set()
        for item in report.get("references", []) or []:
            if isinstance(item, dict):
                url = str(item.get("url") or "")
                if url in real_urls and url not in seen_ref_urls:
                    source = source_by_url[url]
                    refs.append({**item, "origin": "rag" if source.source_type == "internal" else "web"})
                    seen_ref_urls.add(url)
        if self.rag_context:
            reference_sources = [source for source in sources if source.source_type == "internal"][:6]
            reference_sources.extend(source for source in sources if source.source_type != "internal")
            reference_sources = reference_sources[:12]
        else:
            reference_sources = sources[:14]
        target_ref_count = min(12 if self.rag_context else 8, len([source for source in reference_sources if source.url]))
        for source in reference_sources:
            if len(refs) >= target_ref_count:
                break
            if not source.url or source.url in seen_ref_urls:
                continue
            refs.append(
                {
                    "title": source.title or source.domain or source.url,
                    "url": source.url,
                    "note": source.snippet,
                    "origin": "rag" if source.source_type == "internal" else "web",
                }
            )
            seen_ref_urls.add(source.url)
        report["references"] = refs
        report.setdefault(
            "disclaimer",
            "Prepared for strategy discussion; validate source data before investment, transaction or operating decisions."
            if self.language == "en"
            else "本报告用于战略讨论；用于投资、交易或运营决策前需独立核验来源数据。",
        )

    def _filter_rag_exhibits(
        self,
        report: Dict[str, Any],
        source_chunks: Dict[str, str],
        approved_evidence: List[Dict[str, Any]] | None = None,
        grounding_text: str | None = None,
    ) -> None:
        report["exhibits"] = [
            exhibit
            for exhibit in report.get("exhibits", []) or []
            if rag_exhibit_is_grounded(
                exhibit,
                context_text=grounding_text or self.rag_context or "",
                source_chunks=source_chunks,
                approved_evidence=approved_evidence,
            )
        ]

    @staticmethod
    def _label_exhibit_origins(
        report: Dict[str, Any],
        source_chunks: Dict[str, str],
        approved_evidence: List[Dict[str, Any]],
    ) -> None:
        origin_by_id = {
            str(item.get("id") or ""): str(item.get("origin") or "")
            for item in approved_evidence
            if item.get("id")
        }
        for exhibit in report.get("exhibits", []) or []:
            if not isinstance(exhibit, dict):
                continue
            for basis in exhibit.get("data_basis", []) or []:
                if not isinstance(basis, dict) or basis.get("origin"):
                    continue
                basis_id = str(basis.get("chunk_id") or basis.get("id") or "").strip()
                if basis_id in source_chunks:
                    basis["origin"] = "rag"
                elif origin_by_id.get(basis_id):
                    basis["origin"] = origin_by_id[basis_id]

    @staticmethod
    def _apply_source_aware_exhibit_text(report: Dict[str, Any]) -> None:
        for exhibit in report.get("exhibits", []) or []:
            if not isinstance(exhibit, dict):
                continue
            origins = {
                str(item.get("origin") or "")
                for item in exhibit.get("data_basis", []) or []
                if isinstance(item, dict) and item.get("origin")
            }
            if not origins:
                continue
            descriptor = "private-document" if origins == {"rag"} else "supplementary web" if origins == {"web"} else "validated"
            for key in ("title", "subtitle", "caption", "source_note", "footnote"):
                if exhibit.get(key):
                    exhibit[key] = WebReportPipeline._replace_public_source_language(exhibit[key], descriptor)
            for key in ("rows", "columns", "values"):
                if key in exhibit:
                    exhibit[key] = WebReportPipeline._replace_public_source_language(exhibit[key], descriptor)

    @staticmethod
    def _replace_public_source_language(value: Any, descriptor: str) -> Any:
        if isinstance(value, list):
            return [WebReportPipeline._replace_public_source_language(item, descriptor) for item in value]
        if not isinstance(value, str):
            return value
        replacements = {
            r"\bpublic[- ]source(?:s)?\b": f"{descriptor} sources",
            r"\bpublic evidence\b": f"{descriptor} evidence",
            r"\bpublic proof\b": f"{descriptor} proof",
            r"\bpublic data\b": f"{descriptor} data",
            r"\bpublic record\b": f"{descriptor} record",
            r"\bpublic signals?\b": f"{descriptor} signals",
        }
        text_value = value
        for pattern, replacement in replacements.items():
            text_value = re.sub(pattern, replacement, text_value, flags=re.I)
        return text_value

    def _normalize_research_plan(self, plan: Dict[str, Any], topic: str) -> Dict[str, Any]:
        normalized = dict(plan or {})
        normalized["search_queries"] = self._dedupe_texts(_as_list(normalized.get("search_queries")))[:18]
        normalized["hypotheses"] = self._normalize_hypotheses(normalized.get("hypotheses"), topic)
        normalized["market_sizing_plan"] = self._normalize_market_sizing_plan(normalized.get("market_sizing_plan"), topic)
        normalized["validation_data_needs"] = self._normalize_validation_data_needs(normalized.get("validation_data_needs"), topic)

        if not self.rag_context:
            for query in self._analysis_framework_queries(normalized):
                if query not in normalized["search_queries"]:
                    normalized["search_queries"].append(query)
                if len(normalized["search_queries"]) >= 18:
                    break
        return normalized

    def _normalize_hypotheses(self, value: Any, topic: str) -> List[Dict[str, Any]]:
        items = []
        for idx, item in enumerate(_as_list(value), start=1):
            if isinstance(item, dict):
                hypothesis = str(item.get("hypothesis") or item.get("claim") or item.get("title") or "").strip()
                needed = [str(x).strip() for x in _as_list(item.get("needed_evidence") or item.get("evidence_needed") or item.get("data_needed")) if str(x).strip()]
                queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
                decision_relevance = str(item.get("decision_relevance") or item.get("why_it_matters") or item.get("management_relevance") or "").strip()
                item_id = str(item.get("id") or f"H{idx}").strip()
            else:
                hypothesis = str(item or "").strip()
                needed = []
                queries = []
                decision_relevance = ""
                item_id = f"H{idx}"
            if not hypothesis:
                continue
            items.append(
                {
                    "id": item_id or f"H{idx}",
                    "hypothesis": hypothesis,
                    "decision_relevance": decision_relevance,
                    "needed_evidence": needed[:6],
                    "search_queries": queries[:4],
                }
            )
        if len(items) < 5:
            defaults = self._default_hypotheses(topic)
            seen = {self._norm_key(item["hypothesis"]) for item in items}
            for item in defaults:
                if self._norm_key(item["hypothesis"]) not in seen:
                    items.append(item)
                    seen.add(self._norm_key(item["hypothesis"]))
                if len(items) >= 6:
                    break
        return items[:7]

    def _normalize_market_sizing_plan(self, value: Any, topic: str) -> Dict[str, Any]:
        raw = dict(value or {}) if isinstance(value, dict) else {}
        methods = []
        method_source = raw.get("methods") or raw.get("approaches") or ([] if isinstance(value, dict) else value)
        for idx, item in enumerate(_as_list(method_source), start=1):
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or item.get("name") or item.get("type") or f"Method {idx}").strip()
            formula = str(item.get("formula") or item.get("calculation") or "").strip()
            variables = [str(x).strip() for x in _as_list(item.get("variables") or item.get("inputs")) if str(x).strip()]
            sources = [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()]
            queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
            limitations = [str(x).strip() for x in _as_list(item.get("known_limitations") or item.get("limitations")) if str(x).strip()]
            if method:
                methods.append(
                    {
                        "method": method,
                        "formula": formula,
                        "variables": variables[:8],
                        "preferred_sources": sources[:6],
                        "search_queries": queries[:4],
                        "known_limitations": limitations[:4],
                    }
                )
        if len(methods) < 3:
            defaults = self._default_market_sizing_plan(topic)["methods"]
            seen = {self._norm_key(item["method"]) for item in methods}
            for item in defaults:
                if self._norm_key(item["method"]) not in seen:
                    methods.append(item)
                    seen.add(self._norm_key(item["method"]))
                if len(methods) >= 5:
                    break
        default_plan = self._default_market_sizing_plan(topic)
        return {
            "sizing_question": str(raw.get("sizing_question") or raw.get("question") or default_plan["sizing_question"]).strip(),
            "methods": methods[:5],
            "evidence_rule": str(raw.get("evidence_rule") or "Use public source values when available; keep missing variables as validation tasks.").strip(),
        }

    def _normalize_validation_data_needs(self, value: Any, topic: str) -> List[Dict[str, Any]]:
        needs = []
        for idx, item in enumerate(_as_list(value), start=1):
            if isinstance(item, dict):
                metric = str(item.get("metric") or item.get("data") or item.get("name") or item.get("title") or "").strip()
                reason = str(item.get("decision_use") or item.get("reason") or item.get("why_needed") or "").strip()
                sources = [str(x).strip() for x in _as_list(item.get("preferred_sources") or item.get("sources")) if str(x).strip()]
                queries = [str(x).strip() for x in _as_list(item.get("search_queries") or item.get("queries")) if str(x).strip()]
            else:
                metric = str(item or "").strip()
                reason = ""
                sources = []
                queries = []
            if metric:
                needs.append({"id": f"D{idx}", "metric": metric, "decision_use": reason, "preferred_sources": sources[:5], "search_queries": queries[:4]})
        if len(needs) < 8:
            defaults = self._default_validation_data_needs(topic)
            seen = {self._norm_key(item["metric"]) for item in needs}
            for item in defaults:
                if self._norm_key(item["metric"]) not in seen:
                    needs.append(item)
                    seen.add(self._norm_key(item["metric"]))
                if len(needs) >= 12:
                    break
        return needs[:12]

    def _analysis_framework_queries(self, plan: Dict[str, Any]) -> List[str]:
        queries: List[str] = []
        for hypothesis in plan.get("hypotheses", []) or []:
            if isinstance(hypothesis, dict):
                queries.extend(str(x).strip() for x in _as_list(hypothesis.get("search_queries")) if str(x).strip())
        sizing_plan = plan.get("market_sizing_plan") or {}
        if isinstance(sizing_plan, dict):
            for method in sizing_plan.get("methods", []) or []:
                if isinstance(method, dict):
                    queries.extend(str(x).strip() for x in _as_list(method.get("search_queries")) if str(x).strip())
        for need in plan.get("validation_data_needs", []) or []:
            if isinstance(need, dict):
                queries.extend(str(x).strip() for x in _as_list(need.get("search_queries")) if str(x).strip())
        return self._dedupe_texts(queries)

    def _analysis_framework(self, plan: Dict[str, Any], chart_data_needs: List[Dict[str, Any]], storyline_plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision_question": plan.get("decision_question"),
            "hypotheses": plan.get("hypotheses", []),
            "market_sizing_plan": plan.get("market_sizing_plan", {}),
            "validation_data_needs": plan.get("validation_data_needs", []),
            "chart_data_needs": chart_data_needs,
            "storyline_plan": storyline_plan,
        }

    def _publication_contract_metadata(self) -> Dict[str, Any]:
        return {
            "root_cause": "DeepSeek is treated as a diligent low-agency worker. It can collect data and draft structured text, but it is not trusted to decide what belongs in client-visible prose.",
            "architecture": [
                "Step 1: DeepSeek creates a backstage research plan with falsifiable hypotheses, opportunity-sizing methods and searchable data needs.",
                "Step 2: DeepSeek converts those needs into chart storyboards with required metrics, source queries, narrative role, pre-exhibit setup and post-exhibit interpretation.",
                "Step 3: deterministic collection and evidence extraction build a fact pack and evidence ledger from public sources.",
                "Step 4: DeepSeek drafts only the publication layer: conclusions, examples, numbers, mechanisms, counter-evidence and management implications.",
                "Step 5: deterministic exhibit generation replaces model-invented charts with source-backed exhibits and inserts narrative bridges between adjacent exhibits.",
                "Step 6: one shared content gate enforces a 2,000-3,000 word decision brief, evidence depth, management implications and evidence-based actions.",
                "Step 7: a pre-publication editorial audit revises one failed draft, then holds publication if evidence or strategic quality remains below threshold.",
                "Step 8: rendered-output gates fail HTML if workbench language, unsupported charts or consecutive exhibits without prose remain.",
            ],
            "client_visible_contract": publication_contract_prompt(self.language),
        }

    @staticmethod
    def _client_visible_text(report: Dict[str, Any]) -> str:
        visible = {
            "title": report.get("title"),
            "dek": report.get("dek"),
            "intro": report.get("intro"),
            "key_takeaways": report.get("key_takeaways"),
            "sections": report.get("sections"),
            "exhibits": [
                {
                    "title": exhibit.get("title"),
                    "subtitle": exhibit.get("subtitle"),
                    "caption": exhibit.get("caption"),
                    "source_note": exhibit.get("source_note"),
                    "rows": exhibit.get("rows"),
                    "columns": exhibit.get("columns"),
                    "values": exhibit.get("values"),
                    "categories": exhibit.get("categories"),
                    "series": exhibit.get("series"),
                    "points": exhibit.get("points"),
                }
                for exhibit in report.get("exhibits", []) or []
                if isinstance(exhibit, dict)
            ],
            "action_steps": report.get("action_steps"),
            "methodology": report.get("methodology"),
        }
        return json.dumps(visible, ensure_ascii=False)

    def _default_hypotheses(self, topic: str) -> List[Dict[str, Any]]:
        if self.language == "zh":
            return [
                {"id": "H1", "hypothesis": f"{topic} 的需求池足够大，值得进入资源配置讨论。", "decision_relevance": "决定是否需要做完整市场规模测算。", "needed_evidence": ["市场规模", "需求量", "分地区或分场景需求"], "search_queries": [f"{topic} 市场规模 数据", f"{topic} 需求 预测 分场景"]},
                {"id": "H2", "hypothesis": "客户采用和付费意愿已经出现可验证信号。", "decision_relevance": "决定是否进入客户验证和销售资源投入。", "needed_evidence": ["客户数量", "采用率", "订单", "价格或 ARPU"], "search_queries": [f"{topic} 客户 采用率 价格", f"{topic} 订单 客户 案例"]},
                {"id": "H3", "hypothesis": "供给、产能或交付能力不会成为短期增长瓶颈。", "decision_relevance": "决定增长判断是否需要被供应侧约束下调。", "needed_evidence": ["产能", "项目", "供应链", "交付周期"], "search_queries": [f"{topic} 产能 项目 供应链", f"{topic} 公司 公告 产能"]},
                {"id": "H4", "hypothesis": "单位经济性或成本曲线已经接近可商业化区间。", "decision_relevance": "决定机会是预算项、试点项还是观察项。", "needed_evidence": ["成本", "价格", "毛利率", "CAPEX", "OPEX"], "search_queries": [f"{topic} 成本 价格 毛利率", f"{topic} CAPEX OPEX 经济性"]},
                {"id": "H5", "hypothesis": "领先者存在可防守的竞争优势，而不是短期窗口。", "decision_relevance": "决定是否押注特定公司、伙伴或能力。", "needed_evidence": ["市场份额", "专利", "客户案例", "融资", "渠道"], "search_queries": [f"{topic} 竞争格局 市场份额", f"{topic} 领先企业 融资 专利 客户"]},
                {"id": "H6", "hypothesis": "政策、监管或标准的时间表支持商业化节奏。", "decision_relevance": "决定进入节奏和风险缓冲。", "needed_evidence": ["政策日期", "监管许可", "标准", "补贴或采购"], "search_queries": [f"{topic} 政策 监管 标准", f"{topic} 补贴 许可 时间表"]},
            ]
        return [
            {"id": "H1", "hypothesis": f"The demand pool for {topic} is large enough to deserve resource-allocation debate.", "decision_relevance": "Determines whether a full sizing exercise is warranted.", "needed_evidence": ["market size", "demand volume", "regional or segment demand"], "search_queries": [f"{topic} market size data", f"{topic} demand forecast by segment"]},
            {"id": "H2", "hypothesis": "Customer adoption and willingness to pay are visible in public evidence.", "decision_relevance": "Determines whether to fund customer validation and sales work.", "needed_evidence": ["customer count", "adoption rate", "orders", "price or ARPU"], "search_queries": [f"{topic} customer adoption price data", f"{topic} orders customer case study"]},
            {"id": "H3", "hypothesis": "Supply, capacity or delivery capability will not bottleneck near-term growth.", "decision_relevance": "Determines whether the growth case needs a supply-side haircut.", "needed_evidence": ["capacity", "projects", "supply chain", "delivery timeline"], "search_queries": [f"{topic} capacity projects supply chain", f"{topic} company announcement capacity"]},
            {"id": "H4", "hypothesis": "Unit economics or the cost curve is close enough to commercial range.", "decision_relevance": "Determines whether the opportunity is a budget item, pilot or watchlist topic.", "needed_evidence": ["cost", "price", "gross margin", "CAPEX", "OPEX"], "search_queries": [f"{topic} cost price margin", f"{topic} CAPEX OPEX economics"]},
            {"id": "H5", "hypothesis": "Leading players have defensible advantage rather than a temporary window.", "decision_relevance": "Determines whether to back a company, partner or capability.", "needed_evidence": ["market share", "patents", "customer references", "funding", "channels"], "search_queries": [f"{topic} competitive landscape market share", f"{topic} leading companies funding patents customers"]},
            {"id": "H6", "hypothesis": "Policy, regulatory or standards timing supports commercialization.", "decision_relevance": "Determines entry timing and risk buffers.", "needed_evidence": ["policy date", "regulatory license", "standard", "subsidy or procurement"], "search_queries": [f"{topic} regulation policy standard", f"{topic} subsidy license timeline"]},
        ]

    def _default_market_sizing_plan(self, topic: str) -> Dict[str, Any]:
        if self.language == "zh":
            return {
                "sizing_question": f"{topic} 的可信市场空间有多大，哪些变量仍需核验？",
                "methods": [
                    {"method": "Top-down sizing", "formula": "总需求池 x 可服务细分占比 x 可触达地域/场景占比", "variables": ["总市场规模", "细分市场占比", "地域或场景可服务比例"], "preferred_sources": ["政府数据", "行业协会", "国际组织", "咨询报告"], "search_queries": [f"{topic} 总市场规模 细分 占比", f"{topic} demand by segment official"], "known_limitations": ["高层市场规模容易包含不可服务需求"]},
                    {"method": "Bottom-up sizing", "formula": "潜在客户数 x 单客户用量 x 单价/ARPU x 采用率", "variables": ["客户数", "单客户用量", "价格或 ARPU", "采用率"], "preferred_sources": ["公司公告", "年报", "行业调查", "监管数据"], "search_queries": [f"{topic} 客户数量 单价 ARPU", f"{topic} adoption rate units price"], "known_limitations": ["客户和价格口径必须一致"]},
                    {"method": "Adoption funnel sizing", "formula": "目标客户池 x 试点率 x 转化率 x 扩张率", "variables": ["目标客户池", "试点数量", "转化率", "扩张率"], "preferred_sources": ["客户案例", "订单公告", "招投标", "渠道数据"], "search_queries": [f"{topic} pilot customer conversion", f"{topic} 招标 订单 客户"], "known_limitations": ["早期案例可能不能代表大规模采用"]},
                    {"method": "Value pool sizing", "formula": "客户成本/收入池 x 可改善比例 x 供应商价值捕获率", "variables": ["客户成本池", "改善比例", "价值捕获率"], "preferred_sources": ["客户财报", "行业成本基准", "案例研究"], "search_queries": [f"{topic} ROI cost savings case study", f"{topic} value pool customer cost"], "known_limitations": ["价值捕获率通常需要一手访谈验证"]},
                    {"method": "Supply-side sizing", "formula": "可用产能 x 利用率 x 单位价格", "variables": ["产能", "利用率", "单位价格"], "preferred_sources": ["产能公告", "项目备案", "公司年报"], "search_queries": [f"{topic} 产能 利用率 单价", f"{topic} project capacity announcement"], "known_limitations": ["公告产能不等于可交付产能"]},
                ],
            }
        return {
            "sizing_question": f"How large is the credible opportunity for {topic}, and which variables still need validation?",
            "methods": [
                {"method": "Top-down sizing", "formula": "Total demand pool x serviceable segment share x reachable geography/use-case share", "variables": ["total market size", "segment share", "reachable geography or use-case share"], "preferred_sources": ["government data", "industry association", "international organization", "consulting report"], "search_queries": [f"{topic} total market size segment share", f"{topic} demand by segment official data"], "known_limitations": ["High-level market size can include demand that is not serviceable."]},
                {"method": "Bottom-up sizing", "formula": "Potential customers x usage per customer x price/ARPU x adoption rate", "variables": ["customer count", "usage per customer", "price or ARPU", "adoption rate"], "preferred_sources": ["company announcements", "annual reports", "industry surveys", "regulatory datasets"], "search_queries": [f"{topic} customer count price ARPU", f"{topic} adoption rate units price"], "known_limitations": ["Customer and price definitions must use the same scope."]},
                {"method": "Adoption funnel sizing", "formula": "Target customer pool x pilot rate x conversion rate x expansion rate", "variables": ["target customer pool", "pilot count", "conversion rate", "expansion rate"], "preferred_sources": ["customer cases", "order announcements", "procurement data", "channel data"], "search_queries": [f"{topic} pilot customer conversion", f"{topic} orders customers adoption"], "known_limitations": ["Early case studies may not represent scaled adoption."]},
                {"method": "Value pool sizing", "formula": "Customer cost/revenue pool x improvement rate x supplier value-capture rate", "variables": ["customer cost pool", "improvement rate", "value-capture rate"], "preferred_sources": ["customer filings", "industry cost benchmarks", "case studies"], "search_queries": [f"{topic} ROI cost savings case study", f"{topic} value pool customer cost"], "known_limitations": ["Value-capture rate usually needs primary research."]},
                {"method": "Supply-side sizing", "formula": "Available capacity x utilization x unit price", "variables": ["capacity", "utilization", "unit price"], "preferred_sources": ["capacity announcements", "project filings", "company annual reports"], "search_queries": [f"{topic} capacity utilization unit price", f"{topic} project capacity announcement"], "known_limitations": ["Announced capacity is not the same as deliverable capacity."]},
            ],
        }

    def _default_validation_data_needs(self, topic: str) -> List[Dict[str, Any]]:
        labels = [
            ("Market size", "Quantify TAM/SAM before writing a growth claim.", [f"{topic} market size forecast data"]),
            ("Demand volume", "Cross-check revenue pools with physical or usage demand.", [f"{topic} demand volume official data"]),
            ("Customer or user count", "Build a bottom-up buyer base.", [f"{topic} customer count users"]),
            ("Adoption or penetration rate", "Estimate realistic conversion from demand to revenue.", [f"{topic} adoption rate penetration"]),
            ("Price, ARPU or ASP", "Turn demand units into revenue.", [f"{topic} price ARPU ASP"]),
            ("Cost, CAPEX or OPEX", "Test whether the opportunity can earn attractive economics.", [f"{topic} cost CAPEX OPEX"]),
            ("Capacity or project pipeline", "Check whether supply can serve demand.", [f"{topic} capacity project pipeline"]),
            ("Funding or investment", "Validate capital formation and investor conviction.", [f"{topic} funding investment by company"]),
            ("Regulatory or policy gate", "Identify non-market blockers to adoption.", [f"{topic} regulation policy approval"]),
            ("Competitive share", "Separate market growth from player-level advantage.", [f"{topic} market share competitive landscape"]),
            ("Case studies or orders", "Verify that adoption exists outside narrative claims.", [f"{topic} customer case order announcement"]),
            ("Milestone timeline", "Anchor the commercialization clock in dated evidence.", [f"{topic} milestone timeline commercialization"]),
        ]
        if self.language == "zh":
            translations = [
                ("市场规模", "量化 TAM/SAM，避免把增长叙事直接写成规模判断。"),
                ("需求量", "用实物量或使用量交叉验证收入池。"),
                ("客户或用户数", "建立 bottom-up 买方基数。"),
                ("采用率或渗透率", "估计需求向收入转化的现实速度。"),
                ("价格、ARPU 或 ASP", "把需求单位转成收入。"),
                ("成本、CAPEX 或 OPEX", "判断机会是否具备经济性。"),
                ("产能或项目管线", "验证供给是否能服务需求。"),
                ("融资或投资", "验证资本形成和投资者信心。"),
                ("监管或政策门槛", "识别非市场采用阻碍。"),
                ("竞争份额", "区分市场增长和单一玩家优势。"),
                ("客户案例或订单", "验证采用是否存在于叙事之外。"),
                ("里程碑时间线", "用有日期的证据锚定商业化节奏。"),
            ]
            labels = [(metric, reason, queries) for (metric, reason), (_old, _old_reason, queries) in zip(translations, labels)]
        return [
            {"id": f"D{idx}", "metric": metric, "decision_use": reason, "preferred_sources": ["official data", "filings", "annual reports", "industry association", "credible research"], "search_queries": queries}
            for idx, (metric, reason, queries) in enumerate(labels, start=1)
        ]

    @staticmethod
    def _dedupe_texts(values: List[Any]) -> List[str]:
        out = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            key = re.sub(r"\W+", "", text.lower())
            if text and key and key not in seen:
                seen.add(key)
                out.append(text)
        return out

    @staticmethod
    def _norm_key(value: str) -> str:
        return re.sub(r"\W+", "", str(value or "").lower())[:160]

    def _fallback_plan(self, topic: str, reason: str) -> Dict[str, Any]:
        if self.language == "zh":
            queries = [f"{topic} 市场 数据", f"{topic} 政策", f"{topic} 公司 年报", f"{topic} 行业协会", f"{topic} 研究报告"]
            outline = ["该议题首先需要回答管理层决策问题", "证据质量决定可以投入多少资源", "价值创造路径需要拆成可验证假设", "下一步行动应围绕证据缺口推进"]
        else:
            queries = [f"{topic} market data", f"{topic} policy", f"{topic} company annual report", f"{topic} industry association", f"{topic} research report"]
            outline = ["The topic needs to be framed as an executive decision", "Evidence quality determines how much commitment is justified", "The value path should be split into testable assumptions", "Next moves should close the evidence gaps first"]
        return {
            "objective": topic,
            "audience": "CEO, board and strategy team" if self.language == "en" else "CEO、董事会和战略团队",
            "decision_question": topic,
            "issue_tree": [],
            "search_queries": queries,
            "source_strategy": "fallback",
            "outline": outline,
            "exhibit_ideas": [],
            "risks": [reason[:300]],
            "_fallback_used": True,
        }

    def _fallback_report(
        self,
        topic: str,
        plan: Dict[str, Any],
        sources: List[SourceDocument],
        fact_pack: ResearchFactPack,
        reason: str,
    ) -> Dict[str, Any]:
        if self.language == "zh":
            takeaways = [
                f"{topic} 需要先被定义为管理层决策问题，而不是资料摘要。",
                "公开证据不足时，最重要的不是写得更满，而是保留证据边界和核验任务。",
                "下一步应围绕客户价值、成本、竞争和执行能力关闭关键证据缺口。",
            ]
            sections = [
                {
                    "title": "证据边界决定管理层可以多快行动",
                    "lead": "如果公开资料无法支持市场规模、份额、成本或 ROI，报告必须把这些内容保留为待核验假设。",
                    "paragraphs": [
                        "当前兜底版本优先保护事实边界。它不会把模型推断改写成确定事实，也不会用泛化图表填充页面。",
                        "管理层真正需要的是知道哪些判断已经有来源支持，哪些仍只是方向性假设。",
                        "后续应优先补充政府、监管、年报、公告、行业协会和客户案例资料。",
                        "网页化输出的优势是可以保留更清楚的来源区、行动模块和证据边界，而不受 PDF 页高限制。",
                        "这条路径更适合做持续迭代的深度报告。每次新增资料后，可以更新事实包、图表和行动建议。",
                    ],
                    "evidence": fact_pack.validation_issues[:4],
                    "so_what": "先关闭证据缺口，再扩大叙事和设计投入。",
                }
            ]
            methodology = f"兜底报告生成原因：{reason[:240]}。"
        else:
            takeaways = [
                f"{topic} should first be framed as an executive decision, not a source summary.",
                "When public evidence is thin, the right move is to preserve the source boundary and validation tasks.",
                "Next work should close evidence gaps around customer value, cost, competition and execution capacity.",
            ]
            sections = [
                {
                    "title": "Evidence boundaries determine how fast leadership can move",
                    "lead": "If public sources do not support market size, share, cost or ROI, the report should keep those points as validation tasks.",
                    "paragraphs": [
                        "This fallback draft protects the factual boundary first. It does not convert model inference into sourced fact or use generic exhibits to fill space.",
                        "The management need is to know which claims are source-backed and which remain directional assumptions.",
                        "Follow-up research should prioritize government, regulator, filing, annual-report, industry-association and customer-case evidence.",
                        "The HTML-first path can expose source boundaries, action modules and evidence gaps more clearly than a fixed-height PDF page.",
                        "That makes it better suited to an iterative deep-research workflow in which every new source can improve the fact pack, exhibits and management agenda.",
                    ],
                    "evidence": fact_pack.validation_issues[:4],
                    "so_what": "Close the evidence gaps before spending more effort on narrative polish or design.",
                }
            ]
            methodology = f"Fallback report generated because synthesis failed: {reason[:240]}."
        return {
            "title": topic,
            "dek": takeaways[0],
            "category": "Deep research" if self.language == "en" else "深度研究",
            "authors": ["GateX Research"],
            "intro": [takeaways[0]],
            "key_takeaways": takeaways,
            "sections": sections,
            "exhibits": [],
            "action_steps": [],
            "methodology": methodology,
            "evidence_quality": " ".join(fact_pack.validation_issues[:3]),
            "references": [{"title": source.title or source.url, "url": source.url, "note": source.snippet} for source in sources[:10]],
            "_fallback_used": True,
        }


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
