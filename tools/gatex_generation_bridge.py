from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree

import fitz
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen_rpt.deepseek_client import DeepSeekClient
from gen_rpt.main_web import slugify
from gen_rpt.web_fetch import SourceDocument
from gen_rpt.web_report_pipeline import WebReportPipeline


SOURCE_MODES = {"web_only", "collection_only", "web_and_collection"}
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 100_000
MAX_OPENXML_MEMBER_BYTES = 20 * 1024 * 1024
MAX_OPENXML_TOTAL_BYTES = 40 * 1024 * 1024
USER_AGENT = "GateX-GenRPT-Bridge/1.0"


class BridgeError(RuntimeError):
    pass


class GateXApi:
    def __init__(self, base_url: str, token: str, job_id: str) -> None:
        self.base_url = _validated_callback_base(base_url)
        self.token = str(token or "").strip()
        if not self.token:
            raise BridgeError("Missing GATEX_GENERATION_CALLBACK_SECRET.")
        self.job_id = _validated_job_id(job_id)
        self.job_path = f"/api/generation/jobs/{quote(self.job_id, safe='')}"
        self.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def progress(self, phase: str, progress: int, message: str) -> None:
        payload = {
            "phase": str(phase or "running"),
            "progress": max(0, min(99, int(progress))),
            "message": str(message or "")[:500],
            "status": phase if phase in {"ingesting", "reviewing"} else "running",
            "workflowRunId": os.getenv("GITHUB_RUN_ID", ""),
            "startedAt": self.started_at,
        }
        try:
            self._request("POST", f"{self.job_path}/progress", json_payload=payload)
        except Exception as exc:
            print(f"[gatex.bridge] progress callback warning: {exc}", flush=True)

    def source_manifest(self) -> Dict[str, Any]:
        payload = self._request("GET", f"{self.job_path}/sources")
        if not isinstance(payload, dict):
            raise BridgeError("GateX source manifest must be a JSON object.")
        return payload

    def upload_asset(self, relative_path: str, file_path: Path) -> Dict[str, Any]:
        normalized = _normalized_asset_path(relative_path)
        content_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
        endpoint = f"{self.job_path}/assets/{quote(normalized, safe='')}"
        self._request(
            "PUT",
            endpoint,
            raw_body=file_path.read_bytes(),
            headers={
                "content-type": content_type,
                "x-gatex-asset-path": normalized,
            },
            timeout=180,
        )
        return {
            "path": normalized,
            "contentType": content_type,
            "byteSize": file_path.stat().st_size,
            "sha256": _sha256(file_path),
        }

    def complete(self, payload: Dict[str, Any]) -> None:
        self._request("POST", f"{self.job_path}/complete", json_payload=payload, timeout=180)

    def fail(self, error: str, *, message: str = "GateX report generation failed.") -> None:
        payload = {
            "phase": "failed",
            "error": str(error or "Unknown generation error")[:2_000],
            "message": str(message or "GateX report generation failed.")[:500],
            "workflowRunId": os.getenv("GITHUB_RUN_ID", ""),
        }
        self._request("POST", f"{self.job_path}/fail", json_payload=payload)

    def download(self, download_url: str, target: Path) -> None:
        url = urljoin(f"{self.base_url}/", str(download_url or "").strip())
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BridgeError("Source manifest returned an invalid downloadUrl.")

        headers = {"user-agent": USER_AGENT, "accept": "*/*"}
        callback_origin = urlparse(self.base_url)
        if (parsed.scheme, parsed.netloc) == (callback_origin.scheme, callback_origin.netloc):
            headers["authorization"] = f"Bearer {self.token}"

        response = _request_with_retries("GET", url, headers=headers, stream=True, timeout=120)
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > MAX_SOURCE_BYTES:
            response.close()
            raise BridgeError(f"Source exceeds the {MAX_SOURCE_BYTES} byte download limit.")

        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_SOURCE_BYTES:
                    response.close()
                    output.close()
                    target.unlink(missing_ok=True)
                    raise BridgeError(f"Source exceeds the {MAX_SOURCE_BYTES} byte download limit.")
                output.write(chunk)
        response.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Dict[str, Any] | None = None,
        raw_body: Any = None,
        headers: Dict[str, str] | None = None,
        timeout: int = 90,
    ) -> Any:
        request_headers = {
            "authorization": f"Bearer {self.token}",
            "user-agent": USER_AGENT,
            "accept": "application/json",
        }
        request_headers.update(headers or {})
        response = _request_with_retries(
            method,
            f"{self.base_url}{path}",
            headers=request_headers,
            json=json_payload,
            data=raw_body,
            timeout=timeout,
        )
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return response.text
        try:
            return response.json()
        except ValueError as exc:
            raise BridgeError(f"GateX returned invalid JSON for {method} {path}.") from exc


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
                raise BridgeError(f"GateX request failed ({status}) for {method} {url}: {detail}")
            return response
        except (requests.RequestException, BridgeError) as exc:
            last_error = exc
            if isinstance(exc, BridgeError) or attempt >= 3:
                break
            time.sleep(2**attempt)
    raise BridgeError(f"GateX request failed for {method} {url}: {last_error}") from last_error


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
        raise BridgeError("callback_base contains an invalid port.") from exc
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
        raise BridgeError("callback_base must be an allowed GateX HTTPS origin.")
    return f"https://{parsed.hostname.lower()}"


