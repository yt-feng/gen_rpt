"""Evaluate actual GitHub job conditions against intended execution modes."""
import ast
import json
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/gatex-content-localization.yml'


def enabled(job, event='workflow_dispatch', *, inventory=False, news=False, diagnostic=False, sample='', cron=''):
    block = WORKFLOW.read_text().split('  ' + job + ':\n', 1)[1]
    condition = next(line.strip()[4:] for line in block.splitlines() if line.strip().startswith('if: '))
    context = {'github.event_name': event, 'github.event.schedule': cron,
        'inputs.inventory_only': inventory, 'inputs.news_only': news,
        'inputs.ocr_diagnostic_only': diagnostic, 'inputs.sample_id': sample}
    for key, value in context.items():
        condition = condition.replace(key, repr(value))
    condition = condition.replace('&&', ' and ').replace('||', ' or ')
    condition = re.sub(r'!(?!=)', ' not ', condition).strip()
    parsed = ast.parse(condition, mode='eval')
    allowed = (ast.Expression, ast.Constant, ast.BoolOp, ast.And, ast.Or,
        ast.UnaryOp, ast.Not, ast.Compare, ast.Eq, ast.NotEq)
    if any(not isinstance(node, allowed) for node in ast.walk(parsed)):
        raise AssertionError('Unexpected workflow expression syntax')
    return bool(eval(compile(parsed, str(WORKFLOW), 'eval'), {'__builtins__': {}}, {}))


def environment_value(key, supplied, event='workflow_dispatch'):
    line = re.search(r'^\s+' + re.escape(key) + r':\s*\$\{\{(.*?)\}\}\s*$', WORKFLOW.read_text(), re.MULTILINE)
    if not line:
        raise AssertionError('Workflow environment expression missing')
    condition = line.group(1)
    for name, value in {'github.event_name': event, 'inputs.news_max_passes': supplied,
                        'inputs.news_budget_seconds': supplied}.items():
        condition = condition.replace(name, repr(value))
    condition = condition.replace('&&', ' and ').replace('||', ' or ').strip()
    parsed = ast.parse(condition, mode='eval')
    allowed = (ast.Expression, ast.Constant, ast.BoolOp, ast.And, ast.Or,
               ast.Compare, ast.Eq, ast.Call, ast.Name, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(parsed)) or any(
        isinstance(node, ast.Name) and node.id not in {'format', 'toJSON'} for node in ast.walk(parsed)):
        raise AssertionError('Unexpected workflow environment expression')
    functions = {'__builtins__': {}, 'format': lambda template, value: template.format(value),
                 'toJSON': lambda value: json.dumps(value, separators=(',', ':'))}
    return str(eval(compile(parsed, str(WORKFLOW), 'eval'), functions, {}))


class WorkflowModeTests(unittest.TestCase):
    def test_exact_pdf_sample_runs_pdf_without_implicit_news_warming(self):
        self.assertTrue(enabled('localize', sample='managed:sample'))
        self.assertFalse(enabled('warm-news', sample='managed:sample'))

    def test_explicit_news_only_runs_news_without_pdf_and_inventory_takes_priority(self):
        self.assertTrue(enabled('warm-news', news=True))
        self.assertFalse(enabled('localize', news=True))
        self.assertTrue(enabled('warm-news', news=True, sample='ignored-sample'))
        self.assertFalse(enabled('warm-news', inventory=True, news=True))
        self.assertFalse(enabled('localize', inventory=True, sample='managed:sample'))

    def test_diagnostic_never_warms_news_and_bulk_retains_both_jobs(self):
        self.assertFalse(enabled('warm-news', diagnostic=True, sample='managed:sample'))
        self.assertTrue(enabled('localize', diagnostic=True, sample='managed:sample'))
        self.assertTrue(enabled('warm-news'))
        self.assertTrue(enabled('localize'))

    def test_daily_pdf_and_news_crons_are_separate_and_pr_runs_neither(self):
        self.assertTrue(enabled('localize', 'schedule', cron='23 4 * * *'))
        self.assertFalse(enabled('warm-news', 'schedule', cron='23 4 * * *'))
        self.assertTrue(enabled('warm-news', 'schedule', cron='7,37 * * * *'))
        self.assertFalse(enabled('localize', 'schedule', cron='7,37 * * * *'))
        self.assertFalse(enabled('localize', 'pull_request'))
        self.assertFalse(enabled('warm-news', 'pull_request'))

    def test_only_independent_news_schedule_is_enabled(self):
        # Comments and disabled PDF examples cannot count as real trigger entries.
        configured = re.findall(r"^\s+- cron: ['\"]([^'\"]+)['\"]", WORKFLOW.read_text(), re.MULTILINE)
        self.assertEqual(configured, ['7,37 * * * *'])
        self.assertTrue(enabled('warm-news','schedule',cron=configured[0]))
        self.assertFalse(enabled('localize','schedule',cron=configured[0]))
        news_job = WORKFLOW.read_text().split('  warm-news:\n',1)[1]
        self.assertIn('secrets.GATEX_NEWSFEED_CRON_SECRET',news_job)
        self.assertNotIn('secrets.APIMART_API_KEY',news_job)
        self.assertNotIn('secrets.DEEPSEEK_API_KEY',news_job)

    def test_numeric_dispatch_controls_forward_strings_and_numbers_without_json_quotes(self):
        for key, text in [('GATEX_NEWS_MAX_PASSES', '12'), ('GATEX_NEWS_BUDGET_SECONDS', '600')]:
            for value in [text, int(text)]:
                with self.subTest(key=key, supplied=value):
                    rendered = environment_value(key, value)
                    self.assertEqual(rendered, text)
                    self.assertEqual(int(rendered), int(text))
            self.assertEqual(environment_value(key, 0), '0', 'invalid zero must reach runner validation')
            self.assertEqual(environment_value(key, '0'), '0')
        self.assertEqual(environment_value('GATEX_NEWS_MAX_PASSES', None, 'schedule'), '12')
        self.assertEqual(environment_value('GATEX_NEWS_BUDGET_SECONDS', None, 'schedule'), '600')


if __name__ == '__main__':
    unittest.main()
