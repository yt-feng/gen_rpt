# retrieval_fusion.py
# Reciprocal Rank Fusion (RRF) implementation for combining dense and sparse retrieval results

from typing import List, Dict, Any

def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    keyword_results: List[Dict[str, Any]],
    k: int = 60
) -> List[Dict[str, Any]]:
    """
    Applies RRF to combine two ranked lists of retrieved chunks.
    Formula: score = sum(1 / (k + rank))
    """
    rrf_scores = {}
    chunk_map = {}
    
    # Process vector results
    for rank, chunk in enumerate(vector_results, start=1):
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if not chunk_id:
            continue
        chunk_id_str = str(chunk_id)
        rrf_scores[chunk_id_str] = rrf_scores.get(chunk_id_str, 0.0) + (1.0 / (k + rank))
        if chunk_id_str not in chunk_map:
            chunk_map[chunk_id_str] = chunk

    # Process keyword results
    for rank, chunk in enumerate(keyword_results, start=1):
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if not chunk_id:
            continue
        chunk_id_str = str(chunk_id)
        rrf_scores[chunk_id_str] = rrf_scores.get(chunk_id_str, 0.0) + (1.0 / (k + rank))
        if chunk_id_str not in chunk_map:
            chunk_map[chunk_id_str] = chunk
            
    # Compile fusion outputs sorted by score descending
    fused_results = []
    for chunk_id_str, score in rrf_scores.items():
        chunk = chunk_map[chunk_id_str]
        fused_chunk = {
            **chunk,
            "fusion_score": score,
            "final_score": score
        }
        fused_results.append(fused_chunk)
        
    fused_results.sort(key=lambda x: x["fusion_score"], reverse=True)
    for idx, item in enumerate(fused_results, start=1):
        item["rank"] = idx
        
    return fused_results
