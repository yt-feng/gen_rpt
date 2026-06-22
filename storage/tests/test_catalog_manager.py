"""
storage/tests/test_catalog_manager.py

Tests for CatalogManager — runs against the real R2 bucket.
All operations use a prefixed test key to avoid touching production catalog.json.

Run with:
    pytest storage/tests/test_catalog_manager.py -v
"""
import os
import pytest
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")),
    reason="R2 credentials not set in environment",
)

from storage.r2_client import R2Client
from storage.catalog_manager import CatalogManager
from storage.schemas.catalog_schema import CatalogEntry

# Use a separate test catalog key so production data is not touched
TEST_CATALOG_KEY = "test-catalog/catalog.json"


@pytest.fixture(scope="module")
def r2():
    return R2Client()


@pytest.fixture()
def catalog(r2, monkeypatch):
    """CatalogManager pointed at the test catalog key."""
    mgr = CatalogManager(r2)
    monkeypatch.setattr(mgr, "_r2", r2)
    # Override CATALOG_KEY used inside catalog_manager module
    import storage.catalog_manager as cm
    monkeypatch.setattr(cm, "CATALOG_KEY", TEST_CATALOG_KEY)
    # Delete any leftover test catalog
    if r2.object_exists(TEST_CATALOG_KEY):
        r2.delete_object(TEST_CATALOG_KEY)
    yield mgr
    # Cleanup
    if r2.object_exists(TEST_CATALOG_KEY):
        r2.delete_object(TEST_CATALOG_KEY)


def _make_entry(report_id: str, status: str = "generated") -> CatalogEntry:
    return CatalogEntry(
        report_id=report_id,
        title=f"Report {report_id}",
        slug=report_id,
        status=status,
        review_status="pending",
        ai_score=0.0,
        tags=["test"],
    )


def test_get_empty_catalog(catalog):
    """get_catalog() returns [] when catalog does not yet exist."""
    result = catalog.get_catalog()
    assert result == []


def test_upsert_new_entry(catalog):
    """Appends a new entry to an empty catalog."""
    entry = _make_entry("TEST-001")
    catalog.upsert(entry)
    result = catalog.get_catalog()
    assert len(result) == 1
    assert result[0]["report_id"] == "TEST-001"


def test_upsert_update_existing(catalog):
    """Updates an existing entry without creating a duplicate."""
    entry = _make_entry("TEST-002")
    catalog.upsert(entry)
    # Update status
    entry.status = "ai_reviewed"
    entry.ai_score = 88.0
    catalog.upsert(entry)
    result = catalog.get_catalog()
    matches = [e for e in result if e["report_id"] == "TEST-002"]
    assert len(matches) == 1, "Duplicate entry found"
    assert matches[0]["status"] == "ai_reviewed"
    assert matches[0]["ai_score"] == 88.0


def test_no_duplicates_multiple_upserts(catalog):
    """Multiple upserts for the same report_id never create duplicates."""
    for _ in range(3):
        entry = _make_entry("TEST-003", status="generated")
        catalog.upsert(entry)
    result = catalog.get_catalog()
    matches = [e for e in result if e["report_id"] == "TEST-003"]
    assert len(matches) == 1


def test_invalid_status_raises(catalog):
    """upsert() raises ValueError for an invalid status."""
    entry = _make_entry("TEST-004")
    entry.status = "invalid_status_xyz"
    with pytest.raises(ValueError, match="invalid_status_xyz"):
        catalog.upsert(entry)


def test_find_existing(catalog):
    """find() returns the entry dict for a known report_id."""
    entry = _make_entry("TEST-005")
    catalog.upsert(entry)
    result = catalog.find("TEST-005")
    assert result is not None
    assert result["report_id"] == "TEST-005"


def test_find_nonexistent(catalog):
    """find() returns None for a report_id that does not exist."""
    result = catalog.find("NONEXISTENT-9999")
    assert result is None


def test_delete_entry(catalog):
    """delete_entry() removes the entry and returns True."""
    entry = _make_entry("TEST-006")
    catalog.upsert(entry)
    deleted = catalog.delete_entry("TEST-006")
    assert deleted is True
    assert catalog.find("TEST-006") is None


def test_delete_nonexistent_returns_false(catalog):
    """delete_entry() returns False when report_id is not in catalog."""
    result = catalog.delete_entry("NONEXISTENT-XXXX")
    assert result is False
