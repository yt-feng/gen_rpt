import hashlib
import json
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
    build_intelligence_source_profile,
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
            "Asterion Robotics uses counter-positioning stress tests before "
            "major product decisions.\n\n"
            "A second private paragraph explains the operating discipline."
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
                    "excerpt": "E" * 180 + "SENTINEL_AFTER_QUOTE_CAP",
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
            self.assertEqual(len(documents[0].snippet), 180)
            self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", documents[0].snippet)
            self.assertEqual(summaries[0]["contentSha256"], digest)
            self.assertTrue(summaries[0]["privateContent"])

            source_profile = build_intelligence_source_profile(
                manifest,
                documents,
            )
            self.assertEqual(source_profile["mode"], "source_channel")
            self.assertIn("Asterion Robotics", source_profile["anchors"])
            serialized_profile = json.dumps(source_profile, ensure_ascii=False)
            self.assertNotIn("second private paragraph", serialized_profile)
            self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", serialized_profile)
            serialized_source_artifact = json.dumps(
                _source_artifact_dict(documents[0]),
                ensure_ascii=False,
            )
            self.assertNotIn(
                "SENTINEL_AFTER_QUOTE_CAP",
                serialized_source_artifact,
            )

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
                    source_profile=source_profile,
                )

        self.assertEqual(result, {"ok": True})
        passed_seed = pipeline_class.return_value.build_report.call_args.kwargs[
            "seed_sources"
        ][0]
        self.assertEqual(passed_seed.content, source_text)
        self.assertEqual(
            pipeline_class.return_value.build_report.call_args.kwargs[
                "source_profile"
            ]["mode"],
            "source_channel",
        )

    def test_source_channel_profile_fails_closed_without_verified_body(self):
        manifest = {
            "provenanceType": "manual_retry",
            "effectiveProvenanceType": "source_channel",
            "requiresPrivateSourceContent": True,
        }
        excerpt_only = SourceDocument(
            title="Tracked source",
            url="https://example.com/source",
            query="seed",
            snippet="Editor-approved excerpt",
            content="Excerpt-only seed",
            source_type="gatex_seed_social",
            domain="example.com",
            metadata={"source_id": "source-1", "gatex_seed": True},
        )

        with self.assertRaisesRegex(BridgeError, "verified private source content"):
            build_intelligence_source_profile(manifest, [excerpt_only])

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
            snippet="S" * 180 + "SENTINEL_AFTER_QUOTE_CAP",
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

        self.assertEqual(payload["content"], "S" * 180)
        self.assertEqual(payload["snippet"], "S" * 180)
        self.assertNotIn("SECRET FULL BODY", payload["content"])
        self.assertNotIn("SENTINEL_AFTER_QUOTE_CAP", json.dumps(payload))
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

    def test_private_source_leak_guard_counts_unique_fragments_across_artifacts(self):
        segments = [
            f"private-segment-{index:02d}-" + chr(65 + index) * 55
            for index in range(5)
        ]
        document = SourceDocument(
            title="Public source title",
            url="https://example.com/source",
            query="seed",
            snippet="Approved public summary.",
            content=" | ".join(segments),
            source_type="gatex_private_social",
            domain="example.com",
            metadata={
                "gatex_private_content": True,
                "source_id": "source-1",
                "max_quote_characters": 180,
            },
        )

        artifacts = {
            "report": {"lead": segments[0]},
            "research_plan": {"note": segments[2]},
            "evidence": [{"fact": segments[4]}],
        }
        self.assertEqual(
            _private_source_reproduction_violations(artifacts, [document]),
            ["source-1"],
        )

        repeated_approved_excerpt = {
            "report": document.snippet,
            "evidence": [document.snippet, document.snippet],
        }
        self.assertEqual(
            _private_source_reproduction_violations(
                repeated_approved_excerpt,
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
