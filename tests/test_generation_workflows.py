from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GenerationWorkflowTests(unittest.TestCase):
    def test_legacy_workflow_is_manual_only(self):
        workflow = (ROOT / ".github/workflows/generate_deep_research.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIsNone(re.search(r"^  push:\s*$", workflow, re.MULTILINE))

    def test_v2_prints_generator_log_before_failing(self):
        workflow = (ROOT / ".github/workflows/generate_deep_research_v2.yml").read_text(encoding="utf-8")

        self.assertIn('GEN_STATUS="$?"', workflow)
        self.assertIn('cat "$GEN_LOG"', workflow)
        self.assertIn("::error title=Generator failure::", workflow)
        self.assertLess(workflow.index('cat "$GEN_LOG"'), workflow.index('exit "$GEN_STATUS"'))

    def test_gatex_pdf_release_is_manual_version_bound_and_cjk_ready(self):
        workflow = (ROOT / ".github/workflows/render_gatex_release_pdf.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("item_id:", workflow)
        self.assertIn("version_id:", workflow)
        self.assertIn("content_checksum:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("fonts-noto-cjk", workflow)
        self.assertIn("GATEX_GENERATION_CALLBACK_SECRET", workflow)
        self.assertIsNone(re.search(r"^  (push|pull_request):\s*$", workflow, re.MULTILINE))

    def test_gatex_whitepaper_uses_sol_pro_xhigh_without_silent_deepseek_fallback(self):
        workflow = (ROOT / ".github/workflows/generate_gatex_whitepaper.yml").read_text(encoding="utf-8")

        self.assertIn('default: "gpt-5.6-sol"', workflow)
        self.assertIn('APIMART_USE_RESPONSES: "true"', workflow)
        self.assertIn("APIMART_REASONING_EFFORT: xhigh", workflow)
        self.assertIn("APIMART_REASONING_MODE: pro", workflow)
        self.assertIn('APIMART_MAX_OUTPUT_TOKENS: "64000"', workflow)
        self.assertIn('GATEX_EDITORIAL_FALLBACK_MODEL: ""', workflow)
        self.assertIn("Verify APIMart Sol Pro route", workflow)


if __name__ == "__main__":
    unittest.main()
