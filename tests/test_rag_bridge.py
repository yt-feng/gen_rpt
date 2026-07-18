from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from gen_rpt.main_web import RAGBridgeError, _fetch_rag_context
from gen_rpt.web_fetch import (
    SourceDocument,
    build_rag_manifest,
    merge_sources,
    sources_from_validated_context,
)
from gen_rpt.web_publication_contract import rag_report_quality_issues


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

if __name__ == "__main__":
    unittest.main()
