import copy
import base64
import io
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, ModelClient, PROMPT_VERSION, PROGRESS_SCHEMA,
    _image_hashes, _source_identity_hints, _source_verified_record, digest, tokens, translate_pdf, unresolved_english,
    validate_translation, _translation_state, OCR_POLICY_VERSION, _source_masthead_hint, _source_edition_context,
    _writer, _font, _block_writer, _raster_render_aliases, _contained,
    _date_review_draft, _translate, _source_calendar_hints, _quantity_placeholder_errors, _source_page_reference_hints,
    _translation_review_draft, _source_publication_terms,
    _adjacent_review_context, _source_month_year_hints,
    _private_ocr_evidence, OCR_CONTEXT_REVIEW_POLICY, OCR_CONTEXT_REVIEW_SYSTEM,
    _bind_quantity_context, _validate_block_translation,
)
from gen_rpt.gatex_translation_quantities import protect_quantities


def fixture_source_text(item):
    value = item["text"]
    for hint in item.get("quantityHints", []):
        value = value.replace(hint["placeholder"], hint["value"])
    return value


def fixture_generated_text(item, value):
    # Emulate the provider preserving placeholders, without changing prose or
    # silently correcting deliberately bad numeric values in a fixture.
    replacements = {}
    for hint in item.get("quantityHints", []):
        replacements.setdefault(hint["value"], []).append(hint["placeholder"])
    if not replacements:
        return value
    pattern = re.compile("|".join(re.escape(text) for text in sorted(replacements, key=len, reverse=True)))
    def replace(match):
        available = replacements[match.group()]
        return available.pop(0) if available else match.group()
    return pattern.sub(replace, value)



class FixtureTranslator:
    model = "offline-faithful-fixture"

    def __init__(self, replacements):
        self.replacements = replacements
        self.calls = []

    def translate(self, blocks):
        self.calls.append([block.id for block in blocks])
        return {block.id: {"text": self.replacements[block.text], "retainedEnglish": [], "reviewed": True} for block in blocks}

    def ocr(self, image_bytes):
        raise LocalizationError("RASTER_OCR_UNAVAILABLE")