def _validated_job_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BridgeError("job_id must be a UUID.") from exc


def _normalized_asset_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise BridgeError(f"Invalid asset path: {value}")
    return "/".join(parts)


def _manifest_items(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidate: Any = manifest
    if isinstance(candidate.get("data"), dict):
        candidate = candidate["data"]
    for key in ("sources", "documents", "files", "items"):
        value = candidate.get(key) if isinstance(candidate, dict) else None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _safe_file_name(value: str, index: int) -> str:
    name = Path(str(value or "").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    if not name:
        name = f"source-{index:03d}.bin"
    stem = Path(name).stem[:80] or f"source-{index:03d}"
    suffix = Path(name).suffix[:16]
    return f"{index:03d}-{stem}{suffix}"


def _private_reference_url(document_id: str, original_name: str, index: int) -> str:
    """Return an opaque, stable URL used only to retain private-source citations."""

    seed = str(document_id or "").strip() or f"{index}:{original_name}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"private://gatex.collection/{digest}"


def download_private_sources(
    api: GateXApi,
    manifest: Dict[str, Any],
    target_dir: Path,
) -> tuple[List[SourceDocument], List[Dict[str, Any]]]:
    documents: List[SourceDocument] = []
    summaries: List[Dict[str, Any]] = []
    items = _manifest_items(manifest)
    target_dir.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(items, start=1):
        download_url = str(item.get("downloadUrl") or item.get("download_url") or "").strip()
        original_name = str(
            item.get("fileName")
            or item.get("file_name")
            or item.get("name")
            or f"source-{index:03d}"
        ).strip()
        mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip()
        document_id = str(item.get("id") or item.get("documentId") or item.get("document_id") or "").strip()
        summary: Dict[str, Any] = {
            "id": document_id,
            "fileName": original_name,
            "mimeType": mime_type,
            "status": "skipped",
        }
        if not download_url:
            summary["message"] = "Manifest item has no downloadUrl."
            summaries.append(summary)
            continue

        target = target_dir / _safe_file_name(original_name, index)
        try:
            api.download(download_url, target)
            text = _extract_source_text(target, mime_type)
            if len(text.strip()) < 80:
                summary["message"] = "No usable text could be extracted."
                summaries.append(summary)
                continue
            description = str(item.get("description") or item.get("summary") or "").strip()
            snippet = description or re.sub(r"\s+", " ", text[:600]).strip()
            documents.append(
                SourceDocument(
                    title=original_name,
                    url=_private_reference_url(document_id, original_name, index),
                    query="GateX private knowledge collection",
                    snippet=snippet[:600],
                    content=text[:MAX_EXTRACTED_CHARS],
                    source_type=f"private_{_source_kind(target, mime_type)}",
                    content_type=mime_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                    domain="gatex-private",
                )
            )
            summary.update(
                {
                    "status": "ready",
                    "byteSize": target.stat().st_size,
                    "extractedChars": min(len(text), MAX_EXTRACTED_CHARS),
                }
            )
        except Exception as exc:
            summary["status"] = "failed"
            summary["message"] = str(exc)[:500]
        summaries.append(summary)
    return documents, summaries


def _source_kind(path: Path, mime_type: str) -> str:
    mime = mime_type.lower()
    suffix = path.suffix.lower()
    if "pdf" in mime or suffix == ".pdf":
        return "pdf"
    if "html" in mime or suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".docx", ".pptx", ".xlsx"}:
        return suffix.lstrip(".")
    if "json" in mime or suffix == ".json":
        return "json"
    return "text"


def _extract_source_text(path: Path, mime_type: str) -> str:
    kind = _source_kind(path, mime_type)
    if kind == "pdf":
        return _extract_pdf(path)
    if kind == "html":
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:MAX_EXTRACTED_CHARS]
    if kind in {"docx", "pptx", "xlsx"}:
        return _extract_openxml(path)

    raw = path.read_bytes()
    if kind == "json":
        try:
            value = json.loads(raw.decode("utf-8-sig"))
            return json.dumps(value, ensure_ascii=False, indent=2)[:MAX_EXTRACTED_CHARS]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    if b"\x00" in raw[:4096]:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)[:MAX_EXTRACTED_CHARS]
        except UnicodeDecodeError:
            continue
    return ""


