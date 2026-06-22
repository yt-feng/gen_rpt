"""
storage/tests/test_manifest_manager.py

Tests for ManifestManager — runs against the real R2 bucket.
All test manifests use a 'test-manifests/' prefix and are cleaned up afterwards.

Run with:
    pytest storage/tests/test_manifest_manager.py -v
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")),
    reason="R2 credentials not set in environment",
)

from storage.r2_client import R2Client
from storage.manifest_manager import ManifestManager, _manifest_key

TEST_REPORT_ID = "TEST-MANIFEST-001"


@pytest.fixture(scope="module")
def r2():
    return R2Client()


@pytest.fixture(autouse=True)
def cleanup(r2):
    yield
    key = _manifest_key(TEST_REPORT_ID)
    if r2.object_exists(key):
        r2.delete_object(key)


@pytest.fixture()
def mgr(r2):
    return ManifestManager(r2)


def test_get_nonexistent_manifest(mgr):
    """get_manifest() returns None for a report with no manifest yet."""
    result = mgr.get_manifest(TEST_REPORT_ID)
    assert result is None


def test_create_manifest(mgr, r2):
    """generate_or_update() creates a new manifest and writes it to R2."""
    manifest = mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="Test Report Alpha",
        slug=TEST_REPORT_ID,
        files={
            "report_md": f"reports/{TEST_REPORT_ID}/current/report.md",
            "report_pdf": f"reports/{TEST_REPORT_ID}/current/report.pdf",
        },
        status="generated",
        tags=["test", "manifest"],
    )
    assert manifest.report_id == TEST_REPORT_ID
    assert manifest.title == "Test Report Alpha"
    assert r2.object_exists(_manifest_key(TEST_REPORT_ID))


def test_update_manifest_preserves_created_at(mgr):
    """Updating a manifest preserves the original created_at timestamp."""
    first = mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="Initial Title",
        slug=TEST_REPORT_ID,
        files={},
        status="generated",
    )
    created_at = first.created_at

    updated = mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="Updated Title",
        slug=TEST_REPORT_ID,
        files={"review_md": f"reports/{TEST_REPORT_ID}/reviews/review.md"},
        status="ai_reviewed",
        ai_score=90.0,
    )
    assert updated.created_at == created_at
    assert updated.current_status == "ai_reviewed"
    assert updated.ai_score == 90.0
    assert updated.files.review_md == f"reports/{TEST_REPORT_ID}/reviews/review.md"


def test_manifest_merges_files(mgr):
    """Updating a manifest merges files rather than overwriting all."""
    mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="T",
        slug=TEST_REPORT_ID,
        files={"report_md": "reports/T/current/report.md"},
        status="generated",
    )
    mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="T",
        slug=TEST_REPORT_ID,
        files={"review_json": "reports/T/reviews/review.json"},
        status="ai_reviewed",
    )
    manifest = mgr.get_manifest(TEST_REPORT_ID)
    assert manifest.files.report_md == "reports/T/current/report.md"
    assert manifest.files.review_json == "reports/T/reviews/review.json"


def test_patch_files(mgr):
    """patch_files() updates only the files dict without touching other fields."""
    mgr.generate_or_update(
        report_id=TEST_REPORT_ID,
        title="Patch Test",
        slug=TEST_REPORT_ID,
        files={},
        status="generated",
        ai_score=70.0,
    )
    mgr.patch_files(TEST_REPORT_ID, {"scores_json": "reports/T/reviews/scores.json"})
    manifest = mgr.get_manifest(TEST_REPORT_ID)
    assert manifest.ai_score == 70.0
    assert manifest.files.scores_json == "reports/T/reviews/scores.json"
