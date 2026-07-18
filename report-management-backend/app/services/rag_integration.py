import uuid
import json
import hashlib
import time
import httpx
import asyncio
import os
from datetime import datetime, timezone, timedelta


from typing import List, Dict, Any, Optional
from sqlalchemy import select, delete, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.rag_integration import KnowledgeSnapshot, EvidenceAttribution, GenerationAnalytics, GenerationContextCache
from app.models.workflow import GenerationJob
from app.models.validation import ValidationReport
from app.services.retrieval_engine import retrieval_engine_service
from app.services.retrieval_context import build_validated_context
from app.services.validation import validation_service
from app.storage.provider import storage_provider
from app.core.config import settings
from app.logging.logger import logger

from app.utils.serialization import stringify_uuids


class RAGContextPreparationError(RuntimeError):
    """A safe, stage-labelled context preparation failure."""

    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(f"RAG context preparation failed during {stage}")


class ContextCacheService:
    async def get_cached_context(self, db: AsyncSession, cache_key: str) -> Optional[dict]:
        stmt = select(GenerationContextCache).where(
            GenerationContextCache.cache_key == cache_key,
            GenerationContextCache.expires_at > datetime.now(timezone.utc)
        )
        res = await db.execute(stmt)
        cached = res.scalar_one_or_none()
        if cached:
            return cached.context_package
        return None

    async def set_cached_context(self, db: AsyncSession, cache_key: str, package: dict, ttl_seconds: int = 3600) -> None:
        # Check if already exists, update or insert
        stmt = select(GenerationContextCache).where(GenerationContextCache.cache_key == cache_key)
        res = await db.execute(stmt)
        cached = res.scalar_one_or_none()
        
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        if cached:
            cached.context_package = stringify_uuids(package)
            cached.expires_at = expires_at
        else:
            cached = GenerationContextCache(
                id=uuid.uuid4(),
                cache_key=cache_key,
                context_package=stringify_uuids(package),
                expires_at=expires_at
            )
            db.add(cached)
        await db.commit()

    async def clear_expired(self, db: AsyncSession) -> None:
        stmt = delete(GenerationContextCache).where(GenerationContextCache.expires_at <= datetime.now(timezone.utc))
        await db.execute(stmt)
        await db.commit()

    async def invalidate(self, db: AsyncSession, cache_key: str) -> None:
        stmt = delete(GenerationContextCache).where(GenerationContextCache.cache_key == cache_key)
        await db.execute(stmt)
        await db.commit()

    async def invalidate_all(self, db: AsyncSession) -> None:
        stmt = delete(GenerationContextCache)
        await db.execute(stmt)
        await db.commit()


