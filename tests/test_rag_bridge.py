from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from gen_rpt.deepseek_client import normalize_structured_payload
from gen_rpt.main_web import RAGBridgeError, _fetch_rag_context
from gen_rpt.web_fetch import (
    SearchResult,
    SourceDocument,
    _search_searxng,
    build_rag_manifest,
    merge_sources,
    search_web,
    sources_from_validated_context,
)
from gen_rpt.web_evidence import (
    build_evidence_ledger,
    build_source_channel_qualitative_evidence,
    build_verified_private_seed_evidence,
    merge_evidence_exhibits,
    merge_source_channel_public_evidence,
    reconcile_rag_web_evidence,
)
from gen_rpt.research_quality import ResearchFactPack
from gen_rpt.web_publication_contract import (
    backfill_section_evidence_from_ledger,
    combined_evidence_quality_issues,
    compress_report_to_word_budget,
    ground_rag_section_evidence,
    normalize_report_section_prose,
    prune_unsupported_numeric_claims,
    rag_exhibit_is_grounded,
    rag_report_quality_issues,
    rag_rendered_output_issues,
    rag_visible_numbers_supported,
    source_channel_report_quality_issues,
    _report_narrative_text,
    _word_count,
)
from gen_rpt.web_report_pipeline import (
    ReportQualityError,
    WebReportPipeline,
    _freeze_source_channel_bound_narrative,
    _source_channel_has_bound_narrative_token,
    _source_channel_length_budget,
)
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


def _source_channel_words(seed: str, target: int, filler: str) -> str:
    words = seed.split()
    while len(words) < target:
        words.append(filler)
    return " ".join(words[:target])


def _source_channel_quality_report():
    section_names = ["demand", "supply", "policy", "adoption", "execution"]
    paragraph_names = ["evidence", "mechanism", "boundary"]
    sections = []
    for section_name in section_names:
        paragraphs = []
        for paragraph_name in paragraph_names:
            paragraphs.append(_source_channel_words(
                f"The {section_name} {paragraph_name} evidence connects the retained public record "
                "to a clear operating mechanism and preserves the counterpoint, the unresolved "
                "constraint, and the decision boundary that an accountable executive owner must "
                "review before committing additional organisational capacity",
                60,
                f"{section_name}{paragraph_name}context",
            ))
        sections.append(
            {
                "title": f"Validated {section_name} evidence supports a bounded operating decision",
                "lead": _source_channel_words(
                    f"The retained {section_name} record supports a conclusion-first decision while "
                    "keeping the remaining uncertainty visible. Independent corroboration determines "
                    "how quickly management can move and which commitment must remain conditional",
                    30,
                    f"{section_name}leadcontext",
                ),
                "paragraphs": paragraphs,
                "evidence": [
                    _source_channel_words(
                        f"The retained {section_name} public record supports the bounded conclusion and preserves the operating condition for independent review through OpenAlex https://openalex.org/W1234567890",
                        45,
                        f"{section_name}publicsource",
                    ),
                    _source_channel_words(
                        f"Independent {section_name} research corroborates the causal mechanism while keeping the unresolved limitation visible through the retained DOI source https://doi.org/10.1234/example.5678",
                        45,
                        f"{section_name}researchsource",
                    ),
                ],
                "so_what": _source_channel_words(
                    "Management should assign an accountable owner, document the unresolved condition, "
                    "and preserve a clear pause gate until independent evidence confirms that the "
                    "operating mechanism remains valid under the identified constraint. The next review "
                    "must record the evidence accepted, the counterpoint tested, and the resulting response",
                    40,
                    f"{section_name}implication",
                ),
            }
        )
    return {
        "title": "Verified public evidence supports a bounded market response",
        "dek": "Independent corroboration narrows the decision without overstating certainty.",
        "intro": [
            _source_channel_words(
                "The brief separates the supported conclusion from the conditions that still require management verification and keeps independent corroboration visible for accountable operating decisions",
                50,
                "introcontext",
            )
        ],
        "key_takeaways": [
            _source_channel_words("Independent evidence supports a conditional operating response", 25, "firsttakeaway"),
            _source_channel_words("The causal mechanism remains more important than narrative momentum", 25, "secondtakeaway"),
            _source_channel_words("Management ownership and a documented pause gate preserve decision quality", 25, "thirdtakeaway"),
        ],
        "sections": sections,
        "action_steps": [
            {
                "horizon": "Decision gate",
                "action": f"Assign the {section_name} evidence owner",
                "success_metric": "Documented acceptance or pause decision",
                "rationale": _source_channel_words(
                    "The retained evidence supports action only after an accountable owner confirms "
                    "the operating condition and records the decision boundary",
                    18,
                    f"{section_name}actionbasis",
                ),
            }
            for section_name in section_names[:4]
        ],
        "methodology": _source_channel_words("The brief uses retained public sources and independent corroboration", 25, "methodcontext"),
        "evidence_quality": _source_channel_words("The public evidence is corroborated but the response remains conditional", 20, "qualitycontext"),
        "disclaimer": _source_channel_words("This editorial market research does not provide personalised advice", 15, "disclaimercontext"),
        "references": [
            {"title": "OpenAlex", "url": "https://openalex.org/W1234567890"},
            {"title": "DOI", "url": "https://doi.org/10.1234/example.5678"},
        ],
    }


def _source_channel_attempt12_shape_report():
    """Mirror the field lengths that survived attempt12's shared quality gate."""
    report = _source_channel_quality_report()
    paragraph_counts = [
        [60, 44, 60],
        [60, 60, 66],
        [44, 44, 47],
        [47, 74, 60],
        [60, 46, 46],
    ]
    lead_counts = [13, 20, 22, 18, 19]
    implication_counts = [87, 65, 83, 51, 56]
    evidence_counts = [
        [60, 51],
        [48, 60],
        [60, 51],
        [60, 60],
        [60, 51],
    ]
    for section_index, section in enumerate(report["sections"]):
        section["lead"] = _source_channel_words(
            section["lead"],
            lead_counts[section_index],
            f"attempt12lead{section_index}",
        )
        section["paragraphs"] = [
            _source_channel_words(
                paragraph,
                paragraph_counts[section_index][paragraph_index],
                f"attempt12paragraph{section_index}{paragraph_index}",
            )
            for paragraph_index, paragraph in enumerate(section["paragraphs"])
        ]
        section["so_what"] = _source_channel_words(
            section["so_what"],
            implication_counts[section_index],
            f"attempt12implication{section_index}",
        )
        section["evidence"] = [
            _source_channel_words(
                evidence,
                evidence_counts[section_index][evidence_index],
                f"attempt12evidence{section_index}{evidence_index}",
            )
            for evidence_index, evidence in enumerate(section["evidence"])
        ]
    return report


def _source_channel_attempt13_shape_report():
    """Mirror attempt13's valid 2,532-word reader-visible output shape."""
    report = _source_channel_attempt12_shape_report()
    target_words = 2_532
    current_words = _word_count(_report_narrative_text(report))
    evidence_items = [
        (section_index, evidence_index)
        for section_index, section in enumerate(report["sections"])
        for evidence_index, _item in enumerate(section["evidence"])
    ]
    base_addition, remainder = divmod(
        target_words - current_words,
        len(evidence_items),
    )
    for item_index, (section_index, evidence_index) in enumerate(evidence_items):
        evidence = report["sections"][section_index]["evidence"][evidence_index]
        addition = base_addition + (1 if item_index < remainder else 0)
        report["sections"][section_index]["evidence"][evidence_index] = _source_channel_words(
            evidence,
            len(evidence.split()) + addition,
            f"attempt13groundedcontext{section_index}{evidence_index}",
        )
    assert _word_count(_report_narrative_text(report)) == target_words
    return report


def _source_channel_target_overage(target_words: int = 2_601):
    assert 2_601 <= target_words <= 3_400
    report = _source_channel_quality_report()
    current = _word_count(_report_narrative_text(report))
    report["intro"][0] += " " + " ".join(
        ["targetceilingcontext"] * (target_words - current)
    )
    assert _word_count(_report_narrative_text(report)) == target_words
    assert source_channel_report_quality_issues(
        report,
        topic="Bounded market response",
        context_text="The validated public record supports a conditional operating response.",
        source_count=2,
    ) == [
        "The source-channel reader-visible publication ceiling is "
        f"2,600 words; found {target_words}."
    ]
    return report


