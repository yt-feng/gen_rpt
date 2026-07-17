import pytest
import uuid
import json
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, func
import pytest_asyncio

from app.models.base import Base
from app.models.knowledge import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeProcessingQueue,
    KnowledgeChunk,
    EmbeddingMetadata,
    KnowledgeRelationship,
    ValidationResult
)
from app.main import app
from app.core.config import settings
from app.services.knowledge_storage import knowledge_storage_service
from app.services.knowledge_processing.engine import KnowledgeProcessingEngine

# Test Database setup
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(scope="function", autouse=True)
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestingSessionLocal() as session:
        from app.database.session import get_db
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        yield session
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_text_extractors():
    from app.services.knowledge_processing.workers.extraction import extract_document_text
    
    # 1. TXT
    txt_bytes = b"Hello world text document content."
    text, meta = extract_document_text(txt_bytes, "txt")
    assert "Hello world" in text
    assert meta["word_count"] == 5
    
    # 2. MD
    md_bytes = b"# Heading 1\nThis is a paragraph with [link](http://url.com).\n## Heading 2\nSecond section."
    text, meta = extract_document_text(md_bytes, "md")
    assert "Heading 1" in text
    assert "Second section" in text
    assert len(meta["headings"]) == 2
    assert meta["headings"][0]["level"] == 1
    
    # 3. HTML
    html_bytes = b"<html><body><h1>Title</h1><p>Paragraph content</p></body></html>"
    text, meta = extract_document_text(html_bytes, "html")
    assert "Title" in text
    assert "Paragraph content" in text
    
    # 4. DOCX
    docx_bytes = b"dummy docx text" # fails zip, defaults to utf-8 decode
    text, meta = extract_document_text(docx_bytes, "docx")
    assert "dummy docx text" in text

@pytest.mark.asyncio
async def test_end_to_end_processing_pipeline(db_session, monkeypatch):
    # Enable Knowledge features
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PROCESSING_ENABLED", True)
    monkeypatch.setattr(settings, "VALIDATION_ENABLED", True)
    
    user_id = uuid.uuid4()
    
    # 1. Create a Collection
    collection = KnowledgeCollection(
        id=uuid.uuid4(),
        name="Processing Test Collection",
        slug="proc-test",
        owner_id=user_id
    )
    db_session.add(collection)
    await db_session.commit()
    
    # 2. Create a Document
    doc_id = uuid.uuid4()
    storage_path = f"knowledge/collections/{collection.id}/documents/{doc_id}/v1/sample.txt"
    doc = KnowledgeDocument(
        id=doc_id,
        collection_id=collection.id,
        file_name="sample.txt",
        original_file_name="sample.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="dummychecksum",
        storage_path=storage_path,
        size=100,
        processing_status="pending",
        upload_status="uploaded",
        validation_status="pending",
        created_by=user_id
    )
    db_session.add(doc)
    
    # Target document for cross-document relationship resolution
    target_doc = KnowledgeDocument(
        id=uuid.uuid4(),
        collection_id=collection.id,
        file_name="Google_info.txt",
        original_file_name="Google_info.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="otherchecksum",
        storage_path="path/Google_info.txt",
        size=100,
        processing_status="completed",
        upload_status="uploaded",
        validation_status="validated",
        created_by=user_id
    )
    db_session.add(target_doc)
    await db_session.commit()
    
    # 3. Mock R2 storage download to return sample document text
    sample_text = (
        "# Main Title\n\n"
        "This is the first paragraph. Google makes Gemini. Python is used in FastAPI.\n\n"
        "## Sub Heading\n\n"
        "This is the second paragraph under subheading. DeepMind is a branch of Google."
    )
    async def mock_download(path):
        return sample_text.encode("utf-8")
        
    async def mock_upload(data, path, content_type=None):
        return True

    async def mock_generate_chunk_embeddings(chunks, model=None):
        import time, hashlib
        from datetime import datetime, timezone
        res = []
        for c in chunks:
            vector = [0.1] * 1536
            res.append({
                "chunk_id": c["id"],
                "chunk_number": c["chunk_number"],
                "embedding_model": "text-embedding-3-small",
                "embedding_version": "1.0.0",
                "dimension": 1536,
                "status": "completed",
                "generated_time": datetime.now(timezone.utc),
                "provider": "openai",
                "latency": 0.01,
                "vector": vector,
                "checksum": hashlib.sha256(str(vector).encode("utf-8")).hexdigest()
            })
        return res
        
    monkeypatch.setattr(knowledge_storage_service.provider, "download", mock_download)
    monkeypatch.setattr(knowledge_storage_service.provider, "upload", mock_upload)
    
    import app.services.knowledge_processing.engine as engine_module
    monkeypatch.setattr(engine_module, "generate_chunk_embeddings", mock_generate_chunk_embeddings)
    
    # 4. Create Queue Job
    job_id = uuid.uuid4()
    job = KnowledgeProcessingQueue(
        id=job_id,
        document_id=doc_id,
        status="pending",
        attempts=0,
        max_attempts=3
    )
    db_session.add(job)
    await db_session.commit()
    
    # 5. Run Engine
    engine_processor = KnowledgeProcessingEngine()
    success = await engine_processor.process_document_job(db_session, job_id)
    
    assert success is True
    
    # 6. Verify database records
    # Document state updated
    db_session.expire_all()
    res = await db_session.execute(select(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id))
    updated_doc = res.scalars().first()
    assert updated_doc.processing_status == "completed"
    assert updated_doc.validation_status == "validated"
    assert updated_doc.page_count == 1
    assert updated_doc.language == "en"
    
    # Queue state updated
    q_res = await db_session.execute(select(KnowledgeProcessingQueue).filter(KnowledgeProcessingQueue.id == job_id))
    updated_job = q_res.scalars().first()
    assert updated_job.status == "completed"
    
    # Chunks generated
    c_res = await db_session.execute(select(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc_id))
    chunks = c_res.scalars().all()
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.character_count > 0
        assert chunk.token_count > 0
        assert chunk.chunk_metadata["content"] is not None
        assert "embedding" in chunk.chunk_metadata
        assert len(chunk.chunk_metadata["embedding"]) == 1536
        
    # Embeddings recorded
    emb_res = await db_session.execute(select(EmbeddingMetadata))
    embeddings = emb_res.scalars().all()
    assert len(embeddings) == len(chunks)
    
    # Relationships extracted
    rel_res = await db_session.execute(select(KnowledgeRelationship))
    relationships = rel_res.scalars().all()
    assert len(relationships) > 0
    
    # Validation Result recorded
    val_res = await db_session.execute(select(ValidationResult))
    val = val_res.scalars().first()
    assert val is not None
    assert val.result == "validated"
    assert val.confidence == 1.0

