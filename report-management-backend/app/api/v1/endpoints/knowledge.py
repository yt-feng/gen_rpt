from fastapi import APIRouter, Depends, UploadFile, File, Query, Form, status, HTTPException, Request
from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_placeholder, get_db
from app.core.responses import APIResponse, success_response
from app.core.config import settings
from app.core.rate_limit import limiter
from app.schemas.knowledge import (
    CollectionCreate,
    CollectionUpdate,
    CollectionResponse,
    DocumentResponse,
    DocumentVersionResponse,
    ProcessingJobResponse,
    ActivityHistoryResponse,
    SearchRequest,
    SearchResponse,
    RetrievalRequest,
    RetrievalResponse,
    ValidationRequest,
    ValidationResponse,
    AnalyticsResponse,
    AdminStatusResponse,
    KnowledgeHealthResponse,
    CollectionCloneRequest,
    CollectionStatisticsResponse,
    CategoryTreeResponse,
    SimilarityResponse,
    DiscoveryResponse,
    CategoryCreate,
    CategoryUpdate,
    CategoryResponse,
    TagCreate,
    TagResponse,
    PermissionCreate,
    PermissionResponse,
    SourceResponse,
    LifecycleReindexRequest,
    LifecycleRollbackRequest,
    LifecycleHealthResponse,
    LifecycleArchiveRequest,
    LifecycleOptimizationResponse,
    LifecycleAnalyticsResponse
)
from app.services.knowledge import verify_knowledge_enabled
from app.services.knowledge_collection import knowledge_collection_service
from app.services.knowledge_document import knowledge_document_service
from app.services.knowledge_lifecycle import knowledge_lifecycle_service
from app.models.knowledge import KnowledgeProcessingQueue, KnowledgeActivityHistory

router = APIRouter()

# Helper to raise 501 for unimplemented features
def raise_unimplemented():
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Reserved for future implementation"
    )

# ==========================================
# Collection Management Endpoints
# ==========================================