class RAGBridgeTests(unittest.TestCase):
    def _run_source_channel_build_until_quality_failure(
        self,
        *,
        synthesized_report,
        revised_reports,
        audit_results,
        expected_message,
        client_response=None,
        post_process_side_effect=None,
        expected_exception=ReportQualityError,
    ):
        public_sources = [
            SourceDocument(
                title="OECD corroboration",
                url="https://oecd.org/W1234567890",
                query="bounded response evidence",
                snippet="Independent corroboration supports the bounded response.",
                content="The public record supports a conditional operating response.",
                domain="oecd.org",
            ),
            SourceDocument(
                title="University corroboration",
                url="https://governance.example.edu/10.1234/example.5678",
                query="bounded response mechanism",
                snippet="Independent research corroborates the operating mechanism.",
                content="The unresolved limitation remains visible in the public record.",
                domain="governance.example.edu",
            ),
        ]
        seed_text = "A verified source thesis requires independent corroboration before management use."
        private_seed = SourceDocument(
            title="Verified source thesis",
            url="https://example.com/private-source",
            query="seed",
            snippet="Verified source thesis.",
            content=seed_text,
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "content_hash": "a" * 64,
                "source_id": "source-1",
            },
        )
        fact_pack = ResearchFactPack(
            topic="Bounded market response",
            objective="Assess the independently corroborated operating mechanism",
            decision_question="What remains supported?",
            source_count=2,
            authoritative_source_count=2,
            source_domains=["oecd.org", "governance.example.edu"],
            source_refs=[source.url for source in public_sources],
            high_confidence_facts=[source.content for source in public_sources],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        web_evidence = [
            {
                "id": f"WEB-E{index}",
                "fact": (
                    "The public record supports a conditional operating response."
                    if index % 2
                    else "The unresolved limitation remains visible in the public record."
                ) + f" Evidence checkpoint {index}.",
                "value": index,
                "year": None,
                "source_url": public_sources[index % 2].url,
                "domain": public_sources[index % 2].domain,
                "source_type": "html",
                "origin": "web",
                "authoritative": True,
                "score": 10,
                "status": "approved",
            }
            for index in range(1, 11)
        ]
        private_evidence = [{
            "id": "PRIVATE-E1",
            "fact": seed_text,
            "source_url": private_seed.url,
            "domain": private_seed.domain,
            "status": "approved",
        }]
        source_profile = {
            "mode": "source_channel",
            "anchors": ["verified source thesis"],
            "sources": [],
        }

        client = Mock()
        if client_response is not None:
            client.chat_json.return_value = copy.deepcopy(client_response)
        pipeline = WebReportPipeline(client)
        revision = patch.object(
            pipeline,
            "_revise_report_draft",
            side_effect=[copy.deepcopy(item) for item in revised_reports],
        )
        audit = patch.object(
            pipeline,
            "_audit_report_content",
            side_effect=[copy.deepcopy(item) for item in audit_results],
        )
        post_process = (
            patch.object(
                pipeline,
                "_post_process",
                side_effect=post_process_side_effect,
            )
            if post_process_side_effect is not None
            else patch.object(
                pipeline,
                "_post_process",
                wraps=pipeline._post_process,
            )
        )
        compression = patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            pipeline,
            "_plan_research",
            return_value={
                "search_queries": ["bounded response evidence"],
                "outline": ["Evidence", "Mechanism", "Boundary"],
            },
        ), patch.object(
            pipeline,
            "_plan_chart_data_needs",
            return_value=[],
        ), patch.object(
            pipeline,
            "_collect_public_sources",
            return_value=public_sources,
        ), patch.object(
            pipeline,
            "_synthesize_web_report",
            return_value=copy.deepcopy(synthesized_report),
        ), patch(
            "gen_rpt.web_report_pipeline.build_research_fact_pack",
            return_value=fact_pack,
        ), patch(
            "gen_rpt.web_report_pipeline.build_evidence_ledger",
            autospec=True,
            return_value=web_evidence,
        ), patch(
            "gen_rpt.web_report_pipeline.build_verified_private_seed_evidence",
            return_value=private_evidence,
        ), patch(
            "gen_rpt.web_report_pipeline.build_storyline_plan",
            return_value={"selected_modules": ["mechanism", "boundary"]},
        ), revision as revision_mock, audit as audit_mock, post_process as post_process_mock, compression as compression_mock:
            with self.assertRaisesRegex(expected_exception, expected_message):
                pipeline.build_report(
                    "Bounded market response",
                    Path(directory),
                    seed_sources=[private_seed],
                    source_profile=source_profile,
                )

        return {
            "client": client,
            "revision_mock": revision_mock,
            "audit_mock": audit_mock,
            "post_process_mock": post_process_mock,
            "compression_mock": compression_mock,
        }

    def test_build_report_initial_revision_path_fails_closed_without_compaction(self):
        overage = _source_channel_target_overage()

        result = self._run_source_channel_build_until_quality_failure(
            synthesized_report=overage,
            revised_reports=[overage, overage, overage, overage, overage],
            audit_results=[],
            expected_message="Report content quality gate failed",
        )

        self.assertEqual(result["revision_mock"].call_count, 1)
        result["audit_mock"].assert_not_called()
        result["post_process_mock"].assert_not_called()
        result["compression_mock"].assert_not_called()
        result["client"].chat_json.assert_called_once()

    def test_build_report_routes_2952_2900_2882_to_atomic_compliance(self):
        class AtomicConvergenceReached(ReportQualityError):
            pass

        captured = {}

        def stop_after_compliance(report, *_args, **_kwargs):
            captured["report"] = copy.deepcopy(report)
            raise AtomicConvergenceReached("atomic report reached downstream")

        result = self._run_source_channel_build_until_quality_failure(
            synthesized_report=_source_channel_target_overage(2_952),
            revised_reports=[
                _source_channel_target_overage(2_900),
                _source_channel_target_overage(2_882),
            ],
            audit_results=[],
            expected_message="atomic report reached downstream",
            client_response={
                "field_revisions": [{
                    "path": "intro.0",
                    "text": _source_channel_quality_report()["intro"][0],
                }],
            },
            post_process_side_effect=stop_after_compliance,
            expected_exception=AtomicConvergenceReached,
        )

        revision_inputs = [
            _word_count(_report_narrative_text(call.args[0]))
            for call in result["revision_mock"].call_args_list
        ]
        self.assertEqual(revision_inputs, [2_952, 2_900])
        self.assertEqual(result["revision_mock"].call_count, 2)
        result["client"].chat_json.assert_called_once()
        atomic_prompt = result["client"].chat_json.call_args.args[0][1]["content"]
        self.assertIn("Current reader-visible total: 2882", atomic_prompt)
        result["post_process_mock"].assert_called_once()
        result["audit_mock"].assert_not_called()
        self.assertEqual(
            _word_count(_report_narrative_text(captured["report"])),
            2_106,
        )

    def test_source_channel_length_convergence_repairs_attempt15_shape_without_deletion(self):
        rejected = _source_channel_target_overage(2_765)
        converged = _source_channel_quality_report()
        under_minimum = copy.deepcopy(converged)
        under_minimum["intro"][0] = " ".join(
            under_minimum["intro"][0].split()[:-20]
        )
        self.assertEqual(
            _word_count(_report_narrative_text(under_minimum)),
            2_086,
        )
        self.assertEqual(
            source_channel_report_quality_issues(
                under_minimum,
                topic="Bounded market response",
                context_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
            ),
            [
                "The source-channel reader-visible publication minimum is "
                "2,100 words; found 2086."
            ],
        )
        protected_evidence = [
            copy.deepcopy(section["evidence"])
            for section in rejected["sections"]
        ]
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline._revise_report_draft = Mock(
            side_effect=[under_minimum, converged]
        )
        issues = source_channel_report_quality_issues(
            rejected,
            topic="Bounded market response",
            context_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
        )

        with patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            revised, remaining = pipeline._converge_source_channel_length(
                rejected,
                issues,
                storyline_plan={"selected_modules": ["mechanism", "boundary"]},
                topic="Bounded market response",
                grounding_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertEqual(remaining, [])
        self.assertEqual(_word_count(_report_narrative_text(revised)), 2_106)
        self.assertEqual(
            [section["evidence"] for section in revised["sections"]],
            protected_evidence,
        )
        self.assertEqual(pipeline._revise_report_draft.call_count, 2)
        compression.assert_not_called()

    def test_source_channel_atomic_revision_repairs_production_shaped_2907_no_progress(self):
        rejected = _source_channel_target_overage(2_907)
        concise_intro = _source_channel_quality_report()["intro"][0]
        original_evidence = [
            copy.deepcopy(section["evidence"])
            for section in rejected["sections"]
        ]
        client = Mock()
        client.chat_json.return_value = {
            "field_revisions": [
                {
                    "path": "private://model-returned-path-must-not-be-logged",
                    "text": "Untrusted model output.",
                },
                {
                    "path": "sections.0.evidence.0",
                    "text": "A model cannot replace protected evidence.",
                },
                {"path": "intro.0", "text": concise_intro},
            ]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline._revise_report_draft = Mock(return_value=copy.deepcopy(rejected))
        issues = source_channel_report_quality_issues(
            rejected,
            topic="Bounded market response",
            context_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
        )

        with patch.object(
            pipeline,
            "_log",
        ) as log, patch.object(
            pipeline,
            "_prepare_report_draft",
            wraps=pipeline._prepare_report_draft,
        ) as prepare:
            revised, remaining = pipeline._converge_source_channel_length(
                rejected,
                issues,
                storyline_plan={"selected_modules": ["mechanism", "boundary"]},
                topic="Bounded market response",
                grounding_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        final_words = _word_count(_report_narrative_text(revised))
        self.assertEqual(remaining, [])
        self.assertLessEqual(final_words, 2_300)
        self.assertGreaterEqual(final_words, 2_100)
        self.assertEqual(
            [section["evidence"] for section in revised["sections"]],
            original_evidence,
        )
        pipeline._revise_report_draft.assert_called_once()
        client.chat_json.assert_called_once()
        self.assertEqual(prepare.call_count, 2)
        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("Eligible complete fields and hard budgets", prompt)
        self.assertIn('"path": "intro.0"', prompt)
        self.assertNotIn('"path": "sections.0.evidence.0"', prompt)
        log_text = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertNotIn("private://model-returned-path-must-not-be-logged", log_text)

    def test_source_channel_atomic_budget_quantifies_every_production_shaped_field(self):
        budget = _source_channel_length_budget(
            _source_channel_target_overage(2_907)
        )
        fields = {entry["path"]: entry for entry in budget["fields"]}

        self.assertEqual(budget["total_words"], 2_907)
        self.assertEqual(budget["protected_words"], 495)
        self.assertEqual(budget["fixed_words"], 651)
        self.assertEqual(budget["mutable_words"], 2_256)
        self.assertEqual(budget["minimum_feasible_words"], 1_757)
        self.assertEqual(budget["target_words"], 2_200)
        self.assertEqual(sum(entry["words"] for entry in fields.values()), 2_907)
        self.assertEqual(fields["intro.0"]["words"], 851)
        self.assertEqual(fields["intro.0"]["target_max_words"], 340)
        self.assertTrue(fields["intro.0"]["mutable"])
        self.assertEqual(
            fields["sections.0.evidence.0"]["protected_reason"],
            "evidence",
        )
        self.assertFalse(fields["sections.0.evidence.0"]["mutable"])
        for section_index in range(5):
            section_analysis_floor = sum(
                entry["min_words"]
                for entry in fields.values()
                if entry["section_index"] == section_index
                and entry["kind"]
                in {"section_lead", "section_paragraphs", "section_so_what"}
            )
            self.assertGreaterEqual(section_analysis_floor, 200)

    def test_source_channel_atomic_rejects_prepare_that_rebalances_target_paragraph(self):
        def paragraph(label, count):
            return " ".join([f"{label}word"] * count) + "."

        report = {
            "title": "Bounded conclusion",
            "dek": "Concise decision context",
            "intro": [paragraph("intro", 2_400)],
            "key_takeaways": [],
            "sections": [{
                "title": "Operating mechanism",
                "lead": paragraph("lead", 30),
                "paragraphs": [
                    paragraph("first", 40),
                    paragraph("second", 60),
                    paragraph("third", 60),
                    paragraph("fourth", 80),
                ],
                "evidence": [],
                "so_what": paragraph("implication", 40),
            }],
            "action_steps": [],
            "methodology": "",
            "evidence_quality": "",
            "disclaimer": "",
        }
        total_words = _word_count(_report_narrative_text(report))
        issues = [
            "The source-channel reader-visible publication ceiling is "
            f"2,600 words; found {total_words}."
        ]
        replacement = paragraph("concise", 60)
        client = Mock()
        client.chat_json.return_value = {
            "field_revisions": [{
                "path": "sections.0.paragraphs.3",
                "text": replacement,
            }]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        with patch.object(pipeline, "_log") as log, patch.object(
            pipeline,
            "_prepare_report_draft",
            wraps=pipeline._prepare_report_draft,
        ) as prepare:
            revised, remaining = pipeline._revise_source_channel_fields(
                report,
                issues,
                storyline_plan={},
                topic="Bounded conclusion",
                grounding_text="",
                source_count=1,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertEqual(revised, report)
        self.assertEqual(remaining, issues)
        prepare.assert_called_once()
        client.chat_json.assert_called_once()
        log_text = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn("reason=target_field_changed_by_prepare", log_text)

    def test_source_channel_atomic_rejects_prepare_that_changes_another_reader_field(self):
        report = _source_channel_target_overage(2_907)
        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
        )
        replacement = _source_channel_quality_report()["intro"][0]
        client = Mock()
        client.chat_json.return_value = {
            "field_revisions": [{"path": "intro.0", "text": replacement}]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        def mutate_non_target(candidate, **_kwargs):
            candidate["dek"] = "Prepare unexpectedly changed another reader field."
            return candidate, []

        with patch.object(pipeline, "_log") as log, patch.object(
            pipeline,
            "_prepare_report_draft",
            side_effect=mutate_non_target,
        ) as prepare:
            revised, remaining = pipeline._revise_source_channel_fields(
                report,
                issues,
                storyline_plan={},
                topic="Bounded market response",
                grounding_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertEqual(revised, report)
        self.assertEqual(remaining, issues)
        prepare.assert_called_once()
        log_text = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn(
            "reason=non_target_reader_field_changed_by_prepare",
            log_text,
        )

    def test_source_channel_attribution_labels_are_bound_with_unicode_boundaries(self):
        protected_labels = [
            "OpenAlex",
            "openalex",
            "OPENALEX",
            "ＯｐｅｎＡｌｅｘ",
            "DOI",
            "ＤＯＩ",
            "sSrN",
            "来源OpenAlex显示",
            "(DOI)",
        ]
        for label in protected_labels:
            with self.subTest(label=label):
                original = f"The retained {label} attribution supports the conclusion."
                proposed = "The shortened field omits the original attribution."
                self.assertTrue(_source_channel_has_bound_narrative_token(original))
                self.assertEqual(
                    _freeze_source_channel_bound_narrative(original, proposed),
                    original,
                )

        for safe_text in ("OpenAlexical", "preDOIpost", "ssrnish"):
            with self.subTest(safe_text=safe_text):
                self.assertFalse(
                    _source_channel_has_bound_narrative_token(safe_text)
                )

    def test_source_channel_atomic_rejects_bare_attribution_label_injection(self):
        report = _source_channel_target_overage(2_907)
        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
        )
        client = Mock()
        client.chat_json.return_value = {
            "field_revisions": [
                {
                    "path": "intro.0",
                    "text": _source_channel_words(
                        "A bare openalex attribution must not enter safe prose",
                        60,
                        "safecontext",
                    ),
                },
                {
                    "path": "intro.0",
                    "text": _source_channel_words(
                        "A bare ＤＯＩ attribution must not enter safe prose",
                        60,
                        "safecontext",
                    ),
                },
                {
                    "path": "intro.0",
                    "text": _source_channel_words(
                        "A bare sSrN attribution must not enter safe prose",
                        60,
                        "safecontext",
                    ),
                },
            ]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        with patch.object(pipeline, "_log") as log:
            revised, remaining = pipeline._revise_source_channel_fields(
                report,
                issues,
                storyline_plan={},
                topic="Bounded market response",
                grounding_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertEqual(revised, report)
        self.assertEqual(remaining, issues)
        client.chat_json.assert_called_once()
        log_text = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertEqual(log_text.count("reason=introduced_protected_token"), 3)

    def test_source_channel_atomic_revision_fails_closed_when_protected_subtotal_is_infeasible(self):
        report = {
            "intro": [
                "2025 " + " ".join(["protectedcontext"] * 2_650)
            ]
        }
        client = Mock()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        with self.assertRaisesRegex(
            ReportQualityError,
            "protected and required field subtotal is 2651 words",
        ):
            pipeline._revise_source_channel_fields(
                report,
                [
                    "The source-channel reader-visible publication ceiling is "
                    "2,600 words; found 2651."
                ],
                storyline_plan={},
                topic="Protected source report",
                grounding_text="",
                source_count=1,
                source_chunks={},
                approved_evidence=[],
            )

        client.chat_json.assert_not_called()

    def test_build_report_editorial_revision_path_fails_closed_without_compaction(self):
        valid_report = _source_channel_quality_report()
        overage = _source_channel_target_overage()
        failing_audit = {
            "score": 75,
            "thesis_and_logic": 19,
            "evidence_and_citations": 20,
            "uncertainty_and_scenarios": 18,
            "strategic_usefulness": 18,
            "critical_issues": ["The operating boundary needs a tighter synthesis."],
            "revision_instructions": ["Tighten the operating boundary."],
        }

        result = self._run_source_channel_build_until_quality_failure(
            synthesized_report=valid_report,
            revised_reports=[overage],
            audit_results=[failing_audit],
            expected_message="Editorial revision failed the content gate",
        )

        self.assertEqual(result["revision_mock"].call_count, 1)
        self.assertEqual(result["audit_mock"].call_count, 1)
        self.assertEqual(result["post_process_mock"].call_count, 1)
        result["compression_mock"].assert_not_called()
        result["client"].chat_json.assert_not_called()
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

    def test_source_channel_planning_consumes_verified_body_anchors(self):
        client = Mock()
        client.chat_json.return_value = {
            "search_queries": [
                "generic asset allocation statistics",
                "Asterion Robotics counter-positioning evidence site:oecd.org",
            ],
            "outline": ["Counter-positioning changes the decision process"],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {
            "mode": "source_channel",
            "anchors": ["Asterion Robotics", "counter-positioning"],
            "sources": [{
                "title": "Asterion decision note",
                "publisher": "Example publisher",
                "approvedExcerpt": "A bounded editorial summary.",
                "anchors": ["Asterion Robotics", "counter-positioning"],
            }],
        }

        raw_plan = pipeline._plan_research("Source-linked decision scan")
        planner_prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("Asterion Robotics", planner_prompt)
        self.assertNotIn("SECRET FULL SOURCE BODY", planner_prompt)

        plan = pipeline._normalize_research_plan(
            raw_plan,
            "Source-linked decision scan",
        )
        self.assertFalse(
            any("generic asset allocation" in query for query in plan["search_queries"])
        )
        self.assertEqual(plan["market_sizing_plan"]["methods"], [])
        self.assertEqual(plan["critical_evidence_required"], [])
        self.assertTrue(
            all(
                "asterionrobotics" in re.sub(r"\W+", "", query.lower())
                or "counterpositioning" in re.sub(r"\W+", "", query.lower())
                for query in plan["search_queries"]
            )
        )
        expanded = pipeline._expanded_search_queries(
            plan,
            [{"search_queries": ["generic chart market sizing"]}],
        )
        self.assertNotIn("generic chart market sizing", expanded)
        self.assertTrue(any(query.startswith("OECD ") for query in expanded))

        report = {"intro": ["The source thesis needs a decision boundary."]}
        pipeline._enforce_stance_intro_sentence(
            report,
            "validation_only",
            "Source-linked decision scan",
        )
        self.assertIn("management guidance", report["intro"][0])
        self.assertNotIn("capital", report["intro"][0].lower())

        client.chat_json.reset_mock()
        client.chat_json.return_value = {
            "chart_data_needs": [{
                "title": "Independent source comparison",
                "chart_type": "matrix",
                "search_queries": [
                    "Asterion Robotics counter-positioning evidence"
                ],
            }]
        }
        pipeline._plan_chart_data_needs(
            "Source-linked decision scan",
            plan,
        )
        chart_prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("Do not add market sizing", chart_prompt)

    def test_generic_planning_still_expands_chart_and_framework_queries(self):
        pipeline = WebReportPipeline(Mock())
        expanded = pipeline._expanded_search_queries(
            {"search_queries": ["topic primary query"]},
            [{"search_queries": ["topic chart query"]}],
        )

        self.assertIn("topic primary query", expanded)
        self.assertIn("topic chart query", expanded)

    @patch("gen_rpt.web_report_pipeline.collect_openalex_sources")
    @patch("gen_rpt.web_report_pipeline.collect_sources")
    def test_source_channel_collection_adds_openalex_without_changing_generic_path(
        self,
        collect,
        openalex,
    ):
        web_source = SourceDocument(
            title="Official guidance",
            url="https://oecd.org/guidance",
            query="Asterion Robotics evidence",
            snippet="Guidance",
            content="Official guidance with enough source text for collection.",
            domain="oecd.org",
        )
        academic_source = SourceDocument(
            title="Peer-reviewed study",
            url="https://doi.org/10.1000/example",
            query="Asterion Robotics evidence",
            snippet="Study",
            content="Peer-reviewed abstract with enough source text for collection.",
            source_type="academic",
            domain="doi.org",
        )
        collect.return_value = [web_source]
        openalex.return_value = [academic_source]
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {
            "mode": "source_channel",
            "anchors": ["Asterion Robotics"],
        }

        sources = pipeline._collect_public_sources(
            ["Asterion Robotics evidence"],
            per_query=3,
            max_sources=8,
            topic="Decision discipline",
        )

        self.assertEqual(sources, [web_source, academic_source])
        openalex.assert_called_once_with(
            "Asterion Robotics evidence",
            ["Asterion Robotics evidence"],
        )

    def test_verified_private_seed_is_traceable_but_not_public_authority(self):
        private_source = SourceDocument(
            title="Private source title",
            url="https://example.com/private-source",
            query="GateX editorial seed",
            snippet="Counter-positioning is the editor-approved source thesis.",
            content="SECRET BODY " * 80,
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "source_id": "source-1",
                "content_hash": "a" * 64,
                "max_quote_characters": 180,
                "reuse_policy": "original_summary_only",
            },
        )

        evidence = build_verified_private_seed_evidence(
            "Decision discipline",
            [private_source],
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["origin"], "private_seed")
        self.assertFalse(evidence[0]["authoritative"])
        self.assertFalse(evidence[0]["public_authority"])
        self.assertEqual(evidence[0]["status"], "primary_verified")
        self.assertNotIn("SECRET BODY", evidence[0]["fact"])

        private_source.metadata.pop("content_hash")
        self.assertEqual(
            build_verified_private_seed_evidence(
                "Decision discipline",
                [private_source],
            ),
            [],
        )

    def test_source_channel_builds_traceable_qualitative_public_evidence(self):
        sources = [
            SourceDocument(
                title="OECD decision guidance",
                url="https://oecd.org/guidance",
                query="consensus decision discipline",
                snippet=(
                    "Consensus decision discipline improves when management teams "
                    "record dissent before committing operating resources."
                ),
                content="",
                domain="oecd.org",
            ),
            SourceDocument(
                title="University governance study",
                url="https://governance.example.edu/study",
                query="consensus decision discipline",
                snippet=(
                    "Decision discipline depends on independent challenge and a "
                    "documented pause gate for management teams."
                ),
                content="",
                domain="governance.example.edu",
            ),
            SourceDocument(
                title="治理研究",
                url="https://policy.example.cn/research",
                query="共识 决策纪律",
                snippet="治理团队应当保留不同意见，并在形成共识之后再次检查决策纪律是否有效。",
                content="",
                domain="policy.example.cn",
            ),
        ]

        evidence = build_source_channel_qualitative_evidence(
            "共识与 consensus decision discipline",
            sources,
        )

        self.assertEqual(len(evidence), 3)
        self.assertEqual([item["id"] for item in evidence], ["WEB-E1", "WEB-E2", "WEB-E3"])
        self.assertEqual({item["origin"] for item in evidence}, {"web"})
        self.assertEqual(
            {item["metric_family"] for item in evidence},
            {"qualitative_corroboration"},
        )
        self.assertTrue(all(item["value"] is None and item["year"] is None for item in evidence))
        self.assertEqual(
            {item["source_url"] for item in evidence},
            {source.url for source in sources},
        )
        self.assertTrue(evidence[0]["authoritative"])
        self.assertTrue(evidence[1]["authoritative"])
        self.assertFalse(evidence[2]["authoritative"])
        pipeline = WebReportPipeline(Mock())
        self.assertEqual(pipeline._public_authority_domain_count(evidence), 2)

    def test_source_channel_qualitative_lane_rejects_untraceable_inputs_and_duplicates(self):
        accepted_fact = (
            "Consensus discipline requires a documented challenge before management "
            "teams commit operating resources."
        )
        sources = [
            SourceDocument(
                title="Accepted",
                url="https://example.com/accepted",
                query="consensus discipline",
                snippet=accepted_fact,
                content="",
                domain="oecd.org",
            ),
            SourceDocument(
                title="Same URL",
                url="https://example.com/accepted",
                query="consensus discipline",
                snippet="Consensus discipline also requires an accountable decision owner.",
                content="",
            ),
            SourceDocument(
                title="Repeated sentence",
                url="https://second.example/repeated",
                query="consensus discipline",
                snippet=accepted_fact,
                content="",
            ),
            SourceDocument(
                title="Insecure",
                url="http://third.example/insecure",
                query="consensus discipline",
                snippet="Consensus discipline improves when teams challenge assumptions openly.",
                content="",
            ),
            SourceDocument(
                title="Private seed",
                url="https://private.example/seed",
                query="consensus discipline",
                snippet="Approved private thesis.",
                content="SECRET PRIVATE BODY consensus discipline " * 30,
                source_type="pdf",
                metadata={"gatex_private_content": "true"},
            ),
            SourceDocument(
                title="Navigation",
                url="https://fourth.example/navigation",
                query="consensus discipline",
                snippet="Subscribe to the newsletter for consensus discipline updates and click here.",
                content="",
            ),
            SourceDocument(
                title="Unrelated",
                url="https://fifth.example/unrelated",
                query="consensus discipline",
                snippet="Marine habitats benefit when coastal restoration protects native species.",
                content="",
            ),
        ]

        evidence = build_source_channel_qualitative_evidence(
            "consensus discipline",
            sources,
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["source_url"], "https://example.com/accepted")
        self.assertNotIn("SECRET PRIVATE BODY", repr(evidence))
        self.assertFalse(evidence[0]["authoritative"])

    def test_source_channel_qualitative_requires_two_substantive_anchors_and_bound_authority(self):
        sources = [
            SourceDocument(
                title="Unrelated Chinese advice",
                url="https://restaurant.example.cn/advice",
                query="共识 顺向 决策",
                snippet="餐饮企业应该改善门店卫生并培训员工，从而为顾客提供更加稳定可靠的服务。",
                content="",
            ),
            SourceDocument(
                title="Unrelated English review",
                url="https://restaurant.example/review",
                query="consensus decision discipline",
                snippet=(
                    "Restaurant reviewers reached consensus about the seasonal menu "
                    "after tasting several dishes with local ingredients."
                ),
                content="",
            ),
            SourceDocument(
                title="Generic Chinese query suffix",
                url="https://operations.example.cn/process",
                query="共识对 管理决策 研究 官方 指引",
                snippet="企业管理团队需要建立清晰决策流程，并持续提高执行效率和组织沟通质量。",
                content="",
            ),
            SourceDocument(
                title="Generic English query suffix",
                url="https://restaurant.example/menu-process",
                query="consensus management decision guidance",
                snippet=(
                    "Restaurant management uses decision guidance to coordinate "
                    "seasonal menu updates and staff communication."
                ),
                content="",
            ),
            SourceDocument(
                title="Hostname-bound authority",
                url="https://oecd.org.evil.example/research",
                query="consensus decision discipline",
                snippet=(
                    "Consensus decision discipline requires independent challenge "
                    "before management commits operating resources."
                ),
                content="",
                domain="oecd.org",
            ),
            SourceDocument(
                title="IR label is not authority",
                url="https://ir.evil.example/research",
                query="consensus decision discipline",
                snippet=(
                    "Consensus decision discipline preserves independent challenge "
                    "before management commits operating resources."
                ),
                content="",
            ),
            SourceDocument(
                title="Embedded gov label is not authority",
                url="https://agency.gov.com/research",
                query="consensus decision discipline",
                snippet=(
                    "Consensus decision discipline retains independent review "
                    "before management commits operating resources."
                ),
                content="",
            ),
            SourceDocument(
                title="PDF type alone is not authority",
                url="https://publisher.example/paper.pdf",
                query="consensus decision discipline",
                snippet=(
                    "Consensus decision discipline records independent objections "
                    "before management commits operating resources."
                ),
                content="",
                source_type="pdf",
            ),
        ]

        evidence = build_source_channel_qualitative_evidence(
            "共识对，也并不等于应该做顺向 consensus decision discipline",
            sources,
        )

        self.assertEqual(len(evidence), 4)
        self.assertEqual(
            {item["domain"] for item in evidence},
            {
                "oecd.org.evil.example",
                "ir.evil.example",
                "agency.gov.com",
                "publisher.example",
            },
        )
        self.assertFalse(any(item["authoritative"] for item in evidence))
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {"mode": "source_channel", "anchors": ["consensus"]}
        authority_count = pipeline._public_authority_domain_count(evidence)
        self.assertEqual(authority_count, 0)
        issues = pipeline._evidence_base_issues(
            authority_count,
            [{"id": f"E{index}"} for index in range(10)],
            evidence,
            web_required=True,
        )
        self.assertTrue(any("authority-weighted" in issue for issue in issues))

    def test_source_channel_merge_reserves_identity_diversity_before_score_fill(self):
        numeric = [
            {
                "id": f"OLD-{index}",
                "fact": f"Consensus adoption reached {index + 10}% across the tracked cohort.",
                "value": index + 10,
                "year": None,
                "source_url": "https://dense.example/metrics",
                "domain": "dense.example",
                "source_type": "html",
                "origin": "web",
                "authoritative": False,
                "score": 100 - index,
            }
            for index in range(35)
        ]
        qualitative = [
            {
                "id": f"QUAL-{index}",
                "fact": f"Consensus discipline preserves independent challenge for team {name}.",
                "value": None,
                "year": None,
                "source_url": f"https://{name}.example/research",
                "domain": f"{name}.example",
                "source_type": "html",
                "origin": "web",
                "authoritative": False,
                "score": 5,
            }
            for index, name in enumerate(("alpha", "bravo", "charlie"))
        ]
        qualitative.append(
            {
                **numeric[0],
                "id": "DUPLICATE",
                "value": None,
                "score": 1,
            }
        )
        qualitative.append(
            {
                "id": "URL-CREDENTIALS",
                "fact": "Consensus discipline documents independent review before commitment.",
                "value": None,
                "year": None,
                "source_url": "https://user:secret@credential.example/research",
                "domain": "credential.example",
                "source_type": "html",
                "origin": "web",
                "authoritative": False,
                "score": 998,
            }
        )
        qualitative.append(
            {
                "id": "INVALID-PORT",
                "fact": "Consensus discipline uses a second documented challenge before commitment.",
                "value": None,
                "year": None,
                "source_url": "https://invalid.example:bad/research",
                "domain": "oecd.org",
                "source_type": "html",
                "origin": "web",
                "authoritative": True,
                "score": 999,
            }
        )

        merged = merge_source_channel_public_evidence(
            numeric,
            qualitative,
            limit=10,
        )

        domains = [item["domain"] for item in merged]
        self.assertEqual(set(domains), {"dense.example", "alpha.example", "bravo.example", "charlie.example"})
        self.assertNotIn("invalid.example", domains)
        self.assertNotIn("credential.example", domains)
        self.assertLessEqual(domains.count("dense.example"), 5)
        self.assertTrue(any(item["value"] is not None for item in merged))
        self.assertTrue(any(item["value"] is None for item in merged))
        self.assertEqual(
            sum(item["fact"] == numeric[0]["fact"] for item in merged),
            1,
        )
        self.assertEqual([item["id"] for item in merged], [f"WEB-E{index}" for index in range(1, len(merged) + 1)])

    def test_source_channel_production_shape_passes_without_weakening_public_gates(self):
        labels = [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
            "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
            "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
            "victor", "whiskey", "xray", "yankee",
        ]
        public_sources = [
            SourceDocument(
                title=f"Publisher {label}",
                url=f"https://{label}.example/research",
                query="consensus decision discipline",
                snippet=(
                    f"Consensus decision discipline at publisher {label} depends on "
                    "independent challenge before management commits resources."
                ),
                content="",
            )
            for label in labels
        ]
        qualitative = build_source_channel_qualitative_evidence(
            "consensus decision discipline",
            public_sources,
        )
        numeric = [
            {
                "id": f"NUM-{index}",
                "fact": f"Consensus adoption reached {index + 10}% in the measured cohort.",
                "value": index + 10,
                "year": None,
                "source_url": "https://oecd.org/dense-metrics",
                "domain": "oecd.org",
                "source_type": "html",
                "origin": "web",
                "authoritative": True,
                "score": 20,
            }
            for index in range(12)
        ]
        merged = merge_source_channel_public_evidence(numeric, qualitative, limit=30)
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {"mode": "source_channel", "anchors": ["consensus"]}

        self.assertGreaterEqual(len(merged), 10)
        self.assertGreaterEqual(len({item["domain"] for item in merged}), 2)
        self.assertEqual(
            pipeline._evidence_base_issues(
                1,
                [{"id": "PRIVATE-E1", "origin": "private_seed"}, *merged],
                merged,
                web_required=True,
            ),
            [],
        )

        thin = merged[:1]
        issues = pipeline._evidence_base_issues(
            1,
            [{"id": "PRIVATE-E1", "origin": "private_seed"}, *thin],
            thin,
            web_required=True,
        )
        self.assertTrue(any("at least 10 approved" in issue for issue in issues))
        self.assertTrue(any("independently corroborating" in issue for issue in issues))

    def test_source_channel_public_gate_requires_distinct_source_domains(self):
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {
            "mode": "source_channel",
            "anchors": ["Asterion Robotics"],
        }
        same_document_points = [
            {
                "source_url": "https://oecd.org/report",
                "domain": "oecd.org",
            },
            {
                "source_url": "https://oecd.org/report",
                "domain": "oecd.org",
            },
        ]

        issues = pipeline._evidence_base_issues(
            1,
            [{"id": f"E{index}"} for index in range(10)],
            same_document_points,
            web_required=True,
        )

        self.assertTrue(any("distinct public source" in issue for issue in issues))

    def test_generic_evidence_gate_still_rejects_nine_points(self):
        pipeline = WebReportPipeline(Mock())

        issues = pipeline._evidence_base_issues(
            1,
            [{"id": f"E{index}"} for index in range(9)],
            [{"id": f"E{index}"} for index in range(9)],
            web_required=False,
        )

        self.assertTrue(
            any("at least 10 approved evidence points" in issue for issue in issues)
        )

    def test_generic_private_seed_never_enters_source_channel_qualitative_lane(self):
        private_seed = SourceDocument(
            title="Verified seed",
            url="https://example.com/source",
            query="seed",
            snippet="Approved source thesis.",
            content="PRIVATE BODY " * 100,
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "source_id": "source-1",
                "content_hash": "a" * 64,
            },
        )
        public_source = SourceDocument(
            title="Official source",
            url="https://oecd.org/source",
            query="topic evidence",
            snippet="Official source summary.",
            content="Official source content.",
            domain="oecd.org",
        )
        fact_pack = ResearchFactPack(
            topic="Generic market report",
            objective="Generic market report",
            decision_question="What does the evidence show?",
            source_count=2,
            authoritative_source_count=1,
            source_domains=["example.com", "oecd.org"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        pipeline = WebReportPipeline(Mock())

        with tempfile.TemporaryDirectory() as directory, patch.object(
            pipeline,
            "_plan_research",
            return_value={
                "search_queries": ["generic topic official evidence"],
                "outline": ["Generic evidence conclusion"],
            },
        ), patch.object(
            pipeline,
            "_plan_chart_data_needs",
            return_value=[{
                "title": "Evidence",
                "chart_type": "bar",
                "search_queries": ["generic topic evidence"],
            }],
        ), patch.object(
            pipeline,
            "_collect_public_sources",
            return_value=[public_source],
        ), patch(
            "gen_rpt.web_report_pipeline.build_research_fact_pack",
            return_value=fact_pack,
        ), patch(
            "gen_rpt.web_report_pipeline.build_evidence_ledger",
            autospec=True,
            return_value=[{"id": f"E{index}"} for index in range(9)],
        ), patch(
            "gen_rpt.web_report_pipeline.build_verified_private_seed_evidence",
        ) as private_evidence_builder, patch(
            "gen_rpt.web_report_pipeline.build_source_channel_qualitative_evidence",
        ) as qualitative_evidence_builder:
            with self.assertRaisesRegex(
                ReportQualityError,
                "at least 10 approved evidence points",
            ):
                pipeline.build_report(
                    "Generic market report",
                    Path(directory),
                    seed_sources=[private_seed],
                )

        private_evidence_builder.assert_not_called()
        qualitative_evidence_builder.assert_not_called()

    def test_source_channel_fact_pack_is_built_from_public_sources_only(self):
        class StopAfterFactPack(RuntimeError):
            pass

        private_source = SourceDocument(
            title="Verified source",
            url="https://example.com/source",
            query="seed",
            snippet="Approved source thesis.",
            content="PRIVATE BODY " * 100,
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "source_id": "source-1",
                "content_hash": "a" * 64,
            },
        )
        public_source = SourceDocument(
            title="Official study",
            url="https://oecd.org/study",
            query="Asterion Robotics evidence",
            snippet="Official source summary.",
            content="Official source content with dated and numeric evidence.",
            domain="oecd.org",
        )
        pipeline = WebReportPipeline(Mock())
        source_profile = {
            "mode": "source_channel",
            "anchors": ["Asterion Robotics"],
            "sources": [],
        }

        with tempfile.TemporaryDirectory() as directory, patch.object(
            pipeline,
            "_plan_research",
            return_value={
                "search_queries": ["Asterion Robotics official evidence"],
                "outline": ["Source-linked conclusion"],
            },
        ), patch.object(
            pipeline,
            "_plan_chart_data_needs",
            return_value=[{
                "title": "Evidence",
                "chart_type": "bar",
                "search_queries": ["generic chart query"],
            }],
        ), patch.object(
            pipeline,
            "_collect_public_sources",
            return_value=[public_source],
        ), patch(
            "gen_rpt.web_report_pipeline.build_research_fact_pack",
            side_effect=StopAfterFactPack,
        ) as fact_pack_builder:
            with self.assertRaises(StopAfterFactPack):
                pipeline.build_report(
                    "Source-linked scan",
                    Path(directory),
                    seed_sources=[private_source],
                    source_profile=source_profile,
                )

        self.assertEqual(fact_pack_builder.call_args.args[2], [public_source])

    def test_source_channel_rejects_rag_context_before_mixing_publication_contracts(self):
        client = Mock()
        pipeline = WebReportPipeline(client)

        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            RuntimeError,
            "verified seed and public-web evidence only",
        ):
            pipeline.build_report(
                "Source-linked scan",
                Path(directory),
                rag_context="[Chunk: chunk-1] Validated private document context.",
                rag_sources=[
                    SourceDocument(
                        title="Validated document",
                        url="internal://documents/doc-1#chunk=chunk-1",
                        query="source-linked scan",
                        snippet="Validated private document context.",
                        content="Validated private document context.",
                        source_type="internal",
                        metadata={"chunk_id": "chunk-1"},
                    )
                ],
                source_profile={
                    "mode": "source_channel",
                    "anchors": ["source-linked scan"],
                },
            )

        client.chat_json.assert_not_called()

    def test_source_channel_rejects_rag_sources_without_context_before_planning(self):
        client = Mock()
        pipeline = WebReportPipeline(client)

        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            RuntimeError,
            "verified seed and public-web evidence only",
        ):
            pipeline.build_report(
                "Source-linked scan",
                Path(directory),
                rag_sources=[
                    SourceDocument(
                        title="Validated document",
                        url="internal://documents/doc-1#chunk=chunk-1",
                        query="source-linked scan",
                        snippet="Validated private document context.",
                        content="Validated private document context.",
                        source_type="internal",
                        metadata={"chunk_id": "chunk-1"},
                    )
                ],
                source_profile={
                    "mode": "source_channel",
                    "anchors": ["source-linked scan"],
                },
            )

        client.chat_json.assert_not_called()

    def test_source_channel_rejects_private_collection_inputs_before_planning(self):
        private_source = SourceDocument(
            title="Private collection document",
            url="private://collection/document-1",
            query="private collection",
            snippet="Private collection content.",
            content="Private collection content.",
            source_type="private",
        )
        cases = [
            ("collection_only", []),
            ("web_and_collection", [private_source]),
            ("web_only", [private_source]),
        ]

        for source_mode, private_sources in cases:
            with self.subTest(source_mode=source_mode, has_private=bool(private_sources)):
                client = Mock()
                pipeline = WebReportPipeline(client)
                with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
                    RuntimeError,
                    "verified seed_sources and public-web evidence only",
                ):
                    pipeline.build_report(
                        "Source-linked scan",
                        Path(directory),
                        private_sources=private_sources,
                        source_mode=source_mode,
                        source_profile={
                            "mode": "source_channel",
                            "anchors": ["source-linked scan"],
                        },
                    )
                client.chat_json.assert_not_called()

    def test_structured_planner_queries_are_unwrapped_before_search(self):
        pipeline = WebReportPipeline(Mock())
        plan = pipeline._normalize_research_plan(
            {
                "search_queries": [
                    {
                        "query": "site:bis.org derivatives statistics",
                        "purpose": "market baseline",
                    },
                    {"q": "site:cftc.gov commitments of traders"},
                    {"purpose": "missing query text"},
                ]
            },
            "Consensus positioning",
        )

        self.assertEqual(
            plan["search_queries"][:2],
            [
                "site:bis.org derivatives statistics",
                "site:cftc.gov commitments of traders",
            ],
        )
        self.assertFalse(any(query.startswith("{") for query in plan["search_queries"]))

        expanded = pipeline._expanded_search_queries(
            plan,
            [{"search_queries": [{"search_query": "site:sec.gov 13F data"}]}],
        )
        self.assertIn("site:sec.gov 13F data", expanded)
        self.assertFalse(any(query.startswith("{") for query in expanded))

    @patch("gen_rpt.web_fetch._search_bing")
    @patch("gen_rpt.web_fetch._search_duckduckgo")
    def test_site_queries_drop_provider_results_from_other_domains(self, duckduckgo, bing):
        duckduckgo.__name__ = "_search_duckduckgo"
        bing.__name__ = "_search_bing"
        duckduckgo.return_value = [
            SearchResult(
                title="Unrelated dictionary result",
                url="https://dictionary.example/query",
                snippet="Unrelated result.",
                query="site:sec.gov 13F data",
            )
        ]
        bing.return_value = [
            SearchResult(
                title="SEC 13F data",
                url="https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
                snippet="Official dataset.",
                query="site:sec.gov 13F data",
            )
        ]

        with patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "", "SEARXNG_URL": ""},
            clear=False,
        ):
            results = search_web("site:sec.gov 13F data", max_results=5)

        self.assertEqual([result.url for result in results], [
            "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
        ])

    @patch("gen_rpt.web_fetch._search_bing")
    @patch("gen_rpt.web_fetch._search_duckduckgo")
    def test_fallback_search_drops_single_generic_word_matches(self, duckduckgo, bing):
        duckduckgo.__name__ = "_search_duckduckgo"
        bing.__name__ = "_search_bing"
        duckduckgo.return_value = [
            SearchResult(
                title="Consensus definition",
                url="https://dictionary.example/consensus",
                snippet="A definition of consensus.",
                query="analyst consensus accuracy stock returns valuation",
            ),
            SearchResult(
                title="Analyst consensus forecasts and stock returns",
                url="https://nber.org/papers/example",
                snippet="Research on forecast accuracy, valuation, and subsequent returns.",
                query="analyst consensus accuracy stock returns valuation",
            ),
        ]
        bing.return_value = []

        with patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "", "SEARXNG_URL": ""},
            clear=False,
        ):
            results = search_web(
                "analyst consensus accuracy stock returns valuation",
                max_results=5,
            )

        self.assertEqual([result.url for result in results], [
            "https://nber.org/papers/example"
        ])

    def test_missing_section_evidence_is_backfilled_only_from_approved_ledger(self):
        report = {
            "sections": [{
                "title": "Derivatives positioning can amplify crowded consensus trades",
                "lead": "Concentrated derivatives positioning changes reversal sensitivity.",
                "paragraphs": ["Managers should compare notional exposure with positioning concentration."],
                "evidence": ["The model retained one grounded evidence item."],
                "so_what": "Use position data to set a decision gate.",
            }]
        }
        approved = [
            {
                "id": "E1",
                "fact": "BIS derivatives statistics report notional exposure by asset class.",
                "source_title": "BIS derivatives statistics",
                "source_url": "https://bis.org/statistics/derstats.htm",
                "authoritative": True,
                "decision_relevance": "supporting",
            },
            {
                "id": "E2",
                "fact": "CFTC reports position concentration in its Commitments of Traders data.",
                "source_title": "CFTC Commitments of Traders",
                "source_url": "https://cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
                "authoritative": True,
                "decision_relevance": "critical",
            },
        ]

        backfill_section_evidence_from_ledger(report, approved)

        evidence = report["sections"][0]["evidence"]
        self.assertEqual(len(evidence), 2)
        self.assertIn("https://cftc.gov/MarketReports/CommitmentsofTraders/index.htm", evidence[1])
        self.assertNotIn("E2", evidence[1])

        pipeline = WebReportPipeline(Mock())
        prepared, issues = pipeline._prepare_report_draft(
            report,
            topic="Consensus positioning",
            grounding_text=" ".join(item["fact"] for item in approved),
            source_count=2,
            source_chunks={},
            approved_evidence=approved,
        )
        self.assertEqual(len(prepared["sections"][0]["evidence"]), 2)
        self.assertFalse(any("traceable evidence items" in issue for issue in issues))

    def test_evidence_backfill_stays_fail_closed_without_two_source_urls(self):
        report = {"sections": [{"title": "One source only", "evidence": ["One item."]}]}
        approved = [
            {
                "fact": "First supported fact.",
                "source_title": "Only source",
                "source_url": "https://example.com/one",
            },
            {
                "fact": "Second supported fact.",
                "source_title": "Only source",
                "source_url": "https://example.com/one",
            },
        ]

        backfill_section_evidence_from_ledger(report, approved)

        self.assertEqual(report["sections"][0]["evidence"], ["One item."])

    def test_deterministic_compression_preserves_protected_report_contract(self):
        protected = (
            "In 2025, the retained source recorded 68 percent as the validated baseline, "
            "so this numeric claim and its source meaning must remain unchanged."
        )
        redundant = (
            "The surrounding discussion repeats the same descriptive context in greater detail "
            "without adding a number, citation, causal qualification, or new direction "
            "that changes the supported conclusion for the reader."
        )
        sections = []
        for index in range(6):
            sections.append({
                "title": f"Validated positioning evidence supports a bounded decision in segment {index}",
                "lead": "The retained sources establish a conclusion while preserving the limits of the available record for management review.",
                "paragraphs": [
                    " ".join([protected, redundant, redundant, redundant])
                    for _paragraph in range(5)
                ],
                "evidence": [
                    "The 2025 baseline is retained — Source A (https://example.com/a).",
                    "The independent comparison is retained — Source B (https://example.com/b).",
                ],
                "so_what": (
                    "Management should keep the decision conditional, assign a named owner, "
                    "and preserve a documented pause gate until the remaining operating evidence "
                    "has been independently verified and accepted."
                ),
            })
        report = {
            "title": "Validated evidence supports a bounded consensus-positioning decision",
            "dek": "A source-linked management brief.",
            "intro": ["The report separates retained facts from descriptive repetition."],
            "key_takeaways": ["One grounded takeaway.", "A second grounded takeaway.", "A third grounded takeaway."],
            "sections": sections,
            "action_steps": [
                {
                    "horizon": "Decision gate",
                    "action": "Verify the retained operating condition.",
                    "success_metric": "A documented pass or pause decision.",
                    "rationale": "The retained evidence supports action only after the accountable owner confirms the operating condition.",
                }
                for _index in range(4)
            ],
        }
        evidence_before = [list(section["evidence"]) for section in sections]
        implications_before = [section["so_what"] for section in sections]
        actions_before = [dict(action) for action in report["action_steps"]]
        numeric_mentions = _report_narrative_text(report).count("2025")

        before, after = compress_report_to_word_budget(report)

        self.assertGreater(before, 3_800)
        self.assertLessEqual(after, 3_650)
        self.assertGreaterEqual(after, 1_800)
        self.assertEqual(_report_narrative_text(report).count("2025"), numeric_mentions)
        self.assertEqual([section["evidence"] for section in sections], evidence_before)
        self.assertEqual([section["so_what"] for section in sections], implications_before)
        self.assertEqual(report["action_steps"], actions_before)
        for section in sections:
            self.assertTrue(3 <= len(section["paragraphs"]) <= 6)
            self.assertTrue(all(_word_count(item) >= 35 for item in section["paragraphs"]))
            section_words = _word_count(" ".join([
                section["lead"],
                *section["paragraphs"],
                section["so_what"],
            ]))
            self.assertGreaterEqual(section_words, 200)

    def test_source_channel_profile_ignores_identifier_digits_but_rejects_claims(self):
        report = _source_channel_quality_report()
        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertEqual(issues, [])

        report["sections"][0]["paragraphs"][0] += (
            " Unsupported revenue reached 777 million in the latest period."
        )
        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertTrue(any("Numeric claims not found" in issue for issue in issues))

    def test_source_channel_attempt12_shape_passes_when_shared_quality_is_substantive(self):
        report = _source_channel_attempt12_shape_report()

        self.assertLess(_word_count(report["sections"][0]["lead"]), 25)
        self.assertLess(_word_count(report["sections"][0]["paragraphs"][1]), 50)
        self.assertGreater(_word_count(report["sections"][3]["paragraphs"][1]), 65)
        self.assertGreater(_word_count(report["sections"][0]["so_what"]), 50)
        self.assertGreater(_word_count(report["sections"][0]["evidence"][0]), 55)
        self.assertTrue(
            2_100 <= _word_count(_report_narrative_text(report)) <= 2_500
        )
        for section in report["sections"]:
            self.assertTrue(3 <= len(section["paragraphs"]) <= 6)
            self.assertTrue(
                all(_word_count(paragraph) >= 35 for paragraph in section["paragraphs"])
            )
            section_words = _word_count(
                " ".join(
                    [section["lead"], *section["paragraphs"], section["so_what"]]
                )
            )
            self.assertTrue(200 <= section_words <= 550)

        self.assertEqual(
            source_channel_report_quality_issues(
                report,
                topic="Bounded market response",
                context_text="The validated public record supports a conditional operating response.",
                source_count=2,
            ),
            [],
        )

    def test_source_channel_attempt13_2532_shape_passes_initial_gate(self):
        report = _source_channel_attempt13_shape_report()

        self.assertEqual(_word_count(_report_narrative_text(report)), 2_532)
        self.assertEqual(
            source_channel_report_quality_issues(
                report,
                topic="Bounded market response",
                context_text="The validated public record supports a conditional operating response.",
                source_count=2,
            ),
            [],
        )

    def test_source_channel_attempt13_2532_shape_passes_revision_preparation(self):
        report = _source_channel_attempt13_shape_report()
        client = Mock()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        prepared, issues = pipeline._prepare_report_draft(
            report,
            topic="Bounded market response",
            grounding_text="The validated public record supports a conditional operating response.",
            source_count=2,
            source_chunks={},
            approved_evidence=[],
        )

        self.assertIs(prepared, report)
        self.assertEqual(issues, [])
        with patch.object(
            pipeline,
            "_revise_report_draft",
        ) as revision, patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            returned, remaining = pipeline._rescue_final_report(
                prepared,
                issues,
                storyline_plan={},
                topic="Bounded market response",
                grounding_text="The validated public record supports a conditional operating response.",
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertIs(returned, report)
        self.assertEqual(remaining, [])
        revision.assert_not_called()
        compression.assert_not_called()
        client.chat_json.assert_not_called()

    def test_source_channel_attempt13_2532_shape_passes_final_humanized_gate(self):
        report = _source_channel_attempt13_shape_report()
        client = Mock()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        with tempfile.TemporaryDirectory() as directory, patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            pipeline._final_humanized_quality_gate(
                report,
                topic="Bounded market response",
                grounding_text="The validated public record supports a conditional operating response.",
                source_count=2,
                source_chunks={},
                output_dir=Path(directory),
            )

        compression.assert_not_called()
        client.chat_json.assert_not_called()

    def test_source_channel_2601_total_still_fails_closed(self):
        report = _source_channel_target_overage(2_601)

        self.assertEqual(
            source_channel_report_quality_issues(
                report,
                topic="Bounded market response",
                context_text="The validated public record supports a conditional operating response.",
                source_count=2,
            ),
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2601."
            ],
        )

    def test_source_channel_accepts_four_developed_paragraphs_under_shared_gate(self):
        report = _source_channel_attempt12_shape_report()
        report["sections"][0]["paragraphs"].append(
            _source_channel_words(
                "A fourth developed paragraph can preserve an additional supported mechanism or execution boundary without becoming a structural publication failure",
                35,
                "fourthparagraphcontext",
            )
        )

        self.assertEqual(
            source_channel_report_quality_issues(
                report,
                topic="Bounded market response",
                context_text="The validated public record supports a conditional operating response.",
                source_count=2,
            ),
            [],
        )

    def test_source_channel_attempt12_shape_still_rejects_shallow_paragraph(self):
        report = _source_channel_attempt12_shape_report()
        report["sections"][0]["paragraphs"][0] = _source_channel_words(
            report["sections"][0]["paragraphs"][0],
            34,
            "shallowcontext",
        )

        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertTrue(any("underdeveloped paragraphs under 35 words" in issue for issue in issues))

    def test_source_channel_still_rejects_a_missing_lead(self):
        report = _source_channel_attempt12_shape_report()
        report["sections"][0]["lead"] = ""
        report["intro"][0] += " " + " ".join(["leadmargin"] * 13)

        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertIn(
            "Source-channel section 1 requires a non-empty conclusion-first lead.",
            issues,
        )

    def test_source_channel_attempt12_shape_still_rejects_overlong_section(self):
        report = _source_channel_attempt12_shape_report()
        report["intro"] = []
        report["sections"][0]["paragraphs"][0] = _source_channel_words(
            report["sections"][0]["paragraphs"][0],
            360,
            "overlongsectioncontext",
        )
        self.assertLessEqual(_word_count(_report_narrative_text(report)), 2_500)

        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertTrue(any("needs 200-550 words of analysis" in issue for issue in issues))

    def test_source_channel_still_rejects_under_target_total(self):
        report = _source_channel_quality_report()
        report["intro"] = []
        total_words = _word_count(_report_narrative_text(report))
        self.assertTrue(1_800 <= total_words < 2_100)

        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )

        self.assertIn(
            f"The source-channel reader-visible publication minimum is 2,100 words; found {total_words}.",
            issues,
        )

    def test_post_humanization_source_channel_overage_fails_without_deletion(self):
        report = _source_channel_target_overage()
        client = Mock()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        with tempfile.TemporaryDirectory() as directory, patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            with self.assertRaisesRegex(
                ReportQualityError,
                "Post-humanization report quality gate failed",
            ):
                pipeline._final_humanized_quality_gate(
                    report,
                    topic="Bounded market response",
                    grounding_text="The validated public record supports a conditional operating response.",
                    source_count=2,
                    source_chunks={},
                    output_dir=Path(directory),
                )

        compression.assert_not_called()
        client.chat_json.assert_not_called()
        self.assertEqual(_word_count(_report_narrative_text(report)), 2_601)

    def test_source_channel_final_rescue_returns_issues_without_model_revision(self):
        report = _source_channel_target_overage()
        issues = source_channel_report_quality_issues(
            report,
            topic="Bounded market response",
            context_text="The validated public record supports a conditional operating response.",
            source_count=2,
        )
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {"mode": "source_channel"}

        with patch.object(
            pipeline,
            "_revise_report_draft",
        ) as revision, patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            returned, remaining = pipeline._rescue_final_report(
                report,
                issues,
                storyline_plan={},
                topic="Bounded market response",
                grounding_text="The validated public record supports a conditional operating response.",
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertIs(returned, report)
        self.assertEqual(remaining, issues)
        revision.assert_not_called()
        compression.assert_not_called()

    def test_source_channel_final_gate_rejects_six_section_half_product(self):
        report = _source_channel_quality_report()
        report["sections"].append(dict(report["sections"][0]))
        pipeline = WebReportPipeline(Mock())
        pipeline.source_profile = {"mode": "source_channel"}

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ReportQualityError, "exactly 5"):
                pipeline._final_humanized_quality_gate(
                    report,
                    topic="Bounded market response",
                    grounding_text="The validated public record supports a conditional operating response.",
                    source_count=2,
                    source_chunks={},
                    output_dir=Path(directory),
                )

    def test_source_channel_synthesis_failure_never_emits_generic_fallback(self):
        pipeline = WebReportPipeline(Mock())
        upstream_failure = RuntimeError("both editorial routes unavailable")
        publication_failure = ReportQualityError("publication contract failed")

        self.assertFalse(pipeline._synthesis_error_must_fail_closed(upstream_failure))
        self.assertTrue(pipeline._synthesis_error_must_fail_closed(publication_failure))
        pipeline.source_profile = {"mode": "source_channel"}
        self.assertTrue(pipeline._synthesis_error_must_fail_closed(upstream_failure))
        self.assertTrue(pipeline._synthesis_error_must_fail_closed(publication_failure))

    def test_deterministic_compression_keeps_gate_closed_when_only_evidence_is_long(self):
        report = {
            "sections": [{
                "lead": "Protected lead.",
                "paragraphs": [
                    "In 2025, 68 validated observations remain protected in this paragraph. " * 4
                    for _index in range(3)
                ],
                "evidence": ["https://example.com/source " + "validated evidence " * 1_000],
                "so_what": "Management should preserve the decision gate because the evidence remains protected.",
            }],
        }

        before, after = compress_report_to_word_budget(report, max_words=500, min_words=0)

        self.assertEqual(after, before)
        self.assertGreater(after, 500)

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
            stance="validation_only",
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {"temperature": 0.12},
        )
        self.assertIn("documented corridor approval", prompt)
        self.assertIn("CONFLICT REGISTER", prompt)
        self.assertIn("RECOMMENDATION BOUNDARY: validation_only", prompt)
        self.assertIn("paragraphs must be a JSON array", prompt)
        self.assertEqual(prompt.count("A conflicting raw source reports 72% acceptance."), 1)

    def test_source_channel_synthesis_uses_a_non_conflicting_dedicated_profile(self):
        source = SourceDocument(
            title="Verified topic seed",
            url="https://example.com/seed",
            query="topic",
            snippet="Bounded topic seed.",
            content="Public corroboration supports a bounded operating mechanism.",
        )
        fact_pack = ResearchFactPack(
            topic="Bounded response",
            objective="Assess the operating mechanism",
            decision_question="What remains independently supported?",
            source_count=2,
            authoritative_source_count=1,
            source_domains=["example.com", "openalex.org"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        source_client = Mock()
        source_client.chat_json.return_value = {"title": "Bounded response"}
        source_pipeline = WebReportPipeline(source_client)
        source_pipeline.source_profile = {"mode": "source_channel"}

        source_pipeline._synthesize_web_report(
            "Bounded response",
            {},
            [],
            [source],
            fact_pack,
            [],
            {},
        )

        source_prompt = source_client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(
            source_client.chat_json.call_args.kwargs,
            {
                "temperature": 0.12,
                "max_tokens": 8_000,
                "fallback_max_tokens": 8_000,
                "strict_output_budget": True,
            },
        )
        self.assertIn("SOURCE-CHANNEL CONTRACT", source_prompt)
        self.assertIn("exactly 3 separate paragraph strings", source_prompt)
        self.assertIn("2,100-2,600 words", source_prompt)
        self.assertIn("2,100-2,300-word creative target", source_prompt)
        self.assertIn("2,600-word ceiling", source_prompt)
        self.assertIn("is not a drafting target", source_prompt)
        self.assertIn("Publication accepts 3-6 developed paragraphs", source_prompt)
        self.assertIn("not independent publication blockers", source_prompt)
        self.assertIn("each paragraph 50-55, lead 25-30, so_what 35-42", source_prompt)
        self.assertIn("complete section 210-235 words", source_prompt)
        self.assertIn(
            "Never gain headroom by deleting, weakening, or truncating traceable evidence, "
            "supported numbers, real source URLs, any so_what management implication, or any required action field.",
            source_prompt,
        )
        self.assertNotIn("5-6 substantial sections", source_prompt)
        self.assertNotIn("250-450 words", source_prompt)
        self.assertNotIn("2,000-3,000 word", source_prompt)

        generic_client = Mock()
        generic_client.chat_json.return_value = {"title": "Generic response"}
        generic_pipeline = WebReportPipeline(generic_client)
        generic_pipeline._synthesize_web_report(
            "Generic response",
            {},
            [],
            [source],
            fact_pack,
            [],
            {},
        )
        generic_prompt = generic_client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(
            generic_client.chat_json.call_args.kwargs,
            {"temperature": 0.12},
        )
        self.assertIn("5-6 substantial sections", generic_prompt)
        self.assertIn("250-450 words", generic_prompt)
        self.assertIn("2,000-3,000 word", generic_prompt)
        self.assertNotIn("SOURCE-CHANNEL CONTRACT", generic_prompt)
        self.assertEqual(
            hashlib.sha256(generic_prompt.encode("utf-8")).hexdigest(),
            "655c02ee17b60aaff07fa825bc6d35e254801997630e7f23cade4ca481015cd5",
        )

    def test_source_channel_chinese_synthesis_uses_production_headroom_targets(self):
        source = SourceDocument(
            title="公开交叉验证",
            url="https://example.com/public-corroboration",
            query="经营机制 交叉验证",
            snippet="公开材料支持有边界的经营判断。",
            content="独立公开材料支持这一经营机制，同时保留待核验条件。",
        )
        fact_pack = ResearchFactPack(
            topic="有边界的经营判断",
            objective="评估公开证据支持的经营机制",
            decision_question="哪些结论已获得独立交叉验证？",
            source_count=2,
            authoritative_source_count=1,
            source_domains=["example.com", "openalex.org"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )
        client = Mock()
        client.chat_json.return_value = {"title": "有边界的经营判断"}
        pipeline = WebReportPipeline(client, language="zh")
        pipeline.source_profile = {"mode": "source_channel"}

        pipeline._synthesize_web_report(
            "有边界的经营判断",
            {},
            [],
            [source],
            fact_pack,
            [],
            {},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {
                "temperature": 0.12,
                "max_tokens": 8_000,
                "fallback_max_tokens": 8_000,
                "strict_output_budget": True,
            },
        )
        self.assertIn("2,100-2,600 个中文字或英文单词", prompt)
        self.assertIn("2,100-2,300 创作目标", prompt)
        self.assertIn("2,600 仅是保留完整已验证材料的发布上限，不是创作目标", prompt)
        self.assertIn("共享发布门槛接受 3-6 个独立段落", prompt)
        self.assertIn("不作为独立发布阻断项", prompt)
        self.assertIn("每段 50-55 字，lead 25-30 字，so_what 35-42 字", prompt)
        self.assertIn("每章合计 210-235 字", prompt)
        self.assertIn(
            "不得通过删除、弱化或截断可追溯证据、已支持数字、真实来源 URL、"
            "任何 so_what 管理含义或必填 action 字段来换取余量",
            prompt,
        )
        self.assertNotIn("2,000-3,000 字", prompt)

    def test_section_normalization_preserves_body_paragraph_breaks(self):
        report = normalize_structured_payload(
            {
                "sections": [
                    {
                        "title": "Evidence supports a conditional decision",
                        "lead": "The decision remains conditional.",
                        "body": "First developed paragraph.\n\nSecond developed paragraph.\n\nThird developed paragraph.",
                        "proof_points": ["Grounded proof."],
                        "management_implication": "Management should preserve a decision gate.",
                    }
                ]
            }
        )

        self.assertEqual(
            report["sections"][0]["paragraphs"],
            ["First developed paragraph.", "Second developed paragraph.", "Third developed paragraph."],
        )
        self.assertEqual(report["sections"][0]["evidence"], ["Grounded proof."])
        self.assertEqual(report["sections"][0]["so_what"], "Management should preserve a decision gate.")

    def test_action_normalization_canonicalizes_revision_aliases(self):
        report = normalize_structured_payload(
            {
                "action_steps": [
                    {
                        "timing": "Next decision gate",
                        "recommendation": "Validate the investment thesis.",
                        "decision_gate": "Evidence owners approve the documented conditions.",
                        "evidence_basis": "The retained evidence supports validation before additional resources are committed.",
                        "unused_model_field": "USD 800 billion",
                    }
                ]
            }
        )

        self.assertEqual(
            report["action_steps"],
            [
                {
                    "horizon": "Next decision gate",
                    "action": "Validate the investment thesis.",
                    "success_metric": "Evidence owners approve the documented conditions.",
                    "rationale": "The retained evidence supports validation before additional resources are committed.",
                }
            ],
        )

    def test_section_prose_normalization_only_rebalances_existing_text(self):
        sentence = lambda label: f"{label} " + " ".join(f"word{i}" for i in range(29)) + "."
        management = "Management should act " + " ".join(f"decision{i}" for i in range(37)) + "."
        report = {
            "sections": [{
                "paragraphs": [
                    sentence("Evidence"),
                    sentence("Mechanism") + " " + sentence("Exposure"),
                    sentence("Risk") + " " + sentence("Response") + " " + sentence("Outlook"),
                    management,
                ],
                "so_what": "Keep the decision conditional.",
            }]
        }
        words_before = sum(len(item.split()) for item in report["sections"][0]["paragraphs"]) + len(report["sections"][0]["so_what"].split())

        normalize_report_section_prose(report)

        section = report["sections"][0]
        words_after = sum(len(item.split()) for item in section["paragraphs"]) + len(section["so_what"].split())
        self.assertEqual(len(section["paragraphs"]), 3)
        self.assertTrue(all(len(item.split()) >= 45 for item in section["paragraphs"]))
        self.assertIn(management, section["so_what"])
        self.assertEqual(words_after, words_before)

    def test_section_prose_normalization_does_not_pad_thin_content(self):
        report = {"sections": [{"paragraphs": ["Too short."], "so_what": "Also short."}]}

        normalize_report_section_prose(report)

        self.assertEqual(report["sections"][0]["paragraphs"], ["Too short."])
        self.assertEqual(report["sections"][0]["so_what"], "Also short.")

    def test_section_prose_normalization_removes_only_unmatched_terminal_quotes(self):
        report = {"sections": [{"paragraphs": ['Malformed sentence."', '"A complete quotation."']}]}

        normalize_report_section_prose(report)

        self.assertEqual(report["sections"][0]["paragraphs"], ["Malformed sentence.", '"A complete quotation."'])

    def test_section_prose_normalization_removes_exact_repeated_paragraphs(self):
        repeated = "Repeated evidence " + " ".join(f"word{i}" for i in range(50)) + "."
        report = {
            "sections": [
                {"paragraphs": [repeated]},
                {"paragraphs": [repeated, "Distinct mechanism.", "Distinct risk.", "Distinct implication."]},
            ]
        }

        normalize_report_section_prose(report)

        self.assertEqual(report["sections"][0]["paragraphs"], [repeated])
        self.assertNotIn(repeated, report["sections"][1]["paragraphs"])

    def test_numeric_gate_matches_equivalent_chinese_large_number_units(self):
        context = "报告记录了340万次紧急转移、680亿元损失，以及5-15亿美元的市场区间。"

        self.assertTrue(rag_visible_numbers_supported("3.4 million evacuations and 68 billion yuan", context))
        self.assertTrue(rag_visible_numbers_supported("USD 0.5-1.5 billion", context))
        self.assertFalse(rag_visible_numbers_supported("340 million evacuations", context))
        self.assertFalse(rag_visible_numbers_supported("USD 15 billion", context))

    def test_numeric_pruning_preserves_equivalent_unit_conversions(self):
        report = {
            "key_takeaways": [
                "At least 3.4 million evacuations were recorded.",
                "Direct losses exceeded 68 billion yuan.",
                "The decision remains conditional on validated evidence.",
            ]
        }

        removed = prune_unsupported_numeric_claims(report, "至少紧急转移340万人，直接经济损失超过680亿元。")

        self.assertEqual(removed, [])
        self.assertEqual(len(report["key_takeaways"]), 3)

    def test_numeric_gate_ignores_bubble_geometry_and_matches_abbreviated_units(self):
        exhibit = {
            "type": "bubble",
            "points": [{"label": "2024: $302M", "x": 74.05, "y": 63.2, "size": 48.6}],
        }

        self.assertTrue(rag_visible_numbers_supported(exhibit, "In 2024, funding reached $0.302 billion."))

    def test_web_draft_drops_only_exhibits_with_unsupported_numbers(self):
        pipeline = WebReportPipeline(Mock())
        report = {
            "exhibits": [
                {"type": "bar", "title": "Supported", "values": [302]},
                {"type": "line", "title": "Unsupported", "values": [0.08, 0.11], "labels": [2036]},
            ]
        }

        report, issues = pipeline._prepare_report_draft(
            report,
            topic="Flood resilience",
            grounding_text="Validated funding index reached 302.",
            source_count=1,
            source_chunks={},
            approved_evidence=[],
        )

        self.assertEqual([item["title"] for item in report["exhibits"]], ["Supported"])
        self.assertFalse(any("Numeric claims not found" in issue for issue in issues))

        merged = {
            "exhibits": [
                {"type": "bar", "title": "Supported", "values": [302]},
                {"type": "line", "title": "Unsupported", "values": [0.5, 0.8], "labels": [2036]},
            ]
        }
        pipeline._filter_post_merge_exhibits(merged, [], "Validated funding index reached 302.")
        self.assertEqual([item["title"] for item in merged["exhibits"]], ["Supported"])

    def test_report_revision_receives_the_rejected_draft_and_quality_corrections(self):
        client = Mock()
        client.chat_json.return_value = {"title": "Revised report"}
        pipeline = WebReportPipeline(client)
        rejected = {
            "title": "Conditional launch",
            "action_steps": [{"horizon": "Immediate", "action": "Preserve this grounded action."}],
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
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {"temperature": 0.05},
        )
        self.assertEqual(revised["title"], "Revised report")
        self.assertEqual(revised["action_steps"], rejected["action_steps"])
        self.assertEqual(revised["sections"], rejected["sections"])
        self.assertIn("The rejected draft is too short.", prompt)
        self.assertIn("Section 1 needs 3-6 developed analytical paragraphs", prompt)
        self.assertIn("paragraphs must be a JSON array", prompt)
        self.assertIn("[Chunk: chunk-1]", prompt)

    def test_report_revision_preserves_omitted_nested_fields(self):
        client = Mock()
        client.chat_json.return_value = {
            "action_steps": [{"action": "Use the corrected action.", "horizon": ""}],
            "sections": [{"paragraphs": ["Corrected developed paragraph."], "evidence": []}],
        }
        pipeline = WebReportPipeline(client)
        rejected = {
            "action_steps": [{"horizon": "Next decision gate", "action": "Original action."}],
            "sections": [{"paragraphs": ["Original paragraph."], "evidence": ["Grounded evidence."]}],
        }

        revised = pipeline._revise_report_draft(rejected, ["Correct the action and paragraph."], {})

        self.assertEqual(revised["action_steps"][0]["horizon"], "Next decision gate")
        self.assertEqual(revised["action_steps"][0]["action"], "Use the corrected action.")
        self.assertEqual(revised["sections"][0]["paragraphs"], ["Corrected developed paragraph."])
        self.assertEqual(revised["sections"][0]["evidence"], ["Grounded evidence."])

    def test_source_channel_revision_uses_three_paragraph_editorial_target(self):
        rejected = _source_channel_quality_report()
        client = Mock()
        client.chat_json.return_value = {
            "sections": [
                {
                    "title": section["title"],
                    "lead": section["lead"],
                    "paragraphs": section["paragraphs"],
                }
                for section in rejected["sections"]
            ]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        pipeline._revise_report_draft(
            rejected,
            ["Shorten the reader-visible report."],
            {},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {
                "temperature": 0.05,
                "max_tokens": 8_000,
                "fallback_max_tokens": 8_000,
                "strict_output_budget": True,
            },
        )
        self.assertIn("SOURCE-CHANNEL REVISION CONTRACT", prompt)
        self.assertIn("exactly 3 separate paragraph strings as the editorial target", prompt)
        self.assertIn("publication accepts 3-6 developed paragraphs", prompt)
        self.assertIn("2,100-2,600", prompt)
        self.assertIn("2,100-2,300 creative target", prompt)
        self.assertIn("2,600-word ceiling", prompt)
        self.assertIn("is not a drafting target", prompt)
        self.assertIn("each paragraph 50-55, lead 25-30, so_what 35-42", prompt)
        self.assertIn("complete section 210-235", prompt)
        self.assertIn(
            "Never gain headroom by deleting, weakening, or truncating traceable evidence, "
            "supported numbers, real source URLs, any so_what management implication, or any required action field.",
            prompt,
        )
        self.assertNotIn("exactly 4 separate strings", prompt)
        self.assertNotIn("300 and 400 words", prompt)

    def test_source_channel_revision_prompt_handles_shared_quality_failures_with_headroom(self):
        rejected = _source_channel_quality_report()
        shared_quality_issues = [
            "Section 1 has underdeveloped paragraphs under 35 words: [1].",
            "Section 1 needs 200-550 words of analysis; found 551.",
            "The source-channel reader-visible publication ceiling is 2,600 words; found 2601.",
        ]
        client = Mock()
        client.chat_json.return_value = {}
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            shared_quality_issues,
            {"selected_modules": ["mechanism", "execution boundary"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        for issue in shared_quality_issues:
            self.assertIn(issue, prompt)
        self.assertIn("2,100-2,300 creative target", prompt)
        self.assertIn("each paragraph 50-55, lead 25-30, so_what 35-42", prompt)
        self.assertIn("complete section 210-235", prompt)
        self.assertIn(rejected["sections"][0]["evidence"][0], prompt)
        self.assertIn(rejected["action_steps"][0]["success_metric"], prompt)
        self.assertEqual(revised, rejected)

    def test_source_channel_length_convergence_prompt_keeps_evidence_immutable(self):
        rejected = _source_channel_target_overage(2_765)
        original_evidence = [
            copy.deepcopy(section["evidence"])
            for section in rejected["sections"]
        ]
        client = Mock()
        client.chat_json.return_value = {
            "intro": ["A more concise but still complete decision framing."],
            "sections": [
                {
                    "paragraphs": section["paragraphs"],
                    "evidence": ["A model-proposed replacement must not be accepted."],
                }
                for section in rejected["sections"]
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {"selected_modules": ["mechanism", "execution boundary"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("SOURCE-CHANNEL LENGTH CONVERGENCE OVERRIDE", prompt)
        self.assertIn("counter measured 2765", prompt)
        self.assertIn("aim near 2,200", prompt)
        self.assertIn("whole-report prose convergence pass", prompt)
        self.assertIn("Preserve every section evidence array exactly", prompt)
        self.assertEqual(
            [section["evidence"] for section in revised["sections"]],
            original_evidence,
        )
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {
                "temperature": 0.05,
                "max_tokens": 8_000,
                "fallback_max_tokens": 8_000,
                "strict_output_budget": True,
            },
        )

        client.chat_json.return_value["sections"] = client.chat_json.return_value[
            "sections"
        ][:4]
        incomplete_revision = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {"selected_modules": ["mechanism", "execution boundary"]},
        )
        self.assertEqual(
            [section["evidence"] for section in incomplete_revision["sections"]],
            original_evidence,
        )

        client.chat_json.return_value = {}
        pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication minimum is "
                "2,100 words; found 2086."
            ],
            {"selected_modules": ["mechanism", "execution boundary"]},
        )
        minimum_prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("counter measured 2086", minimum_prompt)
        self.assertIn(
            "Add or deepen only the smallest necessary connective analysis",
            minimum_prompt,
        )

    def test_source_channel_length_convergence_restores_numeric_url_and_research_id_fields(self):
        rejected = _source_channel_target_overage(2_765)
        rejected["intro"][0] += (
            " The 2025 baseline at https://old.example/market remains binding."
        )
        rejected["sections"][0]["lead"] = (
            "DOI 10.1234/original and OpenAlex W1234567890 anchor the retained conclusion."
        )
        rejected["action_steps"][0]["rationale"] = (
            "SSRN ID 456789 supports the original 15% execution boundary."
        )
        rejected["sections"][0]["evidence_internal"] = [
            "Internal retained evidence for 2025."
        ]
        rejected["sections"][0]["references"] = [
            {"title": "Retained source", "url": "https://old.example/reference"}
        ]
        original_intro = rejected["intro"][0]
        original_lead = rejected["sections"][0]["lead"]
        original_rationale = rejected["action_steps"][0]["rationale"]
        original_evidence = copy.deepcopy(rejected["sections"][0]["evidence"])
        original_evidence_internal = copy.deepcopy(
            rejected["sections"][0]["evidence_internal"]
        )
        original_section_references = copy.deepcopy(
            rejected["sections"][0]["references"]
        )
        original_references = copy.deepcopy(rejected["references"])
        safe_implication = "A shorter grounded implication keeps the existing boundary visible."
        client = Mock()
        client.chat_json.return_value = {
            "intro": [
                "The 2026 baseline at https://new.example/market replaces the old record."
            ],
            "action_steps": [
                {
                    "rationale": (
                        "SSRN ID 999999 supports a replacement 25% execution boundary."
                    )
                },
                {},
                {},
                {},
            ],
            "sections": [
                {
                    "lead": (
                        "DOI 10.9999/replacement and OpenAlex W9999999999 "
                        "replace the retained conclusion."
                    ),
                    "evidence": ["https://new.example/unapproved-evidence"],
                    "evidence_internal": ["Replacement internal evidence for 2026."],
                    "references": [
                        {
                            "title": "Replacement source",
                            "url": "https://new.example/reference",
                        }
                    ],
                },
                {"so_what": safe_implication},
                {},
                {},
                {},
            ],
            "references": [
                {"title": "Replacement", "url": "https://new.example/top-level"}
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {"selected_modules": ["mechanism", "execution boundary"]},
        )

        self.assertEqual(revised["intro"][0], original_intro)
        self.assertEqual(revised["sections"][0]["lead"], original_lead)
        self.assertEqual(
            revised["action_steps"][0]["rationale"],
            original_rationale,
        )
        self.assertEqual(revised["sections"][0]["evidence"], original_evidence)
        self.assertEqual(
            revised["sections"][0]["evidence_internal"],
            original_evidence_internal,
        )
        self.assertEqual(
            revised["sections"][0]["references"],
            original_section_references,
        )
        self.assertEqual(revised["references"], original_references)
        self.assertEqual(revised["sections"][1]["so_what"], safe_implication)
        self.assertNotIn("2026", json.dumps(revised, ensure_ascii=False))
        self.assertNotIn("new.example", json.dumps(revised, ensure_ascii=False))
        self.assertNotIn("10.9999/replacement", json.dumps(revised, ensure_ascii=False))
        self.assertNotIn("W9999999999", json.dumps(revised, ensure_ascii=False))
        self.assertNotIn("999999", json.dumps(revised, ensure_ascii=False))
        guard_metrics = pipeline._last_source_length_revision_metrics
        restored_paths = {
            item["path"] for item in guard_metrics["restored"]
        }
        self.assertEqual(
            restored_paths,
            {
                "intro.0",
                "sections.0.lead",
                "action_steps.0.rationale",
            },
        )
        self.assertEqual(guard_metrics["restored_path_count"], 3)
        self.assertGreater(guard_metrics["restored_original_words"], 0)
        self.assertGreaterEqual(guard_metrics["net_restored_words"], 0)

    def test_source_channel_length_convergence_rejects_new_numeric_and_url_tokens(self):
        rejected = _source_channel_target_overage(2_765)
        original_title = rejected["title"]
        original_takeaway = rejected["key_takeaways"][0]
        original_lead = rejected["sections"][0]["lead"]
        original_paragraphs = copy.deepcopy(rejected["sections"][0]["paragraphs"])
        safe_lead = "A concise grounded lead preserves the existing operating boundary."
        client = Mock()
        client.chat_json.return_value = {
            "title": f"{original_title} for 2026",
            "key_takeaways": [
                f"{original_takeaway} at 20%",
                rejected["key_takeaways"][1],
                rejected["key_takeaways"][2],
            ],
            "sections": [
                {
                    "lead": f"{original_lead} source.new-example.org/path",
                    "paragraphs": original_paragraphs
                    + ["A new 2026 paragraph cites https://new.example/source."],
                    "new_metric": "2026 https://new.example/source",
                },
                {"lead": safe_lead},
                {},
                {},
                {},
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {},
        )

        self.assertEqual(revised["title"], original_title)
        self.assertEqual(revised["key_takeaways"][0], original_takeaway)
        self.assertEqual(revised["sections"][0]["lead"], original_lead)
        self.assertEqual(revised["sections"][0]["paragraphs"], original_paragraphs)
        self.assertNotIn("new_metric", revised["sections"][0])
        self.assertEqual(revised["sections"][1]["lead"], safe_lead)

    def test_source_channel_length_convergence_uses_immutable_baseline_for_incomplete_shapes(self):
        rejected = _source_channel_target_overage(2_765)
        rejected["sections"][0]["lead"] = (
            "The 2025 boundary remains tied to https://old.example/market."
        )
        rejected["action_steps"][0]["rationale"] = (
            "SSRN ID 456789 supports the retained 15% execution boundary."
        )
        immutable_input = copy.deepcopy(rejected)
        client = Mock()
        client.chat_json.return_value = {
            "sections": [
                {
                    "lead": (
                        "The 2026 boundary replaces it with "
                        "https://new.example/market."
                    )
                },
                {},
                {},
                {},
            ],
            "action_steps": [
                {
                    "rationale": (
                        "SSRN ID 999999 supports a replacement 25% boundary."
                    )
                },
                {},
                {},
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {},
        )

        self.assertEqual(rejected, immutable_input)
        self.assertEqual(len(revised["sections"]), 5)
        self.assertEqual(len(revised["action_steps"]), 4)
        self.assertEqual(
            revised["sections"][0]["lead"],
            immutable_input["sections"][0]["lead"],
        )
        self.assertEqual(
            revised["action_steps"][0]["rationale"],
            immutable_input["action_steps"][0]["rationale"],
        )

    def test_source_channel_length_convergence_rejects_opaque_network_and_unicode_urls(self):
        rejected = _source_channel_target_overage(2_765)
        immutable_input = copy.deepcopy(rejected)
        client = Mock()
        client.chat_json.return_value = {
            "title": "A replacement points to example\u00ad.com/path.",
            "dek": "A replacement points to 例子．公司/报告.",
            "intro": ["A replacement points to urn:example:market."],
            "key_takeaways": [
                "A replacement points to www｡例子｡公司.",
                rejected["key_takeaways"][1],
                rejected["key_takeaways"][2],
            ],
            "sections": [
                {
                    "lead": "A replacement points to ipfs:Qmabcdef.",
                    "paragraphs": rejected["sections"][0]["paragraphs"]
                    + ["A replacement points to //intranet/source."],
                    "new_links": {
                        "magnet": "magnet:?xt=urn:btih:abcdef",
                        "idn": "www。例子。公司",
                    },
                },
                {
                    "paragraphs": rejected["sections"][1]["paragraphs"]
                    + ["A replacement points to münche\u0308.de/report."],
                    "so_what": "A replacement points to //[::ffff]/source.",
                },
                {
                    "title": "A replacement points to 例子.公司/报告.",
                    "paragraphs": rejected["sections"][2]["paragraphs"]
                    + ["A replacement points to example\u200c.com/path."],
                    "new_hidden_link": {
                        "url": "example\u200b.com/path",
                    },
                },
                {"lead": "A replacement points to münchen.de/report."},
                {"title": "A replacement points to 例子。公司/报告."},
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            [
                "The source-channel reader-visible publication ceiling is "
                "2,600 words; found 2765."
            ],
            {},
        )

        self.assertEqual(rejected, immutable_input)
        self.assertEqual(revised["title"], immutable_input["title"])
        self.assertEqual(revised["dek"], immutable_input["dek"])
        self.assertEqual(revised["intro"], immutable_input["intro"])
        self.assertEqual(
            revised["key_takeaways"],
            immutable_input["key_takeaways"],
        )
        self.assertEqual(
            revised["sections"][0]["lead"],
            immutable_input["sections"][0]["lead"],
        )
        self.assertEqual(
            revised["sections"][0]["paragraphs"],
            immutable_input["sections"][0]["paragraphs"],
        )
        self.assertNotIn("new_links", revised["sections"][0])
        self.assertEqual(
            revised["sections"][1]["so_what"],
            immutable_input["sections"][1]["so_what"],
        )
        self.assertEqual(
            revised["sections"][1]["paragraphs"],
            immutable_input["sections"][1]["paragraphs"],
        )
        self.assertEqual(
            revised["sections"][2]["title"],
            immutable_input["sections"][2]["title"],
        )
        self.assertEqual(
            revised["sections"][2]["paragraphs"],
            immutable_input["sections"][2]["paragraphs"],
        )
        self.assertNotIn("new_hidden_link", revised["sections"][2])
        self.assertEqual(
            revised["sections"][3]["lead"],
            immutable_input["sections"][3]["lead"],
        )
        self.assertEqual(
            revised["sections"][4]["title"],
            immutable_input["sections"][4]["title"],
        )

    def test_source_channel_length_convergence_stops_on_structural_issue(self):
        rejected = _source_channel_target_overage(2_765)
        proposed_sections = [
            {"paragraphs": ["Too short for the source publication contract."]},
            {},
            {},
            {},
            {},
        ]
        client = Mock()
        client.chat_json.return_value = {"sections": proposed_sections}
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        issues = source_channel_report_quality_issues(
            rejected,
            topic="Bounded market response",
            context_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
        )

        with patch(
            "gen_rpt.web_report_pipeline.compress_report_to_word_budget",
            wraps=compress_report_to_word_budget,
        ) as compression:
            _revised, remaining = pipeline._converge_source_channel_length(
                rejected,
                issues,
                storyline_plan={"selected_modules": ["mechanism", "boundary"]},
                topic="Bounded market response",
                grounding_text=(
                    "The validated public record supports a conditional operating response."
                ),
                source_count=2,
                source_chunks={},
                approved_evidence=[],
            )

        self.assertTrue(
            any("needs 3-6 developed analytical paragraphs" in issue for issue in remaining)
        )
        self.assertEqual(client.chat_json.call_count, 1)
        compression.assert_not_called()

    def test_source_length_invariant_does_not_change_generic_revision(self):
        client = Mock()
        client.chat_json.return_value = {"title": "Generic revised title"}
        pipeline = WebReportPipeline(client)
        rejected = {
            "title": "2025 https://old.example/path",
            "intro": ["Original generic narrative."],
            "sections": [],
            "action_steps": [],
        }

        revised = pipeline._revise_report_draft(
            rejected,
            ["Correct generic prose."],
            {"selected_modules": ["mechanism"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertEqual(revised["title"], "Generic revised title")
        self.assertEqual(client.chat_json.call_args.kwargs, {"temperature": 0.05})
        self.assertEqual(
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "97905b92d7bdb9e5619580597ef5678bea02fbdd8a61520620dc840498538193",
        )

    def test_source_channel_revision_ignores_extra_top_level_fields(self):
        rejected = _source_channel_quality_report()
        original_references = copy.deepcopy(rejected["references"])
        original_methodology = rejected["methodology"]
        client = Mock()
        client.chat_json.return_value = {
            "title": "A corrected bounded title",
            "references": [{"title": "Invented", "url": "https://invalid.example/new"}],
            "methodology": "A replacement methodology that was not requested.",
            "category": "Unapproved category",
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            ["Correct the title only."],
            {},
        )

        self.assertEqual(revised["title"], "A corrected bounded title")
        self.assertEqual(revised["references"], original_references)
        self.assertEqual(revised["methodology"], original_methodology)
        self.assertNotIn("category", revised)

    def test_source_channel_revision_corrects_exact_list_counts_without_old_tail(self):
        rejected = _source_channel_quality_report()
        rejected["sections"].append(copy.deepcopy(rejected["sections"][-1]))
        rejected["action_steps"].append(copy.deepcopy(rejected["action_steps"][-1]))
        rejected["key_takeaways"] = rejected["key_takeaways"][:2]
        client = Mock()
        client.chat_json.return_value = {
            "key_takeaways": [
                "Corrected first takeaway",
                "Corrected second takeaway",
                "Corrected third takeaway",
            ],
            "sections": [
                {"title": f"Corrected section {index + 1}"}
                for index in range(5)
            ],
            "action_steps": [
                {"action": f"Corrected action {index + 1}"}
                for index in range(4)
            ],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            ["Restore the exact source-channel list counts."],
            {},
        )

        self.assertEqual(len(revised["sections"]), 5)
        self.assertEqual(len(revised["action_steps"]), 4)
        self.assertEqual(len(revised["key_takeaways"]), 3)
        self.assertEqual(revised["key_takeaways"][2], "Corrected third takeaway")
        self.assertEqual(revised["sections"][0]["title"], "Corrected section 1")
        self.assertEqual(revised["sections"][0]["evidence"], rejected["sections"][0]["evidence"])
        self.assertEqual(revised["action_steps"][0]["action"], "Corrected action 1")
        self.assertEqual(revised["action_steps"][0]["horizon"], rejected["action_steps"][0]["horizon"])

    def test_source_channel_partial_revision_preserves_valid_shapes_and_takeaways(self):
        rejected = _source_channel_quality_report()
        original_third_takeaway = rejected["key_takeaways"][2]
        original_evidence = copy.deepcopy(rejected["sections"][0]["evidence"])
        original_horizon = rejected["action_steps"][0]["horizon"]
        client = Mock()
        client.chat_json.return_value = {
            "key_takeaways": ["Corrected first takeaway", "Corrected second takeaway"],
            "sections": [{"lead": "Corrected bounded section lead"}],
            "action_steps": [{"action": "Corrected bounded action", "horizon": ""}],
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}

        revised = pipeline._revise_report_draft(
            rejected,
            ["Apply bounded field corrections without changing valid list shapes."],
            {},
        )

        self.assertEqual(len(revised["sections"]), 5)
        self.assertEqual(len(revised["action_steps"]), 4)
        self.assertEqual(len(revised["key_takeaways"]), 3)
        self.assertEqual(revised["key_takeaways"][:2], ["Corrected first takeaway", "Corrected second takeaway"])
        self.assertEqual(revised["key_takeaways"][2], original_third_takeaway)
        self.assertEqual(revised["sections"][0]["lead"], "Corrected bounded section lead")
        self.assertEqual(revised["sections"][0]["evidence"], original_evidence)
        self.assertEqual(revised["action_steps"][0]["action"], "Corrected bounded action")
        self.assertEqual(revised["action_steps"][0]["horizon"], original_horizon)

    def test_report_revision_rejects_structurally_incomplete_rescue(self):
        client = Mock()
        client.chat_json.return_value = {
            "sections": [{"title": f"Shortened section {index}"} for index in range(4)],
            "action_steps": [{"action": f"Shortened action {index}"} for index in range(3)],
        }
        pipeline = WebReportPipeline(client)
        rejected = {
            "sections": [{"title": f"Grounded section {index}"} for index in range(5)],
            "action_steps": [{"action": f"Grounded action {index}"} for index in range(4)],
        }

        revised = pipeline._revise_report_draft(rejected, ["Improve the report."], {})

        self.assertEqual(revised["sections"], rejected["sections"])
        self.assertEqual(revised["action_steps"], rejected["action_steps"])

    @patch("gen_rpt.web_report_pipeline.normalize_report_section_prose")
    @patch("gen_rpt.web_report_pipeline.report_content_quality_issues")
    @patch("gen_rpt.web_report_pipeline.normalize_web_report")
    def test_final_quality_rescue_continues_until_the_revised_report_passes(self, normalize, quality_issues, normalize_prose):
        pipeline = WebReportPipeline(Mock())
        pipeline._revise_report_draft = Mock(side_effect=lambda report, _issues, _plan: report)
        pipeline._prepare_report_draft = Mock(side_effect=lambda report, **_kwargs: (report, []))
        normalize.side_effect = lambda report, **_kwargs: report
        quality_issues.side_effect = [["Sections still need development."], []]

        report, remaining = pipeline._rescue_final_report(
            {"sections": [], "action_steps": []},
            ["The report requires 5-6 substantive sections; found 4."],
            storyline_plan={},
            topic="Flood-resilience technology transfer",
            grounding_text="Validated evidence",
            source_count=1,
            source_chunks={},
            approved_evidence=[],
        )

        self.assertEqual(remaining, [])
        self.assertEqual(pipeline._revise_report_draft.call_count, 2)
        self.assertEqual(quality_issues.call_count, 2)
        self.assertEqual(normalize_prose.call_count, 2)

    def test_normalization_preserves_exact_rag_citations_for_internal_validation(self):
        citation = '[Chunk: chunk-1] "Validated flood-resilience evidence." — Governing evidence.'
        report = normalize_web_report(
            {
                "title": "Phased market entry protects capital while validating demand",
                "key_takeaways": ["One.", "Two.", "Three."],
                "sections": [{"title": "Evidence supports phased entry", "evidence": [citation]}],
            },
            topic="Flood-resilience technology transfer",
            allow_synthetic_fallbacks=False,
        )

        issues = rag_report_quality_issues(
            report,
            topic="Flood-resilience technology transfer",
            context_text="Validated flood-resilience evidence.",
            source_count=1,
            source_chunks={"chunk-1": "Validated flood-resilience evidence."},
        )

        self.assertEqual(report["sections"][0]["evidence_internal"], [citation])
        self.assertNotIn("[Chunk:", report["sections"][0]["evidence"][0])
        self.assertFalse(any("distinct exact private-document citations" in issue for issue in issues))

    def test_grounding_adds_missing_citations_to_internal_evidence_after_normalization(self):
        first = '[Chunk: chunk-1] "Validated flood-resilience evidence." — Governing evidence.'
        report = {"sections": [{
            "title": "Evidence supports phased entry",
            "paragraphs": ["Flood resilience shapes the entry decision."],
            "evidence": ["Reader-facing citation."],
            "evidence_internal": [first],
        }]}

        ground_rag_section_evidence(
            report,
            {
                "chunk-1": "Validated flood-resilience evidence.",
                "chunk-2": "Technology transfer requires local implementation capacity.",
            },
        )

        self.assertEqual(report["sections"][0]["evidence"], ["Reader-facing citation."])
        self.assertEqual(len(report["sections"][0]["evidence_internal"]), 2)
        self.assertIn("[Chunk: chunk-2]", report["sections"][0]["evidence_internal"][1])

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

    def test_editorial_audit_does_not_request_unsupported_probabilities(self):
        client = Mock()
        client.chat_json.return_value = {"score": 80}
        pipeline = WebReportPipeline(client)

        pipeline._audit_report_content(
            {"title": "Conditional resilience investment"},
            {"selected_modules": ["base, upside and downside scenarios"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("Never penalize omitted probabilities", prompt)
        self.assertIn("remove or qualify it", prompt)
        self.assertIn("Do not require direct numerical comparisons between unlike units", prompt)

    def test_final_editorial_audit_verifies_requested_corrections_without_moving_goalposts(self):
        client = Mock()
        client.chat_json.return_value = {"score": 80}
        pipeline = WebReportPipeline(client)

        pipeline._audit_report_content(
            {"title": "Conditional resilience investment"},
            {"selected_modules": []},
            revision_corrections=["Remove the unmatched trailing quote."],
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("final verification", prompt)
        self.assertIn("Remove the unmatched trailing quote.", prompt)
        self.assertIn("Do not introduce a new requirement", prompt)
        self.assertIn("deterministic check against the complete validated corpus", prompt)

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
