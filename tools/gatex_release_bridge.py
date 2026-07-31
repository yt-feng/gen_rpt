from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.gatex_pdf_renderer import GatexPdfError, render_gatex_release_pdf


USER_AGENT = "GateX-PDF-Release/1.0"
MAX_PDF_BYTES = 50 * 1024 * 1024


class ReleaseBridgeError(RuntimeError):
    pass


class GateXReleaseApi:
    def __init__(self, base_url: str, token: str, item_id: str, version_id: str) -> None:
        self.base_url = _validated_callback_base(base_url)
        self.token = str(token or "").strip()
        if not self.token:
            raise ReleaseBridgeError("Missing GATEX_GENERATION_CALLBACK_SECRET.")
        self.item_id = _uuid(item_id, "item_id")
        self.version_id = _uuid(version_id, "version_id")
        self.release_path = (
            f"/api/generation/releases/{quote(self.item_id, safe='')}/"
            f"{quote(self.version_id, safe='')}"
        )

    def fetch(self) -> Dict[str, Any]:
        payload = self._request("GET", self.release_path)
        if not isinstance(payload, dict):
            raise ReleaseBridgeError("GateX returned an invalid PDF release payload.")
        return payload

    def upload_pdf(self, artifact: Mapping[str, Any], content_checksum: str) -> Dict[str, Any]:
        pdf_path = Path(str(artifact.get("path") or ""))
        if not pdf_path.is_file():
            raise ReleaseBridgeError("Rendered PDF artifact is missing.")
        byte_size = pdf_path.stat().st_size
        if byte_size > MAX_PDF_BYTES:
            raise ReleaseBridgeError("Rendered PDF exceeds the 50 MB release limit.")
        payload = self._request(
            "PUT",
            f"{self.release_path}/pdf",
            raw_body=pdf_path.read_bytes(),
            headers={
                "content-type": "application/pdf",
                "x-gatex-content-checksum": content_checksum,
                "x-gatex-artifact-checksum": str(artifact.get("sha256") or ""),
                "x-gatex-file-name": str(artifact.get("fileName") or "gatex-report.pdf"),
                "x-gatex-page-count": str(artifact.get("pageCount") or 0),
            },
            timeout=240,
        )
        return payload if isinstance(payload, dict) else {}

    def complete(self, artifact: Mapping[str, Any], content_checksum: str) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            f"{self.release_path}/complete",
            json_payload={
                "contentChecksum": content_checksum,
                "artifactChecksum": artifact.get("sha256"),
                "fileName": artifact.get("fileName"),
                "byteSize": artifact.get("byteSize"),
                "pageCount": artifact.get("pageCount"),
                "workflowRunId": os.getenv("GITHUB_RUN_ID", ""),
                "qa": artifact.get("qa") or {},
            },
            timeout=240,
        )
        return payload if isinstance(payload, dict) else {}

    def fail(self, message: str, content_checksum: str = "") -> None:
        self._request(
            "POST",
            f"{self.release_path}/fail",
            json_payload={
                "contentChecksum": content_checksum,
                "error": str(message or "PDF release failed.")[:2_000],
                "workflowRunId": os.getenv("GITHUB_RUN_ID", ""),
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: int = 120,
    ) -> Any:
        request_headers = {
            "authorization": f"Bearer {self.token}",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        }
        request_headers.update(dict(headers or {}))
        response = _request_with_retries(
            method,
            f"{self.base_url}{path}",
            headers=request_headers,
            json=dict(json_payload) if json_payload is not None else None,
            data=raw_body,
            timeout=timeout,
        )
        if response.status_code == 204 or not response.content:
            return None
        if "json" not in response.headers.get("content-type", ""):
            return response.text
        try:
            return response.json()
        except ValueError as exc:
            raise ReleaseBridgeError(f"GateX returned invalid JSON for {method} {path}.") from exc


def _request_with_retries(method: str, url: str, **kwargs: Any) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    response.close()
                    time.sleep(2**attempt)
                    continue
            if not response.ok:
                detail = response.text[:500].replace("\n", " ")
                status = response.status_code
                response.close()
                raise ReleaseBridgeError(
                    f"GateX request failed ({status}) for {method} {url}: {detail}"
                )
            return response
        except (requests.RequestException, ReleaseBridgeError) as exc:
            last_error = exc
            if isinstance(exc, ReleaseBridgeError) or attempt >= 3:
                break
            time.sleep(2**attempt)
    raise ReleaseBridgeError(f"GateX request failed for {method} {url}: {last_error}") from last_error


def _validated_callback_base(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    parsed = urlparse(base)
    allowed = {
        host.strip().lower()
        for host in os.getenv(
            "GATEX_GENERATION_CALLBACK_ALLOWED_HOSTS",
            "gatex.fund,www.gatex.fund",
        ).split(",")
        if host.strip()
    }
    try:
        port = parsed.port
    except ValueError as exc:
        raise ReleaseBridgeError("callback_base contains an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseBridgeError("callback_base must be an allowed GateX HTTPS origin.")
    return f"https://{parsed.hostname.lower()}"


def _uuid(value: Any, field: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReleaseBridgeError(f"{field} must be a UUID.") from exc


def _checksum_text(value: Any, field: str = "checksum") -> str:
    result = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", result):
        raise ReleaseBridgeError(f"{field} must be a SHA-256 checksum.")
    return result


def run_release(args: argparse.Namespace) -> int:
    api = GateXReleaseApi(
        args.callback_base,
        os.getenv("GATEX_GENERATION_CALLBACK_SECRET", ""),
        args.item_id,
        args.version_id,
    )
    requested_checksum = _checksum_text(args.content_checksum, "content_checksum")
    active_checksum = requested_checksum
    try:
        envelope = api.fetch()
        canonical_json = envelope.get("canonicalJson")
        if not isinstance(canonical_json, str) or not canonical_json:
            raise ReleaseBridgeError("GateX release payload is missing canonicalJson.")
        server_checksum = _checksum_text(envelope.get("contentChecksum"), "contentChecksum")
        calculated_checksum = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        if requested_checksum != server_checksum or calculated_checksum != server_checksum:
            raise ReleaseBridgeError("The approved report version checksum changed before rendering.")
        try:
            release_payload = json.loads(canonical_json)
        except json.JSONDecodeError as exc:
            raise ReleaseBridgeError("GateX canonical release payload is not valid JSON.") from exc
        if not isinstance(release_payload, dict):
            raise ReleaseBridgeError("GateX canonical release payload must be an object.")
        if _uuid(release_payload.get("itemId"), "payload.itemId") != api.item_id:
            raise ReleaseBridgeError("Release payload itemId does not match the requested report.")
        if _uuid(release_payload.get("versionId"), "payload.versionId") != api.version_id:
            raise ReleaseBridgeError("Release payload versionId does not match the requested version.")

        target_dir = Path(args.out_root).resolve() / api.item_id / api.version_id
        artifact = render_gatex_release_pdf(release_payload, target_dir)
        api.upload_pdf(artifact, server_checksum)
        result = api.complete(artifact, server_checksum)
        _write_github_output("pdf_path", str(artifact["path"]))
        _write_github_output("pdf_file_name", str(artifact["fileName"]))
        _write_github_output("pdf_sha256", str(artifact["sha256"]))
        _write_github_output("release_completed", "true")
        _write_github_output("release_failure_notified", "false")
        print(
            json.dumps(
                {
                    "ok": True,
                    "itemId": api.item_id,
                    "versionId": api.version_id,
                    "fileName": artifact["fileName"],
                    "pageCount": artifact["pageCount"],
                    "published": bool(result.get("published")),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        traceback.print_exc()
        failure_notified = False
        try:
            api.fail(str(exc), active_checksum)
            failure_notified = True
        except Exception as callback_exc:
            print(f"[gatex.release] failure callback warning: {callback_exc}", file=sys.stderr)
        _write_github_output("release_completed", "false")
        _write_github_output("release_failure_notified", "true" if failure_notified else "false")
        return 1


def notify_failure(args: argparse.Namespace) -> int:
    try:
        api = GateXReleaseApi(
            args.callback_base,
            os.getenv("GATEX_GENERATION_CALLBACK_SECRET", ""),
            args.item_id,
            args.version_id,
        )
        api.fail(args.message, str(args.content_checksum or ""))
        return 0
    except Exception as exc:
        print(f"[gatex.release] unable to send workflow failure callback: {exc}", file=sys.stderr)
        return 1


def _write_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render and publish an approved GateX PDF release.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--item-id", required=True)
    run.add_argument("--version-id", required=True)
    run.add_argument("--content-checksum", required=True)
    run.add_argument("--callback-base", required=True)
    run.add_argument("--out-root", default="output/pdf")
    fail = subparsers.add_parser("notify-failure")
    fail.add_argument("--item-id", required=True)
    fail.add_argument("--version-id", required=True)
    fail.add_argument("--content-checksum", required=True)
    fail.add_argument("--callback-base", required=True)
    fail.add_argument("--message", default="The GateX PDF release workflow failed.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        return run_release(args)
    return notify_failure(args)


if __name__ == "__main__":
    raise SystemExit(main())
