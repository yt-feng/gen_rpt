"""
storage/structured_logger.py

Writes structured JSON log entries to local log files alongside stdout logging.

Log files are written to the `storage/logs/` directory by default, or to
`STORAGE_LOG_DIR` environment variable if set (useful in CI).

Generated log files (per Phase 8):
  - r2_upload.log         — one JSON line per file uploaded to R2
  - catalog_update.log    — one JSON line per catalog.json write
  - manifest_update.log   — one JSON line per manifest.json write
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Log directory resolution ──────────────────────────────────────────────────
def _log_dir() -> Path:
    base = os.getenv("STORAGE_LOG_DIR", "")
    if base:
        d = Path(base)
    else:
        d = Path(__file__).parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Core write function ───────────────────────────────────────────────────────
def _write_entry(log_filename: str, entry: Dict[str, Any]) -> None:
    """Append a single JSON line to the named log file."""
    entry.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
    log_path = _log_dir() / log_filename
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Could not write to %s: %s", log_path, e)


# ── Public log functions ──────────────────────────────────────────────────────

def log_r2_upload(
    *,
    report_id: str,
    operation: str,         # "report" | "review"
    files_uploaded: list,   # list of R2 keys
    elapsed_ms: float,
    status: str = "success",
    error: Optional[str] = None,
) -> None:
    """Write one entry to r2_upload.log."""
    _write_entry("r2_upload.log", {
        "event": "r2_upload",
        "report_id": report_id,
        "operation": operation,
        "files_uploaded": files_uploaded,
        "object_count": len(files_uploaded),
        "elapsed_ms": round(elapsed_ms, 2),
        "status": status,
        "error": error,
    })


def log_catalog_update(
    *,
    report_id: str,
    action: str,            # "upsert" | "delete"
    status_set: str,
    ai_score: float = 0.0,
    catalog_size: int = 0,
    elapsed_ms: float = 0.0,
    status: str = "success",
    error: Optional[str] = None,
) -> None:
    """Write one entry to catalog_update.log."""
    _write_entry("catalog_update.log", {
        "event": "catalog_update",
        "report_id": report_id,
        "action": action,
        "status_set": status_set,
        "ai_score": ai_score,
        "catalog_size": catalog_size,
        "elapsed_ms": round(elapsed_ms, 2),
        "status": status,
        "error": error,
    })


def log_manifest_update(
    *,
    report_id: str,
    action: str,            # "create" | "update" | "patch"
    files_updated: list,    # field names updated
    current_status: str,
    elapsed_ms: float = 0.0,
    status: str = "success",
    error: Optional[str] = None,
) -> None:
    """Write one entry to manifest_update.log."""
    _write_entry("manifest_update.log", {
        "event": "manifest_update",
        "report_id": report_id,
        "action": action,
        "files_updated": files_updated,
        "current_status": current_status,
        "elapsed_ms": round(elapsed_ms, 2),
        "status": status,
        "error": error,
    })
