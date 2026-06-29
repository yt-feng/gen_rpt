# Workflow Orchestration Migration (Phase 5)

## Overview

Historically, the platform's workflow state and report progression were managed directly by GitHub Actions, which modified the `catalog.json` in Cloudflare R2 after finishing long-running tasks. This created several issues with race conditions, lack of a single source of truth, and unscalable concurrency control.

With **Phase 5**, the system architecture has been explicitly refactored:
1. **GitHub Actions** are now completely **Stateless Compute Workers**. They generate reports and compute AI reviews, and they push raw static output to R2 object storage.
2. **FastAPI Backend (Supabase PostgreSQL)** is the **Workflow Orchestrator** and strict **System of Record**.

## Event-Driven Architecture

When a GitHub Action finishes a compute job (like generating a report), it is no longer permitted to modify the state catalog. Instead, it fires a signed HTTP POST request (Webhook) to an internal backend endpoint:

```
[GitHub Action (Stateless)] --(HTTP POST webhook)--> [FastAPI /internal/events/*]
```

## Atomic Transactions & Pessimistic Locking

All workflow state transitions are processed in absolute atomic guarantees using the `WorkflowService`.

1. **Idempotency Checks**: Every webhook carries an `idempotency_key` (derived from the Github `run_id` and `run_attempt`). If a network retry occurs, the `ProcessedEvent` table prevents duplicate processing.
2. **Pessimistic Row-Level Locking**: The backend uses PostgreSQL `SELECT ... FOR UPDATE` (via SQLAlchemy `with_for_update()`) on the target `Document` and `WorkflowInstance`. This explicitly prevents race conditions when concurrent Webhooks attempt to modify the state of the same document simultaneously.
3. **Atomic Rollbacks**: The entire state transition, audit logging, workflow event recording, and idempotency tracking are wrapped within a single asynchronous database context (`db.begin()`). If *any* step fails (such as an invalid document ID), the entire transaction rolls back cleanly, leaving zero orphaned states.

## Testing side-by-side (V1 and V2)

During the migration, `generate_deep_research_v2.yml` and `generate_review_v2.yml` have been created to run in parallel with the legacy V1 actions. The V2 actions demonstrate the new architecture:

1. They generate their reports.
2. They upload to R2 strictly as Object Storage (using `--skip-catalog-update`).
3. They POST their completion event to the FastAPI backend webhook router.

The backend validates the token, locks the record, updates it safely in Supabase, and drops the lock.
