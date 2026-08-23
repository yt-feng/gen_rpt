import uuid
import time
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.knowledge import RetrievalSession, RetrievalResult

class RetrievalAnalyticsService:
    async def log_retrieval_session(
        self,
        db: AsyncSession,
        query: str,
        collection_ids: list,
        user_id: uuid.UUID,
        filters: dict,
        latency_ms: int,
        cache_hit: bool,
        selected_chunks: list,
        snapshot_metadata: dict
    ) -> uuid.UUID:
        """
        Creates an immutable retrieval session entry in the database.
        Registers associated chunks as RetrievalResults.
        """
        # Create session
        session = RetrievalSession(
            id=uuid.uuid4(),
            query=query,
            collection_id=collection_ids[0] if collection_ids else None,
            user_id=user_id,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            duration_ms=latency_ms,
            status="completed",
            request_metadata={
                "collection_ids": [str(c) for c in collection_ids],
                "filters": filters
            },
            snapshot_metadata=snapshot_metadata,
            session_metadata={
                "cache_hit": cache_hit,
                "latency_ms": latency_ms,
                "chunks_count": len(selected_chunks)
            }
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        # Add results
        for idx, chunk in enumerate(selected_chunks):
            result = RetrievalResult(
                id=uuid.uuid4(),
                session_id=session.id,
                chunk_id=chunk["chunk_id"],
                similarity_score=chunk["similarity_score"],
                ranking=chunk["rank"],
                confidence=chunk.get("confidence_score", 1.0),
                source_id=chunk.get("source_id"),
                result_metadata={
                    "final_score": chunk.get("final_score"),
                    "freshness_score": chunk.get("freshness_score")
                }
            )
            db.add(result)
            
        await db.commit()
        return session.id

    async def aggregate_retrieval_performance(
        self,
        db: AsyncSession,
        collection_id: uuid.UUID
    ) -> dict:
        """
        Aggregates performance metrics (avg latency, total queries) for a collection.
        """
        from sqlalchemy import select, func
        from app.core.config import settings

        stmt = select(
            func.avg(RetrievalSession.duration_ms),
            func.count(RetrievalSession.id)
        ).where(RetrievalSession.collection_id == collection_id)
        res = await db.execute(stmt)
        row = res.first()
        avg_latency = float(row[0]) if row and row[0] is not None else 0.0
        total_queries = int(row[1]) if row and row[1] is not None else 0

        return {
            "collection_id": str(collection_id),
            "average_latency_ms": round(avg_latency, 2),
            "total_queries": total_queries,
            "retention_days": getattr(settings, "RAG_ANALYTICS_RETENTION_DAYS", 30)
        }

retrieval_analytics_service = RetrievalAnalyticsService()
