"""Operational Chinese delivery keeps completeness without claiming strict review."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, ModelClient, READABLE_POLICY_VERSION, _readable_translation,
    _translate, _translation_state, _readable_observations, digest, translate_pdf,
)

RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts/gatex_localization_runner.py"
SPEC = importlib.util.spec_from_file_location("readable_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def client():
    with patch.dict(os.environ, {"GATEX_PDF_TRANSLATION_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "offline-fixture"}, clear=True):
        return ModelClient(session_factory=lambda: (_ for _ in ()).throw(AssertionError("network forbidden")), sleep=lambda _: None)


class ReadableTests(unittest.TestCase):
    def test_default_generates_raw_complete_blocks_once_without_precision_review_or_placeholders(self):
        translator, calls = client(), []
        blocks = [Block("quantity", 0, "Demand is 10 -", None),
                  Block("date", 0, "The update arrived in mid-July 2025.", None)]
        def once(messages, **_):
            rows = json.loads(messages[1]["content"])["blocks"]
            calls.append(copy.deepcopy(rows))
            self.assertEqual([row["text"] for row in rows], [block.text for block in blocks])
            return {"blocks": [{"id": "quantity", "text": "需求为10—"},
                               {"id": "date", "text": "更新于七月中旬2025到来。"}]}
        translator._once = once
        accepted = translator.translate(blocks)
        self.assertEqual(translator.acceptance_mode, "readable")
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(accepted), {block.id for block in blocks})
        self.assertTrue(all(row["reviewed"] is False for row in accepted.values()))
        self.assertTrue(all(row["acceptancePolicyVersion"] == READABLE_POLICY_VERSION for row in accepted.values()))

    def test_missing_ids_retry_only_missing_blocks_and_checkpoint_valid_siblings_immediately(self):
        translator, calls, saved = client(), [], []
        translator.accepted_callback = lambda rows: saved.append(copy.deepcopy(rows))
        blocks = [Block("a", 0, "Demand", None), Block("b", 0, "Supply", None)]
        def once(messages, **_):
            rows = json.loads(messages[1]["content"])["blocks"]
            calls.append([row["id"] for row in rows])
            return {"blocks": [{"id": "a", "text": "需求"}]} if len(calls) == 1 else {"blocks": [{"id": "b", "text": "供应"}]}
        translator._once = once
        self.assertEqual(set(translator.translate(blocks)), {"a", "b"})
        self.assertEqual(calls, [["a", "b"], ["b"]])
        self.assertEqual([set(rows) for rows in saved], [{"a"}, {"b"}])

    def test_readable_edition_context_does_not_require_absent_quantity_placeholders(self):
        translator = client()
        block = Block("edition", 0, "This edition is an authorized English translation of material published on 24 September 2026.", None)
        def once(messages, **_):
            row = json.loads(messages[1]["content"])["blocks"][0]
            self.assertEqual(row["text"], block.text)
            self.assertNotIn("must still use the supplied quantity placeholders", json.dumps(row))
            return {"blocks": [{"id": "edition", "text": "原英文版本是2026年九月24日发布材料的授权英文译本。"}]}
        translator._once = once
        self.assertFalse(translator.translate([block])["edition"]["reviewed"])

    def test_empty_english_and_internal_tokens_remain_pending_with_no_partial_pdf(self):
        for target in ("", "Demand has increased", "需求{GATEX_QUANTITY_0}"):
            with self.subTest(target=target):
                translator, count = client(), []
                translator._once = lambda *_args, **_kwargs: (count.append(1) or {"blocks": [{"id": "a", "text": target}]})
                state = {"blocks": {}, "ocr": {}, "status": "in-progress"}
                with self.assertRaisesRegex(LocalizationError, "TRANSLATION_BLOCKS_PENDING"):
                    _translate([Block("a", 0, "Demand has increased", None)], state, lambda _: None, translator)
                self.assertEqual(len(count), 3)
                self.assertEqual(state["blocks"], {})
                self.assertNotEqual(state["status"], "complete")

    def test_numeric_date_and_proper_name_observations_are_bounded_advisory(self):
        translator = client()
        blocks = [Block(f"b{i}", 0, "Revenue grew 7% in January 2024.", None) for i in range(60)]
        records = {block.id: _readable_translation(block.text, {"text": "收入增长8%，在一月2024。"}) for block in blocks}
        state = {}
        result = _readable_observations(blocks, records, state, translator)
        self.assertFalse(all(result["numeric"].values()))
        self.assertGreater(result["summary"]["issueCounts"]["NUMERIC_OR_LINK_MISMATCH"], 0)
        self.assertEqual(len(state["readableAdvisories"]["sample"]), 32)
        self.assertTrue(state["readableAdvisories"]["sampleTruncated"])
        self.assertNotIn("收入", json.dumps(state))
        self.assertEqual(result["qualityCount"], 0)

    def test_866_source_bound_records_resume_without_review_or_ocr_invalidation(self):
        translator = client()
        translator._once = lambda *_args, **_kwargs: self.fail("accepted text must not be regenerated or reviewed")
        blocks = [Block(f"b{i}", 0, f"Demand {i}", None) for i in range(866)]
        original = {block.id: {"sourceHash": block.source_hash, "text": f"需求{index}", "retainedEnglish": [], "reviewed": True}
                    for index, block in enumerate(blocks)}
        state = _translation_state(None, "a"*64, translator.model, False,
                                   require_ocr_coverage=False, translation_policy=translator.translation_policy)
        state["blocks"] = copy.deepcopy(original)
        checkpoint = copy.deepcopy(state)
        resumed = _translation_state(checkpoint, "a"*64, translator.model, False,
                                   require_ocr_coverage=False, translation_policy=translator.translation_policy)
        self.assertEqual(resumed["blocks"], original)
        self.assertEqual(len(_translate(blocks, resumed, lambda _: None, translator)), 866)
        self.assertEqual(resumed["blocks"], original)

    def test_real_pdf_has_complete_pages_figures_and_chinese_but_honest_unreviewed_flags(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, target = directory/"source.pdf", directory/"zh.pdf"
            pdf = fitz.open()
            buffer = io.BytesIO()
            Image.new("RGB", (80, 80), "blue").save(buffer, format="PNG")
            for _ in range(2):
                page = pdf.new_page()
                page.insert_text((50, 50), "Revenue grew 7%.")
                page.insert_image(fitz.Rect(50, 80, 130, 160), stream=buffer.getvalue())
            pdf.save(source); pdf.close()
            translator, calls, checkpoints = client(), [], []
            def once(messages, **_):
                rows = json.loads(messages[1]["content"])["blocks"]
                calls.append(1)
                return {"blocks": [{"id": row["id"], "text": "收入增长8%。"} for row in rows]}
            translator._once = once
            result = translate_pdf(source, target, digest(source.read_bytes()),
                translation_client=translator, raster_ocr=lambda _: {"verified": True, "uncertainRegions": [], "lines": []}, save_progress=checkpoints.append)
            qa = result["qa"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(qa["acceptanceMode"], "readable")
            self.assertTrue(qa["readableChinesePassed"])
            self.assertEqual(qa["readableCheckedBlockCount"], qa["translatedBlockCount"])
            self.assertFalse(qa["semanticReviewPassed"])
            self.assertFalse(qa["professionalChinesePassed"])
            self.assertFalse(qa["numericChecksPassed"])
            self.assertGreater(qa["advisoryIssueCount"], 0)
            runner.validate_qa(qa, result["pageCount"], source_sha256=digest(source.read_bytes()))
            with fitz.open(target) as output:
                self.assertEqual(output.page_count, 2)
                self.assertTrue(all("收入增长8%" in page.get_text() for page in output))
                self.assertTrue(all(page.get_images() for page in output))
            self.assertEqual(checkpoints[-1]["status"], "complete")

    def test_provider_and_source_integrity_errors_still_fail_without_fabricated_success(self):
        translator, calls = client(), []
        def once(*_args, **_kwargs):
            calls.append(1)
            raise LocalizationError("MODEL_HTTP_402")
        translator._once = once
        with self.assertRaisesRegex(LocalizationError, "MODEL_HTTP_402"):
            translator.translate([Block("a", 0, "Demand", None)])
        self.assertEqual(len(calls), 1)
        with TemporaryDirectory() as temporary:
            source, target = Path(temporary)/"source.pdf", Path(temporary)/"target.pdf"
            source.write_bytes(b"%PDF-corrupt")
            with self.assertRaisesRegex(LocalizationError, "SOURCE_DIGEST_MISMATCH"):
                translate_pdf(source, target, "a"*64, translation_client=translator)
            self.assertFalse(target.exists())

    def test_genuine_retained_identity_and_numerical_literals_can_remain_latin(self):
        self.assertEqual(_readable_translation("National Central Storage Company PLC", {"text": "National Central Storage Company PLC",
            "retainedEnglish": [{"text": "National Central Storage Company PLC", "kind": "organisation"}]})["text"], "National Central Storage Company PLC")
        self.assertEqual(_readable_translation("GPU", {"text": "GPU"})["text"], "GPU")
        with self.assertRaisesRegex(LocalizationError, "CHINESE_TRANSLATION_MISSING"):
            _readable_translation("FORECAST", {"text": "FORECAST", "retainedEnglish": [{"text": "FORECAST", "kind": "brand"}]})

    def test_basic_chinese_coverage_rejects_sentences_disguised_as_names_and_token_chinese(self):
        source = "The company has increased production and plans to expand its service operations across the region."
        for target, retained in [(source, [{"text": source, "kind": "organisation"}]),
                (source+"中文", [{"text": source, "kind": "brand"}]),
                ("公司。", []), ("中文 "+source, []),
                ("公司 has increased production and plans to expand its service operations across the region.", [])]:
            with self.subTest(target=target), self.assertRaisesRegex(LocalizationError, "CHINESE_TRANSLATION_MISSING"):
                _readable_translation(source, {"text": target, "retainedEnglish": retained})
        self.assertEqual(_readable_translation(source, {"text": "公司已提高产量，并计划在整个地区扩大服务业务。"})["text"],
                         "公司已提高产量，并计划在整个地区扩大服务业务。")
        for name, kind in [("Paul Graham", "person"), ("Unsolved Problems", "organisation"),
                           ("HBM4E", "code"), ("GateX", "brand")]:
            with self.subTest(name=name):
                self.assertEqual(_readable_translation(name, {"text": name,
                    "retainedEnglish": [{"text": name, "kind": kind}]})["text"], name)

    def test_runner_readable_acceptance_keeps_strict_advisories_but_requires_real_coverage(self):
        from scripts import gatex_pdf_maintenance as maintenance
        qa = {"schema": runner.QA_SCHEMA, "passed": True, "sourceSha256": "a"*64,
            "sourcePageCount": 1, "pageCount": 1, "sourceBlockCount": 2, "translatedBlockCount": 2,
            "missingBlocks": 0, "overflowCount": 0, "unresolvedEnglishCount": 3,
            "figuresPreserved": True, "numericChecksPassed": False, "semanticReviewPassed": False,
            "professionalChinesePassed": False, "ownedTerminologyPassed": False,
            "minimumTranslatedFontSize": 7, "rasterOCRVerified": True,
            "acceptanceMode": "readable", "acceptancePolicyVersion": READABLE_POLICY_VERSION,
            "readableChinesePassed": True, "readableCheckedBlockCount": 2,
            "pages": [{"sourcePage": 1, "targetPages": [1]}]}
        runner.validate_qa(qa, 1, source_sha256="a"*64)
        maintenance.validate_current_qa(qa, 1, "a"*64)
        for changed in ({"sourceSha256": "b"*64}, {"readableCheckedBlockCount": 1},
                {"readableChinesePassed": False}, {"acceptancePolicyVersion": "unknown"},
                {"missingBlocks": 1}, {"translatedBlockCount": 1}, {"figuresPreserved": False},
                {"minimumTranslatedFontSize": 6.9}, {"minimumTranslatedFontSize": float("nan")},
                {"rasterOCRVerified": False}, {"pages": [{"sourcePage": 1, "targetPages": [2]}]}):
            with self.subTest(changed=changed), self.assertRaises(runner.RunnerError):
                runner.validate_qa({**qa, **changed}, 1, source_sha256="a"*64)
            with self.subTest(maintenance_changed=changed), self.assertRaises(maintenance.runner.RunnerError):
                maintenance.validate_current_qa({**qa, **changed}, 1, "a"*64)
        strict = {**qa, "acceptanceMode": "strict"}
        with self.assertRaises(runner.RunnerError):
            runner.validate_qa(strict, 1)


if __name__ == "__main__":
    unittest.main()
