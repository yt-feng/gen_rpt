"""
storage/upload_report.py

Upload a generated report directory to Cloudflare R2.

Usage (CLI):
    python -m storage.upload_report \\
        --report-dir reports/2026-05-29-china-private-equity-market \\
        --title "China Private Equity Market" \\
        --tags "investment,market,china"

Usage (Python):
    from storage.upload_report import upload_report
    upload_report(report_dir="reports/2026-05-29-china-private-equity-market")

Files uploaded (when present):
    report.md, report.pdf, report.html,
    report_payload.json, sources.json, research_plan.json

Target R2 structure:
    reports/{REPORT_ID}/current/report.md
    reports/{REPORT_ID}/current/report.pdf
    reports/{REPORT_ID}/current/report.html
    reports/{REPORT_ID}/manifest.json
    catalog/catalog.json (updated)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

from .r2_client import R2Client
from .catalog_manager import CatalogManager
from .manifest_manager import ManifestManager
from .schemas.catalog_schema import CatalogEntry
from .structured_logger import log_r2_upload, log_catalog_update, log_manifest_update

logger = logging.getLogger(__name__)

# Files to upload from the report directory
REPORT_FILES = {
    "report.md":            ("current/report.md",         "report_md"),
    "report.pdf":           ("current/report.pdf",        "report_pdf"),
    "report.html":          ("current/report.html",       "report_html"),
    "report_payload.json":  ("metadata/report_payload.json", "report_payload_json"),
    "sources.json":         ("metadata/sources.json",     "sources_json"),
    "research_plan.json":   ("metadata/research_plan.json", "research_plan_json"),
}


def upload_report(
    report_dir: str,
    *,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    status: str = "generated",
    r2: Optional[R2Client] = None,
    catalog: Optional[CatalogManager] = None,
    manifests: Optional[ManifestManager] = None,
) -> dict:
    """
    Upload a generated report directory to R2, then update the manifest and catalog.

    Parameters
    ----------
    report_dir : str
        Local path to the report directory (e.g. ``reports/2026-05-29-china-...``).
    title : str, optional
        Human-readable report title. Defaults to the directory name.
    tags : list[str], optional
        Tag strings (e.g. ``["investment", "china"]``).
    status : str
        Initial catalog status (default ``"generated"``).
    r2 / catalog / manifests
        Injectable dependencies (useful for testing).

    Returns
    -------
    dict
        Summary of uploaded keys and updated catalog entry.
    """
    report_path = Path(report_dir)
    if not report_path.is_dir():
        raise FileNotFoundError(f"Report directory not found: {report_dir}")

    report_id = report_path.name
    slug = report_id
    title = title or report_id
    tags = tags or []

    r2 = r2 or R2Client()
    catalog_mgr = catalog or CatalogManager(r2)
    manifest_mgr = manifests or ManifestManager(r2)

    logger.info("=== Uploading report: %s ===", report_id)

    # Ensure folder markers exist
    r2.ensure_folder_markers([
        "reports/",
        f"reports/{report_id}/",
        f"reports/{report_id}/current/",
        f"reports/{report_id}/reviews/",
        f"reports/{report_id}/metadata/",
        "catalog/",
        "assets/",
        "publish/",
    ])

    uploaded_keys: dict = {}
    file_paths: dict = {}

    # ── Upload files ────────────────────────────────────────────────────────
    upload_start = time.monotonic()
    try:
        for local_name, (r2_suffix, schema_field) in REPORT_FILES.items():
            local_file = report_path / local_name
            if not local_file.exists():
                logger.debug("Skipping missing file: %s", local_name)
                continue

            r2_key = f"reports/{report_id}/{r2_suffix}"
            r2.upload_file(str(local_file), r2_key)
            uploaded_keys[local_name] = r2_key
            file_paths[schema_field] = r2_key
            logger.info("  → Uploaded: %s", r2_key)

        upload_elapsed = (time.monotonic() - upload_start) * 1000
        logger.info("Uploaded %d report files in %.0fms", len(uploaded_keys), upload_elapsed)
        log_r2_upload(
            report_id=report_id,
            operation="report",
            files_uploaded=list(uploaded_keys.values()),
            elapsed_ms=upload_elapsed,
            status="success",
        )
    except Exception as exc:
        upload_elapsed = (time.monotonic() - upload_start) * 1000
        log_r2_upload(
            report_id=report_id,
            operation="report",
            files_uploaded=list(uploaded_keys.values()),
            elapsed_ms=upload_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    # ── Update manifest ─────────────────────────────────────────────────────
    manifest_start = time.monotonic()
    try:
        manifest = manifest_mgr.generate_or_update(
            report_id=report_id,
            title=title,
            slug=slug,
            files=file_paths,
            status=status,
            tags=tags,
        )
        manifest_elapsed = (time.monotonic() - manifest_start) * 1000
        log_manifest_update(
            report_id=report_id,
            action="create" if manifest.created_at == manifest.updated_at else "update",
            files_updated=list(file_paths.keys()),
            current_status=status,
            elapsed_ms=manifest_elapsed,
            status="success",
        )
    except Exception as exc:
        manifest_elapsed = (time.monotonic() - manifest_start) * 1000
        log_manifest_update(
            report_id=report_id,
            action="update",
            files_updated=list(file_paths.keys()),
            current_status=status,
            elapsed_ms=manifest_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    # ── Update catalog ──────────────────────────────────────────────────────
    catalog_start = time.monotonic()
    try:
        entry = CatalogEntry(
            report_id=report_id,
            title=title,
            slug=slug,
            status=status,
            review_status="pending",
            ai_score=0.0,
            tags=tags,
        )
        catalog_mgr.upsert(entry)
        catalog_elapsed = (time.monotonic() - catalog_start) * 1000
        current_catalog = catalog_mgr.get_catalog()
        log_catalog_update(
            report_id=report_id,
            action="upsert",
            status_set=status,
            ai_score=0.0,
            catalog_size=len(current_catalog),
            elapsed_ms=catalog_elapsed,
            status="success",
        )
    except Exception as exc:
        catalog_elapsed = (time.monotonic() - catalog_start) * 1000
        log_catalog_update(
            report_id=report_id,
            action="upsert",
            status_set=status,
            elapsed_ms=catalog_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    return {
        "report_id": report_id,
        "uploaded": uploaded_keys,
        "manifest_key": f"reports/{report_id}/manifest.json",
        "catalog_key": "catalog/catalog.json",
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a generated report directory to Cloudflare R2."
    )
    parser.add_argument("--report-dir", required=True, help="Path to report directory")
    parser.add_argument("--title", default="", help="Human-readable report title")
    parser.add_argument("--tags", default="", help="Comma-separated tag list")
    parser.add_argument("--status", default="generated", help="Initial catalog status")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    try:
        result = upload_report(
            args.report_dir,
            title=args.title or None,
            tags=tags,
            status=args.status,
        )
        print("Report uploaded successfully.")
        for k, v in result.items():
            print(f"  {k}: {v}")
    except Exception as exc:
        logger.error("Upload failed: %s", exc)
        sys.exit(1)
