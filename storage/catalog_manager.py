"""
storage/catalog_manager.py

Manages catalog/catalog.json in Cloudflare R2.

The catalog is the primary entry point for the frontend. It is a JSON array
of CatalogEntry objects, one per report.

Operations:
  - upsert(entry)    — Append new or update existing entry by report_id
  - get_catalog()    — Download and return the full catalog as a list of dicts
  - delete_entry(id) — Remove an entry by report_id
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from .r2_client import R2Client
from .schemas.catalog_schema import CatalogEntry, VALID_STATUSES

logger = logging.getLogger(__name__)

CATALOG_KEY = "catalog/catalog.json"


class CatalogManager:
    """
    Manages the central catalog/catalog.json stored in R2.

    All reads and writes go through the R2Client — no local file system access.
    """

    def __init__(self, r2: Optional[R2Client] = None) -> None:
        self._r2 = r2 or R2Client()

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_catalog(self) -> List[Dict[str, Any]]:
        """
        Download and return the current catalog as a list of raw dicts.
        Returns an empty list if the catalog does not yet exist in R2.
        """
        try:
            data = self._r2.get_json(CATALOG_KEY)
            if not isinstance(data, list):
                logger.warning("catalog.json is not a list — resetting to []")
                return []
            return data
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                logger.info("catalog.json does not exist yet — will be created on first upsert.")
                return []
            raise

    # ── Write ────────────────────────────────────────────────────────────────

    def upsert(self, entry: CatalogEntry) -> None:
        """
        Insert or update a CatalogEntry in the catalog.

        If a record with the same report_id already exists it is replaced in
        place (preserving its position in the list). Otherwise the entry is
        appended. The catalog is then written back to R2.

        Raises ValueError if the entry's status is not valid.
        """
        if entry.status not in VALID_STATUSES:
            raise ValueError(
                f"Cannot upsert entry with invalid status '{entry.status}'. "
                f"Valid statuses: {sorted(VALID_STATUSES)}"
            )

        catalog = self.get_catalog()
        entry.touch()

        # Check for existing entry
        updated = False
        for i, existing in enumerate(catalog):
            if existing.get("report_id") == entry.report_id:
                # Preserve original created_at
                entry.created_at = existing.get("created_at", entry.created_at)
                catalog[i] = entry.to_dict()
                updated = True
                logger.info("Updated catalog entry for report_id='%s'", entry.report_id)
                break

        if not updated:
            catalog.append(entry.to_dict())
            logger.info("Appended new catalog entry for report_id='%s'", entry.report_id)

        self._r2.put_json(catalog, CATALOG_KEY)
        logger.info("catalog.json written to R2 (%d entries)", len(catalog))

    def delete_entry(self, report_id: str) -> bool:
        """
        Remove the entry with the given report_id from the catalog.
        Returns True if an entry was removed, False if it wasn't found.
        """
        catalog = self.get_catalog()
        original_len = len(catalog)
        catalog = [e for e in catalog if e.get("report_id") != report_id]

        if len(catalog) == original_len:
            logger.warning("No catalog entry found for report_id='%s'", report_id)
            return False

        self._r2.put_json(catalog, CATALOG_KEY)
        logger.info("Deleted catalog entry for report_id='%s'", report_id)
        return True

    # ── Query helpers ────────────────────────────────────────────────────────

    def find(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Return the catalog entry for *report_id*, or None if not found."""
        for entry in self.get_catalog():
            if entry.get("report_id") == report_id:
                return entry
        return None
