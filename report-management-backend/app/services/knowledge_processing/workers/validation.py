from typing import List, Dict, Any

def run_validation_pipeline(
    extraction_meta: Dict[str, Any],
    metadata_pkg: Dict[str, Any],
    normalized_text: str,
    chunks: List[Dict[str, Any]],
    embeddings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    checks = []
    is_valid = True
    errors = []
    
    # 1. Validate Text Extraction
    extraction_ok = len(normalized_text) > 0
    checks.append({
        "stage": "text_extraction",
        "status": "success" if extraction_ok else "failed",
        "description": f"Extracted {len(normalized_text)} characters of text."
    })
    if not extraction_ok:
        is_valid = False
        errors.append("Extracted text is empty")
        
    # 2. Validate Metadata Extraction
    metadata_ok = metadata_pkg.get("title") is not None and metadata_pkg.get("word_count", 0) > 0
    checks.append({
        "stage": "metadata_extraction",
        "status": "success" if metadata_ok else "failed",
        "description": "Document metadata package generated."
    })
    if not metadata_ok:
        is_valid = False
        errors.append("Metadata package is missing required fields or has zero words")
        
    # 3. Validate Normalization
    normalized_ok = not any(char in normalized_text for char in ["\x00", "\x1f"])
    checks.append({
        "stage": "content_normalization",
        "status": "success" if normalized_ok else "warning",
        "description": "Control characters stripped and layout normalized."
    })
    
    # 4. Validate Chunking
    chunking_ok = len(chunks) > 0
    if chunking_ok:
        avg_chunk_size = sum(c.get("character_count", 0) for c in chunks) / len(chunks)
        chunking_desc = f"Generated {len(chunks)} chunks, average character size: {avg_chunk_size:.1f}."
    else:
        chunking_desc = "No chunks generated."
        is_valid = False
        errors.append("Document text chunk generation resulted in zero chunks")
        
    checks.append({
        "stage": "document_chunking",
        "status": "success" if chunking_ok else "failed",
        "description": chunking_desc
    })
    
    # 5. Validate Embeddings
    embeddings_ok = len(embeddings) == len(chunks)
    checks.append({
        "stage": "embedding_generation",
        "status": "success" if embeddings_ok else "failed",
        "description": f"Generated {len(embeddings)} embeddings out of {len(chunks)} chunks."
    })
    if not embeddings_ok:
        is_valid = False
        errors.append(f"Mismatch between chunks ({len(chunks)}) and embeddings ({len(embeddings)})")
        
    confidence = 1.0 if is_valid else 0.5
    if errors:
        confidence = max(0.1, confidence - (0.2 * len(errors)))
        
    return {
        "is_valid": is_valid,
        "validation_type": "pipeline_validation",
        "confidence": confidence,
        "result": "validated" if is_valid else "flagged",
        "errors": errors,
        "checks": checks,
        "summary": f"Validation passed with {confidence * 100:.0f}% confidence." if is_valid else f"Validation flagged errors: {', '.join(errors)}"
    }
