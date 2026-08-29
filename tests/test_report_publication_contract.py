import unittest
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.web_publication_contract import (
    validate_takeaway_completeness,
    convert_evidence_to_human_readable,
    output_leak_hits,
    rag_rendered_output_issues,
)
from gen_rpt.web_report_pipeline import WebReportPipeline
from gen_rpt.web_report_renderer import _format_human_evidence_item, render_web_report_html


class TestReportPublicationContract(unittest.TestCase):
    def test_malformed_internal_evidence_is_omitted(self):
        malformed = (
            "{'chunk_id': '9fc0f0c7-3f26-4ef4-a7ac-d5e4018b1f5f', "
            "'excerpt': '城市排水与防内涝', 'why_it_matters': 'This identifies the"
        )
        self.assertEqual(_format_human_evidence_item(malformed, "en"), "")
        self.assertEqual(
            _format_human_evidence_item(
                '[Chunk: internal-42] "城市排水与防内涝" — Supporting document evidence.',
                "en",
            ),
            "",
        )
        report = {
            "sections": [
                {
                    "evidence": [
                        malformed,
                        '[Chunk: internal-42] "城市排水与防内涝" — Supporting document evidence.',
                    ]
                }
            ]
        }
        convert_evidence_to_human_readable(
            report,
            {},
            {"internal-42": "Internal document"},
            [],
            language="en",
        )
        self.assertEqual(report["sections"][0]["evidence"], [])

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


    def test_rag_report_quality_issues_signature(self):
        from gen_rpt.web_publication_contract import rag_report_quality_issues
        report = {
            "title": "Valid Title",
            "dek": "Valid Dek Description",
            "intro": ["Valid intro paragraph with sufficient word count to pass quality checks."],
            "sections": [
                {
                    "title": "Section 1",
                    "lead": "Lead paragraph for section 1 with adequate depth.",
                    "paragraphs": ["Paragraph 1 with detailed discussion.", "Paragraph 2 with context."],
                    "evidence": ["Doc.pdf — Sample quote"],
                }
            ],
            "action_steps": [
                {
                    "horizon": "Near-term",
                    "action": "Proceed with deployment",
                    "success_metric": "Adoption rate",
                    "rationale": "High ROI",
                }
            ],
        }
        issues = rag_report_quality_issues(
            report,
            topic="Test Topic",
            context_text="Grounding text content for testing purposes.",
            source_count=1,
            source_chunks={"chunk1": "Sample quote"},
        )
        self.assertIsInstance(issues, list)


    def test_normalize_report_section_prose_preserves_copy_and_normalizes_actions(self):
        from gen_rpt.web_publication_contract import normalize_report_section_prose, _word_count
        report = {
            "title": "Report Title",
            "sections": [
                {
                    "title": "Section 1",
                    "lead": "Lead text",
                    "paragraphs": ["Short p1", "A longer paragraph with enough analytical depth to form a substantial core for section discussion."],
                    "evidence": ["Doc.pdf — Fact 1"],
                }
            ],
            "action_steps": [
                {
                    "action": "Deploy technology",
                    "description": "Short description",
                    "why_it_matters": "Reason text",
                }
            ],
        }
        normalized = normalize_report_section_prose(report)
        self.assertEqual(
            normalized["sections"][0]["paragraphs"],
            [
                "Short p1",
                "A longer paragraph with enough analytical depth to form a substantial core for section discussion.",
            ],
        )
        for act in normalized["action_steps"]:
            self.assertTrue(bool(act.get("horizon")))
            self.assertTrue(bool(act.get("success_metric")))
            self.assertGreaterEqual(_word_count(act.get("rationale")), 12)

    def test_normalize_report_section_prose_handles_four_short_paragraphs(self):
        from gen_rpt.web_publication_contract import normalize_report_section_prose, report_content_quality_issues
        report = {
            "title": "Global Sovereign AI Infrastructure: National Computing Strategies and Resilience by 2030",
            "dek": "A comprehensive strategic assessment of national compute initiatives and sovereign infrastructure.",
            "intro": ["Introduction paragraph with substantial analysis of global sovereign computing priorities."],
            "key_takeaways": [
                "National governments are establishing dedicated compute reserves to ensure independence.",
                "Supply chain concentration remains the critical vulnerability across advanced packaging.",
                "Capital expenditure requirements necessitate public-private co-investment structures.",
            ],
            "sections": [
                {
                    "title": "National Computing Strategies Reshape Sovereign Compute Investment Priorities",
                    "lead": "Leading economies are committing state-backed capital pools to secure sovereign AI infrastructure capacity and reduce reliance on external hardware providers.",
                    "paragraphs": [
                        "National computing initiatives have accelerated as governments recognize compute capacity as critical infrastructure for public sector administration.",
                        "Direct public subsidies and national sovereign wealth investments are funding domestic data center buildouts across European and Asian markets.",
                        "Regulatory mandates now require domestic hosting for sensitive foundation models and citizen data workloads across strategic economic sectors.",
                        "Public-private partnerships are structuring long-term off-take agreements to guarantee commercial viability for localized sovereign infrastructure.",
                    ],
                    "evidence": ["Doc1.pdf — Sovereign compute initiative", "Doc2.pdf — Public capital allocation"],
                    "so_what": "Enterprise leaders must align local cloud procurement with sovereign data residency and national security mandates across all operating jurisdictions.",
                }
            ] * 5,
            "action_steps": [
                {
                    "horizon": "Immediate (0-90 Days)",
                    "action": "Audit sovereign compute exposure.",
                    "success_metric": "Complete inventory of model compute locations.",
                    "rationale": "Sovereign regulatory requirements demand clear visibility of physical hardware hosting environments across all active deployments.",
                }
            ] * 4,
        }
        normalized = normalize_report_section_prose(report)
        issues = report_content_quality_issues(
            normalized,
            topic="Global Sovereign AI Infrastructure",
            context_text="Sovereign AI infrastructure context",
            source_count=5,
        )
        short_paragraph_issues = [issue for issue in issues if "underdeveloped paragraphs" in issue]
        self.assertEqual(short_paragraph_issues, [])

    def test_rag_first_terminology_and_author_byline_normalization(self):
        from gen_rpt.web_publication_contract import clean_client_text
        self.assertEqual(clean_client_text("A RAG-first market investment report"), "An evidence-led market investment report")
        self.assertEqual(clean_client_text("Report prepared by Evidence Synthesis Unit"), "Report prepared by Human Reviewer")

        report = {
            "title": "A RAG-first investment thesis",
            "dek": "A RAG-first market assessment",
            "authors": ["Evidence Synthesis Unit", "RAG-First Analyst"],
            "sections": [
                {
                    "title": "Market Scope",
                    "evidence": ["Doc.pdf — Sample fact"],
                }
            ],
        }
        cleaned = convert_evidence_to_human_readable(report, {}, {}, [])
        self.assertEqual(cleaned["authors"], ["Human Reviewer", "Human Reviewer"])
        self.assertEqual(cleaned["dek"], "An evidence-led market assessment")

        legitimate = {"authors": [{"name": "Aisha Rahman", "role": "Human reviewer"}], "sections": []}
        convert_evidence_to_human_readable(legitimate, {}, {}, [])
        self.assertEqual(legitimate["authors"][0]["name"], "Aisha Rahman")

    def test_raw_dict_and_json_evidence_sanitization(self):
        report = {
            "sections": [
                {
                    "title": "Flood Resilience Evidence",
                    "evidence": [
                        "{'chunk_id': '9fc0f0c7-3f26-4ef4-a7ac-d5e4018b1f5f', 'excerpt': '101 casualties recorded in Guangxi', 'why_it_matters': 'Establishes flood severity'}",
                        {"chunk_id": "abc12345", "excerpt": "Infrastructure investments expanded", "why_it_matters": "Shows capital allocation"},
                    ],
                }
            ]
        }
        cleaned = convert_evidence_to_human_readable(report, {}, {}, [])
        evidence_items = cleaned["sections"][0]["evidence"]
        self.assertEqual(len(evidence_items), 2)
        for item in evidence_items:
            self.assertNotIn("{", item)
            self.assertNotIn("chunk_id", item)
            self.assertNotIn("why_it_matters':", item)
            self.assertTrue("Establishes flood severity" in item or "Shows capital allocation" in item)

    def test_english_html_sanitizes_internal_metadata_and_preserves_report_content(self):
        report = {
            "title": "A RAG-first UAE property outlook",
            "dek": "A RAG-first market investment report for decision makers.",
            "authors": ["Evidence Synthesis Unit"],
            "key_takeaways": [
                "Prime residential demand remains resilient.",
                "Supply timing will determine near-term pricing power.",
                "Investors should stage commitments against delivery milestones.",
            ],
            "sections": [
                {
                    "id": "section-1",
                    "title": "Demand and supply",
                    "paragraphs": ["Normal report prose remains visible to the reader."],
                    "evidence": [
                        {
                            "chunk_id": "chunk-internal-42",
                            "source_title": "Municipal flood review",
                            "excerpt": "洪水造成了严重基础设施损失。",
                            "why_it_matters": "Infrastructure resilience affects underwriting assumptions.",
                            "retrieval_score": 0.97,
                        }
                    ],
                    "so_what": "Use resilience standards in asset screening.",
                }
            ],
            "action_steps": [
                {
                    "horizon": "0-90 days",
                    "action": "Validate project delivery schedules.",
                    "success_metric": "Independent milestones are confirmed.",
                    "rationale": "Delivery timing drives the near-term risk profile.",
                }
            ],
            "references": [{"title": "Municipal flood review", "url": "https://example.com/source"}],
        }
        cleaned = convert_evidence_to_human_readable(report, {}, {}, [], language="en")
        with TemporaryDirectory() as directory:
            path = render_web_report_html(
                cleaned,
                {},
                Path(directory) / "report.html",
                "UAE property outlook",
                "en",
                allow_synthetic_fallbacks=False,
            )
            html = path.read_text(encoding="utf-8")

        self.assertEqual(output_leak_hits(html), [])
        self.assertEqual(rag_rendered_output_issues(html), [])
        self.assertIn("Normal report prose remains visible to the reader.", html)
        self.assertIn("Municipal flood review", html)
        self.assertIn("Infrastructure resilience affects underwriting assumptions.", html)
        self.assertIn("Prepared by: Human Reviewer", html)
        self.assertNotIn("洪水造成了严重基础设施损失", html)

    def test_number_tokens_does_not_scale_on_following_words(self):
        from gen_rpt.web_publication_contract import _number_tokens
        text = "From 2028 to 2032 market trajectory. Expected $100B by 2030."
        tokens = _number_tokens(text)
        self.assertIn("2028", tokens)
        self.assertIn("2032", tokens)
        self.assertIn("2030", tokens)
        self.assertIn("100000000000", tokens)
        self.assertNotIn("2028000000000000", tokens)
        self.assertNotIn("2032000000", tokens)

    def test_convert_evidence_supplements_sparse_sections(self):
        report = {
            "sections": [
                {
                    "title": "Advanced Semiconductor Packaging Constraints",
                    "lead": "CoWoS packaging capacity limits AI accelerator production through 2027.",
                    "paragraphs": ["Capacity bottlenecks constrain supply.", "High-bandwidth memory yields remain limited."],
                    "evidence": ["WEB-E1"],
                }
            ]
        }
        approved = [
            {"id": "WEB-E1", "source_title": "TSMC Quarterly Report", "fact": "CoWoS packaging capacity will expand by 100% by 2026."},
            {"id": "WEB-E2", "source_title": "TrendForce Analysis", "fact": "HBM market demand will exceed supply by 20% in 2026."},
        ]
        convert_evidence_to_human_readable(
            report,
            {},
            {},
            approved,
            language="en",
        )
        self.assertGreaterEqual(len(report["sections"][0]["evidence"]), 2)
        self.assertIn("TSMC Quarterly Report — CoWoS packaging capacity will expand by 100% by 2026.", report["sections"][0]["evidence"])
        self.assertIn("TrendForce Analysis — HBM market demand will exceed supply by 20% in 2026.", report["sections"][0]["evidence"])

    def test_three_balanced_paragraphs_never_creates_paragraphs_under_35_words(self):
        from gen_rpt.web_publication_contract import _three_balanced_paragraphs, _word_count
        paragraphs = [
            "CoWoS packaging yields remain constrained by die placement complexity and thermal dissipation challenges across high-density interposers.",
            "Substrate suppliers are expanding capacity, but qualification cycles require twelve to eighteen months.",
            "Equipment lead times for advanced bonders remain elevated.",
        ]
        result = _three_balanced_paragraphs(paragraphs)
        if result:
            for p in result:
                self.assertGreaterEqual(_word_count(p), 35)


if __name__ == "__main__":
    unittest.main()
