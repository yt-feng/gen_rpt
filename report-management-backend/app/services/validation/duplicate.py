import uuid
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.validation import ValidationPolicy

class DuplicateService:
    async def analyze_duplicates(
        self,
        db: AsyncSession,
        chunks: List[Dict[str, Any]],
        authority_scores: Dict[uuid.UUID, float],
        policy: ValidationPolicy
    ) -> Tuple[Dict[uuid.UUID, bool], Dict[str, Any]]:
        """
        Identifies duplicate chunks and documents within the retrieved session context.
        Keeps the highest-quality chunk and flags duplicates.
        Does not delete them.
        """
        duplicate_flags = {}
        analysis_metadata = {
            "duplicate_chunks_count": 0,
            "duplicate_documents_count": 0,
            "duplicate_references": [],
            "duplicate_ratio": 0.0
        }
        
        if not chunks:
            return duplicate_flags, analysis_metadata
            
        seen_texts = {}  # content hash -> highest quality chunk dict
        duplicate_chunks = []
        
        # Sort chunks by quality: authority score first, then similarity score
        sorted_chunks = sorted(
            chunks,
            key=lambda c: (authority_scores.get(c["document_id"], 0.0), c.get("similarity_score", 0.0)),
            reverse=True
        )
        
        # Check duplicate content
        for chunk in sorted_chunks:
            chunk_id = chunk["chunk_id"]
            text = (chunk.get("text_content") or "").strip().lower()
            
            # Simple content signature
            text_signature = hash(text)
            
            if text_signature in seen_texts:
                # Flag as duplicate since we sorted by descending quality
                duplicate_flags[chunk_id] = True
                duplicate_chunks.append(chunk_id)
                analysis_metadata["duplicate_references"].append({
                    "original_chunk_id": seen_texts[text_signature]["chunk_id"],
                    "duplicate_chunk_id": chunk_id,
                    "reason": "Exact content match"
                })
            else:
                seen_texts[text_signature] = chunk
                duplicate_flags[chunk_id] = False
                
        # Fill in any missing flags
        for chunk in chunks:
            cid = chunk["chunk_id"]
            if cid not in duplicate_flags:
                duplicate_flags[cid] = False
                
        # Stats
        total_chunks = len(chunks)
        dup_count = len(duplicate_chunks)
        analysis_metadata["duplicate_chunks_count"] = dup_count
        analysis_metadata["duplicate_ratio"] = float(round(dup_count / total_chunks if total_chunks > 0 else 0.0, 4))
        
        return duplicate_flags, analysis_metadata

duplicate_service = DuplicateService()
