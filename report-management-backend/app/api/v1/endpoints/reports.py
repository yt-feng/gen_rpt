from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from app.api.deps import get_db, PageParams, FilterParams, get_current_user_placeholder
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()


# Global mock state to allow frontend updates to persist across API calls
MOCK_REPORTS = {}


@router.get("/", response_model=APIResponse[list])
async def list_reports(
    page: PageParams = Depends(),
    filters: FilterParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List all reports based on filters and pagination.
    Merges in-memory MOCK_REPORTS with actual completed GenerationJobs from the DB.
    """
    from sqlalchemy import select
    from app.models.workflow import GenerationJob
    from app.models.document import Document
    from app.models.enums import JobStatusType
    from app.models.identity import User

    # Base list from MOCK_REPORTS
    mock_reports_dict = {r["id"]: r for r in MOCK_REPORTS.values() if "id" in r}

    # Fetch completed jobs from DB to persist across server restarts, joining the User table
    stmt = (
        select(GenerationJob, Document, User)
        .join(Document, GenerationJob.document_id == Document.id)
        .outerjoin(User, Document.owner_id == User.id)
        .where(GenerationJob.status == JobStatusType.completed)
        .order_by(GenerationJob.started.desc())
    )
    result = await db.execute(stmt)
    
    for row in result.all():
        job, doc, owner = row[0], row[1], row[2]
        doc_id = doc.slug or str(doc.id)
        # Avoid overriding if already present in MOCK_REPORTS (it might have richer real-time data)
        if doc_id not in mock_reports_dict:
            mock_reports_dict[doc_id] = {
                "id": doc_id,
                "title": doc.title or job.topic,
                "version": "1.0",
                "status": "Generated",
                "humanStatus": "Pending Review",
                "aiScore": 85,
                "aiGrade": "Silver",
                "commentCount": 0,
                "lastUpdated": (job.completed or job.started).isoformat() + "Z",
                "publishReady": False,
                "aiReview": None,
                "slug": doc.slug,
                "assignedTo": {
                    "id": str(owner.id),
                    "full_name": owner.full_name,
                    "email": owner.email
                } if owner else None,
                "reportContent": {
                    "brand": "GateX",
                    "label": "Deep Research",
                    "date": (job.completed or job.started).strftime("%B %d, %Y"),
                    "sections": [
                        {"heading": "Executive Summary", "body": "Click 'View Report' to load full report content."}
                    ]
                },
                "comments": []
            }
        else:
            # If already present in MOCK_REPORTS, ensure it has the owner sync'd from DB if not set
            if owner and not mock_reports_dict[doc_id].get("assignedTo"):
                mock_reports_dict[doc_id]["assignedTo"] = {
                    "id": str(owner.id),
                    "full_name": owner.full_name,
                    "email": owner.email
                }
    
    reports_list = list(mock_reports_dict.values())

    # --- Reconcile with persisted GateXPublication records ---
    # This ensures Published status survives server restarts (MOCK_REPORTS is in-memory)
    try:
        from app.models.workflow import GateXPublication
        from sqlalchemy import select
        import hashlib, uuid as _uuid

        stmt = select(
            GateXPublication.document_id,
            GateXPublication.publish_status,
            GateXPublication.external_report_id
        ).where(
            GateXPublication.publish_status.in_(["published", "unpublished"])
        )
        result = await db.execute(stmt)
        pub_rows = result.all()

        # Build a map of doc_uuid -> (publish_status, external_id)
        pub_map = {str(row[0]): (row[1], row[2]) for row in pub_rows}

        for report in reports_list:
            rid = report.get("id", "")
            # Compute UUID that the orchestrator would have used
            try:
                doc_uuid = str(_uuid.UUID(rid))
            except ValueError:
                m = hashlib.md5()
                m.update(rid.encode("utf-8"))
                doc_uuid = str(_uuid.UUID(m.hexdigest()))

            if doc_uuid in pub_map:
                db_status, ext_id = pub_map[doc_uuid]
                if db_status == "published" and report.get("status") != "Published":
                    report["status"] = "Published"
                    report["publishStatus"] = "published"
                    report["externalReportId"] = ext_id
                elif db_status == "unpublished" and report.get("status") not in ("Rejected",):
                    report["status"] = "Rejected"
                    report["publishStatus"] = "unpublished"
    except Exception as _e:
        pass  # DB reconciliation is best-effort; never break the reports list

    # Simple mock filtering to support frontend tabs
    if filters.status:
        reports_list = [r for r in reports_list if r["status"].lower() == filters.status.lower()]

    return success_response(
        data=reports_list,
        message="Fetched mock reports successfully",
        metadata={"total": len(reports_list), "offset": page.offset, "limit": page.limit, "has_more": False}
    )

@router.get("/{document_id}", response_model=APIResponse[dict])
async def get_report_details(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get detailed metadata for a specific report document.
    """
    from app.models.identity import User
    from sqlalchemy import select
    
    if document_id in MOCK_REPORTS:
        report = MOCK_REPORTS[document_id]
        # Sync owner details just in case they're in DB but missing in cache
        if not report.get("assignedTo"):
            from app.models.document import Document
            stmt = select(Document).where(Document.slug == document_id)
            res = await db.execute(stmt)
            doc = res.scalar_one_or_none()
            if doc and doc.owner_id:
                owner_res = await db.execute(select(User).where(User.id == doc.owner_id))
                owner = owner_res.scalar_one_or_none()
                if owner:
                    report["assignedTo"] = {
                        "id": str(owner.id),
                        "full_name": owner.full_name,
                        "email": owner.email
                    }
        # Backfill images if missing in cached reportContent
        report_content = report.get("reportContent", {})
        if "images" not in report_content or not report_content["images"]:
            images = []
            try:
                from app.storage.provider import storage_provider
                slug = report.get("slug") or document_id
                r2_prefix = report.get("r2_prefix") or f"reports/{slug}/"
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
                            ExpiresIn=3600
                        )
                        images.append({"key": fname, "url": url})
                report_content["images"] = images
            except Exception as e:
                print(f"[get_report_details] Fallback image generation failed: {e}")
        return success_response(data=report, message="Fetched report details")
        
    # If not in MOCK_REPORTS, try loading dynamically from R2
    from app.models.document import Document
    from app.services.generation import _load_report_payload_from_r2, _build_mock_report_entry
    import uuid

    stmt = select(Document).where(Document.slug == document_id)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    
    if not doc:
        try:
            doc_uuid = uuid.UUID(document_id)
            doc = await db.get(Document, doc_uuid)
        except ValueError:
            pass
            
    if doc:
        slug = doc.slug or str(doc.id)
        topic = doc.title or slug
        payload = await _load_report_payload_from_r2(slug, topic)
        entry = _build_mock_report_entry(document_id, topic, slug, payload)
        
        # Load owner details if set in DB
        if doc.owner_id:
            owner_res = await db.execute(select(User).where(User.id == doc.owner_id))
            owner = owner_res.scalar_one_or_none()
            if owner:
                entry["assignedTo"] = {
                    "id": str(owner.id),
                    "full_name": owner.full_name,
                    "email": owner.email
                }
        else:
            entry["assignedTo"] = None

        MOCK_REPORTS[document_id] = entry
        MOCK_REPORTS[str(doc.id)] = entry
        if doc.slug:
            MOCK_REPORTS[doc.slug] = entry
        return success_response(data=entry, message="Loaded report details from storage")

    # No DB row — try loading directly from R2 by the slug itself.
    # This handles bulk-generated reports that bypassed the frontend UI.
    from app.logging.logger import logger
    try:
        payload = await _load_report_payload_from_r2(document_id, document_id)
        if payload:
            title = (
                payload.get("topic")
                or payload.get("title")
                or document_id.replace('-', ' ').title()
            )
            entry = _build_mock_report_entry(document_id, title, document_id, payload)
            entry["assignedTo"] = None
            MOCK_REPORTS[document_id] = entry
            return success_response(data=entry, message="Loaded report details directly from R2")
    except Exception as e:
        logger.warning(f"[get_report_details] R2 fallback failed for {document_id}: {e}")

    # Last-resort fallback
    report = MOCK_REPORTS.get(document_id, MOCK_REPORTS["doc-3333-review"])
    return success_response(data=report, message="Fetched fallback report details")

