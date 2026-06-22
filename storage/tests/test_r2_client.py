"""
storage/tests/test_r2_client.py

Tests for R2Client — runs against the real Cloudflare R2 bucket.
All test objects are created under a 'test-r2client/' prefix and cleaned up afterwards.

Run with:
    pytest storage/tests/test_r2_client.py -v
"""
import json
import os
import tempfile
import pytest
from dotenv import load_dotenv

load_dotenv()

# Skip entire module if credentials are not set
pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")),
    reason="R2 credentials not set in environment",
)

from storage.r2_client import R2Client

PREFIX = "test-r2client/"


@pytest.fixture(scope="module")
def r2():
    return R2Client()


@pytest.fixture(autouse=True, scope="module")
def cleanup(r2):
    """Delete all test objects after the module finishes."""
    yield
    keys = r2.list_objects(PREFIX)
    for key in keys:
        r2.delete_object(key)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_bucket_access(r2):
    """Phase 1: Verify bucket access with scoped credentials."""
    assert r2.verify_bucket_access(), "Bucket access check failed"


def test_upload_bytes_and_download(r2):
    """Phase 5: Upload raw bytes and download back for verification."""
    key = PREFIX + "test-bytes.txt"
    payload = b"hello cloudflare r2"
    r2.upload_bytes(payload, key, content_type="text/plain")
    result = r2.download_bytes(key)
    assert result == payload, f"Content mismatch: expected {payload!r}, got {result!r}"


def test_upload_file(r2, tmp_path):
    """Phase 5: Upload a local file."""
    local = tmp_path / "test_file.md"
    local.write_text("# Test\nContent here.", encoding="utf-8")
    key = PREFIX + "test-file.md"
    r2.upload_file(str(local), key)
    result = r2.download_bytes(key).decode("utf-8")
    assert "# Test" in result


def test_put_and_get_json(r2):
    """Phase 5: Put and get JSON objects."""
    key = PREFIX + "test-json.json"
    data = {"status": "ok", "score": 95, "tags": ["test", "r2"]}
    r2.put_json(data, key)
    result = r2.get_json(key)
    assert result == data


def test_object_exists(r2):
    """Phase 5: Verify object_exists returns correct boolean."""
    key = PREFIX + "test-exists.txt"
    assert not r2.object_exists(PREFIX + "nonexistent-key-xyz.txt")
    r2.upload_bytes(b"exists", key)
    assert r2.object_exists(key)


def test_list_objects(r2):
    """Phase 5: List objects under a prefix."""
    keys_to_create = [PREFIX + f"list-test-{i}.txt" for i in range(3)]
    for key in keys_to_create:
        r2.upload_bytes(b"x", key)
    listed = r2.list_objects(PREFIX + "list-test-")
    for key in keys_to_create:
        assert key in listed, f"Expected {key} in listing"


def test_update_object(r2):
    """Phase 5: Update (overwrite) an object."""
    key = PREFIX + "test-update.json"
    r2.put_json({"version": 1}, key)
    r2.put_json({"version": 2}, key)
    result = r2.get_json(key)
    assert result["version"] == 2


def test_delete_object(r2):
    """Phase 5: Delete an object."""
    key = PREFIX + "test-delete.txt"
    r2.upload_bytes(b"to be deleted", key)
    assert r2.object_exists(key)
    r2.delete_object(key)
    assert not r2.object_exists(key)


def test_ensure_folder_markers(r2):
    """Phase 3: Create folder markers."""
    prefixes = [PREFIX + "folder-a/", PREFIX + "folder-b/"]
    r2.ensure_folder_markers(prefixes)
    for prefix in prefixes:
        assert r2.object_exists(prefix), f"Folder marker not created: {prefix}"
