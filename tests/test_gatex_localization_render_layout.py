"""Layout regressions use public labels, synthetic prose and observed geometry."""

import copy
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz
from PIL import Image

from gen_rpt.gatex_pdf_localization import (
    Block,
    MIN_FONT,
    OCR_POLICY_VERSION,
    PROGRESS_SCHEMA,
    PROMPT_VERSION,
    _block_writer,
    _font,
    _page_blocks,
    _raster_render_aliases,
    _wrap,
    translate_pdf,
)


CLOSING = "，。、；：！？）》】〕〉」』〗〙〛’”"
OPENING = "（《【〔〈「『〖〘〚‘“"


class NativeRasterOccurrenceTests(unittest.TestCase):
    source_sha = hashlib.sha256(b"synthetic source PDF").hexdigest()
    image_sha = hashlib.sha256(b"synthetic source image pixels").hexdigest()

    def blocks(self):
        native = Block("native", 0, "MEMBER CONFIDENTIAL", (48, 775.32977, 145.328, 786.34576))
        raster = Block("raster", 0, native.text, (48, 775, 149, 788), kind="raster")
        records = {native.id: {"text": "会员保密"}, raster.id: {"text": "会员保密"}}
        raster.image_sha256 = self.image_sha
        return native, raster, records

    def shifted(self):
        native, raster, records = self.blocks()
        # Observed footer rectangles: the image transcription was about 21pt
        # too low. No publication paragraph or private pixels are committed.
        raster.bbox = (48.812597, 796.427723, 149.414169, 809.897959)
        return native, raster, records

    def evidence(self, native, raster, records):
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        return {
            "schema": "gatex-pdf-native-render-alias/v1",
            "sourceSha256": self.source_sha,
            "sourcePage": 1,
            "imageSha256": self.image_sha,
            "rasterBlockId": raster.id,
            "nativeBlockId": native.id,
            "rasterSourceHash": sha(raster.text),
            "nativeSourceHash": sha(native.text),
            "rasterTargetTextSha256": sha(records[raster.id]["text"]),
            "nativeTargetTextSha256": sha(records[native.id]["text"]),
            "rasterBBox": list(raster.bbox),
            "nativeBBox": list(native.bbox),
            "verification": "independent-source-image-review/v1",
            "independentRasterTextPresent": False,
            "verified": True,
        }

    def aliases(self, blocks, records, evidence):
        return _raster_render_aliases(blocks, records, source_sha256=self.source_sha, evidence=evidence)

    def test_observed_shift_needs_exact_source_image_review_evidence(self):
        native, raster, records = self.shifted()
        self.assertEqual(self.aliases([native, raster], records, None), {})
        proof = self.evidence(native, raster, records)
        self.assertEqual(self.aliases([native, raster], records, [proof]), {"raster": "native"})

    def test_evidence_cannot_move_between_source_image_text_or_box_identities(self):
        native, raster, records = self.shifted()
        proof = self.evidence(native, raster, records)
        changes = {
            "schema": "unknown-schema", "sourceSha256": "f" * 64, "sourcePage": 2,
            "imageSha256": "e" * 64, "rasterBlockId": "different-raster", "nativeBlockId": "different-native",
            "rasterSourceHash": "d" * 64, "nativeSourceHash": "d" * 64,
            "rasterTargetTextSha256": "c" * 64, "nativeTargetTextSha256": "c" * 64,
            "rasterBBox": [48, 796, 149, 809], "nativeBBox": [48, 775, 145, 786],
            "verification": "model-guess", "independentRasterTextPresent": True, "verified": False,
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                self.assertEqual(self.aliases([native, raster], records, [{**proof, key: value}]), {})

    def test_even_review_evidence_does_not_alias_distant_or_other_column_text(self):
        for box in [(48, 650, 149, 663), (350, 796, 451, 810)]:
            with self.subTest(box=box):
                native, raster, records = self.shifted()
                raster.bbox = box
                self.assertEqual(self.aliases([native, raster], records, [self.evidence(native, raster, records)]), {})

    def test_proof_does_not_disambiguate_multiple_native_or_raster_candidates(self):
        native, raster, records = self.shifted()
        proof = self.evidence(native, raster, records)
        other = copy.deepcopy(native)
        other.id = "second-native"
        records[other.id] = records[native.id]
        self.assertEqual(self.aliases([native, other, raster], records, [proof]), {})
        other = copy.deepcopy(raster)
        other.id = "second-raster"
        records[other.id] = records[raster.id]
        self.assertEqual(self.aliases([native, raster, other], records,
            [proof, self.evidence(native, other, records)]), {})

    def test_complete_renderer_uses_bound_evidence_and_draws_the_occurrence_once(self):
        class CachedClient:
            model = "layout-test"

            def translate(self, blocks):
                raise AssertionError("Every synthetic block is already independently reviewed")

        def no_ocr(*args, **kwargs):
            raise AssertionError("The fixture has an exact image-SHA OCR cache")

        with TemporaryDirectory() as directory:
            source_file, target_file = [Path(directory) / name for name in ("source.pdf", "target.pdf")]
            image_bytes = io.BytesIO()
            Image.new("RGB", (600, 850), (20, 30, 45)).save(image_bytes, format="PNG")
            with fitz.open() as document:
                page = document.new_page(width=595.276, height=841.89)
                page.insert_image(page.rect, stream=image_bytes.getvalue(), keep_proportion=False)
                page.insert_text((48, 784), "MEMBER CONFIDENTIAL", fontsize=8, color=(1, 1, 1))
                document.save(source_file)
            source_sha = hashlib.sha256(source_file.read_bytes()).hexdigest()
            state = {"schema": PROGRESS_SCHEMA, "sourceSha256": source_sha,
                     "model": "layout-test", "promptVersion": PROMPT_VERSION,
                     "ocrPolicyVersion": OCR_POLICY_VERSION, "blocks": {}, "ocr": {}}
            with fitz.open(source_file) as document:
                page = document[0]
                ocr_box = [48.812597 / page.rect.width * 1000, 796.427723 / page.rect.height * 1000,
                           149.414169 / page.rect.width * 1000, 809.897959 / page.rect.height * 1000]
                blocks, _ = _page_blocks(page, 0, state, lambda value: None, lambda image: {
                    "verified": True, "uncertainRegions": [],
                    "lines": [{"text": "MEMBER CONFIDENTIAL", "bbox": ocr_box}],
                })
            native, raster = [next(block for block in blocks if block.kind == kind) for kind in ("native", "raster")]
            records = {block.id: {"text": "会员保密", "reviewed": True, "retainedEnglish": []} for block in blocks}
            state["blocks"] = {block.id: {"sourceHash": block.source_hash, **records[block.id]} for block in blocks}
            evidence = self.evidence(native, raster, records)
            evidence.update(sourceSha256=source_sha, imageSha256=raster.image_sha256)
            state["renderAliasEvidence"] = [evidence]
            result = translate_pdf(source_file, target_file, source_sha, progress=state,
                                   translation_client=CachedClient(), raster_ocr=no_ocr)
            with fitz.open(target_file) as document:
                self.assertEqual(document[0].get_text().count("会员保密"), 1)
            self.assertEqual(result["qa"]["sourceBlockCount"], 2)
            self.assertEqual(result["qa"]["translatedBlockCount"], 2)
            self.assertEqual(result["qa"]["pages"][0]["reconciledRasterBlockCount"], 1)
            self.assertTrue(result["qa"]["figuresPreserved"])

    def test_an_overlapping_exact_occurrence_has_one_render_owner(self):
        native, raster, records = self.blocks()
        self.assertEqual(_raster_render_aliases([native, raster], records), {"raster": "native"})

    def test_repeated_native_candidates_are_not_collapsed(self):
        native, raster, records = self.blocks()
        second = copy.deepcopy(native)
        second.id = "other-native"
        second.bbox = (49, 775.4, 146.328, 786.4)
        records[second.id] = records[native.id]
        self.assertEqual(_raster_render_aliases([native, second, raster], records), {})

    def test_two_raster_occurrences_cannot_share_one_native_owner(self):
        native, raster, records = self.blocks()
        second = copy.deepcopy(raster)
        second.id = "other-raster"
        second.bbox = (49, 776, 150, 789)
        records[second.id] = records[raster.id]
        self.assertEqual(_raster_render_aliases([native, raster, second], records), {})

    def test_a_real_neighbouring_or_distant_occurrence_needs_its_own_render(self):
        native, raster, records = self.blocks()
        for box in [(48, 790, 149, 803), (48, 600, 149, 613), (350, 775, 451, 788)]:
            with self.subTest(box=box):
                other = copy.deepcopy(raster)
                other.bbox = box
                self.assertEqual(_raster_render_aliases([native, other], records), {})

    def test_source_case_and_character_counts_are_not_equivalent_occurrences(self):
        native, raster, records = self.blocks()
        for text in ["Member Confidential", "MEMBER CONFIDENTIAL CONFIDENTIAL", "MEMBER CONFIDENTIA"]:
            with self.subTest(text=text):
                other = copy.deepcopy(raster)
                other.text = text
                self.assertEqual(_raster_render_aliases([native, other], records), {})

    def test_different_reviewed_targets_or_invisible_native_layers_are_not_aliases(self):
        native, raster, records = self.blocks()
        records[raster.id] = {"text": "仅供内部使用"}
        self.assertEqual(_raster_render_aliases([native, raster], records), {})
        records[raster.id] = records[native.id]
        native.hidden = True
        self.assertEqual(_raster_render_aliases([native, raster], records), {})


class ChineseLineLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = _font()

    def assert_lines(self, text, lines, width, size):
        self.assertEqual("".join(lines), text)
        for line in lines:
            self.assertLessEqual(self.font.text_length(line, fontsize=size), width + 0.05)
            self.assertFalse(line and line[0] in CLOSING, repr(line))
            self.assertFalse(line and line[-1] in OPENING, repr(line))
            self.assertFalse(line and all(char in CLOSING + OPENING for char in line), repr(line))

    def test_closing_punctuation_does_not_start_the_next_line(self):
        text = "甲乙丙丁，戊己庚辛。"
        width, size = 40, 10
        self.assert_lines(text, _wrap(text, width, self.font, size), width, size)

    def test_opening_and_closing_pairs_remain_with_their_text(self):
        text = "甲乙丙（丁戊己）庚辛。"
        width, size = 40, 10
        self.assert_lines(text, _wrap(text, width, self.font, size), width, size)

    def test_minimum_size_still_obeys_line_rules_without_losing_characters(self):
        text = "甲乙丙丁，戊己庚辛。"
        width = 4 * MIN_FONT
        self.assert_lines(text, _wrap(text, width, self.font, MIN_FONT), width, MIN_FONT)

    def render_lines(self, block, text):
        page_rect = fitz.Rect(0, 0, 595.276, 841.89)
        writer = _block_writer(block, text, self.font, page_rect, [])
        self.assertIsNotNone(writer)
        with fitz.open() as document:
            page = document.new_page(width=page_rect.width, height=page_rect.height)
            writer.write_text(page)
            rows = [line for item in page.get_text("dict")["blocks"] if item.get("type") == 0
                    for line in item["lines"]]
            lines = ["".join(span["text"] for span in row["spans"]) for row in rows]
            sizes = [span["size"] for row in rows for span in row["spans"]]
        self.assertGreaterEqual(min(sizes), 7)
        self.assertEqual("".join(lines), text)
        for line in lines:
            self.assertFalse(line and line[0] in CLOSING, repr(line))
            self.assertFalse(line and all(char in CLOSING + OPENING for char in line), repr(line))
        return lines, sizes

    def test_observed_paragraph_geometry_does_not_leave_a_period_only_line(self):
        # Same source rectangle as the observed 44+1-glyph line break, with
        # synthetic text rather than any private publication paragraph.
        block = Block("paragraph", 2, "Synthetic paragraph", (44, 96.25, 464.72989, 123.99), font_size=10)
        self.render_lines(block, "甲" * 42 + "。")

    def test_observed_second_paragraph_geometry_does_not_start_with_comma(self):
        block = Block("paragraph", 3, "Synthetic paragraph", (44, 251.25, 469.72983, 292.99), font_size=10)
        self.render_lines(block, "甲" * 42 + "，" + "乙" * 16 + "。")

    def test_source_small_font_never_reduces_chinese_below_seven_points(self):
        block = Block("small", 0, "Small source label", (40, 40, 200, 90), font_size=5)
        _, sizes = self.render_lines(block, "完整文字。")
        self.assertTrue(all(size == 7 for size in sizes))

    def test_unplaceable_punctuation_unit_overflows_instead_of_using_subseven_font(self):
        block = Block("narrow", 0, "Narrow source", (40, 40, 47, 70), font_size=7)
        text = "甲。"
        self.assertEqual("".join(_wrap(text, 7, self.font, 7)), text)
        self.assertIsNone(_block_writer(block, text, self.font, fitz.Rect(0, 0, 595, 842), []))


if __name__ == "__main__":
    unittest.main()
