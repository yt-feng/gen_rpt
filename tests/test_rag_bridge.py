from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from gen_rpt.main_web import RAGBridgeError, _fetch_rag_context
from gen_rpt.web_fetch import (
    SourceDocument,
    build_rag_manifest,
    merge_sources,
    sources_from_validated_context,
)
from gen_rpt.web_evidence import merge_evidence_exhibits
from gen_rpt.research_quality import ResearchFactPack
from gen_rpt.web_publication_contract import (
    ground_rag_section_evidence,
    rag_exhibit_is_grounded,
    rag_report_quality_issues,
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
        self.assertTrue(any("no traceable" in issue for issue in issues))
        self.assertTrue(any("99" in issue for issue in issues))

    def test_quality_gate_accepts_substantive_grounded_report(self):
        paragraph = (
            "The validated documents support the launch assessment with an investment of $45.5 million "
            "and a 68% consumer acceptance signal. The management implication is to condition the launch "
            "on the documented compliance and fleet-readiness gates before committing additional capital."
        )
        report = {
            "title": "Project SkyNet Requires a Conditional Launch Before Further Investment",
            "dek": "Validated financial, customer, compliance, and fleet evidence supports a gated decision.",
            "key_takeaways": [
                "The documented investment is $45.5 million.",
                "Consumer acceptance is 68% in the validated survey.",
                "The launch should remain conditional on documented readiness gates.",
            ],
            "sections": [
                {
                    "title": f"Validated evidence makes the {area} decision conditional",
                    "lead": "The private documents support a decision only when the recorded launch gates are satisfied.",
                    "paragraphs": [paragraph, paragraph, paragraph],
                    "evidence": [
                        '[Chunk: chunk-1] "The validated investment is $45.5 million and consumer acceptance is 68%." — This supports the conditional decision.'
                    ],
                    "so_what": "Do not release additional capital before the documented gate is met.",
                }
                for area in ("financial", "consumer", "compliance", "fleet")
            ],
        }

        issues = rag_report_quality_issues(
            report,
            topic="Project SkyNet Urban Drone Delivery Launch Decision",
            context_text="The validated investment is $45.5 million and consumer acceptance is 68%.",
            source_count=3,
            source_chunks={
                "chunk-1": "The validated investment is $45.5 million and consumer acceptance is 68%."
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

    def test_section_citation_is_repaired_from_best_matching_chunk(self):
        report = {
            "sections": [
                {
                    "title": "Consumer acceptance supports a conditional launch",
                    "lead": "Acceptance is the strongest demand signal.",
                    "paragraphs": ["Consumer survey evidence should govern the launch decision."],
                    "evidence": ["The survey supports demand."],
                }
            ]
        }
        chunks = {
            "financial": "The validated investment is $45.5 million.",
            "consumer": "The consumer acceptance survey recorded a 68% positive response for drone delivery.",
        }

        ground_rag_section_evidence(report, chunks)

        self.assertTrue(any(item.startswith("[Chunk: consumer]") for item in report["sections"][0]["evidence"]))

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

if __name__ == "__main__":
    unittest.main()
