"""Synthetic counterexamples, not the unavailable report from run 36849227585.

That run retained only its 4406 -> 3994 log. These fixtures reproduce the
conservative compressor's inability to shorten single-sentence paragraphs.
All model answers below are injected; no paid/model/network calls are made.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gen_rpt.report_draft_checkpoint import DraftCheckpoint, fingerprint
from gen_rpt.research_quality import ResearchFactPack
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_publication_contract import (
    _report_narrative_text, _word_count, compress_report_to_word_budget,
    report_content_quality_issues,
)
from gen_rpt.web_report_pipeline import WebReportPipeline, ReportQualityError, _source_channel_length_budget


def words(seed, count):
    tokens = seed.split()
    assert len(tokens) <= count
    return " ".join(tokens + ["context"] * (count - len(tokens))) + "."


def synthetic_report():
    sections = []
    for name in ("demand", "supply", "policy", "adoption", "execution", "financing"):
        sections.append({
            "title": f"Verified {name} conditions support a conditional operating response",
            "lead": words(f"The retained {name} record supports cautious execution while corroboration remains necessary", 30),
            "paragraphs": [words(f"The {name} {role} conclusion remains conditional because corroboration is incomplete and the operating constraint must be verified before commitment", 85)
                           for role in ("evidence", "mechanism", "counterpoint", "implication")],
            "so_what": words(f"Management should assign the {name} owner to verify the unresolved condition before committing capacity and pause when corroboration fails", 40),
            "evidence": [words(f"The {name} record describes conditional execution with a retained 2025 observation https://example.org/{name}/{label}", 70)
                         for label in ("primary", "secondary")],
        })
    report = {
        "title": "Verified operating evidence supports cautious and conditional commitments",
        "dek": "Corroboration must precede changes in operating commitments.",
        "intro": ["A conditional operating response is appropriate."],
        "key_takeaways": [words(f"The {name} finding warrants conditional execution rather than an unconditional commitment", 25)
                          for name in ("demand", "capacity", "policy")],
        "sections": sections,
        "action_steps": [{"horizon": "Next review", "action": f"Verify the {name} condition before commitment.",
                          "success_metric": "Document an independently corroborated operating condition.",
                          "rationale": words(f"The retained {name} record supports conditional action while material uncertainty remains unresolved", 20)}
                         for name in ("demand", "capacity", "policy", "execution")],
        "references": ["https://example.org/primary", "https://example.org/secondary"],
    }
    extra = 4406 - _word_count(_report_narrative_text(report))
    report["intro"][0] = words("A conditional operating response is appropriate", _word_count(report["intro"][0]) + extra)
    assert _word_count(_report_narrative_text(report)) == 4406
    return report


def concise_answer(messages, **kwargs):
    prompt = messages[-1]["content"]
    fields = json.loads(prompt.split("Eligible complete fields and hard budgets:\n", 1)[1].split("\n\nRules:", 1)[0])
    return {"field_revisions": [{"path": field["path"],
                                "text": words(" ".join(field["text"].rstrip(".").split()[:min(24, field["minimum_words"])]), field["maximum_words"])}
                               for field in fields]}


class StandardLengthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.report = synthetic_report()
        self.context = _report_narrative_text(self.report)
        self.client = Mock()
        self.client.chat_json.side_effect = concise_answer
        self.pipeline = WebReportPipeline(self.client)

    def issues(self, report):
        return report_content_quality_issues(report, topic="Synthetic bounded analysis", context_text=self.context, source_count=2)

    def repair(self, report=None):
        report = report if report is not None else self.report
        return self.pipeline._revise_source_channel_fields(
            report, self.issues(report), storyline_plan={}, topic="Synthetic bounded analysis",
            grounding_text=self.context, source_count=2, source_chunks={}, approved_evidence=[], standard_length_repair=True,
        )

    def test_synthetic_stall_reaches_budget_without_deleting_evidence_or_actions(self):
        original = copy.deepcopy(self.report)
        self.assertEqual(compress_report_to_word_budget(self.report), (4406, 4406))
        self.assertTrue(self.pipeline._standard_length_ceiling_only(self.issues(self.report)))
        fixed, issues = self.repair()
        self.assertEqual(issues, [])
        self.assertLessEqual(_word_count(_report_narrative_text(fixed)), 3650)
        self.assertGreaterEqual(_word_count(_report_narrative_text(fixed)), 1800)
        for current, previous in zip(fixed["action_steps"], original["action_steps"]):
            for key in ("horizon", "action", "success_metric"):
                self.assertEqual(current[key], previous[key])
            self.assertGreaterEqual(_word_count(current["rationale"]), 12)
        self.assertEqual(fixed["references"], original["references"])
        self.assertEqual([s["evidence"] for s in fixed["sections"]], [s["evidence"] for s in original["sections"]])
        self.assertEqual(self.client.chat_json.call_count, 1)

    def test_new_numbers_or_partial_fields_cannot_buy_word_headroom(self):
        for invalid in ("A new benchmark is 99 percent.", "Too short."):
            with self.subTest(invalid=invalid):
                self.client.chat_json.return_value = {"field_revisions": [{"path": "intro.0", "text": invalid}]}
                self.client.chat_json.side_effect = None
                before = copy.deepcopy(self.report)
                fixed, issues = self.repair()
                self.assertEqual(fixed, before)
                self.assertTrue(issues)

    def test_fixed_evidence_can_make_length_repair_infeasible_without_model_call(self):
        for section in self.report["sections"]:
            section["evidence"] = [words("A retained source records the 2025 boundary https://example.org/record", 250)] * 2
        with self.assertRaisesRegex(ReportQualityError, "infeasible"):
            self.repair()
        self.client.chat_json.assert_not_called()

    def test_other_quality_failures_are_not_disguised_as_length_only(self):
        for issue in ["No validated sources were retained.", "Section 1 needs at least two traceable evidence items; found 1."]:
            self.assertFalse(self.pipeline._standard_length_ceiling_only(self.issues(self.report) + [issue]))
        self.assertFalse(self.pipeline._standard_length_ceiling_only(["The reader-visible decision brief needs 1,800-3,800 words; found 1200."]))

    def test_simplified_mode_does_not_enter_the_standard_length_route(self):
        self.pipeline.report_mode = "gatex_simplified_v1"
        fixed, issues = self.repair()
        self.assertEqual(fixed, self.report)
        self.assertTrue(issues)
        self.client.chat_json.assert_not_called()

    def test_rag_shape_keeps_exact_private_chunk_quotes_and_unchanged_evidence(self):
        quote = "The retained operating condition requires independent corroboration before commitment."
        self.pipeline.rag_context = quote
        for section in self.report["sections"]:
            section["evidence_internal"] = [f'[Chunk: private-1] "{quote}" — Synthetic original document.']
        original = copy.deepcopy(self.report)
        prepared, issues = self.pipeline._prepare_report_draft(
            self.report, topic="Synthetic bounded analysis", grounding_text=self.context + quote,
            source_count=2, source_chunks={"private-1": quote}, approved_evidence=[],
        )
        self.assertTrue(self.pipeline._standard_length_ceiling_only(issues))
        fixed, issues = self.pipeline._revise_source_channel_fields(
            prepared, issues, storyline_plan={}, topic="Synthetic bounded analysis",
            grounding_text=self.context + quote, source_count=2, source_chunks={"private-1": quote},
            approved_evidence=[], standard_length_repair=True,
        )
        self.assertEqual(issues, [])
        self.assertEqual([s["evidence_internal"] for s in fixed["sections"]], [s["evidence_internal"] for s in original["sections"]])
        self.assertEqual([s["evidence"] for s in fixed["sections"]], [s["evidence"] for s in original["sections"]])

    def test_budget_accounts_for_all_visible_fields_and_keeps_section_floors(self):
        budget = _source_channel_length_budget(self.report, creative_target=3500, creative_max=3650, publication_max=3800)
        self.assertEqual(budget["total_words"], 4406)
        self.assertLessEqual(sum(x["target_max_words"] for x in budget["fields"]), 3650)
        for index in range(6):
            analysis = [x for x in budget["fields"] if x["section_index"] == index and x["kind"] in {"section_lead", "section_paragraphs", "section_so_what"}]
            self.assertGreaterEqual(sum(x["min_words"] for x in analysis), 200)

    def test_checkpoint_preserves_rejected_draft_and_context_without_approval(self):
        source = SourceDocument("Synthetic source", "https://example.org/source", "query", "snippet", "Retained source text.")
        evidence = [{"fact": "Retained fact", "source_url": source.url}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"diagnostic.json"
            checkpoint = DraftCheckpoint(path, inputs={"topic": "synthetic"}, sources=[source], approved_evidence=evidence, storyline_plan={})
            checkpoint.record("prepared", self.report, self.issues(self.report), revisions_used=0)
            first = copy.deepcopy(self.report)
            self.report["title"] = "Mutated later title"
            evidence[0]["fact"] = "Later mutation"
            checkpoint.record("rejected", self.report, ["still over budget"], revisions_used=1, error="No progress")
            data = json.loads(path.read_text())
            self.assertFalse(data["publication_authorized"])
            self.assertEqual(data["events"][0]["report"], first)
            self.assertEqual(data["events"][0]["report_sha256"], fingerprint(first))
            self.assertEqual(data["context_sha256"], fingerprint(data["context"]))
            self.assertNotIn("Retained source text.", path.read_text())
            self.assertEqual(data["events"][-1]["error"], "No progress")

    def test_build_length_route_spends_one_revision_and_reaches_independent_audit(self):
        self._exercise_build(no_progress=False)

    def test_public_sidecar_contains_only_hashes_counts_and_fixed_categories(self):
        secret = "PRIVATE_RAG_INPUT_DO_NOT_PUBLISH"
        source = SourceDocument(secret, "https://private.example/" + secret, "query", "snippet", secret,
                                metadata={"chunk_id": secret, "validated": True})
        self.report["title"] = secret
        budget = _source_channel_length_budget(self.report, creative_target=3500, creative_max=3650, publication_max=3800)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"run-attempt.json"
            checkpoint = DraftCheckpoint(path, inputs={"topic": secret, "rag_url": source.url}, sources=[source],
                                         approved_evidence=[{"chunk_id": secret, "quote": secret}], storyline_plan={"private": secret})
            checkpoint.record("rejected", self.report, [secret], revisions_used=3, error=secret, budget=budget)
            self.assertIn(secret, path.read_text())
            artifact_path = path.with_suffix(".diagnostic.json")
            artifact_text = artifact_path.read_text()
            for forbidden in (secret, source.url, "https://", "complete replacement", '"report":', '"value":'):
                self.assertNotIn(forbidden, artifact_text)
            artifact = json.loads(artifact_text)
            self.assertEqual(artifact["schema"], "gen-rpt-public-diagnostic-v1")
            self.assertFalse(artifact["publication_authorized"])
            self.assertFalse(artifact["raw_draft_retained_in_artifact"])
            self.assertFalse(artifact["automatic_resume_supported"])
            self.assertEqual(artifact["events"][0]["report_sha256"], fingerprint(self.report))
            self.assertEqual(artifact["source_fingerprints"][0]["metadata_sha256"], fingerprint(source.metadata))
            self.assertEqual(artifact["events"][0]["quality_issues"], [{"category": "quality_gate", "sha256": fingerprint(secret)}])
            self.assertEqual(artifact["events"][0]["length_budget"]["total_words"], budget["total_words"])
            self.assertEqual(artifact["events"][0]["length_budget"]["fields"][0]["words"], budget["fields"][0]["words"])

    def test_build_no_progress_stops_after_one_revision_and_keeps_failed_checkpoint(self):
        self._exercise_build(no_progress=True)

    def test_build_small_progress_is_bounded_to_original_three_revision_slots(self):
        self._exercise_build(no_progress=False, small_progress=True)

    def _exercise_build(self, no_progress, small_progress=False):
        sources = [SourceDocument("Synthetic original", "https://example.org/source", "query", "snippet", self.context, domain="example.org")]
        fact_pack = ResearchFactPack(topic="Synthetic bounded analysis", objective="Verify a bounded conclusion", decision_question="What follows?",
            source_count=1, authoritative_source_count=1, source_domains=["example.org"], source_refs=[sources[0].url],
            high_confidence_facts=["A bounded conclusion."], numeric_facts=[], dated_facts=[], validation_issues=[])
        evidence = [{"id": f"E{i}", "fact": "A retained operating condition.", "source_url": sources[0].url, "status": "approved"} for i in range(10)]
        if no_progress:
            self.client.chat_json.side_effect = None
            self.client.chat_json.return_value = {"field_revisions": []}
        elif small_progress:
            def one_short_field(messages, **kwargs):
                revisions = concise_answer(messages, **kwargs)["field_revisions"]
                return {"field_revisions": revisions[-1:]}
            self.client.chat_json.side_effect = one_short_field
        with tempfile.TemporaryDirectory() as directory, patch.object(self.pipeline, "_plan_research", return_value={"search_queries": ["synthetic"]}), \
             patch.object(self.pipeline, "_plan_chart_data_needs", return_value=[]), \
             patch.object(self.pipeline, "_collect_public_sources", return_value=sources), \
             patch.object(self.pipeline, "_synthesize_web_report", return_value=copy.deepcopy(self.report)), \
             patch("gen_rpt.web_report_pipeline.build_research_fact_pack", return_value=fact_pack), \
             patch("gen_rpt.web_report_pipeline.build_evidence_ledger", return_value=evidence), \
             patch("gen_rpt.web_report_pipeline.build_storyline_plan", return_value={}), \
             patch.object(self.pipeline, "_revise_report_draft", side_effect=AssertionError("Whole report rewrite is forbidden for a length-only failure")), \
             patch.object(self.pipeline, "_post_process"), \
             patch.object(self.pipeline, "_audit_report_content", side_effect=ReportQualityError("Independent audit sentinel")) as audit:
            checkpoint = Path(directory)/"diagnostics"/"draft.json"
            with self.assertRaisesRegex(ReportQualityError, "quality gate failed" if no_progress or small_progress else "Independent audit sentinel"):
                self.pipeline.build_report("Synthetic bounded analysis", Path(directory)/"report", checkpoint_path=checkpoint)
            saved = json.loads(checkpoint.read_text())
            self.assertEqual(self.client.chat_json.call_count, 3 if small_progress else 1)
            self.assertEqual(saved["events"][-1]["phase"], "rejected")
            self.assertEqual(saved["events"][-1]["revisions_used"], 3 if small_progress else 1)
            self.assertFalse(saved["publication_authorized"])
            self.assertEqual(audit.call_count, 0 if no_progress or small_progress else 1)


if __name__ == "__main__":
    unittest.main()
