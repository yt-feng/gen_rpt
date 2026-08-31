from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PIL import Image

from gen_rpt.gatex_pdf_renderer import _body_css, _legacy_body_html
from gen_rpt.image_generator import generate_ai_image_assets
from gen_rpt.research_quality import ResearchFactPack
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_report_renderer import _render_reference_sources
from gen_rpt.web_report_pipeline import (
    ReportQualityError,
    WebReportPipeline,
    _source_channel_semantic_repair_scope,
    _source_channel_semantic_source_bound,
)
from tools.gatex_release_bridge import (
    GateXReleaseApi,
    ReleaseBridgeError,
    _materialize_visual_assets,
)
from tools.gatex_generation_bridge import _public_files
from tools import local_web_report_audit


def _semantic_words(seed: str, target: int, filler: str) -> str:
    words = seed.split()
    while len(words) < target:
        words.append(filler)
    return " ".join(words[:target])


def _semantic_source_channel_report() -> dict:
    sections = []
    for section_name in ("demand", "supply", "policy", "adoption", "options"):
        sections.append(
            {
                "title": (
                    f"Validated {section_name} evidence supports a bounded operating decision"
                ),
                "lead": _semantic_words(
                    f"The retained {section_name} record supports a conclusion first decision while "
                    "keeping uncertainty visible and preserving the operating boundary that "
                    "management must review before committing organisational capacity",
                    30,
                    f"{section_name}leadcontext",
                ),
                "paragraphs": [
                    _semantic_words(
                        f"The {section_name} {paragraph_name} connects the retained public record "
                        "to a clear operating mechanism and preserves the counterpoint the unresolved "
                        "constraint and the decision boundary that an accountable executive owner "
                        "must review before committing additional organisational capacity",
                        60,
                        f"{section_name}{paragraph_name}context",
                    )
                    for paragraph_name in ("finding", "mechanism", "boundary")
                ],
                "evidence": [
                    _semantic_words(
                        f"The retained {section_name} public record supports the bounded conclusion "
                        "and preserves the operating condition for independent review through "
                        "OpenAlex https://openalex.org/W1234567890",
                        45,
                        f"{section_name}publicsource",
                    ),
                    _semantic_words(
                        f"Independent {section_name} research corroborates the causal mechanism "
                        "while keeping the unresolved limitation visible through the retained DOI "
                        "source https://doi.org/10.1234/example.5678",
                        45,
                        f"{section_name}researchsource",
                    ),
                ],
                "evidence_internal": [f"frozen-{section_name}-evidence"],
                "so_what": _semantic_words(
                    "Management should assign an accountable owner document the unresolved condition "
                    "and preserve a clear pause gate until independent evidence confirms the operating "
                    "mechanism. The next review must record the counterpoint tested and the resulting response",
                    40,
                    f"{section_name}implication",
                ),
            }
        )
    return {
        "title": "Verified public evidence supports a bounded market response",
        "dek": "Independent corroboration narrows the decision without overstating certainty.",
        "intro": [
            _semantic_words(
                "The brief separates the supported conclusion from conditions that still require "
                "management verification and keeps independent corroboration visible for accountable decisions",
                50,
                "introcontext",
            )
        ],
        "key_takeaways": [
            _semantic_words(
                "Independent evidence supports a conditional operating response",
                25,
                "firsttakeaway",
            ),
            _semantic_words(
                "The causal mechanism remains more important than narrative momentum",
                25,
                "secondtakeaway",
            ),
            _semantic_words(
                "Management ownership and a documented pause gate preserve decision quality",
                25,
                "thirdtakeaway",
            ),
        ],
        "sections": sections,
        "action_steps": [
            {
                "horizon": "Decision gate",
                "action": f"Assign the {section_name} evidence owner",
                "success_metric": "Documented acceptance or pause decision",
                "rationale": _semantic_words(
                    "The retained evidence supports action only after an accountable owner confirms "
                    "the operating condition and records the decision boundary",
                    18,
                    f"{section_name}actionbasis",
                ),
            }
            for section_name in ("demand", "supply", "policy", "adoption")
        ],
        "methodology": _semantic_words(
            "The brief uses retained public sources and independent corroboration",
            25,
            "methodcontext",
        ),
        "evidence_quality": _semantic_words(
            "The public evidence is corroborated but the response remains conditional",
            20,
            "qualitycontext",
        ),
        "disclaimer": _semantic_words(
            "This editorial market research does not provide personalised advice",
            15,
            "disclaimercontext",
        ),
        "references": [
            {"title": "OpenAlex", "url": "https://openalex.org/W1234567890"},
            {"title": "DOI", "url": "https://doi.org/10.1234/example.5678"},
        ],
        "exhibits": [],
        "charts": [],
    }


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
    @staticmethod
    def _failed_semantic_audit() -> dict:
        return {
            "score": 76,
            "thesis_and_logic": 18,
            "evidence_and_citations": 21,
            "uncertainty_and_scenarios": 20,
            "strategic_usefulness": 17,
            "critical_issues": [
                "Section 5 lists options without comparing trade-offs or stating a priority.",
                "Action rationales do not connect directly to the report's presented evidence.",
            ],
            "revision_instructions": [
                "Compare the options' trade-offs and prioritize one bounded path.",
                "Link each action rationale directly to evidence already presented in the report.",
            ],
        }

    @staticmethod
    def _passed_semantic_audit() -> dict:
        return {
            "score": 88,
            "thesis_and_logic": 22,
            "evidence_and_citations": 22,
            "uncertainty_and_scenarios": 21,
            "strategic_usefulness": 23,
            "critical_issues": [],
            "revision_instructions": [],
        }

    @staticmethod
    def _semantic_patch() -> dict:
        option_paragraph = _semantic_words(
            "Management should give priority to the staged option because it preserves "
            "reversibility while the faster option improves learning speed but raises "
            "coordination burden and the slower option protects capacity but delays feedback. "
            "The preferred path remains bounded by the existing pause gate accountable "
            "ownership and review of the unresolved operating condition before commitment",
            65,
            "optiontradeoffcontext",
        )
        rationale_seeds = (
            "The demand finding supports accountable ownership because the retained record keeps the operating condition and pause gate visible",
            "The supply finding supports this action because the documented mechanism remains conditional on capacity and execution boundaries",
            "The policy finding supports this action because the retained counterpoint requires a clear owner and a documented pause decision",
            "The adoption finding supports this action because the visible operating limitation must be tested before organisational commitment",
        )
        rationale_fillers = (
            "demandevidencecontext",
            "supplyevidencecontext",
            "policyevidencecontext",
            "adoptionevidencecontext",
        )
        return {
            "sections": [
                {
                    "index": 5,
                    "paragraphs": [{"index": 3, "text": option_paragraph}],
                }
            ],
            "action_steps": [
                {
                    "index": index,
                    "rationale": _semantic_words(
                        seed,
                        22,
                        filler,
                    ),
                }
                for index, (seed, filler) in enumerate(
                    zip(rationale_seeds, rationale_fillers),
                    start=1,
                )
            ],
        }

    @staticmethod
    def _run_targeted_audit(
        pipeline: WebReportPipeline,
        report: dict,
    ) -> dict:
        return pipeline._audit_simplified_report_content(
            report,
            {"selected_modules": ["options and trade-offs", "management agenda"]},
            topic="Bounded market response",
            grounding_text=(
                "The validated public record supports a conditional operating response."
            ),
            source_count=2,
            source_chunks={},
            approved_evidence=[],
        )

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

    def test_source_simplified_semantic_failure_gets_one_targeted_repair_and_full_regate(
        self,
    ) -> None:
        report = _semantic_source_channel_report()
        original_evidence = copy.deepcopy(
            [
                (section["evidence"], section["evidence_internal"])
                for section in report["sections"]
            ]
        )
        original_references = copy.deepcopy(report["references"])
        client = Mock()
        client.chat_json.return_value = self._semantic_patch()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline._audit_report_content = Mock(
            side_effect=[
                self._failed_semantic_audit(),
                self._passed_semantic_audit(),
            ]
        )

        result = self._run_targeted_audit(pipeline, report)

        self.assertEqual(result, self._passed_semantic_audit())
        self.assertEqual(pipeline._audit_report_content.call_count, 2)
        self.assertEqual(client.chat_json.call_count, 1)
        self.assertIn("priority to the staged option", report["sections"][4]["paragraphs"][2])
        self.assertIn("demand finding", report["action_steps"][0]["rationale"])
        self.assertEqual(
            [
                (section["evidence"], section["evidence_internal"])
                for section in report["sections"]
            ],
            original_evidence,
        )
        self.assertEqual(report["references"], original_references)
        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("do not return or rewrite the whole report", prompt)
        self.assertIn("compare the credible options' trade-offs and state which option has priority", prompt)
        self.assertIn("link the action directly to qualitative evidence already presented", prompt)
        self.assertIn("evidence_read_only", prompt)
        self.assertEqual(
            client.chat_json.call_args.kwargs,
            {
                "temperature": 0.0,
                "max_tokens": 3_000,
                "fallback_max_tokens": 3_000,
                "strict_output_budget": True,
            },
        )

    def test_targeted_semantic_repair_second_failure_rethrows_original_error_once(
        self,
    ) -> None:
        report = _semantic_source_channel_report()
        original = copy.deepcopy(report)
        client = Mock()
        client.chat_json.return_value = self._semantic_patch()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline._audit_report_content = Mock(
            side_effect=[
                self._failed_semantic_audit(),
                {
                    **self._failed_semantic_audit(),
                    "critical_issues": ["The repaired options remain unprioritized."],
                },
            ]
        )

        with self.assertRaisesRegex(
            ReportQualityError,
            "Section 5 lists options without comparing trade-offs",
        ):
            self._run_targeted_audit(pipeline, report)

        self.assertEqual(report, original)
        self.assertEqual(client.chat_json.call_count, 1)
        self.assertEqual(pipeline._audit_report_content.call_count, 2)

    def test_targeted_semantic_repair_rejects_bound_and_non_target_mutations(
        self,
    ) -> None:
        adversarial_patches = {
            "new number and URL": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": "Prioritize the fast path by 2030 using https://invented.example as the new evidence boundary.",
                            }
                        ],
                    }
                ]
            },
            "evidence mutation": {
                "sections": [
                    {
                        "index": 5,
                        "evidence": ["Invented evidence"],
                        "paragraphs": [{"index": 3, "text": "A replacement."}],
                    }
                ]
            },
            "non-target section": {
                "sections": [
                    {
                        "index": 4,
                        "paragraphs": [{"index": 3, "text": "A replacement."}],
                    }
                ]
            },
            "non-target action field": {
                "action_steps": [
                    {
                        "index": 1,
                        "action": "Replace the action",
                        "rationale": "The demand finding supports this bounded action and keeps the existing operating condition visible to management.",
                    }
                ]
            },
            "new source label": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": _semantic_words(
                                    "NewBank data supports the preferred option despite unresolved execution boundaries and operating constraints",
                                    45,
                                    "inventedsourcecontext",
                                ),
                            }
                        ],
                    }
                ]
            },
            "new Reuters attribution": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": _semantic_words(
                                    "Reuters says the staged option deserves priority despite unresolved execution boundaries and operating constraints",
                                    45,
                                    "inventedattributioncontext",
                                ),
                            }
                        ],
                    }
                ]
            },
            "new Reuters proper name without enumerated attribution verb": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": _semantic_words(
                                    "Reuters supports the staged option despite unresolved execution boundaries and operating constraints",
                                    45,
                                    "inventedpropernamecontext",
                                ),
                            }
                        ],
                    }
                ]
            },
            "new McKinsey proper name": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": _semantic_words(
                                    "McKinsey favors the staged option despite unresolved execution boundaries and operating constraints",
                                    45,
                                    "inventedpropernamecontext",
                                ),
                            }
                        ],
                    }
                ]
            },
            "new Bloomberg proper name": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": _semantic_words(
                                    "Bloomberg backs the staged option despite unresolved execution boundaries and operating constraints",
                                    45,
                                    "inventedpropernamecontext",
                                ),
                            }
                        ],
                    }
                ]
            },
            "new Chinese source label": {
                "sections": [
                    {
                        "index": 5,
                        "paragraphs": [
                            {
                                "index": 3,
                                "text": "路透社支持分阶段方案，但现有执行边界与经营约束仍须由管理层核实后再作决定。",
                            }
                        ],
                    }
                ]
            },
        }
        for name, response in adversarial_patches.items():
            with self.subTest(name=name):
                report = _semantic_source_channel_report()
                original = copy.deepcopy(report)
                client = Mock()
                client.chat_json.return_value = response
                pipeline = WebReportPipeline(client)
                pipeline.source_profile = {"mode": "source_channel"}
                pipeline.report_mode = "gatex_simplified_v1"
                pipeline._audit_report_content = Mock(
                    return_value=self._failed_semantic_audit()
                )

                with self.assertRaisesRegex(
                    ReportQualityError,
                    "Editorial audit held simplified publication",
                ):
                    self._run_targeted_audit(pipeline, report)

                self.assertEqual(report, original)
                self.assertEqual(client.chat_json.call_count, 1)
                self.assertEqual(pipeline._audit_report_content.call_count, 1)

    def test_targeted_semantic_repair_rejects_the_wrong_action_index(self) -> None:
        report = _semantic_source_channel_report()
        original = copy.deepcopy(report)
        client = Mock()
        client.chat_json.return_value = {
            "action_steps": [
                {
                    "index": 1,
                    "rationale": "The demand finding supports this action because the retained operating condition remains visible to accountable management.",
                }
            ]
        }
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline._audit_report_content = Mock(
            return_value={
                **self._failed_semantic_audit(),
                "critical_issues": [
                    "Action 2 rationale does not connect directly to presented evidence."
                ],
                "revision_instructions": [
                    "Repair Action 2 rationale using only existing evidence."
                ],
            }
        )

        with self.assertRaisesRegex(
            ReportQualityError,
            "Action 2 rationale does not connect directly",
        ):
            self._run_targeted_audit(pipeline, report)

        self.assertEqual(report, original)
        self.assertEqual(client.chat_json.call_count, 1)
        self.assertEqual(pipeline._audit_report_content.call_count, 1)

    def test_semantic_repair_scope_is_exact_and_explicit_references_fail_closed(
        self,
    ) -> None:
        report = _semantic_source_channel_report()
        for correction in (
            "Repair Action step 2 rationale.",
            "Repair Action #2 rationale.",
            "Repair the second action rationale.",
            "Repair Action two rationale.",
        ):
            with self.subTest(correction=correction):
                self.assertEqual(
                    _source_channel_semantic_repair_scope(report, [correction]),
                    ([], [1]),
                )

        for correction in (
            "Repair Action 99 rationale.",
            "Section 99 options need a priority.",
            "Sections 4 and 5 need clearer trade-offs.",
            "Section 4 and 5 need clearer trade-offs.",
            "Actions 1 and 2 need evidence-linked rationales.",
        ):
            with self.subTest(correction=correction):
                self.assertEqual(
                    _source_channel_semantic_repair_scope(report, [correction]),
                    ([], []),
                )

        self.assertEqual(
            _source_channel_semantic_repair_scope(
                report,
                ["All action rationales need direct evidence linkage."],
            ),
            ([], [0, 1, 2, 3]),
        )
        self.assertEqual(
            _source_channel_semantic_repair_scope(
                report,
                ["An action rationale needs direct evidence linkage."],
            ),
            ([], []),
        )

    def test_semantic_source_name_freeze_rejects_addition_and_removal(self) -> None:
        for original, proposed in (
            (
                "The coverage frames the bounded operating decision.",
                "Reuters coverage frames the bounded operating decision.",
            ),
            (
                "The coverage frames the bounded operating decision.",
                "reuters supports the bounded operating decision.",
            ),
            (
                "The coverage frames the bounded operating decision.",
                "mckinsey favors the bounded operating decision.",
            ),
            (
                "The coverage frames the bounded operating decision.",
                "bloomberg backs the bounded operating decision.",
            ),
            (
                "The record remains uncertain while the operating boundary stays visible.",
                "The record remains uncertain while reuters supports the bounded decision.",
            ),
            (
                "The record remains uncertain and the operating boundary stays visible.",
                "The record remains uncertain and mckinsey favors the bounded decision.",
            ),
            (
                "Reuters coverage frames the bounded operating decision.",
                "The coverage frames the bounded operating decision.",
            ),
            (
                "现有公开材料支持有边界的经营判断。",
                "路透社支持有边界的经营判断。",
            ),
            (
                "路透社支持有边界的经营判断。",
                "现有公开材料支持有边界的经营判断。",
            ),
        ):
            with self.subTest(original=original, proposed=proposed):
                self.assertTrue(
                    _source_channel_semantic_source_bound(
                        original,
                        proposed,
                        source_labels=set(),
                        baseline_acronyms=set(),
                    )
                )
        self.assertFalse(
            _source_channel_semantic_source_bound(
                "The operating record frames the bounded decision.",
                "The demand finding supports the bounded decision.",
                source_labels=set(),
                baseline_acronyms=set(),
            )
        )

    def test_targeted_semantic_repair_audit_exception_is_fail_closed(self) -> None:
        report = _semantic_source_channel_report()
        original = copy.deepcopy(report)
        client = Mock()
        client.chat_json.return_value = self._semantic_patch()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline._audit_report_content = Mock(
            side_effect=[
                self._failed_semantic_audit(),
                ValueError("invalid second audit JSON"),
            ]
        )

        with self.assertRaisesRegex(
            ReportQualityError,
            "Section 5 lists options without comparing trade-offs",
        ):
            self._run_targeted_audit(pipeline, report)

        self.assertEqual(report, original)
        self.assertEqual(client.chat_json.call_count, 1)
        self.assertEqual(pipeline._audit_report_content.call_count, 2)

    def test_targeted_semantic_repair_is_inert_for_standard_and_rag_paths(self) -> None:
        for name in ("standard", "rag"):
            with self.subTest(name=name):
                report = _semantic_source_channel_report()
                client = Mock()
                pipeline = WebReportPipeline(client)
                pipeline.source_profile = {"mode": "source_channel"}
                if name == "standard":
                    pipeline.report_mode = "standard_v1"
                else:
                    pipeline.report_mode = "gatex_simplified_v1"
                    pipeline.rag_context = "Validated private context"
                pipeline._audit_report_content = Mock(
                    return_value=self._failed_semantic_audit()
                )

                with self.assertRaisesRegex(
                    ReportQualityError,
                    "Editorial audit held simplified publication",
                ):
                    self._run_targeted_audit(pipeline, report)

                client.chat_json.assert_not_called()
                pipeline._audit_report_content.assert_called_once()

    def test_source_simplified_audit_prompt_requires_tradeoffs_and_evidence_linkage(
        self,
    ) -> None:
        client = Mock()
        client.chat_json.return_value = self._passed_semantic_audit()
        pipeline = WebReportPipeline(client)
        pipeline.source_profile = {"mode": "source_channel"}
        pipeline.report_mode = "gatex_simplified_v1"

        pipeline._audit_report_content(
            _semantic_source_channel_report(),
            {"selected_modules": ["options and trade-offs"]},
        )

        prompt = client.chat_json.call_args.args[0][1]["content"]
        self.assertIn(
            "require an explicit comparison of their trade-offs and a reasoned priority",
            prompt,
        )
        self.assertIn(
            "Every action rationale must connect directly to qualitative or quantitative evidence already visible",
            prompt,
        )

        standard_client = Mock()
        standard_client.chat_json.return_value = self._passed_semantic_audit()
        WebReportPipeline(standard_client)._audit_report_content(
            _semantic_source_channel_report(),
            {},
        )
        standard_prompt = standard_client.chat_json.call_args.args[0][1]["content"]
        self.assertNotIn("Source-channel simplified decision rules", standard_prompt)

    def test_synthesis_decision_rules_are_inert_for_standard_source_channel(
        self,
    ) -> None:
        source = SourceDocument(
            title="Validated seed",
            url="https://example.com/seed",
            query="bounded response",
            snippet="A bounded public record.",
            content="Public evidence supports a conditional operating response.",
        )
        fact_pack = ResearchFactPack(
            topic="Bounded response",
            objective="Assess the operating mechanism",
            decision_question="What remains independently supported?",
            source_count=2,
            authoritative_source_count=1,
            source_domains=["example.com", "openalex.org"],
            source_refs=[],
            high_confidence_facts=[],
            numeric_facts=[],
            dated_facts=[],
            validation_issues=[],
        )

        standard_client = Mock()
        standard_client.chat_json.return_value = {"title": "Standard"}
        standard_pipeline = WebReportPipeline(standard_client)
        standard_pipeline.source_profile = {"mode": "source_channel"}
        standard_pipeline.report_mode = "standard_v1"
        standard_pipeline._synthesize_web_report(
            "Bounded response",
            {},
            [],
            [source],
            fact_pack,
            [],
            {},
        )
        standard_prompt = standard_client.chat_json.call_args.args[0][1]["content"]
        self.assertIn(
            "Every action has horizon, action, success_metric, and a rationale of at least 12 words. Aim for 25 words",
            standard_prompt,
        )
        self.assertNotIn("compare their trade-offs and state the priority option", standard_prompt)
        self.assertNotIn("Each rationale must connect directly to evidence", standard_prompt)

        simplified_client = Mock()
        simplified_client.chat_json.return_value = {"title": "Simplified"}
        simplified_pipeline = WebReportPipeline(simplified_client)
        simplified_pipeline.source_profile = {"mode": "source_channel"}
        simplified_pipeline.report_mode = "gatex_simplified_v1"
        simplified_pipeline._synthesize_web_report(
            "Bounded response",
            {},
            [],
            [source],
            fact_pack,
            [],
            {},
        )
        simplified_prompt = simplified_client.chat_json.call_args.args[0][1]["content"]
        self.assertIn("compare their trade-offs and state the priority option", simplified_prompt)
        self.assertIn("Each rationale must connect directly to evidence", simplified_prompt)

    def test_any_simplified_synthesis_failure_is_fail_closed(self) -> None:
        pipeline = WebReportPipeline(object())
        pipeline.report_mode = "gatex_simplified_v1"
        pipeline.source_profile = {}
        pipeline.rag_required = False

        self.assertTrue(pipeline._synthesis_error_must_fail_closed(ValueError("provider response failed")))

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
