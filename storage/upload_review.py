"""
storage/upload_review.py

Upload a generated review directory to Cloudflare R2.

Usage (CLI):
    python -m storage.upload_review \\
        --review-dir review_outputs/2026-05-29-china-private-equity-market_review \\
        --report-id 2026-05-29-china-private-equity-market \\
        --ai-score 87.5

Usage (Python):
    from storage.upload_review import upload_review
    upload_review(
        review_dir="review_outputs/2026-05-29-china-private-equity-market_review",
        report_id="2026-05-29-china-private-equity-market",
    )

Files uploaded (when present):
    review.md, review.json, review.html,
    scores.json, findings.json, claims.json, review_status.json

Target R2 structure:
    reports/{REPORT_ID}/reviews/review.md
    reports/{REPORT_ID}/reviews/review.json
    reports/{REPORT_ID}/reviews/review.html
    reports/{REPORT_ID}/reviews/scores.json
    reports/{REPORT_ID}/reviews/findings.json
    reports/{REPORT_ID}/reviews/claims.json
    reports/{REPORT_ID}/manifest.json (updated)
    catalog/catalog.json (updated)
"""
from __future__ import annotations

import argparse
import json
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
from .schemas.catalog_schema import CatalogEntry, VALID_STATUSES
from .structured_logger import log_r2_upload, log_catalog_update, log_manifest_update

logger = logging.getLogger(__name__)

REVIEW_FILES = {
    "review.md":           ("reviews/review.md",           "review_md"),
    "review.json":         ("reviews/review.json",         "review_json"),
    "review.html":         ("reviews/review.html",         "review_html"),
    "scores.json":         ("reviews/scores.json",         "scores_json"),
    "findings.json":       ("reviews/findings.json",       "findings_json"),
    "claims.json":         ("reviews/claims.json",         "claims_json"),
    "review_status.json":  ("reviews/review_status.json",  None),
}


def _extract_ai_score(review_dir: Path) -> float:
    """Try to read ai_score from scores.json, fallback to 0.0."""
    scores_file = review_dir / "scores.json"
    if not scores_file.exists():
        return 0.0
    try:
        with open(scores_file, encoding="utf-8") as f:
            data = json.load(f)
        # Common patterns: {"overall": 87.5} or {"scores": {"overall": 87.5}}
        if isinstance(data, dict):
            return float(
                data.get("overall", data.get("total", data.get("ai_score", 0)))
                or 0
            )
    except Exception:
        pass
    return 0.0


