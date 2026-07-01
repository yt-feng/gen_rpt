# GateX / MENA Compass Enterprise Publishing Integration

**Version:** 1.0  
**Phase:** 14  
**Date:** 2026-07-02

---

## Architecture

```
Frontend (Cloudflare Pages)
    │
    │  POST /api/v1/publish/{report_id}
    ▼
Backend (FastAPI / Render)
    │
    ├─ EligibilityValidator
    ├─ DuplicateProtectionCheck
    ├─ PublishOrchestrator
    │       │
    │       ├── R2 (Cloudflare) ──► fetch PDF bytes
    │       ├── R2 (Cloudflare) ──► fetch cover image bytes
    │       │
    │       ├── GateXClient ──► POST /utils/presigned-url (PDF)
    │       ├── GateXClient ──► PUT  <signed_url> (PDF upload)
    │       ├── GateXClient ──► POST /utils/presigned-url (cover)
    │       ├── GateXClient ──► PUT  <signed_url> (cover upload)
    │       └── GateXClient ──► POST /reports/bulk (metadata)
    │
    ├─ Supabase (PostgreSQL)
    │       └── gatex_publications (external IDs, status, audit)
    │
    └─ AuditService ──► audit_logs
```

**Supabase** remains the system of record.  
**Cloudflare R2** remains internal object storage.  
**GateX** is the external publishing destination.

---

## Publishing Sequence

```
1.  User clicks Publish in the frontend
2.  Frontend calls POST /api/v1/publish/{report_id}
3.  Backend validates eligibility:
        - Status must be "Approved"
        - publishReady must not be false
4.  Backend checks for duplicate:
        - Queries gatex_publications for existing "published"/"publishing" records
        - Aborts if duplicate found
5.  Backend creates GateXPublication record with status="publishing"
6.  Backend fetches PDF bytes from Cloudflare R2
7.  Backend fetches cover image bytes from Cloudflare R2
8.  Backend calls POST /api/utils/presigned-url for PDF
        - uploadType: "REPORT_ORIGINAL"
        - Receives: url, key, method, headers
9.  Backend uploads PDF using PUT <signed_url> with exact headers from step 8
        - Stores returned key as original_object_key
10. Backend calls POST /api/utils/presigned-url for cover image
        - uploadType: "REPORT_IMAGE"
        - Receives: url, key, method, headers, publicUrl
11. Backend uploads cover image using PUT <signed_url>
        - Stores returned key as cover_image_key
12. Backend resolves GateX taxonomy IDs:
        - categoryId from /api/common/categories?type=report (cached)
        - tagIds from /api/common/tags (cached, TTL 1hr)
        - regionId from /api/common/regions (optional, cached)
13. Backend builds GateX report metadata payload
14. Backend calls POST /api/reports/bulk
        - Receives 201 (success) or 207 (partial failure)
        - Extracts external_report_id from response
15. Backend stores external identifiers in GateXPublication record
16. Backend updates report status to "Published"
17. Backend records audit log entries
18. Backend returns success response to frontend
```

---

## API Mapping

### Internal Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/publish/{report_id}` | POST | Trigger full GateX publish pipeline |
| `/api/v1/unpublish/{report_id}` | POST | Trigger unpublish (abstraction layer) |
| `/api/v1/publish/{report_id}/status` | GET | Current publish status + history |
| `/api/v1/publish/history` | GET | All publication records |
| `/api/v1/publish/logs/{report_id}` | GET | Audit logs for a report |
| `/api/v1/publish/taxonomy/status` | GET | GateX taxonomy cache status |
| `/api/v1/publish/taxonomy/refresh` | POST | Force refresh taxonomy cache |

### External GateX Endpoints Called

