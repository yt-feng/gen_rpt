"""Resume and publish Chinese PDF translations through GateX's authenticated API.

Documents and model output stay in a private temporary directory. The durable
checkpoint is GateX R2; the public run summary contains only identities/counts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

BASE = "https://gatex.fund/api/internal/localized-pdfs"
MAX_PDF = 25 * 1024 * 1024
MAX_PROGRESS = 2 * 1024 * 1024
QA_SCHEMA = "gatex-pdf-translation-qa/v1"


class RunnerError(RuntimeError):
    def __init__(self, code: str, status: int | None = None, api_code: str | None = None):
        super().__init__(code)
        self.code, self.status = code, status
        self.api_code = api_code if isinstance(api_code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", api_code) else None


def error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code):
        return code
    name = type(error).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "TRANSLATION_FAILED"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


class GatexApi:
    routes = {"/inventory", "/source", "/progress", "/publish", "/artifact"}
    def __init__(self, token: str, base: str = BASE, session_factory=requests.Session, sleep=time.sleep, on_inventory_page=None):
        if base.rstrip("/") != BASE or not token:
            raise RunnerError("INVALID_API_CONFIGURATION")
        self.token, self.base, self.session_factory, self.sleep = token, base, session_factory, sleep
        self.on_inventory_page = on_inventory_page

    def request(self, method: str, route: str, *, binary=False, retry=True, **kwargs):
        if route.split("?", 1)[0] not in self.routes:
            raise RunnerError("UNTRUSTED_API_ROUTE")
        attempts = 3 if retry else 1
        for attempt in range(attempts):
            try:
                with self.session_factory() as session:
                    response = session.request(method, self.base + route,
                        headers={"Authorization": "Bearer " + self.token, "Accept": "application/pdf" if binary else "application/json"},
                        timeout=(15, 180), allow_redirects=False, stream=True, **kwargs)
                    try:
                        if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                            self.sleep(2 ** attempt)
                            continue
                        if not 200 <= response.status_code < 300:
                            raise RunnerError("API_HTTP_ERROR", response.status_code, response.headers.get("x-gatex-error-code"))
                        maximum = MAX_PDF if binary else MAX_PROGRESS
                        length = response.headers.get("content-length")
                        if length and int(length) > maximum:
                            raise RunnerError("RESPONSE_TOO_LARGE")
                        parts, size = [], 0
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > maximum:
                                raise RunnerError("RESPONSE_TOO_LARGE")
                            parts.append(chunk)
                        data = b"".join(parts)
                        return (data, dict(response.headers)) if binary else json.loads(data)
                    finally:
                        response.close()
            except (requests.Timeout, requests.ConnectionError):
                if attempt == attempts - 1:
                    raise RunnerError("API_CONNECTION_FAILED") from None
                self.sleep(2 ** attempt)
            except (ValueError, json.JSONDecodeError):
                raise RunnerError("INVALID_API_RESPONSE") from None
        raise RunnerError("API_CONNECTION_FAILED")

    def inventory(self) -> list[dict]:
        items, seen, cursor, cursors = [], set(), None, set()
        while True:
            route = "/inventory" + ("?" + urlencode({"cursor": cursor}) if cursor is not None else "")
            page = self.request("GET", route)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise RunnerError("INVALID_INVENTORY")
            for item in page["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise RunnerError("INVALID_INVENTORY_ITEM")
                if item["id"] in seen:
                    raise RunnerError("DUPLICATE_INVENTORY_ITEM")
                seen.add(item["id"])
                items.append(item)
            if self.on_inventory_page:
                self.on_inventory_page({"event": "inventory_page", "scannedCount": len(items), "total": page.get("total")})
            cursor = page.get("nextCursor")
            if cursor is None:
                return items
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise RunnerError("INVALID_INVENTORY_CURSOR")
            cursors.add(cursor)

    def source(self, item: dict):
        url = urlsplit(item.get("sourceUrl", ""))
        if url.scheme or url.netloc or url.path != "/api/internal/localized-pdfs/source" or parse_qs(url.query) != {"id": [item["id"]]}:
            raise RunnerError("UNTRUSTED_SOURCE_URL")
        return self.request("GET", "/source?" + urlencode({"id": item["id"]}), binary=True)

    def progress(self, identifier: str, digest: str):
        result = self.request("GET", "/progress?" + urlencode({"id": identifier, "sourceSha256": digest}))
        progress = result.get("progress") if isinstance(result, dict) else None
        if progress is not None and not isinstance(progress, dict):
            raise RunnerError("INVALID_PROGRESS")
        return progress

    def save_progress(self, identifier: str, digest: str, progress: dict):
        if not isinstance(progress, dict) or len(encoded_json(progress)) > MAX_PROGRESS:
            raise RunnerError("INVALID_PROGRESS")
        self.request("PUT", "/progress?" + urlencode({"id": identifier, "sourceSha256": digest}), json=progress)

    def publish(self, metadata: dict, pdf: Path):
        # Bytes allow an identical safe multipart replay after a transient reply.
        result = self.request("POST", "/publish", files={
            "metadata": (None, encoded_json(metadata), "application/json"),
            "pdf": ("GateX-zh.pdf", pdf.read_bytes(), "application/pdf"),
        })
        for key in ("sourceSha256", "sha256", "pageCount", "byteSize"):
            if not isinstance(result, dict) or result.get(key) != metadata[key]:
                raise RunnerError("PUBLISH_RECEIPT_MISMATCH")
        if result.get("ok") is not True:
            raise RunnerError("PUBLISH_NOT_ACCEPTED")

    def artifact(self, identifier: str, digest: str):
        return self.request("GET", "/artifact?" + urlencode({"id": identifier, "sourceSha256": digest}), binary=True)


def validate_qa(qa: Any, page_count: int):
    if not isinstance(qa, dict) or qa.get("schema") != QA_SCHEMA or qa.get("passed") is not True:
        raise RunnerError("QUALITY_CHECK_FAILED")
    for key in ("sourcePageCount", "pageCount", "sourceBlockCount", "translatedBlockCount"):
        if type(qa.get(key)) is not int or qa[key] < 0:
            raise RunnerError("QUALITY_CHECK_FAILED")
    if type(page_count) is not int or qa["pageCount"] != page_count or page_count < 1 or qa["sourcePageCount"] < 1:
        raise RunnerError("PAGE_COVERAGE_FAILED")
    if qa["sourceBlockCount"] != qa["translatedBlockCount"] or any(qa.get(key) != 0 for key in ("missingBlocks", "overflowCount", "unresolvedEnglishCount")):
        raise RunnerError("QUALITY_CHECK_FAILED")
    if qa.get("figuresPreserved") is not True or qa.get("numericChecksPassed") is not True:
        raise RunnerError("QUALITY_CHECK_FAILED")
    pages = qa.get("pages")
    if not isinstance(pages, list) or len(pages) != qa["sourcePageCount"]:
        raise RunnerError("PAGE_COVERAGE_FAILED")
    numbers = [p.get("sourcePage") if isinstance(p, dict) else None for p in pages]
    if any(type(number) is not int for number in numbers) or sorted(numbers) != list(range(1, qa["sourcePageCount"] + 1)):
        raise RunnerError("PAGE_COVERAGE_FAILED")
    targets = []
    for page in pages:
        mapped = page.get("targetPages")
        if not isinstance(mapped, list) or not mapped or any(type(n) is not int for n in mapped):
            raise RunnerError("TARGET_PAGE_COVERAGE_FAILED")
        targets.extend(mapped)
    if sorted(targets) != list(range(1, page_count + 1)):
        raise RunnerError("TARGET_PAGE_COVERAGE_FAILED")


class NewsfeedApi(GatexApi):
    routes = {"/warm", "/status"}

    def __init__(self, token: str, **kwargs):
        super().__init__(token, **kwargs)
        self.base = "https://gatex.fund/api/newsfeed/locales"


def warm_news(api: NewsfeedApi) -> dict:
    for attempt in range(3):
        try:
            # A timeout may follow a completed model call. Read current state
            # before repeating a warming mutation whose outcome is unknown.
            api.request("POST", "/warm", json={}, retry=False)
        except RunnerError as error:
            if error.code != "API_CONNECTION_FAILED" and not (error.code == "API_HTTP_ERROR" and (error.status == 429 or error.status and error.status >= 500)):
                raise
        status = api.request("GET", "/status")
        if not isinstance(status, dict) or any(type(status.get(key)) is not int or status[key] < 0 for key in ("total", "ready", "pending", "failed")):
            raise RunnerError("INVALID_NEWS_STATUS")
        if any(status[key] > status["total"] for key in ("ready", "pending", "failed")):
            raise RunnerError("INVALID_NEWS_STATUS")
        report = {"schema": "gatex-news-localization-run/v1", "status": "passed" if status.get("ok") is True and status.get("complete") is True and status["failed"] == 0 and status["total"] > 0 and status["ready"] == status["total"] and status["pending"] == 0 else "failed"}
        report.update({key: status.get(key) for key in ("version", "snapshotHash", "total", "ready", "pending", "failed")})
        if report["status"] == "passed":
            return report
        if attempt < 2:
            api.sleep(2 ** (attempt + 1))
    return report


def inventory_report(api: GatexApi) -> dict:
    rows = api.inventory()
    available = [{"id": row["id"], "status": row.get("status"), "sourceVersion": row.get("sourceVersion"), "sourceAvailable": not bool(row.get("error"))} for row in rows]
    unavailable = sum(not row["sourceAvailable"] for row in available)
    return {"schema": "gatex-localization-inventory/v1", "status": "failed" if unavailable else "passed",
        "inventoryCount": len(rows), "readyCount": sum(row["status"] == "ready" for row in available),
        "pendingCount": sum(row["status"] != "ready" for row in available), "unavailableCount": unavailable,
        "items": available}


def engine(module_name: str):
    translator = importlib.import_module(module_name).translate_pdf
    return translator


def run(api: GatexApi, translate: Callable, runtime: Path, *, limit=5, concurrency=2, force=False, sample_id=None, emit_progress=None) -> dict:
    if limit < 0 or concurrency not in (1, 2, 3):
        raise RunnerError("INVALID_BATCH_OPTIONS")
    # Snapshot every inventory page before publishing changes item status.
    inventory = [{"id": sample_id, "status": "pending", "sourceVersion": None, "sourceUrl": "/api/internal/localized-pdfs/source?" + urlencode({"id": sample_id})}] if sample_id else api.inventory()
    eligible = [item for item in inventory if force or item.get("status") != "ready" or item.get("error")]
    selected = eligible if limit == 0 else eligible[:limit]
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest_locks, digest_locks_guard = {}, Lock()

    def process(item):
        phase = "source"
        digest = ""
        identity = sha256(item["id"].encode())
        directory = Path(tempfile.mkdtemp(prefix=identity[:12] + "-", dir=runtime))
        try:
            if item.get("error"):
                raise RunnerError("SOURCE_UNAVAILABLE")
            original, headers = api.source(item)
            normalized_headers = {key.lower(): value for key, value in headers.items()}
            digest = normalized_headers.get("x-source-sha256", "")
            if not re.fullmatch("[a-f0-9]{64}", digest) or digest != sha256(original) or not original.startswith(b"%PDF-"):
                raise RunnerError("SOURCE_DIGEST_MISMATCH")
            if not normalized_headers.get("x-source-version") or (item.get("sourceVersion") is not None and item["sourceVersion"] != normalized_headers.get("x-source-version")):
                raise RunnerError("SOURCE_VERSION_CHANGED")
            source = directory / "source.pdf"
            source.write_bytes(original)
            # Different publication IDs can refer to identical PDF bytes. The
            # durable checkpoint is content-addressed, so serialize its writers
            # and let the next alias load the first alias's validated progress.
            with digest_locks_guard:
                digest_lock = digest_locks.setdefault(digest, Lock())
            with digest_lock:
                phase = "progress"
                progress = api.progress(item["id"], digest)
                phase = "translation"
                artifact = translate(source_pdf=source, output_pdf=directory / "zh.pdf", source_sha256=digest,
                    progress=progress, save_progress=lambda value: api.save_progress(item["id"], digest, value), force=force)
                phase = "validation"
                if not isinstance(artifact, dict):
                    raise RunnerError("INVALID_ENGINE_ARTIFACT")
                pdf = Path(artifact["path"]).resolve()
                if not pdf.is_relative_to(directory.resolve()) or not pdf.is_file():
                    raise RunnerError("INVALID_ENGINE_ARTIFACT")
                data = pdf.read_bytes()
                if not data.startswith(b"%PDF-") or len(data) > MAX_PDF or artifact.get("byteSize") != len(data) or artifact.get("sha256") != sha256(data):
                    raise RunnerError("ARTIFACT_DIGEST_MISMATCH")
                page_count = artifact.get("pageCount")
                validate_qa(artifact.get("qa"), page_count)
                if sha256(data) == digest:
                    raise RunnerError("UNTRANSLATED_SOURCE_ARTIFACT")
                metadata = {"id": item["id"], "sourceSha256": digest, "sha256": sha256(data), "pageCount": page_count, "byteSize": len(data), "qa": artifact["qa"]}
                phase = "publication"
                api.publish(metadata, pdf)
                phase = "readback"
                stored, stored_headers = api.artifact(item["id"], digest)
                stored_headers = {key.lower(): value for key, value in stored_headers.items()}
                if sha256(stored) != metadata["sha256"] or len(stored) != metadata["byteSize"] or stored_headers.get("x-source-sha256") != digest or stored_headers.get("x-artifact-sha256") != metadata["sha256"]:
                    raise RunnerError("ARTIFACT_READBACK_FAILED")
                return {"id": item["id"], "status": "ready", "sourceSha256": digest, "sha256": metadata["sha256"], "pageCount": page_count, "byteSize": len(data), "sourceBlockCount": artifact["qa"]["sourceBlockCount"], "translatedBlockCount": artifact["qa"]["translatedBlockCount"]}
        except Exception as error:
            # Never echo provider responses, source text, credentials or traceback.
            result = {"id": item["id"], "status": "failed", "phase": phase, "code": error_code(error), "httpStatus": error.status if isinstance(error, RunnerError) else None, "apiCode": error.api_code if isinstance(error, RunnerError) else None}
            if result["code"] == "OCR_DIAGNOSTIC_READY":
                counts = getattr(error, "diagnostic_counts", {})
                if isinstance(counts, dict):
                    result["diagnosticCounts"] = {key: counts[key] for key in ("sourcePageCount", "rasterRegionCount", "wordCount") if type(counts.get(key)) is int and counts[key] >= 0}
                result["sourceSha256"] = digest
            return result
        finally:
            import shutil
            shutil.rmtree(directory, ignore_errors=True)

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(process, item) for item in selected]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if emit_progress:
                emit_progress({"event": "pdf_completed", "id": result["id"], "status": result["status"],
                    "completedCount": len(results), "selectedCount": len(selected),
                    "translatedCount": sum(row["status"] == "ready" for row in results),
                    "failedCount": sum(row["status"] == "failed" for row in results)})
    results.sort(key=lambda item: item["id"])
    failed = sum(item["status"] == "failed" for item in results)
    try:
        remaining = None if sample_id else sum(item.get("status") != "ready" or bool(item.get("error")) for item in api.inventory())
    except Exception:
        remaining = None
    return {"schema": "gatex-localization-run/v1", "status": "failed" if failed or (remaining is None and not sample_id) else "passed", "scope": "single" if sample_id else "inventory", "inventoryCount": len(inventory), "selectedCount": len(selected), "translatedCount": len(results)-failed, "failedCount": failed, "remainingPending": remaining, "force": force, "results": results}


def run_ocr_diagnostic(api: GatexApi, translate: Callable, runtime: Path, *, sample_id: str, expected_source_sha256: str, emit_progress=None) -> dict:
    if not sample_id or not re.fullmatch(r"[a-f0-9]{64}", expected_source_sha256 or ""):
        raise RunnerError("DIAGNOSTIC_SAMPLE_AND_DIGEST_REQUIRED")
    def pinned(**kwargs):
        if kwargs["source_sha256"] != expected_source_sha256:
            raise RunnerError("DIAGNOSTIC_SOURCE_CHANGED")
        return translate(**kwargs)
    batch = run(api, pinned, runtime, limit=1, concurrency=1, force=False, sample_id=sample_id, emit_progress=emit_progress)
    result = batch["results"][0]
    counts = result.get("diagnosticCounts", {})
    complete = result.get("code") == "OCR_DIAGNOSTIC_READY" and result.get("sourceSha256") == expected_source_sha256 and set(counts) == {"sourcePageCount", "rasterRegionCount", "wordCount"} and counts["sourcePageCount"] > 0
    return {"schema": "gatex-pdf-ocr-diagnostic/v1", "status": "passed" if complete else "failed", "scope": "diagnostic", "id": sample_id, "sourceSha256": expected_source_sha256,
        "code": result.get("code"), "httpStatus": result.get("httpStatus"), "apiCode": result.get("apiCode"), "counts": counts, "translatedCount": 0, "publishedCount": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="0 processes the entire pending inventory")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--sample-id", help="Process exactly this published report without scanning the entire inventory")
    parser.add_argument("--expected-source-sha256", help="Required immutable source digest for OCR diagnostics")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--news-only", action="store_true")
    mode.add_argument("--inventory-only", action="store_true", help="Audit published inventory without model calls or PDF processing")
    mode.add_argument("--ocr-diagnostic-only", action="store_true", help="Capture private Tesseract evidence without model calls or publication")
    parser.add_argument("--engine-module", default="gen_rpt.gatex_pdf_localization")
    args = parser.parse_args()
    emit = lambda value: print(json.dumps(value, ensure_ascii=False), flush=True)
    try:
        if args.news_only:
            report = warm_news(NewsfeedApi(os.environ.get("GATEX_NEWSFEED_CRON_SECRET", "")))
        else:
            api = GatexApi(os.environ.get("GATEX_GENERATION_CALLBACK_SECRET", ""), on_inventory_page=emit)
            if args.ocr_diagnostic_only:
                if args.force:
                    raise RunnerError("DIAGNOSTIC_FORCE_FORBIDDEN")
                os.environ["GATEX_PDF_OCR_DIAGNOSTIC_ONLY"] = "true"
                os.environ.pop("APIMART_API_KEY", None)
                report = run_ocr_diagnostic(api, engine(args.engine_module), args.runtime,
                    sample_id=args.sample_id, expected_source_sha256=args.expected_source_sha256, emit_progress=emit)
            else:
                report = inventory_report(api) if args.inventory_only else run(api, engine(args.engine_module), args.runtime,
                    limit=args.limit, concurrency=args.concurrency, force=args.force, sample_id=args.sample_id, emit_progress=emit)
    except Exception as error:
        report = {"schema": "gatex-localization-run/v1", "status": "failed", "phase": "news" if args.news_only else "diagnostic" if args.ocr_diagnostic_only else "inventory", "httpStatus": error.status if isinstance(error, RunnerError) else None, "code": error_code(error), "apiCode": error.api_code if isinstance(error, RunnerError) else None}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_bytes(encoded_json(report))
    print(json.dumps({key: value for key, value in report.items() if key not in {"results", "items"}}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
