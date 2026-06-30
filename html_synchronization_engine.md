# Phase 12: HTML Synchronization Engine

## 1. Architecture
The HTML Synchronization Engine acts as the central hub ensuring that every document format (HTML, Markdown, PDF, Exports) flawlessly mirrors the state of the Canonical Document. The architecture enforces a strict one-way flow of data where the Canonical Document acts as the absolute single source of truth. Any edit (Human, AI, or Partial Regeneration) updates the Canonical Document, which in turn triggers a deterministic synchronization pipeline to automatically regenerate all downstream artifacts.

### Data Flow
`Human Edit / AI Edit / Partial Regeneration` → `Canonical Document Updated` → `Version Created` → `Synchronization Engine` → `Renderers (Markdown, HTML, PDF, Exports)` → `Validation` → `Store (R2)` → `Database Metadata Update`

## 2. Synchronization Pipeline
The pipeline ensures a deterministic and repeatable render order:
1. **Validate Canonical Document**
2. **Create Version**
3. **Generate Markdown**
4. **Generate HTML**
5. **Generate PDF**
6. **Generate Additional Exports**
7. **Validate Outputs**
8. **Store Artifacts**
9. **Update Database**
10. **Publish Event**

This pipeline executes automatically upon Canonical update, with no manual synchronization permitted.

## 3. Renderer Architecture
- **HTML Renderer (Primary Production Artifact):** Generates directly from the Canonical Document, independent of Markdown. Preserves typography, layout, tables, images, figures, captions, citations, footnotes, cross-references, TOC, responsive layout, metadata, anchors, and enforces semantic HTML and accessibility.
- **Markdown Renderer:** Regenerates strictly as an AI artifact for prompting, version comparison, and developer debugging.
- **PDF Renderer:** Generates exclusively from the Canonical Document (never from HTML). Preserves precise typography, page layouts, tables, figures, images, references, and metadata.
- **Export Engine:** Automatically generates and synchronizes DOCX, JSON, XML, TXT, and EPUB from the latest Canonical Version.

## 4. Validation Engine
Each rendered artifact undergoes rigorous validation prior to storage:
- **Markdown Validation:** Checks for broken headings, invalid lists, broken tables, and reference consistency.
- **HTML Validation:** Enforces semantic HTML, heading hierarchy, accessibility (ARIA), unique IDs, valid anchors and links, citation mapping, and responsiveness.
- **PDF Validation:** Validates page counts, rendering errors, fonts, tables, images, and embedded metadata.
- **Export Validation:** Verifies schema, encoding, and completeness.

## 5. Consistency Engine & Divergence Detection
The Consistency Engine guarantees that all artifacts belonging to a given synchronization cycle share the identical Document ID, Version ID, Snapshot, Timestamp, Canonical Checksum, and Rendering Manifest. 

A Format Divergence Detector constantly monitors the outputs against the Canonical Document to identify missing nodes (paragraphs, tables, figures), broken citations, incorrect metadata, or stale versions. Any divergence results in immediate rejection of the synchronization cycle.

## 6. Checksums
Cryptographic checksums are generated for the Canonical Document, Markdown, HTML, PDF, and Exports. These checksums are stored in the database and act as the primary mechanism for detecting stale outputs and verifying consistency across versions.

## 7. Failure Recovery
The synchronization pipeline is atomic. If any single renderer (e.g., PDF or HTML) fails or validation fails:
- **No partial outputs are published.**
- The system automatically keeps the previous stable version active.
- The failure is recorded in the audit logs.
- Retries are supported, ensuring the system never enters an inconsistent state.

## 8. Storage Model
- **Cloudflare R2:** Stores the physical artifacts (Canonical Snapshot, HTML, Markdown, PDF, Exports, Assets, Manifests, Checksums).
- **Supabase:** Stores all relational metadata, tracking the active versions, paths to R2 objects, and workflow states.

## 9. Database Updates
Upon successful end-to-end synchronization, Supabase is updated with:
- Current Version
- Current Paths (HTML, Markdown, PDF, Exports)
- Artifact Checksums
- Synchronization Timestamp and Status
- Render Duration
- Associated Artifact Metadata

## 10. Performance Strategy
The engine is optimized for high throughput and large documents (e.g., 500+ pages) through:
- **Incremental & Node-Level Rendering:** Updating only the specific canonical nodes affected by an edit, mapping those to partial HTML structure regenerations where feasible.
- **Renderer Caching:** Caching unmodified sections of the document to accelerate PDF and Export generation.
- **Parallel Export Generation:** Generating HTML, Markdown, PDF, and other exports simultaneously after the Canonical Document is validated.
- **Minimal Memory Usage:** Streaming large outputs directly to storage to minimize RAM overhead.

## 11. Testing Strategy
Automated testing must comprehensively cover:
- Triggers (Human Edits, AI Edits, Partial Regenerations).
- Generation of every artifact type.
- Checksum validation and Divergence Detection under load.
- **Failure Recovery Scenarios:** Intentional injection of renderer failures to verify that the database remains untouched and no partial updates leak into R2.
- **Large Report Performance:** Verifying processing times and memory thresholds on documents exceeding 500 pages.

## 12. Migration Notes
- All historical manual synchronization scripts and direct Markdown edits are deprecated.
- Existing documents must trigger a full initial synchronization pass to establish the baseline Checksums and Rendering Manifests in the database.

## 13. Future Improvements
(Intentionally deferred from Phase 12)
- Real-time synchronization and WebSocket updates.
- Client-side rendering capabilities.
- Direct Alibaba OSS publishing integration.
