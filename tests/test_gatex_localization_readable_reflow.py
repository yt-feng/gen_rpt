"""Readable delivery keeps photographic text layers out of the Chinese page."""
import copy
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import fitz
from PIL import Image, ImageDraw

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, _image_hashes, _page_blocks, _readable_translation,
    _translate, _translation_state, digest, translate_pdf,
)


class ReadableReflowTests(unittest.TestCase):
    def test_source_exact_acronym_forecast_labels_resume_without_a_chinese_prose_requirement(self):
        def forbidden(*_args, **_kwargs):
            self.fail("source-exact technical labels do not require a model")
        blocks = [Block("label-a", 0, "ABGPU, 2026E-2030E", None),
                  Block("label-b", 0, "NPU, 2025E-2029E", None)]
        client = SimpleNamespace(acceptance_mode="readable", translate=forbidden)
        records = _translate(blocks, {"blocks": {}}, lambda _: None, client)
        self.assertEqual([records[block.id]["text"] for block in blocks], [block.text for block in blocks])
        self.assertEqual(_readable_translation(blocks[0].text, {"text": "ABGPU，2026E-2030E"})["text"],
                         "ABGPU，2026E-2030E")
        for source, target in [(blocks[0].text, "ABGPU, 2026E-2031E"),
                               (blocks[0].text, "NEWGPU, 2026E-2030E"),
                               ("The forecast increased in 2025E-2029E", "The forecast increased in 2025E-2029E")]:
            with self.subTest(source=source), self.assertRaisesRegex(LocalizationError, "CHINESE_TRANSLATION_MISSING"):
                _readable_translation(source, {"text": target})

    def test_dark_native_and_raster_cover_is_chinese_first_with_unchanged_source_reference(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_path, output_path = directory / "source.pdf", directory / "zh.pdf"
            image = Image.new("RGB", (600, 800), (7, 15, 25))
            ImageDraw.Draw(image).text((80, 200), "Raster heading", fill="white")
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            source = fitz.open()
            page = source.new_page(width=600, height=800)
            page.insert_image(page.rect, stream=stream.getvalue())
            page.insert_text((80, 225), "Native cover heading", fontsize=22, color=(1, 1, 1))
            page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(80, 200, 300, 230),
                              "uri": "https://example.test/original"})
            page = source.new_page(width=600, height=800)
            page.insert_text((80, 100), "Revenue rose.", fontsize=12)
            page.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(80, 80, 180, 110),
                              "page": 0, "to": fitz.Point(80, 200)})
            source.save(source_path)
            source.close()
            source_sha = digest(source_path.read_bytes())
            state = _translation_state(None, source_sha, "offline-cache", False, require_ocr_coverage=False)

            def ocr(_image):
                return {"verified": True, "uncertainRegions": [],
                        "lines": [{"text": "Raster heading", "bbox": [130, 245, 520, 280]}]}

            with fitz.open(source_path) as original:
                for number, page in enumerate(original):
                    blocks, _ = _page_blocks(page, number, state, lambda _: None, ocr)
                    for block in blocks:
                        state["blocks"][block.id] = {"sourceHash": block.source_hash,
                            "text": "完整中文图注。" if block.kind == "raster" else
                                    "完整中文封面。" if number == 0 else "收入增长。",
                            "retainedEnglish": [], "reviewed": False}
            saved = copy.deepcopy(state["blocks"])

            def forbidden(*_args, **_kwargs):
                self.fail("rendering must reuse cached translations and OCR without a model")

            client = SimpleNamespace(model="offline-cache", acceptance_mode="readable", translate=forbidden, ocr=forbidden)
            result = translate_pdf(source_path, output_path, source_sha, progress=state, translation_client=client)
            qa = result["qa"]
            self.assertEqual(qa["sourcePageCount"], 2)
            self.assertEqual(qa["pageCount"], 3)
            self.assertEqual(qa["sourceBlockCount"], qa["translatedBlockCount"])
            self.assertEqual(qa["pages"][0]["targetPages"], [1, 2])
            self.assertEqual(qa["pages"][0]["referenceTargetPage"], 2)
            self.assertEqual(qa["pages"][1]["targetPages"], [3])
            self.assertNotIn("renderingMode", qa["pages"][1])
            self.assertTrue(qa["readableChinesePassed"])
            self.assertEqual(state["blocks"], saved)
            with fitz.open(source_path) as original, fitz.open(output_path) as output:
                first = output[0]
                self.assertIn("完整中文封面", first.get_text())
                self.assertIn("完整中文图注", first.get_text())
                self.assertNotIn("Native cover heading", first.get_text())
                self.assertFalse(first.get_images())
                self.assertIn("图示参考", output[1].get_text())
                self.assertIn("Native cover heading", output[1].get_text())
                self.assertEqual(_image_hashes(original, original[0]), _image_hashes(output, output[1]))
                reference = output[1].get_pixmap(clip=fitz.Rect(0, 32, 600, 832), alpha=False)
                self.assertEqual(original[0].get_pixmap(alpha=False).samples, reference.samples)
                self.assertEqual(output[1].get_links()[0]["uri"], "https://example.test/original")
                self.assertEqual(output[1].get_links()[0]["from"], fitz.Rect(80, 232, 300, 262))
                self.assertIn("收入增长", output[2].get_text())
                self.assertEqual(output[2].get_links()[0]["page"], 0)


if __name__ == "__main__":
    unittest.main()
