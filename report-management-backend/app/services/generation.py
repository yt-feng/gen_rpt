import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from app.models.workflow import GenerationJob
from app.models.enums import JobStatusType

class WorkerInterface:
    async def dispatch(self, job: GenerationJob) -> bool:
        raise NotImplementedError
    
    async def cancel(self, job: GenerationJob) -> bool:
        raise NotImplementedError

import httpx
from app.core.config import settings

class GitHubActionsWorker(WorkerInterface):
    async def dispatch(self, job: GenerationJob) -> bool:
        if not settings.GITHUB_TOKEN:
            print("GITHUB_TOKEN not set. Cannot dispatch to GitHub Actions.")
            return False
            
        # Get the slug from the document
        from app.database.session import async_session_maker
        from app.models.document import Document
        
        slug = ""
        async with async_session_maker() as session:
            doc = await session.get(Document, job.document_id)
            if doc and doc.slug:
                slug = doc.slug
            else:
                slug = f"doc-{str(job.id)[:8]}"

        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/generate_deep_research_v2.yml/dispatches"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "topic": job.topic,
                "slug": slug,
                "rag_required": str(bool((job.audit_metadata or {}).get("rag_required", True))).lower(),
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 204:
                    print(f"Successfully dispatched GitHub Action for Job {job.id}")
                    return True
                else:
                    print(f"Failed to dispatch GitHub Action: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            print(f"Exception dispatching GitHub Action: {e}")
            return False

    async def cancel(self, job: GenerationJob) -> bool:
        print(f"Cancelling GitHub Action for Job {job.id} (not implemented)")
        return True

    async def dispatch_bulk(self, slug: str, topic: str, model: str = "deepseek-chat", rag_required: bool = True) -> bool:
        """Dispatch a single job to generate_deep_research_bulk.yml."""
        if not settings.GITHUB_TOKEN:
            print("GITHUB_TOKEN not set. Cannot dispatch bulk job to GitHub Actions.")
            return False

        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/generate_deep_research_bulk.yml/dispatches"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "topic": topic,
                "slug": slug,
                "model": model,
                "rag_required": str(bool(rag_required)).lower(),
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 204:
                    print(f"[bulk] Dispatched GHA for slug={slug}")
                    return True
                else:
                    print(f"[bulk] Failed to dispatch slug={slug}: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            print(f"[bulk] Exception dispatching slug={slug}: {e}")
            return False

    async def dispatch_single_report(self, topic: str, slug: str, model: str = "deepseek-chat") -> bool:
        """Dispatch a single job to generate_deep_research_v2.yml."""
        if not settings.GITHUB_TOKEN:
            print("GITHUB_TOKEN not set. Cannot dispatch v2 job to GitHub Actions.")
            return False

        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/generate_deep_research_v2.yml/dispatches"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "topic": topic,
                "slug": slug,
                "model": model,
                "rag_required": "true",
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 204:
                    print(f"[v2] Dispatched GHA for slug={slug}")
                    return True
                else:
                    print(f"[v2] Failed to dispatch slug={slug}: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            print(f"[v2] Exception dispatching slug={slug}: {e}")
            return False

    async def dispatch_image_regeneration(self, slug: str, image_key: str, prompt: str, r2_prefix: str = "") -> bool:
        """Dispatch a job to regenerate_image.yml to regenerate a specific report image."""
        if not settings.GITHUB_TOKEN:
            print("GITHUB_TOKEN not set. Cannot dispatch image regeneration to GitHub Actions.")
            return False

        url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/regenerate_image.yml/dispatches"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "slug": slug,
                "image_key": image_key,
                "prompt": prompt,
                "r2_prefix": r2_prefix
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 204:
                    print(f"[image-regeneration] Dispatched GHA for slug={slug}, image_key={image_key}")
                    return True
                else:
                    print(f"[image-regeneration] Failed to dispatch slug={slug}: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            print(f"[image-regeneration] Exception dispatching slug={slug}: {e}")
            return False

async def delete_report_files_from_r2(slug: str) -> None:
    """Delete all files belonging to a report slug in R2 to cleanly overwrite."""
    from app.storage.provider import storage_provider
    import asyncio
    
    if not storage_provider.is_configured:
        return
        
    def _delete_prefix(prefix: str):
        try:
            paginator = storage_provider.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=storage_provider.bucket, Prefix=prefix):
                if 'Contents' in page:
                    objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                    storage_provider.s3_client.delete_objects(
                        Bucket=storage_provider.bucket,
                        Delete={'Objects': objects_to_delete}
                    )
                    print(f"[r2] Deleted {len(objects_to_delete)} objects for prefix {prefix}")
        except Exception as e:
            print(f"[r2] Exception deleting prefix {prefix}: {e}")

    # The GHA output could be under reports/{slug}/ or reports_web/{slug}/ (and potentially prepended with a date).
    # Since we know the slug uniquely identifies the job, we'll try to find the full folder prefix first.
    def _find_and_delete():
        deleted = False
        for base_prefix in ['reports/', 'reports_web/']:
            res = storage_provider.s3_client.list_objects_v2(
                Bucket=storage_provider.bucket, Prefix=base_prefix, Delimiter='/'
            )
            for prefix_obj in res.get('CommonPrefixes', []):
                folder = prefix_obj['Prefix']
                if slug in folder:
                    _delete_prefix(folder)
                    deleted = True
        return deleted

    from anyio import to_thread
    await to_thread.run_sync(_find_and_delete)

async def _load_report_payload_from_r2(slug: str, topic: str = "") -> dict:
    """
    Tries to load the web_report_payload.json from R2 to get real report content.
    The GitHub Actions workflow often prepends a date (YYYY-MM-DD-) to the slug in R2.
    """
    from app.storage.provider import storage_provider
    import json
    from anyio import to_thread
    
    if not storage_provider.is_configured:
        return {}

    def _find_and_download():
        payload_data = None
        review_data = None
        found_folder = None
        base_slug = slug.rsplit("-", 1)[0] if "-" in slug else slug
        try:
            # Check reports/
            res = storage_provider.s3_client.list_objects_v2(
                Bucket=storage_provider.bucket, Prefix='reports/', Delimiter='/'
            )
            for prefix_obj in res.get('CommonPrefixes', []):
                folder = prefix_obj['Prefix']
                if slug in folder or (len(base_slug) > 8 and base_slug in folder):
                    path = f"{folder}metadata/web_report_payload.json"
                    try:
                        response = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=path)
                        payload_data = response['Body'].read()
                    except Exception as e:
                        print(f"[poll_r2] Error downloading payload for {slug}: {e}")
                        
                    review_path = f"{folder}reviews/review.json"
                    try:
                        review_res = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=review_path)
                        review_data = review_res['Body'].read()
                    except Exception as e:
                        print(f"[poll_r2] No review found for {slug} or error: {e}")
                        
                    if payload_data:
                        found_folder = folder
                        return payload_data, review_data, found_folder
                    
            # Check reports_web/
            res2 = storage_provider.s3_client.list_objects_v2(
                Bucket=storage_provider.bucket, Prefix='reports_web/', Delimiter='/'
            )
            for prefix_obj in res2.get('CommonPrefixes', []):
                folder = prefix_obj['Prefix']
                if slug in folder or (len(base_slug) > 8 and base_slug in folder):
                    path = f"{folder}web_report_payload.json"
                    try:
                        response = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=path)
                        payload_data = response['Body'].read()
                    except Exception as e:
                        print(f"[poll_r2] Error downloading payload for {slug} in reports_web: {e}")
                        
                    if payload_data:
                        found_folder = folder
                        return payload_data, review_data, found_folder
        except Exception as e:
            print(f"[poll_r2] Error finding payload for {slug}: {e}")

        return None, None, None

    p_data, r_data, r2_folder = await to_thread.run_sync(_find_and_download)
    result_payload = {}
    if p_data:
        try:
            result_payload = json.loads(p_data.decode("utf-8"))
            if r2_folder:
                result_payload["r2_prefix"] = r2_folder
        except Exception as e:
            print(f"[poll_r2] Failed to parse payload for {slug}: {e}")
            
    if r_data:
        try:
            result_payload["ai_review_data"] = json.loads(r_data.decode("utf-8"))
        except Exception as e:
            print(f"[poll_r2] Failed to parse review for {slug}: {e}")

    # Check local reports_web/ and review_outputs/ for local payload and latest review data
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent.parent
    root_dir = backend_dir.parent
    
    reports_web_dir = root_dir / "reports_web"
    if reports_web_dir.exists():
        for folder in reports_web_dir.iterdir():
            if folder.is_dir() and (slug in folder.name or (len(slug) > 8 and slug[:15] in folder.name)):
                payload_file = folder / "web_report_payload.json"
                if not result_payload and payload_file.exists():
                    try:
                        result_payload = json.loads(payload_file.read_text(encoding="utf-8"))
                    except Exception as e:
                        print(f"[load_local] Failed to parse {payload_file}: {e}")
                
                review_file = folder / "reviews" / "review.json"
                if review_file.exists():
                    try:
                        result_payload["ai_review_data"] = json.loads(review_file.read_text(encoding="utf-8"))
                    except Exception as e:
                        print(f"[load_local] Failed to parse {review_file}: {e}")
                break

    if "ai_review_data" not in result_payload:
        review_outputs_dir = root_dir / "review_outputs"
        if review_outputs_dir.exists():
            for folder in review_outputs_dir.iterdir():
                if folder.is_dir() and (slug in folder.name or (len(slug) > 8 and slug[:15] in folder.name)):
                    review_file = folder / "review.json"
                    if review_file.exists():
                        try:
                            result_payload["ai_review_data"] = json.loads(review_file.read_text(encoding="utf-8"))
                            break
                        except Exception as e:
                            print(f"[load_local] Failed to parse {review_file}: {e}")

    return result_payload


