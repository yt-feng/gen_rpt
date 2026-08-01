from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from gen_rpt.deepseek_client import normalize_structured_payload
from gen_rpt.main_web import RAGBridgeError, _fetch_rag_context
from gen_rpt.web_fetch import (
    SourceDocument,
    _search_searxng,
    build_rag_manifest,
    merge_sources,
    sources_from_validated_context,
)
from gen_rpt.web_evidence import (
    build_evidence_ledger,
    merge_evidence_exhibits,
    reconcile_rag_web_evidence,
)
from gen_rpt.research_quality import ResearchFactPack
from gen_rpt.web_publication_contract import (
    combined_evidence_quality_issues,
    ground_rag_section_evidence,
    prune_unsupported_numeric_claims,
    rag_exhibit_is_grounded,
    rag_report_quality_issues,
    rag_rendered_output_issues,
    rag_visible_numbers_supported,
)
from gen_rpt.web_report_pipeline import WebReportPipeline
from gen_rpt.web_report_renderer import normalize_web_report, render_web_report_html


def _context_payload():
    return {
        "has_rag_context": True,
        "context_text": "=== DOCUMENT: Fleet plan ===\nThe investment is $45.5 million.",
        "document_count": 1,
        "document_references": [
            {"document_id": "doc-1", "file_name": "fleet-plan.pdf"},
        ],
        "validated_chunks": [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "text": "The investment is $45.5 million and expected demand is 68%.",
                "confidence": 0.91,
                "authority": 0.8,
                "validation_status": "validated",
                "metadata": {"page": 4},
            }
        ],
    }