| GateX Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/common/categories?type=report` | GET | None (public) | Fetch report categories |
| `/api/common/tags` | GET | None (public) | Fetch tags |
| `/api/common/regions` | GET | None (public) | Fetch regions |
| `/api/utils/presigned-url` | POST | X-API-Key | Request presigned upload URL |
| `<presigned_url>` | PUT | None (signed) | Direct storage upload |
| `/api/reports/bulk` | POST | X-API-Key | Submit report metadata |

---

## Field Mapping

| Internal Field | GateX Field | Type | Notes |
|---|---|---|---|
| `report["title"]` | `title` | string | 1–255 chars |
| `report["description"]` | `description` | string | Optional |
| PDF filename | `originalFileName` | string | Must end in `.pdf` |
| `"application/pdf"` | `mimeType` | string | Fixed value |
| `len(pdf_bytes)` | `fileSize` | number | Must be > 0 |
| `pdf_presign.key` | `originalObjectKey` | string | From REPORT_ORIGINAL presign |
| `img_presign.key` | `topImage` | string | From REPORT_IMAGE presign |
| Resolved from `report["industry"]` | `categoryId` | number | Matched from taxonomy cache |
| Resolved from `report["tags"]` | `tagIds` | number[] | 1–5 required |
| Resolved from `report["region"]` | `regionId` | number | Optional |
| `0.0` | `price` | number | Default free |
| `False` | `isFeatured` | boolean | Configurable |
| `True` | `publish` | boolean | Publish immediately on creation |

---

## State Machine

```
[User clicks Publish]
          │
  ┌───────▼────────┐
  │  ELIGIBILITY   │──── FAIL ───► publish_failed
  └───────┬────────┘
          │ PASS
  ┌───────▼────────┐
  │   DUPLICATE    │──── EXISTS ─► Blocked (returns existing state)
  │   CHECK        │
  └───────┬────────┘
          │ CLEAR
  ┌───────▼────────┐
  │   PUBLISHING   │  (internal)
  └───────┬────────┘
          │
  [Files uploaded to GateX storage]
          │
  [POST /api/reports/bulk]
          │
  ┌───────┴──────────────┐
  │ 201 OK               │ 207 / Error
  ▼                      ▼
EXTERNAL_SYNC_PENDING   PUBLISH_FAILED (retryable)
          │
  [GateX processes]
          │
       PUBLISHED
          │
  [User clicks Unpublish]
          │
       UNPUBLISHING
          │
       UNPUBLISHED ──► Manual panel removal required (see Known Limitations)
```

---

## Validation Rules

| Rule | Enforced At | Error |
|---|---|---|
| Status must be "Approved" | Eligibility check | 400 |
| `publishReady` must not be `false` | Eligibility check | 400 |
| PDF must be available in R2 | File fetch | 400 |
| Cover image must be available | File fetch | 400 |
| `categoryId` must resolve | Taxonomy resolution | 400 |
| At least 1 `tagId` must resolve | Taxonomy resolution | 400 |
| No existing "published"/"publishing" record | Duplicate check | 409 |
| `GATEX_ENABLE_PUBLISHING=true` required | Pre-flight | 503 |
| `GATEX_BASE_URL` and `GATEX_API_KEY` set | Pre-flight | 503 |

---

## Error Handling

| Error Type | HTTP Code | Retryable | Handling |
|---|---|---|---|
| `GateXAuthError` | 401 / 403 | ❌ | Fix GATEX_API_KEY on Render |
| `GateXValidationError` | 400 | ❌ | Fix field values (category, tags) |
| `GateXMetadataError` | 207 | Partial | Retry only `data.failed` entries |
| `GateXUploadError` | — | If unknown | Single retry on storage PUT timeout |
| `GateXError` (5xx) | 500+ | ✅ | Exponential backoff, max 3 retries |
| Network timeout | — | ✅ | Exponential backoff, max 3 retries |
| PDF not in R2 | — | ❌ | Generate PDF first |
| Cover not in R2 | — | ❌ | Set `GATEX_DEFAULT_COVER_PATH` |

---

## Retry Strategy

```
Max retries:  GATEX_MAX_RETRIES (default: 3)
Backoff:      2^attempt × 0.5 seconds
              attempt 0 → 0.5s wait
              attempt 1 → 1.0s wait
              attempt 2 → 2.0s wait