def source_pdf(path, *, raster=False, pages=1, outline=False):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_text((40, 45), "Research report", fontsize=16)
        page.insert_textbox(fitz.Rect(40, 80, 535, 130), "Revenue may grow by 7.2% in Q3; investment is USD 347M.", fontsize=10)
        # Deliberately distinct table cells and vector borders.
        page.draw_rect(fitz.Rect(40, 180, 535, 245), color=(0, 0, 0))
        page.draw_line((300, 180), (300, 245), color=(0, 0, 0))
        page.insert_text((48, 200), "Demand", fontsize=9)
        page.insert_text((330, 200), "25%", fontsize=9)
        page.insert_text((48, 230), "Supply", fontsize=9)
        page.insert_text((330, 230), "USD 7M", fontsize=9)
        page.insert_text((40, 790), "Source: https://example.test/data/2026", fontsize=8)
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(40, 780, 350, 795), "uri": "https://example.test/data/2026"})
        page.insert_text((290, 820), str(index + 1), fontsize=8)
        if raster:
            image = Image.new("RGB", (600, 120), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((200, 50, 500, 100), fill="blue")
            draw.text((15, 15), "Demand 25%", fill="black")
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            page.insert_image(fitz.Rect(40, 300, 535, 399), stream=stream.getvalue())
    if outline:
        document.set_toc([[1, "Research report", 1], [1, "Demand", pages]])
    document.set_metadata({"title": "Research report", "author": "GateX"})
    document.save(path)
    document.close()


PAIRS = {
    "Research report": "研究报告",
    "Revenue may grow by 7.2% in Q3; investment is USD 347M.": "收入在Q3可能增长7.2%；投资为USD 347M。",
    "Demand": "需求", "Supply": "供应",
    "Source: https://example.test/data/2026": "来源：https://example.test/data/2026",
    "Demand 25%": "需求25%",
}


class PdfLocalizationTests(unittest.TestCase):
    def test_overlapping_retained_codes_consume_complete_longest_literals_in_any_order(self):
        source = "The path from HBM3E to HBM4, then HBM4E and HBM5, raises bandwidth and capacity."
        target = "从HBM3E到HBM4，再到HBM4E和HBM5的路径提升了带宽和容量。"
        identities = [{"text": code, "kind": "code"} for code in ("HBM3E", "HBM4", "HBM4E", "HBM5")]
        for retained in (identities, list(reversed(identities)), identities + identities):
            with self.subTest(retained=retained):
                self.assertEqual(unresolved_english(source, target, retained), 0)
                self.assertEqual(validate_translation(source, {"text": target, "retainedEnglish": retained, "reviewed": True})["text"], target)
        for extra in (" E", " FORECAST", " ordinary", " HBM4Else", " preHBM4", " HBM4_suffix"):
            with self.subTest(extra=extra):
                self.assertGreater(unresolved_english(source, target + extra, identities), 0)
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(source, {"text": target.replace("HBM4E", "HBM4"), "retainedEnglish": identities, "reviewed": True})
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            unresolved_english("FORECAST HBM4 HBM4E", "FORECAST HBM4 HBM4E", identities[1:3] + [{"text": "FORECAST", "kind": "brand"}])

    def test_month_year_hints_and_gate_require_complete_source_bound_chinese_format(self):
        source = "Version 1 appeared in January 2024; revised conditions followed in November 2025."
        hints = _source_month_year_hints(source)
        self.assertEqual([date["chineseDisplay"] for date in hints], ["2024年一月", "2025年十一月"])
        self.assertEqual(_source_month_year_hints("Demand may rise. We march onward. May demand."), [])
        self.assertEqual(_source_month_year_hints("24 September 2026"), [])
        good = "版本1于2024年一月发布；修订条件随后于2025年十一月发布。"
        self.assertEqual(validate_translation(source, {"text": good, "retainedEnglish": [], "reviewed": True})["text"], good)
        for bad in (good.replace("2024年", "2024"), good.replace("2025年", "2025"),
                    good.replace("2024年一月", "一月2024"), good.replace("一月", "二月"),
                    good.replace("2024年", "2023年")):
            with self.subTest(bad=bad), self.assertRaisesRegex(LocalizationError, "DATE_DISPLAY_ORDER"):
                validate_translation(source, {"text": bad, "retainedEnglish": [], "reviewed": True})
        with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
            validate_translation(source, {"text": good, "retainedEnglish": [], "reviewed": False})

    def test_month_year_retry_corrects_only_observed_format_then_requires_fresh_review(self):
        block = Block("month-year", 0, "Version 1 appeared in January 2024; revisions followed in November 2025.", None)
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, saved, rejected = [], [], []
        client.accepted_callback = saved.append
        client.rejection_callback = rejected.append
        def once(messages, **kwargs):
            calls.append(messages)
            item = json.loads(messages[1]["content"])["blocks"][0]
            if "source" in item:
                self.assertEqual(item["translation"], "版本1于2024年一月发布；修订随后于2025年十一月发布。")
                self.assertEqual(saved, [])
                self.assertEqual(len(messages), 2, "fresh review receives no failure history or approval hint")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
            self.assertEqual([date["chineseDisplay"] for date in item["sourceYearMonthFormats"]], ["2024年一月", "2025年十一月"])
            separator = "" if len(calls) == 1 else "年"
            if len(calls) > 1:
                feedback = json.loads(messages[2]["content"].split(": ", 1)[1])[0]
                self.assertEqual(feedback["validationError"], "DATE_DISPLAY_ORDER")
                self.assertEqual(feedback["sourceYearMonthFormats"], item["sourceYearMonthFormats"])
                self.assertIn("including 年", feedback["instruction"])
            return {"blocks": [{"id": block.id, "text": "版本{GATEX_QUANTITY_0}于{GATEX_QUANTITY_1}" + separator + "一月发布；修订随后于{GATEX_QUANTITY_2}" + separator + "十一月发布。", "retainedEnglish": []}]}
        client._once = once
        result = client.translate([block])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(saved, [result])

    def test_source_matching_line_initial_letter_markers_are_structural_only(self):
        for source, target in [("a) It may change.", "a) 它可能改变。"),
                               ("- b) It may change.", "- b) 它可能改变。"),
                               ("c) It may change.\nd) It may remain.", "c) 它可能改变。\nd) 它可能维持不变。")]:
            with self.subTest(source=source):
                self.assertEqual(validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})["text"], target)
        self.assertEqual(unresolved_english("- a) It may change.", "- a) 它可能改变。", [{"text": "a", "kind": "code"}]), 0)
        for source, target in [("a) It may change.", "b) 它可能改变。"),
                               ("a) It may change.", "a) 它可能改变。\na) 再次出现。"),
                               ("It may change.", "a) 它可能改变。"),
                               ("a) It may change.", "a) 它可能改变，a。"),
                               ("It has a parenthesis.", "它包含 a) 括号。")]:
            with self.subTest(source=source, target=target), self.assertRaises(LocalizationError):
                validate_translation(source, {"text": target, "retainedEnglish": [{"text": "a", "kind": "code"}], "reviewed": True})

    def test_source_folded_metadata_remains_literal_when_name_is_translated(self):
        source = "An agreement with North River\nSecurities Exchange."
        target = "与北河证券交易所签署了一份协议。"
        raw = {"text": target, "retainedEnglish": [{"text": "North River Securities Exchange", "kind": "organisation"}]}
        normalized = _source_verified_record(source, raw)
        self.assertEqual([e["text"] for e in normalized["retainedEnglish"]], ["North", "River", "Securities", "Exchange"])
        self.assertEqual(raw["retainedEnglish"][0]["text"], "North River Securities Exchange")
        with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
            validate_translation(source, normalized)
        self.assertEqual(validate_translation(source, {**normalized, "reviewed": True})["text"], target)
        for original in ("North river\nSecurities Exchange", "North Other River\nSecurities Exchange"):
            with self.subTest(original=original), self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
                validate_translation(original, {**_source_verified_record(original, raw), "reviewed": True})

    def test_publication_vocabulary_is_source_bound_and_not_military_renaming(self):
        hints = _source_publication_terms("This member edition packages a de-SPAC transaction for the GateX\nintelligence room.")
        terms = {h["sourceTerm"]: h["chineseTerm"] for h in hints}
        self.assertEqual(terms["GateX intelligence room"], "GateX 洞察工作区")
        self.assertEqual(terms["de-SPAC transaction"], "SPAC合并上市交易")
        self.assertEqual(_source_publication_terms("Military intelligence changed; a third party's Intelligence Room remained."), [])
        self.assertEqual(_source_publication_terms("Market Intelligence")[0]["chineseTerm"], "市场洞察")
        self.assertEqual(_source_publication_terms("Military market intelligence was assessed."), [])

    def test_translation_review_draft_binds_both_texts_and_current_source_cache(self):
        block = Block("editorial", 0, "This member edition preserves the source report.", None)
        cached = {"sourceHash": block.source_hash, "text": "本会员版保留来源报告。", "retainedEnglish": [], "reviewed": True}
        draft = {"schema": "gatex-pdf-translation-review-draft/v1", "sourceHash": block.source_hash,
                 "previousReviewedRecord": copy.deepcopy(cached), "previousReviewedTextSha256": digest(cached["text"]),
                 "candidate": {"text": "本会员版保留原报告。", "retainedEnglish": [], "reviewed": False},
                 "candidateTextSha256": digest("本会员版保留原报告。")}
        self.assertFalse(_translation_review_draft(block, draft, cached)["reviewed"])
        for field, value in (("sourceHash", "0" * 64), ("candidateTextSha256", "0" * 64), ("previousReviewedTextSha256", "0" * 64)):
            invalid = {**draft, field: value}
            with self.subTest(field=field), self.assertRaises(LocalizationError):
                _translation_review_draft(block, invalid, cached)
        with self.assertRaises(LocalizationError):
            _translation_review_draft(block, draft, {**cached, "text": "另一个旧译文。"})
        with self.assertRaises(LocalizationError):
            _translation_review_draft(block, {**draft, "candidate": {**draft["candidate"], "reviewed": True}}, cached)
        numeric = Block("amount", 0, "Revenue may be $15.", None)
        old = {"sourceHash": numeric.source_hash, "text": "收入可能为$15。", "retainedEnglish": [], "reviewed": True}
        bad = {**draft, "sourceHash": numeric.source_hash, "previousReviewedRecord": old,
               "previousReviewedTextSha256": digest(old["text"]), "candidateTextSha256": digest("收入可能为$16。"),
               "candidate": {"text": "收入可能为$16。", "retainedEnglish": [], "reviewed": False}}
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            _translation_review_draft(numeric, bad, old)

    def test_editorial_draft_first_request_is_fresh_review_and_other_cache_is_reused(self):
        changed = Block("editorial", 0, "Executive Reading", None)
        same = Block("same", 0, "Research report", None)
        previous = {"sourceHash": changed.source_hash, "text": "执行层阅读", "retainedEnglish": [], "reviewed": True}
        state = {"blocks": {changed.id: copy.deepcopy(previous), same.id: {"sourceHash": same.source_hash, "text": "研究报告", "retainedEnglish": [], "reviewed": True}},
                 "ocr": {"preserved": {"verified": True}}, "status": "ready", "artifactSha256": "f" * 64,
                 "translationReviewDrafts": {changed.id: {"schema": "gatex-pdf-translation-review-draft/v1", "sourceHash": changed.source_hash,
                     "previousReviewedRecord": previous, "previousReviewedTextSha256": digest(previous["text"]),
                     "candidateTextSha256": digest("管理层导读"), "candidate": {"text": "管理层导读", "retainedEnglish": [], "reviewed": False}}}}
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, checkpoints = [], []
        def once(messages, **kwargs):
            calls.append(messages)
            rows = json.loads(messages[1]["content"])["blocks"]
            self.assertEqual(rows[0]["source"], changed.text)
            self.assertEqual(rows[0]["translation"], "管理层导读")
            self.assertNotIn("previousReviewedRecord", rows[0])
            self.assertEqual(len(rows), 1)
            return {"blocks": [{"id": changed.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        result = _translate([changed, same], state, checkpoints.append, client)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result[changed.id]["reviewed"])
        self.assertEqual(result[same.id]["text"], "研究报告")
        self.assertEqual(state["translationReviewDrafts"], {})
        self.assertNotIn("artifactSha256", state)
        self.assertEqual(state["ocr"], {"preserved": {"verified": True}})

    def test_fresh_review_context_uses_only_immediate_same_page_source_and_candidates(self):
        first = Block("first", 0, "This edition is packaged for GateX", None)
        second = Block("second", 0, "intelligence room: Source Report.", None)
        other_page = Block("other-page", 1, "Other material.", None)
        metadata = Block("metadata", 0, "Metadata subject.", None, kind="metadata")
        records = {first.id: {"text": "本版整理后供GateX"}, second.id: {"text": "洞察工作区使用：《来源报告》。"}}
        context = _adjacent_review_context(second, [first, second, other_page, metadata], records)
        self.assertEqual(context, [{"position": "previous", "id": first.id, "source": first.text, "translation": records[first.id]["text"]}])
        self.assertEqual(_adjacent_review_context(metadata, [first, second, other_page, metadata], records), [])
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            rows = json.loads(messages[1]["content"])["blocks"]
            self.assertEqual(rows[1]["adjacentSourceContext"][0]["source"], first.text)
            self.assertEqual(rows[1]["adjacentSourceContext"][0]["translation"], records[first.id]["text"])
            return {"blocks": [{"id": row["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for row in rows]}
        client._once = once
        accepted = client.translate([first, second], review_drafts={key: {**row, "retainedEnglish": [], "reviewed": False} for key, row in records.items()}, context_blocks=[first, second, other_page, metadata])
        self.assertEqual(len(calls), 1, "independent fresh review only; no generation for a supplied source-bound draft")
        self.assertEqual(len(accepted), 2)

    def test_adjacent_context_cannot_supply_missing_quantities_or_borrow_a_name(self):
        amount = Block("amount", 0, "Revenue may be $15.", None)
        neighbour = Block("neighbour", 0, "Dr. Jones discussed costs.", None)
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls = []
        def missing(messages, **kwargs):
            calls.append(messages)
            self.assertNotIn("source", json.loads(messages[1]["content"])["blocks"][0], "invalid own-block quantities must never reach a reviewer")
            return {"blocks": [{"id": amount.id, "text": "收入可能出现。", "retainedEnglish": []}]}
        client._once = missing
        with self.assertRaisesRegex(LocalizationError, "QUANTITY_PLACEHOLDER_MISMATCH"):
            client.translate([amount], context_blocks=[amount, neighbour], context_records={neighbour.id: {"text": "琼斯博士讨论$15的成本。"}})
        self.assertEqual(len(calls), 3)
        calls.clear()
        def borrowed(messages, **kwargs):
            calls.append(messages)
            return {"blocks": [{"id": amount.id, "text": "Jones称收入可能为{GATEX_QUANTITY_0}。", "retainedEnglish": [{"text": "Jones", "kind": "person"}]}]}
        client._once = borrowed
        with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
            client.translate([amount], context_blocks=[amount, neighbour])
        self.assertEqual(len(calls), 3)

    def test_rejected_editorial_draft_keeps_false_verdict_and_bounded_regeneration(self):
        block = Block("conditional", 0, "Revenue may rise.", None)
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, rejected = [], []
        client.rejection_callback = rejected.append
        def once(messages, **kwargs):
            calls.append(messages)
            row = json.loads(messages[1]["content"])["blocks"][0]
            if "source" in row:
                self.assertEqual(len(messages), 2, "the independent reviewer never sees history or a requested verdict")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["may became definite"]}]}
            return {"blocks": [{"id": block.id, "text": "收入一定上升。", "retainedEnglish": []}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate([block], review_drafts={block.id: {"text": "收入一定上升。", "retainedEnglish": [], "reviewed": False}})
        self.assertEqual(len(calls), 5, "one draft review plus two full generation/fresh-review attempts")
        self.assertEqual(len(rejected), 3)

    def test_ocr_failures_checkpoint_only_bounded_private_source_image_evidence(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.pdf", Path(directory) / "zh.pdf"
            source_pdf(source, raster=True)
            sha = digest(source.read_bytes())
            with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-private-key"}):
                client = ModelClient(sleep=lambda seconds: None)
            calls, checkpoints = [], []
            def once(messages, **kwargs):
                calls.append(messages)
                return {"verified": False, "uncertainRegions": ["Small source marks cannot be identified."], "lines": []}
            client._once = once
            evidence = {"width": 1485, "height": 297, "words": [{"text": "25%", "confidence": 90, "bbox": [40, 40, 80, 60]}]}
            progress = {"schema": PROGRESS_SCHEMA, "sourceSha256": sha, "promptVersion": PROMPT_VERSION, "model": client.model,
                        "blocks": {}, "ocr": {}, "status": "in-progress", "custom": {"preserve": True}}
            with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence), self.assertRaisesRegex(LocalizationError, "^UNVERIFIED_RASTER_TEXT$"):
                translate_pdf(source, output, sha, progress, checkpoints.append, translation_client=client)
            state = checkpoints[-1]
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(state["ocrRejections"]), 2)
            self.assertEqual([r["attempt"] for r in state["ocrRejections"]], [2, 3])
            for row in state["ocrRejections"]:
                self.assertEqual(row["sourceSha256"], sha)
                self.assertEqual(row["sourcePage"], 1)
                self.assertRegex(row["imageSha256"], r"^[a-f0-9]{64}$")
                self.assertEqual(row["stage"], "full_image")
                self.assertFalse(row["rejectedResponse"]["verified"])
                self.assertEqual(row["evidence"], evidence)
                self.assertTrue(row["nativeVisibleWords"])
            self.assertNotIn("dummy-private-key", json.dumps(state))
            self.assertEqual(state["custom"], {"preserve": True})
            self.assertEqual(state["blocks"], {})
            self.assertEqual(state["ocr"], {})
            self.assertFalse(output.exists())

    def test_private_crop_rejection_captures_detector_and_raw_number_disagreement(self):
        image = Image.new("RGB", (100, 100), "white")
        stream = io.BytesIO(); image.save(stream, format="PNG")
        evidence = {"width": 100, "height": 100, "words": [{"text": "25%", "confidence": 90, "bbox": [40, 40, 60, 50]}]}
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, rejected = [], []
        client.ocr_rejection_callback = rejected.append
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"verified": True, "uncertainRegions": [], "lines": []}
            return {"verified": True, "uncertainRegions": [], "classification": "text", "lines": [{"text": "5%", "bbox": [0, 0, 1000, 1000]}]}
        client._once = once
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence), self.assertRaisesRegex(LocalizationError, "^RASTER_NUMERIC_OR_LINK_MISMATCH$"):
            client.ocr(stream.getvalue())
        self.assertEqual(len(calls), 4, "one full-image attempt and the unchanged three crop attempts")
        self.assertEqual(len(rejected), 3)
        self.assertEqual(rejected[-1]["detectorCandidate"], evidence["words"][0])
        self.assertEqual(rejected[-1]["stage"], "context_crop")
        self.assertEqual(rejected[-1]["rejectedResponse"]["lines"][0]["text"], "5%")
        self.assertEqual(len(rejected[-1]["cropBBox"]), 4)
        self.assertRegex(rejected[-1]["cropImageSha256"], r"^[a-f0-9]{64}$")

    def test_targeted_crop_sends_exact_focus_and_context_without_promoting_outer_text(self):
        image = Image.new("RGB", (300, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.text((80, 35), "Peripheral native heading", fill="blue")
        draw.rectangle((120, 90, 139, 109), fill="gray", outline="black")
        stream = io.BytesIO(); image.save(stream, format="PNG")
        detector = {"text": "q", "confidence": 86, "bbox": [120, 90, 140, 110]}
        evidence = {"width": 300, "height": 200, "words": [detector]}
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"verified": True, "uncertainRegions": [], "lines": []}
            self.assertEqual(messages[0]["content"], OCR_CONTEXT_REVIEW_SYSTEM)
            content = messages[1]["content"]
            request = json.loads(content[0]["text"])
            self.assertEqual(request["policy"], OCR_CONTEXT_REVIEW_POLICY)
            self.assertEqual(len(content), 3, "one tight view and one context, never a replacement source image")
            images = [Image.open(io.BytesIO(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))).convert("RGB") for part in content[1:]]
            self.assertEqual(images[0].size, (20, 20))
            self.assertEqual(images[0].tobytes(), image.crop(tuple(detector["bbox"])).tobytes())
            self.assertEqual(images[1].size, (140, 140))
            self.assertEqual(images[1].tobytes(), image.crop((60, 30, 200, 170)).tobytes())
            self.assertEqual(request["candidateBBoxInContext"], [60/140*1000, 60/140*1000, 80/140*1000, 80/140*1000])
            return {"verified": True, "classification": "non_text", "uncertainRegions": [], "lines": []}
        client._once = once
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            result = client.ocr(stream.getvalue())
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["lines"], [], "outer text cannot be fabricated into a candidate OCR line")
        checked = result["coverage"]["cropChecks"][0]
        self.assertEqual(checked["focusBBox"], detector["bbox"])
        self.assertEqual(checked["contextReviewPolicy"], OCR_CONTEXT_REVIEW_POLICY)
        self.assertEqual(checked["reviewPromptSha256"], digest(OCR_CONTEXT_REVIEW_SYSTEM))
        for key in ("focusImageSha256", "cropImageSha256"):
            self.assertRegex(checked[key], r"^[a-f0-9]{64}$")

    def test_targeted_crop_keeps_text_boundary_numeric_and_uncertainty_gates(self):
        image = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(image).text((40, 40), "25%", fill="black")
        stream = io.BytesIO(); image.save(stream, format="PNG")
        evidence = {"width": 100, "height": 100, "words": [{"text": "25%", "confidence": 90, "bbox": [40, 40, 60, 50]}]}
        # Context bounds are [10, 10, 90, 80]. A complete line may touch or
        # extend past the tight-view edge but must overlap the original box.
        cases = [
            ({"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "25%", "bbox": [350, 400, 650, 600]}]}, None),
            ({"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "5%", "bbox": [350, 400, 650, 600]}]}, "RASTER_NUMERIC_OR_LINK_MISMATCH"),
            ({"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "Peripheral heading", "bbox": [0, 0, 1000, 100]}]}, "RASTER_TEXT_COVERAGE_MISMATCH"),
            ({"verified": False, "classification": "non_text", "uncertainRegions": ["Candidate cannot be read"], "lines": []}, "UNVERIFIED_RASTER_TEXT"),
            ({"verified": True, "classification": "non_text", "uncertainRegions": [], "lines": [{"text": "25%", "bbox": [350, 400, 650, 600]}]}, "RASTER_TEXT_COVERAGE_MISMATCH"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
                    client = ModelClient(sleep=lambda seconds: None)
                calls, rejected = [], []
                client.ocr_rejection_callback = rejected.append
                def once(messages, **kwargs):
                    calls.append(messages)
                    return {"verified": True, "uncertainRegions": [], "lines": []} if len(calls) == 1 else copy.deepcopy(response)
                client._once = once
                with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
                    if code:
                        with self.assertRaisesRegex(LocalizationError, "^" + code + "$"):
                            client.ocr(stream.getvalue())
                    else:
                        result = client.ocr(stream.getvalue())
                        self.assertEqual(result["lines"][0]["text"], "25%")
                        self.assertEqual(result["lines"][0]["bbox"], [380.0, 380.0, 620.0, 520.0])
                self.assertEqual(len(calls), 4 if code else 2, "same original three-attempt budget")
                if code:
                    self.assertEqual(len(rejected), 3)
                    self.assertEqual(rejected[-1]["contextReviewPolicy"], OCR_CONTEXT_REVIEW_POLICY)
                    self.assertEqual(rejected[-1]["reviewPromptSha256"], digest(OCR_CONTEXT_REVIEW_SYSTEM))
                    self.assertEqual(rejected[-1]["focusBBox"], evidence["words"][0]["bbox"])
                    self.assertNotIn("data:image", json.dumps(rejected))

    def test_nonfinite_ocr_candidate_keeps_strict_checkpoint_and_three_attempts(self):
        from scripts.gatex_localization_runner import encoded_json
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.pdf", Path(directory) / "zh.pdf"
            source_pdf(source, raster=True)
            with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
                client = ModelClient(sleep=lambda seconds: None)
            calls, checkpoints = [], []
            def once(messages, **kwargs):
                calls.append(messages)
                return {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand", "bbox": [0, 0, float("nan"), 100]}]}
            def save(state):
                encoded_json(state)  # Use the real runner's allow_nan=False contract.
                checkpoints.append(state)
            client._once = once
            with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value={"width": 1485, "height": 297, "words": []}), self.assertRaisesRegex(LocalizationError, "^INVALID_RASTER_TEXT$"):
                translate_pdf(source, output, digest(source.read_bytes()), save_progress=save, translation_client=client)
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(checkpoints[-1]["ocrRejections"]), 2)
            self.assertEqual(checkpoints[-1]["ocrRejections"][-1]["rejectedResponse"]["lines"][0]["bbox"][2], {"invalidNonFiniteNumber": "nan"})
            self.assertEqual(checkpoints[-1]["ocr"], {})
            self.assertFalse(output.exists())

    def test_private_ocr_diagnostic_projection_is_bounded_and_marks_truncation(self):
        record = {"schema": "gatex-pdf-ocr-rejection/v1", "code": "UNVERIFIED_RASTER_TEXT", "stage": "full_image",
                  "imageSha256": "a" * 64, "evidence": {"width": 100, "height": 100,
                    "words": [{"text": "s" * 500, "bbox": [1, 2, 3, 4]} for _ in range(100)]},
                  "nativeVisibleWords": [{"text": "s" * 500} for _ in range(100)],
                  "rejectedResponse": {"text": "s" * 50000}, "detectorCandidate": {"text": "25%", "bbox": [1, 2, 3, 4]}}
        bounded = _private_ocr_evidence(record, maximum=4096)
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False, allow_nan=False).encode()), 4096)
        self.assertEqual(bounded["diagnosticTruncation"]["detectorWordCount"], 100)
        self.assertEqual(bounded["detectorCandidate"]["text"], "25%")
        self.assertEqual(record["rejectedResponse"]["text"], "s" * 50000, "diagnostic projection never changes validation input")

    def test_source_protected_model_codes_and_shape_letters_get_literal_evidence(self):
        pairs = [("HJ-12 stated static penetration", "HJ-12所述静态穿透力", "HJ-12"),
                 ("folding X-wing light loitering munition", "折叠X翼轻型巡飞弹药", "X"),
                 ("an L-shaped beam", "L形梁", "L"),
                 ("a T-shaped beam", "T型梁", "T"),
                 ("GPT-4.1 and 3PL", "GPT-4.1和3PL", "GPT-4.1")]
        for source, target, identity in pairs:
            with self.subTest(source=source):
                raw = {"text": target, "retainedEnglish": []}
                candidate = _source_verified_record(source, raw)
                self.assertIn({"text": identity, "kind": "code"}, candidate["retainedEnglish"])
                self.assertEqual(raw["retainedEnglish"], [])
                with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
                    validate_translation(source, candidate)
                self.assertEqual(validate_translation(source, {**candidate, "reviewed": True})["text"], target)

    def test_estimated_year_ranges_reconcile_only_complete_source_protected_codes(self):
        for form in ("2025E-2029E", "2026E-2028E", "2025E–2029E", "2025E to 2029E"):
            source = f"The {form} values remain forecasts."
            target = f"{form.replace(' to ', '至')}数值仍为预测值。"
            candidate = _source_verified_record(source, {"text": target, "retainedEnglish": []})
            self.assertGreaterEqual(len(candidate["retainedEnglish"]), 1)
            with self.subTest(form=form):
                self.assertEqual(validate_translation(source, {**candidate, "reviewed": True})["text"], target)
                with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
                    validate_translation(source, candidate)
        source = "The 2025E-2029E values remain forecasts."
        for target in ("2025E-2028E数值仍为预测值。", "2025E-2029E和2025E-2029E数值仍为预测值。",
                       "2025e-2029e数值仍为预测值。", "2025E-2029E数值仍为forecasts。",
                       "2025E-2029E数值 E 仍为预测值。"):
            with self.subTest(target=target), self.assertRaises(LocalizationError):
                validate_translation(source, {**_source_verified_record(source, {"text": target, "retainedEnglish": []}), "reviewed": True})
        self.assertEqual(_source_identity_hints("E and FORECAST remain ordinary text."), [])
        with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
            validate_translation("E is a letter.", {"text": "E是字母。", "retainedEnglish": [], "reviewed": True})

    def test_estimated_year_range_restores_then_receives_fresh_fidelity_review(self):
        block = Block("estimated-range", 0, "The 2025E-2029E values may change.", None)
        for approved in (True, False):
            with self.subTest(approved=approved):
                with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
                    client = ModelClient(sleep=lambda seconds: None)
                calls, saved = [], []
                client.accepted_callback = saved.append
                def once(messages, **kwargs):
                    calls.append(messages)
                    row = json.loads(messages[1]["content"])["blocks"][0]
                    if "source" not in row:
                        self.assertIn("{GATEX_QUANTITY_0}", row["text"])
                        return {"blocks": [{"id": block.id, "text": "{GATEX_QUANTITY_0}数值可能变化。", "retainedEnglish": []}]}
                    self.assertEqual(len(messages), 2, "fresh review never sees earlier verdicts")
                    self.assertEqual(row["source"], block.text)
                    self.assertEqual(row["translation"], "2025E-2029E数值可能变化。")
                    return {"blocks": [{"id": block.id, "complete": True, "faithful": approved,
                        "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [],
                        "issues": [] if approved else ["Fixture rejects source fidelity"]}]}
                client._once = once
                if approved:
                    result = client.translate([block])
                    self.assertIn({"text": "2025E-2029E", "kind": "code"}, result[block.id]["retainedEnglish"])
                    self.assertEqual((len(calls), len(saved)), (2, 1))
                else:
                    with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
                        client.translate([block])
                    self.assertEqual((len(calls), len(saved)), (6, 0), "three failed candidates never enter the factual checkpoint")

    def test_numeric_code_and_shape_hints_cannot_preserve_ordinary_prose_or_changed_codes(self):
        for source in ("FIGURE-1", "REPORT2026", "FORECAST-2026", "Figure-1", "Ordinary title"):
            with self.subTest(source=source):
                self.assertEqual(_source_identity_hints(source), [])
        pairs = [("HJ-12 model", "HJ-13型号"), ("HJ-12 model", "HJ-12和HJ-12型号"),
                 ("HJ-12 model", "hj-12型号"), ("no model identified", "HJ-12型号"),
                 ("X-wing munition", "X翼弹药，X"), ("X-wing munition", "Y翼弹药"),
                 ("X-wing munition", "X"), ("wing munition", "X翼弹药")]
        for source, target in pairs:
            with self.subTest(source=source, target=target), self.assertRaises(LocalizationError):
                validate_translation(source, {**_source_verified_record(source, {"text": target, "retainedEnglish": []}), "reviewed": True})

    def test_source_code_metadata_reconciliation_still_needs_independent_prose_review(self):
        blocks = [Block("weapon-code", 0, "HJ-12 may penetrate.", None),
                  Block("wing-code", 0, "X-wing munition may withdraw.", None)]
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        reviews, generations = [], []
        def once(messages, **kwargs):
            rows = json.loads(messages[1]["content"])["blocks"]
            if "source" in rows[0]:
                reviews.extend(rows)
                return {"blocks": [{"id": r["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for r in rows]}
            generations.extend(rows)
            return {"blocks": [{"id": "weapon-code", "text": "{GATEX_QUANTITY_0}可能穿透。", "retainedEnglish": []},
                               {"id": "wing-code", "text": "X翼弹药可能撤离。", "retainedEnglish": []}]}
        client._once = once
        accepted = client.translate(blocks)
        self.assertEqual((len(generations), len(reviews)), (2, 2))
        self.assertTrue(all(r["reviewed"] for r in accepted.values()))
        self.assertIn({"text": "HJ-12", "kind": "code"}, accepted["weapon-code"]["retainedEnglish"])
        self.assertIn({"text": "X", "kind": "code"}, accepted["wing-code"]["retainedEnglish"])
        generations.clear(); reviews.clear(); calls = []
        def reject(messages, **kwargs):
            rows = json.loads(messages[1]["content"])["blocks"]
            calls.append(messages)
            if "source" in rows[0]:
                return {"blocks": [{"id": "weapon-code", "complete": True, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["may became definite"]}]}
            return {"blocks": [{"id": "weapon-code", "text": "{GATEX_QUANTITY_0}肯定穿透。", "retainedEnglish": []}]}
        client._once = reject
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate(blocks[:1])
        self.assertEqual(len(calls), 6, "three full generation/review attempts; never a coerced approval")

    def test_complete_currency_placeholder_cannot_gain_a_separate_currency_name(self):
        block = Block("quoted-gold", 0, "Gold was above $5,400, rose $600, and was above $2,000 in 2022.", None)
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        generations, reviews, rejections = [], [], []
        client.rejection_callback = rejections.append
        def once(messages, **kwargs):
            row = json.loads(messages[1]["content"])["blocks"][0]
            if "source" in row:
                reviews.append(row)
                self.assertEqual(row["translation"], "黄金高于$5,400，上涨$600，2022年高于$2,000。")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
            generations.append(row)
            if len(generations) > 1:
                feedback = json.loads(messages[2]["content"].split(": ", 1)[1])[0]
                details = feedback["quantityValidationDetails"]
                self.assertEqual(details["sourceStandaloneCurrencies"], [])
                self.assertEqual(details["targetStandaloneCurrencies"], ["美元"])
                self.assertEqual(details["excessStandaloneCurrencyCount"], 1)
                self.assertEqual([r["sourceValue"] for r in details["sourceProtectedValues"]], ["$5,400", "$600", "$2,000", "2022"])
            text = "黄金高于{GATEX_QUANTITY_0}，上涨{GATEX_QUANTITY_1}，{GATEX_QUANTITY_3}年高于{GATEX_QUANTITY_2}" + ("。币种是美元。" if len(generations) == 1 else "。")
            return {"blocks": [{"id": block.id, "text": text, "retainedEnglish": []}]}
        client._once = once
        accepted = client.translate([block])
        self.assertEqual((len(generations), len(reviews), len(rejections)), (2, 1, 1))
        self.assertTrue(accepted[block.id]["reviewed"])
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(block.text, {"text": "黄金高于$5,400，上涨$600，2022年高于$2,000。币种是美元。", "retainedEnglish": [], "reviewed": True})

    def test_pdf_folded_brand_evidence_uses_exact_literal_parts_and_fresh_review(self):
        source, target = "Claude\nGov processed data.", "Claude Gov处理数据。"
        raw = {"text": target, "retainedEnglish": [{"text": "Claude Gov", "kind": "brand"}]}
        record = _source_verified_record(source, raw)
        self.assertEqual(record["text"], target)
        self.assertEqual(record["retainedEnglish"], [{"text": "Claude", "kind": "brand"}, {"text": "Gov", "kind": "brand"}])
        self.assertEqual(raw["retainedEnglish"], [{"text": "Claude Gov", "kind": "brand"}], "raw private evidence is not rewritten")
        with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
            validate_translation(source, record)
        self.assertEqual(validate_translation(source, {**record, "reviewed": True})["text"], target)
        for original in ("Claude GOV processed data.", "Claude Other Gov processed data.", "NewClaude\nGov processed data."):
            with self.subTest(source=original), self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
                validate_translation(original, {**_source_verified_record(original, raw), "reviewed": True})
        prose = {"text": "Revenue Growth", "retainedEnglish": [{"text": "Revenue Growth", "kind": "brand"}]}
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            validate_translation("Revenue\nGrowth", {**_source_verified_record("Revenue\nGrowth", prose), "reviewed": True})

    def test_source_absent_unit_abbreviation_is_translated_not_whitelisted(self):
        block = Block("unit", 0, "Petabyte-scale data.", None)
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        generations, reviews = [], []
        def once(messages, **kwargs):
            row = json.loads(messages[1]["content"])["blocks"][0]
            if "source" in row:
                reviews.append(row)
                self.assertEqual(row["translation"], "拍字节规模数据。")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
            generations.append(row)
            if len(generations) > 1:
                feedback = json.loads(messages[2]["content"].split(": ", 1)[1])[0]
                self.assertEqual(feedback["unresolvedTokens"], ["PB"])
            return {"blocks": [{"id": block.id, "text": "PB规模数据。" if len(generations) == 1 else "拍字节规模数据。", "retainedEnglish": []}]}
        client._once = once
        self.assertTrue(client.translate([block])[block.id]["reviewed"])
        self.assertEqual((len(generations), len(reviews)), (2, 1))
        with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
            validate_translation(block.text, {"text": "PB规模数据。", "retainedEnglish": [], "reviewed": True})

    def test_source_page_range_feedback_preserves_strict_quantity_gate(self):
        source = "Source report, pp. 1-6."
        hint = _source_page_reference_hints(source)
        self.assertEqual(hint[0]["chineseReference"], "1-6页")
        self.assertEqual(_source_page_reference_hints("Source reports 1-6; stages 1-6."), [])
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(source, {"text": "来源报告，第1-6页。", "retainedEnglish": [], "reviewed": True})
        self.assertEqual(validate_translation(source, {"text": "来源报告，1-6页。", "retainedEnglish": [], "reviewed": True})["text"], "来源报告，1-6页。")
        for target in ("来源报告，1-7页。", "来源报告，6-1页。"):
            with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
                validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})

    def test_real_shape_page_retry_and_folded_brand_still_receive_fresh_review(self):
        blocks = [Block("citation", 0, "Source report, pp. 1-6.", None),
                  Block("brand", 0, "Claude\nGov processed data.", None)]
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        generated, reviewed, rejected = [], [], []
        client.rejection_callback = rejected.append
        def once(messages, **kwargs):
            rows = json.loads(messages[1]["content"])["blocks"]
            if "source" in rows[0]:
                reviewed.extend(rows)
                return {"blocks": [{"id": r["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for r in rows]}
            generated.append(rows)
            output = []
            for row in rows:
                if row["id"] == "brand":
                    output.append({"id": "brand", "text": "Claude Gov处理数据。", "retainedEnglish": [{"text": "Claude Gov", "kind": "brand"}]})
                else:
                    if len(generated) > 1:
                        feedback = json.loads(messages[2]["content"].split(": ", 1)[1])[0]
                        self.assertEqual(feedback["sourcePageReferences"][0]["chineseReference"], "1-6页")
                    prefix = "第" if len(generated) == 1 else ""
                    output.append({"id": "citation", "text": "来源报告，" + prefix + "{GATEX_QUANTITY_0}页。", "retainedEnglish": []})
            return {"blocks": output}
        client._once = once
        accepted = client.translate(blocks)
        self.assertEqual([len(rows) for rows in generated], [2, 1], "accepted brand does not get regenerated")
        self.assertEqual({r["id"] for r in reviewed}, {"brand", "citation"})
        self.assertEqual(len(reviewed), 2)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["code"], "NUMERIC_OR_LINK_MISMATCH")
        self.assertTrue(all(r["reviewed"] for r in accepted.values()))

    def test_calendar_hints_require_a_literal_source_date_context(self):
        self.assertEqual(_source_calendar_hints("Demand may rise. May require approval. We march onward."), [])
        self.assertEqual([h["chineseMonth"] for h in _source_calendar_hints("9 March 2026; February 28; May 2026")], ["二月", "三月", "五月"])
        self.assertEqual(_source_calendar_hints("May demand outlook; March production outlook"), [])

    def test_quantity_feedback_reports_exact_missing_duplicate_and_unknown_ids(self):
        protected = protect_quantities("Revenue 120 million; dated 9 March 2026.")
        problems = _quantity_placeholder_errors(protected, "{GATEX_QUANTITY_0} {GATEX_QUANTITY_0} {GATEX_QUANTITY_3}")
        self.assertEqual(problems["duplicatePlaceholders"], {"{GATEX_QUANTITY_0}": 1})
        self.assertEqual(problems["missingPlaceholders"], {"{GATEX_QUANTITY_1}": 1, "{GATEX_QUANTITY_2}": 1})
        self.assertEqual(problems["unknownPlaceholders"], {"{GATEX_QUANTITY_3}": 1})

    def test_written_month_cannot_invent_quantity_and_receives_specific_retry_feedback(self):
        block = Block("footnote-date", 0, "1. Source report, 9 March 2026, pages 1-6.", (40, 50, 500, 90))
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, rejections = [], []
        client.rejection_callback = rejections.append
        def once(messages, **kwargs):
            item = json.loads(messages[1]["content"])["blocks"][0]
            calls.append(messages)
            if "source" in item:
                self.assertEqual(item["translation"], "1. 来源报告，2026年三月9日，页码1-6。")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
            self.assertEqual(item["sourceCalendarMonths"][0]["chineseMonth"], "三月")
            if len(calls) == 1:
                text = "{GATEX_QUANTITY_0}. 来源报告，{GATEX_QUANTITY_1}年{GATEX_QUANTITY_2}月{GATEX_QUANTITY_3}日，页码{GATEX_QUANTITY_4}。"
            else:
                feedback = json.loads(messages[2]["content"].split(": ", 1)[1])[0]
                self.assertEqual(feedback["quantityPlaceholderErrors"]["unknownPlaceholders"], {"{GATEX_QUANTITY_4}": 1})
                text = "{GATEX_QUANTITY_0}. 来源报告，{GATEX_QUANTITY_2}年三月{GATEX_QUANTITY_1}日，页码{GATEX_QUANTITY_3}。"
            return {"blocks": [{"id": block.id, "text": text, "retainedEnglish": []}]}
        client._once = once
        accepted = client.translate([block])
        self.assertEqual(len(calls), 3, "one invalid generation, one corrected generation and one independent review")
        self.assertEqual(len(rejections), 1)
        self.assertTrue(accepted[block.id]["reviewed"])
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(block.text, {"text": "1. 来源报告，2026年四月10日，页码1-6。", "retainedEnglish": [], "reviewed": True})

    def date_draft_fixture(self):
        block = Block("dated-edition", 0, "The English edition was published on 24 September 2026.", (40, 50, 500, 90))
        previous = {"sourceHash": block.source_hash, "text": "原英文版于24 九月 2026发布。", "retainedEnglish": [], "reviewed": True}
        draft = {"schema": "gatex-pdf-date-review-draft/v1", "sourceHash": block.source_hash,
                 "previousReviewedTextSha256": digest(previous["text"]), "previousReviewedRecord": previous}
        return block, draft

    def test_date_migration_changes_only_format_and_never_supplies_approval(self):
        block, draft = self.date_draft_fixture()
        candidate = _date_review_draft(block, draft)
        self.assertEqual(candidate, {"text": "原英文版于2026年九月24日发布。", "retainedEnglish": [], "reviewed": False})
        with self.assertRaisesRegex(LocalizationError, "UNREVIEWED_TRANSLATION"):
            validate_translation(block.text, candidate)
        for field, value in (("sourceHash", "f" * 64), ("previousReviewedTextSha256", "0" * 64)):
            with self.assertRaises(LocalizationError):
                _date_review_draft(block, {**draft, field: value})
        for field, value in (("reviewed", False), ("sourceHash", "f" * 64), ("text", "原英文版于25 九月 2026发布。")):
            altered = copy.deepcopy(draft)
            altered["previousReviewedRecord"][field] = value
            altered["previousReviewedTextSha256"] = digest(altered["previousReviewedRecord"]["text"])
            with self.assertRaises(LocalizationError):
                _date_review_draft(block, altered)
        normalized = copy.deepcopy(draft)
        normalized["previousReviewedRecord"]["text"] = candidate["text"]
        normalized["previousReviewedTextSha256"] = digest(candidate["text"])
        with self.assertRaisesRegex(LocalizationError, "DATE_REVIEW_DRAFT_HAS_NO_FORMAT_CHANGE"):
            _date_review_draft(block, normalized)

    def test_date_draft_receives_fresh_review_without_regenerating_approved_paragraph(self):
        block, draft = self.date_draft_fixture()
        state = {"blocks": {block.id: {"sourceHash": block.source_hash, "text": "上游原文于2026年九月24日发布。", "retainedEnglish": [], "reviewed": True}},
                 "dateReviewDrafts": {block.id: draft}, "ocr": {"unchanged": {"verified": True}}, "status": "complete", "artifactSha256": "a" * 64}
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls, saved = [], []
        def once(messages, **kwargs):
            item = json.loads(messages[1]["content"])["blocks"][0]
            self.assertEqual(item["source"], block.text, "a formatting migration makes no generation request")
            self.assertEqual(item["translation"], "原英文版于2026年九月24日发布。")
            calls.append(messages)
            return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        records = _translate([block], state, lambda s: saved.append(copy.deepcopy(s)), client)
        self.assertEqual(len(calls), 1)
        self.assertTrue(records[block.id]["reviewed"])
        self.assertEqual(state["blocks"][block.id]["text"], "原英文版于2026年九月24日发布。")
        self.assertEqual(state["dateReviewDrafts"], {})
        self.assertEqual(state["ocr"], {"unchanged": {"verified": True}})
        self.assertNotIn("artifactSha256", state)
        self.assertTrue(all(s["status"] == "in-progress" for s in saved))

    def test_negative_date_draft_review_keeps_independence_and_three_total_attempts(self):
        block, draft = self.date_draft_fixture()
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        generation, review, rejected, accepted = [], [], [], []
        client.rejection_callback = rejected.append
        client.accepted_callback = accepted.append
        def once(messages, **kwargs):
            item = json.loads(messages[1]["content"])["blocks"][0]
            if "source" in item:
                review.append(messages)
                self.assertEqual(len(messages), 2, "fresh review does not see previous verdicts")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["Check the edition attribution."]}]}
            generation.append(messages)
            self.assertIn("原英文版于2026年九月24日发布。", messages[-1]["content"])
            return {"blocks": [{"id": block.id, "text": item["text"].replace("The English edition was published on ", "原英文版于").replace("September", "九月").replace(".", "发布。"), "retainedEnglish": []}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate([block], review_drafts={block.id: _date_review_draft(block, draft)})
        self.assertEqual((len(generation), len(review), len(rejected), len(accepted)), (2, 3, 3, 0))
        self.assertEqual(rejected[0]["normalizedCandidate"]["text"], "原英文版于2026年九月24日发布。")

    def test_fractional_raster_box_is_in_page_without_float32_rect_equality(self):
        page = fitz.Rect(0, 0, 595.2760009765625, 841.8900146484375)
        rect = fitz.Rect(38.69294006347656, 87.5565615234375, 110.72133618164062, 102.71058178710938)
        self.assertNotEqual(rect & page, rect, "the real OCR shape exposes float32 intersection rounding")
        self.assertIsNotNone(_writer(rect, "前沿洞察", _font(), 7, page))
        self.assertFalse(_contained(fitz.Rect(0, 0, page.width + 1, 20), page))
        self.assertIsNone(_writer(fitz.Rect(0, 0, page.width + 1, 20), "前沿洞察", _font(), 7, page))

    def test_short_margin_label_keeps_original_location_and_seven_point_minimum(self):
        font, page = _font(), fitz.Rect(0, 0, 595.276, 841.89)
        block = Block("header", 0, "TECHNOLOGY FRONTIERS", (442.8229, 30.79396, 547.27893, 38.79396), font_size=8)
        writer = _block_writer(block, "科技前沿", font, page, [])
        self.assertIsNotNone(writer)
        with fitz.open() as document:
            drawn = document.new_page(width=page.width, height=page.height)
            writer.write_text(drawn)
            span = drawn.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
            self.assertEqual(span["size"], 7)
            self.assertLess(fitz.Rect(span["bbox"]).height, 10)
            self.assertAlmostEqual(span["bbox"][0], block.bbox[0], delta=1)
            self.assertAlmostEqual(span["bbox"][1], block.bbox[1], delta=1)
        # Occupied neighbouring text forbids expansion, even for a header.
        self.assertIsNone(_block_writer(block, "科技前沿", font, page,
            [(440, 38.8, 550, 50), (440, 20, 550, 30.79)]))
        # A true long paragraph still requires complete continuation pages.
        self.assertIsNone(_block_writer(block, "完整正文" * 100, font, page, []))

    def test_rough_ocr_native_alias_is_source_target_exact_unique_and_local(self):
        native = Block("native", 0, "TECHNOLOGY FRONTIERS", (48, 180.24246, 179.12, 190.24246))
        raster = Block("raster", 0, native.text, (38.69294, 165.01044, 183.34501, 181.00635), kind="raster")
        rows = {native.id: {"text": "科技前沿"}, raster.id: {"text": "科技前沿"}}
        self.assertEqual(_raster_render_aliases([native, raster], rows), {"raster": "native"})
        far = copy.deepcopy(raster)
        far.bbox = (38.69294, 300, 183.34501, 316)
        self.assertEqual(_raster_render_aliases([native, far], rows), {})
        adjacent = copy.deepcopy(raster)
        adjacent.bbox = (48, 190.3, 179.12, 200.3)
        self.assertEqual(_raster_render_aliases([native, adjacent], rows), {}, "a nearby distinct label has no shared source glyph area")
        rows[raster.id]["text"] = "前沿科技"
        self.assertEqual(_raster_render_aliases([native, raster], rows), {})
        rows[raster.id]["text"] = "科技前沿"
        hidden = copy.deepcopy(native)
        hidden.hidden = True
        self.assertEqual(_raster_render_aliases([hidden, raster], rows), {})
        duplicate = copy.deepcopy(native)
        duplicate.id = "second-native"
        rows[duplicate.id] = rows[native.id]
        self.assertEqual(_raster_render_aliases([native, duplicate, raster], rows), {})
        second = copy.deepcopy(raster)
        second.id = "second-raster"
        rows[second.id] = rows[raster.id]
        self.assertEqual(_raster_render_aliases([native, raster, second], rows), {})

    def test_source_metadata_publication_and_standalone_date_are_unambiguous(self):
        source = "Unabridged English translation - GateX Technology Frontiers"
        with self.assertRaisesRegex(LocalizationError, "SOURCE_EDITION_REFERENCE_MISSING"):
            validate_translation(source, {"text": "未删节英文翻译 - GateX 科技前沿", "reviewed": True})
        self.assertEqual(validate_translation(source, {"text": "原英文版本为未删节英文译本 - GateX 科技前沿", "reviewed": True})["text"],
                         "原英文版本为未删节英文译本 - GateX 科技前沿")
        with self.assertRaisesRegex(LocalizationError, "SOURCE_PUBLICATION_IDENTITY_MISSING"):
            validate_translation("Unsolved Problems", {"text": "《未解问题》", "reviewed": True})
        self.assertEqual(validate_translation("Unsolved Problems", {"text": "Unsolved Problems", "retainedEnglish": [
            {"text": "Unsolved Problems", "kind": "organisation"}], "reviewed": True})["text"], "Unsolved Problems")
        source = "24 September 2026"
        with self.assertRaisesRegex(LocalizationError, "DATE_DISPLAY_ORDER"):
            validate_translation(source, {"text": "24 九月 2026", "reviewed": True})
        normalized = _source_verified_record(source, {"text": "24 九月 2026", "reviewed": False})
        self.assertEqual(normalized["text"], "2026年九月24日")
        self.assertFalse(normalized["reviewed"], "date reordering never supplies semantic approval")
        self.assertEqual(validate_translation(source, {**normalized, "reviewed": True})["text"], "2026年九月24日")

    def test_protected_date_inside_paragraph_is_reordered_before_fresh_review(self):
        source = "The material was published on 24 September 2026; a revision followed on 7 October 2026."
        record = {"text": "材料于24 九月 2026发布，修订版于7 十月 2026发布。", "retainedEnglish": [], "reviewed": False}
        with self.assertRaisesRegex(LocalizationError, "DATE_DISPLAY_ORDER"):
            validate_translation(source, {**record, "reviewed": True})
        fixed = _source_verified_record(source, record)
        self.assertEqual(fixed["text"], "材料于2026年九月24日发布，修订版于2026年十月7日发布。")
        self.assertFalse(fixed["reviewed"])
        validate_translation(source, {**fixed, "reviewed": True})
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation(source, {**fixed, "text": fixed["text"].replace("24日", "25日"), "reviewed": True})
        foreign = _source_verified_record("The next review is in 2026.", record)
        self.assertEqual(foreign["text"], record["text"], "a date without exact source evidence cannot be reformatted")

    def test_independent_review_receives_natural_restored_paragraph_date(self):
        block = Block("dated-paragraph", 0, "The material was published on 24 September 2026.", (40, 50, 500, 80))
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            client = ModelClient(sleep=lambda seconds: None)
        calls = []
        def once(messages, **kwargs):
            supplied = json.loads(messages[1]["content"])["blocks"][0]
            calls.append(supplied)
            if "source" in supplied:
                self.assertEqual(supplied["source"], block.text)
                self.assertEqual(supplied["translation"], "材料发布于2026年九月24日。")
                return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
            return {"blocks": [{"id": block.id, "text": supplied["text"].replace("The material was published on ", "材料发布于").replace("September", "九月").replace(".", "。"), "retainedEnglish": []}]}
        client._once = once
        result = client.translate([block])
        self.assertEqual(len(calls), 2)
        self.assertTrue(result[block.id]["reviewed"])
        self.assertEqual(result[block.id]["text"], "材料发布于2026年九月24日。")

    def run_fixture(self, directory, replacements=None, **kwargs):
        source, output = Path(directory) / "en.pdf", Path(directory) / "zh.pdf"
        source_pdf(source, raster=kwargs.pop("raster", False), pages=kwargs.pop("pages", 1), outline=kwargs.pop("outline", False))
        translator = kwargs.pop("translation_client", FixtureTranslator(replacements or PAIRS))
        result = translate_pdf(source, output, digest(source.read_bytes()), translation_client=translator, **kwargs)
        return source, output, result, translator

    def test_rejected_records_are_checkpointed_privately_without_an_artifact_or_public_source_text(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "en.pdf", Path(directory) / "zh.pdf"
            source_pdf(source)
            states, calls = [], []
            with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
                client = ModelClient(sleep=lambda seconds: None)
            def once(messages, **kwargs):
                calls.append(messages)
                supplied = json.loads(messages[1]["content"])["blocks"]
                return {"blocks": [{"id": item["id"], "text": fixture_generated_text(item, PAIRS[fixture_source_text(item)]),
                    "retainedEnglish": [{"text": "Invented organisation", "kind": "organisation"}]} for item in supplied]}
            client._once = once
            with self.assertRaises(LocalizationError) as caught:
                translate_pdf(source, output, digest(source.read_bytes()), translation_client=client, save_progress=states.append)
            self.assertEqual(str(caught.exception), "TRANSLATION_BLOCKS_PENDING")
            self.assertTrue(states[-1]["translationFailures"])
            self.assertEqual(set(row["code"] for row in states[-1]["translationFailures"].values()), {"INVALID_RETAINED_IDENTITY"})
            self.assertNotIn("Invented", str(caught.exception))
            self.assertEqual(len(calls), 3)
            self.assertFalse(output.exists())
            self.assertEqual(states[-1]["sourceSha256"], digest(source.read_bytes()))
            rejections = states[-1]["modelRejections"]
            self.assertEqual(len(rejections), 3)
            self.assertEqual(rejections[-1]["record"]["retainedEnglish"][0]["text"], "Invented organisation")
            self.assertEqual(rejections[-1]["sourceHash"], digest(rejections[-1]["source"]))
            self.assertEqual(rejections[-1]["code"], "INVALID_RETAINED_IDENTITY")
            self.assertIn(rejections[-1]["blockId"], calls[1][-1]["content"])

    def test_complete_native_table_footer_link_and_figures(self):
        with TemporaryDirectory() as directory:
            source, output, result, _ = self.run_fixture(directory, raster=True,
                raster_ocr=lambda data: {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [20, 80, 200, 270]}]})
            qa = result["qa"]
            self.assertTrue(qa["passed"])
            self.assertEqual(qa["sourceBlockCount"], qa["translatedBlockCount"])
            self.assertEqual(qa["missingBlocks"], 0)
            self.assertEqual(qa["pages"][0]["rasterLabelCount"], 1)
            with fitz.open(source) as original, fitz.open(output) as translated:
                self.assertFalse(_image_hashes(original, original[0]) - _image_hashes(translated, translated[0]))
                self.assertGreaterEqual(len(translated[0].get_drawings()), len(original[0].get_drawings()))
                text = "".join(page.get_text() for page in translated)
                for word in ("研究报告", "需求", "供应", "来源", "347M", "7.2%", "Q3", "25%", "7M"):
                    self.assertIn(word, text)
                self.assertNotIn("Research report", text)
                self.assertNotIn("Revenue may", text)
                self.assertEqual([link["uri"] for link in translated[0].get_links() if link["kind"] == fitz.LINK_URI], ["https://example.test/data/2026"])
                self.assertEqual(translated.metadata["author"], "GateX")
                self.assertEqual(translated.metadata["title"], "研究报告")
                for page in translated:
                    for block in page.get_text("dict")["blocks"]:
                        for line in block.get("lines", []):
                            for span in line["spans"]:
                                self.assertGreaterEqual(span["size"], 7)

    def test_overflow_is_complete_exclusive_mapped_continuation(self):
        pairs = {**PAIRS, "Research report": "研究报告" + "完整研究内容必须逐项保留。" * 200}
        with TemporaryDirectory() as directory:
            _, output, result, _ = self.run_fixture(directory, pairs, pages=2, outline=True)
            qa = result["qa"]
            self.assertGreater(result["pageCount"], 2)
            mapped = [p for page in qa["pages"] for p in page["targetPages"]]
            self.assertEqual(sorted(mapped), list(range(1, result["pageCount"] + 1)))
            self.assertEqual(len(mapped), len(set(mapped)))
            self.assertTrue(all(page["continuationBlockCount"] > 0 for page in qa["pages"]))
            self.assertTrue(all(page["continuationReferencesPlaced"] > 0 for page in qa["pages"]))
            with fitz.open(output) as document:
                text = "".join(page.get_text() for page in document)
                self.assertIn("原文第 1 页", text)
                self.assertIn("原文第 2 页", text)
                self.assertIn("原文段落", text)
                self.assertIn("中文见续页", text)
                self.assertEqual(document.get_toc()[1][2], qa["pages"][1]["targetPages"][0])
                # Every source paragraph repeated in the fixture is complete,
                # and every outline title is translated separately.
                for mapping in qa["pages"]:
                    for target in mapping["targetPages"][1:]:
                        self.assertIn(f"原文第 {mapping['sourcePage']} 页", document[target - 1].get_text())
                body = re.sub(r"原文第 \d+ 页 · 中文续页\n|续页 \d+\n", "", text).replace("\n", "")
                self.assertEqual(body.count("完整研究内容必须逐项保留。"), 400)

    def test_resume_reuses_reviewed_source_bound_blocks(self):
        with TemporaryDirectory() as directory:
            states = []
            source, output, first, translator = self.run_fixture(directory, save_progress=lambda state: states.append(state))
            self.assertGreater(len(translator.calls), 0)
            self.assertEqual(states[-1]["status"], "complete")
            second_translator = FixtureTranslator(PAIRS)
            second = translate_pdf(source, output, digest(source.read_bytes()), progress=states[-1],
                translation_client=second_translator)
            self.assertEqual(second_translator.calls, [])
            self.assertEqual(first["qa"]["sourceBlockCount"], second["qa"]["sourceBlockCount"])

    def test_progress_cannot_be_reused_for_changed_source(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "en.pdf"
            source_pdf(source)
            with self.assertRaisesRegex(LocalizationError, "PROGRESS_SOURCE_MISMATCH"):
                translate_pdf(source, Path(directory) / "zh.pdf", digest(source.read_bytes()),
                    progress={"schema": PROGRESS_SCHEMA, "sourceSha256": "0" * 64}, translation_client=FixtureTranslator(PAIRS))

    def test_numeric_adjacency_cjk_quarter_and_currency_contract(self):
        source = "Revenue 7.2% in Q3; USD 347M; 20.9 million; $184.4 billion."
        target = "收入7.2%，Q3；USD 347M；20.9百万；$184.4十亿美元。"
        self.assertEqual(tokens(source), tokens(target))
        self.assertNotEqual(tokens(source), tokens(target.replace("Q3", "Q4")))
        self.assertNotEqual(tokens(source), tokens(target.replace("347M", "347B")))
        self.assertNotEqual(tokens(source), tokens(target.replace("20.9", "2090")))
        self.assertEqual(tokens("Source: https://example.test/2026."), tokens("来源：https://example.test/2026。"))

    def test_rotated_original_page_preserves_rotation_and_all_text(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "en.pdf"
            source_pdf(source)
            with fitz.open(source) as document:
                document[0].set_rotation(90)
                document.save(Path(directory) / "rotated.pdf")
            rotated, output = Path(directory) / "rotated.pdf", Path(directory) / "zh.pdf"
            result = translate_pdf(rotated, output, digest(rotated.read_bytes()), translation_client=FixtureTranslator(PAIRS))
            self.assertTrue(result["qa"]["passed"])
            with fitz.open(output) as document:
                self.assertEqual(document[0].rotation, 90)
                self.assertIn("研究报告", document[0].get_text())
                self.assertIn("供应", document[0].get_text())

    def test_numeric_loss_fails_and_preserves_checkpoint(self):
        pairs = {**PAIRS, "Revenue may grow by 7.2% in Q3; investment is USD 347M.": "收入在Q3可能增长；投资为USD 347M。"}
        with TemporaryDirectory() as directory:
            states = []
            source = Path(directory) / "en.pdf"
            source_pdf(source)
            with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
                translate_pdf(source, Path(directory) / "zh.pdf", digest(source.read_bytes()),
                    translation_client=FixtureTranslator(pairs), save_progress=lambda state: states.append(state))
            self.assertEqual(states[-1]["status"], "in-progress")
            self.assertFalse((Path(directory) / "zh.pdf").exists())
            self.assertFalse((Path(directory) / "zh.pdf.partial").exists())

    def test_ordinary_english_is_not_an_acronym_escape(self):
        self.assertGreater(unresolved_english("FORECAST", "FORECAST"), 0)
        self.assertGreater(unresolved_english("FOREWORD", "FOREWORD"), 0)
        self.assertGreater(unresolved_english("SOMETHING", "SOMETHING"), 0)
        self.assertEqual(unresolved_english("AI GDP Q3 GateX", "AI GDP Q3 GateX"), 0)
        self.assertEqual(unresolved_english("NVIDIA", "NVIDIA", [{"text": "NVIDIA", "kind": "brand"}]), 0)
        self.assertEqual(unresolved_english("Bank of America", "Bank of America", [{"text": "Bank of America", "kind": "organisation"}]), 0)
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            unresolved_english("Key findings", "Key findings", [{"text": "Key findings", "kind": "brand"}])

    def test_raster_cannot_pass_without_verbatim_verification(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "en.pdf"
            source_pdf(source, raster=True)
            with self.assertRaisesRegex(LocalizationError, "UNVERIFIED_RASTER_TEXT"):
                translate_pdf(source, Path(directory) / "zh.pdf", digest(source.read_bytes()),
                    translation_client=FixtureTranslator(PAIRS), raster_ocr=lambda data: {"verified": False, "uncertainRegions": ["small text"], "lines": []})

    def test_raster_checkpoint_reuses_verified_image_hash(self):
        with TemporaryDirectory() as directory:
            states, ocr_calls = [], []
            def ocr(data):
                ocr_calls.append(digest(data))
                return {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [20, 80, 200, 270]}]}
            source, output, _, _ = self.run_fixture(directory, raster=True, raster_ocr=ocr, save_progress=states.append)
            translate_pdf(source, output, digest(source.read_bytes()), progress=states[-1],
                translation_client=FixtureTranslator(PAIRS), raster_ocr=ocr)
            self.assertEqual(len(ocr_calls), 1)

    def test_translation_policy_change_reuses_verified_source_image_ocr_but_regenerates_text(self):
        with TemporaryDirectory() as directory:
            old_states = []
            def verified_ocr(data):
                return {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [20, 80, 200, 270]}],
                        "coverage": {"schema": OCR_POLICY_VERSION}}
            source, output, _, _ = self.run_fixture(directory, raster=True, raster_ocr=verified_ocr, save_progress=old_states.append)
            old = old_states[-1]
            old.update({"promptVersion": "old-literal-digit-policy", "model": "old-translation-model", "unknownPrivateField": {"keep": True}})
            snapshot = copy.deepcopy(old)
            with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
                client = ModelClient(sleep=lambda seconds: None)
            calls, states = [], []
            def once(payload, **kwargs):
                calls.append(payload)
                supplied = json.loads(payload[1]["content"])["blocks"]
                if "source" in supplied[0]:
                    return {"blocks": [{"id": item["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for item in supplied]}
                return {"blocks": [{"id": item["id"], "text": fixture_generated_text(item, PAIRS[fixture_source_text(item)]),
                                   "retainedEnglish": []} for item in supplied]}
            client._once = once
            client.ocr = lambda *args, **kwargs: self.fail("translation-policy migration must not repeat verified OCR")
            result = translate_pdf(source, output, digest(source.read_bytes()), progress=old,
                                   translation_client=client, save_progress=states.append)
            self.assertTrue(result["qa"]["passed"])
            self.assertTrue(calls, "old translated blocks must be regenerated and independently reviewed")
            self.assertEqual(states[0]["ocr"], snapshot["ocr"])
            self.assertEqual(states[0]["unknownPrivateField"], snapshot["unknownPrivateField"])
            self.assertEqual(states[0]["status"], "in-progress")
            self.assertNotIn("artifactSha256", states[0])
            self.assertEqual(states[-1]["promptVersion"], PROMPT_VERSION)
            self.assertEqual(states[-1]["ocrPolicyVersion"], OCR_POLICY_VERSION)
            self.assertEqual(old, snapshot, "the caller's old private checkpoint must remain unchanged")

    def test_ocr_migration_only_accepts_verified_current_coverage_and_source_binding(self):
        base = {"schema": PROGRESS_SCHEMA, "sourceSha256": "a" * 64, "promptVersion": "old", "model": "old",
                "blocks": {"old": {}}, "ocr": {}, "status": "complete", "artifactSha256": "b" * 64,
                "ocrDiagnostics": {"privateEvidence": "kept"}, "unknownPrivateField": True}
        valid = {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [0, 0, 100, 100]}],
                 "coverage": {"schema": OCR_POLICY_VERSION}}
        for bad in [{**valid, "verified": False}, {**valid, "uncertainRegions": ["unverified"]},
                    {**valid, "coverage": {"schema": "obsolete"}}, {**valid, "lines": [{"text": "Demand", "bbox": [0, 0, float("nan"), 100]}]}]:
            state = _translation_state({**base, "ocr": {"c" * 64: valid, "d" * 64: bad}}, "a" * 64, "new", False, require_ocr_coverage=True)
            self.assertEqual(set(state["ocr"]), {"c" * 64})
            self.assertEqual(state["blocks"], {})
            self.assertEqual(state["ocrDiagnostics"], base["ocrDiagnostics"])
            self.assertTrue(state["unknownPrivateField"])
            self.assertNotIn("artifactSha256", state)
        for changed in [{**base, "sourceSha256": "e" * 64}, {**base, "schema": "other"}, []]:
            with self.assertRaisesRegex(LocalizationError, "PROGRESS_SOURCE_MISMATCH"):
                _translation_state(changed, "a" * 64, "new", False, require_ocr_coverage=True)
        force = _translation_state({**base, "ocr": {"c" * 64: valid}}, "a" * 64, "new", True, require_ocr_coverage=True)
        self.assertEqual(force["ocr"], {})
        self.assertEqual(force["blocks"], {})
        self.assertNotIn("artifactSha256", force)
        self.assertNotIn("ocrDiagnostics", force)

    def test_mismatched_rendered_image_hash_requires_new_ocr(self):
        with TemporaryDirectory() as directory:
            states = []
            def verified_ocr(data):
                return {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [20, 80, 200, 270]}],
                        "coverage": {"schema": OCR_POLICY_VERSION}}
            source, output, _, _ = self.run_fixture(directory, raster=True, raster_ocr=verified_ocr, save_progress=states.append)
            old = states[-1]
            old["ocr"] = {"0" * 64: next(iter(old["ocr"].values()))}
            ocr_calls = []
            translate_pdf(source, output, digest(source.read_bytes()), progress=old, translation_client=FixtureTranslator(PAIRS),
                          raster_ocr=lambda data: ocr_calls.append(digest(data)) or verified_ocr(data))
            self.assertEqual(len(ocr_calls), 1)
            self.assertNotEqual(ocr_calls[0], "0" * 64)

    def test_invisible_ocr_layer_is_deduplicated_and_visible_raster_label_translated(self):
        with TemporaryDirectory() as directory:
            source, layered, output = Path(directory) / "en.pdf", Path(directory) / "layered.pdf", Path(directory) / "zh.pdf"
            source_pdf(source, raster=True)
            with fitz.open(source) as document:
                document[0].insert_text((50, 320), "Demand 25%", fontsize=10, render_mode=3)
                document.save(layered)
            result = translate_pdf(layered, output, digest(layered.read_bytes()), translation_client=FixtureTranslator(PAIRS),
                raster_ocr=lambda data: {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand 25%", "bbox": [20, 80, 200, 270]}]})
            self.assertEqual(result["qa"]["pages"][0]["rasterLabelCount"], 1)
            with fitz.open(output) as document:
                self.assertNotIn("Demand", document[0].get_text())
                self.assertIn("需求25%", document[0].get_text())

    def test_source_digest_and_overwrite_are_checked_before_work(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "en.pdf"
            source_pdf(source)
            with self.assertRaisesRegex(LocalizationError, "SOURCE_DIGEST_MISMATCH"):
                translate_pdf(source, Path(directory) / "zh.pdf", "0" * 64)
            with self.assertRaisesRegex(LocalizationError, "SOURCE_OVERWRITE_FORBIDDEN"):
                translate_pdf(source, source, digest(source.read_bytes()))

    def test_unreviewed_cache_is_regenerated(self):
        with TemporaryDirectory() as directory:
            states = []
            source, output, _, _ = self.run_fixture(directory, save_progress=states.append)
            progress = copy.deepcopy(states[-1])
            key = next(key for key, value in progress["blocks"].items() if value["text"] == "研究报告")
            progress["blocks"][key]["reviewed"] = False
            translator = FixtureTranslator(PAIRS)
            translate_pdf(source, output, digest(source.read_bytes()), progress=progress, translation_client=translator)
            self.assertEqual(translator.calls, [[key]])

    def test_annotated_pdf_fails_closed(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "en.pdf"
            source_pdf(source)
            with fitz.open(source) as document:
                document[0].add_text_annot((40, 150), "Untranslated visible annotation")
                document.save(Path(directory) / "annotated.pdf")
            annotated = Path(directory) / "annotated.pdf"
            with self.assertRaisesRegex(LocalizationError, "INTERACTIVE_OR_ANNOTATED_PDF_UNSUPPORTED"):
                translate_pdf(annotated, Path(directory) / "zh.pdf", digest(annotated.read_bytes()), translation_client=FixtureTranslator(PAIRS))

    def test_diagnostic_has_zero_model_calls_and_preserves_all_old_checkpoint_fields(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "en.pdf", Path(directory) / "zh.pdf"
            source_pdf(source, raster=True)
            original = {"schema": PROGRESS_SCHEMA, "sourceSha256": digest(source.read_bytes()),
                "model": "different-model", "promptVersion": "older-prompt", "status": "complete", "artifactSha256": "a" * 64,
                "blocks": {"reviewed": {"text": "已审译文", "reviewed": True}}, "ocr": {"old": {"verified": True}},
                "unknownFutureField": {"must": "remain"}}
            states = []
            evidence = {"width": 100, "height": 100, "words": [{"text": "private detector word", "confidence": 91.5, "bbox": [10, 20, 30, 40]}]}
            with patch.dict("os.environ", {"GATEX_PDF_OCR_DIAGNOSTIC_ONLY": "true", "APIMART_API_KEY": "", "APIMART_BASE_URL": "invalid-unused"}), \
                    patch("gen_rpt.gatex_pdf_localization.ModelClient", side_effect=AssertionError("diagnostic cannot construct a model client")), \
                    patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
                with self.assertRaises(LocalizationError) as caught:
                    translate_pdf(source, output, original["sourceSha256"], progress=original, save_progress=states.append, force=True)
            self.assertEqual(str(caught.exception), "OCR_DIAGNOSTIC_READY")
            self.assertEqual(caught.exception.diagnostic_counts, {"sourcePageCount": 1, "rasterRegionCount": 1, "wordCount": 1})
            self.assertEqual({key: value for key, value in states[-1].items() if key != "ocrDiagnostics"}, original)
            self.assertEqual(states[-1]["ocrDiagnostics"]["regions"]["p1-r1"]["words"], evidence["words"])
            self.assertNotIn("private", str(caught.exception))
            self.assertFalse(output.exists())


class ModelContractTests(unittest.TestCase):
    def test_native_count_modifier_uses_same_column_source_not_interleaved_global_next(self):
        block=Block("current",9,"Reporting describes an example 256-",(45,515,270,524),9)
        right=Block("right",9,"Unrelated right-column material.",(308,521,540,530),9)
        following=Block("left-next",9,"GPU single-layer fabric.",(45,527,267,536),9)
        original_hash=block.source_hash;blocks=[block,right,following]
        _bind_quantity_context(blocks)
        self.assertEqual(block.quantity_context["continuationBlockId"],"left-next")
        self.assertEqual(block.source_hash,original_hash)
        client=self.client();calls=[]
        def once(messages,**kwargs):
            calls.append(messages);row=json.loads(messages[1]["content"])["blocks"][0]
            self.assertEqual(row["sourceQuantityContext"]["continuationBlockId"],"left-next")
            if "source" not in row:
                self.assertEqual(row["quantityHints"][0]["value"],"256")
                return {"blocks":[{"id":"current","text":fixture_generated_text(row,"报道描述了一个256个"),"retainedEnglish":[]}]}
            self.assertEqual(len(messages),2)
            self.assertEqual(row["source"],block.text)
            self.assertEqual(row["translation"],"报道描述了一个256个")
            return {"blocks":[{"id":"current","complete":True,"faithful":True,"issues":[],"professionalChinese":True,"ownedTerminology":True,"qualityIssues":[]}]}
        client._once=once
        result=client.translate([block],context_blocks=blocks)
        self.assertEqual(_validate_block_translation(block,result["current"])["text"],"报道描述了一个256个")
        self.assertEqual(len(calls),2)
        self.assertEqual(block.source_hash,original_hash)
        for target in ("报道描述了一个255个","报道描述了一个256个 FORECAST","报道描述了一个256个 GPU"):
            with self.subTest(target=target),self.assertRaises(LocalizationError):
                _validate_block_translation(block,{"text":target,"retainedEnglish":[],"reviewed":True})

    def test_native_continuation_rejects_cross_column_page_caption_ambiguous_or_intervening_text(self):
        for change in ("other-column","other-page","raster","distant","different-font","duplicate","intervening"):
            block=Block("current",9,"Reporting describes an example 256-",(45,515,270,524),9)
            other=Block("next",9,"GPU single-layer fabric.",(45,527,267,536),9);extra=[]
            if change=="other-column":other.bbox=(308,527,530,536)
            if change=="other-page":other.page=10
            if change=="raster":other.kind="raster"
            if change=="distant":other.bbox=(45,627,267,636)
            if change=="different-font":other.font_size=12
            if change=="duplicate":extra=[Block("duplicate",9,other.text,(45,527.1,267,536.1),9)]
            if change=="intervening":
                extra=[Block("intervening",9,"Other unrelated material.",(45,525,265,534),9)]
            with self.subTest(change=change):
                _bind_quantity_context([block,other,*extra])
                with self.assertRaisesRegex(ValueError,"UNSUPPORTED_QUANTITY"):
                    protect_quantities(block.text,source_context=block.quantity_context)

    def test_edition_context_clarifies_the_source_pdf_without_claiming_upstream_language(self):
        source = "Sources, rights and third-party material. This edition is an authorized English translation of material published by Example House on 24 September 2026. Permission for this edition does not grant model-training rights."
        context = _source_edition_context(source)
        self.assertEqual(context["referenceTranslations"], {"This edition": "原英文版本", "Permission for this edition": "原英文版本的许可"})
        self.assertEqual(context["upstreamMaterialLanguage"], "not specified by the source; do not infer it")
        self.assertEqual(context["dateTranslations"], [{"sourceDate": "24 September 2026", "chineseDate": "2026年九月24日"}])
        self.assertEqual(_source_edition_context("An unrelated author says this edition is a translation."), {})
        target = "来源、权利与第三方材料。原英文版本为Example House于2026年九月24日发布材料的经授权英文译本。原英文版本的许可不授予模型训练权利。"
        client, calls = self.client(), []
        def once(messages, **kwargs):
            calls.append(messages)
            entry = json.loads(messages[1]["content"])["blocks"][0]
            self.assertEqual(entry["sourceEditionContext"], context)
            if len(calls) == 1:
                return {"blocks": [{"id": "rights", "text": fixture_generated_text(entry, target),
                    "retainedEnglish": [{"text": "Example House", "kind": "organisation"}]}]}
            self.assertEqual(len(messages), 2, "fresh reviewer receives no generation feedback/history")
            self.assertEqual(entry["source"], source)
            self.assertEqual(entry["translation"], target)
            self.assertIn("does not identify the original language of upstream", messages[0]["content"])
            return {"blocks": [{"id": "rights", "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        self.assertTrue(client.translate([Block("rights", 0, source, (0, 0, 400, 100))])["rights"]["reviewed"])
        self.assertEqual(len(calls), 2)

    def test_inconsistent_negative_review_is_never_overridden_or_marked_approved(self):
        client, calls, accepted = self.client(), [], []
        client.accepted_callback = accepted.append
        def once(messages, **kwargs):
            calls.append(messages)
            if "Independently compare" in messages[0]["content"]:
                self.assertEqual(len(messages), 2)
                return {"blocks": [{"id": "b", "complete": True, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["No substantive discrepancies found."]}]}
            return {"blocks": [{"id": "b", "text": "需求可能增长。", "retainedEnglish": []}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate([Block("b", 0, "Demand may increase.", (0, 0, 100, 30))])
        self.assertEqual(len(calls), 6)
        self.assertEqual(accepted, [])

    def test_source_exact_bare_domains_are_literals_without_allowing_ordinary_english(self):
        for source, target in [("GATEX.FUND", "GATEX.FUND"),
                ("Visit GATEX.FUND", "请访问GATEX.FUND"),
                ("GATEX.FUND GATEX.FUND", "GATEX.FUND GATEX.FUND"),
                ("GATEX.FUND https://GATEX.FUND", "GATEX.FUND https://GATEX.FUND")]:
            with self.subTest(source=source):
                self.assertEqual(unresolved_english(source, target), 0)
                self.assertEqual(validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})["text"], target)
        for source, target in [("Visit GATEX.FUND", "Visit GATEX.FUND"),
                ("GATEX.FUND", "GATEX.FUND FOREWORD"), ("FUND", "FUND")]:
            with self.subTest(source=source), self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
                validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})
        for target in ["gatex.fund", "GATEX.COM", "GATEX.FUND GATEX.FUND", "GATEX.FUND/path"]:
            with self.subTest(target=target), self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
                validate_translation("GATEX.FUND", {"text": target, "retainedEnglish": [], "reviewed": True})
        with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
            validate_translation("GATEX.FUND GATEX.FUND", {"text": "GATEX.FUND", "retainedEnglish": [], "reviewed": True})

    def test_split_masthead_guidance_never_permits_an_added_brand_and_keeps_fresh_review(self):
        hint = _source_masthead_hint("INTELLIGENCE", source_brand_context=True)
        self.assertEqual(hint["translation"], "前沿洞察")
        self.assertIn("no GATEX brand", hint["instruction"])
        self.assertEqual(_source_masthead_hint("Intelligence is useful."), {})
        with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
            validate_translation("INTELLIGENCE", {"text": "GATEX 前沿洞察", "retainedEnglish": [{"text": "GATEX", "kind": "brand"}], "reviewed": True})
        client, calls = self.client(), []
        def once(messages, **kwargs):
            calls.append(messages)
            payload = json.loads(messages[1]["content"])
            if len(calls) == 1:
                self.assertEqual(payload["blocks"][0]["sourceMastheadTranslation"], hint)
                return {"blocks": [{"id": "split", "text": "前沿洞察", "retainedEnglish": []}]}
            self.assertIn("Independently compare", messages[0]["content"])
            self.assertEqual(payload["blocks"][0]["source"], "INTELLIGENCE")
            self.assertEqual(payload["blocks"][0]["translation"], "前沿洞察")
            return {"blocks": [{"id": "split", "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        split = Block("split", 0, "INTELLIGENCE", (0, 10, 100, 30))
        brand = Block("source-brand", 0, "GATEX", (0, 0, 100, 8))
        self.assertTrue(client.translate([split], context_blocks=[brand, split])["split"]["reviewed"])
        self.assertEqual(len(calls), 2)

    def client(self):
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            return ModelClient(sleep=lambda seconds: None)

    def response_client(self, response):
        class Session:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def post(self, url, **kwargs):
                self.kwargs = kwargs
                return response
        with patch.dict("os.environ", {"APIMART_API_KEY": "dummy-not-used"}):
            return ModelClient(session_factory=Session, sleep=lambda seconds: None)

    def test_gateway_data_envelope_and_standard_choices_are_compatible(self):
        for wrapped in (False, True):
            for fenced in (False, True):
                with self.subTest(wrapped=wrapped, fenced=fenced):
                    expected = {"blocks": [{"id": "b", "text": "需求", "retainedEnglish": []}]}
                    content = json.dumps(expected, ensure_ascii=False)
                    if fenced:
                        content = "```json\n" + content + "\n```"
                    completion = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
                    class Response:
                        status_code = 200
                        closed = False
                        def json(self):
                            return {"code": 200, "data": completion} if wrapped else completion
                        def close(self):
                            self.closed = True
                    response = Response()
                    client = self.response_client(response)
                    self.assertEqual(client._once([{"role": "user", "content": "fixture"}]), expected)
                    self.assertTrue(response.closed)

    def test_environment_credential_whitespace_is_normalized_without_logging(self):
        with patch.dict("os.environ", {"APIMART_API_KEY": "  dummy-not-used\n", "APIMART_BASE_URL": " https://api.apimart.ai/v1/\n", "GATEX_TRANSLATION_MODEL": " gpt-4.1\n"}):
            client = ModelClient(sleep=lambda seconds: None)
        self.assertEqual(client.key, "dummy-not-used")
        self.assertEqual(client.model, "gpt-4.1")
        self.assertEqual(client.url, "https://api.apimart.ai/v1/chat/completions")

    def test_response_diagnostics_do_not_expose_source_or_provider_body(self):
        for kind, expected in (("json", "MODEL_RESPONSE_JSON_INVALID"), ("envelope", "MODEL_ENVELOPE_INVALID"),
                               ("content", "MODEL_CONTENT_JSON_INVALID"), ("http", "MODEL_HTTP_502")):
            with self.subTest(kind=kind):
                class Response:
                    status_code = 502 if kind == "http" else 200
                    closed = False
                    def json(self):
                        if kind == "json":
                            raise ValueError("private source/provider diagnostic")
                        if kind == "envelope":
                            return {"data": {"privateSource": "never printed"}}
                        return {"choices": [{"finish_reason": "stop", "message": {"content": "private source/provider diagnostic"}}]}
                    def close(self):
                        self.closed = True
                response = Response()
                with self.assertRaises(LocalizationError) as caught:
                    self.response_client(response)._once([{"role": "user", "content": "private source"}])
                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(response.closed)

    def test_quantity_pipeline_masks_generation_restores_then_reviews_original_values(self):
        client = self.client()
        block = Block("amounts", 0, "Revenue may be -US$.5 billion and EUR 2 million in Q3.", (0, 0, 100, 30))
        calls, saved = [], []
        client.accepted_callback = saved.append
        def once(payload, **kwargs):
            calls.append(payload)
            supplied = json.loads(payload[1]["content"])["blocks"][0]
            if "source" not in supplied:
                self.assertNotIn(".5 billion", supplied["text"])
                self.assertNotIn("2 million", supplied["text"])
                self.assertNotIn("Q3", supplied["text"])
                self.assertEqual([hint["value"] for hint in supplied["quantityHints"]], ["-US$5亿", "EUR 200万", "Q3"])
                return {"blocks": [{"id": block.id, "text": "收入在{GATEX_QUANTITY_2}可能为{GATEX_QUANTITY_0}和{GATEX_QUANTITY_1}。", "retainedEnglish": []}]}
            self.assertEqual(saved, [], "restoration alone does not authorize a checkpoint")
            self.assertEqual(supplied["source"], block.text)
            self.assertEqual(supplied["translation"], "收入在Q3可能为-US$5亿和EUR 200万。")
            self.assertNotIn("GATEX_QUANTITY", json.dumps(supplied))
            self.assertEqual(len(payload), 2)
            return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        result = client.translate([block])
        self.assertEqual(result[block.id]["text"], "收入在Q3可能为-US$5亿和EUR 200万。")
        self.assertEqual(saved, [result])
        self.assertEqual(len(calls), 2)
        self.assertEqual(validate_translation(block.text, result[block.id]), result[block.id])

    def test_placeholder_or_metadata_cannot_bypass_quantity_identity_links_or_english(self):
        cases = [
            ("Revenue $15 billion", "收入", [], "QUANTITY_PLACEHOLDER_MISMATCH"),
            ("Revenue $15 billion", "收入{GATEX_QUANTITY_0}{GATEX_QUANTITY_0}", [], "QUANTITY_PLACEHOLDER_MISMATCH"),
            ("Revenue $15 billion", "收入{GATEX_QUANTITY_0}{GATEX_QUANTITY_99}", [], "QUANTITY_PLACEHOLDER_MISMATCH"),
            ("Revenue $15 billion", "收入EUR {GATEX_QUANTITY_0}", [], "NUMERIC_OR_LINK_MISMATCH"),
            ("FOREWORD: Revenue $15 billion", "FOREWORD：收入{GATEX_QUANTITY_0}", [{"text": "FOREWORD", "kind": "brand"}], "ORDINARY_ENGLISH_RETAINED"),
            ("Revenue $15 billion", "收入{GATEX_QUANTITY_0}和5", [], "NUMERIC_OR_LINK_MISMATCH"),
            ("Revenue $15 billion", "收入{GATEX_QUANTITY_0}", [{"text": "Invented Brand", "kind": "brand"}], "INVALID_RETAINED_IDENTITY"),
            ("Revenue $15 billion", "收入{GATEX_QUANTITY_0}", [{"text": "{GATEX_QUANTITY_0}", "kind": "code"}], "INVALID_RETAINED_IDENTITY"),
            ("Source https://example.test/v2", "来源", [], "QUANTITY_PLACEHOLDER_MISMATCH"),
        ]
        for source, target, retained, code in cases:
            with self.subTest(source=source, target=target, code=code):
                client = self.client()
                calls, saved = [], []
                client.accepted_callback = saved.append
                def once(payload, **kwargs):
                    calls.append(payload)
                    return {"blocks": [{"id": "b", "text": target, "retainedEnglish": retained}]}
                client._once = once
                with self.assertRaisesRegex(LocalizationError, code):
                    client.translate([Block("b", 0, source, (0, 0, 100, 30))])
                self.assertEqual(saved, [])
                self.assertEqual(len(calls), 3, "three generation attempts, no review for invalid restoration")

    def test_final_quantity_equivalence_rejects_currency_transfer_and_operator_changes(self):
        for source, target in [("$1 million £2 million", "$200万 £100万"),
                               ("20% - 5 percentage points", "20% +5个百分点"),
                               ("~$84+ and $90", "$84 and ~$90+"),
                               ("2 million - 1 million", "100万 -200万"),
                               ("Q3 https://example.test/v2", "Q4 https://example.test/v2")]:
            with self.subTest(source=source, target=target):
                with self.assertRaisesRegex(LocalizationError, "NUMERIC_OR_LINK_MISMATCH"):
                    validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})
        with self.assertRaisesRegex(LocalizationError, "QUANTITY_PLACEHOLDER_MISMATCH"):
            validate_translation("Revenue $15 billion", {"text": "收入{GATEX_QUANTITY_0}", "retainedEnglish": [], "reviewed": True})

    def test_failed_semantic_review_regenerates_then_reviews_independently(self):
        client = self.client()
        block = Block("b", 0, "Revenue may grow 7%.", (0, 0, 100, 30))
        responses = [
            {"blocks": [{"id": "b", "text": "收入将增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]},
            {"blocks": [{"id": "b", "complete": True, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["may changed to will"]}]},
            {"blocks": [{"id": "b", "text": "收入可能增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]},
            {"blocks": [{"id": "b", "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]},
        ]
        messages = []
        def once(payload, **kwargs):
            messages.append(payload)
            return responses.pop(0)
        client._once = once
        result = client.translate([block])
        self.assertEqual(result["b"]["text"], "收入可能增长7%。")
        self.assertEqual(len(messages), 4)
        self.assertIn("may changed to will", messages[2][-1]["content"])
        self.assertEqual(len(messages[3]), 2)
        self.assertNotIn("previous response", json.dumps(messages[3]))

    def test_retained_case_failure_has_private_record_and_block_specific_retry_without_relaxing_gate(self):
        client = self.client()
        block = Block("cover-brand", 0, "GATEX", (0, 0, 100, 30))
        responses = [
            {"blocks": [{"id": block.id, "text": "GateX", "retainedEnglish": [{"text": "GateX", "kind": "brand"}]}]},
            {"blocks": [{"id": block.id, "text": "GATEX", "retainedEnglish": [{"text": "GATEX", "kind": "brand"}]}]},
            {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]},
        ]
        messages, rejected = [], []
        client.rejection_callback = rejected.append
        def once(payload, **kwargs):
            messages.append(payload)
            return responses.pop(0)
        client._once = once
        result = client.translate([block])
        self.assertEqual(result[block.id]["text"], "GATEX")
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["source"], "GATEX")
        self.assertEqual(rejected[0]["record"]["retainedEnglish"][0]["text"], "GateX")
        self.assertIn(block.id, messages[1][-1]["content"])
        self.assertIn("exact source spelling", messages[1][-1]["content"])
        self.assertEqual(len(messages[2]), 2, "independent review gets no failure history")
        with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
            validate_translation("GATEX", {"text": "GateX", "retainedEnglish": [{"text": "GateX", "kind": "brand"}], "reviewed": True})

    def test_exact_house_masthead_metadata_reconciliation_preserves_ordinary_gate(self):
        cases = [("GATEX INTELLIGENCE", "GATEX 前沿洞察", [{"text": "GATEX", "kind": "brand"}]),
                 ("TECHNOLOGY FRONTIERS", "科技前沿", [])]
        for source, target, expected in cases:
            with self.subTest(source=source):
                raw = {"text": target, "retainedEnglish": [{"text": source, "kind": "brand"}]}
                record = _source_verified_record(source, raw)
                self.assertEqual(validate_translation(source, {**record, "reviewed": True})["retainedEnglish"], expected)
                self.assertEqual(raw["retainedEnglish"], [{"text": source, "kind": "brand"}], "private raw evidence is unchanged")
                invalid = {"text": target, "retainedEnglish": [{"text": "Invented Brand", "kind": "brand"}]}
                with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
                    validate_translation(source, {**_source_verified_record(source, invalid), "reviewed": True})
        for source, target, retained in [("TECHNOLOGY FRONTIERS", "TECHNOLOGY FRONTIERS", [{"text": "TECHNOLOGY FRONTIERS", "kind": "brand"}]),
                                         ("TECHNOLOGY FRONTIERS overview", "科技前沿", [{"text": "TECHNOLOGY FRONTIERS", "kind": "brand"}]),
                                         ("GATEX INTELLIGENCE", "GATEX INTELLIGENCE", [])]:
            with self.subTest(source=source, target=target):
                with self.assertRaises(LocalizationError):
                    validate_translation(source, {**_source_verified_record(source, {"text": target, "retainedEnglish": retained}), "reviewed": True})

    def test_real_masthead_shapes_still_require_fresh_review_before_checkpoint(self):
        client = self.client()
        blocks = [Block("footer", 1, "GATEX INTELLIGENCE", (0, 0, 100, 30)),
                  Block("series", 2, "TECHNOLOGY FRONTIERS", (0, 40, 100, 70))]
        calls, saved = [], []
        client.accepted_callback = saved.append
        def once(payload, **kwargs):
            calls.append(payload)
            if len(calls) == 1:
                self.assertEqual(saved, [])
                return {"blocks": [{"id": "footer", "text": "GATEX 前沿洞察", "retainedEnglish": [{"text": "GATEX INTELLIGENCE", "kind": "brand"}]},
                                   {"id": "series", "text": "科技前沿", "retainedEnglish": [{"text": "TECHNOLOGY FRONTIERS", "kind": "brand"}]}]}
            self.assertEqual(saved, [], "deterministic metadata reconciliation is not semantic approval")
            return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for block in blocks]}
        client._once = once
        result = client.translate(blocks)
        self.assertEqual(len(calls), 2)
        self.assertEqual(saved, [result])
        self.assertEqual(result["footer"]["retainedEnglish"], [{"text": "GATEX", "kind": "brand"}])
        self.assertEqual(result["series"]["retainedEnglish"], [])

    def test_known_source_title_requires_its_chinese_counterpart_not_surrounding_chinese(self):
        source = 'Today, I am copying the work "Good and Bad Procrastination" by Paul Graham, the founder of YC.'
        raw = {"text": "今天，我正在抄录由YC创始人Paul Graham所著的《Good and Bad Procrastination》。",
               "retainedEnglish": [{"text": "YC", "kind": "organisation"}, {"text": "Paul Graham", "kind": "person"},
                                   {"text": "Good and Bad Procrastination", "kind": "source_title"}], "reviewed": True}
        with self.assertRaisesRegex(LocalizationError, "MISSING_CHINESE_SOURCE_TITLE"):
            validate_translation(source, raw)
        translated = {**raw, "text": "今天，我复制YC创始人Paul Graham的作品《拖延的好与坏》（Good and Bad Procrastination）。"}
        self.assertEqual(validate_translation(source, translated)["text"], translated["text"])
        for alias in ("拖延的好与坏", "好的拖延与坏的拖延", "好与坏的拖延"):
            for title in (f"《{alias}》（Good and Bad Procrastination）", f"《Good and Bad Procrastination》（{alias}）",
                          f"《{alias}（Good and Bad Procrastination）》"):
                with self.subTest(title=title):
                    variant = {**raw, "text": f"今天，我复制YC创始人Paul Graham的作品{title}。"}
                    self.assertEqual(validate_translation(source, variant)["text"], variant["text"])
        with self.assertRaisesRegex(LocalizationError, "MISSING_CHINESE_SOURCE_TITLE"):
            validate_translation(source, {**raw, "text": "我谈论拖延的好与坏，今天抄录YC创始人Paul Graham的作品《Good and Bad Procrastination》。"})
        with self.assertRaisesRegex(LocalizationError, "MISSING_CHINESE_SOURCE_TITLE"):
            validate_translation(source, {**raw, "retainedEnglish": [{**entry, "kind": "brand"} if entry["kind"] == "source_title" else entry for entry in raw["retainedEnglish"]]})

    def test_known_title_failure_gives_source_bound_generation_feedback_without_pressuring_review(self):
        client = self.client()
        block = Block("copied-work", 1, 'I am copying "Good and Bad Procrastination".', (0, 0, 100, 30))
        responses = [
            {"blocks": [{"id": block.id, "text": "我正在抄录《Good and Bad Procrastination》。", "retainedEnglish": [{"text": "Good and Bad Procrastination", "kind": "source_title"}]}]},
            {"blocks": [{"id": block.id, "text": "我复制《拖延的好与坏》（Good and Bad Procrastination）。", "retainedEnglish": [{"text": "Good and Bad Procrastination", "kind": "source_title"}]}]},
            {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]},
        ]
        messages, rejected = [], []
        client.rejection_callback = rejected.append
        def once(payload, **kwargs):
            messages.append(payload)
            return responses.pop(0)
        client._once = once
        result = client.translate([block])
        self.assertEqual(result[block.id]["text"], "我复制《拖延的好与坏》（Good and Bad Procrastination）。")
        self.assertEqual(len(messages), 3)
        self.assertEqual(rejected[0]["code"], "MISSING_CHINESE_SOURCE_TITLE")
        self.assertIn("sourceTitleTranslations", messages[1][-1]["content"])
        self.assertIn("拖延的好与坏", messages[1][-1]["content"])
        self.assertEqual(len(messages[2]), 2)
        self.assertNotIn("rejectedRecord", json.dumps(messages[2]))

    def test_salutation_and_typed_codes_have_positive_source_evidence_without_expanding_names(self):
        person = "Mr. Graham describes the choices."
        code = "This is type C."
        self.assertEqual(_source_identity_hints(person), [{"text": "Graham", "kind": "person"}])
        self.assertEqual(_source_identity_hints(code), [{"text": "C", "kind": "code", "sourceCount": 1}])
        self.assertEqual(_source_identity_hints("Market outlook. Graham describes choices."), [])
        for source, target, identity in [(person, "Graham先生描述可供选择的方案。", {"text": "Graham", "kind": "person"}),
                                        (code, "这是C类。", {"text": "C", "kind": "code"})]:
            record = _source_verified_record(source, {"text": target, "retainedEnglish": [], "reviewed": True})
            self.assertEqual(validate_translation(source, record)["retainedEnglish"], [identity])
        for record in [
            {"text": "Paul Graham先生描述可供选择的方案。", "retainedEnglish": []},
            {"text": "Paul Graham先生描述可供选择的方案。", "retainedEnglish": [{"text": "Paul Graham", "kind": "person"}]},
        ]:
            with self.subTest(record=record):
                with self.assertRaises(LocalizationError):
                    validate_translation(person, {**_source_verified_record(person, record), "reviewed": True})
        for source, target in [("An ordinary article.", "普通文章C类。"), (code, "这是C类 C。"),
                               (code, "这是B类。"), (code, "这是C。"), ("Two type C examples and type C cases.", "两个C类例子。")]:
            with self.subTest(source=source, target=target):
                record = _source_verified_record(source, {"text": target, "retainedEnglish": [], "reviewed": True})
                with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
                    validate_translation(source, record)

    def test_actual_surname_and_type_shapes_receive_independent_review_without_metadata_retry(self):
        client = self.client()
        blocks = [Block("surname", 1, "Mr. Graham describes the choices.", (0, 0, 100, 30)),
                  Block("type-code", 1, "This is type C.", (0, 40, 100, 70))]
        messages, saved = [], []
        client.accepted_callback = saved.append
        def once(payload, **kwargs):
            messages.append(payload)
            if len(messages) == 1:
                return {"blocks": [{"id": "surname", "text": "Graham先生描述可供选择的方案。", "retainedEnglish": []},
                                   {"id": "type-code", "text": "这是C类。", "retainedEnglish": []}]}
            return {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for block in blocks]}
        client._once = once
        result = client.translate(blocks)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0], result)
        generation = json.loads(messages[0][1]["content"])["blocks"]
        self.assertEqual(generation[0]["sourceVerifiedIdentities"], [{"text": "Graham", "kind": "person"}])
        self.assertEqual(result["type-code"]["retainedEnglish"], [{"text": "C", "kind": "code"}])
        self.assertEqual(len(messages[1]), 2, "fresh review receives no failure history")

    def test_reviewed_candidates_are_saved_before_remaining_failures_and_resume_only_pending(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "en.pdf", Path(directory) / "zh.pdf"
            source_pdf(source)
            client = self.client()
            states, generation_ids, review_ids = [], [], []
            bad_ids = set()
            def once(payload, **kwargs):
                supplied = json.loads(payload[1]["content"])["blocks"]
                if "source" in supplied[0]:
                    review_ids.append([item["id"] for item in supplied])
                    return {"blocks": [{"id": item["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for item in supplied]}
                generation_ids.append([item["id"] for item in supplied])
                bad_ids.update(item["id"] for item in supplied if item["text"] == "Supply")
                return {"blocks": [{"id": item["id"], "text": "Supply" if item["text"] == "Supply" else fixture_generated_text(item, PAIRS[fixture_source_text(item)]),
                                   "retainedEnglish": []} for item in supplied]}
            client._once = once
            with self.assertRaisesRegex(LocalizationError, "TRANSLATION_BLOCKS_PENDING"):
                translate_pdf(source, output, digest(source.read_bytes()), translation_client=client, save_progress=states.append)
            self.assertEqual(len(generation_ids), 3)
            self.assertEqual(generation_ids[1], list(bad_ids))
            self.assertEqual(generation_ids[2], list(bad_ids))
            self.assertEqual(len(review_ids), 1)
            self.assertTrue(set(review_ids[0]).isdisjoint(bad_ids))
            checkpoint = states[-1]
            self.assertEqual(checkpoint["status"], "in-progress")
            self.assertTrue(set(review_ids[0]).issubset(checkpoint["blocks"]))
            self.assertTrue(bad_ids.isdisjoint(checkpoint["blocks"]))
            self.assertEqual(set(checkpoint["translationFailures"]), bad_ids)
            self.assertEqual(set(row["code"] for row in checkpoint["translationFailures"].values()), {"UNRESOLVED_ENGLISH"})
            self.assertTrue(all(record["reviewed"] and record["sourceHash"] for record in checkpoint["blocks"].values()))
            self.assertFalse(output.exists(), "partial progress cannot create a PDF artifact")
            self.assertFalse(output.with_name(output.name + ".partial").exists())
            resumed = self.client()
            resume_ids = []
            def resumed_once(payload, **kwargs):
                supplied = json.loads(payload[1]["content"])["blocks"]
                if "source" in supplied[0]:
                    return {"blocks": [{"id": item["id"], "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for item in supplied]}
                resume_ids.extend(item["id"] for item in supplied)
                return {"blocks": [{"id": item["id"], "text": fixture_generated_text(item, PAIRS[fixture_source_text(item)]), "retainedEnglish": []} for item in supplied]}
            resumed._once = resumed_once
            result = translate_pdf(source, output, digest(source.read_bytes()), progress=checkpoint,
                                   translation_client=resumed, save_progress=states.append)
            self.assertEqual(set(resume_ids), bad_ids)
            self.assertTrue(result["qa"]["passed"])
            self.assertEqual(states[-1]["status"], "complete")

    def test_partial_review_only_accepts_true_verdicts_and_does_not_repeat_approved_blocks(self):
        client = self.client()
        blocks = [Block("approved", 0, "Demand", (0, 0, 100, 30)),
                  Block("pending", 0, "Revenue may grow 7%.", (0, 40, 100, 70))]
        messages, saved, rejected = [], [], []
        client.accepted_callback = saved.append
        client.rejection_callback = rejected.append
        def once(payload, **kwargs):
            messages.append(payload)
            supplied = json.loads(payload[1]["content"])["blocks"]
            if "source" in supplied[0]:
                return {"blocks": [{"id": item["id"], "complete": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "faithful": item["id"] == "approved",
                    "issues": [] if item["id"] == "approved" else ["may changed to will"]} for item in supplied]}
            return {"blocks": [{"id": item["id"], "text": "需求" if item["id"] == "approved" else "收入将增长{GATEX_QUANTITY_0}。",
                                "retainedEnglish": []} for item in supplied]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate(blocks)
        self.assertEqual(len(messages), 6, "three generation and fresh review attempts maximum")
        self.assertEqual(list(saved[0]), ["approved"])
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(rejected), 3)
        for rejection in rejected:
            self.assertEqual(rejection["code"], "SEMANTIC_REVIEW_FAILED")
            self.assertEqual(rejection["sourceHash"], digest("Revenue may grow 7%."))
            self.assertEqual(rejection["source"], "Revenue may grow 7%.")
            self.assertEqual(rejection["record"]["text"], "收入将增长{GATEX_QUANTITY_0}。")
            self.assertEqual(rejection["normalizedCandidate"]["text"], "收入将增长7%。")
            self.assertFalse(rejection["normalizedCandidate"]["reviewed"])
            self.assertEqual(rejection["review"]["issues"], ["may changed to will"])
        self.assertIn("Revenue may grow 7%.", messages[2][-1]["content"])
        self.assertIn("收入将增长7%。", messages[2][-1]["content"])
        self.assertIn("may changed to will", messages[2][-1]["content"])
        for payload in [messages[2], messages[4]]:
            self.assertEqual([item["id"] for item in json.loads(payload[1]["content"])["blocks"]], ["pending"])
        for payload in [messages[1], messages[3], messages[5]]:
            self.assertEqual(len(payload), 2, "the reviewer is never pressured to change its verdict")

    def test_malformed_review_cannot_checkpoint_any_candidate(self):
        client = self.client()
        saved, calls = [], []
        client.accepted_callback = saved.append
        def once(payload, **kwargs):
            calls.append(payload)
            if "source" not in json.loads(payload[1]["content"])["blocks"][0]:
                return {"blocks": [{"id": "b", "text": "需求", "retainedEnglish": []}]}
            return {"blocks": [{"id": "b", "complete": "true", "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "REVIEW_COVERAGE_MISMATCH"):
            client.translate([Block("b", 0, "Demand", (0, 0, 100, 30))])
        self.assertEqual(saved, [])
        self.assertEqual(len(calls), 4, "one generation, then at most three fresh reviews of the unchanged candidate")
        reviews = [json.loads(payload[1]["content"])["blocks"][0] for payload in calls[1:]]
        self.assertTrue(all(row["translation"] == "需求" for row in reviews))

    def test_source_verified_parenthesized_labels_keep_counts_and_do_not_allow_articles(self):
        source = "Choose (a) a task, (b) a smaller task, or (c) a larger task."
        target = "选择（a）一项任务、（b）一项较小的任务或（c）一项较大的任务。"
        record = {"text": target, "retainedEnglish": [], "reviewed": True}
        self.assertEqual(validate_translation(source, record)["text"], target)
        for invalid in [target.replace("（c）", "（d）"), target + "（a）", target.replace("（a）", "a")]:
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(LocalizationError, "STRUCTURAL_MARKER_MISMATCH"):
                    validate_translation(source, {**record, "text": invalid})
        with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
            validate_translation(source, {**record, "text": target + " a", "retainedEnglish": [{"text": "a", "kind": "code"}]})
        with self.assertRaisesRegex(LocalizationError, "STRUCTURAL_MARKER_MISMATCH"):
            validate_translation("An ordinary article.", {"text": "普通文章（a）", "retainedEnglish": [], "reviewed": True})

    def test_surname_is_source_verified_only_with_explicit_metadata_or_chinese_translation(self):
        source = "Graham describes the available choices."
        target = "Graham描述可供选择的方案。"
        with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
            validate_translation(source, {"text": target, "retainedEnglish": [], "reviewed": True})
        self.assertEqual(validate_translation(source, {"text": target, "retainedEnglish": [{"text": "Graham", "kind": "person"}], "reviewed": True})["text"], target)
        self.assertEqual(validate_translation(source, {"text": "格雷厄姆描述可供选择的方案。", "retainedEnglish": [], "reviewed": True})["text"], "格雷厄姆描述可供选择的方案。")
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            validate_translation("Market outlook", {"text": "Market展望", "retainedEnglish": [{"text": "Market", "kind": "person"}], "reviewed": True})

    def test_retry_reports_all_rejected_blocks_and_exact_tokens_together(self):
        client = self.client()
        blocks = [Block("name-one", 0, "Graham describes a method.", (0, 0, 100, 30)),
                  Block("name-two", 0, "Stone describes another method.", (0, 40, 100, 70))]
        responses = [
            {"blocks": [{"id": "name-one", "text": "Graham描述一种方法。", "retainedEnglish": []},
                        {"id": "name-two", "text": "Stone描述另一种方法。", "retainedEnglish": []}]},
            {"blocks": [{"id": "name-one", "text": "Graham描述一种方法。", "retainedEnglish": [{"text": "Graham", "kind": "person"}]},
                        {"id": "name-two", "text": "Stone描述另一种方法。", "retainedEnglish": [{"text": "Stone", "kind": "person"}]}]},
            {"blocks": [{"id": block.id, "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []} for block in blocks]},
        ]
        messages, rejected = [], []
        client.rejection_callback = rejected.append
        def once(payload, **kwargs):
            messages.append(payload)
            return responses.pop(0)
        client._once = once
        result = client.translate(blocks)
        self.assertEqual(set(result), {block.id for block in blocks})
        self.assertEqual([record["unresolvedTokens"] for record in rejected], [["Graham"], ["Stone"]])
        feedback = messages[1][-1]["content"]
        for value in ["name-one", "name-two", "Graham", "Stone", "unresolvedTokens"]:
            self.assertIn(value, feedback)
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(messages[2]), 2, "review receives only actual source/counterpart pairs")

    def test_malformed_retained_metadata_keeps_safe_error_and_private_record(self):
        client = self.client()
        block = Block("metadata-shape", 0, "Revenue may grow 7%.", (0, 0, 100, 30))
        messages, rejected = [], []
        client.rejection_callback = rejected.append
        def once(payload, **kwargs):
            messages.append(payload)
            return {"blocks": [{"id": block.id, "text": "收入可能增长{GATEX_QUANTITY_0}。", "retainedEnglish": {"text": "not a list"}}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "UNRESOLVED_ENGLISH"):
            client.translate([block])
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(rejected), 3)
        self.assertEqual(rejected[-1]["retainedMetadataType"], "dict")
        self.assertEqual(rejected[-1]["record"]["retainedEnglish"], {"text": "not a list"})
        self.assertIn('"retainedMetadataType": "dict"', messages[1][-1]["content"])

    def test_persistent_semantic_failure_is_three_generation_attempts(self):
        client = self.client()
        block = Block("b", 0, "Revenue may grow 7%.", (0, 0, 100, 30))
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) % 2:
                return {"blocks": [{"id": "b", "text": "收入将增长{GATEX_QUANTITY_0}。", "retainedEnglish": []}]}
            return {"blocks": [{"id": "b", "complete": False, "faithful": False, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": ["modality lost"]}]}
        client._once = once
        with self.assertRaisesRegex(LocalizationError, "SEMANTIC_REVIEW_FAILED"):
            client.translate([block])
        self.assertEqual(len(calls), 6)

    def test_missing_block_never_reaches_review_and_has_three_attempts(self):
        client = self.client()
        calls = []
        client._once = lambda messages, **kwargs: calls.append(messages) or {"blocks": []}
        with self.assertRaisesRegex(LocalizationError, "BLOCK_COVERAGE_MISMATCH"):
            client.translate([Block("b", 0, "Demand", (0, 0, 100, 30))])
        self.assertEqual(len(calls), 3)

    def test_ocr_independent_detector_prevents_missing_numeric_label(self):
        client = self.client()
        image = Image.new("RGB", (20, 20), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        fake_tesseract = {"text": ["Revenue", "25%"], "conf": [90, 95], "left": [0, 10], "top": [0, 0], "width": [10, 10], "height": [10, 10]}
        calls = []
        client._once = lambda messages, **kwargs: calls.append(messages) or {"verified": True, "uncertainRegions": [], "lines": [{"text": "Revenue", "bbox": [0, 0, 500, 500]}]}
        with patch("shutil.which", return_value="/usr/bin/tesseract"), patch("pytesseract.image_to_data", return_value=fake_tesseract):
            with self.assertRaisesRegex(LocalizationError, "RASTER_TEXT_COVERAGE_MISMATCH"):
                client.ocr(stream.getvalue())
        self.assertEqual(len(calls), 4)

    def test_actual_native_cover_words_and_chair_edge_candidate_are_independently_reconciled(self):
        client = self.client()
        image = Image.new("RGB", (1786, 2526), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        evidence = {"width": 1786, "height": 2526, "words": [
            {"text": "GATEX", "confidence": 91, "bbox": [146, 161, 382, 211]},
            {"text": "e", "confidence": 76, "bbox": [792, 2105, 823, 2132]}]}
        native = [{"text": "GATEX", "bbox": [48 * 1000 / 595.276, 52.1576 * 1000 / 841.89,
                  127.235 * 1000 / 595.276, 75.1576 * 1000 / 841.89]}]
        responses = [{"verified": True, "uncertainRegions": [], "lines": []},
                     {"verified": True, "classification": "non_text", "uncertainRegions": [], "lines": []}]
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            return responses.pop(0)
        client._once = once
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            result = client.ocr(stream.getvalue(), native_words=native)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["coverage"]["nativeWordCount"], 1)
        checks = result["coverage"]["cropChecks"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["detector"]["bbox"], [792, 2105, 823, 2132])
        self.assertEqual(checks[0]["review"]["classification"], "non_text")
        self.assertIn("Independently inspect", calls[1][0]["content"])
        self.assertNotIn('"text": "e"', json.dumps(calls[1][1]))

    def test_independent_crop_recovers_an_omitted_table_number(self):
        client = self.client()
        image = Image.new("RGB", (20, 20), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        evidence = {"width": 20, "height": 20, "words": [
            {"text": "Revenue", "confidence": 90, "bbox": [0, 0, 10, 10]},
            {"text": "25%", "confidence": 95, "bbox": [10, 0, 20, 10]}]}
        responses = [{"verified": True, "uncertainRegions": [], "lines": [{"text": "Revenue", "bbox": [0, 0, 500, 500]}]},
            {"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "25%", "bbox": [500, 0, 1000, 500]}]}]
        client._once = lambda messages, **kwargs: responses.pop(0)
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            result = client.ocr(stream.getvalue())
        self.assertEqual([line["text"] for line in result["lines"]], ["Revenue", "25%"])
        self.assertEqual(result["lines"][1]["bbox"], [500, 0, 1000, 500])

    def test_unreadable_crop_is_not_dismissed_as_graphics(self):
        client = self.client()
        image = Image.new("RGB", (20, 20), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        evidence = {"width": 20, "height": 20, "words": [{"text": "25%", "confidence": 95, "bbox": [10, 0, 20, 10]}]}
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"verified": True, "uncertainRegions": [], "lines": []}
            return {"verified": False, "classification": "non_text", "uncertainRegions": ["Unreadable candidate"], "lines": []}
        client._once = once
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            with self.assertRaisesRegex(LocalizationError, "UNVERIFIED_RASTER_TEXT"):
                client.ocr(stream.getvalue())
        self.assertEqual(len(calls), 4)

    def test_independent_crop_cannot_change_high_confidence_numeric_tokens(self):
        client = self.client()
        image = Image.new("RGB", (20, 20), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        evidence = {"width": 20, "height": 20, "words": [{"text": "25%", "confidence": 95, "bbox": [10, 0, 20, 10]}]}
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"verified": True, "uncertainRegions": [], "lines": []}
            return {"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "24%", "bbox": [500, 0, 1000, 500]}]}
        client._once = once
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            with self.assertRaisesRegex(LocalizationError, "RASTER_NUMERIC_OR_LINK_MISMATCH"):
                client.ocr(stream.getvalue())
        self.assertEqual(len(calls), 4)

    def test_native_number_does_not_cover_omitted_raster_number_at_another_location(self):
        client = self.client()
        image = Image.new("RGB", (100, 100), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        evidence = {"width": 100, "height": 100, "words": [
            {"text": "25%", "confidence": 95, "bbox": [0, 0, 10, 10]},
            {"text": "25%", "confidence": 95, "bbox": [80, 80, 90, 90]}]}
        responses = [{"verified": True, "uncertainRegions": [], "lines": [{"text": "25%", "bbox": [0, 0, 100, 100]}]},
            {"verified": True, "classification": "text", "uncertainRegions": [], "lines": [{"text": "25%", "bbox": [625, 625, 875, 875]}]}]
        client._once = lambda messages, **kwargs: responses.pop(0)
        with patch("gen_rpt.gatex_pdf_localization.tesseract_words", return_value=evidence):
            result = client.ocr(stream.getvalue(), native_words=[{"text": "25%", "bbox": [0, 0, 100, 100]}])
        self.assertEqual(len(result["lines"]), 2)
        self.assertEqual(len(result["coverage"]["cropChecks"]), 1)

    def test_explicit_reviewed_single_letter_panel_code_requires_exact_source(self):
        record = {"text": "图A", "retainedEnglish": [{"text": "A", "kind": "code"}], "reviewed": True}
        self.assertEqual(validate_translation("Figure A", record)["text"], "图A")
        with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
            validate_translation("Figure B", record)
        with self.assertRaisesRegex(LocalizationError, "INVALID_RETAINED_IDENTITY"):
            validate_translation("Figure AI", record)
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            validate_translation("A report", {"text": "A报告", "retainedEnglish": [{"text": "A", "kind": "brand"}], "reviewed": True})

    def test_source_title_requires_parallel_chinese_translation(self):
        source = "The global credit cycle"
        retained = [{"text": source, "kind": "source_title"}]
        with self.assertRaisesRegex(LocalizationError, "ORDINARY_ENGLISH_RETAINED"):
            validate_translation(source, {"text": source, "retainedEnglish": retained, "reviewed": True})
        self.assertEqual(validate_translation(source, {"text": "全球信贷周期（The global credit cycle）", "retainedEnglish": retained, "reviewed": True})["text"], "全球信贷周期（The global credit cycle）")


if __name__ == "__main__":
    unittest.main()
