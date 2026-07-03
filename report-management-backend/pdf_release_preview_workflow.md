# PDF Release Preview Workflow Documentation

## Architecture

The PDF Release Preview system introduces a non-destructive intermediate step between an approved report and GateX publication. The existing publish and unpublish pipelines are **completely unchanged**.

```
Approved Report
      │
      ▼
[Publish Report Instantly] button
  → HumanReviewCard.tsx intercepts click
      │
      ▼
POST /api/v1/pdf-release/{report_id}/preview
  → pdf_release_service.get_or_generate()
      │
      ├── [HTML checksum matches latest PdfRelease?]
      │         YES → reuse existing PDF (is_new=false)
      │         NO  → generate new PDF via xhtml2pdf → upload to R2
      │
      ▼
PdfReleasePreviewModal renders (fixed overlay, no new route)
      │
      ├── [Publish] → actions.sendToPublish.mutateAsync()
      │                → existing POST /api/v1/publish/{id} (UNCHANGED)
      │                → existing GateX 15-step pipeline (UNCHANGED)
      │
      └── [Cancel]  → setPdfPreview(null) → modal closes
                       PDF stays in R2. Nothing deleted.
```

---

## PDF Version Strategy

PDFs are versioned monotonically per document:

| Report Version | HTML Version | PDF Version | R2 Path |
|---|---|---|---|
| v1 | checksum-A | PDF v1 | `reports/{doc_id}/versions/pdf/v1/report.pdf` |
| v2 (content changed) | checksum-B | PDF v2 | `reports/{doc_id}/versions/pdf/v2/report.pdf` |
| v2 (no change, re-publish attempt) | checksum-B | PDF v2 reused | same path |
| v3 (edited again) | checksum-C | PDF v3 | `reports/{doc_id}/versions/pdf/v3/report.pdf` |

- Previous PDF versions are **never deleted or overwritten**.
- The `is_active` flag marks which is the current PDF for a document.
- `gatex_published_version=True` marks which PDF was actually sent to GateX.

---

## Change Detection

Before generating a PDF, the service:
1. Resolves the HTML for the report (from R2 snapshot URL, companion path, or reports_web path).
2. Computes `sha256(html_bytes)` as the `html_checksum`.
3. Queries `pdf_releases` for the latest `is_active=True` record for this `document_id`.
4. Compares checksums:
   - **Match** → return existing `PdfRelease` record with a fresh presigned URL (no regeneration).
   - **Mismatch or no record** → generate new PDF, deactivate old record, create new one.

---

## R2 Storage Structure

```
reports/
  {document_id}/
    versions/
      pdf/
        v1/
          report.pdf      ← immutable, never overwritten
        v2/
          report.pdf      ← immutable
        v3/
          report.pdf      ← current active PDF
```

This path structure is separate from and does not conflict with the existing `reports/{doc_id}/versions/{version_id}/pdf/` path used by `StorageService`.

---

## Database Schema — `pdf_releases` Table

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | Unique record ID |
| `document_id` | UUID FK | Parent document |
| `document_version_id` | UUID FK nullable | Document version (if available) |
| `version_number` | int | Monotonically increasing PDF version per document |
| `html_checksum` | varchar(64) | SHA-256 of source HTML |
| `canonical_version_label` | varchar(64) | Human-readable version label (e.g. "1.2") |
| `storage_path` | text | R2 key for the PDF |
| `file_size_bytes` | bigint | PDF file size |
| `render_duration_ms` | int | Time to generate in milliseconds |
| `generated_by` | UUID FK nullable | Actor who triggered generation |
| `generated_at` | timestamptz | Generation timestamp |
| `is_active` | bool | True = current latest PDF for document |
| `gatex_published_version` | bool | True = this PDF was sent to GateX |

---

## Version Mapping

```
Report v1
  └── HTML v1  (checksum-A)
        └── PDF v1  → stored at reports/{id}/versions/pdf/v1/report.pdf
              └── Published → GateX receives PDF v1
                             gatex_published_version = True

Human edits report
  └── Report v2  (HTML v2, checksum-B ≠ checksum-A)
        └── PDF v2  → stored at reports/{id}/versions/pdf/v2/report.pdf
              └── Preview modal shows PDF v2
                    └── Publish → GateX receives PDF v2
                                 PDF v1 remains in R2 history

No edit, re-publish attempt
  └── checksum-B == checksum-B (match)
        └── PDF v2 reused (is_new=false)
              └── Modal shows "Reused · No Changes" badge
```

---

## Preview Modal Flow

