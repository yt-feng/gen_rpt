import time
import uuid
import copy
import hashlib
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge import KnowledgeCollection, KnowledgeDocument, KnowledgeChunk
from app.services.knowledge_permission import knowledge_permission_service
from app.services.knowledge_cache import knowledge_cache_service
from app.services.retrieval_similarity import calculate_keyword_score
from app.services.retrieval_ranking import rank_retrieved_chunks
from app.services.retrieval_fusion import reciprocal_rank_fusion
from app.services.retrieval_context import build_retrieval_context
from app.services.retrieval_analytics import retrieval_analytics_service
from app.services.knowledge_processing.workers.embedding import generate_query_embedding
from app.core.config import settings

class RetrievalEngineService:
    async def retrieve_knowledge(
        self,
        db: AsyncSession,
        query: str,
        target_count: int = 10,
        collection_ids: Optional[List[uuid.UUID]] = None,
        user_id: Optional[uuid.UUID] = None,
        user_org_id: Optional[uuid.UUID] = None,
        filters: Optional[Dict[str, Any]] = None,
        weights: Optional[Dict[str, float]] = None,
        freshness_policy: str = "exponential",
        token_budget: int = 4000
    ) -> Dict[str, Any]:
        start_time = time.time()
        
        # 1. Collection scope and authorization always run before cache access,
        # so permission revocation takes effect immediately.
        col_stmt = select(KnowledgeCollection).filter(KnowledgeCollection.deleted_at.is_(None))
        if user_org_id:
            col_stmt = col_stmt.filter(KnowledgeCollection.organization_id == user_org_id)
            
        col_res = await db.execute(col_stmt)
        allowed_collections = col_res.scalars().all()
        allowed_ids = {c.id for c in allowed_collections}
        
        # Filter request collection IDs by allowed
        target_ids = []
        if collection_ids:
            candidates = [cid for cid in collection_ids if cid in allowed_ids]
            permitted = await knowledge_permission_service.batch_check_permissions(db, candidates, user_id, "viewer")
            target_ids = [cid for cid in candidates if cid in permitted]
        else:
            # Fallback to all allowed collections
            candidates = list(allowed_ids)
            permitted = await knowledge_permission_service.batch_check_permissions(db, candidates, user_id, "viewer")
            target_ids = [cid for cid in candidates if cid in permitted]
                    
        if not target_ids:
            latency = int((time.time() - start_time) * 1000)
            from app.core.metrics import knowledge_retrieval_latency_ms
            knowledge_retrieval_latency_ms.observe(float(latency))
            return {
                "session_id": uuid.uuid4(),
                "context": "",
                "chunks": [],
                "snapshot": {
                    "knowledge_version": "1.0.0",
                    "collections": [],
                    "documents": [],
                    "chunks": [],
                    "embedding_version": "1.0.0",
                    "validation_version": "1.0.0",
                    "relationship_version": "1.0.0",
                    "metadata_version": "1.0.0"
                },
                "latency_ms": latency,
                "cache_hit": False,
                "sources": []
            }

        # 2. Tenant-scoped cache lookup after resolving current permissions.
        cache_signature = {
            "query": query,
            "collection_ids": sorted(str(cid) for cid in target_ids),
            "user_id": str(user_id) if user_id else None,
            "user_org_id": str(user_org_id) if user_org_id else None,
            "filters": filters or {},
            "weights": weights or {},
            "freshness_policy": freshness_policy,
            "target_count": target_count,
            "token_budget": token_budget,
        }
        cache_digest = hashlib.sha256(
            json.dumps(cache_signature, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        cache_key = f"retrieval:{cache_digest}"
        cached = await knowledge_cache_service.get(cache_key)
        if cached:
            cached = copy.deepcopy(cached)
            elapsed = int((time.time() - start_time) * 1000)
            cached["latency_ms"] = elapsed
            cached["cache_hit"] = True
            from app.core.metrics import knowledge_retrieval_latency_ms
            knowledge_retrieval_latency_ms.observe(float(elapsed))
            return cached
            
        # 3. Retrieve eligible Documents (Isolated by Collections list)
        doc_stmt = select(KnowledgeDocument).filter(
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.collection_id.in_(target_ids),
            KnowledgeDocument.processing_status == "completed",
            KnowledgeDocument.validation_status == "validated",
        ).options(
            selectinload(KnowledgeDocument.tags),
            selectinload(KnowledgeDocument.sources),
        )
        
        doc_res = await db.execute(doc_stmt)
        all_docs = doc_res.scalars().all()
        
        # Apply filters before scoring
        filtered_docs = []
        for doc in all_docs:
            keep = True
            
            if filters:
                # Extension/mime filter
                if filters.get("document_type") and doc.extension != filters["document_type"]:
                    keep = False
                # Language filter
                if filters.get("language") and doc.language != filters["language"]:
                    keep = False
                # Author
                if filters.get("author") and doc.author != filters["author"]:
                    keep = False
                # Publisher
                if filters.get("publisher") and doc.publisher != filters["publisher"]:
                    keep = False
                # Source checks
                if filters.get("source") and doc.source_metadata and doc.source_metadata.get("source") != filters["source"]:
                    keep = False
                # Processing status
                if filters.get("processing_status") and doc.processing_status != filters["processing_status"]:
                    keep = False
                # Validation status
                if filters.get("validation_status") and doc.validation_status != filters["validation_status"]:
                    keep = False
                # Tag intersection checks
                if filters.get("tags"):
                    req_tags = set(filters["tags"])
                    doc_tags = {t.name for t in doc.tags}
                    if not req_tags.intersection(doc_tags):
                        keep = False
                        
            if keep:
                filtered_docs.append(doc)
                
        if not filtered_docs:
            return {
                "session_id": uuid.uuid4(),
                "context": "",
                "chunks": [],
                "snapshot": {
                    "knowledge_version": "1.0.0",
                    "collections": [str(x) for x in target_ids],
                    "documents": [],
                    "chunks": [],
                    "embedding_version": "1.0.0",
                    "validation_version": "1.0.0",
                    "relationship_version": "1.0.0",
                    "metadata_version": "1.0.0"
                },
                "latency_ms": int((time.time() - start_time) * 1000),
                "cache_hit": False,
                "sources": []
            }
            
        doc_ids = [d.id for d in filtered_docs]
        
        # 4. Fetch Chunks and Calculate similarity using pgvector Order By Cosine Distance
        semantic_search_available = True
        try:
            query_vector = await generate_query_embedding(query, model=settings.KNOWLEDGE_EMBEDDING_MODEL)
        except Exception:
            if settings.APP_ENV == "development":
                import random
                h = hashlib.sha256(query.encode("utf-8")).hexdigest()
                rng = random.Random(int(h, 16))
                dim = getattr(settings, "KNOWLEDGE_EMBEDDING_DIMENSION", 384)
                query_vector = [rng.uniform(-1, 1) for _ in range(dim)]
                norm = sum(x * x for x in query_vector) ** 0.5
                if norm > 0:
                    query_vector = [x / norm for x in query_vector]
            else:
                # A transient embedding-provider outage must not make already
                # indexed enterprise knowledge unavailable. Fall back to
                # deterministic lexical ranking, then apply the same validation.
                semantic_search_available = False
                query_vector = None

        if semantic_search_available:
            distance_expr = KnowledgeChunk.embedding.cosine_distance(query_vector)
            similarity_expr = 1.0 - distance_expr
            chunk_stmt = select(
                KnowledgeChunk,
                similarity_expr.label("sim")
            ).filter(
                KnowledgeChunk.document_id.in_(doc_ids),
                KnowledgeChunk.embedding.isnot(None)
            ).order_by(distance_expr).limit(target_count * 3)
            chunk_res = await db.execute(chunk_stmt)
            chunk_rows = chunk_res.all()
        else:
            chunk_stmt = select(KnowledgeChunk).filter(
                KnowledgeChunk.document_id.in_(doc_ids)
            ).limit(max(target_count * 10, 100))
            chunk_res = await db.execute(chunk_stmt)
            chunk_rows = [(chunk, None) for chunk in chunk_res.scalars().all()]
        
        # 5. Hybrid Search & Vector Calculations
        document_map = {document.id: document for document in filtered_docs}

        def build_candidate(ch, sim=None, use_semantic=True):
            meta = ch.chunk_metadata or {}
            keyword = calculate_keyword_score(query, meta.get("content", ""))
            if use_semantic:
                norm_sim = float((sim + 1.0) / 2.0)
                hybrid_score = 0.7 * norm_sim + 0.3 * keyword
            else:
                hybrid_score = keyword

            parent_doc = document_map.get(ch.document_id)
            primary_source = parent_doc.sources[0] if parent_doc and parent_doc.sources else None
            return {
                "chunk_id": ch.id,
                "document_id": ch.document_id,
                "file_name": parent_doc.file_name if parent_doc else "Unknown",
                "text_content": meta.get("content", ""),
                "similarity_score": hybrid_score,
                "created_at": parent_doc.created_at if parent_doc else datetime.now(timezone.utc),
                "validation_status": parent_doc.validation_status if parent_doc else "pending",
                "doc_size": parent_doc.size if parent_doc else 500,
                "source_id": primary_source.id if primary_source else None,
                "metadata": meta
            }

        chunk_candidates = [
            build_candidate(ch, sim, semantic_search_available)
            for ch, sim in chunk_rows
        ]

        min_relevance = float(settings.RAG_MIN_RELEVANCE_SCORE) if semantic_search_available else 0.0
        chunk_candidates = [
            candidate for candidate in chunk_candidates
            if candidate["similarity_score"] > min_relevance
        ]

        # A healthy embedding call can still yield no usable matches because of
        # model drift or an over-selective vector score. Use lexical ranking in
        # that case instead of incorrectly reporting an empty knowledge base.
        if semantic_search_available and not chunk_candidates:
            lexical_stmt = select(KnowledgeChunk).filter(
                KnowledgeChunk.document_id.in_(doc_ids)
            ).limit(max(target_count * 10, 100))
            lexical_res = await db.execute(lexical_stmt)
            chunk_candidates = [
                build_candidate(chunk, use_semantic=False)
                for chunk in lexical_res.scalars().all()
            ]
            chunk_candidates = [
                candidate for candidate in chunk_candidates
                if candidate["similarity_score"] > 0.0
            ]
            
        # 6. Decay Ranking & Confidence
        ranked = rank_retrieved_chunks(chunk_candidates, weights=weights, freshness_policy=freshness_policy)
        ranked = ranked[:target_count]
        
        # 7. Context Budget Compile
        context_pkg = build_retrieval_context(ranked, token_budget=token_budget)
        
        # 8. Build reproducible Knowledge Snapshot metadata
        snapshot = {
            "knowledge_version": "1.0.0",
            "collections": [str(cid) for cid in target_ids],
            "documents": sorted({str(c["document_id"]) for c in context_pkg["selected_chunks"]}),
            "chunks": [str(c["chunk_id"]) for c in context_pkg["selected_chunks"]],
            "embedding_version": "1.0.0",
            "validation_version": "1.0.0",
            "relationship_version": "1.0.0",
            "metadata_version": "1.0.0"
        }
        
        # 9. Immutable Session Persistence & Analytics
        latency = int((time.time() - start_time) * 1000)
        
        session_id = await retrieval_analytics_service.log_retrieval_session(
            db=db,
            query=query,
            collection_ids=target_ids,
            user_id=user_id or uuid.uuid4(),
            filters=filters or {},
            latency_ms=latency,
            cache_hit=False,
            selected_chunks=context_pkg["selected_chunks"],
            snapshot_metadata=snapshot
        )
        
        # 10. Format Final Response
        final_chunks = [
            {
                "chunk_id": sc["chunk_id"],
                "document_id": sc["document_id"],
                "file_name": sc["file_name"],
                "text_content": sc["text_content"],
                "similarity_score": sc["similarity_score"],
                "rank": sc["rank"],
                "confidence": sc["confidence_score"],
                "metadata": sc["metadata"]
            }
            for sc in context_pkg["selected_chunks"]
        ]
        
        result_payload = {
            "session_id": session_id,
            "context": context_pkg["context_string"],
            "chunks": final_chunks,
            "snapshot": snapshot,
            "latency_ms": latency,
            "cache_hit": False,
            "sources": [
                {"document_id": str(d.id), "file_name": d.file_name}
                for d in filtered_docs
                if d.id in {c["document_id"] for c in context_pkg["selected_chunks"]}
            ]
        }
        
        # Save to cache with standard TTL of 300 seconds
        await knowledge_cache_service.set(cache_key, result_payload, ttl=300)
        
        from app.core.metrics import knowledge_retrieval_latency_ms
        knowledge_retrieval_latency_ms.observe((time.time() - start_time) * 1000)
        
        return result_payload

retrieval_engine_service = RetrievalEngineService()
