"""Offline operational contract: ordinary pending resumes the same stored source."""
import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import gatex_localization_runner as runner
from scripts.gatex_news_fullpool import warm_daily_pool
from tests.test_gatex_localization_news_fullpool import Api, status, SNAPSHOT, POOL


class NewsOperationalTests(unittest.TestCase):
    def test_complete_current_pool_is_read_only_and_does_not_capture(self):
        api = Api(status(50, 50))
        result = warm_daily_pool(api, clock=api.clock)
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['complete'])
        self.assertFalse(result['needsRetry'])
        self.assertTrue(all(method == 'GET' for method, _, _ in api.calls))

    def test_uncertain_warm_reads_back_same_pins_and_defers_without_another_post(self):
        def fail(_api, _body):
            raise runner.RunnerError('API_CONNECTION_FAILED')
        api = Api(status(50, 48), fail)
        with patch('scripts.gatex_news_fullpool.warm_news', return_value={'status': 'pending', 'snapshotHash': SNAPSHOT, 'stopReason': 'MAX_PASSES'}):
            result = warm_daily_pool(api, clock=api.clock, max_passes=1)
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['complete'])
        self.assertTrue(result['operationalHealthy'] and result['needsRetry'])
        self.assertEqual(result['pool']['pending'], 2)
        self.assertEqual(result['snapshotHash'], SNAPSHOT)
        self.assertEqual(result['poolHash'], POOL)
        self.assertEqual([method for method, _, _ in api.calls], ['GET', 'POST', 'GET'])
        self.assertFalse(any(route == '/capture' for _, route, _ in api.calls))

    def test_permanent_errors_remain_fatal_and_never_claim_complete(self):
        for error in [runner.RunnerError('API_HTTP_ERROR', 401), runner.RunnerError('API_HTTP_ERROR', 403),
                      runner.RunnerError('API_HTTP_ERROR', 404), runner.RunnerError('NEWS_POOL_CHANGED', 409),
                      runner.RunnerError('INVALID_NEWS_POOL_STATUS')]:
            result = runner.news_operational_result({'status': 'failed', 'pending': 2}, 'API_FAILURE', error)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(result['operationalHealthy'] or result['needsRetry'] or result['complete'])

    def test_temporary_failures_are_honest_deferred_results(self):
        for error in [runner.RunnerError('API_CONNECTION_FAILED'), runner.RunnerError('API_DEADLINE_EXCEEDED'),
                      runner.RunnerError('API_HTTP_ERROR', 429), runner.RunnerError('API_HTTP_ERROR', 503)]:
            result = runner.news_operational_result({'status': 'failed', 'ready': 34, 'pending': 2}, 'API_FAILURE', error)
            self.assertEqual(result['status'], 'deferred')
            self.assertTrue(result['operationalHealthy'] and result['needsRetry'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['pending'], 2)

    def test_fullpool_cli_pending_exits_zero_without_changing_counts(self):
        report = {'schema': 'gatex-news-fullpool-run/v1', 'status': 'pending', 'complete': False,
                  'operationalHealthy': True, 'needsRetry': True, 'public': {'ready': 34, 'pending': 2},
                  'pool': {'ready': 48, 'pending': 2}, 'snapshotHash': SNAPSHOT, 'poolHash': POOL}
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'NewsfeedApi'), \
             patch('scripts.gatex_news_fullpool.warm_daily_pool', return_value=copy.deepcopy(report)), \
             patch.object(sys, 'argv', ['runner', '--news-only', '--news-full-pool', '--runtime', folder+'/runtime', '--summary', folder+'/qa.json']), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
            self.assertEqual(json.loads(Path(folder, 'qa.json').read_text()), report)


if __name__ == '__main__': unittest.main()
