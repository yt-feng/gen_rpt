import math
import re
from typing import List, Dict, Any

def calculate_cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculates the cosine similarity between two float vectors.
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a * a for a in vec1))
    norm_b = math.sqrt(sum(b * b for b in vec2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    cos_sim = dot_product / (norm_a * norm_b)
    return float((cos_sim + 1.0) / 2.0)

def calculate_keyword_score(query: str, text: str) -> float:
    """
    Calculates a simple lexical/Jaccard similarity match between query and chunk content.
    """
    if not query or not text:
        return 0.0
    q_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    t_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    if not q_tokens or not t_tokens:
        return 0.0
    intersection = q_tokens.intersection(t_tokens)
    # Query coverage works better than plain Jaccard for long evidence chunks:
    # relevant chunks naturally contain many words that are not in a short query.
    query_coverage = len(intersection) / len(q_tokens)
    text_precision = len(intersection) / len(t_tokens)
    return float((0.8 * query_coverage) + (0.2 * text_precision))
