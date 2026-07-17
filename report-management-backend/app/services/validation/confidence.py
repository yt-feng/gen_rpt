import uuid
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.validation import ValidationPolicy

class ConfidenceService:
    async def calculate_confidence(
        self,
        db: AsyncSession,
        chunks: List[Dict[str, Any]],
        authority_scores: Dict[uuid.UUID, float],
        freshness_scores: Dict[uuid.UUID, float],
        conflict_map: Dict[uuid.UUID, List[uuid.UUID]],
        duplicate_flags: Dict[uuid.UUID, bool],
        policy: ValidationPolicy
    ) -> Dict[str, Any]:
        """
        Generates overall, per-source, per-chunk, and per-claim confidence scores.
        """
        confidence_results = {
            "overall_confidence": 1.0,
            "per_source_confidence": {},
            "per_chunk_confidence": {},
            "per_claim_confidence": {}
        }
        
        if not chunks:
            return confidence_results
            
        chunk_confidences = {}
        source_chunks = {}  # doc_id -> list of chunk confidences
        
        for c in chunks:
            chunk_id = c["chunk_id"]
            doc_id = c["document_id"]
            
            similarity = c.get("similarity_score", 0.7)
            authority = authority_scores.get(doc_id, 0.5)
            freshness = freshness_scores.get(doc_id, 0.5)
            
            # Base validation bonus: validated status adds value
            validation_bonus = 1.0 if c.get("validation_status") == "validated" else 0.8
            
            # Chunk Confidence Formula
            # 35% Similarity, 35% Authority, 20% Freshness, 10% Validation bonus
            base_conf = (0.35 * similarity) + (0.35 * authority) + (0.20 * freshness) + (0.10 * validation_bonus)
            
            # Penalize conflicts
            if chunk_id in conflict_map:
                base_conf *= 0.5
                
            # Penalize duplicates slightly (keeps it visible but lower priority)
            if duplicate_flags.get(chunk_id, False):
                base_conf *= 0.8
                
            final_conf = max(0.0, min(1.0, base_conf))
            chunk_confidences[chunk_id] = float(round(final_conf, 4))
            
            if doc_id not in source_chunks:
                source_chunks[doc_id] = []
            source_chunks[doc_id].append(final_conf)

        # Source Confidences
        per_source_conf = {}
        for doc_id, confs in source_chunks.items():
            per_source_conf[str(doc_id)] = float(round(sum(confs) / len(confs), 4))
            
        # Source Diversity calculation
        unique_docs = len(source_chunks)
        total_chunks = len(chunks)
        diversity_score = min(1.0, unique_docs / max(1, total_chunks))
        
        # Overall Confidence: weighted average of chunks adjusted by source diversity
        avg_chunk_conf = sum(chunk_confidences.values()) / total_chunks
        overall_conf = avg_chunk_conf * (0.8 + 0.2 * diversity_score)
        
        # Claims Confidence simulation (mapping headings/claims in chunks to their confidence)
        per_claim_conf = {}
        for c in chunks:
            heading = (c.get("metadata") or {}).get("heading", "") or "General Assertion"
            chunk_id = c["chunk_id"]
            per_claim_conf[heading] = chunk_confidences[chunk_id]

        confidence_results["overall_confidence"] = float(round(overall_conf, 4))
        confidence_results["per_source_confidence"] = per_source_conf
        confidence_results["per_chunk_confidence"] = {str(k): v for k, v in chunk_confidences.items()}
        confidence_results["per_claim_confidence"] = per_claim_conf
        
        return confidence_results

confidence_service = ConfidenceService()
