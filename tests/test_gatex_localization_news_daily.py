"""Fixed-day acquisition is separate from source-pinned translation retries."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('daily_runner', Path(__file__).resolve().parents[1] / 'scripts/gatex_localization_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
A, B = 'a' * 64, 'b' * 64


def state(ready=0, digest=A, day='2026-10-15'):
    base = dict(ok=True, version='reviewed-v2', snapshotHash=digest, total=36, ready=ready,
                pending=36-ready, failed=0, complete=ready == 36)
    return {**base, 'candidate': {**base, 'fixed': True},
            'published': dict(available=ready == 36, complete=ready == 36, snapshotHash=digest if ready == 36 else None,
                              total=36 if ready == 36 else 0, ready=ready if ready == 36 else 0),
            'daily': dict(day=day, currentDay='2026-10-15', available=True,
                          capturedAt='2026-10-15T00:00:00Z', poolTotal=427, snapshotHash=digest)}


class NewsDailyTests(unittest.TestCase):
    def run_daily(self, request, *, sleep=lambda _: None, **options):
        api = runner.NewsfeedApi('offline-token', sleep=sleep)
        api.request = request
        return runner.warm_news(api, require_publication=True, capture_daily=True, **options)

    def test_capture_once_then_every_translation_post_is_source_pinned(self):
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route, kwargs.get('json')))
            return state(36 if method == 'POST' and route == '/warm' else 0)
        report = self.run_daily(request)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(calls, [('GET', '/status', None), ('POST', '/capture', {}),
                                 ('POST', '/warm', {'expectedSnapshotHash': A})])
        self.assertEqual(report['daily']['poolTotal'], 427)

    def test_duplicate_daily_run_reads_complete_day_without_translation(self):
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route)); return state(36)
        self.assertEqual(self.run_daily(request)['status'], 'passed')
        self.assertEqual(calls, [('GET', '/status'), ('POST', '/capture')])

    def test_unknown_capture_outcome_is_read_back_without_second_capture(self):
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route))
            if route == '/capture': raise runner.RunnerError('API_CONNECTION_FAILED')
            return state(36)
        self.assertEqual(self.run_daily(request)['status'], 'passed')
        self.assertEqual(calls, [('GET', '/status'), ('POST', '/capture'), ('GET', '/status')])

    def test_previous_day_or_mismatched_daily_source_cannot_warm(self):
        for kind in ['yesterday', 'wrong-hash', 'unavailable']:
            calls = []
            def request(method, route, **kwargs):
                calls.append(route)
                result = state(day='2026-10-14' if kind == 'yesterday' else '2026-10-15')
                if kind == 'wrong-hash': result['daily']['snapshotHash'] = B
                if kind == 'unavailable': result['daily']['available'] = False
                return result
            with self.subTest(kind=kind):
                report = self.run_daily(request)
                self.assertEqual(report['stopReason'], 'DAILY_SOURCE_UNAVAILABLE')
                self.assertNotIn('/warm', calls)

    def test_mid_run_new_day_conflict_does_not_recapture_or_change_pin(self):
        calls = []
        def request(method, route, **kwargs):
            calls.append(route)
            if route == '/warm': raise runner.RunnerError('API_HTTP_ERROR', 409, 'NEWS_SNAPSHOT_CHANGED')
            return state()
        report = self.run_daily(request)
        self.assertEqual(report['stopReason'], 'SNAPSHOT_CHANGED')
        self.assertEqual(report['expectedSnapshotHash'], A)
        self.assertEqual(calls.count('/capture'), 1)

    def test_active_clock_capture_is_read_until_ready_without_another_post(self):
        calls, ticks = [], [0]
        def waiting():
            result = state(); result['daily'].update(available=False, day=None,
                acquisition=dict(state='acquiring', attempts=1, retryInSeconds=2))
            return result
        def request(method, route, **kwargs):
            calls.append((method, route))
            if route == '/capture': raise runner.RunnerError('API_HTTP_ERROR', 503)
            return state(36) if ticks[0] >= 2 else waiting()
        report = self.run_daily(request, clock=lambda: ticks[0], sleep=lambda seconds: ticks.__setitem__(0, ticks[0]+seconds))
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(calls.count(('POST', '/capture')), 1)
        self.assertNotIn(('POST', '/warm'), calls)

    def test_failed_capture_waits_for_server_backoff_then_recovers_only_once(self):
        calls, ticks, posts = [], [0], [0]
        def request(method, route, **kwargs):
            calls.append((method, route))
            if route == '/capture':
                posts[0] += 1
                if posts[0] == 2:
                    raise runner.RunnerError('API_CONNECTION_FAILED')
            if posts[0] == 2: return state(36)
            result = state(); result['daily'].update(available=False, day=None,
                acquisition=dict(state='failed', attempts=1, retryInSeconds=max(0, 3-ticks[0])))
            return result
        report = self.run_daily(request, clock=lambda: ticks[0], sleep=lambda seconds: ticks.__setitem__(0, ticks[0]+seconds))
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(posts[0], 2)
        self.assertGreaterEqual(ticks[0], 5)
        self.assertEqual(calls[-1], ('GET', '/status'), 'unknown recovery result must be read back')

    def test_exhausted_server_capture_budget_does_not_create_a_third_acquisition(self):
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route))
            result = state(); result['daily'].update(available=False, day=None,
                acquisition=dict(state='failed', attempts=2, retryInSeconds=0))
            return result
        report = self.run_daily(request)
        self.assertEqual(report['stopReason'], 'DAILY_SOURCE_UNAVAILABLE')
        self.assertEqual(calls.count(('POST', '/capture')), 1)

    def test_waiting_for_the_daily_clock_shares_the_existing_total_deadline(self):
        calls, ticks = [], [0]
        def request(method, route, **kwargs):
            calls.append((method, route))
            result = state(); result['daily'].update(available=False, day=None,
                acquisition=dict(state='acquiring', attempts=1, retryInSeconds=120))
            return result
        report = self.run_daily(request, budget_seconds=30, clock=lambda: ticks[0], sleep=lambda seconds: ticks.__setitem__(0, ticks[0]+seconds))
        self.assertEqual(report['stopReason'], 'DEADLINE')
        self.assertLess(ticks[0], 30)
        self.assertEqual(calls.count(('POST', '/capture')), 1)

    def test_api_route_allowlist_has_no_email_or_source_override(self):
        self.assertEqual(runner.NewsfeedApi.routes, {'/status', '/capture', '/warm'})


if __name__ == '__main__':
    unittest.main()