def _extract_pdf(path: Path) -> str:
    parts: List[str] = []
    with fitz.open(path) as document:
        for page_index in range(min(document.page_count, 80)):
            text = document.load_page(page_index).get_text("text")
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                parts.append(text)
            if sum(len(part) for part in parts) >= MAX_EXTRACTED_CHARS:
                break
    return "\n".join(parts)[:MAX_EXTRACTED_CHARS]


def _extract_openxml(path: Path) -> str:
    prefixes = {
        ".docx": ("word/document.xml", "word/header", "word/footer"),
        ".pptx": ("ppt/slides/slide",),
        ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/sheet"),
    }.get(path.suffix.lower(), ())
    parts: List[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if name.endswith(".xml") and any(name.startswith(prefix) for prefix in prefixes)
        )
        total_xml_size = 0
        for name in names:
            member_size = archive.getinfo(name).file_size
            if member_size > MAX_OPENXML_MEMBER_BYTES:
                raise BridgeError(f"OpenXML member is too large: {name}")
            total_xml_size += member_size
            if total_xml_size > MAX_OPENXML_TOTAL_BYTES:
                raise BridgeError("OpenXML document expands beyond the supported size.")
        for name in names:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except (ElementTree.ParseError, KeyError):
                continue
            values = []
            for element in root.iter():
                local_name = element.tag.rsplit("}", 1)[-1]
                if local_name in {"t", "v"} and element.text and element.text.strip():
                    values.append(element.text.strip())
            if values:
                parts.append(" ".join(values))
            if sum(len(part) for part in parts) >= MAX_EXTRACTED_CHARS:
                break
    return "\n".join(parts)[:MAX_EXTRACTED_CHARS]


def _run_generator(
    *,
    topic: str,
    language: str,
    model: str,
    source_mode: str,
    private_sources: Sequence[SourceDocument],
    output_dir: Path,
) -> Dict[str, Any]:
    client = DeepSeekClient(model=model)
    pipeline = WebReportPipeline(client=client, language=language)
    return pipeline.build_report(
        topic=topic,
        output_dir=output_dir,
        private_sources=list(private_sources),
        source_mode=source_mode,
    )


