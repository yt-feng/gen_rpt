import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.gatex_localization_runner import RunnerError
from scripts.gatex_news_fullpool import warm_daily_pool, _status

SNAPSHOT = "a" * 64
POOL = "b" * 64
POLICY = "c" * 64


def status(total=50, ready=36, public=True, pool_hash=POOL):
    complete = ready == total
    public_ready = 36 if public else min(ready, 35)
    return {"ok": True, "version": "reviewed-v2", "snapshotHash": SNAPSHOT, "total": 36,
        "ready": public_ready, "pending": 36 - public_ready, "failed": 0, "complete": public,
        "policyComplete": public, "candidate": {"snapshotHash": SNAPSHOT, "fixed": True, "complete": public, "total": 36, "ready": public_ready, "pending": 36-public_ready, "failed": 0},
        "published": {"available": public, "complete": public, "snapshotHash": SNAPSHOT if public else None, "total": 36 if public else 0, "ready": 36 if public else 0},
        "daily": {"available": True, "day": "2026-10-15", "currentDay": "2026-10-15", "capturedAt": "2026-10-15T00:00:00Z", "poolTotal": total, "snapshotHash": SNAPSHOT},
        "pool": {"day": "2026-10-15", "poolHash": pool_hash, "reviewPolicyHash": POLICY, "total": total, "ready": ready,
            "pending": total - ready, "failed": 0, "policyReady": ready, "pendingSourceCount": total - ready, "complete": complete,
            "published": {"available": complete, "day": "2026-10-15" if complete else None, "poolHash": pool_hash if complete else None,
                "reviewPolicyHash": POLICY if complete else None, "total": total if complete else 0}}}


class Api:
    def __init__(self, current, mutate=None):
        self.current, self.mutate = copy.deepcopy(current), mutate
        self.now, self.clock, self.request_deadline, self.calls = 0, lambda: self.now, None, []
        self.sleep = lambda seconds: setattr(self, "now", self.now + seconds)

    def request(self, method, route, **kwargs):
        self.calls.append((method, route, kwargs))
        if self.request_deadline is not None and self.now >= self.request_deadline:
            raise RunnerError("API_DEADLINE_EXCEEDED")
        if method == "POST" and route != "/capture":
            self.assert_pins(kwargs)
            if self.mutate: return self.mutate(self, kwargs["json"])
        return copy.deepcopy(self.current)

    def assert_pins(self, kwargs):
        assert kwargs["retry"] is False
        assert kwargs["json"]["expectedSnapshotHash"] == SNAPSHOT
        if kwargs["json"].get("scope") == "pool": assert kwargs["json"]["expectedPoolHash"] == POOL


