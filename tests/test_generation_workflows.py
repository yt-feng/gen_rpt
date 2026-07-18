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


if __name__ == "__main__":
    unittest.main()