def upload_review(
    review_dir: str,
    report_id: str,
    *,
    ai_score: Optional[float] = None,
    tags: Optional[List[str]] = None,
    r2: Optional[R2Client] = None,
    catalog: Optional[CatalogManager] = None,
    manifests: Optional[ManifestManager] = None,
) -> dict:
    """
    Upload a generated review directory to R2, then update the manifest and catalog.

    Parameters
    ----------
    review_dir : str
        Local path to the review output directory.
    report_id : str
        The parent report's ID (e.g. ``"2026-05-29-china-private-equity-market"``).
    ai_score : float, optional
        AI review score 0–100. If omitted, read from ``scores.json``.
    tags : list[str], optional
        Override tags (passed through to catalog update).
    r2 / catalog / manifests
        Injectable dependencies (useful for testing).

    Returns
    -------
    dict
        Summary of uploaded keys and updated catalog entry.
    """
    review_path = Path(review_dir)
    if not review_path.is_dir():
        raise FileNotFoundError(f"Review directory not found: {review_dir}")

    r2 = r2 or R2Client()
    catalog_mgr = catalog or CatalogManager(r2)
    manifest_mgr = manifests or ManifestManager(r2)

    if ai_score is None:
        ai_score = _extract_ai_score(review_path)

    logger.info("=== Uploading review for report: %s ===", report_id)

    # Ensure review folder marker exists
    r2.ensure_folder_markers([f"reports/{report_id}/reviews/"])

    uploaded_keys: dict = {}
    file_paths: dict = {}

    # ── Upload files ────────────────────────────────────────────────────────
    upload_start = time.monotonic()
    try:
        for local_name, (r2_suffix, schema_field) in REVIEW_FILES.items():
            local_file = review_path / local_name
            if not local_file.exists():
                logger.debug("Skipping missing file: %s", local_name)
                continue

            r2_key = f"reports/{report_id}/{r2_suffix}"
            r2.upload_file(str(local_file), r2_key)
            uploaded_keys[local_name] = r2_key
            if schema_field:
                file_paths[schema_field] = r2_key
            logger.info("  → Uploaded: %s", r2_key)

        upload_elapsed = (time.monotonic() - upload_start) * 1000
        logger.info("Uploaded %d review files in %.0fms", len(uploaded_keys), upload_elapsed)
        log_r2_upload(
            report_id=report_id,
            operation="review",
            files_uploaded=list(uploaded_keys.values()),
            elapsed_ms=upload_elapsed,
            status="success",
        )
    except Exception as exc:
        upload_elapsed = (time.monotonic() - upload_start) * 1000
        log_r2_upload(
            report_id=report_id,
            operation="review",
            files_uploaded=list(uploaded_keys.values()),
            elapsed_ms=upload_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    # ── Update manifest ─────────────────────────────────────────────────────
    manifest_start = time.monotonic()
    try:
        manifest_mgr.generate_or_update(
            report_id=report_id,
            title="",          # empty → existing title is preserved
            slug="",           # empty → existing slug is preserved
            files=file_paths,
            status="ai_reviewed",
            ai_score=ai_score,
            tags=tags,
        )
        manifest_elapsed = (time.monotonic() - manifest_start) * 1000
        log_manifest_update(
            report_id=report_id,
            action="update",
            files_updated=list(file_paths.keys()),
            current_status="ai_reviewed",
            elapsed_ms=manifest_elapsed,
            status="success",
        )
    except Exception as exc:
        manifest_elapsed = (time.monotonic() - manifest_start) * 1000
        log_manifest_update(
            report_id=report_id,
            action="update",
            files_updated=list(file_paths.keys()),
            current_status="ai_reviewed",
            elapsed_ms=manifest_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    # ── Update catalog entry ───────────────────────────────────────────────────
    catalog_start = time.monotonic()
    try:
        existing_entry = catalog_mgr.find(report_id)
        entry_tags = tags or (existing_entry.get("tags", []) if existing_entry else [])
        entry_title = (existing_entry or {}).get("title", report_id)

        entry = CatalogEntry(
            report_id=report_id,
            title=entry_title,
            slug=(existing_entry or {}).get("slug", report_id),
            status="ai_reviewed",
            review_status="ai_reviewed",
            ai_score=ai_score,
            tags=entry_tags,
        )
        catalog_mgr.upsert(entry)
        catalog_elapsed = (time.monotonic() - catalog_start) * 1000
        current_catalog = catalog_mgr.get_catalog()
        log_catalog_update(
            report_id=report_id,
            action="upsert",
            status_set="ai_reviewed",
            ai_score=ai_score,
            catalog_size=len(current_catalog),
            elapsed_ms=catalog_elapsed,
            status="success",
        )
    except Exception as exc:
        catalog_elapsed = (time.monotonic() - catalog_start) * 1000
        log_catalog_update(
            report_id=report_id,
            action="upsert",
            status_set="ai_reviewed",
            ai_score=ai_score,
            elapsed_ms=catalog_elapsed,
            status="error",
            error=str(exc),
        )
        raise

    return {
        "report_id": report_id,
        "uploaded": uploaded_keys,
        "ai_score": ai_score,
        "manifest_key": f"reports/{report_id}/manifest.json",
        "catalog_key": "catalog/catalog.json",
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a generated review directory to Cloudflare R2."
    )
    parser.add_argument("--review-dir", required=True, help="Path to review output directory")
    parser.add_argument("--report-id", required=True, help="Parent report ID")
    parser.add_argument("--ai-score", type=float, default=None, help="AI score 0-100")
    parser.add_argument("--tags", default="", help="Comma-separated tag list")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
    try:
        result = upload_review(
            args.review_dir,
            args.report_id,
            ai_score=args.ai_score,
            tags=tags,
        )
        print("Review uploaded successfully.")
        for k, v in result.items():
            print(f"  {k}: {v}")
    except Exception as exc:
        logger.error("Upload failed: %s", exc)
        sys.exit(1)
