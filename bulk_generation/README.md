# Bulk Report Generation

This folder contains the **completely separate** bulk/concurrent report generation system. It is **additive only** — it does not modify any existing workflow, Python module, or configuration file.

---

## Architecture

```
bulk_generation/
├── dispatch_bulk.py       ← The ONLY entry point for bulk generation
├── jobs.example.json      ← Example input format for the dispatcher
└── README.md              ← This file

.github/workflows/
├── generate_deep_research_v2.yml   ← ORIGINAL (untouched, production)
├── generate_review_v2.yml          ← ORIGINAL (untouched, production)
├── generate_deep_research_bulk.yml ← NEW: bulk generation (this system)
└── generate_review_bulk.yml        ← NEW: bulk review (this system)
```

---

## How to Trigger Bulk Runs

### Prerequisites
- `gh` CLI installed and authenticated (`gh auth login`)
- Run from the repo root

### Option 1: From a JSON file (recommended)
```bash
# Copy and edit the example file
cp bulk_generation/jobs.example.json bulk_generation/my_jobs.json
# ... edit my_jobs.json with your topics and slugs ...

python bulk_generation/dispatch_bulk.py --jobs bulk_generation/my_jobs.json
```

### Option 2: Inline CLI arguments
```bash
python bulk_generation/dispatch_bulk.py \
  --topic "AI in Healthcare" --slug "ai-healthcare-2026" \
  --topic "Quantum Computing" --slug "quantum-2026"
```

### Option 3: Dry run (preview without executing)
```bash
python bulk_generation/dispatch_bulk.py --jobs bulk_generation/my_jobs.json --dry-run
```

### jobs.json Format
```json
[
  {
    "topic": "AI in Healthcare: Strategic Outlook 2026",
    "slug": "ai-healthcare-2026",
    "model": "deepseek-chat"
  },
  {
    "topic": "Quantum Computing Commercialization",
    "slug": "quantum-2026"
  }
]
```
- `topic` (required): The research topic.
- `slug` (required): A **unique** identifier for this report. Used as the R2 folder name and database document ID.
- `model` (optional): DeepSeek model name. Defaults to `deepseek-chat`.

---

## Key Differences from the Single-Report System

| Feature | Original (`v2`) | Bulk (`_bulk`) |
|---|---|---|
| Concurrency group | `github.ref` (sequential per branch) | `bulk-gen-{slug}` (fully parallel) |
| Git commit/push | ✅ Yes (report HTML committed to repo) | ❌ Removed (race condition risk) |
| R2 upload | ✅ Yes | ✅ Yes (unchanged) |
| Backend webhook | ✅ Yes | ✅ Yes (unchanged) |
| Review trigger | `generate_review_v2.yml` | `generate_review_bulk.yml` |
| Trigger method | Single manual dispatch or API | `dispatch_bulk.py` dispatcher |

---

## Why No Git Commit in the Bulk Workflow?

When 10 GitHub Actions runners all finish generating at roughly the same time, they all attempt `git push` to the same branch simultaneously. This causes **merge conflicts and rejected pushes** — some workflows crash. 

Since reports are already stored in Cloudflare R2 and tracked in the PostgreSQL database (via the backend webhook), the git commit was purely redundant. Removing it makes the bulk workflow **stateless and race-condition-proof**.

---

## The Existing System Remains Unchanged

The single-report system (`generate_deep_research_v2.yml` + `generate_review_v2.yml`) is the **default production path**. Do not replace it with the bulk system. The bulk system is for batch workloads only.

> **Important:** Never call `generate_deep_research_bulk.yml` directly from the GitHub UI for production single-report use. Always use the original `v2` workflows for that.
