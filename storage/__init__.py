"""
storage/
Isolated Cloudflare R2 storage layer for the gen_rpt report review platform.

This module handles:
- R2 authentication and object operations (r2_client)
- Uploading generated reports to R2 (upload_report)
- Uploading generated reviews to R2 (upload_review)
- Managing the central catalog index (catalog_manager)
- Managing per-report manifests (manifest_manager)

No generation or review logic lives here.
"""
