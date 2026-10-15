"""Exercise the actual news workflow guard without GitHub or provider calls."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/gatex-news-maintenance.yml'
DIGEST = ROOT / '.github/workflows/run_gatex_newsfeed_digest.yml'
MANUAL = ROOT / '.github/workflows/gatex-content-localization.yml'




class NewsScheduleTests(unittest.TestCase):
    def test_real_schedules_and_manual_dispatch_can_run_news_only(self):
        text = WORKFLOW.read_text()
        self.assertEqual(re.findall(r"^\s+- cron: ['\"]([^'\"]+)['\"]", text, re.MULTILINE), ['5 0 * * *'])
        self.assertIn('ref: ${{ github.event.repository.default_branch }}', text)
        self.assertIn('--news-only', text)
        self.assertIn('--news-max-passes 0', text)
        self.assertIn('--news-budget-seconds 14400', text)
        self.assertNotRegex(text, r'--(?:sample-id|pdf-limit|force|inventory-only|ocr-diagnostic-only)\b')

    def test_daily_capture_has_no_frequent_or_digest_event_fallback(self):
        text = WORKFLOW.read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertNotIn('workflow_run:', text)
        self.assertNotIn('17,47', text)
        self.assertIn('--news-only --news-full-pool --news-capture-daily', text)
        # Mail scheduling remains owned by its separate existing workflow.
        self.assertIn('name: Run GateX Newsfeed Digest', DIGEST.read_text())

    def test_guard_and_manual_job_share_the_mutex_and_never_receive_model_keys(self):
        text = WORKFLOW.read_text()
        manual_news = MANUAL.read_text().split('  warm-news:\n', 1)[1]
        for source in [text, manual_news]:
            self.assertIn('group: gatex-news-localization', source)
            self.assertIn('cancel-in-progress: false', source)
            self.assertEqual(re.findall(r'secrets\.([A-Z_]+)', source), ['GATEX_NEWSFEED_CRON_SECRET'])
        self.assertIn('contents: read', text)
        self.assertNotIn('/digest/run', text)
        self.assertNotIn('pull_request:', text)

    def test_only_safe_qa_leaves_the_runner_and_private_working_files_are_removed(self):
        text = WORKFLOW.read_text()
        artifact = text.split('uses: actions/upload-artifact@v4', 1)[1].split('      - name:', 1)[0]
        self.assertIn('path: ${{ runner.temp }}/gatex-news-localization-qa/summary.json', artifact)
        self.assertNotIn('localization-private', artifact)
        cleanup = text.split('      - name: Remove private working documents', 1)[1]
        self.assertIn('if: always()', cleanup)
        self.assertIn('rm -rf -- "$RUNNER_TEMP/gatex-news-localization-private"', cleanup)
        self.assertIn("- '.github/workflows/gatex-news-maintenance.yml'", MANUAL.read_text())


if __name__ == '__main__':
    unittest.main()
