# VPS Production Migration Guide: Step-by-Step Operational Runbook

**Document Version**: 1.0.0  
**Target Infrastructure**: Shared Docker Production VPS (`rpt-api.gatex.ae`)  
**Scope**: Zero-code-change migration of `gen_rpt-main` and `report-management-backend`  

---

## Pre-Migration Audit Checklist

Before executing any commands on the production VPS, verify the following pre-requisites:

- [ ] Production VPS SSH Access (`ssh deploy@<PROD_VPS_IP>`)
- [ ] Docker (v24.0+) & Docker Compose (v2.20+) installed on Production VPS
- [ ] PostgreSQL (v15+) container or managed service running with `pgvector` extension installed
- [ ] Cloudflare R2 / AWS S3 Production Storage credentials (Access Key, Secret Key, Bucket Name, Endpoint)
- [ ] Production OpenRouter / DeepSeek API Keys active and funded
- [ ] Nginx / Traefik Reverse Proxy container active on shared Docker network (`gatex_network`)
- [ ] SSL Certificate domain (`rpt-api.gatex.ae`) configured in Let's Encrypt / Certbot

---

## Step 1: Shared Docker Network & Directory Setup

Connect to the Production VPS via SSH and establish the isolated project directory and shared Docker bridge network.

```bash
# 1. SSH into Production VPS
ssh deploy@<PROD_VPS_IP>

# 2. Create production application directory
sudo mkdir -p /opt/gen-rpt
sudo chown -R deploy:deploy /opt/gen-rpt
cd /opt/gen-rpt

# 3. Create or verify shared Docker network
docker network create gatex_network || true
```

---

## Step 2: Code Base Synchronization

Clone or pull the repository on the production VPS host without modifying any source files.

```bash
# Clone repository if first-time deployment
git clone https://github.com/yt-feng/gen_rpt.git /opt/gen-rpt

# Navigate to application root
cd /opt/gen-rpt

# Fetch latest production branch
git fetch origin main
git checkout main
git pull origin main

# Confirm active commit hash
git log -n 1 --oneline
```

---

## Step 3: Production Environment Variable Setup (`.env`)

Create the production environment file `report-management-backend/.env` on the VPS host.

```bash
cat << 'EOF' > /opt/gen-rpt/report-management-backend/.env
# Service Configuration
ENVIRONMENT=production
PROJECT_NAME="Report Management API"
PORT=8000
BACKEND_CORS_ORIGINS=["https://rpt.gatex.ae","https://gatex.ae","http://localhost:3000"]
JWT_SECRET="<GENERATE_SECURE_64_CHAR_HEX_STRING>"

# Database Configuration (PostgreSQL + pgvector)
POSTGRES_SERVER="postgres"
POSTGRES_PORT=5432
POSTGRES_USER="gen_rpt_prod"
POSTGRES_PASSWORD="<PRODUCTION_DB_PASSWORD>"
POSTGRES_DB="gen_rpt_prod_db"
DATABASE_URL="postgresql://gen_rpt_prod:<PRODUCTION_DB_PASSWORD>@postgres:5432/gen_rpt_prod_db"

# Cloudflare R2 / OSS Storage Configuration
STORAGE_PROVIDER="r2"
R2_ACCOUNT_ID="<PRODUCTION_R2_ACCOUNT_ID>"
R2_ACCESS_KEY_ID="<PRODUCTION_R2_ACCESS_KEY_ID>"
R2_SECRET_ACCESS_KEY="<PRODUCTION_R2_SECRET_ACCESS_KEY>"
R2_BUCKET_NAME="gen-rpt-prod"
R2_ENDPOINT_URL="https://<PRODUCTION_R2_ACCOUNT_ID>.r2.cloudflarestorage.com"
R2_PUBLIC_URL="https://pub-gen-rpt-prod.r2.dev"

# AI Gateway & LLM API Keys
OPENROUTER_API_KEY="<PRODUCTION_OPENROUTER_API_KEY>"
DEEPSEEK_API_KEY="<PRODUCTION_DEEPSEEK_API_KEY>"
OPENAI_API_KEY="<PRODUCTION_OPENAI_API_KEY>"

# GateX Ingestion Integration
GATEX_API_BASE_URL="https://gatex.ae/api"
GATEX_API_KEY="<PRODUCTION_GATEX_API_KEY>"
EOF

# Set secure permissions
chmod 600 /opt/gen-rpt/report-management-backend/.env
```

---

## Step 4: PostgreSQL Database & `pgvector` Initialization

Prepare the production database and verify `pgvector` extension readiness.

```bash
# Connect to Production PostgreSQL Container/Service
docker exec -it postgres psql -U postgres -c "CREATE DATABASE gen_rpt_prod_db;" || true
docker exec -it postgres psql -U postgres -c "CREATE USER gen_rpt_prod WITH PASSWORD '<PRODUCTION_DB_PASSWORD>';" || true
docker exec -it postgres psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE gen_rpt_prod_db TO gen_rpt_prod;" || true

# Enable pgvector extension
docker exec -it postgres psql -U postgres -d gen_rpt_prod_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

---

## Step 5: Docker Container Build & Deployment

Deploy the production container using `docker-compose.prod.yml`.

```bash
cd /opt/gen-rpt/report-management-backend

# Build backend Docker image with Playwright & Python dependencies
docker compose -f docker-compose.prod.yml build --no-cache

# Launch container in detached daemon mode
docker compose -f docker-compose.prod.yml up -d

# Verify container status
docker ps --filter "name=gen_rpt_backend"
```

---

## Step 6: Nginx Reverse Proxy & TLS Setup (`rpt-api.gatex.ae`)

Configure Nginx on the VPS to route external HTTPS traffic to `127.0.0.1:9000`.

```nginx
# Location: /etc/nginx/sites-available/rpt-api.gatex.ae.conf

server {
    listen 80;
    server_name rpt-api.gatex.ae;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name rpt-api.gatex.ae;

    ssl_certificate /etc/letsencrypt/live/rpt-api.gatex.ae/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/rpt-api.gatex.ae/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
    }
}
```

```bash
# Enable Nginx site configuration & reload
sudo ln -sf /etc/nginx/sites-available/rpt-api.gatex.ae.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 7: Verification & Health Check Procedure

Execute immediate post-deployment verification to validate full system operational readiness.

```bash
# 1. Inspect Docker Container Health Check
curl -s http://127.0.0.1:9000/health | jq .

# Expected Output:
# {
#   "status": "degraded" or "healthy",
#   "environment": "production",
#   "database": { "status": "healthy" },
#   "storage": { "status": "healthy" }
# }

# 2. Test Public HTTPS Gateway Endpoint
curl -s https://rpt-api.gatex.ae/health

# 3. Check Container Migration & Startup Logs
docker logs gen_rpt_backend --tail 50
```

---

## Step 8: Post-Cutover Rollback Plan

If a critical failure occurs during deployment:

1. **Revert Reverse Proxy Router**: Point Nginx site configuration back to Development VPS IP.
2. **Stop Container**: Run `docker compose -f docker-compose.prod.yml down` inside `/opt/gen-rpt/report-management-backend`.
3. **Inspect Crash Logs**: Run `docker logs gen_rpt_backend > /tmp/prod_crash.log`.
4. **Restore Database**: If needed, roll back PostgreSQL using standard backup snapshot: `pg_restore -d gen_rpt_prod_db /tmp/pre_migration_backup.dump`.
