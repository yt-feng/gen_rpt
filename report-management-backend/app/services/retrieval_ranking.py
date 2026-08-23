import math
from datetime import datetime, timezone
from typing import Dict, Any, List

def calculate_freshness_score(created_at: datetime, policy: str = "exponential") -> float:
    """
    Calculates freshness score using time decay.
    policy: 'exponential', 'linear', or 'none'
    """
    if not created_at:
        return 1.0
    now = datetime.now(timezone.utc)
    delta = now - created_at.astimezone(timezone.utc)
    age_days = max(0.0, delta.total_seconds() / 86400.0)
    
    if policy == "none":
        return 1.0
    elif policy == "linear":
        # Linear decay over 365 days
        return max(0.0, 1.0 - (age_days / 365.0))
    else:
        # Exponential decay: e^(-lambda * days), lambda = 0.005 (half life of ~138 days)
        return float(math.exp(-0.005 * age_days))

def calculate_chunk_confidence(similarity: float, val_status: str, doc_size: int) -> float:
    """
    Computes a confidence score based on similarity match, validation state, and chunk quality metrics.
    """
    # Validation multiplier
    val_multiplier = 1.0
    if val_status == "validated":
        val_multiplier = 1.1
    elif val_status in ["flagged", "conflict"]:
        val_multiplier = 0.5
        
    # Size penalty for extremely small chunks
    size_penalty = 1.0
    if doc_size < 100:
        size_penalty = 0.8
        
    confidence = similarity * val_multiplier * size_penalty
    return float(min(1.0, max(0.0, confidence)))

def rank_retrieved_chunks(
    chunks: List[Dict[str, Any]],
    weights: Dict[str, float] = None,
    freshness_policy: str = "exponential"
) -> List[Dict[str, Any]]:
    """
    Ranks chunks using configurable weights for similarity, freshness, and confidence.
    """
    if not weights:
        weights = {"similarity": 0.5, "freshness": 0.25, "confidence": 0.25}
        
    # Normalize weights to sum to 1.0
    total_w = sum(weights.values())
    if total_w > 0:
        normalized_w = {k: v / total_w for k, v in weights.items()}
    else:
        normalized_w = {"similarity": 1.0, "freshness": 0.0, "confidence": 0.0}
        
    ranked_list = []
    for chunk in chunks:
        # Extract inputs
        similarity = chunk.get("similarity_score", 0.0)
        created_at = chunk.get("created_at")
        val_status = chunk.get("validation_status", "pending")
        doc_size = chunk.get("doc_size", 500)
        
        # Calculations
        freshness = calculate_freshness_score(created_at, policy=freshness_policy)
        confidence = calculate_chunk_confidence(similarity, val_status, doc_size)
        
        # Combined score
        final_score = (
            normalized_w.get("similarity", 0.5) * similarity +
            normalized_w.get("freshness", 0.25) * freshness +
            normalized_w.get("confidence", 0.25) * confidence
        )
        
        ranked_chunk = {
            **chunk,
            "freshness_score": freshness,
            "confidence_score": confidence,
            "final_score": final_score
        }
        ranked_list.append(ranked_chunk)
        
    # Sort by final score descending
    ranked_list.sort(key=lambda x: x["final_score"], reverse=True)
    
    # Assign final rank position
    for idx, item in enumerate(ranked_list):
        item["rank"] = idx + 1
        
    return ranked_list

# Support hybrid RRF rank bindings
from app.services.retrieval_fusion import reciprocal_rank_fusion
