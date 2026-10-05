import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, AsyncMock, patch

import requests

from gen_rpt.generation_continuity import run_continuity, template_overview, validate_overview
from gen_rpt.main_web import RAGBridgeError
from gen_rpt.web_report_pipeline import ReportQualityError
from tools.notify_generation_result import notify
from tests.test_generation_retry_context import exact_method, module, Status, DOC, JOB

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("receipt_contract", ROOT / "report-management-backend/app/services/generation_receipt.py")
receipt_contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt_contract)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "reports_web/2026-10-05-test-topic"
        self.args = NS(topic="Supply chain resilience", model="deepseek-chat", language="en",
                       source_dir=None, source_mode=None, checkpoint_path=None,
                       result_path=self.root / "result.json")
        self.client = Mock()
        self.client.chat_json.return_value = template_overview("Supply chain resilience")
        self.pipeline = Mock()
        self.fetch = Mock(return_value=None)
        self.env = patch.dict(os.environ, {"BACKEND_URL": "https://backend.invalid", "INTERNAL_TOKEN": "fake",
                              "RAG_REQUIRED": "true", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
                              "GENERATION_JOB_ID": "job-id", "GENERATION_JOB_RETRY_COUNT": "1",
                              "GENERATION_CONTEXT_STATE": "unknown", "GITHUB_OUTPUT": str(self.root / "outputs"),
                              "GITHUB_STEP_SUMMARY": str(self.root / "summary")})
        self.env.start(); self.addCleanup(self.env.stop)

    def run_report(self):
        return run_continuity(args=self.args, output_dir=self.output, slug="test-topic", fetch_rag=self.fetch,
                              client_factory=Mock(return_value=self.client), pipeline_factory=Mock(return_value=self.pipeline))

    def test_zero_required_rag_produces_disclosed_indexable_real_overview(self):
        result = self.run_report()
        self.assertEqual(result["status"], "generated_degraded")
        self.assertTrue(result["requested_rag_required"])
        self.assertFalse(result["evidence_verified"])
        self.assertGreater(len((self.output / "report.md").read_text().split()), 400)
        self.assertIn("index,follow", (self.output / "report.html").read_text())
        self.assertIn("General overview", (self.output / "report.html").read_text())
        self.pipeline.build_report.assert_not_called()
        payload = (self.output / "web_report_payload.json").read_bytes()
        self.assertEqual(receipt_contract.validate_generation_receipt("test-topic", result, payload)["references"], [])

    def test_context_timeout_after_bounded_rag_retries_uses_overview(self):
        error = RAGBridgeError("context read exhausted")
        error.__cause__ = requests.ReadTimeout("fake transport timeout")
        self.fetch.side_effect = error
        self.assertEqual(self.run_report()["reason"], "context_temporarily_unavailable")

    def test_authentication_error_remains_failure(self):
        error = RAGBridgeError("authorization failed")
        error.__cause__ = requests.HTTPError(response=NS(status_code=403))
        self.fetch.side_effect = error
        with self.assertRaises(RAGBridgeError): self.run_report()
        self.assertFalse(self.output.exists())

    def test_insufficient_citations_discards_rejected_private_draft(self):
        self.fetch.return_value = NS(context_text="private evidence", sources=[object()])
        def fail(**kw):
            kw["output_dir"].mkdir(parents=True)
            (kw["output_dir"] / "private-draft.txt").write_text("must not publish")
            raise ReportQualityError("Insufficient grounded citations")
        self.pipeline.build_report.side_effect = fail
        self.assertEqual(self.run_report()["reason"], "research_quality_downgraded")
        self.assertFalse((self.output / "private-draft.txt").exists())
        self.assertNotIn("private evidence", str(self.client.chat_json.call_args))

    def test_external_balance_and_rate_limit_still_produce_real_template(self):
        for status in (401, 402, 403, 429, 503):
            with self.subTest(status=status):
                self.client.chat_json.side_effect = requests.HTTPError(response=NS(status_code=status))
                result = self.run_report()
                self.assertEqual(result["overview_method"], "editorial_template")
                self.assertEqual(result["status"], "generated_degraded")
                self.assertTrue((self.output / "report.html").is_file())

    def test_model_timeout_uses_template_but_programming_error_does_not(self):
        self.client.chat_json.side_effect = requests.ReadTimeout("model unavailable")
        self.assertEqual(self.run_report()["reason"], "model_timeout")
        self.client.chat_json.side_effect = KeyError("programming error")
        self.args.result_path.unlink()
        with self.assertRaises(KeyError): self.run_report()
        self.assertFalse(self.args.result_path.exists())

    def test_numbers_or_fabricated_citations_are_not_published(self):
        for unsafe in ("Market reaches 500 billion.", "According to Imaginary Institute, growth leads.", "Use https://invented.invalid/report [Citation 1]"):
            value = template_overview("Supply chain resilience")
            value["sections"][0]["paragraphs"].append(unsafe)
            self.assertFalse(validate_overview(value))
            self.client.chat_json.return_value = value
            self.assertEqual(self.run_report()["overview_method"], "editorial_template")
            self.assertNotIn(unsafe, (self.output / "report.md").read_text())

    def test_backend_overview_state_never_reads_stale_cached_scope(self):
        with patch.dict(os.environ, {"GENERATION_CONTEXT_STATE": "overview_only"}):
            self.run_report()
        self.fetch.assert_not_called()

    def test_exact_receipt_rejects_history_empty_and_tampered_output(self):
        result = self.run_report()
        data = (self.output / "web_report_payload.json").read_bytes()
        for changes in ({"slug": "another-topic"}, {"run_id": "previous"}, {"payload_sha256": "0" * 64}, {"report_id": "../old"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                receipt_contract.validate_generation_receipt("test-topic", {**result, **changes}, data)
        value = json.loads(data); value["sections"] = []
        data = json.dumps(value).encode(); result["payload_sha256"] = hashlib.sha256(data).hexdigest()
        with self.assertRaises(ValueError): receipt_contract.validate_generation_receipt("test-topic", result, data)

    def test_upload_callback_reads_exact_three_uploaded_files(self):
        result = self.run_report()
        files = {"metadata/web_report_payload.json": (self.output / "web_report_payload.json").read_bytes(),
                 "current/report.md": (self.output / "report.md").read_bytes(),
                 "current/report.html": (self.output / "report.html").read_bytes()}
        prefix = "reports/2026-10-05-test-topic/"
        storage = NS(download=AsyncMock(side_effect=lambda key: files[key.removeprefix(prefix)]))
        payload = asyncio.run(receipt_contract.load_receipted_report("test-topic", result, storage))
        self.assertEqual(payload["r2_prefix"], prefix)
        self.assertEqual(storage.download.await_count, 3)
        files["current/report.html"] = b"stale document"
        with self.assertRaises(ValueError): asyncio.run(receipt_contract.load_receipted_report("test-topic", result, storage))

    def test_notification_http_and_backend_rejections_remain_failures(self):
        result = self.run_report()
        response = Mock(); response.json.return_value = {"data": {"status": "completed", "job_id": "job-id"}}
        post = Mock(return_value=response)
        capability = Mock(status_code=200)
        capability.json.return_value = {"data": {"generation_receipt_contract": "v1"}}
        get = Mock(return_value=capability)
        self.assertEqual(notify(result, backend_url="https://example.invalid", token="fake", post=post, get=get)["status"], "completed")
        self.assertEqual(post.call_args.kwargs["json"]["metadata"]["generation_receipt"], result)
        response.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError): notify(result, backend_url="https://example.invalid", token="fake", post=post, get=get)
        response.raise_for_status.side_effect = None
        response.json.return_value = {"data": {"status": "completed", "job_id": "different"}}
        with self.assertRaises(RuntimeError): notify(result, backend_url="https://example.invalid", token="fake", post=post, get=get)

    def test_legacy_backend_keeps_real_report_without_claiming_job_completed(self):
        result = self.run_report()
        post, capability = Mock(), Mock(status_code=404)
        ack = notify(result, backend_url="https://old.invalid", token="fake", post=post, get=Mock(return_value=capability))
        self.assertEqual(ack["status"], "deferred_backend_upgrade")
        self.assertFalse(ack["job_completed"])
        post.assert_not_called()
        self.assertTrue((self.output / "report.html").exists())

    def test_template_profiles_have_distinct_topic_specific_frameworks(self):
        profiles = [("Inflation and equity markets", "economy_market", "scenarios"),
                    ("Cross-border e-commerce", "trade_commerce", "fulfilment"),
                    ("Quantum computing", "technology", "system"),
                    ("Workplace collaboration", "general", "question")]
        for topic, profile, concept in profiles:
            report = template_overview(topic)
            self.assertEqual(report["overview_profile"], profile)
            self.assertIn(concept, str(report["sections"]).lower())
            self.assertTrue(validate_overview(report))

    def test_missing_model_configuration_has_a_real_template_deliverable(self):
        receipt = run_continuity(args=self.args, output_dir=self.output, slug="test-topic", fetch_rag=self.fetch,
                                 client_factory=Mock(side_effect=ValueError("Missing DEEPSEEK_API_KEY. Please configure it.")),
                                 pipeline_factory=Mock(return_value=self.pipeline))
        self.assertEqual(receipt["reason"], "model_not_configured")
        self.assertGreater(len((self.output / "report.md").read_text()), 1000)

    def test_workflows_share_default_policy_exact_receipt_and_skip_research_review(self):
        for name in ("generate_deep_research_v2.yml", "generate_deep_research_bulk.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("default: seo_overview", workflow)
            self.assertIn('--content-policy "$CONTENT_POLICY"', workflow)
            self.assertIn('--result-path "$RESULT_PATH"', workflow)
            self.assertIn('python tools/notify_generation_result.py --receipt "$RESULT_PATH"', workflow)
            self.assertIn("inputs.content_policy == 'strict'", workflow)
            self.assertNotIn('$(find reports_web', workflow)
            self.assertIn('Backend job output requires configured R2 storage', workflow)

    def test_cli_workflow_attempts_preserve_prior_report_for_same_slug(self):
        from gen_rpt import main_web
        self.args.out_root = str(self.root / "reports_web")
        self.args.slug, self.args.content_policy = "test-topic", "seo_overview"
        with patch.object(main_web, "parse_args", return_value=self.args), \
             patch.object(main_web, "DeepSeekClient", return_value=self.client), \
             patch.object(main_web, "_fetch_rag_context", return_value=None):
            main_web.main()
            first = json.loads(self.args.result_path.read_text())
            original = (Path(first["report_dir"]) / "report.html").read_bytes()
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "3"}):
                main_web.main()
            second = json.loads(self.args.result_path.read_text())
        self.assertNotEqual(first["report_id"], second["report_id"])
        self.assertTrue(first["report_id"].endswith("--run123-2"))
        self.assertEqual(original, (Path(first["report_dir"]) / "report.html").read_bytes())
        data = (Path(second["report_dir"]) / "web_report_payload.json").read_bytes()
        receipt_contract.validate_generation_receipt("test-topic", second, data)


class BackendReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_waits_for_exact_content_and_binds_job_attempt(self):
        class ResponseError(Exception):
            def __init__(self, **kw): self.status_code = kw["status_code"]
        job_model, doc_model = NS(started=Mock(), document_id=Mock()), NS(slug=Mock())
        doc = NS(id=DOC, slug="test-topic", title="Test topic")
        job = NS(id=JOB, document_id=DOC, retry_count=1, status=Status.running,
                 audit_metadata={"receipt_expected": True})
        receipt = {"job_id": str(JOB), "job_retry_count": "1", "payload_sha256": "valid",
                   "status": "generated_degraded", "reason": "no_validated_context"}
        loader = AsyncMock(return_value={"sections": [{"paragraphs": ["Real report body"]}],
                                         "generation_outcome": receipt})
        cache, queue = {}, AsyncMock()
        imports = {
            "app.models.workflow": module(GenerationJob=job_model),
            "app.models.document": module(Document=doc_model),
            "app.models.enums": module(JobStatusType=Status),
            "app.services.generation": module(_load_report_payload_from_r2=AsyncMock(),
                    _build_mock_report_entry=Mock(return_value={}), generation_service=NS(process_bulk_queue=queue)),
            "app.services.generation_receipt": module(load_receipted_report=loader),
            "app.storage.provider": module(storage_provider=object()),
            "app.api.v1.endpoints.reports": module(MOCK_REPORTS=cache),
            "sqlalchemy": module(select=Mock(return_value=NS(where=Mock(return_value=object())))),
        }
        db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: doc)),
                get=AsyncMock(return_value=job), commit=AsyncMock())
        function = exact_method("api/v1/endpoints/internal.py", "_mark_job_completed_by_slug",
                                {"HTTPException": ResponseError, "logger": Mock()})
        with patch.dict(sys.modules, imports):
            loader.side_effect = ValueError("Payload missing")
            with self.assertRaises(ResponseError): await function(db, "test-topic", receipt)
            self.assertEqual(job.status, Status.running); db.commit.assert_not_awaited()
            loader.side_effect = None
            with self.assertRaises(ResponseError):
                await function(db, "test-topic", {**receipt, "job_retry_count": "0"})
            self.assertEqual(job.status, Status.running); db.commit.assert_not_awaited()
            result = await function(db, "test-topic", receipt)
            self.assertEqual(result["job_id"], str(JOB))
            self.assertEqual(job.status, Status.completed)
            self.assertEqual(job.audit_metadata["generation_outcome"]["status"], "generated_degraded")
            db.commit.assert_awaited_once(); queue.assert_awaited_once()
            await function(db, "test-topic", receipt)
            db.commit.assert_awaited_once()  # idempotent exact receipt

    async def test_new_overview_job_retry_keeps_scope_and_records_downgrade(self):
        from tests.test_generation_retry_context import RetryHarness
        h = RetryHarness()
        h.job.audit_metadata["content_policy"] = "seo_overview"
        h.package = {"validated_chunks": []}
        result = await h.run()
        self.assertEqual(result.audit_metadata["rag"]["status"], "overview_only")
        self.assertTrue(result.audit_metadata["rag_required"])
        self.assertEqual(result.audit_metadata["rag"]["collection_ids"], ["00000000-0000-0000-0000-000000000002"])
        h.context.cache_service.set_cached_context.assert_not_awaited()
        h.worker.dispatch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
