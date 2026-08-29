import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_report_pipeline import (
    _private_source_reproduction_violations,
    _source_artifact_dict,
)
from tools.gatex_generation_bridge import (
    BridgeError,
    _private_reference_url,
    _request_with_retries,
    _run_generator,
    discovered_sources_from_manifest,
    download_intelligence_seed_sources,
    download_private_sources,
    manifest_requires_private_source_content,
)
from tools.local_web_report_audit import required_reference_count


class GateXGenerationBridgeTests(unittest.TestCase):
    def test_source_channel_retry_inherits_private_content_requirement(self):
        manifest = {
            "provenanceType": "manual_retry",
            "effectiveProvenanceType": "source_channel",
            "requiresPrivateSourceContent": True,
            "discoveredSources": [],
        }

        self.assertTrue(manifest_requires_private_source_content(manifest))
        self.assertFalse(
            manifest_requires_private_source_content(
                {"provenanceType": "manual_retry", "discoveredSources": []}
            )
        )

    def test_callback_requests_never_follow_redirects(self):
        with patch("tools.gatex_generation_bridge.requests.request") as request:
            response = request.return_value
            response.status_code = 302
            response.ok = False
            response.text = "Redirected"

            with self.assertRaises(BridgeError):
                _request_with_retries(
                    "GET",
                    "https://gatex.fund/api/generation/jobs/job/sources",
                )

        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        response.close.assert_called_once()

    def test_source_channel_private_body_is_verified_and_passed_as_seed(self):
        source_text = (
            "Canonical source body with market observations.\n\n"
            "A second paragraph for synthesis."
        )
        source_bytes = source_text.encode("utf-8")
        digest = hashlib.sha256(source_bytes).hexdigest()

        class FakeApi:
            def download(self, download_url: str, target: Path) -> None:
                self.download_url = download_url
                target.write_bytes(source_bytes)

        manifest = {
            "provenanceType": "source_channel",
            "discoveredSources": [
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": "social",
                    "url": "https://example.com/source/1",
                    "title": "Tracked source",
                    "publisher": "Example publisher",
                    "excerpt": "Editor-approved summary.",
                    "contentHash": digest,
                    "contentSha256": digest,
                    "contentByteSize": len(source_bytes),
                    "contentType": "text/plain; charset=utf-8",
                    "contentUrl": (
                        "/api/generation/jobs/job/sources/source/content"
                    ),
                    "metadata": {
                        "deletionStatus": "active",
                        "reusePolicy": "original_summary_only",
                        "maxQuoteCharacters": 180,
                    },
                }
            ],
        }
        api = FakeApi()
        with TemporaryDirectory() as directory:
            documents, summaries = download_intelligence_seed_sources(
                api,
                manifest,
                Path(directory),
            )

            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0].content, source_text)
            self.assertTrue(documents[0].metadata["gatex_private_content"])
            self.assertEqual(documents[0].metadata["content_hash"], digest)
            self.assertEqual(summaries[0]["contentSha256"], digest)
            self.assertTrue(summaries[0]["privateContent"])

            with patch("tools.gatex_generation_bridge.DeepSeekClient"), patch(
                "tools.gatex_generation_bridge.WebReportPipeline"
            ) as pipeline_class:
                pipeline_class.return_value.build_report.return_value = {"ok": True}
                result = _run_generator(
                    topic="Tracked market theme",
                    language="zh",
                    model="test-model",
                    source_mode="web_only",
                    private_sources=[],
                    seed_sources=documents,
                    output_dir=Path(directory),
                )

        self.assertEqual(result, {"ok": True})
        passed_seed = pipeline_class.return_value.build_report.call_args.kwargs[
            "seed_sources"
        ][0]
        self.assertEqual(passed_seed.content, source_text)

    def test_trend_manifest_stays_excerpt_seed_and_requires_web_corroboration(self):
        documents, summaries = discovered_sources_from_manifest(
            {
                "provenanceType": "trend_proposal",
                "discoveredSources": [
                    {
                        "id": "trend-source",
                        "kind": "api",
                        "url": "https://example.com/trend",
                        "title": "Rising topic",
                        "publisher": "Trend API",
                        "excerpt": (
                            "The topic appeared across three independent sources."
                        ),
                        "claimIds": "invalid-string-shape",
                        "metadata": {"deletionStatus": "active"},
                    }
                ],
            }
        )

        self.assertEqual(len(documents), 1)
        self.assertIn("corroborate", documents[0].content)
        self.assertFalse(
            documents[0].metadata.get("gatex_private_content", False)
        )
        self.assertEqual(documents[0].metadata["claim_ids"], [])
        self.assertEqual(summaries[0]["status"], "ready")

    def test_private_source_body_is_redacted_from_pipeline_artifacts(self):
        document = SourceDocument(
            title="Private source",
            url="https://example.com/source",
            query="seed",
            snippet="Approved summary",
            content="SECRET FULL BODY " * 100,
            source_type="gatex_private_social",
            content_type="text/plain; charset=utf-8",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "max_quote_characters": 180,
            },
        )

        payload = _source_artifact_dict(document)

        self.assertEqual(payload["content"], "Approved summary")
        self.assertNotIn("SECRET FULL BODY", payload["content"])
        self.assertTrue(payload["metadata"]["private_content_redacted"])

        exact_passage = "A" * 181
        document.content = f"prefix {exact_passage} suffix"
        self.assertEqual(
            _private_source_reproduction_violations(
                {"sections": [{"body": exact_passage}]},
                [document],
            ),
            ["Private source"],
        )
        self.assertEqual(
            _private_source_reproduction_violations(
                {"sections": [{"body": "A" * 180}]},
                [document],
            ),
            [],
        )

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
