"""
storage/manifest_manager.py

Manages per-report manifest files in Cloudflare R2.

Manifest path: reports/{REPORT_ID}/manifest.json

Operations:
  - generate_or_update(...)  — Create or update a manifest
  - get_manifest(report_id)  — Download current manifest
  - patch_files(...)         — Update only the files section
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from .r2_client import R2Client
from .schemas.manifest_schema import Manifest, ManifestFiles

logger = logging.getLogger(__name__)


def _manifest_key(report_id: str) -> str:
    return f"reports/{report_id}/manifest.json"


class ManifestManager:
    """
    Manages per-report manifest.json objects stored in R2.
    """

    def __init__(self, r2: Optional[R2Client] = None) -> None:
        self._r2 = r2 or R2Client()

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_manifest(self, report_id: str) -> Optional[Manifest]:
        """
        Download and return the manifest for *report_id*.
        Returns None if no manifest exists yet.
        """
        key = _manifest_key(report_id)
        try:
            data = self._r2.get_json(key)
            return Manifest.from_dict(data)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    # ── Write ────────────────────────────────────────────────────────────────

    def generate_or_update(
        self,
        *,
        report_id: str,
        title: str,
        slug: str,
        files: Dict[str, str],
        status: str = "generated",
        ai_score: float = 0.0,
        tags: Optional[List[str]] = None,
        latest_version: str = "v1",
    ) -> Manifest:
        """
        Create a new manifest or update an existing one.

        *files* is a dict mapping ManifestFiles field names to R2 keys, e.g.:
            {
                "report_md":  "reports/REPORT-001/current/report.md",
                "report_pdf": "reports/REPORT-001/current/report.pdf",
            }

        Returns the resulting Manifest object (also written to R2).
        """
        existing = self.get_manifest(report_id)

        if existing is None:
            manifest = Manifest(
                report_id=report_id,
                title=title,
                slug=slug,
                latest_version=latest_version,
                current_status=status,
                ai_score=ai_score,
                tags=tags or [],
                files=ManifestFiles.from_dict(files),
            )
            logger.info("Creating new manifest for report_id='%s'", report_id)
        else:
            # Merge: update fields that are explicitly provided
            existing.title = title or existing.title
            existing.slug = slug or existing.slug
            existing.latest_version = latest_version
            existing.current_status = status
            if ai_score:
                existing.ai_score = ai_score
            if tags is not None:
                existing.tags = tags
            # Merge files — only overwrite non-empty values
            existing_files_dict = existing.files.to_dict()
            for field, value in files.items():
                if value:
                    existing_files_dict[field] = value
            existing.files = ManifestFiles.from_dict(existing_files_dict)
            existing.touch()
            manifest = existing
            logger.info("Updating existing manifest for report_id='%s'", report_id)

        self._r2.put_json(manifest.to_dict(), _manifest_key(report_id))
        logger.info("manifest.json written to R2: %s", _manifest_key(report_id))
        return manifest

    def patch_files(self, report_id: str, files: Dict[str, str]) -> Optional[Manifest]:
        """
        Update only the files section of an existing manifest.
        Returns the updated manifest, or None if no manifest existed.
        """
        manifest = self.get_manifest(report_id)
        if manifest is None:
            logger.warning("No manifest found for report_id='%s' — cannot patch files", report_id)
            return None

        existing_files = manifest.files.to_dict()
        for field, value in files.items():
            if value:
                existing_files[field] = value
        manifest.files = ManifestFiles.from_dict(existing_files)
        manifest.touch()

        self._r2.put_json(manifest.to_dict(), _manifest_key(report_id))
        logger.info("Patched manifest files for report_id='%s'", report_id)
        return manifest
