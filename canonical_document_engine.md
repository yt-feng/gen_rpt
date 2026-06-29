# Canonical Document Engine & Iteration Pipeline

## Architecture Overview

Phase 6 introduces the Canonical Document Model. The backend database (Supabase) is now the sole **System of Record** for all editable content. HTML, Markdown, and PDF are now strictly derived artifacts that are automatically regenerated and synchronized on every accepted change.

This shift enables powerful block-level granular iteration without the risk of destroying manual human edits in other sections of the document.

### Node Hierarchy & Stable IDs

Every structural piece of a report is mapped into relational database tables:
* **`DocumentVersion`**: Represents a snapshot of the document in time.
* **`DocumentSection`**: A major chapter (e.g., Executive Summary, Market). Each receives a **`stable_id`** that transcends versions.
* **`DocumentBlock`**: Granular nodes (Paragraphs, Tables, Charts, Citations). Each receives a **`stable_id`**.

> [!IMPORTANT]
> The `stable_id` is the anchor for all future iterations. Instead of targeting "Paragraph 3", the system always targets `block_492ac`. If a new paragraph is inserted before it, the `stable_id` mapping naturally preserves identity without breaking references.

### Block-Level Regeneration (Iteration Engine)

The `IterationEngine` facilitates context-aware AI regeneration at the block level:
1. **Context Extraction**: It looks up the `stable_id` in the current version and pulls the preceding and succeeding blocks.
2. **AI Refinement**: DeepSeek/Groq generates a new version of the specific block based on reviewer instructions.
3. **Version Isolation (`VersionManager`)**: A brand new `DocumentVersion` is minted. The system copies all *unaffected* Sections and Blocks from the parent version (linking them to the new version), and swaps in the newly generated block.
4. **History Tracking**: The `IterationHistory` table strictly records what triggered the new version (AI vs. Human), the prior content, the instruction/prompt, and the execution time.

### Rendering Pipeline (Synchronization)

Once the `IterationEngine` generates a new `DocumentVersion`, it synchronously triggers the `RenderingPipeline`:
1. **HTML Rendering**: Stitches the blocks together into production-ready semantic HTML, appending `id={stable_id}` to elements for frontend anchoring.
2. **Quality Validation**: Automatically checks the generated HTML for broken anchors, missing ARIA tags, and structural integrity.
3. **Markdown Rendering**: Flattens the blocks back down into an AI-friendly markdown representation.
4. **Export Engine**: (Future hook) Will convert the valid HTML to PDF using a headless renderer.
5. **Storage Sync**: The immutable snapshot of these artifacts is immediately uploaded to Cloudflare R2 object storage.

## Future Extensibility

* **Collaborative Editing**: Because blocks are isolated, we can implement Operational Transformation (OT) or CRDTs for real-time collaboration.
* **Frontend Integration**: The frontend can fetch `GET /documents/{id}/canonical` and render a Block-based editor (similar to Notion), allowing reviewers to click directly on a paragraph to invoke the AI iteration webhook.
