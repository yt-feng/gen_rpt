"""Continue one immutable daily pool; only aggregate QA leaves this process."""
from __future__ import annotations
import math
import re
import time
from urllib.parse import urlencode
from scripts.gatex_localization_runner import RunnerError, warm_news


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _status(payload, snapshot=None, pool_pin=None):
    if not isinstance(payload, dict):
        raise RunnerError("INVALID_NEWS_STATUS")
    pool, daily, candidate, published = (payload.get(key) for key in ("pool", "daily", "candidate", "published"))
    if not all(isinstance(value, dict) for value in (pool, daily, candidate, published)):
        raise RunnerError("NEWS_POOL_CONTRACT_REQUIRED")
    if not _digest(payload.get("snapshotHash")) or not _digest(pool.get("poolHash")) or not _digest(pool.get("reviewPolicyHash")):
        raise RunnerError("INVALID_NEWS_POOL_STATUS")
    if daily.get("snapshotHash") != payload["snapshotHash"]:
        raise RunnerError("NEWS_POOL_CHANGED")
    if snapshot and payload["snapshotHash"] != snapshot or pool_pin and pool["poolHash"] != pool_pin:
        raise RunnerError("NEWS_POOL_CHANGED")
    for section in (payload, pool):
        if any(type(section.get(key)) is not int or section[key] < 0 for key in ("total", "ready", "pending", "failed")):
            raise RunnerError("INVALID_NEWS_POOL_STATUS")
        if section["ready"] + section["pending"] != section["total"] or section["failed"] > section["total"] or type(section.get("complete")) is not bool:
            raise RunnerError("INVALID_NEWS_POOL_STATUS")
    if (pool["total"] < payload["total"] or type(daily.get("poolTotal")) is not int or daily.get("available") is not True or pool["total"] != daily.get("poolTotal") or pool.get("day") != daily.get("day")
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(pool.get("day", "")))
        or type(pool.get("policyReady")) is not int or not 0 <= pool["policyReady"] <= pool["ready"]
        or type(pool.get("pendingSourceCount")) is not int or not 0 <= pool["pendingSourceCount"] <= pool["pending"]
        or (pool["pending"] > 0 and pool["pendingSourceCount"] == 0)):
        raise RunnerError("INVALID_NEWS_POOL_STATUS")
    release = pool.get("published")
    if not isinstance(release, dict) or type(release.get("available")) is not bool:
        raise RunnerError("INVALID_NEWS_POOL_PUBLICATION")
    public_complete = (payload.get("ok") is True and payload["complete"] is True and payload["total"] > 0
        and payload["ready"] == payload["total"] and payload["pending"] == 0 and payload["failed"] == 0
        and payload.get("policyComplete") is True and candidate.get("fixed") is True
        and candidate.get("complete") is True and candidate.get("snapshotHash") == payload["snapshotHash"]
        and published.get("available") is True and published.get("complete") is True
        and published.get("snapshotHash") == payload["snapshotHash"]
        and all(type(published.get(key)) is int for key in ("ready", "total"))
        and published.get("ready") == payload["total"] and published.get("total") == payload["total"])
    pool_complete = (pool["complete"] is True and pool["ready"] == pool["total"] and pool["pending"] == 0
        and pool["failed"] == 0 and pool["policyReady"] == pool["total"]
        and release.get("available") is True and release.get("poolHash") == pool["poolHash"]
        and release.get("day") == pool["day"] and type(release.get("total")) is int and release.get("total") == pool["total"]
        and release.get("reviewPolicyHash") == pool["reviewPolicyHash"])
    if pool["complete"] and not pool_complete:
        raise RunnerError("INVALID_NEWS_POOL_PUBLICATION")
    attempted = pool.get("attemptedSourceHashes", [])
    if not isinstance(attempted, list) or any(not _digest(value) for value in attempted):
        raise RunnerError("INVALID_NEWS_POOL_ATTEMPTS")
    safe = {"schema": "gatex-news-fullpool-run/v1", "status": "passed" if public_complete and pool_complete else "failed",
        "snapshotHash": payload["snapshotHash"], "poolHash": pool["poolHash"], "day": pool["day"],
        "public": {key: payload[key] for key in ("total", "ready", "pending", "failed")},
        "pool": {key: pool[key] for key in ("total", "ready", "pending", "failed", "policyReady", "reviewPolicyHash", "pendingSourceCount")},
        "publicComplete": public_complete, "poolComplete": pool_complete}
    return safe, set(attempted)


