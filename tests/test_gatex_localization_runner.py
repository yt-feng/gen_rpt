import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'gatex_localization_runner.py'
spec = importlib.util.spec_from_file_location('gatex_localization_runner', PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
ORIGINAL = b'%PDF-1.7\nOriginal English report'
DIGEST = runner.sha256(ORIGINAL)


def qa():
    return {'schema': runner.QA_SCHEMA, 'passed': True, 'sourcePageCount': 2,
        'pageCount': 2, 'sourceBlockCount': 3, 'translatedBlockCount': 3,
        'missingBlocks': 0, 'overflowCount': 0, 'unresolvedEnglishCount': 0,
        'figuresPreserved': True, 'numericChecksPassed': True,
        'pages': [{'sourcePage': 1, 'targetPages': [1]}, {'sourcePage': 2, 'targetPages': [2]}]}


def item(identifier, status='pending', **extra):
    return {'id': identifier, 'status': status, 'sourceVersion': 'v1',
        'sourceUrl': '/api/internal/localized-pdfs/source?id=' + identifier, **extra}


class FakeApi:
    def __init__(self, items):
        self.items, self.saved, self.published = items, {}, {}
        self.source_header = DIGEST
        self.readback_bad = set()
        self.source_calls = []

    def inventory(self):
        return [{**row, 'status': 'ready' if row['id'] in self.published else row['status']} for row in self.items]

    def source(self, row):
        self.source_calls.append(row['id'])
        return ORIGINAL, {'x-source-sha256': self.source_header, 'x-source-version': row['sourceVersion'] or 'v1'}

    def progress(self, identifier, digest):
        return self.saved.get((identifier, digest))

    def save_progress(self, identifier, digest, value):
        self.saved[(identifier, digest)] = json.loads(json.dumps(value))

    def publish(self, metadata, pdf):
        self.published[metadata['id']] = (metadata, pdf.read_bytes())

    def artifact(self, identifier, digest):
        metadata, data = self.published[identifier]
        if identifier in self.readback_bad:
            data = b'wrong object'
        return data, {'x-source-sha256': digest, 'x-artifact-sha256': metadata['sha256']}


def translated(**kwargs):
    kwargs['save_progress']({'schema': 'engine/v1', 'completed': 3})
    data = b'%PDF-1.7\nChinese edition'
    kwargs['output_pdf'].write_bytes(data)
    return {'path': str(kwargs['output_pdf']), 'sha256': runner.sha256(data),
        'pageCount': 2, 'byteSize': len(data), 'qa': qa()}


class RunnerTests(unittest.TestCase):
    def run_batch(self, api, engine=translated, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return runner.run(api, engine, Path(directory), **kwargs)

    def test_all_pending_processes_and_existing_ready_remains_untouched(self):
        api = FakeApi([item('a'), item('b'), item('old', 'ready')])
        result = self.run_batch(api, limit=0, concurrency=2)
        self.assertEqual(result['translatedCount'], 2)
        self.assertEqual(result['remainingPending'], 0)
        self.assertEqual(set(api.source_calls), {'a', 'b'})
        self.assertEqual(api.saved[('a', DIGEST)]['completed'], 3)

    def test_failed_translation_continues_without_publishing_english_or_private_error(self):
        api = FakeApi([item('a'), item('b')])
        def fail_one(**kwargs):
            if kwargs['source_pdf'].parent.name.startswith(runner.sha256(b'a')[:12]):
                kwargs['save_progress']({'completed': 1})
                raise ValueError('private source paragraph and token must not enter report')
            return translated(**kwargs)
        result = self.run_batch(api, fail_one, limit=0, concurrency=1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failedCount'], 1)
        self.assertEqual(set(api.published), {'b'})
        self.assertEqual(api.saved[('a', DIGEST)], {'completed': 1})
        self.assertNotIn('private source paragraph', json.dumps(result))

    def test_existing_checkpoint_is_received_on_next_run(self):
        api = FakeApi([item('a')]); api.saved[('a', DIGEST)] = {'completed': 1}
        def resumed(**kwargs):
            self.assertEqual(kwargs['progress'], {'completed': 1})
            return translated(**kwargs)
        self.assertEqual(self.run_batch(api, resumed)['translatedCount'], 1)

    def test_source_digest_failure_never_reaches_model(self):
        api = FakeApi([item('a')]); api.source_header = 'a' * 64
        def forbidden(**kwargs):
            self.fail('model called before source integrity verification')
        result = self.run_batch(api, forbidden)
        self.assertEqual(result['results'][0]['code'], 'SOURCE_DIGEST_MISMATCH')
        self.assertFalse(api.published)

    def test_inventory_unavailable_source_is_a_failure_and_other_items_continue(self):
        api = FakeApi([item('a', error='source unavailable'), item('b')])
        result = self.run_batch(api, limit=0)
        self.assertEqual(result['failedCount'], 1)
        self.assertEqual(result['translatedCount'], 1)
        self.assertNotIn('a', api.source_calls)

    def test_bad_readback_cannot_report_success(self):
        api = FakeApi([item('a')]); api.readback_bad.add('a')
        result = self.run_batch(api)
        self.assertEqual(result['failedCount'], 1)
        self.assertEqual(result['results'][0]['code'], 'ARTIFACT_READBACK_FAILED')

    def test_source_pdf_with_fake_chinese_qa_is_not_published(self):
        api = FakeApi([item('a')])
        def unchanged(**kwargs):
            kwargs['output_pdf'].write_bytes(ORIGINAL)
            return {'path': str(kwargs['output_pdf']), 'sha256': DIGEST,
                'pageCount': 2, 'byteSize': len(ORIGINAL), 'qa': qa()}
        result = self.run_batch(api, unchanged)
        self.assertEqual(result['results'][0]['code'], 'UNTRANSLATED_SOURCE_ARTIFACT')
        self.assertFalse(api.published)

    def test_missing_source_page_coverage_is_rejected(self):
        report = qa(); report['pages'] = [{'sourcePage': 1}, {'sourcePage': 1}]
        with self.assertRaises(runner.RunnerError):
            runner.validate_qa(report, 2)

    def test_limit_preserves_backlog_and_force_is_explicit(self):
        api = FakeApi([item('a'), item('b'), item('old', 'ready')])
        result = self.run_batch(api, limit=1)
        self.assertEqual(result['remainingPending'], 1)
        self.assertNotIn('old', api.source_calls)
        self.run_batch(api, limit=0, force=True)
        self.assertIn('old', api.source_calls)

    def test_inventory_cycle_fails_instead_of_looping(self):
        api = runner.GatexApi('test-token')
        api.request = lambda *args, **kwargs: {'items': [], 'nextCursor': '1'}
        with self.assertRaises(runner.RunnerError):
            api.inventory()

    def test_remote_source_and_redirect_target_are_not_allowed(self):
        api = runner.GatexApi('test-token')
        with self.assertRaises(runner.RunnerError):
            api.source(item('a', sourceUrl='https://untrusted.example/steal'))
        with self.assertRaises(runner.RunnerError):
            runner.GatexApi('test-token', 'https://untrusted.example')

    def test_large_checkpoint_is_not_sent(self):
        api = runner.GatexApi('test-token')
        api.request = lambda *args, **kwargs: self.fail('oversize checkpoint sent')
        with self.assertRaises(runner.RunnerError):
            api.save_progress('a', DIGEST, {'text': 'x' * runner.MAX_PROGRESS})

    def test_single_sample_never_scans_all_inventory(self):
        api = FakeApi([])
        api.inventory = lambda: self.fail('sample unnecessarily scans entire inventory')
        result = self.run_batch(api, sample_id='catalog:example', limit=0)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['scope'], 'single')
        self.assertIsNone(result['remainingPending'])
        self.assertEqual(api.source_calls, ['catalog:example'])

    def test_paid_sample_pin_is_checked_before_progress_model_or_publication(self):
        api = FakeApi([])
        api.inventory = lambda: self.fail('pinned sample scanned inventory')
        api.progress = lambda *args: self.fail('changed pinned source read progress')
        api.save_progress = lambda *args: self.fail('changed pinned source wrote progress')
        api.publish = lambda *args: self.fail('changed pinned source published')
        def forbidden(**kwargs):
            self.fail('changed pinned source reached model')
        result = self.run_batch(api, forbidden, sample_id='sample', expected_source_sha256='f' * 64)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['results'][0]['code'], 'SOURCE_CHANGED_FROM_PIN')
        self.assertEqual(result['results'][0]['phase'], 'source')
        self.assertEqual(api.source_calls, ['sample'])

    def test_matching_paid_sample_pin_publishes_and_verifies_original_source_digest(self):
        api = FakeApi([])
        api.inventory = lambda: self.fail('pinned sample scanned inventory')
        result = self.run_batch(api, sample_id='sample', expected_source_sha256=DIGEST)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['results'][0]['sourceSha256'], DIGEST)
        self.assertEqual(set(api.published), {'sample'})

    def test_paid_sample_pin_requires_one_exact_sample_and_valid_digest_before_io(self):
        api = FakeApi([])
        api.inventory = lambda: self.fail('invalid pin read inventory')
        api.source = lambda *args: self.fail('invalid pin downloaded source')
        for sample_id, digest in [(None, DIGEST), ('sample', ''), ('sample', 'not-a-digest')]:
            with self.subTest(sample_id=sample_id, digest=digest), self.assertRaises(runner.RunnerError) as caught:
                self.run_batch(api, sample_id=sample_id, expected_source_sha256=digest)
            self.assertEqual(caught.exception.code, 'PINNED_SAMPLE_AND_DIGEST_REQUIRED')

    def test_continuation_page_mapping_can_preserve_complete_translation(self):
        report = qa(); report['pageCount'] = 3
        report['pages'][1]['targetPages'] = [2, 3]
        runner.validate_qa(report, 3)
        report['pages'][0]['targetPages'] = [1, 3]
        with self.assertRaises(runner.RunnerError):
            runner.validate_qa(report, 3)

    def test_news_pending_is_failure_despite_ok_true(self):
        api = runner.NewsfeedApi('test-token', sleep=lambda _: None)
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route, kwargs))
            return {'ok': True, 'version': 'v1', 'snapshotHash': 'a'*64,
                'total': 36, 'ready': 35, 'pending': 1, 'failed': 0, 'complete': False, 'records': [{'title': 'not allowed in QA summary'}]}
        api.request = request
        report = runner.warm_news(api)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['pending'], 1)
        self.assertEqual(sum(method == 'POST' for method, _, _ in calls), 3)
        self.assertEqual(report['stopReason'], 'NO_PROGRESS')
        self.assertNotIn('records', report)

    def test_news_requires_complete_nonempty_snapshot(self):
        api = runner.NewsfeedApi('test-token', sleep=lambda _: None)
        state = {'ok': True, 'version': 'v1', 'snapshotHash': 'a'*64,
            'total': 36, 'ready': 36, 'pending': 0, 'failed': 0, 'complete': True}
        api.request = lambda *args, **kwargs: state
        self.assertEqual(runner.warm_news(api)['status'], 'passed')
        state.update(total=0, ready=0)
        self.assertEqual(runner.warm_news(api)['status'], 'failed')

    def test_retry_transient_http_failure_without_redirect_or_token_logging(self):
        class Response:
            def __init__(self, status, body=b'{"items":[],"nextCursor":null}'):
                self.status_code, self.body, self.headers = status, body, {}
            def iter_content(self, size):
                yield self.body
            def close(self):
                pass
        replies = iter([Response(503), Response(200)])
        calls, sleeps = [], []
        class Session:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, url, **kwargs):
                calls.append(kwargs)
                return next(replies)
        api = runner.GatexApi('test-token', session_factory=Session, sleep=sleeps.append)
        self.assertEqual(api.inventory(), [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])
        self.assertTrue(all(call['allow_redirects'] is False for call in calls))

    def test_permission_failure_is_not_retried(self):
        class Response:
            status_code = 403
            headers = {}
            def close(self):
                pass
        calls = []
        class Session:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, *args, **kwargs):
                calls.append(kwargs)
                return Response()
        api = runner.GatexApi('test-token', session_factory=Session, sleep=lambda _: self.fail('403 retried'))
        with self.assertRaises(runner.RunnerError) as caught:
            api.inventory()
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(calls), 1)

    def test_http_error_diagnostic_is_allowlisted_without_reading_body(self):
        for header, expected in [('CATALOG_SUBREQUEST_LIMIT', 'CATALOG_SUBREQUEST_LIMIT'),
                ('private source prose and token', None), ('A' * 81, None)]:
            with self.subTest(header=header):
                calls, sleeps = [], []
                class Response:
                    status_code = 500
                    headers = {'x-gatex-error-code': header}
                    def close(self):
                        pass
                    def iter_content(self, _):
                        raise AssertionError('failed response body must not be read')
                class Session:
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                    def request(self, *args, **kwargs):
                        calls.append(kwargs)
                        return Response()
                api = runner.GatexApi('test-token', session_factory=Session, sleep=sleeps.append)
                with self.assertRaises(runner.RunnerError) as caught:
                    api.inventory()
                self.assertEqual(caught.exception.code, 'API_HTTP_ERROR')
                self.assertEqual(caught.exception.status, 500)
                self.assertEqual(caught.exception.api_code, expected)
                self.assertEqual(len(calls), 3)
                self.assertEqual(sleeps, [1, 2])
                self.assertEqual(str(caught.exception), 'API_HTTP_ERROR')

    def test_per_document_failure_summary_retains_only_safe_api_diagnostic(self):
        api = FakeApi([item('a')])
        def fail(_):
            raise runner.RunnerError('API_HTTP_ERROR', 500, 'SOURCE_STORAGE_FAILURE')
        api.source = fail
        report = self.run_batch(api)
        failed = report['results'][0]
        self.assertEqual(failed['code'], 'API_HTTP_ERROR')
        self.assertEqual(failed['httpStatus'], 500)
        self.assertEqual(failed['apiCode'], 'SOURCE_STORAGE_FAILURE')

    def test_unknown_news_post_is_checked_before_repeating_model_work(self):
        api = runner.NewsfeedApi('test-token', sleep=lambda _: self.fail('completed warm retried'))
        calls, completed = [], False
        def request(method, route, **kwargs):
            nonlocal completed
            calls.append((method, route))
            if method == 'POST':
                completed = True
                raise runner.RunnerError('API_CONNECTION_FAILED')
            return {'ok': True, 'complete': completed, 'total': 36, 'ready': 36 if completed else 0,
                'pending': 0 if completed else 36, 'failed': 0, 'version': 'v1', 'snapshotHash': 'a'*64}
        api.request = request
        self.assertEqual(runner.warm_news(api)['status'], 'passed')
        self.assertEqual(calls, [('GET', '/status'), ('POST', '/warm'), ('GET', '/status')])

    def test_inventory_preflight_reports_unavailable_without_source_or_model(self):
        api = FakeApi([item('ready', 'ready'), item('pending'), item('missing', error='private source path')])
        api.source = lambda *_: self.fail('inventory audit downloaded PDF')
        result = runner.inventory_report(api)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['inventoryCount'], 3)
        self.assertEqual(result['pendingCount'], 2)
        self.assertEqual(result['unavailableCount'], 1)
        self.assertNotIn('private source path', json.dumps(result))

    def test_completed_file_progress_only_emits_identity_and_counts(self):
        events = []
        api = FakeApi([item('a'), item('b')])
        self.run_batch(api, limit=0, emit_progress=events.append)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]['completedCount'], 2)
        self.assertEqual(events[-1]['translatedCount'], 2)
        self.assertNotIn('title', json.dumps(events))

    def test_identical_source_aliases_serialize_checkpoint_and_reuse_translation(self):
        guard, downloaded = threading.Lock(), threading.Barrier(2)
        class DigestApi(FakeApi):
            shared_progress = None
            active = maximum = 0
            def source(self, row):
                result = super().source(row)
                downloaded.wait(timeout=2)
                return result
            def progress(self, identifier, digest):
                with guard:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                    return self.shared_progress
            def save_progress(self, identifier, digest, value):
                super().save_progress(identifier, digest, value)
                self.shared_progress = json.loads(json.dumps(value))
            def artifact(self, identifier, digest):
                result = super().artifact(identifier, digest)
                with guard:
                    self.active -= 1
                return result
        api = DigestApi([item('catalogue-alias'), item('commerce-alias')])
        model_calls = []
        def resume(**kwargs):
            if not kwargs['progress']:
                model_calls.append(kwargs['source_sha256'])
                time.sleep(0.02)
            return translated(**kwargs)
        report = self.run_batch(api, resume, limit=0, concurrency=2)
        self.assertEqual(report['translatedCount'], 2)
        self.assertEqual(set(api.published), {'catalogue-alias', 'commerce-alias'})
        self.assertEqual(api.maximum, 1)
        self.assertEqual(api.active, 0)
        self.assertEqual(model_calls, [DIGEST])

    def test_distinct_source_digests_keep_parallel_translation(self):
        guard, translating = threading.Lock(), threading.Barrier(2)
        class UniqueApi(FakeApi):
            def source(self, row):
                self.source_calls.append(row['id'])
                data = ORIGINAL + row['id'].encode()
                return data, {'x-source-sha256': runner.sha256(data), 'x-source-version': 'v1'}
        api = UniqueApi([item('first'), item('second')])
        active = maximum = 0
        def parallel(**kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                translating.wait(timeout=2)
                return translated(**kwargs)
            finally:
                with guard:
                    active -= 1
        report = self.run_batch(api, parallel, limit=0, concurrency=2)
        self.assertEqual(report['translatedCount'], 2)
        self.assertEqual(maximum, 2)
        self.assertEqual(len({row['sourceSha256'] for row in report['results']}), 2)

    def test_ocr_diagnostic_summary_contains_counts_only_and_never_publishes(self):
        class DiagnosticReady(Exception):
            code = 'OCR_DIAGNOSTIC_READY'
            diagnostic_counts = {'sourcePageCount': 4, 'rasterRegionCount': 1, 'wordCount': 8,
                'privateWords': ['private source label']}
        api = FakeApi([item('sample')])
        api.inventory = lambda: self.fail('diagnostic scanned the entire catalogue')
        api.publish = lambda *args: self.fail('diagnostic published a PDF')
        def capture(**kwargs):
            self.assertFalse(kwargs['force'])
            kwargs['save_progress']({'blocks': {'existing': 'preserved private translation'},
                'ocrDiagnostics': {'words': ['private source label']}})
            raise DiagnosticReady('private source label')
        with tempfile.TemporaryDirectory() as directory:
            report = runner.run_ocr_diagnostic(api, capture, Path(directory), sample_id='sample', expected_source_sha256=DIGEST)
        self.assertEqual(report['scope'], 'diagnostic')
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['counts'], {'sourcePageCount': 4, 'rasterRegionCount': 1, 'wordCount': 8})
        self.assertEqual(report['translatedCount'], 0)
        self.assertEqual(report['publishedCount'], 0)
        self.assertNotIn('private source label', json.dumps(report))
        self.assertEqual(api.saved[('sample', DIGEST)]['blocks']['existing'], 'preserved private translation')

    def test_ocr_diagnostic_pins_source_before_capture_or_checkpoint_write(self):
        api = FakeApi([item('sample')])
        def capture(**kwargs):
            self.fail('changed source reached the diagnostic engine')
        api.save_progress = lambda *args: self.fail('changed source wrote progress')
        with tempfile.TemporaryDirectory() as directory:
            report = runner.run_ocr_diagnostic(api, capture, Path(directory), sample_id='sample', expected_source_sha256='f'*64)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['code'], 'DIAGNOSTIC_SOURCE_CHANGED')
        self.assertEqual(api.published, {})

    def test_ocr_diagnostic_requires_exact_sample_and_digest_before_network(self):
        api = FakeApi([])
        api.source = lambda *args: self.fail('invalid diagnostic options downloaded source')
        with tempfile.TemporaryDirectory() as directory:
            for sample_id, digest in [('', DIGEST), ('sample', ''), ('sample', 'not-a-digest')]:
                with self.subTest(sample_id=sample_id, digest=digest), self.assertRaises(runner.RunnerError) as caught:
                    runner.run_ocr_diagnostic(api, translated, Path(directory), sample_id=sample_id, expected_source_sha256=digest)
                self.assertEqual(caught.exception.code, 'DIAGNOSTIC_SAMPLE_AND_DIGEST_REQUIRED')

    def test_safe_engine_error_code_is_preserved_without_exception_message(self):
        class EngineError(Exception):
            code = 'RASTER_OCR_UNRESOLVED'
        api = FakeApi([item('a')])
        def fail(**kwargs):
            raise EngineError('private source prose must never be in summary')
        report = self.run_batch(api, fail)
        self.assertEqual(report['results'][0]['code'], 'RASTER_OCR_UNRESOLVED')
        self.assertNotIn('private source prose', json.dumps(report))
        unsafe = EngineError('hidden')
        unsafe.code = 'private source prose'
        self.assertEqual(runner.error_code(unsafe), 'EngineError')


