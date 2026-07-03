"""
startup_hydration.py
--------------------
Scans Cloudflare R2 at backend startup and pre-populates MOCK_REPORTS with
every report that has already been uploaded (both the standard single-report
path and the bulk-generated path).

This is the fix for reports disappearing after a backend restart: since
MOCK_REPORTS is in-memory, and bulk-generated reports have no DB rows, this
scan is the only reliable way to restore them.

Called once from lifespan() in main.py after R2 is confirmed healthy.
"""
import json
import asyncio
from anyio import to_thread
from app.logging.logger import logger


async def hydrate_mock_reports_from_r2():
    """
    List all report folders in R2 and inject their payloads into MOCK_REPORTS.
    Runs once at startup; safe to call repeatedly (idempotent — only adds,
    never removes existing entries).
    """
    try:
        from app.storage.provider import storage_provider
        from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
        from app.api.v1.endpoints.reports import MOCK_REPORTS

        if not storage_provider.is_configured:
            logger.warning("[startup_hydration] R2 not configured — skipping hydration.")
            return

        def _list_report_folders():
            """Return list of (slug, prefix) tuples for every report folder in R2."""
            folders = []
            client = storage_provider.s3_client
            bucket = storage_provider.bucket

            # Path 1: reports/{slug}/  (upload_report.py canonical path)
            try:
                res = client.list_objects_v2(Bucket=bucket, Prefix='reports/', Delimiter='/')
                for p in res.get('CommonPrefixes', []):
                    prefix = p['Prefix']           # e.g. "reports/china-rmb-.../
                    slug = prefix.rstrip('/').split('/')[-1]
                    if slug:
                        folders.append((slug, prefix, 'reports'))
            except Exception as e:
                logger.warning(f"[startup_hydration] Could not list reports/ in R2: {e}")

            # Path 2: reports_web/{date-slug}/  (direct upload or legacy path)
            try:
                res2 = client.list_objects_v2(Bucket=bucket, Prefix='reports_web/', Delimiter='/')
                for p in res2.get('CommonPrefixes', []):
                    prefix = p['Prefix']           # e.g. "reports_web/2026-07-03-china-.../"
                    folder_name = prefix.rstrip('/').split('/')[-1]
                    # Strip leading date "YYYY-MM-DD-" to recover the slug
                    import re
                    slug = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', folder_name)
                    if slug:
                        folders.append((slug, prefix, 'reports_web'))
            except Exception as e:
                logger.warning(f"[startup_hydration] Could not list reports_web/ in R2: {e}")

            return folders

        folders = await to_thread.run_sync(_list_report_folders)
        logger.info(f"[startup_hydration] Found {len(folders)} report folder(s) in R2.")

        loaded = 0
        for slug, prefix, bucket_path in folders:
            # Skip if already in MOCK_REPORTS (from DB reconciliation or a previous run)
            if slug in MOCK_REPORTS:
                continue

            try:
                payload = await _load_report_payload_from_r2(slug, slug)
                if not payload:
                    logger.debug(f"[startup_hydration] No payload found for slug={slug}, skipping.")
                    continue

                title = (
                    payload.get("topic")
                    or payload.get("title")
                    or slug.replace('-', ' ').title()
                )
                entry = _build_mock_report_entry(slug, title, slug, payload)
                MOCK_REPORTS[slug] = entry
                loaded += 1
                logger.info(f"[startup_hydration] Loaded report: {slug}")
            except Exception as e:
                logger.warning(f"[startup_hydration] Failed to load slug={slug}: {e}")

        logger.info(f"[startup_hydration] Hydration complete. {loaded} new report(s) injected into MOCK_REPORTS.")

    except Exception as e:
        logger.error(f"[startup_hydration] Unexpected error during hydration: {e}")
