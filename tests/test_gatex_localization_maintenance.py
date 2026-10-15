import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import gatex_pdf_maintenance as maintenance
from scripts import gatex_localization_runner as runner

SOURCE = b"%PDF-1.7 PRIVATE original"
TARGET = b"%PDF-1.7 PRIVATE Chinese edition"


def quality():
    return {"schema": runner.QA_SCHEMA, "passed": True, "sourcePageCount": 1, "pageCount": 1,
        "sourceBlockCount": 2, "translatedBlockCount": 2, "missingBlocks": 0, "overflowCount": 0,
        "unresolvedEnglishCount": 0, "figuresPreserved": True, "numericChecksPassed": True,
        "pages": [{"sourcePage": 1, "targetPages": [1]}],
        "qualityPolicyVersion": maintenance.QUALITY_POLICY, "professionalChinesePassed": True,
        "ownedTerminologyPassed": True, "qualityCheckedBlockCount": 2,
        "renderAliasReviewPolicy": maintenance.ALIAS_POLICY, "sourceSha256": runner.sha256(SOURCE),
        "semanticReviewPassed": True, "rasterOCRVerified": True, "minimumTranslatedFontSize": 7}


class Api:
    def __init__(self, rows=None):
        self.rows = rows or {"catalog:a": (SOURCE, "version-a"), "managed:b": (SOURCE + b"b", "version-b"), "managed:c": (SOURCE + b"c", "version-c")}
        self.ready, self.progresses, self.artifacts = set(), {}, {}
        self.source_calls, self.progress_calls, self.publishes = [], [], []
        self.unavailable, self.bad_versions = set(), set()

    def inventory(self):
        return [{"id": identifier, "sourceVersion": version, "status": "ready" if identifier in self.ready else "pending",
            "sourceUrl": "/api/internal/localized-pdfs/source?id=" + identifier,
            **({"error": "PRIVATE lookup detail"} if identifier in self.unavailable else {})}
            for identifier, (_, version) in self.rows.items()]

    def source(self, item):
        self.source_calls.append(item["id"])
        data, version = self.rows[item["id"]]
        return data, {"x-source-sha256": runner.sha256(data), "x-source-version": "changed" if item["id"] in self.bad_versions else version}

    def progress(self, identifier, digest):
        self.progress_calls.append((identifier, digest))
        return copy.deepcopy(self.progresses.get(digest))

    def save_progress(self, identifier, digest, value):
        self.progresses[digest] = copy.deepcopy(value)

    def publish(self, metadata, pdf):
        self.publishes.append(metadata)
        self.ready.add(metadata["id"])
        self.artifacts[metadata["sourceSha256"]] = pdf.read_bytes()

    def artifact(self, identifier, digest):
        data = self.artifacts[digest]
        return data, {"x-source-sha256": digest, "x-artifact-sha256": runner.sha256(data)}


def translator(**kwargs):
    kwargs["save_progress"]({"sourceSha256": kwargs["source_sha256"], "blocks": {"one": {"text": "PRIVATE text", "reviewed": True}}, "ocr": {}})
    kwargs["output_pdf"].write_bytes(TARGET)
    qa = quality()
    qa["sourceSha256"] = kwargs["source_sha256"]
    return {"path": str(kwargs["output_pdf"]), "sha256": runner.sha256(TARGET), "byteSize": len(TARGET), "pageCount": 1, "qa": qa}


