# Enterprise Document Editing Studio

## Architecture Overview

Phase 9 introduces the **Enterprise Document Editing Studio**, a backend architecture designed to support a completely visual editing experience over a heavily structured Canonical Document.

It guarantees that users never directly corrupt raw HTML payloads, but rather issue granular edits against deeply versioned *Document Nodes*.

---

## 1. Node-Locked Editing Model

Concurrent editing is managed strictly through explicit node locks. 
- When an editor clicks a paragraph, a lock is acquired on the `node_stable_id` via `NodeLock`.
- The lock prevents any other reviewer or AI task from modifying the block.
- Locks carry configurable TTLs and can be aggressively reaped if abandoned.

---

## 2. Draft Isolation Sessions

The core philosophy dictates that *nothing is ever overwritten* and *production is never directly altered*.
To support live visual editing:
- `start_draft_session` forks a new `DocumentVersion` dynamically (assigning it `ReleaseStatus.Draft`).
- This version is invisible to public consumers or publication systems.
- Editors mutate this draft over hours or days.
- When they hit `Commit`, the draft transitions to the `Internal_Review` state and becomes the new `current_version_id` for the Document.

---

## 3. Asynchronous Autosave

To prevent data loss, the editor uses incremental `PUT /nodes/{node_id}/autosave`.
- Unlike massive monolithic saves, the autosave exclusively targets a specific `DocumentBlock` or `DocumentSection`.
- Every autosave explicitly captures the delta into the `NodeEditHistory` ledger.
- The `NodeEditHistory` acts as a complete audit trail tracking the `old_value`, `new_value`, `editor_id`, and whether it was `Human` or `AI`.

---

## 4. Immediate AI Tooling Integration

The AI capabilities required by the UI (e.g., Rewrite, Expand, Condense, Professional Tone) are fundamentally supported via the exact same Node-Level architecture.
- Requesting an AI rewrite targets a block and generates an LLM payload locally.
- The backend replaces the Block's `markdown` directly.
- The action logs an `EditorActionType.AI` event into the `NodeEditHistory`.
- The user can instantly hit "Undo", which simply rewinds the draft block by pulling the `old_value` from the `NodeEditHistory`.

---

## 5. HTML Synchronization Pipeline

At the conclusion of the editing cycle, the system must synchronize all states:
1.  **Draft Commits**: The user accepts their session.
2.  **Snapshotting**: The system triggers `SnapshotEngine.generate_snapshot()`.
3.  **Rendering**: The new Canonical tree (containing all node edits) is synchronously flushed into flat Markdown, validated HTML, and a PDF format.
4.  **Distribution**: The generated HTML artifacts are uploaded to Cloudflare R2, instantly reflecting the new changes on the production environment.

*(Real-time preview modes simply pull from the Draft version blocks and render HTML on the fly in memory, completely bypassing R2 until final commit).*
