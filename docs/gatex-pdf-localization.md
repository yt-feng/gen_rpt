# Complete Chinese PDF editions

The manually dispatched GateX localization workflow uses `DEEPSEEK_API_KEY` for
the explicit `deepseek` provider. Text generation and a separate, fresh semantic
review use `deepseek-v4-pro`. Raster transcription and independent contextual
image review use `deepseek-flash`. Thinking is disabled for these bounded JSON
operations. No credential is renamed to `APIMART_API_KEY`, and provider failures
do not silently switch providers. The existing APIMart adapter remains available
for an explicit standalone invocation with `GATEX_PDF_TRANSLATION_PROVIDER=apimart`.

The PDF cron stays disabled until actual sample acceptance. Dispatch an exact
`sample_id` with its `expected_source_sha256` first; a sample skips news warming.
For a full inventory continuation use `pdf_limit=0`, `force=false`. The workflow
has a 180-minute bound; rerunning resumes source-bound checkpoints and skips
already published editions. Aliases with identical source bytes serialize their
checkpoint writers and reuse accepted work.

A change of provider, text/review model, vision model or translation policy
invalidates old translated blocks and any completed artifact. The same immutable
source can reuse OCR only when its rendered-image SHA and verified coverage
policy match. A source/schema mismatch is rejected, and `force=true` clears all
OCR and text progress. Partial accepted blocks are durable progress, never a
ready PDF.

Publication still requires every source page and every native/OCR block, table
cell, heading, footnote, citation and source identity; restored exact quantities,
currencies and links; an independent semantic review; preserved figures and
drawings; and rendered text coverage without clipping at 7pt or larger. Overflow
is included in full on labeled continuation pages with source-to-target page
mapping. Original English PDFs remain available under their original identities
and permissions. Statements that the source is an authorized English translation
refer to the original English edition, without claiming Chinese authorization.

Inspect every rendered target page before approving the initial real artifact.
The runner validates the resulting PDF's bytes, full QA and publication receipt,
then downloads the stored artifact and verifies the source/artifact hashes again.
Provider responses, PDF content and private checkpoints stay out of Actions
logs and public QA artifacts.
