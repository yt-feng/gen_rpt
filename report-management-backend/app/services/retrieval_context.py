import hashlib
from typing import List, Dict, Any, Optional

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


def build_validated_context(
    validated_chunks: List[Dict[str, Any]],
    document_names: Optional[Dict[str, str]] = None,
    token_budget: int = 6000,
    max_chunks: int = 0,
) -> Dict[str, Any]:
    """Compile only validated evidence into a compact, token-bounded prompt block."""
    document_names = document_names or {}
    selected: List[Dict[str, Any]] = []
    parts: List[str] = []
    seen_hashes = set()
    used_tokens = 0

    for chunk in validated_chunks:
        if max_chunks > 0 and len(selected) >= max_chunks:
            break

        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        content_hash = hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            continue

        document_id = str(chunk.get("document_id", ""))
        source_name = document_names.get(document_id, "Unknown")
        header = (
            f"[Evidence {len(selected) + 1} | {source_name} | "
            f"chunk={chunk.get('chunk_id')} | confidence={float(chunk.get('confidence', 0.0)):.2f}]\n"
        )
        header_tokens = estimate_tokens(header)
        remaining = token_budget - used_tokens - header_tokens
        if remaining <= 0:
            break

        text_tokens = estimate_tokens(text)
        if text_tokens > remaining:
            # Whole chunks only: a partial quotation is not auditable evidence.
            continue

        parts.append(f"{header}{text}")
        selected.append(chunk)
        seen_hashes.add(content_hash)
        used_tokens += header_tokens + text_tokens

    return {
        "context_string": "\n\n".join(parts),
        "selected_chunks": selected,
        "estimated_tokens": used_tokens,
        "token_budget": token_budget,
    }