def _run_audit(output_dir: Path) -> Dict[str, Any]:
    command = [sys.executable, str(ROOT / "tools" / "local_web_report_audit.py"), str(output_dir)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode != 0:
        raise BridgeError(f"Generated report failed the deterministic audit (exit {result.returncode}).")
    try:
        parsed = json.loads(result.stdout)
        return parsed if isinstance(parsed, dict) else {"passed": True}
    except json.JSONDecodeError:
        return {"passed": True}


def _review_unavailable(status: str, summary: str, error: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "overall_score": -1,
        "summary": summary,
        "findings": [],
    }
    if error:
        payload["error"] = error[:2_000]
    return payload


def _normalize_automated_review(review: Any) -> Dict[str, Any]:
    if not isinstance(review, dict):
        return _review_unavailable(
            "failed",
            "Automated review returned an invalid payload.",
        )
    normalized = dict(review)
    scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    recommendations = (
        review.get("recommendations")
        if isinstance(review.get("recommendations"), dict)
        else {}
    )
    score = scores.get("overall_score")
    grade = str(scores.get("grade") or "").strip()
    tasks = recommendations.get("improvement_tasks")
    findings: List[str] = []
    if isinstance(tasks, list):
        for task in tasks[:40]:
            if isinstance(task, str):
                finding = task
            elif isinstance(task, dict):
                issue = str(
                    task.get("issue")
                    or task.get("finding")
                    or task.get("message")
                    or task.get("title")
                    or ""
                ).strip()
                fix = str(task.get("fix") or "").strip()
                finding = f"{issue} Recommended action: {fix}" if issue and fix else issue or fix
            else:
                finding = ""
            if finding:
                findings.append(re.sub(r"\s+", " ", finding)[:2_000])
    normalized.update(
        {
            "status": "completed",
            "overall_score": score,
            "grade": grade,
            "summary": (
                f"Automated review completed with score {score}/100"
                f"{f' and grade {grade}' if grade else ''}."
            ),
            "findings": findings,
        }
    )
    return normalized


def _run_automated_review(output_dir: Path) -> Dict[str, Any]:
    if not os.getenv("GROQ_API_KEY", "").strip():
        print("[gatex.bridge] GROQ_API_KEY is unavailable; automated review skipped.", flush=True)
        return _review_unavailable(
            "skipped",
            "Automated review was skipped because GROQ_API_KEY is not configured.",
        )
    review_dir = output_dir / "automated_review"
    command = [
        sys.executable,
        str(ROOT / "review_system" / "main.py"),
        "--report",
        str(output_dir / "report.md"),
        "--output",
        str(review_dir),
        "--model",
        os.getenv("REVIEW_MODEL", "llama-3.3-70b-versatile"),
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1_800)
    except Exception as exc:
        print(f"[gatex.bridge] automated review warning: {exc}", flush=True)
        return _review_unavailable(
            "failed",
            "Automated review could not be completed.",
            str(exc),
        )
    if result.stdout:
        print(result.stdout[-8_000:], flush=True)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr[-4_000:], file=sys.stderr, flush=True)
        print(f"[gatex.bridge] automated review exited with {result.returncode}; continuing without it.", flush=True)
        return _review_unavailable(
            "failed",
            "Automated review could not be completed.",
            result.stderr or f"review process exited with {result.returncode}",
        )
    review_path = review_dir / "review.json"
    if not review_path.exists():
        print("[gatex.bridge] automated review did not produce review.json.", flush=True)
        return _review_unavailable(
            "failed",
            "Automated review did not produce a review payload.",
        )
    return _normalize_automated_review(_read_json(review_path))


def _public_files(output_dir: Path) -> Iterable[tuple[str, Path]]:
    assets_dir = output_dir / "assets"
    if assets_dir.is_dir():
        for path in sorted(assets_dir.rglob("*")):
            if path.is_file():
                yield path.relative_to(output_dir).as_posix(), path


def _upload_public_assets(api: GateXApi, output_dir: Path) -> List[Dict[str, Any]]:
    records = []
    for relative_path, file_path in _public_files(output_dir):
        print(f"[gatex.bridge] uploading {relative_path}", flush=True)
        records.append(api.upload_asset(relative_path, file_path))
    if not records:
        raise BridgeError("No public report assets were generated.")
    return records


