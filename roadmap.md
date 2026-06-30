# Report Management Platform V2 - Development Roadmap

## [ ] Phase 1 — Backend Foundation
**Objective:** Introduce a dedicated backend without affecting the existing frontend or report generation pipeline.

### Scope
- [-] Build FastAPI backend foundation.
- [-] Configure project architecture and API structure.
- [-] Integrate Supabase project.
- [-] Integrate Cloudflare R2 access.
- [-] Configure authentication foundation.
- [-] Configure logging and monitoring.
- [-] Configure environment management.
- [-] Create internal service layer.
- [-] Create API documentation.
- [-] Prepare deployment environment.


---

## [ ] Phase 2 — Database & System of Record
**Objective:** Replace `catalog.json` with a relational database while keeping R2 as object storage.

### Scope
- [-] Design complete Supabase database schema.
- [-] Create report metadata tables.
- [-] Create report version tables.
- [-] Create AI review tables.
- [-] Create human review tables.
- [-] Create reviewer assignment tables.
- [-] Create workflow state tables.
- [-] Create audit log tables.
- [-] Create comments and discussion tables.
- [-] Create report tag system.
- [-] Create publish queue tables.
- [-] Create generation job tracking.
- [-] Create activity history.
- [-] Create notification tables.
- [-] Create user and role management.

*Note: After this phase, Supabase becomes the **single source of truth**.*

---

## [ ] Phase 3 — Storage Architecture
**Objective:** Convert Cloudflare R2 into a pure object storage layer.

### Scope
- [-] Store report files.
- [-] Store HTML files.
- [-] Store Markdown files.
- [-] Store PDF files.
- [-] Store review files.
- [-] Store exported artifacts.
- [-] Store generated assets.
- [-] Store version snapshots.
- [-] Store attachments.
- [-] Remove dependency on catalog.json.
- [-] Link every object with database records.

---

## [ ] Phase 4 — Backend APIs
**Objective:** Replace direct frontend R2 access with backend APIs.

### Scope
- [-] Reports APIs.
- [-] Review APIs.
- [-] Comments APIs.
- [-] Workflow APIs.
- [-] Assignment APIs.
- [-] Version APIs.
- [-] Publishing APIs.
- [-] Search APIs.
- [-] Dashboard APIs.
- [-] Statistics APIs.
- [-] Internal synchronization APIs.
- [-] Signed R2 URL generation.
- [-] Authentication APIs.
- [-] Authorization layer.

*Note: Frontend functionality remains unchanged.*

---

## [x] Phase 5 — Workflow Migration
**Objective:** Move workflow state management from GitHub Actions to the backend.

### Scope
- [x] GitHub Actions upload files only.
- [x] Backend receives synchronization events.
- [x] Backend creates report records.
- [x] Backend creates review records.
- [x] Backend updates workflow state.
- [x] Backend updates audit logs.
- [x] Backend manages assignments.
- [x] Backend manages report lifecycle.
- [x] Backend manages notifications.
- [x] Backend becomes workflow orchestrator.

---

## [x] Phase 6 — Canonical Document Engine
**Objective:** Replace Markdown as the primary editable source.

### Scope
- [x] Introduce canonical document model.
- [x] Structured document storage.
- [x] Section hierarchy.
- [x] Paragraph hierarchy.
- [x] Table hierarchy.
- [x] Figure hierarchy.
- [x] Citation hierarchy.
- [x] Metadata hierarchy.
- [x] Automatic Markdown generation.
- [x] Automatic HTML generation.
- [x] Automatic PDF generation.
- [ ] Automatic export generation.

*Note: The document model becomes the only editable source.*

---

## [x] Phase 7 — Enterprise Version Management
**Objective:** Maintain complete history.

### Scope
- [x] Document versioning.
- [x] Node history.
- [x] AI revision history.
- [x] Human revision history.
- [x] Version comparison.
- [x] Rollback support.
- [x] Snapshot generation.
- [x] Restore previous versions.
- [x] Version metadata.
- [x] Change history.
- [x] Release versions.

*Note: No report is ever overwritten.*

---

## [ ] Phase 8 — Human Review System
**Objective:** Build complete Human-in-the-Loop workflow.

### Scope
- [-] Reviewer assignments.
- [-] Review queues.
- [-] Comments.
- [-] Threaded discussions.
- [-] Approve.
- [-] Reject.
- [-] Needs Revision.
- [-] Draft review.
- [-] Save progress.
- [-] Review completion.
- [-] Reviewer history.
- [-] Multiple reviewers.
- [-] Review ownership.

