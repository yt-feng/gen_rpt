# Phase 13: Change Tracking & Collaboration Platform

## 1. Architecture
The Collaboration Platform transforms the system into an enterprise-grade document system built around the Canonical Document Model. All actions are handled via immutable operations that map to Canonical Node IDs. Users interact with the rendered HTML interface and suggest edits, which create pending proposals rather than immediately modifying the document.

The Canonical Document remains the strict single source of truth, and no HTML is edited directly.

## 2. Operation Model
Every edit is represented as an immutable operation to ensure a permanent audit history. Operations include:
- `INSERT_NODE`
- `DELETE_NODE`
- `UPDATE_NODE`
- `MOVE_NODE`
- `MERGE_NODE`
- `SPLIT_NODE`
- `UPDATE_TABLE` / `UPDATE_IMAGE` / `UPDATE_CITATION` / `UPDATE_METADATA`
- `AI_REWRITE` / `AI_EXPAND` / `AI_SUMMARIZE` / `AI_EXECUTIVE_REWRITE`
- `ROLLBACK` / `RESTORE`

## 3. Change Tracking Engine
The engine tracks insertions, deletions, modifications, moves, and formatting/metadata changes. Every tracked change references stable Canonical Node IDs to maintain structural integrity across versions.

## 4. Suggestion Engine
The suggestion mode ensures edits do not immediately mutate the Canonical Document. 
- **Types of Suggestions**: Human, AI, Reviewer, Manager, Editorial.
- **Workflow Actions**: Accept, Reject, Modify Before Accept, Merge With Existing, Request Revision, Assign To Reviewer, Escalate.
- **Inline Display**: Renders inserted/deleted/modified content visually over the HTML rendering layer without mutating the canonical source until acceptance.

## 5. Diff & Comparison Engine
- **Diff Engine**: Provides comprehensive visual diffs for Documents, Chapters, Sections, Paragraphs, Tables, Figures, Citations, Metadata, Recommendations, and References.
- **Paragraph Comparison**: Side-by-side comparison between Current, Previous, AI Proposals, Human Proposals, and Published Versions (highlighting formatting, metadata, and structural differences).
- **Version Comparison**: Structured differences between any two versions (e.g., Draft vs. Published, Release vs. Release, AI Proposal vs. Current).

## 6. Attribution Strategy
Nothing is anonymous. Every accepted change explicitly records permanent attribution:
- **Reviewer Attribution**: User, Role, Timestamp, Reason, Comment, Version, Workflow State, Organization, Assignment.
- **AI Attribution**: Provider, Model, Model Version, Prompt Version, Prompt, Context Bundle, Execution Time, Token Usage, Confidence, Reviewer, Acceptance Status, Reason.

## 7. Timeline & Activity History
- **Timeline Engine**: The primary audit visualization, displaying a chronological sequence of events (e.g., Generation, AI Review, Comments, Human Edits, Rollbacks, Publish Events).
- **Activity History**: Tracks every granular event across the platform (User, AI, Backend, Workflow, Synchronization, Rendering, Publishing, DB/Storage Events) making the entire system searchable.

## 8. Collaborative Editing
Establishes the foundation for multiple editors and reviewers, maintaining distinct auditing. 
- **Features**: Section Ownership, Node Locking, Editing Sessions, Reviewer Assignments.
- **Note**: Real-time collaborative cursors (e.g., Operational Transform / CRDT) are not required for Phase 13.

## 9. Conflict Detection & Resolution
The conflict engine detects concurrent edits on the same paragraph, table, metadata, or AI/Human proposal. It also detects version conflicts and stale node locks. The engine provides merge options and resolution paths, guaranteeing that another user's work is never silently overwritten.

## 10. Audit Strategy
Every single action must generate:
- Audit Record
- Timeline Event
- Version Event
- Workflow Event
- Synchronization Event
- User/AI Event

## 11. Database Relationships
Entities required in Supabase to support this phase:
- `operations` (Tracked changes)
- `suggestions` (Pending, Accepted, Rejected)
- `reviewer_decisions` & `ai_decisions`
- `timeline_events` & `activity_events`
- `diff_records` & `comparison_results`
- `conflict_records`
- `node_locks`
- `collaboration_sessions`

## 12. Synchronization Strategy
The collaboration platform integrates tightly with Phase 12 (HTML Synchronization Engine).
Upon accepting a suggestion:
1. Update Canonical Document
2. Create New Version
3. Synchronize Markdown, HTML, PDF, Exports
4. Update Timeline, Activity History, and Audit Logs

## 13. Testing Methodology
Automated validation must cover:
- End-to-end Track Changes and Suggestion Lifecycles (Accept/Reject/Modify).
- Diffing and Comparison edge cases across nodes and versions.
- Concurrent editing scenarios testing conflict detection and data retention.
- Validation of AI and Reviewer attribution mapping.
- Rollbacks restoring accurate history and timelines.

## 14. Performance Optimizations
Strategies implemented for large reports (thousands of changes/comments):
- Incremental node-level Diffs.
- Widespread caching for comparisons and timelines.
- Efficient node-level database queries avoiding full document scans.

## 15. Future Collaboration Roadmap
(Intentionally deferred from Phase 13)
- Live collaborative cursors.
- Operational Transform (OT) / CRDT.
- WebSocket real-time synchronization.
- Video/Voice collaboration and external client portals.
