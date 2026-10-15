"""Bounded, version-aware PDF maintenance. Queue/cache files contain metadata only.

The existing runner owns authentication, checkpoints, publication and readback.
Real translation runs in a private child process with an elapsed-time deadline;
discovery does not import the model engine or download source PDFs.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

import requests

from scripts import gatex_localization_runner as runner

SCHEMA = "gatex-pdf-maintenance-queue/v1"
REPORT_SCHEMA = "gatex-pdf-maintenance/v1"
QUALITY_POLICY = "gatex-professional-zh/v1"
ALIAS_POLICY = "gatex-source-occurrence-review/v1"
MAX_QUEUE_BYTES = 2 * 1024 * 1024
MAX_UNIQUE = 2
BACKOFF = (3600, 14400, 43200, 86400)
ENTRY_KEYS = {"id", "sourceVersion", "firstSeenAt", "lastAttemptAt", "nextAttemptAt", "attemptCount", "lastSafeCode", "sourceSha256", "discoveryClass"}
PDF_WORKFLOWS = {".github/workflows/gatex-content-localization.yml", ".github/workflows/gatex-pdf-backfill.yml", ".github/workflows/gatex-pdf-maintenance.yml"}


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def identity(value):
    return isinstance(value, str) and 0 < len(value) <= 1024 and not any(ord(char) < 32 for char in value)


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", value) else "MAINTENANCE_FAILED"


def load_queue(path):
    """A missing/corrupt cache is a rebuild, never proof that work completed."""
    try:
        data = Path(path).read_bytes()
        if len(data) > MAX_QUEUE_BYTES:
            raise ValueError
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != {"schema", "entries"} or value["schema"] != SCHEMA or not isinstance(value["entries"], list):
            raise ValueError
        entries = {}
        for row in value["entries"]:
            if not isinstance(row, dict) or set(row) != ENTRY_KEYS or not identity(row["id"]) or not identity(row["sourceVersion"]) or row["id"] in entries:
                raise ValueError
            if any(not timestamp(row[name]) for name in ("firstSeenAt", "lastAttemptAt", "nextAttemptAt")) or type(row["attemptCount"]) is not int or not 0 <= row["attemptCount"] <= 1_000_000:
                raise ValueError
            if row["sourceSha256"] is not None and not digest(row["sourceSha256"]):
                raise ValueError
            if row["discoveryClass"] not in {"bootstrap", "new", "changed"}:
                raise ValueError
            if row["lastSafeCode"] is not None and safe_code(row["lastSafeCode"]) != row["lastSafeCode"]:
                raise ValueError
            entries[row["id"]] = row
        return entries, False
    except (OSError, ValueError, KeyError, TypeError):
        return {}, True


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = runner.encoded_json(value)
    if len(data) > MAX_QUEUE_BYTES:
        raise runner.RunnerError("MAINTENANCE_METADATA_TOO_LARGE")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    temporary.replace(path)


def save_queue(path, entries):
    write_json(path, {"schema": SCHEMA, "entries": sorted(entries.values(), key=lambda row: row["id"])})


def reconcile(rows, previous, now, *, bootstrap=False):
    result, seen = {}, set()
    for item in rows:
        identifier, version = item.get("id"), item.get("sourceVersion")
        if not identity(version) and item.get("error"):
            # A genuinely unavailable source can lack a version. Keep it as an
            # isolated retry, and replace this sentinel once metadata recovers.
            version = "unavailable-source-metadata"
        if not identity(identifier) or identifier in seen or not identity(version):
            raise runner.RunnerError("INVALID_MAINTENANCE_INVENTORY")
        seen.add(identifier)
        # GateX inventory readiness is current-policy-aware. Never infer it from
        # the queue, old artifacts, checkpoint counts or a previous run outcome.
        if item.get("status") == "ready" and not item.get("error"):
            continue
        old = previous.get(identifier)
        if old and old["sourceVersion"] == version:
            result[identifier] = copy.deepcopy(old)
        else:
            result[identifier] = {"id": identifier, "sourceVersion": version, "firstSeenAt": now,
                "lastAttemptAt": 0, "nextAttemptAt": 0, "attemptCount": 0, "lastSafeCode": None, "sourceSha256": None,
                "discoveryClass": "changed" if old else "bootstrap" if bootstrap else "new"}
    return result


def select(entries, now, maximum=MAX_UNIQUE):
    if type(maximum) is not int or not 1 <= maximum <= MAX_UNIQUE:
        raise runner.RunnerError("INVALID_MAINTENANCE_LIMIT")
    due = sorted((row for row in entries.values() if row["nextAttemptAt"] <= now),
        key=lambda row: (row["lastAttemptAt"] or row["firstSeenAt"], row["firstSeenAt"], row["id"]))
    # One FIFO lane for genuinely new/changed versions, one oldest-due lane.
    # A large initial catalog cannot hide new publications, while an ongoing
    # stream of arrivals cannot consume both slots and starve older work.
    arrivals = sorted((row for row in due if row["attemptCount"] == 0 and row["discoveryClass"] != "bootstrap"),
        key=lambda row: (row["firstSeenAt"], row["id"]))
    ordered = ([arrivals[0]] if arrivals else []) + due
    groups, selected = set(), []
    seen_ids = set()
    for row in ordered:
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        group = row["sourceSha256"] or "id:" + row["id"]
        if group not in groups and len(groups) >= maximum:
            continue
        groups.add(group)
        selected.append(row)
        # At most four identity downloads/publishes even for a large alias group.
        if len(selected) == maximum * 2:
            break
    return selected


def validate_current_qa(qa, pages, source_sha256=None):
    runner.validate_qa(qa, pages)
    if (not digest(qa.get("sourceSha256")) or (source_sha256 is not None and qa["sourceSha256"] != source_sha256)
            or qa.get("semanticReviewPassed") is not True or qa.get("rasterOCRVerified") is not True
            or type(qa.get("minimumTranslatedFontSize")) not in (int, float)
            or not math.isfinite(qa["minimumTranslatedFontSize"]) or qa["minimumTranslatedFontSize"] < 7
            or qa.get("qualityPolicyVersion") != QUALITY_POLICY or qa.get("professionalChinesePassed") is not True
            or qa.get("ownedTerminologyPassed") is not True or type(qa.get("qualityCheckedBlockCount")) is not int
            or qa["qualityCheckedBlockCount"] != qa["translatedBlockCount"] or qa.get("renderAliasReviewPolicy") != ALIAS_POLICY):
        raise runner.RunnerError("CURRENT_QUALITY_POLICY_REQUIRED")


def verify_source(api, item, expected=None):
    if item.get("error"):
        raise runner.RunnerError("SOURCE_UNAVAILABLE")
    data, headers = api.source(item)
    headers = {key.lower(): value for key, value in headers.items()}
    value = headers.get("x-source-sha256")
    if not digest(value) or runner.sha256(data) != value or not data.startswith(b"%PDF-") or len(data) > runner.MAX_PDF:
        raise runner.RunnerError("SOURCE_DIGEST_MISMATCH")
    if headers.get("x-source-version") != item["sourceVersion"]:
        raise runner.RunnerError("SOURCE_VERSION_CHANGED")
    if expected is not None and value != expected:
        raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
    return data, headers


class PinnedApi:
    def __init__(self, api, pins, sources):
        self.api, self.pins, self.sources = api, pins, sources

    def __getattr__(self, key):
        return getattr(self.api, key)

    def inventory(self):
        return [{**pin, "status": "pending"} for pin in self.pins]

    def source(self, item):
        pin = next(pin for pin in self.pins if pin["id"] == item["id"])
        data, headers = self.sources[item["id"]]
        if runner.sha256(data) != pin["sourceSha256"] or headers.get("x-source-version") != pin["sourceVersion"]:
            raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
        return data, headers


def process_group(api, translate, pins, sources, runtime):
    """Aliases share one successful result or one failure and one model budget."""
    cached, failure = None, None

    def once(**kwargs):
        nonlocal cached, failure
        if kwargs["source_sha256"] != pins[0]["sourceSha256"]:
            raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
        if failure is not None:
            raise failure
        if cached is None:
            try:
                result = translate(**kwargs)
                validate_current_qa(result.get("qa"), result.get("pageCount"), pins[0]["sourceSha256"])
                path = Path(result["path"]).resolve()
                if not path.is_relative_to(kwargs["output_pdf"].parent.resolve()):
                    raise runner.RunnerError("INVALID_ENGINE_ARTIFACT")
                data = path.read_bytes()
                if result.get("sha256") != runner.sha256(data) or result.get("byteSize") != len(data):
                    raise runner.RunnerError("ARTIFACT_DIGEST_MISMATCH")
                cached = data, {key: value for key, value in result.items() if key != "path"}
            except Exception as error:
                failure = runner.RunnerError(safe_code(runner.error_code(error)))
                raise failure from None
        data, metadata = cached
        kwargs["output_pdf"].write_bytes(data)
        return {**metadata, "path": str(kwargs["output_pdf"])}

    return runner.run(PinnedApi(api, pins, sources), once, runtime, limit=0, concurrency=1, force=False)["results"]


def child_group(api, pins, sources, runtime, seconds):
    """Hard elapsed deadline including OCR/models, with no private child output."""
    with tempfile.TemporaryDirectory(prefix="maintenance-", dir=runtime) as directory:
        directory = Path(directory)
        rows = []
        for index, pin in enumerate(pins):
            path = directory / f"source-{index}.pdf"
            path.write_bytes(sources[pin["id"]][0])
            rows.append({"pin": pin, "sourcePath": str(path)})
        plan, output = directory / "private-plan.json", directory / "result.json"
        write_json(plan, rows)
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(plan), "--worker-output", str(output)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=max(1, seconds))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise runner.RunnerError("MAINTENANCE_DEADLINE_EXCEEDED") from None
        if process.returncode or not output.is_file() or output.stat().st_size > MAX_QUEUE_BYTES:
            raise runner.RunnerError("MAINTENANCE_WORKER_FAILED")
        value = json.loads(output.read_text())
        if not isinstance(value, list) or len(value) != len(pins) or {row.get("id") for row in value} != {pin["id"] for pin in pins}:
            raise runner.RunnerError("MAINTENANCE_WORKER_RESULT_INVALID")
        return value


def _maintain(api, queue_path, runtime, *, discovery_only=True, maximum=2, budget_seconds=1800,
             clock=time.monotonic, wall=time.time, execute=child_group, emit=None):
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 1 <= budget_seconds <= 3600:
        raise runner.RunnerError("INVALID_MAINTENANCE_BUDGET")
    started, now = clock(), wall()
    prior, rebuilt = load_queue(queue_path)
    inventory = api.inventory()
    entries = reconcile(inventory, prior, now, bootstrap=rebuilt)
    selected = select(entries, now, maximum)
    save_queue(queue_path, entries)
    base = {"schema": REPORT_SCHEMA, "scope": "bounded-maintenance", "discoveryOnly": discovery_only,
        "cacheRebuilt": rebuilt, "inventoryCount": len(inventory), "pendingBefore": len(entries),
        "selectedIds": [row["id"] for row in selected], "limitUniqueSources": maximum}
    if discovery_only:
        return {**base, "status": "planned", "selectedUniqueSources": 0, "readyCount": 0, "failedCount": 0,
            "deferredCount": len(entries), "globalPending": len(entries), "globalComplete": not entries, "results": []}
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    by_id = {item["id"]: item for item in inventory}
    groups, sources, results, deferred = {}, {}, [], set()

    def attempted(row):
        row["attemptCount"] += 1
        row["lastAttemptAt"] = wall()
        row["nextAttemptAt"] = wall() + BACKOFF[min(row["attemptCount"] - 1, len(BACKOFF) - 1)]
        row["lastSafeCode"] = "INCOMPLETE_ATTEMPT"
        save_queue(queue_path, entries)

    for row in selected:
        if clock() - started >= budget_seconds:
            break
        attempted(row)
        try:
            data, headers = verify_source(api, by_id[row["id"]])
            value = headers["x-source-sha256"]
            row["sourceSha256"] = value
            if value not in groups and len(groups) >= maximum:
                row["nextAttemptAt"], row["lastSafeCode"] = 0, "BATCH_DEFERRED"
                row["attemptCount"] -= 1
                deferred.add(row["id"])
                continue
            pin = {"id": row["id"], "sourceVersion": row["sourceVersion"], "sourceSha256": value,
                "sourceUrl": "/api/internal/localized-pdfs/source?" + urlencode({"id": row["id"]})}
            groups.setdefault(value, []).append(pin)
            sources[row["id"]] = (data, headers)
        except Exception as error:
            row["lastSafeCode"] = safe_code(runner.error_code(error))
            results.append({"id": row["id"], "status": "failed", "code": row["lastSafeCode"]})
        save_queue(queue_path, entries)
    for index, (value, pins) in enumerate(groups.items()):
        remaining = budget_seconds - (clock() - started)
        # Reserve a share for every later selected source and for the final
        # metadata readback. A stalled first source cannot take the whole run.
        final_reserve = min(60, budget_seconds * .05)
        share = (remaining - final_reserve) / (len(groups) - index)
        if share < 1:
            break
        try:
            completed = execute(api, pins, sources, runtime, share)
        except Exception as error:
            completed = [{"id": pin["id"], "status": "failed", "code": safe_code(runner.error_code(error))} for pin in pins]
        for result in completed:
            row = entries[result["id"]]
            if result.get("status") == "ready" and result.get("sourceSha256") == value:
                row["lastSafeCode"] = None
                results.append({key: result[key] for key in ("id", "status", "sourceSha256", "sha256", "pageCount", "byteSize", "sourceBlockCount", "translatedBlockCount") if key in result})
            else:
                row["lastSafeCode"] = safe_code(result.get("code"))
                results.append({"id": row["id"], "status": "failed", "code": row["lastSafeCode"]})
        save_queue(queue_path, entries)
        if emit:
            emit({"event": "maintenance_group_completed", "completedIds": len(results), "readyIds": sum(row["status"] == "ready" for row in results), "failedIds": sum(row["status"] == "failed" for row in results)})
    try:
        final = api.inventory()
        final_by_id = {row["id"]: row for row in final}
        for result in results:
            actual = final_by_id.get(result["id"])
            if result["status"] == "ready" and (actual is None or actual.get("status") != "ready" or actual.get("error") or actual.get("sourceVersion") != entries[result["id"]]["sourceVersion"]):
                result.update(status="failed", code="FINAL_READINESS_OR_VERSION_CHANGED")
                entries[result["id"]]["lastSafeCode"] = result["code"]
        entries = reconcile(final, entries, wall())
        save_queue(queue_path, entries)
        unreadable = False
    except Exception:
        unreadable = True
    failed = sum(row["status"] == "failed" for row in results)
    incomplete = {row["id"] for row in selected} - {row["id"] for row in results} - deferred
    return {**base, "status": "failed" if failed or incomplete or unreadable else "passed",
        "selectedUniqueSources": len(groups), "readyCount": sum(row["status"] == "ready" for row in results),
        "failedCount": failed, "incompleteSelectedCount": len(incomplete), "deferredSelectedCount": len(deferred), "inventoryReadbackFailed": unreadable,
        "deferredCount": len(entries), "globalPending": None if unreadable else len(entries),
        "globalComplete": not unreadable and not entries, "results": results}


def maintain(api, queue_path, runtime, **options):
    # Bound source/inventory requests as well as the model child. Restore a
    # caller's deadline when used from another authorized orchestration layer.
    previous = getattr(api, "request_deadline", None)
    clock = options.get("clock", time.monotonic)
    budget = options.get("budget_seconds", 1800)
    if type(budget) not in (int, float) or not math.isfinite(budget) or not 1 <= budget <= 3600:
        raise runner.RunnerError("INVALID_MAINTENANCE_BUDGET")
    if hasattr(api, "request_deadline"):
        deadline = clock() + budget
        api.request_deadline = min(previous, deadline) if previous is not None else deadline
    try:
        return _maintain(api, queue_path, runtime, **options)
    finally:
        if hasattr(api, "request_deadline"):
            api.request_deadline = previous


def pdf_busy(runs, jobs, current_run_id):
    for run in runs:
        if str(run.get("id")) == str(current_run_id) or run.get("event") not in {"workflow_dispatch", "schedule"} or run.get("path", "").split("@", 1)[0] not in PDF_WORKFLOWS or run.get("status") == "completed":
            continue
        if run["path"].split("@", 1)[0] != ".github/workflows/gatex-content-localization.yml":
            return True
        listed = jobs(run["id"])
        if not listed or any(job.get("name") == "localize" and job.get("status") != "completed" for job in listed):
            return True
    return False


def github_busy(token, current_run_id):
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"})
    def get(route):
        response = session.get("https://api.github.com/repos/yt-feng/gen_rpt" + route, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise runner.RunnerError("GITHUB_PREFLIGHT_UNAVAILABLE")
        return response.json()
    runs = []
    for status in ("queued", "in_progress", "waiting", "requested", "pending"):
        page = 1
        while True:
            batch = get(f"/actions/runs?status={status}&per_page=100&page={page}").get("workflow_runs")
            if not isinstance(batch, list):
                raise runner.RunnerError("GITHUB_PREFLIGHT_UNAVAILABLE")
            runs.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return pdf_busy(runs, lambda identifier: get(f"/actions/runs/{identifier}/jobs?per_page=100").get("jobs", []), current_run_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--budget-seconds", type=int, default=1800)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--worker-output", type=Path)
    args = parser.parse_args()
    # Only trusted default-branch code can restore/write the persistent queue or
    # receive translation credentials. No branch override or fork entrypoint.
    if os.environ.get("GITHUB_REPOSITORY") != "yt-feng/gen_rpt" or os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") not in {"workflow_dispatch", "schedule"}:
        raise runner.RunnerError("TRUSTED_MAIN_REQUIRED")
    if args.preflight:
        busy = github_busy(os.environ.get("GH_TOKEN", ""), os.environ.get("GITHUB_RUN_ID"))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("proceed=" + ("false" if busy else "true") + "\n")
        print(json.dumps({"event": "pdf_maintenance_preflight", "status": "deferred" if busy else "ready", "code": "PDF_MUTEX_BUSY" if busy else None}))
        return 0
    api = runner.GatexApi(os.environ.get("GATEX_GENERATION_CALLBACK_SECRET", ""))
    if args.worker:
        rows = json.loads(args.worker.read_text())
        pins, sources = [], {}
        for row in rows:
            pin = row["pin"]
            data = Path(row["sourcePath"]).read_bytes()
            if runner.sha256(data) != pin["sourceSha256"]:
                raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
            pins.append(pin)
            sources[pin["id"]] = (data, {"x-source-sha256": pin["sourceSha256"], "x-source-version": pin["sourceVersion"]})
        results = process_group(api, runner.engine("gen_rpt.gatex_pdf_localization"), pins, sources, args.worker.parent / "engine")
        write_json(args.worker_output, results)
        return 0
    if not all((args.queue, args.summary, args.runtime)):
        parser.error("queue, summary and runtime paths are required")
    try:
        report = maintain(api, args.queue, args.runtime, discovery_only=args.discovery_only,
            budget_seconds=args.budget_seconds, emit=lambda value: print(json.dumps(value), flush=True))
    except Exception as error:
        report = {"schema": REPORT_SCHEMA, "status": "failed", "code": safe_code(runner.error_code(error)), "globalComplete": False}
    write_json(args.summary, report)
    print(json.dumps({key: value for key, value in report.items() if key not in {"results", "selectedIds"}}), flush=True)
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except runner.RunnerError as error:
        print(json.dumps({"status": "failed", "code": safe_code(error.code)}))
        raise SystemExit(1) from None