def executor(translate):
    return lambda api, pins, sources, runtime, seconds: maintenance.process_group(api, translate, pins, sources, runtime)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.queue = self.root / "state" / "queue.json"
        self.now = 100_000

    def run_maintenance(self, api, **kwargs):
        return maintenance.maintain(api, self.queue, self.root / "private", wall=lambda: self.now, **kwargs)

    def pins(self, api, *identifiers):
        return json.dumps([{"id": identifier, "expectedSourceSha256": runner.sha256(api.rows[identifier][0])} for identifier in identifiers])

    def test_manual_pins_override_only_selection_and_validate_actual_source_before_progress(self):
        api = Api()
        self.run_maintenance(api)
        entries, _ = maintenance.load_queue(self.queue)
        entries["managed:c"].update(attemptCount=2, nextAttemptAt=self.now + 9999)
        maintenance.save_queue(self.queue, entries)
        seen = []
        def execute(api, pins, sources, runtime, seconds):
            seen.append((pins, seconds))
            return maintenance.process_group(api, translator, pins, sources, runtime)
        report = self.run_maintenance(api, discovery_only=False, source_pins=self.pins(api, "managed:c", "managed:b"), execute=execute)
        self.assertEqual(report["requestedSourceIds"], ["managed:c", "managed:b"])
        self.assertEqual(api.source_calls, ["managed:c", "managed:b"])
        self.assertEqual({identifier for identifier, _ in api.progress_calls}, {"managed:c", "managed:b"})
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(0 < budget <= 1800 for _, budget in seen))
        self.assertEqual([row["sourceVersion"] for row in report["sourcePinsVerified"]], ["version-c", "version-b"])
        self.assertEqual(report["readyCount"], 2)
        self.assertEqual(report["globalPending"], 1)
        self.assertFalse(report["globalComplete"])

    def test_bad_or_unknown_manual_pins_fail_before_source_or_model(self):
        api = Api()
        valid = {"id": "catalog:a", "expectedSourceSha256": runner.sha256(SOURCE)}
        for value in ["{}", "[]", "null", "not-json", json.dumps([valid, valid]),
                      json.dumps([valid, {**valid, "id": "b"}, {**valid, "id": "c"}]),
                      json.dumps([{**valid, "expectedSourceSha256": "not-a-sha"}]),
                      json.dumps([{**valid, "sourceVersion": "untrusted"}]),
                      json.dumps([{**valid, "id": "bad\ncontrol"}])]:
            with self.subTest(value=value), self.assertRaisesRegex(runner.RunnerError, "INVALID_MAINTENANCE_SOURCE_PINS"):
                self.run_maintenance(api, source_pins=value)
        with self.assertRaisesRegex(runner.RunnerError, "REQUESTED_SOURCE_NOT_IN_INVENTORY"):
            self.run_maintenance(api, source_pins=json.dumps([{**valid, "id": "catalog:missing"}]))
        for maximum in (0, 3, True):
            with self.subTest(maximum=maximum), self.assertRaisesRegex(runner.RunnerError, "INVALID_MAINTENANCE_LIMIT"):
                self.run_maintenance(api, source_pins=json.dumps([valid]), maximum=maximum)
        self.assertEqual(api.source_calls, [])
        self.assertEqual(api.progress_calls, [])

    def test_manual_expected_sha_or_fresh_version_mismatch_never_reaches_model(self):
        for changed_version in (False, True):
            api = Api({"catalog:a": (SOURCE, "version-a")})
            if changed_version:api.bad_versions.add("catalog:a")
            pins = self.pins(api, "catalog:a") if changed_version else json.dumps([{"id": "catalog:a", "expectedSourceSha256": "a" * 64}])
            report = self.run_maintenance(api, discovery_only=False, source_pins=pins, execute=lambda *args: self.fail("drift reached model"))
            self.assertEqual(report["results"][0]["code"], "SOURCE_VERSION_CHANGED" if changed_version else "SOURCE_CHANGED_FROM_PIN")
            self.assertEqual(report["sourcePinsVerified"], [])
            self.assertEqual(api.progress_calls, [])
            self.assertEqual(api.publishes, [])
            self.assertFalse(report["globalComplete"])

    def test_manual_discovery_does_not_download_or_change_default_queue_order(self):
        api = Api()
        report = self.run_maintenance(api, source_pins=self.pins(api, "managed:c"))
        self.assertEqual(report["selectedIds"], ["managed:c"])
        self.assertEqual(report["sourcePinsVerified"], [])
        self.assertEqual(api.source_calls, [])
        self.assertEqual(self.run_maintenance(api)["selectedIds"], ["catalog:a", "managed:b"])

    def test_manual_already_ready_pin_verifies_source_without_forcing_retranslation(self):
        api = Api()
        api.ready.add("managed:c")
        report = self.run_maintenance(api, discovery_only=False, source_pins=self.pins(api, "managed:c"), execute=lambda *args: self.fail("ready source rebuilt"))
        self.assertEqual(api.source_calls, ["managed:c"])
        self.assertEqual(api.progress_calls, [])
        self.assertEqual(report["alreadyReadyRequestedCount"], 1)
        self.assertEqual(report["selectedUniqueSources"], 0)
        self.assertEqual(report["readyCount"], 0, "no newly produced PDF is claimed")
        self.assertEqual(report["globalPending"], 2)
        self.assertFalse(report["globalComplete"])

    def test_manual_budget_exhaustion_preserves_partial_checkpoint_for_next_explicit_run(self):
        api = Api()
        elapsed, budgets = [0], []
        def execute(api, pins, sources, runtime, seconds):
            budgets.append(seconds)
            pin = pins[0]
            api.save_progress(pin["id"], pin["sourceSha256"], {"sourceSha256": pin["sourceSha256"], "blocks": {"accepted": {"reviewed": True}}, "ocr": {"verified": True}})
            elapsed[0] += seconds
            raise runner.RunnerError("MAINTENANCE_DEADLINE_EXCEEDED")
        pins = self.pins(api, "managed:b", "managed:c")
        report = self.run_maintenance(api, discovery_only=False, source_pins=pins, execute=execute, clock=lambda: elapsed[0], budget_seconds=1800)
        self.assertEqual(budgets, [870, 870])
        self.assertEqual(report["budgetExhaustedPendingCount"], 2)
        self.assertEqual(report["readyCount"], 0)
        self.assertEqual(report["failedCount"], 2)
        self.assertEqual(report["globalPending"], 3)
        self.assertFalse(report["globalComplete"])
        def resume(**kwargs):
            self.assertIn("accepted", kwargs["progress"]["blocks"])
            self.assertTrue(kwargs["progress"]["ocr"]["verified"])
            self.assertFalse(kwargs["force"])
            return translator(**kwargs)
        resumed = self.run_maintenance(api, discovery_only=False, source_pins=pins, execute=executor(resume))
        self.assertEqual(resumed["readyCount"], 2)
        self.assertEqual(resumed["globalPending"], 1)
        self.assertFalse(resumed["globalComplete"])

    def test_workflow_passes_manual_json_as_one_literal_argument_without_execution(self):
        text = (Path(__file__).parents[1] / ".github/workflows/gatex-pdf-maintenance.yml").read_text()
        self.assertIn("SOURCE_PINS_JSON: ${{ github.event_name == 'workflow_dispatch' && inputs.source_pins || '' }}", text)
        code = text.split("          umask 077\n", 1)[1].split("      - name:", 1)[0]
        code = "umask 077\n" + "\n".join(line[10:] if line.startswith("          ") else line for line in code.splitlines())
        malicious = json.dumps([{"id": "catalog:$(touch BAD)`touch BAD2`", "expectedSourceSha256": "a" * 64}])
        args = self.root / "argv.txt"
        shell = 'python() { printf "%s\\n" "$@" > "$ARG_FILE"; };\n' + code
        subprocess.run(["bash", "-c", shell], cwd=self.root, env={"PATH": os.environ["PATH"], "ARG_FILE": str(args), "SOURCE_PINS_JSON": malicious, "DISCOVERY_ONLY": "false", "RUNNER_TEMP": str(self.root)}, check=True)
        argv = args.read_text().splitlines()
        self.assertEqual(argv[argv.index("--source-pins-json") + 1], malicious)
        self.assertEqual(argv[argv.index("--budget-seconds") + 1], "1800")
        self.assertFalse((self.root / "BAD").exists())
        self.assertFalse((self.root / "BAD2").exists())

    def test_missing_or_corrupt_cache_rebuilds_pending_inventory_without_claiming_completion(self):
        api = Api()
        with patch.object(runner, "engine", side_effect=AssertionError("model import")):
            report = self.run_maintenance(api)
        self.assertEqual(report["status"], "planned")
        self.assertTrue(report["cacheRebuilt"])
        self.assertFalse(report["globalComplete"])
        self.assertEqual(report["globalPending"], 3)
        self.assertEqual(api.source_calls, [])
        self.queue.write_text('{"schema":"old","private":"body"}')
        self.assertTrue(self.run_maintenance(api)["cacheRebuilt"])
        stored = json.loads(self.queue.read_text())
        self.assertNotIn("private", stored)
        self.assertTrue(all(set(row) == maintenance.ENTRY_KEYS for row in stored["entries"]))

    def test_new_and_changed_versions_reset_only_their_queue_state_and_withdrawals_leave(self):
        api = Api()
        self.run_maintenance(api)
        entries, _ = maintenance.load_queue(self.queue)
        entries["catalog:a"].update(attemptCount=4, nextAttemptAt=self.now + 9999, sourceSha256="a" * 64)
        entries["managed:b"].update(attemptCount=2, nextAttemptAt=self.now + 4444)
        maintenance.save_queue(self.queue, entries)
        api.rows["catalog:a"] = (SOURCE + b"revision", "new-version")
        del api.rows["managed:c"]
        api.rows["managed:new"] = (SOURCE + b"new", "new")
        self.run_maintenance(api)
        entries, _ = maintenance.load_queue(self.queue)
        self.assertEqual(entries["catalog:a"]["attemptCount"], 0)
        self.assertIsNone(entries["catalog:a"]["sourceSha256"])
        self.assertEqual(entries["managed:b"]["attemptCount"], 2)
        self.assertNotIn("managed:c", entries)
        self.assertIn("managed:new", entries)
        self.assertEqual(entries["catalog:a"]["discoveryClass"], "changed")
        self.assertEqual(entries["managed:new"]["discoveryClass"], "new")

    def test_new_arrival_precedes_large_bootstrap_backlog_with_an_oldest_slot_reserved(self):
        api = Api({"old:" + str(index).zfill(3): (SOURCE + str(index).encode(), "v1") for index in range(96)})
        self.run_maintenance(api)
        self.now += 100
        api.rows["new:001"] = (SOURCE + b"new", "new-version")
        planned = self.run_maintenance(api)
        self.assertEqual(planned["selectedIds"], ["new:001", "old:000"])
        # A version change gets the same priority without moving or resetting
        # unrelated old entries. New arrivals use FIFO rather than newest-first.
        self.now += 100
        api.rows["old:095"] = (SOURCE + b"changed", "v2")
        api.rows["new:002"] = (SOURCE + b"later", "v1")
        self.assertEqual(self.run_maintenance(api)["selectedIds"], ["new:001", "old:000"])
        api.ready.add("new:001")
        self.assertEqual(self.run_maintenance(api)["selectedIds"], ["new:002", "old:000"])
        api.ready.add("new:002")
        self.assertEqual(self.run_maintenance(api)["selectedIds"], ["old:095", "old:000"])

    def test_due_failed_source_has_only_one_slot_while_next_new_publication_proceeds(self):
        rows = [{"id": "failed", "sourceVersion": "v", "status": "pending"}]
        entries = maintenance.reconcile(rows, {}, 100)
        entries["failed"].update(attemptCount=10, lastAttemptAt=200, nextAttemptAt=300)
        rows += [{"id": "fresh-a", "sourceVersion": "a", "status": "pending"}, {"id": "fresh-b", "sourceVersion": "b", "status": "pending"}]
        entries = maintenance.reconcile(rows, entries, 400)
        self.assertEqual([row["id"] for row in maintenance.select(entries, 500)], ["fresh-a", "failed"])

    def test_failed_first_source_backs_off_later_selected_source_succeeds_and_backlog_is_explicit(self):
        api = Api()
        api.unavailable.add("catalog:a")
        report = self.run_maintenance(api, discovery_only=False, execute=executor(translator))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failedCount"], 1)
        self.assertEqual(report["readyCount"], 1)
        self.assertEqual(report["globalPending"], 2)
        self.assertFalse(report["globalComplete"])
        self.assertIn("managed:b", api.ready)
        entries, _ = maintenance.load_queue(self.queue)
        self.assertEqual(entries["catalog:a"]["nextAttemptAt"], self.now + 3600)
        next_report = self.run_maintenance(api, discovery_only=False, execute=executor(translator))
        self.assertEqual(next_report["selectedIds"], ["managed:c"])
        self.assertEqual(next_report["status"], "passed")
        self.assertEqual(next_report["globalPending"], 1)
        self.assertFalse(next_report["globalComplete"])

    def test_source_version_changes_before_model_and_checkpoint_load_are_rejected(self):
        api = Api()
        api.bad_versions.add("catalog:a")
        report = self.run_maintenance(api, discovery_only=False, maximum=1,
            execute=lambda *args: self.fail("source mismatch reached model"))
        self.assertEqual(report["results"][0]["code"], "SOURCE_VERSION_CHANGED")
        self.assertEqual(api.progress_calls, [])
        self.assertEqual(api.publishes, [])

    def test_unavailable_source_without_version_does_not_block_another_item(self):
        api = Api({"catalog:a": (SOURCE, None), "managed:b": (SOURCE + b"b", "b")})
        api.unavailable.add("catalog:a")
        report = self.run_maintenance(api, discovery_only=False, execute=executor(translator))
        self.assertEqual(report["failedCount"], 1)
        self.assertEqual(report["readyCount"], 1)
        self.assertEqual(report["results"][0]["code"], "SOURCE_UNAVAILABLE")
        entries, _ = maintenance.load_queue(self.queue)
        self.assertEqual(entries["catalog:a"]["sourceVersion"], "unavailable-source-metadata")
        api.rows["catalog:a"] = (SOURCE, "recovered")
        api.unavailable.clear()
        self.assertEqual(self.run_maintenance(api)["selectedIds"], ["catalog:a"])

    def test_old_policy_ready_is_server_pending_and_engine_must_produce_current_quality(self):
        api = Api({"catalog:a": (SOURCE, "version")})
        digest = runner.sha256(SOURCE)
        api.progresses[digest] = {"blocks": {"already": {"text": "PRIVATE accepted", "reviewed": True}}, "ocr": {"old": {"verified": True}}}
        def old_engine(**kwargs):
            self.assertIn("already", kwargs["progress"]["blocks"])
            self.assertFalse(kwargs["force"])
            result = translator(**kwargs)
            del result["qa"]["professionalChinesePassed"]
            return result
        report = self.run_maintenance(api, discovery_only=False, execute=executor(old_engine))
        self.assertEqual(report["results"][0]["code"], "CURRENT_QUALITY_POLICY_REQUIRED")
        self.assertEqual(api.publishes, [])
        self.assertEqual(report["globalPending"], 1)

    def test_every_current_policy_field_is_mandatory(self):
        for field, value in (("qualityPolicyVersion", "old"), ("professionalChinesePassed", False), ("ownedTerminologyPassed", False), ("qualityCheckedBlockCount", 1), ("qualityCheckedBlockCount", True), ("renderAliasReviewPolicy", "old"), ("semanticReviewPassed", False), ("rasterOCRVerified", False), ("sourceSha256", "bad"), ("minimumTranslatedFontSize", 6.9), ("minimumTranslatedFontSize", float("nan")), ("minimumTranslatedFontSize", True)):
            qa = quality()
            qa[field] = value
            with self.assertRaisesRegex(runner.RunnerError, "CURRENT_QUALITY_POLICY_REQUIRED"):
                maintenance.validate_current_qa(qa, 1)
        with self.assertRaisesRegex(runner.RunnerError, "CURRENT_QUALITY_POLICY_REQUIRED"):
            maintenance.validate_current_qa(quality(), 1, "b" * 64)

    def test_same_sha_aliases_share_one_engine_result_and_each_publish_readback(self):
        api = Api({"catalog:a": (SOURCE, "a"), "review:alias": (SOURCE, "b")})
        calls = []
        def model(**kwargs):
            calls.append(kwargs["source_sha256"])
            return translator(**kwargs)
        report = self.run_maintenance(api, discovery_only=False, execute=executor(model))
        self.assertEqual(calls, [runner.sha256(SOURCE)])
        self.assertEqual(report["selectedUniqueSources"], 1)
        self.assertEqual(report["readyCount"], 2)
        self.assertEqual(len(api.publishes), 2)
        self.assertTrue(report["globalComplete"])

    def test_same_sha_failure_is_not_given_a_second_alias_model_budget(self):
        api = Api({"catalog:a": (SOURCE, "a"), "review:alias": (SOURCE, "b")})
        calls = []
        def fail(**kwargs):
            calls.append(kwargs["source_sha256"])
            raise runner.RunnerError("SOURCE_REVIEW_REJECTED")
        report = self.run_maintenance(api, discovery_only=False, execute=executor(fail))
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["failedCount"], 2)
        self.assertFalse(report["globalComplete"])

    def test_cached_sha_hint_cannot_override_actual_download_identity(self):
        api = Api({"catalog:a": (SOURCE, "a")})
        self.run_maintenance(api)
        entries, _ = maintenance.load_queue(self.queue)
        entries["catalog:a"]["sourceSha256"] = "b" * 64
        maintenance.save_queue(self.queue, entries)
        report = self.run_maintenance(api, discovery_only=False, execute=executor(translator))
        self.assertEqual(report["results"][0]["sourceSha256"], runner.sha256(SOURCE))
        self.assertEqual(api.progress_calls[0][1], runner.sha256(SOURCE))

    def test_stale_alias_hints_cannot_exceed_two_actual_unique_sources(self):
        api = Api()
        self.run_maintenance(api)
        entries, _ = maintenance.load_queue(self.queue)
        for row in entries.values():
            row["sourceSha256"] = "a" * 64
        maintenance.save_queue(self.queue, entries)
        calls = []
        def model(**kwargs):
            calls.append(kwargs["source_sha256"])
            return translator(**kwargs)
        report = self.run_maintenance(api, discovery_only=False, execute=executor(model))
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["selectedUniqueSources"], 2)
        self.assertEqual(report["deferredSelectedCount"], 1)
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["globalComplete"])
        self.assertEqual(report["globalPending"], 1)

    def test_tampered_pinned_bytes_fail_before_checkpoint(self):
        api = Api()
        pin = {**api.inventory()[0], "sourceSha256": "a" * 64}
        result = maintenance.process_group(api, translator, [pin], {pin["id"]: (SOURCE, {"x-source-version": pin["sourceVersion"]})}, self.root / "private")
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(result[0]["code"], "SOURCE_CHANGED_FROM_PIN")
        self.assertEqual(api.progress_calls, [])

    def test_deadline_and_incomplete_child_cannot_claim_success(self):
        api = Api({"catalog:a": (SOURCE, "a")})
        report = self.run_maintenance(api, discovery_only=False,
            execute=lambda *args: (_ for _ in ()).throw(runner.RunnerError("MAINTENANCE_DEADLINE_EXCEEDED")))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["results"][0]["code"], "MAINTENANCE_DEADLINE_EXCEEDED")
        self.assertFalse(report["globalComplete"])
        entries, _ = maintenance.load_queue(self.queue)
        self.assertGreater(entries["catalog:a"]["nextAttemptAt"], self.now)

    def test_first_selected_child_timeout_reserves_budget_for_second_source_success(self):
        api = Api({"catalog:a": (SOURCE, "a"), "managed:b": (SOURCE + b"b", "b")})
        elapsed, budgets = [0], []
        def execute(api, pins, sources, runtime, seconds):
            budgets.append(seconds)
            if len(budgets) == 1:
                elapsed[0] += seconds
                raise runner.RunnerError("MAINTENANCE_DEADLINE_EXCEEDED")
            return maintenance.process_group(api, translator, pins, sources, runtime)
        report = self.run_maintenance(api, discovery_only=False, execute=execute,
            clock=lambda: elapsed[0], budget_seconds=1800)
        self.assertEqual(budgets, [870, 870])
        self.assertEqual(report["failedCount"], 1)
        self.assertEqual(report["readyCount"], 1)
        self.assertIn("managed:b", api.ready)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["globalComplete"])

    def test_model_child_timeout_kills_its_process_group_without_printing_source(self):
        api = Api({"catalog:a": (SOURCE, "a")})
        pin = {**api.inventory()[0], "sourceSha256": runner.sha256(SOURCE)}
        class Process:
            pid = 12345
            calls = 0
            def wait(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise maintenance.subprocess.TimeoutExpired("private-child", timeout)
                return -9
        self.root.mkdir(exist_ok=True)
        process = Process()
        with patch.object(maintenance.subprocess, "Popen", return_value=process) as opened, patch.object(maintenance.os, "killpg") as kill:
            with self.assertRaisesRegex(runner.RunnerError, "MAINTENANCE_DEADLINE_EXCEEDED"):
                maintenance.child_group(api, [pin], {pin["id"]: (SOURCE, {})}, self.root, 0.1)
        self.assertEqual(opened.call_args.kwargs["stdout"], maintenance.subprocess.DEVNULL)
        self.assertEqual(opened.call_args.kwargs["stderr"], maintenance.subprocess.DEVNULL)
        kill.assert_called_once_with(12345, maintenance.signal.SIGKILL)

    def test_api_deadline_is_bounded_then_restored(self):
        api = Api()
        api.request_deadline = 9999
        inventory = api.inventory
        def bounded_inventory():
            self.assertEqual(api.request_deadline, 300)
            return inventory()
        api.inventory = bounded_inventory
        self.run_maintenance(api, clock=lambda: 100, budget_seconds=200)
        self.assertEqual(api.request_deadline, 9999)

    def test_safe_summary_and_queue_never_include_body_checkpoint_or_provider_error_text(self):
        api = Api()
        def fail(*args):
            raise RuntimeError("PRIVATE body and credential-like value")
        report = self.run_maintenance(api, discovery_only=False, execute=fail)
        text = json.dumps(report) + self.queue.read_text()
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("credential", text)
        self.assertNotIn("blocks", text)
        self.assertNotIn("sourceUrl", text)

    def test_known_alias_selection_is_bounded_and_new_sources_precede_due_retries(self):
        rows = [{"id": "id:" + str(index), "sourceVersion": "v", "status": "pending"} for index in range(12)]
        entries = maintenance.reconcile(rows, {}, self.now)
        for row in entries.values():
            row["sourceSha256"] = "a" * 64
            row["attemptCount"] = 2
        entries["id:11"]["attemptCount"] = 0
        selected = maintenance.select(entries, self.now)
        self.assertEqual(selected[0]["id"], "id:11")
        self.assertEqual(len(selected), 4)
        with self.assertRaises(runner.RunnerError):
            maintenance.select(entries, self.now, maximum=3)

    def test_pdf_preflight_never_treats_news_or_its_own_run_as_busy(self):
        base = {"id": 1, "event": "workflow_dispatch", "status": "in_progress"}
        own = {**base, "path": ".github/workflows/gatex-pdf-maintenance.yml"}
        self.assertFalse(maintenance.pdf_busy([own], lambda _: [], 1))
        news = {**base, "path": ".github/workflows/gatex-news-maintenance.yml"}
        self.assertFalse(maintenance.pdf_busy([news], lambda _: [], 99))
        content = {**base, "path": ".github/workflows/gatex-content-localization.yml"}
        self.assertFalse(maintenance.pdf_busy([content], lambda _: [{"name": "localize", "status": "completed", "conclusion": "skipped"}, {"name": "warm-news", "status": "in_progress"}], 99))
        self.assertTrue(maintenance.pdf_busy([content], lambda _: [{"name": "localize", "status": "queued"}], 99))
        self.assertTrue(maintenance.pdf_busy([{**own, "status": "queued"}], lambda _: [], 99))
        self.assertFalse(maintenance.pdf_busy([{**own, "event": "pull_request"}], lambda _: [], 99))

    def test_workflow_hourly_main_only_one_mutex_and_metadata_only_cache(self):
        text = (Path(__file__).parents[1] / ".github/workflows/gatex-pdf-maintenance.yml").read_text()
        self.assertIn("\n  schedule:\n    - cron: '23 * * * *'", text)
        self.assertEqual(text.count("- cron:"), 1)
        self.assertIn("default: true", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertEqual(text.count("group: gatex-pdf-localization"), 1)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("steps.busy.outputs.proceed", text)
        self.assertIn("actions/cache/restore@v4", text)
        self.assertIn("actions/cache/save@v4", text)
        upload = text.split("uses: actions/upload-artifact@v4", 1)[1].split("- name:", 1)[0]
        self.assertIn("/gatex-pdf-maintenance-qa/summary.json", upload)
        self.assertNotIn("/gatex-pdf-maintenance-private", upload)

    def test_scheduled_run_without_manual_inputs_uses_ordinary_bounded_translation(self):
        text = (Path(__file__).parents[1] / ".github/workflows/gatex-pdf-maintenance.yml").read_text()
        # A schedule has no workflow_dispatch inputs. Its env branch must
        # explicitly select translation and omit the manual recovery pins.
        self.assertIn("DISCOVERY_ONLY: ${{ github.event_name == 'workflow_dispatch' && inputs.discovery_only && 'true' || 'false' }}", text)
        self.assertIn("SOURCE_PINS_JSON: ${{ github.event_name == 'workflow_dispatch' && inputs.source_pins || '' }}", text)
        code = text.split("          umask 077\n", 1)[1].split("      - name:", 1)[0]
        code = "umask 077\n" + "\n".join(line[10:] if line.startswith("          ") else line for line in code.splitlines())
        args = self.root / "scheduled-argv.txt"
        shell = 'python() { printf "%s\\n" "$@" > "$ARG_FILE"; };\n' + code
        subprocess.run(["bash", "-c", shell], cwd=self.root, env={
            "PATH": os.environ["PATH"], "ARG_FILE": str(args), "SOURCE_PINS_JSON": "",
            "DISCOVERY_ONLY": "false", "RUNNER_TEMP": str(self.root),
        }, check=True)
        argv = args.read_text().splitlines()
        self.assertNotIn("--discovery-only", argv)
        self.assertNotIn("--source-pins-json", argv)
        self.assertEqual(argv[argv.index("--budget-seconds") + 1], "1800")
        self.assertEqual(maintenance.MAX_UNIQUE, 2)
        with patch.dict(maintenance.os.environ, {
            "GITHUB_REPOSITORY": "yt-feng/gen_rpt", "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "schedule",
        }), patch.object(maintenance.sys, "argv", ["maintenance", *argv[2:]]), \
                patch.object(runner, "GatexApi", return_value=Api()), \
                patch.object(maintenance, "maintain", return_value={"status": "passed"}) as maintain:
            self.assertEqual(maintenance.main(), 0)
        self.assertFalse(maintain.call_args.kwargs["discovery_only"])
        self.assertEqual(maintain.call_args.kwargs["budget_seconds"], 1800)
        self.assertEqual(maintain.call_args.kwargs["source_pins"], "")

    def test_untrusted_branch_cli_exits_before_api_or_model_construction(self):
        with patch.dict(maintenance.os.environ, {"GITHUB_REPOSITORY": "yt-feng/gen_rpt", "GITHUB_REF": "refs/heads/untrusted", "GITHUB_EVENT_NAME": "workflow_dispatch"}), patch.object(maintenance.sys, "argv", ["maintenance", "--preflight"]), patch.object(runner, "GatexApi", side_effect=AssertionError("API constructed")), patch.object(runner, "engine", side_effect=AssertionError("engine imported")):
            with self.assertRaisesRegex(runner.RunnerError, "TRUSTED_MAIN_REQUIRED"):
                maintenance.main()


if __name__ == "__main__":
    unittest.main()
