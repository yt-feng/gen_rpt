"""Explicit PDF text/vision routing; no provider or network requests."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from gen_rpt.gatex_pdf_localization import (
    Block, LocalizationError, ModelClient, OCR_POLICY_VERSION, PROMPT_VERSION,
    PROGRESS_SCHEMA, _translation_state,
)


class ProviderTests(unittest.TestCase):
    def client(self, responses=None, **extra):
        calls = []
        pending = iter(responses or [{"fixture": True}])

        class Response:
            status_code = 200
            def __init__(self, value): self.value = value
            def json(self):
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.value, ensure_ascii=False)}}]}
            def close(self): pass

        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response(next(pending))

        with patch.dict(os.environ, {"GATEX_PDF_TRANSLATION_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "  deepseek-fixture\n", "APIMART_API_KEY": "different-unused-fixture",
                "APIMART_BASE_URL": "https://unused.invalid", **extra}, clear=True):
            client = ModelClient(acceptance_mode="strict", session_factory=Session, sleep=lambda _: None)
        return client, calls

    def test_deepseek_text_uses_pro_and_its_own_key_endpoint(self):
        client, calls = self.client()
        self.assertEqual(client._once([{"role": "user", "content": "fixture"}]), {"fixture": True})
        url, request = calls[0]
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer deepseek-fixture")
        self.assertEqual(request["json"]["model"], "deepseek-v4-pro")
        self.assertEqual(request["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(request["json"]["response_format"], {"type": "json_object"})
        self.assertFalse(request["allow_redirects"])

    def test_actual_image_parts_use_flash_but_source_strings_cannot_choose_vision(self):
        client, calls = self.client([{}, {}, {}])
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture", "detail": "high"}}
        for context in ("full image", "independent contextual crop"):
            client._once([{"role": "user", "content": [{"type": "text", "text": context}, image]}])
        client._once([{"role": "user", "content": json.dumps({"source": image})}])
        self.assertEqual([call[1]["json"]["model"] for call in calls], ["deepseek-flash", "deepseek-flash", "deepseek-v4-pro"])
        self.assertTrue(all(call[1]["json"]["thinking"] == {"type": "disabled"} for call in calls))

    def test_generation_and_fresh_independent_review_both_use_pro(self):
        client, calls = self.client([
            {"blocks": [{"id": "b", "text": "需求可能增长。", "retainedEnglish": []}]},
            {"blocks": [{"id": "b", "complete": True, "faithful": True, "professionalChinese": True, "ownedTerminology": True, "qualityIssues": [], "issues": []}]},
        ])
        result = client.translate([Block("b", 0, "Demand may increase.", (0, 0, 100, 30))])
        self.assertTrue(result["b"]["reviewed"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([call[1]["json"]["model"] for call in calls], ["deepseek-v4-pro"] * 2)
        generation, review = [call[1]["json"]["messages"] for call in calls]
        self.assertIn("Translate every supplied", generation[0]["content"])
        self.assertIn("Independently compare", review[0]["content"])
        self.assertNotIn(generation[0], review)
        self.assertIn("original English edition", generation[0]["content"])
        self.assertIn("must not assert authorization of the Chinese edition", review[0]["content"])

    def test_missing_deepseek_key_never_uses_an_available_apimart_key(self):
        client, calls = self.client(DEEPSEEK_API_KEY="")
        with self.assertRaisesRegex(LocalizationError, "MODEL_KEY_MISSING"):
            client._once([{"role": "user", "content": "fixture"}])
        self.assertEqual(calls, [])

    def test_invalid_provider_or_unsupported_pro_vision_fail_before_transport(self):
        for environment, code in [({"GATEX_PDF_TRANSLATION_PROVIDER": "unknown"}, "INVALID_MODEL_PROVIDER"),
                ({"GATEX_PDF_VISION_MODEL": "deepseek-v4-pro"}, "INVALID_MODEL_CONFIGURATION"),
                ({"GATEX_PDF_TEXT_MODEL": "not-a-reviewed-model"}, "INVALID_MODEL_CONFIGURATION")]:
            with self.subTest(environment=environment), self.assertRaisesRegex(LocalizationError, code):
                self.client(**environment)

    def test_http402_is_safe_and_does_not_switch_provider_or_read_error_body(self):
        calls = []
        class Response:
            status_code = 402
            def json(self): raise AssertionError("error body must not be read")
            def close(self): pass
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def post(self, url, **kwargs): calls.append(url); return Response()
        client, _ = self.client()
        client.session_factory = Session
        with self.assertRaisesRegex(LocalizationError, "MODEL_HTTP_402"):
            client._once([{"role": "user", "content": "private source"}])
        self.assertEqual(calls, ["https://api.deepseek.com/v1/chat/completions"])

    def test_provider_policy_invalidates_text_and_artifact_but_preserves_verified_ocr(self):
        client, _ = self.client()
        valid = {"verified": True, "uncertainRegions": [], "lines": [{"text": "Demand", "bbox": [0, 0, 100, 100]}],
            "coverage": {"schema": OCR_POLICY_VERSION}}
        old = {"schema": PROGRESS_SCHEMA, "sourceSha256": "a" * 64, "promptVersion": PROMPT_VERSION,
            "model": client.model, "translationPolicy": {**client.translation_policy, "provider": "apimart"},
            "blocks": {"old": {"reviewed": True}}, "ocr": {"c" * 64: valid, "d" * 64: {**valid, "verified": False}},
            "status": "complete", "artifactSha256": "b" * 64, "privateUnknownField": {"keep": True}}
        original = copy.deepcopy(old)
        updated = _translation_state(old, "a" * 64, client.model, False, require_ocr_coverage=True,
            translation_policy=client.translation_policy)
        self.assertEqual(updated["blocks"], {})
        self.assertEqual(updated["status"], "in-progress")
        self.assertNotIn("artifactSha256", updated)
        self.assertEqual(updated["ocr"], {"c" * 64: valid})
        self.assertEqual(updated["translationPolicy"], client.translation_policy)
        self.assertEqual(updated["privateUnknownField"], old["privateUnknownField"])
        self.assertEqual(old, original)

    def test_same_policy_reuses_text_and_force_clears_ocr(self):
        client, _ = self.client()
        old = _translation_state(None, "a" * 64, client.model, False, require_ocr_coverage=True,
            translation_policy=client.translation_policy)
        old["blocks"] = {"valid": {"reviewed": True}}
        old["ocr"] = {"c" * 64: {"verified": True, "uncertainRegions": [], "lines": [], "coverage": {"schema": OCR_POLICY_VERSION}}}
        updated = _translation_state(old, "a" * 64, client.model, False, require_ocr_coverage=True,
            translation_policy=client.translation_policy)
        self.assertEqual(updated["blocks"], old["blocks"])
        self.assertEqual(updated["ocr"], old["ocr"])
        forced = _translation_state(old, "a" * 64, client.model, True, require_ocr_coverage=True,
            translation_policy=client.translation_policy)
        self.assertEqual(forced["blocks"], {})
        self.assertEqual(forced["ocr"], {})

    def test_ocr_diagnostic_cli_removes_both_provider_keys(self):
        location = Path(__file__).resolve().parents[1] / "scripts/gatex_localization_runner.py"
        spec = importlib.util.spec_from_file_location("pdf_provider_runner", location)
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        def diagnostic(*args, **kwargs):
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            self.assertNotIn("APIMART_API_KEY", os.environ)
            return {"status": "passed", "scope": "diagnostic", "translatedCount": 0, "publishedCount": 0}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,
                {"DEEPSEEK_API_KEY": "unused-deepseek-fixture", "APIMART_API_KEY": "unused-apimart-fixture"}), \
                patch.object(sys, "argv", ["runner", "--ocr-diagnostic-only", "--sample-id", "sample", "--expected-source-sha256", "a" * 64,
                    "--runtime", directory, "--summary", directory + "/qa.json"]), \
                patch.object(runner, "GatexApi"), patch.object(runner, "engine"), \
                patch.object(runner, "run_ocr_diagnostic", side_effect=diagnostic), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)


if __name__ == "__main__": unittest.main()