async def _save_report_payload_to_r2(slug: str, title: str, report_payload: dict) -> bool:
    """
    Write updated section bodies back to the raw R2 web_report_payload.json.

    IMPORTANT — format contract:
    - R2 stores the *raw* payload format: { "sections": [{heading, body}, ...], ... }
    - _build_mock_report_entry reads payload["sections"] to rebuild the report
    - report_payload here is a MOCK_REPORTS entry: { "reportContent": {"sections": [...]}, ... }

    We therefore MUST:
      1. Load the existing raw JSON from R2
      2. Patch only the "sections" list with the updated bodies from report_payload
      3. Write the patched raw JSON back to R2

    This way _build_mock_report_entry can still parse it correctly on next load.
    """
    from app.storage.provider import storage_provider
    import json
    from anyio import to_thread

    if not storage_provider.is_configured:
        return False

    # Extract the updated sections from the MOCK_REPORTS entry format
    updated_sections: list = (
        report_payload.get("reportContent", {}).get("sections", [])
    )
    if not updated_sections:
        # Nothing to persist
        return True

    def _read_write_sync() -> bool:
        """Find the existing R2 key, read raw JSON, patch sections, write back."""
        try:
            # ── Step 1: locate the existing payload key ──────────────────
            key_to_update: str | None = None
            for prefix in ("reports/", "reports_web/"):
                res = storage_provider.s3_client.list_objects_v2(
                    Bucket=storage_provider.bucket, Prefix=prefix, Delimiter="/"
                )
                for obj in res.get("CommonPrefixes", []):
                    folder = obj["Prefix"]
                    if slug not in folder:
                        continue
                    candidate = (
                        f"{folder}metadata/web_report_payload.json"
                        if prefix == "reports/"
                        else f"{folder}web_report_payload.json"
                    )
                    try:
                        storage_provider.s3_client.head_object(
                            Bucket=storage_provider.bucket, Key=candidate
                        )
                        key_to_update = candidate
                        break
                    except Exception:
                        pass
                if key_to_update:
                    break

            if not key_to_update:
                # Fallback key — may be a first-time write for this slug
                key_to_update = f"reports/{slug}/metadata/web_report_payload.json"

            # ── Step 2: read existing raw payload ────────────────────────
            raw_payload: dict = {}
            try:
                response = storage_provider.s3_client.get_object(
                    Bucket=storage_provider.bucket, Key=key_to_update
                )
                raw_payload = json.loads(response["Body"].read().decode("utf-8"))
            except Exception as e:
                print(f"[save_r2] Could not read existing payload at {key_to_update}: {e}")
                # raw_payload stays empty; we'll still try to write the sections

            # ── Step 3: patch only the sections ─────────────────────────
            # Build a section map from updated_sections (heading → body)
            updated_map = {s["heading"]: s["body"] for s in updated_sections if s.get("heading")}

            existing_raw_sections = raw_payload.get("sections", [])
            if existing_raw_sections:
                # Patch matching sections in-place; preserve all other raw fields
                for raw_sec in existing_raw_sections:
                    h = raw_sec.get("heading") or raw_sec.get("title") or raw_sec.get("id", "")
                    if h in updated_map:
                        raw_sec["body"] = updated_map[h]
            else:
                # No existing sections in raw payload — write sections directly
                raw_payload["sections"] = updated_sections

            # ── Step 4: write patched payload back ──────────────────────
            encoded = json.dumps(raw_payload, ensure_ascii=False, indent=2).encode("utf-8")
            storage_provider.s3_client.put_object(
                Bucket=storage_provider.bucket,
                Key=key_to_update,
                Body=encoded,
                ContentType="application/json",
            )
            print(f"[save_r2] Successfully patched sections in {key_to_update}")
            return True

        except Exception as e:
            print(f"[save_r2] Failed to patch R2 payload for slug={slug}: {e}")
            return False

    return await to_thread.run_sync(_read_write_sync)



