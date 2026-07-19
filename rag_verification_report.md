# RAG Current State and Next Implementation Plan

**Updated:** July 19, 2026
**Reference report:** `project-skynet-urban-drone-delivery-launch-decision-assess-f-c551ae`
**Status:** RAG retrieval, narrative grounding, and the first exhibit-safety package are deployed on `main` at `e21e492`; combined RAG/web evidence handling and full final-output validation remain incomplete.

## 1. Required evidence policy

1. Uploaded RAG documents are the dominant source of truth whenever RAG is active.
2. Web search is complementary. It may fill documented gaps, add current context, or provide external benchmarks.
3. Web evidence must never silently replace or override RAG evidence.
4. Every retained claim must identify its origin as `RAG`, `web`, or `derived`.
5. A conflict between RAG and web evidence must preserve both claims and their sources.
6. Conflicting evidence must be moved to a visible **Conflicts requiring human review** section. The system must not resolve it automatically.
7. Report conclusions must follow RAG evidence unless a human reviewer explicitly accepts an alternative.

## 2. Verified current behavior

The SkyNet report proves the following:

- Five validated chunks were retrieved from all three uploaded documents.
- The financial, consumer, regulatory, and fleet narrative is substantially grounded in those documents.
- Body evidence retains full chunk IDs and matching source excerpts.
- RAG-required generation fails when grounded evidence is unavailable or unsupported narrative numbers are introduced.
- The post-validation prose mutation that previously caused workflow failures has been removed.

This is enough to confirm that the RAG bridge works for the small SkyNet test set. It does not prove correct behavior for large documents or mixed RAG/web evidence.

## 3. Known gaps

### Critical

- Web collection is currently disabled whenever RAG context exists, so complementary web sourcing is not implemented.
- There is no report-generation conflict detector comparing RAG claims with web claims.
- Final rendered HTML does not yet have a general post-render grounding audit covering every visible field and source label.

### Quality

- Automatically generated exhibit text can call private documents “public sources.”
- Same-unit charts can combine percentages with unrelated meanings, creating misleading comparisons.
- Chart labels can be truncated into unreadable fragments.
- The SkyNet report summarizes evidence but does not state a decisive launch recommendation.
- Grounded action steps may be empty even when the documents contain explicit actions.
- Reasonable analysis and source facts are not consistently distinguished; derived statements can look source-verified.

### Verification limitation

The previous claim of “100% coverage and zero hallucinations” applied only to selected narrative facts. It did not inspect the rendered HTML and therefore missed the unsupported chart values. It must not be used as a production-readiness confirmation.

## 4. Implementation progress — July 19, 2026

The first implementation package is deployed on `main` at commit `e21e492`:

- Nested exhibit `data` is now inside the RAG numeric-validation boundary.
- Unsupported model exhibits are removed immediately after each synthesis response, before they can fail or contaminate the report-level gate.
- Supported nested `table` exhibits are converted to the renderer's existing matrix schema.
- Strict RAG rendering no longer re-enables synthetic fallbacks during HTML or Markdown generation.
- New reports preserve complete chunk IDs in exhibit provenance.
- Deterministic exhibits that repeat facts already covered by grounded model exhibits are removed.
- The previously generated SkyNet payload now exposes its unsupported derived `27.5` value during validation.
- A corrected strict render contains the real table values and does not contain the placeholder `A/B/C = 60/45/30` chart.
- All 20 repository unit tests pass, including new nested-table, provenance, deduplication, synthesis-filter, and rendered-output regressions.

Existing generated reports are immutable artifacts and are not rewritten by this change. A new generation is required after deployment to validate the corrected production output.

## 5. What must change

### Phase 1 — Restore complementary web evidence

- Keep validated RAG chunks first in the merged source list.
- Run a bounded web search when RAG is active instead of forcing the public-source list to be empty.
- Use web queries only for gaps identified from the RAG context and report question.
- Keep separate RAG and web source counts in the report manifest.
- Label evidence records with source origin and preserve URLs or full document/chunk identifiers.

**Acceptance:** a mixed run contains RAG and web evidence, but the narrative still follows RAG where RAG supplies an answer.

