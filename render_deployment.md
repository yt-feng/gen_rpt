# Render Deployment Guide

**Service:** Report Management Backend  
**Runtime:** Python 3.12  
**Framework:** FastAPI + Uvicorn  
**Database:** Supabase (PostgreSQL + asyncpg)  
**Storage:** Cloudflare R2 (S3-compatible)

---

## Step 1 — Connect GitHub Repository

1. Go to [render.com](https://render.com) → New → Web Service
2. Connect to your GitHub account
3. Select the `gen_rpt` repository
4. Choose branch: `new_arc`

---

## Step 2 — Build & Start Configuration

| Setting | Value |
|---|---|
| **Root Directory** | `report-management-backend` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Health Check Path** | `/health` |

> [!IMPORTANT]
> Render injects `$PORT` automatically. Do NOT hardcode a port number in the start command.

---

## Step 3 — Required Environment Variables

Set **all** of the following in Render Dashboard → Environment → Environment Variables:

| Variable | Required | Example Value | Notes |
|---|---|---|---|
| `DATABASE_URL` | ✅ **CRITICAL** | `postgresql+asyncpg://postgres:PASSWORD@db.REF.supabase.co:5432/postgres` | Must use `postgresql+asyncpg://` prefix |
| `JWT_SECRET` | ✅ Required | Random 32+ char string | Generate with: `openssl rand -hex 32` |
| `R2_ACCOUNT_ID` | ✅ Required | `abc123...` | From Cloudflare dashboard |
| `R2_ACCESS_KEY_ID` | ✅ Required | `abc...` | R2 API token |
| `R2_SECRET_ACCESS_KEY` | ✅ Required | `xyz...` | R2 API token secret |
| `R2_BUCKET` | ✅ Required | `gen-rpt-reports` | Your R2 bucket name |
| `APP_ENV` | Recommended | `production` | Controls debug mode |
| `APP_DEBUG` | Recommended | `false` | Set to false in production |
| `CORS_ORIGINS` | Recommended | `https://yoursite.pages.dev` | Your Cloudflare Pages domain |
| `SUPABASE_URL` | Optional | `https://REF.supabase.co` | For Supabase client SDK |
| `SUPABASE_SERVICE_ROLE_KEY` | Optional | `eyJ...` | For Supabase admin operations |
| `LOG_LEVEL` | Optional | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `JWT_ALGORITHM` | Optional | `HS256` | Default: HS256 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional | `60` | Token expiry in minutes |

> [!CAUTION]
> `DATABASE_URL` **must** use the `postgresql+asyncpg://` driver prefix. The standard `postgresql://` prefix will fail because the backend uses async SQLAlchemy.
>
> Supabase Connection String format:  
> `postgresql+asyncpg://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres`

---

## Step 4 — First Deploy

1. Click **"Manual Deploy"** → **"Deploy latest commit"**
2. Watch the build log — it should show:
   ```
   Successfully installed fastapi uvicorn sqlalchemy alembic ...
   ```
3. Watch the service log — you should see:
   ```
   Starting Report Management Backend [production]
   DATABASE_URL configured: YES
   Database connection: OK
   Cloudflare R2 connection: OK
   Startup complete. Ready to serve requests.
   ```

---

## Step 5 — Run Database Migrations

After the service is running, open the Render **Shell** tab and run:

```bash
alembic upgrade head
```

This applies all 7 migration scripts to your Supabase database. Only needs to run once (or after each schema change).

---

## Step 6 — Verification

| Check | URL | Expected Response |
|---|---|---|
| Health check | `GET /health` | `{"status": "healthy"}` |
| Swagger UI | `GET /api/v1/docs` | Interactive API documentation |
| OpenAPI JSON | `GET /api/v1/openapi.json` | Full OpenAPI 3.0 spec |

---

## Troubleshooting

### `ValidationError: DATABASE_URL is required but not set`
**Cause:** `DATABASE_URL` env var is missing or empty in Render dashboard.  
**Fix:** Go to Render → Environment Variables → Add `DATABASE_URL` with the full connection string.

### `Database connection FAILED at startup`
**Cause:** Wrong password, host, or SSL configuration.  
**Fix:** Verify the Supabase connection string. Ensure you're using the **Transaction Mode** or **Direct Connection** URL from Supabase → Project Settings → Database.

### `Cloudflare R2 connection: degraded`
**Cause:** R2 credentials missing or incorrect.  
**Fix:** Verify all four R2 variables: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.

### `Build fails: pip install error`
**Cause:** Dependency version conflict or missing system package.  
**Fix:** The Dockerfile lists required system deps (`gcc`, `libpq-dev`). Ensure the Render Python runtime includes these, or switch to Docker-based deployment.

### Port binding issue
**Cause:** Hardcoded port instead of `$PORT`.  
**Fix:** Start command must be `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

---

## render.yaml Reference

The `render.yaml` file in the project root codifies the full service definition for automated deployments.
