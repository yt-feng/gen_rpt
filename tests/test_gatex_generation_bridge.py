import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.gatex_generation_bridge import _private_reference_url, download_private_sources
from tools.local_web_report_audit import required_reference_count


class GateXGenerationBridgeTests(unittest.TestCase):
    def test_downloaded_private_source_gets_retained_reference_identity(self):
        class FakeApi:
            def download(self, _download_url: str, target: Path) -> None:
                target.write_text("A board-grade source document " * 12, encoding="utf-8")

        manifest = {
            "sources": [
                {
                    "id": "document-123",
                    "fileName": "Board Strategy.txt",
                    "mimeType": "text/plain",
                    "downloadUrl": "/private/source",
                }
            ]
        }
        with TemporaryDirectory() as directory:
            documents, summaries = download_private_sources(FakeApi(), manifest, Path(directory))

        self.assertEqual(len(documents), 1)
        self.assertEqual(summaries[0]["status"], "ready")
        self.assertTrue(documents[0].url.startswith("private://gatex.collection/"))

    def test_private_reference_url_is_stable_opaque_and_unique(self):
        first = _private_reference_url("document-123", "Board Strategy.docx", 1)
        repeated = _private_reference_url("document-123", "Renamed.docx", 9)
        second = _private_reference_url("document-456", "Board Strategy.docx", 1)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("private://gatex.collection/"))
        self.assertNotIn("document-123", first)
        self.assertNotIn("Board", first)

    def test_private_reference_url_falls_back_to_file_identity(self):
        first = _private_reference_url("", "One.docx", 1)
        repeated = _private_reference_url("", "One.docx", 1)
        second = _private_reference_url("", "Two.docx", 2)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)

    def test_reference_target_tracks_available_sources_up_to_four(self):
        self.assertEqual(required_reference_count([]), 4)
        self.assertEqual(required_reference_count([{"url": "private://one"}]), 1)
        self.assertEqual(required_reference_count([{}, {}]), 4)
        self.assertEqual(required_reference_count([{"url": "private://one"}, {"url": "private://two"}]), 2)
        self.assertEqual(
            required_reference_count([{"url": f"https://example.com/{index}"} for index in range(5)]),
            4,
        )
        self.assertEqual(
            required_reference_count([{"url": "https://example.com"}, {"url": "https://example.com"}]),
            1,
        )
        self.assertEqual(required_reference_count({"unexpected": "shape"}), 4)


if __name__ == "__main__":
    unittest.main()