---

## [x] Phase 9 — Enterprise Document Editing Studio
**Objective:** Create a structured visual editing environment mapped to the canonical model.

### Scope
- [x] Node locking.
- [x] Edit history.
- [x] Read mode.
- [x] Review mode.
- [x] Edit mode.
- [x] Section editing.
- [x] Paragraph editing.
- [x] Table editing.
- [x] Image editing.
- [x] Citation editing.
- [x] Metadata editing.
- [x] Rich text editing.
- [x] Autosave.
- [x] Draft mode.
- [x] Preview mode.

*Note: Editing updates the canonical document only.*

---

## [x] Phase 10 — AI Assisted Document Intelligence
**Objective:** Integrate AI into the editing workflow.

### Scope
- [x] Rewrite paragraph.
- [x] Rewrite section.
- [x] Expand content.
- [x] Shorten content.
- [x] Executive rewrite.
- [x] Technical rewrite.
- [x] Improve readability.
- [x] Improve grammar.
- [x] Improve citations.
- [x] Improve recommendations.
- [x] AI suggestions.
- [x] Accept / Reject suggestions.

---

## [x] Phase 11 — Partial Report Regeneration
**Objective:** Regenerate only selected content instead of the entire report.

### Scope
- [x] Regenerate paragraph.
- [x] Regenerate section.
- [x] Regenerate chapter.
- [x] Regenerate executive summary.
- [x] Regenerate conclusion.
- [x] Regenerate tables.
- [x] Regenerate recommendations.
- [x] Regenerate references.
- [x] Context-aware regeneration.
- [x] Preserve unaffected content.
- [x] Create new document version after regeneration.

---

## [x] Phase 12 — HTML Synchronization Engine
**Objective:** Keep all output formats synchronized automatically.

### Scope
- [x] Human edits update canonical document.
- [x] AI edits update canonical document.
- [x] Partial regeneration updates canonical document.
- [x] Automatically regenerate Markdown.
- [x] Automatically regenerate HTML.
- [x] Automatically regenerate PDF.
- [x] Automatically regenerate exports.
- [x] Keep every format synchronized.
- [x] Prevent format divergence.
- [x] Maintain output consistency.

*Note: HTML is never edited as the source of truth.*

---

## [x] Phase 13 — Change Tracking & Collaboration
**Objective:** Introduce collaborative editing and auditing.

### Scope
- [x] Track changes.
- [x] Suggested edits.
- [x] Accept changes.
- [x] Reject changes.
- [x] Diff viewer.
- [x] Paragraph comparison.
- [x] Version comparison.
- [x] Reviewer attribution.
- [x] AI attribution.
- [x] Timeline view.
- [x] Activity history.
- [x] Collaborative editing.

---

## [ ] Phase 14 — Publishing Workflow
**Objective:** Build enterprise publishing pipeline.

### Scope
- [ ] Publish queue.
- [ ] Approval workflow.
- [ ] Final validation.
- [ ] Alibaba OSS publishing.
- [ ] Publication history.
- [ ] Publication rollback.
- [ ] Scheduled publishing.
- [ ] Publish audit logs.
- [ ] Distribution tracking.

---

## [ ] Phase 15 — Enterprise Features
**Objective:** Complete the platform.

### Scope
- [ ] Authentication.
- [ ] Role-based access.
- [ ] Team management.
- [ ] Notifications.
- [ ] Search.
- [ ] Analytics.
- [ ] Dashboard metrics.
- [ ] Report templates.
- [ ] Organization settings.
- [ ] API keys.
- [ ] Webhooks.
- [ ] Backup & recovery.
- [ ] Monitoring.
- [ ] Health dashboards.

---

## Final Platform Vision
The completed system will consist of:
- FastAPI Backend
- Supabase PostgreSQL (System of Record)
- Cloudflare R2 (Object Storage)
- GitHub Actions (AI Compute Pipeline)
- React Frontend
- Canonical Document Engine
- AI Review Engine
- Human Review Platform
- Interactive Document Editor
- AI Assisted Editing
- Partial Report Regeneration
- Automatic HTML / Markdown / PDF Synchronization
- Version Management
- Audit & Change Tracking
- Enterprise Publishing Pipeline
- Complete Report Lifecycle Management
