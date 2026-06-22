"""
storage/tests/test_end_to_end.py

Phase 12 & 13: Full end-to-end simulation and PASS/FAIL validation report.

Simulates the complete pipeline:
    Generate mock report → Upload report → Generate mock review → Upload review
    → Verify catalog → Verify manifest → Verify all files in R2

Can be run directly (produces a summary table) or as a pytest module.

Direct run:
    python storage/tests/test_end_to_end.py

Pytest run:
    pytest storage/tests/test_end_to_end.py -v
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("e2e")

# ── Test report/review IDs ────────────────────────────────────────────────────
E2E_REPORT_ID = "E2E-TEST-001"
E2E_CATALOG_KEY = "test-e2e/catalog.json"
E2E_FOLDER_PREFIX = f"reports/{E2E_REPORT_ID}/"

# ── Result tracker ────────────────────────────────────────────────────────────
results: Dict[str, Tuple[str, str]] = {}  # name -> (PASS|FAIL, note)


def record(name: str, passed: bool, note: str = "") -> None:
    results[name] = ("PASS" if passed else "FAIL", note)
    icon = "✓" if passed else "✗"
    logger.info("%s  %-40s  %s", icon, name, note)


# ── Mock data builders ────────────────────────────────────────────────────────

def _make_mock_report_dir(base: Path) -> Path:
    d = base / E2E_REPORT_ID
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("# E2E Test Report\n\nContent.", encoding="utf-8")
    (d / "report.pdf").write_bytes(b"%PDF-1.4 e2e mock")
    (d / "report.html").write_text("<html><body>E2E Report</body></html>", encoding="utf-8")
    (d / "report_payload.json").write_text(json.dumps({"title": "E2E Test Report"}), encoding="utf-8")
    (d / "sources.json").write_text("[]", encoding="utf-8")
    (d / "research_plan.json").write_text(json.dumps({"plan": "e2e"}), encoding="utf-8")
    return d


def _make_mock_review_dir(base: Path) -> Path:
    d = base / f"{E2E_REPORT_ID}_review"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review.md").write_text("# E2E Review\n\nMock.", encoding="utf-8")
    (d / "review.json").write_text(json.dumps({"status": "ai_reviewed"}), encoding="utf-8")
    (d / "review.html").write_text("<html>E2E Review</html>", encoding="utf-8")
    (d / "scores.json").write_text(json.dumps({"overall": 91.0}), encoding="utf-8")
    (d / "findings.json").write_text(json.dumps([{"finding": "e2e"}]), encoding="utf-8")
    (d / "claims.json").write_text(json.dumps([{"claim": "e2e"}]), encoding="utf-8")
    return d


# ── Main simulation ───────────────────────────────────────────────────────────

def run_e2e() -> bool:
    """Run the full end-to-end simulation. Returns True if all checks pass."""
    from storage.r2_client import R2Client
    from storage.catalog_manager import CatalogManager
    from storage.manifest_manager import ManifestManager
    from storage.upload_report import upload_report
    from storage.upload_review import upload_review
    import storage.catalog_manager as cm_module

    # Patch catalog key to avoid touching production catalog
    original_key = cm_module.CATALOG_KEY
    cm_module.CATALOG_KEY = E2E_CATALOG_KEY

    r2 = R2Client()
    catalog = CatalogManager(r2)
    manifests = ManifestManager(r2)

    # ── Phase 1: Authentication ───────────────────────────────────────────────
    try:
        ok = r2.verify_bucket_access()
        record("Authentication", ok, "Bucket access verified")
    except Exception as e:
        record("Authentication", False, str(e))
        cm_module.CATALOG_KEY = original_key
        return False

    # ── Phase 2: Bucket Access ────────────────────────────────────────────────
    record("Bucket Access", ok, os.getenv("R2_BUCKET", ""))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        report_dir = _make_mock_report_dir(tmp_path)
        review_dir = _make_mock_review_dir(tmp_path)

        # ── Phase 3: Upload Report ────────────────────────────────────────────
        try:
            result = upload_report(
                str(report_dir),
                title="E2E Test Report",
                tags=["test", "e2e", "technology"],
                r2=r2, catalog=catalog, manifests=manifests,
            )
            record("Report Upload", len(result["uploaded"]) > 0,
                   f"{len(result['uploaded'])} files uploaded")
        except Exception as e:
            record("Report Upload", False, str(e))

        # ── Phase 4: Upload Review ────────────────────────────────────────────
        try:
            result = upload_review(
                str(review_dir),
                E2E_REPORT_ID,
                r2=r2, catalog=catalog, manifests=manifests,
            )
            record("Review Upload", len(result["uploaded"]) > 0,
                   f"{len(result['uploaded'])} files, score={result['ai_score']}")
        except Exception as e:
            record("Review Upload", False, str(e))

        # ── Phase 5: Verify R2 objects ────────────────────────────────────────
        checks = {
            "report.md":    f"reports/{E2E_REPORT_ID}/current/report.md",
            "report.pdf":   f"reports/{E2E_REPORT_ID}/current/report.pdf",
            "report.html":  f"reports/{E2E_REPORT_ID}/current/report.html",
            "review.md":    f"reports/{E2E_REPORT_ID}/reviews/review.md",
            "review.json":  f"reports/{E2E_REPORT_ID}/reviews/review.json",
            "scores.json":  f"reports/{E2E_REPORT_ID}/reviews/scores.json",
        }
        all_files_ok = True
        for name, key in checks.items():
            exists = r2.object_exists(key)
            if not exists:
                all_files_ok = False
            logger.info("  [%s] %s", "✓" if exists else "✗", key)
        record("Report Files in R2", all_files_ok, f"{len(checks)} objects verified")

        # ── Phase 6: Verify Download ──────────────────────────────────────────
        try:
            content = r2.download_bytes(f"reports/{E2E_REPORT_ID}/current/report.md").decode()
            record("Download", "E2E Test Report" in content, "Content verified")
        except Exception as e:
            record("Download", False, str(e))

        # ── Phase 7: Verify Catalog ───────────────────────────────────────────
        try:
            entry = catalog.find(E2E_REPORT_ID)
            record("Catalog Creation", entry is not None, f"status={entry.get('status') if entry else 'N/A'}")
        except Exception as e:
            record("Catalog Creation", False, str(e))

        # ── Phase 8: Verify Manifest ──────────────────────────────────────────
        try:
            manifest = manifests.get_manifest(E2E_REPORT_ID)
            ok = manifest is not None and manifest.files.report_md != ""
            record("Manifest Creation", ok,
                   f"files.report_md={'set' if ok else 'empty'}")
        except Exception as e:
            record("Manifest Creation", False, str(e))

        # ── Phase 9: GitHub Actions readiness ────────────────────────────────
        has_secrets = all(os.getenv(k) for k in (
            "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"
        ))
        record("GitHub Actions Compatibility", has_secrets,
               "All 4 R2 secrets present" if has_secrets else "R2_BUCKET missing")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    for key in r2.list_objects(E2E_FOLDER_PREFIX):
        r2.delete_object(key)
    for key in r2.list_objects("test-e2e/"):
        r2.delete_object(key)

    cm_module.CATALOG_KEY = original_key

    # ── Print report ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  R2 STORAGE VALIDATION REPORT")
    print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("=" * 60)
    all_pass = True
    for name, (status, note) in results.items():
        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon}  {status:4s}  {name:<40s}  {note}")
        if status == "FAIL":
            all_pass = False
    print("=" * 60)
    print("  OVERALL:", "PASS" if all_pass else "FAIL")
    print("=" * 60 + "\n")
    return all_pass


# ── pytest wrappers ───────────────────────────────────────────────────────────

import pytest

pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")),
    reason="R2 credentials not set in environment",
)


def test_end_to_end():
    """Run full end-to-end simulation and assert all checks pass."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    passed = run_e2e()
    assert passed, "One or more end-to-end checks failed. See output above."


# ── Direct execution ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ok = run_e2e()
    sys.exit(0 if ok else 1)
