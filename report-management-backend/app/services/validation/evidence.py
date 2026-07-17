import uuid
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.validation import ValidationPolicy

class EvidenceService:
    async def evaluate_evidence(
        self,
        db: AsyncSession,
        chunks: List[Dict[str, Any]],
        authority_scores: Dict[uuid.UUID, float],
        freshness_scores: Dict[uuid.UUID, float],
        chunk_confidences: Dict[str, float],
        policy: ValidationPolicy
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Evaluates evidence completeness (completeness score, gaps) and flags unsupported evidence.
        """
        # 1. Evidence Completeness Evaluation
        gaps = []
        completeness_score = 1.0
        
        unique_docs = {c["document_id"] for c in chunks}
        
        # Single Source Dependency Check
        if len(unique_docs) <= 1:
            completeness_score *= 0.6
            gaps.append("Single Source Dependency: Information retrieved from one document only")
            
        # Check source count against policy minimum
        if len(unique_docs) < policy.min_sources:
            completeness_score *= 0.8
            gaps.append(f"Insufficient source diversity: retrieved {len(unique_docs)} source(s), policy requires at least {policy.min_sources}")
            
        # Insufficient Coverage (Total token / character counts check)
        total_tokens = sum((c.get("metadata") or {}).get("token_count", 0) for c in chunks)
        if total_tokens < 300:
            completeness_score *= 0.7
            gaps.append("Insufficient Coverage: Overall text density of context is very low")
            
        # Scan for Missing References in text
        missing_ref_count = 0
        for c in chunks:
            text = c.get("text_content") or ""
            if "according to" in text.lower() and not (c.get("metadata") or {}).get("url"):
                missing_ref_count += 1
                
        if missing_ref_count > 0:
            completeness_score *= 0.9
            gaps.append(f"Potential missing references: found {missing_ref_count} assertions referencing external work without citation URL")
            
        completeness_score = max(0.1, min(1.0, completeness_score))
        
        completeness_details = {
            "completeness_score": float(round(completeness_score, 4)),
            "gaps": gaps,
            "total_tokens": total_tokens,
            "unique_sources_count": len(unique_docs)
        }

        # 2. Unsupported Evidence Detection (Flag but do not remove)
        unsupported_flags = []
        
        for c in chunks:
            chunk_id = c["chunk_id"]
            doc_id = c["document_id"]
            chunk_conf = chunk_confidences.get(str(chunk_id), 1.0)
            authority = authority_scores.get(doc_id, 1.0)
            freshness = freshness_scores.get(doc_id, 1.0)
            
            flags = []
            
            # Low Confidence
            if chunk_conf < policy.min_confidence:
                flags.append("low_confidence_evidence")
                
            # Weak Source
            if authority < policy.min_authority:
                flags.append("weak_source_evidence")
                
            # Expired Knowledge
            if freshness < policy.min_freshness:
                flags.append("expired_knowledge")
                
            # Orphan Chunks (no document ID or source metadata)
            if not doc_id:
                flags.append("orphan_chunk")
                
            if flags:
                unsupported_flags.append({
                    "chunk_id": chunk_id,
                    "file_name": c["file_name"],
                    "flags": flags,
                    "reasons": [f"Fails {f.replace('_', ' ')}" for f in flags]
                })
                
        return completeness_details, unsupported_flags

evidence_service = EvidenceService()
