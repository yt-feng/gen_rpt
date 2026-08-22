# VPS Production Migration Report: Dev to Prod Shift

**Document Version**: 1.0.0  
**Target Environment**: Production VPS Shared Docker Infrastructure  
**System Scope**: `gen_rpt-main` (Backend Orchestration), `report-management-backend` (FastAPI), `gen_rpt_review-frontend-main` (Review UI), and `gatex` (Ingestion API Integration)  

---

## 1. Executive Summary

This report establishes the migration feasibility, technical architecture, data persistence strategy, and operational risk evaluation for transitioning the AI Report Generation and Management System from the **Development VPS** to the **Production VPS Environment**.

The system currently runs on a development VPS host (`207.148.75.21`), where the FastAPI backend service (`gen_rpt_backend`) operates inside a Docker container bound to `127.0.0.1:9000`. The target **Production VPS** utilizes a shared Docker container network topology (`gatex_prod_network`), enabling multiple microservices to coexist under an isolated containerized environment without code changes or API signature breaking modifications.

### Key Migration Objectives
1. **Zero-Code Mutation Guarantee**: Shift production workloads without modifying core application source code, API contracts, or pipeline logic.
2. **Shared Docker Container Topology**: Integrate `gen_rpt_backend` into the production VPS shared container network alongside existing production services.
3. **Data & Asset Integrity**: Maintain continuous persistence across PostgreSQL (`pgvector` enabled), Cloudflare R2 / Object Storage Service (OSS), and Playwright PDF rendering pipelines.
4. **Sub-Second API Responsiveness**: Maintain container health check benchmarks (`GET /health` <= 600ms response time).

---

## 2. Infrastructure Comparison: Dev vs. Production VPS

| Architecture Dimension | Current Development VPS (`207.148.75.21`) | Target Production VPS (`rpt-api.gatex.ae`) |
| :--- | :--- | :--- |
| **Container Engine** | Standalone Docker / Docker Compose | Shared Multi-Tenant Docker Swarm / Compose Network |
| **Host Port Mapping** | `127.0.0.1:9000:8000` | Isolated Loopback `127.0.0.1:9000:8000` or Shared Gateway Route |
| **Reverse Proxy** | Direct Uvicorn / Local Nginx Proxy | Production Nginx / Traefik Gateway with TLS 1.3 Termination |
| **Database Instance** | Development PostgreSQL + `pgvector` | Dedicated Production PostgreSQL + `pgvector` Cluster |
| **Object Storage (OSS)** | Cloudflare R2 Dev Bucket (`gen-rpt-dev`) | Cloudflare R2 Production Bucket (`gen-rpt-prod`) |
| **LLM Inference Gateway** | OpenRouter / DeepSeek API (Dev Key) | OpenRouter / DeepSeek API (Production Enterprise Tier) |
| **GateX Integration** | Sandbox API Ingestion Point | Production GateX API Ingestion (`https://gatex.ae/api`) |
| **SSL / TLS Certificate** | Self-Signed / Let's Encrypt Staging | Automated Certbot TLS 1.3 Production Certificate |

---

## 3. System Architecture & Dependency Topology

```mermaid
graph TD
    Client[Client / Web Browser / GateX Client] -->|HTTPS TLS 1.3| Nginx[Production Nginx / Traefik Reverse Proxy]
    Nginx -->|HTTP 127.0.0.1:9000| DockerContainer["FastAPI Backend Container (gen_rpt_backend)"]
    
    subgraph Shared Docker VPS Infrastructure
        DockerContainer -->|Database Queries| Postgres[PostgreSQL + pgvector DB]
        DockerContainer -->|Cache / Queue| Redis[Redis Service]
        DockerContainer -->|PDF Generation| Playwright[Playwright Headless Renderer]
    end

    subgraph External Cloud Services
        DockerContainer -->|S3 API / SigV4| CloudflareR2[Cloudflare R2 Storage (HTML, PDF, WebP)]
        DockerContainer -->|HTTPS REST| OpenRouter[OpenRouter / DeepSeek AI Gateway]
        DockerContainer -->|REST Ingestion| GateXAPI[GateX Production Ingestion API]
    end
```