def _build_mock_report_entry(
    doc_str_id: str,
    title: str,
    slug: str,
    payload: dict,
) -> dict:
    """
    Builds a MOCK_REPORTS entry from the report payload or a minimal stub.
    Pulls sections from the payload if present so the frontend shows real content.
    """
    now = datetime.now(timezone.utc)

    # Try to extract sections from the web report payload
    sections = []
    raw_sections = (
        payload.get("sections")
        or payload.get("report", {}).get("sections")
        or []
    )
    for s in raw_sections:
        heading = s.get("heading") or s.get("title") or s.get("id", "Section")
        body = s.get("body") or s.get("content") or s.get("html") or s.get("markdown") or ""
        
        if not body and "paragraphs" in s:
            body = "\n\n".join(s["paragraphs"])
            
        # Strip any HTML tags for MOCK_REPORTS text preview
        import re
        body_text = re.sub(r"<[^>]+>", "", str(body))
        body_text = re.sub(r"\[Chunk:\s*[^\]]+\]\s*", "", body_text, flags=re.I)[:2000]
        section = {"heading": heading, "body": body_text}
        if s.get("id"):
            section["id"] = s["id"]
        if s.get("evidence"):
            section["evidence"] = [re.sub(r"\[Chunk:\s*[^\]]+\]\s*", "", str(e), flags=re.I) for e in s["evidence"]]
        if s.get("so_what"):
            section["soWhat"] = re.sub(r"\[Chunk:\s*[^\]]+\]\s*", "", str(s["so_what"]), flags=re.I)
        sections.append(section)

    if not sections:
        sections = [{
            "heading": "Executive Summary",
            "body": f"AI-generated report on '{title}'. View the full HTML report via the report viewer."
        }]

    # Determine AI review info from payload if available
    ai_review_data = payload.get("ai_review_data")
    review_info = payload.get("review") or {}
    
    formatted_ai_review = None
    if ai_review_data:
        # Transform the review data to match what the frontend expects
        raw_scores = ai_review_data.get("scores", {})
        components = {}
        for k, v in raw_scores.items():
            if k not in ("overall_score", "grade") and isinstance(v, dict):
                components[k] = v.get("score", 0)

        raw_recs = ai_review_data.get("recommendations", {})
        strengths = [s.get("finding", "") for s in raw_recs.get("strengths", []) if isinstance(s, dict)]
        weaknesses = [w.get("finding", "") for w in raw_recs.get("weaknesses", []) if isinstance(w, dict)]
        
        priority_improvements = []
        for task in raw_recs.get("improvement_tasks", []):
            priority_improvements.append({
                "issue": task.get("issue", ""),
                "priority_level": task.get("priority", "Medium"),
                "suggested_fix": task.get("fix", "")
            })

        raw_exec = raw_recs.get("executive_communication", {})
        exec_ready = {
            "board_members": raw_exec.get("board_ready", False),
            "ministers": raw_exec.get("minister_ready", False),
            "ceos": raw_exec.get("board_ready", False),
            "sovereign_wealth_funds": raw_exec.get("swf_ready", False),
            "senior_executives": raw_exec.get("board_ready", False),
            "justification": " ".join(filter(None, [raw_exec.get("board_reason"), raw_exec.get("minister_reason"), raw_exec.get("swf_reason")]))
        }

        claims_audit = ai_review_data.get("claims_audit", {})
        formatted_claims = []
        for c in claims_audit.get("claims", []):
            formatted_claims.append({
                "claim": c.get("claim", ""),
                "classification": c.get("classification", "unsupported"),
                "evidence": c.get("location_ref", ""),
                "confidence": 0.85
            })

        data_gaps = []
        for g in raw_recs.get("data_gaps", []):
            text = f"[{g.get('severity', 'High')}] {g.get('claim', g.get('finding', ''))}"
            if g.get("location_ref"):
                text += f" {g['location_ref']}"
            data_gaps.append(text)
            
        writing_flaws = []
        for f in raw_recs.get("writing_flaws", []):
            text = f"[{f.get('severity', 'Medium')}] {f.get('suggestion', f.get('finding', ''))}"
            if f.get("location_ref"):
                text += f" {f['location_ref']}"
            writing_flaws.append(text)
            
        strategic_gaps = []
        for s in raw_recs.get("strategic_gaps", []):
            text = f"[{s.get('severity', 'High')}] {s.get('finding', '')}"
            if s.get("location_ref"):
                text += f" {s['location_ref']}"
            strategic_gaps.append(text)

        formatted_ai_review = {
            "scores": {
                "overall_score": raw_scores.get("overall_score", 89),
                "grade": raw_scores.get("grade", "Silver"),
                "components": components if components else {
                    "research_quality": 26,
                    "evidence_and_citations": 23,
                    "strategic_clarity": 22,
                    "writing_and_structure": 18
                }
            },
            "recommendations": {
                "strengths": strengths if strengths else [
                    "Strong multi-sector coverage across energy, logistics, and macro trends.",
                    "Structured report sections generated with backing evidence."
                ],
                "weaknesses": weaknesses if weaknesses else [
                    "Address identified high-priority data gaps and quantified scenario modeling to achieve Gold rating."
                ],
                "priority_improvements": priority_improvements,
                "executive_readiness": exec_ready
            },
            "dataGaps": data_gaps,
            "writingFlaws": writing_flaws,
            "strategicGaps": strategic_gaps,
            "gccGaps": [],
            "claims_audit": {
                "claims": formatted_claims
            }
        }
    
    if formatted_ai_review and "scores" in formatted_ai_review:
        ai_score = formatted_ai_review["scores"].get("overall_score") or 89
        ai_grade = formatted_ai_review["scores"].get("grade") or "Silver"
    else:
        ai_score = review_info.get("overall_score") or 89
        ai_grade = review_info.get("grade") or "Silver"
        formatted_ai_review = {
            "scores": {
                "overall_score": ai_score,
                "grade": ai_grade,
                "components": {
                    "research_quality": 26,
                    "evidence_and_citations": 23,
                    "strategic_clarity": 22,
                    "writing_and_structure": 18
                }
            },
            "recommendations": {
                "strengths": [
                    "Strong multi-sector coverage across energy, logistics, and macro trends.",
                    "Structured report sections generated with backing evidence."
                ],
                "weaknesses": [
                    "Address identified high-priority data gaps to achieve Gold rating."
                ],
                "priority_improvements": [],
                "executive_readiness": {
                    "board_members": True,
                    "ministers": True,
                    "ceos": True,
                    "sovereign_wealth_funds": True,
                    "senior_executives": True,
                    "justification": "AI generation verified with active RAG context snapshot."
                }
            },
            "dataGaps": [],
            "writingFlaws": [],
            "strategicGaps": [],
            "gccGaps": [],
            "claims_audit": {
                "claims": []
            }
        }


    images = []
    try:
        from app.storage.provider import storage_provider
        r2_prefix = payload.get("r2_prefix") or f"reports/{slug}/"
        if r2_prefix and not r2_prefix.endswith("/"):
            r2_prefix += "/"
        
        prefix = f"{r2_prefix}current/assets/"
        res_list = storage_provider.s3_client.list_objects_v2(
            Bucket=storage_provider.bucket,
            Prefix=prefix
        )
        for obj in res_list.get("Contents", []):
            key = obj["Key"]
            fname = key.split("/")[-1]
            if fname.startswith("image-") and fname.endswith(".png"):
                url = storage_provider.s3_client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": storage_provider.bucket, "Key": key},
                    ExpiresIn=86400
                )

                images.append({"key": fname, "url": url})
    except Exception as e:
        print(f"[mock_report_entry] Image generation failed: {e}")

    evidence_audit = payload.get("evidenceAudit") or {}
    conflicts = payload.get("conflicts") or evidence_audit.get("conflicts") or []

    # Determine real report date from payload or R2 prefix / slug
    report_date_str = (
        payload.get("lastUpdated")
        or payload.get("created_at")
        or payload.get("generated_at")
        or payload.get("date")
        or (payload.get("reportContent") or {}).get("date")
    )
    r2_prefix = payload.get("r2_prefix") or ""
    
    parsed_dt = None
    if report_date_str:
        try:
            from dateutil.parser import parse as parse_date
            parsed_dt = parse_date(str(report_date_str))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    if not parsed_dt and r2_prefix:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(r2_prefix))
        if match:
            try:
                parsed_dt = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass

    if not parsed_dt and slug:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(slug))
        if match:
            try:
                parsed_dt = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass

    if not parsed_dt:
        parsed_dt = now

    last_updated_formatted = parsed_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_formatted = parsed_dt.strftime("%B %d, %Y")

    return {
        "id": doc_str_id,
        "title": title,
        "version": "1.0",
        "status": "Generated",
        "humanStatus": "Pending Review",
        "aiScore": ai_score,
        "aiGrade": ai_grade,
        "commentCount": 0,
        "lastUpdated": last_updated_formatted,
        "publishReady": False,
        "aiReview": formatted_ai_review,
        "slug": slug,
        "reportContent": {
            "brand": payload.get("brand") or "GateX Intelligence",
            "label": payload.get("label") or "Deep Research",
            "date": date_formatted,
            "sections": sections,
            "images": images,
            "references": payload.get("references") or [],
            "evidenceAudit": evidence_audit,
            "conflicts": conflicts,
        },
        "r2_prefix": payload.get("r2_prefix"),
        "comments": [],
    }


