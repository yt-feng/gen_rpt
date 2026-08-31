from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from gen_rpt.gatex_pdf_renderer import _body_css, _legacy_body_html
from gen_rpt.image_generator import generate_ai_image_assets
from gen_rpt.web_report_renderer import _render_reference_sources
from gen_rpt.web_report_pipeline import ReportQualityError, WebReportPipeline
from tools.gatex_release_bridge import (
    GateXReleaseApi,
    ReleaseBridgeError,
    _materialize_visual_assets,
)
from tools.gatex_generation_bridge import _public_files
from tools import local_web_report_audit


def _quality_image(path: Path) -> None:
    image = Image.effect_noise((1_200, 800), 90).convert("RGB")
    image.save(path, format="PNG")


class SimplifiedImageTests(unittest.TestCase):
    def test_single_editorial_mode_generates_only_one_verified_ai_image(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            backup = root / "backup"
            assets.mkdir()
            (assets / "cover-background.png").write_bytes(b"brand-cover")

            def generate(_prompt, target, **_kwargs):
                _quality_image(target)
                return "pollinations", ""

            with patch("gen_rpt.image_generator._download_pollinations_or_fallback", side_effect=generate):
                result = generate_ai_image_assets(
                    object(),
                    "AI infrastructure",
                    {"sections": [{"title": "Deployment economics", "lead": "A grounded market view."}]},
                    assets,
                    backup,
                    single_editorial_image=True,
                )

            self.assertEqual(result, {"image-1": "assets/image-1.png"})
            self.assertEqual((assets / "cover-background.png").read_bytes(), b"brand-cover")
            self.assertFalse((assets / "image-2.png").exists())
            prompts = json.loads((backup / "image_prompts.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["status"] for entry in prompts], ["pollinations"])

    def test_single_editorial_mode_rejects_non_ai_fallback_and_low_information_images(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            backup = root / "backup"

            def fallback(_prompt, target, **_kwargs):
                _quality_image(target)
                return "fallback", "provider unavailable"

            with patch("gen_rpt.image_generator._download_pollinations_or_fallback", side_effect=fallback):
                with self.assertRaisesRegex(RuntimeError, "required editorial image"):
                    generate_ai_image_assets(
                        object(), "Topic", {"sections": []}, assets, backup, single_editorial_image=True
                    )
            self.assertFalse((assets / "image-1.png").exists())

            def black(_prompt, target, **_kwargs):
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1_200, 800), "black").save(target, format="PNG")
                return "pollinations", ""

            with patch("gen_rpt.image_generator._download_pollinations_or_fallback", side_effect=black):
                with self.assertRaisesRegex(RuntimeError, "failed publication quality"):
                    generate_ai_image_assets(
                        object(), "Topic", {"sections": []}, assets, backup, single_editorial_image=True
                    )
            self.assertFalse((assets / "image-1.png").exists())


class SimplifiedReleaseTests(unittest.TestCase):
    def test_generation_upload_path_matches_worker_visual_contract(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "assets" / "image-1.png").write_bytes(b"editorial")
            self.assertEqual(
                [(relative, path.name) for relative, path in _public_files(root)],
                [("assets/image-1.png", "image-1.png")],
            )

    def test_visual_asset_is_runtime_only_and_bound_to_one_section(self) -> None:
        class Api:
            def __init__(self) -> None:
                self.calls = []

            def download_visual(self, url: str, target: Path) -> None:
                self.calls.append((url, target))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"editorial-image")

        payload = {
            "contentSections": [
                {"id": "market", "kind": "section", "heading": "Market", "asset_key": "assets/image-1.png"},
                {"id": "sources", "kind": "sources", "footnotes": ["Source, https://example.com"]},
            ]
        }
        envelope = {
            "visualAssets": [
                {
                    "sectionId": "market",
                    "path": "assets/image-1.png",
                    "downloadUrl": "/api/generation/jobs/11111111-1111-4111-8111-111111111111/assets/assets%2Fimage-1.png",
                }
            ]
        }
        with TemporaryDirectory() as directory:
            api = Api()
            _materialize_visual_assets(api, envelope, payload, Path(directory))
            self.assertEqual(len(api.calls), 1)
            visual_path = Path(payload["contentSections"][0]["visualPath"])
            self.assertTrue(visual_path.is_file())
            self.assertNotIn("visualPath", payload["contentSections"][1])

        with self.assertRaisesRegex(ReleaseBridgeError, "exactly one"):
            _materialize_visual_assets(Api(), {"visualAssets": [envelope["visualAssets"][0]] * 2}, payload, Path("/tmp"))
        fresh_payload = {
            "contentSections": [{"id": "market", "kind": "section", "asset_key": "assets/image-1.png"}]
        }
        with self.assertRaisesRegex(ReleaseBridgeError, "missing its editorial visual envelope"):
            _materialize_visual_assets(Api(), {"visualAssets": []}, fresh_payload, Path("/tmp"))

    def test_visual_download_rejects_external_or_ambiguous_urls_before_network(self) -> None:
        api = GateXReleaseApi(
            "https://gatex.fund",
            "callback-token",
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
        for url in (
            "https://outside.example/image.png",
            "//outside.example/image.png",
            "/api/generation/jobs/11111111-1111-4111-8111-111111111111/assets/image.png?token=x",
            "/api/generation/jobs/not-a-uuid/assets/image.png",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ReleaseBridgeError, "invalid report visual"):
                    api.download_visual(url, Path("/tmp/never-written.png"))

    def test_legacy_pdf_body_has_a_designed_source_register(self) -> None:
        html = _legacy_body_html(
            {
                "title": "GateX simplified report",
                "contentSections": [
                    {"id": "market", "kind": "section", "heading": "Market", "paragraphs": ["Analysis."]},
                    {
                        "id": "sources",
                        "kind": "sources",
                        "heading": "Sources",
                        "footnotes": ["Primary research, https://example.com/source"],
                    },
                ],
            }
        )
        self.assertIn("SOURCE REGISTER", html)
        self.assertIn("https://example.com/source", html)
        self.assertNotIn("<strong>Sources</strong>", html)
        css = _body_css()
        self.assertIn(".whitepaper-sources", css)
        self.assertIn("break-before: page", css)

    def test_html_source_register_never_exposes_private_urls_or_hashes(self) -> None:
        parts = []
        _render_reference_sources(
            parts,
            [
                {
                    "title": "Private ledger aabbccddeeff",
                    "url": "private://gatex.collection/aabbccddeeff",
                    "origin": "rag",
                },
                {"title": "Public source", "url": "https://example.com/public", "origin": "web"},
            ],
            {"contents": "Contents"},
        )
        html = "".join(parts)
        self.assertIn("Private source", html)
        self.assertIn("https://example.com/public", html)
        self.assertNotIn("private://", html)
        self.assertNotIn("aabbccddeeff", html)


class SimplifiedAuditTests(unittest.TestCase):
    def _fixture(self, root: Path, presentation_format: str) -> None:
        payload = {
            "presentation_format": presentation_format,
            "evidenceAudit": {
                "manifest": {
                    "generation_profile": "source_channel",
                    "presentation_format": presentation_format,
                }
            },
            "key_takeaways": ["A retained takeaway."],
            "sections": [
                {
                    "title": "Market structure and operating implications",
                    "lead": "A grounded lead.",
                    "paragraphs": ["A developed analytical paragraph."],
                    "evidence": ["A retained source-backed evidence point."],
                }
            ],
            "exhibits": [],
            "action_steps": [{"action": "Act on the evidence."}],
            "references": [{"title": "Primary source", "url": "https://example.com/source"}],
        }
        files = {
            "web_report_payload.json": payload,
            "publication_contract.json": {},
            "research_fact_pack.json": {"source_count": 1},
            "evidence_ledger.json": [{"fact": "one"}, {"fact": "two"}, {"fact": "three"}],
            "storyline_plan.json": {
                "core_question": "What should the client understand?",
                "exhibit_narrative_rule": "No charts in simplified mode.",
            },
            "chart_data_needs.json": [],
            "sources.json": [{"url": "https://example.com/source", "content": "Grounded source content."}],
        }
        for name, value in files.items():
            (root / name).write_text(json.dumps(value), encoding="utf-8")
        (root / "index.html").write_text(
            "<html><body><article class='article-main'><h2>Key Takeaways</h2><h2>Contents</h2>"
            "<p>Analysis retained public sources.</p><details><summary>Sources</summary></details>"
            "</article></body></html>",
            encoding="utf-8",
        )
        (root / "assets").mkdir()
        (root / "assets" / "image-1.png").write_bytes(b"x" * 1_024)
        (root / "backup").mkdir()
        (root / "backup" / "image_prompts.json").write_text(
            json.dumps([{"id": "image-1", "status": "pollinations"}]),
            encoding="utf-8",
        )

    def _run(self, root: Path) -> tuple[int, dict]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["local_web_report_audit.py", str(root)]),
            patch.object(local_web_report_audit, "source_channel_report_quality_issues", return_value=[]),
            patch.object(local_web_report_audit, "report_content_quality_issues", return_value=[]),
            redirect_stdout(output),
        ):
            result = local_web_report_audit.main()
        return result, json.loads(output.getvalue())

    def test_simplified_profile_passes_without_legacy_exhibits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, "gatex_simplified_v1")
            result, output = self._run(root)
            self.assertEqual(result, 0, output["issues"])
            self.assertEqual(output["metrics"]["exhibits"], 0)

    def test_simplified_profile_rejects_any_additional_section_image(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, "gatex_simplified_v1")
            (root / "assets" / "image-10.png").write_bytes(b"unexpected")
            result, output = self._run(root)
            self.assertEqual(result, 1)
            self.assertTrue(
                any("image-10.png" in issue for issue in output["issues"]),
                output["issues"],
            )

    def test_standard_profile_keeps_existing_exhibit_gate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, "standard_v1")
            result, output = self._run(root)
            self.assertEqual(result, 1)
            self.assertTrue(any("expected 3-6 exhibits" in issue for issue in output["issues"]))


