from typing import List, Dict, Any
from app.services.knowledge_processing.workers.embedding import generate_mock_embedding

def calculate_cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Computes cosine similarity between two float vectors.
    Since they are normalized unit vectors, the dot product is equivalent to cosine similarity.
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(x * y for x, y in zip(vec1, vec2))
    # Cosine similarity is in [-1, 1], normalize to [0, 1]
    return float((dot + 1.0) / 2.0)

def calculate_keyword_score(query: str, text: str) -> float:
    """
    Calculates a simple lexical/Jaccard similarity match between query and chunk content.
    """
    if not query or not text:
        return 0.0
    q_tokens = set(query.lower().split())
    t_tokens = set(text.lower().split())
    if not q_tokens or not t_tokens:
        return 0.0
    intersection = q_tokens.intersection(t_tokens)
    union = q_tokens.union(t_tokens)
    return float(len(intersection) / len(union))
