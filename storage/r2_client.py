"""
storage/r2_client.py

Cloudflare R2 client — the single interface for all S3-compatible operations.

Credentials are read exclusively from environment variables:
    R2_ACCOUNT_ID        — Cloudflare account ID
    R2_ACCESS_KEY_ID     — R2 API token access key
    R2_SECRET_ACCESS_KEY — R2 API token secret key
    R2_BUCKET            — Default bucket name

No credentials are ever hardcoded.
"""
from __future__ import annotations

import json
import logging
import os
from io import BytesIO
from typing import Any, Dict, Iterator, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Environment ──────────────────────────────────────────────────────────────
_REQUIRED_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def _get_env(key: str) -> str:
    val = os.getenv(key, "")
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "Check your .env file or CI/CD secrets."
        )
    return val


# ── Client factory ───────────────────────────────────────────────────────────

def _build_client() -> Any:
    """Build and return an authenticated boto3 S3 client for Cloudflare R2."""
    account_id = _get_env("R2_ACCOUNT_ID")
    access_key = _get_env("R2_ACCESS_KEY_ID")
    secret_key = _get_env("R2_SECRET_ACCESS_KEY")
    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


# ── R2Client ─────────────────────────────────────────────────────────────────

class R2Client:
    """
    High-level wrapper around the Cloudflare R2 S3-compatible API.

    All methods accept an optional `bucket` parameter; when omitted the value
    of the `R2_BUCKET` environment variable is used.
    """

    def __init__(self) -> None:
        self._s3 = _build_client()
        self._default_bucket = os.getenv("R2_BUCKET", "")

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _bucket(self, bucket: Optional[str]) -> str:
        b = bucket or self._default_bucket
        if not b:
            raise EnvironmentError(
                "No bucket specified and R2_BUCKET env var is not set."
            )
        return b

    # ── Core operations ──────────────────────────────────────────────────────

    def upload_file(
        self,
        local_path: str,
        r2_key: str,
        *,
        bucket: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a local file to R2.

        Returns the R2 key of the uploaded object.
        """
        b = self._bucket(bucket)
        extra: Dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        elif r2_key.endswith(".json"):
            extra["ContentType"] = "application/json"
        elif r2_key.endswith(".md"):
            extra["ContentType"] = "text/markdown"
        elif r2_key.endswith(".html"):
            extra["ContentType"] = "text/html"
        elif r2_key.endswith(".pdf"):
            extra["ContentType"] = "application/pdf"

        logger.debug("Uploading %s → s3://%s/%s", local_path, b, r2_key)
        self._s3.upload_file(local_path, b, r2_key, ExtraArgs=extra or None)
        logger.info("Uploaded: %s", r2_key)
        return r2_key

    def upload_bytes(
        self,
        data: bytes,
        r2_key: str,
        *,
        bucket: Optional[str] = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload raw bytes to R2. Returns the R2 key."""
        b = self._bucket(bucket)
        logger.debug("Uploading bytes → s3://%s/%s", b, r2_key)
        self._s3.put_object(Bucket=b, Key=r2_key, Body=data, ContentType=content_type)
        logger.info("Uploaded bytes: %s", r2_key)
        return r2_key

    def download_bytes(
        self, r2_key: str, *, bucket: Optional[str] = None
    ) -> bytes:
        """Download an object from R2 and return its raw bytes."""
        b = self._bucket(bucket)
        response = self._s3.get_object(Bucket=b, Key=r2_key)
        return response["Body"].read()

    def list_objects(
        self, prefix: str = "", *, bucket: Optional[str] = None
    ) -> List[str]:
        """Return a list of all R2 keys under the given prefix."""
        b = self._bucket(bucket)
        keys: List[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=b, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def object_exists(
        self, r2_key: str, *, bucket: Optional[str] = None
    ) -> bool:
        """Return True if an object with the given key exists in R2."""
        b = self._bucket(bucket)
        try:
            self._s3.head_object(Bucket=b, Key=r2_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    def put_json(
        self,
        data: Any,
        r2_key: str,
        *,
        bucket: Optional[str] = None,
        indent: int = 2,
    ) -> str:
        """Serialize *data* to JSON and upload to R2. Returns the R2 key."""
        raw = json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8")
        return self.upload_bytes(raw, r2_key, bucket=bucket, content_type="application/json")

    def get_json(
        self, r2_key: str, *, bucket: Optional[str] = None
    ) -> Any:
        """Download a JSON object from R2 and return the parsed Python value."""
        raw = self.download_bytes(r2_key, bucket=bucket)
        return json.loads(raw.decode("utf-8"))

    def delete_object(
        self, r2_key: str, *, bucket: Optional[str] = None
    ) -> None:
        """Delete an object from R2."""
        b = self._bucket(bucket)
        self._s3.delete_object(Bucket=b, Key=r2_key)
        logger.info("Deleted: %s", r2_key)

    # ── Convenience ──────────────────────────────────────────────────────────

    def ensure_folder_markers(
        self, prefixes: List[str], *, bucket: Optional[str] = None
    ) -> None:
        """
        Create empty 0-byte objects to act as folder markers in R2.
        Safe to call on already-existing prefixes.
        """
        for prefix in prefixes:
            key = prefix if prefix.endswith("/") else prefix + "/"
            if not self.object_exists(key, bucket=bucket):
                self.upload_bytes(b"", key, bucket=bucket, content_type="application/x-directory")
                logger.info("Created folder marker: %s", key)

    def verify_bucket_access(self, *, bucket: Optional[str] = None) -> bool:
        """
        Return True if the configured credentials have read access to the bucket.
        Does NOT require global ListBuckets permission (scoped tokens are supported).
        """
        b = self._bucket(bucket)
        try:
            self._s3.list_objects_v2(Bucket=b, MaxKeys=1)
            return True
        except ClientError as e:
            logger.error("Bucket access check failed for '%s': %s", b, e)
            return False
