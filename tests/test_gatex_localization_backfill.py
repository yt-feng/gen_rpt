import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from scripts import gatex_pdf_backfill as backfill
from scripts import gatex_localization_runner as runner

SOURCE_A = b"%PDF-1.7\nPrivate original report alpha"
SOURCE_B = b"%PDF-1.7\nPrivate original report beta"
TARGET = b"%PDF-1.7\nVerified Chinese edition"


def qa():
    return {"schema": runner.QA_SCHEMA, "passed": True, "sourcePageCount": 2, "pageCount": 2,
            "sourceBlockCount": 3, "translatedBlockCount": 3, "missingBlocks": 0,
            "overflowCount": 0, "unresolvedEnglishCount": 0, "figuresPreserved": True, "numericChecksPassed": True,
            "pages": [{"sourcePage": 1, "targetPages": [1]}, {"sourcePage": 2, "targetPages": [2]}]}


class Api:
    def __init__(self, records=None):
        self.rows = records or {"catalog:a": (SOURCE_A, "v1"), "review:alias": (SOURCE_A, "v2"), "managed:b": (SOURCE_B, "v3")}
        self.published, self.artifacts, self.checkpoints = {}, {}, {}
        self.source_calls, self.progress_calls = [], []
        self.errors, self.bad_artifact = set(), set()

    def inventory(self):
        return [{**backfill.source_item(identifier, version, "ready" if identifier in self.published else "pending"),
                 **({"error": "PRIVATE lookup detail"} if identifier in self.errors else {})}
                for identifier, (_, version) in self.rows.items()]

    def source(self, item):
        self.source_calls.append(item["id"])
        data, version = self.rows[item["id"]]
        return data, {"x-source-sha256": runner.sha256(data), "x-source-version": version}

    def progress(self, identifier, digest):
        self.progress_calls.append((identifier, digest))
        return copy.deepcopy(self.checkpoints.get(digest))

    def save_progress(self, identifier, digest, value):
        self.checkpoints[digest] = copy.deepcopy(value)

    def publish(self, metadata, pdf):
        self.published[metadata["id"]] = metadata["sourceSha256"]
        self.artifacts[metadata["sourceSha256"]] = pdf.read_bytes()

    def artifact(self, identifier, digest):
        if runner.sha256(self.rows[identifier][0]) != digest:
            raise runner.RunnerError("API_HTTP_ERROR", 409)
        if digest not in self.artifacts:
            raise runner.RunnerError("API_HTTP_ERROR", 404)
        data = self.artifacts[digest]
        return data, {"x-source-sha256": digest, "x-artifact-sha256": "wrong" if identifier in self.bad_artifact else runner.sha256(data)}


def translated(**kwargs):
    kwargs["save_progress"]({"schema": "gatex-pdf-translation-progress/v1", "sourceSha256": kwargs["source_sha256"],
                             "blocks": {"block": {"text": "PRIVATE translated paragraph", "reviewed": True}}, "ocr": {}})
    kwargs["output_pdf"].write_bytes(TARGET)
    return {"path": str(kwargs["output_pdf"]), "sha256": runner.sha256(TARGET), "byteSize": len(TARGET), "pageCount": 2, "qa": qa()}


