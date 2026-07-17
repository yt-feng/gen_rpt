from typing import List, Dict, Any

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
