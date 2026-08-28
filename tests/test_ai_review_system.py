import json
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "report-management-backend"))

from shared.report_schema import ParsedReport, Section
from review_system.extractors.section_parser import parse_report
from review_system.extractors.claim_extractor import extract_claims
from review_system.scoring import (
    score_research, score_evidence, score_strategic, score_writing,
)
from review_system.reviewers.review_orchestrator import run_pipeline
from review_system.reviewers.review_builder import assemble
from review_system.outputs.json_writer import write_all_json
from review_system.outputs.markdown_writer import write_markdown
from review_system.outputs.html_writer import write_html
from app.services.generation import _build_mock_report_entry


SAMPLE_REPORT_MD = """# Global Sovereign AI Infrastructure & National Computing Strategies 2030

## Executive Summary
Governments worldwide are accelerating sovereign compute initiatives, investing over $140 billion into state-backed semiconductor and datacenter infrastructure. National strategies prioritize energy availability, localized model training, and supply chain autonomy.

## Strategic Imperatives for Sovereign Compute
National computing strategies require coordinated investments across power, cooling, and advanced packaging facilities:
- Grid connectivity and clean energy co-location reduce operational costs by up to 25%.
- Sovereign clusters with over 320 specialized tensor processing nodes ensure national independence in foundation model development.
- Strategic partnerships mitigate single-supplier risk in advanced lithography.

## Action Steps for Policymakers
1. Establish national semiconductor testbeds within 12 months.
2. Synchronize sovereign wealth fund allocations with long-term utility capacity.
3. Formulate international talent mobility frameworks for specialized chip designers.

## References
- [Global AI Semiconductor Market Outlook 2026-2030](https://example.com/ai-semi-report)
- [National Computing Strategy Whitepaper](https://example.com/computing-whitepaper)
"""