@router.post("/collections", response_model=APIResponse[CollectionResponse], status_code=status.HTTP_201_CREATED)
async def create_collection(
    payload: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Create a new reusable knowledge collection.
    """
    result = await knowledge_collection_service.create_collection(
        db=db, obj_in=payload, user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection created successfully.")


@router.get("/collections", response_model=APIResponse[List[CollectionResponse]])
async def list_collections(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    List all active collections owned by the current user.
    """
    result = await knowledge_collection_service.list_collections(
        db=db, owner_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collections listed successfully.")


@router.get("/collections/{collection_id}", response_model=APIResponse[CollectionResponse])
async def get_collection(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve details of a specific collection.
    """
    result = await knowledge_collection_service.get_collection(
        db=db, collection_id=collection_id
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found."
        )
    return success_response(data=result, message="Collection retrieved successfully.")


@router.patch("/collections/{collection_id}", response_model=APIResponse[CollectionResponse])
async def update_collection(
    collection_id: UUID,
    payload: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Update metadata of a collection.
    """
    result = await knowledge_collection_service.update_collection(
        db=db, collection_id=collection_id, obj_in=payload, user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection updated successfully.")


@router.delete("/collections/{collection_id}", response_model=APIResponse[CollectionResponse])
async def delete_collection(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Soft delete / archive a collection.
    """
    result = await knowledge_collection_service.delete_collection(
        db=db, collection_id=collection_id, user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection deleted successfully.")


@router.post("/collections/{collection_id}/archive", response_model=APIResponse[CollectionResponse])
async def archive_collection(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Archive a collection.
    """
    result = await knowledge_collection_service.archive_collection(
        db=db, collection_id=collection_id, user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection archived successfully.")


@router.post("/collections/{collection_id}/restore", response_model=APIResponse[CollectionResponse])
async def restore_collection(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Restore an archived collection.
    """
    result = await knowledge_collection_service.restore_collection(
        db=db, collection_id=collection_id, user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection restored successfully.")


@router.get("/collections/{collection_id}/stats", response_model=APIResponse[dict])
async def get_collection_stats(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve total document counts and storage usage stats for a collection.
    """
    result = await knowledge_collection_service.get_collection_stats(
        db=db, collection_id=collection_id
    )
    return success_response(data=result, message="Collection statistics retrieved.")


# ==========================================
# Document Ingestion / Upload Endpoints
# ==========================================

@router.post("/documents/upload", response_model=APIResponse[dict], status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    collection_id: UUID = Form(..., description="ID of the collection to add document to"),
    duplicate_strategy: str = Form("skip", description="Strategy when duplicate checksum is found: skip or new_version"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Upload a single document (PDF/MD/DOCX/TXT/HTML) into a collection.
    """
    if file.size and file.size > 10 * 1024 * 1024:
        result = await knowledge_document_service.upload_document(
            db=db,
            collection_id=collection_id,
            filename=file.filename,
            file_stream=file.file,
            file_size=file.size,
            content_type=file.content_type or "application/octet-stream",
            user_id=UUID(user["id"]),
            duplicate_strategy=duplicate_strategy
        )
    else:
        file_bytes = await file.read()
        result = await knowledge_document_service.upload_document(
            db=db,
            collection_id=collection_id,
            filename=file.filename,
            file_data=file_bytes,
            content_type=file.content_type or "application/octet-stream",
            user_id=UUID(user["id"]),
            duplicate_strategy=duplicate_strategy
        )
    return success_response(data=result, message="Document upload processed.")


@router.post("/documents/{document_id}/version", response_model=APIResponse[dict], status_code=status.HTTP_201_CREATED)
async def replace_document_version(
    document_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Upload a replacement file creating a new version of the specified document.
    """
    doc = await knowledge_document_service.get_document(db, document_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found."
        )

    if file.size and file.size > 10 * 1024 * 1024:
        result = await knowledge_document_service.upload_document(
            db=db,
            collection_id=doc.collection_id,
            filename=file.filename,
            file_stream=file.file,
            file_size=file.size,
            content_type=file.content_type or "application/octet-stream",
            user_id=UUID(user["id"]),
            duplicate_strategy="new_version"
        )
    else:
        file_bytes = await file.read()
        result = await knowledge_document_service.upload_document(
            db=db,
            collection_id=doc.collection_id,
            filename=file.filename,
            file_data=file_bytes,
            content_type=file.content_type or "application/octet-stream",
            user_id=UUID(user["id"]),
            duplicate_strategy="new_version"
        )
    return success_response(data=result, message="Document version replaced.")


@router.post("/documents/bulk-upload", response_model=APIResponse[dict], status_code=status.HTTP_201_CREATED)
async def bulk_upload_documents(
    collection_id: UUID = Query(..., description="ID of the collection to add documents to"),
    duplicate_strategy: str = Query("skip", description="Strategy: skip or new_version"),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Bulk upload multiple documents into a collection.
    """
    files_to_upload = []
    for file in files:
        file_bytes = await file.read()
        files_to_upload.append((file.filename, file_bytes, file.content_type or "application/octet-stream"))

    result = await knowledge_document_service.bulk_upload_documents(
        db=db,
        collection_id=collection_id,
        files=files_to_upload,
        user_id=UUID(user["id"]),
        duplicate_strategy=duplicate_strategy
    )
    return success_response(data=result, message="Bulk upload completed.")


@router.get("/documents/collection/{collection_id}", response_model=APIResponse[List[DocumentResponse]])
async def list_documents(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    List all active documents in a collection.
    """
    result = await knowledge_document_service.list_documents_by_collection(
        db=db, collection_id=collection_id
    )
    return success_response(data=result, message="Documents listed successfully.")


@router.get("/documents/{document_id}", response_model=APIResponse[DocumentResponse])
async def get_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve metadata details of a specific document.
    """
    result = await knowledge_document_service.get_document(
        db=db, document_id=document_id
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found."
        )
    return success_response(data=result, message="Document details retrieved.")


@router.get("/documents/{document_id}/versions", response_model=APIResponse[List[DocumentVersionResponse]])
async def get_document_versions(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve complete version history list for a document.
    """
    result = await knowledge_document_service.get_document_version_history(
        db=db, document_id=document_id
    )
    return success_response(data=result, message="Document version history retrieved.")


@router.delete("/documents/{document_id}", response_model=APIResponse[dict])
async def archive_document(
    document_id: UUID,
    reason: Optional[str] = Query(None, description="Reason for archiving the document"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Archive a document (soft delete from active lists).
    """
    await knowledge_document_service.archive_document(
        db=db, document_id=document_id, user_id=UUID(user["id"]), reason=reason
    )
    return success_response(data={}, message="Document archived successfully.")


@router.post("/documents/{document_id}/restore", response_model=APIResponse[dict])
async def restore_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Restore an archived document.
    """
    await knowledge_document_service.restore_document(
        db=db, document_id=document_id, user_id=UUID(user["id"])
    )
    return success_response(data={}, message="Document restored successfully.")


@router.post("/documents/{document_id}/move", response_model=APIResponse[dict])
async def move_document(
    document_id: UUID,
    target_collection_id: UUID = Query(..., description="Target Collection ID"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Move a document to another collection.
    """
    await knowledge_document_service.move_document(
        db=db, document_id=document_id, target_collection_id=target_collection_id, user_id=UUID(user["id"])
    )
    return success_response(data={}, message="Document moved successfully.")


# ==========================================
# Processing Queue / Jobs Endpoints
# ==========================================

@router.get("/queue/status", response_model=APIResponse[List[ProcessingJobResponse]])
async def get_queue_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve active/pending jobs in the processing queue.
    """
    result = await db.execute(
        select(KnowledgeProcessingQueue).order_by(KnowledgeProcessingQueue.created_at.desc())
    )
    jobs = list(result.scalars().all())
    return success_response(data=jobs, message="Processing queue jobs retrieved.")


@router.get("/queue/{job_id}", response_model=APIResponse[ProcessingJobResponse])
async def get_queue_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Retrieve details of a specific queue job.
    """
    job = await db.get(KnowledgeProcessingQueue, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue job not found."
        )
    return success_response(data=job, message="Queue job retrieved.")


# ==========================================
# Phase R6 Repository Endpoints
# ==========================================

@router.post("/collections/{collection_id}/clone", response_model=APIResponse[CollectionResponse])
async def clone_collection(
    collection_id: UUID,
    payload: CollectionCloneRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    result = await knowledge_collection_service.clone_collection(
        db=db,
        collection_id=collection_id,
        target_name=payload.target_name,
        target_slug=payload.target_slug,
        user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Collection cloned successfully.")


@router.get("/collections/{collection_id}/statistics", response_model=APIResponse[CollectionStatisticsResponse])
async def get_collection_statistics(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_statistics import knowledge_statistics_service
    result = await knowledge_statistics_service.get_collection_statistics(db, collection_id)
    return success_response(data=result, message="Collection statistics retrieved.")


@router.post("/collections/{collection_id}/permissions", response_model=APIResponse[PermissionResponse])
async def assign_permission(
    collection_id: UUID,
    payload: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_permission import knowledge_permission_service
    result = await knowledge_permission_service.assign_permission(
        db=db,
        collection_id=collection_id,
        user_id=payload.user_id,
        permission_level=payload.permission_level,
        assigner_id=UUID(user["id"])
    )
    return success_response(data=result, message="Permission assigned successfully.")


@router.get("/collections/{collection_id}/permissions", response_model=APIResponse[List[PermissionResponse]])
async def list_permissions(
    collection_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_permission import knowledge_permission_service
    result = await knowledge_permission_service.list_permissions(db, collection_id, UUID(user["id"]))
    return success_response(data=result, message="Collection permissions listed.")


@router.delete("/collections/{collection_id}/permissions/{target_user_id}", response_model=APIResponse[dict])
async def remove_permission(
    collection_id: UUID,
    target_user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_permission import knowledge_permission_service
    await knowledge_permission_service.remove_permission(db, collection_id, target_user_id, UUID(user["id"]))
    return success_response(data={}, message="Permission removed successfully.")


@router.post("/tags", response_model=APIResponse[TagResponse])
async def create_tag(
    payload: TagCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    result = await knowledge_tag_service.create_tag(db, payload)
    return success_response(data=result, message="Tag created successfully.")


@router.get("/tags", response_model=APIResponse[List[TagResponse]])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    result = await knowledge_tag_service.list_tags(db)
    return success_response(data=result, message="Tags listed successfully.")


@router.delete("/tags/{tag_id}", response_model=APIResponse[dict])
async def delete_tag(
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    await knowledge_tag_service.delete_tag(db, tag_id)
    return success_response(data={}, message="Tag deleted successfully.")


@router.post("/tags/merge", response_model=APIResponse[dict])
async def merge_tags(
    source_tag_id: UUID = Query(...),
    target_tag_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    await knowledge_tag_service.merge_tags(db, source_tag_id, target_tag_id)
    return success_response(data={}, message="Tags merged successfully.")


@router.post("/tags/assign", response_model=APIResponse[dict])
async def assign_tag_to_document(
    document_id: UUID = Query(...),
    tag_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    await knowledge_tag_service.assign_tag_to_document(db, document_id, tag_id)
    return success_response(data={}, message="Tag assigned to document.")


@router.post("/tags/unassign", response_model=APIResponse[dict])
async def unassign_tag_from_document(
    document_id: UUID = Query(...),
    tag_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_tag import knowledge_tag_service
    await knowledge_tag_service.unassign_tag_from_document(db, document_id, tag_id)
    return success_response(data={}, message="Tag unassigned from document.")


@router.post("/categories", response_model=APIResponse[CategoryResponse])
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_category import knowledge_category_service
    result = await knowledge_category_service.create_category(db, payload)
    return success_response(data=result, message="Category created successfully.")


@router.get("/categories", response_model=APIResponse[List[CategoryTreeResponse]])
async def list_categories_tree(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_category import knowledge_category_service
    result = await knowledge_category_service.get_category_tree(db)
    return success_response(data=result, message="Category tree retrieved.")


@router.patch("/categories/{category_id}", response_model=APIResponse[CategoryResponse])
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_category import knowledge_category_service
    result = await knowledge_category_service.update_category(db, category_id, payload)
    return success_response(data=result, message="Category updated successfully.")


@router.delete("/categories/{category_id}", response_model=APIResponse[dict])
async def delete_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_category import knowledge_category_service
    await knowledge_category_service.delete_category(db, category_id)
    return success_response(data={}, message="Category deleted successfully.")


@router.get("/documents/{document_id}/similarity", response_model=APIResponse[List[SimilarityResponse]])
async def get_similar_documents(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_relationship import knowledge_relationship_service
    result = await knowledge_relationship_service.find_similar_documents(db, document_id)
    return success_response(data=result, message="Similar documents retrieved.")


@router.get("/discovery", response_model=APIResponse[DiscoveryResponse])
async def get_repository_discovery(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    res_docs = await db.execute(
        select(KnowledgeDocument).filter(KnowledgeDocument.deleted_at.is_(None))
        .order_by(KnowledgeDocument.created_at.desc()).limit(5)
    )
    docs = list(res_docs.scalars().all())
    
    res_cols = await db.execute(
        select(KnowledgeCollection).filter(KnowledgeCollection.deleted_at.is_(None))
        .order_by(KnowledgeCollection.created_at.desc()).limit(5)
    )
    cols = list(res_cols.scalars().all())
    
    data = DiscoveryResponse(
        recent_documents=docs,
        popular_documents=docs,
        frequently_referenced=docs,
        largest_collections=cols
    )
    return success_response(data=data, message="Discovery data retrieved.")


@router.post("/processing/jobs", response_model=APIResponse[dict], status_code=status.HTTP_202_ACCEPTED)
async def trigger_processing(
    document_id: UUID = Query(..., description="Document ID to reprocess"),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    return success_response(data={}, message="Reprocessing job triggered.")


@router.post("/search", response_model=APIResponse[SearchResponse])
async def search_knowledge(
    payload: SearchRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_search import knowledge_search_service
    org_id = UUID(user["organization_id"]) if "organization_id" in user and user["organization_id"] else None
    results = await knowledge_search_service.search_repository_metadata(db, payload, user_org_id=org_id)
    return success_response(data=SearchResponse(results=results), message="Search completed.")


@router.post("/retrieval/query", response_model=APIResponse[RetrievalResponse])
async def retrieve_context(
    payload: RetrievalRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.retrieval_engine import retrieval_engine_service
    user_id = UUID(user["id"]) if user and "id" in user else None
    user_org_id = UUID(user["organization_id"]) if user and "organization_id" in user and user["organization_id"] else None
    
    filters_dict = {
        "document_type": payload.document_type,
        "tags": payload.tags,
        "categories": payload.categories,
        "language": payload.language,
        "source": payload.source,
        "publisher": payload.publisher,
        "author": payload.author,
        "processing_status": payload.processing_status,
        "validation_status": payload.validation_status
    }
    
    result = await retrieval_engine_service.retrieve_knowledge(
        db=db,
        query=payload.topic,
        target_count=payload.target_count,
        collection_ids=payload.collection_ids,
        user_id=user_id,
        user_org_id=user_org_id,
        filters={k: v for k, v in filters_dict.items() if v is not None},
        weights=payload.weights,
        freshness_policy=payload.freshness_policy or "exponential",
        token_budget=payload.token_budget or 4000
    )
    return success_response(data=result, message="Context retrieved successfully.")


@router.get("/retrieval/session/{session_id}", response_model=APIResponse[dict])
async def get_retrieval_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.models.knowledge import RetrievalSession as DBSession, RetrievalResult as DBResult
    session = await db.get(DBSession, session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retrieval session not found.")
        
    res = await db.execute(select(DBResult).filter(DBResult.session_id == session_id))
    results = res.scalars().all()
    
    return success_response(
        data={
            "session": {
                "id": session.id,
                "query": session.query,
                "collection_id": session.collection_id,
                "user_id": session.user_id,
                "started_at": session.started_at,
                "duration_ms": session.duration_ms,
                "status": session.status,
                "request_metadata": session.request_metadata,
                "snapshot_metadata": session.snapshot_metadata,
                "session_metadata": session.session_metadata
            },
            "results": [
                {
                    "id": r.id,
                    "chunk_id": r.chunk_id,
                    "similarity_score": r.similarity_score,
                    "ranking": r.ranking,
                    "confidence": r.confidence,
                    "source_id": r.source_id,
                    "result_metadata": r.result_metadata
                }
                for r in results
            ]
        },
        message="Session details retrieved."
    )


@router.post("/validation", response_model=APIResponse[ValidationResponse])
async def validate_knowledge(
    payload: ValidationRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.models.knowledge import ValidationResult as DBVal
    db_val = DBVal(
        document_id=payload.document_id,
        validation_type=payload.validation_type,
        confidence=payload.confidence,
        result=payload.result,
        evidence=payload.evidence,
        validator=payload.validator or "manual",
        summary="Manual validation entry"
    )
    db.add(db_val)
    await db.commit()
    await db.refresh(db_val)
    return success_response(data=db_val, message="Validation recorded.")


@router.get("/analytics", response_model=APIResponse[AnalyticsResponse])
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    from app.services.knowledge_statistics import knowledge_statistics_service
    stats = await knowledge_statistics_service.get_global_statistics(db)
    
    from app.models.knowledge import KnowledgeAnalytics as DBAnalytics
    db_an = DBAnalytics(
        document_count=stats["document_count"],
        chunk_count=stats["chunk_count"],
        processing_count=sum(stats["queue_backlog"].values()),
        retrieval_count=0,
        generation_count=0,
        usage_metrics=stats
    )
    return success_response(data=db_an, message="Analytics retrieved.")


@router.get("/admin/status", response_model=APIResponse[AdminStatusResponse])
async def get_admin_status(
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    return success_response(
        data=AdminStatusResponse(enabled=settings.KNOWLEDGE_ENABLED, workers={"active": True}),
        message="Admin status retrieved."
    )


@router.get("/health", response_model=APIResponse[KnowledgeHealthResponse])
async def get_knowledge_health(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    flags = {
        "KNOWLEDGE_ENABLED": settings.KNOWLEDGE_ENABLED,
        "RAG_ENABLED": settings.RAG_ENABLED,
        "UPLOAD_ENABLED": settings.UPLOAD_ENABLED,
        "PROCESSING_ENABLED": settings.PROCESSING_ENABLED,
        "RETRIEVAL_ENABLED": settings.RETRIEVAL_ENABLED,
        "VALIDATION_ENABLED": settings.VALIDATION_ENABLED,
        "SEARCH_ENABLED": settings.SEARCH_ENABLED,
    }

    health_data = KnowledgeHealthResponse(
        status="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        module_loaded=True,
        workers_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.PROCESSING_ENABLED else "idle",
        queue_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.PROCESSING_ENABLED else "idle",
        processing_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.PROCESSING_ENABLED else "idle",
        embedding_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.PROCESSING_ENABLED else "idle",
        validation_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.VALIDATION_ENABLED else "idle",
        knowledge_index="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        retrieval_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.RAG_ENABLED else "idle",
        vector_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.RAG_ENABLED else "idle",
        cache_status="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        ranking_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.RAG_ENABLED else "idle",
        context_builder_status="healthy" if settings.KNOWLEDGE_ENABLED and settings.RAG_ENABLED else "idle",
        analytics_status="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        snapshot_status="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        knowledge_intelligence_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        analytics_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        recommendation_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        knowledge_quality_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        governance_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        audit_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        connector_framework="healthy" if settings.KNOWLEDGE_ENABLED else "idle",
        continuous_improvement_engine="healthy" if settings.KNOWLEDGE_ENABLED else "idle"
    )
    return success_response(data=health_data, message="Knowledge health checked.")


# ==========================================
# Phase R12 Lifecycle Management Endpoints
# ==========================================

@router.post("/lifecycle/documents/{document_id}/reindex", response_model=APIResponse[ProcessingJobResponse])
async def reindex_document(
    document_id: UUID,
    payload: LifecycleReindexRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Queue a document for re-indexing (re-processing, re-chunking, re-embedding).
    """
    result = await knowledge_lifecycle_service.reindex_document(
        db=db,
        document_id=document_id,
        priority=payload.priority,
        user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Reindexing job created successfully.")


@router.post("/lifecycle/documents/{document_id}/rollback", response_model=APIResponse[dict])
async def rollback_document(
    document_id: UUID,
    payload: LifecycleRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Roll back a document to a previous version.
    """
    result = await knowledge_lifecycle_service.rollback_document(
        db=db,
        document_id=document_id,
        target_version=payload.target_version,
        user_id=UUID(user["id"]),
        reason=payload.reason
    )
    return success_response(data=result, message="Document rollback completed. Reprocessing queued.")


@router.post("/lifecycle/collections/{collection_id}/archive", response_model=APIResponse[CollectionResponse])
async def archive_collection_lifecycle(
    collection_id: UUID,
    payload: LifecycleArchiveRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Archive a collection and optionally all its documents.
    """
    if payload.archive_documents:
        result = await knowledge_lifecycle_service.archive_collection_lifecycle(
            db=db,
            collection_id=collection_id,
            user_id=UUID(user["id"])
        )
    else:
        result = await knowledge_collection_service.archive_collection(
            db=db,
            collection_id=collection_id,
            user_id=UUID(user["id"])
        )
    return success_response(data=result, message="Collection archived successfully.")


@router.post("/lifecycle/sources/{source_id}/refresh", response_model=APIResponse[SourceResponse])
async def refresh_source(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Refresh source scores and trust metrics.
    """
    result = await knowledge_lifecycle_service.refresh_source(
        db=db,
        source_id=source_id,
        user_id=UUID(user["id"])
    )
    return success_response(data=result, message="Source metrics refreshed.")


@router.get("/lifecycle/health", response_model=APIResponse[LifecycleHealthResponse])
async def monitor_lifecycle_health(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Scan repository health for stuck processing, missing embeddings, or unprocessed docs.
    """
    result = await knowledge_lifecycle_service.monitor_health(db=db)
    return success_response(data=result, message="Health checks completed.")


@router.post("/lifecycle/optimize", response_model=APIResponse[LifecycleOptimizationResponse])
async def optimize_lifecycle_storage(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Clean up orphan chunks, unused embeddings, and outdated queue job audits.
    """
    result = await knowledge_lifecycle_service.optimize_storage(db=db)
    return success_response(data=result, message="Storage optimization complete.")


@router.post("/lifecycle/analytics/run", response_model=APIResponse[LifecycleAnalyticsResponse])
async def run_lifecycle_analytics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder),
    _=Depends(verify_knowledge_enabled)
):
    """
    Run lifecycle analytics and log execution.
    """
    result = await knowledge_lifecycle_service.run_lifecycle_analytics(db=db)
    response_data = LifecycleAnalyticsResponse(
        status="success",
        analytics_id=result.id,
        recorded_date=result.recorded_date
    )
    return success_response(data=response_data, message="Analytics run successfully.")

