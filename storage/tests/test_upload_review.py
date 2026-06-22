"""
storage/tests/test_upload_review.py

Integration tests for upload_review() — uploads mock review files to R2 and verifies
the resulting manifest and catalog updates. Cleans up all test objects after.

Run with:
    pytest storage/tests/test_upload_review.py -v
"""
import json
import os
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
from storage.upload_review import upload_review
import storage.catalog_manager as cm_module

TEST_REPORT_ID = "TEST-UPLOAD-REVIEW-001"


@pytest.fixture(scope="module")
def r2():
    return R2Client()


@pytest.fixture(scope="module")
def monkeypatch_module(request):
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def cleanup(r2):
    yield
    for key in r2.list_objects(f"reports/{TEST_REPORT_ID}/"):
        r2.delete_object(key)
    for key in r2.list_objects("test-catalog-review/"):
        r2.delete_object(key)


@pytest.fixture(scope="module")
def catalog(r2, monkeypatch_module):
    monkeypatch_module.setattr(cm_module, "CATALOG_KEY", "test-catalog-review/catalog.json")
    return CatalogManager(r2)


@pytest.fixture(scope="module")
def mock_review_dir(tmp_path_factory):
    """Create a mock review output directory with all expected files."""
    d = tmp_path_factory.mktemp("review") / f"{TEST_REPORT_ID}_review"
    d.mkdir()
    (d / "review.md").write_text("# AI Review\n\nMock review.", encoding="utf-8")
    (d / "review.json").write_text(json.dumps({"status": "ai_reviewed"}), encoding="utf-8")
    (d / "review.html").write_text("<html><body>Review</body></html>", encoding="utf-8")
    (d / "scores.json").write_text(json.dumps({"overall": 82.5}), encoding="utf-8")
    (d / "findings.json").write_text(json.dumps([{"finding": "mock"}]), encoding="utf-8")
    (d / "claims.json").write_text(json.dumps([{"claim": "mock"}]), encoding="utf-8")
    return str(d)


def test_upload_review_uploads_files(r2, mock_review_dir, catalog):
    """All expected review files are uploaded to R2."""
    result = upload_review(
        mock_review_dir,
        TEST_REPORT_ID,
        r2=r2,
        catalog=catalog,
        manifests=ManifestManager(r2),
    )
    assert "review.md" in result["uploaded"]
    assert "review.json" in result["uploaded"]
    assert "scores.json" in result["uploaded"]
    assert "findings.json" in result["uploaded"]


def test_upload_review_extracts_ai_score(r2, mock_review_dir, catalog):
    """AI score is correctly extracted from scores.json."""
    result = upload_review(
        mock_review_dir,
        TEST_REPORT_ID,
        r2=r2,
        catalog=catalog,
        manifests=ManifestManager(r2),
    )
    assert result["ai_score"] == 82.5


def test_upload_review_updates_catalog(r2, mock_review_dir, catalog):
    """Catalog status is updated to ai_reviewed after review upload."""
    entry = catalog.find(TEST_REPORT_ID)
    assert entry is not None
    assert entry["status"] == "ai_reviewed"
    assert entry["ai_score"] == 82.5


def test_upload_review_updates_manifest(r2, mock_review_dir, catalog):
    """Manifest files section includes review file paths."""
    manifest_data = r2.get_json(f"reports/{TEST_REPORT_ID}/manifest.json")
    assert "review_md" in manifest_data["files"]
    assert "scores_json" in manifest_data["files"]
