"""Send the exact uploaded result; HTTP/application errors remain workflow errors."""
import argparse
import json
import os
from pathlib import Path

import requests


def supports_receipts(backend_url, token, get=requests.get):
    capability = get(backend_url.rstrip("/") + "/api/internal/generation-contract",
                     headers={"x-internal-token": token}, timeout=(10, 30))
    try:
        if capability.status_code == 404:
            return False
        capability.raise_for_status()
        if capability.json().get("data", {}).get("generation_receipt_contract") != "v1":
            return False
    finally:
        capability.close()
    return True


def notify(receipt, *, backend_url, token, post=requests.post, get=requests.get):
    if not backend_url or not token:
        if receipt.get("job_id"):
            raise RuntimeError("Backend job requires its configured completion callback")
        return {"status": "standalone_report"}
    # Older backends ignore receipt metadata and claim the latest slug job.
    # Keep publishing real content during rollout, but do not send that unsafe
    # legacy acknowledgement or pretend that the backend job was completed.
    if not supports_receipts(backend_url, token, get):
        return {"status": "deferred_backend_upgrade", "job_completed": False}
    response = post(backend_url.rstrip("/") + "/api/internal/events/report-generated",
                    headers={"Content-Type": "application/json", "x-internal-token": token},
                    json={"document_id": receipt["slug"],
                          "idempotency_key": f"github-actions-{receipt['run_id']}-{receipt['run_attempt']}",
                          "metadata": {"generation_receipt": receipt}}, timeout=(10, 30))
    try:
        response.raise_for_status()
        result = response.json().get("data", {})
        if result.get("status") != "completed":
            raise RuntimeError("Backend did not acknowledge the generated report")
        if receipt.get("job_id") and str(result.get("job_id")) != receipt["job_id"]:
            raise RuntimeError("Backend acknowledged a different job")
        return result
    finally:
        response.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--slug", default="")
    args = parser.parse_args()
    if args.failure:
        backend, token = os.getenv("BACKEND_URL"), os.getenv("INTERNAL_TOKEN")
        if not backend or not token:
            return
        if not supports_receipts(backend, token):
            print("Backend failure acknowledgement deferred until receipt-aware backend deployment")
            return
        response = requests.post(backend.rstrip("/") + "/api/internal/events/report-failed",
            headers={"x-internal-token": token}, timeout=(10, 30),
            json={"document_id": args.slug,
                  "idempotency_key": f"github-actions-failed-{os.getenv('GITHUB_RUN_ID')}-{os.getenv('GITHUB_RUN_ATTEMPT')}",
                  "metadata": {"error": "Report execution, upload or callback failed; see the exact workflow run",
                               "generation_attempt": {"job_id": os.getenv("GENERATION_JOB_ID", ""),
                                                      "job_retry_count": os.getenv("GENERATION_JOB_RETRY_COUNT", "")}}})
        try:
            response.raise_for_status()
        finally:
            response.close()
        return
    if args.receipt is None:
        parser.error("--receipt is required for a generated result")
    receipt = json.loads(args.receipt.read_text())
    result = notify(receipt, backend_url=os.getenv("BACKEND_URL", ""), token=os.getenv("INTERNAL_TOKEN", ""))
    receipt["backend_ack"] = result["status"]
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"- Backend acknowledgement: {result['status']} (uploaded content and backend job completion are separate)\n")
    print(f"Generation callback: {result['status']}; outcome: {receipt['status']}; reason: {receipt['reason']}")


if __name__ == "__main__":
    main()