class SimplifiedEditorialGateTests(unittest.TestCase):
    def test_semantic_audit_passes_once_without_a_revision(self) -> None:
        pipeline = WebReportPipeline(object())
        passed = {
            "score": 86,
            "thesis_and_logic": 22,
            "evidence_and_citations": 22,
            "uncertainty_and_scenarios": 20,
            "strategic_usefulness": 22,
            "critical_issues": [],
            "revision_instructions": [],
        }
        pipeline._audit_report_content = unittest.mock.Mock(return_value=passed)
        pipeline._revise_report_draft = unittest.mock.Mock()

        result = pipeline._audit_simplified_report_content({"title": "GateX"}, {})

        self.assertIs(result, passed)
        pipeline._audit_report_content.assert_called_once_with({"title": "GateX"}, {})
        pipeline._revise_report_draft.assert_not_called()

    def test_semantic_audit_failure_stops_without_a_revision(self) -> None:
        pipeline = WebReportPipeline(object())
        pipeline._audit_report_content = unittest.mock.Mock(
            return_value={"status": "failed", "critical_issues": ["Unsupported conclusion"]}
        )
        pipeline._revise_report_draft = unittest.mock.Mock()

        with self.assertRaisesRegex(ReportQualityError, "Editorial audit held simplified publication"):
            pipeline._audit_simplified_report_content({"title": "GateX"}, {})

        pipeline._audit_report_content.assert_called_once()
        pipeline._revise_report_draft.assert_not_called()

    def test_semantic_audit_service_exception_stops_without_a_fallback_report(self) -> None:
        pipeline = WebReportPipeline(object())
        pipeline._audit_report_content = unittest.mock.Mock(side_effect=ValueError("invalid audit JSON"))
        pipeline._revise_report_draft = unittest.mock.Mock()

        with self.assertRaisesRegex(ReportQualityError, "audit was unavailable"):
            pipeline._audit_simplified_report_content({"title": "GateX"}, {})

        pipeline._revise_report_draft.assert_not_called()

    def test_final_quality_rescue_never_rewrites_a_simplified_report(self) -> None:
        pipeline = WebReportPipeline(object())
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline.source_profile = {}
        pipeline._revise_report_draft = unittest.mock.Mock()
        report = {"title": "GateX simplified report", "sections": []}
        issues = ["Section 1 needs 3-6 developed analytical paragraphs; found 2."]

        returned, remaining = pipeline._rescue_final_report(
            report,
            issues,
            storyline_plan={},
            topic="AI infrastructure",
            grounding_text="Grounded context",
            source_count=3,
            source_chunks={},
            approved_evidence=[],
        )

        self.assertIs(returned, report)
        self.assertEqual(remaining, issues)
        pipeline._revise_report_draft.assert_not_called()


if __name__ == "__main__":
    unittest.main()
