# Backend API Architecture

This document serves as the official API reference and architectural guide for Phase 4. FastAPI is now established as the singular backend gateway for the platform, completely isolating the frontend from direct database or object storage interactions.

## 1. Overall API Architecture
- **Framework:** FastAPI (Python 3.11+)
- **Routing Base:** All public endpoints are mounted under `/api/v1`.
- **Internal APIs:** Internal worker endpoints are strictly segregated under `/api/internal` to easily apply network-level or middleware-level firewalls.
- **Design Paradigm:** Business-capability driven. Endpoints map to user actions (e.g., `/{document_id}/publish`) rather than raw database schemas (`/documents`).

## 2. Request & Response Standards
To guarantee frontend consistency, **every** public API endpoint wraps its return data in a unified schema (`APIResponse[T]`).

### Standard Response Shape:
```json
{
  "status": "success", // or "error"
  "message": "Human readable message",
  "data": { ... }, // The actual payload (omitted on error)
  "metadata": {
    "total": 100,
    "offset": 0,
    "limit": 50,
    "has_more": true
  }, // Pagination or context (optional)
  "errors": null, // Structured error array (omitted on success)
  "request_id": "uuid-v4-string",
  "timestamp": "2026-06-29T12:00:00Z"
}
```

## 3. Error Handling Strategy
FastAPI's exception handlers are overridden (`app/core/exceptions.py`) to catch all unhandled exceptions, validation errors (422), and database errors (500), forcing them into the unified `APIResponse` format with `status="error"`.

## 4. Endpoint Catalog (Modules)
The API is split into robust namespaces mapped to business domains:
- **Auth (`/api/v1/auth`)**: Placeholders for `/login` and `/logout`.
- **Reports (`/api/v1/reports`)**: Listing reports, fetching details, listing versions.
- **Reviews (`/api/v1/reports/{id}/reviews`)**: Segregated endpoints for AI (`/ai`) and Human (`/human`) reviews.
- **Comments (`/api/v1/reports/{id}/comments`)**: Threaded discussion endpoints.
- **Workflow (`/api/v1/reports/{id}/workflow`)**: State fetching and transitions.
- **Assignments (`/api/v1/assignments`)**: Workload queues and reviewer assignments.
- **Publishing (`/api/v1/reports/{id}/publish`)**: Placeholders for Alibaba OSS publishing jobs.
- **Search (`/api/v1/search`)**: Global cross-document and metadata search.
- **Dashboard (`/api/v1/dashboard`)**: Aggregated metrics and recent activity feeds.
- **Statistics (`/api/v1/statistics`)**: System and reviewer performance tracking.
- **Internal (`/api/internal`)**: Protected routes (e.g., `/sync/catalog`) for background workers.

## 5. Security & Authentication
- **Authentication**: A dependency (`get_current_user_placeholder`) decodes JWTs (currently mocked) from the `Authorization: Bearer` header.
- **Authorization**: A generic dependency class `RoleChecker(["admin"])` enforces RBAC (Role-Based Access Control) natively at the router level.
- **Signed URLs**: The frontend **never** receives R2 bucket credentials. It must request a download URL via `/api/v1/reports/{id}/download-url`, which verifies permissions and generates a time-limited Presigned URL via the `StorageService`.

## 6. Pagination & Filtering Design
- **Dependencies**: `PageParams` and `FilterParams` (`app/api/deps.py`) are injected into list endpoints.
- **Standardization**: Enforces strict `offset` and `limit` boundaries, alongside dynamic filtering fields (`status`, `reviewer_id`, `tag`, `sort_by`).

## 7. Frontend Integration Approach
The frontend will transition from querying `https://bucket.r2.dev/catalog.json` directly to querying `GET /api/v1/reports`.
- No frontend components need to be redesigned.
- Only the data-fetching layer (services/stores) will be updated.

## 8. Testing Strategy
- Core endpoints and unified response formatting are validated via integration tests (`test_api.py`) using `httpx.AsyncClient` alongside FastAPI's `ASGITransport`.
- Tests strictly validate standard JSON formatting, 404 handler overrides, and 422 validation overrides.

## 9. Future Expansion Plan (Phase 5)
With the backend API fully established, Phase 5 will begin migrating the GitHub Actions workflow orchestration into internal APIs, fully decommissioning the legacy `catalog.json` generation pipeline while keeping existing frontend interfaces seamless.
