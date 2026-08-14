# Agent Directives & Repository Rules

- **VPS Deployment Status Reporting**: Whenever code is pushed, updated, or deployed, ALWAYS explicitly report whether the VPS is updated to the latest commit. Include:
  - Local commit hash & title
  - Remote `origin/main` commit hash
  - VPS active commit hash (`/opt/gen-rpt`)
  - VPS container health status (`GET /health`)

- **Major Agenda Task Tracking & Single Changelog (`task_changelog.md`)**:
  - Applies ONLY to `gen_rpt-main` and `gatex` repository work.
  - When the user explicitly declares a major agenda task (e.g., `"TASK:"`, `"START TASK:"`, `"THIS IS A TASK:"`, `"AGENDA TASK:"`), activate agenda task logging hook.
  - Track start time timestamp when declared, end time timestamp when completed, calculate duration in minutes `Time(min)`, and determine multi-select `Work Type` tags (`Feat Backend`, `Feat Frontend`, `Deploy/Ops`, `Testing`, `Debug`, `Research`, `Doc`, `Design`, `Feat Database`, `Feat ThrdPrty`, `Code Review`, `Meeting/Sync`).
  - Upon completion of the agenda task, append the 7-column entry to `task_changelog.md` (`Start`, `End`, `Time(min)`, `Work Type`, `Task`, `Output`, `Link/Deliverables`) in both tabular and block formats.
  - `task_changelog.md` must remain local-only (listed in `.gitignore`).