def warm_daily_pool(api, *, budget_seconds=14400, max_passes=0, capture_daily=False, clock=time.monotonic, emit_progress=None):
    """Bound wall time, preserve both pins, visit a whole pending cursor cycle.

    A public36 rejection cannot prevent independent non-public records from
    being translated. No second acquisition is introduced: existing daily CAS
    and bounded acquisition recovery are reused before the pins are fixed.
    """
    if (not isinstance(budget_seconds, (int, float)) or isinstance(budget_seconds, bool)
        or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 14400
        or type(max_passes) is not int or not 0 <= max_passes <= 10000):
        raise RunnerError("INVALID_NEWS_POOL_OPTIONS")
    started, old_clock, old_deadline = clock(), api.clock, api.request_deadline
    api.clock, api.request_deadline = clock, started + budget_seconds
    passes, report, snapshot, pool_pin = 0, None, None, None
    attempted, cycle_pending, public_stalls = set(), None, 0

    def finish(reason, error=None):
        result = {**(report or {"schema": "gatex-news-fullpool-run/v1", "status": "failed"}),
            "warmPasses": passes, "stopReason": reason, "elapsedSeconds": round(max(0, clock() - started), 3)}
        if error:
            result.update(status="failed", code=error.code, httpStatus=error.status, apiCode=error.api_code)
        return result

    def read():
        query = {"scope": "pool"}
        if snapshot: query["expectedSnapshotHash"] = snapshot
        if pool_pin: query["expectedPoolHash"] = pool_pin
        return _status(api.request("GET", "/status?" + urlencode(query)), snapshot, pool_pin)

    try:
        # At most one public warming pass; acquisition recovery retains the
        # existing 600-second bound. Its partial public result is not final.
        remaining = api.request_deadline - clock()
        if remaining <= 0: return finish("DEADLINE")
        initial = warm_news(api, max_passes=1, budget_seconds=min(600, remaining),
            clock=clock, require_publication=True, capture_daily=capture_daily)
        if initial.get("stopReason") in {"CAPTURE_FAILED", "DAILY_SOURCE_UNAVAILABLE", "API_FAILURE", "SNAPSHOT_CHANGED"}:
            return {**initial, "scope": "full-daily-pool", "status": "failed"}
        initial_pin = initial.get("expectedSnapshotHash") or initial.get("snapshotHash")
        if _digest(initial_pin): snapshot = initial_pin
        report, _ = read()
        initial_day = initial.get("daily", {}).get("day")
        if initial_day and report["day"] != initial_day: return finish("SOURCE_CHANGED", RunnerError("NEWS_POOL_CHANGED"))
        snapshot, pool_pin = report["snapshotHash"], report["poolHash"]
        cycle_pending = report["pool"]["pendingSourceCount"]
        while True:
            if report["status"] == "passed": return finish("COMPLETE")
            if clock() >= api.request_deadline: return finish("DEADLINE")
            if max_passes and passes >= max_passes: return finish("MAX_PASSES")
            previous, observed = report, set()
            body = {"expectedSnapshotHash": snapshot}
            pool_stage = not report["poolComplete"]
            if pool_stage: body.update(scope="pool", expectedPoolHash=pool_pin)
            try:
                passes += 1
                report, observed = _status(api.request("POST", "/warm", json=body, retry=False), snapshot, pool_pin)
            except RunnerError as error:
                if error.status == 409 or error.code == "NEWS_POOL_CHANGED": return finish("SOURCE_CHANGED", error)
                if error.code == "API_DEADLINE_EXCEEDED": return finish("DEADLINE", error)
                if error.code != "API_CONNECTION_FAILED" and not (error.code == "API_HTTP_ERROR" and (error.status == 429 or error.status and error.status >= 500)):
                    return finish("API_FAILURE", error)
                # An uncertain write is resolved before another POST.
                report, _ = read()
            if emit_progress:
                emit_progress({"event": "news_pool_warm_pass", "pass": passes, **report,
                    "elapsedSeconds": round(max(0, clock() - started), 3)})
            if report["status"] == "passed":
                report, _ = read()  # Verify current pointers and both pins.
                if report["status"] == "passed": return finish("COMPLETE")
            if report["pool"]["ready"] > previous["pool"]["ready"]:
                attempted.clear()
                cycle_pending = report["pool"]["pendingSourceCount"]
            elif pool_stage:
                attempted.update(observed)
                if cycle_pending and len(attempted) >= cycle_pending: return finish("NO_PROGRESS_FULL_POOL_CYCLE")
            else:
                public_stalls += 1
                if public_stalls >= 3: return finish("PUBLIC_PROMOTION_PENDING")
            if api.request_deadline - clock() <= 2: return finish("DEADLINE")
            api.sleep(2)
    except RunnerError as error:
        return finish("SOURCE_CHANGED" if error.status == 409 or error.code in {"NEWS_POOL_CHANGED", "NEWS_SNAPSHOT_CHANGED"} else "API_FAILURE", error)
    finally:
        api.clock, api.request_deadline = old_clock, old_deadline