class BackfillTests(unittest.TestCase):
    def audit(self, api):
        return backfill.audit(api, page_counter=lambda _data: 2)

    def run_shards(self, api, manifest, engine=translated, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return [backfill.run_shard(api, engine, manifest, "a" * 64, shard, Path(directory), **kwargs) for shard in range(3)]

    def test_read_only_audit_hashes_every_identity_groups_aliases_and_never_exports_private_cache(self):
        api = Api()
        digest = runner.sha256(SOURCE_A)
        api.checkpoints[digest] = {"schema": "gatex-pdf-translation-progress/v1", "sourceSha256": digest,
                                   "blocks": {"b": {"text": "PRIVATE source and translated paragraph", "reviewed": True}},
                                   "ocr": {"region": {"verified": True, "uncertainRegions": [], "lines": [],
                                                      "coverage": {"schema": "gatex-raster-text-coverage/v2"}}},
                                   "modelRejections": [{"source": "PRIVATE rejected source"}]}
        with patch.object(runner, "engine", side_effect=AssertionError("model import attempted")):
            report = self.audit(api)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["inventoryCount"], 3)
        self.assertEqual(report["counts"]["uniqueSourceCount"], 2)
        self.assertEqual(report["counts"]["sourcePagesIncludingAliases"], 6)
        self.assertEqual(report["counts"]["uniqueSourcePages"], 4)
        self.assertEqual(report["counts"]["storedBlocks"], 1)
        self.assertEqual(report["counts"]["verifiedOcrRegions"], 1)
        self.assertEqual(len(api.progress_calls), 2)
        self.assertEqual(set(api.source_calls), set(api.rows))
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("sourceUrl", json.dumps(report))
        self.assertFalse(api.published)
        aliases = [row for row in report["items"] if row["sourceSha256"] == digest]
        self.assertEqual(len({row["shard"] for row in aliases}), 1)
        self.assertEqual(aliases[0]["shard"], int(digest, 16) % 3)

    def test_actual_pdf_page_and_native_text_statistics_do_not_export_the_text(self):
        import fitz
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), "PRIVATE original report paragraph")
            document.new_page()
            data = document.tobytes()
        report = backfill.audit(Api({"catalog:real-pdf": (data, "v1")}))
        self.assertEqual(report["counts"]["uniqueSourcePages"], 2)
        self.assertEqual(report["counts"]["uniqueNativeTextBlocks"], 1)
        self.assertGreater(report["counts"]["uniqueNativeTextCharacters"], 20)
        self.assertEqual(report["counts"]["uniqueRasterImageRegions"], 0)
        self.assertNotIn("PRIVATE original", json.dumps(report))

    def test_invalid_source_is_reported_while_other_sources_are_audited(self):
        api = Api(); api.errors.add("catalog:a")
        report = self.audit(api)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["inventoryCount"], 3)
        self.assertEqual(report["counts"]["auditFailures"], 1)
        self.assertEqual(len(backfill.source_groups(report["items"])), 2)
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_audit_detects_inventory_changes_before_publishing_its_snapshot(self):
        api = Api(); original_inventory = api.inventory; calls = 0
        def inventory():
            nonlocal calls
            calls += 1
            if calls == 2:
                api.rows["managed:new"] = (SOURCE_B, "new-version")
            return original_inventory()
        api.inventory = inventory
        manifest = self.audit(api)
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse(manifest["inventoryUnchanged"])
        self.assertEqual(manifest["counts"]["inventoryCount"], 3)
        self.assertEqual(manifest["counts"]["auditFailures"], 1)

    def test_aliases_translate_once_and_each_alias_is_published_and_read_back(self):
        api = Api(); manifest = self.audit(api); calls = []
        def engine(**kwargs):
            calls.append(kwargs["source_sha256"])
            return translated(**kwargs)
        reports = self.run_shards(api, manifest, engine, limit=0, concurrency=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertEqual(set(api.published), set(api.rows))
        self.assertTrue(all(report["status"] == "passed" for report in reports))
        final = backfill.finalize(api, manifest, "a" * 64, reports, page_counter=lambda _data: 2)
        self.assertEqual(final["status"], "passed")
        self.assertEqual(final["verifiedReadyIds"], 3)
        self.assertEqual(final["remainingPending"], 0)
        self.assertNotIn("PRIVATE", json.dumps(final))

    def test_changed_version_or_digest_never_reaches_checkpoint_or_model(self):
        for changed_version in (True, False):
            api = Api({"catalog:a": (SOURCE_A, "v1")}); manifest = self.audit(api)
            api.rows["catalog:a"] = (SOURCE_A, "changed") if changed_version else (SOURCE_B, "v1")
            api.progress_calls.clear()
            def forbidden(**_kwargs):
                self.fail("model received a source that changed from the audit pin")
            reports = self.run_shards(api, manifest, forbidden, limit=0)
            result = next(row for report in reports for row in report["results"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["code"], "SOURCE_VERSION_CHANGED" if changed_version else "SOURCE_CHANGED_FROM_PIN")
            self.assertFalse(api.progress_calls)
            self.assertFalse(api.published)

    def test_one_failed_source_preserves_checkpoint_and_other_unique_sources_continue(self):
        api = Api(); manifest = self.audit(api); calls = []
        def engine(**kwargs):
            calls.append(kwargs["source_sha256"])
            if kwargs["source_sha256"] == runner.sha256(SOURCE_A):
                kwargs["save_progress"]({"schema": "gatex-pdf-translation-progress/v1", "sourceSha256": kwargs["source_sha256"], "blocks": {"accepted": {}}, "ocr": {}})
                raise runner.RunnerError("SEMANTIC_REVIEW_FAILED")
            return translated(**kwargs)
        reports = self.run_shards(api, manifest, engine, limit=0)
        self.assertEqual(calls.count(runner.sha256(SOURCE_A)), 1)
        self.assertEqual(calls.count(runner.sha256(SOURCE_B)), 1)
        self.assertEqual(set(api.published), {"managed:b"})
        self.assertIn("accepted", api.checkpoints[runner.sha256(SOURCE_A)]["blocks"])
        final = backfill.finalize(api, manifest, "a" * 64, reports, page_counter=lambda _data: 2)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["remainingPending"], 2)

    def test_existing_progress_is_resumed_and_invalid_qa_never_publishes(self):
        api = Api({"catalog:a": (SOURCE_A, "v1")}); manifest = self.audit(api)
        api.checkpoints[runner.sha256(SOURCE_A)] = {"completed": 7}
        def engine(**kwargs):
            self.assertEqual(kwargs["progress"], {"completed": 7})
            artifact = translated(**kwargs)
            artifact["qa"]["missingBlocks"] = 1
            return artifact
        self.run_shards(api, manifest, engine, limit=0)
        self.assertFalse(api.published)

    def test_source_page_count_must_match_the_audited_source_not_only_the_engine_claim(self):
        api = Api({"catalog:a": (SOURCE_A, "v1")}); manifest = self.audit(api)
        def engine(**kwargs):
            artifact = translated(**kwargs)
            artifact["qa"].update(sourcePageCount=1, pages=[{"sourcePage": 1, "targetPages": [1, 2]}])
            return artifact
        reports = self.run_shards(api, manifest, engine, limit=0)
        self.assertEqual(next(row for report in reports for row in report["results"])["code"], "SOURCE_PAGE_COUNT_CHANGED")
        self.assertFalse(api.published)

    def test_missing_shards_wrong_manifest_or_corrupt_readback_cannot_report_global_success(self):
        api = Api(); manifest = self.audit(api); reports = self.run_shards(api, manifest, limit=0)
        for wrong in (reports[:2], [{**report, "manifestSha256": "b" * 64} for report in reports]):
            final = backfill.finalize(api, manifest, "a" * 64, wrong, page_counter=lambda _data: 2)
            self.assertEqual(final["status"], "failed")
            self.assertFalse(final["allShardsComplete"])
        api.bad_artifact.add("review:alias")
        final = backfill.finalize(api, manifest, "a" * 64, reports, page_counter=lambda _data: 2)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["remainingPending"], 1)

    def test_new_inventory_identity_or_late_source_change_requires_another_audit(self):
        api = Api(); manifest = self.audit(api); reports = self.run_shards(api, manifest, limit=0)
        api.rows["managed:new"] = (SOURCE_B, "v4")
        final = backfill.finalize(api, manifest, "a" * 64, reports, page_counter=lambda _data: 2)
        self.assertEqual(final["status"], "failed")
        self.assertFalse(final["inventoryUnchanged"])
        api.rows.pop("managed:new")
        api.rows["catalog:a"] = (SOURCE_B, "v1")
        final = backfill.finalize(api, manifest, "a" * 64, reports, page_counter=lambda _data: 2)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["remainingPending"], 1)

    def test_budget_defers_work_without_models_and_never_claims_completeness(self):
        api = Api(); manifest = self.audit(api)
        for shard in range(3):
            ticks = iter([0] + [2] * 20)
            with tempfile.TemporaryDirectory() as directory:
                report = backfill.run_shard(api, lambda **_: self.fail("model after budget"), manifest, "a" * 64,
                                            shard, Path(directory), limit=0, budget_seconds=1, clock=lambda: next(ticks))
            if report["results"]:
                self.assertEqual(report["status"], "failed")
                self.assertTrue(all(row["code"] == "BATCH_DEFERRED" for row in report["results"]))

    def test_manifest_file_hash_snapshot_duplicate_id_and_wrong_shard_are_checked(self):
        manifest = self.audit(Api())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            def write(value):
                data = runner.encoded_json(value); path.write_bytes(data); return runner.sha256(data)
            digest = write(manifest)
            self.assertEqual(backfill.load_manifest(path, digest)["snapshotSha256"], manifest["snapshotSha256"])
            with self.assertRaises(runner.RunnerError): backfill.load_manifest(path, "0" * 64)
            for changed in (lambda value: value["items"].append(value["items"][0]),
                            lambda value: value["items"][0].update(shard=(value["items"][0]["shard"] + 1) % 3),
                            lambda value: value.update(snapshotSha256="0" * 64)):
                value = copy.deepcopy(manifest); changed(value); digest = write(value)
                with self.assertRaises(runner.RunnerError): backfill.load_manifest(path, digest)

    def test_workflow_defaults_to_read_only_and_matrix_cannot_reacquire_parent_pdf_mutex(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/gatex-pdf-backfill.yml").read_text()
        self.assertRegex(workflow, r"audit_only:[\s\S]*?type: boolean\s+default: true")
        self.assertIn("gatex-pdf-backfill-ci-{0}", workflow)
        self.assertIn("|| 'gatex-pdf-localization'", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotRegex(workflow, r"(?m)^    concurrency:")
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("NEWSFEED_CRON_SECRET", workflow)
        audit_job = workflow.split("  audit:", 1)[1].split("  backfill:", 1)[0]
        self.assertNotIn("DEEPSEEK_API_KEY", audit_job)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("--allow-partial-audit", audit_job)
        self.assertIn("if: always() && github.event_name == 'workflow_dispatch'", workflow)

    def test_fatal_audit_cannot_reuse_a_stale_manifest_to_claim_partial_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "manifest.json"; summary = root / "summary.json"
            manifest.write_text("OLD successful manifest")
            arguments = ["backfill", "audit", "--allow-partial-audit", "--manifest", str(manifest),
                         "--summary", str(summary), "--runtime", str(root / "private")]
            with patch.object(sys, "argv", arguments), patch.object(runner, "GatexApi", side_effect=ValueError("PRIVATE credential error")), redirect_stdout(io.StringIO()):
                self.assertEqual(backfill.main(), 1)
            self.assertEqual(json.loads(summary.read_text())["status"], "failed")
            self.assertNotIn("PRIVATE", summary.read_text())


if __name__ == "__main__":
    unittest.main()
