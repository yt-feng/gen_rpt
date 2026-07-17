import time
import json
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.knowledge import RetrievalSession, RetrievalResult, KnowledgeChunk, KnowledgeDocument
from app.models.validation import ValidationReport
from app.schemas.validation import ValidatedContextPackage, ValidatedChunkSchema, ValidatedSourceSchema

from app.services.validation.policy import policy_service
from app.services.validation.source import source_validation_service
from app.services.validation.authority import authority_service
from app.services.validation.freshness import freshness_service
from app.services.validation.duplicate import duplicate_service
from app.services.validation.conflict import conflict_service
from app.services.validation.confidence import confidence_service
from app.services.validation.evidence import evidence_service
from app.services.validation.history import history_service
from app.services.validation.audit import audit_service
from app.storage.provider import storage_provider

class ValidationService:
    async def validate_session(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None
    ) -> ValidatedContextPackage:
        """
        Validates retrieved knowledge for a specific Retrieval Session.
        Builds the immutable Validation Report, logs history/audit, and uploads to R2.
        Returns a ValidatedContextPackage.
        """
        start_time = time.time()
        
        # 1. Fetch Retrieval Session
        sess_stmt = select(RetrievalSession).where(RetrievalSession.id == session_id)
        sess_res = await db.execute(sess_stmt)
        session = sess_res.scalar_one_or_none()
        if not session:
            raise ValueError(f"Retrieval Session with ID {session_id} not found")

        # 2. Fetch Retrieval Results
        res_stmt = select(RetrievalResult).where(RetrievalResult.session_id == session_id)
        res_results = await db.execute(res_stmt)
        results = res_results.scalars().all()
        
        chunk_ids = [r.chunk_id for r in results]
        
        # 3. Load Chunks with parent Documents & Sources
        chunks_stmt = select(KnowledgeChunk).where(
            KnowledgeChunk.id.in_(chunk_ids)
        ).options(
            selectinload(KnowledgeChunk.document).selectinload(KnowledgeDocument.sources),
            selectinload(KnowledgeChunk.document).selectinload(KnowledgeDocument.tags),
            selectinload(KnowledgeChunk.document).selectinload(KnowledgeDocument.collection)
        )
        chunks_res = await db.execute(chunks_stmt)
        db_chunks = chunks_res.scalars().all()
        
        # Map DB objects back into list of dicts for sub-services
        chunks_list = []
        document_ids = []
        doc_map = {}
        
        for ch in db_chunks:
            doc = ch.document
            if not doc:
                continue
                
            doc_map[doc.id] = doc
            if doc.id not in document_ids:
                document_ids.append(doc.id)
                
            # Find matching similarity score
            sim_score = 0.7
            rank = 1
            matching_res = next((r for r in results if r.chunk_id == ch.id), None)
            if matching_res:
                sim_score = matching_res.similarity_score
                rank = matching_res.ranking
                
            chunks_list.append({
                "chunk_id": ch.id,
                "document_id": ch.document_id,
                "file_name": doc.file_name,
                "text_content": (ch.chunk_metadata or {}).get("content", ""),
                "similarity_score": sim_score,
                "rank": rank,
                "validation_status": doc.validation_status,
                "metadata": ch.chunk_metadata or {}
            })

        # 4. Perform modular validation
        policy = await policy_service.get_active_policy(db)
        
        # Source Validation
        source_val_map, source_errors = await source_validation_service.validate_sources(db, document_ids, policy)
        
        # Authority Scoring
        auth_scores = await authority_service.calculate_authority(db, list(doc_map.values()), policy)
        
        # Freshness Validation
        fresh_scores = await freshness_service.calculate_freshness(db, list(doc_map.values()), policy)
        
        # Duplicate Validation
        dup_flags, dup_analysis = await duplicate_service.analyze_duplicates(db, chunks_list, auth_scores, policy)
        
        # Conflict Detection
        conflict_map, conflicts_list = await conflict_service.detect_conflicts(db, chunks_list, policy)
        
        # Confidence Scoring
        conf_results = await confidence_service.calculate_confidence(
            db, chunks_list, auth_scores, fresh_scores, conflict_map, dup_flags, policy
        )
        
        # Evidence Completeness
        completeness_details, unsupported_flags = await evidence_service.evaluate_evidence(
            db, chunks_list, auth_scores, fresh_scores, conf_results["per_chunk_confidence"], policy
        )

        # 5. Create Validation Report & Reference ID
        report_id = uuid.uuid4()
        summary = f"Validated {len(chunks_list)} chunks from {len(document_ids)} sources. Overall confidence is {conf_results['overall_confidence']}. Found {len(conflicts_list)} conflicts."
        
        # Upload Full Report to Cloudflare R2
        full_report_json = {
            "validation_id": str(report_id),
            "session_id": str(session_id),
            "validation_timestamp": datetime.now(timezone.utc).isoformat(),
            "knowledge_snapshot": session.snapshot_metadata or {},
            "validation_summary": summary,
            "authority_scores": {str(k): v for k, v in auth_scores.items()},
            "freshness_scores": {str(k): v for k, v in fresh_scores.items()},
            "confidence_scores": conf_results,
            "conflicts": conflicts_list,
            "duplicate_analysis": dup_analysis,
            "evidence_completeness": completeness_details,
            "unsupported_evidence": unsupported_flags,
            "recommendations": {
                "flagged_chunks": [str(c["chunk_id"]) for c in unsupported_flags],
                "duplicate_chunks": [str(k) for k, v in dup_flags.items() if v],
                "action": "proceed" if conf_results["overall_confidence"] >= policy.min_confidence else "review"
            }
        }
        
        r2_path = f"knowledge/validation_reports/{report_id}.json"
        try:
            await storage_provider.upload(
                json.dumps(full_report_json).encode("utf-8"),
                r2_path,
                "application/json"
            )
        except Exception as e:
            # Non-fatal if R2 is not configured
            r2_path = None
            
        # Store metadata in PostgreSQL
        report = ValidationReport(
            id=report_id,
            session_id=session_id,
            knowledge_snapshot=session.snapshot_metadata or {},
            retrieved_sources={"sources": [str(d) for d in document_ids]},
            validation_summary=summary,
            authority_scores={str(k): v for k, v in auth_scores.items()},
            freshness_scores={str(k): v for k, v in fresh_scores.items()},
            confidence_scores=conf_results,
            conflicts={"conflicts": conflicts_list},
            duplicate_analysis=dup_analysis,
            evidence_completeness=completeness_details,
            unsupported_evidence={"unsupported": unsupported_flags},
            recommendations=full_report_json["recommendations"],
            r2_path=r2_path
        )
        db.add(report)
        await db.commit()

        # 6. Maintenance of Validation History & Audit Logs
        duration_ms = int((time.time() - start_time) * 1000)
        
        await history_service.log_history_run(
            db=db,
            session_id=session_id,
            validation_run_id=report_id,
            knowledge_version=(session.snapshot_metadata or {}).get("knowledge_version", "1.0.0"),
            validation_policy_id=policy.id,
            confidence_score=conf_results["overall_confidence"],
            conflict_count=len(conflicts_list),
            freshness_score=float(sum(fresh_scores.values()) / max(1, len(fresh_scores))),
            details=full_report_json
        )
        
        await audit_service.log_audit(
            db=db,
            validator_version="1.0.0",
            execution_time_ms=duration_ms,
            knowledge_snapshot=session.snapshot_metadata,
            retrieved_chunks={"chunks": chunks_list},
            validation_rules=policy.rules,
            results=full_report_json,
            warnings={"source_errors": source_errors},
            errors={},
            user_id=user_id
        )

        # 7. Construct Validated Context Package
        validated_chunks_schemas = []
        for c in chunks_list:
            cid = c["chunk_id"]
            doc_id = c["document_id"]
            
            # Map chunk validation status based on duplicates, conflicts, etc.
            status = "validated"
            if dup_flags.get(cid, False):
                status = "duplicate"
            elif cid in conflict_map:
                status = "conflict"
            elif any(f["chunk_id"] == cid for f in unsupported_flags):
                status = "flagged"
                
            validated_chunks_schemas.append(
                ValidatedChunkSchema(
                    chunk_id=cid,
                    document_id=doc_id,
                    text=c["text_content"],
                    confidence=conf_results["per_chunk_confidence"].get(str(cid), 1.0),
                    authority=auth_scores.get(doc_id, 1.0),
                    is_duplicate=dup_flags.get(cid, False),
                    conflicts_with=conflict_map.get(cid, []),
                    validation_status=status,
                    metadata=c["metadata"]
                )
            )
            
        validated_sources_schemas = []
        for doc_id, doc in doc_map.items():
            status = "validated"
            if not source_val_map.get(doc_id, {}).get("is_valid", True):
                status = "failed"
                
            validated_sources_schemas.append(
                ValidatedSourceSchema(
                    source_id=doc_id,
                    document_id=doc_id,
                    publisher=source_val_map.get(doc_id, {}).get("publisher"),
                    source_type=source_val_map.get(doc_id, {}).get("source_type", "unknown"),
                    authority_score=auth_scores.get(doc_id, 1.0),
                    freshness_score=fresh_scores.get(doc_id, 1.0),
                    validation_status=status
                )
            )

        # Evidence Ranking: sort chunks by confidence score descending
        sorted_chunk_ids = [c.chunk_id for c in sorted(validated_chunks_schemas, key=lambda x: x.confidence, reverse=True)]

        # Collect metadata from collections represented
        collection_metadata = {}
        for doc in doc_map.values():
            if doc.collection and doc.collection.id not in collection_metadata:
                collection_metadata[str(doc.collection.id)] = {
                    "name": doc.collection.name,
                    "slug": doc.collection.slug,
                    "visibility": doc.collection.visibility
                }

        # Build Context Metadata
        context_metadata = {
            "validation_duration_ms": duration_ms,
            "overall_confidence": conf_results["overall_confidence"],
            "completeness_score": completeness_details["completeness_score"],
            "duplicate_ratio": dup_analysis["duplicate_ratio"],
            "conflict_count": len(conflicts_list)
        }

        return ValidatedContextPackage(
            validated_chunks=validated_chunks_schemas,
            validated_sources=validated_sources_schemas,
            confidence_scores=conf_results,
            authority_scores={str(k): v for k, v in auth_scores.items()},
            evidence_ranking=sorted_chunk_ids,
            knowledge_snapshot=session.snapshot_metadata or {},
            validation_report_reference=report_id,
            collection_metadata=collection_metadata,
            document_references=[{"document_id": str(k), "file_name": v.file_name} for k, v in doc_map.items()],
            context_metadata=context_metadata
        )

validation_service = ValidationService()