1. User opens a report in "Approved" state.
2. User selects "Approved" decision in HumanReviewCard → clicks **"Publish Report Instantly"**.
3. Button transitions to "Preparing PDF Preview…" with spinner.
4. `publishService.getPdfReleasePreview(reportId)` calls `POST /api/v1/pdf-release/{id}/preview`.
5. Backend generates or reuses a PDF and returns a presigned URL (valid 1 hour).
6. `PdfReleasePreviewModal` renders as a fixed overlay:
   - Left panel: embedded `<iframe>` showing the PDF.
   - Right sidebar: version number, document version, timestamp, file size, checksum, sync status.
   - Header badge: "Newly Generated" (blue) or "Reused · No Changes" (green).
7. **Publish** → calls `actions.sendToPublish.mutateAsync()` (existing pipeline, zero changes).
8. **Cancel** → `setPdfPreview(null)`. PDF stays in R2. No publish occurs. Status unchanged.

---

## Backend Service — `PdfReleaseService`

**File:** `app/services/pdf_release.py`

### `get_or_generate(db, report_id, report, actor_id) → PdfReleaseResult`
Main entry point. Resolves HTML → computes checksum → queries DB → returns existing or new PDF.

### HTML Resolution Priority
1. `report['snapshot_html_url']` (real mode — R2 snapshot path)
2. Companion `.html` path derived from `report['pdfPath']`
3. `reports_web/{slug}/index.html` (GitHub Actions generated web report)
4. Fallback: build HTML from `reportContent` sections dict (mock mode)

### PDF Generation
- Library: `xhtml2pdf` (pure Python, no system libraries required)
- Runs in a thread via `anyio.to_thread.run_sync` to avoid blocking the async event loop
- Page format: A4 with header/footer via CSS `@page` rules
- Preserves typography, spacing, sections, headings, body text

### `get_latest_for_document(db, document_id) → Optional[PdfRelease]`
Returns the latest active `PdfRelease` for a document. Can be used by future systems (e.g. versioned PDF history API).

---

## Frontend Flow

### New files
- `src/components/review/PdfReleasePreviewModal.tsx` — modal component
- Type `PdfReleasePreview` added to `src/types/publish.types.ts`

### Modified files
- `src/services/publish.service.ts` — new `getPdfReleasePreview()` method added
- `src/components/review/HumanReviewCard.tsx` — `handlePublish` intercepted

### What did NOT change in HumanReviewCard
- `handleDecisionChange` (Approved / Needs Revision / Rejected logic)
- `handleSubmitComment`
- `handleSaveReview`
- `handleConfirmReject`
- `handleCancelReject`
- All JSX below the Approved button section
- Needs Revision and Rejected flows
- "Save Approval" button

---

## Performance Optimizations

| Scenario | Behavior |
|---|---|
| Same HTML content, re-publish | Checksum match → PDF reused, no generation cost |
| Content changed | New PDF generated, old PDF preserved |
| R2 unavailable | Error surfaced to user via toast |
| Large HTML | xhtml2pdf runs in anyio thread pool, no event loop blocking |
| Presigned URL | 1-hour validity; re-requesting preview generates a fresh URL for the same stored PDF |

---

## Testing Results

### Verified
- ✅ `from app.models.pdf_release import PdfRelease` imports successfully
- ✅ `from app.services.pdf_release import pdf_release_service` imports successfully  
- ✅ `xhtml2pdf` generates PDF bytes correctly (test: 1761 bytes for `<h1>Test</h1>`)
- ✅ Backend committed and pushed: `8330bdb`
- ✅ Frontend committed and pushed: `25a476e`

### Manual Verification Steps
1. Open an Approved report in the review page.
2. Click **"Publish Report Instantly"** → button shows "Preparing PDF Preview…" spinner.
3. Modal appears with embedded PDF and metadata.
4. "Newly Generated" badge appears (PDF v1 for new report).
5. Click **Cancel** → modal closes, report status unchanged.
6. Click **"Publish Report Instantly"** again → modal reappears with "Reused · No Changes" badge.
7. Click **Publish** in modal → existing GateX publish pipeline executes → status → Published.
8. Verify unpublish flow works identically to before.

---

## Future Enhancements

- **PDF History API**: `GET /api/v1/pdf-release/{report_id}/history` returning all versions
- **Rollback**: Allow re-publishing a prior PDF version from history
- **WeasyPrint upgrade**: Replace xhtml2pdf with WeasyPrint for higher fidelity (tables, images, charts) once libpango is confirmed available on Render
- **Background pre-generation**: Pre-generate PDF when report moves to "Approved" state so the preview appears instantly with no wait
- **PDF Diff viewer**: Side-by-side comparison of current vs previous PDF version
