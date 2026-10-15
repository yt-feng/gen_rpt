# Bounded PDF maintenance

The persistent entrypoint is `.github/workflows/gatex-pdf-maintenance.yml`.
The content-localization and SHA-backfill workflows remain manual tools; they
must not acquire their own PDF schedules.

## Schedule and operation

The workflow runs once per hour at minute 23 UTC on the default branch. It
discovers pending PDFs and resumes the existing queue. Email delivery remains
unchanged.

Operational acceptance requires two separate checks:

1. Inspect actual artifacts from a normal maintenance run and verify the
   producer/server current-policy QA contract and publication readback.
2. Verify an actual `schedule` event, its `maintain` job and safe QA summary.
   A manual run, a skipped busy preflight, or offline tests do not demonstrate
   that scheduled translation ran.

To pause future automatic translation, disable this workflow in GitHub Actions
or revert its `schedule` block. This does not cancel an active run. Disabling
the whole workflow also disables manual runs; reverting only its schedule keeps
manual metadata discovery available with its default `discovery_only=true`.
Deliberate manual recovery can still use exact source pins.

GitHub schedules may be delayed; the queue does not depend on a run starting at
an exact time. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

## Work and recovery bounds

Each run re-reads the authenticated inventory and processes at most two unique
source PDFs, serially, within a 1,800-second total translation budget. It
reserves a fair share for each selected source and the final inventory readback.
The job timeout remains 45 minutes to include setup and cleanup. The hourly
cadence offers at most 48 source-attempt slots per day; it is not a promise of
48 completed translations.

The metadata queue uses an Actions cache restricted to trusted `main` runs.
It stores identities, versions, hashes, attempt times and safe error codes.
If the cache is missing or corrupt, the inventory reconstructs pending work;
no queue or previous result can declare an artifact ready.

New and changed source versions get one priority lane. A second oldest-due lane
preserves progress on the existing backlog. Failed sources wait 1 hour, then
4 hours, 12 hours and at most 24 hours between attempts. A changed source
version resets only that source's retry state. A failed source does not stop a
later independently selected source.

Actual translation resumes source-SHA-bound private R2 checkpoints. Inventory
versions and downloaded PDF SHA-256 are checked before model work. Current QA,
publication/readback, source identity and minimum font-size checks remain
mandatory; `force` stays false. A partial checkpoint is progress, not a ready
Chinese PDF. Unresolved selected sources fail the run. A successful bounded run
may still report `globalPending > 0`; only `globalComplete=true` means the
entire current inventory is ready.

## Concurrency and records

The preflight defers while another PDF run is active or pending. The maintenance
writer uses the existing `gatex-pdf-localization` concurrency group with
`cancel-in-progress:false`; neither child processes nor preflight take a second
lock. News has an independent group. Avoid queuing duplicate manual PDF runs.
GitHub's [concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
describes the shared pending-run behavior.

Only safe queue metadata and the QA summary persist in Actions. Source PDFs,
source text, OCR/model responses and private checkpoints must stay out of logs
and artifacts. Private working files are removed at the end of the job.
