"""
storage/tests/test_upload_report.py

Integration tests for upload_report() — uploads mock files to R2 and verifies
the resulting manifest and catalog entries. Cleans up all test objects after.

Run with:
    pytest storage/tests/test_upload_report.py -v
"""
import json
import os
import tempfile
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")),
    reason="R2 credentials not set in environment",
)

from storage.r2_client import R2Client
from storage.catalog_manager import CatalogManager
from storage.manifest_manager import ManifestManager
from storage.upload_report import upload_report

TEST_REPORT_ID = "TEST-UPLOAD-REPORT-001"
import storage.catalog_manager as cm_module


@pytest.fixture(scope="module")
def r2():
    return R2Client()


@pytest.fixture(scope="module", autouse=True)
def cleanup(r2):
    yield
    # Clean up all objects under test prefix
    for key in r2.list_objects(f"reports/{TEST_REPORT_ID}/"):
        r2.delete_object(key)
    for key in r2.list_objects("test-catalog/"):
        r2.delete_object(key)


@pytest.fixture(scope="module")
def mock_report_dir(tmp_path_factory):
    """Create a mock report directory with all expected files."""
    d = tmp_path_factory.mktemp("report") / TEST_REPORT_ID
    d.mkdir()
    (d / "report.md").write_text("# Test Report\n\nThis is a mock report.", encoding="utf-8")
    (d / "report.pdf").write_bytes(b"%PDF-1.4 mock")
    (d / "report.html").write_text("<html><body><h1>Test Report</h1></body></html>", encoding="utf-8")
    (d / "report_payload.json").write_text(json.dumps({"title": "Test Report"}), encoding="utf-8")
    (d / "sources.json").write_text(json.dumps([]), encoding="utf-8")
    (d / "research_plan.json").write_text(json.dumps({"plan": "mock"}), encoding="utf-8")
    return str(d)


@pytest.fixture(scope="module")
def catalog(r2, monkeypatch_module):
    """CatalogManager using a test-isolated catalog key."""
    monkeypatch_module.setattr(cm_module, "CATALOG_KEY", "test-catalog/catalog.json")
    return CatalogManager(r2)


# pytest doesn't have a built-in module-scoped monkeypatch; create one:
@pytest.fixture(scope="module")
def monkeypatch_module(request):
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_upload_report_uploads_files(r2, mock_report_dir, catalog):
    """All expected files are uploaded to R2."""
    result = upload_report(
        mock_report_dir,
        title="Test Report Upload",
        tags=["test", "investment"],
        r2=r2,
        catalog=catalog,
        manifests=ManifestManager(r2),
    )
    assert "report.md" in result["uploaded"]
    assert "report.pdf" in result["uploaded"]
    assert "report.html" in result["uploaded"]


def test_upload_report_creates_manifest(r2, mock_report_dir, catalog):
    """Manifest is created in R2 after upload."""
    manifest_key = f"reports/{TEST_REPORT_ID}/manifest.json"
    assert r2.object_exists(manifest_key), "manifest.json not found in R2"
    manifest_data = r2.get_json(manifest_key)
    assert manifest_data["report_id"] == TEST_REPORT_ID
    assert manifest_data["files"]["report_md"].endswith("report.md")


def test_upload_report_updates_catalog(r2, mock_report_dir, catalog):
    """Catalog is updated in R2 after upload."""
    entry = catalog.find(TEST_REPORT_ID)
    assert entry is not None, "Catalog entry not found"
    assert entry["status"] == "generated"
    assert "investment" in entry["tags"]
