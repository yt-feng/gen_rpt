import uuid
import time
import json
import traceback
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeProcessingQueue,
    KnowledgeChunk,
    EmbeddingMetadata,
    KnowledgeRelationship,
    ValidationResult,
    KnowledgeProcessingAuditLog,
    KnowledgeActivityHistory
)
from app.services.knowledge_storage import knowledge_storage_service
from app.core.config import settings

# Import worker components
from app.services.knowledge_processing.workers.extraction import extract_document_text
from app.services.knowledge_processing.workers.metadata_language import extract_metadata_package
from app.services.knowledge_processing.workers.normalization import normalize_text
from app.services.knowledge_processing.workers.chunking import chunk_document, build_headings_outline
from app.services.knowledge_processing.workers.entity_relationship import extract_entities_and_relationships
from app.services.knowledge_processing.workers.embedding import generate_chunk_embeddings
from app.services.knowledge_processing.workers.validation import run_validation_pipeline

class KnowledgeProcessingEngine:
    async def process_document_job(self, db: AsyncSession, job_id: uuid.UUID) -> bool:
        """
        Orchestrates the entire asynchronous knowledge processing pipeline.
        Updates state transitions, records audit logs, saves R2 artifacts, and updates database.
        """
        # 1. Fetch job
        job_result = await db.execute(
            select(KnowledgeProcessingQueue).filter(KnowledgeProcessingQueue.id == job_id)
        )
        job = job_result.scalars().first()
        if not job or job.status in ("completed", "cancelled"):
            return False

        # Fetch document
        doc_result = await db.execute(
            select(KnowledgeDocument).filter(KnowledgeDocument.id == job.document_id)
        )
        doc = doc_result.scalars().first()
        if not doc:
            job.status = "failed"
            job.logs = "Associated document not found."
            await db.commit()
            return False

        # Set job status to running and document status to processing
        job.status = "running"
        job.attempts += 1
        job.worker = "enterprise_processor_v1"
        doc.processing_status = "processing"
        await db.commit()

        start_time = time.time()
        logs_list = [f"[{datetime.now(timezone.utc).isoformat()}] Starting processing job {job_id} for doc {doc.id} ({doc.file_name})"]
        
        try:
            # 2. Download original file from R2
            logs_list.append("Downloading original file from Cloudflare R2...")
            file_bytes = await knowledge_storage_service.provider.download(doc.storage_path)
            if not file_bytes:
                raise ValueError(f"Failed to download original document from path: {doc.storage_path}")

            # 3. Stage 1: Text Extraction
            logs_list.append("Running Text Extraction Stage...")
            doc.processing_status = "extracting"
            await db.commit()
            
            stage_start = time.time()
            extracted_text, extraction_meta = extract_document_text(file_bytes, doc.extension)
            
            # Save extracted text to R2
            extract_output_id = uuid.uuid4()
            extract_path = knowledge_storage_service.generate_processing_path(doc.id, "extraction", extract_output_id)
            extract_data = json.dumps({"text": extracted_text, "extraction_metadata": extraction_meta}).encode("utf-8")
            await knowledge_storage_service.provider.upload(extract_data, extract_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "text_extraction", stage_start, extract_output_id, len(file_bytes))
            logs_list.append(f"Text Extraction completed in {time.time() - stage_start:.2f}s. Character count: {len(extracted_text)}")

            # 4. Stage 2: Metadata & Language
            logs_list.append("Running Metadata & Language Detection Stage...")
            stage_start = time.time()
            metadata_pkg = extract_metadata_package(
                extracted_text, doc.file_name, doc.mime_type, doc.extension, doc.size, extraction_meta
            )
            
            # Update Document table metadata
            doc.language = metadata_pkg.get("language")
            doc.page_count = metadata_pkg.get("page_count", 1)
            
            # Write metadata json to R2
            meta_output_id = uuid.uuid4()
            meta_path = knowledge_storage_service.generate_processing_path(doc.id, "metadata", meta_output_id)
            meta_data = json.dumps(metadata_pkg).encode("utf-8")
            await knowledge_storage_service.provider.upload(meta_data, meta_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "metadata_extraction", stage_start, meta_output_id, len(meta_data))
            logs_list.append(f"Metadata extraction completed. Language detected: {doc.language}")

            # 5. Stage 3: Content Normalization
            logs_list.append("Running Content Normalization Stage...")
            doc.processing_status = "normalizing"
            await db.commit()
            
            stage_start = time.time()
            normalized_text = normalize_text(extracted_text)
            
            # Write normalized text to R2
            norm_output_id = uuid.uuid4()
            norm_path = knowledge_storage_service.generate_processing_path(doc.id, "normalization", norm_output_id)
            norm_data = json.dumps({"normalized_text": normalized_text}).encode("utf-8")
            await knowledge_storage_service.provider.upload(norm_data, norm_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "normalization", stage_start, norm_output_id, len(norm_data))
            logs_list.append("Content normalization completed.")

            # 6. Stage 4: Chunk Generation
            logs_list.append("Running Document Chunking Stage...")
            doc.processing_status = "chunking"
            await db.commit()
            
            stage_start = time.time()
            # Retrieve chunk settings from config
            chunk_size = settings.KNOWLEDGE_CHUNK_SIZE
            chunk_overlap = settings.KNOWLEDGE_CHUNK_OVERLAP
            
            chunks_list = chunk_document(normalized_text, chunk_size, chunk_overlap)
            
            # Clean up old chunks for this doc if it's a re-run
            await db.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc.id)
                .values(document_id=doc.id) # Dummy operation to construct syntax, wait, delete is better
            )
            # Delete old chunks
            from sqlalchemy import delete
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
            
            db_chunks = []
            for ch in chunks_list:
                db_chunk = KnowledgeChunk(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    chunk_number=ch["chunk_number"],
                    heading=ch.get("heading"),
                    section=ch.get("section"),
                    token_count=ch["token_count"],
                    character_count=ch["character_count"],
                    hash=ch["hash"],
                    processing_version="1.0",
                    chunk_metadata={"content": ch["content"]}
                )
                db.add(db_chunk)
                db_chunks.append(db_chunk)
            await db.commit()
            
            # Write chunks outline to R2
            chunks_output_id = uuid.uuid4()
            chunks_path = knowledge_storage_service.generate_processing_path(doc.id, "chunking", chunks_output_id)
            chunks_data = json.dumps({"chunks": [{"num": c.chunk_number, "id": str(c.id)} for c in db_chunks]}).encode("utf-8")
            await knowledge_storage_service.provider.upload(chunks_data, chunks_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "chunking", stage_start, chunks_output_id, len(chunks_data))
            logs_list.append(f"Document chunking completed. Generated {len(db_chunks)} chunks.")

            # 7. Stage 5: Entity & Relationship Extraction
            logs_list.append("Running Entity & Relationship Extraction Stage...")
            stage_start = time.time()
            
            entities, relationships = extract_entities_and_relationships(normalized_text)
            
            # Delete old relationships
            await db.execute(delete(KnowledgeRelationship).where(
                (KnowledgeRelationship.source_document_id == doc.id) | 
                (KnowledgeRelationship.target_document_id == doc.id)
            ))
            
            # Add new relationships if linked to valid document targets (for testing we can self-reference or mock target references)
            for rel in relationships:
                db_rel = KnowledgeRelationship(
                    source_document_id=doc.id,
                    target_document_id=doc.id,  # Local fallback to self
                    relationship_type=rel["type"],
                    relationship_metadata={"source_name": rel["source"], "target_name": rel["target"], "sentence": rel["sentence"]}
                )
                db.add(db_rel)
            await db.commit()
            
            # Write entity package to R2
            entity_output_id = uuid.uuid4()
            entity_path = knowledge_storage_service.generate_processing_path(doc.id, "entities", entity_output_id)
            entity_data = json.dumps({"entities": entities, "relationships": relationships}).encode("utf-8")
            await knowledge_storage_service.provider.upload(entity_data, entity_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "entity_extraction", stage_start, entity_output_id, len(entity_data))
            logs_list.append(f"Entity extraction completed. Found {len(entities)} entities.")

            # 8. Stage 6: Embedding Generation
            logs_list.append("Running Embedding Generation Stage...")
            doc.processing_status = "embedding"
            await db.commit()
            
            stage_start = time.time()
            chunks_for_embedding = [{"id": c.id, "chunk_number": c.chunk_number, "content": c.chunk_metadata["content"]} for c in db_chunks]
            embeddings_pkg = await generate_chunk_embeddings(chunks_for_embedding, model=settings.KNOWLEDGE_EMBEDDING_MODEL)
            
            # Write embeddings to db & R2
            embeds_output_id = uuid.uuid4()
            vectors_snapshot = []
            
            for emb in embeddings_pkg:
                # Store vector floats list inside the chunk_metadata of KnowledgeChunk to enable semantic searches
                chunk_id = emb["chunk_id"]
                target_chunk = next((c for c in db_chunks if c.id == chunk_id), None)
                if target_chunk:
                    target_chunk.chunk_metadata = {
                        "content": target_chunk.chunk_metadata["content"],
                        "embedding": emb["vector"]
                    }
                    target_chunk.embedding = emb["vector"]
                    db.add(target_chunk)
                    
                # Create EmbeddingMetadata
                db_emb = EmbeddingMetadata(
                    chunk_id=chunk_id,
                    embedding_model=emb["embedding_model"],
                    embedding_version=emb["embedding_version"],
                    dimension=emb["dimension"],
                    status="completed",
                    generated_time=emb["generated_time"],
                    provider=emb["provider"],
                    latency=emb["latency"],
                    checksum=emb["checksum"]
                )
                db.add(db_emb)
                
                vectors_snapshot.append({
                    "chunk_id": str(chunk_id),
                    "vector": emb["vector"]
                })
            await db.commit()
            
            # Upload vector snapshot to R2
            embed_path = knowledge_storage_service.generate_processing_path(doc.id, "embeddings", embeds_output_id)
            embed_data = json.dumps(vectors_snapshot).encode("utf-8")
            await knowledge_storage_service.provider.upload(embed_data, embed_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "embedding", stage_start, embeds_output_id, len(embed_data))
            logs_list.append("Embedding generation completed successfully.")

            # 9. Stage 7: Validation Stage
            logs_list.append("Running Quality & Integrity Validation...")
            doc.processing_status = "validating"
            await db.commit()
            
            stage_start = time.time()
            val_report = run_validation_pipeline(
                extraction_meta, metadata_pkg, normalized_text, chunks_list, embeddings_pkg
            )
            
            # Create ValidationResult in PostgreSQL
            db_val = ValidationResult(
                document_id=doc.id,
                validation_type=val_report["validation_type"],
                confidence=val_report["confidence"],
                result=val_report["result"],
                evidence={"checks": val_report["checks"], "errors": val_report["errors"]},
                validator="enterprise_validator_v1",
                summary=val_report["summary"]
            )
            db.add(db_val)
            
            # Write validation report to R2
            val_output_id = uuid.uuid4()
            val_path = knowledge_storage_service.generate_processing_path(doc.id, "validation", val_output_id)
            val_data = json.dumps(val_report).encode("utf-8")
            await knowledge_storage_service.provider.upload(val_data, val_path, content_type="application/json")
            
            await self._record_audit_log(db, job_id, "validation", stage_start, val_output_id, len(val_data))
            logs_list.append("Quality Validation stage completed.")

            # 10. Complete processing job
            job.status = "completed"
            doc.processing_status = "completed"
            doc.validation_status = "validated"
            
            # Record activity history log
            activity_log = KnowledgeActivityHistory(
                collection_id=doc.collection_id,
                document_id=doc.id,
                activity_type="processing",
                details={"status": "completed", "duration_seconds": round(time.time() - start_time, 2)}
            )
            db.add(activity_log)
            
            logs_list.append(f"[{datetime.now(timezone.utc).isoformat()}] Processing job completed successfully.")
            job.logs = "\n".join(logs_list)
            await db.commit()
            
            # Write full processing logs to R2
            log_path = knowledge_storage_service.generate_log_path("processing", datetime.now(timezone.utc))
            await knowledge_storage_service.provider.upload(job.logs.encode("utf-8"), log_path, content_type="text/plain")
            return True

        except Exception as e:
            tb = traceback.format_exc()
            logs_list.append(f"[{datetime.now(timezone.utc).isoformat()}] Pipeline encountered exception: {str(e)}")
            logs_list.append(tb)
            
            job.logs = "\n".join(logs_list)
            doc.processing_status = "failed"
            doc.validation_status = "failed"
            
            # Determine if we should retry
            if job.attempts < job.max_attempts:
                job.status = "retry"
                logs_list.append(f"Scheduling retry {job.attempts + 1} of {job.max_attempts}...")
            else:
                job.status = "failed"
                
            # Log failure audit
            audit_err = KnowledgeProcessingAuditLog(
                job_id=job_id,
                worker="enterprise_processor_v1",
                stage=doc.processing_status,
                duration_ms=int((time.time() - start_time) * 1000),
                errors=str(e),
                retries=job.attempts - 1
            )
            db.add(audit_err)
            
            # Record activity history log
            activity_log = KnowledgeActivityHistory(
                collection_id=doc.collection_id,
                document_id=doc.id,
                activity_type="processing",
                details={"status": "failed", "error": str(e), "attempt": job.attempts}
            )
            db.add(activity_log)
            
            await db.commit()
            
            # Write failure logs to R2
            try:
                log_path = knowledge_storage_service.generate_log_path("processing_error", datetime.now(timezone.utc))
                await knowledge_storage_service.provider.upload(job.logs.encode("utf-8"), log_path, content_type="text/plain")
            except Exception:
                pass
                
            return False

    async def _record_audit_log(
        self, db: AsyncSession, job_id: uuid.UUID, stage: str, start_time: float, output_id: uuid.UUID, output_size: int
    ):
        duration = int((time.time() - start_time) * 1000)
        audit = KnowledgeProcessingAuditLog(
            job_id=job_id,
            worker="enterprise_processor_v1",
            stage=stage,
            duration_ms=duration,
            outputs={"output_id": str(output_id), "size_bytes": output_size}
        )
        db.add(audit)
        await db.commit()