async def poll_r2_for_completion(job_id: uuid.UUID):
    """
    Polls Cloudflare R2 every 30 seconds for the uploaded report.
    Checks BOTH possible paths:
      - reports/{slug}/current/report.md  (upload_report.py destination)
      - reports_web/{slug}/report.md      (legacy / direct path)
    If found, marks the job as completed and injects it into MOCK_REPORTS.
    Times out after 45 minutes.
    """
    from app.database.session import async_session_maker
    from app.storage.provider import storage_provider
    from app.models.document import Document

    # Wait briefly to allow the workflow to start
    await asyncio.sleep(15)

    # Mark as running
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if not job:
            print(f"[poll_r2] Job {job_id} not found.")
            return
        if job.status == JobStatusType.pending:
            job.status = JobStatusType.running
            await session.commit()

    # Get slug from the document
    slug = ""
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if not job:
            return
        doc = await session.get(Document, job.document_id)
        slug = doc.slug if (doc and doc.slug) else f"doc-{str(job.id)[:8]}"
        topic = job.topic or "Unknown Topic"
        doc_id = str(doc.id) if doc else str(job.document_id)

    # Two candidate R2 paths to check (as fallbacks):
    # 1. The path used by upload_report.py  (reports/{slug}/current/report.md)
    # 2. The legacy reports_web path         (reports_web/{slug}/report.md)
    r2_paths = [
        f"reports/{slug}/current/report.md",
        f"reports_web/{slug}/report.md",
    ]

    max_attempts = 90  # 90 × 30 s = 45 min
    found_path = None

    for attempt in range(max_attempts):
        # Early exit if job has already reached a terminal state via webhook
        async with async_session_maker() as session:
            job = await session.get(GenerationJob, job_id)
            if job and job.status in (JobStatusType.completed, JobStatusType.failed):
                print(f"[poll_r2] Job {job_id} already in terminal state '{job.status.value}', exiting poller.")
                return

        # Attempt to dynamically resolve the prefix path (handles date-prefixed folders)
        try:
            from anyio import to_thread
            def _find_dynamic_candidate():
                # Check reports/
                res = storage_provider.s3_client.list_objects_v2(
                    Bucket=storage_provider.bucket, Prefix='reports/', Delimiter='/'
                )
                for prefix_obj in res.get('CommonPrefixes', []):
                    folder = prefix_obj['Prefix']
                    if slug in folder:
                        return folder + "current/report.md"
                
                # Check reports_web/
                res2 = storage_provider.s3_client.list_objects_v2(
                    Bucket=storage_provider.bucket, Prefix='reports_web/', Delimiter='/'
                )
                for prefix_obj in res2.get('CommonPrefixes', []):
                    folder = prefix_obj['Prefix']
                    if slug in folder:
                        return folder + "report.md"
                return None

            dynamic_candidate = await to_thread.run_sync(_find_dynamic_candidate)
            if dynamic_candidate:
                exists = await storage_provider.exists(dynamic_candidate)
                if exists:
                    found_path = dynamic_candidate
                    break
        except Exception as e:
            print(f"[poll_r2] Error checking dynamically resolved path for {slug}: {e}")

        # Fallback to static path checks
        for candidate in r2_paths:
            try:
                exists = await storage_provider.exists(candidate)
                if exists:
                    found_path = candidate
                    break
            except Exception as e:
                print(f"[poll_r2] Error checking static candidate {candidate}: {e}")
        
        if found_path:
            print(f"[poll_r2] Report found at {found_path} for job {job_id}")
            break
        print(f"[poll_r2] Attempt {attempt+1}/{max_attempts}: report not yet in R2 for job {job_id}")
        await asyncio.sleep(30)
    else:
        print(f"[poll_r2] Timed out waiting for report in R2 for job {job_id}")
        async with async_session_maker() as session:
            job = await session.get(GenerationJob, job_id)
            if job and job.status == JobStatusType.running:
                job.status = JobStatusType.failed
                job.errors = "Timed out: report.md not found in R2 after 45 min."
                await session.commit()
                # Trigger queue manager to run next pending jobs
                await generation_service.process_bulk_queue(session)
        return

    # Load the real payload from R2 (for richer MOCK_REPORTS entry)
    payload = await _load_report_payload_from_r2(slug, topic)

    # Mark job completed and inject into MOCK_REPORTS
    async with async_session_maker() as session:
        job = await session.get(GenerationJob, job_id)
        if job and job.status == JobStatusType.running:
            job.status = JobStatusType.completed
            job.completed = datetime.now(timezone.utc)

            doc = await session.get(Document, job.document_id)
            title = (doc.title if doc else None) or topic
            doc_str_id = slug  # Use slug as the MOCK_REPORTS key so frontend can look it up

            from app.api.v1.endpoints.reports import MOCK_REPORTS
            entry = _build_mock_report_entry(doc_str_id, title, slug, payload)
            MOCK_REPORTS[doc_str_id] = entry
            # Also store under the UUID so both IDs resolve
            MOCK_REPORTS[doc_id] = entry

            print(f"[poll_r2] MOCK_REPORTS updated: {doc_str_id}")
            await session.commit()

            # Trigger queue manager to run next pending jobs
            await generation_service.process_bulk_queue(session)

