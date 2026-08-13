from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from gen_rpt.image_generator import (
    _download_source_candidate,
    _image_source_record,
    _source_first_image,
)
from gen_rpt.web_report_renderer import _render_section


def _png(path: Path, size: tuple[int, int] = (1200, 800)) -> None:
    Image.new("RGB", size, "#176DDC").save(path, "PNG")


class WebImageSelectionTests(unittest.TestCase):
    def test_relevant_web_image_wins_without_ai(self) -> None:
        candidate = {
            "source_title": "DOE fusion commercialization milestone",
            "source_query": "fusion commercialization",
            "source_snippet": "public private fusion facility",
            "caption": "Fusion research facility",
            "alt_text": "DOE fusion facility",
            "original_image_url": "https://energy.gov/fusion.jpg",
        }
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "image-1.png"

            def download(_candidate, target, **_kwargs):
                _png(target)
                return "unique-web-hash", ""

            with (
                patch("gen_rpt.image_generator._download_source_candidate", side_effect=download),
                patch("gen_rpt.image_generator._download_wikimedia_source") as wiki,
                patch("gen_rpt.image_generator._download_pollinations_or_fallback") as ai,
            ):
                status, _reason, source = _source_first_image(
                    "DOE fusion commercialization facility",
                    output,
                    [candidate],
                    set(),
                    kind="section",
                    timeout_seconds=2,
                    retries=1,
                    allow_fallback=True,
                    client=Mock(),
                )
            self.assertEqual(status, "web_source")
            self.assertEqual(source["image_sha256"], "unique-web-hash")
            self.assertTrue(output.exists())
            wiki.assert_not_called()
            ai.assert_not_called()

    def test_broken_low_resolution_and_irrelevant_images_do_not_enter_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "image.png"
            broken = Mock()
            broken.raise_for_status.side_effect = RuntimeError("broken")
            with patch("gen_rpt.image_generator.requests.get", return_value=broken):
                digest, _reason = _download_source_candidate(
                    {"original_image_url": "https://example.gov/broken.jpg", "source_page_url": "https://example.gov/article"},
                    output,
                    timeout_seconds=1,
                )
            self.assertFalse(digest)

            tiny = Path(folder) / "tiny.png"
            _png(tiny, (320, 180))
            response = Mock()
            response.headers = {"Content-Type": "image/png"}
            response.raw.read.return_value = tiny.read_bytes()
            response.raise_for_status.return_value = None
            with patch("gen_rpt.image_generator.requests.get", return_value=response):
                digest, reason = _download_source_candidate(
                    {"original_image_url": "https://example.gov/tiny.png", "source_page_url": "https://example.gov/article"},
                    output,
                    timeout_seconds=1,
                )
            self.assertFalse(digest)
            self.assertIn("low resolution", reason)

            def fallback(_prompt, target, **_kwargs):
                _png(target)
                return "fallback", ""

            with (
                patch("gen_rpt.image_generator._download_source_candidate") as web,
                patch("gen_rpt.image_generator._download_wikimedia_source", return_value=("", "none", None)),
                patch("gen_rpt.image_generator._polish_prompt", return_value="safe prompt"),
                patch("gen_rpt.image_generator._download_pollinations_or_fallback", side_effect=fallback) as ai,
            ):
                status, _reason, source = _source_first_image(
                    "fusion facility",
                    output,
                    [{"source_title": "unrelated cooking recipe", "caption": "dessert", "original_image_url": "https://example.com/food.jpg"}],
                    set(),
                    kind="section",
                    timeout_seconds=1,
                    retries=1,
                    allow_fallback=True,
                    client=Mock(),
                )
            self.assertEqual(status, "fallback")
            self.assertIsNone(source)
            web.assert_not_called()
            ai.assert_called_once()

    def test_duplicate_web_image_is_skipped_for_next_licensed_source(self) -> None:
        candidate = {
            "source_title": "Fusion facility",
            "caption": "Fusion facility",
            "original_image_url": "https://energy.gov/fusion.jpg",
        }
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "image.png"

            def duplicate(_candidate, target, **_kwargs):
                _png(target)
                return "already-used", ""

            def wikimedia(_prompt, target, **_kwargs):
                _png(target)
                return "wikimedia", "fusion", {"image_sha256": "new-hash", "caption": "Licensed fusion facility"}

            with (
                patch("gen_rpt.image_generator._download_source_candidate", side_effect=duplicate),
                patch("gen_rpt.image_generator._download_wikimedia_source", side_effect=wikimedia),
                patch("gen_rpt.image_generator._download_pollinations_or_fallback") as ai,
            ):
                status, _reason, source = _source_first_image(
                    "fusion facility",
                    output,
                    [candidate],
                    {"already-used"},
                    kind="section",
                    timeout_seconds=1,
                    retries=1,
                    allow_fallback=True,
                    client=Mock(),
                )
            self.assertEqual(status, "wikimedia")
            self.assertEqual(source["image_sha256"], "new-hash")
            ai.assert_not_called()

    def test_metadata_and_html_keep_traceability_without_raw_objects(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            report_dir = Path(folder) / "report-id"
            assets = report_dir / "assets"
            assets.mkdir(parents=True)
            image = assets / "image-1.png"
            _png(image)
            record = _image_source_record(
                {
                    "original_image_url": "https://energy.gov/fusion.jpg",
                    "source_page_url": "https://energy.gov/fusion",
                    "source_domain": "energy.gov",
                    "source_publication": "U.S. Department of Energy",
                    "caption": "Fusion research facility",
                    "attribution": "U.S. Department of Energy",
                    "license": "Public domain",
                },
                image,
                assets,
                "image-1",
                "Commercialization",
                "web_source",
                {"version": "2.0"},
            )
            self.assertEqual(record["r2_object_path"], "reports/report-id/current/assets/image-1.png")
            self.assertEqual(record["report_version"], "2.0")
            self.assertTrue(record["image_sha256"])

            parts: list[str] = []
            _render_section(
                parts,
                {
                    "id": "section-1",
                    "title": "Commercialization",
                    "lead": "",
                    "paragraphs": ["Normal report prose."],
                    "evidence": [],
                    "so_what": "",
                    "image_caption": record["caption"],
                    "image_source": record["attribution"],
                },
                1,
                {"part": "Part"},
                {"image-1": "assets/image-1.png"},
            )
            html = "".join(parts)
            self.assertIn("assets/image-1.png", html)
            self.assertIn("Fusion research facility · U.S. Department of Energy", html)
            self.assertNotIn("original_image_url", html)


if __name__ == "__main__":
    unittest.main()
