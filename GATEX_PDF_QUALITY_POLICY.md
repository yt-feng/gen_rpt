# GateX complete Chinese PDF quality policy

The engine separates faithful translation from Chinese expression and source-evidenced GateX terminology. Every native, raster, outline, metadata and literal record is counted. Necessary source comparisons, third-party names, military/diplomatic intelligence, external professional categories, conditions, negation, numeric values and source identities take priority over house style. A GateX label is identified by an exact brand phrase or source-logo geometry; a standalone domain heading is not sufficient.

New translations require independent `complete`, `faithful`, `professionalChinese` and `ownedTerminology` verdicts. A style-only failure permits the smallest wording edit followed by a new independent fidelity and quality review. The original maximum is three translation/review cycles. Old reviewed factual text is not discarded: missing quality sidecars first receive a style-only audit. Unchanged passing text reuses its existing fidelity approval; edited text must pass a fresh review. One old-text audit leaves at most two edit/review cycles. Pure numeric/identifier literals may receive an explicit static literal-policy record; this is not represented as model review.

Quality sidecars bind the exact source block hash, target text hash, model/provider, policy version, and SHA-256 of the actual review system prompt. The source-occurrence sidecar binds the immutable source SHA, exact rendered OCR image SHA, crop SHA, original source IDs/text/boxes and the actual image-review prompt hash. Unknown image observations are bounded private diagnostics, not reusable approved evidence. A genuine second text occurrence is preserved. A verified single occurrence with differing Chinese wording uses the reviewed native text only as an unreviewed raster candidate; strict numeric/identity gates and fresh fidelity/quality review must pass before exact equal target hashes permit one render owner.

Changing a professional rule, relevant review prompt or source-occurrence policy requires a new explicit policy version **and** a coordinated GateX server readiness/publish contract update. Do not merely refresh the golden prompt hashes under the existing version. The golden contract regression records the initial prompts for `gatex-professional-zh/v1` and `gatex-source-occurrence-review/v1`. Prompt hashes protect per-source cache reuse; explicit version changes make already-ready inventory entries re-enter processing. Existing reviewed factual blocks, verified source/image OCR and prior successful readable artifacts are preserved during this migration.

Successful QA includes:

- `qualityPolicyVersion: gatex-professional-zh/v1`
- `professionalChinesePassed: true`
- `ownedTerminologyPassed: true`
- `qualityCheckedBlockCount === translatedBlockCount`
- `renderAliasReviewPolicy: gatex-source-occurrence-review/v1`

All original complete-translation checks remain required. Publication still requires the actual source/artifact hashes, complete page/block mappings, numbers/currencies/links/identities, preserved figures, no missing text or overflow, verified OCR and fonts of at least 7pt. Private review candidates, source text, image bytes and rejected responses never belong in public Actions artifacts. Mock tests verify control flow and cache contracts; they do not establish real semantic or visual quality. A real source-pinned specimen with private source/target readback and all-page visual inspection remains required before claiming the new automatic policy accepted.