class KnowledgeSnapshotService:
    async def create_snapshot(
        self,
        db: AsyncSession,
        knowledge_version: str,
        collections_used: List[uuid.UUID],
        documents_used: List[uuid.UUID],
        chunks_used: List[uuid.UUID],
        retrieval_session_id: Optional[uuid.UUID] = None,
        configuration: Optional[dict] = None
    ) -> KnowledgeSnapshot:
        snapshot_id = uuid.uuid4()
        
        # Detailed snapshot structure
        detail_json = {
            "snapshot_id": str(snapshot_id),
            "knowledge_version": knowledge_version,
            "collections_used": [str(c) for c in collections_used],
            "documents_used": [str(d) for d in documents_used],
            "chunks_used": [str(ch) for ch in chunks_used],
            "embedding_version": "1.0",
            "validation_version": "1.0",
            "configuration": configuration or {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Upload details to R2
        r2_path = f"knowledge/snapshots/{snapshot_id}/snapshot.json"
        try:
            await storage_provider.upload(
                json.dumps(detail_json).encode("utf-8"),
                r2_path,
                "application/json"
            )
        except Exception as e:
            logger.warning(f"Failed to upload snapshot to R2: {e}")
            r2_path = None
            
        snapshot = KnowledgeSnapshot(
            id=snapshot_id,
            knowledge_version=knowledge_version,
            collections_used=[str(c) for c in collections_used],
            documents_used=[str(d) for d in documents_used],
            chunks_used=[str(ch) for ch in chunks_used],
            retrieval_session_id=retrieval_session_id,
            configuration=configuration,
            r2_path=r2_path
        )
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)
        return snapshot

    async def get_snapshot(self, db: AsyncSession, snapshot_id: uuid.UUID) -> Optional[KnowledgeSnapshot]:
        return await db.get(KnowledgeSnapshot, snapshot_id)


class EvidenceAttributionService:
    async def create_attributions(
        self,
        db: AsyncSession,
        generation_job_id: uuid.UUID,
        section_attributions: List[dict],
        snapshot_id: uuid.UUID,
        validation_report_id: uuid.UUID
    ) -> List[EvidenceAttribution]:
        created = []
        for sect in section_attributions:
            attrib = EvidenceAttribution(
                id=uuid.uuid4(),
                generation_job_id=generation_job_id,
                section_id=sect.get("section_id"),
                supporting_chunks=[str(c) for c in sect.get("supporting_chunks", [])],
                supporting_documents=[str(d) for d in sect.get("supporting_documents", [])],
                supporting_sources=[str(s) for s in sect.get("supporting_sources", [])],
                supporting_collections=[str(col) for col in sect.get("supporting_collections", [])],
                confidence=sect.get("confidence", 1.0),
                validation_report_id=validation_report_id,
                snapshot_id=snapshot_id
            )
            db.add(attrib)
            created.append(attrib)
        await db.commit()
        return created

    async def get_attributions_for_job(self, db: AsyncSession, generation_job_id: uuid.UUID) -> List[EvidenceAttribution]:
        stmt = select(EvidenceAttribution).where(EvidenceAttribution.generation_job_id == generation_job_id)
        res = await db.execute(stmt)
        return list(res.scalars().all())


class GenerationAnalyticsService:
    async def log_analytics(
        self,
        db: AsyncSession,
        generation_job_id: Optional[uuid.UUID],
        collections_used: List[uuid.UUID],
        retrieved_documents_count: int,
        retrieved_chunks_count: int,
        context_size: int,
        cache_hit: bool,
        retrieval_time_ms: int,
        validation_time_ms: int,
        prompt_build_time_ms: int,
        generation_time_ms: int,
        knowledge_reuse_metrics: Optional[dict] = None,
        evidence_usage_metrics: Optional[dict] = None
    ) -> GenerationAnalytics:
        analytics = GenerationAnalytics(
            id=uuid.uuid4(),
            generation_job_id=generation_job_id,
            collections_used=[str(c) for c in collections_used],
            retrieved_documents_count=retrieved_documents_count,
            retrieved_chunks_count=retrieved_chunks_count,
            context_size=context_size,
            cache_hit=cache_hit,
            retrieval_time_ms=retrieval_time_ms,
            validation_time_ms=validation_time_ms,
            prompt_build_time_ms=prompt_build_time_ms,
            generation_time_ms=generation_time_ms,
            knowledge_reuse_metrics=knowledge_reuse_metrics,
            evidence_usage_metrics=evidence_usage_metrics
        )
        db.add(analytics)
        await db.commit()
        await db.refresh(analytics)
        return analytics

    async def get_analytics_summary(self, db: AsyncSession) -> dict:
        stmt = select(
            func.count(GenerationAnalytics.id).label("requests"),
            func.avg(GenerationAnalytics.retrieval_time_ms).label("avg_retrieval_time"),
            func.avg(GenerationAnalytics.validation_time_ms).label("avg_validation_time"),
            func.avg(GenerationAnalytics.prompt_build_time_ms).label("avg_prompt_build_time"),
            func.avg(GenerationAnalytics.generation_time_ms).label("avg_generation_time"),
            func.avg(GenerationAnalytics.context_size).label("avg_context_size"),
            func.sum(GenerationAnalytics.cache_hit.cast(Integer)).label("cache_hits")
        )
        res = await db.execute(stmt)
        row = res.first()
        
        requests = row.requests if row else 0
        hits = row.cache_hits if row and row.cache_hits else 0
        hit_ratio = round((hits / requests), 2) if requests > 0 else 0.0
        
        snapshot_count_res = await db.execute(select(func.count(KnowledgeSnapshot.id)))
        snapshot_count = snapshot_count_res.scalar() or 0
        
        attr_count_res = await db.execute(select(func.count(EvidenceAttribution.id)))
        attr_count = attr_count_res.scalar() or 0
        
        return {
            "total_generation_requests": requests,
            "context_cache_hit_ratio": hit_ratio,
            "average_retrieval_time_ms": round(row.avg_retrieval_time, 2) if row and row.avg_retrieval_time else 0.0,
            "average_validation_time_ms": round(row.avg_validation_time, 2) if row and row.avg_validation_time else 0.0,
            "average_prompt_build_time_ms": round(row.avg_prompt_build_time, 2) if row and row.avg_prompt_build_time else 0.0,
            "average_generation_time_ms": round(row.avg_generation_time, 2) if row and row.avg_generation_time else 0.0,
            "average_context_size_bytes": round(row.avg_context_size, 2) if row and row.avg_context_size else 0.0,
            "knowledge_snapshot_count": snapshot_count,
            "evidence_attribution_count": attr_count
        }


class PromptBuilderService:
    def build_prompt(
        self,
        original_prompt: str,
        context_package: dict,
        configuration: Optional[dict] = None
    ) -> str:
        # Prompt Builder compiles Validated Context Package and Metadata
        chunks = context_package.get("validated_chunks", [])
        context_str = "\n\n".join([f"[Source Chunk {c['chunk_id']}] (Confidence: {c['confidence']:.2f})\n{c['text']}" for c in chunks])
        
        config = configuration or {}
        language = config.get("language", "en")
        
        if language == "zh":
            compiled = (
                f"你是一个拥有丰富行业经验的资深顾问。请基于以下提供的已验证企业知识来回答用户的请求。\n\n"
                f"--- 已验证企业知识 ---\n"
                f"{context_str}\n\n"
                f"--- 用户请求 ---\n"
                f"{original_prompt}\n\n"
                f"请确保所有关键判断都可以从提供的知识库片段中找到直接证据，并在输出中提供引用。"
            )
        else:
            compiled = (
                f"You are an elite research consultant. Answer the user prompt based strictly on the validated enterprise knowledge provided below.\n\n"
                f"--- VALIDATED ENTERPRISE KNOWLEDGE ---\n"
                f"{context_str}\n\n"
                f"--- USER PROMPT ---\n"
                f"{original_prompt}\n\n"
                f"Ensure every material claim is supported by direct evidence from the sources above, referencing them clearly."
            )
        return compiled

    def build_partial_prompt(
        self,
        action: str,
        original_text: str,
        context_package: dict,
        configuration: Optional[dict] = None
    ) -> str:
        chunks = context_package.get("validated_chunks", [])
        context_str = "\n\n".join([f"[Source Chunk {c['chunk_id']}] (Confidence: {c['confidence']:.2f})\n{c['text']}" for c in chunks])
        
        config = configuration or {}
        language = config.get("language", "en")
        
        action_desc = "Rewrite the following text."
        if action == "expand":
            action_desc = "Expand on the following text, providing more detail and context."
        elif action == "rewrite":
            action_desc = "Rewrite the following text to make it more concise and professional."
        elif action == "regenerate":
            action_desc = "Completely regenerate the following text, providing a fresh perspective."
            
        if language == "zh":
            compiled = (
                f"你是一个拥有丰富行业经验的资深顾问。请基于以下提供的已验证企业知识对文本进行修改。\n\n"
                f"--- 已验证企业知识 ---\n"
                f"{context_str}\n\n"
                f"--- 操作要求 ---\n"
                f"{action_desc}\n\n"
                f"--- 原始文本 ---\n"
                f"{original_text}\n\n"
                f"请确保所有引用的事实在已验证企业知识中存在，并且仅返回修改后的文本本身，不需要多余的解释。"
            )
        else:
            compiled = (
                f"You are an elite research consultant. Modify the text based strictly on the validated enterprise knowledge provided below.\n\n"
                f"--- VALIDATED ENTERPRISE KNOWLEDGE ---\n"
                f"{context_str}\n\n"
                f"--- ACTION ---\n"
                f"{action_desc}\n\n"
                f"--- ORIGINAL TEXT ---\n"
                f"{original_text}\n\n"
                f"Ensure every material claim is supported by direct evidence from the sources above. Return only the edited text without any conversational filler or quotes."
            )
        return compiled


class GenerationContextService:
    def __init__(self):
        self.cache_service = ContextCacheService()
        self.snapshot_service = KnowledgeSnapshotService()
        self.analytics_service = GenerationAnalyticsService()

    async def prepare_context(
        self,
        db: AsyncSession,
        query: str,
        collection_ids: Optional[List[uuid.UUID]] = None,
        user_id: Optional[uuid.UUID] = None,
        user_org_id: Optional[uuid.UUID] = None,
        config: Optional[dict] = None,
        generation_job_id: Optional[uuid.UUID] = None,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        ret_start = time.time()
        
        # 1. Cache Key signature
        if slug:
            cache_key = f"context:slug:{slug}"
        else:
            col_str = ",".join(sorted([str(c) for c in (collection_ids or [])]))
            sig_input = (
                f"{query}:{col_str}:{user_id or 'anonymous'}:"
                f"{user_org_id or 'no-org'}:{settings.APP_ENV}"
            )
            cache_key = hashlib.sha256(sig_input.encode('utf-8')).hexdigest()

        
        # 2. Check Context Cache
        cached_pkg = await self.cache_service.get_cached_context(db, cache_key)
        if cached_pkg:
            ret_time = int((time.time() - ret_start) * 1000)
            logger.info("Context Cache hit!")
            # Save quick analytics
            await self.analytics_service.log_analytics(
                db=db,
                generation_job_id=generation_job_id,
                collections_used=collection_ids or [],
                retrieved_documents_count=len(cached_pkg.get("document_references", [])),
                retrieved_chunks_count=len(cached_pkg.get("validated_chunks", [])),
                context_size=len(cached_pkg.get("validated_context_string", "")),
                cache_hit=True,
                retrieval_time_ms=ret_time,
                validation_time_ms=0,
                prompt_build_time_ms=0,
                generation_time_ms=0
            )
            return cached_pkg

        # 3. Retrieve Knowledge
        try:
            ret_payload = await retrieval_engine_service.retrieve_knowledge(
                db=db,
                query=query,
                target_count=20,
                collection_ids=collection_ids,
                user_id=user_id,
                user_org_id=user_org_id,
                token_budget=settings.RAG_CONTEXT_TOKEN_BUDGET
            )
        except Exception as exc:
            raise RAGContextPreparationError("retrieval") from exc
        ret_time = int((time.time() - ret_start) * 1000)

        # No eligible/relevant evidence is a valid result, not a validation
        # exception. Preserve a short-lived, observable empty package.
        if not ret_payload.get("chunks"):
            snapshot = await self.snapshot_service.create_snapshot(
                db=db,
                knowledge_version="1.0.0",
                collections_used=[uuid.UUID(c) for c in ret_payload["snapshot"].get("collections", [])],
                documents_used=[],
                chunks_used=[],
                retrieval_session_id=None,
                configuration=config,
            )
            empty_pkg = stringify_uuids({
                "validated_chunks": [],
                "validated_sources": [],
                "confidence_scores": {"overall_confidence": 0.0},
                "authority_scores": {},
                "evidence_ranking": [],
                "knowledge_snapshot": ret_payload.get("snapshot", {}),
                "knowledge_snapshot_id": str(snapshot.id),
                "validation_report_reference": None,
                "collection_metadata": {},
                "document_references": [],
                "context_metadata": {
                    "retrieved_chunk_count": 0,
                    "validated_chunk_count": 0,
                    "estimated_tokens": 0,
                    "rag_status": "no_evidence",
                },
                "validated_context_string": "",
            })
            await self.cache_service.set_cached_context(
                db, cache_key, empty_pkg, ttl_seconds=settings.RAG_CONTEXT_CACHE_TTL_SECONDS
            )
            return empty_pkg
        
        val_start = time.time()
        # 4. Validate Results (orchestrator from Phase R8)
        session_id = ret_payload["session_id"]
        try:
            val_pkg = await validation_service.validate_session(db, session_id, user_id)
        except Exception as exc:
            raise RAGContextPreparationError("validation") from exc
        val_time = int((time.time() - val_start) * 1000)
        
        rank_index = {str(chunk_id): position for position, chunk_id in enumerate(val_pkg.evidence_ranking)}
        validated_chunk_dicts = [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "text": c.text,
                "confidence": c.confidence,
                "authority": c.authority,
                "is_duplicate": c.is_duplicate,
                "conflicts_with": c.conflicts_with,
                "validation_status": c.validation_status,
                "metadata": c.metadata,
            }
            for c in val_pkg.validated_chunks
        ]
        validated_chunk_dicts.sort(key=lambda c: rank_index.get(str(c["chunk_id"]), 10**9))
        document_names = {
            str(ref["document_id"]): ref.get("file_name", "Unknown")
            for ref in val_pkg.document_references
        }
        compiled_context = build_validated_context(
            validated_chunk_dicts,
            document_names=document_names,
            token_budget=settings.RAG_CONTEXT_TOKEN_BUDGET,
            max_chunks=settings.RAG_MAX_CHUNKS,
        )
        selected_ids = {str(c["chunk_id"]) for c in compiled_context["selected_chunks"]}
        validated_chunk_dicts = [
            c for c in validated_chunk_dicts if str(c["chunk_id"]) in selected_ids
        ]

        # Build unified context package from the post-validation, token-bounded evidence only.
        pkg_data = {
            "validated_chunks": validated_chunk_dicts,
            "validated_sources": [
                {
                    "source_id": s.source_id,
                    "document_id": s.document_id,
                    "publisher": s.publisher,
                    "source_type": s.source_type,
                    "authority_score": s.authority_score,
                    "freshness_score": s.freshness_score,
                    "validation_status": s.validation_status
                }
                for s in val_pkg.validated_sources
            ],
            "confidence_scores": val_pkg.confidence_scores,
            "authority_scores": val_pkg.authority_scores,
            "evidence_ranking": val_pkg.evidence_ranking,
            "knowledge_snapshot": val_pkg.knowledge_snapshot,
            "validation_report_reference": val_pkg.validation_report_reference,
            "collection_metadata": val_pkg.collection_metadata,
            "document_references": val_pkg.document_references,
            "context_metadata": {
                **val_pkg.context_metadata,
                "estimated_tokens": compiled_context["estimated_tokens"],
                "token_budget": compiled_context["token_budget"],
                "rag_status": "ready" if validated_chunk_dicts else "no_valid_evidence",
            },
            "validated_context_string": compiled_context["context_string"],
        }
        
        # Safe serialize
        pkg_data = stringify_uuids(pkg_data)
        
        # 5. Create Knowledge Snapshot
        snapshot = await self.snapshot_service.create_snapshot(
            db=db,
            knowledge_version="1.0.0",
            collections_used=collection_ids or [uuid.UUID(c) for c in ret_payload["snapshot"].get("collections", [])],
            documents_used=sorted({uuid.UUID(str(c["document_id"])) for c in validated_chunk_dicts}, key=str),
            chunks_used=[uuid.UUID(str(c["chunk_id"])) for c in validated_chunk_dicts],
            retrieval_session_id=session_id,
            configuration=config
        )
        pkg_data["knowledge_snapshot_id"] = str(snapshot.id)
        
        # 6. Cache package
        await self.cache_service.set_cached_context(
            db, cache_key, pkg_data, ttl_seconds=settings.RAG_CONTEXT_CACHE_TTL_SECONDS
        )

        
        # 7. Log Generation Analytics
        await self.analytics_service.log_analytics(
            db=db,
            generation_job_id=generation_job_id,
            collections_used=collection_ids or [],
            retrieved_documents_count=len(pkg_data["document_references"]),
            retrieved_chunks_count=len(pkg_data["validated_chunks"]),
            context_size=len(pkg_data["validated_context_string"]),
            cache_hit=False,
            retrieval_time_ms=ret_time,
            validation_time_ms=val_time,
            prompt_build_time_ms=0,
            generation_time_ms=0
        )
        
        return pkg_data


class AIGatewayService:
    async def chat_completion(
        self,
        db: AsyncSession,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        model: str = "deepseek-chat",
        response_format: Optional[dict] = None,
        max_tokens: Optional[int] = None,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Proxy completions request to DeepSeek, managing budget, logging, and retry strategies.
        """
        from pydantic import ValidationError
        api_key = settings.DEEPSEEK_API_KEY
        if not api_key or api_key == "REPLACE_WITH_REAL_VALUE":
            raise ValueError("DEEPSEEK_API_KEY is not configured. Set it in environment variables.")
            
        # Standard DeepSeek endpoint
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens
            
        logger.info(f"AI Gateway: forwarding request to LLM (model={model}, messages={len(messages)})")
        
        # Implement retry logic on transient errors/timeouts (up to 3 retries)
        retries = 3
        last_err = None
        start_time = time.time()
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            for attempt in range(retries):
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    
                    raw_body = resp.text
                    try:
                        data = json.loads(raw_body)
                        from app.schemas.ai_gateway import DeepSeekResponse
                        DeepSeekResponse(**data)
                    except ValidationError as ve:
                        logger.error(
                            "AI Gateway: LLM response did not match expected schema",
                            error=str(ve),
                            raw_response=raw_body
                        )
                        raise ValueError(f"Invalid DeepSeek response schema: {ve}") from ve
                    
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info(f"AI Gateway: successful LLM response in {elapsed_ms}ms")
                    
                    # Log generation statistics if we have a job
                    if slug:
                        # Find job by slug (or documents.slug)
                        from app.models.document import Document
                        from app.models.workflow import GenerationJob
                        
                        job_stmt = select(GenerationJob).join(Document, GenerationJob.document_id == Document.id).where(Document.slug == slug)
                        job_res = await db.execute(job_stmt)
                        job = job_res.scalar_one_or_none()
                        
                        if job:
                            # Update token usage and analytics
                            token_usage = data.get("usage", {})
                            job.token_usage = token_usage
                            
                            # Increment analytics generation time
                            from sqlalchemy import update
                            from app.models.rag_integration import GenerationAnalytics
                            stmt = update(GenerationAnalytics).where(GenerationAnalytics.generation_job_id == job.id).values(
                                generation_time_ms=elapsed_ms
                            )
                            await db.execute(stmt)
                            await db.commit()
                            
                    return data
                    
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code >= 500 or e.response.status_code == 429:
                        wait = (2 ** attempt) * 0.5
                        logger.warning(f"LLM request error {e.response.status_code}, retrying in {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    raise e
                except Exception as e:
                    last_err = e
                    wait = (2 ** attempt) * 0.5
                    logger.warning(f"LLM connection error: {e}, retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    
        raise Exception(f"AI Gateway: DeepSeek completions proxy failed after all retries. Last error: {last_err}")


class SelectiveContextBuilder:
    def __init__(self, context_service: GenerationContextService):
        self.context_service = context_service

    async def build_context(
        self,
        db: AsyncSession,
        query: str,
        collection_ids: Optional[List[uuid.UUID]] = None,
        user_id: Optional[uuid.UUID] = None,
        user_org_id: Optional[uuid.UUID] = None,
        config: Optional[dict] = None,
        generation_job_id: Optional[uuid.UUID] = None,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Builds a focused context package for partial regeneration.
        Simply proxies to generation_context_service.prepare_context which handles
        retrieval, validation, snapshotting, caching, and analytics logging.
        """
        return await self.context_service.prepare_context(
            db=db,
            query=query,
            collection_ids=collection_ids,
            user_id=user_id,
            user_org_id=user_org_id,
            config=config,
            generation_job_id=generation_job_id,
            slug=slug
        )


# Singletons
context_cache_service = ContextCacheService()
knowledge_snapshot_service = KnowledgeSnapshotService()
evidence_attribution_service = EvidenceAttributionService()
generation_analytics_service = GenerationAnalyticsService()
prompt_builder_service = PromptBuilderService()
generation_context_service = GenerationContextService()
ai_gateway_service = AIGatewayService()
selective_context_builder = SelectiveContextBuilder(generation_context_service)