### Architectural Integrity Analysis
- **Stateless Execution Core**: The FastAPI application and `WebReportPipeline` are stateless. Report generation tasks fetch context dynamically from PostgreSQL/R2, synthesize artifacts, and store outputs back to object storage.
- **Headless Renderer Isolation**: Chromium Playwright binary dependencies remain packaged inside the Docker container image (`report-management-backend/Dockerfile`), preventing host OS dependency collisions.
- **Alembic Database Migrations**: Schema evolution is fully automated via `alembic upgrade head` upon container launch (`docker-compose.prod.yml`).

---

## 4. Shared Docker Container & Security Isolation Model

Moving to the production VPS involves deploying `gen_rpt_backend` as a managed service within a shared Docker daemon.

### Security & Resource Isolation Strategy
1. **Network Boundary**: The container exposes only internal port `8000`. Public traffic reaches the service solely through the loopback-bound reverse proxy (`127.0.0.1:9000`), blocking direct external port exposure.
2. **Container Network**: `gen_rpt_backend` joins a custom bridge network (`gatex_network`) to communicate securely with PostgreSQL and Redis containers via internal service names.
3. **Environment Security**: Sensitive secrets (`JWT_SECRET`, `POSTGRES_PASSWORD`, `R2_SECRET_ACCESS_KEY`, `OPENROUTER_API_KEY`) are managed via isolated `.env` files with strict `600` file permissions.
4. **Health Check Monitoring**: Automated container health checks poll `http://localhost:8000/health` every 30 seconds to trigger automatic restarts if a memory or deadlock failure occurs.

---

## 5. Data Persistence & Asset Storage Strategy

### Relational & Vector Data (`PostgreSQL + pgvector`)
- **Schema Control**: Database tables (`reports`, `users`, `pdf_releases`, `knowledge_documents`, `knowledge_chunks`, `knowledge_collections`) are maintained via standard SQLAlchemy models and Alembic migrations.
- **Data Migration**: Baseline database records (users, collections, historical report indices) are dumped from Dev PostgreSQL using `pg_dump -Fc` and restored to Prod PostgreSQL using `pg_restore`.

### Object Storage (`Cloudflare R2 / AWS S3 Compliant`)
- **Immutable Artifact Storage**: Generated HTML reports, raw Markdown files, SVG/WebP exhibits, and release PDFs are stored in R2 buckets.
- **Presigned Media Assets**: S3 SigV4 presigned URLs ensure temporary, authenticated access (24-hour expiration for cover images, 1-hour expiration for release PDFs).

---

## 6. Risk Assessment & Mitigation Matrix

| Identified Risk | Severity | Impact Area | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **Port Conflict on Shared VPS** | Medium | Host Networking | Bind backend loopback port explicitly (e.g. `127.0.0.1:9000:8000` or custom unassigned port `127.0.0.1:9001`). |
| **Playwright PDF Render Timeout** | Medium | Resource Limits | Ensure Docker container limits allocate at least 2 CPU cores and 4GB RAM to prevent Chromium rendering timeouts. |
| **CORS Access Block on Production Domain** | High | Frontend Review UI | Configure `BACKEND_CORS_ORIGINS` in production `.env` to include production frontend domain (`https://rpt.gatex.ae`). |
| **R2 Presigned Asset Expiration** | Low | Media Loading | Preserve standard SigV4 presign utility (`publish_orchestrator.py` & `pdf_release.py`) to refresh media links dynamically. |
| **Database Vector Extension Missing** | High | RAG Retrieval | Ensure `CREATE EXTENSION IF NOT EXISTS vector;` is executed on production PostgreSQL before running migrations. |

---

## 7. Migration Acceptance & Verification Benchmarks

Before authorizing full cutover, the production deployment must satisfy the following technical benchmarks:

1. **Container Health Gate**: `GET http://127.0.0.1:9000/health` returns `200 OK` with `"database": {"status": "healthy"}` and `"storage": {"status": "healthy"}`.
2. **Pytest Integration Gate**: Execution of non-destructive backend test suites (`test_report_publication_contract.py`, `test_gatex_bulk_push_and_block.py`, `test_pdf_release_output.py`) achieves 100% passing status.
3. **Playwright PDF Generation Gate**: `PdfReleaseService.create_release()` generates a valid 100% sanitized PDF without leaks or internal provenance tags.
4. **GateX Ingestion Gate**: Test payload pre-validation (`validate_payload()`) passes contract constraints and issues idempotent API calls successfully.