Retryable:    5xx responses, timeout, connect errors
NOT retried:  4xx responses, GateXAuthError, GateXValidationError

Storage PUT:  Single retry on timeout only
              (API docs: do not retry PUT unless status is unknown)

For 207 Multi-Status: retry only entries returned in data.failed[]
```

---

## Audit Model

All publish events are recorded in `audit_logs`:

| Event | `action` | `table_name` |
|---|---|---|
| Eligibility failed | `eligibility_failed` | `gatex_publications` |
| Duplicate blocked | `duplicate_blocked` | `gatex_publications` |
| Publish succeeded | `publish_success` | `gatex_publications` |
| Publish failed | `publish_failed` | `gatex_publications` |
| Unpublish requested | `unpublish_requested` | `gatex_publications` |

Step-level detail is included in the API response `audit_trail` array (in-memory per request).

---

## External ID Mapping (gatex_publications table)

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Internal primary key |
| `document_id` | UUID FK | → documents.id |
| `version_id` | UUID FK | → document_versions.id (nullable) |
| `external_report_id` | Integer | GateX assigned report ID |
| `original_object_key` | String | data.key from REPORT_ORIGINAL presign |
| `cover_image_key` | String | data.key from REPORT_IMAGE presign |
| `publish_status` | String | Current lifecycle state |
| `external_response` | JSONB | Full GateX bulk response |
| `published_at` | Timestamp | Successful submission time (UTC) |
| `published_by` | UUID FK | User who triggered publish |
| `publish_duration_ms` | Integer | Total pipeline time |
| `retry_count` | Integer | Retries performed |
| `errors` | String | Last error message |
| `last_synced_at` | Timestamp | Last status verification |
| `created_at` | Timestamp | Record creation time |

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GATEX_BASE_URL` | `""` | GateX API base URL e.g. `https://<host>/api` |
| `GATEX_API_KEY` | `""` | X-API-Key from MENA Compass team |
| `GATEX_TIMEOUT` | `30` | HTTP request timeout (seconds) |
| `GATEX_MAX_RETRIES` | `3` | Max retries for 5xx/timeout |
| `GATEX_VERIFY_UPLOAD` | `true` | Verify upload after PUT |
| `GATEX_ENABLE_PUBLISHING` | `false` | Master switch — set `true` to enable |
| `GATEX_DEFAULT_COVER_PATH` | `""` | R2 fallback cover image path |

---

## Known Limitations

1. **No Official GateX Unpublish API**  
   GateX does not document a DELETE/unpublish endpoint. Internal records are marked `unpublished` but the report remains in MENA Compass until manually removed from the admin panel. The `unpublish_report()` abstraction stub in `GateXClient` is ready to be wired when the endpoint is confirmed.

2. **Mock-mode PDF/Cover Paths**  
   The system currently uses `MOCK_REPORTS` dict. The orchestrator looks for `pdfPath` and `coverImagePath` fields. In production with real `DocumentFile` records in Supabase, these resolve from the database. Set `GATEX_DEFAULT_COVER_PATH` as a fallback.

3. **No GateX Processing Status Polling**  
   GateX creates reports with `processingStatus: "PROCESSING"`. The platform processes asynchronously and sets `READY`. The current integration does not poll for this — status remains `external_sync_pending` until manually confirmed. A future background task can close this gap.

4. **In-Memory Taxonomy Cache**  
   Category/tag/region cache is per-process in memory. Works on Render (single worker). For multi-worker deployments, migrate cache to Redis.

---

## Future Enhancements

- Background polling task for GateX `processingStatus` → auto-transition to `published`
- Webhook receiver for GateX status callbacks
- Real GateX unpublish endpoint (when provided by MENA Compass team)
- Redis-backed taxonomy cache for multi-worker deployments
- PDF auto-generation via existing rendering service stored to R2
- Admin dashboard for publish history visualization
- Re-publish via `PATCH /api/reports/:id` to update existing GateX records
