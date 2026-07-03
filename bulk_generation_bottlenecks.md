# Bulk Report Generation: Workflow Architecture & Bottlenecks

This document provides a detailed overview of the current report generation and review workflows, how they operate, and the specific bottlenecks that currently prevent them from being run concurrently at scale (e.g., generating 10+ reports simultaneously).

## Current Workflow Architecture

The report generation pipeline relies on two primary GitHub Actions workflows. They operate sequentially and maintain state both in the GitHub repository and external services (R2, PostgreSQL).

### 1. Generation Workflow (`generate_deep_research_v2.yml`)
This workflow is responsible for taking a raw topic and generating the full HTML thought leadership report.

**How it works:**
1. **Trigger:** Triggered via `workflow_dispatch` (manually or via API) with inputs: `topic`, `slug`, and `model`.
2. **Execution:** Runs `python -m gen_rpt.main_web` to orchestrate DeepSeek API calls, research, and HTML generation.
3. **Storage (Git):** Commits the generated HTML files directly back into the GitHub repository under the `reports_web` folder (`git add`, `git commit`, `git pull --rebase`, `git push`).
4. **Storage (Cloudflare R2):** Uploads the pure artifacts to an R2 bucket.
5. **Database Webhook:** Pings the backend API (`/api/internal/events/report-generated`) to notify that the document is ready.
6. **Trigger Review:** Uses the `gh` CLI to trigger the subsequent Review Workflow.

### 2. Review Workflow (`generate_review_v2.yml`)
This workflow acts as an automated AI editor, analyzing the generated report for quality.

**How it works:**
1. **Trigger:** Automatically invoked at the end of the Generation Workflow.
2. **Execution:** Runs Python scripts to critique the generated sections.
3. **Database Webhook:** Pings the backend to update the report status (e.g., pushing it to the `Needs Human Review` queue).

---

## The Goal: Bulk Concurrent Generation
The objective is to trigger multiple (e.g., 10) generation workflows at the exact same time, completely independently, allowing 10 reports to be researched, generated, reviewed, and published to R2 in parallel.

## Critical Bottlenecks to Concurrency

Attempting to run the current architecture concurrently will fail or bottleneck due to the following three structural issues:

### Bottleneck 1: The Sequential Concurrency Queue
Both workflows currently enforce a strict sequential queue via their YAML configurations:

```yaml
concurrency:
  group: gen-rpt-v2-${{ github.ref }}
  cancel-in-progress: false
```

> [!WARNING]  
> Because the group relies on `github.ref` (the branch name, e.g., `main`), GitHub Actions forces all runs on `main` to share the same concurrency lock. If 10 workflows are triggered, **1 will run while the other 9 wait in a "Pending" queue**, executing one-by-one.

**Solution:** The concurrency group must be made unique per report (e.g., scoping it to the `slug` or `run_id`). 

### Bottleneck 2: Git Push Race Conditions (Stateful Repository)
The Generation Workflow treats the GitHub repository as a database by committing generated HTML files back to it.

> [!CAUTION]  
> If Concurrency Bottleneck 1 is removed and 10 runners operate simultaneously, they will all attempt to run `git push` at the same time. This will result in **remote rejected** merge conflicts because the `HEAD` of the branch will constantly be shifting. Multiple workflows will crash.

**Solution:** The workflow must become "stateless." Since reports are already safely uploaded to Cloudflare R2 and tracked in your PostgreSQL backend, the `git commit / git push` steps must be completely removed from the GitHub Action. R2 should be the sole source of truth for generated artifacts.

### Bottleneck 3: Uncoordinated Rate Limits
When generating one report, rate limits are rarely an issue. However, 10 independent GitHub Action servers slamming the DeepSeek API simultaneously lack a centralized rate limiter.

> [!TIP]  
> Unlike the local MQ script (`pipeline.py`) which uses an Adaptive Limiter to back off when DeepSeek's servers are busy, 10 independent runners do not know about each other. If DeepSeek returns a `429 Too Many Requests`, the individual runners must have highly robust exponential backoff and retry logic in `gen_rpt.main_web`, otherwise the concurrent workflows will fail.

**Solution:** Ensure the Python scripts executed by the GitHub Actions have aggressive retry logic, or migrate the bulk generation orchestration away from GitHub Actions entirely and use a centralized backend worker queue (like the provided `deepseek concurrent mqs` architecture) for massive scale.

---

## Summary of Required Fixes for Bulk Execution

If you wish to retain GitHub Actions for bulk generation, the following code changes must be applied to the `.github/workflows/*.yml` files:

1. **Update `concurrency` groups:** Change `gen-rpt-v2-${{ github.ref }}` to `gen-rpt-v2-${{ inputs.slug || github.run_id }}`.
2. **Remove Git Commits:** Delete the `Commit generated report back to repo` step.
3. **Verify Retries:** Ensure the Python generation code handles `429` API errors gracefully.