@pytest.mark.asyncio
async def test_processing_pipeline_retry(db_session, monkeypatch):
    # Enable Knowledge features
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PROCESSING_ENABLED", True)
    
    user_id = uuid.uuid4()
    collection = KnowledgeCollection(
        id=uuid.uuid4(),
        name="Retry Test Collection",
        slug="retry-test",
        owner_id=user_id
    )
    db_session.add(collection)
    await db_session.commit()
    
    doc_id = uuid.uuid4()
    doc = KnowledgeDocument(
        id=doc_id,
        collection_id=collection.id,
        file_name="retry_sample.txt",
        original_file_name="retry_sample.txt",
        mime_type="text/plain",
        extension="txt",
        checksum="retrychecksum",
        storage_path="path/retry_sample.txt",
        size=100,
        processing_status="pending",
        upload_status="uploaded",
        validation_status="pending",
        created_by=user_id
    )
    db_session.add(doc)
    await db_session.commit()
    
    # Force storage download to return None (triggering download failure retry)
    async def mock_download_failure(path):
        return None
        
    monkeypatch.setattr(knowledge_storage_service.provider, "download", mock_download_failure)
    
    job_id = uuid.uuid4()
    job = KnowledgeProcessingQueue(
        id=job_id,
        document_id=doc_id,
        status="pending",
        attempts=0,
        max_attempts=3
    )
    db_session.add(job)
    await db_session.commit()
    
    engine_processor = KnowledgeProcessingEngine()
    success = await engine_processor.process_document_job(db_session, job_id)
    
    assert success is False
    
    # Verify job goes to "retry" state
    db_session.expire_all()
    q_res = await db_session.execute(select(KnowledgeProcessingQueue).filter(KnowledgeProcessingQueue.id == job_id))
    updated_job = q_res.scalars().first()
    assert updated_job.status == "retry"
    assert updated_job.attempts == 1
    
    # Verify failed status on document
    doc_res = await db_session.execute(select(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id))
    updated_doc = doc_res.scalars().first()
    assert updated_doc.processing_status == "failed"
