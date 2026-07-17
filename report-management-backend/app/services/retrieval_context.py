from typing import List, Dict, Any

def estimate_tokens(text: str) -> int:
    """
    Estimates token counts based on character heuristic (roughly 4 characters per token).
    """
    if not text:
        return 0
    return max(1, int(len(text) / 4.0))

def build_retrieval_context(
    ranked_chunks: List[Dict[str, Any]],
    token_budget: int = 4000
) -> Dict[str, Any]:
    """
    Token budget manager and context compiler.
    Appends top ranked chunks under token limits, deduplicates duplicates, and generates structured references.
    """
    selected_chunks = []
    seen_contents = set()
    total_tokens = 0
    
    context_parts = []
    
    for chunk in ranked_chunks:
        text = chunk.get("text_content", "")
        # Deduplication
        if text in seen_contents:
            continue
            
        chunk_tokens = estimate_tokens(text)
        if total_tokens + chunk_tokens > token_budget:
            continue
            
        seen_contents.add(text)
        total_tokens += chunk_tokens
        selected_chunks.append(chunk)
        
        # Build structured segment
        ref = f"Source: {chunk.get('file_name', 'Unknown')}\n"
        ref += f"Chunk ID: {chunk.get('chunk_id')}\n"
        ref += f"Validation Status: {chunk.get('validation_status', 'pending')}\n"
        ref += f"Confidence Score: {chunk.get('confidence_score', 1.0):.2f}\n"
        ref += f"Content:\n{text}\n"
        ref += "-" * 40 + "\n"
        context_parts.append(ref)
        
    compiled_context = "\n".join(context_parts)
    
    return {
        "context_string": compiled_context,
        "selected_chunks": selected_chunks,
        "estimated_tokens": total_tokens,
        "token_budget": token_budget
    }