### Phase 2 — Detect and expose conflicts

- Reuse the existing conflict metadata and conflict-detection rules where practical.
- Compare claims only when they concern the same entity, metric, unit, geography, and time period.
- Record both values, both sources, the comparison reason, and `status: requires_human_review`.
- Exclude unresolved web-conflicting claims from conclusions, actions, and exhibits.
- Build a deterministic **Conflicts requiring human review** report section from the conflict records.
- Do not ask the language model to decide which conflicting value is correct.

**Acceptance:** if RAG states a 25 lb limit and a web source states 30 lb for the same rule and period, the report uses 25 lb as its working basis and shows both values in the review section.

### Phase 3 — Fix exhibits and provenance

- [Completed locally] Convert supported nested tables to the existing matrix/table-compatible schema, or reject them before rendering.
- [Completed locally] Prevent the generic `A/B/C = 60/45/30` rendering fallback in RAG mode.
- [Completed locally] Preserve full chunk IDs in `data_basis` for new reports.
- [Completed locally] Validate nested exhibit `data` and filter unsupported model exhibits before the report gate.
- [Completed locally] Prefer grounded model exhibits; add deterministic exhibits only when they contribute different evidence.
- Generate source-aware labels: private document, supplementary web source, or derived calculation.
- Reject charts that compare unlike metrics solely because their units match.

**Acceptance:** every displayed value is traceable, no fallback values appear, labels are readable, and repeated exhibits are removed.

### Phase 4 — Strengthen the decision output

- Require a conclusion-first title and an explicit launch/investment decision supported by RAG.
- Produce action steps from explicit document recommendations first.
- Mark analytical implications as `derived` and keep them separate from quoted facts.
- State missing evidence instead of implying operational readiness.

**Acceptance:** the report clearly states the decision, evidence conditions, required actions, and open questions without inventing facts.

### Phase 5 — Validate the final artifact

- Audit the final normalized payload and rendered HTML, not only the model response.
- Fail generation if the final HTML contains unsupported numeric claims, unresolved source labels, invalid chunk IDs, empty charts, or hidden fallback values.
- Save a compact evidence/conflict audit beside the report for review and debugging.

**Acceptance:** the final HTML passes the same grounding rules as the narrative payload.

## 6. What must not change

- Do not replace or redesign the existing RAG ingestion, chunking, embedding, retrieval, or validation services without a demonstrated defect.
- Do not allow web evidence to override RAG evidence automatically.
- Do not automatically resolve conflicts.
- Do not weaken the strict grounded-number or citation gates to make workflows pass.
- Do not reintroduce synthetic prose, actions, exhibits, scores, or fallback chart values in RAG mode.
- Do not change the frontend report inputs; title and sector remain sufficient.
- Do not change authentication, CORS, Render startup, R2 storage, publishing, or unrelated workflow behavior.
- Do not change public-only report generation except where shared final-artifact validation exposes an existing defect.
- Do not add a new framework, service, database migration, or dependency unless the existing code cannot support the requirement.
- Do not rewrite the full report pipeline. Apply focused changes at source collection, evidence reconciliation, exhibit normalization, and final validation boundaries.

## 7. Implementation order

1. ~~Add regression tests reproducing the SkyNet fallback-chart defect.~~ Completed locally.
2. ~~Correct exhibit normalization, full-ID preservation, deduplication, and strict final rendering.~~ Completed locally.
3. Enable bounded complementary web collection during RAG runs.
4. Add RAG-first evidence priority and source-origin labels.
5. Add deterministic conflict records and the human-review section.
6. Add decision/action requirements that use facts first and label derived analysis.
7. Run unit and workflow tests.
8. Deploy once, then execute the validation matrix below.

This order fixes the known hallucination path before adding new mixed-source behavior.

## 8. End-to-end validation matrix

