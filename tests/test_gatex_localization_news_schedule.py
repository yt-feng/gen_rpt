"""Exercise the actual news workflow guard without GitHub or provider calls."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/gatex-news-maintenance.yml'
DIGEST = ROOT / '.github/workflows/run_gatex_newsfeed_digest.yml'
MANUAL = ROOT / '.github/workflows/gatex-content-localization.yml'


def guard(event, *, upstream='schedule', branch='main', default='main', repository='yt-feng/gen_rpt', origin='yt-feng/gen_rpt'):
    condition = re.search(r'^    if: (.+)$', WORKFLOW.read_text(), re.MULTILINE).group(1)
    context = {'github.event_name': event, 'github.event.workflow_run.event': upstream,
               'github.event.workflow_run.head_branch': branch,
               'github.event.repository.default_branch': default,
               'github.event.workflow_run.head_repository.full_name': origin,
               'github.repository': repository}
    for key in sorted(context, key=len, reverse=True):
        condition = condition.replace(key, repr(context[key]))
    condition = condition.replace('&&', ' and ').replace('||', ' or ')
    parsed = ast.parse(condition.strip(), mode='eval')
    allowed = (ast.Expression, ast.Constant, ast.BoolOp, ast.And, ast.Or,
               ast.Compare, ast.Eq, ast.NotEq)
    if any(not isinstance(node, allowed) for node in ast.walk(parsed)):
        raise AssertionError('Unexpected workflow guard syntax')
    return bool(eval(compile(parsed, str(WORKFLOW), 'eval'), {'__builtins__': {}}, {}))


class NewsScheduleTests(unittest.TestCase):
    def test_real_schedules_and_manual_dispatch_can_run_news_only(self):
        self.assertTrue(guard('schedule'))
        self.assertTrue(guard('workflow_dispatch'))
        text = WORKFLOW.read_text()
        self.assertEqual(re.findall(r"^\s+- cron: ['\"]([^'\"]+)['\"]", text, re.MULTILINE), ['17,47 * * * *'])
        self.assertIn('ref: ${{ github.event.repository.default_branch }}', text)
        self.assertIn('--news-only', text)
        self.assertIn('--news-max-passes 12', text)
        self.assertIn('--news-budget-seconds 600', text)
        self.assertNotRegex(text, r'--(?:sample-id|pdf-limit|force|inventory-only|ocr-diagnostic-only)\b')

    def test_fallback_accepts_only_the_same_repositorys_default_branch_scheduled_event(self):
        self.assertTrue(guard('workflow_run'))
        self.assertTrue(guard('workflow_run', branch='trunk', default='trunk'))
        for values in [dict(upstream='pull_request'), dict(upstream='workflow_dispatch'),
                       dict(branch='feature'), dict(origin='someone/fork'),
                       dict(default='trunk'), dict(origin=''), dict(upstream='')]:
            with self.subTest(values=values):
                self.assertFalse(guard('workflow_run', **values))
        text = WORKFLOW.read_text()
        # The platform's upstream-name filter must follow the actual owned file.
        name = re.search(r'^name: (.+)$', DIGEST.read_text(), re.MULTILINE).group(1)
        workflows = ast.literal_eval(re.search(r'^    workflows: (.+)$', text, re.MULTILINE).group(1))
        self.assertEqual(workflows, [name])
        self.assertIn('types: [completed]', text)
        self.assertNotIn('workflow_run.conclusion', text, 'email success or failure must not gate independent news maintenance')

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
