"""Validate the exact uploaded result before acknowledging a generation job."""
import hashlib
import json
import re


def validate_generation_receipt(slug, receipt, payload_bytes):
    if not isinstance(receipt, dict) or receipt.get("status") not in {"generated", "generated_degraded"}:
        raise ValueError("A generated report receipt is required")
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff.-]{0,180}", slug):
        raise ValueError("Invalid receipt slug")
    report_id = str(receipt.get("report_id", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(slug) + r"(?:--run\d+-\d+)?", report_id):
        raise ValueError("Receipt does not match the document slug")
    if receipt.get("slug") != slug or not str(receipt.get("run_id", "")).isdigit():
        raise ValueError("Receipt lacks its workflow identity")
    if hashlib.sha256(payload_bytes).hexdigest() != receipt.get("payload_sha256"):
        raise ValueError("Uploaded payload does not match this generation receipt")
    payload = json.loads(payload_bytes)
    outcome = payload.get("generation_outcome") or {}
    for key in ("status", "reason", "content_policy", "requested_rag_required", "run_id", "run_attempt", "job_id", "job_retry_count", "slug", "report_id"):
        if key not in receipt or outcome.get(key) != receipt[key]:
            raise ValueError("Uploaded generation identity differs from receipt")
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Uploaded report contains no sections")
    body = " ".join(" ".join(s.get("paragraphs") or []) or str(s.get("body") or s.get("content") or "")
                    for s in sections if isinstance(s, dict))
    if len(body.strip()) < 400:
        raise ValueError("Uploaded report is empty or incomplete")
    if receipt["status"] == "generated_degraded":
        if payload.get("content_mode") != "general_overview" or not payload.get("disclaimer"):
            raise ValueError("Degraded report is missing its reader disclosure")
    return payload


async def load_receipted_report(slug, receipt, storage):
    report_id = str(receipt.get("report_id", ""))
    # Validate the key before any storage access; never use fuzzy slug lookup.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(slug) + r"(?:--run\d+-\d+)?", report_id) or "/" in slug:
        raise ValueError("Invalid exact report key")
    prefix = f"reports/{report_id}/"
    payload_bytes = await storage.download(prefix + "metadata/web_report_payload.json")
    if not payload_bytes:
        raise ValueError("Exact generated payload is not available in storage")
    payload = validate_generation_receipt(slug, receipt, payload_bytes)
    for name in ("report.html", "report.md"):
        data = await storage.download(prefix + "current/" + name)
        if not data or hashlib.sha256(data).hexdigest() != receipt.get(name + "_sha256"):
            raise ValueError("Exact generated document is not available in storage")
    payload["r2_prefix"] = prefix
    return payload
