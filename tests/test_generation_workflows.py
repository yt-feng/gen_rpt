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

    def test_gatex_whitepaper_uses_deepseek_without_apimart_dependency(self):
        workflow = (ROOT / ".github/workflows/generate_gatex_whitepaper.yml").read_text(encoding="utf-8")

        self.assertTrue(any(m in workflow for m in ['default: "deepseek-chat"', 'default: "deepseek-v4-pro"']))
        self.assertTrue(any(m in workflow for m in ['GATEX_RESEARCH_MODEL: deepseek-chat', 'GATEX_RESEARCH_MODEL: deepseek-v4-pro']))
        self.assertTrue(any(p in workflow for p in ['GATEX_IMAGE_PROVIDER: pollinations', 'GATEX_IMAGE_PROVIDER: apimart']))
        self.assertIn('GATEX_EDITORIAL_FALLBACK_MODEL: ""', workflow)




if __name__ == "__main__":
    unittest.main()
