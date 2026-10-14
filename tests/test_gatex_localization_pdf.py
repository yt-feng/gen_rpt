import copy
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
    _image_hashes, digest, tokens, translate_pdf, unresolved_english,
    validate_translation,
)


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
    def run_fixture(self, directory, replacements=None, **kwargs):
        source, output = Path(directory) / "en.pdf", Path(directory) / "zh.pdf"
        source_pdf(source, raster=kwargs.pop("raster", False), pages=kwargs.pop("pages", 1), outline=kwargs.pop("outline", False))
        translator = kwargs.pop("translation_client", FixtureTranslator(replacements or PAIRS))
        result = translate_pdf(source, output, digest(source.read_bytes()), translation_client=translator, **kwargs)
        return source, output, result, translator

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

    def test_failed_semantic_review_regenerates_then_reviews_independently(self):
        client = self.client()
        block = Block("b", 0, "Revenue may grow 7%.", (0, 0, 100, 30))
        responses = [
            {"blocks": [{"id": "b", "text": "收入将增长7%。", "retainedEnglish": []}]},
            {"blocks": [{"id": "b", "complete": True, "faithful": False, "issues": ["may changed to will"]}]},
            {"blocks": [{"id": "b", "text": "收入可能增长7%。", "retainedEnglish": []}]},
            {"blocks": [{"id": "b", "complete": True, "faithful": True, "issues": []}]},
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

    def test_persistent_semantic_failure_is_three_generation_attempts(self):
        client = self.client()
        block = Block("b", 0, "Revenue may grow 7%.", (0, 0, 100, 30))
        calls = []
        def once(messages, **kwargs):
            calls.append(messages)
            if len(calls) % 2:
                return {"blocks": [{"id": "b", "text": "收入将增长7%。", "retainedEnglish": []}]}
            return {"blocks": [{"id": "b", "complete": False, "faithful": False, "issues": ["modality lost"]}]}
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
