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
- [ ] Design complete Supabase database schema.
- [ ] Create report metadata tables.
- [ ] Create report version tables.
- [ ] Create AI review tables.
- [ ] Create human review tables.
- [ ] Create reviewer assignment tables.
- [ ] Create workflow state tables.
- [ ] Create audit log tables.
- [ ] Create comments and discussion tables.
- [ ] Create report tag system.
- [ ] Create publish queue tables.
- [ ] Create generation job tracking.
- [ ] Create activity history.
- [ ] Create notification tables.
- [ ] Create user and role management.

*Note: After this phase, Supabase becomes the **single source of truth**.*

---

## [ ] Phase 3 — Storage Architecture
**Objective:** Convert Cloudflare R2 into a pure object storage layer.

### Scope
- [ ] Store report files.
- [ ] Store HTML files.
- [ ] Store Markdown files.
- [ ] Store PDF files.
- [ ] Store review files.
- [ ] Store exported artifacts.
- [ ] Store generated assets.
- [ ] Store version snapshots.
- [ ] Store attachments.
- [ ] Remove dependency on catalog.json.
- [ ] Link every object with database records.

---

## [ ] Phase 4 — Backend APIs
**Objective:** Replace direct frontend R2 access with backend APIs.

### Scope
- [ ] Reports APIs.
- [ ] Review APIs.
- [ ] Comments APIs.
- [ ] Workflow APIs.
- [ ] Assignment APIs.
- [ ] Version APIs.
- [ ] Publishing APIs.
- [ ] Search APIs.
- [ ] Dashboard APIs.
- [ ] Statistics APIs.
- [ ] Internal synchronization APIs.
- [ ] Signed R2 URL generation.
- [ ] Authentication APIs.
- [ ] Authorization layer.

*Note: Frontend functionality remains unchanged.*

---

## [ ] Phase 5 — Workflow Migration
**Objective:** Move workflow state management from GitHub Actions to the backend.

### Scope
- [ ] GitHub Actions upload files only.
- [ ] Backend receives synchronization events.
- [ ] Backend creates report records.
- [ ] Backend creates review records.
- [ ] Backend updates workflow state.
- [ ] Backend updates audit logs.
- [ ] Backend manages assignments.
- [ ] Backend manages report lifecycle.
- [ ] Backend manages notifications.
- [ ] Backend becomes workflow orchestrator.

---

## [ ] Phase 6 — Canonical Document Engine
**Objective:** Replace Markdown as the primary editable source.

### Scope
- [ ] Introduce canonical document model.
- [ ] Structured document storage.
- [ ] Section hierarchy.
- [ ] Paragraph hierarchy.
- [ ] Table hierarchy.
- [ ] Figure hierarchy.
- [ ] Citation hierarchy.
- [ ] Metadata hierarchy.
- [ ] Automatic Markdown generation.
- [ ] Automatic HTML generation.
- [ ] Automatic PDF generation.
- [ ] Automatic export generation.

*Note: The document model becomes the only editable source.*

---

## [ ] Phase 7 — Version Management
**Objective:** Build complete document versioning.

### Scope
- [ ] Report versions.
- [ ] Section versions.
- [ ] Paragraph versions.
- [ ] AI revision history.
- [ ] Human revision history.
- [ ] Version comparison.
- [ ] Rollback support.
- [ ] Snapshot generation.
- [ ] Restore previous versions.
- [ ] Version metadata.
- [ ] Change history.
- [ ] Release versions.

*Note: No report is ever overwritten.*

---

## [ ] Phase 8 — Human Review System
**Objective:** Build complete Human-in-the-Loop workflow.

### Scope
- [ ] Reviewer assignments.
- [ ] Review queues.
- [ ] Comments.
- [ ] Threaded discussions.
- [ ] Approve.
- [ ] Reject.
- [ ] Needs Revision.
- [ ] Draft review.
- [ ] Save progress.
- [ ] Review completion.
- [ ] Reviewer history.
- [ ] Multiple reviewers.
- [ ] Review ownership.

---

## [ ] Phase 9 — Interactive Editing System
**Objective:** Allow humans to edit reports directly.

### Scope
- [ ] Visual document editor.
- [ ] Inline editing.
- [ ] Section editing.
- [ ] Paragraph editing.
- [ ] Table editing.
- [ ] Image editing.
- [ ] Citation editing.
- [ ] Metadata editing.
- [ ] Rich text editing.
- [ ] Autosave.
- [ ] Draft mode.
- [ ] Preview mode.

*Note: Editing updates the canonical document only.*

---

## [ ] Phase 10 — AI Assisted Editing
**Objective:** Integrate AI into the editing workflow.

### Scope
- [ ] Rewrite paragraph.
- [ ] Rewrite section.
- [ ] Expand content.
- [ ] Shorten content.
- [ ] Executive rewrite.
- [ ] Technical rewrite.
- [ ] Improve readability.
- [ ] Improve grammar.
- [ ] Improve citations.
- [ ] Improve recommendations.
- [ ] AI suggestions.
- [ ] Accept / Reject suggestions.

---

## [ ] Phase 11 — Partial Report Regeneration
**Objective:** Regenerate only selected content instead of the entire report.

### Scope
- [ ] Regenerate paragraph.
- [ ] Regenerate section.
- [ ] Regenerate chapter.
- [ ] Regenerate executive summary.
- [ ] Regenerate conclusion.
- [ ] Regenerate tables.
- [ ] Regenerate recommendations.
- [ ] Regenerate references.
- [ ] Context-aware regeneration.
- [ ] Preserve unaffected content.
- [ ] Create new document version after regeneration.

---

## [ ] Phase 12 — HTML Synchronization Engine
**Objective:** Keep all output formats synchronized automatically.

### Scope
- [ ] Human edits update canonical document.
- [ ] AI edits update canonical document.
- [ ] Partial regeneration updates canonical document.
- [ ] Automatically regenerate Markdown.
- [ ] Automatically regenerate HTML.
- [ ] Automatically regenerate PDF.
- [ ] Automatically regenerate exports.
- [ ] Keep every format synchronized.
- [ ] Prevent format divergence.
- [ ] Maintain output consistency.

*Note: HTML is never edited as the source of truth.*

---

## [ ] Phase 13 — Change Tracking & Collaboration
**Objective:** Introduce collaborative editing and auditing.

### Scope
- [ ] Track changes.
- [ ] Suggested edits.
- [ ] Accept changes.
- [ ] Reject changes.
- [ ] Diff viewer.
- [ ] Paragraph comparison.
- [ ] Version comparison.
- [ ] Reviewer attribution.
- [ ] AI attribution.
- [ ] Timeline view.
- [ ] Activity history.
- [ ] Collaborative editing.

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
