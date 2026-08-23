# test_rag_advanced_hybrid.py
# Comprehensive advanced unit tests verifying Reciprocal Rank Fusion logic and RAG integration limits

import pytest
import uuid
import math
from typing import List, Dict, Any
from app.services.retrieval_fusion import reciprocal_rank_fusion
from app.services.retrieval_ranking import calculate_freshness_score, calculate_chunk_confidence

def create_mock_chunk(chunk_id: str, score: float, rank: int) -> Dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "content": f"Sample document content for chunk {chunk_id}",
        "similarity_score": score,
        "rank": rank,
        "metadata": {
            "source": "knowledge_processor",
            "version": "1.0.0"
        }
    }

def test_rrf_scoring_math_correctness():
    """
    Verifies that chunks appearing in both lists have their RRF scores summed correctly,
    and the final sorting order matches the fused rank score.
    """
    # 1. Setup mock results
    vector_results = [
        create_mock_chunk("chunk_a", 0.95, 1),
        create_mock_chunk("chunk_b", 0.85, 2),
        create_mock_chunk("chunk_c", 0.75, 3),
    ]
    
    keyword_results = [
        create_mock_chunk("chunk_b", 0.90, 1),
        create_mock_chunk("chunk_d", 0.80, 2),
        create_mock_chunk("chunk_a", 0.70, 3),
    ]
    
    # 2. Run RRF with standard k=60
    k = 60
    fused_results = reciprocal_rank_fusion(vector_results, keyword_results, k=k)
    
    # Expected scores calculation:
    # chunk_a: 1 / (60 + 1) + 1 / (60 + 3) = 1/61 + 1/63 = 0.01639 + 0.01587 = 0.03226
    # chunk_b: 1 / (60 + 2) + 1 / (60 + 1) = 1/62 + 1/61 = 0.01612 + 0.01639 = 0.03251
    # chunk_c: 1 / (60 + 3) = 1/63 = 0.01587
    # chunk_d: 1 / (60 + 2) = 1/62 = 0.01612
    
    assert len(fused_results) == 4
    
    # chunk_b should be first because 0.03251 > 0.03226
    assert fused_results[0]["chunk_id"] == "chunk_b"
    assert math.isclose(fused_results[0]["fusion_score"], (1.0/62.0) + (1.0/61.0), rel_tol=1e-5)
    
    # chunk_a should be second
    assert fused_results[1]["chunk_id"] == "chunk_a"
    assert math.isclose(fused_results[1]["fusion_score"], (1.0/61.0) + (1.0/63.0), rel_tol=1e-5)

def test_rrf_empty_input_boundaries():
    """
    Tests fusion boundaries when one or both of the result lists are empty.
    """
    vector_results = [
        create_mock_chunk("chunk_a", 0.95, 1),
        create_mock_chunk("chunk_b", 0.85, 2),
    ]
    keyword_results = []
    
    fused_results = reciprocal_rank_fusion(vector_results, keyword_results, k=60)
    assert len(fused_results) == 2
    assert fused_results[0]["chunk_id"] == "chunk_a"
    assert fused_results[1]["chunk_id"] == "chunk_b"
    
    # Both empty
    fused_empty = reciprocal_rank_fusion([], [], k=60)
    assert len(fused_empty) == 0

def test_rrf_large_k_parameter():
    """
    Tests if scaling the constant k dampens the rank variations effectively.
    """
    vector_results = [create_mock_chunk("chunk_a", 0.95, 1)]
    keyword_results = [create_mock_chunk("chunk_b", 0.90, 1)]
    
    fused_large_k = reciprocal_rank_fusion(vector_results, keyword_results, k=1000)
    assert len(fused_large_k) == 2
    # Chunks should have identical scores because they both rank 1st in their respective lists
    assert math.isclose(fused_large_k[0]["fusion_score"], 1.0 / 1001.0, rel_tol=1e-6)
    assert math.isclose(fused_large_k[1]["fusion_score"], 1.0 / 1001.0, rel_tol=1e-6)

def test_freshness_score_calculations():
    """
    Verifies time decay calculations for chunk freshness policies.
    """
    from datetime import datetime, timedelta, timezone
    
    # Present time
    now = datetime.now(timezone.utc)
    
    # 1. Fresh document (0 age) should have ~1.0 freshness
    fresh_score = calculate_freshness_score(now, policy="exponential")
    assert math.isclose(fresh_score, 1.0, rel_tol=1e-3)
    
    # 2. Old document (100 days old) linear decay
    old_date = now - timedelta(days=100)
    linear_score = calculate_freshness_score(old_date, policy="linear")
    # 1.0 - (100 / 365) = 1.0 - 0.27397 = 0.72602
    assert math.isclose(linear_score, 1.0 - (100.0 / 365.0), rel_tol=1e-4)

def test_chunk_confidence_scoring():
    """
    Verifies that chunk confidence factors in validation status and document size correctly.
    """
    # 1. Ideal chunk (similarity 0.9, validated, large size)
    high_conf = calculate_chunk_confidence(similarity=0.9, val_status="validated", doc_size=500)
    # 0.9 * 1.1 * 1.0 = 0.99
    assert math.isclose(high_conf, 0.99, rel_tol=1e-4)
    
    # 2. Flagged chunk (similarity 0.8, flagged, small size)
    low_conf = calculate_chunk_confidence(similarity=0.8, val_status="flagged", doc_size=50)
    # 0.8 * 0.5 * 0.8 = 0.32
    assert math.isclose(low_conf, 0.32, rel_tol=1e-4)


@pytest.mark.anyio
async def test_retrieval_analytics_tracking():
    """
    Verifies retrieval analytics aggregation service bindings and settings flags.
    """
    from app.services.retrieval_analytics import retrieval_analytics_service
    from app.core.config import settings
    
    assert settings.RAG_RETRIEVAL_ANALYTICS_ENABLED is True
    assert settings.RAG_ANALYTICS_RETENTION_DAYS == 30
    assert hasattr(retrieval_analytics_service, "aggregate_retrieval_performance")