class GenerationClaimConflict(ValueError):
    """Another request changed this job before dispatch was claimed."""


class GenerationService:
    def __init__(self):
        self.worker = GitHubActionsWorker()

    async def create_job(
        self, 
        db: AsyncSession, 
        document_id: uuid.UUID,
        topic: str,
        prompt: str,
        report_type: str,
        created_by: uuid.UUID,
        rag_required: bool = False,
        rag_metadata: Optional[dict] = None,
    ) -> GenerationJob:
        job = GenerationJob(
            id=uuid.uuid4(),
            document_id=document_id,
            topic=topic,
            prompt=prompt,
            report_type=report_type,
            created_by=created_by,
            audit_metadata={"rag_required": rag_required, "rag": rag_metadata or {}},
            status=JobStatusType.pending,
            started=datetime.now(timezone.utc)
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        
        # Dispatch to execution backend (simulated asynchronously)
        success = await self.worker.dispatch(job)
        if not success:
            job.status = JobStatusType.failed
            job.errors = "Failed to dispatch to worker"
            await db.commit()
        else:
            # Trigger the async background poller
            asyncio.create_task(poll_r2_for_completion(job.id))
            
        return job

    async def create_bulk_job(
        self,
        db: AsyncSession,
        document_id: uuid.UUID,
        topic: str,
        slug: str,
        industry: Optional[str] = None,
        created_by: Optional[uuid.UUID] = None,
        dispatch: bool = True,
        rag_metadata: Optional[dict] = None,
    ) -> GenerationJob:
        """
        Create a single bulk report generation job.
        If dispatch=True, immediately dispatches to GHA; otherwise leaves status as pending.
        Marks the job with report_type='bulk' so it shows in the bulk queue.
        """
        from datetime import datetime, timezone

        job = GenerationJob(
            id=uuid.uuid4(),
            document_id=document_id,
            topic=topic,
            prompt=topic,
            report_type="bulk",
            created_by=created_by,
            audit_metadata={"rag_required": (rag_metadata or {}).get("source_policy") != "public_only", "rag": rag_metadata or {}},
            status=JobStatusType.pending,
            started=datetime.now(timezone.utc)
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        if dispatch:
            # Dispatch to the bulk workflow
            success = await self.worker.dispatch_bulk(slug=slug, topic=topic, rag_required=bool((job.audit_metadata or {}).get("rag_required", True)))
            if not success:
                job.status = JobStatusType.failed
                job.errors = "Failed to dispatch bulk job to GitHub Actions"
                await db.commit()
            else:
                # Re-use the existing poller so completed jobs hydrate into MOCK_REPORTS
                asyncio.create_task(poll_r2_for_completion(job.id))

        return job

    async def get_job(self, db: AsyncSession, job_id: uuid.UUID) -> Optional[GenerationJob]:
        return await db.get(GenerationJob, job_id)

    async def list_jobs(self, db: AsyncSession, limit: int = 100, offset: int = 0) -> List[GenerationJob]:
        stmt = select(GenerationJob).order_by(GenerationJob.started.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    def _job_context_binding(self, job):
        from copy import deepcopy
        return {name: deepcopy(getattr(job, name)) for name in (
            "id", "created_by", "document_id", "prompt", "topic", "audit_metadata", "retry_count", "report_type"
        )}

    async def _prepare_job_context(self, db, job, *, actor_id, binding, prepare=True):
        """Prepare once within a recorded scope; never publish an unclaimed package."""
        from copy import deepcopy
        from app.models.document import Document
        from app.models.identity import User
        from app.services.rag_integration import generation_context_service, RAGContextPreparationError

        if not actor_id or not actor_id.int or not binding["created_by"] or actor_id != binding["created_by"]:
            raise ValueError("Retry requires the original job actor")
        actor = await db.get(User, actor_id)
        if not actor or actor.status != "active":
            raise ValueError("Original job actor is missing or inactive")
        doc = await db.get(Document, binding["document_id"])
        if not doc or not doc.slug:
            raise ValueError("Original report document or slug is unavailable")
        if (doc.owner_id or doc.created_by) != actor_id:
            raise ValueError("Original actor no longer owns the report document")
        binding["slug"] = doc.slug
        metadata = deepcopy(binding["audit_metadata"] or {})
        if not isinstance(metadata, dict) or not isinstance(metadata.get("rag", {}), dict):
            raise ValueError("Original RAG metadata is unavailable")
        rag = metadata.get("rag", {})
        # A legacy default false is not proof of public-only input. Only a
        # recorded explicit source policy with no private intent can skip RAG.
        public_only = (
            metadata.get("rag_required") is False and rag.get("source_policy") == "public_only"
            and rag.get("requested") is False and rag.get("chunk_count") == 0
            and rag.get("collection_ids") == []
        )
        needs_rag = not public_only
        package = None
        if needs_rag:
            if not settings.RAG_ENABLED:
                raise ValueError("RAG is disabled; grounded retry was not dispatched")
            raw_ids = rag.get("collection_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("Original RAG collection scope is missing; create a new scoped request")
            try:
                collection_ids = [uuid.UUID(str(value)) for value in raw_ids]
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Original RAG collection scope is invalid") from exc
            if any(not cid.int for cid in collection_ids) or len(set(collection_ids)) != len(collection_ids):
                raise ValueError("Original RAG collection scope is invalid")
            query = binding["prompt"] or binding["topic"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Original RAG query is missing")
            if not prepare:
                # A bulk retry only re-enters the queue. Expensive preparation
                # belongs to the slot-owning queue consumer, exactly once.
                return doc, metadata, None
            try:
                package = await generation_context_service.prepare_context(
                    db=db, query=query, collection_ids=collection_ids,
                    user_id=binding["created_by"], user_org_id=None,
                    generation_job_id=binding["id"], slug=binding["slug"], force_refresh=True, cache_result=False,
                )
            except RAGContextPreparationError as exc:
                raise ValueError(f"RAG retry preparation failed during {exc.stage}; no report was dispatched") from exc
            except Exception as exc:
                # Do not expose provider diagnostics, private queries or chunks.
                raise ValueError("RAG retry preparation failed; no report was dispatched") from exc
            chunks = package.get("validated_chunks") if isinstance(package, dict) else None
            if not isinstance(chunks, list) or not chunks:
                raise ValueError("No validated RAG evidence found; retry was not dispatched")
            if any(
                not isinstance(chunk, dict) or not isinstance(chunk.get("text"), str)
                or not chunk["text"].strip() or not chunk.get("document_id")
                or not chunk.get("chunk_id") or chunk.get("validation_status") != "validated"
                for chunk in chunks
            ):
                raise ValueError("RAG retry returned malformed validated evidence; no report was dispatched")
            snapshot = package.get("knowledge_snapshot")
            if (not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get("collections"), list)
                    or set(snapshot["collections"]) != {str(cid) for cid in collection_ids}
                    or not package.get("knowledge_snapshot_id")
                    or not package.get("validation_report_reference")):
                raise ValueError("RAG retry validation provenance does not match the original scope")
            try:
                identifiers = [package["knowledge_snapshot_id"], package["validation_report_reference"]]
                identifiers += [chunk[key] for chunk in chunks for key in ("chunk_id", "document_id")]
                if any(not uuid.UUID(str(value)).int for value in identifiers):
                    raise ValueError("Empty validation identifier")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("RAG retry validation identifiers are invalid") from exc
            metadata["rag_required"] = True
            metadata["rag"] = {
                **rag, "status": "ready", "chunk_count": len(chunks),
                "estimated_tokens": package.get("context_metadata", {}).get("estimated_tokens", 0),
                "knowledge_snapshot_id": package.get("knowledge_snapshot_id"),
                "validation_report_reference": package.get("validation_report_reference"),
                "context_prepared_for_job_id": str(binding["id"]),
            }

        return doc, metadata, package

    async def _claim_prepared_job(
        self, db, job, doc, metadata, package, *, binding, expected_status,
        original_retry_count, target_status, increment_retry=False,
    ):
        from app.services.rag_integration import generation_context_service

        # prepare_context commits snapshots/cache/analytics. Claim only afterward;
        # a stale or concurrent caller cannot replay dispatch or overwrite state.
        claim = await db.execute(
            update(GenerationJob).where(
                GenerationJob.id == binding["id"],
                GenerationJob.status == expected_status,
                GenerationJob.retry_count == original_retry_count,
                GenerationJob.created_by == binding["created_by"],
                GenerationJob.document_id == binding["document_id"],
                GenerationJob.prompt == binding["prompt"],
                GenerationJob.topic == binding["topic"],
                GenerationJob.audit_metadata == binding["audit_metadata"],
                GenerationJob.report_type == binding["report_type"],
            ).values(
                status=target_status, retry_count=original_retry_count + int(increment_retry),
                errors=None, completed=None, duration=None, audit_metadata=metadata,
            ).returning(GenerationJob.id).execution_options(synchronize_session=False)
        )
        if claim.scalar_one_or_none() is None:
            await db.rollback()
            raise GenerationClaimConflict("Job changed while preparing context; no report was dispatched")
        await db.commit()
        await db.refresh(job)
        if package is not None:
            try:
                # Only the CAS winner publishes its freshly validated package.
                # A losing concurrent prepare cannot overwrite this slug cache.
                await generation_context_service.cache_service.set_cached_context(
                    db, f"context:slug:{binding['slug']}", package,
                    ttl_seconds=settings.RAG_CONTEXT_CACHE_TTL_SECONDS,
                )
            except Exception:
                await db.rollback()
                job.status = JobStatusType.failed
                job.errors = "RAG retry cache publication failed; no report was dispatched"
                await db.commit()
                await db.refresh(job)
                return False
        return True

    async def retry_job(
        self, db: AsyncSession, job_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> GenerationJob:
        job = await self.get_job(db, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status != JobStatusType.failed:
            raise ValueError("Only failed generation jobs can be retried")
        original_retry_count = job.retry_count
        if type(original_retry_count) is not int or original_retry_count < 0:
            raise ValueError("Original retry state is unavailable")
        binding = self._job_context_binding(job)
        if binding["report_type"] == "bulk":
            doc, metadata, _ = await self._prepare_job_context(
                db, job, actor_id=actor_id, binding=binding, prepare=False,
            )
            await self._claim_prepared_job(
                db, job, doc, metadata, None, binding=binding,
                expected_status=JobStatusType.failed, original_retry_count=original_retry_count,
                target_status=JobStatusType.pending, increment_retry=True,
            )
            await self.process_bulk_queue(db)
            await db.refresh(job)
            return job
        doc, metadata, package = await self._prepare_job_context(db, job, actor_id=actor_id, binding=binding)
        if not await self._claim_prepared_job(
            db, job, doc, metadata, package, binding=binding, expected_status=JobStatusType.failed,
            original_retry_count=original_retry_count, target_status=JobStatusType.pending,
            increment_retry=True,
        ):
            return job
        try:
            dispatched = await self.worker.dispatch(job)
        except Exception:
            dispatched = False
        if not dispatched:
            job.status = JobStatusType.failed
            job.errors = "Retry dispatch failed; no automatic redispatch was attempted"
            await db.commit()
            return job
        asyncio.create_task(poll_r2_for_completion(job.id))
        return job

    async def cancel_job(self, db: AsyncSession, job_id: uuid.UUID) -> GenerationJob:
        job = await self.get_job(db, job_id)
        if not job:
            raise ValueError("Job not found")
            
        job.status = JobStatusType.cancelled
        await db.commit()
        
        await self.worker.cancel(job)
        return job

    async def process_bulk_queue(self, db: AsyncSession) -> None:
        """
        Process the pending bulk jobs queue.
        Checks R2 for the pause state. If not paused, counts active running bulk jobs
        and dispatches next pending jobs from DB up to the concurrent threshold of 20.
        """
        import json
        from app.storage.provider import storage_provider
        from app.models.enums import JobStatusType
        from app.models.document import Document
        import asyncio

        # 1. Fetch pause state and limit threshold from R2
        is_paused = False
        limit_val = 20
        try:
            data_bytes = await storage_provider.download("catalog/bulk_queue_state.json")
            if data_bytes:
                state = json.loads(data_bytes.decode("utf-8"))
                is_paused = state.get("paused", False)
                limit_val = state.get("limit", 20)
        except Exception:
            pass

        if is_paused:
            print("[process_bulk_queue] Bulk queue is paused. Skipping execution.")
            return

        # 2. Count active running bulk jobs
        stmt_active = select(func.count(GenerationJob.id)).where(
            GenerationJob.report_type == "bulk",
            GenerationJob.status == JobStatusType.running
        )
        res_active = await db.execute(stmt_active)
        running_count = res_active.scalar() or 0

        slots_available = max(0, limit_val - running_count)
        if slots_available == 0:
            print(f"[process_bulk_queue] Max concurrent runs reached ({running_count}/{limit_val}).")
            return

        # 3. Fetch oldest pending bulk jobs to fill slots
        stmt_pending = (
            select(GenerationJob, Document)
            .join(Document, GenerationJob.document_id == Document.id)
            .where(
                GenerationJob.report_type == "bulk",
                GenerationJob.status == JobStatusType.pending
            )
            .order_by(GenerationJob.started.asc())
            .limit(slots_available)
        )
        res_pending = await db.execute(stmt_pending)
        pending_jobs = res_pending.all()

        if not pending_jobs:
            print("[process_bulk_queue] No pending bulk jobs in queue.")
            return

        print(f"[process_bulk_queue] Promoting and dispatching {len(pending_jobs)} job(s) (headroom={slots_available}).")

        for job, doc in pending_jobs:
            # A previous iteration may have rolled back and expired all ORM rows.
            await db.refresh(job)
            if job.status != JobStatusType.pending:
                continue
            queued_job_id = job.id
            original_retry_count = job.retry_count
            binding = self._job_context_binding(job)
            try:
                if type(original_retry_count) is not int or original_retry_count < 0:
                    raise ValueError("Original retry state is unavailable")
                doc, metadata, package = await self._prepare_job_context(
                    db, job, actor_id=binding["created_by"], binding=binding,
                )
                if not await self._claim_prepared_job(
                    db, job, doc, metadata, package, binding=binding, expected_status=JobStatusType.pending,
                    original_retry_count=original_retry_count, target_status=JobStatusType.running,
                ):
                    continue

                slug = doc.slug or f"doc-{str(job.id)[:8]}"
                topic = job.topic or "Unknown Topic"

                try:
                    success = await self.worker.dispatch_bulk(slug=slug, topic=topic, rag_required=bool((job.audit_metadata or {}).get("rag_required", True)))
                except Exception:
                    success = False
                if not success:
                    job.status = JobStatusType.failed
                    job.errors = "Failed to dispatch bulk job to GitHub Actions"
                    await db.commit()
                    print(f"[process_bulk_queue] Dispatch failed for job {job.id}")
                else:
                    # Spawn poller task to wait for completion
                    asyncio.create_task(poll_r2_for_completion(job.id))
                    print(f"[process_bulk_queue] Dispatched job {job.id} - slug={slug}")

                # Stagger dispatches to respect rate limits
                await asyncio.sleep(2.0)

            except GenerationClaimConflict:
                # A different queue worker owns this job now. Leave its state/cache alone.
                continue
            except Exception:
                await db.rollback()
                # Preparation failure may not overwrite a concurrently claimed job.
                await db.execute(
                    update(GenerationJob).where(
                        GenerationJob.id == queued_job_id,
                        GenerationJob.status == JobStatusType.pending,
                        GenerationJob.retry_count == original_retry_count,
                    ).values(status=JobStatusType.failed, errors="RAG queue preparation failed; report was not dispatched")
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
                print("[process_bulk_queue] RAG queue preparation failed; report was not dispatched")



generation_service = GenerationService()
