# Enterprise Version Management

## Architecture Overview

Phase 7 of the platform introduces a fully-fledged **Enterprise Version Management** system. It pivots the document architecture from simple mutable rows into an append-only, fully auditable snapshot engine capable of Rollbacks, Node-Level Restores, and Cross-Version diffing.

This ensures that every human edit, AI regeneration, and systematic update is perfectly preserved and reproducible.

---

## 1. Version Hierarchy

*   **Document Versioning**: At the root level, `DocumentVersion` represents the immutable snapshot. It maintains `version_number`, `parent_version` (to trace lineage), and the `checksum` representing exact canonical contents.
*   **Node Versioning**: Instead of tracking explicit versions inside paragraphs, we implement **Structural Version Mapping**. When a new version is minted, the `VersionManager` deeply clones unchanged sections and blocks, pointing them to the new `version_id`, while substituting the new nodes. `stable_id` anchors these across time.

---

## 2. Version Lifecycle

Every change originates from a specific `Actor` via a `DocChangeType`:
1.  **AI_GENERATION** / **AI_REGENERATION**: DeepSeek generation. Captured via `IterationHistory` tracking prompt + response.
2.  **HUMAN_EDIT**: A reviewer explicitly saving manual text modifications.
3.  **ROLLBACK**: Reverting a specific node to a past state.
4.  **RESTORE**: Deep copying a past historical document to become the new head version.
5.  **PUBLISH_RELEASE**: Creating a locked state mapped to a specific `ReleaseStatus` (Draft -> Release Candidate -> Published).

---

## 3. Snapshot Engine

Located in `app/services/snapshot.py`, the `SnapshotEngine` executes synchronously at the end of every mutating workflow:
1.  Extracts the full Canonical JSON tree from Supabase.
2.  Calculates a SHA-256 cryptographic `checksum` to ensure zero-tampering.
3.  Renders the Canonical structure into:
    *   **HTML** (For production delivery to Alibaba Cloud)
    *   **Markdown** (For AI context windows)
    *   **PDF** (Export)
4.  Uploads these immutable artifacts into **Cloudflare R2 Object Storage**.
5.  Persists the direct URLs on the `DocumentVersion`.

---

## 4. Rollback & Restore Strategies

The golden rule of the enterprise system is **Nothing is Overwritten**.

### Node Rollback
When rolling back a specific paragraph (`node_stable_id`) to a past state (`target_version_id`):
1.  A brand new `DocumentVersion` is minted with change type `ROLLBACK`.
2.  The current version's entire structural tree is duplicated.
3.  The single node is replaced with the text found in the target historical version.

### Document Restore
When restoring an entire document to `target_version_id`:
1.  A brand new `DocumentVersion` is minted with change type `RESTORE`.
2.  The tree of the `target_version` is duplicated into the new version snapshot.
3.  The system head pointer (`current_version_id`) moves to the new ID.

---

## 5. Comparison Engine

The `compare_versions` service dynamically evaluates two `DocumentVersion` trees by aligning their `stable_id` properties.
It emits a structured payload for frontend consumption:
```json
{
  "added": ["sec_3", "block_x"],
  "removed": ["block_y"],
  "modified": [{"stable_id": "block_z", "old": "text", "new": "text"}],
  "unchanged": ["sec_1"]
}
```
This enables Git-style diff viewing directly inside the platform interface.