class RAGBridgeTests(unittest.TestCase):
    def test_validated_chunks_become_traceable_sources(self):
        sources = sources_from_validated_context(_context_payload(), "Fleet launch decision")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].source_type, "internal")
        self.assertEqual(sources[0].confidence, 0.91)
        self.assertEqual(sources[0].metadata["chunk_id"], "chunk-1")
        self.assertEqual(sources[0].metadata["document_id"], "doc-1")
        self.assertIn("fleet-plan.pdf", sources[0].title)
        self.assertEqual(sources[0].url, "internal://documents/doc-1#chunk=chunk-1")

    @patch("requests.get")
    def test_bridge_fetches_internal_endpoint_once_and_preserves_sources(self, mock_get):
        response = Mock()
        response.json.return_value = {"data": _context_payload()}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        package = _fetch_rag_context(
            "report-slug", "https://backend.example", "secret", "Fleet launch decision"
        )

        self.assertIsNotNone(package)
        self.assertEqual(package.document_count, 1)
        self.assertEqual(len(package.sources), 1)
        self.assertIn("[Chunk: chunk-1 | Document: fleet-plan.pdf]", package.context_text)
        mock_get.assert_called_once_with(
            "https://backend.example/api/internal/context/report-slug",
            headers={"Authorization": "Bearer secret"},
            timeout=15,
        )

    @patch("requests.get")
    def test_bridge_surfaces_backend_failure(self, mock_get):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("503 Service Unavailable")
        mock_get.return_value = response

        with self.assertRaisesRegex(RAGBridgeError, "503 Service Unavailable"):
            _fetch_rag_context("report-slug", "https://backend.example", "secret")

    def test_internal_sources_are_kept_ahead_of_public_sources(self):
        internal = sources_from_validated_context(_context_payload(), "Fleet launch decision")
        public = [
            SourceDocument(
                title="Regulator",
                url="https://regulator.example/rule",
                query="rule",
                snippet="Rule summary",
                content="A sufficiently detailed public rule summary.",
            )
        ]

        merged = merge_sources(internal, public)

        self.assertEqual(merged[0].source_type, "internal")
        self.assertEqual(len(merged), 2)

    def test_manifest_preserves_chunk_document_and_evidence_counts(self):
        sources = sources_from_validated_context(_context_payload(), "Fleet launch decision")
        manifest = build_rag_manifest(
            "Private context",
            sources,
            [{"source_url": sources[0].url}],
            required=True,
        )

        self.assertEqual(manifest["status"], "active")
        self.assertEqual(manifest["validated_chunk_count"], 1)
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(manifest["internal_evidence_points"], 1)

    def test_manifest_separates_rag_web_and_conflict_counts(self):
        rag_sources = sources_from_validated_context(_context_payload(), "Fleet launch decision")
        web_sources = [
            SourceDocument(
                title="Regulator",
                url="https://regulator.example/rule",
                query="rule",
                snippet="Rule summary",
                content="The regulator published a sufficiently detailed corridor rule.",
                metadata={"search_provider": "searxng"},
            )
        ]

        manifest = build_rag_manifest(
            "Private context",
            rag_sources,
            [{"origin": "rag"}, {"origin": "web"}],
            required=True,
            public_sources=web_sources,
            conflicts=[{"id": "C1"}],
            web_required=True,
            web_query_count=2,
        )

        self.assertEqual(manifest["rag_source_count"], 1)
        self.assertEqual(manifest["web_source_count"], 1)
        self.assertEqual(manifest["rag_evidence_points"], 1)
        self.assertEqual(manifest["web_evidence_points"], 1)
        self.assertEqual(manifest["conflict_count"], 1)
        self.assertEqual(manifest["web_search_status"], "success")
        self.assertEqual(manifest["web_search_providers"], ["searxng"])

    @patch("gen_rpt.web_fetch.requests.get")
    def test_searxng_search_provider_returns_traceable_results(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "title": "FAA corridor update",
                    "url": "https://faa.example/corridor",
                    "content": "The regulator published an updated corridor requirement.",
                }
            ]
        }
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with patch.dict(os.environ, {"SEARXNG_URL": "https://search.example"}):
            results = _search_searxng("urban drone corridor", max_results=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "searxng")
        self.assertIn("updated corridor", results[0].snippet)
        self.assertEqual(mock_get.call_args.args[0], "https://search.example/search")
        self.assertEqual(mock_get.call_args.kwargs["params"]["format"], "json")

    def test_r2_upload_contract_includes_combined_evidence_artifacts(self):
        tree = ast.parse(Path("storage/upload_report.py").read_text(encoding="utf-8"))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "REPORT_FILES" for target in node.targets)
        )
        uploaded_names = {key.value for key in assignment.value.keys if isinstance(key, ast.Constant)}

        self.assertTrue(
            {
                "rag_manifest.json",
                "evidence_ledger.json",
                "rag_evidence_ledger.json",
                "web_evidence_ledger.json",
                "approved_evidence.json",
                "evidence_conflicts.json",
            }.issubset(uploaded_names)
        )

    def test_evidence_ledger_labels_rag_and_web_origin(self):
        rag_source = SourceDocument(
            title="Fleet plan",
            url="internal://documents/doc-1#chunk=chunk-1",
            query="fleet",
            snippet="",
            content="The documented fleet investment is $45.5 million for the launch program.",
            source_type="internal",
        )
        web_source = SourceDocument(
            title="Regulatory benchmark",
            url="https://regulator.example/benchmark",
            query="benchmark",
            snippet="",
            content="The public regulatory benchmark reports a 68% acceptance threshold for the corridor.",
            source_type="html",
        )
        fact_pack = ResearchFactPack(
            topic="Fleet launch",
            objective="Assess launch",
            decision_question="Launch?",
            source_count=2,
            authoritative_source_count=0,
            source_domains=["internal.enterprise", "regulator.example"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )

        ledger = build_evidence_ledger("Fleet launch", [rag_source, web_source], fact_pack)

        self.assertEqual({item["origin"] for item in ledger}, {"rag", "web"})

    def test_reconciliation_keeps_rag_and_moves_conflicting_web_value_to_review(self):
        ledger = [
            {
                "id": "E1",
                "origin": "rag",
                "fact": "Corridor consumer acceptance in 2025 is 68% for Project SkyNet.",
                "value": 68.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
                "source_title": "Consumer study.pdf",
                "source_url": "internal://documents/doc-1#chunk=chunk-1",
            },
            {
                "id": "E2",
                "origin": "web",
                "fact": "Corridor consumer acceptance in 2025 is 72% for Project SkyNet.",
                "value": 72.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
                "source_title": "External survey",
                "source_url": "https://survey.example/skynet",
            },
        ]

        result = reconcile_rag_web_evidence(ledger)

        self.assertEqual([item["id"] for item in result["approved"]], ["E1"])
        self.assertEqual(result["rag"][0]["status"], "requires_human_review")
        self.assertEqual(result["web"][0]["status"], "requires_human_review")
        self.assertEqual(result["conflicts"][0]["rag"]["value"], 68.0)
        self.assertEqual(result["conflicts"][0]["web"]["value"], 72.0)

    def test_reconciliation_marks_matching_web_value_as_corroboration(self):
        ledger = [
            {
                "id": "E1",
                "origin": "rag",
                "fact": "Project SkyNet investment in 2025 is $45.5 million.",
                "value": 45.5,
                "unit": "$M",
                "year": 2025,
                "metric_family": "funding",
            },
            {
                "id": "E2",
                "origin": "web",
                "fact": "Project SkyNet investment in 2025 is $45.5 million.",
                "value": 45.5,
                "unit": "$M",
                "year": 2025,
                "metric_family": "funding",
            },
        ]

        result = reconcile_rag_web_evidence(ledger)

        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["web"][0]["status"], "corroborates_rag")
        self.assertEqual({item["id"] for item in result["approved"]}, {"E1", "E2"})

    def test_same_unit_unrelated_claims_are_not_misclassified_as_conflicts(self):
        ledger = [
            {
                "id": "E1",
                "origin": "rag",
                "fact": "Project SkyNet consumer acceptance in 2025 is 68%.",
                "value": 68.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
            },
            {
                "id": "E2",
                "origin": "web",
                "fact": "Regional battery recycling efficiency in 2025 is 72%.",
                "value": 72.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
            },
        ]

        result = reconcile_rag_web_evidence(ledger)

        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["web"][0]["status"], "supplementary")

    def test_multi_metric_percentage_sentences_are_not_auto_matched(self):
        ledger = [
            {
                "id": "E1",
                "origin": "rag",
                "fact": "In 2025, Project SkyNet acceptance is 68% and repeat intent is 51%.",
                "value": 68.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
            },
            {
                "id": "E2",
                "origin": "web",
                "fact": "In 2025, Project SkyNet acceptance is 72% and repeat intent is 51%.",
                "value": 72.0,
                "unit": "%",
                "year": 2025,
                "metric_family": "adoption",
            },
        ]

        result = reconcile_rag_web_evidence(ledger)

        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["web"][0]["status"], "supplementary")

    def test_quality_gate_rejects_generic_empty_and_unsupported_report(self):
        report = {
            "title": "Project SkyNet Urban Drone Delivery Launch Decision",
            "key_takeaways": ["The investment is $99 million."],
            "sections": [{"title": f"Section {index}", "paragraphs": [], "evidence": []} for index in range(1, 5)],
        }

        issues = rag_report_quality_issues(
            report,
            topic="Project SkyNet Urban Drone Delivery Launch Decision",
            context_text="The validated investment is $45.5 million.",
            source_count=1,
            source_chunks={"chunk-1": "The validated investment is $45.5 million."},
        )

        self.assertTrue(any("decision conclusion" in issue for issue in issues))
        self.assertTrue(any("conclusion-first" in issue for issue in issues))
        self.assertTrue(any("traceable evidence" in issue for issue in issues))
        self.assertTrue(any("99" in issue for issue in issues))

    def test_quality_gate_accepts_substantive_grounded_report(self):
        areas = ("financial", "consumer", "compliance", "fleet", "operating", "governance")

        def paragraphs(area):
            return [
                f"The {area} evidence establishes a decision baseline rather than a promise of automatic success. Management should distinguish what the documents demonstrate from what still depends on execution, because a sound commitment follows verified operating conditions instead of treating a positive signal as permission to scale without controls, ownership, or review.",
                f"The causal mechanism in the {area} case runs through disciplined sequencing. Evidence supports a conditional move, while the value of that move depends on compliance, readiness, and accountable decision rights working together. If one gate remains unresolved, leadership retains the option to pause without abandoning the broader opportunity or weakening factual discipline.",
                f"Counter-evidence matters because the same {area} signal can be interpreted too aggressively when it is separated from implementation risk. The documents support measured progress, not certainty. A credible report therefore explains both the positive case and the conditions that could delay, narrow, or reverse the recommendation before additional resources become difficult to recover.",
                f"The practical implication for the {area} workstream is to connect every commitment to an observable gate and named owner. That approach converts research into a decision sequence, preserves learning, and prevents a broad strategic narrative from outrunning the evidence that the organization can actually verify and govern during implementation.",
                f"Source discipline keeps the {area} recommendation useful after the initial decision. Teams can revisit the same evidence when conditions change, compare the observed result with the original rationale, and update the commitment without rewriting history. This creates a repeatable management process instead of a one-time narrative that becomes harder to challenge once execution begins.",
            ]

        report = {
            "title": "Project SkyNet Requires a Conditional Launch Before Further Investment",
            "dek": "Validated financial, customer, compliance, and fleet evidence supports a gated decision.",
            "intro": [
                "The decision brief connects validated financial, customer, compliance, fleet, operating, and governance evidence into one conditional recommendation. It separates demonstrated facts from execution assumptions, explains how the major gates interact, and preserves explicit pause conditions so leadership can advance the opportunity without treating a positive signal as proof that every implementation risk has already been resolved."
            ],
            "key_takeaways": [
                "The documented investment is $45.5 million.",
                "Consumer acceptance is 68% in the validated survey.",
                "The launch should remain conditional on documented readiness gates.",
            ],
            "sections": [
                {
                    "title": f"Validated evidence makes the {area} decision conditional",
                    "lead": "The private documents support a decision only when the recorded launch gates are satisfied.",
                    "paragraphs": paragraphs(area),
                    "evidence": [
                        '[Chunk: chunk-1] "The validated investment is $45.5 million and consumer acceptance is 68%." — This supports the conditional decision.',
                        '[Chunk: chunk-2] "Compliance and fleet readiness must be verified before launch approval." — This establishes the operating gate.',
                    ],
                    "so_what": "Leadership should release resources only after the documented gate is met, assign an accountable owner to each unresolved condition, and preserve a pause decision if compliance, operating readiness, or governance evidence weakens before the next commitment.",
                }
                for area in areas
            ],
            "action_steps": [
                {
                    "horizon": "Immediate",
                    "action": f"Confirm the documented {area} decision gate.",
                    "success_metric": "The accountable owner records a pass, pause, or escalate decision.",
                    "rationale": "The private evidence supports conditional progress only when the relevant readiness gate is verified.",
                }
                for area in areas[:4]
            ],
        }

        issues = rag_report_quality_issues(
            report,
            topic="Project SkyNet Urban Drone Delivery Launch Decision",
            context_text="The validated investment is $45.5 million and consumer acceptance is 68%. Compliance and fleet readiness must be verified before launch approval.",
            source_count=3,
            source_chunks={
                "chunk-1": "The validated investment is $45.5 million and consumer acceptance is 68%.",
                "chunk-2": "Compliance and fleet readiness must be verified before launch approval.",
            },
        )

        self.assertEqual(issues, [])

    def test_grounded_model_exhibit_is_preserved_and_unsupported_exhibit_is_rejected(self):
        context = "The validated investment is $45.5 million and consumer acceptance is 68%."
        grounded = {
            "type": "bar",
            "title": "Validated investment and acceptance evidence",
            "values": [45.5, 68],
            "data_basis": [
                {
                    "id": "chunk-1",
                    "fact": "The validated investment is $45.5 million and consumer acceptance is 68%.",
                }
            ],
        }
        unsupported = {"type": "bar", "title": "Invented case", "values": [99]}
        chunks = {"chunk-1": context}

        self.assertTrue(rag_exhibit_is_grounded(grounded, context_text=context, source_chunks=chunks))
        self.assertFalse(rag_exhibit_is_grounded(unsupported, context_text=context, source_chunks=chunks))
        self.assertFalse(rag_visible_numbers_supported(unsupported, context))

        report = {"exhibits": [grounded]}
        merged = merge_evidence_exhibits(
            report,
            [{"type": "timeline", "title": "Documented launch sequence"}],
            preserve_existing=True,
        )
        self.assertEqual(len(merged["exhibits"]), 2)
        self.assertEqual(merged["exhibits"][0]["title"], grounded["title"])

    def test_approved_web_evidence_can_ground_a_supplementary_exhibit(self):
        evidence = {
            "id": "E2",
            "origin": "web",
            "status": "supplementary",
            "fact": "The regulator reports a 72% corridor compliance benchmark.",
        }
        exhibit = {
            "type": "bar",
            "title": "Supplementary compliance benchmark",
            "categories": ["Compliance benchmark"],
            "values": [72],
            "data_basis": [{"id": "E2", "fact": evidence["fact"]}],
        }

        self.assertTrue(
            rag_exhibit_is_grounded(
                exhibit,
                context_text="Private evidence. " + evidence["fact"],
                source_chunks={},
                approved_evidence=[evidence],
            )
        )

    def test_combined_gate_rejects_quarantined_conflict_evidence(self):
        issues = combined_evidence_quality_issues(
            {"exhibits": [{"data_basis": [{"id": "WEB-E2"}]}]},
            approved_evidence=[{"id": "RAG-E1", "origin": "rag"}],
            conflicts=[{"web": {"id": "WEB-E2"}}],
            source_chunks={"chunk-1": "Private fact"},
        )

        self.assertTrue(any("quarantined" in issue for issue in issues))

    def test_rendered_output_gate_detects_legacy_fallback_and_missing_review(self):
        issues = rag_rendered_output_issues(
            "<text>A</text><text>60</text><text>45</text><text>30</text>",
            conflict_count=1,
        )

        self.assertEqual(len(issues), 3)
        self.assertTrue(any("management agenda" in issue for issue in issues))

    @patch("gen_rpt.web_report_pipeline.collect_sources")
    def test_rag_web_collection_is_bounded_without_changing_public_collection(self, mock_collect):
        mock_collect.return_value = []
        queries = [f"query {index}" for index in range(10)]
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = "Private context"

        with patch.dict(os.environ, {"GEN_RPT_RAG_WEB_REQUIRED": "false"}, clear=False):
            pipeline._collect_public_sources(queries, per_query=5, max_sources=32)

        mock_collect.assert_called_once_with(queries[:4], per_query=2, max_sources=8)
        mock_collect.reset_mock()
        pipeline.rag_context = None

        pipeline._collect_public_sources(queries, per_query=5, max_sources=32)

        mock_collect.assert_called_once_with(queries, per_query=5, max_sources=32)

    @patch("gen_rpt.web_report_pipeline.collect_sources", return_value=[])
    def test_required_combined_mode_rejects_silent_pure_rag_fallback(self, _mock_collect):
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = "Private context"

        with patch.dict(os.environ, {"GEN_RPT_RAG_WEB_REQUIRED": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "zero usable sources"):
                pipeline._collect_public_sources(["external corridor benchmark"], per_query=5, max_sources=32)

    def test_rag_search_uses_only_planner_gap_queries(self):
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = "Private context"

        queries = pipeline._expanded_search_queries(
            {"search_queries": ["external regulation gap", "external market benchmark"]},
            [{"search_queries": ["generic chart query"]}],
        )

        self.assertEqual(queries, ["external regulation gap", "external market benchmark"])

    def test_deterministic_exhibits_do_not_repeat_existing_rag_facts(self):
        fact = "The validated investment is $45.5 million."
        report = {
            "exhibits": [
                {
                    "type": "matrix",
                    "title": "Documented investment",
                    "data_basis": [{"id": "chunk-1", "fact": fact}],
                }
            ]
        }

        merged = merge_evidence_exhibits(
            report,
            [
                {
                    "type": "metric_row",
                    "title": "Repeated investment",
                    "data_basis": [{"id": "E1", "fact": fact}],
                },
                {
                    "type": "bar",
                    "title": "Distinct acceptance evidence",
                    "data_basis": [{"id": "E2", "fact": "Consumer acceptance is 68%."}],
                },
            ],
            preserve_existing=True,
        )

        self.assertEqual(
            [item["title"] for item in merged["exhibits"]],
            ["Documented investment", "Distinct acceptance evidence"],
        )

    def test_final_quality_gate_checks_action_and_exhibit_numbers(self):
        paragraph = "Documented evidence supports the decision without adding unsupported operating assumptions. " * 6
        report = {
            "title": "Validated Evidence Requires a Conditional Launch Decision",
            "key_takeaways": ["Evidence is validated.", "The decision is conditional.", "Gates remain documented."],
            "sections": [
                {
                    "title": f"Validated evidence supports decision area {index}",
                    "lead": "The decision follows the uploaded evidence.",
                    "paragraphs": [paragraph, paragraph, paragraph],
                    "evidence": ['[Chunk: chunk-1] "The validated investment is $45.5 million." — Verified.'],
                }
                for index in range(1, 5)
            ],
            "action_steps": [{"horizon": "Within 90 days", "action": "Reassess the launch"}],
            "exhibits": [{"type": "bar", "title": "Unsupported forecast", "values": [99]}],
        }

        issues = rag_report_quality_issues(
            report,
            topic="SkyNet launch decision",
            context_text="The validated investment is $45.5 million.",
            source_count=1,
            source_chunks={"chunk-1": "The validated investment is $45.5 million."},
        )

        self.assertTrue(any("90" in issue and "99" in issue for issue in issues))

    def test_unsupported_narrative_number_is_pruned_without_weakening_gate(self):
        report = {
            "title": "Validated evidence supports a conditional launch",
            "intro": ["Acceptance is 68%. An unsupported derived gap is 27.5%."],
            "action_steps": [
                {"action": "Use the documented decision gate."},
                {"action": "Target an unsupported 27.5% improvement."},
                {
                    "horizon": "Next gate",
                    "action": "Preserve the evidence-based action.",
                    "success_metric": "Reach an unsupported 27.5% improvement.",
                    "rationale": "The documented evidence supports retaining this action while its metric is corrected.",
                },
            ],
        }

        removed = prune_unsupported_numeric_claims(report, "Documented acceptance is 68%.")

        self.assertEqual(removed, ["27.5"])
        self.assertEqual(report["intro"], ["Acceptance is 68%."])
        self.assertEqual(
            report["action_steps"],
            [
                {"action": "Use the documented decision gate."},
                {
                    "horizon": "Next gate",
                    "action": "Preserve the evidence-based action.",
                    "success_metric": "",
                    "rationale": "The documented evidence supports retaining this action while its metric is corrected.",
                },
            ],
        )
        self.assertNotIn("27.5", str(report))

    def test_section_citation_is_repaired_from_best_matching_chunk(self):
        report = {
            "sections": [
                {
                    "title": "Consumer acceptance supports a conditional launch",
                    "lead": "Acceptance is the strongest demand signal.",
                    "paragraphs": ["Consumer survey evidence should govern the launch decision."],
                    "evidence": ['[Chunk: consumer] "This quotation does not occur in the private document." — Invalid.'],
                }
            ]
        }
        chunks = {
            "financial": "The validated investment is $45.5 million.",
            "consumer": "The consumer acceptance survey recorded a 68% positive response for drone delivery.",
        }

        ground_rag_section_evidence(report, chunks)

        self.assertTrue(
            any("The consumer acceptance survey recorded" in item for item in report["sections"][0]["evidence"])
        )

    def test_rag_normalization_does_not_inject_synthetic_fallbacks(self):
        report = normalize_web_report(
            {"title": "Grounded report", "key_takeaways": ["A", "B", "C"], "sections": []},
            topic="Grounded report",
            allow_synthetic_fallbacks=False,
        )

        self.assertEqual(report["sections"], [])
        self.assertEqual(report["exhibits"], [])
        self.assertEqual(report["action_steps"], [])

    def test_rag_post_processing_does_not_add_fact_pack_claims(self):
        report = {
            "sections": [
                {
                    "title": "Validated evidence supports a conditional launch",
                    "paragraphs": ["Grounded paragraph one.", "Grounded paragraph two.", "Grounded paragraph three."],
                    "evidence": ['[Chunk: chunk-1] "The investment is $45.5 million." — Verified.'],
                }
            ]
        }
        fact_pack = ResearchFactPack(
            topic="SkyNet",
            objective="Assess launch",
            decision_question="Launch?",
            source_count=1,
            authoritative_source_count=0,
            source_domains=["internal.enterprise"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=["An unvalidated post-processing claim contains 45."],
            dated_facts=[],
            validation_issues=[],
        )
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = "The investment is $45.5 million."

        pipeline._post_process(report, "SkyNet", [], fact_pack)

        self.assertEqual(
            report["sections"][0]["paragraphs"],
            ["Grounded paragraph one.", "Grounded paragraph two.", "Grounded paragraph three."],
        )
        self.assertNotIn("contains 45", str(report))

    def test_rag_references_reserve_space_for_supplementary_web_sources(self):
        sources = [
            SourceDocument(
                title=f"Private fragment {index}",
                url=f"internal://documents/doc-1#chunk={index}",
                query="SkyNet",
                snippet="Private evidence",
                content="Private evidence",
                source_type="internal",
            )
            for index in range(8)
        ]
        sources.append(
            SourceDocument(
                title="Supplementary regulator",
                url="https://regulator.example/skynet",
                query="SkyNet regulation",
                snippet="Supplementary evidence",
                content="Supplementary evidence",
                source_type="html",
            )
        )
        fact_pack = ResearchFactPack(
            topic="SkyNet",
            objective="Assess launch",
            decision_question="Launch?",
            source_count=8,
            authoritative_source_count=0,
            source_domains=["internal.enterprise"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = "Private evidence"
        report = {"sections": [], "references": []}

        pipeline._post_process(report, "SkyNet", sources, fact_pack)

        self.assertIn("rag", {item["origin"] for item in report["references"]})
        self.assertIn("web", {item["origin"] for item in report["references"]})

    def test_rag_exhibit_labels_do_not_call_private_documents_public_sources(self):
        report = {
            "exhibits": [
                {
                    "title": "Public evidence defines the decision",
                    "caption": "Public sources support the comparison.",
                    "data_basis": [{"id": "chunk-1", "origin": "rag"}],
                }
            ]
        }

        WebReportPipeline._apply_source_aware_exhibit_text(report)

        self.assertIn("private-document evidence", report["exhibits"][0]["title"])
        self.assertIn("private-document sources", report["exhibits"][0]["caption"])
        self.assertNotIn("Public", str(report))

    def test_rag_synthesis_receives_approved_web_facts_not_raw_conflicting_excerpts(self):
        client = Mock()
        client.chat_json.return_value = {"title": "Conditional launch"}
        pipeline = WebReportPipeline(client)
        pipeline.rag_context = "The private acceptance result is 68%."
        source = SourceDocument(
            title="External survey",
            url="https://survey.example/skynet",
            query="survey",
            snippet="",
            content="A conflicting raw source reports 72% acceptance.",
        )
        fact_pack = ResearchFactPack(
            topic="SkyNet",
            objective="Assess launch",
            decision_question="Launch?",
            source_count=1,
            authoritative_source_count=0,
            source_domains=["internal.enterprise"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        approved = [
            {
                "id": "WEB-E1",
                "origin": "web",
                "status": "supplementary",
                "fact": "The regulator requires documented corridor approval before launch.",
            }
        ]

        pipeline._synthesize_web_report(
            "SkyNet launch",
            {},
            [],
            [source],
            fact_pack,
            approved,
            {},
            evidence_conflicts=[{"id": "C1", "web": {"fact": source.content}}],
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("documented corridor approval", prompt)
        self.assertIn("CONFLICT REGISTER", prompt)
        self.assertIn("paragraphs must be a JSON array", prompt)
        self.assertEqual(prompt.count("A conflicting raw source reports 72% acceptance."), 1)

    def test_section_normalization_preserves_body_paragraph_breaks(self):
        report = normalize_structured_payload(
            {
                "sections": [
                    {
                        "title": "Evidence supports a conditional decision",
                        "lead": "The decision remains conditional.",
                        "body": "First developed paragraph.\n\nSecond developed paragraph.\n\nThird developed paragraph.",
                    }
                ]
            }
        )

        self.assertEqual(
            report["sections"][0]["paragraphs"],
            ["First developed paragraph.", "Second developed paragraph.", "Third developed paragraph."],
        )

    def test_report_revision_receives_the_rejected_draft_and_quality_corrections(self):
        client = Mock()
        client.chat_json.return_value = {"title": "Revised report"}
        pipeline = WebReportPipeline(client)
        rejected = {
            "title": "Conditional launch",
            "sections": [
                {
                    "title": "Evidence supports a gate",
                    "paragraphs": ["The rejected draft is too short."],
                    "evidence": ['[Chunk: chunk-1] "The documented gate must be verified before launch." — Governing evidence.'],
                }
            ],
        }

        revised = pipeline._revise_report_draft(
            rejected,
            ["Section 1 needs 3-6 developed analytical paragraphs; found 1."],
            {"selected_modules": ["failure modes and uncertainty"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(revised, {"title": "Revised report"})
        self.assertIn("The rejected draft is too short.", prompt)
        self.assertIn("Section 1 needs 3-6 developed analytical paragraphs", prompt)
        self.assertIn("paragraphs must be a JSON array", prompt)
        self.assertIn("[Chunk: chunk-1]", prompt)

    def test_action_normalization_uses_a_non_numeric_default_horizon(self):
        report = normalize_web_report(
            {
                "title": "Grounded report",
                "key_takeaways": ["A", "B", "C"],
                "sections": [],
                "action_steps": [{"action": f"Action {index}"} for index in range(1, 5)],
            },
            topic="Grounded report",
            allow_synthetic_fallbacks=False,
        )

        self.assertEqual(report["action_steps"][3]["horizon"], "Decision gate")

    def test_editorial_audit_requires_evidence_strategy_and_no_critical_issues(self):
        passing = {
            "score": 84,
            "evidence_and_citations": 21,
            "strategic_usefulness": 22,
            "critical_issues": [],
        }
        failing = {**passing, "critical_issues": ["A scenario probability is unsupported."]}

        self.assertTrue(WebReportPipeline._editorial_audit_passed(passing))
        self.assertFalse(WebReportPipeline._editorial_audit_passed(failing))

    def test_nested_table_numbers_are_inside_the_rag_quality_boundary(self):
        exhibit = {
            "type": "table",
            "title": "Drone weight comparison",
            "data": {
                "columns": ["Drone", "Weight"],
                "rows": [["Swift-Class", 18], ["Invented model", 99]],
            },
        }

        self.assertFalse(rag_visible_numbers_supported(exhibit, "Swift-Class weighs 18 lbs."))

    def test_pipeline_removes_unsupported_nested_table_before_report_gate(self):
        chunk_id = "chunk-1"
        fact = "Swift-Class weighs 18 lbs."
        report = {
            "exhibits": [
                {
                    "type": "table",
                    "title": "Grounded weight",
                    "data": {"columns": ["Drone", "Weight"], "rows": [["Swift-Class", 18]]},
                    "data_basis": [{"id": chunk_id, "fact": fact}],
                },
                {
                    "type": "table",
                    "title": "Unsupported weight",
                    "data": {"columns": ["Drone", "Weight"], "rows": [["Invented model", 99]]},
                    "data_basis": [{"id": chunk_id, "fact": fact}],
                },
            ]
        }
        pipeline = WebReportPipeline(Mock())
        pipeline.rag_context = fact

        pipeline._filter_rag_exhibits(report, {chunk_id: fact})

        self.assertEqual([item["title"] for item in report["exhibits"]], ["Grounded weight"])

    def test_nested_table_normalizes_to_matrix_and_preserves_full_chunk_id(self):
        chunk_id = "dfe35bb5-0eda-4a50-8180-aacb83f79cbd"
        report = normalize_web_report(
            {
                "title": "Grounded report",
                "key_takeaways": ["A", "B", "C"],
                "sections": [],
                "exhibits": [
                    {
                        "type": "table",
                        "title": "Budget breakdown",
                        "data": {
                            "columns": ["Category", "Amount ($M)"],
                            "rows": [["Hardware", 22.0], ["Software", 12.5]],
                        },
                        "data_basis": [{"id": chunk_id, "fact": "Hardware: $22.0M"}],
                    }
                ],
            },
            topic="Grounded report",
            allow_synthetic_fallbacks=False,
        )

        exhibit = report["exhibits"][0]
        self.assertEqual(exhibit["type"], "matrix")
        self.assertEqual(exhibit["rows"], ["Hardware", "Software"])
        self.assertEqual(exhibit["columns"], ["Amount ($M)"])
        self.assertEqual(exhibit["values"], [[22.0], [12.5]])
        self.assertEqual(exhibit["data_basis"][0]["id"], chunk_id)

    def test_strict_renderer_does_not_inject_placeholder_chart_values(self):
        report = {
            "title": "Grounded budget decision",
            "dek": "The uploaded document supplies the budget values.",
            "key_takeaways": ["Budget is documented.", "Values are traceable.", "No fallback is allowed."],
            "sections": [
                {
                    "id": "section-1",
                    "title": "The documented budget defines the decision boundary",
                    "paragraphs": ["Grounded evidence paragraph."],
                }
            ],
            "exhibits": [
                {
                    "type": "table",
                    "title": "Project SkyNet Budget Breakdown",
                    "after_section_id": "section-1",
                    "data": {
                        "columns": ["Category", "Amount ($M)"],
                        "rows": [["Hardware", 22.0], ["Software", 12.5]],
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "index.html"
            render_web_report_html(
                report,
                {},
                output,
                "Grounded budget decision",
                allow_synthetic_fallbacks=False,
            )
            html = output.read_text(encoding="utf-8")

        self.assertIn("Hardware", html)
        self.assertIn("22", html)
        self.assertNotIn(">A</text>", html)
        self.assertNotIn(">60</text>", html)
        self.assertNotIn(">45</text>", html)
        self.assertNotIn(">30</text>", html)

    def test_renderer_exposes_conflicts_and_source_origins_for_human_review(self):
        report = {
            "title": "Project SkyNet remains conditional",
            "dek": "RAG remains the working source of truth.",
            "key_takeaways": ["RAG is primary.", "Web evidence is supplementary.", "Conflicts require review."],
            "sections": [],
            "references": [
                {"title": "Fleet plan.pdf", "url": "internal://documents/doc-1", "origin": "rag"},
                {"title": "External survey", "url": "https://survey.example/skynet", "origin": "web"},
            ],
            "conflicts": [
                {
                    "id": "C1",
                    "status": "requires_human_review",
                    "reason": "Comparable claims report different values.",
                    "rag": {"value": "68%", "fact": "Document acceptance is 68%.", "source_title": "Fleet plan.pdf"},
                    "web": {"value": "72%", "fact": "External acceptance is 72%.", "source_title": "External survey"},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "index.html"
            render_web_report_html(
                report,
                {},
                output,
                "Project SkyNet remains conditional",
                allow_synthetic_fallbacks=False,
            )
            html = output.read_text(encoding="utf-8")

        self.assertIn("Conflicts requiring human review", html)
        self.assertIn("Document acceptance is 68%", html)
        self.assertIn("External acceptance is 72%", html)
        self.assertIn("private-document", html)
        self.assertIn("supplementary web", html)

if __name__ == "__main__":
    unittest.main()
