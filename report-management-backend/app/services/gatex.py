"""
GateX (MENA Compass) API Client Service
=========================================
Implements the Bulk Report Ingestion API flow exactly as documented:

  Step 1 — GET  /api/common/categories?type=report  (taxonomy, cached)
  Step 2 — POST /api/utils/presigned-url             (PDF)
  Step 3 — PUT  <signed_url>                         (upload PDF)
  Step 4 — POST /api/utils/presigned-url             (cover image)
  Step 5 — PUT  <signed_url>                         (upload cover image)
  Step 6 — POST /api/reports/bulk                    (submit metadata)

Authentication:  X-API-Key header on Steps 2 and 6 only.
Steps 3 and 5 use the exact URL/method/headers from the presign response.

Retry strategy (transient 5xx / timeout only):
  - Max retries: GATEX_MAX_RETRIES (default 3)
  - Backoff: 2^attempt * 0.5 seconds (0.5s, 1s, 2s, …)
  - Never retry 4xx responses (non-retryable).
  - Never retry a storage PUT unless the response was a clear failure/timeout.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import httpx

from app.core.config import settings
from app.logging.logger import logger


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------
class GateXError(Exception):
    """Base GateX error."""
    pass


class GateXAuthError(GateXError):
    """401/403 — non-retryable."""
    pass


class GateXValidationError(GateXError):
    """400 or item-level field error — non-retryable."""
    def __init__(self, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.detail = detail or {}


class GateXUploadError(GateXError):
    """Presigned storage PUT failed — retryable if status unknown."""
    pass


class GateXMetadataError(GateXError):
    """Report metadata submission failed."""
    pass


class GateXDisabledError(GateXError):
    """Publishing is disabled via GATEX_ENABLE_PUBLISHING=false."""
    pass


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class PresignResponse:
    url: str
    key: str
    method: str
    headers: Dict[str, str]
    public_url: Optional[str] = None


@dataclass
class GateXReportPayload:
    """Maps internal report metadata to GateX API fields."""
    title: str
    original_file_name: str
    mime_type: str
    file_size: int
    original_object_key: str   # data.key from REPORT_ORIGINAL presign
    top_image: str             # data.key from REPORT_IMAGE presign
    category_id: int
    tag_ids: List[int]
    description: Optional[str] = None
    region_id: Optional[int] = None
    price: float = 5800.0     # GateX minimum price requirement is 5800
    is_featured: bool = False
    publish: bool = False      # Set True to publish immediately on creation


@dataclass
class GateXSubmitResult:
    success: bool
    external_report_id: Optional[int]
    external_status: Optional[str]        # "draft", "published", etc.
    processing_status: Optional[str]      # "PROCESSING", "READY"
    raw_response: Dict[str, Any] = field(default_factory=dict)
    failed_entries: List[dict] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass
class GateXUnpublishResult:
    """
    Unpublish result from the GateX block API.
    """
    success: bool
    supported: bool = True
    message: str = "Report successfully blocked on GateX."
    external_report_id: Optional[int] = None
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# GateX Client
# ---------------------------------------------------------------------------
class GateXClient:
    """
    Async HTTP client for the GateX (MENA Compass) Bulk Report Ingestion API.
    All methods raise GateXError subclasses on failures.
    """

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-Key": settings.GATEX_API_KEY,
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return settings.GATEX_BASE_URL.rstrip("/")

    def _assert_enabled(self) -> None:
        if not settings.GATEX_ENABLE_PUBLISHING:
            raise GateXDisabledError(
                "GateX publishing is disabled (GATEX_ENABLE_PUBLISHING=false). "
                "Set GATEX_ENABLE_PUBLISHING=true and configure GATEX_BASE_URL and GATEX_API_KEY to enable."
            )
        if not settings.GATEX_BASE_URL or not settings.GATEX_API_KEY:
            raise GateXDisabledError(
                "GATEX_BASE_URL and GATEX_API_KEY must both be set to publish."
            )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: Optional[int] = None,
        **kwargs
    ) -> httpx.Response:
        """
        Executes an HTTP request with exponential backoff on transient 5xx/timeout.
        Non-retryable status codes (4xx) are raised immediately.
        """
        retries = max_retries if max_retries is not None else settings.GATEX_MAX_RETRIES
        last_exc: Optional[Exception] = None

        async with httpx.AsyncClient(timeout=settings.GATEX_TIMEOUT) as client:
            for attempt in range(retries + 1):
                try:
                    resp = await client.request(method, url, **kwargs)

                    # Non-retryable auth errors
                    if resp.status_code == 401:
                        raise GateXAuthError(f"GateX authentication failed (401): check GATEX_API_KEY.")
                    if resp.status_code == 403:
                        raise GateXAuthError(f"GateX forbidden (403): API key does not permit this upload type.")

                    # Non-retryable client errors
                    if 400 <= resp.status_code < 500:
                        raise GateXValidationError(
                            f"GateX client error {resp.status_code}: {resp.text[:300]}",
                            detail={"status_code": resp.status_code, "body": resp.text[:500]}
                        )

                    # Retryable server errors
                    if resp.status_code >= 500:
                        if attempt < retries:
                            backoff = (2 ** attempt) * 0.5
                            logger.warning(
                                f"GateX server error {resp.status_code} on attempt {attempt+1}/{retries+1}. "
                                f"Retrying in {backoff:.1f}s..."
                            )
                            await asyncio.sleep(backoff)
                            last_exc = GateXError(f"GateX server error {resp.status_code}")
                            continue
                        raise GateXError(f"GateX server error {resp.status_code} after {retries} retries: {resp.text[:300]}")

                    return resp

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    if attempt < retries:
                        backoff = (2 ** attempt) * 0.5
                        logger.warning(f"GateX network error on attempt {attempt+1}/{retries+1}: {e}. Retrying in {backoff:.1f}s...")
                        await asyncio.sleep(backoff)
                        last_exc = e
                        continue
                    raise GateXError(f"GateX network error after {retries} retries: {e}") from e
                except (GateXAuthError, GateXValidationError):
                    raise  # Never retry these

        if last_exc:
            raise GateXError(f"GateX request failed after all retries") from last_exc
        raise GateXError("GateX request failed with unknown error")

    # -----------------------------------------------------------------------
    # Step 2: Get Presigned URL for PDF or Cover Image
    # -----------------------------------------------------------------------
    async def get_presigned_url(
        self,
        key: str,
        content_type: str,
        upload_type: str,   # "REPORT_ORIGINAL" or "REPORT_IMAGE"
        file_size: int,
    ) -> PresignResponse:
        """
        POST /api/utils/presigned-url
        Returns the presigned URL, method, and exact headers to use for the PUT upload.
        """
        self._assert_enabled()

        payload = {
            "key": key,
            "contentType": content_type,
            "uploadType": upload_type,
            "fileSize": file_size,
        }

        logger.info(f"Requesting GateX presigned URL: uploadType={upload_type} key={key} size={file_size}")

        resp = await self._request_with_retry(
            "POST",
            f"{self._base()}/utils/presigned-url",
            headers=self._headers(),
            json=payload,
        )

        data = resp.json().get("data", {})
        presign = PresignResponse(
            url=data["url"],
            key=data["key"],
            method=data.get("method", "PUT"),
            headers=data.get("headers", {}),
            public_url=data.get("publicUrl"),
        )
        logger.info(f"Presigned URL received: object_key={presign.key}")
        return presign

    # -----------------------------------------------------------------------
    # Step 3 / 5: Upload file to presigned URL
    # -----------------------------------------------------------------------
    async def upload_file(
        self,
        presign: PresignResponse,
        file_data: bytes,
    ) -> None:
        """
        Performs the direct PUT upload to the storage provider URL returned by GateX.
        Uses the exact URL, method, and headers from the presign response.
        Does NOT send the X-API-Key here — this is a direct storage call.

        Per API docs: Do not retry a storage PUT unless the response failed or is unknown.
        """
        logger.info(f"Uploading {len(file_data)} bytes to GateX storage: key={presign.key}")

        # Use a single attempt for the PUT (no retry per API docs),
        # but we DO retry on timeout/connect error since those are "unknown"
        async with httpx.AsyncClient(timeout=settings.GATEX_TIMEOUT * 2) as client:  # extra time for uploads
            try:
                resp = await client.request(
                    method=presign.method.upper(),
                    url=presign.url,
                    headers=presign.headers,
                    content=file_data,
                )
                if resp.status_code not in (200, 201, 204):
                    raise GateXUploadError(
                        f"Storage upload failed: HTTP {resp.status_code} — {resp.text[:200]}"
                    )
                logger.info(f"Upload succeeded: key={presign.key} status={resp.status_code}")

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                # Timeout on PUT — status unknown, retrying is allowed per docs
                logger.warning(f"Storage upload timeout for key={presign.key}: {e}. Retrying once...")
                await asyncio.sleep(2.0)
                retry_resp = await client.request(
                    method=presign.method.upper(),
                    url=presign.url,
                    headers=presign.headers,
                    content=file_data,
                )
                if retry_resp.status_code not in (200, 201, 204):
                    raise GateXUploadError(
                        f"Storage upload retry failed: HTTP {retry_resp.status_code}"
                    )
                logger.info(f"Upload retry succeeded: key={presign.key}")

    # -----------------------------------------------------------------------
    # Step 6: Submit bulk report metadata
    # -----------------------------------------------------------------------
    async def submit_bulk_report(self, payload: GateXReportPayload) -> GateXSubmitResult:
        """
        POST /api/reports/bulk
        Sends exactly one report item in the batch.
        Handles 201 (all success) and 207 (partial/full failure).
        """
        self._assert_enabled()

        body = {
            "reports": [
                {
                    "title": payload.title,
                    "originalFileName": payload.original_file_name,
                    "mimeType": payload.mime_type,
                    "fileSize": payload.file_size,
                    "originalObjectKey": payload.original_object_key,
                    "topImage": payload.top_image,
                    "categoryId": payload.category_id,
                    "tagIds": payload.tag_ids,
                    "isFeatured": payload.is_featured,
                    "price": payload.price,
                    "publish": payload.publish,
                    **({"description": payload.description} if payload.description else {}),
                    **({"regionId": payload.region_id} if payload.region_id else {}),
                }
            ]
        }

        logger.info(f"Submitting report to GateX bulk API: title={payload.title!r}")

        resp = await self._request_with_retry(
            "POST",
            f"{self._base()}/reports/bulk",
            headers=self._headers(),
            json=body,
        )

        resp_json = resp.json()
        data = resp_json.get("data", {})
        items = data.get("items", [])
        failed = data.get("failed", [])

        if resp.status_code == 207 or failed:
            # Partial or full failure
            first_error = failed[0].get("error", {}) if failed else {}
            # Include field-level details if present (e.g. price minimum validation)
            details = first_error.get("details", [])
            detail_str = "; ".join(f"{d.get('field','?')}: {d.get('message','?')}" for d in details)
            error_msg = first_error.get("message", "GateX reported a failure for this report")
            if detail_str:
                error_msg = f"{error_msg} — {detail_str}"
            logger.error(f"GateX bulk submission failed: {failed}")
            return GateXSubmitResult(
                success=False,
                external_report_id=items[0]["id"] if items else None,
                external_status=items[0].get("status") if items else None,
                processing_status=items[0].get("processingStatus") if items else None,
                raw_response=resp_json,
                failed_entries=failed,
                error_message=error_msg,
            )

        # 201 — all succeeded
        first = items[0] if items else {}
        logger.info(f"GateX bulk submission successful: external_id={first.get('id')} status={first.get('status')}")
        return GateXSubmitResult(
            success=True,
            external_report_id=first.get("id"),
            external_status=first.get("status"),
            processing_status=first.get("processingStatus"),
            raw_response=resp_json,
        )

    # -----------------------------------------------------------------------
    # Unpublish (Block) Report
    # -----------------------------------------------------------------------
    async def unpublish_report(self, external_report_id: int) -> GateXUnpublishResult:
        """
        Calls the GateX API to block/remove a report.
        """
        url = f"{self._base()}/reports/{external_report_id}/block/by-api-key"
        headers = {"X-API-Key": settings.GATEX_API_KEY}

        logger.info(f"Calling GateX unpublish API: PATCH {url}")
        try:
            # We don't use the retry client here because 403s should fail fast
            async with httpx.AsyncClient(timeout=settings.GATEX_TIMEOUT) as client:
                resp = await client.patch(url, headers=headers)
                
            resp_json = resp.json() if resp.text else {}
            
            if resp.status_code in (200, 204):
                logger.info(f"GateX unpublish successful for external_report_id={external_report_id}")
                return GateXUnpublishResult(
                    success=True,
                    external_report_id=external_report_id,
                )
                
            # Handle failure
            error_msg = resp_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
            logger.error(f"GateX unpublish failed: {resp.status_code} - {error_msg}")
            return GateXUnpublishResult(
                success=False,
                external_report_id=external_report_id,
                error_message=error_msg,
            )
        except Exception as e:
            logger.exception(f"Exception during GateX unpublish for {external_report_id}")
            return GateXUnpublishResult(
                success=False,
                external_report_id=external_report_id,
                error_message=str(e),
            )


# ---------------------------------------------------------------------------
# Singleton client instance
# ---------------------------------------------------------------------------
gatex_client = GateXClient()
