# Human Review Platform

## Architecture Overview

Phase 8 introduces a **Human-in-the-Loop (HITL)** architecture built seamlessly over the Enterprise Versioning layer. It enables asynchronous queue assignment, node-level commenting, interactive threaded discussions, AI generation requests directly from the comments, and manual HTML editing, ensuring maximum collaboration and robust workflow.

---

## 1. Assignments & Review Queues

Every `Document` holds an `owner_id`. To distribute workload, the system utilizes `ReviewAssignment` entities containing:
*   `reviewer_id`: The ID of the targeted human reviewer.
*   `role`: Their persona/purpose (e.g., `primary`, `secondary`, `technical`, `editorial`, `manager`).
*   `status`: The current queue state (`pending`, `in_progress`, `completed`, `reassigned`).

When a review is assigned, the underlying canonical `Document` changes its status to `assigned`. Reviewers can view their specific queues by pulling against this object.

---

## 2. Node-Anchored Comments

Reviewers do not comment on raw HTML or random text offsets. Instead, the UI attaches comments tightly to a Canonical Document's structural `node_stable_id`. 
*   **Targeting**: `ReviewComment.node_stable_id` links directly to a `DocumentBlock` stable ID or a `DocumentSection` stable ID.
*   **Threading**: A `ReviewComment` can specify a `parent_comment_id` pointing to another comment instance, enabling unlimited deep conversation trees directly attached to the text.

---

## 3. Review Decisions & Draft States

The `HumanReview` schema acts as a mutable container representing a user's active session.
Reviewers can repeatedly execute **Draft Saves**:
1.  **Save Draft**: Updates `HumanReview.is_draft = True`. The `decision` and `summary` are stored, but they do NOT advance the global workflow. The global Document state remains `in_review`.
2.  **Complete Review**: Commits the state, marking `is_draft = False` and formally assigning a `completed_at` timestamp. Based on the `decision` type (`approved`, `needs_revision`, `rejected`), the system orchestrates the final document state transitions to `ready_for_publish` or routes it back.

---

## 4. AI-Assisted Feedback Loops

The most critical capability is requesting the Iteration Engine to automatically fix or augment a Canonical Node natively based on a comment.

When `ReviewComment.action_type == ai_request` is triggered, the backend:
1.  Validates the node reference.
2.  Creates a new clone of the document using `VersionManager.create_new_version(change_type=AI_REGENERATION)`.
3.  Feeds the requested Node text + the user's comment string into the LLM logic layer.
4.  Substitutes the Node's payload with the AI response.
5.  Triggers the `SnapshotEngine` to synchronously freeze a snapshot and push rendered markdown/HTML up to Cloudflare R2!

*(Because it targets just one Node via its stable_id, the rest of the massive document remains untouched, drastically reducing latency and review pollution.)*

---

## 5. Audit & Version Propagation

Every action is structurally audited via the Enterprise Version Management infrastructure created in Phase 7.
*   Manual HTML tweaks trigger a `HUMAN_EDIT` version.
*   AI edits trigger an `AI_REGENERATION` version.
*   Approval chains trigger `PUBLISH_RELEASE` branches.

Nothing is overwritten. Every iteration lives alongside its explicit tracking trail.
