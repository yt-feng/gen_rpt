import unittest
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.web_publication_contract import (
    validate_takeaway_completeness,
    convert_evidence_to_human_readable,
    rag_rendered_output_issues,
)
from gen_rpt.web_report_pipeline import WebReportPipeline


class TestReportPublicationContract(unittest.TestCase):
    def test_internal_evidence_id_regex(self):
        regex = r"\b(?:WEB-E|RAG-E|E)\d+\b"
        self.assertTrue(re.search(regex, "Based on WEB-E1 summary."))
        self.assertTrue(re.search(regex, "Refer to RAG-E12 for details."))
        self.assertTrue(re.search(regex, "See E5 data point."))
        self.assertFalse(re.search(regex, "Section E or WEB-E or RAG-E without number."))

    def test_validate_takeaway_completeness(self):
        valid = [
            "Demand grew by 15% year-over-year.",
            "Regulatory approval remains a key prerequisite for commercial rollout.",
            "Unit economics improve significantly at scale.",
        ]
        self.assertEqual(validate_takeaway_completeness(valid), [])

        invalid = [
            "Demand grew by 15% year-over-year and",
            "Regulatory approval remains a key prerequisite for.",
            "Unit economics improve significantly at scale",
        ]
        issues = validate_takeaway_completeness(invalid)
        self.assertTrue(any("ends prematurely with connector word 'and'" in msg for msg in issues))
        self.assertTrue(any("ends prematurely with connector word 'for'" in msg for msg in issues))
        self.assertTrue(any("does not end with complete sentence punctuation" in msg for msg in issues))


    def test_convert_evidence_to_human_readable(self):
        report = {
            "sections": [
                {
                    "title": "Market Demand",
                    "paragraphs": ["Paragraph text."],
                    "evidence_internal": [
                        '[Chunk: chunk_abc123] "Sample quote from document"',
                        "WEB-E1",
                    ],
                    "evidence": [
                        '[Chunk: chunk_abc123] "Sample quote from document"',
                        "WEB-E1",
                    ],
                }
            ]
        }
        rag_chunks = {"chunk_abc123": "Sample quote from document"}
        rag_titles = {"chunk_abc123": "Industry_Report_2026.pdf"}
        approved_evidence = [
            {
                "id": "WEB-E1",
                "source_title": "Market Study 2026",
                "fact": "Demand reached 500MW in 2025",
            }
        ]

        humanized = convert_evidence_to_human_readable(
            report,
            rag_chunks,
            rag_titles,
            approved_evidence,
        )

        sec = humanized["sections"][0]
        self.assertEqual(sec["evidence_internal"], [
            '[Chunk: chunk_abc123] "Sample quote from document"',
            "WEB-E1",
        ])
        self.assertEqual(sec["evidence"][0], "Industry_Report_2026.pdf — Sample quote from document")
        self.assertEqual(sec["evidence"][1], "Market Study 2026 — Demand reached 500MW in 2025")

    def test_evaluate_recommendation_stance(self):
        pipeline = WebReportPipeline(client=None)
        plan = {
            "critical_evidence_required": [
                {"id": "BASE-1", "type": "demand_signal", "required_for": "conditional_pilot"},
                {"id": "BASE-4", "type": "financial", "required_for": "invest"},
            ]
        }

        # Case 1: Disproving thesis / conflicts -> do_not_proceed
        disproved_ledger = [{"id": "E1", "status": "disproves_thesis"}]
        self.assertEqual(pipeline._evaluate_recommendation_stance(plan, disproved_ledger), "do_not_proceed")

        conflicts = [{}, {}, {}]
        self.assertEqual(pipeline._evaluate_recommendation_stance(plan, [], conflicts=conflicts), "do_not_proceed")

        # Case 2: Missing demand evidence -> validation_only
        no_demand_ledger = [
            {"id": "E1", "critical_requirement_id": "BASE-4", "relevance_type": "financial", "source_url": "http://a.com"}
        ]
        self.assertEqual(pipeline._evaluate_recommendation_stance(plan, no_demand_ledger), "validation_only")

        # Case 3: Conditional pilot satisfied -> conditional_pilot
        pilot_ledger = [
            {"id": "E1", "critical_requirement_id": "BASE-1", "relevance_type": "demand_signal", "source_url": "http://a.com"}
        ]
        self.assertEqual(pipeline._evaluate_recommendation_stance(plan, pilot_ledger), "conditional_pilot")

        # Case 4: All satisfied + >1 demand source -> invest
        invest_ledger = [
            {"id": "E1", "critical_requirement_id": "BASE-1", "relevance_type": "demand_signal", "source_url": "http://a.com"},
            {"id": "E2", "critical_requirement_id": "BASE-1", "relevance_type": "demand_signal", "source_url": "http://b.com"},
            {"id": "E3", "critical_requirement_id": "BASE-4", "relevance_type": "financial", "source_url": "http://a.com"},
        ]
        self.assertEqual(pipeline._evaluate_recommendation_stance(plan, invest_ledger), "invest")

    def test_rag_rendered_output_issues(self):
        clean_html = """
        <html><body>
            <div class="action-block">Management Agenda</div>
            <p>This report presents findings on market expansion.</p>
        </body></html>
        """
        self.assertEqual(rag_rendered_output_issues(clean_html), [])

        leaky_html = """
        <html><body>
            <div class="action-block">Management Agenda</div>
            <p>Evidence shows [Chunk: chunk_123] 'quote text' and WEB-E1 data.</p>
        </body></html>
        """
        issues = rag_rendered_output_issues(leaky_html)
        self.assertEqual(len(issues), 2)
        self.assertIn("un-humanized raw [Chunk: ...] citations", issues[0])
        self.assertIn("un-humanized internal evidence IDs", issues[1])


if __name__ == "__main__":
    unittest.main()