class FullPoolTests(unittest.TestCase):
    def run_pool(self, api, **kwargs):
        with patch("scripts.gatex_news_fullpool.warm_news", return_value={"stopReason": "MAX_PASSES", "status": "failed"}) as initial:
            result = warm_daily_pool(api, clock=api.clock, **kwargs)
        return result, initial

    def test_public36_cannot_end_a_pending_50_record_pool(self):
        def fill(api, body):
            self.assertEqual(body["scope"], "pool")
            api.current = status(50, 50)
            return copy.deepcopy(api.current)
        api = Api(status(), fill)
        result, initial = self.run_pool(api)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["pool"]["total"], 50)
        self.assertEqual(result["public"]["total"], 36)
        self.assertEqual([call[0] for call in api.calls], ["GET", "POST", "GET"])
        self.assertEqual(initial.call_args.kwargs["max_passes"], 1)
        self.assertEqual(initial.call_args.kwargs["budget_seconds"], 600)
        self.assertEqual(api.request_deadline, None)

    def test_real_public_warmer_acquires_only_once_then_continues_pool(self):
        def fill(api, _body):
            api.current = status(50, 50)
            return copy.deepcopy(api.current)
        api = Api(status(), fill)
        result = warm_daily_pool(api, clock=api.clock, capture_daily=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(sum(route == "/capture" for _, route, _ in api.calls), 1)
        self.assertEqual(sum(method == "POST" and route == "/warm" for method, route, _ in api.calls), 1)

    def test_complete_pool_still_needs_the_public_pointer(self):
        def publish(api, body):
            self.assertNotIn("scope", body)
            api.current = status(50, 50)
            return copy.deepcopy(api.current)
        result, _ = self.run_pool(Api(status(50, 50, public=False), publish))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["publicComplete"] and result["poolComplete"])

    def test_no_progress_waits_for_entire_200_source_cursor_cycle(self):
        cursor = 0
        def reject(api, _body):
            nonlocal cursor
            payload = copy.deepcopy(api.current)
            payload["pool"]["attemptedSourceHashes"] = [f"{n:064x}" for n in range(cursor, min(cursor + 4, 200))]
            cursor += 4
            return payload
        api = Api(status(200, 0), reject)
        result, _ = self.run_pool(api)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopReason"], "NO_PROGRESS_FULL_POOL_CYCLE")
        self.assertEqual(result["warmPasses"], 50, "the previous 12-pass cap must not prevent a full pool traversal")
        self.assertEqual(result["pool"]["pending"], 200)

    def test_source_aliases_use_pending_unique_source_count(self):
        current = status(50, 48)
        current["pool"]["pendingSourceCount"] = 1
        def reject(api, _body):
            payload = copy.deepcopy(api.current)
            payload["pool"]["attemptedSourceHashes"] = ["d" * 64]
            return payload
        result, _ = self.run_pool(Api(current, reject))
        self.assertEqual(result["warmPasses"], 1)
        self.assertEqual(result["stopReason"], "NO_PROGRESS_FULL_POOL_CYCLE")

    def test_uncertain_write_is_read_back_before_any_repeat(self):
        def unknown(api, _body):
            api.current = status(50, 50)
            raise RunnerError("API_CONNECTION_FAILED")
        api = Api(status(), unknown)
        result, _ = self.run_pool(api)
        self.assertEqual(result["status"], "passed")
        self.assertEqual([call[0] for call in api.calls], ["GET", "POST", "GET", "GET"])

    def test_source_change_stops_without_chasing_another_edition(self):
        def changed(_api, _body): raise RunnerError("API_HTTP_ERROR", 409)
        result, _ = self.run_pool(Api(status(), changed))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopReason"], "SOURCE_CHANGED")
        self.assertEqual(result["warmPasses"], 1)

    def test_overall_deadline_stops_without_another_post(self):
        def slow(api, _body):
            api.now = api.request_deadline
            raise RunnerError("API_DEADLINE_EXCEEDED")
        result, _ = self.run_pool(Api(status(), slow), budget_seconds=5)
        self.assertEqual(result["stopReason"], "DEADLINE")
        self.assertEqual(result["elapsedSeconds"], 5)
        self.assertEqual(result["status"], "failed")

    def test_bad_item_does_not_prevent_independent_records_from_being_counted(self):
        calls = 0
        def advance(api, _body):
            nonlocal calls
            calls += 1
            api.current = status(50, 49)
            payload = copy.deepcopy(api.current)
            payload["pool"]["attemptedSourceHashes"] = ["d" * 64]
            return payload
        result, _ = self.run_pool(Api(status(50, 36), advance))
        self.assertEqual(result["pool"]["ready"], 49)
        self.assertEqual(result["pool"]["pending"], 1)
        self.assertEqual(result["warmPasses"], 2)
        self.assertEqual(result["stopReason"], "NO_PROGRESS_FULL_POOL_CYCLE")

    def test_malformed_policy_or_publication_proofs_cannot_pass(self):
        for change in [lambda p: p["pool"].update(total=51), lambda p: p["pool"].update(complete=True),
            lambda p: p["pool"]["published"].update(poolHash="e" * 64),
            lambda p: p["pool"].update(reviewPolicyHash="invalid"), lambda p: p["pool"].update(pendingSourceCount=True)]:
            payload = status(50, 50)
            change(payload)
            # complete=True is only inconsistent when the pool has a pending record.
            if payload == status(50, 50): payload["pool"].update(ready=49, pending=1, pendingSourceCount=1)
            with self.subTest(payload=payload["pool"]):
                result, _ = self.run_pool(Api(payload))
                self.assertEqual(result["status"], "failed")

    def test_capture_failure_does_not_add_another_acquisition(self):
        api = Api(status())
        with patch("scripts.gatex_news_fullpool.warm_news", return_value={"stopReason": "DAILY_SOURCE_UNAVAILABLE", "status": "failed"}) as initial:
            result = warm_daily_pool(api, clock=api.clock, capture_daily=True)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(initial.call_args.kwargs["capture_daily"])
        self.assertEqual(api.calls, [])

    def test_safe_summary_contains_no_source_or_provider_payload(self):
        payload = status(50, 50)
        payload.update(records=[{"title": "private source prose"}], providerResponse="private provider prose")
        result, _ = self.run_pool(Api(payload))
        text = json.dumps(result)
        self.assertNotIn("private source prose", text)
        self.assertNotIn("private provider prose", text)
        self.assertNotIn("records", text)

    def test_workflow_uses_one_daily_clock_existing_news_mutex_and_four_hour_budget(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/gatex-news-maintenance.yml").read_text()
        self.assertIn("'5 0 * * *'", workflow)
        self.assertIn("group: gatex-news-localization", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("timeout-minutes: 270", workflow)
        self.assertIn("--news-only --news-full-pool --news-capture-daily", workflow)
        self.assertIn("--news-budget-seconds 14400", workflow)
        self.assertIn("--news-max-passes 0", workflow)
        self.assertNotIn("digest", workflow.replace("digest/email endpoint", ""))
        self.assertNotIn("GATEX_GENERATION_CALLBACK_SECRET", workflow)


if __name__ == "__main__": unittest.main()