from pydantic import BaseModel
from typing import Optional

class StatusUpdatePayload(BaseModel):
    status: Optional[str] = None
    humanStatus: Optional[str] = None
    publishReady: Optional[bool] = None

@router.post("/{document_id}/status", response_model=APIResponse[dict])
async def update_report_status(
    document_id: str,
    payload: StatusUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Update the status of a specific report.
    """
    report = MOCK_REPORTS.get(document_id)
    if not report:
        # fallback for new generated jobs
        report = MOCK_REPORTS["doc-3333-review"].copy()
        report["id"] = document_id
        MOCK_REPORTS[document_id] = report
        
    if payload.status is not None:
        report["status"] = payload.status
    if payload.humanStatus is not None:
        report["humanStatus"] = payload.humanStatus
    if payload.publishReady is not None:
        report["publishReady"] = payload.publishReady

    # When a report is explicitly re-approved, clear the "unpublished" DB record
    # so the reconciliation logic does NOT override it back to "Rejected"
    if payload.status in ("Approved", "approved"):
        import uuid, hashlib
        from sqlalchemy import update as sql_update
        from app.models.workflow import GateXPublication
        try:
            try:
                doc_uuid = uuid.UUID(document_id)
            except ValueError:
                m = hashlib.md5()
                m.update(document_id.encode("utf-8"))
                doc_uuid = uuid.UUID(m.hexdigest())
            await db.execute(
                sql_update(GateXPublication)
                .where(GateXPublication.document_id == doc_uuid)
                .where(GateXPublication.publish_status == "unpublished")
                .values(publish_status="re_approved")
            )
            await db.commit()
            # Also clear in-memory markers so it doesn't linger
            report["publishStatus"] = None
            report["externalReportId"] = None
        except Exception:
            pass  # Best-effort — never break the status update
        
    # Removed the legacy 'Needs Revision' full report generation job.
    # Revisions are now handled by the surgical /revise-section endpoint.
        
    return success_response(data=report, message="Report status updated")

class SectionRevisionRequest(BaseModel):
    section_heading: str
    instructions: str

@router.post("/{document_id}/revise-section", response_model=APIResponse[dict])
async def revise_section(
    document_id: str,
    req: SectionRevisionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Surgically revise a single section of the report using AI.
    """
    import httpx
    import os

    if document_id not in MOCK_REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    report = MOCK_REPORTS[document_id]
    
    if req.section_heading == "Overall Report":
        # Full report regeneration
        title = report.get("title", "")
        industry = report.get("industry", "")
        topic_input = title
        if industry:
            topic_input = f"{title} (Sector: {industry})"
            
        slug = report.get("slug") or document_id
        
        # 1. Delete previous files from R2
        from app.services.generation import delete_report_files_from_r2, GitHubActionsWorker
        await delete_report_files_from_r2(slug)
        
        # 2. Dispatch GHA workflow
        worker = GitHubActionsWorker()
        success = await worker.dispatch_single_report(topic=topic_input, slug=slug)
        
        if success:
            # Update status to running so frontend queue updates
            report["status"] = "running"
            report["humanStatus"] = "Regenerating"
            return success_response(data=report, message="Full report regeneration started.")
        else:
            raise HTTPException(status_code=500, detail="Failed to dispatch full report regeneration to GitHub Actions.")

    original_text = ""
    target_section = None
    
    for section in report.get("reportContent", {}).get("sections", []):
        if section.get("heading") == req.section_heading:
            original_text = section.get("body", "")
            target_section = section
            break
            
    if not target_section:
        raise HTTPException(status_code=404, detail="Section not found in report")

    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY")
    
    if not api_key:
        new_text = f"Revised content in accordance with reviewer guidance: {original_text}"
    else:
        try:
            is_groq = "gsk_" in api_key
            
            if is_groq:
                url = "https://api.groq.com/openai/v1/chat/completions"
                model = "llama-3.3-70b-versatile"
            else:
                url = "https://api.deepseek.com/chat/completions"
                model = "deepseek-chat"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            prompt = f"Rewrite the following report section based on these instructions from a reviewer:\nReviewer Instructions: {req.instructions}\n\nOriginal Section Text:\n{original_text}"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a professional report editor. Return only the edited text without any conversational filler or quotes. Maintain the professional tone of the report."},
                    {"role": "user", "content": prompt}
                ]
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    new_text = data["choices"][0]["message"]["content"].strip()
                else:
                    return error_response(message=f"AI API failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            return error_response(message=f"AI API request failed: {str(e)}")

    # Update the section
    target_section["body"] = new_text

    # Try to push the updated JSON back to R2 and trigger PDF generation
    try:
        from app.services.generation import _save_report_payload_to_r2
        from app.services.pdf_release import pdf_release_service
        
        slug = report.get("slug") or document_id
        await _save_report_payload_to_r2(slug, report.get("title", ""), report)
        
        # Reconstruct updated markdown and save to R2 as well
        markdown_lines = []
        for sec in report.get("reportContent", {}).get("sections", []):
            h = sec.get("heading", "")
            b = sec.get("body", "")
            if h:
                markdown_lines.append(f"## {h}\n")
            markdown_lines.append(f"{b}\n")
        updated_md = "\n".join(markdown_lines)
        
        try:
            folder_prefix = None
            from app.storage.provider import storage_provider
            import json
            for prefix in ("reports/", "reports_web/"):
                res_obj = storage_provider.s3_client.list_objects_v2(
                    Bucket=storage_provider.bucket, Prefix=prefix, Delimiter="/"
                )
                for obj in res_obj.get("CommonPrefixes", []):
                    folder = obj["Prefix"]
                    if slug in folder:
                        folder_prefix = folder
                        break
                if folder_prefix:
                    break
            
            if not folder_prefix:
                folder_prefix = f"reports/{slug}/"
                
            report_md_path = f"{folder_prefix}current/report.md"
            try:
                resp = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=f"{folder_prefix}manifest.json")
                m_data = json.loads(resp['Body'].read().decode('utf-8'))
                if m_data.get("files", {}).get("report_md"):
                    report_md_path = m_data["files"]["report_md"]
            except Exception:
                pass
                
            storage_provider.s3_client.put_object(
                Bucket=storage_provider.bucket,
                Key=report_md_path,
                Body=updated_md.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
        except Exception as e_md:
            print(f"Failed to write report.md to R2: {e_md}")
            
        actor_id = str(user.get("id")) if user and user.get("id") else "00000000-0000-0000-0000-000000000000"
        await pdf_release_service.get_or_generate(db, slug, report, actor_id)
    except Exception as e:
        print(f"Failed to sync revised report to R2 or PDF: {e}")

    return success_response(
        data={"edited_text": new_text}, 
        message="Section revised successfully"
    )

@router.get("/{document_id}/download-url", response_model=APIResponse[dict])
async def get_report_download_url(
    document_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Generate a signed URL for secure downloading of report artifacts.
    """
    from app.services.storage import storage_service
    url = await storage_service.get_signed_url(db, file_id)
    if not url:
        return error_response(message="File not found or unauthorized")
    return success_response(data={"url": url}, message="Generated signed URL successfully")

from pydantic import BaseModel
class AIEditRequest(BaseModel):
    documentId: str
    action: str
    paragraphId: str
    text: str

@router.post("/edit", response_model=APIResponse[dict])
async def ai_edit_block(
    req: AIEditRequest,
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Handle AI Rewrite toolbar actions from the frontend for specific document blocks.
    Replaces the exact paragraph text in the mock database.
    """
    import httpx
    import os
    from app.core.config import settings

    doc_id = req.documentId
    if doc_id not in MOCK_REPORTS:
        return error_response(message="Report not found")

    report = MOCK_REPORTS[doc_id]
    original_text = req.text.strip()
    
    # Simple prompt depending on action
    prompt_instruction = "Rewrite the following text."
    if req.action == "expand":
        prompt_instruction = "Expand on the following text, providing more detail and context."
    elif req.action == "rewrite":
        prompt_instruction = "Rewrite the following text to make it more concise and professional."
    elif req.action == "regenerate":
        prompt_instruction = "Completely regenerate the following text, providing a fresh perspective."

    # Call AI (DeepSeek or Groq if available)
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY")

    if not api_key:
        if req.action == "expand":
            new_text = f"{original_text} Furthermore, additional strategic indicators confirm that these dynamics are critical to achieving sustained long-term performance objectives."
        elif req.action == "rewrite":
            new_text = f"Regarding the primary subject matter: {original_text}"
        else:
            new_text = f"Based on updated analytical parameters: {original_text}"
    else:
        try:
            is_groq = "gsk_" in api_key
            
            if is_groq:
                url = "https://api.groq.com/openai/v1/chat/completions"
                model = "llama-3.3-70b-versatile"
            else:
                url = "https://api.deepseek.com/chat/completions"
                model = "deepseek-chat"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a professional report editor. Return only the edited text without any conversational filler or quotes."},
                    {"role": "user", "content": f"{prompt_instruction}\n\n{original_text}"}
                ]
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=20.0)
                if resp.status_code == 200:
                    data = resp.json()
                    new_text = data["choices"][0]["message"]["content"].strip()
                else:
                    return error_response(message=f"AI API failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            return error_response(message=f"AI API request failed: {str(e)}")

    # Find the paragraph in the report content and replace it
    updated = False
    for section in report.get("reportContent", {}).get("sections", []):
        body = section.get("body", "")
        if original_text in body:
            # Replace the paragraph in the body
            section["body"] = body.replace(original_text, new_text)
            updated = True
            break
            
    if not updated:
        return error_response(message="Could not find the specified text in the report body to edit.")

    return success_response(
        data={"edited_text": new_text}, 
        message=f"AI {req.action} applied successfully"
    )

@router.post("/{document_id}/claim", response_model=APIResponse[dict])
async def claim_report(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Claim a report and assign it to the currently logged in reviewer.
    """
    from app.models.document import Document
    from app.models.identity import User
    from sqlalchemy import select
    import uuid
    
    # 1. Verify user exists in the DB
    stmt = select(User).where(User.email == user["email"])
    res = await db.execute(stmt)
    db_user = res.scalar_one_or_none()
    if not db_user:
        # Create user record dynamically if missing
        db_user = User(
            id=uuid.UUID(user["id"]),
            full_name=user["full_name"],
            email=user["email"],
            status="active"
        )
        db.add(db_user)
        await db.commit()
    
    # 2. Find document
    stmt = select(Document).where(Document.slug == document_id)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    
    if not doc:
        try:
            doc_uuid = uuid.UUID(document_id)
            doc = await db.get(Document, doc_uuid)
        except ValueError:
            pass
            
    if not doc:
        return error_response(message="Document not found")
        
    # 3. Update owner_id in DB
    doc.owner_id = db_user.id
    await db.commit()
    
    # 4. Update in-memory MOCK_REPORTS
    report = MOCK_REPORTS.get(document_id)
    if not report:
        report = MOCK_REPORTS.get(doc.slug) or MOCK_REPORTS.get(str(doc.id))
        
    if report:
        report["assignedTo"] = {
            "id": str(db_user.id),
            "full_name": db_user.full_name,
            "email": db_user.email
        }
        report["humanStatus"] = "In Progress"
        MOCK_REPORTS[document_id] = report
        MOCK_REPORTS[doc.slug] = report
        MOCK_REPORTS[str(doc.id)] = report
        
    return success_response(
        data={
            "document_id": document_id,
            "assignedTo": {
                "id": str(db_user.id),
                "full_name": db_user.full_name,
                "email": db_user.email
            }
        },
        message="Report claimed successfully"
    )


# ── Inline Text Edit ────────────────────────────────────────────────────────
# Accepts a map of paragraphId → newText from the frontend contentEditable editor.
# paragraphId format: "<section-slug>-p<N>" e.g. "key-highlights-p2"
# Strategy: scan every section body, split into paragraphs, match by 1-based
# counter (the same logic used by paragraphId() in the frontend), replace
# matching paragraphs, and persist the update back to R2.

class ContentEditPayload(BaseModel):
    edits: dict  # { paragraphId: str -> newText: str }

@router.put("/{document_id}/content", response_model=APIResponse[dict])
async def save_content_edits(
    document_id: str,
    payload: ContentEditPayload,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
):
    """
    Persist inline text edits made via the contentEditable document viewer.

    Accepts a dict of { paragraphId: newText } pairs and applies them to the
    report's section bodies.  After updating MOCK_REPORTS, the new payload is
    pushed back to R2 and a fresh PDF is generated so that the next publish
    operation uses the updated text.
    """
    import re

    report = MOCK_REPORTS.get(document_id)
    if not report:
        return error_response(message="Report not found")

    if not payload.edits:
        return success_response(data={}, message="No edits to apply")

    # paragraphId format (mirrors frontend paragraphId() util):
    #   "<section-heading-slugified>-p<N>"
    # e.g. heading "Key Highlights" -> "key-highlights" -> "key-highlights-p2"
    def heading_slug(heading: str) -> str:
        return re.sub(r"[^\w]+", "-", heading.lower()).strip("-")

    applied: list[str] = []
    skipped: list[str] = []

    for para_id, new_text in payload.edits.items():
        # Parse paragraphId: split off "-p<N>" suffix
        m = re.match(r"^(.+)-p(\d+)$", para_id)
        if not m:
            skipped.append(para_id)
            continue

        target_slug = m.group(1)
        target_idx = int(m.group(2))  # 1-based

        found = False
        for section in report.get("reportContent", {}).get("sections", []):
            if heading_slug(section.get("heading", "")) != target_slug:
                continue

            # Split body into paragraphs the same way as the frontend
            raw_paragraphs = [p.strip() for p in section.get("body", "").split("\n\n") if p.strip()]

            para_counter = 0
            new_paragraphs = list(raw_paragraphs)
            for i, para in enumerate(raw_paragraphs):
                lines = para.split("\n")
                is_list = any(
                    l.strip().startswith("- ") or bool(re.match(r"^\d+\.", l.strip()))
                    for l in lines
                )
                if is_list:
                    continue  # lists don't get paragraph IDs
                para_counter += 1
                if para_counter == target_idx:
                    new_paragraphs[i] = new_text.strip()
                    found = True
                    break

            if found:
                section["body"] = "\n\n".join(new_paragraphs)
                applied.append(para_id)
                break

        if not found:
            skipped.append(para_id)

    # Update in-memory store with all key aliases
    slug = report.get("slug") or document_id
    MOCK_REPORTS[document_id] = report
    MOCK_REPORTS[slug] = report

    # Persist to R2 + force PDF regeneration (best-effort; never fails the request)
    try:
        from app.services.generation import _save_report_payload_to_r2
        from app.services.pdf_release import pdf_release_service
        
        await _save_report_payload_to_r2(slug, report.get("title", ""), report)
        
        # Reconstruct updated markdown and save to R2 as well
        markdown_lines = []
        for sec in report.get("reportContent", {}).get("sections", []):
            h = sec.get("heading", "")
            b = sec.get("body", "")
            if h:
                markdown_lines.append(f"## {h}\n")
            markdown_lines.append(f"{b}\n")
        updated_md = "\n".join(markdown_lines)
        
        try:
            folder_prefix = None
            from app.storage.provider import storage_provider
            import json
            for prefix in ("reports/", "reports_web/"):
                res_obj = storage_provider.s3_client.list_objects_v2(
                    Bucket=storage_provider.bucket, Prefix=prefix, Delimiter="/"
                )
                for obj in res_obj.get("CommonPrefixes", []):
                    folder = obj["Prefix"]
                    if slug in folder:
                        folder_prefix = folder
                        break
                if folder_prefix:
                    break
            
            if not folder_prefix:
                folder_prefix = f"reports/{slug}/"
                
            report_md_path = f"{folder_prefix}current/report.md"
            try:
                resp = storage_provider.s3_client.get_object(Bucket=storage_provider.bucket, Key=f"{folder_prefix}manifest.json")
                m_data = json.loads(resp['Body'].read().decode('utf-8'))
                if m_data.get("files", {}).get("report_md"):
                    report_md_path = m_data["files"]["report_md"]
            except Exception:
                pass
                
            storage_provider.s3_client.put_object(
                Bucket=storage_provider.bucket,
                Key=report_md_path,
                Body=updated_md.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
        except Exception as e_md:
            print(f"Failed to write report.md to R2: {e_md}")
            
        actor_id = str(user.get("id")) if user and user.get("id") else "00000000-0000-0000-0000-000000000000"
        await pdf_release_service.get_or_generate(db, slug, report, actor_id)
    except Exception as e:
        print(f"[content-edit] R2/PDF sync failed (non-fatal): {e}")

    return success_response(
        data={"applied": applied, "skipped": skipped},
        message=f"Applied {len(applied)} edit(s) successfully",
    )


@router.post("/{document_id}/replace-image", response_model=APIResponse[dict])
async def replace_report_image(
    document_id: str,
    image_key: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Replace a specific image in the report's R2 assets folder.
    The new image is uploaded to the exact same R2 key so that:
      - The report viewer picks it up immediately (new presigned URL returned).
      - PDF generation (which resolves relative asset paths from R2) automatically
        uses the new image on the next PDF build — no other files need updating.
    """
    from app.storage.provider import storage_provider

    # Locate report in memory cache
    report = MOCK_REPORTS.get(document_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    slug = report.get("slug") or document_id
    r2_prefix = report.get("r2_prefix") or f"reports/{slug}/"
    if r2_prefix and not r2_prefix.endswith("/"):
        r2_prefix += "/"

    # Safety: only allow replacing known image files (prevent path traversal)
    safe_key = image_key.split("/")[-1]  # strip any path prefix the caller may have sent
    if not (safe_key.endswith(".png") or safe_key.endswith(".jpg") or safe_key.endswith(".jpeg")):
        raise HTTPException(status_code=400, detail="Only PNG and JPG images are supported")

    r2_key = f"{r2_prefix}current/assets/{safe_key}"
    print(f"[replace-image] Uploading {safe_key} to R2 key: {r2_key}")

    # Read file bytes
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    content_type = file.content_type or "image/png"

    # PUT to R2 (idempotent — overwrites existing object at same key)
    success = await storage_provider.upload(content_bytes, r2_key, content_type)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to upload image to R2")

    # Generate a fresh presigned GET URL for the new image
    new_url = await storage_provider.get_signed_url(r2_key, expiration_sec=3600)

    # Update in-memory cache so subsequent GET /reports/:id returns the fresh URL
    report_content = report.get("reportContent", {})
    for img in report_content.get("images", []):
        if img.get("key") == safe_key:
            img["url"] = new_url
            break

    print(f"[replace-image] Successfully replaced {safe_key} for document {document_id}")
    return success_response(
        data={"key": safe_key, "url": new_url},
        message="Image replaced successfully",
    )
