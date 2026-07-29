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
        from app.database.session import async_session_maker
        from app.models.document import Document
        from sqlalchemy import select
        import re

        if not storage_provider.is_configured:
            logger.warning("[startup_hydration] R2 not configured — skipping hydration.")
            return

        def _list_report_folders():
            """Return list of (bare_slug, folder_name, prefix) tuples for every report folder in R2."""
            folders = []
            client = storage_provider.s3_client
            bucket = storage_provider.bucket

            # Scan both Path 1 (reports/) and Path 2 (reports_web/)
            for prefix_path in ['reports/', 'reports_web/']:
                try:
                    res = client.list_objects_v2(Bucket=bucket, Prefix=prefix_path, Delimiter='/')
                    for p in res.get('CommonPrefixes', []):
                        prefix = p['Prefix']           # e.g. "reports/2026-07-03-china-.../"
                        folder_name = prefix.rstrip('/').split('/')[-1]
                        if folder_name:
                            # Strip leading date "YYYY-MM-DD-" to recover the bare slug
                            bare_slug = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', folder_name)
                            folders.append((bare_slug, folder_name, prefix))
                except Exception as e:
                    logger.warning(f"[startup_hydration] Could not list {prefix_path} in R2: {e}")

            return folders

        folders = await to_thread.run_sync(_list_report_folders)
        logger.info(f"[startup_hydration] Found {len(folders)} report folder(s) in R2.")

        loaded = 0
        async with async_session_maker() as session:
            for bare_slug, folder_name, prefix in folders:
                # Skip if already cached under bare slug
                if bare_slug in MOCK_REPORTS:
                    continue

                try:
                    payload = await _load_report_payload_from_r2(bare_slug, bare_slug)
                    if not payload:
                        logger.debug(f"[startup_hydration] No payload found for slug={bare_slug}, skipping.")
                        continue

                    title = (
                        payload.get("topic")
                        or payload.get("title")
                        or bare_slug.replace('-', ' ').title()
                    )
                    entry = _build_mock_report_entry(bare_slug, title, bare_slug, payload)
                    
                    # 3. Document UUID key (if matches DB)
                    from app.models.identity import User
                    stmt = select(Document).where(Document.slug == bare_slug)
                    res = await session.execute(stmt)
                    doc = res.scalar_one_or_none()
                    
                    if doc:
                        MOCK_REPORTS[str(doc.id)] = entry
                        if doc.owner_id:
                            owner_res = await session.execute(select(User).where(User.id == doc.owner_id))
                            owner = owner_res.scalar_one_or_none()
                            if owner:
                                entry["assignedTo"] = {
                                    "id": str(owner.id),
                                    "full_name": owner.full_name,
                                    "email": owner.email
                                }
                            else:
                                entry["assignedTo"] = None
                        else:
                            entry["assignedTo"] = None
                    else:
                        entry["assignedTo"] = None

                    # Store under canonical bare slug key (what frontend uses)
                    MOCK_REPORTS[bare_slug] = entry
                    loaded += 1
                    logger.info(f"[startup_hydration] Loaded report: {bare_slug}")
                except Exception as e:
                    logger.warning(f"[startup_hydration] Failed to load slug={bare_slug}: {e}")

        logger.info(f"[startup_hydration] Hydration complete. {loaded} new report(s) injected into MOCK_REPORTS.")

    except Exception as e:
        logger.error(f"[startup_hydration] Unexpected error during hydration: {e}")
