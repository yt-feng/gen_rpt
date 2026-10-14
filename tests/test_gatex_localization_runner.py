import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
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
        calls = []
        def request(method, route, **kwargs):
            calls.append((method, route))
            if method == 'POST':
                raise runner.RunnerError('API_CONNECTION_FAILED')
            return {'ok': True, 'complete': True, 'total': 36, 'ready': 36,
                'pending': 0, 'failed': 0, 'version': 'v1', 'snapshotHash': 'a'*64}
        api.request = request
        self.assertEqual(runner.warm_news(api)['status'], 'passed')
        self.assertEqual(calls, [('POST', '/warm'), ('GET', '/status')])

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


if __name__ == '__main__':
    unittest.main()
