# Agent Directives & Repository Rules

- **VPS Deployment Status Reporting**: Whenever code is pushed, updated, or deployed, ALWAYS explicitly report whether the VPS is updated to the latest commit. Include:
  - Local commit hash & title
  - Remote `origin/main` commit hash
  - VPS active commit hash (`/opt/gen-rpt`)
  - VPS container health status (`GET /health`)