def _source_summary(sources: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary = []
    for item in sources[:64]:
        if not isinstance(item, dict):
            continue
        summary.append(
            {
                "title": str(item.get("title") or "")[:300],
                "url": str(item.get("url") or "")[:1_500],
                "domain": str(item.get("domain") or "")[:200],
                "sourceType": str(item.get("source_type") or "")[:100],
                "contentType": str(item.get("content_type") or "")[:200],
                "query": str(item.get("query") or "")[:500],
                "snippet": re.sub(r"\s+", " ", str(item.get("snippet") or ""))[:800],
            }
        )
    return summary


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"Unable to read generated JSON: {path.name}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_job(args: argparse.Namespace) -> int:
    token = os.getenv("GATEX_GENERATION_CALLBACK_SECRET", "")
    api = GateXApi(args.callback_base, token, args.job_id)
    source_mode = str(args.source_mode or "web_only").strip().lower()
    if source_mode not in SOURCE_MODES:
        raise BridgeError(f"Unsupported source_mode: {source_mode}")
    language = "zh" if str(args.language or "").lower().startswith("zh") else "en"
    topic = str(args.topic or "").strip()
    if not topic:
        raise BridgeError("topic is required in GateX job mode.")

    output_root = Path(args.out_root).resolve()
    output_dir = output_root / api.job_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    private_dir = output_dir / ".private_sources"
    requested_slug = str(args.slug or "").strip()
    output_slug = (
        slugify(requested_slug)
        if requested_slug
        else f"{slugify(topic)[:48]}-{api.job_id.split('-', 1)[0]}"
    )

    try:
        api.progress("ingesting", 3, "GateX generation runner started.")
        private_sources: List[SourceDocument] = []
        private_source_summary: List[Dict[str, Any]] = []
        if source_mode in {"collection_only", "web_and_collection"}:
            api.progress("ingesting", 8, "Downloading the private source collection.")
            manifest = api.source_manifest()
            private_sources, private_source_summary = download_private_sources(api, manifest, private_dir)
            if not private_sources:
                raise BridgeError(
                    f"{source_mode} generation has no usable private source documents."
                )
            api.progress(
                "ingesting",
                16,
                f"Prepared {len(private_sources)} private source document(s).",
            )

        api.progress("running", 20, "Research planning and report generation started.")
        result = _run_generator(
            topic=topic,
            language=language,
            model=str(args.model or "deepseek-chat"),
            source_mode=source_mode,
            private_sources=private_sources,
            output_dir=output_dir,
        )

        api.progress("reviewing", 74, "Running the deterministic publication audit.")
        audit = _run_audit(output_dir)
        api.progress("reviewing", 82, "Running the automated report review when configured.")
        automated_review = _run_automated_review(output_dir)

        api.progress("uploading", 90, "Uploading public report assets.")
        assets = _upload_public_assets(api, output_dir)
        sources = result.get("sources") if isinstance(result.get("sources"), list) else []
        web_report_payload = _read_json(output_dir / "web_report_payload.json")
        completion = {
            "webReportPayload": web_report_payload,
            "researchFactPack": _read_json(output_dir / "research_fact_pack.json"),
            "evidenceLedger": _read_json(output_dir / "evidence_ledger.json"),
            "sources": _source_summary(sources),
            "automatedReview": automated_review,
            "outputSlug": output_slug,
            "workflowRunId": os.getenv("GITHUB_RUN_ID", ""),
            "checksum": hashlib.sha256(
                json.dumps(web_report_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "language": language,
            "sourceMode": source_mode,
            "assets": assets,
            "audit": audit,
            "privateSourceIngestion": private_source_summary,
        }
        api.complete(completion)
        _write_github_output("report_dir", str(output_dir))
        _write_github_output("output_slug", output_slug)
        _write_github_output("gatex_completed", "true")
        _write_github_output("gatex_failure_notified", "false")
        print(f"[gatex.bridge] GateX generation completed: {output_slug}", flush=True)
        return 0
    except Exception as exc:
        traceback.print_exc()
        failure_notified = False
        try:
            api.fail(str(exc))
            failure_notified = True
        except Exception as callback_exc:
            print(f"[gatex.bridge] failure callback warning: {callback_exc}", file=sys.stderr, flush=True)
        _write_github_output("gatex_completed", "false")
        _write_github_output("gatex_failure_notified", "true" if failure_notified else "false")
        return 1
    finally:
        if private_dir.exists():
            shutil.rmtree(private_dir)


def notify_failure(args: argparse.Namespace) -> int:
    try:
        api = GateXApi(
            args.callback_base,
            os.getenv("GATEX_GENERATION_CALLBACK_SECRET", ""),
            args.job_id,
        )
        api.fail(args.message, message="The GateX generation workflow stopped before completion.")
        return 0
    except Exception as exc:
        print(f"[gatex.bridge] unable to send workflow failure callback: {exc}", file=sys.stderr)
        return 1


def _write_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge gen_rpt jobs with the GateX review system.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run one GateX generation job end to end.")
    run.add_argument("--job-id", required=True)
    run.add_argument("--topic", required=True)
    run.add_argument("--slug", default="")
    run.add_argument("--language", default="en")
    run.add_argument("--source-mode", default="web_only", choices=sorted(SOURCE_MODES))
    run.add_argument("--callback-base", required=True)
    run.add_argument("--model", default="deepseek-chat")
    run.add_argument("--out-root", default="reports_web/gatex_jobs")

    fail = subparsers.add_parser("notify-failure", help="Send a fail callback for an outer workflow failure.")
    fail.add_argument("--job-id", required=True)
    fail.add_argument("--callback-base", required=True)
    fail.add_argument("--message", default="The GitHub Actions workflow failed.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        try:
            return run_job(args)
        except Exception as exc:
            print(f"[gatex.bridge] startup failure: {exc}", file=sys.stderr)
            failure_notified = False
            try:
                api = GateXApi(
                    args.callback_base,
                    os.getenv("GATEX_GENERATION_CALLBACK_SECRET", ""),
                    args.job_id,
                )
                api.fail(str(exc))
                failure_notified = True
            except Exception:
                pass
            _write_github_output("gatex_completed", "false")
            _write_github_output("gatex_failure_notified", "true" if failure_notified else "false")
            return 1
    return notify_failure(args)


if __name__ == "__main__":
    raise SystemExit(main())
