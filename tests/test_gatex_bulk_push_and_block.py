"""
GateX Bulk Report Ingestion & Idempotent Block API Test Suite
============================================================
Validates pushing 5 test reports via GateX Bulk Ingestion flow (presigned URLs -> storage upload -> bulk creation)
and deleting/blocking them using the idempotent block endpoint (PATCH /api/reports/{id}/block/by-api-key).
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Ensure report-management-backend package is on sys.path
backend_dir = str(Path(__file__).resolve().parent.parent / "report-management-backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import httpx


from app.core.config import settings

from app.services.gatex import (
    GateXClient,
    GateXReportPayload,
    GateXSubmitResult,
    GateXUnpublishResult,
)


class GateXBulkPushAndBlockTests(unittest.IsolatedAsyncioTestCase):
    """
    Test suite for GateX bulk report submission (5 reports) and idempotent block/removal.
    """

    def setUp(self):
        self.base_url = settings.GATEX_BASE_URL or "https://dev.gatex.ae/api"
        self.api_key = settings.GATEX_API_KEY or "test-gatex-api-key"
        self.client = GateXClient()

    async def test_bulk_push_5_reports_and_idempotent_block(self):
        """
        End-to-end workflow:
          1. Resolve category & tag taxonomy.
          2. Generate 5 test report payloads with presigned URL uploads.
          3. Bulk submit 5 reports and capture created report IDs.
          4. Block/remove all 5 reports via PATCH /reports/{id}/block/by-api-key.
          5. Verify idempotency: re-block returns 200 OK without error.
        """
        # Mock HTTP transport to guarantee deterministic test execution regardless of network
        fake_created_ids = [1001, 1002, 1003, 1004, 1005]
        created_report_log = []
        blocked_ids = set()

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            method = request.method.upper()

            # 1. Category taxonomy lookup
            if method == "GET" and "/common/categories" in url_str:
                return httpx.Response(
                    200,
                    json={"data": {"items": [{"id": 6, "name": "MENA Report", "type": "report"}], "total": 1}},
                )

            # 2. Tag taxonomy lookup
            if method == "GET" and "/common/tags" in url_str:
                return httpx.Response(
                    200,
                    json={"data": {"items": [{"id": 1, "name": "Macroeconomics"}], "total": 1}},
                )

            # 3. Presigned URL request
            if method == "POST" and "/utils/presigned-url" in url_str:
                body = request.read().decode("utf-8")
                upload_type = "REPORT_ORIGINAL" if "REPORT_ORIGINAL" in body else "REPORT_IMAGE"
                fake_key = f"mock-storage/key-{upload_type.lower()}-file"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "url": "https://mock-storage.gatex.ae/upload",
                            "key": fake_key,
                            "method": "PUT",
                            "headers": {"Content-Type": "application/octet-stream"},
                        }
                    },
                )

            # 4. Storage PUT upload
            if method == "PUT" and "mock-storage" in url_str:
                return httpx.Response(200, text="OK")

            # 5. Bulk report submit
            if method == "POST" and "/reports/bulk" in url_str:
                # Verify X-API-Key header
                if request.headers.get("X-API-Key") != self.api_key:
                    return httpx.Response(401, json={"error": "Invalid API Key"})
                body_json = json.loads(request.read().decode("utf-8"))
                reports = body_json.get("reports", [])
                cid = 1000 + len(created_report_log) + 1
                created_report_log.append(cid)
                items = [{"id": cid, "status": "draft", "processingStatus": "READY"}]
                return httpx.Response(201, json={"data": {"items": items, "failed": []}})

            # 6. Idempotent Block / Delete endpoint by API Key
            if method == "PATCH" and "/block/by-api-key" in url_str:
                if request.headers.get("X-API-Key") != self.api_key:
                    return httpx.Response(401, json={"error": "Invalid API Key"})
                # Extract report_id from URL: /reports/{report_id}/block/by-api-key
                parts = url_str.split("/")
                rid_index = parts.index("reports") + 1
                rid = int(parts[rid_index])
                if rid in blocked_ids:
                    # Idempotent response: 200 OK with "already blocked"
                    return httpx.Response(
                        200,
                        json={"status": 200, "message": "Report is already blocked", "data": {"id": rid, "status": "blocked"}},
                    )
                blocked_ids.add(rid)
                return httpx.Response(
                    200,
                    json={"status": 200, "message": "Report successfully blocked", "data": {"id": rid, "status": "blocked"}},
                )

            return httpx.Response(404, json={"error": "Not Found"})

        transport = httpx.MockTransport(mock_handler)
        original_async_client = httpx.AsyncClient

        def mock_client_factory(**kwargs):
            kwargs["transport"] = transport
            return original_async_client(**kwargs)

        with patch("httpx.AsyncClient", side_effect=mock_client_factory):

            # Enable GateX settings for testing
            with patch.object(settings, "GATEX_ENABLE_PUBLISHING", True), \
                 patch.object(settings, "GATEX_BASE_URL", self.base_url), \
                 patch.object(settings, "GATEX_API_KEY", self.api_key):

                # Step 1: Prepare 5 test report payloads
                test_payloads = [
                    GateXReportPayload(
                        title=f"Automated Test Report #{i}",
                        original_file_name=f"test_report_{i}.pdf",
                        mime_type="application/pdf",
                        file_size=1024 * i,
                        original_object_key=f"mock-storage/key-pdf-{i}",
                        top_image=f"mock-storage/key-img-{i}",
                        category_id=6,
                        tag_ids=[1],
                        description=f"Automated test report payload item #{i}",
                        price=5800.0,
                        publish=False,
                    )
                    for i in range(1, 6)
                ]

                # Step 2: Push 5 reports via Bulk API
                created_report_ids = []
                for payload in test_payloads:
                    submit_res: GateXSubmitResult = await self.client.submit_bulk_report(payload)
                    self.assertTrue(submit_res.success, f"Failed submitting payload {payload.title}: {submit_res.error_message}")
                    self.assertIsNotNone(submit_res.external_report_id)
                    created_report_ids.append(submit_res.external_report_id)

                self.assertEqual(len(created_report_ids), 5, "Expected 5 report IDs to be created")
                self.assertEqual(created_report_ids, fake_created_ids)

                # Step 3: Block / delete all 5 reports using PATCH /reports/{id}/block/by-api-key
                for rid in created_report_ids:
                    unpublish_res: GateXUnpublishResult = await self.client.unpublish_report(rid)
                    self.assertTrue(
                        unpublish_res.success,
                        f"Failed blocking report ID {rid}: {unpublish_res.error_message}",
                    )
                    self.assertEqual(unpublish_res.external_report_id, rid)

                self.assertEqual(len(blocked_ids), 5, "Expected all 5 reports to be blocked")

                # Step 4: Verify Idempotency — re-blocking already deleted reports returns success (200 OK)
                for rid in created_report_ids:
                    reblock_res: GateXUnpublishResult = await self.client.unpublish_report(rid)
                    self.assertTrue(
                        reblock_res.success,
                        f"Idempotent re-block failed for report ID {rid}: {reblock_res.error_message}",
                    )
                    self.assertEqual(reblock_res.external_report_id, rid)

    async def test_unpublish_report_method_contract(self):
        """
        Direct test of unpublish_report method on GateXClient to confirm endpoint format and header.
        """
        client = GateXClient()
        captured_request = {}

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            captured_request["method"] = request.method
            captured_request["url"] = str(request.url)
            captured_request["api_key_header"] = request.headers.get("X-API-Key")
            return httpx.Response(200, json={"status": 200, "message": "already blocked"})

        transport = httpx.MockTransport(capture_handler)
        original_async_client = httpx.AsyncClient

        def mock_client_factory(**kwargs):
            kwargs["transport"] = transport
            return original_async_client(**kwargs)

        with patch("httpx.AsyncClient", side_effect=mock_client_factory), \
             patch.object(settings, "GATEX_BASE_URL", "https://dev.gatex.ae/api"), \
             patch.object(settings, "GATEX_API_KEY", "secret-test-key"):
            
            res = await client.unpublish_report(9999)
            self.assertTrue(res.success)
            self.assertEqual(res.external_report_id, 9999)
            self.assertEqual(captured_request["method"], "PATCH")
            self.assertEqual(captured_request["url"], "https://dev.gatex.ae/api/reports/9999/block/by-api-key")
            self.assertEqual(captured_request["api_key_header"], "secret-test-key")



if __name__ == "__main__":
    unittest.main()
