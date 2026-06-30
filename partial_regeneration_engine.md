# Phase 11: Partial Report Regeneration Engine

## 1. Architecture
The Partial Report Regeneration Engine introduces selective, context-aware regeneration. It ensures that only specifically targeted nodes of the Canonical Document are modified, preserving the integrity of all unaffected nodes. The engine relies heavily on the Canonical Document structure, leveraging stable Node IDs rather than fluid positioning indices. 

The core philosophy dictates that the AI generates a structured proposal rather than mutating the document directly. Once approved by a human reviewer, the canonical document is updated, which immediately cascades into the automated regeneration of HTML, Markdown, and PDF outputs.

## 2. Node Targeting
Regeneration is strictly bound to Canonical Node IDs. It is impossible to regenerate via vague identifiers (e.g., "Paragraph 2" or "HTML line 45").

Every regeneration request must explicitly define:
- **Document ID**
- **Version ID**
- **Target Node IDs**
- **Regeneration Type** (e.g., Rewrite, Expand, Condense)
- **Reviewer Instruction** (Custom directions for the LLM)
- **Priority & Reason**
- **Workflow Context**

Single nodes, multiple discrete nodes, or entire hierarchical sections (Chapters, Summaries, Tables, Captions) can be targeted, provided their Canonical Node IDs are passed.

## 3. Context Bundle
To ensure the LLM has sufficient context without rewriting the entire document, a Context Bundle is generated. This bundle is tightly scoped and includes:
- **The Target Nodes** (Current Content)
- **Hierarchical Context** (Parent Section, Previous/Next Sections, Sibling Nodes)
- **Document Context** (Metadata, Objective, Audience, Report Category, Executive Summary)
- **Review Context** (AI/Human Review Findings, Outstanding Tasks, Comments)
- **Referential Context** (Relevant Citations, Related Tables)

The full report is *never* sent unless explicitly requested, optimizing token usage and model focus.

## 4. Prompt Construction
The Prompt Builder combines the Context Bundle with the Regeneration Type and Reviewer Instruction. It enforces strict constraints instructing the LLM to return only the modified content for the targeted Node IDs, formatted in a structured proposal payload (JSON), ensuring no hallucinated structural changes occur.

## 5. Regeneration Pipeline
1. **Regeneration Request:** Triggered via UI with target nodes and instructions.
2. **Node Selection & Context Bundle:** System gathers precise node data and context.
3. **Prompt Builder & LLM:** Context is structured and sent to the LLM.
4. **Validation:** LLM output is validated against expected Node IDs and structure.
5. **Proposal:** A structured proposal is created (Previous Content vs. Proposed Content).

## 6. Proposal Lifecycle
The AI **never** updates the Canonical Document directly.
1. The AI generates a structured proposal detailing the affected Node IDs, proposed content, reasoning, confidence, and potential risks.
2. A Human Reviewer evaluates the proposal.
3. If **Rejected**: The document remains unchanged. The rejected proposal is stored for auditing.
4. If **Accepted**: The Canonical Document is updated, triggering versioning and rendering.

## 7. Validation Strategy
Before any regeneration proposal is applied, the system validates:
- **Existence:** Node and Version must exist.
- **State:** Node Lock Status and Workflow Permissions.
- **Integrity:** Citation, Metadata, Document Hierarchy, and Reference Consistency.
- **Output:** HTML Validity (Semantic HTML, Heading Hierarchy, Accessibility, Broken Links).

## 8. Version Integration
The platform maintains strict immutability. An accepted regeneration yields:
- A New Document Version
- New Node Versions (for affected nodes)
- A New Snapshot
- Audit Records & Workflow Events
The previous version is preserved and can be rolled back to at any time.

## 9. Rendering Pipeline & HTML Synchronization
After the Canonical Document is updated, the Rendering Pipeline automatically executes:
1. **HTML Regeneration:** Re-rendered from the Canonical Document. Validated for semantic correctness.
2. **Markdown Regeneration:** Kept as a synchronized AI artifact.
3. **PDF Regeneration:** Synchronized for export.
4. **Snapshot Storage:** All artifacts are stored in Cloudflare R2 and linked in Supabase.

No manual HTML editing is permitted. The Canonical Document is the absolute source of truth.

## 10. Performance Optimizations
- **Minimal Prompts:** Context bundles ensure only necessary tokens are consumed.
- **Incremental Rendering:** Only the affected nodes trigger rendering updates where possible, or the rendering pipeline leverages fast static generation.
- **Parallel Execution:** Batch regeneration requests for independent nodes are executed in parallel.
- **Caching:** Unchanged sections are cached during the HTML/PDF rendering phases.

## 11. Database Schema Additions
To support this phase, the following entities will be managed in Supabase:
- `regeneration_jobs`: Tracks Document ID, Target Nodes, Model, Prompt, Token Usage, and Execution Time.
- `ai_proposals`: Stores Proposed Content, Previous Content, Reasoning, Confidence, Risks.
- `proposal_outcomes`: Tracks Acceptance/Rejection, Reviewer ID, Reason, and mapped Version ID.

## 12. Testing Strategy
Automated testing must cover end-to-end scenarios:
- **Granular Targeting:** Regenerating a single paragraph, table, or reference without affecting sibling nodes.
- **Hierarchical Targeting:** Regenerating a full chapter or executive summary.
- **Rejection Flow:** Ensuring document state remains identical upon rejection.
- **Acceptance Flow:** Ensuring Canonical updates trigger HTML, Markdown, and PDF syncs.
- **Validation:** Ensuring broken HTML or invalid node IDs automatically fail the proposal stage.

## 13. Migration Notes
- All existing reports must have their nodes fully indexed with Canonical Node IDs before utilizing the regeneration engine.
- Existing human review workflows must be updated to support the new `ai_proposal` review type alongside standard human peer reviews.

## 14. Future Enhancements
(Intentionally deferred from Phase 11)
- Whole-document regeneration by default.
- Real-time collaborative regeneration (multi-player AI).
- Autonomous AI approvals (without human-in-the-loop).
- Automatic publishing post-regeneration.
