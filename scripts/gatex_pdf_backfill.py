"""Source-pinned PDF audit and bounded backfill; artifacts contain counts only."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode

from scripts import gatex_localization_runner as runner

SCHEMA = "gatex-pdf-backfill-manifest/v1"
RESULT_SCHEMA = "gatex-pdf-backfill-result/v1"
SHARDS = 3


def pdf_pages(data):
    import fitz
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.is_encrypted or document.page_count < 1:
            raise runner.RunnerError("INVALID_SOURCE_PDF")
        return document.page_count


def pdf_metrics(data):
    import fitz
    blocks = characters = images = 0
    with fitz.open(stream=data, filetype="pdf") as document:
        for page in document:
            for block in page.get_text("blocks"):
                if len(block) >= 7 and block[6] == 0 and block[4].strip():
                    blocks += 1
                    characters += len(block[4])
            images += len(page.get_image_info())
    return {"nativeTextBlocks": blocks, "nativeTextCharacters": characters, "rasterImageRegions": images}


def source_item(identifier, version, status="pending"):
    return {"id": identifier, "sourceVersion": version, "status": status,
            "sourceUrl": "/api/internal/localized-pdfs/source?" + urlencode({"id": identifier})}


def failure(identifier, error):
    return {"id": identifier, "status": "failed", "code": runner.error_code(error),
            "httpStatus": error.status if isinstance(error, runner.RunnerError) else None,
            "apiCode": error.api_code if isinstance(error, runner.RunnerError) else None}


def checked_source(api, item, expected_digest=None, page_counter=pdf_pages):
    data, headers = api.source(item)
    headers = {key.lower(): value for key, value in headers.items()}
    digest = headers.get("x-source-sha256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or runner.sha256(data) != digest or not data.startswith(b"%PDF-"):
        raise runner.RunnerError("SOURCE_DIGEST_MISMATCH")
    if expected_digest is not None and digest != expected_digest:
        raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
    if not item.get("sourceVersion") or headers.get("x-source-version") != item["sourceVersion"]:
        raise runner.RunnerError("SOURCE_VERSION_CHANGED")
    pages = page_counter(data)
    if type(pages) is not int or pages < 1:
        raise runner.RunnerError("INVALID_SOURCE_PDF")
    return data, digest, pages


def checked_artifact(api, identifier, digest, page_counter=pdf_pages):
    data, headers = api.artifact(identifier, digest)
    headers = {key.lower(): value for key, value in headers.items()}
    target = runner.sha256(data)
    if (not data.startswith(b"%PDF-") or len(data) > runner.MAX_PDF or target == digest
            or headers.get("x-source-sha256") != digest or headers.get("x-artifact-sha256") != target):
        raise runner.RunnerError("ARTIFACT_READBACK_FAILED")
    pages = page_counter(data)
    if type(pages) is not int or pages < 1:
        raise runner.RunnerError("ARTIFACT_READBACK_FAILED")
    return {"available": True, "sha256": target, "pageCount": pages, "byteSize": len(data)}


def checkpoint_counts(progress, digest):
    empty = {"exists": False, "storedBlocks": 0, "reviewedBlocks": 0, "verifiedOcrRegions": 0, "dateReviewDrafts": 0}
    if progress is None:
        return empty
    if not isinstance(progress, dict) or progress.get("sourceSha256") != digest or progress.get("schema") != "gatex-pdf-translation-progress/v1":
        raise runner.RunnerError("PROGRESS_SOURCE_MISMATCH")
    blocks, ocr = progress.get("blocks", {}), progress.get("ocr", {})
    if not isinstance(blocks, dict) or not isinstance(ocr, dict):
        raise runner.RunnerError("INVALID_PROGRESS")
    # These are stored-cache statistics, never an approval or a promise that an
    # older model/policy cache can be reused. The engine revalidates every block.
    return {"exists": True, "storedBlocks": len(blocks),
            "reviewedBlocks": sum(isinstance(value, dict) and value.get("reviewed") is True for value in blocks.values()),
            "verifiedOcrRegions": sum(isinstance(value, dict) and value.get("verified") is True
                                      and value.get("uncertainRegions") == [] and isinstance(value.get("coverage"), dict)
                                      and value["coverage"].get("schema") == "gatex-raster-text-coverage/v2" for value in ocr.values()),
            "dateReviewDrafts": len(progress.get("dateReviewDrafts", {})) if isinstance(progress.get("dateReviewDrafts", {}), dict) else 0}


def snapshot_digest(items):
    identities = [{key: row.get(key) for key in ("id", "sourceVersion", "sourceSha256")} for row in sorted(items, key=lambda row: row["id"])]
    return runner.sha256(runner.encoded_json(identities))


def source_groups(items):
    groups = defaultdict(list)
    for row in items:
        if row.get("sourceSha256"):
            groups[row["sourceSha256"]].append(row)
    return dict(sorted(groups.items()))


def audit(api, *, concurrency=3, page_counter=pdf_pages, emit=None):
    if concurrency not in (1, 2, 3):
        raise runner.RunnerError("INVALID_BATCH_OPTIONS")
    inventory = api.inventory()
    if not inventory:
        raise runner.RunnerError("EMPTY_INVENTORY")

    def inspect(item):
        row = {"id": item["id"], "sourceVersion": item.get("sourceVersion"), "status": item.get("status")}
        try:
            if item.get("error"):
                raise runner.RunnerError("SOURCE_UNAVAILABLE")
            data, digest, pages = checked_source(api, item, page_counter=page_counter)
            metrics = pdf_metrics(data) if page_counter is pdf_pages else {"nativeTextBlocks": 0, "nativeTextCharacters": 0, "rasterImageRegions": 0}
            row.update(sourceSha256=digest, pageCount=pages, byteSize=len(data), shard=int(digest, 16) % SHARDS, **metrics)
        except Exception as error:
            row.update(failure(item["id"], error))
        return row

    items = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(inspect, item) for item in inventory]
        for future in as_completed(futures):
            items.append(future.result())
            if emit:
                emit({"event": "pdf_source_audited", "completedCount": len(items), "inventoryCount": len(inventory),
                      "unavailableCount": sum(row["status"] == "failed" for row in items)})
    items.sort(key=lambda row: row["id"])
    sources = []
    for digest, aliases in source_groups(items).items():
        record = {"sourceSha256": digest, "shard": int(digest, 16) % SHARDS,
                  "aliases": sorted(row["id"] for row in aliases), "pageCount": aliases[0]["pageCount"], "byteSize": aliases[0]["byteSize"],
                  **{key: aliases[0][key] for key in ("nativeTextBlocks", "nativeTextCharacters", "rasterImageRegions")}}
        try:
            if len({(row["pageCount"], row["byteSize"]) for row in aliases}) != 1:
                raise runner.RunnerError("SOURCE_ALIAS_METADATA_MISMATCH")
            record["checkpoint"] = checkpoint_counts(api.progress(aliases[0]["id"], digest), digest)
            try:
                record["artifact"] = checked_artifact(api, aliases[0]["id"], digest, page_counter)
            except runner.RunnerError as error:
                if error.status != 404:
                    raise
                record["artifact"] = {"available": False}
            if any(row["status"] == "ready" for row in aliases) and not record["artifact"]["available"]:
                raise runner.RunnerError("READY_ARTIFACT_UNAVAILABLE")
        except Exception as error:
            record["error"] = failure(aliases[0]["id"], error)
        sources.append(record)
    inventory_unchanged, inventory_check = False, None
    try:
        current = api.inventory()
        inventory_unchanged = {row["id"]: row.get("sourceVersion") for row in current} == {row["id"]: row.get("sourceVersion") for row in items}
        if not inventory_unchanged:
            inventory_check = {"code": "INVENTORY_CHANGED"}
    except Exception as error:
        inventory_check = failure("inventory", error)
    failed = sum(row["status"] == "failed" for row in items) + sum("error" in row for row in sources) + int(not inventory_unchanged)
    counts = {"inventoryCount": len(items), "uniqueSourceCount": len(sources),
              "sourceVerifiedIds": sum(bool(row.get("sourceSha256")) for row in items),
              "sourceUnavailableIds": sum(not row.get("sourceSha256") for row in items),
              "sourcePagesIncludingAliases": sum(row.get("pageCount", 0) for row in items),
              "uniqueSourcePages": sum(row["pageCount"] for row in sources),
              "uniqueNativeTextBlocks": sum(row["nativeTextBlocks"] for row in sources),
              "uniqueNativeTextCharacters": sum(row["nativeTextCharacters"] for row in sources),
              "uniqueRasterImageRegions": sum(row["rasterImageRegions"] for row in sources),
              "readyIds": sum(row["status"] == "ready" for row in items),
              "pendingIds": sum(row["status"] != "ready" for row in items),
              "verifiedArtifactSources": sum(row.get("artifact", {}).get("available") is True for row in sources),
              "checkpointSources": sum(row.get("checkpoint", {}).get("exists") is True for row in sources),
              "storedBlocks": sum(row.get("checkpoint", {}).get("storedBlocks", 0) for row in sources),
              "storedReviewedBlocks": sum(row.get("checkpoint", {}).get("reviewedBlocks", 0) for row in sources),
              "verifiedOcrRegions": sum(row.get("checkpoint", {}).get("verifiedOcrRegions", 0) for row in sources),
              "auditFailures": failed}
    return {"schema": SCHEMA, "status": "failed" if failed else "passed", "shardCount": SHARDS,
            "inventoryUnchanged": inventory_unchanged, "inventoryCheck": inventory_check,
            "capturedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "snapshotSha256": snapshot_digest(items),
            "counts": counts, "items": items, "sources": sources}


def load_manifest(path, expected_sha256):
    data = path.read_bytes()
    if len(data) > runner.MAX_PROGRESS or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256 or "") or runner.sha256(data) != expected_sha256:
        raise runner.RunnerError("MANIFEST_DIGEST_MISMATCH")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("shardCount") != SHARDS or not isinstance(value.get("items"), list):
        raise runner.RunnerError("INVALID_MANIFEST")
    ids = set()
    for row in value["items"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            raise runner.RunnerError("INVALID_MANIFEST")
        ids.add(row["id"])
        if row.get("sourceSha256"):
            digest = row["sourceSha256"]
            if (not re.fullmatch(r"[a-f0-9]{64}", digest) or row.get("shard") != int(digest, 16) % SHARDS
                    or not isinstance(row.get("sourceVersion"), str) or not row["sourceVersion"]
                    or type(row.get("pageCount")) is not int or row["pageCount"] < 1):
                raise runner.RunnerError("INVALID_MANIFEST")
    if value.get("snapshotSha256") != snapshot_digest(value["items"]):
        raise runner.RunnerError("MANIFEST_SNAPSHOT_MISMATCH")
    return value


class PinnedApi:
    def __init__(self, api, items):
        self.api, self.items = api, items
        self.pins = {row["id"]: row for row in items}

    def __getattr__(self, key):
        return getattr(self.api, key)

    def inventory(self):
        return [source_item(row["id"], row["sourceVersion"], row["status"]) for row in self.items]

    def source(self, item):
        data, headers = self.api.source(item)
        normalized = {key.lower(): value for key, value in headers.items()}
        pin = self.pins[item["id"]]
        if normalized.get("x-source-sha256") != pin["sourceSha256"] or runner.sha256(data) != pin["sourceSha256"]:
            raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
        if normalized.get("x-source-version") != pin["sourceVersion"]:
            raise runner.RunnerError("SOURCE_VERSION_CHANGED")
        return data, headers


def run_shard(api, translate, manifest, manifest_sha256, shard, runtime, *, concurrency=1, limit=3, budget_seconds=7200, clock=time.monotonic, emit=None):
    if shard not in range(SHARDS) or concurrency not in (1, 2) or type(limit) is not int or limit < 0 or not 0 < budget_seconds <= 7200:
        raise runner.RunnerError("INVALID_BATCH_OPTIONS")
    current = {row["id"]: row for row in api.inventory()}
    groups = [(digest, rows) for digest, rows in source_groups(manifest["items"]).items() if int(digest, 16) % SHARDS == shard]
    pending = [(digest, rows) for digest, rows in groups if any(current.get(row["id"], {}).get("status") != "ready" for row in rows)]
    selected = {digest for digest, _ in (pending if limit == 0 else pending[:limit])}
    deadline = clock() + budget_seconds
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)

    def process(group):
        digest, aliases = group
        try:
            rows = []
            for pin in aliases:
                actual = current.get(pin["id"])
                if actual is None or actual.get("sourceVersion") != pin["sourceVersion"]:
                    raise runner.RunnerError("SOURCE_VERSION_CHANGED")
                if actual.get("error"):
                    raise runner.RunnerError("SOURCE_UNAVAILABLE")
                rows.append({**pin, "status": actual.get("status")})
            if all(row["status"] == "ready" for row in rows):
                return [{"id": row["id"], "status": "ready", "sourceSha256": digest, "existing": True} for row in rows]
            if digest not in selected or clock() >= deadline:
                return [{"id": row["id"], "status": "pending", "sourceSha256": digest, "code": "BATCH_DEFERRED"} for row in rows]
            cached = None
            translation_error = None

            def once(**kwargs):
                nonlocal cached, translation_error
                if kwargs["source_sha256"] != digest:
                    raise runner.RunnerError("SOURCE_CHANGED_FROM_PIN")
                if translation_error is not None:
                    raise translation_error
                if cached is None:
                    try:
                        result = translate(**kwargs)
                        path = Path(result["path"]).resolve()
                        if not path.is_relative_to(kwargs["output_pdf"].parent.resolve()):
                            raise runner.RunnerError("INVALID_ENGINE_ARTIFACT")
                        data = path.read_bytes()
                        runner.validate_qa(result.get("qa"), result.get("pageCount"))
                        if result["qa"]["sourcePageCount"] != aliases[0]["pageCount"]:
                            raise runner.RunnerError("SOURCE_PAGE_COUNT_CHANGED")
                        if (not data.startswith(b"%PDF-") or len(data) > runner.MAX_PDF or result.get("byteSize") != len(data)
                                or result.get("sha256") != runner.sha256(data) or runner.sha256(data) == digest):
                            raise runner.RunnerError("ARTIFACT_DIGEST_MISMATCH")
                        cached = data, {key: value for key, value in result.items() if key != "path"}
                    except Exception as error:
                        # Aliases share the same translation attempt budget as
                        # their source. Preserve the checkpoint for a later run.
                        translation_error = runner.RunnerError(runner.error_code(error),
                            error.status if isinstance(error, runner.RunnerError) else None,
                            error.api_code if isinstance(error, runner.RunnerError) else None)
                        raise translation_error from None
                data, metadata = cached
                kwargs["output_pdf"].write_bytes(data)
                return {**metadata, "path": str(kwargs["output_pdf"])}

            with tempfile.TemporaryDirectory(prefix=digest[:12] + "-", dir=runtime) as directory:
                batch = runner.run(PinnedApi(api, rows), once, Path(directory), limit=0, concurrency=1, force=False)
            outputs = {row["id"]: row for row in batch["results"]}
            return [outputs.get(row["id"], {"id": row["id"], "status": "ready", "sourceSha256": digest, "existing": True}
                                if row["status"] == "ready" else failure(row["id"], runner.RunnerError("TRANSLATION_RESULT_MISSING"))) for row in rows]
        except Exception as error:
            return [failure(row["id"], error) for row in aliases]

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(process, group) for group in groups]
        for future in as_completed(futures):
            rows = future.result()
            results.extend(rows)
            if emit:
                emit({"event": "pdf_shard_progress", "shard": shard, "completedIds": len(results),
                      "readyIds": sum(row["status"] == "ready" for row in results), "totalIds": sum(len(rows) for _, rows in groups)})
    complete = all(row["status"] == "ready" for row in results)
    return {"schema": RESULT_SCHEMA, "scope": "shard", "status": "passed" if complete else "failed", "shard": shard,
            "manifestSha256": manifest_sha256, "snapshotSha256": manifest["snapshotSha256"],
            "uniqueSources": len(groups), "selectedUniqueSources": len(selected), "results": sorted(results, key=lambda row: row["id"])}


def finalize(api, manifest, manifest_sha256, reports, *, page_counter=pdf_pages):
    expected_ids = {row["id"] for row in manifest["items"] if row.get("sourceSha256")}
    reported = []
    valid_reports = len(reports) == SHARDS and {row.get("shard") for row in reports} == set(range(SHARDS))
    for report in reports:
        valid_reports = valid_reports and report.get("schema") == RESULT_SCHEMA and report.get("manifestSha256") == manifest_sha256 and report.get("snapshotSha256") == manifest["snapshotSha256"] and report.get("status") == "passed"
        reported.extend(row.get("id") for row in report.get("results", []))
    valid_reports = valid_reports and len(reported) == len(set(reported)) and set(reported) == expected_ids
    inventory = api.inventory()
    current = {row["id"]: row for row in inventory}
    unchanged_ids = set(current) == {row["id"] for row in manifest["items"]}

    def inspect(pin):
        try:
            actual = current.get(pin["id"])
            if actual is None or actual.get("sourceVersion") != pin.get("sourceVersion"):
                raise runner.RunnerError("SOURCE_VERSION_CHANGED")
            if not pin.get("sourceSha256") or actual.get("error"):
                raise runner.RunnerError("SOURCE_AUDIT_FAILED")
            if actual.get("status") != "ready":
                return {"id": pin["id"], "status": "pending"}
            artifact = checked_artifact(api, pin["id"], pin["sourceSha256"], page_counter)
            return {"id": pin["id"], "status": "ready", "sourceSha256": pin["sourceSha256"], **artifact}
        except Exception as error:
            return failure(pin["id"], error)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(inspect, manifest["items"]))
    ready = sum(row["status"] == "ready" for row in results)
    pending_ids = {row["id"] for row in inventory if row.get("status") != "ready" or row.get("error")}
    pending_ids.update(row["id"] for row in results if row["status"] != "ready")
    pending_ids.update(set(current) - {row["id"] for row in manifest["items"]})
    pending = len(pending_ids)
    passed = bool(results) and valid_reports and unchanged_ids and manifest.get("status") == "passed" and ready == len(results) and pending == 0
    return {"schema": RESULT_SCHEMA, "scope": "global", "status": "passed" if passed else "failed",
            "manifestSha256": manifest_sha256, "snapshotSha256": manifest["snapshotSha256"], "inventoryUnchanged": unchanged_ids,
            "allShardsComplete": bool(valid_reports), "inventoryCount": len(inventory), "verifiedReadyIds": ready,
            "remainingPending": pending, "auditCounts": manifest["counts"], "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "shard", "finalize"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--shard", type=int, choices=range(SHARDS))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1, choices=(1, 2))
    parser.add_argument("--reports", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--allow-partial-audit", action="store_true")
    args = parser.parse_args()
    emit = lambda value: print(json.dumps(value), flush=True)
    wrote_manifest = False
    try:
        api = runner.GatexApi(os.environ.get("GATEX_GENERATION_CALLBACK_SECRET", ""))
        if args.mode == "audit":
            api.request_deadline = api.clock() + 1200
            report = audit(api, emit=emit)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_bytes(runner.encoded_json(report))
            wrote_manifest = True
        else:
            manifest = load_manifest(args.manifest, args.expected_manifest_sha256)
            if args.mode == "shard":
                report = run_shard(api, runner.engine("gen_rpt.gatex_pdf_localization"), manifest, args.expected_manifest_sha256,
                                   args.shard, args.runtime, concurrency=args.concurrency, limit=args.limit, emit=emit)
            elif args.audit_only:
                report = {"schema": RESULT_SCHEMA, "scope": "audit", "status": manifest["status"], "counts": manifest["counts"], "snapshotSha256": manifest["snapshotSha256"]}
            else:
                api.request_deadline = api.clock() + 1200
                reports = [json.loads(path.read_text()) for path in sorted(args.reports.glob("shard-*.json"))] if args.reports else []
                report = finalize(api, manifest, args.expected_manifest_sha256, reports)
    except Exception as error:
        report = {"schema": RESULT_SCHEMA, "scope": args.mode, "status": "failed", "code": runner.error_code(error)}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_bytes(runner.encoded_json(report))
    emit({key: value for key, value in report.items() if key not in {"items", "sources", "results"}})
    return 0 if report["status"] == "passed" or args.mode == "audit" and args.allow_partial_audit and wrote_manifest else 1


if __name__ == "__main__":
    raise SystemExit(main())
