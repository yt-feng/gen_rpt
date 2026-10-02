"""
startup_hydration.py
--------------------
Synchronizes MOCK_REPORTS with the deployed production environment and Cloudflare R2
at backend startup so local and remote are 100% in sync with identical report lists,
titles, scores, and status counts.

Called once from lifespan() in main.py after R2 is confirmed healthy.
"""
import json
import asyncio
import urllib.request
import ssl
from anyio import to_thread
from app.logging.logger import logger


async def hydrate_mock_reports_from_r2():
    """
    Synchronizes MOCK_REPORTS with production reports.
    First attempts direct sync with the production API (https://rpt-api.gatex.ae/api/v1/reports/)
    for instant 100% parity with the deployed environment.
    Falls back to R2 storage + database if offline.
    """
    from app.api.v1.endpoints.reports import MOCK_REPORTS

    # 1. Primary Sync: Production API parity sync
    try:
        def _fetch_remote_reports():
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(
                "https://rpt-api.gatex.ae/api/v1/reports/",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("data", [])

        remote_reports = await to_thread.run_sync(_fetch_remote_reports)
        if remote_reports:
            loaded = 0
            for r in remote_reports:
                rid = r.get("id")
                slug = r.get("slug") or rid
                if not rid:
                    continue
                MOCK_REPORTS[rid] = r
                if slug:
                    MOCK_REPORTS[slug] = r
                loaded += 1
            logger.info(f"[startup_hydration] Production parity sync complete: loaded {loaded} reports from rpt-api.gatex.ae.")
            return
    except Exception as e:
        logger.warning(f"[startup_hydration] Remote sync skipped or failed ({e}), falling back to local R2/DB scan.")

    # 2. Fallback: Local R2 + Database Scan
    try:
        from app.storage.provider import storage_provider
        from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
        from app.database.session import async_session_maker
        from app.models.document import Document
        from sqlalchemy import select
        import re

        if not storage_provider.is_configured:
            logger.warning("[startup_hydration] R2 not configured — skipping hydration.")
            return

        def _list_report_folders():
            folders = []
            client = storage_provider.s3_client
            bucket = storage_provider.bucket
            for prefix_path in ['reports/', 'reports_web/']:
                try:
                    res = client.list_objects_v2(Bucket=bucket, Prefix=prefix_path, Delimiter='/')
                    for p in res.get('CommonPrefixes', []):
                        prefix = p['Prefix']
                        folder_name = prefix.rstrip('/').split('/')[-1]
                        if folder_name:
                            bare_slug = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', folder_name)
                            folders.append((bare_slug, folder_name, prefix))
                except Exception as ex:
                    logger.warning(f"[startup_hydration] Could not list {prefix_path} in R2: {ex}")
            return folders

        folders = await to_thread.run_sync(_list_report_folders)
        seen = set()
        unique_folders = []
        for b, f, p in folders:
            if b not in seen:
                seen.add(b)
                unique_folders.append((b, f, p))

        loaded = 0
        async with async_session_maker() as session:
            for bare_slug, folder_name, prefix in unique_folders:
                if bare_slug in MOCK_REPORTS:
                    continue
                try:
                    payload = await _load_report_payload_from_r2(bare_slug, bare_slug)
                    if not payload:
                        continue
                    stmt = select(Document).where(Document.slug == bare_slug)
                    res = await session.execute(stmt)
                    doc = res.scalar_one_or_none()
                    title = (doc.title if doc else None) or payload.get("topic") or payload.get("title") or bare_slug.replace('-', ' ').title()
                    entry = _build_mock_report_entry(bare_slug, title, bare_slug, payload)
                    if doc:
                        MOCK_REPORTS[str(doc.id)] = entry
                    MOCK_REPORTS[bare_slug] = entry
                    loaded += 1
                except Exception as ex:
                    logger.warning(f"[startup_hydration] Failed to load slug={bare_slug}: {ex}")

        logger.info(f"[startup_hydration] Local hydration complete. {loaded} reports active.")
    except Exception as e:
        logger.error(f"[startup_hydration] Unexpected error during hydration: {e}")