class TestAIReviewSystem(unittest.TestCase):
    """End-to-end mock test suite for the AI Review System."""

    def test_01_section_parser(self):
        """Verify report parser extracts sections, word count, and title."""
        parsed = parse_report(SAMPLE_REPORT_MD, title="Global Sovereign AI Infrastructure")
        self.assertIsInstance(parsed, ParsedReport)
        self.assertEqual(parsed.title, "Global Sovereign AI Infrastructure")
        self.assertGreater(len(parsed.sections), 2)
        self.assertGreater(parsed.total_words, 50)
        headings = [s.title for s in parsed.sections]
        self.assertIn("Executive Summary", headings)
        self.assertIn("References", headings)

    def test_02_scoring_rubrics_and_caps(self):
        """Verify rubric calculation and scoring caps for all 4 dimensions."""
        parsed = parse_report(SAMPLE_REPORT_MD, title="Global Sovereign AI Infrastructure")
        mock_engine = MagicMock()

        claims_audit = {
            "total_claims": 10,
            "supported_count": 8,
            "partially_supported_count": 2,
            "unsupported_count": 0,
            "high_risk_count": 0,
            "quantification_ratio": 0.6,
        }

        full_scores = {
            "research_quality": {"score": 25, "what_works": ["Strong datasets"], "what_fails": []},
            "evidence_and_citations": {"score": 22, "what_works": ["High attribution"], "what_fails": []},
            "strategic_clarity": {"score": 23, "what_works": ["Clear recommendations"], "what_fails": []},
            "writing_and_structure": {"score": 18, "what_works": ["Pyramid structure"], "what_fails": []},
        }

        # 1. Research Quality (max 30)
        rq = score_research(mock_engine, parsed, claims_audit, full_scores)
        self.assertEqual(rq["score"], 25)
        self.assertEqual(rq["max_points"], 30)

        # 2. Evidence & Citations (max 25)
        ec = score_evidence(mock_engine, parsed, claims_audit, full_scores)
        self.assertEqual(ec["score"], 22)
        self.assertEqual(ec["max_points"], 25)

        # 3. Strategic Clarity (max 25)
        sc = score_strategic(mock_engine, parsed, claims_audit, full_scores)
        self.assertEqual(sc["score"], 23)
        self.assertEqual(sc["max_points"], 25)

        # 4. Writing & Structure (max 20)
        ws = score_writing(
            mock_engine, parsed, claims_audit, full_scores,
            writing_findings={"writing_flaws": [{"severity": "High"}]}
        )
        self.assertGreaterEqual(ws["score"], 15)
        self.assertLessEqual(ws["score"], 20)

    def test_03_mock_pipeline_execution_and_assembly(self):
        """Run full review orchestrator pipeline with a mock engine."""
        parsed = parse_report(SAMPLE_REPORT_MD, title="Global Sovereign AI Infrastructure")

        mock_engine = MagicMock()
        
        # Mock claim extraction response
        mock_claims_resp = {
            "claims": [
                {
                    "claim": "Governments investing over $140 billion into state-backed semiconductor infrastructure.",
                    "claim_type": "Quantitative",
                    "classification": "supported",
                    "confidence": 0.9,
                    "location_ref": "Executive Summary",
                    "source_ref": "https://example.com/ai-semi-report",
                    "risk_level": "Low",
                },
                {
                    "claim": "Sovereign clusters with over 320 specialized nodes ensure national independence.",
                    "claim_type": "Quantitative",
                    "classification": "supported",
                    "confidence": 0.88,
                    "location_ref": "Strategic Imperatives",
                    "source_ref": "https://example.com/computing-whitepaper",
                    "risk_level": "Low",
                }
            ],
            "total_claims": 2,
            "supported_count": 2,
            "partially_supported_count": 0,
            "unsupported_count": 0,
            "high_risk_count": 0,
            "speculative_count": 0,
            "quantification_ratio": 1.0,
        }

        # Mock scoring response
        mock_scoring_resp = {
            "research_quality": {"score": 25, "what_works": ["Deep analysis"], "what_fails": []},
            "evidence_and_citations": {"score": 22, "what_works": ["Strong backing"], "what_fails": []},
            "strategic_clarity": {"score": 23, "what_works": ["Clear execution"], "what_fails": []},
            "writing_and_structure": {"score": 18, "what_works": ["Well structured"], "what_fails": []},
        }

        # Mock combined analysis response (lean mode flat schema)
        mock_combined_resp = {
            "strengths": [{"finding": "Solid roadmap", "location_ref": "Location -> [Exec Summary]", "severity": "Low"}],
            "weaknesses": [{"finding": "Supply chain concentration", "location_ref": "Location -> [Strategic]", "severity": "Medium"}],
            "data_gaps": [],
            "weak_assumptions": [],
            "citation_strengths": [{"finding": "Strong whitepaper citations", "location_ref": "Location -> [References]", "severity": "Low"}],
            "citation_weaknesses": [],
            "has_bibliography": True,
            "named_sources_count": 2,
            "writing_flaws": [],
            "narrative_gaps": [],
            "overall_narrative_coherence": "Strong",
            "strategic_gaps": [],
            "has_explicit_recommendations": True,
            "has_risk_opportunity_split": True,
            "audience_relevance_gaps": [],
            "minister_ready": True,
            "board_ready": True,
            "swf_ready": True,
            "minister_reason": "Clear sovereign policy implications.",
            "board_reason": "Quantified capital requirements.",
            "swf_reason": "High-conviction infrastructure investment focus.",
            "flagged_sections": [],
        }

        # Mock synthesis recommendations
        mock_synth_resp = {
            "strengths": [
                {"finding": "Solid multi-layered sovereign infrastructure roadmap.", "severity": "Positive"},
                {"finding": "Strong empirical basis with quantified cluster metrics.", "severity": "Positive"},
            ],
            "weaknesses": [
                {"finding": "Expand regulatory scenario modeling for advanced lithography exports.", "severity": "Medium"}
            ],
            "improvement_tasks": [
                {
                    "priority": "High",
                    "issue": "Add sensitivity analysis on geopolitical export restrictions.",
                    "fix": "Include a contingency matrix for alternative packaging suppliers.",
                }
            ],
            "executive_communication": {
                "minister_ready": True,
                "minister_reason": "Clear sovereign policy implications.",
                "board_ready": True,
                "board_reason": "Quantified capital requirements.",
                "swf_ready": True,
                "swf_reason": "High-conviction infrastructure investment focus.",
            }
        }

        def mock_chat_json(messages, **kwargs):
            sys_msg = str(messages[0]["content"] if messages else "")
            if "CLAIM EXTRACTION" in sys_msg or "extract" in sys_msg.lower():
                return mock_claims_resp
            if "RUBRIC SCORING" in sys_msg or "score" in sys_msg.lower():
                return mock_scoring_resp
            if "COMBINED REPORT ANALYSIS" in sys_msg or "analyzer" in sys_msg.lower():
                return mock_combined_resp
            if "REVIEW SYNTHESIS" in sys_msg or "synthesis" in sys_msg.lower() or "recommendation" in sys_msg.lower():
                return mock_synth_resp
            return mock_combined_resp

        mock_engine.chat_json.side_effect = mock_chat_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)

            # Step 1: Run Orchestrator Pipeline
            pipeline_results = run_pipeline(mock_engine, parsed, out_dir, lean_mode=True)
            self.assertIn("scores", pipeline_results)
            self.assertIn("claims_audit", pipeline_results)
            self.assertEqual(pipeline_results["scores"]["overall_score"], 88)
            self.assertEqual(pipeline_results["scores"]["grade"], "Silver")

            # Step 2: Assemble Final ReviewData
            review_data = assemble(
                mock_engine,
                pipeline_results,
                report_title=parsed.title,
                report_path="reports_web/test/report.md",
            )
            self.assertIsInstance(review_data, dict)
            self.assertEqual(review_data["scores"]["overall_score"], 88)
            self.assertEqual(review_data["scores"]["grade"], "Silver")
            self.assertEqual(len(review_data["claims_audit"]["claims"]), 2)

            # Step 3: Write Output Artifacts
            write_all_json(out_dir, review_data)
            write_markdown(out_dir, review_data)
            write_html(out_dir, review_data)

            # Step 4: Validate Artifacts on Disk
            for filename in [
                "review.json", "scores.json", "claims.json",
                "findings.json", "audit_manifest.json", "review.md", "review.html"
            ]:
                fpath = out_dir / filename
                self.assertTrue(fpath.exists(), f"Missing expected artifact: {filename}")
                self.assertGreater(fpath.stat().st_size, 0, f"Artifact is empty: {filename}")

            # Step 5: Validate Backend Hydration Contract
            saved_review = json.loads((out_dir / "review.json").read_text(encoding="utf-8"))
            backend_entry = _build_mock_report_entry(
                doc_str_id="test-doc-id",
                title="Global Sovereign AI Infrastructure",
                slug="test-sovereign-ai",
                payload={"ai_review_data": saved_review, "sections": [{"heading": "Exec Summary", "body": "Text"}]},
            )
            self.assertEqual(backend_entry["aiScore"], 88)
            self.assertEqual(backend_entry["aiGrade"], "Silver")
            ai_rev = backend_entry["aiReview"]
            self.assertIsNotNone(ai_rev)
            self.assertEqual(ai_rev["scores"]["overall_score"], 88)
            self.assertEqual(ai_rev["scores"]["grade"], "Silver")
            self.assertTrue(ai_rev["recommendations"]["executive_readiness"]["board_members"])
            self.assertTrue(ai_rev["recommendations"]["executive_readiness"]["ministers"])
            self.assertEqual(len(ai_rev["claims_audit"]["claims"]), 2)


if __name__ == "__main__":
    unittest.main()
