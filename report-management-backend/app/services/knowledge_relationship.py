import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.knowledge import KnowledgeDocument, KnowledgeRelationship, ValidationResult
from app.schemas.knowledge import SimilarityResponse

class KnowledgeRelationshipService:
    async def create_relationship(
        self, db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID, rel_type: str, metadata: Optional[Dict[str, Any]] = None
    ) -> KnowledgeRelationship:
        rel = KnowledgeRelationship(
            source_document_id=source_id,
            target_document_id=target_id,
            relationship_type=rel_type,
            relationship_metadata=metadata
        )
        db.add(rel)
        await db.commit()
        await db.refresh(rel)
        return rel

    async def list_document_relationships(self, db: AsyncSession, document_id: uuid.UUID) -> List[KnowledgeRelationship]:
        res = await db.execute(
            select(KnowledgeRelationship).filter(
                or_(
                    KnowledgeRelationship.source_document_id == document_id,
                    KnowledgeRelationship.target_document_id == document_id
                )
            )
        )
        return list(res.scalars().all())

    async def find_similar_documents(
        self, db: AsyncSession, document_id: uuid.UUID, limit: int = 5
    ) -> List[SimilarityResponse]:
        """
        Computes non-vector metadata-based similarity candidate reports.
        Compares formats, extension types, filename tokens Jaccard score, and matching languages.
        """
        # Fetch target document
        target_doc = await db.get(KnowledgeDocument, document_id)
        if not target_doc:
            return []
            
        # Fetch other documents in the same collection
        others_res = await db.execute(
            select(KnowledgeDocument).filter(
                KnowledgeDocument.collection_id == target_doc.collection_id,
                KnowledgeDocument.id != document_id,
                KnowledgeDocument.deleted_at.is_(None)
            )
        )
        other_docs = others_res.scalars().all()
        
        target_words = set(target_doc.file_name.lower().split("_"))
        results = []
        
        for doc in other_docs:
            score = 0.0
            reasons = []
            
            # 1. Filename Jaccard token matching
            doc_words = set(doc.file_name.lower().split("_"))
            intersection = target_words.intersection(doc_words)
            union = target_words.union(doc_words)
            jaccard = len(intersection) / len(union) if union else 0.0
            
            if jaccard > 0.0:
                score += jaccard * 0.5
                reasons.append(f"Filename Jaccard token overlap ({jaccard:.1f})")
                
            # 2. Match exact size range
            size_ratio = min(target_doc.size, doc.size) / max(target_doc.size, doc.size)
            if size_ratio > 0.9:
                score += 0.3
                reasons.append("Highly matching file byte sizes")
                
            # 3. Match format extensions
            if target_doc.extension == doc.extension:
                score += 0.2
                reasons.append("Identical document extension")
                
            if score > 0.1:
                results.append(
                    SimilarityResponse(
                        document_id=doc.id,
                        file_name=doc.file_name,
                        similarity_score=round(score, 3),
                        reason=", ".join(reasons)
                    )
                )
                
        # Sort by similarity score
        results.sort(key=lambda x: x.similarity_score, reverse=True)
        return results[:limit]

knowledge_relationship_service = KnowledgeRelationshipService()
