# Generation Capability Audit

**Date:** June 30, 2026
**Component audited:** FastAPI Backend

## 1. Capability Audit Checklist
| Capability | Exists | Notes |
| :--- | :---: | :--- |
| **Create generation jobs** | ❌ No | Existing `GenerationJob` model is hardcoded for GitHub runs, but lacks creation logic, topics, prompts, or API endpoints. |
| **Execute report generation** | ❌ No | Backend currently relies fully on GitHub Actions. No internal execution logic exists. |
| **Track progress** | ❌ No | Missing status tracking or polling endpoints for workers. |
| **Track failures** | ❌ No | Existing job model has no fields for structured error tracking or failure states beyond a simple enum. |
| **Cancel jobs** | ❌ No | Missing cancellation logic or cancellation propagation to workers. |
| **Retry jobs** | ❌ No | No concept of retry limits or retry endpoints. |
| **Return generated report metadata** | ❌ No | Missing artifacts mapping logic. |
| **Return generated artifacts** | ❌ No | Cannot currently serve artifacts tied to generation jobs directly. |
| **Update Supabase** | ❌ No | Supabase updates are mocked in testing endpoints. |
| **Upload to Cloudflare R2** | ❌ No | Missing storage integration in generation lifecycle. |
| **Trigger AI Review** | ❌ No | The AI review service has no hooks in the generation pipeline. |
| **Trigger synchronization** | ❌ No | Synchronization engine has no hooks in the generation pipeline. |

## 2. Gap Analysis
The existing architecture relies on GitHub Actions as the absolute controller of report generation. The backend acts as a passive repository that receives webhook events but cannot proactively control, cancel, or orchestrate jobs.

### Missing Components
1. **Generation API Engine**: `/generation/jobs` routes.
2. **Robust Job Model**: Extension of `GenerationJob` to track prompts, models, token usage, and artifacts.
3. **Execution Backend Manager**: Pluggable interface (`WorkerInterface`) to route jobs to Local Python Workers, GitHub Actions, or future celery/Kubernetes runners.
4. **Generation Workflow Coordinator**: Background service to poll, orchestrate R2 uploads, trigger the AI review, and maintain State Machine consistency.

### Existing Code to Reuse
- `GenerationJob` model (needs schema expansion).
- `JobStatusType` enum.
- Webhook routes (can be refactored as status callbacks for the Execution Backend).