class NewsWarmBudgetTests(unittest.TestCase):
    class Clock:
        now = 0.0
        def __call__(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds

    @staticmethod
    def state(ready=0, total=36, snapshot='a' * 64):
        return {'ok': True, 'version': 'reviewed-v2', 'snapshotHash': snapshot,
            'total': total, 'ready': ready, 'pending': total-ready, 'failed': 0,
            'complete': total > 0 and ready == total,
            'records': [{'summary': 'private-looking source content excluded from statistics'}]}

    def test_status_first_cache_hit_never_sends_warming_post(self):
        api = runner.NewsfeedApi('test-token')
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route))
            self.assertEqual((method, route), ('GET', '/status'))
            return self.state(36)
        api.request = request
        report = runner.warm_news(api)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['warmPasses'], 0)
        self.assertEqual(calls, [('GET', '/status')])
        self.assertNotIn('records', report)
        self.assertIsNone(api.request_deadline)

    def test_nine_four_record_passes_complete_with_safe_aggregate_progress(self):
        clock = self.Clock()
        api = runner.NewsfeedApi('test-token', sleep=clock.sleep, clock=clock)
        ready, calls, events = 0, [], []
        def request(method, route, **kwargs):
            nonlocal ready
            calls.append((method, route))
            clock.now += 1
            if method == 'POST':
                self.assertFalse(kwargs['retry'])
                ready = min(36, ready + 4)
            return self.state(ready)
        api.request = request
        report = runner.warm_news(api, clock=clock, emit_progress=events.append)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['warmPasses'], 9)
        self.assertEqual(report['ready'], 36)
        self.assertEqual(report['pending'], 0)
        self.assertEqual(calls[0], ('GET', '/status'))
        self.assertTrue(all(calls[i+1] == ('GET', '/status') for i, c in enumerate(calls) if c[0] == 'POST'))
        self.assertEqual(len(events), 9)
        self.assertEqual(events[-1]['ready'], 36)
        self.assertNotIn('source content', json.dumps(events))
        self.assertNotIn('records', report)
        self.assertIsNone(api.request_deadline)

    def test_deadline_stops_new_passes_and_preserves_last_verified_counts(self):
        clock = self.Clock()
        api = runner.NewsfeedApi('test-token', sleep=clock.sleep, clock=clock)
        ready, calls = 0, []
        def request(method, route, **kwargs):
            nonlocal ready
            calls.append((method, route))
            if method == 'POST':
                ready += 4
                clock.now += 2
            return self.state(ready)
        api.request = request
        report = runner.warm_news(api, budget_seconds=3, clock=clock)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['stopReason'], 'DEADLINE')
        self.assertEqual(report['warmPasses'], 1)
        self.assertEqual(report['ready'], 4)
        self.assertEqual(report['pending'], 32)
        self.assertLessEqual(report['elapsedSeconds'], 3)
        self.assertEqual(sum(c[0] == 'POST' for c in calls), 1)
        self.assertIsNone(api.request_deadline)

    def test_explicit_pass_limit_remains_an_incomplete_failure(self):
        api = runner.NewsfeedApi('test-token', sleep=lambda _: None)
        api.request = lambda *args, **kwargs: self.state(35)
        report = runner.warm_news(api, max_passes=2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['stopReason'], 'MAX_PASSES')
        self.assertEqual(report['warmPasses'], 2)
        self.assertEqual(report['pending'], 1)

    def test_approved_records_do_not_extend_the_stalled_pending_cycle(self):
        for ready, expected_passes in [(27, 3), (21, 4), (0, 9)]:
            with self.subTest(ready=ready):
                clock = self.Clock()
                api = runner.NewsfeedApi('test-token', sleep=clock.sleep, clock=clock)
                calls = []
                def request(method, route, **kwargs):
                    calls.append((method, route))
                    return self.state(ready)
                api.request = request
                report = runner.warm_news(api, clock=clock)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['stopReason'], 'NO_PROGRESS')
                self.assertEqual(report['warmPasses'], expected_passes)
                self.assertEqual(report['ready'], ready)
                self.assertEqual(report['pending'], 36-ready)
                self.assertEqual(sum(method == 'POST' for method, _ in calls), expected_passes)
                self.assertTrue(all(calls[i+1] == ('GET', '/status') for i, call in enumerate(calls) if call[0] == 'POST'))

    def test_invalid_budgets_fail_before_status_or_warming_requests(self):
        api = runner.NewsfeedApi('test-token')
        api.request = lambda *args, **kwargs: self.fail('invalid options reached API')
        for passes, budget in [(0, 600), (37, 600), (12, 0), (12, 601), (True, 600), (12, float('nan'))]:
            with self.subTest(passes=passes, budget=budget), self.assertRaises(runner.RunnerError) as caught:
                runner.warm_news(api, max_passes=passes, budget_seconds=budget)
            self.assertEqual(caught.exception.code, 'INVALID_NEWS_BATCH_OPTIONS')

    def test_http_timeout_and_stream_reads_obey_remaining_budget(self):
        clock = self.Clock()
        calls = []
        class Response:
            status_code, headers = 200, {}
            closed = False
            def iter_content(self, size):
                clock.now += 1
                yield b'{"ok":true}'
            def close(self):
                self.closed = True
        response = Response()
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, method, url, **kwargs):
                calls.append(kwargs)
                return response
        api = runner.NewsfeedApi('test-token', session_factory=Session, clock=clock)
        api.request_deadline = 0.8
        with self.assertRaises(runner.RunnerError) as caught:
            api.request('GET', '/status')
        self.assertEqual(caught.exception.code, 'API_DEADLINE_EXCEEDED')
        self.assertLessEqual(sum(calls[0]['timeout']), 0.8)
        self.assertEqual(len(calls), 1)
        self.assertTrue(response.closed)

    def test_empty_snapshot_does_not_pass_or_call_provider(self):
        api = runner.NewsfeedApi('test-token')
        def request(method, route, **kwargs):
            self.assertEqual(method, 'GET')
            return self.state(0, 0)
        api.request = request
        report = runner.warm_news(api)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['stopReason'], 'EMPTY_SNAPSHOT')
        self.assertEqual(report['warmPasses'], 0)


if __name__ == '__main__':
    unittest.main()