| Scenario | Expected result |
|---|---|
| RAG only: current SkyNet documents | All document facts remain grounded; no web claims or fallback values appear. |
| RAG plus complementary web evidence | Web evidence fills a real gap and is labelled supplementary; RAG remains dominant. |
| RAG/web agreement | Corroboration is recorded without duplicating the claim or exhibit. |
| Deliberate RAG/web numeric conflict | Both claims appear only in the human-review section; RAG remains the working basis. |
| Unsupported model table schema | Exhibit is normalized safely or removed; no generic chart is rendered. |
| Large multi-section document | Retrieval covers distinct sections rather than relying only on a small global top-K set. |
| Public-only report | Existing web-report behavior continues to work. |
| Missing RAG when required | Generation fails clearly before synthesis. |

## 9. Definition of done

RAG is considered properly working only when all of the following are true:

- Required RAG context is present and used.
- RAG evidence has explicit priority over web evidence.
- Web search supplements gaps without replacing RAG claims.
- Conflicts are preserved for human review and never resolved silently.
- Every visible material claim and number has valid provenance.
- Full chunk IDs remain resolvable.
- Final HTML contains no unsupported fallback content.
- Exhibits are relevant, non-duplicative, and semantically comparable.
- Facts, derived analysis, and unresolved conflicts are visibly distinguishable.
- One mixed-source end-to-end production run passes review after deployment.

## 10. Concrete combined RAG/web design

Use the existing pipeline and data structures. Do not add another service, database, framework, or model pass.

### Processing flow

1. Retrieve and validate RAG chunks exactly as today.
2. Ask the existing document-grounded planner for external-gap queries only.
3. Run a bounded web collection for those queries: start with at most four queries, two results per query, and eight accepted web sources.
4. Build separate RAG and web evidence ledgers before combining them.
5. Add an `origin` value (`rag`, `web`, or `derived`) to each evidence record using the existing `source_type`; `internal` means `rag`, and fetched HTTP/PDF sources mean `web`.
6. Reconcile comparable evidence deterministically before synthesis.
7. Give synthesis three separate blocks: approved working evidence, supplementary web evidence, and conflicts requiring review.
8. Exclude unresolved conflicts from conclusions, actions, and exhibits.
9. Render conflicts in a deterministic human-review section.
10. Audit the normalized payload and rendered HTML before publishing.

### Comparison and conflict rule

Compare two claims only when all available identifying fields agree: entity, metric, unit, geography, and time period. A partial or uncertain match is not a conflict and must remain separate evidence.

For a valid match:

- Same value: mark the web record `corroborates_rag`; retain one narrative claim with both sources.
- Different value: mark both records `requires_human_review`; keep the RAG value as the working basis and exclude the web value from generated conclusions and charts.
- No RAG equivalent: mark the web record `supplementary`; it may fill the documented gap with a visible web-source label.
- Derived calculation: retain its input evidence IDs and formula; never present it as a directly sourced fact.

Each reconciled record needs only these fields: `id`, `origin`, `fact`, `value`, `unit`, `entity`, `metric`, `geography`, `period`, `source_id`, `status`, and optional `conflicts_with` or `derived_from`.

### Minimal code boundaries

- `web_report_pipeline.py`: enable bounded web collection in RAG mode, build separate ledgers, and pass reconciled blocks to synthesis.
- `web_evidence.py`: label evidence origin and add deterministic reconciliation/conflict records.
- `web_fetch.py`: extend the existing manifest with separate RAG/web counts; do not replace collection or source models.
- `web_publication_contract.py`: reject conclusions, actions, or exhibits that use unresolved conflicts or lack valid provenance.
- `web_report_renderer.py`: render source-origin labels and the human-review conflict section.
- `tests/test_rag_bridge.py`: cover agreement, gap filling, conflict isolation, derived values, bounded collection, and public-only regression behavior.

### Release gates

Implement and deploy in three small packages:

1. **Collection and origin:** bounded web search plus separate RAG/web evidence records. No report behavior changes until tests prove RAG remains first.
2. **Reconciliation and review:** deterministic agreement/conflict classification plus the human-review section.
3. **Final artifact audit:** reject unsupported or conflict-contaminated payloads and HTML, then run the full validation matrix.

Each package must keep public-only generation unchanged, pass existing tests, add focused regression tests, and be deployable or revertible as one commit.
